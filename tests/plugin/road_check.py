"""Checks the road-from-curve helper: a road segment repeated along a curve with its forward axis, starting at the curve's
start, and white vertex colours when the segment has none.

    blender -b --factory-startup --python-exit-code 1 -P tests/plugin/road_check.py -- <plugin parent>

<plugin parent> holds the plugin as package fpmp_baseline (see translator_test.py). Exit code 0 when every check passes."""
import sys

import bmesh
import bpy
from mathutils import Matrix, Vector

sys.path.insert(0, sys.argv[sys.argv.index("--") + 1])
import fpmp_baseline  # noqa: E402,F401
from fpmp_baseline.material_porter import road  # noqa: E402

FAILS, PASSES = [], [0]


def check(name, got, want, tol=1e-3):
    ok = abs(got - want) <= tol if isinstance(want, float) else got == want
    if ok:
        PASSES[0] += 1
    else:
        FAILS.append(name)
        print("[road_check] FAIL %s: got %r, want %r" % (name, got, want))


scene = bpy.context.scene


def segment(name, start=0.0):
    """A 2 m long, 1 m wide strip along X from `start`, cut into 4 so it can bend."""
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=4, y_segments=1, size=1.0)
    bmesh.ops.transform(bm, matrix=Matrix.Translation((start + 1.0, 0.0, 0.0)) @ Matrix.Diagonal((1.0, 0.5, 1.0, 1.0)), verts=bm.verts)
    bm.to_mesh(me)
    obj = bpy.data.objects.new(name, me)
    scene.collection.objects.link(obj)
    return obj


def line(name, length, location=(0.0, 0.0, 0.0)):
    cu = bpy.data.curves.new(name, 'CURVE')
    cu.dimensions = '3D'
    sp = cu.splines.new('POLY')
    sp.points.add(1)
    sp.points[0].co = (0.0, 0.0, 0.0, 1.0)
    sp.points[1].co = (length, 0.0, 0.0, 1.0)
    obj = bpy.data.objects.new(name, cu)
    obj.location = location
    scene.collection.objects.link(obj)
    return obj


def evaluated_x(obj):
    bpy.context.view_layer.update()
    ev = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    xs = [(ev.matrix_world @ v.co).x for v in ev.data.vertices]
    return min(xs), max(xs)


seg, path = segment("seg"), line("path", 10.0, (5.0, 0.0, 0.0))
built = road.road_from_curve(seg, path)
lo, hi = evaluated_x(built)
check("the road starts at the curve's start", round(lo, 3), 5.0)
check("and runs its length", round(hi - lo, 1), 10.0)
check("array then curve", [m.type for m in built.modifiers], ['ARRAY', 'CURVE'])
check("the segment mesh is shared, not copied", built.data == seg.data, True)
check("white colours where there were none", seg.data.color_attributes.get("COL0") is not None, True)

off = segment("off", start=3.0)
built2 = road.road_from_curve(off, line("path2", 6.0))
lo2, hi2 = evaluated_x(built2)
check("a segment away from its origin still starts at the curve", round(lo2, 3), 0.0)

bent_cu = bpy.data.curves.new("bent", 'CURVE')
bent_cu.dimensions = '3D'
sp = bent_cu.splines.new('POLY')
sp.points.add(2)
for p_, co in zip(sp.points, ((0, 0, 0, 1), (10, 0, 0, 1), (10, 10, 0, 1))):
    p_.co = co
bent = bpy.data.objects.new("bent", bent_cu)
scene.collection.objects.link(bent)
built3 = road.road_from_curve(segment("seg3"), bent)
bpy.context.view_layer.update()
ev = built3.evaluated_get(bpy.context.evaluated_depsgraph_get())
check("a bent curve takes the road round its corner", round(max((ev.matrix_world @ v.co).y for v in ev.data.vertices), 0), 10.0)

from fpmp_baseline.operator import convert_op  # noqa: E402
try:
    convert_op.register()
except ValueError:
    pass
picked, path4 = segment("seg4"), line("path4", 8.0)
for o in bpy.context.selected_objects:
    o.select_set(False)
picked.select_set(True)
path4.select_set(True)
bpy.context.view_layer.objects.active = path4
check("the panel's operator runs on segment + active curve", bpy.ops.fpmp.road_from_curve(), {'FINISHED'})
check("and leaves the road selected", bpy.context.active_object.name, "path4 Road")

print("[road_check] %d passed, %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
