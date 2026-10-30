"""The editor's UI core, headless: rects, the shell's columns and their folding, frames and the router's effects.

    blender -b --factory-startup --python-exit-code 1 -P editor_ui_check.py -- <plugin parent>
"""
import sys

sys.path.insert(0, sys.argv[sys.argv.index("--") + 1])
from fpmp_baseline.operator.fortnite_editor.ui import router, shell  # noqa: E402
from fpmp_baseline.operator.fortnite_editor.ui.frame import Frame, Hit  # noqa: E402
from fpmp_baseline.operator.fortnite_editor.ui.rect import Rect  # noqa: E402
from fpmp_baseline.operator.fortnite_editor.ui.theme import Theme  # noqa: E402

FAILS, PASSES = [], [0]


def check(name, ok, detail=""):
    if ok:
        PASSES[0] += 1
    else:
        FAILS.append(name)
        print("[editor_ui_check] FAIL %s %s" % (name, detail))


# --- rects
r = Rect(0, 0, 100, 50)
left = r.cut_left(30)
check("cut_left", left == Rect(0, 0, 30, 50) and r == Rect(30, 0, 70, 50), (left, r))
top = r.cut_top(10)
check("cut_top keeps the bottom", top == Rect(30, 40, 70, 10) and r == Rect(30, 0, 70, 40), (top, r))
check("cut more than there is", Rect(0, 0, 10, 10).cut_right(50) == Rect(0, 0, 10, 10))
check("pad", Rect(0, 0, 100, 50).pad(5) == Rect(5, 5, 90, 40))
check("contains, right edge out", Rect(0, 0, 10, 10).contains(9.9, 0) and not Rect(0, 0, 10, 10).contains(10, 0))
check("intersect", Rect(0, 0, 10, 10).intersect(Rect(5, 5, 10, 10)) == Rect(5, 5, 5, 5))


# --- the shell's columns
class Tab:
    def __init__(self, side=True, info=True):
        self.side = (lambda ui, r: None) if side else None
        self.info = (lambda ui, r: None) if info else None


t = Theme(1.0)
c = shell.columns(Tab(), 1300, 800, t)
check("an icon rail when not wide", c["nav"].w == shell.ICON_COLUMN and not c["wide"])
check("well between the columns", c["side"].right <= c["well"].x and c["well"].right <= c["info"].x, c)
check("wide nav", shell.columns(Tab(), 1600, 800, t)["nav"].w == t.nav_wide - 2 * t.pad)
check("info folds first", shell.columns(Tab(), 800, 800, t)["info"] is None and shell.columns(Tab(), 800, 800, t)["side"] is not None)
check("then the list", shell.columns(Tab(), 600, 800, t)["side"] is None)
check("no list column for a tab without one", shell.columns(Tab(side=False), 1300, 800, t)["side"] is None)
o = shell.columns(Tab(), 1300, 800, t, left=0, right=280)
check("Blender's sidebar over the region pushes the info column", o["info"].right <= 1300 - 280, o["info"])
check("scaled", Theme(2.0).rail == 2 * t.rail)
m, p = shell.margin(t, 0), c["panel"]
check("the nav's icons with the same margin each side, the panel that margin from every edge",
      c["nav"].x == m and p.x - c["nav"].right == m and p.y == m and 800 - p.top == m and 1300 - p.right == m, c)
check("one gap between the panel, its columns and the well", c["side"].x - p.x == t.pad and c["well"].x - c["side"].right == t.pad
      and c["info"].x - c["well"].right == t.pad and p.right - c["info"].right == t.pad and c["well"].y - p.y == t.pad, c)
f = shell.columns(Tab(), 1300, 800, t, frame=14)
check("Blender's arrow strip as the margin, even all round", f["nav"].x == 14 and f["panel"].x - f["nav"].right == 14
      and f["panel"].y == 14 and 800 - f["panel"].top == 14 and 1300 - f["panel"].right == 14, f)
q = shell.columns(Tab(), 1300, 800, t, side_w=60)
check("a list column dragged narrow shows icons only", q["compact"] and q["side"].w == shell.ICON_COLUMN, q["side"])
check("dragged widths kept in range", shell.columns(Tab(), 1300, 800, t, side_w=9000)["side"].w == shell.SIDE_MAX
      and shell.columns(Tab(), 1300, 800, t, info_w=10)["info"].w == shell.INFO_MIN)
card, control = t.r_card + t.pad, t.r_small + t.pad
check("panel corners concentric with what sits in them", shell.corners(t, c) == (card, control, control, control)
      and shell.corners(t, shell.columns(Tab(side=False, info=False), 1300, 800, t)) == (card,) * 4, shell.corners(t, c))
e = shell.columns(Tab(), 1300, 800, t, has_info=False)
check("an empty info column folds", e["info"] is None and e["panel"].right - e["well"].right == t.pad, e)

# --- frames and the router
f = Frame()
f.well = Rect(100, 0, 500, 400)
clicked = []
f.add(Hit("btn", Rect(10, 10, 50, 20), kind="button", action=lambda ctx: clicked.append("btn"), tip="Button"))
f.add(Hit("row", Rect(10, 40, 80, 20), kind="row", action=lambda ctx: clicked.append("row"),
          double=lambda ctx: clicked.append("double")))
f.add(Hit("list", Rect(0, 30, 90, 200), kind="scroll", scroll=("list", 300.0)))
f.add(Hit("row2", Rect(10, 40, 80, 20), kind="row"))       # drawn last: on top
s = router.Store()


def effects(ev):
    return [e[0] for e in router.route(f, s, ev)]


def run(ev):
    out = router.route(f, s, ev)
    for e in out:
        if e[0] == "run":
            e[1](None)
    return [e[0] for e in out]


check("hover: redraw once, then pass", effects(router.Event("move", 20, 15, time=1.0)) == ["redraw", "pass"]
      and effects(router.Event("move", 21, 15, time=1.1)) == ["pass"], s.hot)
check("hover sets the hot widget", s.hot == "btn")
check("topmost hit wins", f.at(20, 45).id == "row2")
check("a button captures on press", run(router.Event("press", 20, 15)) == ["redraw", "capture"] and not clicked)
check("and clicks on release inside", run(router.Event("release", 21, 16)) == ["redraw", "release", "run"] and clicked == ["btn"])
run(router.Event("press", 20, 15))
check("released outside: no click", "run" not in run(router.Event("release", 300, 300)) and clicked == ["btn"])
f.hits.pop()        # row2 gone: the row itself on top
check("a row runs on press", run(router.Event("press", 20, 45)) == ["redraw", "run"] and clicked[-1] == "row")
check("double click", run(router.Event("double", 20, 45)) == ["redraw", "run"] and clicked[-1] == "double")
check("press in the well passes on", effects(router.Event("press", 300, 100)) == ["pass"])
check("press on the shell's empty ground ends there", effects(router.Event("press", 95, 300)) == ["consume"])
check("wheel scrolls the list", effects(router.Event("wheel", 20, 100, 1)) == ["redraw"] and s.offset("list") == router.WHEEL_STEP)
for _ in range(20):
    router.route(f, s, router.Event("wheel", 20, 100, 1))
check("wheel clamps", s.offset("list") == 300.0, s.offset("list"))
check("wheel over the well passes on", effects(router.Event("wheel", 300, 100, 1)) == ["pass"])
check("no frame yet: pass", [e[0] for e in router.route(None, s, router.Event("press", 1, 1))] == ["pass"])

print("[editor_ui_check] %d passed, %d failed" % (PASSES[0], len(FAILS)))
if FAILS:
    sys.exit(1)
