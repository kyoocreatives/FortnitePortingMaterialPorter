"""Quick Import (Ctrl+Shift+F), headless: its keymap item, the popup's enum items (FP not ready, categories loading,
every listed asset by name with its category, hidden ones left out, values kept while lists arrive), loading every
category once into STATE["tabs"], picking (import with default styles, queued when busy, Ctrl: shown in the editor)
and the build cost with 300k items.

    python plugin_test.py tests/plugin/editor_quick_search_check.py --name quick_search
"""
import sys
import time
from types import SimpleNamespace

import bpy

sys.path.insert(0, sys.argv[sys.argv.index("--") + 1])
from fpmp_baseline.operator.fortnite_editor import importing, logbook, quick_search as qs  # noqa: E402
from fpmp_baseline.operator.fortnite_editor import state as st  # noqa: E402

FAILS, PASSES = [], [0]


def check(name, ok, detail=""):
    if ok:
        PASSES[0] += 1
    else:
        FAILS.append(name)
        print("[editor_quick_search_check] FAIL %s %s" % (name, detail))


class FakeEngine:
    def __init__(self):
        self.state, self.status_text, self.asked, self.ensured = "loading", "Loading game files...", [], 0

    def ensure(self):
        self.ensured += 1

    def request(self, route, done, timeout=600, **query):
        self.asked.append((route, query, done))

    def answer(self, route, kind, result):
        """Hand the oldest matching request its answer (as engine.pump does on the editor's timer)."""
        for n, (r, q, done) in enumerate(self.asked):
            if r == route and q.get("type") == kind:
                del self.asked[n]
                return done(result)
        raise AssertionError("no %s request for %s" % (route, kind))


def item(name, path=None, hidden=False):
    return {"name": name, "path": path or "/Game/%s.%s" % (name.replace(" ", ""), name.replace(" ", "")), "hidden": hidden}


def labels():
    return [i[1] for i in qs.search_items()]


e = FakeEngine()
st.STATE["engine"] = e
qs._ctrl_held = lambda: False       # the real keyboard is not this test's
qs.register()
check("the operator registers", hasattr(bpy.types, "FP_OT_quick_import"))

# --- the shortcut: Window keymap (over every editor), Ctrl+Shift+F
kc = bpy.context.window_manager.keyconfigs.new("fp_quick_search_test")
kmi = qs.register_keys(kc)
km = qs._keymaps[-1][0]
check("in the Window keymap", km.name == "Window" and km.space_type == 'EMPTY' and km.region_type == 'WINDOW',
      (km.name, km.space_type, km.region_type))
check("Ctrl+Shift+F runs it", (kmi.idname, kmi.type, kmi.value, kmi.ctrl, kmi.shift, kmi.alt, kmi.oskey) ==
      ("fp.quick_import", 'F', 'PRESS', True, True, False, False), (kmi.idname, kmi.type, kmi.ctrl, kmi.shift, kmi.alt))

# --- FP not ready: one inert entry saying so
st.STATE["categories"] = None
items = qs.search_items()
check("not ready: one entry", len(items) == 1 and items[0][0] == qs.INFO, items)
check("it says why", "Loading game files" in items[0][1], items[0][1])
check("no category asked yet", e.asked == [])
calls = []
importing.start = lambda kind, it, picks: calls.append(("start", kind, it["name"], picks))
importing.start_all = lambda entries: calls.append(("start_all",) + tuple((k, i["name"], p) for k, i, p in entries))
r = bpy.ops.fp.quick_import('EXEC_DEFAULT', asset=qs.INFO)
check("picking it does nothing", r == {'CANCELLED'} and calls == [], (r, calls))

# --- ready: categories first, then every category asked once (in flight: not again)
e.state, e.status_text = "ready", ""
check("ready, no categories: loading entry", labels() == ["Loading categories..."], labels())
qs.LOAD["on"] = True
st.STATE["tab_asked"].clear()
qs._load_tick()
check("categories asked", [a[0] for a in e.asked] == ["browse-categories"], e.asked)
e.asked.pop()[2]([{"category": "Cosmetics", "tabs": ["Outfit", "Pickaxe"]}, {"category": "Gameplay", "tabs": ["Spray"]}])
check("categories kept in STATE", [c["category"] for c in st.STATE["categories"]] == ["Cosmetics", "Gameplay"])
qs._load_tick()
qs._load_tick()
check("each category asked once", sorted(q["type"] for r, q, d in e.asked if r == "browse") == ["Outfit", "Pickaxe", "Spray"],
      [(r, q) for r, q, d in e.asked])

OUTFITS = [item("Zed"), item("renegade Raider"), item("Ghost", hidden=True), item("Pipe | Dream"), item("Aura")]
e.answer("browse", "Outfit", {"finished": True, "items": OUTFITS})
e.answer("browse", "Pickaxe", {"finished": False, "loaded": 3, "total": 10, "items": []})
e.answer("browse", "Spray", RuntimeError("no sprays"))
check("finished list lands in STATE['tabs'] like the editor's", st.STATE["tabs"]["Outfit"]["items"] is OUTFITS)
check("an unfinished one too (its counts)", st.STATE["tabs"]["Pickaxe"]["loaded"] == 3)
items = qs.search_items()
check("loading entry first, the rest by name", labels() == ["Loading 2 categories...|Pickaxe, Spray", "Aura|Outfit",
                                                              "Pipe / Dream|Outfit", "renegade Raider|Outfit", "Zed|Outfit"], labels())
check("hidden items left out", not any("Ghost" in l for l in labels()))
check("each asset has its category's icon", all(i[3] == qs._icon('USER') and i[3] for i in items[1:]), [i[3] for i in items])
check("values unique", len({i[4] for i in items}) == len(items))
check("identifiers are the values", all(i[0] == str(i[4]) for i in items[1:]))
check("cached: the same list until something changes", qs.search_items() is items)
raider = next(i for i in items if i[1].startswith("renegade"))
check("an identifier names its asset", qs.entry(raider[0]) == ("Outfit", OUTFITS[1]), qs.entry(raider[0]))
check("the inert entry names none", qs.entry(qs.INFO) is None)

# --- asked again a second later; a failing category is left out after 3 tries
for k in list(st.STATE["tab_asked"]):
    st.STATE["tab_asked"][k] -= 5
qs._load_tick()
check("unfinished ones asked again", sorted(q["type"] for r, q, d in e.asked) == ["Pickaxe", "Spray"], e.asked)
e.answer("browse", "Spray", RuntimeError("no sprays"))
for k in list(st.STATE["tab_asked"]):
    st.STATE["tab_asked"][k] -= 5
qs._load_tick()
e.answer("browse", "Spray", RuntimeError("no sprays"))
check("given up on after 3 failures", qs.loading() == ["Pickaxe"], qs.loading())
check("and said so in the log", any("no Spray list" in l[3] for l in logbook.LOG["lines"]), list(logbook.LOG["lines"])[-2:])
PICKAXES = [item("Axe"), item("Zzz Pick")]
e.answer("browse", "Pickaxe", {"finished": True, "items": PICKAXES})
items2 = qs.search_items()
check("rebuilt when a list arrives", items2 is not items)
check("no loading entry once all are in", labels() == ["Aura|Outfit", "Axe|Pickaxe", "Pipe / Dream|Outfit",
                                                        "renegade Raider|Outfit", "Zed|Outfit", "Zzz Pick|Pickaxe"], labels())
check("an asset keeps its value while others arrive", next(i for i in items2 if i[1].startswith("renegade"))[4] == raider[4])
check("earlier item lists still referenced", items in qs._kept)
check("loading stops when all are in", qs._load_tick() is None and not qs.LOAD["on"])
done = qs._answer("Outfit")
qs.LOAD["inflight"].add("Outfit")
done({"finished": False, "items": []})
check("a late unfinished answer never replaces a finished list", st.STATE["tabs"]["Outfit"]["items"] is OUTFITS)

# --- picking: import with default styles; queued behind an import in flight; Ctrl: the editor instead
st.STATE["busy"] = None
r = bpy.ops.fp.quick_import('EXEC_DEFAULT', asset=raider[0])
check("pick imports with default styles", r == {'FINISHED'} and calls == [("start", "Outfit", "renegade Raider", {})], (r, calls))
calls.clear()
st.STATE["busy"] = "Exporting Zed..."
r = bpy.ops.fp.quick_import('EXEC_DEFAULT', asset=raider[0])
check("busy: queued", calls == [("start_all", ("Outfit", "renegade Raider", {}))], calls)
st.STATE["busy"] = None
calls.clear()

wm = SimpleNamespace(fp_browse_type="Pickaxe", fp_browse_filters="Favorites", fp_browse_search="zed", fp_browse_sort='NAME',
                     fp_browse_selected="", windows=[])
opened = []
real_bpy = qs.bpy
qs.bpy = SimpleNamespace(ops=SimpleNamespace(fp=SimpleNamespace(workspace=lambda name: opened.append(name))))
try:
    said = qs.show_in_editor(SimpleNamespace(window_manager=wm, window=None), "Outfit", OUTFITS[1])
finally:
    qs.bpy = real_bpy
check("Ctrl: the editor's tab and pick set", (wm.fp_browse_type, wm.fp_browse_selected) == ("Outfit", OUTFITS[1]["path"]),
      (wm.fp_browse_type, wm.fp_browse_selected))
check("the search and filters that hid it cleared", (wm.fp_browse_search, wm.fp_browse_filters) == ("", ""),
      (wm.fp_browse_search, wm.fp_browse_filters))
check("no editor open: FP Studio opened", opened == ["FP Studio"] and calls == [], (opened, said))
wm.fp_browse_search = "raider"
qs.bpy = SimpleNamespace(ops=SimpleNamespace(fp=SimpleNamespace(workspace=lambda name: opened.append(name))))
try:
    qs.show_in_editor(SimpleNamespace(window_manager=wm, window=None), "Outfit", OUTFITS[1])
finally:
    qs.bpy = real_bpy
check("a search that shows it is kept", wm.fp_browse_search == "raider")

# --- a list replaced under the popup (Refresh): a stale value imports nothing
st.STATE["tabs"]["Outfit"] = {"finished": True, "items": OUTFITS[:1]}
check("a value whose list shrank names nothing", qs.entry(raider[0]) is None, qs.entry(raider[0]))
st.STATE["tabs"]["Outfit"] = {"finished": True, "items": OUTFITS}

# --- Animation and Effect (90k+ between them) stay out of the quick search
st.STATE["categories"] = [{"category": "All", "tabs": ["Outfit", "Animation", "Effect"]}]
check("Animation and Effect are left out", qs.kinds() == ["Outfit"], qs.kinds())

# --- cost: 300k items in 6 categories (the test instance lists ~360k with Props and Animations)
kinds = ["Outfit", "Prop", "Backpack", "Pickaxe", "LegoProp", "Emote"]
sizes = [3500, 110000, 138000, 19500, 10000, 19000]
st.STATE["categories"] = [{"category": "Big", "tabs": kinds}]
for kind, n in zip(kinds, sizes):
    st.STATE["tabs"][kind] = {"finished": True, "items": [item("%s %06d" % (kind, (i * 7919) % n), "/Game/%s/A%d.A%d" % (kind, i, i))
                                                          for i in range(n)]}
t0 = time.perf_counter()
big = qs.search_items()
first = time.perf_counter() - t0
st.STATE["tabs"]["Emote"] = {"finished": True, "items": st.STATE["tabs"]["Emote"]["items"][:-1]}
t0 = time.perf_counter()
qs.search_items()
again = time.perf_counter() - t0
t0 = time.perf_counter()
for _ in range(20):
    qs.search_items()
cached = (time.perf_counter() - t0) / 20
t0 = time.perf_counter()
r = bpy.ops.fp.quick_import('EXEC_DEFAULT', asset=big[150000][0])      # Blender reads (converts) every item
through_rna = time.perf_counter() - t0
print("[editor_quick_search_check] %d items: first build %.3f s, one list changed %.3f s, cached %.2f ms, a pick through "
      "Blender %.3f s" % (len(big), first, again, cached * 1000, through_rna))
check("300k items build under 1.5 s", first < 1.5, first)
check("one category arriving rebuilds under 0.6 s", again < 0.6, again)
check("Blender's per-key call is cached (< 2 ms)", cached < 0.002, cached)
check("the pick resolved", r == {'FINISHED'} and calls and calls[-1][0] == "start", (r, calls[-1:]))

# --- the editor's own search (in a Fortnite area) over the same items
from fpmp_baseline.operator.fortnite_editor.ui import popups  # noqa: E402
t0 = time.perf_counter()
rows = qs.list_items()
made = time.perf_counter() - t0
check("its rows: one per item, made once", len(rows) == len(qs.search_items()) and qs.list_items() is rows, len(rows))
check("name and category apart", "|" not in rows[-1].get("label", "") and rows[-1].get("detail"), rows[-1])
p = SimpleNamespace(state={})
t0 = time.perf_counter()
some = popups.filtered(p, rows, "prop 0012")
wide = time.perf_counter() - t0
t0 = time.perf_counter()
fewer = popups.filtered(p, rows, "prop 00123")
narrow = time.perf_counter() - t0
check("filters like matches()", fewer == popups.matches(rows, "prop 00123") and len(fewer) < len(some))
check("the same query again: kept", popups.filtered(p, rows, "prop 00123") is fewer)
print("[editor_quick_search_check] editor search: rows %.3f s, first filter %.3f s, narrowed %.3f s" % (made, wide, narrow))
check("rows under 1 s, a filter under 0.5 s, narrowing under 0.1 s", made < 1.0 and wide < 0.5 and narrow < 0.1,
      (made, wide, narrow))

# --- unregister
qs.unregister()
check("unregister removes it", not hasattr(bpy.types, "FP_OT_quick_import") and qs._keymaps == [] and qs._kept == [])
check("and its keymap item", not any(k.idname == "fp.quick_import" for k in km.keymap_items))
bpy.context.window_manager.keyconfigs.remove(kc)

print("[editor_quick_search_check] %d passed, %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
