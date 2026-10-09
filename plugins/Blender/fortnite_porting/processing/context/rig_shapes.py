"""Control shapes the creature and vehicle rigs build themselves (Tasty's CTRL_ shapes come from
the add-on's data blend; these are created once per file). Each is a wire in its XY plane, Z up."""

from math import cos, pi, radians, sin

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


def _circle(steps=32):
    """Circle of radius 1 about Z."""
    verts = [(cos(radians(360.0 * i / steps)), sin(radians(360.0 * i / steps)), 0.0) for i in range(steps)]
    return verts, [(i, (i + 1) % steps) for i in range(steps)]


def _circle_tick():
    """Circle with a tick past its top (+Y), so a turn reads at a glance."""
    verts, edges = _circle()
    verts += [(0.0, 1.0, 0.0), (0.0, 1.35, 0.0)]
    edges.append((len(verts) - 2, len(verts) - 1))
    return verts, edges


def _square():
    """Square of half-size 1."""
    verts = [(-1.0, -1.0, 0.0), (1.0, -1.0, 0.0), (1.0, 1.0, 0.0), (-1.0, 1.0, 0.0)]
    return verts, [(i, (i + 1) % 4) for i in range(4)]


def _box():
    """Wire cube of half-size 1."""
    verts = [(x, y, z) for z in (-1.0, 1.0) for y in (-1.0, 1.0) for x in (-1.0, 1.0)]
    edges = [(0, 1), (2, 3), (4, 5), (6, 7), (0, 2), (1, 3), (4, 6), (5, 7), (0, 4), (1, 5), (2, 6), (3, 7)]
    return verts, edges


def _diamond():
    """Octahedron of radius 1 (a pole: readable from any side)."""
    verts = [(1.0, 0.0, 0.0), (-1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, -1.0, 0.0), (0.0, 0.0, 1.0), (0.0, 0.0, -1.0)]
    edges = [(a, b) for a in range(6) for b in range(a + 1, 6) if a // 2 != b // 2]
    return verts, edges


def _gear(teeth=8):
    """Gear: a ring of radius 0.75 with `teeth` square teeth out to 1, and a hub circle."""
    verts = []
    for i in range(teeth):
        a, step = 2.0 * pi * i / teeth, 2.0 * pi / teeth
        for angle, r in ((a, 0.75), (a + step * 0.15, 1.0), (a + step * 0.45, 1.0), (a + step * 0.6, 0.75)):
            verts.append((r * cos(angle), r * sin(angle), 0.0))
    edges = [(i, (i + 1) % len(verts)) for i in range(len(verts))]
    hub, hub_edges = _circle(16)
    first = len(verts)
    verts += [(x * 0.3, y * 0.3, 0.0) for x, y, _ in hub]
    edges += [(a + first, b + first) for a, b in hub_edges]
    return verts, edges


def _circle_arrow():
    """Circle with a chevron ahead of it (+Y): a ground control that shows which way the rig faces."""
    verts, edges = _circle()
    verts += [(-0.18, 1.12, 0.0), (0.0, 1.3, 0.0), (0.18, 1.12, 0.0)]
    n = len(verts)
    edges += [(n - 3, n - 2), (n - 2, n - 1)]
    return verts, edges


SHAPES = {"CR_Arrow": _arrow, "CR_Turn": _turn, "CR_Swing": lambda: _turn(18.0), "CR_Foot": _foot, "CR_Glasses": _glasses,
          "CR_UpDown": _updown, "CR_Circle": _circle, "CR_CircleTick": _circle_tick, "CR_Square": _square, "CR_Box": _box,
          "CR_Diamond": _diamond, "CR_Gear": _gear, "CR_CircleArrow": _circle_arrow}


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
