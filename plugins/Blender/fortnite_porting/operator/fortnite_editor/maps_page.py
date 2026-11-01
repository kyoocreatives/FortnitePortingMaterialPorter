"""The Map tab's columns in FP's layout: the maps (FP's groups, folding), the open map's info (its card, All / None,
its places, landmarks and data layers, the export pinned at the bottom), and its header (the maps' menu when the list
column folds, Fit, search, export flags, terrain, actor filter, images)."""
import bpy

from . import import_options, tabs
from .maps_tab import MAPS, can_export, map_kinds, on_map, opened, places
from .ui import blender as ui_blender
from .ui import paint
from .ui.rect import Rect

OPEN = {"landmark": False}      # group -> unfolded (all are but the long landmark list, until clicked)
QUEUE_ROWS = 6                  # the export's cells shown at once (the rest scroll)
ORDER = {"exporting": 0, "queued": 1, "failed": 2, "cancelled": 3, "done": 4}       # what's happening on top
IMAGES = (("minimap", "Map"), ("height", "Height"), ("normal", "Normal"), ("weight", "Weights"))


def _op(idname, /, **props):
    def run(context):
        getattr(bpy.ops.fp, idname)(**props)
    return run


def _fold(group):
    def run(context):
        OPEN[group] = not OPEN.get(group, True)
    return run


def _map_name(map_id):
    return next((m["name"] for m in (MAPS["list"] or {}).get("maps") or [] if m["id"] == map_id), map_id)


# ------------------------------------------------------------------ the list column: the maps

def side(ui, r):
    t, wm = ui.t, bpy.context.window_manager
    listed = MAPS["list"] or {}
    maps = listed.get("maps") or []
    if MAPS["list"] is None or listed.get("loading"):
        ui.label(r.cut_top(t.row_pitch).pad(t.pad, 0), "Loading maps (%d)..." % len(maps), t.text3)
    words = wm.fp_map_search.lower().split()
    rows = []
    for kind, title in map_kinds(maps):
        group = [m for m in maps if m["kind"] == kind and all(w in m["name"].lower() for w in words)]
        if group:
            rows.append(("group", kind, title))
            if OPEN.get(kind, True):
                rows += [("map", m, None) for m in group]
    if not rows and maps and words:
        ui.label(r.cut_top(t.row_pitch).pad(t.pad, 0), "No map matches", t.text3)
        return
    offset = ui.scroll("map:list", r, len(rows) * t.row_pitch)
    y = r.top + offset
    text_w = r.w - ui.bar_room - 3 * t.pad - t.icon
    for what, value, title in rows:
        y -= t.row_pitch
        if y > r.top or y + t.row_pitch < r.y:
            continue
        cell = Rect(r.x, y + (t.row_pitch - t.row) / 2, r.w - ui.bar_room, t.row)
        if what == "group":
            ui.group("mapgroup:" + value, cell, title, OPEN.get(value, True), _fold(value))
            continue
        name = value["name"]
        ui.row("map:" + value["id"], cell, name, "mdi_Map" if value["minimap"] else "mdi_Earth",
               value["id"] == wm.fp_map_id, _op("map_open", id=value["id"]),
               tip=name if paint.measure(name, t.text) > text_w else "")
    ui.end_scroll()


# ------------------------------------------------------------------ the info column: the open map

def info(ui, r):
    t, wm = ui.t, bpy.context.window_manager
    got = opened(wm)
    if got is None:
        return
    queue = MAPS["queue"]
    states = sorted(queue.state.items(), key=lambda kv: ORDER.get(kv[1], 9)) if queue.busy and queue.map_id == got["id"] else []
    shown = min(len(states), QUEUE_ROWS)
    export = r.cut_bottom(t.action + (shown * t.row_pitch + t.pad if states else 0))
    _export(ui, export, states, wm)         # the cards' width: one right edge down the column
    r.cut_bottom(t.pad)
    _card(ui, r.cut_top(64 * t.s + 2 * t.pad), got, wm)
    r.cut_top(t.pad)
    _picks(ui, r.cut_top(t.row))
    r.cut_top(t.pad)
    _lists(ui, r, got)


def _card(ui, r, got, wm):
    t = ui.t
    ui.panel(r)
    inner = r.pad(t.pad)
    pic = inner.cut_left(64 * t.s)
    if MAPS["texture"] is not None:
        aspect = got["width"] / max(1, got["height"])
        w, h = (pic.w, pic.w / aspect) if aspect >= 1 else (pic.h * aspect, pic.h)
        paint.rect(Rect(pic.x + (pic.w - w) / 2, pic.y + (pic.h - h) / 2, w, h), (0, 0, 0, 0), t.r_card - t.pad,
                   image=MAPS["texture"])      # concentric with the card round it
    inner.cut_left(t.pad)
    paint.text_in(_map_name(got["id"]), inner.cut_top(t.title + t.gap * 2), t.text1, t.title)
    kind = next((m["kind"] for m in (MAPS["list"] or {}).get("maps") or [] if m["id"] == got["id"]), None)
    title = dict(map_kinds((MAPS["list"] or {}).get("maps") or [])).get(kind)
    if title:
        paint.text_in(title, inner.cut_top(t.text + t.gap * 2), t.text2, t.caption)
    picked = "%d cells, %d picked" % (len(on_map(got)), len(MAPS["selected"]))
    paint.text_in(picked + (" + terrain" if wm.fp_map_main else " + their terrain" if wm.fp_map_tiles else ""),
                  inner.cut_top(t.caption + t.gap * 2), t.text3, t.caption)


def _picks(ui, r):
    t = ui.t
    w = (r.w - t.gap) / 2
    for i, (id, text, op, tip) in enumerate((("map:all", "All", "map_all", "Pick every cell"),
                                            ("map:none", "None", "map_clear", "Pick no cell"))):
        ui.button(id, Rect(r.x + i * (w + t.gap), r.y, w, r.h), text, action=_op(op), tip=tip)


def _lists(ui, r, got):
    t = ui.t
    found = places(got)

    def spots(kind):
        return [(label, "%s:%s" % (kind, label), bool(cells) and set(cells) <= MAPS["selected"],
                 _op("map_poi", name=label), "Pick the cells around it") for label, cells in found[kind]]
    groups = (("poi", "Places", spots("poi")), ("landmark", "Landmarks", spots("landmark")),
              ("layers", "Data Layers", [(layer, "layer:" + layer, layer in MAPS["layers"], _op("map_layer", name=layer),
                                          "Export this data layer's actors too") for layer in got.get("layers") or []]))
    rows = []
    for key, title, members in groups:
        if members:
            rows.append(("group", key, title))
            if OPEN.get(key, True):
                rows += [("row", m, None) for m in members]
    offset = ui.scroll("map:info", r, len(rows) * t.row_pitch)
    y = r.top + offset
    for what, value, title in rows:
        y -= t.row_pitch
        if y > r.top or y + t.row_pitch < r.y:
            continue
        cell = Rect(r.x, y + (t.row_pitch - t.row) / 2, r.w - 4 * t.s, t.row)
        if what == "group":
            ui.group("mapgroup:" + value, cell, title, OPEN.get(value, True), _fold(value))
        else:
            label, id, selected, action, tip = value
            ui.row(id, cell, label, None, selected, action, tip=tip, depth=1)
    ui.end_scroll()


def _export(ui, r, states, wm):
    t = ui.t
    if states:      # the progress is the header's: the rows only
        list_r = r.cut_top(min(len(states), QUEUE_ROWS) * t.row_pitch)
        r.cut_top(t.pad)
        offset = ui.scroll("map:queue", list_r, len(states) * t.row_pitch)
        colors = {"queued": t.text3, "exporting": t.accent, "done": t.ok, "failed": t.error, "cancelled": t.text3}
        y = list_r.top + offset
        for cell, state in states:
            y -= t.row_pitch
            if y > list_r.top or y + t.row_pitch < list_r.y:
                continue
            row = Rect(list_r.x, y, list_r.w - 4 * t.s, t.row_pitch).pad(t.pad, 0)
            dot = row.cut_left(t.icon)      # an icon's slot: the names line up with the lists' icon rows
            row.cut_left(t.pad)
            size = 6 * t.s
            paint.rect(Rect(dot.x + (dot.w - size) / 2, dot.y + (dot.h - size) / 2, size, size), colors[state], size / 2)
            paint.text_in(state.capitalize(), row.cut_right(72 * t.s), t.text3, t.caption, "right")
            paint.text_in("Terrain" if cell == "terrain" else "Cell %s" % cell, row, t.text1, t.text)
        ui.end_scroll()
    gear = r.cut_right(r.h)
    ui.button("map:settings", gear, icon="mdi_FileCog", action=_settings(gear), tip="Export Options")
    r.cut_right(t.gap)
    import_options.button(ui, r.cut_right(r.h), "map:import_options", "map")
    r.cut_right(t.gap)
    if MAPS["queue"].busy:
        ui.button("map:cancel", r, "Cancel", "mdi_Cancel", _op("map_cancel"),
                  "Stop the export now (cells already in Blender stay)")
        return
    ui.button("map:export", r, import_options.import_label(_export_text(len(MAPS["selected"]), wm.fp_map_main, wm.fp_map_tiles), "map"), "mdi_Download", _op("map_export"),
              "Export the picked cells one by one: each lands in Blender as it finishes", enabled=can_export(wm),
              style="accent")


def _export_text(cells, terrain, tiles=False):
    text = "%d cell%s" % (cells, "" if cells == 1 else "s")
    if tiles and cells:
        return "Export " + text + " + terrain under"
    if terrain:
        return "Export terrain + " + text if cells else "Export terrain"
    return "Export " + text


def _settings(anchor):
    """The gear's Export Options, beside it."""
    def run(context):
        from . import settings_tab
        settings_tab.open_settings(context, anchor, "left")
    return run


# ------------------------------------------------------------------ the header's pop-ups

def map_items(listed, current):
    """The maps pop-up's rows: grouped as the list column, the open one (id `current`) marked, a loading row while
    FP lists them (`listed`: MAPS["list"])."""
    listed = listed or {}
    maps = listed.get("maps") or []
    items = []
    if not listed or listed.get("loading"):
        items.append({"label": "Loading maps (%d)..." % len(maps), "icon": "mdi_Refresh", "id": "loading"})
    for kind, title in map_kinds(maps):
        group = [m for m in maps if m["kind"] == kind]
        if group:
            items.append({"header": title})
            items += [{"label": m["name"], "icon": "mdi_Map" if m["minimap"] else "mdi_Earth", "check": m["id"] == current,
                       "action": _op("map_open", id=m["id"]), "id": m["id"]} for m in group]
    return items


def image_items(can):
    """The map images menu: one row per kind; greyed with no map open (or FP busy)."""
    return [{"label": label, "icon": "mdi_Image", "action": _op("map_images", kind=kind), "id": kind, "enabled": can,
             "tip": "" if can else "Open a map first (and wait for FP)"} for kind, label in IMAGES]


def open_maps(context, anchor, side="below"):
    """Open a map (type to search)"""
    from .ui import popups
    wm = context.window_manager
    return popups.open_list(context, anchor, lambda: map_items(MAPS["list"], wm.fp_map_id), "map:list", "Search maps",
                            width=300, height=420, side=side)


def open_images(context, anchor, side="below"):
    """Bring the map's images into Blender (minimap, or the landscape's height, normal or weight maps)"""
    from .state import STATE
    from .ui import popups
    wm = context.window_manager
    return popups.open_menu(context, anchor, lambda: image_items(opened(wm) is not None and not STATE["busy"]),
                            "map:images", "Map Images", side=side)


OPENERS = {"map:list": open_maps, "map:images": open_images}


# ------------------------------------------------------------------ the header


def _folded(context):
    """(side, info): the columns the area is too narrow for (the header stands in for them)."""
    area = context.area
    region = next((r for r in area.regions if r.type == 'WINDOW'), None)
    if region is None:
        return False, False
    cols = ui_blender.columns(area, region, tabs.current(area.spaces.active), context)
    return cols["side"] is None, cols["info"] is None


def header(layout, context):
    wm = context.window_manager
    side_folded, info_folded = _folded(context)
    name = _map_name(wm.fp_map_id) if wm.fp_map_id else ("Map" if not side_folded else "No map")
    if side_folded:     # the list column as a pop-up; the header search filters the column only
        layout.operator("fp.popup_open", text=name, icon='WORLD').name = "map:list"
    else:
        layout.label(text=name, icon='WORLD')
    sub = layout.row()
    sub.enabled = opened(wm) is not None
    sub.operator("fp.map_fit", text="", icon='ZOOM_ALL')
    if not side_folded:
        row = layout.row(align=True)
        row.ui_units_x = 8 if info_folded else 12
        row.prop(wm, "fp_map_search", text="", icon='VIEWZOOM')
    if info_folded:      # the info column's Export, first: a narrow header cuts its end
        if MAPS["queue"].busy:
            layout.operator("fp.map_cancel", text="Cancel", icon='CANCEL')
        else:
            layout.operator("fp.map_export", text="Export", icon='EXPORT')
    layout.row(align=True).prop(wm, "fp_map_flags", text="")      # icon toggles: each item's tooltip names it
    layout.prop(wm, "fp_map_main", text="", icon='WORLD_DATA')
    layout.prop(wm, "fp_map_tiles", text="", icon='MESH_GRID')
    sub = layout.row()
    sub.ui_units_x = 7
    sub.prop(wm, "fp_map_filter", text="", icon='FILTER')
    layout.operator("fp.popup_open", text="", icon='IMAGE_DATA').name = "map:images"


def has_info(context):
    """The info column shows the open map: none open (or still loading), it folds."""
    return opened(context.window_manager) is not None


def register():
    ui_blender.OPENERS.update(OPENERS)


def unregister():
    for name in OPENERS:
        ui_blender.OPENERS.pop(name, None)
