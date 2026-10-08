"""Control shapes the creature and vehicle rigs build themselves (Tasty's CTRL_ shapes come from
the add-on's data blend; these are created once per file). Each is a wire in its XY plane, Z up."""

from math import cos, radians, sin

import bpy
from mathutils import Vector


def _arrow():
    """Arrow from the origin to +Y 1, 0.4 wide at the head."""
    shaft, head, neck = 0.08, 0.2, 0.65
    points = [(-shaft, 0.0), (shaft, 0.0), (shaft, neck), (head, neck), (0.0, 1.0), (-head, neck), (-shaft, neck)]
    verts = [(x, y, 0.0) for x, y in points]
    return verts, [(i, (i + 1) % len(verts)) for i in range(len(verts))]


def _turn(degrees=55.0):
    """Arc of radius 1 about Z, `degrees` each side of +Y, with an arrowhead at each end."""
    span, steps = radians(degrees), 24
    angles = [-span + 2.0 * span * i / steps for i in range(steps + 1)]
    verts = [(sin(a), cos(a), 0.0) for a in angles]
    edges = [(i, i + 1) for i in range(steps)]
    for end, sign in ((0, -1.0), (steps, 1.0)):
        a = sign * span
        tip = Vector((sin(a), cos(a), 0.0))
        along = Vector((cos(a), -sin(a), 0.0)) * sign       # tangent pointing out of the arc's end
        for side in (-1.0, 1.0):
            verts.append(tuple(tip - along * 0.14 + tip * 0.07 * side))
            edges.append((end, len(verts) - 1))
    return verts, edges


def _foot():
    """Footprint about the origin: rounded box 0.6 wide (X), 1 long (Y), pointed at the front."""
    verts, steps, r = [], 6, 0.2
    for cx, cy, start in ((0.1, 0.3, 0.0), (-0.1, 0.3, 90.0), (-0.1, -0.3, 180.0), (0.1, -0.3, 270.0)):
        for i in range(steps + 1):
            a = radians(start + 90.0 * i / steps)
            verts.append((cx + r * cos(a), cy + r * sin(a), 0.0))
    edges = [(i, (i + 1) % len(verts)) for i in range(len(verts))]
    verts += [(-0.12, 0.62, 0.0), (0.0, 0.72, 0.0), (0.12, 0.62, 0.0)]     # front chevron
    n = len(verts)
    edges += [(n - 3, n - 2), (n - 2, n - 1)]
    return verts, edges


def _glasses():
    """Two rings in the XZ plane (facing Y) at X -1 and 1, 0.55 radius, joined by a bridge."""
    verts, edges, steps = [], [], 16
    for cx in (-1.0, 1.0):
        first = len(verts)
        verts += [(cx + 0.55 * cos(radians(360.0 * i / steps)), 0.0, 0.55 * sin(radians(360.0 * i / steps))) for i in range(steps)]
        edges += [(first + i, first + (i + 1) % steps) for i in range(steps)]
    verts += [(-0.45, 0.0, 0.1), (0.45, 0.0, 0.1)]
    edges.append((len(verts) - 2, len(verts) - 1))
    return verts, edges


def _updown():
    """Double arrow along Y (-1 to 1) drawn in the XY and ZY planes, so it shows from any side."""
    verts, edges = [], []
    for across in (0, 2):
        first = len(verts)
        for a, b in ((0.0, -1.0), (0.0, 1.0), (-0.35, 0.6), (0.35, 0.6), (-0.35, -0.6), (0.35, -0.6)):
            v = [0.0, b, 0.0]
            v[across] = a
            verts.append(tuple(v))
        edges += [(first, first + 1), (first + 1, first + 2), (first + 1, first + 3), (first, first + 4), (first, first + 5)]
    return verts, edges


SHAPES = {"CR_Arrow": _arrow, "CR_Turn": _turn, "CR_Swing": lambda: _turn(18.0), "CR_Foot": _foot, "CR_Glasses": _glasses, "CR_UpDown": _updown}


def ensure(name):
    """The shape object, created on first use."""
    obj = bpy.data.objects.get(name)
    if obj is not None and obj.type == 'MESH':
        return obj
    verts, edges = SHAPES[name]()
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, edges, [])
    return bpy.data.objects.new(name, mesh)


def place(obj, pose_bone, at):
    """Offset a control's shape so it is drawn at `at` (armature space)."""
    bone = obj.data.bones[pose_bone.name]
    pose_bone.custom_shape_translation = bone.matrix_local.to_3x3().inverted() @ (at - bone.head_local)


def color(pose_bone, rgb):
    """Custom colour for a control (theme palettes are too dark for wires): `rgb` 0-1, lighter when selected."""
    pose_bone.color.palette = 'CUSTOM'
    pose_bone.color.custom.normal = rgb
    pose_bone.color.custom.select = tuple(min(1.0, c * 0.5 + 0.5) for c in rgb)
    pose_bone.color.custom.active = (1.0, 1.0, 1.0)


def footprint(name, length, width):
    """Footprint `length` along Y and `width` across, rounded corners, chevron at the front.
    Built at its final size because rounded corners don't scale."""
    hx, hy = width / 2.0, length / 2.0
    r, steps = min(width, length) * 0.18, 6
    verts = []
    for cx, cy, start in ((hx - r, hy - r, 0.0), (r - hx, hy - r, 90.0), (r - hx, r - hy, 180.0), (hx - r, r - hy, 270.0)):
        for i in range(steps + 1):
            a = radians(start + 90.0 * i / steps)
            verts.append((cx + r * cos(a), cy + r * sin(a), 0.0))
    edges = [(i, (i + 1) % len(verts)) for i in range(len(verts))]
    tip, wing = hy - length * 0.05, hy - length * 0.13
    verts += [(-width * 0.18, wing, 0.0), (0.0, tip, 0.0), (width * 0.18, wing, 0.0)]
    n = len(verts)
    edges += [(n - 3, n - 2), (n - 2, n - 1)]
    obj = bpy.data.objects.get(name)
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, edges, [])
    if obj is None or obj.type != 'MESH':
        return bpy.data.objects.new(name, mesh)
    obj.data = mesh
    return obj
