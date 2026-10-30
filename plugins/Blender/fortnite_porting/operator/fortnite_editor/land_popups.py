"""The Landscape tab's Scatter pop-ups (the editor's own, ui/popups.py): a section's settings, Ground, the weight
source, the biome, the layer list (a folded list column), the game's grass types, and Remove's confirmation. The info
column's rows open them beside themselves; the header's buttons under themselves."""
import json

import bpy

from . import land_grass_list
from . import land_scatter as ls, land_scatter_info
from . import land_select
from .ui import popups

WORLD = (0, "World (by position)")


def _picked(context=None):
    """The layer the pop-ups read: the first picked of several, or the one."""
    if land_select.many(context):
        return next(iter(land_select.layers(context)), None)
    return ls.selected(context)


def _mods(e):
    return ls.modifiers(e) if e is not None and e["kind"] != "placed" else []


def _title(context, e, section):
    picked = land_select.entries(context)
    return "%s · %s" % (land_select.title(picked) if len(picked) > 1 else e["label"], section)


def _op(name, **props):
    return lambda context: getattr(bpy.ops.fp, name)(**props)


# ------------------------------------------------------------------ items

def weight_items(context=None):
    """None, the grass masks (the mesh's and the game grass outputs), the landscape layers: one marked."""
    from ...material_porter import landscape_layers
    e = _picked(context)
    mods = _mods(e)
    if not mods:
        return [{"header": "Pick the layer again"}]
    obj = mods[0][0]
    current = land_select.shared_weight(context) if land_select.many(context) else json.dumps(e["weight"])

    def item(text, weight, n):
        w = json.dumps(weight)
        return {"label": text, "check": w == current, "action": _op("scatter_weight", weight=w), "id": n}
    out = [item("None (the paint mask alone)", None, "none")]
    _, pieces = ls.shown_group(context)
    sc = ls.core()
    masks = [a.name[len(sc.GRASS):] for a in obj.data.attributes if a.name.startswith(sc.GRASS)]
    masks += [o["name"] for o in (ls.grass_payload(pieces) or {}).get("outputs") or [] if o.get("name") not in masks]
    if masks:
        out += [{"sep": True}, {"header": "Grass Masks"}] + [item(m, {"attribute": m}, "m:" + m) for m in masks]
    layers = sorted(landscape_layers.table(obj))
    if layers:
        out += [{"sep": True}, {"header": "Landscape Layers"}] + [item(n, {"layer": n}, "l:" + n) for n in layers]
    return out


def biome_items(context=None):
    """World (the game's map at the position), then the biomes the layer's grass can take: the current one marked."""
    sc = ls.core()
    if land_select.many(context):
        es = land_select.layers(context)
        current = {sc._get(m[0][1], "Biome") or 0 for e in es for m in [ls.modifiers(e)] if m}
        choices = [WORLD] + land_select._biomes(es)
    else:
        e = ls.selected(context)
        mods = _mods(e)
        if not mods:
            return [{"header": "Pick the layer again"}]
        obj, mod = mods[0]
        current = {sc._get(mod, "Biome") or 0}
        choices = [WORLD] + ls._biomes(obj, e)
    return [{"label": name, "check": current == {index}, "action": _op("scatter_biome", index=index), "id": index}
            for index, name in choices]


def layer_items(context=None):
    """The list column's layers (a narrow area folds it away): picking one picks it there."""
    return [{"label": "%s · %s" % (ls.short(e["type"]), e["label"]) if e["kind"] == "game" else e["label"],
             "check": e["key"] == ls.SCATTER["selected"], "action": _op("scatter_pick", key=e["key"]), "id": e["key"]}
            for e in ls.rows(context)]


def type_items():
    """The game's grass types: picking one adds it to the shown landscape."""
    from .scatter_ops import _origin
    return [{"label": t["name"], "detail": "%s · %d varieties" % (_origin(t["path"]), t.get("varieties", 0)),
             "tip": t["path"], "action": lambda context, path=t["path"]: bpy.ops.fp.scatter_add_type('EXEC_DEFAULT', type=path),
             "id": t["path"]} for t in ls.SCATTER["types"] or []]


# ------------------------------------------------------------------ the settings panel

def _ground(ui, col, context, e):
    from . import land_ground
    mode = land_ground.ground_of(e)["mode"]
    col.prop(context.scene, "fp_ground_mode", expand=True)
    col.enabled = mode == "On"
    col.prop(context.scene, "fp_ground_style")
    col.prop(context.scene, "fp_ground_strength", slider=True)
    col.enabled = True


def _members(section):
    return next((m for title, m in land_scatter_info.SECTIONS if title == section), None)


def _edited(e):
    """A field's write on the layer's first piece: the other pieces follow, every piece shows it now."""
    def run(context):
        from .land_tab import refresh
        ls.SCATTER["synced"] = None
        ls._sync(e)
        ls.invalidate()
        refresh([obj for obj, _mod in _mods(e)])
    return run


def section_content(section):
    """A section's fields: the modifier's own inputs (several layers: the batch values, the first one's shown)."""
    def content(ui, col):
        context = bpy.context
        e = ls.selected(context)
        members = _members(section)
        if e is None or (section != "Ground" and (members is None or not _mods(_picked(context)))):
            col.label("Pick the layer again", ui.t.text3)
            return
        col.label(_title(context, e, section), ui.t.text1)
        if section == "Ground":
            _ground(ui, col, context, e)
            return
        sc = ls.core()
        if land_select.many(context):
            vals = [sc._values(mods[0][1]) for x in land_select.layers(context) for mods in [ls.modifiers(x)] if mods]
            batch = getattr(context.scene, land_select.BATCH)
            for label, names, when in members:
                if when is None or any(when(v) for v in vals):
                    for k, name in enumerate(names):
                        col.prop(batch, land_select.prop_of(name),
                                 text=label if len(names) == 1 else ("%s Min" % label, "Max")[k])
            return
        mod = _mods(e)[0][1]
        values = sc._values(mod)
        edited = _edited(e)
        for label, names, when in members:
            if when is not None and not when(values):
                continue
            for k, name in enumerate(names):
                target = getattr(mod.properties.inputs, sc._item(mod.node_group, name).identifier)
                col.prop(target, "value", text=label if len(names) == 1 else ("%s Min" % label, "Max")[k],
                         on_change=edited, id="scatter:%s:%s" % (name, k))
    return content


# ------------------------------------------------------------------ opening

def open_section(context, anchor, section):
    return popups.open_panel(context, anchor, section_content(section), "scatter_section", width=300, side="left")


def open_weight(context, anchor):
    return popups.open_list(context, anchor, lambda: weight_items(bpy.context), "scatter_weight",
                            "Search weights", width=280, side="left")


def open_biome(context, anchor):
    return popups.open_menu(context, anchor, lambda: biome_items(bpy.context), "scatter_biome", side="left")


def open_layers(context, anchor, side="below"):
    """Scatter layers (the header's stand-in for the list column)"""
    return popups.open_list(context, anchor, lambda: layer_items(bpy.context), "scatter_layers", "Search layers",
                            side=side)


def open_types(context, anchor, side="below"):
    """Add a grass type of the game files"""
    return popups.open_list(context, anchor, type_items, "scatter_types", "Search grass types", width=340,
                            height=420, side=side)


def confirm_remove(context):
    found = land_select.entries(context)
    if not found:
        return None
    what = found[0]["label"] if len(found) == 1 else land_select.title(found)
    return popups.confirm(context, "Remove %s?" % what, "Its modifiers and paint mask go (placed foliage: its points "
                          "object). Undo brings them back.", lambda c: bpy.ops.fp.scatter_remove('EXEC_DEFAULT'),
                          ok="Remove", id="scatter_remove")


OPENERS = {"scatter_layers": open_layers, "scatter_types": open_types, "scatter_grass": land_grass_list.open_list}


def register():
    from .ui import blender as ub
    ub.OPENERS.update(OPENERS)


def unregister():
    from .ui import blender as ub
    for name in OPENERS:
        ub.OPENERS.pop(name, None)
