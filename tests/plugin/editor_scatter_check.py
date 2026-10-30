"""The Landscape tab's Scatter mode logic, headless (operator/fortnite_editor/land_scatter.py, scatter_ops.py, the
MaskStore in land_store.py): a layer over two map-like pieces listed and counted as one, labels kept unique, the first
piece's settings copied to the second, the mask store's weight (mask x source: a grass attribute, a weightmap layer on a
custom terrain), mask painting written back and lowering the count, a layer's Biome row (offered and named) and wind
sprites listed hidden, placed foliage found over the pieces and thinned with its attributes kept, the MPGrass helpers,
and placement storing MPGrass on a landscape.

    python plugin_test.py tests/plugin/editor_scatter_check.py --name editor_scatter
"""
import json
import sys

import bpy
import numpy as np

sys.path.insert(0, sys.argv[sys.argv.index("--") + 1])
import fpmp_baseline  # noqa: E402,F401
from fpmp_baseline.material_porter import placement, scatter, weightmaps  # noqa: E402
from fpmp_baseline.operator.fortnite_editor import land_scatter, land_scatter_info, land_store, land_view, scatter_ops  # noqa: E402

FAILS, PASSES = [], [0]


def check(name, ok, detail=""):
    if ok:
        PASSES[0] += 1
    else:
        FAILS.append(name)
    print("[editor_scatter_check] %s %s %s" % ("PASS" if ok else "FAIL", name, detail))


bpy.ops.wm.read_homefile(use_empty=True)
scene = bpy.context.scene
terrain = bpy.data.collections.new("Map")
scene.collection.children.link(terrain)


def plane(name, x0, size=20.0, cuts=21, collection=None):
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=cuts, y_subdivisions=cuts, size=size, location=(x0, 0, 0))
    o = bpy.context.object
    o.name = name
    for c in list(o.users_collection):
        c.objects.unlink(o)
    (collection or terrain).objects.link(o)
    o["mp_landscape"] = 1.0
    o["mp_layers"] = {}
    return o


def counts():
    return land_scatter.count_all(land_scatter.entries(pieces, scene))


a, b = plane("Land_A", 0.0), plane("Land_B", 20.0)
pieces = [a, b]
for o in pieces:        # a grass mask: 1 west of x = 10, 0 east
    co = np.empty(len(o.data.vertices) * 3, np.float32)
    o.data.vertices.foreach_get("co", co)
    x = co.reshape(-1, 3)[:, 0] + o.location.x
    attr = o.data.attributes.new(scatter.GRASS + "Grass01", 'FLOAT', 'POINT')
    attr.data.foreach_set("value", (x < 10.0).astype(np.float32))
    o["mp_grass"] = json.dumps({"material": "/M", "outputs": [{"name": "Grass01", "type": [
        {"type": "/Game/LGT_Grass01", "variety": 0, "mesh": "/Game/Grass_A"}]}]})
bpy.ops.mesh.primitive_cube_add(size=0.2)
blade = bpy.context.object
blade.name = "Blade"

# adding: one label over both pieces, unique in the group
settings = {"type": "/Game/LGT_Grass01", "variety": 0, "mesh": "/Game/Grass_A", "density": 400.0}
key = scatter_ops.add_layer_to(pieces, settings, blade, {"attribute": "Grass01"})
again_key = scatter_ops.add_layer_to(pieces, dict(settings, variety=1), blade, None)
found = land_scatter.entries(pieces, scene)
label, again = [next(e["label"] for e in found if e["key"] == k) for k in (key, again_key)]
check("labels from the mesh, unique", (label, again) == ("Grass_A", "Grass_A 2"), (label, again))
check("keys are the type's", key == land_scatter.layer_key("Grass_A", "/Game/LGT_Grass01"), key)
check("one entry per label over both pieces", [len(e["members"]) for e in found] == [2, 2], found)
check("game entries grouped", [e["kind"] for e in found] == ["game", "game"], [e["kind"] for e in found])
check("output_for finds the grass output", land_scatter.output_for(pieces, "/Game/LGT_Grass01.LGT_Grass01") == "Grass01")
check("output_for: none for another type", land_scatter.output_for(pieces, "/Game/LGT_Other") is None)
check("describe a grass weight", land_scatter.describe(a, {"attribute": "Grass01"}) == "Grass01 mask")

bpy.context.view_layer.update()
first = counts()
weighted, plain = first[key], first[again_key]
area = 40.0 * 20.0
check("an unweighted layer counts both pieces", abs(plain - area * 4) < area * 4 * 0.1, (plain, area * 4))
check("the grass mask halves it (west only)", abs(weighted - plain / 2) < plain * 0.06, (weighted, plain))

# the first piece's settings follow to the other
e = next(e for e in found if e["label"] == again)
mods = land_scatter.modifiers(e)
scatter._set(mods[0][1], "Density", 100.0)
land_scatter._sync(e)
check("the first piece's density copied to the second", scatter._get(mods[1][1], "Density") == 100.0,
      scatter._get(mods[1][1], "Density"))

# the mask store: weight = mask x source, painting writes the masks
paint_store = land_store.AttributeStore(pieces)
store = land_store.MaskStore(pieces, paint_store)
check("mask store on the paint store's lattice", store.grid.shape == paint_store.grid.shape, store.grid.shape)
e = next(e for e in found if e["label"] == label)
store.set_layer(label, {n: scatter.MASK + layer for n, layer, _ in e["members"]},
                lambda obj: land_scatter.source_values(obj, e["weight"]))
w = store.weight()
g = store.grid
col_west, col_east = g.to_cell(5.0, 0.0)[0], g.to_cell(25.0, 0.0)[0]
row = int(round(g.to_cell(0.0, 0.0)[1]))
check("weight 1 west, 0 east (absent mask = 1)", w[row, int(round(col_west))] == 1.0 and w[row, int(round(col_east))] == 0.0)
rgba = store.picture(None, 1)
check("picture tints the weighted ground", rgba[row, int(round(col_west)), 1] > rgba[row, int(round(col_east)), 1] + 0.05)
region, amount = land_view.dab(g, 5.0, 0.0, 4.0, 0.0)
land_view.apply(g, label, region, amount, 1.0, "erase")
dirty = land_view.mark(g, region)
store.write(store.touch(*land_view.bounds(region)), None, dirty)
mask = a.data.attributes.get(scatter.MASK + e["members"][0][1])
check("the mask attribute made by the first paint", mask is not None and mask.domain == 'POINT')
values = land_store.mask_values(a, mask.name)
check("erased under the brush, 1 elsewhere", values.min() == 0.0 and values.max() == 1.0, (values.min(), values.max()))
check("the other piece untouched (brush west)", b.data.attributes.get(scatter.MASK + e["members"][1][1]) is None)
bpy.context.view_layer.update()
after = counts()[key]
check("the erased mask lowers the count", after < weighted * 0.9, (weighted, after))
check("readout names mask and source", "mask 0.00" in store.readout(5.0, 0.0), store.readout(5.0, 0.0))

# a custom terrain: weightmap images; weight from a landscape layer
custom = plane("Custom", 0.0, size=40.0, cuts=41, collection=bpy.data.collections.new("Custom"))
scene.collection.children.link(custom.users_collection[0])
weightmaps.ensure([custom], ["Dirt", "Grass"])
weightmaps.fill(custom, "Grass", 1.0)
image_store = land_store.ImageStore([custom])
cstore = land_store.MaskStore([custom], image_store)
check("custom terrain: a vertex lattice", cstore.grid.shape == (42, 42), cstore.grid.shape)
layer = scatter.add_layer(custom, {"density": 100.0}, blade, label="Rock", weight={"layer": "Grass"})
grass = land_scatter.source_values(custom, {"layer": "Grass"})
check("a weightmap layer as source", grass is not None and abs(float(grass.mean()) - 1.0) < 1e-3, None if grass is None else grass.mean())
dirt = land_scatter.source_values(custom, {"layer": "Dirt"})
check("an unpainted layer reads 0", float(dirt.max()) == 0.0)
cstore.set_layer("Rock", {custom.name: scatter.MASK + layer}, lambda obj: land_scatter.source_values(obj, {"layer": "Grass"}))
check("custom weight 1 over the painted layer", float(cstore.weight()[cstore.grid.filled].min()) == 1.0)

# a layer's Biome row: offered when its instance's material reads a biome mask and the mesh paints none of its own
ground = bpy.data.materials.new("Ground")
ground["mp_map_textures"] = json.dumps({"BiomeTexture": {
    "texture": "/T/T_BiomeMask.T_BiomeMask", "rects": [[0, 0, .5, .5], [.5, 0, 1, .5], [0, .5, .5, 1], [.5, .5, 1, 1]],
    "labels": {"0,0": "Meadow", "0,1": "Red Woods", "1,3": "Remove Stripes"}}})
rock = next(e for e in land_scatter.entries([custom], scene) if e["label"] == "Rock")
check("no Biome row without a biome mask", land_scatter._biomes(custom, rock) == [])
blade.data.materials.append(ground)
biomes = land_scatter._biomes(custom, rock)
check("the instance material's biomes", biomes == [(1, "Meadow"), (2, "Red Woods")], biomes)


def biome_row():
    values = scatter._values(land_scatter.modifiers(rock)[0][1])
    return next((p for k, _s, p in land_scatter_info.info_rows(rock, values, False, biomes) if k == "biome"), None)


check("the Biome row reads World by default", biome_row() == ("Biome", "World"), biome_row())
scatter.set_biome(custom, layer, 2)
check("and the chosen biome's name", biome_row() == ("Biome", "Red Woods"), biome_row())
scatter.set_biome(custom, layer, 0)
custom["mp_biome_maps"] = {"BiomeTexture": "x"}
check("none on a mesh painting its own biome maps", land_scatter._biomes(custom, rock) == [])
del custom["mp_biome_maps"]
blade.data.materials.clear()
bpy.ops.mesh.primitive_cube_add(size=0.2)
motes = bpy.context.object
motes.name = "SM_Apollo_Motes_Few_01"
sprite = scatter.add_layer(custom, {"density": 100.0}, motes, label="Motes")
e = next(e for e in land_scatter.entries([custom], scene) if e["label"] == "Motes")
check("wind sprites listed hidden", e["hidden"] and scatter.is_wind_sprite(scatter._get(land_scatter.modifiers(e)[0][1], "Instance")))
scatter.remove_layer(custom, sprite)

# placed foliage: found over the pieces (not by collection: a map's cells import apart from its terrain), thinned with
# its attributes
pts = bpy.data.meshes.new("Placed")
pts.vertices.add(100)
pts.vertices.foreach_set("co", np.column_stack([np.arange(100) * 0.3, np.zeros(100), np.zeros(100)]).astype(np.float32).ravel())
rot = pts.attributes.new("rotation", 'QUATERNION', 'POINT')
rot.data.foreach_set("value", np.tile([1, 0, 0, 0], 100).astype(np.float32) * np.repeat(np.arange(100), 4).astype(np.float32))
pts.attributes.new("scale", 'FLOAT_VECTOR', 'POINT').data.foreach_set("vector", np.repeat(np.arange(100), 3).astype(np.float32))
placed = bpy.data.objects.new("Placed", pts)
terrain.objects.link(placed)
placed["mp_placed"] = {"mesh": "/Game/SM_Bush.SM_Bush", "class": "ISM", "actor": "PCG", "count": 100}
far = bpy.data.meshes.new("Elsewhere")      # beyond the pieces
far.vertices.add(2)
far.vertices.foreach_set("co", np.array([5000, 5000, 0, 5010, 5000, 0], np.float32))
other = bpy.data.objects.new("Elsewhere", far)
custom.users_collection[0].objects.link(other)
other["mp_placed"] = {"mesh": "/Game/X"}
found = land_scatter.entries(pieces, scene)
check("placed foliage over the pieces listed, not elsewhere", [e["object"] for e in found if e["kind"] == "placed"] == ["Placed"])
check("placed label from its mesh", found[-1]["label"] == "SM_Bush", found[-1]["label"])
keep = np.arange(100) % 3 != 0
land_scatter.keep_points(pts, keep)
q = np.empty(len(pts.vertices) * 4, np.float32)
pts.attributes["rotation"].data.foreach_get("value", q)
s = np.empty(len(pts.vertices) * 3, np.float32)
pts.attributes["scale"].data.foreach_get("vector", s)
check("points thinned", len(pts.vertices) == int(keep.sum()), len(pts.vertices))
check("their rotation and scale kept with them", np.allclose(q[0::4], np.flatnonzero(keep)) and
      np.allclose(s[0::3], np.flatnonzero(keep)))
check("placed count", counts()["P:Placed"] == int(keep.sum()))

# removing a layer from both pieces
for n, lid, _ in next(e for e in found if e["label"] == again)["members"]:
    scatter.remove_layer(bpy.data.objects[n], lid)
check("removed from both pieces", [e["label"] for e in land_scatter.entries(pieces, scene) if e["kind"] != "placed"] == [label])

# Add Game Grass on another group: empty outputs skipped (no mesh asked), labels numbered within their type, one
# layer step once every mesh is in; Update Grass Masks adds a skipped output once painted, the others left alone
from fpmp_baseline.operator.fortnite_editor import logbook, scatter_sources  # noqa: E402

group = bpy.data.collections.new("Skip")
scene.collection.children.link(group)
c, d = plane("Skip_A", 100.0, collection=group), plane("Skip_B", 120.0, collection=group)
skip_pieces = [c, d]
G1, SNOW = "/Game/LGT_G1.LGT_G1", "/Game/LGT_Snow.LGT_Snow"


def mask(o, name, west):
    co = np.empty(len(o.data.vertices) * 3, np.float32)
    o.data.vertices.foreach_get("co", co)
    x = co.reshape(-1, 3)[:, 0] + o.location.x
    a = o.data.attributes.get(scatter.GRASS + name) or o.data.attributes.new(scatter.GRASS + name, 'FLOAT', 'POINT')
    a.data.foreach_set("value", ((x < 110.0) if west else np.zeros(len(x), bool)).astype(np.float32))


for o in skip_pieces:
    mask(o, "Grass01", True)
    mask(o, "Snow", False)
    o["mp_grass"] = json.dumps({"material": "/M", "outputs": [
        {"name": "Grass01", "type": [dict(type=G1, variety=0, mesh="/Game/Grass_A", materials=["/Game/M1"], density=100.0),
                                     dict(type=G1, variety=1, mesh="/Game/Grass_A", materials=["/Game/M2", ""], density=100.0)]},
        {"name": "Snow", "type": [dict(type=SNOW, variety=0, mesh="/Game/Grass_A", materials=["/Game/M1"], density=50.0)]}]})
asked, logged = [], []
saved = (land_scatter.shown_group, scatter_ops.shown_group, scatter_sources.ensure_source, scatter_ops.update_masks,
         logbook.write)
land_scatter.shown_group = scatter_ops.shown_group = lambda context=None: ("Skip", skip_pieces)


def ensure(mesh, materials, done):
    asked.append(scatter_sources.key_of(mesh, materials))
    done(blade)


scatter_sources.ensure_source = ensure
logbook.write = lambda text, *a, **k: logged.append(str(text))


class Op:
    def report(self, kinds, text):
        logged.append(text)


from fpmp_baseline.operator.fortnite_editor.ui.rect import Rect  # noqa: E402
from fpmp_baseline.operator.fortnite_editor.ui.theme import Theme  # noqa: E402


class FakeUi:
    """kit.Ui's calls the list and info columns make, recorded (no GPU headless): hits by id, texts drawn."""

    def __init__(self):
        self.t, self.compact, self.hits, self.groups, self.texts = Theme(1.0), False, {}, [], []

    def hot(self, id):
        return False

    @property
    def bar_room(self):
        return 0 if self.compact else 4

    def scroll(self, id, r, h):
        return 0.0

    def end_scroll(self):
        pass

    def icon(self, *a, **k):
        return True

    def group(self, id, r, text, open_, action, select=None, tip=""):
        self.groups.append(id)
        self.hits[id] = action
        if select is not None:
            self.hits[id + ":name"] = select
        self.texts.append(text)

    def row(self, id, r, text, icon=None, selected=False, action=None, **k):
        self.hits[id] = action

    def button(self, id, r, text="", icon=None, action=None, **k):
        self.hits[id] = action
        self.texts.append(text)

    def run(self, id):
        self.hits[id](bpy.context)


class stub_paint:
    """land_scatter's paint calls recorded as texts (blf and gpu need a window)."""

    def __enter__(self):
        self.mods = [m for m in (land_scatter, land_scatter_info, sys.modules.get(land_scatter.__name__.rsplit(".", 1)[0] + ".land_select"))
                     if m is not None]
        self.saved = [m.paint for m in self.mods]
        texts = []

        class P:
            def __getattr__(self, name):
                if name == "measure":
                    return lambda s, size: 6.0 * len(str(s))
                if name in ("fit", "wrap"):
                    return lambda s, *a: [s] if name == "wrap" else s
                return lambda s=None, *a, **k: texts.append(s) if isinstance(s, str) else None
        for m in self.mods:
            m.paint = P()
        self.texts = texts
        return self

    def __exit__(self, *a):
        for m, p in zip(self.mods, self.saved):
            m.paint = p


try:
    land_scatter.invalidate()
    scatter_ops.FP_OT_scatter_game_grass.execute(Op(), bpy.context)
    land_scatter.invalidate()
    game = [e for e in land_scatter.rows() if e["kind"] == "game"]
    check("empty output skipped: no layer, no mesh asked", sorted(e["weight"]["attribute"] for e in game) == ["Grass01", "Grass01"]
          and len(asked) == 2, ([e["label"] for e in game], asked))
    check("skipped outputs kept on the pieces", all(json.loads(o[scatter_ops.SKIPPED]) == ["Snow"] for o in skip_pieces))
    check("the status log says how many were skipped", any("skipped 1 empty grass output (Snow)" in t for t in logged), logged)
    check("same mesh, two materials: two meshes asked", len(set(asked)) == 2, asked)
    labels = [e["label"] for e in game]
    check("labels numbered within the type", labels == ["Grass_A", "Grass_A 2"], labels)
    before = {m.name for o in skip_pieces for m in o.modifiers}

    def painted(pieces, names=None):       # Snow painted east of x = 110
        for o in pieces:
            co = np.empty(len(o.data.vertices) * 3, np.float32)
            o.data.vertices.foreach_get("co", co)
            o.data.attributes[scatter.GRASS + "Snow"].data.foreach_set(
                "value", (co.reshape(-1, 3)[:, 0] + o.location.x >= 110.0).astype(np.float32))
        return None
    scatter_ops.update_masks = painted
    selected_before = land_scatter.SCATTER["selected"]
    scatter_ops.FP_OT_scatter_masks.execute(Op(), bpy.context)
    land_scatter.invalidate()
    game = [e for e in land_scatter.rows() if e["kind"] == "game"]
    snow = [e for e in game if e["weight"]["attribute"] == "Snow"]
    check("Update Grass Masks adds the painted output", len(snow) == 1 and len(asked) == 3, ([e["label"] for e in game], asked))
    check("its label numbered within its own type", snow and snow[0]["label"] == "Grass_A", snow and snow[0]["label"])
    check("existing layers left alone", before <= {m.name for o in skip_pieces for m in o.modifiers} and
          len(game) == 3, sorted({m.name for o in skip_pieces for m in o.modifiers}))
    check("the pick stays", land_scatter.SCATTER["selected"] == selected_before)
    check("nothing skipped any more", all(json.loads(o[scatter_ops.SKIPPED]) == [] for o in skip_pieces))
    scatter_ops.FP_OT_scatter_masks.execute(Op(), bpy.context)
    land_scatter.invalidate()
    check("a second update adds nothing", len([e for e in land_scatter.rows() if e["kind"] == "game"]) == 3 and len(asked) == 3)
    # a folded type's row: its layers' total
    bpy.context.view_layer.update()
    land_scatter.SCATTER["counts"] = land_scatter.count_all(land_scatter.rows())
    land_scatter.SCATTER["open"]["type:" + G1] = False
    rows_ = land_scatter.list_rows()
    group_row = next(p for k, p in rows_ if k == "group" and p[0] == "type:" + G1)
    total = sum(land_scatter.SCATTER["counts"][e["key"]] for e in game if e["type"] == G1)
    check("a folded type shows its total", group_row[2] == land_scatter.amount(total) and total > 0, (group_row, total))
    open_row = next(p for k, p in rows_ if k == "group" and p[0] == "type:" + SNOW)
    check("an open type shows none (its layers do)", open_row[2] is None, open_row)
    check("no layer row under the folded type", not any(k == "layer" and p[0]["type"] == G1 for k, p in rows_))
    # every type folded (or the only one): the headers stay drawn and unfold, not the empty list's note
    land_scatter.SCATTER["open"]["type:" + SNOW] = False
    ui = FakeUi()
    with stub_paint() as drawn:
        land_scatter.side(ui, Rect(0, 0, 220, 600))
    check("all types folded: their headers still drawn", {"scatter:g:type:" + G1, "scatter:g:type:" + SNOW} <= set(ui.groups),
          (ui.groups, drawn.texts[:3]))
    check("and no empty-list note", not any("Nothing scattered" in s for s in drawn.texts), drawn.texts[:3])
    ui.run("scatter:g:type:" + G1)
    check("a folded header unfolds", land_scatter.SCATTER["open"]["type:" + G1] is True)
    # an icons-only (compact) list draws no titles: its groups stay open
    land_scatter.SCATTER["open"]["type:" + G1] = False
    compact = [k for k, _ in land_scatter.list_rows(compact=True)]
    check("compact list: folded types open", compact.count("layer") == 3, compact)
    # drawn compact (the real kit): rows centred in the column, no scroll bar while the mouse is elsewhere
    from fpmp_baseline.operator.fortnite_editor.ui import frame as ui_frame, kit, paint as kpaint, router, shell
    kui = kit.Ui(Theme(1.0), ui_frame.Frame(), router.Store(), 0.0)
    kui.compact = True
    drawn_rects = []
    kept = {n: getattr(kpaint, n) for n in ("rect", "line", "text_in", "push_clip", "pop_clip")}
    kpaint.rect = lambda box, fill, *a, **k: drawn_rects.append((box.copy(), tuple(fill)))
    kpaint.line = kpaint.text_in = kpaint.push_clip = kpaint.pop_clip = lambda *a, **k: None
    kept_glyph, kit.glyph = kit.glyph, lambda name: None       # no GPU headless
    column = Rect(10, 0, shell.ICON_COLUMN, 60)
    try:
        land_scatter.side(kui, column.copy())
    finally:
        for n, fn in kept.items():
            setattr(kpaint, n, fn)
        kit.glyph = kept_glyph
    rows_hit = [h.rect for h in kui.frame.hits if h.id.startswith("scatter:layer:")]
    margins = {(round(h.x - column.x, 2), round(column.right - h.right, 2)) for h in rows_hit}
    check("compact scatter rows centred in the column", rows_hit and all(abs(a - b) <= 1 for a, b in margins), margins)
    check("compact: no eye buttons", not any(h.id.startswith("scatter:show:") for h in kui.frame.hits))
    bar = [b for b, f in drawn_rects if f == kui.t.text3[:3] + (0.35,)]
    check("compact scatter list: no scroll bar while the mouse is elsewhere", not bar, bar)
    land_scatter.SCATTER["open"].clear()
finally:
    (land_scatter.shown_group, scatter_ops.shown_group, scatter_sources.ensure_source, scatter_ops.update_masks,
     logbook.write) = saved
    land_scatter.invalidate()

# the import queue: answers wait for the timer, one import per step
landed = []
saved_landed = scatter_sources._landed
scatter_sources._landed = lambda *a: landed.append(a[0])
try:
    for k in "abc":
        scatter_sources._arrive(k, k, {})
    check("answers queued, not imported in the pump", landed == [] and bpy.app.timers.is_registered(scatter_sources._step))
    steps = [scatter_sources._step() for _ in range(3)]
    check("one import per timer step", landed == list("abc") and steps[:2] == [scatter_sources.STEP] * 2 and steps[2] is None,
          (landed, steps))
finally:
    scatter_sources._landed = saved_landed
    if bpy.app.timers.is_registered(scatter_sources._step):
        bpy.app.timers.unregister(scatter_sources._step)
check("trailing keep-the-slot entries share a key", scatter_sources.key_of("/m", ["/a", ""]) == scatter_sources.key_of("/m", ["/a"]))

# exports asked a few ahead of the imports; identical (mesh, materials) share one
requests, got = [], []


class Engine:
    def request(self, route, done, timeout=600, **query):
        requests.append((query["mesh"], done))


saved_client, saved_landed = scatter_sources.client, scatter_sources._landed
scatter_sources.client = lambda: Engine()
scatter_sources._landed = lambda key, label, result: [d(None) for d in scatter_sources._waiting.pop(key, [])]
try:
    for k in range(5):
        scatter_sources.ensure_source("/Game/Ahead_%d" % k, [], lambda obj, k=k: got.append(k))
    scatter_sources.ensure_source("/Game/Ahead_0", [""], lambda obj: got.append("again"))
    check("exports asked %d ahead" % scatter_sources.AHEAD, [m for m, _ in requests] == ["/Game/Ahead_0", "/Game/Ahead_1"],
          [m for m, _ in requests])
    requests[0][1]({"Exports": []})
    scatter_sources._step()
    check("an import asks the next export", [m for m, _ in requests][2:] == ["/Game/Ahead_2"] and got == [0, "again"],
          ([m for m, _ in requests], got))
    while len(got) < 6 and requests:
        mesh, done = requests.pop(0)
        if mesh != "/Game/Ahead_0":
            done({"Exports": []})
            scatter_sources._step()
    check("every export asked once, all answered", sorted(got, key=str) == [0, 1, 2, 3, 4, "again"] and
          not scatter_sources._waiting and not scatter_sources._asks and scatter_sources._open[0] == 0,
          (got, scatter_sources._open))
finally:
    scatter_sources.client, scatter_sources._landed = saved_client, saved_landed
    if bpy.app.timers.is_registered(scatter_sources._step):
        bpy.app.timers.unregister(scatter_sources._step)

# placement stores MPGrass on a landscape
land = bpy.data.objects.new("Imported", bpy.data.meshes.new("Imported"))
scene.collection.objects.link(land)
placement.after_import({"MPLayerNames": {}, "MPGrass": {"outputs": [{"name": "Snow"}]}}, land, None, 0.01)
check("placement keeps MPGrass", json.loads(land.get("mp_grass") or "{}").get("outputs") == [{"name": "Snow"}])

# ground colour (land_ground): the section's rows, Auto update off by default, a layer's and placed foliage's Ground
# rows calling through, undo after Bake putting the image back with the state, the Auto update timer after a stroke
from fpmp_baseline.material_porter import ground as ground_core, instances, rvt  # noqa: E402
from fpmp_baseline.operator.fortnite_editor import land_ground, land_ops  # noqa: E402

land_ground.register()
bpy.utils.register_class(land_ops.FP_OT_land_bake)
saved_group = land_scatter.shown_group
land_scatter.shown_group = lambda context=None: ("Map", pieces)
land_scatter.invalidate()


def writer(name, x0, size=20.0, color=(0.6, 0.3, 0.1, 1.0)):
    """A plane writing its "paint" colour to the RVT (as a landscape material's AOV)."""
    me = bpy.data.meshes.new(name)
    me.from_pydata([(x0, 0, 0), (x0 + size, 0, 0), (x0 + size, size, 0), (x0, size, 0)], [], [(0, 1, 2, 3)])
    me.color_attributes.new("paint", 'FLOAT_COLOR', 'POINT').data.foreach_set("color", list(color) * 4)
    mat = bpy.data.materials.new(name)
    attr = mat.node_tree.nodes.new("ShaderNodeAttribute")
    attr.attribute_name = "paint"
    aov = mat.node_tree.nodes.new("ShaderNodeOutputAOV")
    aov.aov_name = rvt.AOV
    mat.node_tree.links.new(attr.outputs["Color"], aov.inputs["Color"])
    me.materials.append(mat)
    o = bpy.data.objects.new(name, me)
    scene.collection.objects.link(o)
    return o


def image_size():
    im = bpy.data.images.get(rvt.NAME)
    return tuple(im.size) if im is not None else None


def pixel(i=0):
    im = bpy.data.images.get(rvt.NAME)
    return tuple(round(v, 3) for v in im.pixels[i * 4:i * 4 + 4]) if im is not None else None


try:
    found = dict((k, p) for k, p in land_ground.colour_rows(scene))
    check("ground colour rows", list(found) == ["title", "status", "bake", "auto"], list(found))
    check("not baked yet", found.get("status") == "Not baked", found.get("status"))
    check("auto update off by default", land_store.auto_update(scene) is False and found.get("auto") is False)

    e = next(e for e in land_scatter.entries(pieces, scene) if e["label"] == label)
    land_scatter.pick(e["key"])

    def ground_rows(e):
        values = scatter._values(land_scatter.modifiers(e)[0][1]) if e["kind"] != "placed" else {}
        rows_ = land_scatter_info.info_rows(e, values, False) if e["kind"] != "placed" else land_ground.info_rows(e)
        return [p for k, s, p in rows_ if s == "Ground" and k == "value"]
    check("a layer's Ground row reads Game", ground_rows(e) == [("Ground", "Game")], ground_rows(e))
    scene.fp_ground_mode = "Off"
    check("Off calls scatter.set_ground on every piece",
          all(scatter.ground_of(bpy.data.objects[n], lid)["mode"] == "Off" for n, lid, _ in e["members"]),
          [scatter.ground_of(bpy.data.objects[n], lid) for n, lid, _ in e["members"]])
    scene.fp_ground_mode = "On"
    scene.fp_ground_strength = 0.4
    check("On shows Style and Strength", [p[0] for p in ground_rows(e)] == ["Ground", "Style", "Strength"] and
          ground_rows(e)[2][1] == "0.40", ground_rows(e))
    pl = next(x for x in land_scatter.entries(pieces, scene) if x["kind"] == "placed")
    land_scatter.pick(pl["key"])
    scene.fp_ground_mode = "On"
    scene.fp_ground_style = ground_core.STYLES[1]
    check("placed foliage through instances.set_ground", instances.ground_of(bpy.data.objects[pl["object"]]) ==
          {"mode": "On", "style": ground_core.STYLES[1], "strength": 1.0}, instances.ground_of(bpy.data.objects[pl["object"]]))
    check("placed foliage's Ground rows", [p[0] for p in ground_rows(pl)] == ["Ground", "Style", "Strength"], ground_rows(pl))

    # hand-placed foliage: the object panel's controls, shown for foliage materials only
    bpy.ops.mesh.primitive_plane_add(size=1.0)
    bush = bpy.context.object
    check("not foliage without a Ground Reference", not land_ground.is_foliage(bush))
    root = bpy.data.node_groups.new("Root", 'ShaderNodeTree')
    root.interface.new_socket(ground_core.REFERENCE, in_out='INPUT', socket_type='NodeSocketFloat')
    leaf = bpy.data.materials.new("Leaf")
    leaf.node_tree.nodes.new("ShaderNodeGroup").node_tree = root
    bush.data.materials.append(leaf)
    check("foliage with one", land_ground.is_foliage(bush))
    bush.fp_ground_mode = "On"
    bush.fp_ground_strength = 0.7
    check("object controls call ground.set_object", ground_core.get_object(bush) ==
          {"mode": "On", "style": ground_core.STYLES[0], "strength": 0.7}, ground_core.get_object(bush))

    # Review Focus 4: undo after Bake puts the image back with the state
    writer("Ground_W", 0.0)
    bpy.ops.ed.undo_push(message="Setup")
    r = bpy.ops.fp.land_bake()
    bpy.ops.ed.undo_push(message="Bake Ground Colour")
    first, first_size, first_px = rvt.baked(), image_size(), pixel()
    found = dict(land_ground.colour_rows(scene))
    check("baked: the status line", found["status"].startswith("Baked %d×%d px, 1 object, " % first_size), found["status"])
    writer("Ground_E", 40.0, color=(0.1, 0.5, 0.9, 1.0))
    bpy.data.objects["Ground_W"].data.color_attributes["paint"].data.foreach_set("color", [0.1, 0.8, 0.2, 1.0] * 4)
    bpy.data.objects["Ground_W"].data.update()      # repainted: the second bake's pixels differ
    bpy.ops.ed.undo_push(message="Add terrain")
    bpy.ops.fp.land_bake()
    bpy.ops.ed.undo_push(message="Bake Ground Colour")
    second = rvt.baked()
    check("the second bake is bigger, its pixels other", image_size() != first_size and
          second["bounds"] != first["bounds"] and pixel() != first_px, (first_size, image_size(), pixel()))
    bpy.ops.ed.undo()
    scene = bpy.context.scene
    check("undo: the state back", rvt.baked() == first, (rvt.baked(), first))
    check("undo: the image size back with it", image_size() == first_size, (image_size(), first_size))
    check("undo: its pixels too", pixel() == first_px, (pixel(), first_px))
    bpy.ops.ed.redo()
    scene = bpy.context.scene
    check("redo: state and image of the second bake", rvt.baked() == second and image_size() == tuple(second["size"]),
          (rvt.baked(), image_size()))
    nothing = bpy.data.scenes.new("Empty")
    with bpy.context.temp_override(scene=nothing):
        r = bpy.ops.fp.land_bake()
    check("nothing to bake: cancelled, no state", r == {'CANCELLED'} and not rvt.baked(nothing), r)

    # Auto update: a stroke's end arms the re-bake when on, not when off; strokes within the delay join
    bounds = rvt.baked(scene)["bounds"]
    land_store.set_auto_update(scene, False)
    check("off: no timer after a stroke", not land_ground.after_stroke(scene, (1, 1, 3, 3)) and
          not bpy.app.timers.is_registered(land_ground._fire))
    land_store.set_auto_update(scene, True)
    check("on: the timer armed", land_ground.after_stroke(scene, (1, 1, 3, 3)) and
          bpy.app.timers.is_registered(land_ground._fire))
    land_ground.after_stroke(scene, (50, 2, 52, 4))
    check("a second stroke re-arms, the areas joined", land_ground.PENDING["rect"] == (1, 1, 52, 4), land_ground.PENDING)
    before = rvt.baked(scene)
    land_ground._fire()
    check("the timer re-bakes the area, bounds kept", rvt.baked(scene)["bounds"] == before["bounds"] and
          land_ground.PENDING["rect"] is None)
    grid = paint_store.grid
    x0, y0, x1, y1 = land_ground.stroke_rect(grid, (2, 3, 5, 7), 1.5)
    check("a stroke's cells to metres, padded by the radius",
          abs(x0 - (grid.to_world(3, 2)[0] - 1.5)) < 1e-6 and abs(y1 - (grid.to_world(6, 4)[1] + 1.5)) < 1e-6,
          (x0, y0, x1, y1))
finally:
    land_scatter.shown_group = saved_group
    if bpy.app.timers.is_registered(land_ground._fire):
        bpy.app.timers.unregister(land_ground._fire)

# multi-select (land_select): Ctrl toggles, Shift takes the range, a type header's name takes its layers; the info
# column shows shared values ("—" where they differ); an edit lands on every picked layer as one undo step; scatter
# layers and placed foliage mix, each setting where it exists; a single pick as before
from fpmp_baseline.operator.fortnite_editor import land_select  # noqa: E402

for c in scatter_ops.classes + land_scatter.classes:
    bpy.utils.register_class(c)
land_select.register()
land_scatter.shown_group = scatter_ops.shown_group = lambda context=None: ("Map", pieces)
k1 = key
k2 = scatter_ops.add_layer_to(pieces, dict(settings, variety=1, density=200.0), blade)
k3 = scatter_ops.add_layer_to(pieces, dict(settings, variety=2, density=300.0), blade)
pk = "P:Placed"
land_scatter.invalidate()


def picks():
    return land_select.keys()


def mods_of(k):
    return land_scatter.modifiers(next(e for e in land_scatter.rows() if e["key"] == k))


def values_of(k, name):
    return [scatter._get(m, name) for _o, m in mods_of(k)]


def multi_rows():
    return {(s, p[0]): p for kind, s, p in land_select.info_rows(land_select.entries()) if kind != "group"}


try:
    check("three game layers listed", [e["key"] for e in land_scatter.rows() if e["kind"] == "game"] == [k1, k2, k3],
          [e["key"] for e in land_scatter.rows()])
    bpy.ops.fp.scatter_pick(key=k1)
    check("a plain click picks one", picks() == [k1] and land_scatter.SCATTER["selected"] == k1 and not land_select.many())
    bpy.ops.fp.scatter_pick(key=k3, extend=True)
    check("Ctrl-click adds, the clicked one leads", picks() == [k1, k3] and land_scatter.SCATTER["selected"] == k3, picks())
    bpy.ops.fp.scatter_pick(key=k1, extend=True)
    check("Ctrl-click again removes", picks() == [k3], picks())
    bpy.ops.fp.scatter_pick(key=k1)
    bpy.ops.fp.scatter_pick(key=k3, span=True)
    check("Shift-click takes the range from the last pick", picks() == [k1, k2, k3] and
          land_scatter.SCATTER["selected"] == k3, picks())
    bpy.ops.fp.scatter_pick(key=k2)
    ui = FakeUi()
    with stub_paint():
        land_scatter.side(ui, Rect(0, 0, 220, 600))
    head = "scatter:g:type:" + settings["type"]
    check("a type header: its name a hit of its own", head + ":name" in ui.hits and head in ui.hits, sorted(ui.hits))
    ui.run(head + ":name")
    check("its name picks all its layers", picks() == [k1, k2, k3], picks())
    check("the header reads N layers", land_select.title(land_select.entries()) == "3 layers")
    found = multi_rows()
    check("differing values read —", found[("Points", "Density")][1] == land_select.MIXED, found.get(("Points", "Density")))
    check("shared values read as one", found[("Points", "Seed")][1] == "0", found.get(("Points", "Seed")))
    check("a shared check reads its state", found[("Points", "Random Rotation")][2] is True, found.get(("Points", "Random Rotation")))
    scatter._set(mods_of(k2)[0][1], "Align To Surface", False)
    check("a differing check reads mixed", multi_rows()[("Points", "Align To Surface")][2] is None)
    scatter._set(mods_of(k2)[0][1], "Align To Surface", True)
    ui = FakeUi()
    with stub_paint() as drawn:
        land_select.info(ui, Rect(0, 0, 272, 800), land_select.entries())
    check("the info column draws the shared rows", "3 layers" in drawn.texts and land_select.MIXED in drawn.texts and
          "scatter:remove" in ui.hits, drawn.texts[:8])

    # an edit lands on every picked layer (each piece), one undo step takes all back
    bpy.ops.ed.undo_push(message="Before batch")
    bpy.ops.fp.scatter_set(name="Random Rotation", on=False)
    check("a check edit lands on every picked layer", all(v is False for k in (k1, k2, k3) for v in values_of(k, "Random Rotation")),
          [values_of(k, "Random Rotation") for k in (k1, k2, k3)])
    scene = bpy.context.scene
    getattr(scene, land_select.BATCH).density = 50.0
    check("a value edit (the popup's) lands on every picked layer", all(v == 50.0 for k in (k1, k2, k3)
                                                                         for v in values_of(k, "Density")))
    check("and reads as one again", multi_rows()[("Points", "Density")][1] == "50 / 100 m²", multi_rows()[("Points", "Density")])
    bpy.ops.fp.scatter_weight(weight="null")
    land_scatter.invalidate()
    check("Weight on every picked layer", all(e["weight"] is None for e in land_select.entries()))
    bpy.ops.ed.undo_push(message="Batch edit")
    bpy.ops.ed.undo()
    scene = bpy.context.scene
    pieces = [bpy.data.objects["Land_A"], bpy.data.objects["Land_B"]]
    land_scatter.invalidate()
    check("one undo takes every layer back", [values_of(k, "Density") for k in (k1, k2, k3)] == [[400.0] * 2, [200.0] * 2, [300.0] * 2]
          and all(v is True for k in (k1, k2, k3) for v in values_of(k, "Random Rotation")),
          [values_of(k, "Density") for k in (k1, k2, k3)])
    check("the pick survives the undo", picks() == [k1, k2, k3], picks())

    # scatter layers and placed foliage mixed: Ground on both kinds, layer settings on the layers only
    bpy.ops.fp.scatter_pick(key=pk, extend=True)
    check("placed foliage joins the pick", picks() == [k1, k2, k3, pk] and land_select.title(land_select.entries()) == "4 layers")
    found = multi_rows()
    check("mixed: layer settings and Ground shown", ("Points", "Density") in found and ("Ground", "Ground") in found, sorted(found))
    scene.fp_ground_mode = "Off"
    scene.fp_ground_mode = "On"
    scene.fp_ground_strength = 0.3
    land_scatter.invalidate()
    on = [scatter.ground_of(bpy.data.objects[n], lid) for k in (k1, k2, k3)
          for n, lid, _ in next(e for e in land_scatter.rows() if e["key"] == k)["members"]]
    check("Ground on every picked layer", all(g["mode"] == "On" and abs(g["strength"] - 0.3) < 1e-6 for g in on), on)
    g = instances.ground_of(bpy.data.objects["Placed"])
    check("and on the placed foliage", g["mode"] == "On" and abs(g["strength"] - 0.3) < 1e-6, g)
    check("a layer setting skips the placed foliage", bpy.ops.fp.scatter_set(name="Random Rotation", on=False) == {'FINISHED'}
          and all(v is False for k in (k1, k2, k3) for v in values_of(k, "Random Rotation")))
    bpy.ops.fp.scatter_pick(key=pk)
    found = multi_rows()
    check("placed alone: no layer settings", not any(s == "Points" for s, _ in found) and ("Ground", "Ground") in found, sorted(found))

    # a single pick as before: its own info, edits on it alone
    bpy.ops.fp.scatter_pick(key=k2)
    bpy.ops.fp.scatter_set(name="Random Rotation", on=True)
    check("a single pick's edit lands on it alone", values_of(k2, "Random Rotation") == [True, True] and
          values_of(k1, "Random Rotation") == [False, False])
    check("a single pick: no batch info", not land_select.many() and land_select.entries() == [land_scatter.selected()])

    # a value typed in the section pop-up (the modifier's own input) re-evaluates both pieces at once: no
    # update_tag, no tick between (Python writes to mod.properties.inputs tag nothing by themselves)
    from types import SimpleNamespace
    from fpmp_baseline.operator.fortnite_editor import land_popups as lp
    from fpmp_baseline.operator.fortnite_editor.ui import fields

    class RecCol:
        def __init__(self):
            self.props = {}

        def label(self, *_a, **_k):
            pass

        def prop(self, owner, name, text=None, on_change=None, **_k):
            self.props[text] = (owner, name, on_change)

    def shown(k):
        e = next(x for x in land_scatter.rows() if x["key"] == k)
        return [scatter.count(bpy.data.objects[n], lid) for n, lid, _ in e["members"]]

    col = RecCol()
    lp.section_content("Points")(SimpleNamespace(t=SimpleNamespace(text1=(1,) * 4, text3=(1,) * 4)), col)
    before = shown(k2)
    owner, name, on_change = col.props["Density"]
    fields.write(owner, name, 50.0, on_change)(bpy.context)
    after = shown(k2)
    check("a pop-up value re-evaluates the layer at once", after[0] < before[0] * 0.5, (before, after))
    check("and its other piece, without a tick", after[1] < before[1] * 0.5 and values_of(k2, "Density") == [50.0] * 2,
          (before, after, values_of(k2, "Density")))

    # the pop-ups (land_popups, the editor's own): items, kinds, Remove's confirmation
    from fpmp_baseline.operator.fortnite_editor import land_popups
    from fpmp_baseline.operator.fortnite_editor.ui import blender as ub
    from fpmp_baseline.operator.fortnite_editor.ui.rect import Rect as R
    w = land_popups.weight_items()
    check("weight: None first, marked (unweighted)", w[0]["label"].startswith("None") and w[0]["check"], w[:1])
    check("weight: one marked", sum(1 for i in w if i.get("check")) == 1)
    b = land_popups.biome_items()
    check("biome: World first and marked", b[0]["label"].startswith("World") and b[0]["check"], b[:1])
    lay = land_popups.layer_items()
    check("layers: every row, the pick marked", len(lay) == len(land_scatter.rows()) and
          [i["id"] for i in lay if i["check"]] == [land_scatter.SCATTER["selected"]])
    opened = []
    real_open = ub.open
    ub.open = lambda context, p: opened.append(p) or p
    try:
        cell = R(1000, 400, 260, 22)
        land_popups.open_section(bpy.context, cell, "Points")
        land_popups.open_weight(bpy.context, cell)
        land_popups.open_biome(bpy.context, cell)
        check("section: a panel left of its row", opened[0].kind == "panel" and opened[0].side == "left"
              and opened[0].anchor is cell)
        check("weight: a list, biome: a menu", opened[1].kind == "list" and opened[2].kind == "menu")
        check("no legacy scatter pop-ups", not any(hasattr(bpy.types, n) for n in (
            "FP_PT_scatter_section", "FP_MT_scatter_weight", "FP_MT_scatter_biome", "FP_MT_scatter_layers")))

        # remove: every picked layer, one confirmation (ours)
        bpy.ops.fp.scatter_pick(key=k3, extend=True)
        opened.clear()
        land_popups.confirm_remove(bpy.context)       # what fp.scatter_remove's invoke opens (background: no invoke)
        p = opened[0] if opened else None
        check("remove asks first: a modal dialog", p is not None and p.kind == "dialog" and p.modal
              and p.title == "Remove 2 layers?", p and p.title)
        check("nothing removed before OK", k3 in [e["key"] for e in land_scatter.rows()])
        p.on_accept(bpy.context)
    finally:
        ub.open = real_open
    left = [e["key"] for e in land_scatter.rows()]
    check("remove takes every picked layer", k2 not in left and k3 not in left and k1 in left, left)
finally:
    land_scatter.shown_group = scatter_ops.shown_group = saved_group

# "Grass of this material" (land_grass_list): the material's outputs, a type and its meshes under each, marks for
# scattered and empty, picking an output or a variety adds it weighted by its mask; a custom terrain's from the app
from fpmp_baseline.operator.fortnite_editor import land_grass_list as lg, state as editor_state  # noqa: E402
from fpmp_baseline.operator.fortnite_editor.ui import blender as ub  # noqa: E402

LGT_G, LGT_S, LGT_V = "/Game/LGT_ListGrass", "/Game/LGT_ListSnow", "/Game/LGT_ListGravel"
HERA = "/Hera/MI_Fixture.MI_Fixture"
blade = bpy.data.objects["Blade"]
group = bpy.data.collections.new("GrassList")
bpy.context.scene.collection.children.link(group)
scene = bpy.context.scene
list_pieces = [plane("List_A", 300.0, collection=group), plane("List_B", 320.0, collection=group)]


def set_mask(o, name, value):
    a = o.data.attributes.get(scatter.GRASS + name) or o.data.attributes.new(scatter.GRASS + name, 'FLOAT', 'POINT')
    a.data.foreach_set("value", np.full(len(o.data.vertices), value, np.float32))


def fixture_grass():
    grass = dict(type=LGT_G, name="LGT_ListGrass", materials=[], density=20.0)
    return {"material": "/Hera/M_Fixture", "outputs": [
        {"name": "Grass01", "type": [dict(grass, variety=0, mesh="/Game/SM_Leafy"), dict(grass, variety=1, mesh="/Game/SM_Long"),
                                     dict(grass, variety=2, mesh="/Game/SM_Unused", density=0.0)]},
        {"name": "Snow", "type": [dict(type=LGT_S, name="LGT_ListSnow", variety=0, mesh="/Game/SM_Snowy", materials=[], density=5.0)]},
        {"name": "Gravel", "type": [dict(type=LGT_V, name="LGT_ListGravel", variety=0, mesh="/Game/SM_Stones", materials=[], density=5.0)]},
        {"name": "Bare", "type": []}]}


for o in list_pieces:
    set_mask(o, "Grass01", 1.0)
    set_mask(o, "Snow", 0.0)      # Gravel's and Bare's: not read yet
    o["mp_grass"] = json.dumps(fixture_grass())
shown = {"pieces": list_pieces}
land_scatter.shown_group = scatter_ops.shown_group = lambda context=None: ("GrassList", shown["pieces"])
kept = (scatter_sources.ensure_source, scatter_ops.update_masks, logbook.write, ub.open, editor_state.client)
scatter_sources.ensure_source = lambda mesh, materials, done: done(blade)
mask_calls, logged = [], []


def fake_masks(pieces_, names=None):
    mask_calls.append(list(names or []))
    for o in pieces_:
        for n in names or []:
            set_mask(o, n, 1.0)


scatter_ops.update_masks = fake_masks
logbook.write = lambda text, *a, **k: logged.append(str(text))


def rows_by_id(query=""):
    return {it.get("id"): it for it in lg.items(bpy.context, query) if "id" in it}


try:
    land_scatter.invalidate()
    lg.STATE["folds"].clear()
    lg.refresh(list_pieces)
    found = lg.items(bpy.context)
    outs = [it["label"] for it in found if it.get("depth") == 0]
    check("grass list: a row per output, in the material's order", outs == ["Grass01", "Snow", "Gravel", "Bare"], outs)
    check("grass list: outputs start folded", not any(it.get("depth") for it in found), [it.get("label") for it in found])
    mat = bpy.data.materials.new("MI_ListFixture")
    mat["mp_path"] = HERA
    list_pieces[0].data.materials.append(mat)
    check("grass list: the landscape material and its master named", lg.heading(list_pieces) == ("MI_Fixture", "M_Fixture"),
          lg.heading(list_pieces))
    second = bpy.data.materials.new("MI_ListFixture_B")
    second["mp_path"] = "/Hera/MI_Fixture_B.MI_Fixture_B"
    list_pieces[1].data.materials.append(second)
    check("grass list: several instances named by their master", lg.heading(list_pieces) == ("M_Fixture", "2 instances"),
          lg.heading(list_pieces))
    list_pieces[1].data.materials.clear()
    by = rows_by_id()
    check("grass list: Snow's mask all 0 reads empty", "empty" in (by.get("o:Snow") or {}).get("detail", ""), by.get("o:Snow"))
    check("grass list: an empty output stays pickable", (by.get("o:Snow") or {}).get("action") is not None)
    check("grass list: a mask not read yet isn't empty", "empty" not in (by.get("o:Gravel") or {}).get("detail", ""), by.get("o:Gravel"))
    check("grass list: painted output unmarked", by.get("o:Grass01") and not by["o:Grass01"].get("check") and not by["o:Grass01"].get("detail"),
          by.get("o:Grass01"))
    check("grass list: an output without a type can't be picked", by.get("o:Bare") and by["o:Bare"].get("action") is None, by.get("o:Bare"))

    fold = (by.get("o:Grass01") or {}).get("fold")
    if fold:
        fold[1](bpy.context)
    labelled = [(it["label"], it.get("depth")) for it in lg.items(bpy.context) if "label" in it][:5]
    check("grass list: an open output shows its type, then its meshes",
          labelled == [("Grass01", 0), ("LGT_ListGrass", 1), ("SM_Leafy", 2), ("SM_Long", 2), ("SM_Unused", 2)], labelled)
    by = rows_by_id()
    check("grass list: the type's row isn't a pick", by.get("t:Grass01") and by["t:Grass01"].get("action") is None, by.get("t:Grass01"))
    unused = by.get("v:Grass01:2") or {}
    check("grass list: a variety placing nothing shown, not pickable", unused.get("action") is None and "density 0" in unused.get("detail", ""), unused)
    check("grass list: a variety's density shown", "20" in (by.get("v:Grass01:0") or {}).get("detail", ""), by.get("v:Grass01:0"))
    searched = [it["label"] for it in lg.items(bpy.context, "stones") if "label" in it]
    check("grass list: search reaches a mesh in a folded output", searched == ["Gravel", "LGT_ListGravel", "SM_Stones"], searched)
    searched = [it["label"] for it in lg.items(bpy.context, "grass01") if "label" in it]
    check("grass list: search on an output keeps its meshes", "SM_Leafy" in searched and "Snow" not in searched, searched)

    # picking: an output adds its usable varieties weighted by its mask, a variety just that one
    bpy.ops.fp.scatter_output(output="Grass01")
    land_scatter.invalidate()
    game = [e for e in land_scatter.rows() if e["kind"] == "game"]
    check("scatter one output: a layer per usable variety", [e["variety"] for e in game] == [0, 1], [(e["label"], e["variety"]) for e in game])
    check("scatter one output: weighted by its mask", all(e["weight"] == {"attribute": "Grass01"} for e in game), [e["weight"] for e in game])
    check("scatter one output: its mask already read, not asked", mask_calls == [], mask_calls)
    mod = land_scatter.modifiers(game[0])[0][1]
    check("scatter one output: the modifier reads the mask", scatter._values(mod).get("Source Attribute") == scatter.GRASS + "Grass01",
          scatter._values(mod).get("Source Attribute"))
    by = rows_by_id()
    check("grass list: scattered output marked", (by.get("o:Grass01") or {}).get("check") is True, by.get("o:Grass01"))
    check("grass list: scattered output no action", (by.get("o:Grass01") or {}).get("action") is None)
    check("grass list: scattered variety marked, no action", (by.get("v:Grass01:0") or {}).get("check") is True and by["v:Grass01:0"].get("action") is None)
    count = len(game)
    r = bpy.ops.fp.scatter_output(output="Grass01")
    land_scatter.invalidate()
    check("scatter one output: again adds nothing", r == {'CANCELLED'} and len([e for e in land_scatter.rows() if e["kind"] == "game"]) == count, r)

    bpy.ops.fp.scatter_output(output="Gravel")
    land_scatter.invalidate()
    check("scatter one output: its missing mask read first", mask_calls == [["Gravel"]], mask_calls)
    for o in list_pieces:
        o["mp_grass"] = json.dumps(dict(fixture_grass(), outputs=fixture_grass()["outputs"] + [
            {"name": "Mixed", "type": [dict(type="/Game/LGT_Mixed", variety=k, mesh="/Game/SM_Mix%d" % k, materials=[], density=5.0)
                                       for k in range(3)]}]))
        set_mask(o, "Mixed", 1.0)
    bpy.ops.fp.scatter_output(output="Mixed", variety=1)
    land_scatter.invalidate()
    mixed = [e for e in land_scatter.rows() if e["type"] == "/Game/LGT_Mixed"]
    check("scatter one variety: that one only, weighted", [(e["variety"], e["weight"]) for e in mixed] == [(1, {"attribute": "Mixed"})], mixed)
    by = rows_by_id()
    check("grass list: partly scattered reads how many", "1 of 3" in (by.get("o:Mixed") or {}).get("detail", ""), by.get("o:Mixed"))
    check("grass list: partly scattered still pickable", (by.get("o:Mixed") or {}).get("action") is not None)
    bpy.ops.fp.scatter_output(output="Mixed")
    land_scatter.invalidate()
    mixed = [e for e in land_scatter.rows() if e["type"] == "/Game/LGT_Mixed"]
    check("picking it adds the rest", sorted(e["variety"] for e in mixed) == [0, 1, 2], [e["variety"] for e in mixed])
    land_scatter.SCATTER["pending"]["LGT_ListSnow"] = 1
    by = rows_by_id()
    check("grass list: an output importing can't be picked twice", (by.get("o:Snow") or {}).get("action") is None and
          "importing" in by["o:Snow"].get("detail", ""), by.get("o:Snow"))
    land_scatter.SCATTER["pending"].clear()

    # the masks of a piece read per timer step: only what's missing, marks after the last
    from fpmp_baseline.material_porter import grass_masks  # noqa: E402
    written = []
    real_write = grass_masks.write

    def fake_write(obj, outputs=None, material=None, app=None):
        written.append((obj.name, [o["name"] for o in outputs]))
        for o_ in outputs:
            set_mask(obj, o_["name"], 0.0)
        return {"written": [], "unknown": {"Fancy": 2}}
    grass_masks.write = fake_write
    try:
        set_mask(list_pieces[1], "Gravel", 1.0)
        for o in list_pieces:
            a = o.data.attributes.get(scatter.GRASS + "Gravel")
            if a is not None:
                o.data.attributes.remove(a)
        lg.STATE["open"] = True
        lg.start_masks(list_pieces)
        check("masks: a piece per step", len(lg.STATE["todo"]) == 2 and lg.STATE["total"] == 2, lg.STATE["todo"])
        check("masks: progress shown", any("Reading grass masks" in it.get("header", "") for it in lg.items(bpy.context)), lg.items(bpy.context)[:1])
        lg.step()
        check("masks: only the missing outputs of one piece", written == [("List_A", ["Gravel"])], written)
        check("masks: not marked until the last piece", "empty" not in rows_by_id()["o:Gravel"].get("detail", ""))
        again = lg.step()
        check("masks: done after the last", again is None and lg.STATE["todo"] == [] and len(written) == 2, written)
        check("masks: marks refreshed", "empty" in rows_by_id()["o:Gravel"].get("detail", ""), rows_by_id()["o:Gravel"])
        check("masks: unknown expressions logged once", sum("Fancy" in t for t in logged) == 1, logged[-3:])
        for o in list_pieces:
            o.data.attributes.remove(o.data.attributes[scatter.GRASS + "Gravel"])

        def broken(obj, outputs=None, material=None, app=None):
            raise ValueError("no graph")
        grass_masks.write = broken
        lg.start_masks(list_pieces)
        lg.step()
        check("masks: a failure logged, the rest dropped", lg.STATE["todo"] == [] and any("no graph" in t for t in logged), logged[-2:])
    finally:
        grass_masks.write = real_write
        lg.STATE["open"] = False

    # the pop-up: a search list left of its button, stays open while picking
    opened = []
    ub.open = lambda context, p: opened.append(p) or p
    from fpmp_baseline.operator.fortnite_editor.ui.rect import Rect as R  # noqa: E402
    cell = R(1000, 100, 120, 26)
    lg.open_list(bpy.context, cell)
    p = opened[0] if opened else None
    check("pop-up: a search list beside its button, kept open", p is not None and p.kind == "list" and p.side == "left" and
          p.keep_open and p.query is not None and p.anchor is cell, p)
    check("pop-up: open while shown", lg.STATE["open"] is True)
    if p is not None:
        p.on_close()
    check("pop-up: closing stops the masks", lg.STATE["open"] is False and lg.STATE["todo"] == [])
    if p is not None:           # drawn with the real kit: a row's hit, its chevron's, folding from it
        kui = kit.Ui(Theme(1.0), ui_frame.Frame(), router.Store(), 0.0)
        kept_draw = {n: getattr(kpaint, n) for n in ("rect", "line", "text_in", "push_clip", "pop_clip")}
        kpaint.rect = kpaint.line = kpaint.text_in = kpaint.push_clip = kpaint.pop_clip = lambda *a, **k: None
        kept_glyph, kit.glyph = kit.glyph, lambda name: None
        try:
            lg.STATE["folds"]["Snow"] = False
            p.draw(kui, R(0, 0, 380, 440), p)
        finally:
            for n, fn in kept_draw.items():
                setattr(kpaint, n, fn)
            kit.glyph = kept_glyph
        ids = {h.id: h for h in kui.frame.hits}
        check("drawn: a row per output", all("scatter_grass:o:" + n in ids for n in ("Grass01", "Snow", "Gravel", "Bare")), sorted(ids))
        chevron, row = ids.get("scatter_grass:o:Snow:fold"), ids.get("scatter_grass:o:Snow")
        check("drawn: the chevron a hit of its own, over the row's left end", chevron is not None and row is not None and
              chevron.rect.x == row.rect.x and chevron.rect.right < row.rect.x + 80 and
              kui.frame.hits.index(chevron) > kui.frame.hits.index(row))
        check("drawn: no chevron without a type", "scatter_grass:o:Bare:fold" not in ids)
        if chevron is not None:
            chevron.action(bpy.context)
        check("drawn: the chevron unfolds", lg.STATE["folds"].get("Snow") is True, lg.STATE["folds"])
    lg.STATE["folds"].clear()

    # a custom terrain: no MPGrass on it, the app's answer for its material's mp_path
    plot = plane("Custom_List", 400.0, collection=group)
    custom_mat = bpy.data.materials.new("MI_Custom_Fixture")
    custom_mat["mp_path"] = HERA
    plot.data.materials.append(custom_mat)
    set_mask(plot, "Grass01", 1.0)
    shown["pieces"] = [plot]
    land_scatter.invalidate()
    asks = []

    class Engine:
        state = "ready"

        def request(self, route, done, timeout=600, **query):
            asks.append((route, query, done))

    editor_state.client = lambda: Engine()
    lg.ANSWERS.clear()
    lg.STATE["asked"].clear()
    check("custom terrain: nothing until the app answers", land_scatter.grass_payload([plot]) is None)
    reading = [it.get("header", "") for it in lg.items(bpy.context)]
    check("custom terrain: the list says it's reading", any("Reading" in h for h in reading), reading)
    lg.ask([plot])
    lg.ask([plot])
    check("custom terrain: asks the app once, with the material's mp_path",
          [(r_, q) for r_, q, _d in asks] == [("foliage-material-grass", {"material": HERA})], asks)
    if asks:
        asks[0][2](fixture_grass())
    outs = [it["label"] for it in lg.items(bpy.context) if it.get("depth") == 0]
    check("custom terrain: gets the list", outs == ["Grass01", "Snow", "Gravel", "Bare"], outs)
    check("custom terrain: Add Game Grass and output_for see it", land_scatter.output_for([plot], LGT_S) == "Snow")
    check("custom terrain: its heading", lg.heading([plot]) == ("MI_Fixture", "M_Fixture"), lg.heading([plot]))
    actions = [b[0] for row_ in land_scatter_info._actions([plot]) for b in row_]
    check("the button beside Add Game Grass", "scatter:grass" in actions and "scatter:game" in actions, actions)
    bpy.ops.fp.scatter_output(output="Grass01", variety=0)
    land_scatter.invalidate()
    game = [e for e in land_scatter.rows() if e["kind"] == "game"]
    check("custom terrain: a pick adds a layer weighted by its mask", [e["weight"] for e in game] == [{"attribute": "Grass01"}], game)
    check("custom terrain: the answer isn't stored on the mesh", plot.get("mp_grass") is None)
    other = bpy.data.materials.new("MI_NoGrass")
    other["mp_path"] = "/Game/MI_NoGrass.MI_NoGrass"
    plot.data.materials[0] = other
    land_scatter.invalidate()
    asks.clear()
    lg.ask([plot])
    check("custom terrain: another material, asked again", len(asks) == 1 and asks[0][1] == {"material": other["mp_path"]}, asks)
    if asks:
        asks[0][2]({"material": "/Game/M_NoGrass", "outputs": []})
    notes = [it.get("header", "") for it in lg.items(bpy.context)]
    check("custom terrain: a material without grass says so", any("no grass outputs" in h for h in notes), notes)
    third = bpy.data.materials.new("MI_Gone")
    third["mp_path"] = "/Game/MI_Gone.MI_Gone"
    plot.data.materials[0] = third
    asks.clear()
    lg.ask([plot])
    if asks:
        asks[0][2](RuntimeError("no material"))
    notes = [it.get("header", "") for it in lg.items(bpy.context)]
    check("custom terrain: a failed answer says why", any("no material" in h for h in notes), notes)
    plot.data.materials.clear()
    check("no game material: no button", "scatter:grass" not in [b[0] for row_ in land_scatter_info._actions([plot]) for b in row_])
    lg.ANSWERS.clear()
finally:
    land_scatter.shown_group = scatter_ops.shown_group = saved_group
    scatter_sources.ensure_source, scatter_ops.update_masks, logbook.write, ub.open, editor_state.client = kept
    land_scatter.invalidate()

print("[editor_scatter_check] %d passed, %d failed%s" % (PASSES[0], len(FAILS), (": " + ", ".join(FAILS)) if FAILS else ""))
sys.exit(1 if FAILS else 0)
