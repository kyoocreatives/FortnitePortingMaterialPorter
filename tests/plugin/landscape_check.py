"""Checks the landscape helpers: a terrain exported at less detail (every 2nd/4th grid line, components' edges kept) and
a remeshed copy given back the original's UVs and paint layers.

    blender -b --factory-startup --python-exit-code 1 -P tests/plugin/landscape_check.py -- <plugin parent>

<plugin parent> holds the plugin as package fpmp_baseline (see translator_test.py). Exit code 0 when every check passes."""
import sys

import bpy
import numpy as np

sys.path.insert(0, sys.argv[sys.argv.index("--") + 1])
import fpmp_baseline  # noqa: E402,F401
from fpmp_baseline.material_porter import landscape_detail as ld  # noqa: E402

FAILS, PASSES = [], [0]


def check(name, got, want, tol=1e-4):
    if isinstance(want, float):
        ok = got is not None and abs(got - want) <= tol
    elif isinstance(want, (tuple, list)) and want and all(isinstance(w, float) for w in want):
        ok = len(got) == len(want) and all(abs(g - w) <= tol for g, w in zip(got, want))
    else:
        ok = got == want
    if ok:
        PASSES[0] += 1
    else:
        FAILS.append(name)
        print("[landscape_check] FAIL %s: got %r, want %r" % (name, got, want))


def terrain(name, cs=4, components=((0, 0), (4, 0))):
    """Components of cs x cs quads laid as LandscapeMeshDto writes them: each its own (cs+1)^2 vertices, x fastest; UV0
    the landscape's quad coordinates (V flipped as the importer stores it), UV1 the 0..1 spread, a paint layer per
    vertex, a material per component."""
    verts, faces, uv0, uv1, grass, mats = [], [], [], [], [], []
    width = max(bx for bx, _ in components) + cs
    for c, (bx, by) in enumerate(components):
        base = len(verts)
        for y in range(cs + 1):
            for x in range(cs + 1):
                u, v = bx + x, by + y
                verts.append((float(u), float(v), 0.1 * u * u + 0.05 * v))
                uv0.append((u, 1.0 - v))
                uv1.append((u / width, 1.0 - v / cs))
                grass.append(min(1.0, u / width))
        for y in range(cs):
            for x in range(cs):
                w = base + x + y * (cs + 1)
                faces += [(w, w + cs + 2, w + 1), (w, w + cs + 1, w + cs + 2)]
                mats += [c, c]
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    for i, data in ((0, uv0), (1, uv1)):
        layer = me.uv_layers.new(name="UV%d" % i)
        for loop in me.loops:
            layer.data[loop.index].uv = data[loop.vertex_index]
    col = me.color_attributes.new("Grass", 'FLOAT_COLOR', 'POINT')
    col.data.foreach_set("color", np.repeat(np.array(grass, np.float32), 4) * np.tile([1, 1, 1, 0], len(grass)) + np.tile([0, 0, 0, 1.0], len(grass)))
    for c in range(len(components)):
        me.materials.append(bpy.data.materials.get("land%d" % c) or bpy.data.materials.new("land%d" % c))
    me.polygons.foreach_set("material_index", mats)
    obj = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(obj)
    obj["mp_landscape"] = 1.0
    return obj


def per_vertex_uv(me, name):
    out = {}
    for loop in me.loops:
        out[loop.vertex_index] = tuple(round(c, 5) for c in me.uv_layers[name].data[loop.index].uv)
    return out


# --- less detail
land = terrain("land")
half = ld.reduce(land.data, 2)
check("half detail: every 2nd line plus the edges (3 x 3 per component)", (len(half.vertices), len(half.polygons)), (18, 8))
pos = {tuple(round(c, 5) for c in v.co) for v in half.vertices}
check("kept points sit exactly where they were", (1.0, 1.0, 0.1 + 0.05) in {(p[0], p[1], p[2]) for p in pos}, False)
check("an even grid point is kept unchanged", (2.0, 2.0, round(0.4 + 0.1, 5)) in pos, True)
check("the shared edge between components stays (no cracks)", sum(1 for p in pos if p[0] == 4.0), 3)
uv = per_vertex_uv(half, "UV0")
check("UV0 keeps the landscape's quad coordinates", sorted({u for u, _ in uv.values()}), [0.0, 2.0, 4.0, 6.0, 8.0])
grass = np.zeros(len(half.vertices) * 4, np.float32)
half.color_attributes["Grass"].data.foreach_get("color", grass)
xs = np.array([v.co.x for v in half.vertices])
check("paint layers keep their values", float(np.abs(grass[0::4] - np.minimum(1.0, xs / 8.0)).max()), 0.0)
mats = np.zeros(len(half.polygons), np.int32)
half.polygons.foreach_get("material_index", mats)
check("each component keeps its material", (sorted(set(mats.tolist())), [m.name for m in half.materials]), ([0, 1], ["land0", "land1"]))
check("odd component sizes keep their last line", len(ld.reduce(terrain("odd", cs=5, components=((0, 0),)).data, 2).vertices), 16)
check("detail 1 leaves the mesh as it is", ld.reduce(land.data, 1) is land.data, True)
mangled = bpy.data.meshes.new("mangled")
mangled.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
check("a mesh that isn't a landscape grid is left as it is", ld.reduce(mangled, 2) is mangled, True)

# imported with Landscape Detail Half: the placed landscape's mesh is the reduced one
from fpmp_baseline.material_porter.placement import after_import  # noqa: E402
placed = terrain("placed")
after_import({"MPLayerNames": {}}, placed, None, 0.01, 2)
check("an imported landscape at half detail", (len(placed.data.vertices), placed.get("mp_landscape")), (18, 1.0))
other = terrain("not_land")
after_import({}, other, None, 0.01, 2)
check("other meshes keep their detail", len(other.data.vertices), 50)

# --- restore onto a remeshed copy
source = terrain("source")
copy = bpy.data.objects.new("remeshed", bpy.data.meshes.new("remeshed"))
bpy.context.scene.collection.objects.link(copy)
copy.data.from_pydata([(1.5, 1.5, 0.3), (5.25, 0.5, 2.8), (7.0, 3.0, 5.0), (2.0, 3.5, 0.6)], [], [(0, 1, 2, 3)])
copy.matrix_world = source.matrix_world.copy()
done = ld.restore(copy, source)
check("restore reports what it gave back", done, {"uvs": ["UV0", "UV1"], "layers": ["Grass"]})
uv = per_vertex_uv(copy.data, "UV0")
check("UV0 refitted from position, exactly", (uv[0], uv[1]), ((1.5, -0.5), (5.25, 0.5)))
check("UV1 too", per_vertex_uv(copy.data, "UV1")[2], (round(7.0 / 8, 5), round(1.0 - 3.0 / 4, 5)))
g = np.zeros(16, np.float32)
copy.data.color_attributes["Grass"].data.foreach_get("color", g)
check("paint layers sampled from the surface under each vertex", [round(float(x), 4) for x in g[0::4]], [0.1875, 0.6562, 0.875, 0.25])
check("the materials and landscape mark come along", ([m.name for m in copy.data.materials], copy.get("mp_landscape")), (["land0", "land1"], 1.0))
moved = bpy.data.objects.new("moved", copy.data.copy())
bpy.context.scene.collection.objects.link(moved)
moved.location = (100.0, 0.0, 0.0)
bpy.context.view_layer.update()
moved.data.vertices[0].co.x -= 100.0
ld.restore(moved, source)
check("a copy placed elsewhere is matched in world space", per_vertex_uv(moved.data, "UV0")[0], (1.5, -0.5))

# the panel's button: original selected, the remeshed copy active
fpmp_baseline.register()
again = bpy.data.objects.new("again", bpy.data.meshes.new("again"))
bpy.context.scene.collection.objects.link(again)
again.data.from_pydata([(1.0, 1.0, 0.2), (3.0, 1.0, 1.0), (3.0, 3.0, 1.0)], [], [(0, 1, 2)])
for o in bpy.context.selected_objects:
    o.select_set(False)
source.select_set(True)
again.select_set(True)
bpy.context.view_layer.objects.active = again
check("the button restores the active copy from the selected original", bpy.ops.fpmp.restore_landscape(), {'FINISHED'})
check("its UVs are there", "UV0" in again.data.uv_layers, True)

print("[landscape_check] %d passed, %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
