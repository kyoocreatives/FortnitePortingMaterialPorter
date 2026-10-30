"""The editor header's Fortnite menu, headless: our Menu (the FP workspaces, then New Window) opened by fp.popup_open
under its header button; the legacy Blender menu is gone.

    blender -b --factory-startup --python-exit-code 1 -P editor_fortnite_menu_check.py -- <plugin parent>
"""
import sys
from types import SimpleNamespace

import bpy

sys.path.insert(0, sys.argv[sys.argv.index("--") + 1])
from fpmp_baseline.operator.fortnite_editor import workspaces  # noqa: E402
from fpmp_baseline.operator.fortnite_editor.ui import blender as ub  # noqa: E402
from fpmp_baseline.operator.fortnite_editor.ui.rect import Rect  # noqa: E402

FAILS, PASSES = [], [0]


def check(name, ok, detail=""):
    if ok:
        PASSES[0] += 1
    else:
        FAILS.append(name)
        print("[editor_fortnite_menu_check] FAIL %s %s" % (name, detail))


workspaces.register()
items = workspaces.menu_items()
labels = [i.get("label") for i in items if "label" in i]
check("the workspaces, then New Window", labels == list(workspaces.LAYOUTS) + ["Open in New Window"], labels)
check("a separator before New Window", "sep" in items[len(workspaces.LAYOUTS)], items[len(workspaces.LAYOUTS)])
check("each workspace says what it is", all(i.get("tip") for i in items[:len(workspaces.LAYOUTS)]))
check("every item runs something", all(callable(i["action"]) for i in items if "label" in i))

opened = []
real_open = ub.open
ub.open = lambda context, p: opened.append(p) or p
try:
    anchor = Rect(20, 700, 16, 0)
    ub.OPENERS["fortnite"](SimpleNamespace(preferences=bpy.context.preferences), anchor, "below")
finally:
    ub.open = real_open
p = opened[0] if opened else None
check("its header button opens a menu under it", p is not None and p.kind == "menu" and p.anchor is anchor
      and p.side == "below", p and (p.kind, p.side))
check("no legacy Fortnite menu", not hasattr(bpy.types, "FP_MT_fortnite"))
check("the workspace operators stay", hasattr(bpy.types, "FP_OT_workspace") and hasattr(bpy.types, "FP_OT_editor_window"))
workspaces.unregister()
check("unregistered: its opener gone", "fortnite" not in ub.OPENERS)

print("[editor_fortnite_menu_check] %d passed, %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
