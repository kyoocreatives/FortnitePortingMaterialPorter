"""Drawing: rounded rects (one SDF shader: fill, border, optional image), text (Blender's UI font, cut to fit, clipped),
and a clip stack (GPU scissor). Coordinates are region pixels."""
import blf
import gpu
import numpy as np
from gpu_extras.batch import batch_for_shader

FONT = 0
_shaders = {}

_VERT = """
void main() {
    /* pos: a unit quad, placed on the rect (one cached batch for every rect) */
    p = rect.xy + pos * rect.zw;
    uv = pos;
    gl_Position = ModelViewProjectionMatrix * vec4(p, 0.0, 1.0);
}
"""
_FRAG = """
void main() {
    vec2 c = rect.xy + rect.zw * 0.5;
    vec2 h = p - c;
    /* each corner its own radius: radii = (top right, bottom right, top left, bottom left) */
    vec2 side = h.x > 0.0 ? radii.xy : radii.zw;
    float r = h.y > 0.0 ? side.x : side.y;
    vec2 q = abs(h) - rect.zw * 0.5 + r;
    float d = length(max(q, 0.0)) + min(max(q.x, q.y), 0.0) - r;
    float inside = clamp(0.5 - d, 0.0, 1.0);
    vec4 col = gradient > 0 ? mix(fill, fill2, uv.y) : fill;
    vec4 base = col;
    if (use_image > 0) {
        vec4 t = texture(image, uv);
        vec3 rgb = base.a > 0.0 ? mix(base.rgb, t.rgb, t.a) : t.rgb;      /* no fill: the image's own edges */
        col = vec4(rgb, base.a + t.a * (1.0 - base.a)) * tint;
    }
    if (border > 0.0) {
        float ring = clamp(0.5 - abs(d + border * 0.5) + border * 0.5, 0.0, 1.0);
        col = mix(col, edge, ring * edge.a);
    }
    /* theme colours and icons are sRGB, the region's framebuffer is linear (Blender's own shaders convert too) */
    vec3 lin = mix(col.rgb / 12.92, pow((col.rgb + 0.055) / 1.055, vec3(2.4)), step(0.04045, col.rgb));
    frag = vec4(lin, col.a * (invert > 0 ? 1.0 - inside : inside));
}
"""


def _rounded():
    if "rounded" not in _shaders:
        info = gpu.types.GPUShaderCreateInfo()
        info.vertex_in(0, 'VEC2', "pos")
        iface = gpu.types.GPUStageInterfaceInfo("fp_rounded")
        iface.smooth('VEC2', "p")
        iface.smooth('VEC2', "uv")
        info.vertex_out(iface)
        info.push_constant('MAT4', "ModelViewProjectionMatrix")
        info.push_constant('VEC4', "rect")
        info.push_constant('VEC4', "radii")
        info.push_constant('FLOAT', "border")
        info.push_constant('VEC4', "fill")
        info.push_constant('VEC4', "fill2")
        info.push_constant('INT', "gradient")
        info.push_constant('VEC4', "edge")
        info.push_constant('VEC4', "tint")
        info.push_constant('INT', "use_image")
        info.push_constant('INT', "invert")
        info.sampler(0, 'FLOAT_2D', "image")
        info.fragment_out(0, 'VEC4', "frag")
        info.vertex_source(_VERT)
        info.fragment_source(_FRAG)
        _shaders["rounded"] = gpu.shader.create_from_info(info)
    return _shaders["rounded"]


def _builtin(name):
    if name not in _shaders:
        _shaders[name] = gpu.shader.from_builtin(name)
    return _shaders[name]


_blank = []


def _blank_texture():
    if not _blank:
        _blank.append(gpu.types.GPUTexture((1, 1), format='RGBA8'))
    return _blank[0]


def rect(r, fill, radius=0.0, edge=None, border=0.0, image=None, tint=(1, 1, 1, 1), invert=False, corners=None, top=None):
    """A rounded rect: `fill` (to `top` up the rect: a vertical gradient), an `edge` ring `border` px wide inside it, or an
    image (drawn over the fill); `corners` (top right, bottom right, top left, bottom left) rounds them one by one;
    `invert` fills its corners outside the rounding instead (rounds what was drawn under it). `image` may be an icon in
    the atlas (textures.Icon): drawn over the rest, untinted."""
    if r.w <= 0 or r.h <= 0:
        return
    if image is not None and hasattr(image, "place"):      # an icon in the atlas (textures.Icon): over the fill
        if fill[3] > 0 or top is not None or edge is not None:
            rect(r, fill, radius, edge, border, None, tint, invert, corners, top)
        icons = Icons()
        icons.add(r, image.place, radius, corners)
        icons.draw(image.pages, image.size)
        return
    shader = _rounded()
    if "quad" not in _shaders:
        _shaders["quad"] = batch_for_shader(shader, 'TRIS', {"pos": [(0, 0), (1, 0), (1, 1), (0, 1)]},
                                            indices=((0, 1, 2), (0, 2, 3)))
    batch = _shaders["quad"]
    gpu.state.blend_set('ALPHA')
    shader.bind()
    shader.uniform_float("rect", (r.x, r.y, r.w, r.h))
    most = min(r.w, r.h) / 2
    shader.uniform_float("radii", tuple(min(c, most) for c in (corners or (radius,) * 4)))
    shader.uniform_float("border", border if edge is not None else 0.0)
    shader.uniform_float("fill", fill)
    shader.uniform_float("fill2", top or fill)
    shader.uniform_int("gradient", 1 if top is not None else 0)
    shader.uniform_float("edge", edge or (0, 0, 0, 0))
    shader.uniform_float("tint", tint)
    shader.uniform_int("use_image", 1 if image is not None else 0)
    shader.uniform_int("invert", 1 if invert else 0)
    shader.uniform_sampler("image", image if image is not None else _blank_texture())
    batch.draw(shader)


_BATCH_VERT = """
void main() {
    p = pos;
    box = rect_in;
    corner = radii_in;
    col0 = fill_in;
    col1 = fill2_in;
    ring = ring_in;
    gl_Position = ModelViewProjectionMatrix * vec4(pos, 0.0, 1.0);
}
"""
_BATCH_FRAG = """
void main() {
    vec2 h = p - (box.xy + box.zw * 0.5);
    vec2 side = h.x > 0.0 ? corner.xy : corner.zw;
    float r = h.y > 0.0 ? side.x : side.y;
    vec2 q = abs(h) - box.zw * 0.5 + r;
    float d = length(max(q, 0.0)) + min(max(q.x, q.y), 0.0) - r;
    vec4 col = mix(col0, col1, clamp((p.y - box.y) / max(box.w, 1.0), 0.0, 1.0));
    vec3 lin = mix(col.rgb / 12.92, pow((col.rgb + 0.055) / 1.055, vec3(2.4)), step(0.04045, col.rgb));
    float cover = clamp(0.5 - d, 0.0, 1.0);
    if (ring > 0.0) {
        cover *= clamp(0.5 + d + ring, 0.0, 1.0);     /* only a band `ring` px wide inside the edge */
    }
    frag = vec4(lin, col.a * cover);
}
"""


def _batched():
    if "batched" not in _shaders:
        info = gpu.types.GPUShaderCreateInfo()
        info.vertex_in(0, 'VEC2', "pos")
        info.vertex_in(1, 'VEC4', "rect_in")
        info.vertex_in(2, 'VEC4', "radii_in")
        info.vertex_in(3, 'VEC4', "fill_in")
        info.vertex_in(4, 'VEC4', "fill2_in")
        info.vertex_in(5, 'FLOAT', "ring_in")
        iface = gpu.types.GPUStageInterfaceInfo("fp_batched")
        iface.smooth('VEC2', "p")
        iface.flat('VEC4', "box")
        iface.flat('VEC4', "corner")
        iface.flat('VEC4', "col0")
        iface.flat('VEC4', "col1")
        iface.flat('FLOAT', "ring")
        info.vertex_out(iface)
        info.push_constant('MAT4', "ModelViewProjectionMatrix")
        info.fragment_out(0, 'VEC4', "frag")
        info.vertex_source(_BATCH_VERT)
        info.fragment_source(_BATCH_FRAG)
        _shaders["batched"] = gpu.shader.create_from_info(info)
    return _shaders["batched"]


_CORNERS = np.array([[0, 0], [1, 0], [1, 1], [0, 1]], np.float64)
_quad_tris = [np.zeros((0, 3), np.int32)]


def _quads(rows, width):
    """Shapes gathered as flat rows (`width` floats each, x y w h first) -> (per-vertex rows, their positions), four
    vertices a shape, and the quads' triangles."""
    a = np.asarray(rows, np.float64).reshape(-1, width)
    n = len(a)
    v = np.repeat(a, 4, axis=0)
    pos = v[:, 0:2] + v[:, 2:4] * np.tile(_CORNERS, (n, 1))
    if len(_quad_tris[0]) < 2 * n:
        base = np.arange(max(n, 256), dtype=np.int32)[:, None] * 4
        _quad_tris[0] = np.hstack((base, base + 1, base + 2, base, base + 2, base + 3)).reshape(-1, 3)
    return v.astype(np.float32), pos.astype(np.float32), _quad_tris[0][:2 * n]


def _cols(v, i, j):
    return np.ascontiguousarray(v[:, i:j])


class Rects:
    """Plain rounded rects (fill or vertical gradient, per-corner radii, or a `ring` px band inside the edge) gathered,
    then drawn in one call, in the order added: for views that draw many alike (the grid's tiles)."""
    WIDTH = 17      # x y w h, radii, fill, fill2, ring

    def __init__(self):
        self.rows = []

    def add(self, r, fill, radius=0.0, corners=None, top=None, ring=0.0):
        if r.w <= 0 or r.h <= 0:
            return
        most = min(r.w, r.h) / 2
        if corners is None:
            c = min(radius, most)
            radii = (c, c, c, c)
        else:
            radii = tuple(min(c, most) for c in corners)
        self.rows += (r.x, r.y, r.w, r.h, *radii, *fill, *(top or fill), ring)

    def draw(self):
        if not self.rows:
            return
        shader = _batched()
        v, pos, tris = _quads(self.rows, self.WIDTH)
        batch = batch_for_shader(shader, 'TRIS', {"pos": pos, "rect_in": _cols(v, 0, 4), "radii_in": _cols(v, 4, 8),
                                                  "fill_in": _cols(v, 8, 12), "fill2_in": _cols(v, 12, 16),
                                                  "ring_in": _cols(v, 16, 17).ravel()}, indices=tris)
        gpu.state.blend_set('ALPHA')
        batch.draw(shader)


_ICONS_VERT = """
void main() {
    p = pos;
    uv = (pos - rect_in.xy) / rect_in.zw;
    box = rect_in;
    corner = radii_in;
    src = src_in;
    page = int(page_in.x);
    mip0 = page_in.y;
    gl_Position = ModelViewProjectionMatrix * vec4(pos, 0.0, 1.0);
}
"""
_ICONS_FRAG = """
/* the page at `at` (0..1), filtered as an icon texture is (the page has its mips and sampler); an icon read without
   mips (`mip0`): off mip 0, as its texture would be */
vec4 sample_page(vec2 at, vec2 dx, vec2 dy) {
    if (mip0 > 0.5) {
        dx = vec2(0.0);
        dy = vec2(0.0);
    }
    switch (page) {
        case 0: return textureGrad(page0, at, dx, dy);
        case 1: return textureGrad(page1, at, dx, dy);
        case 2: return textureGrad(page2, at, dx, dy);
        case 3: return textureGrad(page3, at, dx, dy);
        case 4: return textureGrad(page4, at, dx, dy);
        case 5: return textureGrad(page5, at, dx, dy);
        case 6: return textureGrad(page6, at, dx, dy);
        default: return textureGrad(page7, at, dx, dy);
    }
}

void main() {
    vec2 h = p - (box.xy + box.zw * 0.5);
    vec2 side = h.x > 0.0 ? corner.xy : corner.zw;
    float r = h.y > 0.0 ? side.x : side.y;
    vec2 q = abs(h) - box.zw * 0.5 + r;
    float d = length(max(q, 0.0)) + min(max(q.x, q.y), 0.0) - r;
    float inside = clamp(0.5 - d, 0.0, 1.0);
    vec2 at = (src.xy + uv * src.zw) / PAGE;
    vec4 col = sample_page(at, dFdx(at), dFdy(at));
    vec3 lin = mix(col.rgb / 12.92, pow((col.rgb + 0.055) / 1.055, vec3(2.4)), step(0.04045, col.rgb));
    frag = vec4(lin, col.a * inside);
}
"""


def _icons(page):
    key = "icons%d" % page      # one per page size (the Texture Limit may cut the pages)
    if key not in _shaders:
        info = gpu.types.GPUShaderCreateInfo()
        info.vertex_in(0, 'VEC2', "pos")
        info.vertex_in(1, 'VEC4', "rect_in")
        info.vertex_in(2, 'VEC4', "radii_in")
        info.vertex_in(3, 'VEC4', "src_in")
        info.vertex_in(4, 'VEC2', "page_in")
        iface = gpu.types.GPUStageInterfaceInfo("fp_icons")
        iface.smooth('VEC2', "p")
        iface.smooth('VEC2', "uv")
        iface.flat('VEC4', "box")
        iface.flat('VEC4', "corner")
        iface.flat('VEC4', "src")
        iface.flat('INT', "page")
        iface.flat('FLOAT', "mip0")
        info.vertex_out(iface)
        info.push_constant('MAT4', "ModelViewProjectionMatrix")
        for i in range(8):
            info.sampler(i, 'FLOAT_2D', "page%d" % i)
        info.fragment_out(0, 'VEC4', "frag")
        info.define("PAGE", "%.1f" % page)
        info.vertex_source(_ICONS_VERT)
        info.fragment_source(_ICONS_FRAG)
        _shaders[key] = gpu.shader.create_from_info(info)
    return _shaders[key]


class Icons:
    """Icons out of an atlas (textures: square pages, each icon with its mips) in rounded rects, drawn in one call: each
    as rect(r, (0, 0, 0, 0), radius, image=its texture, corners=corners) draws it."""
    WIDTH = 14      # x y w h, radii, the icon on its page (x y w h), page, flat

    def __init__(self):
        self.rows = []

    def add(self, r, place, radius=0.0, corners=None):
        """`place`: (page, x, y, w, h, flat), the icon on the page's mip 0 in texels; `flat`: read off mip 0 only."""
        if r.w <= 0 or r.h <= 0:
            return
        most = min(r.w, r.h) / 2
        if corners is None:
            c = min(radius, most)
            radii = (c, c, c, c)
        else:
            radii = tuple(min(c, most) for c in corners)
        page, x, y, w, h, flat = place
        self.rows += (r.x, r.y, r.w, r.h, *radii, x, y, w, h, page, flat)

    def draw(self, pages, page_size):
        if not self.rows or not pages:
            return
        shader = _icons(page_size)
        v, pos, tris = _quads(self.rows, self.WIDTH)
        batch = batch_for_shader(shader, 'TRIS', {"pos": pos, "rect_in": _cols(v, 0, 4), "radii_in": _cols(v, 4, 8),
                                                  "src_in": _cols(v, 8, 12), "page_in": _cols(v, 12, 14)},
                                 indices=tris)
        gpu.state.blend_set('ALPHA')
        shader.bind()
        for i in range(8):
            shader.uniform_sampler("page%d" % i, pages[i] if i < len(pages) else _blank_texture())
        batch.draw(shader)


def line(x0, y0, x1, y1, color, width=1.0):
    shader = _builtin('UNIFORM_COLOR')
    gpu.state.blend_set('ALPHA')
    gpu.state.line_width_set(width)
    shader.uniform_float("color", color)
    batch_for_shader(shader, 'LINES', {"pos": [(x0, y0), (x1, y1)]}).draw(shader)
    gpu.state.line_width_set(1.0)


def measure(text, size):
    blf.size(FONT, size)
    return blf.dimensions(FONT, text)[0]


def fit(text, size, width):
    """The text, cut with an ellipsis to fit `width` px."""
    blf.size(FONT, size)
    if blf.dimensions(FONT, text)[0] <= width:
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if blf.dimensions(FONT, text[:mid] + "…")[0] <= width:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo] + "…" if lo else ""


def text(s, x, y, color, size, align="left", width=None):
    """`s` with its baseline-box bottom at y; cut to `width` when given; align left, center or right within it."""
    if width is not None:
        s = fit(s, size, width)
    blf.size(FONT, size)
    w = blf.dimensions(FONT, s)[0]
    if align == "center" and width is not None:
        x += (width - w) / 2
    elif align == "right" and width is not None:
        x += width - w
    blf.color(FONT, *color)
    blf.position(FONT, x, y, 0)
    blf.draw(FONT, s)


def text_in(s, r, color, size, align="left", pad=0.0):
    """`s` centred vertically in rect `r` (its capitals: what the eye centres)."""
    blf.size(FONT, size)
    cap = blf.dimensions(FONT, "H")[1]
    text(s, r.x + pad, r.y + (r.h - cap) / 2, color, size, align, r.w - 2 * pad)


def wrap(s, size, width, lines):
    """`s` broken into at most `lines` lines of `width` px (the last cut with an ellipsis)."""
    words, out, cur = s.split(), [], ""
    blf.size(FONT, size)
    for word in words:
        trial = (cur + " " + word).strip()
        if blf.dimensions(FONT, trial)[0] <= width or not cur:
            cur = trial
        else:
            out.append(cur)
            cur = word
    if cur:
        out.append(cur)
    if len(out) > lines:
        out = out[:lines]
        out[-1] = fit(out[-1] + " …", size, width)
    return out


_clips = []


def push_clip(r):
    """Draw only inside `r` (and inside the clip already set) until pop_clip."""
    if _clips:
        r = r.intersect(_clips[-1])
    _clips.append(r)
    _apply()


def pop_clip():
    _clips.pop()
    _apply()


def _apply():
    if _clips:
        r = _clips[-1]
        gpu.state.scissor_test_set(True)
        gpu.state.scissor_set(int(r.x), int(r.y), max(0, int(r.w + 0.999)), max(0, int(r.h + 0.999)))
    else:
        gpu.state.scissor_test_set(False)


def begin(region):
    """Before a draw. The scissor works in region pixels during a region's draw, as the drawing does."""
    _clips.clear()


def end():
    _clips.clear()
    gpu.state.scissor_test_set(False)
    gpu.state.blend_set('NONE')
