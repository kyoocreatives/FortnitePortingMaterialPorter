"""The scatter core (material_porter/scatter.py), headless: density accuracy (random and grid), weight sources (grass
attribute, packed layer channel, weightmap image), the paint mask, slope/height filters, scale modes and attenuation,
seeds, biomes (instance attributes, painted terrain groups, wind sprites hidden), Display % in the viewport only
(render count through a probe engine), camera cull, a painted patch's edge (complementary masks), world-space density on a scaled terrain, remove, a version rebuild
keeping modifiers, and a 1 km² timing.

    python plugin_test.py tests/plugin/scatter_check.py --name scatter
"""
import json
import math
import sys
import time

import bpy
import numpy as np

sys.path.insert(0, sys.argv[sys.argv.index("--") + 1])
from fpmp_baseline.material_porter import scatter, scatter_biomes  # noqa: E402

FAILS, PASSES = [], [0]


def check(name, ok, detail=""):
    if ok:
        PASSES[0] += 1
    else:
        FAILS.append(name)
    print("[scatter_check] %s %s %s" % ("PASS" if ok else "FAIL", name, detail))


def near(value, expected, tolerance):
    return abs(value - expected) <= tolerance * max(expected, 1)


bpy.ops.wm.read_homefile(use_empty=True)
scene = bpy.context.scene


def grid(name, size, cuts, location=(0, 0, 0), rotation=(0, 0, 0)):
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=cuts, y_subdivisions=cuts, size=size, location=location,
                                    rotation=rotation)
    o = bpy.context.object
    o.name = name
    return o


def point_attr(obj, name, values):
    a = obj.data.attributes.get(name) or obj.data.attributes.new(name, 'FLOAT', 'POINT')
    a.data.foreach_set("value", np.asarray(values, dtype=np.float32))


def xs(obj):
    co = np.empty(len(obj.data.vertices) * 3, dtype=np.float32)
    obj.data.vertices.foreach_get("co", co)
    return co.reshape(-1, 3)[:, 0]


def instances(obj, layer):
    """(transforms, positions) of the layer's instances."""
    ev = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    geometry = bpy.types.GeometrySet.from_evaluated_object(ev)
    cloud = geometry.instances_pointcloud()
    tag = np.zeros(len(cloud.points), dtype=bool)
    cloud.attributes[scatter.TAG + layer].data.foreach_get("value", tag)
    m = np.empty(len(cloud.points) * 16, dtype=np.float32)
    cloud.attributes["instance_transform"].data.foreach_get("value", m)
    m = m.reshape(-1, 4, 4)[tag]
    return m, m[:, 3, :3]


def scales(m):
    return np.linalg.norm(m[:, :3, :3], axis=2)      # (column-major rows: each row an axis)


bpy.ops.mesh.primitive_cube_add(size=0.2, location=(0, 0, -50))
blade = bpy.context.object
blade.name = "Blade"

BASE = {"density": 100, "use_grid": False, "jitter": 1, "scaling": "Uniform", "scale_x": [1, 1], "scale_y": [1, 1],
        "scale_z": [1, 1], "random_rotation": True, "align_to_surface": True, "density_range": [0, 1],
        "weight_attenuates_max_scale": False, "max_scale_weight_attenuation": 0.5, "cull": [3500, 5000],
        "type": "/Game/Test/LGT_Test", "variety": 0, "name": "LGT_Test"}
EXPECTED = 100 * 100 / 100 * 100        # 100 m x 100 m at 100 per 100 m²

land = grid("Land", 100, 40)

# --- density
lid = scatter.add_layer(land, BASE, blade)
n = scatter.count(land, lid)
check("random density ~ area/100 x density", near(n, EXPECTED, 0.05), (n, EXPECTED))
info = scatter.layers(land)
check("layers() lists it", len(info) == 1 and info[0]["id"] == lid and info[0]["modifier"] == "FP Scatter · LGT_Test"
      and info[0]["source"] == "game" and info[0]["variety"] == 0, info)
scatter.update_layer(land, lid, {"use_grid": True, "jitter": 0})
n = scatter.count(land, lid)
check("Fortnite placement ignores Use Grid", n == 10021 or near(n, EXPECTED, 0.05), n)
for density, jitter in ((100, 0), (25, 0), (100, 0.5), (25, 1.0)):
    scatter.update_layer(land, lid, {"placement": "UE", "jitter": jitter, "density": density})
    cell = 10.0 / math.sqrt(density)
    m, pos = instances(land, lid)
    want = int(round(100 / cell)) ** 2
    off = np.abs(((pos[:, :2] + 50.0) % cell) - cell / 2)
    reach = min(jitter, 0.99) * cell * 0.5
    check("UE grid density %d jitter %.2f: one per %.0f m cell" % (density, jitter, cell), len(pos) == want,
          (len(pos), want))
    check("UE grid density %d jitter %.2f: within jitter of the cell centre" % (density, jitter),
          off.max() <= reach + 1e-3 and (jitter == 0 or off.max() > reach * 0.9), (off.max(), reach))
scatter.update_layer(land, lid, {"placement": "UE", "use_grid": False, "density": 100})
m, pos = instances(land, lid)
check("UE Halton: SqrtMax² points", len(pos) == 10000, len(pos))


def radical(n, base):
    value, f = 0.0, 1.0 / base
    while n:
        value, n, f = value + (n % base) * f, n // base, f / base
    return value


halton = np.array([[radical(n, 2) * 100 - 50, radical(n, 3) * 100 - 50] for n in range(1, 10001)])
order = np.lexsort((pos[:, 1], pos[:, 0]))
expect = np.lexsort((halton[:, 1], halton[:, 0]))
check("UE Halton: the (2, 3) sequence from index 1", np.allclose(pos[order, :2], halton[expect], atol=2e-3))
scatter.update_layer(land, lid, {"seed": 1})
check("UE Halton: a seed takes the next run", len(instances(land, lid)[1]) == 10000
      and not np.allclose(np.sort(instances(land, lid)[1][:, 0]), np.sort(pos[:, 0])))
scatter.update_layer(land, lid, {"placement": "Fortnite", "use_grid": False, "jitter": 1, "density": 100, "seed": 0})

# --- weights
point_attr(land, "mp_grass_Test", [0.5] * len(land.data.vertices))
scatter.set_weight(land, lid, {"attribute": "Test"})
n = scatter.count(land, lid)
check("grass attribute at 0.5 halves it", near(n, EXPECTED / 2, 0.06), (n, EXPECTED / 2))
scatter.update_layer(land, lid, {"density_range": [0, 0.4]})
check("density range [0, 0.4] filters weight 0.5 out", scatter.count(land, lid) == 0)
scatter.update_layer(land, lid, {"density_range": [0.4, 0.6]})
n = scatter.count(land, lid)
check("density range [0.4, 0.6] keeps weight 0.5, not rescaled", near(n, EXPECTED / 2, 0.06), n)
scatter.update_layer(land, lid, {"density_range": [0.5, 1]})
check("density range min is exclusive (UE: weight > min)", scatter.count(land, lid) == 0)
scatter.update_layer(land, lid, {"density_range": [0, 0.5]})
check("density range max is inclusive (weight <= max)", near(scatter.count(land, lid), EXPECTED / 2, 0.06))
scatter.update_layer(land, lid, {"density_range": [0, 1]})
point_attr(land, "mp_grass_Test", [0.0] * len(land.data.vertices))
check("weight 0: none", scatter.count(land, lid) == 0)
scatter.set_weight(land, lid, {"attribute": "mp_grass_Missing"})
check("a missing source attribute places none", scatter.count(land, lid) == 0)

colour = land.data.color_attributes.new("Attribute1", 'FLOAT_COLOR', 'POINT')
rgba = np.zeros((len(land.data.vertices), 4), dtype=np.float32)
rgba[:, 1] = 1.0
colour.data.foreach_set("color", rgba.ravel())
land["mp_layers"] = {"Grass": "Attribute1.G", "Rock": "Attribute1.R"}
scatter.set_weight(land, lid, {"layer": "Grass"})
n = scatter.count(land, lid)
check("packed layer channel at 1: full", near(n, EXPECTED, 0.05), n)
scatter.set_weight(land, lid, {"layer": "Rock"})
check("packed layer channel at 0: none", scatter.count(land, lid) == 0)

image = bpy.data.images.new("Weights", 8, 8, alpha=False, float_buffer=True)
px = np.zeros((8, 8, 4), dtype=np.float32)
px[:, :4, 2] = 1.0              # blue: the left half (u < 0.5)
px[..., 3] = 1.0
image.pixels.foreach_set(px.ravel())
uv = land.data.uv_layers.new(name="mp_weights")
loops = np.empty(len(land.data.loops), dtype=np.int32)
land.data.loops.foreach_get("vertex_index", loops)
co = np.empty(len(land.data.vertices) * 3, dtype=np.float32)
land.data.vertices.foreach_get("co", co)
co = co.reshape(-1, 3)
uv.data.foreach_set("uv", ((co[loops, :2] + 50.0) / 100.0).astype(np.float32).ravel())
scatter.set_weight(land, lid, {"image": "Weights", "channel": 2})
m, pos = instances(land, lid)
n = len(pos)
check("weightmap image channel: half", near(n, EXPECTED / 2, 0.07), n)
# linear filtering blends over one texel (12.5 m) across the edge at x = 0
check("image: only where painted", n and float(pos[:, 0].max()) < 6.25, float(pos[:, 0].max()) if n else None)
scatter.set_weight(land, lid, None)
check("weight None: full again", near(scatter.count(land, lid), EXPECTED, 0.05))
rec = {r["id"]: r for r in scatter.layers(land)}[lid]
check("record keeps the weight", rec["weight"] is None)

# --- paint mask
point_attr(land, scatter.MASK + lid, (xs(land) > 0).astype(np.float32))
m, pos = instances(land, lid)
check("mask paints out half", near(len(pos), EXPECTED / 2, 0.07) and float(pos[:, 0].min()) > -2.0, len(pos))
point_attr(land, scatter.MASK + lid, np.zeros(len(land.data.vertices)))
check("mask 0 everywhere: none", scatter.count(land, lid) == 0)
land.data.attributes.remove(land.data.attributes[scatter.MASK + lid])

# --- filters
scatter.update_layer(land, lid, {"height": [100, 1000]})
check("height above the land: none", scatter.count(land, lid) == 0)
scatter.update_layer(land, lid, {"height": [-100, 100]})
check("height around it: all", near(scatter.count(land, lid), EXPECTED, 0.05))
land.rotation_euler = (math.radians(30), 0, 0)
scatter.update_layer(land, lid, {"slope": [0, 20], "height": [-1e7, 1e7]})
check("30 deg slope, slope 0-20: none", scatter.count(land, lid) == 0)
scatter.update_layer(land, lid, {"slope": [20, 40]})
check("30 deg slope, slope 20-40: all", near(scatter.count(land, lid), EXPECTED, 0.05))
m, pos = instances(land, lid)
world = np.array(land.matrix_world)[:3, :3] @ m[:, :3, :3].transpose(0, 2, 1)       # instance axes in world
ups = world[:, :, 2]
check("aligned to the surface normal", np.allclose(ups / np.linalg.norm(ups, axis=1, keepdims=True),
                                                   np.array(land.matrix_world.col[2][:3]), atol=1e-3))
scatter.update_layer(land, lid, {"random_rotation": False})
m, pos = instances(land, lid)
world = np.array(land.matrix_world)[:3, :3] @ m[:, :3, :3].transpose(0, 2, 1)
normal = np.array(land.matrix_world.col[2][:3])
x_axis = np.cross((0, -1, 0), normal)
x_axis /= np.linalg.norm(x_axis)
check("aligned frame: X = normalize((0,-1,0) x N), Y = Z x X", np.allclose(world[0][:, 0], x_axis, atol=1e-3)
      and np.allclose(world[0][:, 1], np.cross(normal, x_axis), atol=1e-3), world[0])
scatter.update_layer(land, lid, {"random_rotation": True, "placement": "UE", "use_grid": True, "jitter": 0.5,
                                 "align_to_triangle_normals": False})
m, pos = instances(land, lid)
ups = (np.array(land.matrix_world)[:3, :3] @ m[:, :3, :3].transpose(0, 2, 1))[:, :, 2]
check("UE grid normal (no triangle normals) on a plane: its normal", len(ups) > 1000 and np.allclose(
    ups / np.linalg.norm(ups, axis=1, keepdims=True), normal, atol=1e-3), len(ups))
scatter.update_layer(land, lid, {"placement": "Fortnite", "align_to_triangle_normals": True})
land.rotation_euler = (math.pi, 0, 0)
scatter.update_layer(land, lid, {"slope": [0, 1]})
m, pos = instances(land, lid)
ups = (np.array(land.matrix_world)[:3, :3] @ m[:, :3, :3].transpose(0, 2, 1))[:, :, 2]
check("a face turned down counts as up (N x sign(N.z))", near(len(pos), EXPECTED, 0.05)
      and np.allclose(ups / np.linalg.norm(ups, axis=1, keepdims=True), (0, 0, 1), atol=1e-3), len(pos))
land.rotation_euler = (0, 0, 0)
land.scale = (2, 2, 1)
scatter.update_layer(land, lid, {"slope": [0, 90]})
n = scatter.count(land, lid)
check("density per world m² (land scaled x2)", near(n, EXPECTED * 4, 0.05), n)
m, pos = instances(land, lid)
world_scale = scales(m) * np.array([2, 2, 1])
check("instances keep their world size", np.allclose(world_scale[:, 2], 1, atol=1e-3), world_scale[:3])
land.scale = (2, 0.5, 3)
land.rotation_euler = (0, 0, math.radians(45))
n = scatter.count(land, lid)
check("density per world m² (non-uniform scale, turned)", near(n, EXPECTED, 0.05), n)
scatter.update_layer(land, lid, {"placement": "UE", "use_grid": True, "jitter": 0})
n = scatter.count(land, lid)
check("UE grid on a turned, scaled land: about its area", near(n, EXPECTED, 0.05), n)
scatter.update_layer(land, lid, {"placement": "Fortnite"})
land.scale = (1, 1, 1)
land.rotation_euler = (0, 0, 0)

# --- scale modes
ranges = {"scale_x": [1, 2], "scale_y": [3, 4], "scale_z": [5, 6], "random_rotation": False, "align_to_surface": False}
scatter.update_layer(land, lid, dict(ranges, scaling="Uniform"))
s = scales(instances(land, lid)[0])
check("Uniform: all from X's range", np.allclose(s[:, 0], s[:, 1], atol=1e-4) and np.allclose(s[:, 0], s[:, 2], atol=1e-4)
      and s[:, 0].min() >= 1 - 1e-4 and s[:, 0].max() <= 2 + 1e-4 and np.ptp(s[:, 0]) > 0.9, s[:2])
scatter.update_layer(land, lid, {"scaling": "LockXY"})
s = scales(instances(land, lid)[0])
check("LockXY: Y = X, Z its own", np.allclose(s[:, 0], s[:, 1], atol=1e-4) and s[:, 2].min() >= 5 - 1e-4
      and s[:, 2].max() <= 6 + 1e-4, s[:2])
scatter.update_layer(land, lid, {"scaling": "EGrassScaling::Free"})
s = scales(instances(land, lid)[0])
check("Free: each axis its own range", s[:, 1].min() >= 3 - 1e-4 and s[:, 1].max() <= 4 + 1e-4
      and s[:, 2].min() >= 5 - 1e-4 and np.ptp(s[:, 1]) > 0.9, s[:2])
point_attr(land, "mp_grass_Half", [0.5] * len(land.data.vertices))
scatter.set_weight(land, lid, {"attribute": "Half"})
for att in (1.0, 0.5, 0.25, 0.0):
    scatter.update_layer(land, lid, {"weight_attenuates_max_scale": True, "max_scale_weight_attenuation": att})
    s = scales(instances(land, lid)[0])
    factor = min(max((0.5 - att) / (1 - att), 0), 1) if att < 1 else 0.0       # UE: lerp(range, rand x factor)
    top = 1 + factor
    check("weight 0.5, attenuation %.2f: max %.2f" % (att, top), s[:, 0].max() <= top + 1e-3
          and s[:, 0].max() > top - 0.05 and s[:, 0].min() >= 1 - 1e-4, (s[:, 0].min(), s[:, 0].max()))
scatter.set_weight(land, lid, None)
scatter.update_layer(land, lid, {"weight_attenuates_max_scale": False, "scaling": "Uniform", "scale_x": [1, 1],
                                 "scale_y": [1, 1], "scale_z": [1, 1]})

# --- seeds
a = instances(land, lid)[1].copy()
b = instances(land, lid)[1]
check("same seed, same points", a.shape == b.shape and np.allclose(a, b))
scatter.update_layer(land, lid, {"seed": 7})
c = instances(land, lid)[1]
check("another seed, other points", a.shape != c.shape or not np.allclose(a, c))
scatter.update_layer(land, lid, {"seed": 0})
check("back to seed 0, the first points", np.allclose(instances(land, lid)[1], a))


# --- Display % (viewport only)
class Probe(bpy.types.RenderEngine):
    bl_idname = "SCATTER_PROBE"
    bl_label = "Scatter probe"
    seen = []

    def render(self, depsgraph):
        Probe.seen.append(scatter.count(land, lid, depsgraph))


bpy.utils.register_class(Probe)
cam_data = bpy.data.cameras.new("Cam")
cam = bpy.data.objects.new("Cam", cam_data)
scene.collection.objects.link(cam)
scene.camera = cam
cam.location = (0, 0, 2)
scene.render.engine = "SCATTER_PROBE"
scene.render.resolution_x = scene.render.resolution_y = 8
scatter.update_layer(land, lid, {"display": 50})
n = scatter.count(land, lid)
check("Display 50%: half in the viewport", near(n, EXPECTED / 2, 0.06), n)
bpy.ops.render.render()
check("Display 50%: all in a render", Probe.seen and near(Probe.seen[-1], EXPECTED, 0.05), Probe.seen)
scatter.update_layer(land, lid, {"display": 100})

# --- camera cull
scatter.update_layer(land, lid, {"camera_cull": True, "cull": [0, 2000]})
m, pos = instances(land, lid)
reach = np.linalg.norm(pos - np.array(cam.location), axis=1)
area = math.pi * (20 ** 2 - 2 ** 2)
check("camera cull: within 20 m only", len(pos) and reach.max() <= 20.0 + 1e-3, reach.max() if len(pos) else None)
check("camera cull: the disc's count", near(len(pos), area / 100 * 100, 0.08), (len(pos), area))
scatter.update_layer(land, lid, {"cull": [1000, 2000]})
ev = land.evaluated_get(bpy.context.evaluated_depsgraph_get())
geometry = bpy.types.GeometrySet.from_evaluated_object(ev)
cloud = geometry.instances_pointcloud()
fade = np.zeros(len(cloud.points), dtype=np.float32)
cloud.attributes[scatter.FADE].data.foreach_get("value", fade)
mine = np.zeros(len(cloud.points), dtype=bool)
cloud.attributes[scatter.TAG + lid].data.foreach_get("value", mine)
fade = fade[mine]
m, pos = instances(land, lid)
reach = np.linalg.norm(pos - np.array(cam.location), axis=1)
check("mp_fade = clamp((d - start) / (end - start))", len(fade) == len(reach)
      and np.allclose(fade, np.clip((reach - 10) / 10, 0, 1), atol=1e-3), fade[:4])
cam.location = (300, 0, 2)
check("camera cull: camera away, none", scatter.count(land, lid) == 0)
cam.location = (0, 0, 2)
scatter.update_layer(land, lid, {"placement": "UE", "use_grid": True})
m, pos = instances(land, lid)
reach = np.linalg.norm(pos - np.array(cam.location), axis=1)
check("UE grid with camera cull: within 20 m only", len(pos) and reach.max() <= 20 + 1e-3, len(pos))
scatter.update_layer(land, lid, {"placement": "Fortnite"})
scatter.update_layer(land, lid, {"camera_cull": False})

# --- a second layer, remove
bpy.ops.mesh.primitive_cube_add(size=0.3, location=(0, 0, -60))
pebble = bpy.context.object
other = scatter.add_layer(land, dict(BASE, density=10, cast_shadow=False), pebble, label="Sparse")
check("cast_shadow false: the instanced object casts none", pebble.visible_shadow is False and blade.visible_shadow)
check("cast_shadow in the record", {r["id"]: r for r in scatter.layers(land)}[other]["cast_shadow"] is False)
point_attr(land, scatter.MASK + other, [1.0] * len(land.data.vertices))
n1, n2 = scatter.count(land, lid), scatter.count(land, other)
check("two layers counted apart", near(n1, EXPECTED, 0.05) and near(n2, EXPECTED / 10, 0.1), (n1, n2))
scatter.remove_layer(land, other)
check("remove: modifier gone", all(m.name != "FP Scatter · Sparse" for m in land.modifiers), [m.name for m in land.modifiers])
check("remove: mask gone", scatter.MASK + other not in land.data.attributes)
check("remove: record gone", [r["id"] for r in scatter.layers(land)] == [lid])
check("remove: the other layer stays", near(scatter.count(land, lid), EXPECTED, 0.05))

# --- biomes (scatter_biomes): instances carry their terrain's weightmap UV under their root and the layer's biome; a
# painted terrain fills the shared groups map-wide samples read through; wind sprites start hidden
def inst_attr(obj, layer, name, width=1):
    ev = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    geometry = bpy.types.GeometrySet.from_evaluated_object(ev)      # (owns the cloud: keep it alive)
    cloud = geometry.instances_pointcloud()
    tag = np.zeros(len(cloud.points), dtype=bool)
    cloud.attributes[scatter.TAG + layer].data.foreach_get("value", tag)
    a = cloud.attributes[name]
    v = np.empty(len(cloud.points) * width, dtype=np.float32 if width > 1 or a.data_type == 'FLOAT' else np.int32)
    a.data.foreach_get("vector" if width > 1 else "value", v)
    return v.reshape(-1, width)[tag] if width > 1 else v[tag]


_m, pos = instances(land, lid)
root_uv = inst_attr(land, lid, scatter_biomes.UV, 3)
check("instances carry the weightmap UV under their root",
      len(pos) and float(np.abs(root_uv[:, :2] - (pos[:, :2] + 50.0) / 100.0).max()) < 2e-3)
check("biome 0 (the world's) by default", int(inst_attr(land, lid, scatter_biomes.CHOICE).max()) == 0)
scatter.set_biome(land, lid, 3)
check("set_biome: every instance's", set(inst_attr(land, lid, scatter_biomes.CHOICE).tolist()) == {3})
scatter.set_biome(land, lid, 0)

# --- Ground Blend (ground.py): a layer's mode as instance attributes; layers sharing a mesh keep their own
from fpmp_baseline.material_porter import ground as gb  # noqa: E402
check("ground: a new layer is Game", scatter.ground_of(land, lid) == {"mode": "Game", "style": "Whole blade", "strength": 1.0},
      scatter.ground_of(land, lid))
scatter.set_ground(land, lid, "On", "Root to tip", 0.6)
check("ground: layer On writes mp_ground_blend", np.allclose(inst_attr(land, lid, gb.ATTR_BLEND), 0.6))
check("ground: ...and the style", np.allclose(inst_attr(land, lid, gb.ATTR_STYLE), 1.0))
twin = scatter.add_layer(land, dict(BASE, density=10), blade, label="Twin")
point_attr(land, scatter.MASK + twin, [1.0] * len(land.data.vertices))
scatter.set_ground(land, twin, "Off", "Whole blade", 1.0)
check("ground: layer Off writes mp_ground_off 1", np.allclose(inst_attr(land, twin, gb.ATTR_OFF), 1.0))
check("ground: a layer sharing the mesh keeps its own", np.allclose(inst_attr(land, lid, gb.ATTR_OFF), 0.0)
      and np.allclose(inst_attr(land, lid, gb.ATTR_BLEND), 0.6))
check("ground: the mode comes back", scatter.ground_of(land, lid) == {"mode": "On", "style": "Root to tip", "strength": 0.6},
      scatter.ground_of(land, lid))
scatter.remove_layer(land, twin)
bpy.ops.mesh.primitive_cube_add(size=0.2, location=(0, 0, -70))
motes = bpy.context.object
motes.name = "SM_Apollo_Motes_Few_01"
sprites = scatter.add_layer(land, BASE, motes, label="Motes")
mods = {m.name: m for m in land.modifiers}
check("wind sprites start hidden", not mods["FP Scatter · Motes"].show_viewport
      and not mods["FP Scatter · Motes"].show_render and mods["FP Scatter · LGT_Test"].show_viewport)
scatter.remove_layer(land, sprites)

ground = bpy.data.materials.new("Ground")
RECTS = [[0.0, 0.0, 0.5, 0.5], [0.5, 0.0, 1.0, 0.5], [0.0, 0.5, 0.5, 1.0], [0.5, 0.5, 1.0, 1.0]]
ground["mp_map_textures"] = json.dumps({"BiomeTexture": {
    "texture": "/T/T_BiomeMask.T_BiomeMask", "rects": RECTS,
    "labels": {"0,0": "Meadow", "0,1": "Red Woods", "1,3": "Remove Stripes"}}})
land.data.materials.append(ground)
check("choices: the mask's named biomes, 1-based", scatter_biomes.choices([None, ground]) == [(1, "Meadow"), (2, "Red Woods")],
      scatter_biomes.choices([ground]))
blade.data.materials.append(ground)
check("a layer's biomes come from its instance's material", scatter.biomes(land, lid) == [(1, "Meadow"), (2, "Red Woods")])
blade.data.materials.clear()
painted = bpy.data.images.new("Land BiomeTexture", 8, 8, alpha=True)
group = scatter_biomes.group_for("BiomeTexture", ("rect", (0.5, 0.0, 1.0, 0.5), 2.0))
check("one group per parameter and rectangle", scatter_biomes.group_for("BiomeTexture", ("rect", [0.5, 0.0, 1.0, 0.5])) == group
      and scatter_biomes.group_for("BiomeTexture", ("rect", (0.0, 0.0, 0.5, 0.5), 2.0)) != group)
land["mp_biome_maps"] = {"BiomeTexture": painted.name}
scatter_biomes.refresh()


def reads(g, kind, **props):
    return [n for n in g.nodes if n.bl_idname == kind and all(getattr(n, k) == v for k, v in props.items())]


check("refresh: the painted set numbered on the terrain", land.get(scatter_biomes.SET) == 1, land.get(scatter_biomes.SET))
check("refresh: UV per metre", abs(land.get(scatter_biomes.SCALE, 0) - 0.01) < 1e-6, land.get(scatter_biomes.SCALE))
check("refresh: the group reads the painted map", [n.image for n in reads(group, "ShaderNodeTexImage")] == [painted])
check("refresh: by the instancer's set and the layer's biome", all(
    reads(group, "ShaderNodeAttribute", attribute_type='INSTANCER', attribute_name=a)
    for a in (scatter_biomes.SET, scatter_biomes.CHOICE, scatter_biomes.UV, scatter_biomes.SCALE)))
out = reads(group, "NodeGroupOutput")[0]
check("refresh: Painted wired", out.inputs["Painted"].is_linked)
check("refresh: unchanged inputs leave the groups", scatter_biomes.refresh() == 0)
del land["mp_biome_maps"]
scatter_biomes.refresh()
check("unpainted: the set number cleared", land.get(scatter_biomes.SET) == 0)
check("unpainted: the group reads no map", not reads(group, "ShaderNodeTexImage"))
land.data.materials.clear()

# --- rebuild on a version change
scatter.update_layer(land, lid, {"density": 50, "seed": 3})
before = scatter.count(land, lid)
scatter.group()[scatter.VERSION_KEY] = -1
g = scatter.group()
mod = land.modifiers["FP Scatter · LGT_Test"]
check("rebuilt group keeps its name", g.name == scatter.GROUP and len([x for x in bpy.data.node_groups
                                                                       if x.name.startswith(scatter.GROUP)]) == 1,
      [x.name for x in bpy.data.node_groups])
check("modifier on the new group with its values", mod.node_group == g and scatter._get(mod, "Density") == 50
      and scatter._get(mod, "Instance") == blade and scatter._get(mod, "Seed") == 3)
check("rebuilt: same count", scatter.count(land, lid) == before, (scatter.count(land, lid), before))
check("rebuilt: Ground Blend kept", np.allclose(inst_attr(land, lid, gb.ATTR_BLEND), 0.6))


def old_group():
    """The group as version 4 left it: no Biome or Ground inputs."""
    g = scatter.group()
    g[scatter.VERSION_KEY] = 4
    for name in ("Biome", *scatter.GROUND):
        g.interface.remove(scatter._item(g, name))


def runs(f):
    try:
        f()
        return None
    except Exception as e:
        return repr(e)


old_group()
err = runs(lambda: scatter.set_ground(land, lid, "On", "Whole blade", 0.3))
check("v4 group: set_ground rebuilds it first", err is None and np.allclose(inst_attr(land, lid, gb.ATTR_BLEND), 0.3),
      err)
old_group()
err = runs(lambda: scatter.set_biome(land, lid, 2))
check("v4 group: set_biome too", err is None and set(inst_attr(land, lid, scatter_biomes.CHOICE).tolist()) == {2}, err)
scatter.set_biome(land, lid, 0)

# --- UE's grid normal on a bumpy surface: (P(i-1) - P(i+1)) x (P(j-1) - P(j+1)) of the neighbouring cells' points
from mathutils.bvhtree import BVHTree  # noqa: E402

bumps = grid("Bumps", 40, 80, location=(500, 0, 0))
co = np.empty(len(bumps.data.vertices) * 3, dtype=np.float32)
bumps.data.vertices.foreach_get("co", co)
co = co.reshape(-1, 3)
co[:, 2] = np.sin(co[:, 0] / 3.0) * 2.0 + np.cos(co[:, 1] / 5.0)
bumps.data.vertices.foreach_set("co", co.ravel())
bumps.data.update()
tree = BVHTree.FromObject(bumps, bpy.context.evaluated_depsgraph_get())
bid = scatter.add_layer(bumps, dict(BASE, density=25, placement="UE", use_grid=True, jitter=0, random_rotation=False,
                                    align_to_triangle_normals=False), blade)
m, pos = instances(bumps, bid)
ups = m[:, 2, :3] / np.linalg.norm(m[:, 2, :3], axis=1, keepdims=True)      # (rows: the instance's axes)


def surface(x, y):
    hit = tree.ray_cast((x, y, 10.0), (0, 0, -1))[0]
    return np.array(hit)


bad, tested = 0, 0
for p, up in zip(pos, ups):
    i, j = np.floor((p[:2] + 20.0) / 2.0).astype(int)
    if not (0 < i < 19 and 0 < j < 19):
        continue
    cx, cy = -20 + 2 * np.array([i, j]) + 1.0
    n = np.cross(surface(cx - 2, cy) - surface(cx + 2, cy), surface(cx, cy - 2) - surface(cx, cy + 2))
    n = n / np.linalg.norm(n) * np.sign(n[2])
    tested += 1
    bad += not np.allclose(up, n, atol=2e-3)
check("UE grid-based normals match the neighbouring cells' cross product", tested > 250 and bad == 0, (bad, tested))
tri = np.array([tree.ray_cast((p[0], p[1], 10.0), (0, 0, -1))[1] for p in pos])
check("... and differ from the triangle normals there", np.abs(ups - tri).max() > 0.05)
# (jittered: at jitter 0 the cell centres sit on the mesh edges, where either triangle is right)
scatter.update_layer(bumps, bid, {"align_to_triangle_normals": True, "jitter": 0.37})
m, pos = instances(bumps, bid)
ups = m[:, 2, :3] / np.linalg.norm(m[:, 2, :3], axis=1, keepdims=True)
tri = np.array([tree.ray_cast((p[0], p[1], 10.0), (0, 0, -1))[1] for p in pos])
diff = np.abs(ups - tri).max(axis=1)
check("triangle normals: the hit triangle's", np.allclose(ups, tri, atol=2e-3), (int((diff > 2e-3).sum()), len(diff)))
bumps.hide_viewport = True

# --- a painted patch's edge, as Hera grass meets gravel: two layers by complementary masks (grass_masks' Grass01 =
# 1 - the gravel layer's mask, Gravel = that mask) thin out across the edge by weight alone; none past their zero
patch = grid("Patch", 20, 80)
ramp = np.clip((xs(patch) + 2.0) / 4.0, 0.0, 1.0)       # gravel 0 at x = -2 m, 1 at x = +2 m
point_attr(patch, "mp_grass_Grass01", 1.0 - ramp)
point_attr(patch, "mp_grass_Gravel", ramp)
dense = dict(BASE, density=8000)
grass_id = scatter.add_layer(patch, dense, blade, label="Edge Grass", weight={"attribute": "Grass01"})
gravel_id = scatter.add_layer(patch, dense, blade, label="Edge Gravel", weight={"attribute": "Gravel"})
gx = instances(patch, grass_id)[1][:, 0]
vx = instances(patch, gravel_id)[1][:, 0]
check("patch edge: no grass past the gravel's full weight", int((gx > 2.0 + 1e-3).sum()) == 0, int((gx > 2.0).sum()))
check("patch edge: no gravel cover on full grass", int((vx < -2.0 - 1e-3).sum()) == 0, int((vx < -2.0).sum()))
per_band = 8000 / 100 * 20                               # instances a 1 m x 20 m band holds at weight 1
bands = [(lo, lo + 1.0) for lo in (-2.0, -1.0, 0.0, 1.0)]
got = [(int(((gx >= lo) & (gx < hi)).sum()), int(((vx >= lo) & (vx < hi)).sum())) for lo, hi in bands]
want = [(per_band * (1 - (lo + 2.5) / 4), per_band * (lo + 2.5) / 4) for lo, _ in bands]
check("patch edge: each layer's count per 1 m band follows its mean weight",
      all(near(g, wg, 0.15) and near(v, wv, 0.15) for (g, v), (wg, wv) in zip(got, want)), (got, want))
patch.hide_viewport = True

# --- timing: 1 km² at density 46, camera cull on
scene.render.engine = "BLENDER_EEVEE"
big = grid("Big", 1000, 200)
for o in (land,):
    o.hide_viewport = True
t0 = time.perf_counter()
big_id = scatter.add_layer(big, dict(BASE, density=46, cull=[3500, 5000]), blade)
t1 = time.perf_counter()
n = scatter.count(big, big_id)
t2 = time.perf_counter()
cam.location = (120, -40, 2)
n_moved = scatter.count(big, big_id)
t3 = time.perf_counter()
centre = instances(big, big_id)[1].mean(axis=0)
check("1 km²: the instances follow the camera", np.allclose(centre[:2], (120, -40), atol=3), centre)
mod = big.modifiers[-1]
print("[scatter_check] TIMING 1 km2 density 46 cull on: add %.3f s, first eval %.3f s (%d instances), camera move "
      "%.3f s (%d); defaults Display %% %.1f, Camera Cull %s" % (t1 - t0, t2 - t1, n, t3 - t2, n_moved,
                                                                 scatter._get(mod, "Display %"),
                                                                 scatter._get(mod, "Camera Cull")))
check("1 km² defaults: camera cull on", scatter._get(mod, "Camera Cull") is True)
scatter.update_layer(big, big_id, {"camera_cull": False, "display": 100})
t4 = time.perf_counter()
n_all = scatter.count(big, big_id)
t5 = time.perf_counter()
print("[scatter_check] TIMING 1 km2 density 46 cull off, display 100: eval %.3f s (%d instances)" % (t5 - t4, n_all))
check("1 km² full count", near(n_all, 1e6 / 100 * 46, 0.03), n_all)
for use_grid, cull in ((True, True), (True, False), (False, True), (False, False)):
    scatter.update_layer(big, big_id, {"placement": "UE", "use_grid": use_grid, "camera_cull": cull, "seed": 5 + cull})
    t6 = time.perf_counter()
    n_grid = scatter.count(big, big_id)
    print("[scatter_check] TIMING 1 km2 density 46 UE %s, cull %s: eval %.3f s (%d instances)"
          % ("grid" if use_grid else "Halton", cull, time.perf_counter() - t6, n_grid))

print("[scatter_check] %d passed, %d failed" % (PASSES[0], len(FAILS)))
if FAILS:
    sys.exit(1)
