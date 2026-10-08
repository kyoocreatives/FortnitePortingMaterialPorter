"""Lays a built node group out as framed, left-to-right sections, derived from the finished graph.

  * Every node carries the section the builder was in when it made it (nodelib's `sec()`).
    A section is a frame, nested along its path: "Reaper/Matcap UV" is a Matcap UV frame inside a Reaper frame.
  * Inside a frame nodes are layered left to right by dataflow, ordered to avoid wire crossings and nudged
    so wires run straight (the usual Sugiyama recipe). A frame is laid out before its parent and placed there
    as one block, ports and all.
  * Pure input nodes (Group Input, Geometry, Texture Coordinate, the bundle split, ...) are copied into every
    frame that reads them (like UE's local parameter nodes) so no wire crosses the whole graph to fetch a socket.
  * Unused outputs are hidden; nodes that no longer reach the output (the retired fade terms, kept on purpose,
    see sphere_only.simplify_fade) are parked in an "Unused" frame underneath.

It changes where nodes sit and what they draw, never what they compute. It must run last, after every insertion
pass: those find their anchors by label and neighbour and are gated by tree markers, so they never rerun on a laid-out group.

Node sizes are estimates: Blender only measures a node when it draws it and the build runs in the background.
The table below was measured in a UI session (tools/layout_measure.py), good to a few pixels.
"""
import colorsys
import bisect
import heapq
import zlib
from collections import Counter, defaultdict

from .nodelib import SECTION_KEY

UNUSED = "Unused"

# ------------------------------------------------------------------ metrics
# Height of a node with no visible sockets, by type (and data type where that changes the buttons drawn).
# Each visible socket row adds ROW; an unlinked vector input draws three fields and adds VEC instead.
ROW = 22.4
VEC = 84.0
BASE_H = {
    "ShaderNodeMath": 81.6,
    ("ShaderNodeMix", "RGBA"): 131.2,
    ("ShaderNodeMix", "FLOAT"): 81.6,
    ("ShaderNodeMix", "VECTOR"): 106.4,
    ("ShaderNodeMapRange", "FLOAT"): 81.6,
    ("ShaderNodeMapRange", "FLOAT_VECTOR"): 106.4,
    "ShaderNodeVectorMath": 56.8,
    "ShaderNodeCombineColor": 54.4,
    "ShaderNodeSeparateColor": 54.4,
    "ShaderNodeCombineXYZ": 29.6,
    "ShaderNodeSeparateXYZ": 29.6,
    "NodeEvaluateClosure": 52.0,
    "ShaderNodeVectorTransform": 104.0,
    "ShaderNodeHueSaturation": 29.6,
    "ShaderNodeNormalMap": 128.8,
    "ShaderNodeTexWhiteNoise": 54.4,
    "NodeGroupInput": 17.6,
    "NodeGroupOutput": 26.4,
    "NodeSeparateBundle": 14.4,
    "NodeCombineBundle": 9.6,
    "ShaderNodeNewGeometry": 20.8,
    "ShaderNodeCameraData": 27.2,
    "ShaderNodeObjectInfo": 27.2,
    "ShaderNodeVectorRotate": 54.4,
    "ShaderNodeTexCoord": 76.8,
    "ShaderNodeValue": 27.2,
    "ShaderNodeUVMap": 81.6,
    "ShaderNodeTangent": 81.6,
    "ShaderNodeRGB": 128.8,
    "ShaderNodeTexVoronoi": 123.2,
    "ShaderNodeRGBToBW": 29.6,
    "ShaderNodeGamma": 3.2,
    "GeometryNodeMenuSwitch": 60.0,
    "NodeReroute": 0.0,
    # the sky's (tools/build_sky.py, build_fog.py, build_grade.py): generous estimates, since a gap reads better than an overlap
    "ShaderNodeFloatCurve": 262.0,
    "ShaderNodeTexImage": 206.0,
    "ShaderNodeAttribute": 58.0,
    "ShaderNodeVertexColor": 32.0,
    "ShaderNodeGroup": 32.0,
}
BASE_DEFAULT = 60.0
HEAD = 24.0     # node top to the first output row
FOOT = 6.0      # last input row to the node bottom

# Frames: Blender draws a shrink-wrapped frame this far outside its children, plus the label on top.
# Kept a little generous so a frame never reaches into its neighbour.
PAD = 34.0
LABEL = {1: 34, 2: 22}          # label_size by depth; deeper reuses the last
GAP_NODE_Y = 45.0
GAP_BOX_Y = 90.0
GAP_DUMMY_Y = 20.0
GAP_LANE_BOX = 40.0      # a lane or a loose node beside a frame
LANE_LIMIT = 160         # reroutes a tree's lanes may make, the most useful first; past it, plain wires
LANE_WORK = 40000        # ...and reroutes times the tree's nodes (each new node and link costs Blender a pass over the tree)
ROUTE_LONG = 600.0       # a wire longer than this that climbs more than ROUTE_SLANT is routed level (_wire_routes)
ROUTE_SLANT = 40.0
ROUTE_M = 16.0           # a routed wire's clearance from nodes and frames
ROUTE_SEP = 14.0         # ...and from another routed wire running level
ROUTE_BEND_W = 50.0      # the narrowest gap a bend takes
ROUTE_BEND = 100.0       # a bend's cost, in level pixels x 1000
ROUTE_SPAN = 400.0       # how far above and below its ends a route may go
ROUTE_GRID = 40000       # a route's grid past this: the plain wire
ROUTE_SNAP = 8.0         # grid lines closer than this merge
ROUTE_LANES = True
SPLIT_FANOUTS = True
HIDE_IDLE_INPUTS = True  # unlinked inputs still at their default hidden, Ctrl+H style (_hide_idle_inputs)
PULL_UP = True           # rows rise into the free space above them (_pull_up)
ALIGN = "top"            # what a node lines up with its neighbours by: "top", "socket", "centre"
GAP_PART_Y = 110.0       # between packed rows that don't wire to each other: parts read apart
GAP_PART_X = 100.0       # the same, sideways
GAP_NODE_X = 70.0
GAP_BOX_X = 130.0

# Frame colours by top-level section. Unlisted ones get a stable hue from their name; sub-frames are a lighter shade of the parent.
PALETTE = {
    "Common": (0.20, 0.20, 0.22),
    "Golden": (0.40, 0.30, 0.07),
    "Shared · Cube Base": (0.10, 0.24, 0.23),
    "Cube": (0.08, 0.30, 0.27),
    "Shared · Billboard Plane": (0.16, 0.19, 0.28),
    "CheatMaster": (0.10, 0.30, 0.10),
    "LootHacker": (0.25, 0.12, 0.36),
    "Reaper": (0.07, 0.11, 0.30),
    "Galaxy": (0.22, 0.09, 0.32),
    "Gem": (0.04, 0.27, 0.35),
    "Holofoil": (0.35, 0.14, 0.30),
    "Quack": (0.36, 0.33, 0.06),
    "Candy": (0.40, 0.11, 0.19),
    "Variant Switch": (0.22, 0.22, 0.24),
    "Variant Fade": (0.26, 0.18, 0.11),
    "Output": (0.18, 0.18, 0.18),
    UNUSED: (0.10, 0.10, 0.10),
}

# Nodes with only outputs whose value doesn't depend on where they sit, so a copy is the same node.
# Value and RGB are not here: they hold a number someone may edit, and editing one copy would be a trap.
# The scene-time Value is the exception, recognised by its driver.
PURE_SOURCES = {
    "NodeGroupInput": (),
    "ShaderNodeNewGeometry": (),
    "ShaderNodeTexCoord": ("object", "from_instancer"),
    "ShaderNodeCameraData": (),
    "ShaderNodeObjectInfo": (),
    "ShaderNodeUVMap": ("uv_map", "from_instancer"),
    "ShaderNodeTangent": ("direction_type", "axis", "uv_map"),
    # a scene value (the hour, Time of Day, a slider) or the dome's paint
    "ShaderNodeAttribute": ("attribute_type", "attribute_name"),
    "ShaderNodeVertexColor": ("layer_name",),
}


# ------------------------------------------------------------------ sizes
def _vis(s):
    return s.enabled and not s.hide


def _tall(s):
    return s.type == 'VECTOR' and not s.is_linked and not s.hide_value


def _base(n):
    if STAND_IN in n:
        return BASE_H["NodeGroupInput"]
    key = (n.bl_idname, getattr(n, "data_type", None))
    if key in BASE_H:
        return BASE_H[key]
    return BASE_H.get(n.bl_idname, BASE_DEFAULT)


HIDDEN_H = 26.0     # a collapsed node: its header only


def node_height(n):
    if n.bl_idname == "NodeReroute":
        return 16.0
    if n.hide:
        return HIDDEN_H
    rows = sum(ROW for s in n.outputs if _vis(s))
    rows += sum(VEC if _tall(s) else ROW for s in n.inputs if _vis(s))
    return _base(n) + rows


def node_width(n):
    return 16.0 if n.bl_idname == "NodeReroute" else float(n.width)


def socket_offset(n, sock):
    """Distance from the node's top edge down to a socket's centre."""
    if n.bl_idname == "NodeReroute":
        return 8.0
    if n.hide:
        return HIDDEN_H * 0.5
    if sock.is_output:
        i = 0
        for s in n.outputs:
            if s == sock:
                return HEAD + ROW * (i + 0.5)
            if _vis(s):
                i += 1
        return HEAD
    below = 0.0
    h = node_height(n)
    for s in reversed(list(n.inputs)):
        if not _vis(s) and s != sock:
            continue
        r = VEC if _tall(s) else ROW
        if s == sock:
            return h - FOOT - below - r + ROW * 0.5
        below += r
    return h * 0.5


# ------------------------------------------------------------------ links
# Blender answers `socket.links` by walking every link in the tree, and any read after a link edit rebuilds
# its topology cache first; on the Variants group a few thousand reads take a minute. So the links are read
# once into plain records, the passes below work on those, and each pass writes its edits back in one go.
#
# Sockets are kept by identifier, not by handle: a reroute rebuilds its sockets when the first link gives it
# a type, and a handle taken before that points at freed memory.
class _Wire:
    __slots__ = ("link", "a", "ia", "type", "b", "ib")

    def __init__(self, link, a, ia, type_, b, ib):
        self.link, self.a, self.ia, self.type, self.b, self.ib = link, a, ia, type_, b, ib


def _out(node, ident):
    return next(s for s in node.outputs if s.identifier == ident)


def _in(node, ident):
    return next(s for s in node.inputs if s.identifier == ident)


def _scan(tree):
    wires = []
    for l in tree.links:
        sa = l.from_socket
        wires.append(_Wire(l, l.from_node, sa.identifier, sa.type,
                           l.to_node, l.to_socket.identifier))
    return wires


def _adjacency(wires):
    ins, outs = defaultdict(list), defaultdict(list)
    for w in wires:
        outs[w.a.name].append(w)
        ins[w.b.name].append(w)
    return ins, outs


# ------------------------------------------------------------------ tags
def _read_tags(tree):
    tags = {}
    for n in tree.nodes:
        if n.bl_idname == "NodeFrame":
            continue
        raw = n.get(SECTION_KEY)
        tags[n.name] = None if raw is None else tuple(p for p in raw.split("/") if p)
    return tags


def _is_source(n, ins):
    if n.bl_idname in PURE_SOURCES:
        return True
    if n.bl_idname == "NodeSeparateBundle":
        feeds = ins.get(n.name, [])
        return bool(feeds) and all(w.a.type == 'GROUP_INPUT' for w in feeds)
    return n.bl_idname == "ShaderNodeValue" and _driver(n) is not None


def _resolve_untagged(tree, tags, wires):
    """Nodes an insertion pass added after the builder finished.

    They go with the node they read from, which is where every pass inserts: the mirror after the
    object-space read, the grade after the variant colour, the cell variation after the LootHacker base.
    """
    ins, outs = _adjacency(wires)
    pending = [n for n in tree.nodes if n.name in tags and tags[n.name] is None]
    while pending:
        rest, moved = [], False
        for n in pending:
            votes = Counter()
            for w in ins[n.name]:
                t = tags.get(w.a.name)
                if t is not None and not _is_source(w.a, ins):
                    votes[t] += 2
            for w in outs[n.name]:
                t = tags.get(w.b.name)
                if t is not None:
                    votes[t] += 1
            if votes:
                tags[n.name] = votes.most_common(1)[0][0]
                moved = True
            else:
                rest.append(n)
        if not moved:
            for n in rest:
                tags[n.name] = ()
            break
        pending = rest


def _park_dead(tree, tags, wires):
    """Tag whatever no longer reaches the output into the Unused frame."""
    ins, _ = _adjacency(wires)
    live = set()
    stack = [n.name for n in tree.nodes if n.type in ('GROUP_OUTPUT', 'OUTPUT_MATERIAL')]
    while stack:
        name = stack.pop()
        if name in live:
            continue
        live.add(name)
        stack.extend(w.a.name for w in ins[name])
    parked = 0
    for n in tree.nodes:
        if n.name not in tags or n.name in live or n.type == 'GROUP_INPUT':
            continue
        top = tags[n.name][0] if tags[n.name] else "Misc"
        tags[n.name] = (UNUSED, top)
        parked += 1
    return parked


# ------------------------------------------------------------------ sources
def _driver(n):
    ad = n.id_data.animation_data
    if ad is None:
        return None
    path = 'nodes["%s"].outputs[0].default_value' % n.name
    return next((fc for fc in ad.drivers if fc.data_path == path), None)


def _clone(tree, src):
    c = tree.nodes.new(src.bl_idname)
    c.label = src.label
    c.width = src.width
    for prop in PURE_SOURCES.get(src.bl_idname, ()):
        setattr(c, prop, getattr(src, prop))
    if src.bl_idname == "NodeSeparateBundle":
        c.bundle_items.clear()
        for it in src.bundle_items:
            c.bundle_items.new(it.socket_type, it.name)
        c.define_signature = src.define_signature
    if src.bl_idname == "ShaderNodeValue":
        c.outputs[0].default_value = src.outputs[0].default_value
        old = _driver(src)
        fc = c.outputs[0].driver_add("default_value")
        fc.driver.type = old.driver.type
        fc.driver.expression = old.driver.expression
        for v in old.driver.variables:
            nv = fc.driver.variables.new()
            nv.name, nv.type = v.name, v.type
            for i, t in enumerate(v.targets):
                nt = nv.targets[i]
                if v.type == 'SINGLE_PROP':
                    nt.id_type = t.id_type
                nt.id = t.id
                nt.data_path = t.data_path
    return c


def _key(n):
    props = tuple(str(getattr(n, p)) for p in PURE_SOURCES.get(n.bl_idname, ()))
    if n.bl_idname == "ShaderNodeValue":
        props = (_driver(n).driver.expression,)
    if n.bl_idname == "NodeSeparateBundle":
        props = tuple((it.name, it.socket_type) for it in n.bundle_items)
    return (n.bl_idname, props)


def _relink(tree, a, ia, b, ib):
    """Point input `ib` of `b` at output `ia` of `a`.

    A new link into a single input replaces the existing one, saving a separate remove
    (every edit costs Blender a tree update).
    """
    sb = _in(b, ib)
    if sb.is_multi_input:
        for l in [l for l in tree.links if l.to_socket == sb]:
            tree.links.remove(l)
    return tree.links.new(_out(a, ia), sb)


# A Group Input carries every interface socket (361 on the Variants group) and the cost of any link edit grows
# with the sockets in the tree; a hundred copies make every later edit ten times slower. So until the last edit
# a copy is stood in for by a Separate Bundle holding only the outputs that copy will show (same height and
# order), and the real Group Input replaces it at the very end (_realize_inputs).
STAND_IN = "fpv4_group_input"


def _out_of(node, ident, stand_ins):
    """Output `ident` of a node, or of the Group Input a stand-in stands for."""
    idents = stand_ins.get(node.name)
    if idents is None:
        return _out(node, ident)
    outs = [s for s in node.outputs if s.identifier != "__extend__"]
    if ident in idents:
        return outs[idents.index(ident)]
    # Wires scanned after localizing name the stand-in's own outputs (Item_N), not the Group Input's (Socket_N).
    # A lane routed out of a stand-in into a nested frame arrives here with one of those; the two never collide.
    return _out(node, ident)


def _stand_in(tree, gi, idents):
    ph = tree.nodes.new("NodeSeparateBundle")
    ph.bundle_items.clear()
    kept = []
    for s in gi.outputs:
        if s.identifier in idents:
            kind = {'VALUE': 'FLOAT', 'CUSTOM': 'FLOAT'}.get(s.type, s.type)
            ph.bundle_items.new(kind, s.name or s.identifier)
            kept.append(s.identifier)
    ph[STAND_IN] = 1
    ph.width = gi.width
    return ph, kept


def _localize_sources(tree, tags, wires):
    """Copy every pure input node into each frame that reads it, and a Group Input to each node that reads it,
    showing only what that node reads: a parameter sits beside its reader (one Group Input feeding a whole frame
    sent dozens of wires across it). A parameter bundle's Separate Bundle goes to each column of its readers with
    a Group Input of its own (one per frame sent its items across the frame).

    Returns (copies made, {stand-in name: Group Input output ids}).
    """
    ins, outs = _adjacency(wires)
    order = sorted((n for n in tree.nodes if n.name in tags and _is_source(n, ins)),
                   key=lambda n: 0 if n.bl_idname == "NodeSeparateBundle" else 1)
    sources = {n.name for n in order}
    # what each frame (or reader) reads from each source, so a stand-in can have exactly those outputs;
    # key (source, path, reader or None)
    stays, moves = set(), defaultdict(list)
    rank = _ranks(tree, outs)
    for src in order:
        own = tags[src.name]
        split = src.bl_idname == "NodeSeparateBundle"
        for w in outs[src.name]:
            if src.type == 'GROUP_INPUT' and w.b.name not in sources:
                moves[(src.name, tags[w.b.name], w.b.name)].append(w)
            elif split and _in(w.b, w.ib).is_multi_input:
                stays.add(src.name)     # a Join reads its links in order; relinking would change it
            elif split:
                moves[(src.name, tags[w.b.name], ("column", rank.get(w.b.name, 0), ins[src.name][0].ia))].append(w)
            elif tags[w.b.name] == own:
                stays.add(src.name)
            else:
                moves[(src.name, tags[w.b.name], None)].append(w)
    # the reader taking most of a Group Input's wires keeps the original
    best = {}
    for k, group in moves.items():
        if k[2] is not None and len(group) > len(moves.get(best.get(k[0]), ())):
            best[k[0]] = k
    for name, k in best.items():
        if name not in stays:
            stays.add(name)
            tags[name] = k[1]
            del moves[k]
    needs = defaultdict(set)
    for (name, path, reader), group in moves.items():
        src = tree.nodes[name]
        if src.type == 'GROUP_INPUT':
            needs[(_key(src), path, reader)].update(w.ia for w in group)
        elif src.bl_idname == "NodeSeparateBundle":
            feed = ins[name][0]
            needs[(_key(feed.a), path, ("split", reader))].add(feed.ia)

    local, stand_ins = {}, {}

    def copy_in(src, path, reader=None):
        k = (_key(src), path, reader)
        if k not in local:
            if src.type == 'GROUP_INPUT':
                c, stand_ins_ids = _stand_in(tree, src, needs[k])
                stand_ins[c.name] = stand_ins_ids
            else:
                c = _clone(tree, src)
            tags[c.name] = path
            local[k] = c
            if src.bl_idname == "NodeSeparateBundle":
                feed = ins[src.name][0]
                gi = copy_in(feed.a, path, ("split", reader))
                tree.links.new(_out_of(gi, feed.ia, stand_ins), c.inputs[0])
        return local[k]

    for (name, path, reader), group in moves.items():
        c = copy_in(tree.nodes[name], path, reader)
        for w in group:
            sb = _in(w.b, w.ib)
            tree.links.new(_out_of(c, w.ia, stand_ins), sb)
    # a parameter bundle's Separate Bundle stays where most of its readers are, with its own Group Input
    # beside it (the first one's could be a frame away)
    for name in sorted(stays):
        n = tree.nodes.get(name)
        if n is None or n.bl_idname != "NodeSeparateBundle" or not ins[name] or ins[name][0].a.type != 'GROUP_INPUT':
            continue
        feed = ins[name][0]
        needs[(_key(feed.a), tags[name], ("split", name))].add(feed.ia)
        gi = copy_in(feed.a, tags[name], ("split", name))
        tree.links.new(_out_of(gi, feed.ia, stand_ins), n.inputs[0])
    # an original nothing reads any more is clutter, but keep the first Group Input, which older code
    # looks the interface up through (a material's own tree has none)
    keep = next((n for n in tree.nodes if n.type == 'GROUP_INPUT'), None)
    for src in order:
        if src != keep and src.name not in stays:
            del tags[src.name]
            tree.nodes.remove(src)
    return len(local), stand_ins


def _ranks(tree, outs):
    """Each node's longest path to an output, in links. Nodes with the same value share a column
    (the layering puts each just before its nearest reader)."""
    rank = {}
    for n in tree.nodes:
        stack = [n.name]
        while stack:
            a = stack[-1]
            if a in rank:
                stack.pop()
                continue
            pending = [w.b.name for w in outs[a] if w.b.name not in rank and w.b.name != a]
            if pending:
                stack.extend(pending)
                continue
            rank[a] = 1 + max((rank.get(w.b.name, 0) for w in outs[a]), default=-1)
            stack.pop()
    return rank


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


# ------------------------------------------------------------------ boxes
class Box:
    def __init__(self, path, parent):
        self.path = path
        self.parent = parent
        self.kids = {}
        self.nodes = []
        self.links = []
        self.x = self.y = 0.0
        self.w = self.h = 0.0
        self.off = {}                             # node name -> y below our top
        self.rel = {}                             # own node name -> (x, y) in content
        self.lanes = []
        self.order = 0

    @property
    def depth(self):
        return len(self.path)

    def pad_top(self):
        if not self.path:
            return 0.0
        return PAD + LABEL.get(self.depth, LABEL[2]) * 1.4

    def pad_side(self):
        return PAD if self.path else 0.0


def _build_boxes(tree, tags):
    root = Box((), None)
    index = {}
    for i, n in enumerate(tree.nodes):
        if n.name not in tags:
            continue
        index[n.name] = i
        b = root
        for d, part in enumerate(tags[n.name]):
            if part not in b.kids:
                b.kids[part] = Box(tags[n.name][:d + 1], b)
                b.kids[part].order = i
            b = b.kids[part]
        b.nodes.append(n)
    for link in tree.links:
        pu, pv = tags.get(link.from_node.name), tags.get(link.to_node.name)
        if pu is None or pv is None:
            continue
        k = 0
        while k < min(len(pu), len(pv)) and pu[k] == pv[k]:
            k += 1
        b = root
        for part in pu[:k]:
            b = b.kids[part]
        b.links.append((link, pu, pv, k))
    return root, index


# ------------------------------------------------------------------ sugiyama
class _It:
    __slots__ = ("key", "kind", "obj", "w", "h", "x", "y", "layer", "pos",
                 "order", "pin", "dot")

    def __init__(self, key, kind, obj, w, h, order, pin=False):
        self.key, self.kind, self.obj = key, kind, obj
        self.w, self.h = w, h
        self.x = self.y = 0.0
        self.layer = 0
        self.pos = 0.0
        self.order = order
        self.pin = pin
        # A reroute's wire goes straight through, so it lines up by socket rather than top edge.
        # Lanes line up by top edge like the nodes they run beside; mixing the two lets a chain creep a
        # socket's height per sweep until it drifts away from its neighbours.
        self.dot = kind == "node" and obj.bl_idname == "NodeReroute"


def _vgap(a, b):
    if a.kind == "box" and b.kind == "box":
        return GAP_BOX_Y
    if a.kind == "box" or b.kind == "box":
        return GAP_LANE_BOX
    if a.dot or b.dot or a.kind == "dummy" or b.kind == "dummy":
        return GAP_DUMMY_Y
    return GAP_NODE_Y


def _wmedian(cands):
    cands.sort()
    total = sum(w for _, w in cands)
    acc = 0.0
    for v, w in cands:
        acc += w
        if acc >= total * 0.5:
            return v
    return cands[-1][0]


def _settle(col, desired, weight):
    """Closest positions to `desired` that keep `col`'s order and gaps.

    Weighted isotonic regression (pool adjacent violators) on y minus each item's cumulative minimum
    offset from the top of the column.
    """
    n = len(col)
    c = [0.0] * n
    for i in range(1, n):
        c[i] = c[i - 1] + col[i - 1].h + _vgap(col[i - 1], col[i])
    blocks = []
    for i in range(n):
        blocks.append([desired[i] - c[i], weight[i], 1])
        while len(blocks) > 1 and blocks[-2][0] > blocks[-1][0]:
            t2, w2, n2 = blocks.pop()
            t1, w1, n1 = blocks.pop()
            blocks.append([(t1 * w1 + t2 * w2) / (w1 + w2), w1 + w2, n1 + n2])
    i = 0
    for t, _, k in blocks:
        for _ in range(k):
            col[i].y = t + c[i]
            i += 1


def _crossings(segs):
    """Pairs of (a, b) segments that cross: a Fenwick-tree inversion count."""
    if len(segs) < 2:
        return 0
    segs = sorted(segs)
    ranks = {v: i + 1 for i, v in enumerate(sorted({b for _, b in segs}))}
    tree = [0] * (len(ranks) + 1)
    count = 0
    for seen, (_, b) in enumerate(segs):
        r = ranks[b]
        # how many already placed have a larger b
        i, le = r, 0
        while i > 0:
            le += tree[i]
            i -= i & -i
        count += seen - le
        i = r
        while i < len(tree):
            tree[i] += 1
            i += i & -i
    return count


def _brandes_koepf(layers, ins, outs):
    """y for the ordered columns (y down): Brandes and Koepf's placement, by sockets.

    Each item joins a block with its median neighbour in the column before (or after), the wire between them straight
    socket to socket, unless that would cross a block already made or a long wire's lane (lanes stay straight first).
    Blocks then pack as close as the column gaps allow. Four passes (blocks to the left or right, packed up or down)
    are balanced: each item at the mean of its two middle positions, then settled back into its column's order and gaps.

    ins/outs: id(item) -> [(upper, lower, upper port, lower port, weight)]."""
    if not layers:
        return
    # type 1 conflicts: a wire that would cross a lane (dummy to dummy) gives way
    pos = {id(it): i for col in layers for i, it in enumerate(col)}
    conflicts = set()
    for l in range(1, len(layers)):
        prev, col = layers[l - 1], layers[l]
        k0, scan = 0, 0
        for i, v in enumerate(col):
            inner = next((s for s in ins[id(v)] if s[0].kind == "dummy" and v.kind == "dummy"), None)
            if inner is None and i != len(col) - 1:
                continue
            k1 = pos[id(inner[0])] if inner is not None else len(prev) - 1
            for w in col[scan:i + 1]:
                for s in ins[id(w)]:
                    k = pos[id(s[0])]
                    if (k < k0 or k > k1) and not (s[0].kind == "dummy" and w.kind == "dummy"):
                        conflicts.add((id(s[0]), id(w)))
            scan, k0 = i + 1, k1
    items = [it for col in layers for it in col]
    runs = []
    for left in (True, False):          # blocks follow the column before or after
        for down in (True, False):      # columns read top down (packed up) or bottom up
            cols = layers if left else layers[::-1]
            cols = [col if down else col[::-1] for col in cols]
            lpos = {id(it): i for col in cols for i, it in enumerate(col)}
            root = {id(it): it for it in items}
            align = {id(it): it for it in items}
            off = {id(it): 0.0 for it in items}     # item y minus its block root's y
            for col in cols[1:]:
                r = -1
                for v in col:
                    if left:
                        ns = [(s[0], s[2], s[3], (id(s[0]), id(v))) for s in ins[id(v)]]
                    else:
                        ns = [(s[1], s[3], s[2], (id(v), id(s[1]))) for s in outs[id(v)]]
                    if not ns:
                        continue
                    ns.sort(key=lambda t: lpos[id(t[0])])
                    d = len(ns)
                    for m in sorted({(d - 1) // 2, d // 2}):
                        u, pu, pv, key = ns[m]
                        if align[id(v)] is not v:
                            break
                        if key in conflicts or r >= lpos[id(u)] or align[id(u)] is not u and \
                                not (align[id(u)] is root[id(u)]):
                            continue
                        # u's block may only grow at its end: u must be its last item
                        if align[id(u)] is not root[id(u)]:
                            continue
                        align[id(u)] = v
                        root[id(v)] = root[id(u)]
                        align[id(v)] = root[id(v)]
                        off[id(v)] = off[id(u)] + pu - pv
                        r = lpos[id(u)]
            # pack: in real column order a above b needs y(b) >= y(a) + h(a) + gap
            succ = defaultdict(list)
            indeg = defaultdict(int)
            roots = {id(root[id(it)]) for it in items}
            for col in layers:
                for a, b in zip(col, col[1:]):
                    ra, rb = id(root[id(a)]), id(root[id(b)])
                    if ra == rb:
                        continue
                    sep = a.h + _vgap(a, b) + off[id(a)] - off[id(b)]
                    if down:
                        succ[ra].append((rb, sep))
                    else:
                        succ[rb].append((ra, sep))
                    indeg[rb if down else ra] += 1
            ybase = {r: 0.0 for r in roots}
            queue = [r for r in roots if indeg[r] == 0]
            seen = 0
            while queue:
                r = queue.pop()
                seen += 1
                for q, sep in succ[r]:
                    ybase[q] = max(ybase[q], ybase[r] + sep)
                    indeg[q] -= 1
                    if indeg[q] == 0:
                        queue.append(q)
            if seen < len(roots):
                continue        # a cycle: leave this pass out
            sign = 1.0 if down else -1.0
            runs.append({id(it): sign * ybase[id(root[id(it)])] + off[id(it)] for it in items})
    if not runs:
        return
    # line the runs up on the smallest one's top, then put each item at the mean of its middle two
    spans = [(max(r[id(it)] + it.h for it in items) - min(r.values()), i) for i, r in enumerate(runs)]
    tops = [min(r.values()) for r in runs]
    for r, top in zip(runs, tops):
        for k in r:
            r[k] -= top
    for it in items:
        ys = sorted(r[id(it)] for r in runs)
        it.y = (ys[(len(ys) - 1) // 2] + ys[len(ys) // 2]) * 0.5
    # the mean of two runs can overlap neighbours slightly, so each column is settled back
    for col in layers:
        _settle(col, [it.y for it in col], [1.0] * len(col))


def _sugiyama(items, edges):
    """Place `items`, x and y relative to the block's top-left.

    edges: (u, v, u_port, v_port, weight, wire) with ports measured from each
    item's top edge and wire = (from node, output id, to node, input id).

    Returns (w, h, routes): the reroutes that carry wires between frames, each
    (source node, output id, [(x, y) per reroute], [(i, j) links between them], [(reader node, input id, i)]),
    with i = -1 for the source itself.
    """
    if not items:
        return 0.0, 0.0, []
    # --- break cycles (frames can feed each other both ways)
    succ = defaultdict(list)
    for e in edges:
        succ[id(e[0])].append(e[1])
    state, back = {}, set()
    for root in sorted(items, key=lambda it: it.order):
        if id(root) in state:
            continue
        stack = [(root, iter(succ[id(root)]))]
        state[id(root)] = 1
        while stack:
            u, it = stack[-1]
            v = next(it, None)
            if v is None:
                state[id(u)] = 2
                stack.pop()
                continue
            s = state.get(id(v))
            if s == 1:
                back.add((id(u), id(v)))
            elif s is None:
                state[id(v)] = 1
                stack.append((v, iter(succ[id(v)])))
    dag = [e for e in edges if (id(e[0]), id(e[1])) not in back]

    # --- layers, as late as possible: sinks on the right, everything else just before its nearest consumer.
    # Entry and exit ports then get a column of their own on the far left and far right, the frame's edges.
    out = defaultdict(set)
    for e in dag:
        out[id(e[0])].add(e[1])
    rank = {}
    for it in sorted(items, key=lambda it: it.order):
        stack = [it]
        while stack:
            u = stack[-1]
            if id(u) in rank:
                stack.pop()
                continue
            pending = [v for v in out[id(u)] if id(v) not in rank]
            if pending:
                stack.extend(pending)
                continue
            rank[id(u)] = 1 + max((rank[id(v)] for v in out[id(u)]), default=-1)
            stack.pop()
    top = max(rank.values())
    first = 1 if any(it.pin == "first" for it in items) else 0
    for it in items:
        if it.pin == "first":
            it.layer = 0
        elif it.pin == "last":
            it.layer = top + first + 1
        else:
            it.layer = top - rank[id(it)] + first
    layers = [[] for _ in range(max(it.layer for it in items) + 1)]
    for it in items:
        layers[it.layer].append(it)
    layers = [col for col in layers if col]
    for l, col in enumerate(layers):
        for it in col:
            it.layer = l

    # --- long edges become chains of dummies, one chain per source socket, shared by every reader of it
    segs = []                                     # (a, b, pa, pb, w)
    trunks = {}                                   # source socket -> chain
    reads = defaultdict(list)                     # source socket -> [(v, v port, wire)]
    trunk_port = {}                               # source socket -> its port
    for u, v, pu, pv, w, wire in dag:
        if v.layer <= u.layer:
            continue
        if v.layer == u.layer + 1:
            segs.append((u, v, pu, pv, w))
            continue
        key = (wire[0], wire[1])
        chain = trunks.setdefault(key, [u])
        trunk_port[key] = pu
        reads[key].append((v, pv, wire))
        while chain[-1].layer < v.layer - 1:
            prev = chain[-1]
            d = _It(key, "dummy", u, 0.0, 0.0, u.order)
            d.layer = prev.layer + 1
            layers[d.layer].append(d)
            segs.append((prev, d, pu if prev is u else 0.0, 0.0, 3.0))
            chain.append(d)
        last = chain[v.layer - u.layer - 1]
        segs.append((last, v, pu if last is u else 0.0, pv, w))

    ins, outs = defaultdict(list), defaultdict(list)
    for s in segs:
        outs[id(s[0])].append(s)
        ins[id(s[1])].append(s)

    # --- order within layers: barycentre sweeps from creation order. Frames keep the order the builder made
    # them in (Golden above CheatMaster above LootHacker reads better than whatever minimises crossings);
    # only nodes and lanes move around them.
    def reorder(col, key):
        boxes = sorted((it for it in col if it.kind == "box"), key=lambda it: it.order)
        col.sort(key=key)
        slots = [i for i, it in enumerate(col) if it.kind == "box"]
        for i, b in zip(slots, boxes):
            col[i] = b
        for i, it in enumerate(col):
            it.pos = i

    for col in layers:
        reorder(col, lambda it: it.order)

    def frac(it, port):
        return 0.99 * port / it.h if it.h > 0 else 0.0

    def count_all():
        total = 0
        for col in layers:
            total += _crossings([(a.pos + frac(a, pa), b.pos + frac(b, pb))
                                 for a in col for (_, b, pa, pb, _) in outs[id(a)]])
        return total

    best = count_all()
    snapshot = [list(col) for col in layers]
    for sweep in range(16):
        down = sweep % 2 == 0
        rng = range(1, len(layers)) if down else range(len(layers) - 2, -1, -1)
        for l in rng:
            keys = {}
            for it in layers[l]:
                nb = ([s[0].pos + frac(s[0], s[2]) for s in ins[id(it)]] if down else
                      [s[1].pos + frac(s[1], s[3]) for s in outs[id(it)]])
                keys[id(it)] = sum(nb) / len(nb) if nb else it.pos
            reorder(layers[l], lambda it: keys[id(it)])
        c = count_all()
        if c < best:
            best, snapshot = c, [list(col) for col in layers]
    layers = snapshot
    for col in layers:
        for i, it in enumerate(col):
            it.pos = i

    # --- y: stack, then pull every item level with what it connects to.
    # Level means top edges, not sockets: Blender draws a node's outputs above its inputs, so lining sockets up
    # makes every chain climb a step per node and a long one ends up diagonal. Tops keep chains in rows and
    # wires only dip by a socket or two. Reroutes and lanes are the exception (a wire goes straight through
    # them) and line up by socket. Frames chase nothing: they stay stacked from the top of their column,
    # packing like a masonry wall instead of leaving holes where two frames tried to line up with different neighbours.
    for col in layers:
        y = 0.0
        for i, it in enumerate(col):
            if i:
                y += col[i - 1].h + _vgap(col[i - 1], it)
            it.y = y

    def target(it, other, p_it, p_other):
        if it.dot or other.dot or ALIGN == "socket":
            return other.y + p_other - p_it
        if ALIGN == "centre":
            return other.y + other.h * 0.5 - it.h * 0.5
        return other.y

    def pull(col, downward):
        desired, weight = [], []
        for it in col:
            if it.kind == "box":
                desired.append(it.y)
                weight.append(1e6)
                continue
            if downward:
                cands = [(target(it, s[0], s[3], s[2]), s[4]) for s in ins[id(it)]]
            else:
                cands = [(target(it, s[1], s[2], s[3]), s[4]) for s in outs[id(it)]]
            if cands:
                desired.append(_wmedian(cands))
                weight.append(sum(w for _, w in cands))
            else:
                desired.append(it.y)
                weight.append(0.01)
        _settle(col, desired, weight)

    if ALIGN == "bk":
        _brandes_koepf(layers, ins, outs)
    else:
        for _ in range(10):
            for l in range(1, len(layers)):
                pull(layers[l], True)
            for l in range(len(layers) - 2, -1, -1):
                pull(layers[l], False)
        for l in range(1, len(layers)):
            pull(layers[l], True)

    # close any band no column uses: two chains sharing nothing can end up far apart,
    # and the frame would carry the empty stretch between them
    hole = GAP_BOX_Y if any(it.kind == "box" for it in items) else 2 * GAP_NODE_Y
    shift, bottom = 0.0, None
    for it in sorted((it for col in layers for it in col), key=lambda it: it.y):
        if bottom is not None and it.y - shift > bottom + hole:
            shift += it.y - shift - bottom - hole
        it.y -= shift
        bottom = it.y + it.h if bottom is None else max(bottom, it.y + it.h)

    real = [it for col in layers for it in col if it.kind != "dummy"]
    y0 = min(it.y for it in real)
    for col in layers:
        for it in col:
            it.y -= y0
    _straighten(layers, trunks, reads, trunk_port)

    # --- x: columns, wider gaps where more wires cross. Frames sit on the column's left edge so the gap
    # before them stays clear; nodes sit on its right edge, so a column's outputs line up and a small node
    # sharing a column with a wide frame stays next to what it feeds.
    x, col_left, col_right = 0.0, {}, {}
    for l, col in enumerate(layers):
        width = max((it.w for it in col if it.kind != "dummy"), default=0.0)
        col_left[l], col_right[l] = x, x + width
        for it in col:
            if it.kind == "dummy":
                it.x, it.w = x, width
            else:
                it.x = x if it.kind == "box" else x + width - it.w
        if l + 1 < len(layers):
            n_wires = sum(len(outs[id(it)]) for it in col)
            boxes = any(it.kind == "box" for it in col + layers[l + 1])
            gap = (GAP_BOX_X if boxes else GAP_NODE_X) + min(60.0, 2.0 * n_wires)
            x += width + gap
        else:
            x += width

    _pull_right(layers, dag)
    if PULL_UP:
        _pull_up(layers, dag, trunks, reads, trunk_port)
    _snap_sources(layers, dag)

    placed = []
    if ROUTE_LANES:
        placed += _wire_routes(dag, real, trunks)
    return x, max(it.y + it.h for it in real), placed


def _straighten(layers, trunks, reads, trunk_port):
    """Put each long wire's lane at one height where its columns leave room (every step in a lane is a pair
    of reroutes): its source's socket height if possible (no bend leaving it), else a reader's (none arriving),
    else one its stretches already have. A reroute source (a frame's port) moves level with its lane instead,
    where its own column allows."""
    where = {id(it): (l, i) for l, col in enumerate(layers) for i, it in enumerate(col)}

    def room(it, y, h):
        l, i = where[id(it)]
        col = layers[l]
        if i > 0 and col[i - 1].y + col[i - 1].h + _vgap(col[i - 1], it) > y + 0.01:
            return False
        if i + 1 < len(col) and y + h + _vgap(it, col[i + 1]) > col[i + 1].y + 0.01:
            return False
        return True
    for key, chain in trunks.items():
        u, ds = chain[0], chain[1:]
        if not ds:
            continue
        pu = trunk_port[key]
        cands = [u.y + pu] + [v.y + pv for v, pv, _ in reads[key]] + [d.y for d in ds]
        best = next((y for y in cands if all(room(d, y, 0.0) for d in ds)), None)
        if best is None:
            continue
        if abs(best - (u.y + pu)) > 0.5 and u.dot and room(u, best - pu, u.h):
            u.y = best - pu
        for d in ds:
            d.y = best


def _snap_sources(layers, dag):
    """Put an input that feeds one node only (a Group Input copy, a texture coordinate) level with it, wherever
    the columns left it: a short wire across, not one down half the frame from a node that rose into free space above."""
    real = [it for col in layers for it in col if it.kind != "dummy"]
    lanes = [it for col in layers for it in col if it.kind == "dummy"]
    ins, outs = defaultdict(list), defaultdict(list)
    for e in dag:
        outs[id(e[0])].append(e)
        ins[id(e[1])].append(e)
    for it in real:
        if it.kind != "node" or it.pin or ins[id(it)] or not outs[id(it)]:
            continue
        if len({id(e[1]) for e in outs[id(it)]}) != 1:
            continue
        u, v, pu, pv = outs[id(it)][0][:4]
        y = v.y + pv - pu if it.dot or v.dot or ALIGN != "top" else v.y
        if abs(y - it.y) < 1.0:
            continue
        top, bottom = y - GAP_NODE_Y * 0.5, y + it.h + GAP_NODE_Y * 0.5
        left, right = it.x - GAP_NODE_X * 0.5, it.x + it.w + GAP_NODE_X * 0.5
        if not any(o is not it and o.x < right and o.x + o.w > left and o.y <= bottom and o.y + o.h >= top
                   for o in real + lanes):
            it.y = y


def _crosses(xa, ya, xb, yb, rects, skip):
    """Whether the wire Blender draws from (xa, ya) to (xb, yb) runs behind one of `rects`."""
    lo, hi = min(ya, yb), max(ya, yb)
    near = [it for it in rects if it.x < xb and it.x + it.w > xa and it.y < hi and it.y + it.h > lo
            and it not in skip]
    if not near:
        return False
    d = 0.4 * abs(xb - xa)
    for k in range(1, 32):
        t = k / 32.0
        s = 1.0 - t
        x = s * s * s * xa + 3 * s * s * t * (xa + d) + 3 * s * t * t * (xb - d) + t * t * t * xb
        y = (s * s * s + 3 * s * s * t) * ya + (3 * s * t * t + t * t * t) * yb
        for it in near:
            if it.x + 4 < x < it.x + it.w - 4 and it.y + 4 < y < it.y + it.h - 4:
                return True
    return False


def _wire_routes(dag, real, trunks):
    """Reroutes for wires that would run behind a node or frame, long wires that would cross the graph on a slant,
    and a bundle read in several columns. Such a wire runs level and bends only where there is room, a dot at each
    end of a bend: few bends, none away from where it goes and back. Routed on the finished positions (the columns'
    lanes are candidate heights, not the route), longest wires first; a lane already laid keeps the next off its height.
    A wire into a multi-input socket stays plain (a Join reads its links in order; relinking would change it).

    Returns [(source node, output id, [(x, y)], [(i, j)], [(reader node, input id, i)])]."""
    nets = defaultdict(list)
    for u, v, pu, pv, _, wire in dag:
        if v.layer > u.layer and not wire[4]:
            nets[(wire[0], wire[1])].append((u, v, pu, pv, wire))
    runs, routes = [], []
    reach = lambda es: max(v.x - es[0][0].x - es[0][0].w for _, v, *_ in es)
    for key, es in sorted(nets.items(), key=lambda kv: -reach(kv[1])):
        u, pu = es[0][0], es[0][2]
        xa, ya = u.x + u.w, u.y + pu
        bus = es[0][4][5] and len({v.layer for _, v, *_ in es}) > 1
        want = []
        for _, v, _, pv, wire in es:
            xb, yb = v.x, v.y + pv
            if xb < xa + GAP_NODE_X * 0.5:
                continue
            # a wire to the next column that clips a node stays plain (its dots would crowd)
            if (bus or v.layer > u.layer + 1 and _crosses(xa, ya, xb, yb, real, (u, v))
                    or xb - xa > ROUTE_LONG and abs(yb - ya) > ROUTE_SLANT):
                want.append((v, xb, yb, wire))
        if not want:
            continue
        lane = [d.y for d in trunks.get(key, [])[1:]]
        r = _route_net(u, xa, ya, want, real, runs, lane)
        if r:
            routes.append((key[0], key[1]) + r)
    return routes


def _route_net(u, xa, ya, want, real, runs, lane):
    """One source's routed wires: the cheapest path over a grid of heights just clear of obstacles and the x where
    something starts or ends. Level stretches are free, each bend costs (more when steep), and a run away from
    the source's height costs a little, so a wire leaves level and bends late and wires to several readers share their start."""
    readers = {id(v) for v, *_ in want}
    x_end = max(xb for _, xb, _, _ in want)
    ys_all = [ya] + [yb for _, _, yb, _ in want]
    lo, hi = min(ys_all) - ROUTE_SPAN, max(ys_all) + ROUTE_SPAN
    rects = []
    for it in real:
        if it is u or it.x >= x_end or it.x + it.w <= xa or it.y > hi or it.y + it.h < lo:
            continue
        m = 0.0 if id(it) in readers else ROUTE_M
        rects.append((it.x - m, it.y - m, it.x + it.w + m, it.y + it.h + m))
    lanes = [r for r in runs if r[0] < x_end and r[1] > xa]

    def grid(fixed, extra):
        # ends exact; the rest thinned where they crowd (rows of nodes share their edges)
        out = sorted(set(fixed))
        for v in sorted(extra):
            j = bisect.bisect_left(out, v)
            if (j == len(out) or out[j] - v > ROUTE_SNAP) and (j == 0 or v - out[j - 1] > ROUTE_SNAP):
                out.insert(j, v)
        return out
    X = grid([xa, *(xb for _, xb, _, _ in want)], [x for r in rects for x in (r[0], r[2]) if xa < x < x_end])
    # a wide empty stretch gives room to bend near either end, not only right across it
    X = grid(X, [x for a, b in zip(X, X[1:]) if b - a > 4 * ROUTE_BEND_W
                 for x in (a + 2 * ROUTE_BEND_W, b - 2 * ROUTE_BEND_W)])
    Y = grid(ys_all, [*lane, *(y for r in rects for y in (r[1] - 1.0, r[3] + 1.0) if lo < y < hi)])
    if len(X) * len(Y) > ROUTE_GRID:
        return None
    xi = {x: i for i, x in enumerate(X)}
    yi = {y: i for i, y in enumerate(Y)}
    across = {}

    def blocks(i, j):
        """What stands between X[i] and X[j]: rects and lanes."""
        if (i, j) not in across:
            x0, x1 = X[i], X[j]
            across[i, j] = ([r for r in rects if r[0] < x1 - 0.5 and r[2] > x0 + 0.5],
                            [r[2] for r in lanes if r[0] < x1 and r[1] > x0])
        return across[i, j]

    def level(i, y):
        rs, ls = blocks(i, i + 1)
        return not any(r[1] < y < r[3] for r in rs) and not any(abs(ly - y) < ROUTE_SEP for ly in ls)

    def span(i, j, y):
        """The heights a bend from y across X[i]..X[j] can reach, up to what is above and down to what is below."""
        rs = blocks(i, j)[0]
        top = max((r[3] for r in rs if r[1] < y), default=-1e18)
        bottom = min((r[1] for r in rs if r[3] > y), default=1e18)
        return top, bottom

    start = (0, yi[ya])
    goals = {(xi[xb], yi[yb]) for _, xb, yb, _ in want}
    goal_xy = [(xb, yb) for _, xb, yb, _ in want]

    def h(i, k):
        # A*: what is left at the least cost: its level run, plus a bend if it isn't level with any reader
        x, y = X[i], Y[k]
        return min((xb - x) * 1e-3 + (0.0 if yb == y else ROUTE_BEND) for xb, yb in goal_xy if xb >= x)             if any(xb >= x for xb, _ in goal_xy) else 1e18
    dist, prev = {start: 0.0}, {}
    heap, done = [(h(*start), 0.0, start)], set()
    while heap and not goals <= done:
        _, c, s = heapq.heappop(heap)
        if s in done:
            continue
        done.add(s)
        i, k = s
        y = Y[k]
        moves = []
        if i + 1 < len(X) and level(i, y):
            moves.append(((i + 1, k), (X[i + 1] - X[i]) * (1e-3 if y == ya else 2e-3)))
        # bends: across the narrowest gap wide enough, and the next two wider ones
        j0 = bisect.bisect_left(X, X[i] + ROUTE_BEND_W, i + 1)
        for j in range(j0, min(j0 + 3, len(X))):
            w = X[j] - X[i]
            top, bottom = span(i, j, y)
            for k2 in range(bisect.bisect_left(Y, top), bisect.bisect_right(Y, bottom)):
                if k2 != k:
                    dy = abs(Y[k2] - y)
                    moves.append(((j, k2), ROUTE_BEND + 0.05 * dy + 40.0 * max(0.0, dy / w - 2.0) + w * 4e-3))
        for s2, cost in moves:
            if c + cost < dist.get(s2, float("inf")):
                dist[s2], prev[s2] = c + cost, s
                heapq.heappush(heap, (c + cost + h(*s2), c + cost, s2))
    paths = []
    for v, xb, yb, wire in want:
        g = (xi[xb], yi[yb])
        if g not in done:
            continue
        p = [g]
        while p[-1] != start:
            p.append(prev[p[-1]])
        paths.append((wire, p[::-1]))
    if not paths:
        return None
    # a dot where a bend starts or ends and where readers part; none at the source or a reader
    bend_end = set()
    for _, p in paths:
        for a, b in zip(p, p[1:]):
            if a[1] != b[1]:
                bend_end.update((a, b))
    inner = {s for _, p in paths for s in p[:-1]}
    pts, index, links, feeds = [], {}, [], []
    for wire, p in paths:
        cur = -1
        for s in p[1:]:
            if s in bend_end and (s in inner or s != p[-1]):
                if s not in index:
                    index[s] = len(pts)
                    pts.append((X[s[0]], Y[s[1]]))
                if (cur, index[s]) not in links:
                    links.append((cur, index[s]))
                cur = index[s]
        feeds.append((wire[2], wire[3], cur))
        run = None
        for a, b in zip(p, p[1:]):
            if a[1] == b[1]:
                run = (run[0] if run else X[a[0]], X[b[0]], Y[a[1]])
            elif run:
                runs.append(run)
                run = None
        if run:
            runs.append(run)
    if not pts:
        return None
    return pts, links, feeds


def _pull_up(layers, dag, trunks=None, reads=None, trunk_port=None):
    """Move rows up into the free space above them.

    A row is the items its level wires join (top edges equal, as Sugiyama lined them up), moved as one so its
    wires stay straight; it rises until something overlapping it horizontally is in the way. (Columns are stacked
    whole, so a short column under a long frame's end would otherwise sit as low as the tallest.)
    A long wire's lane moves as one with what it lines up with at either end."""
    real = [it for col in layers for it in col]
    up = {id(it): id(it) for it in real}

    def find(a):
        while up[a] != a:
            up[a] = up[up[a]]
            a = up[a]
        return a
    for u, v, *_ in dag:
        if id(u) in up and id(v) in up and abs(u.y - v.y) < 0.5:
            up[find(id(u))] = find(id(v))
    for key, chain in (trunks or {}).items():
        for a, b in zip(chain[1:], chain[2:]):
            up[find(id(a))] = find(id(b))
        pu = trunk_port[key] if trunk_port else 0.0
        if abs(chain[0].y - chain[1].y) < 0.5 or abs(chain[0].y + pu - chain[1].y) < 0.5:
            up[find(id(chain[0]))] = find(id(chain[1]))
        for v, pv, _ in reads[key]:
            last = chain[v.layer - chain[0].layer - 1]
            if abs(v.y - last.y) < 0.5 or abs(v.y + pv - last.y) < 0.5:
                up[find(id(v))] = find(id(last))
    rows = defaultdict(list)
    for it in real:
        rows[find(id(it))].append(it)
    # a row packs close under what it is wired to and further from the rest, so separate parts of the
    # shader stay apart instead of setting into one block
    wired = defaultdict(set)
    for u, v, *_ in dag:
        wired[id(u)].add(find(id(v)))
        wired[id(v)].add(find(id(u)))
    placed = []
    for row in sorted(rows.values(), key=lambda r: min(it.y for it in r)):
        shift = None
        mine = find(id(row[0]))
        for it in row:
            floor = 0.0
            for o in placed:
                if o.x < it.x + it.w + 10.0 and it.x < o.x + o.w + 10.0:
                    lane = o.kind == "dummy" or it.kind == "dummy"
                    gap = _vgap(o, it) if lane or mine in wired[id(o)] else max(_vgap(o, it), GAP_PART_Y)
                    floor = max(floor, o.y + o.h + gap)
            room = it.y - floor
            shift = room if shift is None else min(shift, room)
        if shift and shift > 0.5:
            for it in row:
                it.y -= shift
        placed += row


def _pull_right(layers, dag):
    """Move each item right to just before the nearest thing that reads it, as far as nothing in its rows is in the way.

    A column is as wide as its widest item, so a short chain beside a long frame would otherwise wait at the left
    end of a wire as long as the frame. Readers go first so a chain follows its last node; frame ports keep their edge columns."""
    real = [it for col in layers for it in col if it.kind != "dummy"]
    lanes = [it for col in layers for it in col if it.kind == "dummy"]
    readers = defaultdict(list)
    for u, v, *_ in dag:
        if v.layer > u.layer:
            readers[id(u)].append(v)

    def gap(a, b):
        return GAP_BOX_X if a.kind == "box" or b.kind == "box" else GAP_NODE_X

    for it in sorted(real, key=lambda it: -it.layer):
        if it.pin or not readers[id(it)]:
            continue
        want = min(v.x - gap(it, v) for v in readers[id(it)]) - it.w
        if want <= it.x:
            continue
        top, bottom = it.y - GAP_DUMMY_Y, it.y + it.h + GAP_DUMMY_Y
        mine = {id(v) for v in readers[id(it)]}
        for o in real + [d for d in lanes if d.obj is not it]:
            # whatever is further right in the rows it spans stops it short (further off if it doesn't
            # read this: another part), and so does a lane running through them
            if o is not it and o.x >= it.x + it.w and o.y < bottom and o.y + o.h >= top:
                want = min(want, o.x - (gap(it, o) if id(o) in mine else max(gap(it, o), GAP_PART_X)) - it.w)
        if want > it.x:
            it.x = want


# ------------------------------------------------------------------ layout
def _item_of(node, path, k):
    """The item at level k that holds `node`: the node itself or a child box."""
    if len(path) > k:
        return ("box", path[k])
    return ("node", node.name)


def _layout(box, index, ports):
    for kid in box.kids.values():
        _layout(kid, index, ports)
    items = {}
    for name, kid in box.kids.items():
        # retired terms go last, under whatever shares their column
        order = float("inf") if name == UNUSED else kid.order
        items[("box", name)] = _It(("box", name), "box", kid, kid.w, kid.h, order)
    for n in box.nodes:
        items[("node", n.name)] = _It(("node", n.name), "node", n, node_width(n),
                                      node_height(n), index[n.name],
                                      pin=ports.get(n.name))

    def port(it, node, sock):
        base = it.obj.off[node.name] if it.kind == "box" else 0.0
        return base + socket_offset(node, sock)

    edges = []
    for link, pu, pv, k in box.links:
        u = items.get(_item_of(link.from_node, pu, k))
        v = items.get(_item_of(link.to_node, pv, k))
        if u is None or v is None or u is v:
            continue
        main = (v.kind == "node"
                and next((s for s in v.obj.inputs if s.is_linked), None) == link.to_socket)
        wire = (link.from_node.name, link.from_socket.identifier,
                link.to_node.name, link.to_socket.identifier, link.to_socket.is_multi_input,
                link.from_socket.type == 'BUNDLE')
        edges.append((u, v, port(u, link.from_node, link.from_socket),
                      port(v, link.to_node, link.to_socket), 2.0 if main else 1.0, wire))

    w, h, box.lanes = _sugiyama(list(items.values()), edges)
    for it in items.values():
        if it.kind == "box":
            it.obj.x, it.obj.y = it.x, it.y
        else:
            box.off[it.obj.name] = box.pad_top() + it.y
            box.rel[it.obj.name] = (it.x, it.y)
    for kid in box.kids.values():
        for name, off in kid.off.items():
            box.off[name] = box.pad_top() + kid.y + off
    box.w = w + 2 * box.pad_side()
    box.h = h + box.pad_top() + box.pad_side()


def _colour(path):
    base = PALETTE.get(path[0])
    if base is None:
        hue = (zlib.crc32(path[0].encode()) % 360) / 360.0
        base = colorsys.hsv_to_rgb(hue, 0.55, 0.30)
    lift = 0.05 * (len(path) - 1)
    return tuple(min(1.0, c + lift) for c in base)


def _apply(tree, root, stand_ins):
    """Make the frames and lanes and move every node, in one bulk write.

    Assigning a location per node costs Blender a tree update each; `foreach_set` writes them all at once.
    Frames go first, one by one and parents before children, so each node's location is taken relative to a
    frame already where it will stay.
    """
    N = tree.nodes
    where = {}
    routed = []

    # A big flat graph can ask for thousands of reroutes (a face material: 9,000 for 875 nodes), and made one by
    # one they take Blender minutes. Lanes carrying the most wires per dot go first until the tree's allowance
    # is spent; the rest stay plain wires.
    def boxes(box):
        yield box
        for k in box.kids.values():
            yield from boxes(k)
    lanes = sorted(((len(lane[4]) / max(len(lane[2]), 1), id(lane)) for b in boxes(root) for lane in b.lanes),
                   reverse=True)
    keep, spent = set(), 0
    allowance = min(LANE_LIMIT, LANE_WORK / max(len(N), 1))
    sizes = {id(lane): len(lane[2]) for b in boxes(root) for lane in b.lanes}
    for _, k in lanes:
        if spent + sizes[k] <= allowance:
            keep.add(k)
            spent += sizes[k]

    def place(box, ox, oy, parent_frame):
        frame = None
        if box.path:
            frame = N.new("NodeFrame")
            frame.name = "/".join(box.path)
            frame.label = box.path[-1]
            frame.label_size = LABEL.get(box.depth, LABEL[2])
            frame.shrink = True
            frame.use_custom_color = True
            frame.color = _colour(box.path)
            frame.parent = parent_frame
            frame.location_absolute = (ox, -oy)
            where[frame.name] = (ox, -oy)
        cx, cy = ox + box.pad_side(), oy + box.pad_top()
        for n in box.nodes:
            n.parent = frame
            x, y = box.rel[n.name]
            x, y = cx + x, cy + y
            if n.bl_idname == "NodeReroute":
                # a reroute's location is its dot, not a corner
                x, y = x + 8.0, y + 8.0
            where[n.name] = (x, -y)
        for src, ident, points, links, feeds in (lane for lane in box.lanes if id(lane) in keep):
            dots = []
            for x, y in points:
                r = N.new("NodeReroute")
                r.parent = frame
                where[r.name] = (cx + x, -(cy + y))
                dots.append(r)
            routed.append((src, ident, dots, links, feeds))
        for kid in box.kids.values():
            place(kid, cx + kid.x, cy + kid.y, frame)

    place(root, 0.0, 0.0, None)
    flat = []
    for n in N:
        flat.extend(where.get(n.name, tuple(n.location_absolute)))
    N.foreach_set("location_absolute", flat)

    # wire from the source outwards: a reroute takes its type from its input
    for src, ident, dots, links, feeds in routed:
        def out_of(i):
            if i < 0:
                return _out_of(N[src], ident, stand_ins)
            return dots[i].outputs[0]
        for i, j in links:
            tree.links.new(out_of(i), dots[j].inputs[0])
        for to, to_ident, i in feeds:
            tree.links.new(out_of(i), _in(N[to], to_ident))
    _realize_inputs(tree, stand_ins)
    return sum(len(r[2]) for r in routed)


def arrange(tree):
    """Frame, place and tidy every node of `tree`. Returns a short report."""
    for n in [n for n in tree.nodes if n.bl_idname == "NodeFrame"]:
        tree.nodes.remove(n)
    tags = _read_tags(tree)
    untagged = sum(1 for t in tags.values() if t is None)
    wires = _scan(tree)
    _resolve_untagged(tree, tags, wires)
    parked = _park_dead(tree, tags, wires)
    split = _split_fanouts(tree, tags, wires) if SPLIT_FANOUTS else 0
    if split:
        wires = _scan(tree)
    ports = _ports(tree, tags, wires)
    copies, stand_ins = _localize_sources(tree, tags, _scan(tree))
    copies += split
    wires = _scan(tree)
    _hide_unused_outputs(tree, wires)
    if HIDE_IDLE_INPUTS:
        _hide_idle_inputs(tree, wires)
    _collapse_trivial(tree, wires)
    root, index = _build_boxes(tree, tags)
    _layout(root, index, ports)
    lanes = _apply(tree, root, stand_ins)
    for n in tree.nodes:
        if SECTION_KEY in n:
            del n[SECTION_KEY]
    # a node made from Python starts selected; deselect all
    tree.nodes.foreach_set("select", [False] * len(tree.nodes))
    frames = sum(1 for n in tree.nodes if n.bl_idname == "NodeFrame")
    return ("%d frames, %d local input copies, %d ports, %d bus reroutes, "
            "%d placed by neighbour, %d unused parked, %.0f x %.0f"
            % (frames, copies, len(ports), lanes, untagged, parked, root.w, root.h))
