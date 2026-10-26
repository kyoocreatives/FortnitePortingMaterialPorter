"""Biome maps: a landscape material's map-wide textures (ue_graph.maptex: Hera's BiomeTexture, Athena's BiomeColormap
and PaintLayerMasks...) made paintable on custom meshes.

The map image takes the game texture's place and layout: each rectangle the material reads (Hera's four quads) with
its RGBA channels, one biome each. The meshes sharing weightmaps share it, and read it across their combined area
through UV map mp_weights; to paint a rectangle, UV map "mp_map_<parameter>_<n>" puts the meshes over it.
obj["mp_biome_maps"] maps each parameter to its image; the image is packed into the .blend."""
import functools
import json

import bpy
import numpy as np

from . import weightmaps
from .ue_graph import maptex

KEY = "mp_biome_maps"
SIZE = 1024             # a map whose game scale isn't known
SPAN = (32, 1024)       # pixels a rectangle spans across the meshes, at most and at least

_loads = functools.lru_cache(maxsize=32)(json.loads)     # panels read it on every redraw


def maps(mat):
    """{parameter: {"texture", "rects"}} of the map-wide textures a material reads (as last built). Shared between
    calls: read it, don't change it."""
    return _loads(mat.get("mp_map_textures") or "{}") if mat is not None else {}


def uv_name(param, index):
    return "mp_map_%s_%d" % (param, index)


def _uv_maps(meshes, param, rects):
    for o in meshes:
        me = o.data
        base = me.uv_layers.get(weightmaps.UV)
        if base is None:
            continue
        uv = np.empty(len(me.loops) * 2, dtype=np.float32)
        base.data.foreach_get("uv", uv)
        uv = uv.reshape(-1, 2)
        for i, rect in enumerate(rects):
            layer = me.uv_layers.get(uv_name(param, i)) or me.uv_layers.new(name=uv_name(param, i))
            u, v = maptex.paint_uv(rect, uv[:, 0], uv[:, 1])
            layer.data.foreach_set("uv", np.stack([u, v], axis=1).astype(np.float32).ravel())


def is_colour(param, info):
    """Whether a map holds colours (Athena's BiomeColormap, Hera's grass colour array), not masks. The texture's
    colour space doesn't tell: Athena's colour map is data, Hera 4200's biome mask sRGB."""
    label = (param + " " + info.get("texture", "").split("/")[-1]).lower()      # (not its folder: Hera's BiomeMask)
    return ("color" in label or "colour" in label) and "mask" not in label


def create(meshes, param, info, game=None):
    """A paintable map for `param` on these meshes (they share weightmaps): its image, unpainted (a colour map, as
    Athena's BiomeColormap: each rectangle at the game texture's (`game`) average colour; black would paint the
    terrain out)."""
    meshes = [o for o in meshes if weightmaps.UV in o.data.uv_layers]
    if not meshes:
        return None
    name = "%s %s" % (meshes[0].get(weightmaps.SET) or "Landscape", param)
    size = _size(meshes, info, game)
    image = bpy.data.images.new(name, size, size, alpha=True, float_buffer=False)
    image.colorspace_settings.name = 'Non-Color'
    px = np.zeros((size, size, 4), dtype=np.float32)
    if game is not None and is_colour(param, info) and game.size[0] and game.size[1]:
        _start(px, info["rects"], game)
    weightmaps._store(image, px.reshape(-1, 4))
    _uv_maps(meshes, param, info["rects"])
    for o in meshes:
        found = dict(o.get(KEY) or {})
        found[param] = image.name
        o[KEY] = found
    return image


def _size(meshes, info, game):
    """The map's size: as many metres a pixel as the game's texture over its island (Hera's biome masks: about 5 m),
    so painted edges blend over the distance they do in game (a pixel per centimetre made biomes' contrasts cut
    hard edges)."""
    if game is None or not info.get("per_km") or not game.size[0]:
        return SIZE
    metres = 1000.0 / (info["per_km"] * game.size[0])
    points = np.concatenate([_world_xy(o) for o in meshes])
    extent = float(max(np.ptp(points[:, 0]), np.ptp(points[:, 1]), 1e-3))
    width = min(abs(r[2] - r[0]) for r in info["rects"]) or 1.0
    span = min(max(int(round(extent / metres)), SPAN[0]), SPAN[1])
    return int(round(span / width))


def _world_xy(o):
    co = np.empty(len(o.data.vertices) * 3, dtype=np.float32)
    o.data.vertices.foreach_get("co", co)
    m = np.array(o.matrix_world)
    return (co.reshape(-1, 3) @ m[:3, :3].T + m[:3, 3])[:, :2]


def _pixels_in(rect, size, height=None):
    """Pixel index ranges (columns, rows) of `rect` in an image `size` wide, `height` (else `size`) high (Blender's
    rows run up)."""
    height = height or size
    u0, v0, u1, v1 = rect
    xs = slice(int(round(min(u0, u1) * size)), max(int(round(max(u0, u1) * size)), int(round(min(u0, u1) * size)) + 1))
    # UE's v runs down: rows from 1 - v
    ys = slice(int(round((1.0 - max(v0, v1)) * height)), max(int(round((1.0 - min(v0, v1)) * height)), 1))
    return xs, ys


def _start(px, rects, game):
    w, h = game.size
    src = weightmaps._pixels(game).reshape(h, w, 4)
    if not game.is_float and not game.colorspace_settings.is_data:
        # an sRGB texture's bytes: the map is read as data (linear)
        src = np.where(src <= 0.04045, src / 12.92, ((src + 0.055) / 1.055) ** 2.4)
    for rect in rects:
        gx, gy = _pixels_in(rect, w, h)
        xs, ys = _pixels_in(rect, px.shape[1])
        px[ys, xs, :3] = src[gy, gx, :3].reshape(-1, 3).mean(axis=0)
        px[ys, xs, 3] = 1.0


def fill(obj, param, index, channel, info, value=1.0):
    """This biome everywhere: its channel of its rectangle at 1, every other biome's (every channel of every
    rectangle) at 0; channel -1 clears them all. `value` 0: only this biome cleared, the others kept."""
    image = bpy.data.images.get(dict(obj.get(KEY) or {}).get(param, ""))
    if image is None:
        return
    size = image.size[0]
    px = weightmaps._pixels(image).reshape(size, size, 4)
    for i, rect in enumerate(info["rects"]):
        xs, ys = _pixels_in(rect, size)
        if value <= 0.0 and channel >= 0:
            if i == index:
                px[ys, xs, channel] = 0.0
            continue
        px[ys, xs, :] = 0.0
        if i == index and channel >= 0:
            px[ys, xs, channel] = value
    weightmaps._store(image, px.reshape(-1, 4))
