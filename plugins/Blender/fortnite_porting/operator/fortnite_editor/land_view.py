"""The Landscape tab's maths, on a world-XY grid (no bpy): the brush (a dab's region and falloff), painting, erasing and
smoothing a layer, and the top-view picture (height shading tinted by the layers). Rows run along +Y, columns along +X.
UE layers are weight-blended (they share 1: painting one lowers the others) or independent (masks, alpha-blended:
painting one moves only it); Fortnite's recent maps paint independent layers."""
import colorsys

import numpy as np

EPS = 1e-6
SAMPLE = 100_000


class Grid:
    """Layer weights W (layers, rows, cols), where each layer exists (avail), the height, and which cells hold land."""

    def __init__(self, layers, origin, cell, shape):
        self.layers, self.origin, self.cell, self.shape = list(layers), origin, float(cell), tuple(shape)
        n = (len(self.layers),) + self.shape
        self.W = np.zeros(n, np.float32)
        self.avail = np.zeros(n, bool)
        self.height = np.zeros(self.shape, np.float32)
        self.filled = np.zeros(self.shape, bool)
        self.blended = np.ones(len(self.layers), bool)      # per layer: shares 1 with the other blended layers
        self.erase_shares = True        # erasing a blended layer gives its weight to the others (biomes: to no biome)

    def to_cell(self, x, y):
        return (x - self.origin[0]) / self.cell, (y - self.origin[1]) / self.cell

    def to_world(self, col, row):
        return self.origin[0] + col * self.cell, self.origin[1] + row * self.cell

    def index(self, layer):
        return self.layers.index(layer)


def detect_blended(W, tolerance=0.03, share=0.9):
    """Which layers (rows of W: weights of the painted cells) are weight-blended: the largest set summing to 1 on
    `share` of the cells, found by dropping layers one by one; none when no set does (all independent)."""
    keep = list(range(W.shape[0]))
    painted = W.sum(axis=0) > 0.01
    if not painted.any():
        return np.zeros(W.shape[0], bool)
    W = W[:, painted]
    if W.shape[1] > SAMPLE:         # a big map: a sample answers as well
        W = W[:, np.random.default_rng(0).choice(W.shape[1], SAMPLE, replace=False)]

    def fit(rows):
        return (np.abs(W[rows].sum(axis=0) - 1.0) < tolerance).mean() if rows else 0.0
    score = fit(keep)
    while score < share and len(keep) > 1:
        best = max(((fit([k for k in keep if k != r]), r) for r in keep), key=lambda t: t[0])
        if best[0] <= score:
            break
        score = best[0]
        keep.remove(best[1])
    out = np.zeros(W.shape[0], bool)
    if score >= share and len(keep) >= 2:
        out[keep] = True
    return out


def dab(grid, x, y, radius, falloff):
    """The cells a brush at world (x, y) reaches: (row slice, col slice) and each cell's amount 0..1. `falloff` 0 is a
    hard disc, 1 fades from the centre."""
    col, row = grid.to_cell(x, y)
    r = max(radius / grid.cell, 0.5)
    r0, r1 = max(0, int(np.floor(row - r))), min(grid.shape[0], int(np.ceil(row + r)) + 1)
    c0, c1 = max(0, int(np.floor(col - r))), min(grid.shape[1], int(np.ceil(col + r)) + 1)
    if r0 >= r1 or c0 >= c1:
        return (slice(0, 0), slice(0, 0)), np.zeros((0, 0), np.float32)
    rows, cols = np.mgrid[r0:r1, c0:c1]
    d = np.sqrt((rows - row) ** 2 + (cols - col) ** 2) / r
    return (slice(r0, r1), slice(c0, c1)), fade(d, falloff)


def fade(d, falloff):
    """The brush's amount 0..1 at distances `d` (in radii) from its centre."""
    inner = 1.0 - min(max(falloff, 0.0), 1.0)
    t = np.clip((1.0 - d) / max(1.0 - inner, EPS), 0.0, 1.0)
    return np.where(d <= 1.0, t * t * (3 - 2 * t), 0.0).astype(np.float32)     # smoothstep to the edge


def bounds(region):
    return region[0].start, region[1].start, region[0].stop, region[1].stop


def mark(grid, region, dirty=None):
    """The cells a stroke changed (written back alone: vertices elsewhere keep their own weights)."""
    if dirty is None:
        dirty = np.zeros(grid.shape, bool)
    dirty[region] = True
    return dirty


def apply(grid, layer, region, amount, strength, mode):
    """Paint, erase or smooth `layer` in a dab's region; returns how many reached cells lack the layer (left alone)."""
    if amount.size == 0:
        return 0
    li = grid.index(layer)
    W, avail = grid.W[:, region[0], region[1]], grid.avail[:, region[0], region[1]]
    land = grid.filled[region]
    a = amount * min(max(strength, 0.0), 1.0)
    here = avail[li] & land & (a > 0)
    lacking = int(((~avail[li]) & land & (a > 0)).sum())
    blended = grid.blended
    shares = avail & blended[:, None, None]      # the layers a blended layer shares 1 with
    if not blended[li] or (mode == "erase" and not grid.erase_shares):      # only it moves
        if mode == "paint":
            W[li] = np.where(here, W[li] + a * (1.0 - W[li]), W[li])
        elif mode == "erase":
            W[li] = np.where(here, W[li] * (1.0 - a), W[li])
        elif mode == "smooth":
            _smooth(grid, region, a, land, W, avail, [li])
        return lacking
    if mode == "paint":
        # the blended weights lerp toward this layer alone: the others lose by the dab's amount, as UE paints. Scaling
        # them to fill 1 instead lifted a half-painted cell's faint layer to full at the dab's very edge (a hard ring)
        W[li] = np.where(here, W[li] + a * (1.0 - W[li]), W[li])
        for i in range(W.shape[0]):
            if i != li:
                W[i] = np.where(here & shares[i], W[i] * (1.0 - a), W[i])
    elif mode == "erase":
        before = W[li].copy()
        W[li] = np.where(here, W[li] * (1.0 - a), W[li])
        freed = before - W[li]
        others = [i for i in range(W.shape[0]) if i != li and blended[i]]
        total = sum((W[i] * avail[i] for i in others), np.zeros_like(freed))
        count = sum((avail[i].astype(np.float32) for i in others), np.zeros_like(freed))
        stuck = here & (count == 0)         # the only layer here: nothing to give its weight to
        W[li] = np.where(stuck, before, W[li])
        freed = np.where(stuck, 0.0, freed)
        for i in others:
            share = np.where(total > EPS, W[i] / np.maximum(total, EPS), 1.0 / np.maximum(count, 1.0))
            W[i] = np.where(here & avail[i], W[i] + freed * share, W[i])
    elif mode == "smooth":
        rows = [i for i in range(W.shape[0]) if blended[i]]
        cells = land & (a > 0)
        # each cell's blended total eases toward its neighbours' like the layers do (1 stays 1); rescaling every
        # reached cell to 1 lifted a half-painted cell to full at the dab's edge
        r0, r1, c0, c1 = _around(grid, region)
        both = grid.avail[:, r0:r1, c0:c1] & blended[:, None, None]
        before = (W * shares).sum(axis=0)
        target = before + a * (_mean(grid, region, (grid.W[:, r0:r1, c0:c1] * both).sum(axis=0)) - before)
        _smooth(grid, region, a, land, W, avail, rows)
        after = (W * shares).sum(axis=0)
        scale = np.where(cells & (after > EPS), target / np.maximum(after, EPS), 1.0)
        for i in rows:
            W[i] = np.where(shares[i], W[i] * scale, W[i])
    return lacking


def _around(grid, region):
    """The region grown by a cell each way, within the grid: (r0, r1, c0, c1)."""
    rs, cs = region
    return max(rs.start - 1, 0), min(rs.stop + 1, grid.shape[0]), max(cs.start - 1, 0), min(cs.stop + 1, grid.shape[1])


def _mean(grid, region, plane):
    """The 3x3 neighbourhood mean over land of `plane` (the values of _around's box), for the region's cells."""
    rs, cs = region
    r0, r1, c0, c1 = _around(grid, region)
    fill = grid.filled[r0:r1, c0:c1].astype(np.float32)
    padded_w, padded_n = np.pad(plane * fill, 1), np.pad(fill, 1)
    acc, n = np.zeros_like(plane), np.zeros_like(plane)
    for dr in (0, 1, 2):
        for dc in (0, 1, 2):
            acc += padded_w[dr:dr + plane.shape[0], dc:dc + plane.shape[1]]
            n += padded_n[dr:dr + plane.shape[0], dc:dc + plane.shape[1]]
    return (acc / np.maximum(n, 1.0))[rs.start - r0:rs.stop - r0, cs.start - c0:cs.stop - c0]


def _smooth(grid, region, a, land, W, avail, rows):
    """These layers pulled toward their 3x3 neighbourhood's mean (neighbours past the region read from the grid)."""
    r0, r1, c0, c1 = _around(grid, region)
    cells = land & (a > 0)
    for i in rows:
        mean = _mean(grid, region, grid.W[i, r0:r1, c0:c1])
        W[i] = np.where(cells & avail[i], W[i] + a * (mean - W[i]), W[i])


def retarget(frame, old, new):
    """A view of grid `old` made a view of grid `new` showing the same ground at the same place (frame cell f is
    world origin + (f - 0.5) * cell)."""
    k = new.cell / old.cell
    frame.ox += frame.scale * ((new.origin[0] - old.origin[0]) / old.cell + 0.5 * (1 - k))
    frame.oy += frame.scale * ((new.origin[1] - old.origin[1]) / old.cell + 0.5 * (1 - k))
    frame.scale, frame.fit = frame.scale * k, frame.fit * k
    frame.cols, frame.rows = new.shape[1], new.shape[0]


class Frame:
    """The grid (cols x rows, row 0 south) in a region: fitted to a box (x, y, width, height), then zoomed about the
    mouse and panned."""

    def __init__(self, cols, rows, width, height, margin=12, x=0.0, y=0.0):
        self.cols, self.rows = cols, rows
        self.scale = max(min((width - 2 * margin) / max(cols, 1), (height - 2 * margin) / max(rows, 1)), 1e-3)
        self.fit = self.scale
        self.ox, self.oy = x + (width - cols * self.scale) / 2, y + (height - rows * self.scale) / 2
        self.box, self.moved = (x, y, width, height), False

    def to_region(self, col, row):
        return self.ox + col * self.scale, self.oy + row * self.scale

    def to_cell(self, x, y):
        return (x - self.ox) / self.scale, (y - self.oy) / self.scale

    def zoom_at(self, x, y, steps):
        col, row = self.to_cell(x, y)
        self.scale = min(max(self.scale * 1.2 ** steps, self.fit * 0.5), self.fit * 400)
        self.ox, self.oy = x - col * self.scale, y - row * self.scale
        self.moved = True

    def pan(self, dx, dy):
        self.ox += dx
        self.oy += dy
        self.moved = True

    def follow(self, x, y, width, height):
        """The box changed (the area resized, a region opened): the view moves with its centre."""
        bx, by, bw, bh = self.box
        self.ox += x + width / 2 - (bx + bw / 2)
        self.oy += y + height / 2 - (by + bh / 2)
        self.box = (x, y, width, height)


PALETTE = ((0.36, 0.72, 0.30), (0.62, 0.52, 0.36), (0.85, 0.78, 0.52), (0.55, 0.55, 0.58), (0.30, 0.55, 0.85),
           (0.80, 0.40, 0.30), (0.70, 0.45, 0.80), (0.35, 0.75, 0.70), (0.90, 0.65, 0.20), (0.45, 0.35, 0.25))


def palette(names):
    """A readable colour per layer, stable by sorted name: a fixed list first, then evenly spread hues."""
    out = {}
    for i, name in enumerate(sorted(names)):
        if i < len(PALETTE):
            out[name] = PALETTE[i]
        else:
            out[name] = colorsys.hsv_to_rgb((i * 0.618) % 1.0, 0.55, 0.85)
    return out


REACH = 4      # cells an empty cell looks for land to show (a finer grid's gaps, a coarser piece's)


def source(grid):
    """For each cell, the flat index of the land cell it shows: itself, or the nearest land within REACH (the empty
    cells between a finer grid's vertices), -1 for none. Kept on the grid (land never moves)."""
    if getattr(grid, "_source", None) is not None:
        return grid._source
    h, w = grid.shape
    src = np.where(grid.filled, np.arange(h * w).reshape(h, w), -1)
    for _ in range(REACH):
        if (src >= 0).all():
            break
        grown = src.copy()
        for dr, dc in ((0, 1), (0, -1), (1, 0), (-1, 0), (1, 1), (1, -1), (-1, 1), (-1, -1)):
            shifted = np.full_like(src, -1)
            shifted[max(dr, 0):h + min(dr, 0), max(dc, 0):w + min(dc, 0)] = src[max(-dr, 0):h + min(-dr, 0), max(-dc, 0):w + min(-dc, 0)]
            grown = np.where(grown < 0, shifted, grown)
        src = grown
    grid._source = src
    return src


def hillshade(height, cell, filled, src=None):
    """0.35..1 shading lit from the north-west, flat land 0.8; `src` (source()): empty cells take their land's height."""
    if src is not None:
        height = np.where(src >= 0, height.ravel()[np.maximum(src, 0)], height)
        filled = src >= 0
    h = np.where(filled, height, np.nan)
    h = np.where(np.isnan(h), np.nanmean(h) if filled.any() else 0.0, h)
    dz_dy, dz_dx = np.gradient(h, cell)
    nx, ny, nz = -dz_dx, -dz_dy, np.ones_like(h)
    length = np.sqrt(nx * nx + ny * ny + nz * nz)
    light = np.array((-0.5, 0.5, 0.7))
    light = light / np.linalg.norm(light)
    lit = (nx * light[0] + ny * light[1] + nz * light[2]) / length
    flat = light[2]
    return np.clip(0.8 + (lit - flat) * 1.6, 0.35, 1.0).astype(np.float32)


def compose(grid, colors, shown, layer_only, shade=None, step=1, window=None):
    """The top view as RGBA (rows, cols, 4): the shading tinted by the shown layers' colours by weight, or (layer_only:
    a layer's name) that layer's weight as grey over a faint shading. Transparent where there is no land."""
    """`shade`: the hillshade already made; `step`: every step-th cell (a big grid drawn smaller); `window`: picture
    pixels (r0, c0, r1, c1) to make alone (a stroke's part)."""
    r0, c0, r1, c1 = window or (0, 0, -(-grid.shape[0] // step), -(-grid.shape[1] // step))
    kr, kc = slice(r0 * step, r1 * step, step), slice(c0 * step, c1 * step, step)
    src = source(grid)[kr, kc]
    show = np.maximum(src, 0).ravel()
    W = grid.W.reshape(len(grid.layers), -1)[:, show].reshape((len(grid.layers),) + src.shape)
    avail = grid.avail.reshape(len(grid.layers), -1)[:, show].reshape((len(grid.layers),) + src.shape)
    filled = src >= 0
    if shade is None:
        shade = hillshade(grid.height, grid.cell, grid.filled, source(grid))
    shade = shade[kr, kc]
    shape = filled.shape
    rgba = np.zeros(shape + (4,), np.float32)
    if layer_only is not None:
        w = W[grid.index(layer_only)] * avail[grid.index(layer_only)]
        rgba[..., 0] = rgba[..., 1] = rgba[..., 2] = w
    else:
        tint = np.zeros(shape + (3,), np.float32)
        weight = np.zeros(shape, np.float32)
        for i, name in enumerate(grid.layers):
            if name in shown:
                w = W[i] * avail[i]
                tint += w[..., None] * np.array(colors[name], np.float32)
                weight += w
        base = np.float32(0.5)
        mix = np.clip(weight, 0.0, 1.0)[..., None]
        colour = tint / np.maximum(weight, EPS)[..., None]
        rgba[..., :3] = (base * (1 - mix) + colour * mix) * shade[..., None]
    rgba[..., 3] = filled.astype(np.float32)
    return rgba


WEIGHT = (0.36, 0.72, 0.30)     # a scatter layer's weight over the ground


def overlay(grid, plane, shade, step=1, window=None):
    """The top view with `plane` (rows, cols: 0..1, None: none) tinting the grey shading: a scatter layer's weight."""
    r0, c0, r1, c1 = window or (0, 0, -(-grid.shape[0] // step), -(-grid.shape[1] // step))
    kr, kc = slice(r0 * step, r1 * step, step), slice(c0 * step, c1 * step, step)
    src = source(grid)[kr, kc]
    filled = src >= 0
    if shade is None:
        shade = hillshade(grid.height, grid.cell, grid.filled, source(grid))
    shade = shade[kr, kc]
    rgba = np.zeros(filled.shape + (4,), np.float32)
    w = np.zeros(filled.shape, np.float32) if plane is None else \
        np.clip(plane.ravel()[np.maximum(src, 0).ravel()].reshape(src.shape), 0.0, 1.0)
    base = np.float32(0.45)
    rgba[..., :3] = (base * (1 - w[..., None]) + np.array(WEIGHT, np.float32) * w[..., None]) * shade[..., None]
    rgba[..., 3] = filled.astype(np.float32)
    return rgba
