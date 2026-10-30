"""A landscape group's layer weights as one world-XY grid (land_view.Grid) and back. Map imports keep each piece's
layers in corner colour channels (obj["mp_layers"]): vertices land one per cell, so the shared border vertices of two
pieces read and write the same cell. Custom landscapes keep them in weightmap images over the set's bounds: the grid
is the image. The plugin's landscape code is only read here (landscape_layers.table, weightmaps.images)."""
import numpy as np

from . import land_view
from .land_view import Grid

MAX_CELLS = 4096
LANDSCAPE = "mp_landscape"
AUTO = "mp_rvt_auto"        # a scene's: re-bake the ground colour after each paint stroke


def auto_update(scene):
    """Whether the ground colour re-bakes after each paint stroke (off by default)."""
    return bool(scene.get(AUTO, False))


def set_auto_update(scene, on):
    scene[AUTO] = bool(on)


def groups(scene):
    """{collection name: [pieces]} of the scene's landscapes (a map's pieces sit in its collection)."""
    out = {}
    for obj in scene.objects:
        if obj.type == 'MESH' and obj.get(LANDSCAPE):
            name = obj.users_collection[0].name if obj.users_collection else obj.name
            out.setdefault(name, []).append(obj)
    for pieces in out.values():
        pieces.sort(key=lambda o: o.name)
    return out


def _world(obj):
    me = obj.data
    co = np.empty(len(me.vertices) * 3, np.float32)
    me.vertices.foreach_get("co", co)
    m = np.array(obj.matrix_world, np.float64)
    return co.reshape(-1, 3) @ m[:3, :3].T + m[:3, 3]


def _spacing(xy):
    """The pieces' vertex spacing: the median distance between consecutive vertices (rows run x fastest)."""
    d = np.linalg.norm(np.diff(xy[: min(len(xy), 4096)], axis=0), axis=1)
    d = d[d > 1e-4]
    return float(np.median(d)) if len(d) else 1.0


def _table(obj):
    from ...material_porter import landscape_layers
    return landscape_layers.table(obj)


def _images(obj):
    from ...material_porter import weightmaps
    return weightmaps.images(obj)


def _channels(attribute):
    v = np.empty(len(attribute.data) * 4, np.float32)
    attribute.data.foreach_get("color", v)
    return v.reshape(-1, 4)


def _cells(worlds, lo, cell, shape):
    rows = [np.clip(np.rint((w[:, 1] - lo[1]) / cell).astype(np.int64), 0, shape[0] - 1) for w in worlds]
    cols = [np.clip(np.rint((w[:, 0] - lo[0]) / cell).astype(np.int64), 0, shape[1] - 1) for w in worlds]
    return rows, cols


def _collide(worlds, rows, cols, shape):
    """Whether two distinct vertices (not a shared border's copies) fell in one cell."""
    keys = np.concatenate([r * shape[1] + c for r, c in zip(rows, cols)])
    xy = np.round(np.concatenate([w[:, :2] for w in worlds]), 3)
    order = np.argsort(keys, kind="stable")
    k, p = keys[order], xy[order]
    same = k[1:] == k[:-1]
    return bool((same & np.any(p[1:] != p[:-1], axis=1)).any())


def _layer_hide_key(name):
    from ...material_porter import landscape_layers
    return landscape_layers.hide_key(name)


def _lattice(pieces):
    """The pieces' vertices on one world-XY grid, a cell per vertex: (worlds, lo, cell, shape, rows, cols)."""
    worlds = [_world(o) for o in pieces]
    if any(len(w) == 0 for w in worlds):
        raise ValueError("a landscape piece without vertices")
    cell = min(_spacing(w[:, :2]) for w in worlds)
    lo = np.min([w[:, :2].min(axis=0) for w in worlds], axis=0)
    hi = np.max([w[:, :2].max(axis=0) for w in worlds], axis=0)
    while (hi - lo).max() / cell + 1 > MAX_CELLS:        # a huge group: coarser cells, several vertices each
        cell *= 2.0
    for _ in range(2):      # lattices half a cell apart (reduced-detail pieces): finer cells, one vertex each
        shape = (int(round((hi[1] - lo[1]) / cell)) + 1, int(round((hi[0] - lo[0]) / cell)) + 1)
        rows, cols = _cells(worlds, lo, cell, shape)
        if not _collide(worlds, rows, cols, shape) or (hi - lo).max() / (cell / 2) + 1 > MAX_CELLS:
            break
        cell /= 2.0
    shape = (int(round((hi[1] - lo[1]) / cell)) + 1, int(round((hi[0] - lo[0]) / cell)) + 1)
    all_rows, all_cols = _cells(worlds, lo, cell, shape)
    return worlds, lo, cell, shape, all_rows, all_cols


class AttributeStore:
    kind = "attributes"
    hide_key = staticmethod(_layer_hide_key)       # the pieces' property hiding a layer in the material

    def __init__(self, pieces):
        self.pieces = []
        worlds, lo, cell, shape, all_rows, all_cols = _lattice(pieces)
        layers = sorted({layer for o in pieces for layer in _table(o)})
        self.grid = g = Grid(layers, (float(lo[0]), float(lo[1])), cell, shape)
        # a cell offers a layer only when every piece on it has the layer (a border shared with a piece lacking it
        # would otherwise lose that piece's sum of 1)
        on_cell = np.zeros(shape, np.uint16)
        has = np.zeros((len(layers),) + shape, np.uint16)
        for obj, world, rows, cols in zip(pieces, worlds, all_rows, all_cols):
            me = obj.data
            corners = np.empty(len(me.loops), np.int32)
            me.loops.foreach_get("vertex_index", corners)
            first = np.full(len(me.vertices), -1, np.int64)       # a corner of each vertex (a vertex's corners agree)
            first[corners[::-1]] = np.arange(len(corners))[::-1]
            cells = np.unique(rows * shape[1] + cols)
            cell_rows, cell_cols = cells // shape[1], cells % shape[1]
            table = _table(obj)
            values = {name: _channels(me.color_attributes[name]) for name in {a for a, _ in table.values()}
                      if name in me.color_attributes}
            for layer, (attribute, channel) in table.items():
                if attribute in values:
                    li = g.index(layer)
                    g.W[li, rows, cols] = values[attribute][first, channel]
                    has[li, cell_rows, cell_cols] += 1
            g.height[rows, cols] = world[:, 2]
            g.filled[rows, cols] = True
            on_cell[cell_rows, cell_cols] += 1
            self.pieces.append({"obj": obj, "rows": rows, "cols": cols, "corners": corners, "table": table,
                                "box": (int(rows.min()), int(cols.min()), int(rows.max()) + 1, int(cols.max()) + 1)})
        g.avail[:] = (has == on_cell[None]) & (on_cell[None] > 0)
        g.blended[:] = land_view.detect_blended(g.W[:, g.filled])

    def keep(self):
        """Attributes live in the mesh: Blender's undo stores them as they are."""

    def touch(self, r0, c0, r1, c1):
        """The pieces under a grid rectangle."""
        return [p for p in self.pieces if p["box"][0] < r1 and r0 < p["box"][2] and p["box"][1] < c1 and c0 < p["box"][3]]

    def write(self, pieces, layers=None, dirty=None):
        """The grid's weights into these pieces' attributes: only the attributes holding `layers` (None: all), only
        the vertices of `dirty` cells (None: all). A painted piece's channels stay cached, so later writes only set
        them."""
        g = self.grid
        for p in pieces:
            me = p["obj"].data
            by_attribute = {}
            for layer, (attribute, channel) in p["table"].items():
                if layers is None or layer in layers:
                    by_attribute.setdefault(attribute, []).append((g.index(layer), channel))
            cache = p.setdefault("values", {})
            for attribute, slots in by_attribute.items():
                a = me.color_attributes.get(attribute)
                if a is None:
                    continue
                v = cache.get(attribute)
                if v is None:
                    v = cache[attribute] = _channels(a)
                at = dirty[p["rows"], p["cols"]][p["corners"]] if dirty is not None else slice(None)
                for li, channel in slots:
                    v[at, channel] = g.W[li, p["rows"], p["cols"]][p["corners"]][at]
                a.data.foreach_set("color", v.ravel())
            if by_attribute:
                me.update()


class ImageStore:
    """A custom landscape's weightmaps: one grid cell per texel, laid over the set's bounds (the mp_weights UV)."""
    kind = "images"
    hide_key = staticmethod(_layer_hide_key)

    def __init__(self, pieces):
        from ...material_porter import weightmaps
        first = pieces[0]
        self.maps = _images(first)
        self.table = _table(first)
        size = next(iter(self.maps.values())).size[0]
        lo, extent = self._frame(pieces, weightmaps.UV)
        self.lo, self.extent = lo, extent
        layers = sorted(self.table)
        self.grid = g = Grid(layers, (lo[0] + extent / size / 2, lo[1] + extent / size / 2), extent / size, (size, size))
        self.pixels = {}
        for name, image in self.maps.items():
            px = np.empty(size * size * 4, np.float32)
            image.pixels.foreach_get(px)
            self.pixels[name] = px.reshape(size, size, 4)
        for layer, (name, channel) in self.table.items():
            if name in self.pixels:
                g.W[g.index(layer)] = self.pixels[name][..., channel]
                g.avail[g.index(layer)] = True
        heights = np.full((size, size), np.nan, np.float32)
        for obj in pieces:
            world = _world(obj)
            rows = np.clip(((world[:, 1] - lo[1]) / extent * size).astype(np.int64), 0, size - 1)
            cols = np.clip(((world[:, 0] - lo[0]) / extent * size).astype(np.int64), 0, size - 1)
            heights[rows, cols] = world[:, 2]
            g.filled[rows.min():rows.max() + 1, cols.min():cols.max() + 1] = True
        if (~np.isnan(heights)).any():      # a few vertices over many texels: the texels between interpolated
            g.height[:] = _fill_between(heights)
        # read from the weights as a map import's are: Fortnite's recent materials sample each layer as its own mask
        # (Hera's LandscapeLayerSample), so unpainted ground starts unlinked
        g.blended[:] = land_view.detect_blended(g.W[:, g.filled])
        self.pieces = list(pieces)

    @staticmethod
    def _frame(pieces, uv_name):
        """The square the weightmaps cover (lo corner, side), read back from a mesh's mp_weights UV."""
        obj = pieces[0]
        me = obj.data
        uv = me.uv_layers[uv_name]
        world = _world(obj)
        corners = np.empty(len(me.loops), np.int32)
        me.loops.foreach_get("vertex_index", corners)
        uvs = np.empty(len(me.loops) * 2, np.float32)
        uv.data.foreach_get("uv", uvs)
        uvs = uvs.reshape(-1, 2)
        a, b = int(np.argmin(uvs[:, 0])), int(np.argmax(uvs[:, 0]))
        extent = (world[corners[b], 0] - world[corners[a], 0]) / max(uvs[b, 0] - uvs[a, 0], 1e-9)
        lo = world[corners[a], :2] - uvs[a] * extent
        return lo, float(extent)

    def keep(self):
        """Weightmaps are packed: Blender's undo stores an image's packed file, not its pixels, so pack them as painted."""
        for image in self.maps.values():
            if image.is_dirty:
                image.pack()

    def touch(self, r0, c0, r1, c1):
        return [name for name in self.maps]

    def write(self, names, layers=None, dirty=None):
        g = self.grid
        for layer, (name, channel) in self.table.items():
            if name in self.pixels and (layers is None or layer in layers):
                self.pixels[name][..., channel] = g.W[g.index(layer)]
        if layers is not None:
            names = [n for n in names if any(self.table[l][0] == n for l in layers if l in self.table)]
        for name in names:
            image = self.maps[name]
            image.pixels.foreach_set(self.pixels[name].ravel())
            image.update()


class BiomeStore:
    """A custom landscape's painted biome map (biome_maps: one mask per channel of each rectangle the material reads)
    as a grid over the weightmaps' square, a cell per map pixel. The masks share 1 like weight-blended layers (the
    material lerps each biome's colours over the previous ones: overlapping masks let the later biome cover the
    earlier), the rest being no biome; erasing gives back to no biome."""
    kind = "biomes"

    def __init__(self, pieces, layers, param, info, image):
        size = image.size[0]
        if image.size[1] != size:
            raise ValueError("a biome map that isn't square")
        sides = {(int(round(abs(r[2] - r[0]) * size)), int(round(abs(r[3] - r[1]) * size))) for r in info["rects"]}
        if len(sides) != 1 or len(set(next(iter(sides)))) != 1:
            raise ValueError("biome rectangles of different sizes")
        n = next(iter(sides))[0]
        labels = info.get("labels") or {}
        masks = [(i, c, labels.get("%d,%d" % (i, c))) for i in range(len(info["rects"])) for c in range(4)]
        masks.sort(key=lambda m: m[2] is None)      # the named biomes first
        names = []
        for i, c, name in masks:
            name = name or "Mask %d" % (i * 4 + c + 1)
            names.append(name if name not in names else "%s %d" % (name, i * 4 + c + 1))
        self.image, self.param, self.masks, self.pieces = image, param, [(i, c) for i, c, _ in masks], list(pieces)
        self.rects = info["rects"]
        lo, extent = layers.lo, layers.extent
        self.grid = g = Grid(names, (lo[0] + extent / n / 2, lo[1] + extent / n / 2), extent / n, (n, n))
        t = (np.arange(n) + 0.5) / n
        # each rectangle's pixel rows and columns under the grid's rows (+Y) and columns (+X): maptex.paint_uv
        self.at = [(np.clip(np.floor((1.0 - (r[1] + t * (r[3] - r[1]))) * size), 0, size - 1).astype(np.int64),
                    np.clip(np.floor((r[0] + t * (r[2] - r[0])) * size), 0, size - 1).astype(np.int64)) for r in info["rects"]]
        px = np.empty(size * size * 4, np.float32)
        image.pixels.foreach_get(px)
        self.px = px.reshape(size, size, 4)
        for k, (i, c) in enumerate(self.masks):
            rows, cols = self.at[i]
            g.W[k] = self.px[np.ix_(rows, cols)][..., c]
        lg = layers.grid       # the ground: the layer grid's height at each cell's centre, land where a pixel has any
        rows = np.clip(np.rint((g.origin[1] + np.arange(n) * g.cell - lg.origin[1]) / lg.cell), 0, lg.shape[0] - 1).astype(np.int64)
        cols = np.clip(np.rint((g.origin[0] + np.arange(n) * g.cell - lg.origin[0]) / lg.cell), 0, lg.shape[1] - 1).astype(np.int64)
        g.height[:] = lg.height[np.ix_(rows, cols)]
        starts = [np.floor(np.arange(n) * lg.shape[k] / n).astype(np.int64) for k in (0, 1)]
        land = np.maximum.reduceat(lg.filled.astype(np.uint8), starts[0], axis=0)
        g.filled[:] = np.maximum.reduceat(land, starts[1], axis=1) > 0
        g.avail[:] = g.filled[None]
        g.blended[:] = True
        g.erase_shares = False

    def hide_key(self, name):
        """The pieces' property hiding a biome's mask in the material (ue_graph.maptex)."""
        from ...material_porter.ue_graph import maptex
        i, c = self.masks[self.grid.index(name)]
        return maptex.hide_key(self.image.name, self.rects[i], c)

    def keep(self):
        """Blender's undo stores the image's packed file: packed as painted."""
        if self.image.is_dirty:
            self.image.pack()

    def touch(self, r0, c0, r1, c1):
        return [self.image.name]

    def write(self, names, layers=None, dirty=None):
        g = self.grid
        for k, (i, c) in enumerate(self.masks):
            if layers is None or g.layers[k] in layers:
                rows, cols = self.at[i]
                self.px[rows[:, None], cols[None, :], c] = g.W[k]
        self.image.pixels.foreach_set(self.px.ravel())
        self.image.update()


class MaskStore:
    """The Scatter mode's store: a scatter layer's paint mask (a float point attribute per piece, absent = 1) as one grid
    at the pieces' vertices (a map import's: its layer grid's), plus the layer's source weight per cell for the picture
    (weight = mask x source, as the scatter modifier reads it). No layer picked: the ground alone."""
    kind = "mask"

    def __init__(self, pieces, base=None):
        self.pieces = []
        if isinstance(base, AttributeStore):        # the same lattice
            b = base.grid
            self.grid = g = Grid([], b.origin, b.cell, b.shape)
            g.height[:], g.filled[:] = b.height, b.filled
            self.pieces = [{k: p[k] for k in ("obj", "rows", "cols", "corners", "box")} for p in base.pieces]
        else:
            worlds, lo, cell, shape, all_rows, all_cols = _lattice(pieces)
            self.grid = g = Grid([], (float(lo[0]), float(lo[1])), cell, shape)
            for obj, world, rows, cols in zip(pieces, worlds, all_rows, all_cols):
                g.height[rows, cols] = world[:, 2]
                g.filled[rows, cols] = True
                self.pieces.append({"obj": obj, "rows": rows, "cols": cols,
                                    "box": (int(rows.min()), int(cols.min()), int(rows.max()) + 1, int(cols.max()) + 1)})
        self.source = np.ones(g.shape, np.float32)
        self.masks = {}         # piece name: the picked layer's mask attribute on it
        self.colors = {}

    def set_layer(self, label, masks, source_of=None):
        """The layer the brush paints: `masks` {piece name: mask attribute}, source_of(obj) its source weight per
        vertex (None: 1). label None: no layer."""
        g = self.grid
        self.masks = dict(masks or {})
        self.source[:] = 1.0
        if label is None:
            g.layers, g.W, g.avail, g.blended = [], np.zeros((0,) + g.shape, np.float32), \
                np.zeros((0,) + g.shape, bool), np.zeros(0, bool)
            return
        g.layers = [label]
        g.W = np.ones((1,) + g.shape, np.float32)
        g.avail = g.filled[None].copy()
        g.blended = np.zeros(1, bool)       # a mask moves alone
        for p in self.pieces:
            obj = p["obj"]
            name = self.masks.get(obj.name)
            if name is None:        # a piece without the layer: nothing scattered, nothing to paint
                g.avail[0, p["rows"], p["cols"]] = False
                self.source[p["rows"], p["cols"]] = 0.0
                continue
            g.W[0, p["rows"], p["cols"]] = mask_values(obj, name)
            weight = source_of(obj) if source_of is not None else None
            if weight is not None:
                self.source[p["rows"], p["cols"]] = weight

    def weight(self):
        """mask x source per cell (None without a layer)."""
        return self.grid.W[0] * self.source if self.grid.layers else None

    def picture(self, shade, step, window=None):
        from .land_view import overlay
        return overlay(self.grid, self.weight(), shade, step, window)

    def readout(self, x, y):
        g = self.grid
        col, row = (int(round(v)) for v in g.to_cell(x, y))
        if not (0 <= row < g.shape[0] and 0 <= col < g.shape[1]) or not g.filled[row, col]:
            return ""
        text = "%.1f m" % g.height[row, col]
        if g.layers:
            m, s = float(g.W[0, row, col]), float(self.source[row, col])
            text += "   weight %.2f   mask %.2f × source %.2f" % (m * s, m, s)
        return text

    def keep(self):
        """Attributes live in the mesh: Blender's undo stores them."""

    def touch(self, r0, c0, r1, c1):
        return [p for p in self.pieces if p["box"][0] < r1 and r0 < p["box"][2] and p["box"][1] < c1 and c0 < p["box"][3]]

    def write(self, pieces, layers=None, dirty=None):
        """The grid's mask into these pieces' mask attributes (made, at 1, when absent), only `dirty` cells' vertices."""
        g = self.grid
        if not g.layers:
            return
        for p in pieces:
            me, name = p["obj"].data, self.masks.get(p["obj"].name)
            if name is None:
                continue
            a = me.attributes.get(name)
            if a is not None and (a.domain != 'POINT' or a.data_type != 'FLOAT'):
                me.attributes.remove(a)
                a = None
            if a is None:
                a = me.attributes.new(name, 'FLOAT', 'POINT')
                a.data.foreach_set("value", np.ones(len(a.data), np.float32))
            v = np.empty(len(a.data), np.float32)
            a.data.foreach_get("value", v)
            at = dirty[p["rows"], p["cols"]] if dirty is not None else slice(None)
            v[at] = g.W[0, p["rows"], p["cols"]][at]
            a.data.foreach_set("value", v)
            me.update()


def mask_values(obj, name):
    """A mask attribute's values per vertex (absent: 1)."""
    me = obj.data
    a = me.attributes.get(name)
    v = np.ones(len(me.vertices), np.float32)
    if a is not None and a.domain == 'POINT' and a.data_type == 'FLOAT':
        a.data.foreach_get("value", v)
    return v


def reload_images(pieces):
    """The pieces' weightmaps and painted maps read again from their packed files: an undo puts the packed file back
    but an image a material draws keeps its pixels (the stroke's) until reloaded."""
    import bpy
    names = set(dict(pieces[0].get("mp_weightmaps") or {}).values()) | set(dict(pieces[0].get("mp_biome_maps") or {}).values())
    for name in names:
        image = bpy.data.images.get(name)
        if image is not None and image.packed_file is not None:
            image.reload()


def biome_map(pieces):
    """(parameter, info, image) of the pieces' painted biome mask map, else None."""
    from ...material_porter import biome_maps
    import bpy
    piece = pieces[0]
    painted = dict(piece.get(biome_maps.KEY) or {})
    for param, info in biome_maps.maps(piece.active_material).items():
        image = bpy.data.images.get(painted.get(param, ""))
        if image is not None and not biome_maps.is_colour(param, info):
            return param, info, image
    return None


def try_biomes(pieces, layers):
    """(biome store, None), (None, why) when the material reads a biome map the tab can't paint, (None, None) when
    it reads none."""
    found = biome_map(pieces) if isinstance(layers, ImageStore) else None
    if found is None:
        return None, None
    try:
        return BiomeStore(pieces, layers, *found), None
    except (ValueError, KeyError, IndexError, MemoryError, ReferenceError) as e:
        return None, "Couldn't read the biome map: %s" % (e or type(e).__name__)


def _fill_between(a):
    """NaNs filled by linear interpolation between known values along each column, then each row."""
    out = a.copy()
    for b in (out.T, out):      # columns, then rows (views: filled in place)
        for line in b:
            known = ~np.isnan(line)
            if known.sum() >= 2 and not known.all():
                x = np.flatnonzero(known)
                line[~known] = np.interp(np.flatnonzero(~known), x, line[known])
            elif known.sum() == 1:
                line[~known] = line[known][0]
    return np.where(np.isnan(out), np.nanmean(a), out)


def try_load(pieces):
    """(store, None), or (None, why) when the pieces can't be read."""
    try:
        return load(pieces), None
    except (ValueError, KeyError, IndexError, MemoryError, ReferenceError) as e:
        return None, "Couldn't read this landscape: %s" % (e or type(e).__name__)


def load(pieces):
    """The group's store: its pieces' weightmaps when they have them, else their colour attributes."""
    return ImageStore(pieces) if _images(pieces[0]) else AttributeStore(pieces)
