"""The editor's pure parts (operator/fortnite_editor: layout, colors, style picks), headless.

    blender -b --factory-startup --python-exit-code 1 -P editor_layout_check.py -- <plugin parent>
"""
import sys

sys.path.insert(0, sys.argv[sys.argv.index("--") + 1])
from fpmp_baseline.operator.fortnite_editor import colors, importing, layout  # noqa: E402

FAILS, PASSES = [], [0]


def check(name, ok, detail=""):
    if ok:
        PASSES[0] += 1
    else:
        FAILS.append(name)
        print("[editor_layout_check] FAIL %s %s" % (name, detail))


g = layout.grid(600, 800, 96, 4, 100, 0)
check("columns at 600", g.columns == 5, g.columns)
check("columns at 1200", layout.grid(1200, 800, 96, 4, 100, 0).columns == 11)
check("rows", g.rows == 20, g.rows)
x, y, w, h = g.rect(7)
check("tile size", (w, h) == (96, g.tile_h), (w, h))
check("hit inside tile 7", g.hit(x + w // 2, y + h // 2) == 7, g.hit(x + w // 2, y + h // 2))
check("hit in a gap is None", g.hit(x - 2, y + h // 2) is None)
check("hit past the last item is None", layout.grid(600, 800, 96, 4, 3, 0).hit(*layout.grid(600, 800, 96, 4, 3, 0).rect(4)[:2]) is None)
first = g.rect(0)
check("first tile at the top", first[1] + first[3] <= 800 and first[1] + first[3] >= 800 - 10, first)
check("max scroll", g.max_scroll == max(0, g.rows * (g.tile_h + 4) + 4 - 800), g.max_scroll)
check("scroll clamps", layout.grid(600, 800, 96, 4, 100, 10 ** 9).scroll == g.max_scroll)
big = layout.grid(1200, 800, 96, 4, 3500, 20000)
rows_on_screen = 800 // (big.tile_h + 4) + 1
check("visible range only", len(big.visible) <= big.columns * (rows_on_screen + 2) and big.visible.start > 0, big.visible)
check("visible items are on screen", all(big.rect(i)[1] < 800 and big.rect(i)[1] + big.rect(i)[3] > 0 for i in big.visible))
check("empty grid", layout.grid(600, 800, 96, 4, 0, 0).visible == range(0, 0))

top, bottom, accent = colors.tile("Legendary", None)
check("legendary is orange", top[0] > 0.6 and top[0] > top[1] > top[2], top)
check("rgba", len(top) == 4 and len(bottom) == 4 and len(accent) == 4)
check("bottom darker", sum(bottom[:3]) < sum(top[:3]), (top, bottom))
s_top, s_bottom, s_accent = colors.tile("Legendary", ["#FF0000", "#00FF00", "#0000FF"])
check("series colours win", s_top[:3] == (1.0, 0.0, 0.0) and s_bottom[:3] == (0.0, 0.0, 1.0), (s_top, s_bottom))
check("unknown rarity falls back", colors.tile("Whatever", None) == colors.tile("Uncommon", None))

check("style picks query", importing.styles_query({"Style": "Two", "Color": "Red"}) == "Style:Two;Color:Red",
      importing.styles_query({"Style": "Two", "Color": "Red"}))
check("no picks", importing.styles_query({}) == "")

import bpy  # noqa: E402
from fpmp_baseline.operator.fortnite_editor import grid_ops, settings_tab, state, textures, tool_tabs  # noqa: E402
from fpmp_baseline.operator.fortnite_editor import tabs as tabs_mod  # noqa: E402

# --- an icon that failed waits before it's asked again (no request storm)
state.icon_failed("icon/a", 100.0)
check("failed icon not asked at once", not state.icon_wanted("icon/a", 110.0))
check("failed icon asked after the backoff", state.icon_wanted("icon/a", 131.0))
check("other icons asked", state.icon_wanted("icon/b", 110.0))

# --- each area hit-tests against its own grid
ga, gb = layout.grid(600, 800, 96, 4, 3, 0), layout.grid(600, 800, 96, 4, 3, 0)
state.STATE["grids"] = {1: (ga, [{"path": "A0"}, {"path": "A1"}, {"path": "A2"}]), 2: (gb, [{"path": "B0"}, {"path": "B1"}, {"path": "B2"}])}
x, y, w, h = ga.rect(1)
check("area 1 hits its own item", (grid_ops.hit_item(1, x + 5, y + 5) or {}).get("path") == "A1", grid_ops.hit_item(1, x + 5, y + 5))
check("area 2 hits its own item", (grid_ops.hit_item(2, x + 5, y + 5) or {}).get("path") == "B1")
check("unknown area hits nothing", grid_ops.hit_item(3, x + 5, y + 5) is None)

# --- setting changes: one request in flight per setting, the latest value wins
sent = []
sender = settings_tab.Sender(lambda name, value, done: sent.append((name, value, done)))
sender.send("Cavity", "0.30")
sender.send("Cavity", "0.31")
sender.send("Cavity", "0.32")
check("one request in flight", [(n, v) for n, v, _ in sent] == [("Cavity", "0.30")], sent)
sent[0][2]({"value": 0.30})
check("then only the latest", [(n, v) for n, v, _ in sent] == [("Cavity", "0.30"), ("Cavity", "0.32")], sent)
sent[1][2]({"value": 0.32})
check("nothing more", len(sent) == 2)

# --- caches dropped when another file loads (their images are freed with it)
textures.install()
img = bpy.data.images.new(".fp_icon_test", 4, 4)
textures._cache["x"] = (img, None)
bpy.ops.wm.read_homefile(use_empty=True)
check("icon cache cleared on file load", not textures._cache, list(textures._cache))
textures.uninstall()

# --- the tool tabs' delayed registration survives a file load (Blender started with a .blend)
tool_tabs.register()
bpy.ops.wm.read_homefile(use_empty=True)
check("tool tabs timer survives a file load", bpy.app.timers.is_registered(tool_tabs._make))
tool_tabs.unregister()


# --- a tool tab's copy leaves the viewport's panel whole (a subclass of a registered panel took its Python class: the
# viewport panel drew empty)
class FPMP_PT_tab_test(bpy.types.Panel):
    bl_label, bl_idname, bl_space_type, bl_region_type, bl_category = "T", "FPMP_PT_tab_test", 'VIEW_3D', 'UI', "Fortnite Porting"

    def _helper(self):
        return 1

    def draw(self, context):
        self._helper()


class FPMP_PT_tab_test_sub(bpy.types.Panel):
    bl_label, bl_idname, bl_space_type, bl_region_type = "S", "FPMP_PT_tab_test_sub", 'VIEW_3D', 'UI'
    bl_parent_id = "FPMP_PT_tab_test"

    def draw(self, context):
        pass


bpy.utils.register_class(FPMP_PT_tab_test)
saved = tool_tabs.PANELS
tool_tabs.PANELS = (("FPMP_PT_tab_test", {"rig"}),)
tool_tabs._make()
copy = getattr(bpy.types, "FPMP_PT_tab_test_fortnite", None)
check("viewport panel keeps its class", getattr(bpy.types, "FPMP_PT_tab_test", None) is FPMP_PT_tab_test, getattr(bpy.types, "FPMP_PT_tab_test", None))
check("the tab's copy has the panel's code", copy is not None and copy.bl_space_type == 'NODE_EDITOR' and hasattr(copy, "_helper"), copy)
check("the copies share one sidebar category", copy is not None and copy.bl_category == tabs_mod.SIDEBAR, copy and copy.bl_category)
tool_tabs.unregister()
tool_tabs.PANELS = saved
bpy.utils.unregister_class(FPMP_PT_tab_test)

# --- the sidebar follows the main tab: rig panels on Rig, scene tools on the other tabs, never outside the editor
from types import SimpleNamespace  # noqa: E402
from fpmp_baseline.operator.fortnite_editor import tabs as tabs_mod  # noqa: E402
for tab_id in ("cosmetics", "rig", "landscape"):
    if tab_id not in tabs_mod.ids():
        tabs_mod.register_tab(tabs_mod.Tab(tab_id, tab_id, 'NONE'))


from fpmp_baseline.operator.fortnite_editor import host  # noqa: E402


def editor_space(tab_id):
    """A node editor showing the Fortnite editor's tree, its tab `tab_id`."""
    return SimpleNamespace(type='NODE_EDITOR', tree_type=host.TREE, node_tree=SimpleNamespace(bl_idname=host.TREE, tab=tab_id))


def on(tab_id, where):
    return tool_tabs.shown(SimpleNamespace(space_data=editor_space(tab_id)), where)


check("rig panels on the Rig tab only", on("rig", {"rig"}) and not on("cosmetics", {"rig"}) and not on("landscape", {"rig"}))
rest = {tool_tabs.REST}
check("scene tools on the other tabs", on("cosmetics", rest) and not on("rig", rest) and not on("landscape", rest))
check("the rest plus a named tab", on("landscape", {tool_tabs.REST, "landscape"}) and on("cosmetics", {tool_tabs.REST, "landscape"})
      and not on("rig", {tool_tabs.REST, "landscape"}))
check("shell fur on the Landscape tab too", dict(tool_tabs.PANELS)["FPMP_PT_shell_fur"] == {tool_tabs.REST, "landscape"})
check("not outside the editor", not tool_tabs.shown(SimpleNamespace(space_data=SimpleNamespace(type='VIEW_3D')), rest))
check("not in another node editor", not tool_tabs.shown(SimpleNamespace(space_data=SimpleNamespace(
    type='NODE_EDITOR', tree_type="ShaderNodeTree", node_tree=None)), rest))
tool_tabs.PANELS = (("FPMP_PT_tab_test_sub", {"rig"}),)       # its parent isn't listed
bpy.utils.register_class(FPMP_PT_tab_test)
bpy.utils.register_class(FPMP_PT_tab_test_sub)
tool_tabs._make()
check("a sub-panel whose parent is left out is left out", getattr(bpy.types, "FPMP_PT_tab_test_sub_fortnite", None) is None)
tool_tabs.unregister()
tool_tabs.FLAT = tool_tabs.FLAT | {"FPMP_PT_tab_test"}
tool_tabs._make()
flat = getattr(bpy.types, "FPMP_PT_tab_test_sub_fortnite", None)
check("under a flattened parent it stands on its own", flat is not None and not getattr(flat, "bl_parent_id", ""), flat)
tool_tabs.FLAT = tool_tabs.FLAT - {"FPMP_PT_tab_test"}
tool_tabs.unregister()
tool_tabs.PANELS = saved
bpy.utils.unregister_class(FPMP_PT_tab_test_sub)
bpy.utils.unregister_class(FPMP_PT_tab_test)

# --- the host: registering twice is harmless, other add-ons' header items survive it, Blender's panels come back
header = bpy.types.NODE_HT_header
funcs = header._dyn_ui_initialize()
original = funcs[0]


def other_addon(self, context):
    pass


header.append(other_addon)
tools_poll = getattr(bpy.types.NODE_PT_active_tool, "poll", None)
host.register()
host.register()         # a reload that skipped unregister
funcs = header.draw._draw_funcs
check("host: the header hook once", funcs.count(host._header) == 1 and funcs[0] is host._header, funcs)
check("host: another add-on's header item kept", other_addon in funcs)
check("host: Blender's panels refused for our tree", getattr(bpy.types.NODE_PT_active_tool.poll, "_fp_refuse", False))
header.remove(other_addon)      # the other add-on goes while ours is on
host.unregister()
funcs = header.draw._draw_funcs
check("host: Blender's header back", funcs[0] is original and host._header not in funcs and other_addon not in funcs, funcs)
check("host: Blender's panels back", not getattr(getattr(bpy.types.NODE_PT_active_tool, "poll", None), "_fp_refuse", False))
host.unregister()       # twice: harmless

# --- the tab registry: order kept, each area's tab by id, empty or unknown ids fall back to the first
from fpmp_baseline.operator.fortnite_editor import tabs  # noqa: E402
saved = list(tabs._TABS)
tabs._TABS.clear()
tabs.register_tab(tabs.Tab("a", "A", 'NONE'))
tabs.register_tab(tabs.Tab("b", "B", 'NONE'))
tabs.register_tab(tabs.Tab("a", "A2", 'NONE'))      # same id: replaced in place


def Space(tab):
    return editor_space(tab)


check("tab order", tabs.ids() == ["a", "b"], tabs.ids())
check("replaced in place", tabs.current(Space("a")).label == "A2")
check("empty tab is the first", tabs.current(Space("")).id == "a")
check("unknown tab falls back", tabs.current(Space("gone")).id == "a")
check("named tab", tabs.current(Space("b")).id == "b")
tabs._TABS[:] = saved

# --- the Files tab's list rows and its search (debounced, the latest query wins)
from fpmp_baseline.operator.fortnite_editor import files_tab, rows  # noqa: E402
r = rows.Rows(400, 200, 20, 100, 0)
check("rows visible", r.visible.start == 0 and len(r.visible) <= 12, r.visible)
check("row rect", r.rect(0) == (0, 180, 400, 20), r.rect(0))
check("row hit", r.hit(10, 175) == 1 and r.hit(10, 199) == 0, (r.hit(10, 175), r.hit(10, 199)))
check("row hit past the end", rows.Rows(400, 200, 20, 3, 0).hit(10, 50) is None)
check("rows scroll clamps", rows.Rows(400, 200, 20, 100, 10 ** 6).scroll == 100 * 20 - 200)
shifted = rows.Rows(400, 200, 20, 100, 0, 56)
check("rows start past an overlapping toolbar", shifted.rect(0)[0] == 56 and shifted.hit(60, 190) == 0 and shifted.hit(40, 190) is None)
g0, g56 = layout.grid(600, 800, 96, 4, 100, 0), layout.grid(600, 800, 96, 4, 100, 0, 56)
check("grid starts past an overlapping toolbar", g56.rect(3)[0] == g0.rect(3)[0] + 56
      and g56.hit(g56.rect(3)[0] + 5, g56.rect(3)[1] + 5) == 3)
R = SimpleNamespace
main = R(type='WINDOW', x=0, y=0, width=1000, height=600)
area_ = R(regions=[main, R(type='TOOLS', x=0, y=0, width=56, height=600), R(type='UI', x=720, y=0, width=280, height=600)])
check("inset: toolbar and sidebar over the main region", host.inset(area_, main) == (56, 280), host.inset(area_, main))
apart = R(regions=[R(type='WINDOW', x=56, y=0, width=664, height=600), R(type='TOOLS', x=0, y=0, width=56, height=600),
                   R(type='UI', x=720, y=0, width=280, height=600)])
check("inset: nothing when they sit beside it", host.inset(apart, apart.regions[0]) == (0, 0), host.inset(apart, apart.regions[0]))
hidden = R(regions=[main, R(type='UI', x=999, y=0, width=1, height=600)])
check("inset: a hidden sidebar covers nothing", host.inset(hidden, main) == (0, 0))
s_ = files_tab.Search(delay=0.25)
s_.ask("bar", "", 10.0)
check("not due while typing", s_.due(10.1) is None)
s_.ask("barrel", "", 10.2)
first = s_.due(10.5)
check("due after the pause, the last text", first is not None and first[1] == "barrel", first)
check("asked once", s_.due(10.6) is None)
s_.ask("barrel sm", "Mesh", 11.0)
second = s_.due(11.3)
check("old answer dropped", not s_.accept(first[0], {"items": []}))
check("latest answer kept", s_.accept(second[0], {"items": [1]}) and s_.result == {"items": [1]})
s_.ask("", "", 20.0)
check("an empty search sends nothing", s_.due(21.0) is None)
check("and clears the results", s_.result is None)
s_ = files_tab.Search(delay=0.25)
s_.ask("barrel", "", 30.0)
sent = s_.due(30.5)
check("in flight until answered", s_.waiting)
check("a loading answer is not kept", not s_.accept(sent[0], {"loading": True}, 31.0) and s_.result is None)
check("still waiting while the app loads", s_.waiting)
again = s_.due(32.1)
check("asked again after a second", again is not None and again[1] == "barrel" and s_.due(32.2) is None, again)
s_.accept(again[0], {"items": [1]}, 32.5)
check("answered: not in flight", not s_.waiting)
files_tab.FILES.update(folder=None, folder_asked="FortniteGame", folder_retry=0.0)
files_tab.got_folder("FortniteGame", {"loading": True}, 40.0)
check("folder: a loading answer asks again later",
      files_tab.FILES["folder_asked"] is None and files_tab.FILES["folder_retry"] > 40.0 and files_tab.FILES["folder"] is None)
files_tab.FILES.update(folder_asked="X")
files_tab.got_folder("X", RuntimeError("no folder X"), 41.0)
check("folder: an error says so, not asked again", files_tab.FILES["folder_asked"] == "X"
      and "no folder X" in (files_tab.FILES["folder_error"] or ""))
files_tab.reset()
check("reset forgets folder and search", files_tab.FILES["folder_asked"] is None and files_tab.FILES["search"].result is None)

# --- the Map tab's view: picture pixels (y down) <-> region (y up), zoom about the mouse, cell hits
from fpmp_baseline.operator.fortnite_editor import map_view  # noqa: E402


def near(a, b, eps=1e-3):
    return all(abs(x - y) < eps for x, y in zip(a, b))


v = map_view.MapView(400, 300, 2048, 2048)
check("map fits, centred", near(v.to_region(1024, 1024), (200, 150)) and abs(v.scale - 300 / 2048 * 0.95) < 1e-6, v.scale)
check("picture top is up", v.to_region(1024, 0)[1] > 150)
check("region <-> picture", near(v.to_image(*v.to_region(300, 700)), (300, 700)))
p = v.to_image(100, 80)
v.zoom_at(100, 80, 5)
check("zoom keeps the point under the mouse", near(v.to_image(100, 80), p) and v.scale > 300 / 2048)
cells = [{"id": 0, "x": 1000, "y": 1000, "size": 50}, {"id": 1, "x": 1050, "y": 1000, "size": 50},
         {"id": 2, "x": 1000, "y": 1300, "size": 50}]
v = map_view.MapView(400, 300, 2048, 2048)
v.zoom_at(*v.to_region(1050, 1000), 12)
check("hit at high zoom", v.cell_at(cells, *v.to_region(1070, 1010)) == 1 and v.cell_at(cells, *v.to_region(1010, 990)) == 0)
check("no hit off the cells", v.cell_at(cells, *v.to_region(1500, 1500)) is None)
v = map_view.MapView(400, 300, 2048, 2048)
a, b = v.to_region(1100, 1100), v.to_region(900, 900)
check("box picks the cells inside, any corner order", sorted(v.cells_in(cells, *b, *a)) == [0, 1] == sorted(v.cells_in(cells, *a, *b)))
check("cells near a point", sorted(map_view.cells_near(cells, 1000, 1000, 60)) == [0, 1])
x0 = v.to_region(0, 0)
v.pan(15, -10)
check("pan moves the picture", near(v.to_region(0, 0), (x0[0] + 15, x0[1] - 10)))
v.resize(800, 600)
check("resize keeps zoom and pan about the new centre", near(v.to_region(1024, 1024), (400 + 15, 300 - 10)))
late = map_view.Latest()
late.ask("A")
late.ask("B")
check("latest map wins", not late.accept("A") and late.accept("B"))

# --- the Map tab's export queue: one cell at a time, progress, cancel keeps what landed
from fpmp_baseline.operator.fortnite_editor import maps_export  # noqa: E402
q = maps_export.Queue()
q.start("M", [3, 5, 9])
first = q.next()
check("queue: one cell at a time", first == 3 and q.next() is None and q.state[3] == "exporting", q.state)
check("queue: progress", q.progress() == "Exporting cell 1 / 3", q.progress())
q.finished(3, True)
check("queue: next after the first lands", q.next() == 5 and q.state[3] == "done")
q.cancel()
check("queue: cancel stops the rest", q.next() is None and 9 not in q.state and q.state[5] == "exporting" and q.busy)
q.finished(5, None)
check("queue: the cell in flight ends cancelled, done ones stay", q.state == {3: "done", 5: "cancelled"} and not q.busy, q.state)
q.start("M", [1])
q.finished(q.next(), False)
check("queue: a failed cell says so", q.state[1] == "failed" and not q.busy)
q.start("M", [1, 2, 3], {"flags": "Landscape", "layers": "A"})
q.next()
check("queue: export settings kept from Export", q.options == {"flags": "Landscape", "layers": "A"})
q.cancel()
check("queue: says it is cancelling", q.progress() == "Cancelling...", q.progress())

q.start("M", [], {"main": True})
check("queue: Include Terrain alone is one step", q.busy and q.next() == maps_export.TERRAIN and q.progress() == "Exporting terrain", q.progress())
q.finished(maps_export.TERRAIN, True)
q.start("M", [4], {"main": True})
check("queue: terrain first, then the cells", q.next() == maps_export.TERRAIN and q.total == 2)

# --- the Map tab's engine traffic: nothing until a Map tab is shown, one request in flight per route
from fpmp_baseline.operator.fortnite_editor import maps_tab  # noqa: E402


class FakeEngine:
    state, sent = "ready", []

    def request(self, route, done, **query):
        self.sent.append((route, done))


fake = FakeEngine()
maps_tab.client = lambda: fake
if not hasattr(bpy.types.WindowManager, "fp_map_id"):     # the editor isn't registered in this headless run
    bpy.types.WindowManager.fp_map_id = bpy.props.StringProperty()
bpy.context.window_manager.fp_map_id = ""
maps_tab.MAPS.update(list=None, list_at=0.0, open=None, open_at=0.0)
maps_tab.tick()
check("map tab: no maps asked while no Map tab shows", fake.sent == [], fake.sent)
bpy.context.window_manager.fp_map_id = "X"
maps_tab.tick()
asked = [r for r, _ in fake.sent]
maps_tab.MAPS.update(list_at=0.0, open_at=0.0)
maps_tab.tick()
check("map tab: asks once the map is picked", sorted(asked) == ["maps-list", "maps-open"], asked)
check("map tab: not again while the answers are out", [r for r, _ in fake.sent] == asked, [r for r, _ in fake.sent])
dict(fake.sent)["maps-open"](RuntimeError("no map X"))
check("map tab: an error answer can be asked again", maps_tab.MAPS["open"].get("error") and not maps_tab.MAPS["open_pending"])
bpy.context.window_manager.fp_map_id = ""

# --- quiet answers: an answer that changes nothing (the app still loading) doesn't redraw the editor
from fpmp_baseline.operator.fortnite_editor import engine as eng  # noqa: E402
e_ = eng.Engine("http://127.0.0.1:9", launcher=lambda *a, **k: None)
e_._done.put(("answer", (lambda result: False, {})))
check("engine: a callback answering False asks no redraw", e_.pump() is False)
e_._done.put(("answer", (lambda result: None, {})))
check("engine: other answers redraw", e_.pump() is True)
maps_tab.MAPS.update(list={"loading": True, "maps": []}, list_pending=True)
check("map list: the same answer again is quiet", maps_tab._got_list({"loading": True, "maps": []}) is False)
check("map list: a new map is kept", maps_tab._got_list({"loading": True, "maps": [{"id": "a"}]}) is False
      and maps_tab.MAPS["list"]["maps"] == [{"id": "a"}])
files_tab.FILES.update(folder_asked="F")
check("folder: a loading answer is quiet", files_tab.got_folder("F", {"loading": True}, 50.0) is False)

# --- the Rig tab: body picker (front view, finger grids beside the hands) and face board (knobs move along their own axes)
from fpmp_baseline.operator.fortnite_editor import rig_layout as rl  # noqa: E402
C = rl.Control
body = [C("root_ctrl", "C", "root", "", 0.0, 0.0), C("head_ctrl", "C", "body", "", 0.0, 1.7),
        C("hand_l_ik_ctrl", "L", "limb", "arm_l", 0.7, 1.4), C("hand_r_ik_ctrl", "R", "limb", "arm_r", -0.7, 1.4),
        C("foot_l_ik_ctrl", "L", "limb", "leg_l", 0.1, 0.1),
        # fingers come in rig_source's hand grid: x the finger's column, z its joint row
        C("index_01_l_ctrl", "L", "finger", "fingers_l", 1.0, 1.0), C("index_02_l_ctrl", "L", "finger", "fingers_l", 1.0, 2.0),
        C("index_01_r_ctrl", "R", "finger", "fingers_r", 3.0, 1.0)]
pk = rl.Picker(body, 600, 600)
hx, hy, hr = pk.place["head_ctrl"]
rx, ry, _ = pk.place["root_ctrl"]
check("picker: head above root, centred", hy > ry and abs(hx - rx) < 1e-6 and abs(hx - 300) < 1)
(lx, ly, _), (qx, qy, _) = pk.place["hand_l_ik_ctrl"], pk.place["hand_r_ik_ctrl"]
check("picker: the character's left is screen right", lx > hx > qx)
fl, fr = pk.place["index_01_l_ctrl"], pk.place["index_01_r_ctrl"]
check("picker: fingers beside their hand, outboard", fl[0] > lx and fr[0] < qx and abs(fl[1] - ly) < 60 and abs(fr[1] - qy) < 60,
      (fl, fr, lx, qx))
check("picker: fingertips under the knuckles", pk.place["index_02_l_ctrl"][1] < fl[1] - 15)
check("picker: aspect kept", abs((lx - hx) / (hy - ry) - 0.7 / 1.7) < 0.02)
check("picker: hit", pk.hit(hx + 2, hy - 2) == "head_ctrl" and pk.hit(5, 595) is None)
boxed = set(pk.in_box(lx + 1, hy + 1, qx - 1, ry - 1))
check("picker: box (any corner order) takes the body, not the finger grids",
      boxed == {"root_ctrl", "head_ctrl", "hand_l_ik_ctrl", "hand_r_ik_ctrl", "foot_l_ik_ctrl"}, boxed)
narrow = rl.Picker(body, 400, 600)
check("picker: narrow, the grids go under the hands",
      narrow.place["index_01_l_ctrl"][1] < narrow.place["hand_l_ik_ctrl"][1] - 10, narrow.place["index_01_l_ctrl"])
gh = rl.Picker(body, 600, 600, hidden={"head_ctrl"})
check("picker: the inactive chain stays put, not clickable",
      gh.place["head_ctrl"] == pk.place["head_ctrl"] and gh.hit(hx, hy) != "head_ctrl" and "head_ctrl" not in gh.in_box(0, 0, 600, 600))
check("picker: control kinds", [rl.kind_of(c) for c in (C("hand_l_ik_ctrl", "L", "limb", "", 0, 0), C("upperarm_l_fk_ctrl", "L", "limb", "", 0, 0),
                                                        C("knee_l_pv_ctrl", "L", "limb", "", 0, 0), C("body_ctrl", "C", "body", "", 0, 0),
                                                        C("root_ctrl", "C", "root", "", 0, 0), C("index_01_l_ctrl", "L", "finger", "", 0, 0))]
      == ["ik", "fk", "pole", "cog", "root", "finger"])
stack = [C("global_ctrl", "C", "root", "", 0.0, 0.0), C("root_ctrl", "C", "root", "", 0.0, 0.0),
         C("body_ctrl", "C", "body", "", 0.0, 1.0), C("head_ctrl", "C", "body", "", 0.0, 1.7)]
ps = rl.Picker(stack, 400, 600)
(gx, gy, gr), (qx, qy, _) = ps.place["global_ctrl"], ps.place["root_ctrl"]
(qr) = ps.place["root_ctrl"][2]
check("picker: root and global on one spot: nested rings, centred", (gx, gy) == (qx, qy) and gr - qr >= rl.RING_STEP - 1e-6,
      (ps.place["global_ctrl"], ps.place["root_ctrl"]))
check("picker: each ring clicked on its band", ps.hit(gx + gr - 1, gy) == "global_ctrl" and ps.hit(qx + qr - 1, qy) == "root_ctrl")
cog = rl.Picker([C("body_ctrl", "C", "body", "", 0.0, 1.0), C("pelvis_ctrl", "C", "body", "", 0.0, 1.0),
                 C("head_ctrl", "C", "body", "", 0.0, 1.7)], 400, 600)
(bx, by, br), (px_, py_, pr) = cog.place["body_ctrl"], cog.place["pelvis_ctrl"]
check("picker: the COG a ring round the pelvis, both centred, both clickable",
      (bx, by) == (px_, py_) and br > pr and cog.hit(px_, py_) == "pelvis_ctrl" and cog.hit(bx + br - 1, by) == "body_ctrl",
      cog.place)
two = rl.Picker([C("hand_l_fk_ctrl", "L", "limb", "", 0.7, 1.4), C("hand_l_extra_ctrl", "L", "limb", "", 0.7, 1.4),
                 C("head_ctrl", "C", "body", "", 0.0, 1.7)], 400, 600)
(ax, _, ar), (ex, _, _) = two.place["hand_l_fk_ctrl"], two.place["hand_l_extra_ctrl"]
check("picker: solid controls on one spot side by side", abs(ax - ex) >= 2 * ar - 1e-6)
pair = [C("head_fk_ctrl", "C", "body", "", 0.0, 1.7), C("head_ik_ctrl", "C", "body", "", 0.0, 1.7), C("root_ctrl", "C", "root", "", 0.0, 0.0)]
fk = rl.Picker(pair, 400, 600, hidden={"head_ik_ctrl"})
ik = rl.Picker(pair, 400, 600, hidden={"head_fk_ctrl"})
check("picker: an FK/IK pair on one spot: the live one centred, either way", fk.place["head_fk_ctrl"][0] == fk.place["root_ctrl"][0]
      and ik.place["head_ik_ctrl"][0] == ik.place["root_ctrl"][0] and fk.place["head_fk_ctrl"] == ik.place["head_ik_ctrl"],
      (fk.place, ik.place))
A = rl.Axis
board = rl.Board("metahuman", [rl.Knob("MB_CTRL_C_jaw", "jaw", 0.0, 0.0, (A(0, 1.0, 0.0, -1.0, 1.0), A(2, 0.0, 1.0, -1.0, 0.0))),
                               rl.Knob("MB_CTRL_L_brow_down", "brow", 2.0, 3.0, (A(2, 0.0, 1.0, 0.0, 1.0),))], None)
fb = rl.FaceBoard(board, 300, 300)
jx, jy = fb.rest["MB_CTRL_C_jaw"]
up = fb.values_at("MB_CTRL_L_brow_down", *fb.rest["MB_CTRL_L_brow_down"][:1], fb.rest["MB_CTRL_L_brow_down"][1] + 10000)
check("face: a slider clamps to its range", up == {2: 1.0}, up)
down = fb.values_at("MB_CTRL_L_brow_down", 0, -10000)
check("face: and to its low end, ignoring the other axis", down == {2: 0.0}, down)
half = fb.values_at("MB_CTRL_C_jaw", jx + fb.scale * 0.5, jy - fb.scale * 0.25)
check("face: a box maps both axes", abs(half[0] - 0.5) < 1e-6 and abs(half[2] + 0.25) < 1e-6, half)
check("face: knob drawn where its values put it", near(fb.knob_at("MB_CTRL_C_jaw", {0: 0.5, 2: -0.25}), (jx + fb.scale * 0.5, jy - fb.scale * 0.25)))
check("face: hit", fb.hit(jx, jy, {}) == "MB_CTRL_C_jaw" and fb.hit(jx + 1000, jy, {}) is None)
check("face: mirror twins", (rl.mirror_of("MB_CTRL_L_brow_down"), rl.mirror_of("MB_CTRL_R_eye_blink"),
                             rl.mirror_of("CR_Face_BrowL"), rl.mirror_of("MB_CTRL_C_jaw"))
      == ("MB_CTRL_R_brow_down", "MB_CTRL_L_eye_blink", "CR_Face_BrowR", None))

# --- the Rig tab's panes zoom and pan on their own (a PaneView each), dots keep their size
pv = rl.PaneView()
pk1 = rl.Picker(body, 600, 600)
hx1, hy1, hr1 = pk1.place["head_ctrl"]
pv.zoom_at(hx1, hy1, 300, 300, 4)
pk2 = rl.Picker(body, 600, 600, view=pv)
hx2, hy2, hr2 = pk2.place["head_ctrl"]
check("pane: zoom keeps the point under the mouse", near((hx2, hy2), (hx1, hy1)) and hr2 == hr1)
check("pane: zoom spreads the controls", abs(pk2.place["hand_l_ik_ctrl"][0] - hx2) > 1.9 * abs(pk1.place["hand_l_ik_ctrl"][0] - hx1))
pv.pan(30, -20)
pk3 = rl.Picker(body, 600, 600, view=pv)
check("pane: pan moves everything", near(pk3.place["head_ctrl"][:2], (hx2 + 30, hy2 - 20)) and pk3.hit(hx2 + 30, hy2 - 20) == "head_ctrl")
lim = rl.PaneView()
lim.zoom_at(10, 10, 200, 300, 200)
big = lim.zoom
lim.zoom_at(10, 10, 200, 300, -400)
check("pane: zoom has limits", big <= rl.PaneView.MAX and lim.zoom >= rl.PaneView.MIN, (big, lim.zoom))
fv = rl.PaneView()
fb2 = rl.FaceBoard(board, 300, 300)
j0 = fb2.rest["MB_CTRL_C_jaw"]
fv.zoom_at(j0[0], j0[1], 150, 150, 3)
fb2.set_view(fv)
check("face: zoom about a knob keeps it put", near(fb2.rest["MB_CTRL_C_jaw"], j0))
check("face: zoomed, a pixel moves the knob less", abs(fb2.values_at("MB_CTRL_C_jaw", j0[0] + 10, j0[1])[0] - 10 / fb2.scale) < 1e-9
      and fb2.scale > fb.scale * 1.5)
fb2.set_view(rl.PaneView())
check("face: back to the fit", near(fb2.rest["MB_CTRL_C_jaw"], fb.rest["MB_CTRL_C_jaw"]))
check("face: snapping a value to tenths of its range", rl.snap(0.437, -1.0, 1.0) == 0.4 and rl.snap(-0.96, -1.0, 1.0) == -1.0)

# --- flipbook / LEGO / toon boards (bone sliders): whole frames, labelled "Mouth 3 / 15"; pads stay smooth
fbb = rl.Board("bones", [rl.Knob("CR_Face_Mouth", "", 0.0, 0.0, (A(0, 1.0, 0.0, -0.5, 3.0),)),
                         rl.Knob("CR_Face_Mouth_UV", "", 0.0, -1.0, (A(0, 1.0, 0.0, -0.25, 0.25), A(1, 0.0, 1.0, -0.25, 0.25)))],
               None, 0.25)
ff = rl.FaceBoard(fbb, 400, 200)
mx, my = ff.rest["CR_Face_Mouth"]
v = ff.values_at("CR_Face_Mouth", mx + ff.scale * 0.6, my)
check("bones board: a slider lands on whole frames", abs(v[0] - 0.5) < 1e-9, v)
pv_ = ff.values_at("CR_Face_Mouth_UV", ff.rest["CR_Face_Mouth_UV"][0] + ff.scale * 0.13, ff.rest["CR_Face_Mouth_UV"][1])
check("bones board: a pad stays smooth", abs(pv_[0] - 0.13) < 1e-9, pv_)
check("bones board: slider label", rl.knob_label(fbb, fbb.knobs[0], {0: 0.5}) == "Mouth 5 / 15", rl.knob_label(fbb, fbb.knobs[0], {0: 0.5}))
check("bones board: pad label", rl.knob_label(fbb, fbb.knobs[1], {}) == "Mouth pad")
check("metahuman knobs keep their names", rl.knob_label(board, board.knobs[0], {}) == "CTRL_C_jaw")

# --- the rig picker's leftovers: finger parts, the palm row, the board's two heads, panes split by content
check("finger parts", [rl.finger_part(n) for n in ("index_02_l_ctrl", "thumb_metacarpal_r_ctrl", "index_curl_l_ctrl", "hand_l_ctrl")]
      == [("index", 2), ("thumb", 0), ("index", None), (None, None)])
palm = rl.Picker([C("hand_l_ik_ctrl", "L", "limb", "", 0.7, 1.4), C("fingers_curl_l_ctrl", "L", "finger", "", 2.0, -1.2),
                  C("index_01_l_ctrl", "L", "finger", "", 1.0, 1.0)], 600, 600)
check("picker: the palm row", palm.palm == {"fingers_curl_l_ctrl"}
      and palm.place["fingers_curl_l_ctrl"][1] > palm.place["index_01_l_ctrl"][1])
heads = rl.Board("metahuman", (rl.Knob("MB_jaw", "Jaw", 0.0, 0.0, (A(0, 1.0, 0.0, -1.0, 1.0),)),
                               rl.Knob("MB_tweak", "Tweaker", 10.0, 0.0, (A(0, 1.0, 0.0, -1.0, 1.0),))),
                 [(0, 0), (1, 0), (0, 1), (10, 0), (11, 0), (10, 1)])
check("board: two heads", rl.has_tweakers(heads) and not rl.has_tweakers(board))
fp, tp = rl.board_part(heads, "face"), rl.board_part(heads, "tweakers")
check("board: each head its knobs and its side of the outline",
      [k.bone for k in fp.knobs] == ["MB_jaw"] and fp.outline == [(0, 0), (1, 0), (0, 1)]
      and [k.bone for k in tp.knobs] == ["MB_tweak"] and tp.outline == [(10, 0), (11, 0), (10, 1)], (fp, tp))
check("board: all of it", rl.board_part(heads, "all") is heads and rl.board_part(board, "face") is board)
check("split: by content, each at least 35%", rl.split(1000, 300, 900) == 350 and abs(rl.split(1000, 500, 500) - 500) < 1e-9
      and rl.split(1000, 600, 400) == 600)

print("[editor_layout_check] %d passed, %d failed" % (PASSES[0], len(FAILS)))
if FAILS:
    sys.exit(1)
