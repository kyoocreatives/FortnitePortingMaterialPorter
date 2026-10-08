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
    SPLIT_FANOUTS,
    STAND_IN,
    UNUSED,
    VEC,
    _base,
    _tall,
    _vis,
    node_height,
    node_width,
    socket_offset,
)
from .wires import (  # noqa: F401
    _Wire,
    _adjacency,
    _clone,
    _driver,
    _in,
    _is_source,
    _key,
    _localize_sources,
    _out,
    _out_of,
    _park_dead,
    _ranks,
    _read_tags,
    _relink,
    _resolve_untagged,
    _scan,
    _stand_in,
)
from .ports import (  # noqa: F401
    COLLAPSIBLE,
    EXIT_MARK,
    OPERATORS,
    PORT_MARK,
    PORT_TYPES,
    _DEFAULTS,
    _collapse_trivial,
    _defaults,
    _hide_idle_inputs,
    _hide_unused_outputs,
    _port_name,
    _ports,
    _realize_inputs,
    _same,
    _split_fanouts,
)
from .placement import (  # noqa: F401
    Box,
    _It,
    _brandes_koepf,
    _build_boxes,
    _crossings,
    _settle,
    _snap_sources,
    _straighten,
    _sugiyama,
    _wmedian,
)
from .routing import (  # noqa: F401
    _crosses,
    _pull_right,
    _pull_up,
    _route_net,
    _vgap,
    _wire_routes,
)
from .frames import (  # noqa: F401
    _apply,
    _colour,
    _item_of,
    _layout,
    arrange,
)
