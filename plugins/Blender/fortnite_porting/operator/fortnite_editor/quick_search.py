"""Quick Import (Ctrl+Shift+F over any editor): a search popup of every browsable asset, by name with its category;
picking one imports it with its default styles the editor's way (importing.start), Ctrl+pick shows it in the editor
instead. The first popup loads every category's list into STATE["tabs"], where the editor's own tabs land too."""
import time
from itertools import chain

import bpy

from . import cosmetics_page, host, importing
from .state import STATE, client, items as shown_items, tag_redraw

KEY = {"type": 'F', "value": 'PRESS', "ctrl": True, "shift": True}      # free in Blender 5.2's default keymap
INFO = "info"           # the one inert entry: FP not ready, or categories still loading
SLOT = 1 << 20          # an item's enum value: its category's slot times SLOT plus its index in that list
ASK_AGAIN = 1.0         # s before an unfinished category is asked again
GIVE_UP = 3             # failed asks before a category is left out

LOAD = {"on": False, "inflight": set(), "failed": {}, "asked": 0.0}
_slots = []             # kinds in the order they got a slot (values stay the same while the popup is open)
_rows = {}              # kind -> (its items list, its sorted rows): rebuilt only when the list changes
_built = {"key": None, "items": [], "lists": {}}
_kept = []              # earlier item lists: Blender reads the strings of a dynamic enum's items without a reference
_icons = {}
_keymaps = []


def _icon(name):
    """An icon's number: Blender looks a name up icon by icon for every item, a number it takes as is."""
    if not _icons:
        _icons.update((i.identifier, i.value)
                      for i in bpy.types.UILayout.bl_rna.functions["label"].parameters["icon"].enum_items)
    return _icons.get(name, 0)


# the categories the editor's TAB_ICONS doesn't name (its menus show them less often than this list does)
MORE_ICONS = {"SideKick": 'COMMUNITY', "Contrail": 'CURVE_PATH', "WeaponMod": 'MODIFIER', "Wildlife": 'OUTLINER_OB_ARMATURE',
              "Sprite": 'IMAGE_PLANE', "Effect": 'PARTICLES', "Animation": 'ACTION',
              "FestivalGuitar": 'SPEAKER', "FestivalBass": 'SPEAKER', "FestivalKeytar": 'SPEAKER', "FestivalDrum": 'SPEAKER',
              "FestivalMic": 'SPEAKER', "FallGuysOutfit": 'USER', "LegoOutfit": 'USER', "LegoEmote": 'ARMATURE_DATA',
              "LegoProp": 'MESH_CUBE', "LegoWildlife": 'OUTLINER_OB_ARMATURE'}


def _kind_icon(kind):
    from . import TAB_ICONS, tab_icon      # the package's, at call time (it imports this module)
    return (kind not in TAB_ICONS and _icon(MORE_ICONS.get(kind))) or _icon(tab_icon(kind))


SKIP = {"Animation", "Effect"}      # 90k+ entries between them, rarely searched by name: their tabs list them


def kinds():
    return [k for cat in STATE["categories"] or () for k in cat["tabs"] if k not in SKIP]


def loading():
    """The categories still on their way (failed ones are left out)."""
    if STATE["categories"] is None:
        return None
    tabs = STATE["tabs"]
    return [k for k in kinds() if not (tabs.get(k) or {}).get("finished") and LOAD["failed"].get(k, 0) < GIVE_UP]


def _slot(kind):
    if kind not in _slots:
        _slots.append(kind)
    return _slots.index(kind) + 1       # 0 is the inert entry's


def _kind_rows(kind, found):
    """(sort key, value, enum item) for a category's listed items, sorted; hidden items left out (FP's default)."""
    cached = _rows.get(kind)
    if cached is not None and cached[0] is found:
        return cached[1]
    hint, icon, base = "|" + cosmetics_page.words(kind), _kind_icon(kind), _slot(kind) * SLOT
    rows = []
    for n, item in enumerate(found):
        if item.get("hidden"):
            continue
        name = (item.get("name") or "").strip() or item["path"].rsplit(".", 1)[-1]
        name = name.replace("|", "/")
        rows.append((name.casefold(), base + n, (str(base + n), name + hint, "", icon, base + n)))
    rows.sort()
    _rows[kind] = (found, rows)
    return rows


def _not_ready(e):
    if e is None:
        return "Starting FP..."
    if e.state == "ready":
        return None
    return e.status_text or {"stopped": "FP is stopped: Start it in the Fortnite editor", "unknown": "Connecting to FP...",
                             "starting": "Starting FP..."}.get(e.state, e.state)


def search_items():
    """The popup's enum items: an inert entry when FP isn't ready or categories are loading, then every listed asset
    by name (its category as the hint). Built again only when a category's list changed; Blender asks every key."""
    e = STATE["engine"]
    waiting = _not_ready(e)
    tabs = STATE["tabs"]
    lists = {k: (tabs.get(k) or {}).get("items") for k in kinds()}
    left = loading()
    key = (waiting, None if left is None else len(left), tuple((k, id(v)) for k, v in lists.items()))
    if key == _built["key"]:
        return _built["items"]
    out = []
    if waiting:
        out.append((INFO, "FP isn't ready: %s" % waiting.replace("|", "/")[:120], "", _icon('ERROR'), 0))
    else:
        if left is None:
            out.append((INFO, "Loading categories...", "", _icon('SORTTIME'), 0))
        elif left:
            names = ", ".join(cosmetics_page.words(k) for k in left[:4]) + (", ..." if len(left) > 4 else "")
            out.append((INFO, "Loading %d categor%s...|%s" % (len(left), "y" if len(left) == 1 else "ies", names),
                        "", _icon('SORTTIME'), 0))
        runs = [_kind_rows(k, v) for k, v in lists.items() if v and (tabs.get(k) or {}).get("finished")]
        merged = sorted(chain.from_iterable(runs))      # sorted runs: timsort merges them
        out += [row[2] for row in merged]
        if not out:
            out.append((INFO, "No assets listed", "", _icon('INFO'), 0))
    _kept.append(_built["items"])
    del _kept[:-3]
    _built.update(key=key, items=out, lists=lists)     # (the lists kept: a freed list's id could come back)
    return out


def entry(identifier):
    """The (kind, item) an enum identifier names; None for the inert entry or a list that changed since."""
    if not identifier or identifier == INFO:
        return None
    value = int(identifier)
    slot, n = divmod(value, SLOT)
    if not 0 < slot <= len(_slots):
        return None
    kind = _slots[slot - 1]
    found = (STATE["tabs"].get(kind) or {}).get("items") or []
    return (kind, found[n]) if n < len(found) else None


# ------------------------------------------------------------------ loading every category, once asked

def _answer(kind):
    def done(result):
        LOAD["inflight"].discard(kind)
        if isinstance(result, Exception):
            LOAD["failed"][kind] = LOAD["failed"].get(kind, 0) + 1
            if LOAD["failed"][kind] == GIVE_UP:
                from . import logbook
                logbook.write("Quick Import: no %s list (%s)" % (cosmetics_page.words(kind), result), "warning")
            return False
        if (STATE["tabs"].get(kind) or {}).get("finished") and not result.get("finished"):
            return False        # the editor's answer came first: an older one never replaces it
        STATE["tabs"][kind] = result
        STATE["items_rev"] += 1
        if not result.get("finished"):
            return False        # nothing drawn changed
        STATE["header_dirty"] = True
    return done


def _categories(result):
    if isinstance(result, Exception):
        return False
    STATE["categories"] = result
    STATE["header_dirty"] = True


def _load_tick():
    if not LOAD["on"]:
        return None
    e = STATE["engine"]
    if e is None or e.state != "ready":
        return 0.5
    now = time.time()
    if STATE["categories"] is None:
        if now - max(LOAD["asked"], STATE["tab_asked"].get("categories", 0)) > 10:
            LOAD["asked"] = STATE["tab_asked"]["categories"] = now
            e.request("browse-categories", _categories)
        return 0.5
    left = loading()
    if not left:
        LOAD["on"] = False
        return None
    for kind in left:
        if kind not in LOAD["inflight"] and now - STATE["tab_asked"].get(kind, 0) > ASK_AGAIN:
            LOAD["inflight"].add(kind)
            STATE["tab_asked"][kind] = now
            e.request("browse", _answer(kind), type=kind)
    return 0.25


def start_loading():
    if STATE["categories"] is None:
        LOAD["failed"].clear()      # a Refresh (or another game version) tries every category again
    LOAD["on"] = True
    if not bpy.app.timers.is_registered(_load_tick):
        bpy.app.timers.register(_load_tick, first_interval=0.0, persistent=True)


# ------------------------------------------------------------------ the operator

def _ctrl_held():
    """Ctrl down as the pick happened (the search popup hands execute no event)."""
    try:
        import ctypes
        return bool(ctypes.windll.user32.GetKeyState(0x11) & 0x8000)
    except (AttributeError, OSError):
        return False


def import_asset(kind, item):
    """Imported with its default styles, the editor's way; queued after an import in flight."""
    if STATE["busy"]:
        importing.start_all([(kind, item, {})])
        return "Queued %s" % item.get("name")
    importing.start(kind, item, {})
    return "Importing %s" % item.get("name")


def _reveal(window, area, path):
    """Scroll the editor's grid to the pick once it has drawn this tab."""
    def run():
        if window not in bpy.context.window_manager.windows[:] or area not in window.screen.areas[:]:
            return None
        if STATE["grids"].get(area.as_pointer()) is None and run.tries < 10:
            run.tries += 1
            area.tag_redraw()
            return 0.1
        from . import browse_ops
        with bpy.context.temp_override(window=window, screen=window.screen, area=area):
            browse_ops.show(bpy.context, path)
        return None
    run.tries = 0
    bpy.app.timers.register(run, first_interval=0.05)


def show_in_editor(context, kind, item):
    """The Assets tab on this item: an editor area of this window (else any), or the FP Studio workspace."""
    wm = context.window_manager
    wm.fp_browse_type = kind
    STATE["picked"] = []
    STATE["hover"].clear()
    wm.fp_browse_selected = item["path"]
    for attr in ("fp_browse_search", "fp_browse_filters"):     # cleared only when they hide it
        if not any(i["path"] == item["path"] for i in shown_items(wm)):
            setattr(wm, attr, "")
    here = context.window.screen.areas[:] if context.window else []
    areas = sorted(host.editor_areas(context), key=lambda a: a not in here)       # this window's first
    if not areas:
        bpy.ops.fp.workspace(name="FP Studio")
        return "Showing %s in FP Studio" % item.get("name")
    area = areas[0]
    window = next(w for w in wm.windows if area in w.screen.areas[:])
    host.set_tab(area.spaces.active, "cosmetics")
    tag_redraw()
    _reveal(window, area, item["path"])
    return "Showing %s" % item.get("name")


def _open_when_known(context, wait=1.0):
    """The popup opened where it was asked once the engine's first check answered (or `wait` s passed)."""
    window, area, region = context.window, context.area, context.region
    until = time.time() + wait

    def run():
        e = STATE["engine"]
        if e is not None and e.state == "unknown" and time.time() < until:
            return 0.02
        if window not in bpy.context.window_manager.windows[:]:
            return None
        live = area is not None and area in window.screen.areas[:]
        where = {"window": window, "screen": window.screen}
        if live:
            where.update(area=area, region=region if region in area.regions[:] else None)
        with bpy.context.temp_override(**where):
            bpy.ops.fp.quick_import('INVOKE_DEFAULT', waited=True)
        return None
    bpy.app.timers.register(run, first_interval=0.02)


class FP_OT_quick_import(bpy.types.Operator):
    """Find any Fortnite asset by name and import it here with its default styles (Ctrl: show it in the Fortnite editor instead)"""
    bl_idname = "fp.quick_import"
    bl_label = "Quick Import Fortnite Asset"
    bl_property = "asset"
    asset: bpy.props.EnumProperty(name="Asset", items=lambda self, context: search_items(), options={'SKIP_SAVE'})
    show_editor: bpy.props.BoolProperty(name="Show in Editor", description="Show the asset in the Fortnite editor instead",
                                        options={'SKIP_SAVE'})
    waited: bpy.props.BoolProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def invoke(self, context, event):
        e = client()
        e.ensure()      # checked again if started; never started from here
        start_loading()
        if e.state == "unknown" and not self.waited:
            _open_when_known(context)       # the first check takes a tick: not "Connecting..." for nothing
            return {'CANCELLED'}
        left = loading() if e.state == "ready" else None
        if left:
            self.report({'INFO'}, "Loading %d categories" % len(left))
        area = editor_area(context)
        if area is None:        # no Fortnite area in this window to draw in: Blender's search
            context.window_manager.invoke_search_popup(self)
            return {'RUNNING_MODAL'}
        open_list(context, area)
        return {'FINISHED'}

    def execute(self, context):
        picked = entry(self.asset)
        if picked is None:
            waiting = _not_ready(STATE["engine"])
            left = loading()
            self.report({'WARNING'}, waiting or ("Still loading %d categories" % len(left) if left else
                                                 "That list changed: search again"))
            return {'CANCELLED'}
        kind, item = picked
        if self.show_editor or _ctrl_held():
            self.report({'INFO'}, show_in_editor(context, kind, item))
        else:
            self.report({'INFO'}, import_asset(kind, item))
        return {'FINISHED'}


def editor_area(context):
    """Where the editor's own search opens: the Fortnite area under the mouse, else this window's biggest one."""
    if context.area is not None and host.is_editor_area(context.area):
        return context.area
    here = context.window.screen.areas[:] if context.window else []
    found = [a for a in here if host.is_editor_area(a)]
    return max(found, key=lambda a: a.width * a.height) if found else None


_listed = {"items": None, "out": []}


def list_items():
    """The search's rows from the enum items (made again only when those were): name, its category the detail; the
    inert entries (FP not ready, loading) as headers."""
    items = search_items()
    if items is _listed["items"]:
        return _listed["out"]
    out = []
    for ident, name, _desc, _icon_id, _n in items:
        label, _, detail = name.partition("|")
        if ident == INFO:
            out.append({"header": label.rstrip(".") + ": " + detail if detail else label})
        else:
            out.append({"label": label, "detail": detail, "id": ident, "action":
                        lambda context, ident=ident: bpy.ops.fp.quick_import('EXEC_DEFAULT', asset=ident)})
    _listed.update(items=items, out=out)
    return out


def open_list(context, area):
    """The editor's own search, at the top of the area's main region."""
    from .ui import blender as ub, popups
    from .ui.rect import Rect
    region = next(r for r in area.regions if r.type == 'WINDOW')
    t = ub.theme(context)
    w = min(560 * t.s, region.width - 4 * t.pad)
    anchor = Rect((region.width - w) / 2, region.height - t.big, w, 0)
    with context.temp_override(area=area, region=region):
        return popups.open_list(bpy.context, anchor, list_items, "quick_import",
                                "Find any Fortnite asset (Enter imports, Ctrl+Enter shows it in the editor)",
                                width=w / t.s, height=480)


def _menu(self, context):
    self.layout.operator(FP_OT_quick_import.bl_idname, text="Fortnite Asset...", icon='VIEWZOOM')


def register_keys(kc):
    km = kc.keymaps.new("Window", space_type='EMPTY', region_type='WINDOW')
    kmi = km.keymap_items.new(FP_OT_quick_import.bl_idname, **KEY)
    _keymaps.append((km, kmi))
    return kmi


def register():
    bpy.utils.register_class(FP_OT_quick_import)
    bpy.types.TOPBAR_MT_file_import.append(_menu)
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc is not None:      # none in background Blender
        register_keys(kc)


def unregister():
    for km, kmi in _keymaps:
        try:
            km.keymap_items.remove(kmi)
        except (ReferenceError, RuntimeError):
            pass
    _keymaps.clear()
    LOAD.update(on=False, asked=0.0)
    LOAD["inflight"].clear()
    LOAD["failed"].clear()
    if bpy.app.timers.is_registered(_load_tick):
        bpy.app.timers.unregister(_load_tick)
    bpy.types.TOPBAR_MT_file_import.remove(_menu)
    bpy.utils.unregister_class(FP_OT_quick_import)
    _rows.clear()
    _built.update(key=None, items=[], lists={})
    _kept.clear()
