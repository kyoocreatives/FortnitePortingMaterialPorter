"""The colour picker pop-up: an HSV square, a hue strip, hex. The maths is pure (tested headless); draw() needs gpu."""
import colorsys


def rgb_to_hsv(r, g, b):
    return colorsys.rgb_to_hsv(r, g, b)


def hsv_to_rgb(h, s, v):
    return colorsys.hsv_to_rgb(h, s, v)


def _to_linear(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _to_srgb(c):
    c = max(0.0, c)
    return c * 12.92 if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def srgb_to_linear(rgb):
    return tuple(_to_linear(c) for c in rgb)


def linear_to_srgb(rgb):
    return tuple(_to_srgb(c) for c in rgb)


def to_hex(rgb, linear=False):
    """RRGGBB of a colour (`linear`: a scene-linear one, shown as its display value like Blender's hex)."""
    if linear:
        rgb = linear_to_srgb(rgb)
    return "".join("%02X" % int(round(max(0.0, min(1.0, c)) * 255)) for c in rgb[:3])


def from_hex(text):
    """(r, g, b) 0-1 from RRGGBB / RGB, with or without #; None when it isn't hex."""
    h = text.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        return None
    try:
        return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    except ValueError:
        return None


# ------------------------------------------------------------------ the pop-up (gpu)

def _smooth(quads):
    """Quads of per-corner colours (display values), one batch."""
    import gpu
    from gpu_extras.batch import batch_for_shader
    pos, col, idx = [], [], []
    for corners in quads:
        n = len(pos)
        for (x, y), c in corners:
            pos.append((x, y))
            col.append(srgb_to_linear(c[:3]) + (1.0,))
        idx += [(n, n + 1, n + 2), (n, n + 2, n + 3)]
    shader = gpu.shader.from_builtin('SMOOTH_COLOR')
    gpu.state.blend_set('ALPHA')
    batch_for_shader(shader, 'TRIS', {"pos": pos, "color": col}, indices=idx).draw(shader)


def _hsv_of(p, rgb):
    """The pop-up's HSV: kept between draws so hue and saturation survive black and grey."""
    h, s, v = p.state.get("hsv") or rgb_to_hsv(*rgb)
    if hsv_to_rgb(h, s, v) != tuple(rgb) and any(abs(a - b) > 1e-4 for a, b in zip(hsv_to_rgb(h, s, v), rgb)):
        h, s, v = rgb_to_hsv(*rgb)
    p.state["hsv"] = (h, s, v)
    return h, s, v


def picker(owner, name, on_change=None, title=""):
    """The picker's draw: an SV square, a hue strip, before / after and hex. Edits write the property live."""
    def draw(ui, r, p):
        from .fields import text_box
        from .frame import Hit
        from .rect import Rect
        from . import paint
        t = ui.t
        if title:
            paint.text_in(title, r.cut_top(t.row), t.text1, t.text)
            r.cut_top(t.gap)
        try:
            prop = owner.bl_rna.properties[name]
            value = tuple(getattr(owner, name))
        except ReferenceError:      # its material was rebuilt
            paint.text_in("Pick the input again", r.cut_top(t.row), t.text3, t.text)
            return
        linear = prop.subtype == 'COLOR'

        shown = linear_to_srgb(value[:3]) if linear else tuple(value[:3])
        shown = tuple(min(1.0, max(0.0, c)) for c in shown)
        p.state.setdefault("before", shown)
        h, s, v = _hsv_of(p, shown)

        def put(context, hsv):
            p.state["hsv"] = hsv
            rgb = hsv_to_rgb(*hsv)
            rgb = srgb_to_linear(rgb) if linear else rgb
            setattr(owner, name, tuple(rgb) + tuple(value[3:]))
            if on_change is not None:
                on_change(context)
        square = r.cut_top(r.w * 0.72)
        r.cut_top(t.pad)
        strip = r.cut_top(14 * t.s)
        r.cut_top(t.pad)
        hue = hsv_to_rgb(h, 1, 1)
        _smooth([(((square.x, square.y), (0, 0, 0)), ((square.right, square.y), (0, 0, 0)),
                  ((square.right, square.top), hue), ((square.x, square.top), (1, 1, 1)))])
        seg = strip.w / 6
        _smooth([(((strip.x + k * seg, strip.y), hsv_to_rgb(k / 6, 1, 1)),
                  ((strip.x + (k + 1) * seg, strip.y), hsv_to_rgb((k + 1) / 6, 1, 1)),
                  ((strip.x + (k + 1) * seg, strip.top), hsv_to_rgb((k + 1) / 6, 1, 1)),
                  ((strip.x + k * seg, strip.top), hsv_to_rgb(k / 6, 1, 1))) for k in range(6)])
        dot = 5 * t.s
        cx, cy = square.x + s * square.w, square.y + v * square.h
        paint.rect(Rect(cx - dot, cy - dot, 2 * dot, 2 * dot), (0, 0, 0, 0), dot, edge=(1, 1, 1, 1), border=1.5 * t.s)
        hx = strip.x + h * strip.w
        paint.rect(Rect(hx - 2 * t.s, strip.y - t.s, 4 * t.s, strip.h + 2 * t.s), (0, 0, 0, 0), t.s,
                   edge=(1, 1, 1, 1), border=1.5 * t.s)

        def at(rect, fn):
            def click(context):
                fn(context, *ui.store.press_at)

            def drag(context, dx, dy):
                fn(context, ui.store.press_at[0] + dx, ui.store.press_at[1] + dy)
            return click, drag

        def sv(context, x, y, h=h):
            put(context, (h, min(max((x - square.x) / square.w, 0), 1), min(max((y - square.y) / square.h, 0), 1)))

        def hue_at(context, x, y, s=s, v=v):
            put(context, (min(max((x - strip.x) / strip.w, 0), 0.9999), s, v))
        for id, rect, fn in ((p.id + ":sv", square, sv), (p.id + ":hue", strip, hue_at)):
            click, drag = at(rect, fn)
            ui.frame.add(Hit(id, rect, ui.clip, "button", click, None, "", None, drag))
        row = r.cut_top(t.row)
        before, after = row.cut_left(t.row * 1.5), row.cut_left(t.row * 1.5)
        paint.rect(before, p.state["before"] + (1.0,), t.r_small, corners=(0, 0, t.r_small, t.r_small))
        paint.rect(after, shown + (1.0,), t.r_small, corners=(t.r_small, t.r_small, 0, 0))

        def back(context):
            rgb = p.state["before"]
            put(context, rgb_to_hsv(*rgb))
        ui.frame.add(Hit(p.id + ":before", before, ui.clip, "button", back, None, "Back to the colour it had"))
        row.cut_left(t.pad)

        def set_hex(context, text):
            rgb = from_hex(text)
            if rgb is not None:
                put(context, rgb_to_hsv(*rgb))
        text_box(ui, p.id + ":hex", row, to_hex(shown), set_hex, icon=None)
        p.fields = [p.id + ":hex"]
    return draw


def _close(owner, name):
    from .fields import _undo
    try:
        _undo(owner, name)
    except ReferenceError:
        pass


def open_for(context, anchor, owner, name, parent=None, on_change=None, title=""):
    """The picker beside `anchor`, editing owner.name; one undo step when it closes."""
    from .blender import open, theme
    from .popup import Popup
    t = theme(context)
    w = 220 * t.s
    h = w * 0.72 + 14 * t.s + t.row + 2 * t.pad + 2 * t.pad + ((t.row + t.gap) if title else 0)
    p = Popup((parent.id if parent is not None else "color") + ":color", picker(owner, name, on_change, title), anchor,
              "right", (w, h), "color", parent.id if parent is not None else None, keep_open=True,
              on_close=lambda: _close(owner, name))
    return open(context, p)
