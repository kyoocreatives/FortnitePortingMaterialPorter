"""The asset grid, drawn into the editor's main region (a POST_PIXEL callback): locker tiles (rarity or series
gradient, icon, name strip), a thin outline on hover and selection, a scroll bar; the engine's state when it has
nothing to show yet."""
import blf
import bpy
import gpu
from gpu_extras.batch import batch_for_shader

from . import colors, layout, tabs, textures
from .ui import blender as ui_blender
from .ui import paint
from .ui.rect import Rect
from .state import STATE, client, items, picked
from .ui.kit import glyph

FONT = 0
_shaders = {}


def _shader(name):
    if name not in _shaders:
        _shaders[name] = gpu.shader.from_builtin(name)
    return _shaders[name]


def _quad(x, y, w, h):
    return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]


def _fill(x, y, w, h, color):
    shader = _shader('UNIFORM_COLOR')
    batch = batch_for_shader(shader, 'TRIS', {"pos": _quad(x, y, w, h)}, indices=((0, 1, 2), (0, 2, 3)))
    shader.uniform_float("color", color)
    batch.draw(shader)


def _image(x, y, w, h, texture):
    shader = _shader('IMAGE')
    batch = batch_for_shader(shader, 'TRI_FAN', {"pos": _quad(x, y, w, h), "texCoord": [(0, 0), (1, 0), (1, 1), (0, 1)]})
    shader.uniform_sampler("image", texture)
    batch.draw(shader)


def _text(text, x, y, color, size):
    blf.size(FONT, size)
    blf.color(FONT, *color)
    blf.position(FONT, x, y, 0)
    blf.draw(FONT, text)


_labels = {}


def _label(name, size, width):
    """(the name cut to width, its width): measured once per name, size and tile width."""
    key = (name, size, int(width))
    if key not in _labels:
        if len(_labels) > 20000:
            _labels.clear()
        cut = paint.fit(name, size, width)
        _labels[key] = cut, paint.measure(cut, size)
    return _labels[key]


def _hover_label(t, grid, i, item, name_h, radius, size):
    """The hovered tile's name and codename (its CID) over the bottom of its picture, on a shade that fades up."""
    x, y, w, _h = grid.rect(i)
    band = Rect(x, y + name_h, w, max(30 * t.s, w * 0.36))
    corners = (0, 0, 0, 0) if name_h else (0, radius, 0, radius)     # the picture's own corners where it ends
    paint.rect(band, (0, 0, 0, 0.82), corners=corners, top=(0, 0, 0, 0))
    name, width = _label(item.get("name") or item["path"].rsplit(".", 1)[-1], size, w - 2 * t.pad)
    code = item.get("id") or item["path"].rsplit(".", 1)[-1]
    small = max(8, size - 2)
    code_cut, code_w = _label(code, small, w - 2 * t.pad)
    blf.size(FONT, size)
    cap = blf.dimensions(FONT, "H")[1]
    base = band.y + t.pad
    _text(code_cut, x + (w - code_w) / 2, base, t.text2, small)
    _text(name, x + (w - width) / 2, base + cap + t.pad, t.text1, size)


def _centered(box, lines, scale):
    """Lines centred in the well (`box`)."""
    blf.size(FONT, 13 * scale)
    y = box.y + box.h / 2 + (len(lines) - 1) * 11 * scale
    for line, color in lines:
        w = blf.dimensions(FONT, line)[0]
        _text(line, box.x + (box.w - w) / 2, y, color, 13 * scale)
        y -= 22 * scale


def draw():
    context = bpy.context
    region, wm = context.region, context.window_manager
    scale = context.preferences.system.ui_scale
    t = ui_blender.theme(context)
    well = ui_blender.well(context.area, region, tabs.current(context.space_data))
    e = client()
    gpu.state.blend_set('ALPHA')
    tab = STATE["tabs"].get(wm.fp_browse_type) or {}
    if e.state != "ready" or not tab.get("finished"):
        text = e.status_text if e.state != "ready" else "Loading %s..." % bpy.path.display_name(wm.fp_browse_type)
        if e.state == "ready" and tab.get("total"):
            text += "  %d / %d" % (tab.get("loaded", 0), tab["total"])
        _centered(well, [(text or "Connecting to FP...", t.text2)], scale)
        STATE["grid"] = None
        STATE["grids"].pop(context.area.as_pointer(), None)     # nothing on screen to click while it loads
        return
    shown = items(wm)
    tile, gap = int(wm.fp_browse_tile * scale), int(t.pad)       # the gap holds a selection ring (t.gap) and a gap
    # tiles grow to fill the well (inset t.pad: the well's radius = the tiles' + t.pad); the scroll bar rides the gap
    box = well.pad(t.pad - gap)
    names = wm.fp_browse_names       # FP's Show Names: off, a tile is its picture and the name shows on hover
    grid = layout.grid(box.w, box.h, tile, gap, len(shown), wm.fp_browse_scroll, box.x, fill=True, y0=box.y,
                       strip=layout.NAME_STRIP if names else 0.0)
    STATE["grid"], STATE["grid_items"] = grid, shown
    key = context.area.as_pointer()
    STATE["grids"][key] = (grid, shown)
    textures.set_visible(len(grid.visible))
    hover = STATE["hover"].get(key)
    if not shown:
        _centered(well, [("Nothing matches “%s”" % wm.fp_browse_search, t.text2)], scale)
        return
    want = []
    name_h = grid.tile_h - grid.tile
    size = max(9, round(min(11, 7 + grid.tile / 32) * scale))
    radius, ring = t.r_tile, t.gap         # the ring around a tile concentric with it
    chosen = set(picked(wm))
    heart = glyph("mdi_Heart")
    line = max(1, int(2 * scale))       # the rarity line on the strip's top: the name centres on the rest
    # every plain shape in one draw, then every icon in one more (out of the atlas); an icon the atlas lacks but
    # another view holds as a texture (the info card's) draws on its own
    rects, icons, loose = paint.Rects(), paint.Icons(), []
    art_corners = (radius, 0, radius, 0) if names else None
    for i in grid.visible:
        item = shown[i]
        x, y, w, h = grid.rect(i)
        box = Rect(x, y, w, h)
        if item["path"] in chosen:
            rects.add(box.pad(-ring), t.accent, radius + ring)
        elif i == hover:
            rects.add(box.pad(-ring), t.hover, radius + ring)
        top, bottom, accent = colors.tile(item.get("rarity"), item.get("colors"))
        art = Rect(x, y + name_h, w, w)
        rects.add(art, bottom, radius, corners=art_corners, top=top)
        if names:
            rects.add(Rect(x, y, w, name_h), t.card, corners=(0, radius, 0, radius))      # a card: the tile shows on the well
            rects.add(Rect(x, y + name_h - line, w, line), accent)
        icon = item.get("icon")
        if not icon:
            continue
        place = textures.slot(icon)
        if place is not None:
            icons.add(art, place, radius, corners=art_corners)
        elif textures.get(icon) is not None:
            loose.append((art, textures.get(icon)))
        else:
            want.append(icon)
    rects.draw()
    icons.draw(textures.pages(), textures.page_size())
    for art, texture in loose:
        paint.rect(art, (0, 0, 0, 0), radius, image=texture, corners=art_corners)
    blf.size(FONT, size)
    cap = blf.dimensions(FONT, "H")[1]
    blf.color(FONT, *t.text1)
    for i in grid.visible:
        item = shown[i]
        x, y, w, h = grid.rect(i)
        if names:
            name, width = _label(item.get("name") or item["path"].rsplit(".", 1)[-1], size, w - 2 * t.pad)
            blf.position(FONT, x + (w - width) / 2, y + (name_h - line - cap) / 2, 0)
            blf.draw(FONT, name)
        if item.get("favorite") and heart is not None:      # FP marks favourites in the tile's corner
            mark = max(10 * t.s, w / 7)
            paint.rect(Rect(x + w - mark - t.gap * 2, y + h - mark - t.gap * 2, mark, mark), (0, 0, 0, 0), 0,
                       image=heart, tint=t.text1)
    if hover is not None and hover < len(shown) and hover in grid.visible:
        _hover_label(t, grid, hover, shown[hover], name_h, radius, size)
    gpu.state.blend_set('ALPHA')
    if grid.max_scroll > 0:      # as the shell's lists draw theirs (kit.end_scroll)
        track = well.pad(t.pad)
        bar_h = max(t.big * 2, track.h * track.h / (track.h + grid.max_scroll))
        bar_y = track.y + (track.h - bar_h) * (1 - grid.scroll / grid.max_scroll)
        paint.rect(Rect(well.right - 3 * t.s - 1, bar_y, 2 * t.s, bar_h), t.text3[:3] + (0.35,), t.s)
    STATE["want_icons"] = want
    gpu.state.blend_set('NONE')
