"""The Assets tab's FP tools, headless: filters, the Newest sort, ctrl/shift picks and the import queue.

    blender -b --factory-startup --python-exit-code 1 -P editor_browse_check.py -- <plugin parent>
"""
import sys
from types import SimpleNamespace

sys.path.insert(0, sys.argv[sys.argv.index("--") + 1])
from fpmp_baseline.operator.fortnite_editor import browse_ops, importing  # noqa: E402
from fpmp_baseline.operator.fortnite_editor import state as st  # noqa: E402

FAILS, PASSES = [], [0]


def check(name, ok, detail=""):
    if ok:
        PASSES[0] += 1
    else:
        FAILS.append(name)
        print("[editor_browse_check] FAIL %s %s" % (name, detail))


def item(name, season=None, id="", filters=(), favorite=False, hidden=False):
    return {"name": name, "path": "/Game/" + name, "season": season, "id": id, "filters": list(filters),
            "favorite": favorite, "hidden": hidden}


ITEMS = [item("Alpha", 3, "CID_100", ["Battle Pass"]), item("Bravo", 30, "CID_200", ["Item Shop"], favorite=True),
         item("Charlie", None, "CID_050"), item("Delta", 30, "CID_900", ["Item Shop", "Save The World"]),
         item("Echo", 12, "CID_300", hidden=True)]
st.STATE["tabs"]["Outfit"] = {"finished": True, "items": ITEMS, "filters": [
    {"category": "COSMETIC", "filters": [{"title": "Battle Pass", "any": None}, {"title": "Item Shop", "any": None}]},
    {"category": "GAME", "filters": [{"title": "Battle Royale", "any": "game"}, {"title": "Save The World", "any": "game"}]}]}
wm = SimpleNamespace(fp_browse_type="Outfit", fp_browse_filters="", fp_browse_search="", fp_browse_sort='NAME',
                     fp_browse_selected="")


def names():
    return [i["name"] for i in st.items(wm)]


# --- filters
check("no filter hides hidden items", names() == ["Alpha", "Bravo", "Charlie", "Delta"], names())
wm.fp_browse_filters = "Hidden Items"
check("Hidden Items shows only them", names() == ["Echo"], names())
wm.fp_browse_filters = "Favorites"
check("Favorites", names() == ["Bravo"], names())
wm.fp_browse_filters = "Item Shop"
check("a plain filter", names() == ["Bravo", "Delta"], names())
wm.fp_browse_filters = "Item Shop|Battle Pass"
check("plain filters all match", names() == [], names())
wm.fp_browse_filters = "Battle Royale|Save The World"
check("an any-of group matches any", names() == ["Delta"], names())
wm.fp_browse_filters = "Pickaxe Only"
check("another tab's filter doesn't apply", names() == ["Alpha", "Bravo", "Charlie", "Delta"], names())
check("groups: application first", [g for g, _ in st.filter_groups(wm)] == ["APPLICATION", "COSMETIC", "GAME"])
wm.fp_browse_filters = ""

# --- the Newest sort
wm.fp_browse_sort = 'NEWEST'
check("newest: season down, later ids first, no season last", names() == ["Delta", "Bravo", "Alpha", "Charlie"], names())
wm.fp_browse_sort = 'NAME'

# --- picks
browse_ops.pick(wm, "/Game/Alpha")
check("a click picks one", st.picked(wm) == ["/Game/Alpha"], st.picked(wm))
browse_ops.pick(wm, "/Game/Charlie", extend=True)
check("ctrl adds", st.picked(wm) == ["/Game/Alpha", "/Game/Charlie"] and wm.fp_browse_selected == "/Game/Charlie",
      st.picked(wm))
browse_ops.pick(wm, "/Game/Charlie", extend=True)
check("ctrl again removes", st.picked(wm) == ["/Game/Alpha"] and wm.fp_browse_selected == "/Game/Alpha", st.picked(wm))
browse_ops.pick(wm, "/Game/Delta", range_=True)
check("shift picks the run", st.picked(wm) == ["/Game/Alpha", "/Game/Bravo", "/Game/Charlie", "/Game/Delta"],
      st.picked(wm))
browse_ops.pick(wm, "/Game/Bravo")
check("a plain click drops the others", st.picked(wm) == ["/Game/Bravo"], st.picked(wm))


# --- the import queue
class Engine:
    def __init__(self):
        self.calls = []

    def request(self, route, done, **query):
        self.calls.append((route, query["path"], done))


engine = st.STATE["engine"] = Engine()
st.STATE["busy"], st.STATE["queue"] = None, []
importing.start_all([("Outfit", i, {}) for i in ITEMS[:3]])
check("the first starts", [c[1] for c in engine.calls] == ["/Game/Alpha"] and "(2 more)" in (st.STATE["busy"] or ""),
      st.STATE["busy"])
engine.calls[0][2](RuntimeError("boom"))
check("the next starts when one ends", [c[1] for c in engine.calls] == ["/Game/Alpha", "/Game/Bravo"])
check("a failure stays shown while the rest import", "boom" in (st.STATE["error"] or ""), st.STATE["error"])
engine.calls[1][2](RuntimeError("bang"))
engine.calls[2][2](RuntimeError("last"))
check("the queue drains", not st.STATE["busy"] and not st.STATE["queue"] and len(engine.calls) == 3)
st.STATE["engine"] = None

# --- the icon atlas's slots: free first, then the least recently drawn, never one on screen
from fpmp_baseline.operator.fortnite_editor import textures  # noqa: E402

s = textures.Slots(3)
check("free slots first, lowest first", [s.take(p, 1) for p in "abc"] == [(0, None), (1, None), (2, None)])
check("full and all on screen: no slot", s.take("d", 1) == (None, None))
s.get("a", 5)       # drawn later: b is now the least recently drawn
check("an old one is reused, the least recently drawn", s.take("d", 5) == (1, "b"), s.held)
check("the icon it held is gone", s.get("b", 5) is None and s.get("d", 5) == 1)
s.get("a", 6), s.get("c", 6), s.get("d", 6)
check("drawn in the last draws: kept", s.take("e", 7) == (None, None))
check("forced: the least recently drawn goes", s.take("e", 7, force=True) == (0, "a"))
check("taking a held icon keeps its slot", s.take("e", 8) == (0, None) and len(s.held) == 3)
s.grow(2)
check("a new page's slots before any reuse", s.take("f", 8) == (3, None) and s.take("g", 8) == (4, None))
s = textures.Slots(textures.PER_PAGE)
ok, now = True, 0
for start in range(0, 3500, 60):     # scrolled through a long list, a screen of 60 at a time
    now += 1
    for i in range(start, start + 60):
        slot, _old = s.take("icon%d" % i, now)
        ok = ok and slot is not None
check("a long list scrolls through one page", ok and len(s.held) == textures.PER_PAGE and s.count == textures.PER_PAGE,
      (len(s.held), s.count))
check("slots start on whole texels down to mip 4", textures.PITCH % 32 == 0 and textures.GUTTER % 16 == 0
      and textures.PER_ROW * textures.PITCH <= textures.PAGE)
# the Texture Limit cuts a page made from an image (2048 here): the slots are laid out on the page it got
atlas = textures.Atlas()
check("a full page lays out as it always did", atlas.size == textures.PAGE and atlas.per_page == textures.PER_PAGE)
atlas.fit(2048)
spots = [atlas.spot(n) for n in range(2 * atlas.per_page)]
check("a cut page's slots all lie on it", atlas.per_page == 49 and all(
    x >= textures.GUTTER and y >= textures.GUTTER and x + textures.SLOT + textures.GUTTER <= 2048
    and y + textures.SLOT + textures.GUTTER <= 2048 for _p, x, y in spots), spots[atlas.per_page - 1])
check("the slots after them start the next page", spots[atlas.per_page] == (1, textures.GUTTER, textures.GUTTER))
try:
    atlas.fit(1024)
    cut = False
except RuntimeError:
    cut = True
check("pages cut below MIN_PAGE: no atlas (the grid draws icon textures)", cut)
before = textures._draws[0]
textures.set_visible(10)
check("each grid draw ticks the atlas's clock", textures._draws[0] == before + 1)
check("an icon the atlas lacks is noted for add()", textures.slot("/x/icon") is None and "/x/icon" in textures._asked)
check("add() without a file fails", textures.add("/x/icon", None) is False)

# --- the Filters panel: a checkbox row per filter under its group, Clear filters while some are on
import bpy  # noqa: E402
from fpmp_baseline.operator.fortnite_editor import cosmetics_page  # noqa: E402
from fpmp_baseline.operator.fortnite_editor.ui import blender as ub  # noqa: E402
from fpmp_baseline.operator.fortnite_editor.ui.rect import Rect  # noqa: E402

wm.fp_browse_filters = ""
ctx = SimpleNamespace(window_manager=wm, preferences=bpy.context.preferences)
rows = browse_ops.filter_items(wm)
check("filter groups as headers, application first", [r["header"] for r in rows if "header" in r]
      == ["Application", "Cosmetic", "Game"], [r.get("header") for r in rows])
check("a row per filter, none ticked", [r["label"] for r in rows if "check" in r][-4:]
      == ["Battle Pass", "Item Shop", "Battle Royale", "Save The World"] and not any(r.get("check") for r in rows))
check("no Clear filters with none on", not any(r.get("id") == "clear" for r in rows))
next(r for r in rows if r.get("label") == "Item Shop")["action"](ctx)
check("a row ticks its filter", wm.fp_browse_filters == "Item Shop", wm.fp_browse_filters)
rows = browse_ops.filter_items(wm)
check("then it's ticked", [r["label"] for r in rows if r.get("check")] == ["Item Shop"])
next(r for r in rows if r.get("label") == "Battle Pass")["action"](ctx)
next(r for r in rows if r.get("label") == "Item Shop")["action"](ctx)
check("ticking again unticks", wm.fp_browse_filters == "Battle Pass", wm.fp_browse_filters)
clear = [r for r in browse_ops.filter_items(wm) if r.get("id") == "clear"]
check("Clear filters while some are on", len(clear) == 1)
clear[0]["action"](ctx)
check("Clear filters clears", wm.fp_browse_filters == "")

# --- the Categories picker (the list column folded): FP's groups as headers, the shown tab marked
st.STATE["categories"] = [{"category": "Cosmetics", "tabs": ["Outfit", "Backpack"]},
                          {"category": "FallGuys", "tabs": ["FallGuysOutfit"]}]
went = []
real_browse = cosmetics_page._browse
cosmetics_page._browse = lambda kind: (lambda context: went.append(kind))
try:
    cats = cosmetics_page.categories_items("Backpack")
finally:
    cosmetics_page._browse = real_browse
check("group headers", [c["header"] for c in cats if "header" in c] == ["Cosmetics", "Fall Guys"])
check("plural names", [c["label"] for c in cats if "label" in c] == ["Outfits", "Backpacks", "Fall Guys Outfits"])
check("the shown tab marked", [c["label"] for c in cats if c.get("check")] == ["Backpacks"])
check("FP's glyphs", [c["icon"] for c in cats if "label" in c] == ["Outfit", "Backpack", "FallGuysOutfit"])
next(c for c in cats if c.get("label") == "Fall Guys Outfits")["action"](ctx)
check("a row shows its tab", went == ["FallGuysOutfit"], went)
st.STATE["categories"] = None
check("no categories yet: a quiet row", [c.get("label") for c in cosmetics_page.categories_items("Outfit")]
      == ["Waiting for FP..."])

# --- their pop-ups
opened = []
real_open = ub.open
ub.open = lambda context, p: opened.append(p) or p
try:
    anchor = Rect(100, 500, 16, 0)
    cosmetics_page.open_categories(ctx, anchor, "below")
    browse_ops.open_filters(ctx, anchor, "below")
finally:
    ub.open = real_open
kinds = [(p.kind, p.anchor is anchor, p.side) for p in opened]
check("categories: a list picker under its button, filters: a panel", kinds == [("list", True, "below"), ("panel", True, "below")],
      kinds)
browse_ops.register()
check("the header opens Filters through fp.popup_open", ub.OPENERS.get("filters") is browse_ops.open_filters)
check("no legacy Filters popover", not hasattr(bpy.types, "FP_PT_browse_filters"))
check("no legacy Categories menu", not hasattr(cosmetics_page, "FP_MT_fortnite_categories"))
browse_ops.unregister()
check("unregistered: its opener gone", "filters" not in ub.OPENERS)

# --- the categories column dragged down to icons: centred in the column, no scroll bar until the mouse is over it
from fpmp_baseline.operator.fortnite_editor.ui import frame as ui_frame, kit, paint, router, shell  # noqa: E402
from fpmp_baseline.operator.fortnite_editor.ui.theme import Theme  # noqa: E402


def draw_side(side, r, store, compact=True):
    """side(ui, r) with the real kit, paint recorded: (frame, icon boxes, other rects (rect, fill))."""
    ui = kit.Ui(Theme(1.0), ui_frame.Frame(), store, 0.0)
    ui.compact = compact
    icons, rects = [], []
    saved = {n: getattr(paint, n) for n in ("rect", "line", "text_in", "push_clip", "pop_clip")}
    saved_glyph = kit.glyph
    paint.rect = lambda box, fill, *a, image=None, **k: (icons if image is not None else rects).append((box.copy(), fill))
    paint.line = paint.text_in = paint.push_clip = paint.pop_clip = lambda *a, **k: None
    kit.glyph = lambda name: object()
    try:
        side(ui, r.copy())
    finally:
        for n, fn in saved.items():
            setattr(paint, n, fn)
        kit.glyph = saved_glyph
    return ui.frame, [b for b, _ in icons], rects


if not hasattr(bpy.types.WindowManager, "fp_browse_names"):
    bpy.types.WindowManager.fp_browse_names = bpy.props.BoolProperty()
    bpy.types.WindowManager.fp_browse_type = bpy.props.StringProperty()
st.STATE["categories"] = [{"category": "Cosmetics", "tabs": ["Outfit%d" % i for i in range(14)]},
                          {"category": "Fall Guys", "tabs": ["Fall%d" % i for i in range(14)]}]
column = Rect(10, 0, shell.ICON_COLUMN, 300)
store = router.Store()
frame, icons, rects = draw_side(cosmetics_page.side, column, store)
bar_fill = Theme(1.0).text3[:3] + (0.35,)
row_icons = [b for b in icons if b.y > column.y + 34]        # the list's (the names button sits under it)
margins = {(round(b.x - column.x, 2), round(column.right - b.right, 2)) for b in row_icons}
check("compact: the icons centred in the column", row_icons and all(abs(a - b) <= 1 for a, b in margins), margins)
check("compact: no scroll bar while the mouse is elsewhere", not [b for b, f in rects if tuple(f) == bar_fill],
      [b for b, f in rects if tuple(f) == bar_fill])
fx = router.route(frame, store, router.Event("move", column.x + 16, 150, time=1.0))
check("the mouse over the list redraws", ("redraw",) in fx and store.over == "cosmetics:categories", (fx, store.over))
_f, _i, rects = draw_side(cosmetics_page.side, column, store)
bars = [b for b, f in rects if tuple(f) == bar_fill]
check("and shows a thin bar over the column's edge", len(bars) == 1 and bars[0].w <= 2 and bars[0].right <= column.right,
      bars)
st.STATE["categories"] = None

print("[editor_browse_check] %d passed, %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
