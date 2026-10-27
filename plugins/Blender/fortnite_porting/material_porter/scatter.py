"""Scatter layers: an object (game grass, a prop...) scattered over a mesh as Geometry Nodes instances, one modifier
"FP Scatter · <label>" per layer, all sharing the node group "FP Scatter".

Placement follows UE 5.8's landscape grass builder (LandscapeGrass.cpp): points at the variety's density (instances per
10 m x 10 m; random as Fortnite's PCG places them, or UE's jittered grid / Halton sequence), a point kept where
density range min < weight <= max and weight >= its random value, scaled per the variety's Scaling, turned at random
about its up axis and aligned to the surface. Weight = the layer's paint mask (float point attribute mp_scatter_<id>, absent = 1) x its source: none, a
float attribute (grass_masks' mp_grass_<name>), a packed landscape layer's colour channel, or a weightmap image's
channel read through UV map mp_weights (weightmaps). Points are placed in world space (density per world m², height in
world Z) and the instances are never realized. With camera cull on, each instance's fade (0 at the variety's cull
start, 1 at its end) is in instance attribute mp_fade.

obj["mp_scatter"] (JSON) keeps each layer: {id: {label, source, type, variety, weight, cast_shadow}}. Settings arrive in
the app's schema (UE units: cm, degrees) and are converted here."""
import json
import math
import uuid

import bpy
import numpy as np

from . import landscape_layers, weightmaps

GROUP = "FP Scatter"
VERSION = 3
VERSION_KEY = "mp_scatter_version"
KEY = "mp_scatter"
MASK = "mp_scatter_"            # + id: the layer's paint mask (float, point domain)
TAG = "mp_scatter_layer_"       # + id: marks the layer's instances (boolean, instance domain), for count()
GRASS = "mp_grass_"             # + name: a grass mask (grass_masks)
FADE = "mp_fade"                # per instance: 0 to 1 from cull start to end (camera cull)
WEIGHT = ".mp_scatter_weight"   # inside the group: mask x source, on the points
NORMAL = ".mp_scatter_normal"
CELL = ".mp_scatter_cell"       # UE grid: the cell a point started in
HALTON_WRAP = 1 << 24           # Halton indices wrap here (its digits stay exact in float)
PREFIX = "FP Scatter · "
CM = 0.01
VIEW_LIMIT = 250000             # instances a new layer shows in the viewport before its Display % drops

SOURCES = ("None", "Attribute", "Layer", "Image")
SCALING = ("Uniform", "Free", "LockXY")
PLACEMENT = ("Fortnite", "UE")

# name, socket type, default, min, max, description (panel rows: a str opens a panel)
INPUTS = (
    ("Instance", 'NodeSocketObject', None, None, None, "The object scattered"),
    "Points",
    ("Density", 'NodeSocketFloat', 400.0, 0.0, 1e5, "Instances per 10 m x 10 m (UE's GrassDensity)"),
    ("Placement", 'NodeSocketMenu', "Fortnite", None, None,
     "Fortnite: random points (its PCG ignores Use Grid); UE: a jittered grid when Use Grid"),
    ("Use Grid", 'NodeSocketBool', False, None, None, "UE placement: a jittered grid (else the Halton sequence)"),
    ("Jitter", 'NodeSocketFloat', 1.0, 0.0, 1.0, "UE grid: how far a point strays in its cell"),
    ("Triangle Normals", 'NodeSocketBool', True, None, None,
     "UE grid: align to the triangle's normal (else a smooth one from the neighbouring cells)"),
    ("Seed", 'NodeSocketInt', 0, None, None, "Another random placement"),
    ("Random Rotation", 'NodeSocketBool', True, None, None, "Turn each instance at random about its up axis"),
    ("Align To Surface", 'NodeSocketBool', True, None, None, "Point each instance's up along the surface normal"),
    "Scale",
    ("Scaling", 'NodeSocketMenu', "Uniform", None, None, "Uniform: X for all; Free: each axis its own; LockXY: Y = X"),
    ("Scale X Min", 'NodeSocketFloat', 1.0, 0.0, None, ""),
    ("Scale X Max", 'NodeSocketFloat', 1.0, 0.0, None, ""),
    ("Scale Y Min", 'NodeSocketFloat', 1.0, 0.0, None, "Free only"),
    ("Scale Y Max", 'NodeSocketFloat', 1.0, 0.0, None, "Free only"),
    ("Scale Z Min", 'NodeSocketFloat', 1.0, 0.0, None, "Free and LockXY"),
    ("Scale Z Max", 'NodeSocketFloat', 1.0, 0.0, None, "Free and LockXY"),
    ("Weight Attenuates Max Scale", 'NodeSocketBool', False, None, None, "Lower weight, smaller instances"),
    ("Attenuation", 'NodeSocketFloat', 0.5, 0.0, 1.0, "Weights from here to 1 span the scale range; below: Min"),
    "Filters",
    ("Density Range Min", 'NodeSocketFloat', 0.0, 0.0, 1.0, "Weights up to it: none (UE's AllowedDensityRange)"),
    ("Density Range Max", 'NodeSocketFloat', 1.0, 0.0, 1.0, "Weights above it: none"),
    ("Slope Min", 'NodeSocketFloat', 0.0, 0.0, 180.0, "Degrees from flat"),
    ("Slope Max", 'NodeSocketFloat', 90.0, 0.0, 180.0, "Degrees from flat"),
    ("Height Min", 'NodeSocketFloat', -1e5, None, None, "World Z, metres"),
    ("Height Max", 'NodeSocketFloat', 1e5, None, None, "World Z, metres"),
    "Weight",
    ("Weight Source", 'NodeSocketMenu', "None", None, None, "What weights the layer besides its paint mask"),
    ("Source Attribute", 'NodeSocketString', "", None, None, "Float attribute (Attribute) or colour attribute (Layer)"),
    ("Source Channel", 'NodeSocketInt', 0, 0, 3, "Channel of the colour attribute or image: R, G, B, A"),
    ("Source Image", 'NodeSocketImage', None, None, None, "Weightmap image, read through UV map " + weightmaps.UV),
    ("Mask", 'NodeSocketString', "", None, None, "The layer's paint mask (float point attribute, absent = 1)"),
    "Display",
    ("Display %", 'NodeSocketFloat', 100.0, 0.0, 100.0, "Share of the instances shown in the viewport"),
    ("Camera Cull", 'NodeSocketBool', False, None, None, "No instances beyond Cull End from the active camera"),
    ("Cull Start", 'NodeSocketFloat', 100.0, 0.0, None, "Metres: where mp_fade starts (UE's StartCullDistance)"),
    ("Cull End", 'NodeSocketFloat', 100.0, 0.0, None, "Metres: the cut (UE's EndCullDistance)"),
    ("Tag", 'NodeSocketString', "", None, None, ""),
)


# ------------------------------------------------------------------ the node group

def group():
    """The shared node group, built, or rebuilt when built by another version (modifiers keep their values)."""
    old = bpy.data.node_groups.get(GROUP)
    if old is not None and old.get(VERSION_KEY) == VERSION:
        return old
    new = bpy.data.node_groups.new(GROUP if old is None else GROUP + " new", 'GeometryNodeTree')
    _build(new)
    new[VERSION_KEY] = VERSION
    if old is not None:
        _swap(old, new)
    return new


def _swap(old, new):
    for o in bpy.data.objects:
        for m in o.modifiers:
            if m.type == 'NODES' and m.node_group == old:
                values = _values(m)
                m.node_group = new
                for name, value in values.items():
                    try:
                        _set(m, name, value)
                    except (KeyError, TypeError, ValueError, AttributeError):
                        pass        # an input this version dropped or retyped
    old.user_remap(new)         # (a node editor showing it)
    bpy.data.node_groups.remove(old)
    new.name = GROUP


class _Nodes:
    """Node-building shorthand: nodes on a grid of columns and rows, inputs fed links or values."""

    def __init__(self, tree):
        self.tree = tree

    def new(self, kind, col, row, **props):
        n = self.tree.nodes.new(kind)
        n.location = (col * 200, -row * 160)
        for key, value in props.items():
            setattr(n, key, value)
        return n

    def feed(self, socket, value):
        if isinstance(value, bpy.types.NodeSocket):
            self.tree.links.new(value, socket)
        elif value is not None:
            socket.default_value = value

    def node(self, kind, col, row, inputs=(), **props):
        """A node with its inputs fed in order (or by name: pairs)."""
        n = self.new(kind, col, row, **props)
        for i, value in enumerate(inputs):
            if isinstance(value, tuple) and isinstance(value[0], str):
                self.feed(n.inputs[value[0]], value[1])
            else:
                self.feed(n.inputs[i], value)
        return n

    def math(self, op, a, b=None, col=0, row=0):
        return self.node("ShaderNodeMath", col, row, (a, b), operation=op).outputs[0]

    def mad(self, a, b, c, col=0, row=0):
        """a x b + c"""
        return self.node("ShaderNodeMath", col, row, (a, b, c), operation='MULTIPLY_ADD').outputs[0]

    def compare(self, op, a, b, col=0, row=0):
        return self.node("FunctionNodeCompare", col, row, (a, b), data_type='FLOAT', operation=op).outputs[0]

    def both(self, a, b, col=0, row=0, op='AND'):
        return self.node("FunctionNodeBooleanMath", col, row, (a, b), operation=op).outputs[0]

    def within(self, value, lo, hi, col=0, row=0):
        return self.both(self.compare('GREATER_EQUAL', value, lo, col, row),
                         self.compare('LESS_EQUAL', value, hi, col, row + 1), col + 1, row)

    def xyz(self, vector, col=0, row=0):
        return self.node("ShaderNodeSeparateXYZ", col, row, (vector,)).outputs

    def switch(self, kind, condition, off, on, col=0, row=0):
        return self.node("GeometryNodeSwitch", col, row, (condition, off, on), input_type=kind).outputs[0]

    def menu(self, kind, names, values, menu, col=0, row=0):
        n = self.new("GeometryNodeMenuSwitch", col, row, data_type=kind)
        n.enum_items.clear()
        for name in names:
            n.enum_items.new(name)
        self.feed(n.inputs["Menu"], menu)
        for name, value in zip(names, values):
            self.feed(n.inputs[name], value)
        return n.outputs        # [0]: the value; [name]: whether that item is picked

    def channel(self, colour, index, alpha=None, col=0, row=0):
        """Channel `index` (R, G, B, A) of a colour (its alpha: `alpha` when given)."""
        rgb = self.node("FunctionNodeSeparateColor", col, row, (colour,)).outputs
        alpha = rgb[3] if alpha is None else alpha
        n = self.new("GeometryNodeIndexSwitch", col + 1, row, data_type='FLOAT')
        n.index_switch_items.new()
        n.index_switch_items.new()
        self.feed(n.inputs["Index"], index)
        for i, value in enumerate((rgb[0], rgb[1], rgb[2], alpha)):
            self.feed(n.inputs[i + 1], value)
        return n.outputs[0]

    def store(self, geometry, name, kind, domain, value, col=0, row=0, selection=None):
        return self.node("GeometryNodeStoreNamedAttribute", col, row, (geometry, selection, name, value),
                         data_type=kind, domain=domain).outputs[0]

    def attribute(self, kind, name, col=0, row=0):
        return self.node("GeometryNodeInputNamedAttribute", col, row, (name,), data_type=kind).outputs


def _interface(tree):
    face = tree.interface
    face.new_socket("Geometry", in_out='INPUT', socket_type='NodeSocketGeometry')
    face.new_socket("Geometry", in_out='OUTPUT', socket_type='NodeSocketGeometry')
    panel = None
    for row in INPUTS:
        if isinstance(row, str):
            panel = face.new_panel(row, default_closed=row not in ("Points", "Weight"))
            continue
        name, kind, default, lo, hi, desc = row
        s = face.new_socket(name, in_out='INPUT', socket_type=kind, parent=panel)
        s.description = desc
        if hasattr(s, "force_non_field"):
            s.force_non_field = True
        if default is not None and kind != 'NodeSocketMenu':
            s.default_value = default
        if lo is not None:
            s.min_value = lo
        if hi is not None:
            s.max_value = hi
        if name == "Tag":
            s.hide_in_modifier = True


def _build(tree):
    _interface(tree)
    b = _Nodes(tree)
    gi = b.new("NodeGroupInput", -12, 0)
    I = gi.outputs
    world, matrix, camera, has_camera = _world(b, I)
    mesh = _precull(b, I, world, camera, has_camera)
    # triangles (split as Blender tessellates quads): normals are the triangle's, as UE aligns to
    mesh = b.node("GeometryNodeTriangulate", -1, 1, (mesh, ("Quad Method", 'Fixed'))).outputs[0]
    painted, by_image = _painted(b, I)
    mesh = b.store(mesh, WEIGHT, 'FLOAT', 'CORNER', painted, -1, 2)
    as_ue = b.menu('BOOLEAN', PLACEMENT, (False, True), I["Placement"], 0, -2)[0]
    points = b.switch('GEOMETRY', as_ue, _random(b, I, mesh), _ue(b, I, mesh, camera, has_camera), 4, 0)
    weight = _weight(b, I, by_image)
    normal = b.attribute('FLOAT_VECTOR', NORMAL, 4, 10)["Attribute"]
    # UE: N x sign(N.z), a face turned down counts as up
    up = b.math('SIGN', b.xyz(normal, 5, 9)[2], None, 6, 9)
    normal = b.node("ShaderNodeVectorMath", 7, 10, (normal, ("Scale", up)), operation='SCALE').outputs[0]
    keep, cull = _keep(b, I, weight, normal, camera, has_camera)
    item = b.node("GeometryNodeObjectInfo", 8, 6, (I["Instance"], True), transform_space='ORIGINAL')
    inst = b.node("GeometryNodeInstanceOnPoints", 9, 0, (points, keep, item.outputs["Geometry"], None, None,
                                                         _rotation(b, I, normal), _scale(b, I, weight))).outputs[0]
    inst = b.store(inst, I["Tag"], 'BOOLEAN', 'INSTANCE', True, 10, 0)
    inst = b.store(inst, FADE, 'FLOAT', 'INSTANCE', _fade(b, I, camera), 11, 0, selection=cull)
    back = b.node("FunctionNodeInvertMatrix", 11, 3, (matrix,))
    local = b.node("GeometryNodeTransform", 12, 0, (inst, ("Mode", 'Matrix'), ("Transform", back.outputs["Matrix"])))
    join = b.new("GeometryNodeJoinGeometry", 13, 0)
    b.feed(join.inputs[0], I["Geometry"])
    b.feed(join.inputs[0], local.outputs[0])
    out = b.new("NodeGroupOutput", 14, 0)
    b.feed(out.inputs[0], join.outputs[0])
    _set_menu_defaults(tree)


def _set_menu_defaults(tree):
    for row in INPUTS:
        if not isinstance(row, str) and row[1] == 'NodeSocketMenu':
            _item(tree, row[0]).default_value = row[2]


def _world(b, I):
    """The mesh in world space, the object's matrix, the active camera's location and whether there is one."""
    me = b.new("GeometryNodeSelfObject", -12, 4)
    info = b.node("GeometryNodeObjectInfo", -11, 4, (me.outputs[0],), transform_space='ORIGINAL')
    mesh = b.node("GeometryNodeSeparateComponents", -11, 0, (I["Geometry"],)).outputs["Mesh"]
    world = b.node("GeometryNodeTransform", -10, 0, (mesh, ("Mode", 'Matrix'), ("Transform", info.outputs["Transform"])))
    cam = b.new("GeometryNodeInputActiveCamera", -12, 7)
    where = b.node("GeometryNodeObjectInfo", -11, 7, (cam.outputs[0],), transform_space='ORIGINAL')
    # no camera: a zero matrix
    has = b.node("FunctionNodeInvertMatrix", -10, 7, (where.outputs["Transform"],)).outputs["Invertible"]
    return world.outputs[0], info.outputs["Transform"], where.outputs["Location"], has


def _culling(b, I, has_camera, col, row):
    return b.both(I["Camera Cull"], has_camera, col, row)


def _precull(b, I, mesh, camera, has_camera):
    """Faces wholly beyond Cull End dropped before points are made (a map-sized terrain keeps few)."""
    centre = b.new("GeometryNodeInputPosition", -10, 9).outputs[0]
    far = b.node("ShaderNodeVectorMath", -9, 9, (centre, camera), operation='DISTANCE').outputs["Value"]
    ends = b.new("GeometryNodeInputMeshEdgeVertices", -10, 11).outputs
    edge = b.node("ShaderNodeVectorMath", -9, 11, (ends["Position 1"], ends["Position 2"]),
                  operation='DISTANCE').outputs["Value"]
    mean = b.node("GeometryNodeFieldOnDomain", -8, 11, (edge,), domain='EDGE', data_type='FLOAT').outputs[0]
    corners = b.new("GeometryNodeCornersOfFace", -8, 12).outputs["Total"]
    # every point of a face lies within half its perimeter of its centre
    reach = b.math('MULTIPLY', b.math('MULTIPLY', mean, corners, -7, 11), 0.5, -6, 11)
    beyond = b.compare('GREATER_THAN', b.math('SUBTRACT', far, reach, -5, 9), I["Cull End"], -4, 9)
    drop = b.both(beyond, _culling(b, I, has_camera, -3, 10), -2, 9)
    return b.node("GeometryNodeDeleteGeometry", -2, 0, (mesh, drop), domain='FACE', mode='ALL').outputs[0]


def _painted(b, I):
    """Paint mask x the attribute sources, on the mesh's corners (an image source is read on the points)."""
    mask = b.attribute('FLOAT', I["Mask"], -6, 14)
    painted = b.switch('FLOAT', mask["Exists"], 1.0, mask["Attribute"], -5, 14)
    plain = b.attribute('FLOAT', I["Source Attribute"], -6, 16)["Attribute"]
    colour = b.attribute('FLOAT_COLOR', I["Source Attribute"], -6, 18)["Attribute"]
    layer = b.channel(colour, I["Source Channel"], None, -5, 18)
    source = b.menu('FLOAT', SOURCES, (1.0, plain, layer, 1.0), I["Weight Source"], -3, 16)
    return b.math('MULTIPLY', painted, source[0], -2, 14), source["Image"]


def _random(b, I, mesh):
    """Fortnite's placement: random points at Density (its PCG ignores Use Grid and Jitter)."""
    per_m2 = b.math('DIVIDE', I["Density"], 100.0, -1, 3)
    loose = b.node("GeometryNodeDistributePointsOnFaces", 0, 0, (mesh, None, None, None, per_m2, None, I["Seed"]),
                   distribute_method='RANDOM')
    return b.store(loose.outputs["Points"], NORMAL, 'FLOAT_VECTOR', 'POINT', loose.outputs["Normal"], 1, 0)


def _ue(b, I, mesh, camera, has_camera):
    """UE's placement over the mesh's world XY bounds (as one landscape component): SqrtMax = ceil(sqrt(area x
    Density)) and SqrtMax² points, on the grid (Use Grid) or the Halton (2, 3) sequence, dropped onto the mesh."""
    box = b.node("GeometryNodeBoundBox", 0, 4, (mesh,)).outputs
    size = b.xyz(b.node("ShaderNodeVectorMath", 1, 4, (box["Max"], box["Min"]), operation='SUBTRACT').outputs[0], 2, 4)
    low = b.xyz(box["Min"], 2, 6)
    top = b.math('ADD', b.xyz(box["Max"], 2, 7)[2], 1.0, 3, 7)
    area = b.math('MULTIPLY', b.math('MULTIPLY', size[0], size[1], 0, 8), I["Density"], 1, 8)
    side = b.math('MAXIMUM', b.math('CEIL', b.math('SQRT', b.math('DIVIDE', area, 100.0, 2, 8), None, 3, 8), None,
                                    4, 8), 1.0, 5, 8)
    total = b.node("FunctionNodeIntegerMath", 6, 8, (side, side), operation='MULTIPLY').outputs[0]
    grid = _Grid(b, I, side, size, low, top)
    index = b.new("GeometryNodeInputIndex", 3, 11).outputs[0]
    on_grid = grid.start(index)
    loose = _halton_start(b, I, index, total, size, low, top)
    points = b.node("GeometryNodePoints", 10, 4, (total, b.switch('VECTOR', I["Use Grid"], loose, on_grid, 9, 10)))
    points = b.store(points.outputs[0], CELL, 'INT', 'POINT', index, 11, 4)      # before culling renumbers them
    # beyond Cull End across the ground: beyond it on the surface too
    gap = b.xyz(b.node("ShaderNodeVectorMath", 10, 8, (b.new("GeometryNodeInputPosition", 9, 8).outputs[0], camera),
                       operation='SUBTRACT').outputs[0], 11, 8)
    across = b.math('SQRT', b.math('ADD', b.math('MULTIPLY', gap[0], gap[0], 12, 8),
                                   b.math('MULTIPLY', gap[1], gap[1], 12, 9), 13, 8), None, 14, 8)
    far = b.both(b.compare('GREATER_THAN', across, I["Cull End"], 15, 8), _culling(b, I, has_camera, 15, 9), 16, 8)
    points = b.node("GeometryNodeDeleteGeometry", 12, 4, (points, far), domain='POINT').outputs[0]
    return _drop(b, I, mesh, points, b.math('ADD', size[2], 2.0, 12, 6), grid)


class _Grid:
    """UE's jittered grid: cell (i, j) of SqrtMax a side at its centre, moved by (rand x 2 - 1) x clamp(Jitter, 0, 0.99)
    x half a cell per axis (the random of the cell's index, so a neighbour's place can be found again)."""

    def __init__(self, b, I, side, size, low, top):
        self.b, self.side, self.low, self.top = b, side, low, top
        self.cell = [b.math('DIVIDE', size[axis], side, 6, 4 + axis) for axis in (0, 1)]
        self.jitter = b.math('MULTIPLY', b.math('MINIMUM', b.math('MAXIMUM', I["Jitter"], 0.0, 0, 9), 0.99, 1, 9), 0.5,
                             2, 9)
        self.seed = b.node("FunctionNodeIntegerMath", 0, 10, (I["Seed"], 3), operation='ADD').outputs[0]

    def cells(self, index):
        """(i, j) of a cell index."""
        b = self.b
        return (b.node("FunctionNodeIntegerMath", 4, 12, (index, self.side), operation='FLOORED_MODULO').outputs[0],
                b.node("FunctionNodeIntegerMath", 4, 13, (index, self.side), operation='DIVIDE_FLOOR').outputs[0])

    def at(self, i, j):
        """The start point (above the mesh) of cell (i, j)."""
        b = self.b
        index = b.node("FunctionNodeIntegerMath", 5, 14, (j, self.side, i), operation='MULTIPLY_ADD').outputs[0]
        rnd = b.xyz(b.node("FunctionNodeRandomValue", 6, 14, (("ID", index), ("Seed", self.seed)),
                           data_type='FLOAT_VECTOR').outputs[0], 7, 14)
        xy = []
        for axis, step in enumerate((i, j)):
            centre = b.mad(b.math('ADD', step, 0.5, 8, 12 + axis), self.cell[axis], self.low[axis], 9, 12 + axis)
            stray = b.math('MULTIPLY', b.mad(rnd[axis], 2.0, -1.0, 8, 14 + axis),
                           b.math('MULTIPLY', self.jitter, self.cell[axis], 9, 14 + axis), 10, 14 + axis)
            xy.append(b.math('ADD', centre, stray, 11, 12 + axis))
        return b.node("ShaderNodeCombineXYZ", 12, 12, (xy[0], xy[1], self.top)).outputs[0]

    def start(self, index):
        return self.at(*self.cells(index))


def _halton(b, index, base, digits, row):
    """The radical inverse of `index` in `base` (UE's Halton), over its first `digits` digits."""
    value = 0.0
    for k in range(digits):
        q = b.node("FunctionNodeIntegerMath", 4 + k, row, (index, base ** k), operation='DIVIDE_FLOOR').outputs[0]
        digit = b.node("FunctionNodeIntegerMath", 4 + k, row + 1, (q, base), operation='FLOORED_MODULO').outputs[0]
        value = b.mad(digit, float(base) ** -(k + 1), value, 4 + k, row + 2)
    return value


def _halton_start(b, I, index, total, size, low, top):
    """Point `index` of the Halton (2, 3) sequence over the bounds; the seed picks another run of it (UE: the run
    after the earlier components')."""
    first = b.node("FunctionNodeIntegerMath", 3, 20, (I["Seed"], total, 1), operation='MULTIPLY_ADD').outputs[0]
    n = b.node("FunctionNodeIntegerMath", 3, 21, (b.node("FunctionNodeIntegerMath", 3, 22, (index, first),
                                                         operation='ADD').outputs[0], HALTON_WRAP),
               operation='FLOORED_MODULO').outputs[0]
    x = b.mad(_halton(b, n, 2, 24, 20), size[0], low[0], 30, 20)
    y = b.mad(_halton(b, n, 3, 16, 24), size[1], low[1], 30, 24)
    return b.node("ShaderNodeCombineXYZ", 31, 22, (x, y, top)).outputs[0]


def _cast(b, mesh, start, length, kind='FLOAT', value=None, col=0, row=0):
    return b.node("GeometryNodeRaycast", col, row, (mesh, value, None, start, (0.0, 0.0, -1.0), length),
                  data_type=kind).outputs


def _drop(b, I, mesh, points, length, grid):
    """Points cast down onto the mesh: moved to the hit, given its weight, UV and normal (the triangle's, or with
    Use Grid and no Triangle Normals UE's smooth one from the neighbouring cells); misses dropped."""
    position = b.new("GeometryNodeInputPosition", 12, 15).outputs[0]
    weight = _cast(b, mesh, position, length, 'FLOAT', b.attribute('FLOAT', WEIGHT, 11, 16)["Attribute"], 13, 16)
    uv = _cast(b, mesh, position, length, 'FLOAT_VECTOR',
               b.attribute('FLOAT_VECTOR', weightmaps.UV, 11, 18)["Attribute"], 13, 18)
    smooth = b.both(I["Use Grid"], I["Triangle Normals"], 13, 22, op='NIMPLY')
    normal = b.switch('VECTOR', smooth, weight["Hit Normal"], _smooth(b, mesh, length, grid, weight["Hit Position"]),
                      14, 20)
    hit = b.new("GeometryNodeCaptureAttribute", 15, 4, domain='POINT')
    captured = {}
    for name, kind, value in (("Hit", 'BOOLEAN', weight["Is Hit"]), ("Position", 'VECTOR', weight["Hit Position"]),
                              ("Normal", 'VECTOR', normal), ("Weight", 'FLOAT', weight["Attribute"]),
                              ("UV", 'VECTOR', uv["Attribute"])):
        hit.capture_items.new(kind, name)
        b.feed(hit.inputs[name], value)
        captured[name] = hit.outputs[name]
    b.feed(hit.inputs["Geometry"], points)
    miss = b.node("FunctionNodeBooleanMath", 16, 6, (captured["Hit"],), operation='NOT').outputs[0]
    geo = b.node("GeometryNodeDeleteGeometry", 16, 4, (hit.outputs["Geometry"], miss), domain='POINT').outputs[0]
    geo = b.node("GeometryNodeSetPosition", 17, 4, (geo, None, captured["Position"])).outputs[0]
    geo = b.store(geo, WEIGHT, 'FLOAT', 'POINT', captured["Weight"], 18, 4)
    geo = b.store(geo, weightmaps.UV, 'FLOAT2', 'POINT', captured["UV"], 19, 4)
    return b.store(geo, NORMAL, 'FLOAT_VECTOR', 'POINT', captured["Normal"], 20, 4)


def _smooth(b, mesh, length, grid, here):
    """UE's grid-based normal: (P(i-1) - P(i+1)) x (P(j-1) - P(j+1)) of the neighbouring cells' surface points (a
    cell on the edge uses its own), up when they coincide."""
    i, j = grid.cells(b.attribute('INT', CELL, 12, 26)["Attribute"])
    last = b.node("FunctionNodeIntegerMath", 13, 26, (grid.side, 1), operation='SUBTRACT').outputs[0]
    ends = []
    for axis, (step, other) in enumerate(((i, j), (j, i))):
        pair = []
        for k, (delta, edge, op) in enumerate(((-1, 0, 'GREATER_THAN'), (1, last, 'LESS_THAN'))):
            row = 26 + axis * 4 + k * 2
            moved = b.node("FunctionNodeIntegerMath", 14, row, (step, delta), operation='ADD').outputs[0]
            cell = (moved, other) if axis == 0 else (other, moved)
            there = _cast(b, mesh, grid.at(*cell), length, col=15, row=row)
            inside = b.both(b.node("FunctionNodeCompare", 16, row, (step, edge), data_type='INT',
                                   operation=op).outputs[0], there["Is Hit"], 17, row)
            pair.append(b.switch('VECTOR', inside, here, there["Hit Position"], 18, row))
        ends.append(b.node("ShaderNodeVectorMath", 19, 26 + axis * 4, pair, operation='SUBTRACT').outputs[0])
    cross = b.node("ShaderNodeVectorMath", 20, 26, ends, operation='CROSS_PRODUCT').outputs[0]
    flat = b.compare('LESS_THAN', b.node("ShaderNodeVectorMath", 21, 27, (cross,), operation='LENGTH').outputs["Value"],
                     1e-12, 22, 27)
    unit = b.node("ShaderNodeVectorMath", 21, 26, (cross,), operation='NORMALIZE').outputs[0]
    return b.switch('VECTOR', flat, unit, (0.0, 0.0, 1.0), 23, 26)


def _weight(b, I, by_image):
    """The points' weight: mask x attribute source (from the mesh) x image source."""
    uv = b.attribute('FLOAT_VECTOR', weightmaps.UV, 0, 22)["Attribute"]
    image = b.node("GeometryNodeImageTexture", 1, 22, (I["Source Image"], uv), interpolation='Linear',
                   extension='EXTEND')
    texel = b.channel(image.outputs["Color"], I["Source Channel"], image.outputs["Alpha"], 2, 22)
    source = b.switch('FLOAT', by_image, 1.0, texel, 4, 22)
    # clamped: interpolated weights of 1 come out a hair above it
    return b.node("ShaderNodeMath", 5, 20, (b.attribute('FLOAT', WEIGHT, 4, 20)["Attribute"], source),
                  operation='MULTIPLY', use_clamp=True).outputs[0]


def _keep(b, I, weight, normal, camera, has_camera):
    """Kept (UE): Min < weight <= Max of the density range and weight >= the point's random value; then slope and
    height in range, shown (Display %) and within the camera's reach. Returns (kept, whether culling)."""
    rnd = b.xyz(b.node("FunctionNodeRandomValue", 5, 8, (("Seed", I["Seed"]),), data_type='FLOAT_VECTOR').outputs[0],
                6, 8)
    keep = b.both(b.compare('GREATER_THAN', weight, I["Density Range Min"], 7, 7),
                  b.compare('LESS_EQUAL', weight, I["Density Range Max"], 7, 8), 8, 7)
    keep = b.both(keep, b.compare('LESS_EQUAL', rnd[0], weight, 7, 9), 9, 7)
    up = b.xyz(normal, 4, 10)[2]
    slope = b.math('DEGREES', b.math('ARCCOSINE', b.math('MINIMUM', up, 1.0, 5, 10), None, 6, 10), None, 7, 10)
    keep = b.both(keep, b.within(slope, I["Slope Min"], I["Slope Max"], 8, 10), 10, 8)
    position = b.new("GeometryNodeInputPosition", 4, 12).outputs[0]
    z = b.xyz(position, 5, 12)[2]
    keep = b.both(keep, b.within(z, I["Height Min"], I["Height Max"], 8, 12), 11, 8)
    viewport = b.new("GeometryNodeIsViewport", 6, 13).outputs[0]
    hidden = b.both(viewport, b.compare('GREATER_EQUAL', b.math('MULTIPLY', rnd[2], 100.0, 6, 14),
                                        I["Display %"], 7, 14), 8, 13)
    keep = b.both(keep, hidden, 12, 8, op='NIMPLY')
    far = b.compare('GREATER_THAN', b.node("ShaderNodeVectorMath", 6, 16, (position, camera),
                                           operation='DISTANCE').outputs["Value"], I["Cull End"], 7, 16)
    culling = _culling(b, I, has_camera, 8, 17)
    return b.both(keep, b.both(far, culling, 9, 16), 13, 8, op='NIMPLY'), culling


def _fade(b, I, camera):
    """0 at Cull Start from the camera, 1 at Cull End (UE's PerInstanceFadeAmount)."""
    position = b.new("GeometryNodeInputPosition", 10, 3).outputs[0]
    d = b.node("ShaderNodeVectorMath", 10, 4, (position, camera), operation='DISTANCE').outputs["Value"]
    return b.node("ShaderNodeMapRange", 11, 4, (d, I["Cull Start"], I["Cull End"], 0.0, 1.0), clamp=True).outputs[0]


def _scale(b, I, weight):
    """UE's GetRandomScale: each axis lerps its range at a random fraction (Uniform: X's for all; LockXY: X's for X and
    Y, Z's own). With Weight Attenuates Max Scale the fraction is scaled by clamp((w - Att) / (1 - Att)) (Att 1: 0)."""
    seed = b.node("FunctionNodeIntegerMath", 5, 24, (I["Seed"], 1), operation='ADD').outputs[0]
    rnd = b.xyz(b.node("FunctionNodeRandomValue", 6, 24, (("Seed", seed),), data_type='FLOAT_VECTOR').outputs[0],
                7, 24)
    # (Map Range gives 0 when Att = 1, as UE)
    factor = b.node("ShaderNodeMapRange", 6, 26, (weight, I["Attenuation"], 1.0, 0.0, 1.0), clamp=True).outputs[0]
    loss = b.math('MULTIPLY', b.math('SUBTRACT', 1.0, factor, 7, 26), I["Weight Attenuates Max Scale"], 8, 26)
    reach = b.math('SUBTRACT', 1.0, loss, 9, 26)
    axes = []
    for i, axis in enumerate("XYZ"):
        t = b.math('MULTIPLY', rnd[i], reach, 10, 24 + i)
        axes.append(b.node("ShaderNodeMapRange", 11, 24 + i * 2, (t, 0.0, 1.0, I["Scale %s Min" % axis],
                                                                  I["Scale %s Max" % axis]), clamp=False).outputs[0])
    x, y, z = axes
    vectors = [b.node("ShaderNodeCombineXYZ", 12, 24 + i, v).outputs[0]
               for i, v in enumerate(((x, x, x), (x, y, z), (x, x, z)))]
    return b.menu('VECTOR', SCALING, vectors, I["Scaling"], 13, 24)[0]


def _rotation(b, I, normal):
    """UE: a yaw (0-360) in the instance's own frame; aligned, that frame is Z = N, X = normalize((0, -1, 0) x N),
    Y = Z x X."""
    rnd = b.node("FunctionNodeRandomValue", 5, 30, (("Seed", I["Seed"]),), data_type='FLOAT_VECTOR').outputs[0]
    yaw = b.math('MULTIPLY', b.math('MULTIPLY', b.xyz(rnd, 6, 30)[1], 2.0 * math.pi, 7, 30), I["Random Rotation"],
                 8, 30)
    euler = b.node("ShaderNodeCombineXYZ", 9, 30, (0.0, 0.0, yaw)).outputs[0]
    turn = b.node("FunctionNodeEulerToRotation", 10, 30, (euler,)).outputs[0]
    side = b.node("ShaderNodeVectorMath", 9, 32, ((0.0, -1.0, 0.0), normal), operation='CROSS_PRODUCT').outputs[0]
    frame = b.node("FunctionNodeAxesToRotation", 10, 32, (normal, side), primary_axis='Z',
                   secondary_axis='X').outputs[0]
    aligned = b.node("FunctionNodeRotateRotation", 11, 32, (frame, turn), rotation_space='LOCAL').outputs[0]
    return b.switch('ROTATION', I["Align To Surface"], turn, aligned, 12, 30)


# ------------------------------------------------------------------ modifier inputs

def _item(group_, name):
    return next(i for i in group_.interface.items_tree
                if i.item_type == 'SOCKET' and i.in_out == 'INPUT' and i.name == name)


def _get(mod, name):
    identifier = _item(mod.node_group, name).identifier
    try:
        return getattr(mod.properties.inputs, identifier).value
    except AttributeError:
        return mod.get(identifier)


def _set(mod, name, value):
    identifier = _item(mod.node_group, name).identifier
    try:
        target = getattr(mod.properties.inputs, identifier)
    except AttributeError:
        mod[identifier] = value         # Blender before 5.x
        return
    if target.value != value:
        target.value = value


def _values(mod):
    return {i.name: _get(mod, i.name) for i in mod.node_group.interface.items_tree
            if i.item_type == 'SOCKET' and i.in_out == 'INPUT' and i.socket_type != 'NodeSocketGeometry'}


def _pair(value):
    lo, hi = list(value)[:2]
    return float(lo), float(hi)


def inputs(settings):
    """{input: value} of the settings given (the app's schema, UE units; plus placement "Fortnite"|"UE", slope [deg],
    height [cm], seed, display [%], camera_cull)."""
    out = {}
    if settings.get("placement") in PLACEMENT:
        out["Placement"] = settings["placement"]
    plain = {"density": ("Density", float), "use_grid": ("Use Grid", bool), "jitter": ("Jitter", float),
             "random_rotation": ("Random Rotation", bool), "align_to_surface": ("Align To Surface", bool),
             "align_to_triangle_normals": ("Triangle Normals", bool),
             "weight_attenuates_max_scale": ("Weight Attenuates Max Scale", bool),
             "max_scale_weight_attenuation": ("Attenuation", float), "seed": ("Seed", int),
             "display": ("Display %", float), "camera_cull": ("Camera Cull", bool)}
    for key, (name, kind) in plain.items():
        if settings.get(key) is not None:
            out[name] = kind(settings[key])
    if settings.get("scaling"):
        scaling = str(settings["scaling"]).split("::")[-1]
        out["Scaling"] = scaling if scaling in SCALING else "Uniform"
    ranges = {"scale_x": ("Scale X", 1.0), "scale_y": ("Scale Y", 1.0), "scale_z": ("Scale Z", 1.0),
              "density_range": ("Density Range", 1.0), "slope": ("Slope", 1.0), "height": ("Height", CM)}
    for key, (name, unit) in ranges.items():
        if settings.get(key) is not None:
            lo, hi = _pair(settings[key])
            out[name + " Min"], out[name + " Max"] = lo * unit, hi * unit
    if settings.get("cull") is not None:
        start, end = _pair(settings["cull"])
        out["Cull Start"], out["Cull End"] = start * CM, end * CM
    return out


def _shadow(instance_obj, settings):
    """cast_shadow false (Hera's grass: bCastDynamicShadow off): the instanced object casts none, wherever used."""
    if instance_obj is not None and settings.get("cast_shadow") is not None:
        instance_obj.visible_shadow = bool(settings["cast_shadow"])


def _area(obj):
    """The mesh's world area, m²."""
    me = obj.data
    if not len(me.polygons):
        return 0.0
    a = np.empty(len(me.polygons), dtype=np.float32)
    me.polygons.foreach_get("area", a)
    sx, sy, sz = obj.matrix_world.to_scale()
    return float(a.sum()) * abs(sx * sy)        # (a terrain: mostly flat)


def _large(obj, settings):
    """A map-sized terrain's defaults: Display % down to about VIEW_LIMIT instances and camera culling on."""
    if "display" in settings or "camera_cull" in settings:
        return {}
    expected = _area(obj) / 100.0 * float(settings.get("density", 400.0))
    if expected <= VIEW_LIMIT:
        return {}
    return {"Display %": max(1.0, round(100.0 * VIEW_LIMIT / expected, 1)), "Camera Cull": True}


# ------------------------------------------------------------------ layers

def _records(obj):
    return json.loads(obj.get(KEY) or "{}")


def _save(obj, records):
    obj[KEY] = json.dumps(records)


def _modifier(obj, layer):
    for m in obj.modifiers:
        if m.type == 'NODES' and m.node_group is not None and m.node_group.name.startswith(GROUP):
            try:
                if _get(m, "Tag") == TAG + layer:
                    return m
            except StopIteration:
                continue
    return None


def _apply(obj, mod, values):
    for name, value in values.items():
        _set(mod, name, value)
    obj.update_tag()


def add_layer(obj, settings, instance_obj, label=None, weight=None):
    """A new layer scattering `instance_obj` over the mesh with these settings; returns its id."""
    g = group()
    layer = uuid.uuid4().hex[:8]
    label = label or settings.get("name") or (instance_obj.name if instance_obj else "Scatter")
    mod = obj.modifiers.new(PREFIX + label, 'NODES')
    mod.node_group = g
    values = {"Instance": instance_obj, "Mask": MASK + layer, "Tag": TAG + layer}
    values.update(_large(obj, settings))
    values.update(inputs(settings))
    _apply(obj, mod, values)
    _shadow(instance_obj, settings)
    records = _records(obj)
    records[layer] = {"label": label, "source": settings.get("source") or ("game" if settings.get("type") else "custom"),
                      "type": settings.get("type", ""), "variety": settings.get("variety", 0), "weight": None,
                      "cast_shadow": bool(settings.get("cast_shadow", True))}
    _save(obj, records)
    set_weight(obj, layer, weight)
    return layer


def layers(obj):
    """[{id, label, modifier, source, type, variety, weight, cast_shadow, mask}] in the order they were added."""
    out = []
    for layer, record in _records(obj).items():
        mod = _modifier(obj, layer)
        out.append(dict(record, id=layer, modifier=mod.name if mod else None, mask=MASK + layer))
    return out


def _source(obj, weight):
    """Weight Source inputs of a weight form."""
    off = {"Weight Source": "None"}
    if not weight:
        return off
    if weight.get("attribute"):
        name = weight["attribute"]
        return {"Weight Source": "Attribute", "Source Attribute": name if name.startswith("mp_") else GRASS + name}
    if weight.get("image"):
        image = bpy.data.images.get(weight["image"])
        if image is None:
            raise ValueError("no image %r" % weight["image"])
        return {"Weight Source": "Image", "Source Image": image, "Source Channel": int(weight.get("channel", 0))}
    if weight.get("layer"):
        where = landscape_layers.table(obj).get(weight["layer"])
        if where is None:
            raise ValueError("%s has no landscape layer %r" % (obj.name, weight["layer"]))
        maps = weightmaps.images(obj)
        if where[0] in maps:
            return {"Weight Source": "Image", "Source Image": maps[where[0]], "Source Channel": where[1]}
        return {"Weight Source": "Layer", "Source Attribute": where[0], "Source Channel": where[1]}
    raise ValueError("unknown weight %r" % (weight,))


def set_weight(obj, layer, weight):
    """The layer's source weight: None, {"attribute": name}, {"layer": landscape layer}, {"image": name, "channel": i}.
    An attribute name without the mp_ prefix is a grass mask's (mp_grass_<name>)."""
    mod = _modifier(obj, layer)
    if mod is None:
        raise KeyError(layer)
    _apply(obj, mod, _source(obj, weight))
    records = _records(obj)
    records[layer]["weight"] = weight
    _save(obj, records)


def update_layer(obj, layer, settings):
    """Change a layer's settings (any subset of add_layer's; "label" renames it, "instance" swaps the object)."""
    mod = _modifier(obj, layer)
    if mod is None:
        raise KeyError(layer)
    values = inputs(settings)
    if settings.get("instance") is not None:
        values["Instance"] = settings["instance"]
    _apply(obj, mod, values)
    _shadow(_get(mod, "Instance"), settings)
    records = _records(obj)
    record = records[layer]
    for key in ("type", "variety", "source", "cast_shadow"):
        if key in settings:
            record[key] = settings[key]
    if settings.get("label"):
        record["label"] = settings["label"]
        mod.name = PREFIX + settings["label"]
    _save(obj, records)


def remove_layer(obj, layer):
    """The layer's modifier, mask attribute and record removed."""
    mod = _modifier(obj, layer)
    if mod is not None:
        obj.modifiers.remove(mod)
    mask = obj.data.attributes.get(MASK + layer)
    if mask is not None:
        obj.data.attributes.remove(mask)
    records = _records(obj)
    records.pop(layer, None)
    _save(obj, records)


def count(obj, layer, depsgraph=None):
    """How many instances the layer shows now (the evaluated viewport geometry; `depsgraph` another one)."""
    depsgraph = depsgraph or bpy.context.evaluated_depsgraph_get()
    ev = obj.evaluated_get(depsgraph)
    geometry = bpy.types.GeometrySet.from_evaluated_object(ev)      # (owns the cloud: keep it alive)
    cloud = geometry.instances_pointcloud()
    if cloud is None:
        return 0
    tag = cloud.attributes.get(TAG + layer)
    if tag is None:
        return 0
    marks = np.zeros(len(tag.data), dtype=bool)
    tag.data.foreach_get("value", marks)
    return int(marks.sum())
