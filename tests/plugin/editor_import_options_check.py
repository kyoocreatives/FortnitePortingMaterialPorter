"""The editor's import options, headless: the settings= query they make, a batch keeping the ones set when it
started, the Files and Maps exports, Reset, the Import button's text, the texture cap in the material shape key, and
the Export Options read again after a file load.

    python plugin_test.py tests/plugin/editor_import_options_check.py --name import_options
"""
import sys

import bpy

sys.path.insert(0, sys.argv[sys.argv.index("--") + 1])
from fpmp_baseline.operator.fortnite_editor import import_options, importing, maps_tab, settings_tab  # noqa: E402
from fpmp_baseline.operator.fortnite_editor import state as st  # noqa: E402
from fpmp_baseline.material_porter import build  # noqa: E402

FAILS, PASSES = [], [0]


def check(name, ok, detail=""):
    if ok:
        PASSES[0] += 1
    else:
        FAILS.append(name)
        print("[editor_import_options_check] FAIL %s %s" % (name, detail))


SIZES = ["Full", "Size4096", "Size2048", "Size1024", "Size512"]
FIELDS = [
    {"name": "TargetLOD", "label": "Level of Detail", "section": "Mesh", "type": "int", "value": 0, "min": 0, "max": 7},
    {"name": "RigType", "label": "Rig", "section": "Armature", "type": "enum", "value": "Default",
     "choices": ["Default", "Tasty", "Official"]},
    {"name": "MaxTextureSize", "label": "Max Texture Size", "section": "Texture", "type": "enum", "value": "Full", "choices": SIZES},
    {"name": "MapTextureSize", "label": "Map Texture Size", "section": "Texture", "type": "enum", "value": "Size1024", "choices": SIZES},
    {"name": "PreferFPShaders", "label": "Prefer FP Shaders", "section": "Material", "type": "bool", "value": False},
    {"name": "ExportMaterials", "label": "Export", "section": "Material", "type": "bool", "value": True},
]
settings_tab.register()
settings_tab._build(FIELDS)
settings_tab._sync(FIELDS)
import_options.build(FIELDS)
wm = bpy.context.window_manager
o = wm.fp_import_options

check("none set: no settings", importing.import_options.query() == "" and import_options.import_label("Import") == "Import")
o.use_TargetLOD = True
check("ticking starts from the Export Options' value", o.TargetLOD == 0, o.TargetLOD)
o.TargetLOD = 2
o.use_RigType, o.RigType = True, "Tasty"
o.use_MaxTextureSize, o.MaxTextureSize = True, "Size1024"
o.use_ExportMaterials, o.ExportMaterials = True, False
q = import_options.query()
check("query", q == "TargetLOD:2;RigType:Tasty;MaxTextureSize:Size1024;MapTextureSize:Size1024;ExportMaterials:false", q)
label = import_options.import_label("Import 2")
check("the Import button names them", label == "Import 2  ·  LOD 2, Tasty Rig, Textures 1024, No Materials", label)
check("a map reads LOD, texture size, materials", import_options.query(wm, "map") ==
      "TargetLOD:2;MaxTextureSize:Size1024;MapTextureSize:Size1024;ExportMaterials:false", import_options.query(wm, "map"))
check("Export Options untouched", wm.fp_export_settings.TargetLOD == 0 and wm.fp_export_settings.RigType == "Default")


# --- a batch keeps the options set when it started
class Engine:
    def __init__(self):
        self.calls = []

    def request(self, route, done, **query):
        self.calls.append((route, query, done))


engine = st.STATE["engine"] = Engine()
st.STATE["busy"], st.STATE["queue"] = None, []
items = [{"name": n, "path": "/Game/" + n} for n in ("Alpha", "Bravo")]
importing.start_all([("Outfit", i, {}) for i in items])
o.TargetLOD = 5     # changed while the batch runs
engine.calls[0][2](RuntimeError("boom"))
sent = [c[1]["settings"] for c in engine.calls]
check("both of a batch with the options it started with", len(sent) == 2 and all(s == q for s in sent), sent)
engine.calls[1][2](RuntimeError("done"))
check("nothing to undo after a failed export", importing._step == [], importing._step)

# --- Files
from fpmp_baseline.operator.fortnite_editor import files_tab  # noqa: E402
st.STATE["busy"] = None
files_tab._import(["/Game/Props/SM_Thing.SM_Thing"])
check("files-export carries them", engine.calls[-1][0] == "files-export" and "TargetLOD:5" in engine.calls[-1][1]["settings"],
      engine.calls[-1][1])
engine.calls[-1][2](RuntimeError("done"))

# --- Maps: the export's options snapshot
wm_ns = type("WM", (), {"fp_map_flags": {"Actors"}, "fp_map_filter": "", "fp_map_main": False, "fp_map_tiles": False,
                        "fp_import_options": o})()
opts = maps_tab.export_options(wm_ns)
check("a map export snapshots them", opts["settings"].startswith("TargetLOD:5;MaxTextureSize:Size1024"), opts)
st.STATE["engine"] = None

# --- Reset
bpy.ops.fp.import_options_reset()
check("Reset: none set", import_options.query() == "" and not o.use_TargetLOD)

# --- the texture cap gets a material shape of its own (build_like shares the images it doesn't swap)
entry = {"graph": "g", "master": "/Game/M", "switches": {}, "name": "x"}
full = build.shape_key(entry)
check("no cap: the shape key as before", full == build.shape_key(dict(entry, cap=0)))
check("a cap: another shape", full != build.shape_key(dict(entry, cap=1024)) != build.shape_key(dict(entry, cap=512)))

# --- the Import Options panel: a row per option (override, value), greyed rows showing the Export Options' value
from types import SimpleNamespace  # noqa: E402
from fpmp_baseline.operator.fortnite_editor.ui import blender as ub  # noqa: E402
from fpmp_baseline.operator.fortnite_editor.ui.rect import Rect  # noqa: E402

o.use_RigType, o.RigType = True, "Official"
rows = import_options.rows(wm, "assets")
check("assets: every option", [r[0] for r in rows] == [n for n, _ in import_options.OPTIONS], [r[0] for r in rows])
rig = next(r for r in rows if r[0] == "RigType")
check("set: its own value", rig[2] is True and rig[3] == o and rig[4] == "RigType", rig)
lod = next(r for r in rows if r[0] == "TargetLOD")
check("not set: the Export Options' value", lod[2] is False and lod[3] == wm.fp_export_settings and lod[4] == "TargetLOD", lod)
check("the override's label", lod[1] == "Override Level of Detail", lod[1])
rows = import_options.rows(wm, "map")
check("map: LOD, texture size, materials", [r[0] for r in rows] == list(import_options.MAP_OPTIONS))
tex = next(r for r in rows if r[0] == "MaxTextureSize")
check("map: the Map Texture Size", tex[1] == "Override Map Texture Size" and tex[4] == "MapTextureSize", tex)
o.use_RigType = False

opened = []
real_open = ub.open
ub.open = lambda context, p: opened.append(p) or p
ctx = SimpleNamespace(window_manager=wm, preferences=bpy.context.preferences)
try:
    anchor = Rect(400, 60, 30, 30)
    import_options.open_options(ctx, anchor, "map")
    check("the options' button opens a panel by it", opened and opened[-1].kind == "panel" and opened[-1].anchor is anchor)
    check("opened from the map: its rows", import_options.OPENED[0] == "map")
    settings_tab.open_settings(ctx, anchor)
    p = opened[-1]
    check("Export Options: a panel under its button", p.kind == "panel" and p.anchor is anchor and p.side == "below"
          and abs(p.size[0] - 360 * bpy.context.preferences.system.ui_scale) < 1, (p.kind, p.side, p.size))
finally:
    ub.open = real_open
import_options.OPENED[0] = "assets"
check("Export Options by section, in the engine's order", settings_tab.sections(FIELDS) == [
    ("Mesh", ["TargetLOD"]), ("Armature", ["RigType"]), ("Texture", ["MaxTextureSize", "MapTextureSize"]),
    ("Material", ["PreferFPShaders", "ExportMaterials"])], settings_tab.sections(FIELDS))
check("header buttons open it", ub.OPENERS.get("settings") is settings_tab.open_settings)
check("no legacy Export Options popover", not hasattr(bpy.types, "FP_PT_fortnite_settings"))
check("no legacy Import Options popover", not hasattr(bpy.types, "FP_PT_fortnite_import_options"))

# --- a file load wipes the window manager's values: nothing shown or saved until the app's are read again
REAL = [dict(f) for f in FIELDS]
next(f for f in REAL if f["name"] == "RigType")["value"] = "Official"
next(f for f in REAL if f["name"] == "PreferFPShaders")["value"] = True


class SettingsEngine:
    state = "ready"

    def __init__(self):
        self.sets, self.fail = [], False

    def request(self, route, done, **query):
        if route == "settings-blender":
            done([dict(f) for f in REAL])
        else:
            self.sets.append((query["name"], query["value"]))
            done(RuntimeError("no engine") if self.fail else {})


engine = st.STATE["engine"] = SettingsEngine()
st.STATE["settings"] = None
settings_tab.ask()
got = bpy.context.window_manager.fp_export_settings
check("read: the app's values", got.RigType == "Official" and got.PreferFPShaders and st.settings_read(), got.RigType)

bpy.ops.wm.read_homefile(use_factory_startup=True)
got = bpy.context.window_manager.fp_export_settings
print("[editor_import_options_check] after load: RigType=%s, read=%s" % (got.RigType, st.settings_read()))
check("a load: values not shown as read", not st.settings_read())
got.RigType = "Tasty"
check("a load: an edit before the read isn't saved", engine.sets == [], engine.sets)
settings_tab.ask()
got = bpy.context.window_manager.fp_export_settings
check("a load: read again", st.settings_read() and got.RigType == "Official" and got.PreferFPShaders, got.RigType)
check("a load: reading saves nothing", engine.sets == [], engine.sets)

got.ExportMaterials = False
check("read: an edit is saved", engine.sets == [("ExportMaterials", "false")], engine.sets)
engine.fail = True
got.TargetLOD = 3
check("an edit the app refused: read again", not st.settings_read())
engine.fail = False
settings_tab.ask()
check("an edit the app refused: its value back", bpy.context.window_manager.fp_export_settings.TargetLOD == 0)
st.STATE["engine"] = None

import_options.unregister()
print("[editor_import_options_check] %d passed, %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
