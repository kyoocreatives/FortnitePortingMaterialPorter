"""The header pop-ups of the Files, Map, Rig and Landscape tabs, headless: each one's items (folders with Up, maps
grouped with the open one marked and a loading row, map images, characters, paint layers and biomes, landscapes), what
picking runs, each opener giving our pop-up (list / menu) under its header button, OPENERS registered and gone again,
and the legacy menus gone.

    blender -b --factory-startup --python-exit-code 1 -P editor_header_popups_check.py -- <plugin parent>
"""
import sys
from types import SimpleNamespace

import bpy

sys.path.insert(0, sys.argv[sys.argv.index("--") + 1])
import fpmp_baseline  # noqa: E402,F401
from fpmp_baseline.operator.fortnite_editor import files_tab, land_tab, maps_page, rig_tab  # noqa: E402
from fpmp_baseline.operator.fortnite_editor.ui import blender as ub, popups  # noqa: E402
from fpmp_baseline.operator.fortnite_editor.ui.rect import Rect  # noqa: E402

FAILS, PASSES = [], [0]


def check(name, ok, detail=""):
    if ok:
        PASSES[0] += 1
    else:
        FAILS.append(name)
        print("[editor_header_popups_check] FAIL %s %s" % (name, detail))


def labels(items):
    return [it.get("label", it.get("header", "-")) for it in items]


def marked(items):
    return [it["label"] for it in items if it.get("check")]


MODULES = (files_tab, maps_page, rig_tab, land_tab)
for m in MODULES:
    m.register()
wm = bpy.context.window_manager

# --- Files: Up + the folder's subfolders; a pick opens the folder
went = []
real_go = files_tab._go
files_tab._go = lambda path: (lambda context: went.append(path))
try:
    listing = {"path": "FortniteGame/Content", "folders": [{"name": "Athena", "path": "FortniteGame/Content/Athena"},
                                                          {"name": "Characters", "path": "FortniteGame/Content/Characters"}]}
    items = files_tab.folder_items("FortniteGame/Content", listing)
    check("folders: Up, a line, the subfolders", labels(items) == ["Up", "-", "Athena", "Characters"], labels(items))
    check("folders: Up names where it goes", items[0].get("detail") == "FortniteGame", items[0].get("detail"))
    items[0]["action"](None)
    items[3]["action"](None)
    check("folders: Up goes up, a row opens its folder",
          went == ["FortniteGame", "FortniteGame/Content/Characters"], went)
    check("folders: the root has no Up", labels(files_tab.folder_items("FortniteGame", {"folders": []})) == [])
    loading = files_tab.folder_items("FortniteGame/Content", None)
    check("folders: still listing: Up and a loading row, not pickable",
          labels(loading)[-1].startswith("Loading") and loading[-1].get("action") is None, labels(loading))
    check("folders: the search finds a subfolder", labels(popups.matches(items, "char")) == ["Characters"])
finally:
    files_tab._go = real_go

# --- Maps: grouped as the list column, the open one marked, a loading row while the list comes
opened = []
real_op = maps_page._op
maps_page._op = lambda idname, **props: (lambda context: opened.append((idname, props)))
try:
    maps = [{"id": "a", "name": "Hera", "kind": "br", "minimap": True}, {"id": "b", "name": "Feat", "kind": "feature",
                                                                      "minimap": False},
            {"id": "c", "name": "Helios", "kind": "br", "minimap": False}]
    items = maps_page.map_items({"maps": maps}, "c")
    check("maps: grouped by kind, in the list column's order",
          labels(items) == ["Battle Royale", "Hera", "Helios", "Game Features", "Feat"], labels(items))
    check("maps: the open map marked", marked(items) == ["Helios"], marked(items))
    check("maps: a map without a minimap has the globe", items[1]["icon"] == "mdi_Map" and items[2]["icon"] == "mdi_Earth")
    items[1]["action"](None)
    check("maps: a pick opens the map", opened == [("map_open", {"id": "a"})], opened)
    check("maps: the search keeps a match's group", labels(popups.matches(items, "fea")) == ["Game Features", "Feat"])
    loading = maps_page.map_items({"maps": maps[:1], "loading": True}, "")
    check("maps: listing: a loading row first, not pickable",
          loading[0]["label"].startswith("Loading maps") and loading[0].get("action") is None and "Hera" in labels(loading),
          labels(loading))
    check("maps: no list yet: the loading row alone", labels(maps_page.map_items(None, "")) == ["Loading maps (0)..."])
    opened.clear()
    images = maps_page.image_items(True)
    check("map images: the four kinds", labels(images) == ["Map", "Height", "Normal", "Weights"], labels(images))
    images[1]["action"](None)
    check("map images: a pick brings that kind in", opened == [("map_images", {"kind": "height"})], opened)
    check("map images: no map open: greyed with a reason",
          all(not it["enabled"] and it["tip"] for it in maps_page.image_items(False)))
finally:
    maps_page._op = real_op

# --- Rig: the characters the operator's enum gives, the shown one marked; a pick runs fp.rig_use
used = []
real_use, real_rigged, real_arm = rig_tab._use, rig_tab.rigged, rig_tab.armature
a, b = (bpy.data.objects.new(n, bpy.data.armatures.new(n)) for n in ("Jonesy", "Peely"))
rig_tab._use = lambda name: (lambda context: used.append(name))
rig_tab.rigged = lambda context: [a, b]
rig_tab.armature = lambda context: b
try:
    items = rig_tab.character_items(bpy.context)
    check("characters: one row each", labels(items) == ["Jonesy", "Peely"], labels(items))
    check("characters: the shown one marked", marked(items) == ["Peely"], marked(items))
    items[0]["action"](None)
    check("characters: a pick shows it", used == ["Jonesy"], used)
    rig_tab.rigged = lambda context: []
    rig_tab.armature = lambda context: None
    none = rig_tab.character_items(bpy.context)
    check("characters: none rigged: one row saying so, not pickable",
          labels(none) == ["No rigged character"] and none[0].get("action") is None, labels(none))
finally:
    rig_tab._use, rig_tab.rigged, rig_tab.armature = real_use, real_rigged, real_arm

# --- Landscape: paint layers and biomes under headers, the painted one marked; the scene's landscapes
ran = []
real_run = land_tab._run
land_tab._run = lambda op, name, **props: (lambda context: ran.append((op, name, props)))
saved = {k: land_tab.LAND.get(k) for k in ("layers", "biomes", "store", "group")}
try:
    layers = SimpleNamespace(grid=SimpleNamespace(layers=["Grass", "Rock"]))
    biomes = SimpleNamespace(grid=SimpleNamespace(layers=["Forest"]))
    land_tab.LAND.update(layers=layers, biomes=biomes, store=layers)
    items = land_tab.layer_items("Rock")
    check("layers: Layers then Biomes, headed", labels(items) == ["Layers", "Grass", "Rock", "Biomes", "Forest"],
          labels(items))
    check("layers: the painted one marked", marked(items) == ["Rock"], marked(items))
    land_tab.LAND["store"] = biomes
    check("layers: a biome painted: it is the marked one", marked(land_tab.layer_items("Rock")) == [],
          marked(land_tab.layer_items("Rock")))
    check("layers: a biome of the same name marked", marked(land_tab.layer_items("Forest")) == ["Forest"])
    items[2]["action"](None)
    items[4]["action"](None)
    check("layers: a pick paints it (biome flagged)",
          ran == [("land_layer", "Rock", {"biome": False}), ("land_layer", "Forest", {"biome": True})], ran)
    land_tab.LAND["biomes"] = None
    check("layers: no biomes: no Biomes header", labels(land_tab.layer_items("")) == ["Layers", "Grass", "Rock"])
    ran.clear()
    land_tab.LAND["group"] = "South"
    items = land_tab.group_items({"North": [1, 2], "South": [1]})
    check("landscapes: sorted, piece counts", labels(items) == ["North", "South"]
          and [it["detail"] for it in items] == ["2 pieces", "1 piece"], [(it["label"], it.get("detail")) for it in items])
    check("landscapes: the shown one marked", marked(items) == ["South"])
    items[0]["action"](None)
    check("landscapes: a pick shows it", ran == [("land_group", "North", {})], ran)
    none = land_tab.group_items({})
    check("landscapes: none: one row saying so", labels(none) == ["No landscape"] and none[0].get("action") is None)
finally:
    land_tab._run = real_run
    land_tab.LAND.update(saved)

# --- the openers: under the header button, our kinds
NAMES = {"files:folders": "list", "map:list": "list", "map:images": "menu", "rig:characters": "list",
         "land:layers": "list", "land:groups": "list"}
check("every opener registered", all(n in ub.OPENERS for n in NAMES), sorted(ub.OPENERS))
check("each opener has its tooltip", all((ub.OPENERS[n].__doc__ or "").strip() for n in NAMES if n in ub.OPENERS))
got = []
real_open = ub.open
ub.open = lambda context, p: got.append(p) or p
try:
    ctx = SimpleNamespace(preferences=bpy.context.preferences, window_manager=wm, scene=bpy.context.scene)
    anchor = Rect(100, 600, 16, 0)
    for name, kind in NAMES.items():
        got.clear()
        if name in ub.OPENERS:
            ub.OPENERS[name](ctx, anchor, "below")
        p = got[0] if got else None
        check("%s: a %s under the button" % (name, kind), p is not None and p.kind == kind and p.anchor is anchor
              and p.side == "below" and p.query is not None, p and (p.kind, p.side))
finally:
    ub.open = real_open

# --- legacy gone, the operators other code runs kept
for legacy in ("FP_MT_files_folders", "FP_MT_map_list", "FP_MT_map_images", "FP_MT_land_layers"):
    check("no " + legacy, not hasattr(bpy.types, legacy))
for m in reversed(MODULES):
    m.unregister()
check("unregistered: the openers gone", not any(n in ub.OPENERS for n in NAMES), sorted(ub.OPENERS))

print("[editor_header_popups_check] %d passed, %d failed" % (PASSES[0], len(FAILS)))
if FAILS:
    sys.exit(1)
