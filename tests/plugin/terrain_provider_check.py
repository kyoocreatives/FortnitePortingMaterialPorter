"""The terrain provider API (material_porter/terrain_provider.py), headless: publishing in bpy.app.driver_namespace,
write_layers into packed colour channels and weightmap images (the channels the painter would use, normalised as
Normalize does, a map proxy's scene order), terrain_changed refreshing scatter layers and surviving deleted objects, a
rebuilt mesh's mp_weights UV, adopt listing the mesh in the Landscape tab, and timings on 1M vertices.

    blender -b --factory-startup --python-exit-code 1 -P tests/plugin/terrain_provider_check.py -- <plugin parent>
"""
import inspect
import sys
import time

import bpy
import numpy as np

sys.path.insert(0, sys.argv[sys.argv.index("--") + 1])
import fpmp_baseline  # noqa: E402
from fpmp_baseline.material_porter import landscape_layers, scatter, terrain_provider as tp, weightmaps  # noqa: E402
from fpmp_baseline.operator.fortnite_editor import land_store  # noqa: E402

FAILS, PASSES = [], [0]


def check(name, ok, detail=""):
    if ok:
        PASSES[0] += 1
    else:
        FAILS.append(name)
    print("[terrain_provider_check] %s %s %s" % ("PASS" if ok else "FAIL", name, detail))


bpy.ops.wm.read_homefile(use_empty=True)
scene = bpy.context.scene


def grid(name, n, size, x0=0.0):
    """n x n vertices over size metres from (x0, 0), rows along x."""
    t = np.linspace(0.0, size, n)
    xx, yy = np.meshgrid(t + x0, t)
    verts = np.column_stack([xx.ravel(), yy.ravel(), np.zeros(n * n)])
    faces = [(j * n + i, j * n + i + 1, (j + 1) * n + i + 1, (j + 1) * n + i) for j in range(n - 1) for i in range(n - 1)]
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts.tolist(), [], faces)
    obj = bpy.data.objects.new(name, me)
    scene.collection.objects.link(obj)
    return obj


def xs(obj):
    co = np.empty(len(obj.data.vertices) * 3, np.float32)
    obj.data.vertices.foreach_get("co", co)
    return co.reshape(-1, 3)


def channel(obj, layer):
    a, c = landscape_layers.table(obj)[layer]
    return landscape_layers._values(obj.data.color_attributes[a])[:, c]


# --- publishing
api = bpy.app.driver_namespace.get(tp.NAME)
check("not published before register", api is None)
tp.register()
api = bpy.app.driver_namespace.get(tp.NAME)
check("published at register", api is tp and api.VERSION == 1
      and all(callable(getattr(api, f, None)) for f in ("game_layers", "write_layers", "terrain_changed", "adopt")))
tp.unregister()
check("removed at unregister", tp.NAME not in bpy.app.driver_namespace)
other = object()
bpy.app.driver_namespace[tp.NAME] = other
tp.unregister()
check("someone else's left alone", bpy.app.driver_namespace.get(tp.NAME) is other)
del bpy.app.driver_namespace[tp.NAME]
src = inspect.getsource(fpmp_baseline.register) + inspect.getsource(fpmp_baseline.unregister)
check("plugin register/unregister call it", src.count("terrain_provider.register()") == 1
      and src.count("terrain_provider.unregister()") == 1)
tp.register()

# --- packed colour channels
a = grid("A", 9, 16.0)
n = len(a.data.vertices)
x = xs(a)[:, 0] / 16.0
g, r = x.astype(np.float32), (0.5 * (1 - x)).astype(np.float32)
r[:9] = 0.0
g[:9] = 0.0           # first row: nothing painted
tp.write_layers(a, {"Rock": r, "Grass": g})
b = grid("B", 9, 16.0)
for name, v in (("Grass", g), ("Rock", r)):
    attr = b.data.color_attributes.new(name, 'FLOAT_COLOR', 'POINT')
    col = np.zeros((n, 4), np.float32)
    col[:, 0] = v
    attr.data.foreach_set("color", col.ravel())
landscape_layers.pack(b, ["Grass", "Rock"])
check("channels as the painter packs them", dict(a["mp_layers"]) == dict(b["mp_layers"]) ==
      {"Grass": "Attribute1.R", "Rock": "Attribute1.G"}, (dict(a["mp_layers"]), dict(b["mp_layers"])))
total = channel(a, "Grass") + channel(a, "Rock")
check("normalised where painted", np.allclose(total[9:], 1.0, atol=1e-5), total[9:].min())
check("unpainted left at 0", np.allclose(total[:9], 0.0))
check("ratio kept", np.allclose(channel(a, "Grass")[9:] / total[9:], g[9:] / (g[9:] + r[9:]), atol=1e-5))
s = np.ones(n, np.float32)
tp.write_layers(a, {"Snow": s})
check("new layer in the next free channel", dict(a["mp_layers"])["Snow"] == "Attribute1.B")
check("others kept and normalised with it", np.allclose(channel(a, "Snow")[9:], 0.5, atol=1e-5)
      and np.allclose(channel(a, "Snow")[:9], 1.0))
try:
    tp.write_layers(a, {"Grass": np.ones(n - 1)})
    check("wrong length refused", False)
except ValueError:
    check("wrong length refused", True)

# a map proxy: every layer of the scene's order, in its channel
scene[landscape_layers.ORDER] = ["Dirt", "Grass", "Rock"]
c = grid("C", 5, 8.0)
c[landscape_layers.MAP] = 1.0
tp.write_layers(c, {"Rock": np.ones(25, np.float32), "Sand": np.ones(25, np.float32)})
check("map proxy keeps the scene order", dict(c["mp_layers"]) == {"Dirt": "Attribute1.R", "Grass": "Attribute1.G",
      "Rock": "Attribute1.B", "Sand": "Attribute2.R"} and list(scene[landscape_layers.ORDER])[-1] == "Sand",
      dict(c["mp_layers"]))

# --- weightmap images (a mesh a landscape material was put on)
d = grid("D", 33, 64.0)
weightmaps.ensure([d], ["Grass", "Rock", "Snow"])
size = next(iter(weightmaps.images(d).values())).size[0]
x = xs(d)[:, 0] / 64.0
tp.write_layers(d, {"Grass": x, "Rock": 0.5 * (1 - x)})
maps, table = weightmaps.images(d), landscape_layers.table(d)
px = {k: weightmaps._pixels(im).reshape(size, size, 4) for k, im in maps.items()}
plane = {layer: px[w][..., ch] for layer, (w, ch) in table.items()}
col = np.arange(size)
u = (col + 0.5) / size
expect = u / (u + 0.5 * (1 - u))
got = plane["Grass"][size // 2]
check("image texels sampled and normalised", np.allclose(got, expect, atol=2e-3), np.abs(got - expect).max())
check("image texels sum 1", np.allclose(plane["Grass"] + plane["Rock"] + plane["Snow"], 1.0, atol=1e-4))
check("Snow untouched (0)", np.allclose(plane["Snow"], 0.0))
# the painter's Normalize on the same raw weights agrees
e = grid("E", 33, 64.0)
weightmaps.ensure([e], ["Grass", "Rock", "Snow"])
emaps = weightmaps.images(e)
raw = weightmaps._pixels(emaps["Weightmap1"]).reshape(size, size, 4)
raw[..., 0] = u[None, :]
raw[..., 1] = 0.5 * (1 - u)[None, :]
weightmaps._store(emaps["Weightmap1"], raw.reshape(-1, 4))
weightmaps.normalize(e)
eraw = weightmaps._pixels(emaps["Weightmap1"]).reshape(size, size, 4)
check("same as Normalize (to a byte)", np.allclose(eraw[..., :3], px["Weightmap1"][..., :3], atol=1.5 / 255),
      np.abs(eraw[..., :3] - px["Weightmap1"][..., :3]).max())
tp.write_layers(d, {"Mud": np.zeros(len(d.data.vertices), np.float32)})
check("new layer gets a weightmap channel", dict(d["mp_layers"]).get("Mud") == "Weightmap2.R"
      and "Weightmap2" in weightmaps.images(d), dict(d["mp_layers"]))
check("refresh scheduled", bpy.app.timers.is_registered(tp._tick))
tp.flush()
check("flushed: timer gone", not bpy.app.timers.is_registered(tp._tick))
check("images packed", all(im.packed_file is not None and not im.is_dirty for im in weightmaps.images(d).values()))
check("frame kept", d.get(tp.FRAME) is not None)

# a rebuilt mesh at another resolution, its mp_weights UV lost: laid again from the frame
uv0 = np.empty(len(d.data.loops) * 2, np.float32)
d.data.uv_layers[weightmaps.UV].data.foreach_get("uv", uv0)
f = grid("F_tmp", 65, 64.0)
old = d.data
d.data = f.data
bpy.data.objects.remove(f)
check("rebuilt mesh has no UV", weightmaps.UV not in d.data.uv_layers)
tp.terrain_changed(d)
tp.flush()
uv = np.empty(len(d.data.loops) * 2, np.float32)
d.data.uv_layers[weightmaps.UV].data.foreach_get("uv", uv)
check("UV laid again over the same square", np.isclose(uv.min(), uv0.min(), atol=1e-4) and np.isclose(uv.max(), uv0.max(), atol=1e-4),
      (uv.min(), uv.max(), uv0.min(), uv0.max()))
check("vertex count recorded", d[tp.FRAME]["n"] == 65 * 65)

# --- scatter layers follow the weights
bpy.ops.mesh.primitive_cube_add(size=0.2, location=(0, 0, -50))
blade = bpy.context.object
BASE = {"density": 100, "use_grid": False, "jitter": 1, "scaling": "Uniform", "scale_x": [1, 1], "scale_y": [1, 1],
        "scale_z": [1, 1], "random_rotation": True, "align_to_surface": True, "density_range": [0, 1],
        "weight_attenuates_max_scale": False, "max_scale_weight_attenuation": 0.5, "cull": [3500, 5000],
        "name": "Test"}
for label, land in (("attributes", grid("Land", 21, 40.0)), ("images", d)):
    nv = len(land.data.vertices)
    if label == "images":
        tp.write_layers(land, {"Grass": np.ones(nv, np.float32)})
    else:
        tp.write_layers(land, {"Grass": np.ones(nv, np.float32), "Rock": np.zeros(nv, np.float32)})
    tp.flush()
    lid = scatter.add_layer(land, BASE, blade, weight={"layer": "Grass"})
    before = scatter.count(land, lid)
    tp.write_layers(land, {"Grass": np.zeros(nv, np.float32), "Rock": np.ones(nv, np.float32)})
    tp.terrain_changed(land)
    tp.flush()
    after = scatter.count(land, lid)
    check("%s: scatter follows written weights" % label, before > 100 and after == 0, (before, after))
    half = (xs(land)[:, 0] < xs(land)[:, 0].mean()).astype(np.float32)
    tp.write_layers(land, {"Grass": half, "Rock": 1 - half})
    tp.flush()
    mid = scatter.count(land, lid)
    check("%s: half painted, about half" % label, 0.3 * before < mid < 0.7 * before, (mid, before))

# a layer's source moved to another channel (a material put on since) is read again
h = grid("H", 11, 20.0)
tp.write_layers(h, {"Grass": np.ones(121, np.float32)})
hid = scatter.add_layer(h, BASE, blade, weight={"layer": "Grass"})
weightmaps.ensure([h], ["Grass", "Rock"])       # channels now in images, unpainted
tp.terrain_changed(h)
tp.flush()
check("source re-resolved to the weightmap", scatter._get(scatter._modifier(h, hid), "Weight Source") == "Image")

# --- deleted objects
k = grid("K", 5, 4.0)
tp.terrain_changed(k)
bpy.data.objects.remove(k)
try:
    tp.terrain_changed(k)       # a dead reference
    done = tp.flush()
    check("deleted object: no error", done == 0, done)
except Exception as ex:
    check("deleted object: no error", False, repr(ex))

# --- adopt and game_layers
m = grid("M", 5, 4.0)
check("no material: no game layers", tp.game_layers(m) == [])
tp.adopt(m)
tp.adopt(m)
listed = [o for pieces in land_store.groups(scene).values() for o in pieces]
check("adopted: listed in the Landscape tab", m in listed and m["mp_landscape"] == 1.0)
mat = bpy.data.materials.new("LandMat")
mat.use_nodes = True
mat["mp_path"] = "/Game/Test/M_Land"
for layer in ("Rock", "Grass"):
    node = mat.node_tree.nodes.new("ShaderNodeVertexColor")
    node.label = "layer " + layer
m.data.materials.append(mat)
check("game layers from the material", tp.game_layers(m) == ["Grass", "Rock"], tp.game_layers(m))
check("game layers cached", tp._material(m)[1] == {"Grass", "Rock"} and len(tp._CACHE) >= 1)
mat.node_tree.nodes.new("ShaderNodeVertexColor").label = "layer Snow"
check("cache follows a changed tree", tp.game_layers(m) == ["Grass", "Rock", "Snow"], tp.game_layers(m))
tp.unadopt(m)
listed = [o for pieces in land_store.groups(scene).values() for o in pieces]
check("unadopted: not listed", m not in listed and "mp_landscape" not in m)
check("unadopt leaves a landscape it didn't adopt", (tp.unadopt(d), d.get("mp_landscape"))[1] == 1.0)
check("on_resize: the caller resamples", tp.on_resize(d, 33 * 33, 65 * 65) is False and d.name in tp.STATE["pending"])
tp.flush()
try:
    tp.adopt(bpy.data.objects.new("Empty", None))
    check("adopt refuses a non-mesh", False)
except TypeError:
    check("adopt refuses a non-mesh", True)

# --- timings
for label in ("attributes", "images"):
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=1024, y_subdivisions=1024, size=1000.0)
    big = bpy.context.object
    nv = len(big.data.vertices)
    if label == "images":
        weightmaps.ensure([big], ["L%d" % i for i in range(6)])
    co = xs(big)
    w = {"L%d" % i: np.clip(np.sin(co[:, 0] * 0.01 * (i + 1)), 0, 1).astype(np.float32) for i in range(6)}
    t0 = time.perf_counter()
    tp.write_layers(big, w)
    t1 = time.perf_counter()
    tp.flush()
    t2 = time.perf_counter()
    size = next(iter(weightmaps.images(big).values())).size[0] if label == "images" else 0
    print("[terrain_provider_check] TIMING %s %d vertices, 6 layers%s: write %.3f s, refresh %.3f s"
          % (label, nv, " (%d px maps)" % size if size else "", t1 - t0, t2 - t1))
    check("%s: 1M vertices written in under 5 s" % label, t1 - t0 < 5.0, t1 - t0)

tp.unregister()
print("[terrain_provider_check] %d passed, %d failed" % (PASSES[0], len(FAILS)))
if FAILS:
    sys.exit(1)
