"""A pop-up's fields: checkbox, dropdown, number (drag, or click to type), text, colour swatch; prop() picks one from a
property's RNA. Column lays them out top-down like a Blender panel (label left, field right). Writes run as actions
(operator context); a typed value lands through the store's on_commit (commit())."""
import bpy

from . import paint, popup
from .frame import Hit
from .rect import Rect
from .textedit import Text, number as parse_number

_commits = {}       # field id -> fn(Text): what keeping a typed value does (registered each draw)
_dragged = set()    # number fields a press dragged (their release isn't a click)
SPLIT = 0.42        # the label's share of a property row


def commit(id, text):
    """The store's on_commit: the focused field's typed text, kept."""
    fn = _commits.get(id)
    if fn is not None:
        fn(text)


def _undo(owner, name):
    data = getattr(owner, "id_data", None)
    if data is not None and not isinstance(data, (bpy.types.WindowManager, bpy.types.Screen)):
        try:
            bpy.ops.ed.undo_push(message="Set %s" % name)
        except RuntimeError:
            pass


def write(owner, name, value, on_change=None):
    """The action of a field: set the property (one undo step for scene data), then on_change(context)."""
    def run(context):
        setattr(owner, name, value)
        _undo(owner, name)
        if on_change is not None:
            on_change(context)
    return run


# ------------------------------------------------------------------ widgets

def _box(ui, id, r, hot=None):
    t = ui.t
    hot = ui.hot(id) if hot is None else hot
    fill = tuple(min(1.0, c * 1.12) for c in t.control[:3]) + (1.0,) if hot else t.control
    paint.rect(r, fill, t.r_small)


def checkbox(ui, id, r, value, text, action, enabled=True, tip=""):
    t = ui.t
    box = Rect(r.x, r.y + (r.h - t.icon) / 2, t.icon, t.icon)
    fill = t.accent if value else (t.control if not ui.hot(id) else t.hover[:3] + (1.0,))
    paint.rect(box, fill if enabled else fill[:3] + (0.4,), 4 * t.s)
    if value:
        ui.icon(box, "mdi_Check", t.text1 if enabled else t.text3)
    paint.text_in(text, Rect(box.right + t.pad, r.y, r.right - box.right - t.pad, r.h),
                  t.text1 if enabled else t.text3, t.text)
    if enabled:
        ui.frame.add(Hit(id, r, ui.clip, "button", action, None, tip))


def dropdown(ui, id, r, text, action, icon=None, enabled=True, tip=""):
    t = ui.t
    _box(ui, id, r)
    inner = r.pad(t.pad, 0)
    ui.icon(inner.cut_right(t.icon), "mdi_ChevronDown", t.text2)
    if icon:
        ui.icon(inner.cut_left(t.icon), icon, t.text2)
        inner.cut_left(t.gap)
    paint.text_in(text, inner, t.text1 if enabled else t.text3, t.text)
    if enabled:
        ui.frame.add(Hit(id, r, ui.clip, "button", action, None, tip))


def _listen(context):
    from . import blender     # (it imports this module)
    blender.listen(context)


def _focused(ui, id, current):
    """The field's Text when it has the keys (made from `current` when Tab just gave it them), else None."""
    store = ui.store
    if store.focus != id:
        return None
    if store.focus_text is None:
        popup.focus(store, id, Text(current))
    return store.focus_text


def _caret(ui, r, text, size):
    t = ui.t
    a, b = text.selection()
    x0 = r.x + paint.measure(text.value[:a], size)
    if a != b:
        paint.rect(Rect(x0, r.y + t.gap, paint.measure(text.value[a:b], size), r.h - 2 * t.gap), t.accent[:3] + (0.6,))
    cx = r.x + paint.measure(text.value[:text.cursor], size)
    paint.rect(Rect(cx, r.y + t.gap, max(1.0, t.s), r.h - 2 * t.gap), t.text1)


def text_box(ui, id, r, value, on_set, placeholder="", icon=None, live=False):
    """A text field: a click gives it the keys; Enter / Tab / a click elsewhere keeps the text (on_set(context, s)).
    `live`: the field always has the keys and on_set hears every edit (a pop-up's search)."""
    t = ui.t
    text = live if isinstance(live, Text) else _focused(ui, id, value)
    paint.rect(r, t.well if text is not None else t.control, t.r_small,
               edge=t.accent if text is not None and not live else None, border=t.s)
    inner = r.pad(t.pad, 0)
    if icon:
        ui.icon(inner.cut_left(t.icon), icon, t.text3)
        inner.cut_left(t.gap)
    shown = text.value if text is not None else value
    if not shown and placeholder:       # after the caret, never under it
        paint.text_in(placeholder, inner.pad(3 * t.s if text is not None else 0, 0, 0, 0), t.text3, t.text)
    else:
        paint.text_in(shown, inner, t.text1, t.text)
    if text is not None:
        _caret(ui, inner, text, t.text)
    if live:
        return

    def keep(text):
        on_set(bpy.context, text.value)
    _commits[id] = keep

    def click(context):
        popup.focus(ui.store, id, Text(value))
        _listen(context)
    ui.frame.add(Hit(id, r, ui.clip, "button", click, None, ""))


def number(ui, id, r, value, on_set, integer=False, lo=None, hi=None, step=None, slider=False, fmt=None,
           label="", enabled=True):
    """A number: drag left / right changes it, a click types it (arithmetic allowed). `slider`: a fill shows where it
    is between lo and hi."""
    t = ui.t
    fmt = fmt or ("%d" if integer else "%.3f")
    text = _focused(ui, id, fmt % value)
    if text is not None:
        paint.rect(r, t.well, t.r_small, edge=t.accent, border=t.s)
        inner = r.pad(t.pad, 0)
        paint.text_in(text.value, inner, t.text1, t.text)
        _caret(ui, inner, text, t.text)
    else:
        _box(ui, id, r, ui.hot(id) or ui.pressed(id))
        if slider and lo is not None and hi is not None and hi > lo:
            k = min(max((value - lo) / (hi - lo), 0.0), 1.0)
            paint.rect(Rect(r.x, r.y, r.w * k, r.h), t.accent[:3] + (0.75,), t.r_small)
        inner = r.pad(t.pad, 0)
        if label:
            paint.text_in(label, inner, t.text2 if enabled else t.text3, t.text)
            paint.text_in(fmt % value, inner, t.text1 if enabled else t.text3, t.text, "right")
        else:
            paint.text_in(fmt % value, inner, t.text1 if enabled else t.text3, t.text, "center")
    if not enabled:
        return

    def keep(text):
        v = parse_number(text.value, integer, lo, hi)
        if v is not None:
            on_set(bpy.context, v)
    _commits[id] = keep
    if step is None:
        step = ((hi - lo) / max(r.w, 1.0)) if slider and lo is not None and hi is not None else (0.25 if integer else 0.01)

    def drag(context, dx, dy):
        if abs(dx) < 3 * t.s and id not in _dragged:
            return
        _dragged.add(id)
        v = value + dx * step
        v = int(round(v)) if integer else v
        if lo is not None:
            v = max(lo, v)
        if hi is not None:
            v = min(hi, v)
        on_set(context, v)

    def click(context):
        if id in _dragged:
            _dragged.discard(id)
            return
        popup.focus(ui.store, id, Text(fmt % value))
        _listen(context)
    ui.frame.add(Hit(id, r, ui.clip, "button", click, None, "Drag, or click to type", None, drag))


def swatch(ui, id, r, rgba, action, tip="Pick a colour"):
    """A colour (display values) that opens the colour picker."""
    t = ui.t
    paint.rect(r, tuple(rgba[:3]) + (1.0,), t.r_small, edge=t.text1 if ui.hot(id) else t.divider, border=t.s)
    ui.frame.add(Hit(id, r, ui.clip, "button", action, None, tip))


# ------------------------------------------------------------------ RNA

def enum_items(owner, name):
    """[(identifier, name, icon)]: a static enum's items, or a dynamic one's identifiers (Blender lists them in the
    error a wrong value raises)."""
    prop = owner.bl_rna.properties[name]
    items = [(i.identifier, i.name, i.icon) for i in prop.enum_items]
    if items:
        return items
    try:
        setattr(owner, name, "\x01")
    except TypeError as e:
        found = str(e).split("not found in (", 1)
        if len(found) == 2:
            return [(s.strip(" '"), s.strip(" '"), 'NONE') for s in found[1].rstrip(")").split(",") if s.strip(" '")]
    return []


class Column:
    """Rows top-down in `r`, like a Blender panel: prop() (label left, field right), label(), button(), section()."""

    def __init__(self, ui, r, p, open_menu=None):
        self.ui, self.r, self.p = ui, r, p
        self.y = r.top
        self.open_menu = open_menu      # fn(context, anchor, items, parent popup): an enum's menu (popups.menu_at)
        self.enabled = True

    @property
    def used(self):
        return self.r.top - self.y

    def take(self, h=None):
        t = self.ui.t
        h = t.row if h is None else h
        row = Rect(self.r.x, self.y - h, self.r.w, h)
        self.y -= h + t.gap
        return row

    def space(self, h=None):
        self.y -= self.ui.t.pad if h is None else h

    def label(self, text, color=None, size=None, icon=None):
        t = self.ui.t
        r = self.take()
        if icon:
            self.ui.icon(r.cut_left(t.icon), icon, color or t.text2)
            r.cut_left(t.gap)
        paint.text_in(text, r, color or t.text2, size or t.text)

    def separator(self):
        t = self.ui.t
        self.y -= t.gap
        self.ui.divider(self.r.x, self.r.right, self.y)
        self.y -= t.gap + t.s

    def section(self, id, title, default=True):
        """A fold header; True when open (the pop-up remembers it)."""
        t = self.ui.t
        key = "fold:" + id
        open_ = self.p.state.get(key, default)
        r = self.take()

        def flip(context):
            self.p.state[key] = not open_
        self.ui.icon(r.cut_left(t.icon), "mdi_ChevronDown" if open_ else "mdi_ChevronRight", t.text3)
        r.cut_left(t.gap)
        paint.text_in(title, r, t.text2 if self.ui.hot(key) else t.text1, t.text)
        self.ui.frame.add(Hit(key, Rect(self.r.x, r.y, self.r.w, r.h), self.ui.clip, "button", flip, None, ""))
        return open_

    def button(self, id, text, icon=None, action=None, style="control", enabled=True, tip=""):
        self.ui.button(id, self.take(), text, icon, action, tip, enabled=enabled and self.enabled, style=style)

    def split(self, label=""):
        """(label rect, field rect) of a new row."""
        r = self.take()
        if not label:
            return None, r
        left = r.cut_left(r.w * SPLIT)
        r.cut_left(self.ui.t.pad)
        paint.text_in(label, left, self.ui.t.text2 if self.enabled else self.ui.t.text3, self.ui.t.text, "right")
        return left, r

    def prop(self, owner, name, text=None, slider=False, on_change=None, id=None, items=None, expand=False):
        """A property's field by its RNA type; `items`: a dynamic enum's [(id, name, icon)]."""
        ui, t = self.ui, self.ui.t
        prop = owner.bl_rna.properties[name]
        id = id or "prop:%s:%s" % (getattr(owner, "as_pointer", lambda: 0)(), name)
        label = prop.name if text is None else text
        value = getattr(owner, name)
        on = self.enabled and not prop.is_readonly
        kind = prop.type
        if kind == 'BOOLEAN' and not getattr(prop, "is_array", False):
            _, r = self.split("")
            r.cut_left(self.r.w * SPLIT + t.pad)
            checkbox(ui, id, r, value, label, write(owner, name, not value, on_change), on)
            return
        _, r = self.split(label)
        if kind == 'ENUM' and not prop.is_enum_flag:
            choices = items or enum_items(owner, name)
            if expand:
                self._expanded(id, r, owner, name, choices, value, on_change, on)
                return
            current = next((c for c in choices if c[0] == value), (value, value, None))

            def open_(context, r=r):
                if self.open_menu is not None:
                    self.open_menu(context, r, [{"label": c[1], "check": c[0] == value,
                                                 "action": write(owner, name, c[0], on_change)} for c in choices],
                                   self.p)
            dropdown(ui, id, r, current[1], open_, enabled=on)
        elif kind in ('INT', 'FLOAT') and getattr(prop, "subtype", "") in ('COLOR', 'COLOR_GAMMA'):
            from . import colorpick
            linear = prop.subtype == 'COLOR'
            rgba = tuple(value)
            shown = colorpick.linear_to_srgb(rgba[:3]) if linear else rgba[:3]

            def pick(context, r=r):
                colorpick.open_for(context, r, owner, name, self.p, on_change)
            swatch(ui, id, r, shown, pick)
        elif kind in ('INT', 'FLOAT') and not getattr(prop, "is_array", False):
            integer = kind == 'INT'
            lo, hi = prop.hard_min, prop.hard_max
            factor = prop.subtype in ('FACTOR', 'PERCENTAGE') or slider
            fmt = "%d" if integer else "%%.%df" % max(0, min(prop.precision, 4))
            if prop.subtype == 'PERCENTAGE':
                fmt += "%%"

            def set_(context, v):
                setattr(owner, name, v)
                _undo(owner, name)
                if on_change is not None:
                    on_change(context)
            number(ui, id, r, value, set_, integer, lo, hi, None, factor and prop.soft_max - prop.soft_min < 1e6,
                   fmt, enabled=on)
        elif kind == 'STRING':
            def set_s(context, s):
                setattr(owner, name, s)
                if on_change is not None:
                    on_change(context)
            text_box(ui, id, r, value, set_s)
        else:
            paint.text_in(str(value), r, t.text3, t.text)

    def _expanded(self, id, r, owner, name, choices, value, on_change, on):
        """An enum as a row of buttons (Blender's expand=True)."""
        t = self.ui.t
        w = (r.w - t.gap * (len(choices) - 1)) / max(1, len(choices))
        for n, c in enumerate(choices):
            cell = Rect(r.x + n * (w + t.gap), r.y, w, r.h)
            self.ui.button("%s:%s" % (id, c[0]), cell, c[1], action=write(owner, name, c[0], on_change),
                           selected=c[0] == value, enabled=on)
