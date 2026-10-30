"""The Landscape sidebar panel (operator/landscape_paint.py): the parent and its sub-panels Layers / Biome Maps /
Features / Material, their polls per object kind (and without a material), the Features filter and rows editing the
material as the switch operator always did, mask rows with Fill and Clear, the Follow toggle as a property (also with
the landscape unselected or a mesh of its set excluded; the selection given back, a failed rebuild undone; its operator
on a library override), and JSON custom properties parsed once per value instead of per redraw. Draws go through a
recording stand-in layout, so no window is needed.

    python plugin_test.py tests/plugin/sidebar_land_check.py --blend out/fpdev/mat_onto/mat_onto.blend
"""
import sys

import bpy

sys.path.insert(0, sys.argv[sys.argv.index("--") + 1])
import fpmp_baseline  # noqa: E402

fpmp_baseline.register()
from fpmp_baseline.material_porter import biome_colours, biome_maps, weightmaps  # noqa: E402
from fpmp_baseline.operator import landscape_paint as lp  # noqa: E402

FAILS, PASSES = [], [0]


def check(name, ok, detail=""):
    if ok:
        PASSES[0] += 1
    else:
        FAILS.append(name)
    print("[sidebar_land_check] %s %s %s" % ("PASS" if ok else "FAIL", name, detail))


PANELS = ("FPMP_PT_landscape_layers", "FPMP_PT_landscape_paint", "FPMP_PT_landscape_biomes",
          "FPMP_PT_landscape_features", "FPMP_PT_landscape_material")
types = {p: getattr(bpy.types, p, None) for p in PANELS}
check("the five panels register", all(types.values()), [p for p, t in types.items() if t is None])
if not all(types.values()):
    print("[sidebar_land_check] %d passed, %d failed" % (PASSES[0], len(FAILS)))
    sys.exit(1)

parent = types["FPMP_PT_landscape_layers"]
check("parent: Landscape, bl_order 1", (parent.bl_label, parent.bl_order) == ("Landscape", 1), (parent.bl_label, parent.bl_order))
subs = PANELS[1:]
check("sub-panels hang under the parent", all(types[p].bl_parent_id == PANELS[0] for p in subs),
      [types[p].bl_parent_id for p in subs])
check("sub-panel labels", [types[p].bl_label for p in subs] == ["Layers", "Biome Maps", "Features", "Material"],
      [types[p].bl_label for p in subs])
check("every panel sets bl_order", all("bl_order" in dir(types[p]) and isinstance(types[p].bl_order, int) for p in PANELS))
check("Features and Material start closed",
      all('DEFAULT_CLOSED' in getattr(types[p], "bl_options", set()) for p in ("FPMP_PT_landscape_features", "FPMP_PT_landscape_material")))

land = bpy.data.objects["Plane"]
mat = land.active_material
plain = bpy.data.objects.new("PlainMesh", bpy.data.meshes.new("PlainMesh"))
bpy.context.scene.collection.objects.link(plain)
vl = bpy.context.view_layer


def polls(obj):
    vl.objects.active = obj
    return [bool(types[p].poll(bpy.context)) for p in PANELS]


class Rec:
    """A stand-in UILayout recording what a panel draws: (where, call, args, kwargs, returned) in `log`; layout panels
    tag what their header and body hold."""

    def __init__(self, log, where="body"):
        self._log, self._where = log, where

    def __getattr__(self, name):
        def call(*args, **kw):
            if name == "panel":
                return Rec(self._log, "header:" + args[0]), Rec(self._log, "body:" + args[0])
            out = Rec(self._log, self._where)
            self._log.append((self._where, name, args, kw, out))
            return out
        return call


def drawn(cls):
    """What a panel's draw lays out, in the current context."""
    log = []
    fake = type("Drawn", (), {k: v for k, v in vars(cls).items() if not k.startswith("__")})()
    fake.layout = Rec(log)
    cls.draw(fake, bpy.context)
    return log


def ops(log, idname):
    return [e for e in log if e[1] == "operator" and e[2][0] == idname]


check("polls on a landscape mesh", polls(land) == [True] * 5, polls(land))
check("polls on a plain mesh", polls(plain) == [False] * 5, polls(plain))
check("polls with no active object", polls(None) == [False] * 5, polls(None))
# a landscape whose material reads no biome maps: no Biomes panel
other = land.copy()
other.data = land.data.copy()
bpy.context.scene.collection.objects.link(other)
bare = mat.copy()
del bare["mp_map_textures"]
other.material_slots[0].material = bare
check("Biomes poll needs a material reading biome maps", polls(other) == [True, True, False, True, True], polls(other))
# an empty material slot: no Features panel (its draw read the material), no switch operator
other.material_slots[0].material = None
check("Features poll needs a material", polls(other)[3] is False, polls(other))
check("the switch operator needs one too", not bpy.ops.fpmp.landscape_switch.poll())
bpy.data.objects.remove(other)
vl.objects.active = land

# Features filter: a window-manager string above the switch list, case-insensitive substring
wm_prop = bpy.types.WindowManager.bl_rna.properties.get("fpmp_feature_filter")
check("filter is a WindowManager string", wm_prop is not None and wm_prop.type == 'STRING')
wm = bpy.context.window_manager
log = drawn(lp.FPMP_PT_LandscapeFeatures)
first = log[0] if log else None
check("filter row drawn first, VIEWZOOM", first is not None and first[1] == "prop" and first[2][1] == "fpmp_feature_filter"
      and first[3].get("icon") == 'VIEWZOOM', first and first[:4])
check("no filter: every switch", len(ops(log, "fpmp.landscape_switch")) == 44, len(ops(log, "fpmp.landscape_switch")))
wm.fpmp_feature_filter = "aSPh"
shown = [e[3]["text"] for e in ops(drawn(lp.FPMP_PT_LandscapeFeatures), "fpmp.landscape_switch")]
check("filter keeps matching switches only", shown and all("asph" in s.lower() for s in shown) and "UseAsphalt" in shown, shown)
check("features() filters the same way", [n for n, _ in lp.features(mat, "aSPh")] == shown)
wm.fpmp_feature_filter = ""
check("Update Material still drawn", bool(ops(log, "fpmp.update_landscape_material")))

# Fill reads as fill, not as an unticked checkbox
icons = bpy.types.UILayout.bl_rna.functions["label"].parameters["icon"].enum_items.keys()
check("GP_DRAW_FILL exists", "GP_DRAW_FILL" in icons)
log = drawn(lp.FPMP_PT_LandscapePaint)
fills = ops(log, "fpmp.fill_landscape_layer")
check("layer Fill / Clear icons", fills and {e[3]["icon"] for e in fills} == {"GP_DRAW_FILL", "X"}, {e[3].get("icon") for e in fills})

# Features: the rows show the switches as built, edited ones as edited; the operator records edits as before
rows = lp.features(mat)
check("a row per built switch", len(rows) == 44 and dict(rows)["UseAsphalt"] is True, len(rows))
r = bpy.ops.fpmp.landscape_switch(name="UseAsphalt", on=False)
check("switching a feature off records the edit", r == {'FINISHED'} and dict(mat.get(lp.EDITS)) == {"UseAsphalt": 0},
      dict(mat.get(lp.EDITS) or {}))
check("the row shows the edit", dict(lp.features(mat))["UseAsphalt"] is False)
bpy.ops.fpmp.landscape_switch(name="UseAsphalt", on=True)
check("switching it back drops the edit", dict(mat.get(lp.EDITS) or {}) == {}, dict(mat.get(lp.EDITS) or {}))
check("the row is back as built", dict(lp.features(mat))["UseAsphalt"] is True)

# parsing cached per value: same object while the property is unchanged, fresh when it changes
check("switches parsed once", lp.switches(mat) is lp.switches(mat))
check("biome maps parsed once", biome_maps.maps(mat) is biome_maps.maps(mat) and len(biome_maps.maps(mat)) == 2)
saved = mat["mp_switches"]
mat["mp_switches"] = '{"Only": true}'
check("a changed property parses again", lp.switches(mat) == {"Only": True})
mat["mp_switches"] = saved
check("no material: no switches, no maps", lp.switches(None) == {} and biome_maps.maps(None) == {})

# biome_colours.pair takes the maps it is given instead of parsing them again
found = biome_maps.maps(mat)
meshes = [o for o in bpy.data.objects if o.type == 'MESH' and o.get("mp_landscape")]
for param, info in found.items():
    biome_maps.create(meshes, param, info)
real = biome_maps.maps
biome_maps.maps = lambda m: (_ for _ in ()).throw(AssertionError("parsed again"))
try:
    which = biome_colours.pair(land, found)
    check("pair uses the maps passed in", which == ("BiomeTexture", "BiomeGrassColorTextureArray"), which)
except AssertionError as e:
    check("pair uses the maps passed in", False, e)
finally:
    biome_maps.maps = real

# Biome Maps: mask rows like layer rows (Paint | Fill | Clear), Clear All in the Masks header, unlabelled masks under Other
mask_param = which[0]
log = drawn(lp.FPMP_PT_LandscapeBiomes)
masks = [e for e in ops(log, "fpmp.fill_biome") if e[4].channel >= 0]
paints = [e for e in ops(log, "fpmp.paint_biome") if e[4].channel >= 0]
check("a Fill and a Clear per mask row", masks and len(masks) == 2 * len(paints)
      and sorted({(e[3]["icon"], e[4].value) for e in masks}) == [("GP_DRAW_FILL", 1.0), ("X", 0.0)],
      sorted({(e[3].get("icon"), getattr(e[4], "value", None)) for e in masks}))
clear_all = [e for e in ops(log, "fpmp.fill_biome") if e[4].channel < 0]
check("Clear All in the Masks header", len(clear_all) == 1 and clear_all[0][0].startswith("header:fpmp_biome_masks_"),
      [e[0] for e in clear_all])
labels = [e[3].get("text") for e in log if e[1] == "label"]
check("no SNAP_FACE anywhere", not any(e[3].get("icon") == "SNAP_FACE" for e in log + drawn(lp.FPMP_PT_LandscapePaint)))
named = biome_maps.maps(mat)[mask_param].get("labels") or {}
if named and len(named) < 4 * len(biome_maps.maps(mat)[mask_param]["rects"]):
    check("unlabelled masks under Other", "Other" in labels and "Unnamed" not in labels, labels)

# Clear one mask: that channel of that rectangle only
img = bpy.data.images[dict(land[biome_maps.KEY])[mask_param]]
bpy.ops.fpmp.fill_biome(param=mask_param, index=0, channel=1)
size = img.size[0]
px = weightmaps._pixels(img).reshape(size, size, 4)
xs, ys = biome_maps._pixels_in(biome_maps.maps(mat)[mask_param]["rects"][1], size)
px[ys, xs, 2] = 1.0
weightmaps._store(img, px.reshape(-1, 4))
r = bpy.ops.fpmp.fill_biome(param=mask_param, index=0, channel=1, value=0.0)
px = weightmaps._pixels(img).reshape(size, size, 4)
x0, y0 = biome_maps._pixels_in(biome_maps.maps(mat)[mask_param]["rects"][0], size)
check("Clear empties that mask", r == {'FINISHED'} and float(px[y0, x0, 1].max()) == 0.0, float(px[y0, x0, 1].max()))
check("Clear leaves the other masks", float(px[ys, xs, 2].min()) == 1.0, float(px[ys, xs, 2].min()))

# Follow: a property reading and writing obj["mp_follow_biomes"] through the operator's own function
check("Follow property on objects", hasattr(land, "fpmp_follow_biomes"))
for o in meshes:
    o.select_set(True)
vl.objects.active = land
check("Follow reads the default (on)", land.fpmp_follow_biomes is True)
land.fpmp_follow_biomes = False
check("Follow off writes every mesh of the set", [bool(o.get(biome_colours.FOLLOW, True)) for o in meshes] == [False] * len(meshes),
      [o.get(biome_colours.FOLLOW) for o in meshes])
check("Follow reads it back", land.fpmp_follow_biomes is False)
bpy.ops.fpmp.follow_biomes()
check("the operator still toggles the same state", land.fpmp_follow_biomes is True and land[biome_colours.FOLLOW] is not None)
check("weightmaps intact", bool(weightmaps.images(land)))
check("Follow not overridable (an override draws the operator)",
      not bpy.types.Object.bl_rna.properties["fpmp_follow_biomes"].is_library_editable)

# Follow with the landscape active but not selected: it rebuilds the set, then gives the selection back
for o in bpy.context.selected_objects:
    o.select_set(False)
plain.select_set(True)
vl.objects.active = land
land.fpmp_follow_biomes = False
check("Follow off works unselected", land.fpmp_follow_biomes is False)
back = (sorted(o.name for o in bpy.context.selected_objects), vl.objects.active)
check("the selection and active object given back", back == (["PlainMesh"], land), back)
land.fpmp_follow_biomes = True

# a mesh of the set in an excluded collection can't be selected: the rebuild leaves it out instead of raising
hidden_coll = bpy.data.collections.new("Excluded")
bpy.context.scene.collection.children.link(hidden_coll)
twin = land.copy()
hidden_coll.objects.link(twin)
vl.layer_collection.children["Excluded"].exclude = True
try:
    land.fpmp_follow_biomes = False
    got = (land.fpmp_follow_biomes, twin.get(biome_colours.FOLLOW, True))
    check("Follow skips meshes outside the view layer", got == (False, True), got)
except RuntimeError as e:
    check("Follow skips meshes outside the view layer", False, e)
land.fpmp_follow_biomes = True
bpy.data.objects.remove(twin)
bpy.data.collections.remove(hidden_coll)

# a failed rebuild (the app not running) puts Follow back instead of leaving the flag flipped
from fpmp_baseline.material_porter import hook  # noqa: E402
build_exact, hook.build_exact = hook.build_exact, (lambda *a, **k: None)
land.fpmp_follow_biomes = False
got = [bool(o.get(biome_colours.FOLLOW, True)) for o in meshes]
check("a failed rebuild leaves Follow as it was", got == [True] * len(meshes), got)
check("Follow operator cancels when the rebuild fails", bpy.ops.fpmp.follow_biomes() == {'CANCELLED'}
      and land.fpmp_follow_biomes is True)
hook.build_exact = build_exact

# Fill and Clear take a mask value in 0..1
value = bpy.ops.fpmp.fill_biome.get_rna_type().properties["value"]
check("Fill value bounded 0..1", (value.hard_min, value.hard_max) == (0.0, 1.0), (value.hard_min, value.hard_max))
plain.select_set(False)
vl.objects.active = plain
plain.fpmp_follow_biomes = False
check("Follow on a plain mesh does nothing", biome_colours.FOLLOW not in plain and plain.fpmp_follow_biomes is True)

# Follow is a property toggle on a local landscape, its operator (pressed while on) on a library override
def follow_drawn():
    log = drawn(lp.FPMP_PT_LandscapeBiomes)
    return ([e for e in log if e[1] == "prop" and e[2][1] == "fpmp_follow_biomes"],
            [e[3].get("depress") for e in ops(log, "fpmp.follow_biomes")])


vl.objects.active = land
props, pressed = follow_drawn()
check("Follow drawn as its property", len(props) == 1 and pressed == [], (len(props), pressed))
import os  # noqa: E402
import tempfile  # noqa: E402
lib = os.path.join(tempfile.mkdtemp(), "lib.blend")
coll = land.users_collection[0]
coll = coll.name if coll != bpy.context.scene.collection else None
if coll is None:            # the landscape sits in the scene's own collection: give it one to link
    holder = bpy.data.collections.new("Landscape")
    bpy.context.scene.collection.children.link(holder)
    holder.objects.link(land)
    coll = holder.name
bpy.ops.wm.save_as_mainfile(filepath=lib, copy=True)
bpy.ops.wm.read_homefile(use_empty=True)
with bpy.data.libraries.load(lib, link=True) as (_, dst):
    dst.collections = [coll]
bpy.context.scene.collection.children.link(dst.collections[0])
ov = dst.collections[0].override_hierarchy_create(bpy.context.scene, bpy.context.view_layer)
vl = bpy.context.view_layer
land = next(o for o in ov.all_objects if o.name.startswith("Plane"))
vl.objects.active = land
props, pressed = follow_drawn()
check("override: Follow drawn as its operator, pressed while on", props == [] and pressed == [land.fpmp_follow_biomes],
      (len(props), pressed))

print("[sidebar_land_check] %d passed, %d failed" % (PASSES[0], len(FAILS)))
if FAILS:
    sys.exit(1)
