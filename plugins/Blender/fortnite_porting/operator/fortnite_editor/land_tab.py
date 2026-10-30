"""The editor's Landscape tab: a landscape group's pieces as one picture seen from above (height shading tinted by
the layers) and a brush that paints, erases or smooths a layer across pieces, weights kept summed to 1. Works on the
scene alone: the group of the active landscape piece, or the one picked in the header."""
import time

import bpy
import gpu
import numpy as np
from bpy.app.handlers import persistent
from gpu_extras.batch import batch_for_shader

from . import host, land_ground, land_material, land_scatter, land_scatter_info, land_select, land_store, land_view, tabs
from .grid_draw import _image, _shader
from .ui import blender as ui_blender
from .ui import paint
from .ui.rect import Rect

MAX_PICTURE = 1024      # picture pixels a side: bigger grids draw every n-th cell

# group: shown group name; layers / biomes: its paint layers' and biome masks' land_store (None until read, biomes
# None without a painted biome map: biome_note says why); store: the one the brush and the picture work on (shade,
# colors, step: its picture's); views: {area: land_view.Frame}; readout: {area: text}; hover: {area: (x, y)}; image:
# the picture as a hidden image
LAND = {"group": None, "store": None, "layers": None, "biomes": None, "biome_note": None, "store_key": None,
        "shade": None, "colors": {}, "hidden": set(), "dirty": True, "image": None, "step": 1, "views": {},
        "readout": {}, "hover": {}, "message": "", "load_at": 0.0, "groups": None, "blend_override": {},
        "biome_make": None}


def scene_groups(scene):
    """The scene's landscape groups, kept until objects or collections change (a redraw shouldn't scan them all)."""
    if LAND["groups"] is None or LAND["groups"][0] != scene.name:
        LAND["groups"] = (scene.name, land_store.groups(scene))
    return LAND["groups"][1]


def wanted_group(context):
    """The group to show: the active piece's, else the one picked, else the scene's first."""
    found = scene_groups(context.scene)
    active = context.view_layer.objects.active
    for name, pieces in found.items():
        if active in pieces:
            return name, pieces
    if LAND["group"] in found:
        return LAND["group"], found[LAND["group"]]
    if found:
        name = sorted(found)[0]
        return name, found[name]
    return None, []


def ensure_store(context):
    """The shown group's store, read again when the group or its pieces changed."""
    name, pieces = wanted_group(context)
    key = (name, tuple(_reads(o) for o in pieces))
    if key != LAND["store_key"]:
        LAND.update(group=name, store=None, layers=None, biomes=None, biome_note=None, store_key=key, dirty=True,
                    message="")
        if pieces:
            LAND["load_at"] = time.time()       # read on the next tick: the first draw says so first
    return LAND["store"]


def _reads(obj):
    """What the tab reads of a piece: a rebuild bringing in a layer (a feature: snow on BiomeC), a biome map made
    paintable or removed, a new material or a re-import changes it, and the group is read again."""
    mat = obj.active_material
    return (obj.name, len(obj.data.vertices), mat.name if mat is not None else "",
            tuple(sorted(dict(obj.get("mp_layers") or {}).items())), tuple(sorted(dict(obj.get("mp_weightmaps") or {}).items())),
            tuple(sorted(dict(obj.get("mp_biome_maps") or {}).items())))


def tick():
    _load()
    land_scatter.tick()


def _load():
    if LAND["store"] is None and LAND["load_at"] and time.time() >= LAND["load_at"]:
        LAND["load_at"] = 0.0
        name, pieces = wanted_group(bpy.context)
        if pieces:
            if LAND.pop("undone", False):
                land_store.reload_images(pieces)
            store, error = land_store.try_load(pieces)
            if store is None:       # said in the tab; the editor's timer goes on
                LAND["message"] = error
                redraw_all()
                return
            for layer, on in LAND["blend_override"].get(name, {}).items():
                if layer in store.grid.layers:
                    store.grid.blended[store.grid.index(layer)] = on
            biomes, why = land_store.try_biomes(pieces, store)
            for s in (store, biomes):
                if s is not None:
                    g = s.grid
                    s.shade = land_view.hillshade(g.height, g.cell, g.filled, land_view.source(g))
                    s.colors = land_view.palette(g.layers)
                    s.step = max(1, int(np.ceil(max(g.shape) / MAX_PICTURE)))
            LAND.update(layers=store, biomes=biomes, biome_note=why if biomes is None else None,
                        hidden={n for s in (store, biomes) if s is not None for n in s.grid.layers
                                if pieces[0].get(s.hide_key(n))})
            if biomes is None and why is None:
                LAND["biome_note"] = game_biomes(pieces)
            LAND["biome_make"] = land_material.unpainted_biomes(pieces) if biomes is None and why is None else None
            wm = bpy.context.window_manager
            if biomes is not None and wm.fp_land_layer in biomes.grid.layers:
                activate(biomes)
            else:
                activate(store)
                if wm.fp_land_layer not in store.grid.layers and store.grid.layers:
                    wm.fp_land_layer = store.grid.layers[0]
            redraw_all()


def game_biomes(pieces):
    """Why the biomes can't be painted when the material reads the game's biome map as is (a map import), else
    None."""
    mat, _ = land_material.material(pieces, None)
    node = land_material.root(mat) if mat is not None else None
    if node is None or any(o.get("mp_weight_set") for o in pieces[:1]):
        return None
    reads = [s.name for s in node.inputs if s.bl_idname == "NodeSocketClosure" and "biome" in s.name.lower()
             and "colo" not in s.name.lower()]
    if not reads:
        return None
    return ("The game's biome map (%s): an imported map reads it as the game has it. Biomes are paintable on a "
            "custom landscape (the landscape material put on a mesh, then Biome Maps > Make Paintable)" % reads[0])


def activate(store):
    """The brush and the picture on this store (the layers, the biomes, Scatter mode's); each view keeps showing the
    same ground (from the grid it was last on: after an undo there's no old store, the views are kept)."""
    old = LAND["store"]
    if store is old:
        return
    for frame in LAND["views"].values():
        was = getattr(frame, "ground", None) or (old.grid if old is not None else None)
        if was is not None and _ground(was) != _ground(store.grid):
            land_view.retarget(frame, was, store.grid)
        frame.ground = _ground(store.grid)
    LAND.update(store=store, shade=store.shade, colors=store.colors, step=store.step, dirty=True, rgba=None)


def _ground(grid):
    """What places a grid on the ground (a view's frame maps its cells)."""
    from types import SimpleNamespace
    return SimpleNamespace(origin=tuple(grid.origin), cell=float(grid.cell), shape=tuple(grid.shape))


def redraw_all():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if host.is_editor_area(area) and host.tab_id(area.spaces.active) == "landscape":
                area.tag_redraw()


def refresh(objects=()):
    """Edited objects shown now: tagged for the depsgraph (a Python write to a modifier's inputs tags nothing), the 3D
    viewports and the tab redrawn (a depsgraph tag alone redraws no viewport)."""
    for obj in objects:
        obj.update_tag()
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()
    redraw_all()


def refresh_picture(box=None):
    """The picture image redrawn from the grid; `box` (grid cells r0, c0, r1, c1): only that part made again."""
    store, wm = LAND["store"], bpy.context.window_manager
    shown = set(store.grid.layers) - LAND["hidden"]
    only = wm.fp_land_layer if wm.fp_land_layer_only and wm.fp_land_layer in store.grid.layers else None
    step = LAND["step"]
    rgba = LAND.get("rgba")
    own = getattr(store, "picture", None)      # a store drawing its own picture (Scatter mode's)

    def make(window=None):
        if own is not None:
            return own(LAND["shade"], step, window)
        return land_view.compose(store.grid, LAND["colors"], shown, only, LAND["shade"], step, window)
    if box is not None and rgba is not None and LAND["image"] is not None:
        r0, c0 = box[0] // step, box[1] // step
        r1, c1 = min(-(-box[2] // step), rgba.shape[0]), min(-(-box[3] // step), rgba.shape[1])
        rgba[r0:r1, c0:c1] = make((r0, c0, r1, c1))
    else:
        rgba = LAND["rgba"] = make()
    h, w = rgba.shape[:2]
    image = LAND["image"]
    try:
        stale = image is None or tuple(image.size) != (w, h)
    except ReferenceError:      # removed under us (an undo past its making): made again
        image, stale = None, True
    if stale:
        if image is not None:
            bpy.data.images.remove(image)
        image = bpy.data.images.new(".fp_landscape", w, h, alpha=True, float_buffer=True)
        image.colorspace_settings.name = 'Non-Color'
        LAND["image"] = image
    image.pixels.foreach_set(rgba.ravel())
    image.update()
    LAND["dirty"] = False


def well_of(area, region):
    return ui_blender.well(area, region, tabs.current(area.spaces.active))


def frame_for(area, region):
    """The area's view of the picture, fitted to the shell's well (again when the well changes, unless zoomed or
    panned: then it keeps its centre)."""
    grid = LAND["store"].grid
    key = area.as_pointer()
    frame = LAND["views"].get(key)
    well = well_of(area, region)
    box = (well.x, well.y, well.w, well.h)
    if frame is not None and frame.moved and frame.box != box:
        frame.follow(*box)
    elif frame is None or (frame.cols, frame.rows) != (grid.shape[1], grid.shape[0]) or frame.box != box:
        frame = land_view.Frame(grid.shape[1], grid.shape[0], well.w, well.h, ui_blender.theme().pad, well.x, well.y)
        frame.ground = _ground(grid)
        LAND["views"][key] = frame
    return frame


def world_at(frame, x, y):
    """World XY under a region point (cell centres sit at half a pixel)."""
    col, row = frame.to_cell(x, y)
    return LAND["store"].grid.to_world(col - 0.5, row - 0.5)


def readout(x, y):
    """The height and the layer weights at a world point, as one line."""
    if hasattr(LAND["store"], "readout"):
        return LAND["store"].readout(x, y)
    grid = LAND["store"].grid
    col, row = (int(round(v)) for v in grid.to_cell(x, y))
    if not (0 <= row < grid.shape[0] and 0 <= col < grid.shape[1]) or not grid.filled[row, col]:
        return ""
    parts = ["%.1f m" % grid.height[row, col]]
    weights = sorted(((grid.W[i, row, col], name) for i, name in enumerate(grid.layers) if grid.avail[i, row, col]), reverse=True)
    parts += ["%s %.2f" % (name, w) for w, name in weights if w >= 0.005] or ["unpainted"]
    return "   ".join(parts)


def _circle(x, y, r, color, segments=48):
    import math
    pts = []
    for i in range(segments):
        a0, a1 = 2 * math.pi * i / segments, 2 * math.pi * (i + 1) / segments
        pts += [(x + r * math.cos(a0), y + r * math.sin(a0)), (x + r * math.cos(a1), y + r * math.sin(a1))]
    shader = _shader('UNIFORM_COLOR')
    shader.uniform_float("color", color)
    batch_for_shader(shader, 'LINES', {"pos": pts}).draw(shader)


def draw():
    context = bpy.context
    region, area, wm = context.region, context.area, context.window_manager
    t = ui_blender.theme(context)
    well = well_of(area, region)
    gpu.state.blend_set('ALPHA')
    paint.rect(Rect(0, 0, region.width, region.height), t.well)
    store = ensure_store(context)
    if store is None:
        text = LAND["message"] or ("Reading %s..." % LAND["group"] if LAND["group"] else "No landscape in the scene")
        paint.text_in(text, well, t.text2, t.title, "center", t.big)
        return
    if LAND["dirty"] or LAND["image"] is None:
        refresh_picture()
    frame = frame_for(area, region)
    grid = store.grid
    step = LAND["step"]
    h, w = LAND["image"].size[1], LAND["image"].size[0]
    x0, y0 = frame.to_region(0, 0)
    x1, y1 = frame.to_region(w * step, h * step)
    _image(x0, y0, x1 - x0, y1 - y0, gpu.texture.from_image(LAND["image"]))
    if store is land_scatter.SCATTER["store"]:
        land_scatter.draw_over(frame)
    key = area.as_pointer()
    hover = LAND["hover"].get(key)
    if hover:
        r = wm.fp_land_radius / grid.cell * frame.scale
        _circle(hover[0], hover[1], r, (1, 1, 1, 0.9))
        _circle(hover[0], hover[1], r * (1.0 - wm.fp_land_falloff), (1, 1, 1, 0.35))
    text = LAND["readout"].get(key) or ""
    if LAND["message"]:
        text = (text + "      " if text else "") + LAND["message"]
    if text:        # a chip in the well's corner: radius 4 + padding 6 = the well's 10
        chip = Rect(well.x + t.pad, well.y + t.pad, min(paint.measure(text, t.text) + 2 * t.pad, well.w - 2 * t.pad), t.row)
        paint.rect(chip, t.surface[:3] + (0.9,), t.r_small)
        paint.text_in(text, chip, t.text1, t.text, "left", t.pad)


def hover(context, x, y):
    """The brush circle and the readout follow the mouse over the picture (x None: it left); True: redraw."""
    key = context.area.as_pointer()
    frame = LAND["views"].get(key)
    if x is None or LAND["store"] is None or frame is None:
        gone = [LAND[k].pop(key, None) for k in ("hover", "readout")]
        return any(v is not None for v in gone)
    LAND["hover"][key] = (x, y)
    LAND["readout"][key] = readout(*world_at(frame, x, y))
    return True


# ------------------------------------------------------------------ the list column and the header

def _run(op, name, **props):
    def run(context):
        getattr(bpy.ops.fp, op)(name=name, **props)
    return run


def _rows():
    """The list column's rows: (kind, payload), the paint layers then the biome masks (or why there are none)."""
    rows = [("title", "Layers")] + [("layer", (i, n)) for i, n in enumerate(LAND["layers"].grid.layers)]
    biomes = LAND["biomes"]
    if biomes is not None:
        rows += [("title", "Biomes")] + [("biome", (i, n)) for i, n in enumerate(biomes.grid.layers)]
    elif LAND["biome_note"]:
        rows += [("title", "Biomes"), ("note", LAND["biome_note"])]
    elif LAND.get("biome_make"):
        rows += [("title", "Biomes"), ("make", LAND["biome_make"])]
    return rows


def side(ui, r):
    """The paint layers and the biome masks: the one the brush paints selected, each with its colour in the picture,
    a layer's link (weight blend), and visibility. Folded (has_side) until a group is read. Scatter mode: its layers."""
    if land_scatter.is_on():
        land_scatter.side(ui, r)
        return
    t, wm = ui.t, bpy.context.window_manager
    layers, biomes, store = LAND["layers"], LAND["biomes"], LAND["store"]
    rows = _rows()
    offset = ui.scroll("land:layers", r, len(rows) * t.row_pitch)
    y = r.top + offset
    for kind, payload in rows:
        y -= t.row_pitch
        if y > r.top or y + t.row_pitch < r.y:
            continue
        cell = Rect(r.x, y + (t.row_pitch - t.row) / 2, r.w - ui.bar_room, t.row)
        if kind == "title":
            paint.text_in(payload, cell.pad(t.pad, 0), t.text3, t.text)
        elif kind == "layer":
            i, name = payload
            _layer(ui, cell, layers, name, bool(layers.grid.blended[i]), store is layers and name == wm.fp_land_layer)
        elif kind == "biome":
            _layer(ui, cell, biomes, payload[1], None, store is biomes and payload[1] == wm.fp_land_layer)
        elif kind == "note":        # the reason on hover
            ui.row("land:biomes:off", cell, "", None, False, None, tip=payload)
            paint.text_in("Game biome map", cell.pad(t.pad, 0), t.text3, t.text)
        else:
            piece, param = payload
            ui.button("land:biomes:make", cell, "Make Paintable", "mdi_Brush",
                      land_material.make_paintable(piece, param),
                      tip="Paint the biomes here: an image in the game map's place (rebuilds the material)")
    ui.end_scroll()


def _layer(ui, r, store, name, linked, painting):
    """A paint layer's or (linked None) a biome's row."""
    t = ui.t
    hidden = name in LAND["hidden"]
    biome = linked is None
    rid = ("land:biome:" if biome else "land:layer:") + name
    ui.row(rid, r, "", None, painting, _run("land_layer", name, biome=biome))
    buttons = r.copy()
    eye = buttons.cut_right(r.h)
    link = None if biome else buttons.cut_right(r.h)
    inner = buttons.pad(t.pad, 0)
    slot = inner.cut_left(t.icon)
    size = 12 * t.s
    paint.rect(Rect(slot.x + (slot.w - size) / 2, slot.y + (slot.h - size) / 2, size, size),
               store.colors[name] + ((0.3,) if hidden else (1.0,)), 3 * t.s)
    inner.cut_left(t.pad)
    color = t.text3 if hidden else (t.text1 if painting or ui.hot(rid) else t.text2)
    paint.text_in(name, inner, color, t.text)
    if link is not None:
        ui.button("land:link:" + name, link, icon="mdi_LinkVariant" if linked else "mdi_LinkVariantOff",
                  action=_run("land_blend", name), style="flat",
                  tip="Linked: shares 1 with the other linked layers" if linked else "Unlinked: moves alone (a mask)")
    ui.button(("land:bshow:" if biome else "land:show:") + name, eye, icon="mdi_EyeOff" if hidden else "mdi_Eye",
              action=_run("land_show", name, biome=biome), style="flat",
              tip="Show again (the paint is kept)" if hidden else "Hide in the picture and the material")


def shown_material(context):
    """(material, piece showing it, pieces) of the shown group, material None without a built one."""
    name, pieces = wanted_group(context)
    mat, piece = land_material.material(pieces, context.view_layer.objects.active)
    return mat, piece, pieces


def info(ui, r):
    """The material's biome colours, biome maps and features (land_material); Scatter mode: the layer's settings."""
    if land_scatter.is_on():
        land_scatter_info.info(ui, r)
        return
    mat, piece, pieces = shown_material(bpy.context)
    if mat is not None:
        land_material.info(ui, r, mat, piece, pieces, LAND["layers"].grid.layers)


def header(layout, context):
    wm = context.window_manager
    narrow = bool(ui_blender.folded(context, tabs.current(context.space_data)))
    layout.operator("fp.popup_open", text=LAND["group"] or "No landscape", icon='MESH_GRID').name = "land:groups"
    layout = layout.row()
    layout.enabled = LAND["store"] is not None       # nothing to paint
    layout.prop(wm, "fp_land_mode", expand=True)
    row = layout.row(align=True)
    row.prop(wm, "fp_land_radius", text="Rad" if narrow else "Radius")       # a short header: short names
    row.prop(wm, "fp_land_strength", text="Str" if narrow else "Strength", slider=True)
    row.prop(wm, "fp_land_falloff", text="Fall" if narrow else "Falloff", slider=True)
    if land_scatter.is_on(context):
        if LAND["store"] is not None:
            land_scatter.header(layout, context, narrow)
        return
    layout.prop(wm, "fp_land_layer_only", text="" if narrow else "Layer Only", icon='LAYER_ACTIVE', toggle=True)
    if LAND["store"] is not None and "side" in ui_blender.folded(context, tabs.current(context.space_data)):
        layout.operator("fp.popup_open", text=wm.fp_land_layer or "Layer", icon='BRUSH_DATA').name = "land:layers"
    if LAND["store"] is not None and "info" in ui_blender.folded(context, tabs.current(context.space_data)):
        waiting = land_material.pending(shown_material(context)[0])
        if waiting:         # the info column's essential: features changed, waiting for a rebuild
            layout.operator("fp.land_update", text="Update Material (%d)" % waiting, icon='FILE_REFRESH')


# ------------------------------------------------------------------ the header's pop-ups

def layer_items(current):
    """The paint layer pop-up's rows (the list column's essential): Layers then Biomes, the painted one marked."""
    items = []
    for title, store, icon in (("Layers", LAND["layers"], "mdi_Brush"), ("Biomes", LAND["biomes"], "mdi_Terrain")):
        if store is None:
            continue
        biome = store is LAND["biomes"]
        items.append({"header": title})
        items += [{"label": name, "icon": icon, "check": store is LAND["store"] and name == current,
                   "action": _run("land_layer", name, biome=biome), "id": ("biome:" if biome else "layer:") + name}
                  for name in store.grid.layers]
    return items


def group_items(groups):
    """The landscape pop-up's rows: the scene's landscapes (name -> pieces), the shown one marked."""
    if not groups:
        return [{"label": "No landscape", "id": "none"}]
    return [{"label": name, "icon": "mdi_Terrain", "detail": "%d piece%s" % (len(p), "" if len(p) == 1 else "s"),
             "check": name == LAND["group"], "action": _run("land_group", name), "id": name}
            for name, p in sorted(groups.items())]


def open_layers(context, anchor, side="below"):
    """Paint another layer or biome (type to search)"""
    from .ui import popups
    wm = context.window_manager
    return popups.open_list(context, anchor, lambda: layer_items(wm.fp_land_layer), "land:layers", "Search layers",
                            side=side)


def open_groups(context, anchor, side="below"):
    """Show another landscape (type to search)"""
    from .ui import popups
    return popups.open_list(context, anchor, lambda: group_items(land_store.groups(bpy.context.scene)), "land:groups",
                            "Search landscapes", side=side)


OPENERS = {"land:layers": open_layers, "land:groups": open_groups}


# ------------------------------------------------------------------ the scene changes elsewhere

@persistent
def _scene_changed(scene, depsgraph):
    if depsgraph.id_type_updated('OBJECT') or depsgraph.id_type_updated('COLLECTION'):
        LAND["groups"] = None


@persistent
def _undone(*_):
    """Blender's own undo put the data back (and may have removed what was made since, the picture image included):
    read it all again."""
    LAND.update(store=None, layers=None, biomes=None, store_key=None, groups=None, image=None, rgba=None, dirty=True,
                undone=True)
    redraw_all()


@persistent
def _loaded(*_):
    image = LAND["image"]
    LAND.update(group=None, store=None, layers=None, biomes=None, store_key=None, image=None, rgba=None, views={},
                readout={}, hover={},
                groups=None, blend_override={})
    land_material.STATE["cache"].clear()      # keyed by names, which another file reuses
    if image is not None:
        try:
            bpy.data.images.remove(image)
        except ReferenceError:
            pass


def _repaint(self, context):
    LAND["dirty"] = True
    redraw_all()


def _has_info(context):
    if LAND["store"] is None:
        return False
    return land_scatter.is_on(context) or shown_material(context)[0] is not None


def register():
    wm = bpy.types.WindowManager
    wm.fp_land_layer = bpy.props.StringProperty(name="Paint Layer", update=_repaint)
    wm.fp_land_radius = bpy.props.FloatProperty(name="Radius", default=10.0, min=0.1, soft_max=500.0, unit='LENGTH',
                                                description="Brush radius on the ground (F)")
    wm.fp_land_strength = bpy.props.FloatProperty(name="Strength", default=0.5, min=0.0, max=1.0,
                                                  description="How much a dab moves the weight (Shift+F)")
    wm.fp_land_falloff = bpy.props.FloatProperty(name="Falloff", default=0.5, min=0.0, max=1.0,
                                                 description="0: a hard disc, 1: fades from the centre")
    wm.fp_land_layer_only = bpy.props.BoolProperty(name="Layer Only", update=_repaint,
                                                   description="Show the paint layer's weight alone (white = 1)")
    wm.fp_land_mode = bpy.props.EnumProperty(name="Mode", update=_repaint, items=[
        ('PAINT', "Paint", "Paint the landscape's layers"),
        ('SCATTER', "Scatter", "Scatter grass and objects over it: its layers, their settings, their masks painted")])
    ui_blender.OPENERS.update(OPENERS)
    land_material.register()
    land_scatter.register()
    land_select.register()
    land_ground.register()
    tabs.register_tab(tabs.Tab("landscape", "Landscape", 'MESH_GRID', header=header, draw=draw, tick=tick,
                               nav_icon="mdi_Terrain", group="tools", side=side, hover=hover, info=info,
                               has_side=lambda context: LAND["layers"] is not None,
                               has_info=_has_info))
    for name, fn in HANDLERS:
        getattr(bpy.app.handlers, name).append(fn)


HANDLERS = (("load_post", _loaded), ("undo_post", _undone), ("redo_post", _undone),
            ("depsgraph_update_post", _scene_changed))


def unregister():
    for name, fn in HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if fn in handlers:
            handlers.remove(fn)
    _loaded()
    tabs.unregister_tab("landscape")
    land_ground.unregister()
    land_select.unregister()
    land_scatter.unregister()
    land_material.unregister()
    for name in OPENERS:
        ui_blender.OPENERS.pop(name, None)
    for p in ("fp_land_layer", "fp_land_radius", "fp_land_strength", "fp_land_falloff", "fp_land_layer_only", "fp_land_mode"):
        delattr(bpy.types.WindowManager, p)
