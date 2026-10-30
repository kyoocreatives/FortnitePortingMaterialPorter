"""The editor's choices kept across Blender sessions and files (the window manager's properties are reset by both):
the Assets tab's category, sort, tile size, names and filters, the Files tab's type and folder, the Landscape brush and mode,
the map export's options, the Rig tab's panes, the columns' dragged widths and the category groups' folds. Searches,
picks and scroll positions stay per session. Saved in the editor's prefs file when they change."""
import os
import time

import bpy
from bpy.app.handlers import persistent

from . import cosmetics_page
from .state import prefs, save_prefs
from .ui import blender as ui_blender

KEEP = ("fp_browse_type", "fp_browse_sort", "fp_browse_tile", "fp_browse_names", "fp_browse_filters",
        "fp_files_kind", "fp_files_folder", "fp_land_radius", "fp_land_strength", "fp_land_falloff",
        "fp_land_layer_only", "fp_land_mode", "fp_map_flags", "fp_map_main", "fp_rig_panes", "fp_rig_mirror", "fp_rig_face_part")
CHECK = 1.0     # s between looks for a change
MEM = {"applied": False, "saved": None, "looked": 0.0}


def snapshot(wm):
    """{key: value} of what's kept, JSON-ready."""
    out = {}
    for name in KEEP:
        if hasattr(wm, name):
            value = getattr(wm, name)
            out[name] = sorted(value) if isinstance(value, set) else value
    out["widths"] = {"%s|%s" % key: w for key, w in ui_blender.DEFAULT_WIDTHS.items()}
    out["folds"] = dict(cosmetics_page.OPEN)
    return out


def apply(wm, data):
    """Puts kept values back; a value the property no longer takes (an enum item gone) is skipped."""
    for name in KEEP:
        if name in data and hasattr(wm, name):
            value = data[name]
            try:
                setattr(wm, name, set(value) if isinstance(getattr(wm, name), set) else value)
            except (TypeError, ValueError, AttributeError):
                pass
    for key, w in (data.get("widths") or {}).items():
        tab, _, column = key.partition("|")
        if column in ("side", "info") and isinstance(w, (int, float)):
            ui_blender.DEFAULT_WIDTHS[(tab or None, column)] = float(w)
    cosmetics_page.OPEN.update({k: bool(v) for k, v in (data.get("folds") or {}).items()})


def tick():
    """The timer's: puts the kept values back once (and after each file load), then saves them when they change."""
    if os.environ.get("FP_EDITOR_FRESH"):       # test runs: every one starts from the defaults
        return
    wm = bpy.context.window_manager
    if not MEM["applied"]:
        apply(wm, prefs().get("ui") or {})
        MEM["applied"] = True
        MEM["saved"] = snapshot(wm)
        return
    if time.time() - MEM["looked"] < CHECK:
        return
    MEM["looked"] = time.time()
    now = snapshot(wm)
    if now != MEM["saved"]:
        MEM["saved"] = now
        save_prefs(ui=now)


@persistent
def _loaded(*_):
    MEM["applied"] = False      # a file load brings its own window manager: its properties are the defaults again


def register():
    if _loaded not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_loaded)


def unregister():
    if _loaded in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_loaded)
