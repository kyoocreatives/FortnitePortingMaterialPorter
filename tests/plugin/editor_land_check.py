"""The Landscape tab's maths and stores (operator/fortnite_editor/land_view.py, land_store.py): pieces as a map import
writes them (corner byte colours, three layers each, obj["mp_layers"]) and a custom landscape's weightmap images.

    blender -b --factory-startup --python-exit-code 1 -P tests/plugin/editor_land_check.py -- <plugin parent>
"""
import sys

import bpy
import numpy as np

sys.path.insert(0, sys.argv[sys.argv.index("--") + 1])
import fpmp_baseline  # noqa: E402,F401
from fpmp_baseline.material_porter import weightmaps  # noqa: E402
from fpmp_baseline.operator.fortnite_editor import land_store, land_view  # noqa: E402

FAILS, PASSES = [], [0]


def check(name, ok, detail=""):
    if ok:
        PASSES[0] += 1
    else:
        FAILS.append(name)
        print("[editor_land_check] FAIL %s %s" % (name, detail))


def piece(name, x0, layers, n=5, spacing=2.0, collection=None):
    """n x n vertices from x0 (metres), packed corner byte colour layers like a map import; Grass 1 everywhere."""
    verts = [(x0 + i * spacing, j * spacing, 0.3 * i + 0.1 * j) for j in range(n) for i in range(n)]
    faces = [(j * n + i, j * n + i + 1, (j + 1) * n + i + 1, (j + 1) * n + i) for j in range(n - 1) for i in range(n - 1)]
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    table = {}
    for k, layer in enumerate(sorted(layers)):
        attribute, channel = "Attribute%d" % (k // 3 + 1), k % 3
        if attribute not in me.color_attributes:
            a = me.color_attributes.new(attribute, 'BYTE_COLOR', 'CORNER')
            black = np.zeros((len(a.data), 4), np.float32)
            black[:, 3] = 1.0
            a.data.foreach_set("color", black.ravel())
        table[layer] = "%s.%s" % (attribute, "RGB"[channel])
    obj = bpy.data.objects.new(name, me)
    (collection or bpy.context.scene.collection).objects.link(obj)
    obj["mp_landscape"] = 1.0
    obj["mp_layers"] = table
    a, c = table["Grass"].split(".")
    attr = me.color_attributes[a]
    v = np.empty(len(attr.data) * 4, np.float32)
    attr.data.foreach_get("color", v)
    v = v.reshape(-1, 4)
    v[:, "RGB".index(c)] = 1.0
    attr.data.foreach_set("color", v.ravel())
    return obj


def vertex_weights(obj, layer):
    """{(x, y): weight} of a piece's layer, read straight from its attribute."""
    a, c = obj["mp_layers"][layer].split(".")
    attr = obj.data.color_attributes[a]
    v = np.empty(len(attr.data) * 4, np.float32)
    attr.data.foreach_get("color", v)
    v = v.reshape(-1, 4)[:, "RGB".index(c)]
    out = {}
    for loop in obj.data.loops:
        co = obj.matrix_world @ obj.data.vertices[loop.vertex_index].co
        out[(round(co.x, 3), round(co.y, 3))] = float(v[loop.index])
    return out


terrain = bpy.data.collections.new("Test_Terrain")
bpy.context.scene.collection.children.link(terrain)
a = piece("A", 0.0, ["Grass", "Rock"], collection=terrain)
b = piece("B", 8.0, ["Grass", "Rock", "Sand"], collection=terrain)       # shares the column x = 8 with A
groups = land_store.groups(bpy.context.scene)
check("groups by collection", groups.get("Test_Terrain") == [a, b] or sorted(o.name for o in groups.get("Test_Terrain", [])) == ["A", "B"], groups)
store = land_store.load([a, b])
g = store.grid
check("store kind", store.kind == "attributes", store.kind)
check("layers: the union", g.layers == ["Grass", "Rock", "Sand"], g.layers)
check("one cell per vertex spacing", abs(g.cell - 2.0) < 1e-6 and g.shape == (5, 9), (g.cell, g.shape))
check("grass read", np.allclose(g.W[0][g.filled], 1.0))
check("sand only where B is", not g.avail[2][:, :5].any() and g.avail[2][:, 5:].all(), g.avail[2].astype(int))
before = {o.name: vertex_weights(o, "Grass") for o in (a, b)}
store.write(store.touch(0, 0, *g.shape))
check("round trip leaves weights as they were", all(vertex_weights(o, "Grass") == before[o.name] for o in (a, b)))

# paint Rock on the seam (x = 8): both pieces' border vertices end equal, every vertex summed to 1
region, amount = land_view.dab(g, 8.0, 4.0, 3.0, 0.5)
land_view.apply(g, "Rock", region, amount, 1.0, "paint")
store.write(store.touch(*land_view.bounds(region)))
ra, rb = vertex_weights(a, "Rock"), vertex_weights(b, "Rock")
seam = [k for k in ra if k[0] == 8.0]
check("paint raises the layer", ra[(8.0, 4.0)] > 0.9, ra[(8.0, 4.0)])
check("seam vertices equal", all(abs(ra[k] - rb[k]) < 1 / 255 + 1e-6 for k in seam), [(ra[k], rb[k]) for k in seam])
sums = g.W[:, g.filled].sum(axis=0)
check("weights summed to 1", np.allclose(sums, 1.0, atol=1e-5), sums.min())

# Sand: only B gets it, A untouched
before_a = vertex_weights(a, "Grass")
region, amount = land_view.dab(g, 12.0, 4.0, 6.0, 0.5)
skipped = land_view.apply(g, "Sand", region, amount, 1.0, "paint")
store.write(store.touch(*land_view.bounds(region)))
check("a piece without the layer is left alone", vertex_weights(a, "Grass") == before_a)
check("the cells lacking it are reported", skipped > 0, skipped)
sb = vertex_weights(b, "Sand")
check("the piece with it gets it", sb[(12.0, 4.0)] > 0.5, sb[(12.0, 4.0)])
check("the shared border isn't offered a layer one side lacks", sb[(8.0, 4.0)] == 0.0, sb[(8.0, 4.0)])
check("sand only where every piece on the cell has it", not g.avail[2][:, :5].any() and g.avail[2][:, 5:].all(), g.avail[2].astype(int))

# erase where Grass is the only layer: its weight goes to the other layers, never to nothing
g.W[:, 0, 0] = (1.0, 0.0, 0.0)
region, amount = land_view.dab(g, 0.0, 0.0, 1.0, 0.0)
land_view.apply(g, "Grass", region, amount, 1.0, "erase")
check("erase moves the weight to the other layers", g.W[0, 0, 0] < 1e-6 and abs(g.W[1, 0, 0] - 1.0) < 1e-6, g.W[:, 0, 0])
check("erase keeps sums at 1", np.allclose(g.W[:, g.filled].sum(axis=0), 1.0, atol=1e-5))

# smooth pulls a lone spike toward its neighbours
g.W[:, 2, 2] = (0.0, 1.0, 0.0)
g.W[:, 1:4, 1:2] = np.array([1.0, 0.0, 0.0])[:, None, None]
region, amount = land_view.dab(g, 4.0, 4.0, 2.5, 0.0)
spike = g.W[1, 2, 2]
land_view.apply(g, "Rock", region, amount, 1.0, "smooth")
check("smooth lowers a spike", g.W[1, 2, 2] < spike, g.W[1, 2, 2])
check("smooth keeps sums at 1", np.allclose(g.W[:, g.filled].sum(axis=0), 1.0, atol=1e-5))

# blend modes: weight-blended layers share 1, independent ones (Hera's masks) only move themselves
check("detected: layers summing to 1 are blended", store.grid.blended.all(), store.grid.blended)
rng = np.random.default_rng(1)
Wi = rng.uniform(0, 1, (3, 50)).astype(np.float32)
check("detected: layers that never sum to 1 are independent", not land_view.detect_blended(Wi).any(), land_view.detect_blended(Wi))
Wm = np.vstack([np.full((1, 50), 0.3, np.float32), rng.uniform(0, 1, (2, 50)).astype(np.float32)])
Wm[2] = 1.0 - Wm[1]
check("detected: the subset that sums to 1", list(land_view.detect_blended(Wm)) == [False, True, True], land_view.detect_blended(Wm))
gi = land_view.Grid(["Gravel", "Mud", "Sand"], (0.0, 0.0), 1.0, (5, 5))
gi.filled[:] = True
gi.avail[:] = True
gi.W[:] = np.array([0.6, 0.5, 0.3], np.float32)[:, None, None]
gi.blended[:] = False
region, amount = land_view.dab(gi, 2.0, 2.0, 1.5, 0.0)
land_view.apply(gi, "Gravel", region, amount, 1.0, "paint")
check("independent paint moves only its layer", abs(gi.W[0, 2, 2] - 1.0) < 1e-6 and abs(gi.W[1, 2, 2] - 0.5) < 1e-6 and abs(gi.W[2, 2, 2] - 0.3) < 1e-6,
      gi.W[:, 2, 2])
land_view.apply(gi, "Mud", region, amount, 1.0, "erase")
check("independent erase clears only its layer", abs(gi.W[1, 2, 2]) < 1e-6 and abs(gi.W[2, 2, 2] - 0.3) < 1e-6, gi.W[:, 2, 2])

# soft strokes of two linked layers side by side on unpainted ground (a new custom landscape): the second's dabs
# never lift the first's faint edge to full (each dab used to rescale its whole disc to sum 1: a hard ring)
gs = land_view.Grid(["Green", "Orange"], (0.0, 0.0), 1.0, (40, 60))
gs.filled[:] = gs.avail[:] = True
for x in (15, 18, 21):
    region, amount = land_view.dab(gs, x, 20, 6, 1.0)
    land_view.apply(gs, "Orange", region, amount, 0.5, "paint")
orange = gs.W[1].copy()
for x in (30, 33):
    region, amount = land_view.dab(gs, x, 20, 6, 1.0)
    land_view.apply(gs, "Green", region, amount, 0.5, "paint")
check("painting a linked layer never raises another", (gs.W[1] <= orange + 1e-6).all(), float((gs.W[1] - orange).max()))
step = lambda w: float(np.abs(np.diff(w, axis=1)).max())      # noqa: E731
check("soft strokes stay soft where they meet", step(gs.W[1]) <= step(orange) + 1e-3 and step(gs.W.sum(axis=0)) < 0.15,
      (step(orange), step(gs.W[1]), step(gs.W.sum(axis=0))))
total = gs.W.sum(axis=0)
region, amount = land_view.dab(gs, 28, 20, 4, 1.0)
land_view.apply(gs, "Green", region, amount, 1.0, "smooth")
check("smoothing partly painted ground eases its total, no jump to 1", float(np.abs(gs.W.sum(axis=0) - total).max()) < 0.1,
      float(np.abs(gs.W.sum(axis=0) - total).max()))

# the picture: a colour per filled cell, transparent elsewhere; the world maps to the cells
colors = land_view.palette(g.layers)
rgba = land_view.compose(g, colors, set(g.layers), None)
check("picture shape", rgba.shape == (5, 9, 4))
check("picture opaque on the land", (rgba[..., 3][g.filled] == 1.0).all())
check("palette distinct", len({tuple(round(c, 3) for c in v) for v in colors.values()}) == 3)
col, row = g.to_cell(10.0, 6.0)
check("world to cell", (round(col), round(row)) == (5, 3), (col, row))
only = land_view.compose(g, colors, set(g.layers), "Rock")
check("layer only: the layer's weight as grey", abs(only[2, 4, 0] - g.W[1, 2, 4]) < 1e-5, (only[2, 4, :3], g.W[1, 2, 4]))

# the view: the grid fitted in the region, zoomed about the mouse, panned
fr = land_view.Frame(9, 5, 400, 300)
x0, y0 = fr.to_region(0, 0)
x1, y1 = fr.to_region(9, 5)
check("frame fits, north up", 0 <= x0 < x1 <= 400 and 0 <= y0 < y1 <= 300 and abs((x1 - x0) / (y1 - y0) - 9 / 5) < 1e-6, (x0, y0, x1, y1))
at = fr.to_cell(200, 150)
fr.zoom_at(200, 150, 3)
check("zoom keeps the point under the mouse", all(abs(u - v) < 1e-6 for u, v in zip(fr.to_cell(200, 150), at)))
fr.pan(10, -5)
cx, cy = fr.to_region(*at)
check("pan moves the picture", abs(cx - 210) < 1e-6 and abs(cy - 145) < 1e-6, (cx, cy))
check("cells per pixel", fr.scale > 0)
fw = land_view.Frame(9, 5, 400, 300, 12, 220, 6)       # the shell's well: past the nav and the list column
(x0, y0), (x1, y1) = fw.to_region(0, 0), fw.to_region(9, 5)
check("frame fits the well's box", 232 <= x0 < x1 <= 608 and 18 <= y0 < y1 <= 294 and abs((x0 + x1) / 2 - 420) < 1e-6,
      (x0, y0, x1, y1))
check("a well too small still frames", land_view.Frame(9, 5, 10, 10).scale > 0)
check("an untouched view isn't moved, a zoomed one is", not fw.moved and fr.moved)
centre = fr.to_cell(200, 150)
fr.follow(100, 0, 400, 300)
check("a zoomed view follows its box's centre", all(abs(u - v) < 1e-6 for u, v in zip(fr.to_cell(300, 150), centre)))

# a custom landscape: weightmap images
me = bpy.data.meshes.new("custom")
me.from_pydata([(0, 0, 0), (40, 0, 0), (40, 40, 1), (0, 40, 1)], [], [(0, 1, 2, 3)])
custom = bpy.data.objects.new("custom", me)
bpy.context.scene.collection.objects.link(custom)
weightmaps.ensure([custom], ["Dirt", "Grass"])
weightmaps.fill(custom, "Grass", 1.0)
s2 = land_store.load([custom])
check("images kind", s2.kind == "images", s2.kind)
check("images grid is the weightmap", s2.grid.shape[0] == next(iter(weightmaps.images(custom).values())).size[1], s2.grid.shape)
region, amount = land_view.dab(s2.grid, 20.0, 20.0, 5.0, 0.5)
land_view.apply(s2.grid, "Dirt", region, amount, 1.0, "paint")
s2.write(s2.touch(*land_view.bounds(region)))
dirt = weightmaps.weight(custom, "Dirt")
check("images painted", dirt and dirt > 1.0, dirt)
check("images sums at 1", np.allclose(s2.grid.W[:, s2.grid.filled].sum(axis=0), 1.0, atol=1e-3))
check("weightmaps summing to 1 read linked", s2.grid.blended.all(), s2.grid.blended)
me_f = bpy.data.meshes.new("fresh")
me_f.from_pydata([(100, 0, 0), (140, 0, 0), (140, 40, 0), (100, 40, 0)], [], [(0, 1, 2, 3)])
fresh = bpy.data.objects.new("fresh", me_f)
bpy.context.scene.collection.objects.link(fresh)
weightmaps.ensure([fresh], ["BiomeA", "Gravel"])
check("unpainted weightmaps read unlinked (Hera samples each layer alone)", not land_store.load([fresh]).grid.blended.any(),
      land_store.load([fresh]).grid.blended)

# a custom landscape's biome map (Hera's: four quads of RGBA masks), painted with the tab's brush
import json  # noqa: E402
from fpmp_baseline.material_porter import biome_maps  # noqa: E402
from fpmp_baseline.material_porter.ue_graph import maptex  # noqa: E402
RECTS = [[0.0, 0.0, 0.5, 0.5], [0.5, 0.0, 1.0, 0.5], [0.0, 0.5, 0.5, 1.0], [0.5, 0.5, 1.0, 1.0]]
mat = bpy.data.materials.new("LandMat")
mat["mp_map_textures"] = json.dumps({"BiomeTexture": {"texture": "/x/T_BiomeMask.T_BiomeMask", "rects": RECTS,
                                                      "labels": {"0,0": "Red Woods", "1,2": "Desert"}}})
custom.data.materials.append(mat)
check("no biome map until made paintable", land_store.try_biomes([custom], s2) == (None, None))
info = biome_maps.maps(mat)["BiomeTexture"]
image = biome_maps.create([custom], "BiomeTexture", info)
size = image.size[0]
rng = np.random.default_rng(7)
px = rng.uniform(0, 1, (size, size, 4)).astype(np.float32)
image.pixels.foreach_set(px.ravel())
bs, why = land_store.try_biomes([custom], s2)
check("biome map read", bs is not None and why is None, why)
bg = bs.grid
check("biomes: the named first, a cell per map pixel", bg.layers[:2] == ["Red Woods", "Desert"] and bg.shape == (size // 2, size // 2),
      (bg.layers[:3], bg.shape))
cells = [(int(r), int(c)) for r, c in rng.integers(0, size // 2, (20, 2))]
ok = True
for r, c in cells:         # each cell reads the pixel the material samples there (maptex.paint_uv)
    u, v = (c + 0.5) / bg.shape[1], (r + 0.5) / bg.shape[0]
    for k, (i, ch) in enumerate(bs.masks):
        x, y = maptex.paint_uv(RECTS[i], u, v)
        ok &= abs(bg.W[k, r, c] - px[int(y * size), int(x * size), ch]) < 1 / 255 + 1e-6      # (a byte image)
check("biome cells read the pixels the material samples", ok)
bs.write(bs.touch(0, 0, *bg.shape))
back = np.empty(size * size * 4, np.float32)
image.pixels.foreach_get(back)
check("biome round trip leaves the map as it was", np.allclose(back.reshape(size, size, 4), px, atol=1 / 255 + 1e-6))
bg.W[:] = 0.0
bg.W[bg.index("Desert")] = 1.0
red, desert = bg.index("Red Woods"), bg.index("Desert")
region, amount = land_view.dab(bg, 20.0, 20.0, 6.0, 0.5)
land_view.apply(bg, "Red Woods", region, amount, 0.5, "paint")
centre = bg.to_cell(20.0, 20.0)
rc = (int(round(centre[1])), int(round(centre[0])))
check("painting a biome lowers the others as much (one biome per texel)",
      abs(bg.W[red][rc] + bg.W[desert][rc] - 1.0) < 1e-5 and bg.W[red][rc] > 0.4, (bg.W[red][rc], bg.W[desert][rc]))
check("biome strokes stay soft", float(np.abs(np.diff(bg.W[desert], axis=1)).max()) < 0.2)
before = bg.W.copy()
land_view.apply(bg, "Red Woods", region, amount, 1.0, "erase")
check("erasing a biome gives back to no biome, not to the others", np.allclose(bg.W[desert], before[desert])
      and bg.W[red][rc] < before[red][rc], (before[red][rc], bg.W[red][rc]))
land_view.apply(bg, "Red Woods", region, amount, 0.5, "paint")
bs.write(bs.touch(*land_view.bounds(region)))
image.pixels.foreach_get(back)
back = back.reshape(size, size, 4)
x, y = maptex.paint_uv(RECTS[1], (rc[1] + 0.5) / bg.shape[1], (rc[0] + 0.5) / bg.shape[0])
check("the stroke reaches the map: Desert's quad lowered under the brush", back[int(y * size), int(x * size), 2] < 0.9,
      back[int(y * size), int(x * size), 2])

keys = [bs.hide_key(n) for n in bg.layers]
check("a biome's hide property: its own, a valid property name", len(set(keys)) == len(keys) and max(map(len, keys)) <= 63
      and keys[0] == maptex.hide_key(image.name, RECTS[0], 0), keys[:2])

# a rebuild bringing in a layer (Update Material with snow on BiomeC: weightmaps.ensure) reads the group again
from fpmp_baseline.operator.fortnite_editor import land_tab as lt_  # noqa: E402
before = lt_._reads(custom)
weightmaps.ensure([custom], ["Dirt", "Grass", "Snow"])
check("a layer added to a piece changes what the tab reads", lt_._reads(custom) != before)
check("  and the store read again lists it", "Snow" in land_store.load([custom]).grid.layers)
mat2 = bpy.data.materials.new("LandMat.001")
before = lt_._reads(custom)
custom.active_material = mat2
check("a new material on a piece changes what the tab reads", lt_._reads(custom) != before)

# switching the brush between grids keeps the view on the same ground
fr = land_view.Frame(s2.grid.shape[1], s2.grid.shape[0], 400, 400)
fr.zoom_at(130, 170, 4)
col, row = s2.grid.to_cell(23.0, 31.0)
at = fr.to_region(col + 0.5, row + 0.5)
land_view.retarget(fr, s2.grid, bg)
col, row = bg.to_cell(23.0, 31.0)
check("retarget keeps a world point under the same pixel", np.allclose(fr.to_region(col + 0.5, row + 0.5), at, atol=1e-4),
      (fr.to_region(col + 0.5, row + 0.5), at))

# --- final review: writes touch only painted cells; collisions refine the grid; failures don't raise; smoothing a
# linked layer leaves unlinked ones; undo saves only the stroke's layers; image heights fill smoothly
far = piece("Far", 200.0, ["Grass", "Rock"], n=9, spacing=1.0)
far_store = land_store.load([far])
fg = far_store.grid
attr, ch = far["mp_layers"]["Rock"].split(".")
raw = np.empty(len(far.data.loops) * 4, np.float32)
far.data.color_attributes[attr].data.foreach_get("color", raw)
raw = raw.reshape(-1, 4)
raw[:, "RGB".index(ch)] = 0.25          # changed outside the tab (the panel's Fill) after the store read the piece
far.data.color_attributes[attr].data.foreach_set("color", raw.ravel())
region, amount = land_view.dab(fg, 200.0, 0.0, 1.0, 0.0)
dirty = land_view.mark(fg, region)
land_view.apply(fg, "Rock", region, amount, 1.0, "paint")
far_store.write(far_store.touch(*land_view.bounds(region)), {"Rock", "Grass"}, dirty)
rock_far = vertex_weights(far, "Rock")
check("a write leaves cells the stroke didn't touch", abs(rock_far[(208.0, 8.0)] - 0.25) < 1 / 255 + 1e-6, rock_far[(208.0, 8.0)])
check("and sets the painted ones", rock_far[(200.0, 0.0)] > 0.9, rock_far[(200.0, 0.0)])

# a reduced-detail piece: its lattice half a cell off a finer piece's — every vertex still its own cell
half = piece("Half", 300.5, ["Grass", "Rock"], n=5, spacing=1.0)
fine = piece("Fine", 296.0, ["Grass", "Rock"], n=5, spacing=1.0)
for o in (half, fine):        # one ground under both: the height of the world point
    for v in o.data.vertices:
        v.co.z = 0.3 * v.co.x + 0.1 * v.co.y
both = land_store.load([fine, half])
cells = {}
for p in both.pieces:
    for r, c in zip(p["rows"], p["cols"]):
        cells.setdefault((int(r), int(c)), set()).add(p["obj"].name)
distinct = len({(int(r), int(c)) for p in both.pieces for r, c in zip(p["rows"], p["cols"])})
check("half-offset lattices land in distinct cells", distinct == 50, distinct)

rgba_both = land_view.compose(both.grid, land_view.palette(both.grid.layers), set(both.grid.layers), None)
inside = rgba_both[2:-2, 2:-2, 3]
check("the picture has no holes between a finer grid's vertices", (inside == 1.0).all(), float((inside == 1.0).mean()))
shade_both = land_view.hillshade(both.grid.height, both.grid.cell, both.grid.filled, land_view.source(both.grid))
check("and no dark speckle in the shading", float(shade_both[2:-2, 2:-2].std()) < 0.05, float(shade_both[2:-2, 2:-2].std()))
raw_shade = land_view.hillshade(both.grid.height, both.grid.cell, both.grid.filled)
check("  (the same land unfilled does speckle: the check sees it)", float(raw_shade[2:-2, 2:-2].std()) > 2 * float(shade_both[2:-2, 2:-2].std()) + 0.02,
      (float(raw_shade[2:-2, 2:-2].std()), float(shade_both[2:-2, 2:-2].std())))

empty_me = bpy.data.meshes.new("empty")
empty = bpy.data.objects.new("Empty", empty_me)
bpy.context.scene.collection.objects.link(empty)
empty["mp_landscape"] = 1.0
store_e, error = land_store.try_load([empty])
check("a piece that can't be read: an error, not a raise", store_e is None and error, error)

gm = land_view.Grid(["A", "B", "Mask"], (0.0, 0.0), 1.0, (5, 5))
gm.filled[:] = gm.avail[:] = True
gm.W[:] = np.array([0.5, 0.5, 0.7], np.float32)[:, None, None]
gm.W[0, 2, 2], gm.W[1, 2, 2] = 1.0, 0.0
gm.blended[:] = (True, True, False)
region, amount = land_view.dab(gm, 2.0, 2.0, 1.5, 0.0)
land_view.apply(gm, "A", region, amount, 1.0, "smooth")
check("smoothing a linked layer leaves the unlinked ones", np.allclose(gm.W[2], 0.7), gm.W[2, 1:4, 1:4])

hi = s2.grid.height
check("image heights filled from the nearest vertices, not a flat mean", float(np.abs(np.diff(hi, axis=0)).max()) < 0.5,
      float(np.abs(np.diff(hi, axis=0)).max()))

from fpmp_baseline.operator.fortnite_editor import land_tab  # noqa: E402
check("tab: undo, redo, file load and scene edits reread the landscape, and survive File > Open",
      all(hasattr(f, "_bpy_persistent") for f in (land_tab._undone, land_tab._loaded, land_tab._scene_changed))
      and {n for n, _ in land_tab.HANDLERS} >= {"undo_post", "redo_post", "load_post", "depsgraph_update_post"})

print("[editor_land_check] %d passed, %d failed" % (PASSES[0], len(FAILS)))
if FAILS:
    sys.exit(1)
