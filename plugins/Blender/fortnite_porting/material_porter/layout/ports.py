"""Frame ports and fan-outs, and the sockets a laid-out node hides."""

from collections import defaultdict

from .metrics import STAND_IN, UNUSED
from .wires import _Wire, _adjacency, _clone, _in, _is_source, _out, _ranks, _relink, _scan


def _split_fanouts(tree, tags, wires):
    """Split a Separate Bundle read across many columns, which would send a wire per item the whole way.

    Each column of readers (a frame's nodes equally far from the output) gets its own copy right before it,
    showing only what that column reads, and only the bundle travels (one wire shared by every copy).
    Readers through a multi-input socket keep the original (a Join reads its links in order); one fed by a
    Group Input is a pure input, copied per frame later (_localize_sources).

    Returns the copies made."""
    ins, outs = _adjacency(wires)
    rank = _ranks(tree, outs)
    made = 0
    for n in [n for n in tree.nodes if n.bl_idname == "NodeSeparateBundle" and STAND_IN not in n]:
        if len(ins[n.name]) != 1 or n.name not in tags or _is_source(n, ins):
            continue
        groups = defaultdict(list)
        for w in outs[n.name]:
            if w.b.name in tags and not _in(w.b, w.ib).is_multi_input:
                groups[(tags[w.b.name], rank[w.b.name])].append(w)
        if len(groups) < 2:
            continue
        feed = ins[n.name][0]
        idents = [o.identifier for o in n.outputs]
        # the nearest column keeps the original
        for k in sorted(groups, key=lambda k: -k[1])[1:]:
            c = _clone(tree, n)
            tags[c.name] = k[0]
            tree.links.new(_out(feed.a, feed.ia), c.inputs[0])
            for w in groups[k]:
                tree.links.new(c.outputs[idents.index(w.ia)], _in(w.b, w.ib))
            made += 1
    return made


def _realize_inputs(tree, stand_ins):
    """Swap every stand-in for the Group Input it stands for."""
    N = tree.nodes
    feeds = defaultdict(list)
    for w in _scan(tree):
        if w.a.name in stand_ins:
            feeds[w.a.name].append(w)
    for name, idents in stand_ins.items():
        ph = N[name]
        gi = N.new("NodeGroupInput")
        gi.parent = ph.parent
        gi.location_absolute = ph.location_absolute
        for s in gi.outputs:
            s.hide = s.identifier not in idents
        outs = [s.identifier for s in ph.outputs if s.identifier != "__extend__"]
        for w in feeds[name]:
            _relink(tree, gi, idents[outs.index(w.ia)], w.b, w.ib)
        N.remove(ph)


# ------------------------------------------------------------------ ports
PORT_TYPES = {'VALUE', 'VECTOR', 'RGBA', 'INT', 'BOOLEAN', 'BUNDLE'}
PORT_MARK = "▸ "
EXIT_MARK = " ▸"


def _port_name(node, src):
    """What a port shows: its source's label, marked, so lookups by exact label (passes, tests) never take a port for its node."""
    name = node.label or node.name
    if name.startswith(PORT_MARK):
        name = name[len(PORT_MARK):]
    if name.endswith(EXIT_MARK):
        name = name[:-len(EXIT_MARK)]
    if node.bl_idname != "NodeReroute" and sum(1 for o in node.outputs if o.enabled) > 1:
        name += " · " + src.name
    return name


def _ports(tree, tags, wires):
    """Reroutes where a signal crosses a frame's edge, UE comment-box style.

    Exits first, inside out. A value made deep inside a frame and read outside would leave from wherever its node
    sits and cut across the rest of the frame. When the part that makes it also feeds something else in the frame
    (so the layering can't put it on the right edge by itself) it goes out through a reroute that sits there.

    Then entries, outside in. A value read by several nodes inside a frame arrives as one wire at a labelled
    reroute on the frame's left edge and fans out from there. So does a value read by one node that sits behind
    something else in the frame. A sub-frame gets its own entry, fed from its parent's.

    Pure inputs get neither (they are about to be copied into the frame). This runs before those copies exist,
    while link edits are still cheap. Returns {reroute name: "first" | "last"}, the column each one is pinned to.
    """
    L = tree.links
    pins = {}
    ins, _ = _adjacency(wires)
    # nor a wire into a multi-input socket (a Join reads its links in order; a relink there replaces them all)
    live = [w for w in wires if not _is_source(w.a, ins) and not _in(w.b, w.ib).is_multi_input]
    paths = {p[:k] for p in tags.values() if p for k in range(1, len(p) + 1)}
    paths = [p for p in paths if p[0] != UNUSED]

    def item(path, depth, node):
        return path[depth] if len(path) > depth else node.name

    def reroute(path, label, pin):
        r = tree.nodes.new("NodeReroute")
        r.label = label
        tags[r.name] = path
        pins[r.name] = pin
        return r

    def carry(group, r):
        """Feed `r` from the group's source and the group's readers from `r`."""
        node, ia, type_ = group[0].a, group[0].ia, group[0].type
        link = L.new(_out(node, ia), r.inputs[0])
        made = [_Wire(link, node, ia, type_, r, link.to_socket.identifier)]
        r_out = r.outputs[0].identifier
        for w in group:
            link = _relink(tree, r, r_out, w.b, w.ib)
            made.append(_Wire(link, r, r_out, type_, w.b, w.ib))
        return made

    for path in sorted(paths, key=lambda p: (-len(p), p)):
        depth = len(path)
        busy, groups = set(), defaultdict(list)
        for w in live:
            pu, pv = tags[w.a.name], tags[w.b.name]
            if pu[:depth] != path:
                continue
            if pv[:depth] == path:
                if item(pu, depth, w.a) != item(pv, depth, w.b):
                    busy.add(item(pu, depth, w.a))
            elif w.type in PORT_TYPES:
                groups[(w.a.name, w.ia)].append(w)
        gone, made = set(), []
        for group in groups.values():
            if item(tags[group[0].a.name], depth, group[0].a) not in busy:
                continue
            src = _out(group[0].a, group[0].ia)
            r = reroute(path, _port_name(group[0].a, src) + EXIT_MARK, "last")
            gone.update(id(w) for w in group)
            made += carry(group, r)
        live = [w for w in live if id(w) not in gone] + made

    for path in sorted(paths, key=lambda p: (len(p), p)):
        depth = len(path)
        fed, groups = set(), defaultdict(list)
        for w in live:
            pu, pv = tags[w.a.name], tags[w.b.name]
            if pv[:depth] != path:
                continue
            if pu[:depth] == path:
                if item(pu, depth, w.a) != item(pv, depth, w.b):
                    fed.add(item(pv, depth, w.b))
                continue
            if pu[:1] == (UNUSED,) or w.type not in PORT_TYPES:
                continue
            groups[(w.a.name, w.ia)].append(w)
        gone, made = set(), []
        for group in groups.values():
            # a reader on the frame's left edge can take the wire directly; one further in would have it cross
            # whatever sits in front
            deep = any(item(tags[w.b.name], depth, w.b) in fed for w in group)
            if len(group) < 2 and not deep:
                continue
            src = _out(group[0].a, group[0].ia)
            r = reroute(path, PORT_MARK + _port_name(group[0].a, src), "first")
            gone.update(id(w) for w in group)
            made += carry(group, r)
        live = [w for w in live if id(w) not in gone] + made
    return pins


# Nodes whose whole content is their wiring, when every input is wired: drawn collapsed (a row each) so they
# stop taking the height of their socket list. A Separate/Combine Color in a mode other than RGB, or a node
# with a typed-in value, stays expanded.
COLLAPSIBLE = {"ShaderNodeSeparateXYZ", "ShaderNodeCombineXYZ", "ShaderNodeSeparateColor",
               "ShaderNodeCombineColor", "NodeEvaluateClosure"}


def _collapse_trivial(tree, wires):
    fed = {(w.b.name, w.ib) for w in wires}
    for n in tree.nodes:
        if n.bl_idname not in COLLAPSIBLE or getattr(n, "mode", 'RGB') != 'RGB':
            continue
        if all((n.name, s.identifier) in fed for s in n.inputs if s.enabled and s.identifier != "__extend__"):
            n.hide = True


def _hide_unused_outputs(tree, wires):
    used = {(w.a.name, w.ia) for w in wires}
    for n in tree.nodes:
        if n.bl_idname in ("NodeFrame", "NodeReroute", "NodeGroupOutput"):
            continue
        outs = [s for s in n.outputs if s.enabled]
        if len(outs) < 2 and n.type != 'GROUP_INPUT':
            continue
        for s in outs:
            s.hide = (n.name, s.identifier) not in used


# A constant on an operator is part of what it does (Multiply by 0.5 is not Multiply), so these show every input
OPERATORS = {"ShaderNodeMath", "ShaderNodeVectorMath", "ShaderNodeMix", "ShaderNodeMixRGB",
             "FunctionNodeCompare", "ShaderNodeClamp", "ShaderNodeMapRange", "FunctionNodeBooleanMath",
             "FunctionNodeIntegerMath", "ShaderNodeMixShader", "ShaderNodeVectorRotate"}
_DEFAULTS = {}      # (tree type, node type) -> {input id: default}


def _same(a, b):
    try:
        return all(abs(x - y) < 1e-6 for x, y in zip(a, b)) and len(a) == len(b)
    except TypeError:
        pass
    try:
        return abs(a - b) < 1e-6
    except TypeError:
        return a == b


def _defaults(tree, n):
    """A node's input defaults: a group's from its interface, a built-in's from a fresh node (made once per type in a scratch tree)."""
    if n.bl_idname == "ShaderNodeGroup" or n.type == 'GROUP':
        if n.node_tree is None:
            return {}
        return {it.identifier: getattr(it, "default_value", None)
                for it in n.node_tree.interface.items_tree
                if it.item_type == 'SOCKET' and it.in_out == 'INPUT'}
    key = (tree.bl_idname, n.bl_idname)
    if key not in _DEFAULTS:
        import bpy
        scratch = bpy.data.node_groups.new(".layout defaults", tree.bl_idname)
        try:
            ref = scratch.nodes.new(n.bl_idname)
            _DEFAULTS[key] = {i.identifier: (tuple(i.default_value) if hasattr(i.default_value, "__len__")
                                             and not isinstance(i.default_value, str) else i.default_value)
                              for i in ref.inputs if hasattr(i, "default_value")}
        except RuntimeError:
            _DEFAULTS[key] = {}
        bpy.data.node_groups.remove(scratch)
    return _DEFAULTS[key]


def _hide_idle_inputs(tree, wires):
    """Ctrl+H where nothing is lost: hide unlinked inputs still at their default (and ones with no value),
    except on operators and in a material's own tree, whose group node inputs are the material's controls."""
    if tree.is_embedded_data:
        return
    fed = {(w.b.name, w.ib) for w in wires}
    for n in tree.nodes:
        if n.bl_idname in OPERATORS or n.type in ('FRAME', 'REROUTE', 'GROUP_INPUT', 'GROUP_OUTPUT') \
                or n.hide:
            continue
        defaults = None
        for i in n.inputs:
            if not i.enabled or i.hide or (n.name, i.identifier) in fed or i.identifier == "__extend__":
                continue
            if defaults is None:
                defaults = _defaults(tree, n)
            if not hasattr(i, "default_value"):
                i.hide = True
                continue
            if i.identifier not in defaults or defaults[i.identifier] is None:
                continue
            v = i.default_value
            v = tuple(v) if hasattr(v, "__len__") and not isinstance(v, str) else v
            if _same(v, defaults[i.identifier]):
                i.hide = True
