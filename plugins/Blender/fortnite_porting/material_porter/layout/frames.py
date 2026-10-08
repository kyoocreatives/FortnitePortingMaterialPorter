"""Lays out frames inside out and writes the result to the tree."""

import colorsys
import zlib

from ..nodelib import SECTION_KEY

from .metrics import (
    HIDE_IDLE_INPUTS,
    LABEL,
    LANE_LIMIT,
    LANE_WORK,
    PALETTE,
    SPLIT_FANOUTS,
    UNUSED,
    node_height,
    node_width,
    socket_offset,
)
from .wires import _in, _localize_sources, _out_of, _park_dead, _read_tags, _resolve_untagged, _scan
from .ports import (
    _collapse_trivial,
    _hide_idle_inputs,
    _hide_unused_outputs,
    _ports,
    _realize_inputs,
    _split_fanouts,
)
from .placement import _It, _build_boxes, _sugiyama


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
