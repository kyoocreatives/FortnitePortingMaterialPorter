"""The pop-up kinds, drawn with the kit: menu, list (search + rows), tiles (search + a grid of big thumbnails), panel
(fields), dialog. Each open_*() builds a popup.Popup and opens it in the context's area (ui/blender.open).

Items are dicts (or a callable giving them, asked at every draw so they follow the data):
    {"label", "action": fn(context), "icon": "mdi_X", "detail", "check": True/False (a mark; None: none),
     "enabled", "tip", "image": an icon file (tiles), "id", "dim": grey though pickable,
     "depth": a tree row's indent, "fold": (open, fn(context)): its chevron} / {"header": "Title"} / {"sep": True}
"""
from . import paint
from .fields import Column, text_box
from .frame import Hit
from .popup import Popup
from .rect import Rect

ZOOM = {}           # tile picker -> its tile size (px at UI scale 1), as Ctrl+wheel left it
TILE, TILE_MIN, TILE_MAX = 96, 48, 256
LABELS_FROM = 72        # px: tiles narrower than this show no name under them


def _items(items):
    return items() if callable(items) else items


def _run(p, action):
    """A click on an item: the menu closes (unless it stays open), then the action runs."""
    def run(context):
        if not p.keep_open and p.stack is not None:
            p.stack.close_all()
        action(context)
    return run


def matches(items, query):
    """The items whose label has the query's words (any case); a header stays when something under it does."""
    words = query.lower().split()
    if not words:
        return items
    out, header = [], None
    for it in items:
        if "header" in it:
            header = it
        elif "sep" not in it and all(w in it.get("label", "").lower() for w in words):
            if header is not None:
                out.append(header)
                header = None
            out.append(it)
    return out


def _resize(ui, p, w, h):
    if p.size != (w, h):
        p.size = (w, h)
        ui.again = True         # placed with the old size: one more draw


def _item_h(t, it):
    return 2 * t.gap + t.s if "sep" in it else t.row


def menu_width(t, items, title=""):
    widest = max([paint.measure(it.get("label", it.get("header", "")), t.text)
                  + (paint.measure(it["detail"], t.caption) + t.big if it.get("detail") else 0)
                  for it in items] + [paint.measure(title, t.text), 120 * t.s])
    return widest + 2 * t.icon + 4 * t.pad


def _search_row(ui, p, r, placeholder):
    text_box(ui, p.id + ":search", r, "", None, placeholder, "mdi_Magnify", live=p.query)


def _rows(ui, p, r, items, has_icons):
    """The items as rows in `r` (scrolled when they don't fit), keyboard item followed."""
    t = ui.t
    p.nav = []
    content = sum(_item_h(t, it) for it in items)
    sid = p.id + ":scroll"
    if "opened" not in p.state:         # a new pop-up: at the top, or at the current item
        p.state["opened"] = True
        y = 0
        ui.store.scroll[sid] = 0.0
        for it in items:
            if it.get("check"):
                ui.store.scroll[sid] = max(0.0, y - r.h / 2)
                break
            y += _item_h(t, it)
    if p.cursor is not None and p.state.get("followed") != p.cursor:       # the keys moved: keep it in view
        p.state["followed"] = p.cursor
        y = 0
        for n, it in enumerate(items):
            if "%s:%s" % (p.id, it.get("id", n)) == p.cursor:
                off = ui.store.offset(sid)
                if y < off:
                    ui.store.scroll[sid] = y
                elif y + t.row > off + r.h:
                    ui.store.scroll[sid] = y + t.row - r.h
                break
            y += _item_h(t, it)
    offset = ui.scroll(sid, r, content)
    if content > r.h + 0.5:         # rows clear of the scroll bar
        r = Rect(r.x, r.y, r.w - 6 * ui.t.s, r.h)
    y = r.top + offset
    for n, it in enumerate(items):
        h = _item_h(t, it)
        y -= h
        if y > r.top or y + h < r.y:
            if "label" in it and "header" not in it:
                p.nav.append(("%s:%s" % (p.id, it.get("id", n)), it.get("action") if it.get("enabled", True) else None))
            continue
        row = Rect(r.x, y, r.w, h)
        if "sep" in it:
            ui.divider(row.x + t.pad, row.right - t.pad, row.y + h / 2)
        elif "header" in it:
            paint.text_in(it["header"], row.pad(t.pad, 0), t.text3, t.caption)
        else:
            _item(ui, p, row, it, n, has_icons)
    ui.end_scroll()


def _item(ui, p, r, it, n, has_icons):
    t = ui.t
    id = "%s:%s" % (p.id, it.get("id", n))
    on = it.get("enabled", True) and it.get("action") is not None
    lit = ui.hot(id) or (p.cursor == id and ui.store.hot is None)
    if lit and on:
        paint.rect(r, t.accent[:3] + (0.55,), t.r_small)
    inner = r.pad(t.pad, 0)
    color = t.text1 if on and not it.get("dim") else t.text3
    tree = "depth" in it
    fold = it.get("fold")
    if tree:
        chevron = inner.cut_left(t.icon)
        if fold:
            ui.icon(chevron, "mdi_ChevronDown" if fold[0] else "mdi_ChevronRight", t.text1 if ui.hot(id + ":fold") else t.text3)
        inner.cut_left(it["depth"] * t.big)
    if has_icons:
        cell = inner.cut_left(t.icon)
        if it.get("check"):
            ui.icon(cell, "mdi_Check", color)
        elif it.get("icon"):
            ui.icon(cell, it["icon"], color if lit else t.text2)
        inner.cut_left(t.pad)
    if it.get("detail"):
        dw = min(paint.measure(it["detail"], t.caption) + t.pad, inner.w * 0.45)
        paint.text_in(it["detail"], inner.cut_right(dw), t.text3, t.caption, "right")
    paint.text_in(paint.fit(it["label"], t.text, inner.w) if tree else it.get("label", ""), inner, color, t.text)
    p.nav.append((id, it.get("action") if on else None))
    ui.frame.add(Hit(id, r, ui.clip, "button", _run(p, it["action"]) if on else None, None, it.get("tip", "")))
    if fold:        # over the row's left end: the rest of the row picks
        ui.frame.add(Hit(id + ":fold", Rect(r.x, r.y, chevron.right - r.x, r.h), ui.clip, "button", fold[1], None, ""))


# ------------------------------------------------------------------ kinds

def menu(items, title=""):
    """A menu: rows, type to filter (the filter shows in its title row)."""
    def draw(ui, r, p):
        t = ui.t
        all_ = _items(items)
        shown = matches(all_, p.query.value) if p.query is not None else all_
        typed = p.query is not None and p.query.value
        head = title or typed
        h = sum(_item_h(t, it) for it in shown) + (t.row + t.gap if head else 0)
        _resize(ui, p, max(p.size[0], menu_width(t, all_, title)), h + 2 * t.pad)
        if head:
            row = r.cut_top(t.row)
            r.cut_top(t.gap)
            if typed:
                ui.icon(row.cut_left(t.icon), "mdi_Magnify", t.text3)
                row.cut_left(t.gap)
            paint.text_in(typed or title, row.pad(t.gap, 0), t.text1 if typed else t.text3, t.text if typed else t.caption)
        has_icons = any(it.get("icon") or it.get("check") for it in shown)
        _rows(ui, p, r, shown, has_icons)
    return draw


def filtered(p, all_, query):
    """matches(), kept between draws: again only when the list or the query changed, and a longer query filters
    what the shorter one kept (a list of every asset is 300k rows)."""
    st = p.state
    if st.get("f_list") is not all_:
        st.update(f_list=all_, f_query=None, f_lows=None)
    if st["f_query"] == query:
        return st["f_out"]
    if not any("header" in it or "sep" in it for it in all_[:1]) and len(all_) > 2000:      # a plain big list
        if st["f_lows"] is None:
            st["f_lows"] = [it.get("label", "").lower() for it in all_]
        words = query.lower().split()
        prev = st["f_query"]
        base = st["f_idx"] if prev is not None and query.startswith(prev) and prev.strip() else range(len(all_))
        lows = st["f_lows"]
        idx = [i for i in base if all(w in lows[i] for w in words)] if words else range(len(all_))
        st.update(f_idx=idx, f_out=[all_[i] for i in idx] if words else all_)
    else:
        st["f_out"] = matches(all_, query)
    st["f_query"] = query
    return st["f_out"]


SHOWN_MAX = 500         # rows a list draws at most: past that, keep typing


def listing(items, placeholder="Search"):
    """A list picker: a search field (it has the keys) over rows."""
    def draw(ui, r, p):
        t = ui.t
        shown = filtered(p, _items(items), p.query.value)
        _search_row(ui, p, r.cut_top(t.row), placeholder)
        r.cut_top(t.pad)
        if not shown:
            p.nav = []      # (else Enter picks a row of the last draw)
            paint.text_in("Nothing matches", r.cut_top(t.row), t.text3, t.text, "center")
            return
        if len(shown) > SHOWN_MAX:
            shown = list(shown[:SHOWN_MAX]) + [{"header": "%d more: type to narrow" % (len(shown) - SHOWN_MAX)}]
        most = p.state.setdefault("most", p.size[1])       # the height asked for: short lists shrink to fit
        fixed = 3 * t.pad + t.row
        content = sum(_item_h(t, it) for it in shown)
        if content > most - fixed:      # scrolled: whole rows, none cut in half at the bottom
            content = 0
            for it in shown:
                if content + _item_h(t, it) > most - fixed:
                    break
                content += _item_h(t, it)
        _resize(ui, p, p.size[0], fixed + content)
        _rows(ui, p, r, shown, any(it.get("icon") or it.get("check") for it in shown[:50]))
    return draw


def zoom(p, notches):
    """Ctrl+wheel over a tile picker: bigger or smaller tiles, remembered for this picker."""
    key = p.state.get("zoom")
    if key is None:
        return False
    ZOOM[key] = min(max(ZOOM.get(key, TILE) - notches * 16, TILE_MIN), TILE_MAX)
    return True


def tiles(items, placeholder="Search", key="tiles"):
    """A tile picker: a search field over a grid of big thumbnails, names under them, the current one ringed."""
    def draw(ui, r, p):
        from .. import textures
        t = ui.t
        p.state["zoom"] = key
        shown = [it for it in matches(_items(items), p.query.value) if "label" in it]
        _search_row(ui, p, r.cut_top(t.row), placeholder)
        r.cut_top(t.pad)
        size = ZOOM.get(key, TILE) * t.s
        cols = max(1, int((r.w - 6 * t.s + t.pad) // (size + t.pad)))      # (room for the scroll bar)
        p.cols = cols
        cw = (r.w - 6 * t.s - t.pad * (cols - 1)) / cols
        named = cw >= LABELS_FROM * t.s        # small tiles: the name is the tile's tooltip only
        ch = cw + (t.row if named else 0)
        rows = (len(shown) + cols - 1) // cols
        most = p.state.setdefault("most", p.size[1])       # the height asked for: shrinks to fit, never grows past
        _resize(ui, p, p.size[0], min(most, 2 * t.pad + t.row + max(rows, 1) * (ch + t.pad)))
        p.nav = []
        sid = p.id + ":scroll"
        if "opened" not in p.state:     # a new picker: at the top, or with the current pick in view
            p.state["opened"] = True
            n = next((i for i, it in enumerate(shown) if it.get("check")), 0)
            ui.store.scroll[sid] = max(0.0, (n // cols) * (ch + t.pad) - (r.h - ch) / 2)
        if p.cursor is not None and p.state.get("followed") != p.cursor:
            p.state["followed"] = p.cursor
            n = next((i for i, it in enumerate(shown) if "%s:%s" % (p.id, it.get("id", i)) == p.cursor), None)
            if n is not None:
                y, off = (n // cols) * (ch + t.pad), ui.store.offset(sid)
                if y < off:
                    ui.store.scroll[sid] = y
                elif y + ch > off + r.h:
                    ui.store.scroll[sid] = y + ch - r.h
        if not shown:
            paint.text_in("Nothing matches", r.cut_top(t.row), t.text3, t.text, "center")
            return
        offset = ui.scroll(sid, r, rows * (ch + t.pad) - t.pad)
        for n, it in enumerate(shown):
            id = "%s:%s" % (p.id, it.get("id", n))
            p.nav.append((id, it.get("action")))
            row, col = divmod(n, cols)
            cell = Rect(r.x + col * (cw + t.pad), r.top + offset - row * (ch + t.pad) - ch, cw, ch)
            if cell.y > r.top or cell.top < r.y:
                continue
            lit = ui.hot(id) or (p.cursor == id and ui.store.hot is None)
            if lit:
                paint.rect(cell, t.hover, t.r_small)
            thumb = Rect(cell.x + t.gap, cell.top - cw + t.gap, cw - 2 * t.gap, cw - 2 * t.gap)
            paint.rect(thumb, t.well, t.r_small - t.gap)
            file = it.get("image")
            if file and textures.add(file, file):
                paint.rect(thumb, (0, 0, 0, 0), t.r_small - t.gap, image=textures.get(file))
            else:       # no art: a None / Stock option (no file), or its file isn't readable (yet)
                ui.icon(thumb, it.get("icon") or ("mdi_Image" if file else "mdi_Cancel"), t.text2)
            if it.get("check"):
                paint.rect(cell, (0, 0, 0, 0), t.r_small, edge=t.accent, border=2 * t.s)
            if named:
                paint.text_in(paint.fit(it["label"], t.caption, cell.w - 2 * t.gap), Rect(cell.x, cell.y, cell.w, t.row),
                              t.text1 if lit or it.get("check") else t.text2, t.caption, "center")
            ui.frame.add(Hit(id, cell, ui.clip, "button", _run(p, it["action"]) if it.get("action") else None, None,
                             it["label"]))
        ui.end_scroll()
    return draw


def panel(content, title=""):
    """Fields and buttons, laid out by a Column; it grows to what they take, and scrolls past the region's height."""
    def draw(ui, r, p):
        t = ui.t
        if title:
            paint.text_in(title, r.cut_top(t.row).pad(t.gap, 0), t.text1, t.text)
            r.cut_top(t.gap)
        used = p.state.get("used", 0.0)         # the content's height, as the last draw laid it out
        offset = ui.scroll(p.id + ":scroll", r, used)
        w = r.w - (6 * t.s if used > r.h + 0.5 else 0)        # room for the scroll bar
        col = Column(ui, Rect(r.x, r.y, w, r.h + offset), p, open_menu=open_menu_at)
        content(ui, col)
        ui.end_scroll()
        if abs(col.used - used) > 0.5:
            p.state["used"] = col.used
            ui.again = True
        _resize(ui, p, p.size[0], col.used + 2 * t.pad + ((t.row + t.gap) if title else 0))
    return draw


def dialog(content, ok="OK", cancel="Cancel"):
    """A centred, modal panel with its buttons: OK (Enter) runs on_accept, Cancel (Esc) on_cancel."""
    def draw(ui, r, p):
        t = ui.t
        if p.title:
            paint.text_in(p.title, r.cut_top(t.row), t.text1, t.title)
            r.cut_top(t.gap)
        buttons = r.cut_bottom(t.action)
        r.cut_bottom(t.pad)
        col = Column(ui, r, p, open_menu=open_menu_at)
        if content is not None:
            content(ui, col)

        def done(fn):
            def run(context):
                p.stack.close(p)
                if fn is not None:
                    fn(context)
            return run
        bw = max(paint.measure(ok, t.text), paint.measure(cancel, t.text), 60 * t.s) + 2 * t.big
        ui.button(p.id + ":ok", buttons.cut_right(bw), ok, action=done(p.on_accept), style="accent")
        buttons.cut_right(t.pad)
        if cancel:
            ui.button(p.id + ":cancel", buttons.cut_right(bw), cancel, action=done(p.on_cancel))
        head = (t.row + t.pad) if p.title else 0
        _resize(ui, p, p.size[0], col.used + head + t.action + 3 * t.pad)
    return draw


# ------------------------------------------------------------------ opening

def _open(context, p):
    from . import blender
    return blender.open(context, p)


def open_menu(context, anchor, items, id, title="", side="below", parent=None, search=True, keep_open=False):
    from .blender import theme
    t = theme(context)
    first = _items(items)
    p = Popup(id, menu(items, title), anchor, side, (menu_width(t, first, title), 200 * t.s), "menu", parent,
              search=search, keep_open=keep_open)
    return _open(context, p)


def open_menu_at(context, anchor, items, parent):
    """An enum field's menu, a child of the panel it's in."""
    return open_menu(context, anchor, items, parent.id + ":enum", parent=parent.id, side="below")


def open_list(context, anchor, items, id, placeholder="Search", width=280, height=360, side="below", parent=None):
    from .blender import theme
    t = theme(context)
    return _open(context, Popup(id, listing(items, placeholder), anchor, side, (width * t.s, height * t.s), "list",
                                parent, search=True))


def open_tiles(context, anchor, items, id, placeholder="Search", width=520, height=440, side="left", key="tiles",
               parent=None):
    from .blender import theme
    t = theme(context)
    return _open(context, Popup(id, tiles(items, placeholder, key), anchor, side, (width * t.s, height * t.s),
                                "tiles", parent, search=True))


def open_panel(context, anchor, content, id, title="", width=300, side="below", parent=None):
    from .blender import theme
    t = theme(context)
    return _open(context, Popup(id, panel(content, title), anchor, side, (width * t.s, 120 * t.s), "panel", parent,
                                keep_open=True))


def open_dialog(context, title, content, on_accept, id="dialog", ok="OK", cancel="Cancel", on_cancel=None, width=360):
    from .blender import theme
    t = theme(context)
    return _open(context, Popup(id, dialog(content, ok, cancel), None, "center", (width * t.s, 140 * t.s), "dialog",
                                modal=True, on_accept=on_accept, on_cancel=on_cancel, title=title))


def confirm(context, title, text, on_accept, ok="OK", id="confirm"):
    """Blender's invoke_confirm, ours: a line of text, OK / Cancel."""
    def content(ui, col):
        t = ui.t
        for line in paint.wrap(text, t.text, col.r.w, 4) if text else ():
            paint.text_in(line, col.take(round(t.text * 1.5)), t.text2, t.text)
            col.y += t.gap      # text lines: no row gap between them
    return open_dialog(context, title, content, on_accept, id, ok)
