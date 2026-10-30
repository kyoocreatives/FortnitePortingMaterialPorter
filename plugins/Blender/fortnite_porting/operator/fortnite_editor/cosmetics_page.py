"""The Assets tab's columns in FP's layout: the categories (FP's groups, plural names, its glyphs; groups fold), and
the picked asset's info (its card, a Style card per channel, Import pinned at the bottom)."""
import re

import bpy

from . import colors, import_options, textures
from .state import STATE, picked, selected
from .ui import paint
from .ui.rect import Rect

NAMES = {"SideKick": "Sidekicks", "Kicks": "Kicks", "Toy": "Toys", "Lego": "LEGO"}
GROUP_NAMES = {"Lego": "LEGO"}
OPEN = {}       # category -> unfolded (the first one is, until clicked)


def words(name):
    """"FallGuys" -> "Fall Guys"."""
    return GROUP_NAMES.get(name) or re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name)


def plural(kind):
    """FP's tab names: "LoadingScreen" -> "Loading Screens", "SideKick" -> "Sidekicks"."""
    if kind in NAMES:
        return NAMES[kind]
    text = words(kind)
    return text if text.endswith("s") else text + "s"


def categories_items(current):
    """The category list's picker (the area too narrow for its column): FP's groups as headers, the shown tab marked."""
    cats = STATE["categories"]
    if cats is None:
        return [{"label": "Waiting for FP...", "enabled": False, "id": "waiting"}]
    items = []
    for cat in cats:
        items.append({"header": words(cat["category"])})
        items += [{"label": plural(kind), "icon": kind, "check": kind == current, "action": _browse(kind), "id": kind}
                  for kind in cat["tabs"]]
    return items


def open_categories(context, anchor, side="below"):
    """Pick the assets to show"""
    from .ui import popups
    return popups.open_list(context, anchor, lambda: categories_items(bpy.context.window_manager.fp_browse_type),
                            "categories", "Search categories", width=240, side=side)


def _browse(kind):
    def run(context):
        bpy.ops.fp.browse_tab(type=kind)
    return run


def _fold(category):
    def run(context):
        OPEN[category] = not _open(category)
    return run


def _open(category):
    cats = STATE["categories"] or []
    return OPEN.get(category, bool(cats) and cats[0]["category"] == category)


def _toggle_names(context):
    wm = context.window_manager
    wm.fp_browse_names = not wm.fp_browse_names


def side(ui, r):
    t, wm = ui.t, bpy.context.window_manager
    label = "Hide Names" if wm.fp_browse_names else "Show Names"       # FP's button under its categories
    ui.button("cosmetics:names", r.cut_bottom(t.action), "" if ui.compact else label,
              "mdi_EyeOff" if wm.fp_browse_names else "mdi_Eye", _toggle_names, label)
    r.cut_bottom(t.pad)
    cats = STATE["categories"]
    if cats is None:
        ui.label(r.cut_top(t.row_pitch), "Waiting for FP...", t.text3)
        return
    rows = []
    for n, cat in enumerate(cats):
        if n:
            rows.append(("space", None))        # groups apart from each other
        rows.append(("group", cat))
        if ui.compact or _open(cat["category"]):        # icons only: every group's, a divider between groups
            rows += [("tab", kind) for kind in cat["tabs"]]
    height = sum(t.pad if what == "space" else t.row_pitch for what, _ in rows)
    offset = ui.scroll("cosmetics:categories", r, height)
    y = r.top + offset
    for what, value in rows:
        if what == "space":
            y -= t.pad
            continue
        y -= t.row_pitch
        if y > r.top or y + t.row_pitch < r.y:
            continue
        cell = Rect(r.x, y + (t.row_pitch - t.row) / 2, r.w, t.row)
        if what == "group":
            name = value["category"]
            ui.group("cat:" + name, cell, words(name), _open(name), _fold(name))
        else:
            ui.row("tab:" + value, cell, plural(value), value, value == wm.fp_browse_type, _browse(value))
    ui.end_scroll()


def info(ui, r):
    t, wm = ui.t, bpy.context.window_manager
    item = selected(wm)
    if item is None:        # the column stays (a click must not move the grid under a double-click): a quiet state
        ui.label(r, "Nothing selected", t.text3, t.text, "center")
        return
    count = len(picked(wm))
    _export_row(ui, r.cut_bottom(t.action), count)         # the cards' width: one right edge down the column
    r.cut_bottom(t.pad)
    card = r.cut_top(64 * t.s + 2 * t.pad)
    _card(ui, card, item)
    r.cut_top(t.pad)
    if count > 1:       # the card is the last pick's; the rest import with their own style picks
        ui.label(r.cut_top(t.row_pitch).pad(t.gap, 0), "%d selected  ·  styles of %s" % (count, item.get("name") or ""),
                 t.text2, t.caption)
    _styles(ui, r, item)


def _card(ui, r, item):
    t = ui.t
    ui.panel(r)
    inner = r.pad(t.pad)

    def favorite(context):
        bpy.ops.fp.browse_favorite(path=item["path"])
    off = t.r_card - t.r_small      # in the card's corner, concentric with it
    fav = Rect(r.right - off - t.row, r.top - off - t.row, t.row, t.row)
    ui.button("info:favorite", fav, icon="mdi_Heart", action=favorite, style="flat", selected=bool(item.get("favorite")),
              tip="Remove from Favorites" if item.get("favorite") else "Add to Favorites")
    inner.cut_right(r.right - t.pad - fav.x + t.gap)      # the text clear of the heart
    pic = inner.cut_left(64 * t.s)
    top, bottom, _accent = colors.tile(item.get("rarity"), item.get("colors"))
    paint.rect(pic, bottom, t.r_card - t.pad)      # concentric with the card round it
    tex = textures.get(item.get("icon")) if item.get("icon") else None
    if tex is not None:
        paint.rect(pic, (0, 0, 0, 0), t.r_card - t.pad, image=tex)
    inner.cut_left(t.pad)
    facts = "  ·  ".join(f for f in (item.get("rarity"), item.get("series")) if f)
    lines = [(item.get("name") or item["path"].rsplit(".", 1)[-1], t.text1, t.title)]
    lines += [(facts, t.text2, t.caption)] if facts else []
    lines += [(line, t.text3, t.caption) for line in paint.wrap(item.get("description") or "", t.caption, inner.w, 2)]
    # the text block centred on the picture beside it
    heights = [size + t.gap * 2 for _, _, size in lines]
    y = inner.y + (inner.h + sum(heights)) / 2
    for (text, color, size), h in zip(lines, heights):
        y -= h
        paint.text_in(text, Rect(inner.x, y, inner.w, h), color, size)


def _styles(ui, r, item):
    t = ui.t
    channels = STATE["styles"].get(item["path"])
    if channels == "asked" or channels is None:
        ui.label(r.cut_top(t.row_pitch), "Reading styles...", t.text3, t.caption, "center")
        return
    picks = STATE["picks"].get(item["path"], {})
    shown = [c for c in channels if len(c["options"]) >= 2]
    h = t.row_pitch + 40 * t.s          # a channel: its name (Blender's label row), its dropdown
    height = len(shown) * (h + t.pad)
    offset = ui.scroll("cosmetics:styles", r, height)
    y = r.top + offset
    for channel in shown:
        y -= h
        inner = Rect(r.x, y, r.w, h)
        y -= t.pad
        if inner.top < r.y or inner.y > r.top:
            continue
        ui.label(inner.cut_top(t.row_pitch).pad(t.gap, 0), channel["channel"], t.text2, t.text)
        current = picks.get(channel["channel"], channel["selected"])
        option = next((o for o in channel["options"] if o["name"] == current), channel["options"][0])
        _dropdown(ui, inner, item, channel, option)
    ui.end_scroll()


def _dropdown(ui, r, item, channel, option):
    t = ui.t
    key = "style:%s:%s" % (item["path"], channel["channel"])
    paint.rect(r, t.hover if ui.hot(key) else t.control[:3] + (0.6,), t.r_small)
    inner = r.pad(t.gap)
    thumb = inner.cut_left(inner.h)
    file = option.get("icon")
    if file and textures.add(file, file):
        paint.rect(thumb, (0, 0, 0, 0), t.r_small - t.gap, image=textures.get(file))
    inner.cut_left(t.pad)
    ui.icon(inner.cut_right(t.icon), "mdi_ChevronDown", t.text2)
    paint.text_in(option["name"], inner, t.text1, t.text)

    def open_menu(context):
        from .asset_tab import open_styles
        open_styles(context, r, item["path"], channel)
    from .ui.frame import Hit
    ui.frame.add(Hit(key, r, ui.clip, "button", open_menu, None, "Pick this style"))


def _export_row(ui, r, count=1):
    t = ui.t
    gear = r.cut_right(r.h)

    def settings(context):
        from .settings_tab import open_settings
        open_settings(context, gear, "above")
    ui.button("info:settings", gear, icon="mdi_FileCog", action=settings, tip="Export Options")
    r.cut_right(t.gap)
    import_options.button(ui, r.cut_right(r.h), "info:import_options", "assets")
    r.cut_right(t.gap)

    def run(context):
        bpy.ops.fp.browse_import()
    busy = STATE["busy"]
    label = busy or import_options.import_label("Import %d" % count if count > 1 else "Import")
    ui.button("info:import", r, label, "mdi_Download", run, "Import into this scene (or double-click the tile)",
              enabled=not busy, style="accent")
