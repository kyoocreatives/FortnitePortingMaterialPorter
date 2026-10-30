"""The Landscape tab's Scatter mode, info column (land_scatter.py: the list and the data): the modifier's inputs as
sections of value rows, the add buttons, and the placed foliage's facts. Rows open land_popups.py's pop-ups."""
import bpy

from .land_scatter import (SCATTER, _biomes, _fold, _run, _run_invoke, amount, core, describe, grass_payload, modifiers,
                           rows, selected, short, shown_group)
from .ui import paint
from .ui.rect import Rect


# ------------------------------------------------------------------ the info column

def _ue(v):
    return v.get("Placement") == "UE"


SECTIONS = (        # (title, [(label, inputs, shown when)]): the modifier's inputs as the info column lists them
    ("Points", [("Density", ("Density",), None), ("Placement", ("Placement",), None),
                ("Use Grid", ("Use Grid",), _ue), ("Jitter", ("Jitter",), lambda v: _ue(v) and v.get("Use Grid")),
                ("Seed", ("Seed",), None), ("Random Rotation", ("Random Rotation",), None),
                ("Align To Surface", ("Align To Surface",), None)]),
    ("Scale", [("Scaling", ("Scaling",), None), ("Scale X", ("Scale X Min", "Scale X Max"), None),
               ("Scale Y", ("Scale Y Min", "Scale Y Max"), lambda v: v.get("Scaling") == "Free"),
               ("Scale Z", ("Scale Z Min", "Scale Z Max"), lambda v: v.get("Scaling") != "Uniform"),
               ("Attenuate by Weight", ("Weight Attenuates Max Scale",), None),
               ("Attenuation", ("Attenuation",), lambda v: v.get("Weight Attenuates Max Scale"))]),
    ("Filters", [("Density Range", ("Density Range Min", "Density Range Max"), None),
                 ("Slope", ("Slope Min", "Slope Max"), None), ("Height", ("Height Min", "Height Max"), None)]),
    ("Weight", []),
    ("Display", [("Display %", ("Display %",), None), ("Camera Cull", ("Camera Cull",), None),
                 ("Cull Distance", ("Cull Start", "Cull End"), lambda v: v.get("Camera Cull"))]),
)


def _fmt(label, values):
    lo = values[0]
    if isinstance(lo, str):
        return lo
    if label == "Density":
        return "%g / 100 m²" % round(lo, 2)
    if label == "Display %":
        return "%g %%" % round(lo, 1)
    if label == "Seed":
        return str(lo)
    hi = values[1] if len(values) > 1 else None
    if label == "Height" and lo <= -1e4 and hi >= 1e4:
        return "Any"
    unit = {"Slope": "°", "Height": " m", "Cull Distance": " m"}.get(label, "")
    digits = 0 if unit else 2
    if hi is None:
        return "%.*f%s" % (digits, lo, unit)
    return "%.*f – %.*f%s" % (digits, lo, digits, hi, unit)


def info_rows(e, values, shadows, biomes=()):
    """The info column's rows for a layer: (kind, key, payload). `biomes`: [(index, name)] the layer can choose."""
    out = []
    for title, members in SECTIONS:
        out.append(("group", title, title))
        if not SCATTER["open"].get("s:" + title, True):
            continue
        if title == "Weight":
            out.append(("value", "Weight", ("Source", None)))
            if biomes:
                out.append(("biome", "Weight", ("Biome", dict(biomes).get(values.get("Biome") or 0, "World"))))
            continue
        for label, names, when in members:
            if when is not None and not when(values):
                continue
            vals = [values.get(n) for n in names]
            if any(v is None for v in vals):
                continue
            if isinstance(vals[0], bool):
                out.append(("check", title, (label, names[0], vals[0])))
            else:
                out.append(("value", title, (label, _fmt(label, vals))))
        if title == "Display" and shadows is not None:
            out.append(("check", title, ("Shadows", None, shadows)))
    from . import land_ground
    return out + land_ground.info_rows(e)


def _actions(pieces):
    """The column's buttons, bottom up: rows of (id, text, icon, action, tip, style)."""
    from . import land_grass_list, land_material
    out = [[("scatter:type", "Add Grass Type…", None, _run_invoke("scatter_add_type"),
             "Scatter a grass type of the game files here (a layer per variety)", "control"),
            ("scatter:object", "Add from Object", None, _run("scatter_add_object"),
             "Scatter the selected objects (or the active collection's) here", "control")]]
    payload = grass_payload(pieces)
    has_material = land_material.material(pieces, None)[0] is not None
    game = []
    if payload and payload.get("outputs"):
        none_yet = not any(e["kind"] == "game" for e in rows())
        game.append(("scatter:game", "Add Game Grass", "mdi_Terrain", _run("scatter_game_grass"),
                     "Scatter every grass of the landscape's material, each weighted by its mask",
                     "accent" if none_yet else "control"))
    if has_material or payload:
        game.append((land_grass_list.BUTTON, "Material Grass…", None, land_grass_list.open_beside,
                     "Browse the grass of the landscape's material: scatter one output or one mesh", "control"))
    if game:
        out.append(game)
    if has_material:
        out.append([("scatter:masks", "Update Grass Masks", "mdi_Refresh", _run("scatter_masks"),
                     "Evaluate the material's grass masks again (after painting its layers)", "control")])
    return out


def info(ui, r):
    t = ui.t
    name, pieces = shown_group()
    for row in _actions(pieces):
        cell = r.cut_bottom(t.action)
        r.cut_bottom(t.pad)
        w = (cell.w - t.pad * (len(row) - 1)) / len(row)
        for k, (bid, text, icon, action, tip, style) in enumerate(row):
            ui.button(bid, Rect(cell.x + k * (w + t.pad), cell.y, w, cell.h), text, icon, action, tip=tip, style=style)
    from . import land_ground
    land_ground.colour(ui, r)
    from . import land_select
    picked = land_select.entries()
    if len(picked) > 1:
        land_select.info(ui, r, picked)
        return
    e = selected()
    if e is None:
        return
    head = r.cut_top(t.row_pitch)
    trash = Rect(head.right - t.row, head.y + (head.h - t.row) / 2, t.row, t.row)
    ui.button("scatter:remove", trash, icon="mdi_Close", action=_run_invoke("scatter_remove"), style="flat",
              tip="Remove this layer" if e["kind"] != "placed" else "Delete these placed instances")
    label = Rect(head.x, head.y, head.w - t.row - t.gap, head.h).pad(t.pad, 0)
    paint.text_in(paint.fit(e["label"], t.title, label.w), label, t.text1, t.title)
    sub = r.cut_top(t.row_pitch * 0.75)
    if e["kind"] == "game":
        caption = "%s · variety %d" % (short(e["type"]), e.get("variety", 0) + 1)
    elif e["kind"] == "custom":
        mods = modifiers(e)
        inst = core()._get(mods[0][1], "Instance") if mods else None
        caption = "Custom · %s" % (inst.name if inst is not None else "no object")
    else:
        caption = "Placed · %s" % (e["info"].get("class") or e["object"])
    paint.text_in(paint.fit(caption, t.caption, sub.w - 2 * t.pad), sub.pad(t.pad, 0), t.text3, t.caption)
    if e["kind"] == "placed":
        r.cut_top(t.gap)
        _placed_info(ui, r, e)
        return
    mods = modifiers(e)
    if not mods:
        return
    obj, mod = mods[0]
    values = core()._values(mod)
    inst = values.get("Instance")
    if core().is_wind_sprite(inst):
        note = r.cut_top(t.row_pitch * 0.75)
        paint.text_in(paint.fit("Wind sprites: not shown in Blender", t.caption, note.w - 2 * t.pad), note.pad(t.pad, 0),
                      t.text3, t.caption)
    r.cut_top(t.gap)
    items = info_rows(e, values, bool(inst.visible_shadow) if inst is not None else None, _biomes(obj, e))
    offset = ui.scroll("scatter:settings", r, len(items) * t.row_pitch)
    y = r.top + offset
    for kind, key, payload in items:
        y -= t.row_pitch
        if y > r.top or y + t.row_pitch < r.y:
            continue
        cell = Rect(r.x, y + (t.row_pitch - t.row) / 2, r.w - 4 * t.s, t.row)
        if kind == "group":
            ui.group("scatter:s:" + key, cell, payload, SCATTER["open"].get("s:" + key, True), _fold("s:" + key))
        elif kind == "check":
            _check(ui, cell, key, *payload)
        elif kind == "biome":
            _dropdown(ui, cell, *payload)
        else:
            label, text = payload
            if key == "Weight":
                text = describe(obj, e["weight"])
            _value(ui, cell, key, label, text)
    ui.end_scroll()


def _value(ui, cell, section, label, text):
    t = ui.t
    rid = "scatter:v:%s:%s" % (section, label)
    from . import land_popups
    if section == "Weight":
        def action(context):
            land_popups.open_weight(context, cell)
    else:
        def action(context):
            land_popups.open_section(context, cell, section)
    ui.row(rid, cell, "", None, False, action,
           tip="Weight: the paint mask times this" if section == "Weight" else "Edit the %s settings" % section)
    inner = cell.pad(t.pad + t.big, 0, t.pad, 0)
    w = min(paint.measure(text, t.text) + t.pad, inner.w * 0.6)
    paint.text_in(paint.fit(text, t.text, w), inner.cut_right(w), t.text1 if ui.hot(rid) else t.text2, t.text, "right")
    paint.text_in(paint.fit(label, t.text, inner.w), inner, t.text2, t.text)


def _dropdown(ui, cell, label, text):
    t = ui.t
    rid = "scatter:biome"
    from . import land_popups
    ui.row(rid, cell, "", None, False, lambda context: land_popups.open_biome(context, cell),
           tip="The biome the grass takes its colour from (World: the game's map at its position)")
    inner = cell.pad(t.pad + t.big, 0, t.pad, 0)
    ui.icon(inner.cut_right(t.icon), "mdi_ChevronDown", t.text3)
    w = min(paint.measure(text, t.text) + t.pad, inner.w * 0.6)
    paint.text_in(paint.fit(text, t.text, w), inner.cut_right(w), t.text1 if ui.hot(rid) else t.text2, t.text, "right")
    paint.text_in(paint.fit(label, t.text, inner.w), inner, t.text2, t.text)


def _check(ui, cell, section, label, name, on):
    t = ui.t
    rid = "scatter:c:" + label
    action = _run("scatter_shadow") if name is None else _run("scatter_set", name=name, on=not on)
    ui.row(rid, cell, "", None, False, action,
           tip="The instanced object casts shadows (everywhere it's used)" if name is None else "")
    inner = cell.pad(t.pad + t.big, 0)
    slot = inner.cut_left(t.icon)
    size = 12 * t.s
    box = Rect(slot.x + (slot.w - size) / 2, slot.y + (slot.h - size) / 2, size, size)
    # inside the row's hover fill, a padding in: radius = row radius - padding
    paint.rect(box, t.accent if on else (0, 0, 0, 0), max(t.r_small - t.pad, t.s), edge=t.text3, border=0 if on else 1)
    if on:
        ui.icon(box, "mdi_Check", t.text1)
    elif on is None:        # picked layers differ: a dash (a click turns them all on)
        paint.rect(Rect(box.x + 3 * t.s, box.y + box.h / 2 - t.s, box.w - 6 * t.s, 2 * t.s), t.text2, 0)
    inner.cut_left(t.pad)
    paint.text_in(label, inner, t.text1 if ui.hot(rid) else t.text2, t.text)


def _placed_info(ui, r, e):
    t = ui.t
    lines = [("Instances", amount(SCATTER["counts"].get(e["key"]))), ("Mesh", short(e["info"].get("mesh"))),
             ("Actor", e["info"].get("actor") or "")]
    y = r.top
    for label, text in lines:
        if not text:
            continue
        y -= t.row_pitch
        cell = Rect(r.x, y + (t.row_pitch - t.row) / 2, r.w - 4 * t.s, t.row).pad(t.pad + t.big, 0, t.pad, 0)
        w = min(paint.measure(text, t.text) + t.pad, cell.w * 0.65)
        paint.text_in(paint.fit(text, t.text, w), cell.cut_right(w), t.text2, t.text, "right")
        paint.text_in(label, cell, t.text2, t.text)
    y -= t.row_pitch
    paint.text_in("Drag over the picture to erase instances", Rect(r.x, y, r.w, t.row_pitch).pad(t.pad, 0), t.text3,
                  t.caption)
    from . import land_ground
    for kind, key, payload in land_ground.info_rows(e):
        y -= t.row_pitch
        cell = Rect(r.x, y + (t.row_pitch - t.row) / 2, r.w - 4 * t.s, t.row)
        if kind == "group":
            ui.group("scatter:s:" + key, cell, payload, SCATTER["open"].get("s:" + key, True), _fold("s:" + key))
        else:
            _value(ui, cell, key, *payload)
