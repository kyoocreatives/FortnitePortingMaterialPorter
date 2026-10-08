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
        if hasattr(sock.default_value, "__len__"):
            n = len(sock.default_value)
            vals = tuple(value) if hasattr(value, "__len__") else (value,) * n
            sock.default_value = (vals + (1.0,) * n)[:n]
        else:
            sock.default_value = value[0] if hasattr(value, "__len__") else value
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


def _users(group, trees):
    return [(t, n) for t in trees for n in t.nodes if n.bl_idname == "ShaderNodeGroup" and n.node_tree == group]


def _items(node, names, types):
    """Create one bundle item per name. Returns {name: item name as Blender stores it} (it rewrites "(" and ":")."""
    node.bundle_items.clear()
    for name in names:
        node.bundle_items.new(types[name], name)
    return {name: node.bundle_items[i].name for i, name in enumerate(names)}


def _bundle_group(group, trees):
    """Group's attribute socket sets as bundles, inside and where it is used. How many sets."""
    ins, outs = _attribute_sets(group, 'INPUT'), _attribute_sets(group, 'OUTPUT')
    if not ins and not outs:
        return 0
    users = _users(group, trees)
    iface = group.interface
    made = 0
    for in_out, sets in (('INPUT', ins), ('OUTPUT', outs)):
        for prefix, pairs in sets.items():
            socks = [it for it, _name in pairs]
            idents = {it.identifier: name for it, name in pairs}
            names = [name for _it, name in pairs]
            # record what the group nodes' sockets carry (links, values) before removing them
            types, recorded = {}, []
            for t, n in users:
                rec = {}
                sockets = n.inputs if in_out == 'INPUT' else n.outputs
                for s in sockets:
                    if s.identifier not in idents:
                        continue
                    name = idents[s.identifier]
                    types.setdefault(name, _item_type(s))
                    if in_out == 'INPUT':
                        rec[name] = (_ref(s.links[0].from_socket) if s.is_linked else None, _value(s))
                    else:
                        rec[name] = [_ref(l.to_socket) for l in s.links]
                recorded.append((t, n, rec))
            # and inside: Group Input links out of those sockets, Group Output links into them
            inside = []
            for n in group.nodes:
                if in_out == 'INPUT' and n.bl_idname == "NodeGroupInput":
                    for s in n.outputs:
                        if s.identifier in idents and s.is_linked:
                            types.setdefault(idents[s.identifier], _item_type(s))
                            inside.append((n, idents[s.identifier], [_ref(l.to_socket) for l in s.links]))
                elif in_out == 'OUTPUT' and n.bl_idname == "NodeGroupOutput":
                    for s in n.inputs:
                        if s.identifier in idents:
                            types.setdefault(idents[s.identifier], _item_type(s))
                            inside.append((n, idents[s.identifier], (_ref(s.links[0].from_socket) if s.is_linked else None, _value(s))))
            for name in names:
                types[name] = ITEM_OF.get(name, types.get(name, 'FLOAT'))
            # the bundle socket takes the first socket's place
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
            # inside the group
            if in_out == 'INPUT':
                by_gi = {}
                for gi, name, targets in inside:
                    sep = by_gi.get(gi.name)
                    if sep is None:
                        sep = group.nodes.new("NodeSeparateBundle")
                        sep.label, sep[TAG] = prefix, "in"
                        sep.location = (gi.location.x + 60, gi.location.y)
                        kept = _items(sep, names, types)
                        group.links.new(next(o for o in gi.outputs if o.identifier == ident), sep.inputs[0])
                        by_gi[gi.name] = sep
                    out = sep.outputs[kept[name]]
                    for target in targets:
                        _link(group, out, ref_in=target)
            else:
                outs_by_node = {}
                for go, name, (src, value) in inside:
                    comb = outs_by_node.get(go.name)
                    if comb is None:
                        comb = group.nodes.new("NodeCombineBundle")
                        comb.label, comb[TAG] = prefix, "out"
                        comb.location = (go.location.x - 160, go.location.y)
                        kept = _items(comb, names, types)
                        group.links.new(comb.outputs[0], next(i for i in go.inputs if i.identifier == ident))
                        outs_by_node[go.name] = comb
                        for extra, kind, default in COMPLETE:
                            if extra not in names:
                                comb.bundle_items.new(kind, extra)
                                _set(comb.inputs[extra], default)
                    if src is not None:
                        _link(group, None, ref_out=src, into=comb.inputs[kept[name]])
                    else:
                        _set(comb.inputs[kept[name]], value)
            # where the group is used
            for t, n, rec in recorded:
                sock = next(s for s in (n.inputs if in_out == 'INPUT' else n.outputs) if s.identifier == ident)
                if in_out == 'INPUT':
                    comb = t.nodes.new("NodeCombineBundle")
                    comb.label, comb[TAG] = prefix, "use_in"
                    comb.location = (n.location.x - 180, n.location.y)
                    kept = _items(comb, names, types)
                    for name, (src, value) in rec.items():
                        if src is not None:
                            _link(t, None, ref_out=src, into=comb.inputs[kept[name]])
                        else:
                            _set(comb.inputs[kept[name]], value)
                    t.links.new(comb.outputs[0], sock)
                else:
                    if not any(rec.values()):
                        continue
                    sep = t.nodes.new("NodeSeparateBundle")
                    sep.label, sep[TAG] = prefix, "use_out"
                    sep.location = (n.location.x + n.width + 40, n.location.y)
                    kept = _items(sep, [nm for nm in names if rec.get(nm)], types)
                    t.links.new(sock, sep.inputs[0])
                    for name, targets in rec.items():
                        for target in targets:
                            _link(t, sep.outputs[kept[name]], ref_in=target)
            made += 1
    return made


def _zero(v):
    if v is None:
        return True
    vals = v if hasattr(v, "__len__") else (v,)
    return all(abs(float(x)) < 1e-9 for x in vals)


def _contents(sock, ctx=(), depth=0):
    """{item: value, or None where computed} that a bundle socket carries.

    Traces back through Combine/Join Bundles, group nodes (into their Group Output) and Group Inputs
    (out to the calling group node, ctx). None when unknown (e.g. a bundle given to a group)."""
    if depth > 64:
        return None
    n = sock.node
    if n.bl_idname == "NodeReroute":
        return _contents(n.inputs[0].links[0].from_socket, ctx, depth + 1) if n.inputs[0].is_linked else {}
    if n.bl_idname == "NodeCombineBundle":
        return {s.name: (None if s.is_linked else _value(s)) for s in n.inputs if s.name and s.identifier != "__extend__"}
    if n.bl_idname == "NodeJoinBundle":
        out = {}
        # later links win, merged in order
        for l in sorted(n.inputs[0].links, key=lambda l: l.multi_input_sort_id):
            c = _contents(l.from_socket, ctx, depth + 1)
            if c is None:
                return None
            out.update(c)
        return out
    if n.bl_idname == "ShaderNodeGroup" and n.node_tree is not None:
        go = next((x for x in n.node_tree.nodes if x.bl_idname == "NodeGroupOutput" and x.is_active_output), None)
        inner = next((x for x in go.inputs if x.identifier == sock.identifier), None) if go else None
        if inner is None or not inner.is_linked:
            return {}
        return _contents(inner.links[0].from_socket, ctx + (n,), depth + 1)
    if n.bl_idname == "NodeGroupInput":
        if not ctx:
            return None
        caller = ctx[-1]
        given = next((x for x in caller.inputs if x.identifier == sock.identifier), None)
        if given is None or not given.is_linked:
            return {}
        return _contents(given.links[0].from_socket, ctx[:-1], depth + 1)
    return None


def _same(a, b):
    va = a if hasattr(a, "__len__") else (a,)
    vb = b if hasattr(b, "__len__") else (b,)
    n = min(len(va), len(vb), 3)
    return all(abs(float(va[i]) - float(vb[i])) < 1e-6 for i in range(n))


MISSING = object()


def _collapse(trees):
    """Replace a Combine Bundle that regathers a module's bundle (via its Separate Bundle) with that bundle itself.

    Differing items are joined over it as a "set" bundle. The Separate is removed once nothing else reads it."""
    n_done = 0
    # A module's own output first (what it passes through rides its input bundle; only what it sets is set
    # over it, like FP's SetMaterialAttribute), then where modules are used, so callers see what the module passes on.
    for kind, t in [(k, t) for k in ("out", "use_in") for t in trees]:
        for comb in [n for n in t.nodes if n.get(TAG) == kind]:
            items = [s for s in comb.inputs if s.name and s.identifier != "__extend__" and s.enabled]
            counts = {}
            for s in items:
                if s.is_linked:
                    src = s.links[0].from_socket
                    if src.node.bl_idname == "NodeSeparateBundle" and src.node.get(TAG) in ("use_out", "in") and src.name == s.name:
                        counts[src.node] = counts.get(src.node, 0) + 1
            if not counts:
                continue
            source = max(counts, key=counts.get)
            if counts[source] < 2 or not source.inputs[0].is_linked:
                continue
            made = _contents(source.inputs[0].links[0].from_socket)
            strict = made is None       # items unknown: set every value over the bundle
            made = made or {}
            overrides = []
            for s in items:
                if s.is_linked:
                    src = s.links[0].from_socket
                    if src.node == source and src.name == s.name:
                        continue
                    overrides.append((s, src, None))
                    continue
                v = _value(s)
                pv = made.get(s.name, MISSING)
                if strict:
                    pass
                elif pv is MISSING:
                    if _zero(v):
                        continue        # missing from the bundle reads as 0, the same
                elif pv is not None and _same(v, pv):
                    continue
                overrides.append((s, None, v))
            if len(overrides) * 2 > len(items):
                continue
            bundle = source.inputs[0].links[0].from_socket
            target = bundle
            if overrides:
                put = t.nodes.new("NodeCombineBundle")
                put.label, put[TAG] = "set", "set"
                put.location = (comb.location.x, comb.location.y - 120)
                put.bundle_items.clear()
                for s, src, v in overrides:
                    put.bundle_items.new(ITEM_OF.get(s.name, ITEM_TYPE.get(s.type, 'FLOAT')), s.name)
                for s, src, v in overrides:
                    if src is not None:
                        t.links.new(src, put.inputs[s.name])
                    else:
                        _set(put.inputs[s.name], v)
                join = t.nodes.new("NodeJoinBundle")
                join.label, join[TAG] = comb.label, "join"
                join.location = comb.location
                # bundle first, then what's set over it: the later link wins
                t.links.new(bundle, join.inputs[0])
                t.links.new(put.outputs[0], join.inputs[0])
                target = join.outputs[0]
            for l in list(comb.outputs[0].links):
                t.links.new(target, l.to_socket)
            t.nodes.remove(comb)
            if not any(o.is_linked for o in source.outputs):
                t.nodes.remove(source)
            n_done += 1
    return n_done


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
    trees = _all_trees()
    made = 0
    for g in groups:
        try:
            n = _bundle_group(g, trees)
        except ReferenceError:
            continue
        if n and key_fp and key_fp in g:
            old = g[key_fp]
            g[KEY_FROM] = old
            g[key_fp] = old + "+bundled"
            if known is not None and known.get(old) == g:
                del known[old]
        made += n
    joined = _collapse(trees) if made else 0
    shared = _share_parameters(trees) if made else 0
    return made, joined + shared
