"""The editor's Map tab: the engine's maps-* traffic and the view (the picked map's picture with its cells, drawn in the
shell's well: click, paint, box, places pick cells). Its list and info columns are maps_page; cells export one by one
and land here as each finishes."""
import os
import time
import uuid

import bpy
import gpu
from gpu_extras.batch import batch_for_shader

from . import host, import_options, importing, map_view, maps_export, tabs
from .grid_draw import _fill, _image, _shader, _text
from .state import STATE, client, tag_redraw
from .ui import blender as ui_blender
from .ui import paint
from .ui.rect import Rect

FLAGS = (("Actors", "Actors", 'OBJECT_DATA'), ("Landscape", "Landscape", 'MESH_GRID'),
         ("InstancedFoliage", "Foliage", 'OUTLINER_OB_POINTCLOUD'), ("Lights", "Lights", 'LIGHT'),
         ("Decals", "Decals", 'IMAGE_DATA'), ("Effects", "Effects", 'PARTICLES'), ("HLODs", "HLODs", 'MOD_DECIM'),
         ("Hidden", "Hidden", 'HIDE_OFF'))
DEFAULT_FLAGS = {"Actors", "Landscape", "InstancedFoliage", "Lights", "Decals", "Effects"}      # as the Map page
KIND_NAMES = (("br", "Battle Royale"), ("feature", "Game Features"))


def map_kinds(maps):
    """(kind, group title) in list order; a kind the app adds gets its own group after these."""
    known = {kind for kind, _title in KIND_NAMES}
    return list(KIND_NAMES) + [(k, k.capitalize() + "s") for k in sorted({m["kind"] for m in maps} - known)]



# the map's own: bright enough over the picture; red is failed only, as in the queue
CELL_COLORS = {"selected": (0.1, 0.8, 1.0), "queued": (0.85, 0.85, 0.85), "exporting": (1.0, 0.75, 0.0),
               "done": (0.25, 1.0, 0.4), "failed": (0.9, 0.2, 0.15), "cancelled": (0.6, 0.6, 0.6)}
FILL_ALPHA, EDGE_ALPHA = 0.38, 1.0       # a picked cell must read over blue water too: solid edge, tinted inside
POI_RADIUS = {"poi": 1.6, "landmark": 1.0}      # in cells: the data gives a point, not an extent

MAPS = {"list": None, "list_at": 0.0, "list_pending": False, "open": None, "open_at": 0.0, "open_pending": False,
        "latest": map_view.Latest(), "views": {}, "selected": set(), "queue": maps_export.Queue(), "run": "",
        "hover": {}, "box": {}, "layers": set(), "image": None, "texture": None, "texture_key": None, "places": None}


def reset():
    """Forget the maps (Refresh, after FP loaded another game version); an export running is cancelled."""
    if MAPS["queue"].busy:
        MAPS["queue"].cancel()
    MAPS.update(list=None, list_at=0.0, open=None, open_at=0.0, views={}, selected=set(), layers=set(), places=None)


def opened(wm):
    """The picked map's answer once loaded, else None."""
    got = MAPS["open"]
    return got if got and not got.get("loading") and got.get("id") == wm.fp_map_id else None


def cell_size(got):
    sizes = sorted(c["size"] for c in got["cells"])
    return sizes[len(sizes) // 2] if sizes else 1


def poi_cells(got, poi):
    return map_view.cells_near(got["cells"], poi["x"], poi["y"], POI_RADIUS.get(poi["kind"], 1.0) * cell_size(got))


def places(got):
    """{kind: [(label, cells)]} by name; landmarks sharing a name are told apart ("Override Console 2"), west to east."""
    cached = MAPS["places"]
    if cached is not None and cached[0] is got:
        return cached[1]
    out = {}
    pois = sorted(got.get("pois") or [], key=lambda p: (p["name"].lower(), p["x"], p["y"]))
    for kind in ("poi", "landmark"):
        seen, rows = {}, []
        for p in (p for p in pois if p["kind"] == kind):
            seen[p["name"]] = seen.get(p["name"], 0) + 1
            label = p["name"] if seen[p["name"]] == 1 else "%s %d" % (p["name"], seen[p["name"]])
            rows.append((label, poi_cells(got, p)))
        out[kind] = rows
    MAPS["places"] = (got, out)
    return out


# ------------------------------------------------------------------ engine traffic (the editor's timer)

def _map_shown():
    return any(host.is_editor_area(area) and host.tab_id(area.spaces.active) == "map"
               for window in bpy.context.window_manager.windows for area in window.screen.areas)


def tick():
    e, wm, now = client(), bpy.context.window_manager, time.time()
    if e.state != "ready" or not (wm.fp_map_id or MAPS["queue"].busy or _map_shown()):
        return      # the app loads every map when first asked: not before the Map tab is used
    if (MAPS["list"] is None or MAPS["list"].get("loading")) and now >= MAPS["list_at"] and not MAPS["list_pending"]:
        MAPS["list_at"], MAPS["list_pending"] = now + 2.0, True
        e.request("maps-list", _got_list)
    map_id = wm.fp_map_id
    got = MAPS["open"]
    if (map_id and (got is None or got.get("id") != map_id or got.get("loading")) and now >= MAPS["open_at"]
            and not MAPS["open_pending"]):
        MAPS["open_at"], MAPS["open_pending"] = now + 1.5, True
        MAPS["latest"].ask(map_id)
        e.request("maps-open", lambda result, k=map_id: _got_map(k, result), id=map_id)
    _send_next(e)


def _got_list(result):
    MAPS["list_pending"] = False
    if isinstance(result, Exception) or result == MAPS["list"]:
        return False        # asked every 2 s while the app loads its maps: redraw only when one came in
    MAPS["list"] = result
    for window in bpy.context.window_manager.windows:      # the Map tab's list column shows it, not the header
        for area in window.screen.areas:
            if host.is_editor_area(area) and host.tab_id(area.spaces.active) == "map":
                for region in area.regions:
                    if region.type == 'WINDOW':
                        region.tag_redraw()
    return False


def _got_map(map_id, result):
    MAPS["open_pending"] = False
    if not MAPS["latest"].accept(map_id):
        return
    if isinstance(result, Exception):
        MAPS["open"] = {"id": map_id, "error": "%s" % result}
        if "no map" in str(result):
            MAPS["list"] = None     # the app read its maps again (another game version): ask for them again
    else:
        answer = dict(result, id=map_id)
        if result.get("loading") and answer == MAPS["open"]:
            return False
        MAPS["open"] = answer
        if not result.get("loading"):
            try:
                _load_picture(result["image"])
            except (RuntimeError, OSError) as e:
                MAPS["open"] = {"id": map_id, "error": "The map's picture couldn't be read: %s" % e}
    tag_redraw()


def _load_picture(path):
    key = (path, os.path.getmtime(path) if os.path.isfile(path) else 0)
    if key == MAPS["texture_key"]:
        return
    _free_picture()
    image = bpy.data.images.load(path, check_existing=False)
    image.name = ".fp_map"
    image.colorspace_settings.name = 'Non-Color'      # drawn as stored, like the grid's icons
    MAPS["image"], MAPS["texture"], MAPS["texture_key"] = image, gpu.texture.from_image(image), key


def _free_picture():
    image = MAPS["image"]
    MAPS["image"] = MAPS["texture"] = MAPS["texture_key"] = None
    if image is not None:
        try:
            bpy.data.images.remove(image)
        except ReferenceError:
            pass


def can_export(wm):
    """Export's poll: the terrain or a cell to send, the map loaded, nothing else exporting."""
    return bool((MAPS["selected"] or wm.fp_map_main) and opened(wm) is not None and not MAPS["queue"].busy
                and not STATE["busy"])


def export_options(wm):
    """The export settings as Export is pressed; every cell of that export uses them."""
    MAPS["run"] = uuid.uuid4().hex[:12]     # unique across Blenders: Cancel stops this export, no other client's
    return {"flags": ",".join(sorted(wm.fp_map_flags)), "filter": wm.fp_map_filter.strip(),
            "layers": ";".join(sorted(MAPS["layers"])), "main": wm.fp_map_main, "run": MAPS["run"],
            "settings": import_options.query(wm, "map")}


def _send_next(e):
    queue = MAPS["queue"]
    cell = queue.next()
    if cell is None:
        return
    STATE["busy"] = queue.progress()
    options = queue.options
    terrain = cell == maps_export.TERRAIN
    query = {"id": queue.map_id, "cells": "" if terrain else str(cell), "flags": options.get("flags", ""),
             "filter": options.get("filter", ""), "layers": options.get("layers", ""), "run": options.get("run", "0"),
             "settings": options.get("settings", "")}
    if terrain:
        query["main"] = "1"
    e.request("maps-export", lambda result, c=cell: _cell_done(c, result), timeout=1800, **query)
    tag_redraw()


def _cell_done(cell, result):
    queue = MAPS["queue"]
    if isinstance(result, dict) and result.get("cancelled"):
        queue.finished(cell, None)
    else:
        error = importing.land("cell %s" % cell, result)
        importing.undo_step()
        queue.finished(cell, error is None or error.startswith("Skipped"))
        if error:
            STATE["error"] = error
    STATE["busy"] = queue.progress() or None
    tag_redraw()


# ------------------------------------------------------------------ the view (the shell's well)

def well(area, region):
    return ui_blender.well(area, region, tabs.current(area.spaces.active))


def view_for(area, region, got):
    """The area's view of the map, fitted to the well when new (Fit forgets it)."""
    key, w = area.as_pointer(), well(area, region)
    view = MAPS["views"].get(key)
    if view is None or getattr(view, "map_id", None) != got["id"] or (view.image_w, view.image_h) != (got["width"], got["height"]):
        view = map_view.MapView(w.w, w.h, got["width"], got["height"], w.x, w.y)
        view.map_id = got["id"]
        MAPS["views"][key] = view
    elif (view.x, view.y, view.width, view.height) != (w.x, w.y, w.w, w.h):
        view.resize(w.w, w.h, w.x, w.y)
    return view


def draw():
    context = bpy.context
    region, wm, area = context.region, context.window_manager, context.area
    t = ui_blender.theme(context)
    paint.rect(Rect(0, 0, region.width, region.height), t.well)
    got = MAPS["open"]
    if not wm.fp_map_id or not opened(wm) or MAPS["texture"] is None:
        text = "No map open" if not wm.fp_map_id else             (got or {}).get("error") or (got or {}).get("status") or "Loading the map..."
        paint.text_in(text, well(area, region), t.text2, t.title, "center")
        return
    view = view_for(area, region, got)
    x0, y0 = view.to_region(0, got["height"])
    x1, y1 = view.to_region(got["width"], 0)
    gpu.state.blend_set('ALPHA')
    _image(x0, y0, x1 - x0, y1 - y0, MAPS["texture"])
    _draw_cells(view, got, region, area.as_pointer())
    _draw_pois(view, got, region, t.s)
    box = MAPS["box"].get(area.as_pointer())
    if box:
        (bx0, by0), (bx1, by1) = box
        _fill(min(bx0, bx1), min(by0, by1), abs(bx1 - bx0), abs(by1 - by0), (0.0, 0.75, 1.0, 0.12))


def on_map(got):
    """The cells the map picture covers: the ones drawn, clicked and picked by All (a level can reach past it)."""
    w, h = got["width"], got["height"]
    return [c for c in got["cells"] if 0 <= c["x"] <= w and 0 <= c["y"] <= h]


def _draw_cells(view, got, region, key):
    queue = MAPS["queue"]
    states = queue.state if queue.map_id == got["id"] else {}
    selected, hover = MAPS["selected"], MAPS["hover"].get(key)
    fills, fill_colors, lines, edges, edge_colors, hot = [], [], [], [], [], None
    for cell in on_map(got):
        cx, cy = view.to_region(cell["x"], cell["y"])
        half = cell["size"] * view.scale / 2
        if cx + half < 0 or cy + half < 0 or cx - half > region.width or cy - half > region.height:
            continue
        a, b, c, d = (cx - half, cy - half), (cx + half, cy - half), (cx + half, cy + half), (cx - half, cy + half)
        lines += [a, b, b, c, c, d, d, a]
        state = states.get(cell["id"]) or ("selected" if cell["id"] in selected else None)
        if state:
            fills += [a, b, c, a, c, d]
            fill_colors += [CELL_COLORS[state] + (FILL_ALPHA,)] * 6
            inset = 1.0
            ia, ib, ic, id_ = (a[0] + inset, a[1] + inset), (b[0] - inset, b[1] + inset), (c[0] - inset, c[1] - inset), (d[0] + inset, d[1] - inset)
            edges += [ia, ib, ib, ic, ic, id_, id_, ia]
            edge_colors += [CELL_COLORS[state] + (EDGE_ALPHA,)] * 8
        if cell["id"] == hover:
            hot = (a, b, c, d)
    if fills:
        shader = _shader('SMOOTH_COLOR')
        batch_for_shader(shader, 'TRIS', {"pos": fills, "color": fill_colors}).draw(shader)
    shader = _shader('UNIFORM_COLOR')
    if lines:
        shader.uniform_float("color", (1, 1, 1, 0.22))
        batch_for_shader(shader, 'LINES', {"pos": lines}).draw(shader)
    if edges:
        gpu.state.line_width_set(2.0)
        smooth = _shader('SMOOTH_COLOR')
        batch_for_shader(smooth, 'LINES', {"pos": edges, "color": edge_colors}).draw(smooth)
        gpu.state.line_width_set(1.0)
    if hot:
        gpu.state.line_width_set(2.0)
        shader.uniform_float("color", (1, 1, 1, 0.9))
        batch_for_shader(shader, 'LINE_LOOP', {"pos": hot}).draw(shader)
        gpu.state.line_width_set(1.0)


def _draw_pois(view, got, region, scale):
    show_landmarks = view.scale > view.fit * 1.8
    for poi in got.get("pois") or []:
        named = poi["kind"] == "poi"
        if not named and not show_landmarks:
            continue
        x, y = view.to_region(poi["x"], poi["y"])
        if not (0 <= x <= region.width and 0 <= y <= region.height):
            continue
        r = (4 if named else 3) * scale
        _fill(x - r, y - r, 2 * r, 2 * r, (1, 0.85, 0.2, 1) if named else (1, 1, 1, 0.85))
        size = (12 if named else 10) * scale
        _text(poi["name"], x + r + 3 * scale + 1, y - size / 3 - 1, (0, 0, 0, 0.8), size)
        _text(poi["name"], x + r + 3 * scale, y - size / 3, (1, 1, 1, 1) if named else (0.85, 0.85, 0.85, 1), size)


def register():
    wm = bpy.types.WindowManager
    wm.fp_map_id = bpy.props.StringProperty()
    wm.fp_map_search = bpy.props.StringProperty(name="Search Maps", options={'TEXTEDIT_UPDATE'},
                                                update=lambda self, context: tag_redraw())
    wm.fp_map_flags = bpy.props.EnumProperty(name="Export", options={'ENUM_FLAG'}, default=DEFAULT_FLAGS,
                                             items=[(k, label, "Export the cells' %s" % label.lower(), icon, 1 << i)
                                                    for i, (k, label, icon) in enumerate(FLAGS)])
    wm.fp_map_filter = bpy.props.StringProperty(name="Actor Filter", description="Only actors whose name holds this (empty: all)")
    wm.fp_map_main = bpy.props.BoolProperty(name="Include Terrain", update=lambda self, context: tag_redraw(),
                                            description="The map's own level (its terrain), exported first, also with no cell picked")
    from . import maps_ops, maps_page
    maps_page.register()
    tabs.register_tab(tabs.Tab("map", "Map", 'WORLD', header=maps_page.header, draw=draw, tick=tick, nav_icon="mdi_Map",
                               side=maps_page.side, info=maps_page.info, hover=maps_ops.hover,
                               has_info=maps_page.has_info))


def unregister():
    from . import maps_page
    tabs.unregister_tab("map")
    _free_picture()
    maps_page.unregister()
    for p in ("fp_map_id", "fp_map_search", "fp_map_flags", "fp_map_filter", "fp_map_main"):
        delattr(bpy.types.WindowManager, p)
