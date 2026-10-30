"""The Landscape tab's info column: the shown landscape's material as Fortnite's landscape materials expose it. Biome
colours (the per-biome tints of each ground layer: CliffColor_RedWoods...), biome maps (a custom landscape's
paintable biome masks and grass colours), and features (the static switches: a paint layer's look, biome colours, snow,
grass...), changed here and applied by Update Material. Its controls run the plugin's own landscape operators
(operator/landscape_paint.py) on a piece of the group."""
import difflib
import re

import bpy

from .ui import paint
from .ui.rect import Rect

COLOUR = re.compile(r"^([A-Za-z0-9]+?)Color_([A-Za-z0-9]+)$")       # <layer>Color_<biome>
FAMILIES = (("Biome", "Biomes"), ("Grass", "Grass"), ("Snow", "Snow"), ("Cliff", "Cliff"), ("Asphalt", "Asphalt"))
CLOSED = {"sw:Other"}       # groups that start folded
STATE = {"open": {}, "cache": {}}


def _lp():
    from ...operator import landscape_paint
    return landscape_paint


def material(pieces, active):
    """(the group's built landscape material, the piece showing it): the active piece's, else the first piece's that
    has one; (None, None) when no piece has one."""
    from ...material_porter import build
    for o in ([active] if active in pieces else []) + list(pieces):
        mat = o.active_material
        if mat is not None and mat.get(build.KEY_PATH) and mat.node_tree is not None:
            return mat, o
    return None, None


def root(mat):
    return _lp()._root_node(mat)


def words(name):
    """CamelCase as words (RedWoods: Red Woods)."""
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name)


def biome_colours(mat):
    """(layers, [(biome, {layer: input name})]): the material's <layer>Color_<biome> colour inputs as a table; biome
    names one letter apart are one biome (the game's MudColor_RedWods is RedWoods')."""
    node = root(mat)
    if node is None:
        return [], []
    key = (mat.name, node.node_tree.name if node.node_tree else "")
    if key in STATE["cache"]:
        return STATE["cache"][key]
    layers, rows = [], {}
    for sock in node.inputs:
        m = COLOUR.match(sock.name)
        if m is None or sock.bl_idname != "NodeSocketColor":
            continue
        layer, biome = m.groups()
        if layer not in layers:
            layers.append(layer)
        near = difflib.get_close_matches(biome, list(rows), n=1, cutoff=0.9)
        rows.setdefault(near[0] if near else biome, {})[layer] = sock.name
    out = (layers, sorted(rows.items())) if rows else ([], [])
    STATE["cache"][key] = out
    return out


def switch_groups(mat, layers):
    """[(group key, title, [(switch, on, edited)])]: the switches naming a paint layer under it (in layer order), then
    by family (Biomes, Grass, Snow...), the rest under Other."""
    lp = _lp()
    built = lp.switches(mat)
    edits = dict(mat.get(lp.EDITS) or {})
    by_layer = sorted(layers, key=len, reverse=True)
    groups = {}
    for name, on in lp.features(mat):
        layer = next((l for l in by_layer if re.search(re.escape(l) + r"(?![a-z])", name)), None)
        family = next((title for word, title in FAMILIES if word.lower() in name.lower()), "Other")
        groups.setdefault(("layer:" + layer, layer) if layer else ("sw:" + family, family), []).append(
            (name, on, name in edits and bool(edits[name]) != built.get(name)))
    order = ["layer:" + l for l in layers] + ["sw:" + t for _, t in FAMILIES] + ["sw:Other"]
    return [(key, title, rows) for (key, title), rows in sorted(groups.items(), key=lambda kv: order.index(kv[0][0]))]


def pending(mat):
    return len(mat.get(_lp().EDITS) or {}) if mat is not None else 0


def is_open(key):
    return STATE["open"].get(key, key not in CLOSED)


# ------------------------------------------------------------------ actions (run in operator context)

def _on_piece(piece, fn):
    """fn() with the piece active (the plugin's operators work on the active object); the group shown stays."""
    def run(context):
        layer = context.view_layer
        if piece.name in layer.objects and layer.objects.active is not piece:
            if context.object is not None and context.object.mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')
            layer.objects.active = piece
        with context.temp_override(active_object=piece, object=piece):
            fn(context)
        _redraw()
    return run


def _redraw():
    from .land_tab import redraw_all
    redraw_all()


def _fold(key):
    def run(context):
        STATE["open"][key] = not is_open(key)
    return run


def _switch(piece, name, on):
    return _on_piece(piece, lambda context: bpy.ops.fpmp.landscape_switch(name=name, on=on))


def update(piece, pieces):
    """Update Material on the group's pieces sharing the piece's material."""
    def run(context):
        mat = piece.active_material
        meshes = [o for o in pieces if o.active_material == mat and o.name in context.view_layer.objects]
        if not _lp()._rebuild(context, piece, meshes or [piece]):
            raise RuntimeError("Not rebuilt (is the FP app running?)")
        _redraw()
    return run


def _colour(mat, name, label, anchor):
    """The input's colour picker (the editor's own) beside its swatch."""
    def run(context):
        from .ui import colorpick
        node = root(mat)
        sock = node.inputs.get(name) if node is not None else None
        if sock is not None:
            colorpick.open_for(context, anchor, sock, "default_value", title=label)
    return run


def make_paintable(piece, param):
    return _op(piece, "make_biome_map", param=param)


def unpainted_biomes(pieces):
    """(piece, parameter) of a custom landscape's biome mask map not yet made paintable, else None."""
    from ...material_porter import biome_maps
    mat, piece = material(pieces, None)
    if mat is None or not piece.get("mp_weight_set"):
        return None
    painted = dict(piece.get(biome_maps.KEY) or {})
    return next(((piece, p) for p, info in biome_maps.maps(mat).items()
                 if p not in painted and not biome_maps.is_colour(p, info)), None)


def _op(piece, op, **props):
    return _on_piece(piece, lambda context: getattr(bpy.ops.fpmp, op)(**props))


def _follow(piece):
    return _on_piece(piece, lambda context: setattr(piece, "fpmp_follow_biomes", not piece.fpmp_follow_biomes))


# ------------------------------------------------------------------ drawing

def _srgb(c):
    """A linear material colour as the shell draws it (sRGB, clamped; its alpha is data, not opacity)."""
    out = []
    for v in c[:3]:
        v = min(max(float(v), 0.0), 1.0)
        out.append(v * 12.92 if v <= 0.0031308 else 1.055 * v ** (1 / 2.4) - 0.055)
    return tuple(out) + (1.0,)


def _rows(mat, piece, layers):
    """The column's rows, top down: (kind, key, payload)."""
    from ...material_porter import biome_colours as follow, biome_maps
    rows = []
    columns, table = biome_colours(mat)
    if table:
        rows.append(("group", "colours", "Biome Colours"))
        if is_open("colours"):
            rows.append(("columns", None, columns))
            rows += [("biome", biome, (columns, names)) for biome, names in table]
    found = biome_maps.maps(mat)
    if found:
        rows.append(("group", "maps", "Biome Maps"))
        if is_open("maps"):
            painted = dict(piece.get(biome_maps.KEY) or {})
            which = follow.pair(piece, found)
            for param, info in found.items():       # the masks themselves are painted from the list column
                colour = biome_maps.is_colour(param, info)
                rows.append(("map", param, (colour, param in painted, bool(which and which[1] == param))))
    groups = switch_groups(mat, layers)
    if groups:
        rows.append(("title", None, "Features"))
        for key, title, members in groups:
            rows.append(("group", key, title))
            if is_open(key):
                rows += [("switch", name, (on, edited)) for name, on, edited in members]
    return rows


def info(ui, r, mat, piece, pieces, layers):
    t = ui.t
    waiting = pending(mat)
    bottom = r.cut_bottom(t.action)
    ui.button("land:update", bottom, "Update Material (%d changed)" % waiting if waiting else "Update Material",
              "mdi_Refresh", update(piece, pieces), style="accent" if waiting else "control",
              tip="Rebuild the material with the changed features (the FP app must be running)")
    r.cut_bottom(t.pad)
    head = r.cut_top(t.row_pitch)
    paint.text_in(mat.get("OriginalName") or mat.name, head.pad(t.pad, 0), t.text2, t.text)
    rows = _rows(mat, piece, layers)
    offset = ui.scroll("land:material", r, len(rows) * t.row_pitch)
    y = r.top + offset
    for kind, key, payload in rows:
        y -= t.row_pitch
        if y > r.top or y + t.row_pitch < r.y:
            continue
        cell = Rect(r.x, y + (t.row_pitch - t.row) / 2, r.w - 4 * t.s, t.row)
        if kind == "group":
            ui.group("land:g:" + key, cell, payload, is_open(key), _fold(key))
        elif kind == "title":
            paint.text_in(payload, cell.pad(t.pad, 0), t.text3, t.caption)
        elif kind == "columns":
            _columns(ui, cell, payload)
        elif kind == "biome":
            _biome(ui, cell, mat, key, *payload)
        elif kind == "map":
            _map(ui, cell, piece, key, *payload)
        else:
            _check(ui, cell, piece, key, *payload)
    ui.end_scroll()


def _swatches(t, cell, n):
    """The biome table's swatch slots: n equal cells at the row's right (buttons sit flush, as the list's), the
    biome's name left of them."""
    names = cell.pad(t.pad + t.big, 0, 0, 0)
    w = min(max((names.w - 72 * t.s) / max(n, 1), 18 * t.s), 44 * t.s)
    slots = [names.cut_right(w) for _ in range(n)][::-1]
    return names, slots


def _columns(ui, cell, columns):
    t = ui.t
    _, slots = _swatches(t, cell, len(columns))
    for name, slot in zip(columns, slots):
        paint.text_in(name, slot, t.text3, t.caption, "center")
        if paint.measure(name, t.caption) > slot.w:     # cut short: the whole name on hover
            ui.frame.add(_hit("land:col:" + name, slot, ui.clip, None, name))


def _biome(ui, cell, mat, biome, columns, names):
    t = ui.t
    label, slots = _swatches(t, cell, len(columns))
    paint.text_in(words(biome), label, t.text2, t.text)
    node = root(mat)
    for layer, slot in zip(columns, slots):
        name = names.get(layer)
        sock = node.inputs.get(name) if name else None
        if sock is None:
            continue
        rid = "land:c:" + name
        box = slot.pad(t.gap)
        if ui.hot(rid):
            paint.rect(box, t.hover, t.r_small)
        # inside the hover fill, inset by gap: radius = outer - gap
        paint.rect(box.pad(t.gap), _srgb(sock.default_value), t.r_small - t.gap, edge=t.divider, border=1)
        label = "%s, %s" % (words(biome), layer)
        ui.frame.add(_hit(rid, box, ui.clip, _colour(mat, name, label, box), "%s (%s)" % (label, name)))


def _hit(id, r, clip, action, tip):
    from .ui.frame import Hit
    return Hit(id, r, clip, "button", action, None, tip)


def _check(ui, cell, piece, name, on, edited):
    t = ui.t
    rid = "land:sw:" + name
    inner = cell.pad(t.pad + t.big, 0)
    cut = paint.measure(name, t.text) > inner.w - t.icon - t.pad - (paint.measure("changed", t.caption) + t.pad if edited else 0)
    ui.row(rid, cell, "", None, False, _switch(piece, name, not on), detail="changed" if edited else "",
           tip="Changed: Update Material applies it" if edited else (name if cut else ""))
    slot = inner.cut_left(t.icon)
    size = 12 * t.s
    box = Rect(slot.x + (slot.w - size) / 2, slot.y + (slot.h - size) / 2, size, size)
    # inside the row's hover fill, a padding in: radius = row radius - padding
    paint.rect(box, t.accent if on else (0, 0, 0, 0), max(t.r_small - t.pad, t.s), edge=t.text3, border=0 if on else 1)
    if on:
        ui.icon(box, "mdi_Check", t.text1)
    inner.cut_left(t.pad)
    if edited:
        inner.cut_right(paint.measure("changed", t.caption) + t.pad)
    paint.text_in(name, inner, t.text1 if ui.hot(rid) else t.text2, t.text)


def _map(ui, cell, piece, param, colour, painted, follows_pair):
    t = ui.t
    inner = cell.pad(t.pad + t.big, 0, 0, 0)
    paint.text_in(("Grass Colours" if "grass" in param.lower() else "Colours") if colour else "Biomes", inner, t.text2, t.text)
    if not painted:
        w = paint.measure("Make Paintable", t.text) + 2 * t.pad
        ui.button("land:mk:" + param, Rect(cell.right - w, cell.y, w, cell.h), "Make Paintable",
                  action=make_paintable(piece, param),
                  tip="An image in the game texture's place to paint (rebuilds the material)")
        return
    trash = Rect(cell.right - cell.h, cell.y, cell.h, cell.h)
    ui.button("land:rm:" + param, trash, icon="mdi_Close", action=_op(piece, "remove_biome_map", param=param),
              style="flat", tip="Remove the painted map: the game's again")
    if follows_pair:
        on = piece.fpmp_follow_biomes
        ui.button("land:follow:" + param, Rect(trash.x - t.gap - cell.h, cell.y, cell.h, cell.h), icon="mdi_LinkVariant"
                  if on else "mdi_LinkVariantOff", action=_follow(piece), selected=on, style="flat",
                  tip="Grass colours follow the painted biomes" if on else "Grass colours painted by hand")


def register():
    pass


def unregister():
    STATE.update(open={}, cache={})
