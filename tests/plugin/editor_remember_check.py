"""The editor's kept choices, headless: what a snapshot holds, putting it back (an enum value that's gone skipped),
saving only on a change, and a file load putting them back again.

    blender -b --factory-startup --python-exit-code 1 -P editor_remember_check.py -- <plugin parent>
"""
import os
import sys
import tempfile

import bpy

sys.path.insert(0, sys.argv[sys.argv.index("--") + 1])
import fpmp_baseline  # noqa: E402
from fpmp_baseline.operator.fortnite_editor import cosmetics_page, remember  # noqa: E402
from fpmp_baseline.operator.fortnite_editor import state as st  # noqa: E402
from fpmp_baseline.operator.fortnite_editor.ui import blender as ub  # noqa: E402

FAILS, PASSES = [], [0]


def check(name, ok, detail=""):
    if ok:
        PASSES[0] += 1
    else:
        FAILS.append(name)
        print("[editor_remember_check] FAIL %s %s" % (name, detail))


fpmp_baseline.register()
saved = os.path.join(tempfile.mkdtemp(), "fp_editor.json")
st.prefs_path = lambda: saved       # a scratch prefs file, not the user's
wm = bpy.context.window_manager

remember.tick()     # first tick: puts back what's kept (nothing yet)
wm.fp_browse_type, wm.fp_browse_tile, wm.fp_browse_names, wm.fp_rig_panes = "Pickaxe", 160, True, 'FACE'
wm.fp_browse_search = "raider"      # a search isn't kept
ub.DEFAULT_WIDTHS[("cosmetics", "side")] = 40.0
cosmetics_page.OPEN["Creative"] = True
remember.MEM["looked"] = 0.0
remember.tick()
kept = st.prefs().get("ui") or {}
check("a change is saved", kept.get("fp_browse_type") == "Pickaxe" and kept.get("fp_browse_tile") == 160
      and kept.get("fp_browse_names") is True and kept.get("widths", {}).get("cosmetics|side") == 40.0
      and kept.get("folds", {}).get("Creative") is True, kept)
check("searches aren't kept", "fp_browse_search" not in kept)
mtime = os.path.getmtime(saved)
remember.MEM["looked"] = 0.0
remember.tick()
check("nothing changed: nothing written", os.path.getmtime(saved) == mtime)

# a new session (the defaults again), then the first tick puts everything back
wm.fp_browse_type, wm.fp_browse_tile, wm.fp_browse_names, wm.fp_rig_panes = "Outfit", 96, False, 'BOTH'
ub.DEFAULT_WIDTHS.clear()
cosmetics_page.OPEN.clear()
remember._loaded()
remember.tick()
check("put back after a file load", (wm.fp_browse_type, wm.fp_browse_tile, wm.fp_browse_names, wm.fp_rig_panes)
      == ("Pickaxe", 160, True, 'FACE') and ub.DEFAULT_WIDTHS.get(("cosmetics", "side")) == 40.0
      and cosmetics_page.OPEN.get("Creative") is True)
remember.apply(wm, {"fp_rig_panes": "GONE", "fp_browse_tile": 128})
check("a value the property no longer takes is skipped, the rest put back", wm.fp_rig_panes == 'FACE' and wm.fp_browse_tile == 128)

print("[editor_remember_check] %d passed, %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
