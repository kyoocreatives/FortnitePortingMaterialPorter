"""The widgets, immediate mode: each call draws its widget and records what a click on it does in the area's frame.
Actions are callables run later in operator context (a draw can't write Blender data)."""
import os

import bpy
import gpu

from . import paint
from .rect import Rect

ICONS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "icons")
_glyphs = {}        # icon file name -> (image, texture), kept until another file loads


def glyph(name):
    """The icons/<name>.png texture (FP's FN glyphs, mdi_<Kind> Material icons), None when there is none."""
    if name in _glyphs:
        entry = _glyphs[name]
        if entry is None:
            return None         # no such file: looked once
        try:
            entry[0].name
            return entry[1]
        except ReferenceError:      # a file load freed its image
            del _glyphs[name]
    path = os.path.join(ICONS, (name or "") + ".png")
    if not name or not os.path.isfile(path):
        _glyphs[name] = None
        return None
    image = bpy.data.images.load(path, check_existing=False)
    image.name = ".fp_glyph"
    image.colorspace_settings.name = 'Non-Color'
    _glyphs[name] = (image, gpu.texture.from_image(image))
    return _glyphs[name][1]


def forget_glyphs():
    _glyphs.clear()


class Ui:
    def __init__(self, theme, frame, store, now):
        self.t, self.frame, self.store, self.now = theme, frame, store, now
        self.clip = None
        self.compact = False        # drawing a list column dragged down to icons
        self.again = False          # a pop-up changed size while drawing: draw once more

    def hot(self, id):
        return self.store.hot == id

    @property
    def bar_room(self):
        """What a list's rows leave for its scroll bar: none in a compact column (its bar shows on hover, over them)."""
        return 0 if self.compact else 4 * self.t.s

    def pressed(self, id):
        return self.store.pressed == id

    # ---------------------------------------------------------------- surfaces

    def panel(self, r, color=None, radius=None):
        paint.rect(r, color or self.t.card, self.t.r_card if radius is None else radius)

    def icon(self, r, name, color=None):
        tex = glyph(name)
        if tex is None:
            return False
        size = min(r.w, r.h, self.t.icon)
        box = Rect(r.x + (r.w - size) / 2, r.y + (r.h - size) / 2, size, size)
        paint.rect(box, (0, 0, 0, 0), 0, image=tex, tint=color or self.t.text1)
        return True

    def label(self, r, text, color=None, size=None, align="left"):
        paint.text_in(text, r, color or self.t.text1, size or self.t.text, align)

    def divider(self, x0, x1, y):
        paint.line(x0, y, x1, y, self.t.divider)

    # ---------------------------------------------------------------- input widgets

    def button(self, id, r, text="", icon=None, action=None, tip="", selected=False, enabled=True, style="control",
               align="center"):
        """style: control (filled), flat (fill on hover only), accent (the main action)."""
        t = self.t
        hot, down = self.hot(id) and enabled, self.pressed(id)
        if style == "accent":
            fill = t.accent if enabled else t.control[:3] + (0.4,)
        elif selected:
            fill = t.control
        elif style == "flat":
            fill = t.hover if hot else (0, 0, 0, 0)
        else:
            fill = t.control if not hot else tuple(min(1.0, c * 1.12) for c in t.control[:3]) + (1.0,)
        if down:
            fill = tuple(c * 0.85 for c in fill[:3]) + (fill[3],)
        paint.rect(r, fill, t.r_small)
        color = t.text1 if (enabled and (selected or style != "flat" or hot)) else (t.text2 if enabled else t.text3)
        inner = r.pad(t.pad if text else 0, 0)
        if icon and text:
            ir = inner.cut_left(t.icon)
            self.icon(ir, icon, color)
            inner.cut_left(t.pad)
        elif icon:
            self.icon(inner, icon, color)
        if text:
            paint.text_in(text, inner, color, t.text, align if not icon else "left")
        if enabled:
            self.frame.add(_hit(id, r, self.clip, "button", action, None, tip))

    def row(self, id, r, text, icon=None, selected=False, action=None, double=None, tip="", detail="", depth=0):
        """A list row (FP's sidebar item): selects on press, icon + label left-aligned, a detail on the right; in a
        compact column (dragged narrow) its icon only, the label its tip."""
        t = self.t
        if selected:
            paint.rect(r, t.selected, t.r_small)
        elif self.hot(id):
            paint.rect(r, t.hover, t.r_small)
        if self.compact:
            if not self.icon(r, icon, t.text1 if selected or self.hot(id) else t.text2):
                paint.text_in(text[:1], r, t.text2, t.text, "center")       # no icon: its initial
            self.frame.add(_hit(id, r, self.clip, "row", action, double, tip or text))
            return
        inner = r.pad(t.pad + depth * t.big, 0)
        color = t.text1 if selected or self.hot(id) else t.text2
        if icon is not None:
            ir = inner.cut_left(t.icon)
            self.icon(ir, icon, color)
            inner.cut_left(t.pad)
        if detail:
            dw = min(paint.measure(detail, t.caption) + t.pad, inner.w * 0.4)
            dr = inner.cut_right(dw)
            paint.text_in(detail, dr, t.text3, t.caption, "right")
        paint.text_in(text, inner, color, t.text)
        self.frame.add(_hit(id, r, self.clip, "row", action, double, tip))

    def group(self, id, r, text, open_, action, select=None, tip=""):
        """A collapsible group header (FP's "Creative ›"). With `select`, its name is a hit of its own (id + ":name",
        `tip` its tooltip) and only the chevron's end folds."""
        t = self.t
        if self.compact:        # a divider between groups; the header still folds it
            self.divider(r.x + t.pad, r.right - t.pad, r.y + r.h / 2)
            self.frame.add(_hit(id, r, self.clip, "button", action, None, text))
            return
        inner = r.pad(t.pad, 0)
        ir = inner.cut_right(t.icon)
        named = select is not None and self.hot(id + ":name")
        fold_hot = self.hot(id) and select is not None
        self.icon(ir, "mdi_ChevronDown" if open_ else "mdi_ChevronRight", t.text1 if fold_hot else t.text3)
        paint.text_in(text, inner, t.text2 if named or (self.hot(id) and select is None) else t.text3, t.text)
        if select is None:
            self.frame.add(_hit(id, r, self.clip, "button", action, None, ""))
            return
        x = ir.x - t.pad        # the chevron and its padding fold, the rest picks
        self.frame.add(_hit(id + ":name", Rect(r.x, r.y, x - r.x, r.h), self.clip, "button", select, None, tip))
        self.frame.add(_hit(id, Rect(x, r.y, r.right - x, r.h), self.clip, "button", action, None, ""))

    def drag(self, id, r, fn, tip=""):
        """A drag handle: fn(context, dx, dy) on every move while pressed (dx, dy: since the press)."""
        from .frame import Hit
        self.frame.add(Hit(id, r, self.clip, "button", None, None, tip, None, fn))

    # ---------------------------------------------------------------- scrolling

    def scroll(self, id, r, content_h):
        """A scrolled list in `r`: returns its offset (px scrolled down); draw rows at y - offset, then end_scroll()."""
        most = max(0.0, content_h - r.h)
        offset = min(self.store.offset(id), most)
        self.store.scroll[id] = offset
        self.frame.add(_hit(id, r, self.clip, "scroll", scroll=(id, most)))
        paint.push_clip(r)
        self._scroll_stack = getattr(self, "_scroll_stack", []) + [(id, self.clip, r, offset, most, content_h)]
        self.clip = r if self.clip is None else self.clip.intersect(r)
        return offset

    def end_scroll(self):
        id, clip, r, offset, most, content_h = self._scroll_stack.pop()
        self.clip = clip
        paint.pop_clip()
        if most > 0 and (not self.compact or self.store.over == id):       # compact: only under the mouse
            t = self.t
            bar = max(t.big * 2, r.h * r.h / content_h)
            y = r.y + (r.h - bar) * (1 - offset / most)
            paint.rect(Rect(r.right - 3 * t.s, y, 2 * t.s, bar), t.text3[:3] + (0.35,), t.s)

    # ---------------------------------------------------------------- tooltip

    def tooltip(self, region_w, region_h, delay=0.5):
        """The hot widget's tip after `delay` s, drawn over everything (call last)."""
        hit = self.frame.find(self.store.hot) if self.store.hot is not None else None
        if hit is None or not hit.tip or self.now - self.store.tip_since < delay:
            return hit is not None and bool(hit.tip)        # True: the caller asks for a redraw when the delay ends
        t = self.t
        w = paint.measure(hit.tip, t.text) + 2 * t.pad
        x = min(hit.rect.right + t.pad, region_w - w - t.pad)
        y = min(max(hit.rect.y + (hit.rect.h - t.row) / 2, t.pad), region_h - t.row - t.pad)
        if x < hit.rect.right and hit.rect.y - t.row - t.gap > 0:       # no room beside it: under it
            x, y = max(t.pad, hit.rect.x), hit.rect.y - t.row - t.gap
        box = Rect(x, y, w, t.row)
        paint.rect(box, (0.1, 0.1, 0.1, 0.95), t.r_small, edge=t.divider, border=1)
        paint.text_in(hit.tip, box, t.text1, t.text, "left", t.pad)
        return False


def _hit(id, r, clip, kind, action=None, double=None, tip="", scroll=None):
    from .frame import Hit
    return Hit(id, r, clip, kind, action, double, tip, scroll)
