"""Turn a function group's Material Attributes into bundles, as FP's own modules pass them.

A UE function's Material Attributes input or output is one socket per attribute on its group node
("Material: Base Color", ... "Result: Base Color", ...), so twenty wires from one module to the next.
After an import's groups are pruned (build.pruning), each set of two or more such sockets becomes one Bundle socket:
inside the group a Separate Bundle hands the attributes out and a Combine Bundle gathers what the group sets;
where the group is used, a Combine Bundle gathers the inputs and a Separate Bundle hands out the outputs.
A module's bundle feeding the next one attribute for attribute is then one wire (Pre FX -> Post FX).
The compiled shader is the same: bundles resolve at compile time.
"""
import bpy

from .ue_graph import ATTRIBUTE_DEFAULT, ATTRIBUTE_WIDTH, BREAK_ORDER, CARRIED, pretty
from .ue_graph.core import MADE
from .ue_graph.edits import holds

KEY_FROM = "mp_bundled_from"    # a bundled group's fingerprint (build.KEY_FP) from before bundling
TAG = "mp_bundle"               # on the bundle nodes made here: "in" / "out" / "use_in" / "use_out" / "set" / "join" / "shared"
ATTRIBUTE_NAMES = {pretty(a) for a in BREAK_ORDER}
# Item type per attribute, the same in every bundle (a Separate Bundle reads an item of another type as 0)
ITEM_OF = {pretty(a): 'FLOAT' if ATTRIBUTE_WIDTH.get(a, 1) == 1 else 'VECTOR' for a in BREAK_ORDER}
# a module's full Material Attributes; attributes it doesn't set get UE's defaults (as FP's modules pass them)
COMPLETE = [(pretty(a), ITEM_OF[pretty(a)], ATTRIBUTE_DEFAULT.get(a)) for a in CARRIED]
PARAMETER_PREFIXES = ("P", "V")     # parameter sockets ("P: Roughness"), not attributes
ITEM_TYPE = {'VALUE': 'FLOAT', 'VECTOR': 'VECTOR', 'RGBA': 'RGBA', 'INT': 'INT', 'BOOLEAN': 'BOOLEAN'}


PARAMETERS = "Parameters"       # bundle of a function's parameters ("P: name" / "V: name" inputs)


def _attribute_sets(tree, in_out):
    """{bundle name: [(interface socket, item name), ...]} for sets of two or more sockets.

    Covers Material Attributes sockets ("Material: Base Color" -> item "Base Color") and
    a function's parameter inputs ("P: Darkside" -> item "Darkside")."""
    sets = {}
    for it in tree.interface.items_tree:
        if it.item_type != 'SOCKET' or it.in_out != in_out or ": " not in it.name:
            continue
        prefix, attribute = it.name.rsplit(": ", 1)
        if prefix in PARAMETER_PREFIXES:
            if in_out == 'INPUT' and it.socket_type in ('NodeSocketFloat', 'NodeSocketVector', 'NodeSocketColor'):
                # bundle item names can't contain ":"; a vector parameter sharing a scalar's name gets a suffix
                taken = {n for _i, n in sets.get(PARAMETERS, [])}
                name = attribute if attribute not in taken else attribute + " (" + prefix + ")"
                sets.setdefault(PARAMETERS, []).append((it, name))
        elif attribute in ATTRIBUTE_NAMES:
            sets.setdefault(prefix, []).append((it, attribute))
    return {p: items for p, items in sets.items() if len(items) >= 2}


def _item_type(sock):
    return ITEM_TYPE.get(sock.type, 'FLOAT')


def _value(sock):
    v = getattr(sock, "default_value", None)
    return tuple(v) if hasattr(v, "__len__") else v


def _set(sock, value):
    if value is None or not hasattr(sock, "default_value"):
        return
    try:
        cur = sock.default_value
        if hasattr(cur, "__len__"):
            n = len(cur)
            vals = tuple(value) if hasattr(value, "__len__") else (value,) * n
            value = (vals + (1.0,) * n)[:n]
        elif hasattr(value, "__len__"):
            value = value[0]
        if not holds(cur, value):       # (each set updates the whole tree)
            sock.default_value = value
    except (TypeError, ValueError):
        pass


def _ref(sock):
    """Socket as (node name, identifier, is_output): direct references go stale when a group's interface is rebuilt."""
    return None if sock is None else (sock.node.name, sock.identifier, sock.is_output)


def _sock(tree, ref):
    if ref is None:
        return None
    node = tree.nodes.get(ref[0])
    if node is None:
        return None
    return next((s for s in (node.outputs if ref[2] else node.inputs) if s.identifier == ref[1]), None)


def _link(tree, out, ref_in=None, ref_out=None, into=None):
    """Link out (or ref_out) to into (or ref_in), looking the references up now. Does nothing when either is gone."""
    src = out if out is not None else _sock(tree, ref_out)
    dst = into if into is not None else _sock(tree, ref_in)
    if src is not None and dst is not None and src.is_output and not dst.is_output:
        tree.links.new(src, dst)


def _users(trees):
    """{group pointer: [(tree, group node), ...]} in tree order (bundling adds no group nodes)."""
    out = {}
    for t in trees:
        for n in t.nodes:
            if n.bl_idname == "ShaderNodeGroup" and n.node_tree is not None:
                out.setdefault(n.node_tree.as_pointer(), []).append((t, n))
    return out


def scope(groups):
    """Pointers of the groups and of every tree using one, directly or through other groups.
    A pass touches nothing else; scanning the whole file grew with every cell of a map."""
    keep, todo = set(), list(groups)
    while todo:
        new = [g for g in todo if g.as_pointer() not in keep]
        keep.update(g.as_pointer() for g in new)
        todo = []
        for users in (bpy.data.user_map(subset=new).values() if new else ()):
            for u in users:
                if isinstance(u, bpy.types.NodeTree):
                    todo.append(u)
                elif getattr(u, "node_tree", None) is not None:
                    keep.add(u.node_tree.as_pointer())
    return keep


def in_scope(items, near):
    """The trees (or the owners of embedded trees) among items whose tree is in near, in order."""
    out = []
    for x in items:
        t = x if isinstance(x, bpy.types.NodeTree) else getattr(x, "node_tree", None)
        if t is not None and t.as_pointer() in near:
            out.append(x)
    return out


def _items(node, names, types):
    """Create one bundle item per name. Returns {name: item name as Blender stores it} (it rewrites "(" and ":")."""
    if len(node.bundle_items):
        node.bundle_items.clear()
    for name in names:
        node.bundle_items.new(types[name], name)
    return {name: node.bundle_items[i].name for i, name in enumerate(names)}


class _Pending:
    """A Combine Bundle ("out", "use_in") made empty, its items held here until _collapse keeps it (fill)
    or replaces it: most are replaced, and each item, value and link set on the node updates the whole tree.
    The node itself is made where it always was, so node names stay as they were."""

    def __init__(self, run, tree, node, names, types, values, srcs, extras=()):
        self.tree, self.node = tree, node
        self.names, self.types, self.values, self.extras = names, types, values, extras
        self.src = {}           # name -> output it reads, as _ref
        for name, ref in srcs.items():
            run.point(self, name, ref)
        # what Blender stores for these items (names rewritten, values as floats), from a scratch node
        sc = run.scratch()
        self.kept = _items(sc, names, types)
        for extra, kind, default in extras:
            sc.bundle_items.new(kind, extra)
            _set(sc.inputs[extra], default)
        for name, value in values.items():
            _set(sc.inputs[self.kept[name]], value)
        self.items = [(s.name, s.type, _value(s)) for s in sc.inputs if s.name and s.identifier != "__extend__"]

    def view(self):
        """[(item name, socket type, output it reads or None, value)], as the filled node has them."""
        back = {v: k for k, v in self.kept.items()}
        out = []
        for name, kind, value in self.items:
            ref = self.src.get(back.get(name))
            out.append((name, kind, _sock(self.tree, ref) if ref else None, value))
        return out

    def fill(self, run):
        node, kept = self.node, _items(self.node, self.names, self.types)
        for extra, kind, default in self.extras:
            node.bundle_items.new(kind, extra)
            _set(node.inputs[extra], default)
        for name, value in self.values.items():
            _set(node.inputs[kept[name]], value)
        for name, ref in self.src.items():
            _link(self.tree, None, ref_out=ref, into=node.inputs[kept[name]])
        run.drop(self)


class _Run:
    """One bundle_attributes pass: its _Pending nodes, who reads what through them, and the scratch node."""

    def __init__(self):
        self.pending = {}       # node pointer -> _Pending
        self.named = {}         # (tree pointer, node name) -> _Pending
        self.reads = {}         # (tree pointer, node name, output identifier) -> {(_Pending, name): None}
        self.read_count = {}    # (tree pointer, node name) -> items read from its outputs
        self._scratch = None

    def scratch(self):
        if self._scratch is None:
            self._scratch = bpy.data.node_groups.new(".mp bundle items", "ShaderNodeTree")
            self._scratch.nodes.new("NodeCombineBundle")
        return self._scratch.nodes[0]

    def add(self, *args, **kw):
        p = _Pending(self, *args, **kw)
        self.pending[p.node.as_pointer()] = p
        self.named[(p.tree.as_pointer(), p.node.name)] = p
        return p

    def _unread(self, p, name, ref):
        key = (p.tree.as_pointer(),) + ref[:2]
        if self.reads.get(key, {}).pop((p, name), MISSING) is not MISSING:
            self.read_count[key[:2]] -= 1

    def point(self, p, name, ref):
        old = p.src.get(name)
        if old is not None:
            self._unread(p, name, old)
        p.src[name] = ref
        key = (p.tree.as_pointer(),) + ref[:2]
        got = self.reads.setdefault(key, {})
        if (p, name) not in got:
            self.read_count[key[:2]] = self.read_count.get(key[:2], 0) + 1
        got[(p, name)] = None

    def readers(self, tree, node_name, ident):
        """The pending items reading an output, as targets of _repoint (where a link's reference would be)."""
        got = self.reads.get((tree.as_pointer(), node_name, ident), {})
        return [(p.node.name, ("item", name), False) for p, name in got]

    def repoint(self, tree, target, out):
        p = self.named[(tree.as_pointer(), target[0])]
        self.point(p, target[1][1], _ref(out))

    def read(self, tree, node):
        return self.read_count.get((tree.as_pointer(), node.name), 0) > 0

    def retree(self, old, new):
        """new (a copy, same node names) takes old's place: its pending nodes and reads move over."""
        op, np = old.as_pointer(), new.as_pointer()
        for p in [p for p in self.pending.values() if p.tree.as_pointer() == op]:
            del self.pending[p.node.as_pointer()]
            del self.named[(op, p.node.name)]
            p.tree, p.node = new, new.nodes[p.node.name]
            self.pending[p.node.as_pointer()] = p
            self.named[(np, p.node.name)] = p
        for d in (self.reads, self.read_count):
            for k in [k for k in d if k[0] == op]:
                d[(np,) + k[1:]] = d.pop(k)

    def drop(self, p):
        for name, ref in p.src.items():
            self._unread(p, name, ref)
        del self.pending[p.node.as_pointer()]
        del self.named[(p.tree.as_pointer(), p.node.name)]

    def close(self):
        for p in list(self.pending.values()):
            p.fill(self)
        if self._scratch is not None:
            bpy.data.node_groups.remove(self._scratch)


def _relink(run, tree, out, targets):
    for target in targets:
        if isinstance(target[1], tuple):       # an item of a _Pending node
            run.repoint(tree, target, out)
        else:
            _link(tree, out, ref_in=target)


def _unused_copy(group, users, trees, run):
    """A copy of group for its own edits: an interface or link edit in a group with users updates every tree using
    it (6x the cost). Its users switch to it afterwards (_bundle_group); node names stay, so references hold."""
    c = group.copy()
    run.retree(group, c)
    op = group.as_pointer()
    for lst in users.values():
        for i, (t, n) in enumerate(lst):
            if t.as_pointer() == op:
                lst[i] = (c, c.nodes[n.name])
    trees[:] = [c if t.as_pointer() == op else t for t in trees]
    MADE.pop(op, None)
    return c


def _bundle_group(group, users, run, trees):
    """Group's attribute socket sets as bundles, inside and where it is used (users: _users).
    Returns (how many sets, the group now: a copy replaces one that had users)."""
    ins, outs = _attribute_sets(group, 'INPUT'), _attribute_sets(group, 'OUTPUT')
    if not ins and not outs:
        return 0, group
    mine = users.get(group.as_pointer(), [])
    sets = [(in_out, prefix, pairs) for in_out, s in (('INPUT', ins), ('OUTPUT', outs)) for prefix, pairs in s.items()]
    bundled_in = {it.identifier for in_out, _p, pairs in sets if in_out == 'INPUT' for it, _n in pairs}
    bundled_out = {it.identifier for in_out, _p, pairs in sets if in_out == 'OUTPUT' for it, _n in pairs}
    instances = {(t.as_pointer(), n.name) for t, n in mine}
    # what the group nodes' sockets carry (links, values), before anything changes
    recs = []
    for in_out, prefix, pairs in sets:
        idents = {it.identifier: name for it, name in pairs}
        types, recorded = {}, []
        for t, n in mine:
            rec = {}
            for s in (n.inputs if in_out == 'INPUT' else n.outputs):
                if s.identifier not in idents:
                    continue
                name = idents[s.identifier]
                types.setdefault(name, _item_type(s))
                if in_out == 'INPUT':
                    rec[name] = (_ref(s.links[0].from_socket) if s.is_linked else None, _value(s))
                else:
                    # a link into another instance's bundled input is read by that input's pending item instead
                    rec[name] = ([_ref(l.to_socket) for l in s.links
                                  if (t.as_pointer(), l.to_node.name) not in instances
                                  or l.to_socket.identifier not in bundled_in] if s.is_linked else [], s.identifier)
            recorded.append((t, n, rec))
        recs.append((types, recorded))
    g = group
    if mine and group.users == len(mine):
        g = _unused_copy(group, users, trees, run)
    iface = g.interface
    items = {it.identifier: it for it in iface.items_tree if it.item_type == 'SOCKET'}
    idents_made = []
    # inside the group
    for (in_out, prefix, pairs), (types, _recorded) in zip(sets, recs):
        idents = {it.identifier: name for it, name in pairs}
        names = [name for _it, name in pairs]
        inside = []
        for n in g.nodes:
            if in_out == 'INPUT' and n.bl_idname == "NodeGroupInput":
                for s in n.outputs:
                    pending = run.readers(g, n.name, s.identifier) if s.identifier in idents else []
                    if s.identifier in idents and (s.is_linked or pending):
                        types.setdefault(idents[s.identifier], _item_type(s))
                        inside.append((n, idents[s.identifier],
                                       ([_ref(l.to_socket) for l in s.links] if s.is_linked else []) + pending))
            elif in_out == 'OUTPUT' and n.bl_idname == "NodeGroupOutput":
                for s in n.inputs:
                    if s.identifier in idents:
                        types.setdefault(idents[s.identifier], _item_type(s))
                        inside.append((n, idents[s.identifier], (_ref(s.links[0].from_socket) if s.is_linked else None, _value(s))))
        for name in names:
            types[name] = ITEM_OF.get(name, types.get(name, 'FLOAT'))
        # the bundle socket takes the first socket's place
        socks = [items[it.identifier] for it, _name in pairs]
        first = socks[0]
        parent, position = first.parent, first.position
        bundle = iface.new_socket(prefix, in_out=in_out, socket_type='NodeSocketBundle')
        if parent is not None and parent.name:
            iface.move_to_parent(bundle, parent, position)
        else:
            iface.move(bundle, position)
        for it in socks:
            iface.remove(it)
        ident = bundle.identifier
        idents_made.append(ident)
        if in_out == 'INPUT':
            by_gi = {}
            for gi, name, targets in inside:
                sep = by_gi.get(gi.name)
                if sep is None:
                    sep = g.nodes.new("NodeSeparateBundle")
                    sep.label, sep[TAG] = prefix, "in"
                    sep.location = (gi.location.x + 60, gi.location.y)
                    kept = _items(sep, names, types)
                    g.links.new(next(o for o in gi.outputs if o.identifier == ident), sep.inputs[0])
                    by_gi[gi.name] = sep
                _relink(run, g, sep.outputs[kept[name]], targets)
        else:
            outs_by_node = {}       # Group Output name -> (node, {name: output it reads}, {name: value})
            for go, name, (src, value) in inside:
                if go.name not in outs_by_node:
                    comb = g.nodes.new("NodeCombineBundle")
                    comb.label, comb[TAG] = prefix, "out"
                    comb.location = (go.location.x - 160, go.location.y)
                    g.links.new(comb.outputs[0], next(i for i in go.inputs if i.identifier == ident))
                    outs_by_node[go.name] = (comb, {}, {})
                _comb, srcs, values = outs_by_node[go.name]
                if src is not None:
                    if _sock(g, src) is not None:
                        srcs[name] = src
                else:
                    values[name] = value
            extras = [c for c in COMPLETE if c[0] not in names]
            for comb, srcs, values in outs_by_node.values():
                run.add(g, comb, names, types, values, srcs, extras)
    if g is not group:
        for _t, n in mine:
            n.node_tree = g

    def alive(t, ref):
        # an instance's bundled output is gone already but is still read until its own set repoints the readers
        return _sock(t, ref) is not None or (t.as_pointer(), ref[0]) in instances and ref[1] in bundled_out
    # where the group is used
    for (in_out, prefix, pairs), (types, recorded), ident in zip(sets, recs, idents_made):
        names = [name for _it, name in pairs]
        for t, n, rec in recorded:
            sock = next(s for s in (n.inputs if in_out == 'INPUT' else n.outputs) if s.identifier == ident)
            if in_out == 'INPUT':
                comb = t.nodes.new("NodeCombineBundle")
                comb.label, comb[TAG] = prefix, "use_in"
                comb.location = (n.location.x - 180, n.location.y)
                srcs = {name: src for name, (src, _v) in rec.items() if src is not None and alive(t, src)}
                values = {name: value for name, (src, value) in rec.items() if src is None}
                run.add(t, comb, names, types, values, srcs)
                t.links.new(comb.outputs[0], sock)
            else:
                rec = {name: targets + run.readers(t, n.name, old) for name, (targets, old) in rec.items()}
                if not any(rec.values()):
                    continue
                sep = t.nodes.new("NodeSeparateBundle")
                sep.label, sep[TAG] = prefix, "use_out"
                sep.location = (n.location.x + n.width + 40, n.location.y)
                kept = _items(sep, [nm for nm in names if rec.get(nm)], types)
                t.links.new(sock, sep.inputs[0])
                for name, targets in rec.items():
                    if targets:
                        _relink(run, t, sep.outputs[kept[name]], targets)
    return len(sets), g


def _zero(v):
    if v is None:
        return True
    vals = v if hasattr(v, "__len__") else (v,)
    return all(abs(float(x)) < 1e-9 for x in vals)


def _contents(sock, run, ctx=(), depth=0):
    """{item: value, or None where computed} that a bundle socket carries.

    Traces back through Combine/Join Bundles, group nodes (into their Group Output) and Group Inputs
    (out to the calling group node, ctx). None when unknown (e.g. a bundle given to a group)."""
    if depth > 64:
        return None
    n = sock.node
    if n.bl_idname == "NodeReroute":
        return _contents(n.inputs[0].links[0].from_socket, run, ctx, depth + 1) if n.inputs[0].is_linked else {}
    if n.bl_idname == "NodeCombineBundle":
        p = run.pending.get(n.as_pointer())
        if p is not None:
            return {name: (None if src is not None else v) for name, _kind, src, v in p.view()}
        return {s.name: (None if s.is_linked else _value(s)) for s in n.inputs if s.name and s.identifier != "__extend__"}
    if n.bl_idname == "NodeJoinBundle":
        out = {}
        # later links win, merged in order
        for l in sorted(n.inputs[0].links, key=lambda l: l.multi_input_sort_id):
            c = _contents(l.from_socket, run, ctx, depth + 1)
            if c is None:
                return None
            out.update(c)
        return out
    if n.bl_idname == "ShaderNodeGroup" and n.node_tree is not None:
        go = next((x for x in n.node_tree.nodes if x.bl_idname == "NodeGroupOutput" and x.is_active_output), None)
        inner = next((x for x in go.inputs if x.identifier == sock.identifier), None) if go else None
        if inner is None or not inner.is_linked:
            return {}
        return _contents(inner.links[0].from_socket, run, ctx + (n,), depth + 1)
    if n.bl_idname == "NodeGroupInput":
        if not ctx:
            return None
        caller = ctx[-1]
        given = next((x for x in caller.inputs if x.identifier == sock.identifier), None)
        if given is None or not given.is_linked:
            return {}
        return _contents(given.links[0].from_socket, run, ctx[:-1], depth + 1)
    return None


def _same(a, b):
    va = a if hasattr(a, "__len__") else (a,)
    vb = b if hasattr(b, "__len__") else (b,)
    n = min(len(va), len(vb), 3)
    return all(abs(float(va[i]) - float(vb[i])) < 1e-6 for i in range(n))


MISSING = object()


def _collapse(trees, run):
    """Replace a Combine Bundle that regathers a module's bundle (via its Separate Bundle) with that bundle itself.

    Differing items are joined over it as a "set" bundle. The Separate is removed once nothing else reads it.
    A _Pending node kept is filled here."""
    n_done = 0
    # A module's own output first (what it passes through rides its input bundle; only what it sets is set
    # over it, like FP's SetMaterialAttribute), then where modules are used, so callers see what the module passes on.
    for kind, t in [(k, t) for k in ("out", "use_in") for t in trees]:
        for comb in [n for n in t.nodes if n.get(TAG) == kind]:
            p = run.pending.get(comb.as_pointer())
            if p is not None:
                items = p.view()
            else:       # one an earlier import bundled
                items = [(s.name, s.type, s.links[0].from_socket if s.is_linked else None, _value(s))
                         for s in comb.inputs if s.name and s.identifier != "__extend__" and s.enabled]
            if _collapse_one(t, comb, items, run, p):
                n_done += 1
            elif p is not None:
                p.fill(run)
    return n_done


def _collapse_one(t, comb, items, run, p):
    """_collapse for one Combine Bundle; items: [(name, socket type, output it reads or None, value)]."""
    counts = {}
    for name, _kind, src, _v in items:
        if src is not None:
            if src.node.bl_idname == "NodeSeparateBundle" and src.node.get(TAG) in ("use_out", "in") and src.name == name:
                counts[src.node] = counts.get(src.node, 0) + 1
    if not counts:
        return False
    source = max(counts, key=counts.get)
    if counts[source] < 2 or not source.inputs[0].is_linked:
        return False
    bundle = source.inputs[0].links[0].from_socket
    made = _contents(bundle, run)
    strict = made is None       # items unknown: set every value over the bundle
    made = made or {}
    overrides = []
    for name, kind, src, v in items:
        if src is not None:
            if src.node == source and src.name == name:
                continue
            overrides.append((name, kind, src, None))
            continue
        pv = made.get(name, MISSING)
        if strict:
            pass
        elif pv is MISSING:
            if _zero(v):
                continue        # missing from the bundle reads as 0, the same
        elif pv is not None and _same(v, pv):
            continue
        overrides.append((name, kind, None, v))
    if len(overrides) * 2 > len(items):
        return False
    target = bundle
    if overrides:
        put = t.nodes.new("NodeCombineBundle")
        put.label, put[TAG] = "set", "set"
        put.location = (comb.location.x, comb.location.y - 120)
        for name, kind, src, v in overrides:
            put.bundle_items.new(ITEM_OF.get(name, ITEM_TYPE.get(kind, 'FLOAT')), name)
        for name, kind, src, v in overrides:
            if src is not None:
                t.links.new(src, put.inputs[name])
            else:
                _set(put.inputs[name], v)
        join = t.nodes.new("NodeJoinBundle")
        join.label, join[TAG] = comb.label, "join"
        join.location = comb.location
        # bundle first, then what's set over it: the later link wins
        t.links.new(bundle, join.inputs[0])
        t.links.new(put.outputs[0], join.inputs[0])
        target = join.outputs[0]
    for l in list(comb.outputs[0].links):
        t.links.new(target, l.to_socket)
    if p is not None:
        run.drop(p)
    t.nodes.remove(comb)
    # (what a _Pending node reads counts as read)
    if not any(o.is_linked for o in source.outputs) and not run.read(t, source):
        t.nodes.remove(source)
    return True


def _share_parameters(trees):
    """Merge, per tree, the modules' Parameters bundles that only gather the tree's own Group Input values.

    One Parameters Combine Bundle next to the Group Input feeds each module; items a module takes elsewhere are set over it."""
    n_done = 0
    for t in trees:
        combs = [n for n in t.nodes if n.get(TAG) == "use_in" and n.label == PARAMETERS]
        if len(combs) < 2:
            continue
        sources = {}        # item -> (Group Input socket identifier, type) where all agree
        clash = set()
        for c in combs:
            for s in c.inputs:
                if not s.name or s.identifier == "__extend__" or not s.is_linked:
                    continue
                src = s.links[0].from_socket
                if src.node.bl_idname != "NodeGroupInput":
                    continue
                key = (src.identifier, ITEM_TYPE.get(s.type, 'FLOAT'))
                if sources.get(s.name, key) != key:
                    clash.add(s.name)
                sources.setdefault(s.name, key)
        for name in clash:
            sources.pop(name, None)
        if len(sources) < 2:
            continue
        gi = next(n for n in t.nodes if n.bl_idname == "NodeGroupInput")
        shared = t.nodes.new("NodeCombineBundle")
        shared.label, shared[TAG] = PARAMETERS, "shared"
        shared.location = (gi.location.x + 220, gi.location.y)
        shared.bundle_items.clear()
        for name in sorted(sources):
            shared.bundle_items.new(sources[name][1], name)
        for name, (ident, _kind) in sources.items():
            out = next((o for o in gi.outputs if o.identifier == ident), None)
            if out is not None:
                t.links.new(out, shared.inputs[name])
        for c in combs:
            overrides = []
            for s in c.inputs:
                if not s.name or s.identifier == "__extend__" or not s.enabled:
                    continue
                if s.is_linked:
                    src = s.links[0].from_socket
                    if src.node.bl_idname == "NodeGroupInput" and s.name in sources and sources[s.name][0] == src.identifier:
                        continue
                    overrides.append((s, src, None))
                elif s.name in sources or not _zero(_value(s)):
                    overrides.append((s, None, _value(s)))
            target = shared.outputs[0]
            if overrides:
                put = t.nodes.new("NodeCombineBundle")
                put.label, put[TAG] = "set", "set"
                put.location = (c.location.x, c.location.y - 120)
                put.bundle_items.clear()
                for s, src, v in overrides:
                    put.bundle_items.new(ITEM_TYPE.get(s.type, 'FLOAT'), s.name)
                for s, src, v in overrides:
                    if src is not None:
                        t.links.new(src, put.inputs[s.name])
                    else:
                        _set(put.inputs[s.name], v)
                join = t.nodes.new("NodeJoinBundle")
                join.label, join[TAG] = PARAMETERS, "join"
                join.location = c.location
                t.links.new(shared.outputs[0], join.inputs[0])
                t.links.new(put.outputs[0], join.inputs[0])
                target = join.outputs[0]
            for l in list(c.outputs[0].links):
                t.links.new(target, l.to_socket)
            t.nodes.remove(c)
            n_done += 1
    return n_done


def _all_trees():
    return [m.node_tree for m in bpy.data.materials if m.node_tree is not None] + list(bpy.data.node_groups)


def bundle_attributes(groups, key_fp=None, known=None):
    """Bundle the attribute sockets of the given function groups (an import's, after pruning).

    key_fp/known: the sharing fingerprint key and its cache (build.KEY_FP, build._KNOWN); a bundled group
    no longer stands for its unbundled fingerprint. Returns (sets bundled, wires joined)."""
    trees = in_scope(_all_trees(), scope(groups))
    users = _users(trees)
    made = 0
    run = _Run()
    replaced = []       # (group, its copy): removed and renamed once the pass is done
    try:
        for g in groups:
            try:
                old = g.get(key_fp) if key_fp else None
                was_known = old is not None and known is not None and known.get(old) == g
                n, now = _bundle_group(g, users, run, trees)
            except ReferenceError:
                continue
            if now is not g:
                replaced.append((g, now))
            if n and old is not None:
                now[KEY_FROM] = old
                now[key_fp] = old + "+bundled"
                if was_known:
                    del known[old]
            made += n
        joined = _collapse(trees, run) if made else 0
    finally:
        run.close()
        if replaced:
            names = [g.name for g, _c in replaced]
            bpy.data.batch_remove([g for g, _c in replaced])
            for (_g, c), name in zip(replaced, names):
                c.name = name
    shared = _share_parameters(trees) if made else 0
    return made, joined + shared
