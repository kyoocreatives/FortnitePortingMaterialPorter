"""Icons as GPU textures. The engine writes each icon to a file, Blender loads it (a hidden image) and makes a texture
of it. Icons the grid asked for (slot()) are then copied, mips and all, into a slot of the atlas and their image freed:
the grid draws every icon in one call (paint.Icons), and the atlas reuses its least recently drawn slot when full, so
icon memory stays the same however long the list. Other views (the info card, the files preview, style thumbnails)
keep their own textures (get(), image()), the KEEP most recently used, or draw an icon the grid put in the atlas out of
it (get() gives an Icon)."""
import math
from collections import OrderedDict

import bpy
import gpu
from gpu_extras.batch import batch_for_shader

KEEP = 64           # textures kept for the other views
SLOT = 256          # an icon of up to SLOT² texels (bigger ones: a mip that fits)
GUTTER = 16         # round each icon: its opposite edges, so an edge reads as the icon's own texture (it repeats)
PITCH = SLOT + 2 * GUTTER       # slots (and the icons in them) start on a whole texel down to mip 4
PAGE = 4096         # a page: PAGE² RGBA8 with its mips (85 MB), PER_PAGE slots
PER_ROW = PAGE // PITCH
PER_PAGE = PER_ROW * PER_ROW
# Blender's Texture Limit (Preferences > Viewport) cuts a page made from an image to it: the atlas lays its slots out
# on the page it got (2048: 49 slots a page); on smaller pages too few icons fit, the grid draws their own textures
MIN_PAGE = 2048
MAX_PAGES = 8       # more only when more icons are on screen at once (a page is added when none can be reused)
RECENT = 2          # a slot drawn in the last RECENT grid draws is on screen: never reused (two areas draw twice)

_cache = OrderedDict()      # path -> (image, texture): the other views', least recently used first
_asked = {}                 # icons the grid asked for that the atlas lacks: add() puts them in it
_draws = [0]                # grid draws so far: the atlas's clock
_visible = [0]              # icons on screen at the last grid draw


class Slots:
    """Which icon holds which atlas slot: a free slot first, else the least recently drawn icon's, unless that one was
    drawn in the last RECENT draws (`now` counts them): it is on screen, take() answers None and the caller adds slots
    (grow) or forces it. Pure: tested headless."""

    def __init__(self, count=0):
        self.count, self.free, self.held, self.drawn = 0, [], OrderedDict(), {}
        self.grow(count)

    def grow(self, n):
        self.free = list(range(self.count + n - 1, self.count - 1, -1)) + self.free      # lowest slot first
        self.count += n

    def get(self, path, now):
        slot = self.held.get(path)
        if slot is not None:
            self.held.move_to_end(path)
            self.drawn[path] = now
        return slot

    def take(self, path, now, force=False):
        """(slot, the icon it was taken from or None); (None, None) when every slot is on screen and not `force`."""
        if path in self.held:
            return self.get(path, now), None
        old = None
        if self.free:
            slot = self.free.pop()
        elif self.held:
            old = next(iter(self.held))
            if self.drawn.get(old, -RECENT - 1) >= now - RECENT and not force:
                return None, None
            slot = self.held.pop(old)
            self.drawn.pop(old, None)
        else:
            return None, None
        self.held[path], self.drawn[path] = slot, now
        return slot, old


class Atlas:
    """Pages of PITCH² slots: an icon at (GUTTER, GUTTER) in its slot, wrapped round by GUTTER texels, on mips 0-4.
    A page is an image's texture (made from a blank image, freed at once: the texture keeps living): its mips and
    sampler (trilinear, anisotropic) are the ones every icon texture has, so the GPU reads an icon out of the atlas as
    it reads the icon's own texture. places[slot] = (page, x, y, w, h, flat): the icon on mip 0, in texels; `flat`:
    a texture the GPU reads without mips (a DDS that has none) is read off mip 0 only, as it would be."""

    def __init__(self):
        self.slots = Slots()
        self.pages, self.buffers, self.places = [], [], {}
        self.ok = True      # False once a copy failed: the grid draws icon textures one by one instead
        self.fit(PAGE)

    def fit(self, size):
        """Lay the slots out on pages of size² texels (the first page's: what the Texture Limit left of PAGE)."""
        if size < MIN_PAGE:
            raise RuntimeError("pages cut to %d px by the Texture Limit" % size)
        self.size, self.per_row = size, size // PITCH
        self.per_page = self.per_row * self.per_row

    def spot(self, slot):
        """(page, x, y): where the slot's icon starts, on mip 0 in texels."""
        page, cell = divmod(slot, self.per_page)
        row, col = divmod(cell, self.per_row)
        return page, col * PITCH + GUTTER, row * PITCH + GUTTER

    def place(self, path):
        slot = self.slots.get(path, _draws[0])
        return None if slot is None else self.places.get(slot)

    def put(self, path, texture):
        now = _draws[0]
        slot, _old = self.slots.take(path, now)
        if slot is None and len(self.pages) < MAX_PAGES:
            self._add_page()
            slot, _old = self.slots.take(path, now)
        if slot is None:
            slot, _old = self.slots.take(path, now, force=True)
        page, x, y = self.spot(slot)
        w, h, shift = texture.width, texture.height, 0
        while max(w, h) > SLOT:       # the grid's icons are SLOT or a power of two over it: one of their mips fits
            w, h, shift = w / 2, h / 2, shift + 1
        flat = not _has_mips(texture)
        _copy(texture, self.buffers[page][:1] if flat else self.buffers[page], x, y, w, h, shift)
        self.places[slot] = (page, x, y, w, h, flat)

    def _add_page(self):
        image = bpy.data.images.new(".fp_icon_atlas", self.size, self.size, alpha=True)
        image.generated_color = (0.0, 0.0, 0.0, 0.0)
        image.colorspace_settings.name = 'Non-Color'      # read raw, as the icons are
        page = gpu.texture.from_image(image)
        _remove(image)      # the texture outlives it (the Python object holds it): no 64 MB copy in memory
        if not self.pages:
            self.fit(page.width)
        elif page.width != self.size:       # the limit lowered since the first page
            raise RuntimeError("a page cut to %d px, the atlas's are %d" % (page.width, self.size))
        levels = int(math.log2(self.size)) + 1
        self.pages.append(page)
        self.buffers.append([gpu.types.GPUFrameBuffer(color_slots={"texture": page, "mip": k}) for k in range(levels)])
        self.slots.grow(self.per_page)

    def bytes(self):
        return len(self.pages) * self.size * self.size * 4 * 4 // 3


_atlas = Atlas()

_COPY_VERT = """
void main() {
    uv = mix(span.xy, span.zw, pos);
    gl_Position = vec4(pos * 2.0 - 1.0, 0.0, 1.0);
}
"""
_COPY_FRAG = """
void main() {
    frag = textureLod(image, uv, lod);      /* the texture's mip `lod` as its sampler reads it (wrapping round) */
}
"""
_copier = {}


def _copy_shader():
    if "shader" not in _copier:
        info = gpu.types.GPUShaderCreateInfo()
        info.vertex_in(0, 'VEC2', "pos")
        iface = gpu.types.GPUStageInterfaceInfo("fp_icon_copy")
        iface.smooth('VEC2', "uv")
        info.vertex_out(iface)
        info.push_constant('VEC4', "span")
        info.push_constant('FLOAT', "lod")
        info.sampler(0, 'FLOAT_2D', "image")
        info.fragment_out(0, 'VEC4', "frag")
        info.vertex_source(_COPY_VERT)
        info.fragment_source(_COPY_FRAG)
        shader = gpu.shader.create_from_info(info)
        _copier["shader"] = shader
        _copier["quad"] = batch_for_shader(shader, 'TRIS', {"pos": [(0, 0), (1, 0), (1, 1), (0, 1)]},
                                           indices=((0, 1, 2), (0, 2, 3)))
    return _copier["shader"], _copier["quad"]


def _copy(texture, buffers, x, y, w, h, shift):
    """The texture's mips (from mip `shift`) into the page's: on mip k the icon is at (x, y) / 2^k, (w, h) / 2^k
    texels, each texel the texture's own on its mip k (same centres), the gutter round it the texture repeated.
    Blending off; the GPU state put back as it was."""
    shader, quad = _copy_shader()
    blend, viewport = gpu.state.blend_get(), gpu.state.viewport_get()
    for k, buffer in enumerate(buffers):
        s = 2 ** k
        if x % s or y % s:
            break       # from mip 5 on a slot no longer starts on a whole texel (tiles never read them: < 16 px)
        x0, y0, g = x // s, y // s, max(1, GUTTER // s)
        cw, ch = w / s, h / s
        rw, rh = math.ceil(cw) + 2 * g, math.ceil(ch) + 2 * g
        with buffer.bind():
            gpu.state.blend_set('NONE')
            buffer.viewport_set(x0 - g, y0 - g, rw, rh)
            shader.bind()
            shader.uniform_sampler("image", texture)
            shader.uniform_float("span", (-g / cw, -g / ch, (rw - g) / cw, (rh - g) / ch))
            shader.uniform_float("lod", float(k + shift))
            quad.draw(shader)
    gpu.state.viewport_set(*viewport)
    gpu.state.blend_set(blend)


def _has_mips(texture):
    """Whether the GPU reads the texture's mips: its centre read on mip 0 and on its last mip differ (a texture read
    without mips gives mip 0 for both; a flat colour reads the same either way)."""
    shader, quad = _copy_shader()
    if "probe" not in _copier:
        _copier["probe"] = gpu.types.GPUOffScreen(2, 1, format='RGBA32F')
    probe = _copier["probe"]
    blend = gpu.state.blend_get()
    with probe.bind():
        buffer = gpu.state.active_framebuffer_get()
        gpu.state.blend_set('NONE')
        shader.bind()
        shader.uniform_sampler("image", texture)
        shader.uniform_float("span", (0.5, 0.5, 0.5, 0.5))
        for x, lod in ((0, 0.0), (1, 20.0)):
            buffer.viewport_set(x, 0, 1, 1)
            shader.uniform_float("lod", lod)
            quad.draw(shader)
        read = buffer.read_color(0, 0, 2, 1, 4, 0, 'FLOAT')
    gpu.state.blend_set(blend)
    read.dimensions = 8
    return list(read[:4]) != list(read[4:])


def set_visible(count):
    """Called once per grid draw (before slot()): the atlas's clock; `count` icons on screen."""
    _draws[0] += 1
    _visible[0] = count


def slot(path):
    """The grid's icon in the atlas: (page, x, y, w, h), on mip 0 in texels; None (and noted: add() will put it in
    the atlas) before it's there."""
    place = _atlas.place(path)
    if place is None and _atlas.ok:
        if len(_asked) > 20000:
            _asked.clear()
        _asked[path] = True
    return place


def pages():
    return _atlas.pages


def page_size():
    """The pages' size in texels: PAGE, or less under the Texture Limit."""
    return _atlas.size


class Icon:
    """An icon in the atlas, as get() gives it to the other views: paint.rect draws it out of the atlas."""
    __slots__ = ("place",)

    def __init__(self, place):
        self.place = place

    @property
    def pages(self):
        return _atlas.pages

    @property
    def size(self):
        return _atlas.size


def get(path):
    """The icon's texture, or its Icon when it's in the atlas (the info card's, picked in the grid), None before."""
    entry = _cache.get(path)
    if entry is not None:
        _cache.move_to_end(path)
        return entry[1]
    place = _atlas.place(path)
    return None if place is None else Icon(place)


def image(path):
    entry = _cache.get(path)
    return entry[0] if entry else None


def add(path, file):
    """True once the icon is a texture, or in the atlas when the grid asked for it (False: a file Blender can't read)."""
    if path in _cache:
        return True
    if not file:
        return False
    try:
        image = bpy.data.images.load(file, check_existing=False)
    except RuntimeError:
        return False
    image.name = ".fp_icon"
    image.use_fake_user = False
    # the UI draws straight to the sRGB screen: a colour-managed texture would be made linear (dark, oversaturated)
    image.colorspace_settings.name = 'Non-Color'
    texture = gpu.texture.from_image(image)
    if _asked.pop(path, None) and _atlas.place(path) is None:
        try:
            _atlas.put(path, texture)
            _remove(image)      # the atlas holds it now
            return True
        except Exception as e:      # a GPU that can't draw into an image's mips: the grid draws textures one by one
            print("fp_editor: icon atlas off: %s" % e)
            _atlas.ok = False
    _cache[path] = (image, texture)
    # without the atlas the grid's icons are kept here: two screenfuls at least (fewer would evict icons drawn together)
    while len(_cache) > (KEEP if _atlas.ok else max(400, 2 * _visible[0])):
        _, (old, _tex) = _cache.popitem(last=False)
        _remove(old)
    return True


def stats():
    """What the icons hold now: GPU and CPU bytes (estimates: RGBA8 or DXT plus mips, the images' pixels)."""
    gpu_b = cpu_b = 0
    for image, texture in _cache.values():
        fmt = texture.format
        bpp = 0.5 if "DXT1" in fmt else 1 if "DXT" in fmt else 16 if "32F" in fmt else 8 if "16" in fmt else 4
        gpu_b += int(texture.width * texture.height * bpp * 4 / 3)
        cpu_b += image.size[0] * image.size[1] * 4
    return {"gpu": gpu_b + _atlas.bytes(), "cpu": cpu_b, "textures": len(_cache), "pages": len(_atlas.pages),
            "atlas_icons": len(_atlas.slots.held),
            "images": sum(1 for i in bpy.data.images if i.name.startswith(".fp_icon"))}


def _remove(image):
    try:
        bpy.data.images.remove(image)
    except ReferenceError:
        pass


@bpy.app.handlers.persistent
def _file_loaded(*_args):
    _cache.clear()      # the loaded file freed their images: drop the references, don't touch them


def install():
    if _file_loaded not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_file_loaded)


def uninstall():
    if _file_loaded in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_file_loaded)


def clear():
    while _cache:
        _, (image, _tex) = _cache.popitem()
        _remove(image)
    _asked.clear()
    _atlas.__init__()       # its pages freed with it
