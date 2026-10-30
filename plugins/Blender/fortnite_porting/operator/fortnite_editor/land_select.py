"""Scatter mode's multi-selection: Ctrl-click toggles a layer, Shift-click takes the range from the last pick, a grass
type header's name takes its layers. The info column then shows what the picked entries share ("—" where they
differ) and an edit lands on every one of them, one undo step: layer settings on the scatter layers, Ground on those
and on placed foliage. SCATTER["selected"] stays the lead (the last clicked): the well shows it, the brush paints it.

Values the section popups edit go through Scene.fp_scatter_batch: a property per modifier input whose setter writes
every picked layer at once, so Blender's own undo push after the edit holds them all."""
import json
import re

import bpy

from . import land_scatter as ls, land_scatter_info
from .ui import paint
from .ui.rect import Rect

MIXED = "—"
BATCH = "fp_scatter_batch"
MENUS = {"Placement": "PLACEMENT", "Scaling": "SCALING"}       # menu inputs: their items in scatter.py
KINDS = (("game", "game grass"), ("custom", "custom"), ("placed", "placed"))

# keys: the picked entries' keys (the lead among them); anchor: where a Shift-click's range starts
PICKED = {"keys": [], "anchor": None}


# ------------------------------------------------------------------ the pick

def keys(context=None):
    """The picked keys in list order: PICKED's while the lead is among them, else the lead alone."""
    lead = ls.SCATTER["selected"]
    known = [e["key"] for e in ls.rows(context)]
    found = [k for k in known if k in PICKED["keys"]]
    if lead in found and len(found) > 1:
        return found
    return [lead] if lead in known else []


def entries(context=None):
    picked = set(keys(context))
    return [e for e in ls.rows(context) if e["key"] in picked]


def layers(context=None):
    """The picked scatter layers (placed foliage aside)."""
    return [e for e in entries(context) if e["kind"] != "placed"]


def many(context=None):
    return len(keys(context)) > 1


def _lead(key):
    if key != ls.SCATTER["selected"]:
        ls.pick(key)


def click(key, extend=False, span=False, context=None):
    """A row clicked: alone (plain, as before), toggled (Ctrl), or the range from the anchor (Shift; Ctrl+Shift adds
    it)."""
    now = keys(context)
    anchor = PICKED["anchor"] if PICKED["anchor"] in now else ls.SCATTER["selected"]
    if span and anchor is not None:
        order = [p[0]["key"] for kind, p in ls.list_rows(context) if kind == "layer"]
        if key not in order or anchor not in order:     # one of them folded away: the full list's order
            order = [e["key"] for e in ls.rows(context)]
        i, j = order.index(anchor), order.index(key)
        run = order[min(i, j):max(i, j) + 1]
        PICKED["keys"] = now + [k for k in run if k not in now] if extend else run
        PICKED["anchor"] = anchor
        _lead(key)
    elif extend and key in now:
        if len(now) > 1:        # the last one stays picked
            PICKED["keys"] = [k for k in now if k != key]
            PICKED["anchor"] = PICKED["keys"][-1]
            _lead(ls.SCATTER["selected"] if ls.SCATTER["selected"] != key else PICKED["keys"][-1])
    elif extend:
        PICKED.update(keys=now + [key], anchor=key)
        _lead(key)
    else:
        PICKED.update(keys=[key], anchor=key)
        ls.pick(key)


def pick_group(group, extend=False, context=None):
    """A grass type header's name clicked: its layers (Ctrl: added to the pick)."""
    found = [e["key"] for e in ls.rows(context) if e["kind"] == "game" and "type:" + e["type"] == group]
    if not found:
        return
    now = keys(context)
    PICKED.update(keys=now + [k for k in found if k not in now] if extend else found, anchor=found[0])
    if ls.SCATTER["selected"] not in PICKED["keys"]:
        ls.pick(found[0])


def _held():
    from .ui.blender import HELD
    return HELD


def pick_action(key):
    """A layer row's press: the click's Ctrl and Shift go with it."""
    def run(context):
        held = _held()
        bpy.ops.fp.scatter_pick(key=key, extend=held["ctrl"], span=held["shift"])
    return run


def group_action(group):
    def run(context):
        bpy.ops.fp.scatter_pick(group=group, extend=_held()["ctrl"])
    return run


# ------------------------------------------------------------------ the info column

def _same(values):
    values = list(values)
    return values[0] if values and all(v == values[0] for v in values) else MIXED


def title(es):
    return "%d layers" % len(es)


def caption(es):
    return " · ".join("%d %s" % (n, word) for kind, word in KINDS
                      for n in [sum(e["kind"] == kind for e in es)] if n)


def _biomes(es):
    """[(index, name)] every picked layer can choose ([] when one can't)."""
    common = None
    for e in es:
        mods = ls.modifiers(e)
        found = ls._biomes(mods[0][0], e) if mods else []
        common = found if common is None else [c for c in common if c in found]
    return common or []


def info_rows(es):
    """The info column's rows (kind, section, payload) for several entries: a row where any picked layer shows it, its
    value where they agree (a check: None when they don't)."""
    sc = ls.core()
    scatter = [(e, mods) for e in es if e["kind"] != "placed" for mods in [ls.modifiers(e)] if mods]
    vals = [sc._values(mods[0][1]) for _e, mods in scatter]
    out = []
    for section, members in land_scatter_info.SECTIONS if scatter else ():
        out.append(("group", section, section))
        if not ls.SCATTER["open"].get("s:" + section, True):
            continue
        if section == "Weight":
            out.append(("value", "Weight", ("Source", _same(ls.describe(None, e["weight"]) for e, _m in scatter))))
            biomes = _biomes([e for e, _m in scatter])
            if biomes:
                names = (dict(biomes).get(v.get("Biome") or 0, "World") for v in vals)
                out.append(("biome", "Weight", ("Biome", _same(names))))
            continue
        for label, names, when in members:
            shown = [[v.get(n) for n in names] for v in vals if when is None or when(v)]
            shown = [p for p in shown if None not in p]
            if not shown:
                continue
            if isinstance(shown[0][0], bool):
                on = _same(p[0] for p in shown)
                out.append(("check", section, (label, names[0], None if on is MIXED else on)))
            else:
                out.append(("value", section, (label, _same(land_scatter_info._fmt(label, p) for p in shown))))
        if section == "Display":
            insts = [v.get("Instance") for v in vals if v.get("Instance") is not None]
            if insts:
                on = _same(bool(i.visible_shadow) for i in insts)
                out.append(("check", section, ("Shadows", None, None if on is MIXED else on)))
    return out + ground_rows(es)


def ground_rows(es):
    """The Ground rows over scatter layers and placed foliage alike."""
    from . import land_ground
    out = [("group", "Ground", "Ground")]
    if not ls.SCATTER["open"].get("s:Ground", True):
        return out
    found = [land_ground.ground_of(e) for e in es]
    out.append(("value", "Ground", ("Ground", _same(g["mode"] for g in found))))
    on = [g for g in found if g["mode"] == "On"]
    if on:
        out += [("value", "Ground", ("Style", _same(g["style"] for g in on))),
                ("value", "Ground", ("Strength", _same("%.2f" % g["strength"] for g in on)))]
    return out


def info(ui, r, es):
    """The info column for several picked entries: "N layers" with the remove button, what kinds, the shared rows."""
    t = ui.t
    head = r.cut_top(t.row_pitch)
    trash = Rect(head.right - t.row, head.y + (head.h - t.row) / 2, t.row, t.row)
    ui.button("scatter:remove", trash, icon="mdi_Close", action=ls._run_invoke("scatter_remove"), style="flat",
              tip="Remove these %d" % len(es))
    label = Rect(head.x, head.y, head.w - t.row - t.gap, head.h).pad(t.pad, 0)
    paint.text_in(paint.fit(title(es), t.title, label.w), label, t.text1, t.title)
    sub = r.cut_top(t.row_pitch * 0.75)
    paint.text_in(paint.fit(caption(es), t.caption, sub.w - 2 * t.pad), sub.pad(t.pad, 0), t.text3, t.caption)
    r.cut_top(t.gap)
    items = info_rows(es)
    offset = ui.scroll("scatter:settings", r, len(items) * t.row_pitch)
    y = r.top + offset
    for kind, key, payload in items:
        y -= t.row_pitch
        if y > r.top or y + t.row_pitch < r.y:
            continue
        cell = Rect(r.x, y + (t.row_pitch - t.row) / 2, r.w - 4 * t.s, t.row)
        if kind == "group":
            ui.group("scatter:s:" + key, cell, payload, ls.SCATTER["open"].get("s:" + key, True), ls._fold("s:" + key))
        elif kind == "check":
            land_scatter_info._check(ui, cell, key, *payload)
        elif kind == "biome":
            land_scatter_info._dropdown(ui, cell, *payload)
        else:
            land_scatter_info._value(ui, cell, key, *payload)
    ui.end_scroll()


# ------------------------------------------------------------------ the popups' batch values

def prop_of(name):
    """A modifier input's property on Scene.fp_scatter_batch (Display % -> display)."""
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def _batch_inputs():
    """[(name, socket type, min, max, description)] the section popups edit."""
    wanted = {n for _s, members in land_scatter_info.SECTIONS for _l, names, _w in members for n in names}
    return [(i[0], i[1], i[3], i[4], i[5]) for i in ls.core().INPUTS if isinstance(i, tuple) and i[0] in wanted]


def _first(context=None):
    found = layers(context)
    mods = ls.modifiers(found[0]) if found else []
    return mods[0][1] if mods else None


def _get(name, kind, default):
    def get(owner):
        mod = _first()
        value = ls.core()._get(mod, name) if mod is not None else None
        if value is None:
            return default
        if name in MENUS:
            items = getattr(ls.core(), MENUS[name])
            return items.index(value) if value in items else 0
        return value
    return get


def _set(name):
    def set_(owner, value):
        sc = ls.core()
        if name in MENUS:
            value = getattr(sc, MENUS[name])[value]
        objs = []
        for e in layers():
            for obj, mod in ls.modifiers(e):
                sc._set(mod, name, value)
                objs.append(obj)
        ls.SCATTER["synced"] = None
        ls.invalidate()
        from .land_tab import refresh
        refresh(objs)
    return set_


def _props():
    out = {}
    for name, kind, lo, hi, tip in _batch_inputs():
        limits = {k: v for k, v in (("min", lo), ("max", hi)) if v is not None}
        if name in MENUS:
            items = [(v, v, "") for v in getattr(ls.core(), MENUS[name])]
            out[prop_of(name)] = bpy.props.EnumProperty(name=name, items=items, description=tip, get=_get(name, kind, 0),
                                                        set=_set(name))
        elif kind == 'NodeSocketFloat':
            out[prop_of(name)] = bpy.props.FloatProperty(name=name, description=tip, get=_get(name, kind, 0.0),
                                                         set=_set(name), **limits)
        elif kind == 'NodeSocketInt':
            out[prop_of(name)] = bpy.props.IntProperty(name=name, description=tip, get=_get(name, kind, 0),
                                                       set=_set(name), **limits)
        elif kind == 'NodeSocketBool':
            out[prop_of(name)] = bpy.props.BoolProperty(name=name, description=tip, get=_get(name, kind, False),
                                                        set=_set(name))
    return out


def shared_weight(context=None):
    """The picked layers' weight (JSON) when they share one, else None."""
    found = {json.dumps(e["weight"]) for e in layers(context)}
    return found.pop() if len(found) == 1 else None


_group = None


def register():
    global _group
    _group = type("FP_PG_scatter_batch", (bpy.types.PropertyGroup,), {"__annotations__": _props()})
    bpy.utils.register_class(_group)
    setattr(bpy.types.Scene, BATCH, bpy.props.PointerProperty(type=_group))


def unregister():
    global _group
    if hasattr(bpy.types.Scene, BATCH):
        delattr(bpy.types.Scene, BATCH)
    if _group is not None:
        bpy.utils.unregister_class(_group)
        _group = None
    PICKED.update(keys=[], anchor=None)
