"""The editor's pop-ups, headless: placement, the stack, layered hits, keys (highlight, Enter, filter, focus), text
editing, numbers typed into fields, colours.

    blender -b --factory-startup --python-exit-code 1 -P editor_popup_check.py -- <plugin parent>
"""
import sys

sys.path.insert(0, sys.argv[sys.argv.index("--") + 1])
from fpmp_baseline.operator.fortnite_editor.ui import colorpick, popup, router, textedit  # noqa: E402
from fpmp_baseline.operator.fortnite_editor.ui.frame import Frame, Hit  # noqa: E402
from fpmp_baseline.operator.fortnite_editor.ui.rect import Rect  # noqa: E402

FAILS, PASSES = [], [0]


def check(name, ok, detail=""):
    if ok:
        PASSES[0] += 1
    else:
        FAILS.append(name)
        print("[editor_popup_check] FAIL %s %s" % (name, detail))


def inside(r, bounds):
    return r.x >= bounds.x and r.y >= bounds.y and r.right <= bounds.right and r.top <= bounds.top


# --- placement (y up: a rect's y is its bottom)
B = Rect(0, 0, 1000, 600)
a = Rect(100, 400, 80, 20)
r = popup.place(a, (200, 150), "below", B, 4)
check("below: under the anchor, left edges level", r.top == a.y - 4 and r.x == a.x and (r.w, r.h) == (200, 150), r)
low = Rect(100, 60, 80, 20)
r = popup.place(low, (200, 150), "below", B, 4)
check("no room below: above", r.y == low.top + 4 and inside(r, B), r)
r = popup.place(Rect(950, 400, 40, 20), (200, 150), "below", B, 4)
check("past the right edge: slid back in", r.right == B.right and inside(r, B), r)
r = popup.place(Rect(100, 300, 80, 20), (200, 150), "right", B, 4)
check("right: beside it, tops level", r.x == 184 and r.top == 320, r)
r = popup.place(Rect(900, 300, 80, 20), (200, 150), "right", B, 4)
check("no room right: left", r.right == 896, r)
r = popup.place(Rect(500, 300, 80, 20), (200, 150), "left", B, 4)
check("left", r.right == 496 and r.top == 320, r)
r = popup.place(None, (200, 150), "center", B, 4)
check("centred", (r.x, r.y) == (400, 225), r)
r = popup.place(Rect(300, 200, 0, 0), (200, 150), "at", B, 4)
check("at the mouse: its top-left corner there", (r.x, r.top) == (300, 200), r)
r = popup.place(a, (2000, 900), "below", B, 4)
check("bigger than the region: clamped to it", inside(r, B) and r.w == B.w and r.h <= B.h, r)
r = popup.place(Rect(100, 300, 80, 20), (200, 400), "below", B, 4)
check("fits neither side: the side with more room, shrunk", inside(r, B) and r.h <= 400, r)


# --- the stack
def mk(id, parent=None, **kw):
    return popup.Popup(id, lambda ui, rect, p: None, anchor=Rect(0, 0, 10, 10), parent=parent, **kw)


s = popup.Stack()
m = s.open(mk("menu"))
sub = s.open(mk("sub", parent="menu"))
check("open: on top", s.top is sub and len(s) == 2)
s.open(mk("other"))
check("a new root closes the others", [p.id for p in s.popups] == ["other"])
m = s.open(mk("m"))
sub = s.open(mk("sub", parent="m"))
sub2 = s.open(mk("sub2", parent="m"))
check("a sibling replaces the open child", [p.id for p in s.popups] == ["m", "sub2"])
closed = []
s.popups[-1].on_close = lambda: closed.append("sub2")
s.close(s.find("m"))
check("close takes what it opened with it", not s and closed == ["sub2"])
s.open(mk("x"))
s.open(mk("x"))
check("opening the same id again: replaced, not stacked", len(s) == 1)
s.close_all()

# --- layered hits: nothing under a pop-up answers
f = Frame()
f.add(Hit("under", Rect(0, 0, 100, 100)))
f.layer = 1
f.add(Hit("item", Rect(10, 10, 20, 20)))
f.block(1)
check("the pop-up's hit answers", f.at(15, 15).id == "item")
check("the shell's doesn't", f.at(80, 80) is None)
f.block(0)
check("unblocked: the shell again", f.at(80, 80).id == "under")


# --- outside press
s = popup.Stack()
m = s.open(mk("m"))
m.rect = Rect(0, 0, 100, 100)
sub = s.open(mk("sub", parent="m"))
sub.rect = Rect(100, 0, 100, 100)
check("press inside the top: kept", popup.press(s, 150, 50) == "inside" and len(s) == 2)
check("press in the parent: the child closes, the press goes on", popup.press(s, 50, 50) == "inside" and len(s) == 1)
check("press outside: all close, eaten", popup.press(s, 500, 500) == "closed" and not s)

# --- keys: highlight, Enter, Esc, filter
ran = []
s = popup.Stack()
m = s.open(mk("menu", search=True))
m.nav = [("a", lambda c: ran.append("a")), ("b", lambda c: ran.append("b")), ("c", None)]
store = router.Store()


def key(k, text="", ctrl=False, shift=False):
    out = popup.key(s, store, popup.Key(k, text, ctrl, shift))
    for e in out:
        if e[0] == "run":
            e[1](None)
    return [e[0] for e in out]


check("down: the first item", "redraw" in key("DOWN_ARROW") and m.cursor == "a")
key("DOWN_ARROW")
check("down again", m.cursor == "b")
key("DOWN_ARROW")
key("DOWN_ARROW")
check("wraps", m.cursor == "a", m.cursor)
key("UP_ARROW")
check("up wraps", m.cursor == "c", m.cursor)
key("END")
check("end", m.cursor == "c")
key("HOME")
check("home", m.cursor == "a")
check("enter runs it and closes", key("RET") == ["run", "redraw"] and ran == ["a"] and not s, key)
m = s.open(mk("menu", search=True))
m.nav = [("a", None)]
key("B", "b")
key("O", "o")
check("typing filters", m.query.value == "bo" and m.cursor is None)
key("BACK_SPACE")
check("backspace in the filter", m.query.value == "b")
key("ESC")
check("esc closes", not s)
m = s.open(mk("menu", search=True, keep_open=True))
m.nav = [("a", lambda c: ran.append("a2"))]
key("DOWN_ARROW")
key("RET")
check("a keep-open pop-up stays after Enter", ran[-1] == "a2" and len(s) == 1)
s.close_all()

# --- tile grids: arrows by columns
g = s.open(mk("tiles"))
g.nav, g.cols = [(str(n), None) for n in range(10)], 4
g.cursor = "1"
key("DOWN_ARROW")
check("down a row", g.cursor == "5")
key("RIGHT_ARROW")
check("right one", g.cursor == "6")
key("DOWN_ARROW")
check("down past the last row: the last", g.cursor == "9", g.cursor)
key("LEFT_ARROW")
check("left one", g.cursor == "8")
s.close_all()

# --- dialogs: Enter is the default button, Esc cancels
done = []
d = s.open(mk("dlg", kind="dialog", modal=True, on_accept=lambda c: done.append("ok"), on_cancel=lambda c: done.append("no")))
key("RET")
check("enter accepts", done == ["ok"] and not s)
d = s.open(mk("dlg", kind="dialog", modal=True, on_accept=lambda c: done.append("ok"), on_cancel=lambda c: done.append("no")))
key("ESC")
check("esc cancels", done == ["ok", "no"] and not s)
d = s.open(mk("dlg", kind="dialog", modal=True))
d.rect = Rect(0, 0, 50, 50)
check("a modal dialog ignores an outside press", popup.press(s, 500, 500) == "inside" and len(s) == 1)
s.close_all()

# --- focus: a field takes the keys, Tab moves on
p = s.open(mk("panel", kind="panel"))
fields = {"f1": textedit.Text("abc"), "f2": textedit.Text("")}
p.fields = ["f1", "f2"]
popup.focus(store, "f1", fields["f1"])
check("focusing selects it all", fields["f1"].selection() == (0, 3))
fields["f1"].key("END")
committed = []
store.on_commit = lambda id, text: committed.append((id, text.value))
key("D", "d")
check("typing goes to the focused field", fields["f1"].value == "abcd" and len(s) == 1)
key("TAB")
check("tab commits and moves focus", committed == [("f1", "abcd")] and store.focus == "f2")
key("TAB", shift=True)
check("shift+tab back", store.focus == "f1")
key("X", "x")
key("ESC")
check("esc in a field reverts it, the pop-up stays", fields["f1"].value == "abcd" and store.focus is None and len(s) == 1,
      fields["f1"].value)
s.close_all()

# --- text editing
t = textedit.Text("hello world")
check("cursor at the end", t.cursor == 11)
t.key("LEFT_ARROW", ctrl=True)
check("ctrl+left: word start", t.cursor == 6)
t.key("BACK_SPACE", ctrl=True)
check("ctrl+backspace: the word before", t.value == "world" and t.cursor == 0, (t.value, t.cursor))
t.key("END", shift=True)
check("shift+end selects", t.selection() == (0, 5))
t.key("", text="W")
check("typing replaces the selection", t.value == "W" and t.cursor == 1)
t.key("A", ctrl=True)
clip = []
t.key("C", ctrl=True, clipboard=clip)
check("ctrl+a, ctrl+c", clip == ["W"])
t.key("V", ctrl=True, clipboard=["hi there"])
check("ctrl+v replaces the selection", t.value == "hi there")
t.key("HOME")
t.key("DEL")
check("delete", t.value == "i there")
t.key("RIGHT_ARROW", shift=True)
t.key("RIGHT_ARROW", shift=True)
cut = []
t.key("X", ctrl=True, clipboard=cut)
check("ctrl+x", cut == ["i "] and t.value == "there")
t.revert()
check("revert to the value it had when focused", t.value == "hello world")

# --- numbers typed into a field
check("plain", textedit.number("2.5") == 2.5)
check("expression", textedit.number("2*3+1") == 7)
check("parentheses, power", textedit.number("(1+1)**3") == 8)
check("int rounds", textedit.number("2.6", integer=True) == 3)
check("clamped", textedit.number("50", lo=0, hi=10) == 10)
check("rejects names", textedit.number("__import__('os')") is None)
check("rejects junk", textedit.number("2+") is None)
check("unary minus", textedit.number("-3") == -3)

# --- colours
for rgb in ((1, 0, 0), (0.2, 0.4, 0.6), (0, 0, 0), (1, 1, 1)):
    back = colorpick.hsv_to_rgb(*colorpick.rgb_to_hsv(*rgb))
    check("hsv round trip %s" % (rgb,), all(abs(x - y) < 1e-6 for x, y in zip(rgb, back)), back)
check("hex", colorpick.to_hex((1, 0.5, 0)) == "FF8000")
check("hex parse", colorpick.from_hex("#ff8000") == (1.0, 128 / 255, 0.0))
check("short hex", colorpick.from_hex("f80") == (1.0, 136 / 255, 0.0))
check("bad hex", colorpick.from_hex("zz") is None)
check("hex is display (sRGB) of a linear colour", colorpick.to_hex(colorpick.srgb_to_linear((0.5, 0.5, 0.5)), linear=True) == "808080")

print("[editor_popup_check] %d passed, %d failed" % (PASSES[0], len(FAILS)))
if FAILS:
    sys.exit(1)
