"""The Assets tab's style picker, headless: every channel (2 options or 300) opens one tile picker; its items (names
once, the option's icon as the tile, the current pick marked) and picking setting the import pick; the legacy menu and
search popup are gone.

    blender -b --factory-startup --python-exit-code 1 -P editor_style_search_check.py -- <plugin parent>
"""
import sys
from types import SimpleNamespace

import bpy

sys.path.insert(0, sys.argv[sys.argv.index("--") + 1])
from fpmp_baseline.operator.fortnite_editor import asset_tab  # noqa: E402
from fpmp_baseline.operator.fortnite_editor import state as st  # noqa: E402
from fpmp_baseline.operator.fortnite_editor.ui import blender as ub, popups  # noqa: E402
from fpmp_baseline.operator.fortnite_editor.ui.rect import Rect  # noqa: E402

FAILS, PASSES = [], [0]


def check(name, ok, detail=""):
    if ok:
        PASSES[0] += 1
    else:
        FAILS.append(name)
        print("[editor_style_search_check] FAIL %s %s" % (name, detail))


asset_tab.register()
PATH = "/Game/Cars/Body_Test"
wheels = [{"name": "Wheel %02d" % n, "icon": "C:/icons/wheel%02d.png" % n if n % 2 else None} for n in range(30)]
wheels.append({"name": "Wheel 05", "icon": None})       # the same name twice: one tile (picks are by name)
channel = {"channel": "Wheels", "selected": "Wheel 03", "options": wheels}
short = {"channel": "Body", "selected": "Red", "options": [{"name": "Red", "icon": None}, {"name": "Blue", "icon": None}]}

# --- the items
st.STATE["picks"].clear()
items = asset_tab.style_items(PATH, channel)
check("each name once, in order", [i["label"] for i in items] == ["Wheel %02d" % n for n in range(30)],
      [i["label"] for i in items][:6])
check("the option's icon is the tile", items[1]["image"] == "C:/icons/wheel01.png" and not items[0]["image"])
check("no pick yet: the channel's own style marked", [i["label"] for i in items if i["check"]] == ["Wheel 03"])
items[27]["action"](None)
check("picking sets the import pick", st.STATE["picks"].get(PATH) == {"Wheels": "Wheel 27"}, st.STATE["picks"])
items = asset_tab.style_items(PATH, channel)
check("then it's the marked one", [i["label"] for i in items if i["check"]] == ["Wheel 27"])
check("the search finds by name, any case, words in any order", [i["label"] for i in popups.matches(items, "27 WHEEL")] == ["Wheel 27"])

# --- one tile picker for every channel size
opened = []
real_open = ub.open
ub.open = lambda context, p: opened.append(p) or p
try:
    anchor = Rect(900, 300, 200, 40)
    for ch in (short, channel):
        opened.clear()
        asset_tab.open_styles(SimpleNamespace(preferences=bpy.context.preferences), anchor, PATH, ch)
        p = opened[0] if opened else None
        check("%d options: a tile picker" % len(ch["options"]), p is not None and p.kind == "tiles" and p.query is not None
              and p.anchor is anchor and p.side == "left", p and (p.kind, p.side))
finally:
    ub.open = real_open
check("its tiles' size is remembered per picker", popups.zoom(SimpleNamespace(state={"zoom": "styles"}), -2)
      and popups.ZOOM["styles"] == popups.TILE + 32)

# --- the legacy pop-ups are gone
check("no style menu", not hasattr(bpy.types, "FP_MT_style_options"))
check("no style search popup", not hasattr(bpy.types, "FP_OT_style_search"))
r = bpy.ops.fp.style_pick(path=PATH, channel="Body", option="Blue")
check("fp.style_pick still sets a pick", r == {'FINISHED'} and st.STATE["picks"][PATH]["Body"] == "Blue")
asset_tab.unregister()

print("[editor_style_search_check] %d passed, %d failed" % (PASSES[0], len(FAILS)))
if FAILS:
    sys.exit(1)
