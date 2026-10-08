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
The size table (metrics.py) was measured in a UI session (tools/layout_measure.py), good to a few pixels."""
from .metrics import (  # noqa: F401
    ALIGN,
    _base,
    BASE_DEFAULT,
    BASE_H,
    FOOT,
    GAP_BOX_X,
    GAP_BOX_Y,
    GAP_DUMMY_Y,
    GAP_LANE_BOX,
    GAP_NODE_X,
    GAP_NODE_Y,
    GAP_PART_X,
    GAP_PART_Y,
    HEAD,
    HIDDEN_H,
    HIDE_IDLE_INPUTS,
    LABEL,
    LANE_LIMIT,
    LANE_WORK,
    node_height,
    node_width,
    PAD,
    PALETTE,
    PULL_UP,
    PURE_SOURCES,
    ROUTE_BEND,
    ROUTE_BEND_W,
    ROUTE_GRID,
    ROUTE_LANES,
    ROUTE_LONG,
    ROUTE_M,
    ROUTE_SEP,
    ROUTE_SLANT,
    ROUTE_SNAP,
    ROUTE_SPAN,
    ROW,
    socket_offset,
    SPLIT_FANOUTS,
    STAND_IN,
    _tall,
    UNUSED,
    VEC,
    _vis,
)
from .wires import (  # noqa: F401
    adjacency,
    clone_node,
    _driver,
    input_socket,
    is_source,
    _key,
    localize_sources,
    node_ranks,
    output_socket,
    park_dead,
    read_tags,
    relink,
    resolve_untagged,
    scan_wires,
    source_output,
    _stand_in,
    Wire,
)
from .ports import (  # noqa: F401
    collapse_trivial,
    COLLAPSIBLE,
    _DEFAULTS,
    _defaults,
    EXIT_MARK,
    frame_ports,
    hide_idle_inputs,
    hide_unused_outputs,
    OPERATORS,
    PORT_MARK,
    _port_name,
    PORT_TYPES,
    realize_inputs,
    _same,
    split_fanouts,
)
from .placement import (  # noqa: F401
    Box,
    _brandes_koepf,
    build_boxes,
    _crossings,
    It,
    _settle,
    _snap_sources,
    _straighten,
    sugiyama,
    _wmedian,
)
from .routing import (  # noqa: F401
    _crosses,
    pull_right,
    pull_up,
    _route_net,
    vgap,
    wire_routes,
)
from .frames import (  # noqa: F401
    _apply,
    arrange,
    _colour,
    _item_of,
    _layout,
)
