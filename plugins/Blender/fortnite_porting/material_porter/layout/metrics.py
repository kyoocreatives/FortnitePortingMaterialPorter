"""Node size estimates (measured in a UI session) and socket positions."""


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


# A Group Input carries every interface socket (361 on the Variants group) and the cost of any link edit grows
# with the sockets in the tree; a hundred copies make every later edit ten times slower. So until the last edit
# a copy is stood in for by a Separate Bundle holding only the outputs that copy will show (same height and
# order), and the real Group Input replaces it at the very end (_realize_inputs).
STAND_IN = "fpv4_group_input"
