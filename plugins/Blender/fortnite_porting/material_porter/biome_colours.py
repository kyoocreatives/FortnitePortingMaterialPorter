"""Colour maps that follow the biome masks (Hera's grass colours): each biome's colour measured from the game, where
its mask covers the game's map, and the mesh's colour map painted from its own masks with them.

The game's mask texture (BiomeTexture: four quarters of masks) and colour map (BiomeGrassColorTextureArray) cover the
same island, so where Red Woods' mask is on in the game the colour map holds Red Woods' grass colour. On the mesh,
colour = the ground's (no biome) blended toward each painted biome's by its weight, worked out in the shader from the
painted masks (config(): entry["biome_follow"], ue_graph.maptex.follow). obj["mp_follow_biomes"] turns it on (the
default); turned off, bake() paints the colour map with it once, to be painted by hand. The measured colours are kept
in obj["mp_biome_colours"]."""
import bpy
import numpy as np

from . import biome_maps, weightmaps

FOLLOW = "mp_follow_biomes"
COLOURS = "mp_biome_colours"
SAMPLES = 256


def game_image(info):
    """The game texture a map-wide parameter read (its image is named after it)."""
    return bpy.data.images.get(info["texture"].split("/")[-1].split(".")[0])


def _linear(im, px):
    if im.is_float or im.colorspace_settings.is_data:
        return px
    return np.where(px <= 0.04045, px / 12.92, ((px + 0.055) / 1.055) ** 2.4)


def _view(px, rect, n):
    """`px` (rows up) over `rect`, n x n, rows in the mesh's V order (as maptex.sample reads a map)."""
    h, w = px.shape[:2]
    u0, v0, u1, v1 = rect
    t = (np.arange(n) + 0.5) / n
    u, v = np.meshgrid(t, t)
    xs = np.clip(((u0 + u * (u1 - u0)) * w).astype(int), 0, w - 1)
    ys = np.clip(((1.0 - (v0 + v * (v1 - v0))) * h).astype(int), 0, h - 1)
    return px[ys, xs]


def _masks(px, info, n):
    return {(i, c): _view(px, rect, n)[..., c] for i, rect in enumerate(info["rects"]) for c in range(4)}


def measure(mask_info, colour_info):
    """{"ground": rgb, "rect,channel": rgb}: the game's colour where no mask is on, and under each mask (on over
    half), linear; None without both game textures."""
    mask, colour = game_image(mask_info), game_image(colour_info)
    if mask is None or colour is None or not mask.size[0] or not colour.size[0]:
        return None
    c = _linear(colour, weightmaps._pixels(colour).reshape(colour.size[1], colour.size[0], 4))
    rgb = _view(c, colour_info["rects"][0], SAMPLES)[..., :3]
    masks = _masks(weightmaps._pixels(mask).reshape(mask.size[1], mask.size[0], 4), mask_info, SAMPLES)
    bare = sum(masks.values()) < 0.05
    out = {"ground": (rgb[bare] if bare.any() else rgb.reshape(-1, 3)).mean(axis=0).tolist()}
    for (i, ch), m in masks.items():
        on = m > 0.5
        if on.mean() > 0.001:
            out["%d,%d" % (i, ch)] = rgb[on].mean(axis=0).tolist()
    return out


def pair(obj, found=None):
    """(mask parameter, colour parameter) of the mesh's painted maps that follow each other, else None."""
    found = biome_maps.maps(obj.active_material) if found is None else found
    painted = dict(obj.get(biome_maps.KEY) or {})
    masks = [p for p, i in found.items() if p in painted and not biome_maps.is_colour(p, i) and len(i["rects"]) == 4]
    colours = [p for p, i in found.items() if p in painted and biome_maps.is_colour(p, i) and len(i["rects"]) == 1]
    return (masks[0], colours[0]) if masks and colours else None


def _colours(obj, found, mask_param, colour_param):
    cache = dict(obj.get(COLOURS) or {})
    colours = cache.get(colour_param)
    if colours is None:
        colours = measure(found[mask_param], found[colour_param])
        if colours is None:
            return None
        cache[colour_param] = colours
        obj[COLOURS] = cache
    return {k: [float(x) for x in v] for k, v in dict(colours).items()}


def config(obj, found):
    """entry["biome_follow"]: {colour parameter: {"mask": image, "rects", "colours"}} while the colours follow the
    masks, else {}. `found`: the maps the material read (biome_maps.maps)."""
    which = pair(obj, found)
    if which is None or not obj.get(FOLLOW, True):
        return {}
    mask_param, colour_param = which
    mask_img = dict(obj.get(biome_maps.KEY))[mask_param]
    colours = _colours(obj, found, mask_param, colour_param)
    if colours is None or bpy.data.images.get(mask_img) is None:
        return {}
    return {colour_param: {"mask": mask_img, "rects": found[mask_param]["rects"], "colours": colours}}


def bake(obj):
    """The colour map painted from the mesh's masks, as the shader follows them; whether it was."""
    found, which = biome_maps.maps(obj.active_material), pair(obj)
    if which is None:
        return False
    mask_param, colour_param = which
    painted = dict(obj.get(biome_maps.KEY))
    mask_img, colour_img = bpy.data.images.get(painted[mask_param]), bpy.data.images.get(painted[colour_param])
    colours = _colours(obj, found, mask_param, colour_param)
    if mask_img is None or colour_img is None or colours is None:
        return False
    colours = {k: np.array(v, dtype=np.float32) for k, v in colours.items()}
    n = colour_img.size[0]
    masks = _masks(weightmaps._pixels(mask_img).reshape(mask_img.size[1], mask_img.size[0], 4), found[mask_param], n)
    total = np.zeros((n, n), np.float32)
    mixed = np.zeros((n, n, 3), np.float32)
    for (i, ch), m in masks.items():
        c = colours.get("%d,%d" % (i, ch))
        if c is not None:
            total += m
            mixed += m[..., None] * c
    scale = np.where(total > 1.0, 1.0 / np.maximum(total, 1e-6), 1.0)
    rgb = colours["ground"] * (1.0 - total * scale)[..., None] + mixed * scale[..., None]
    px = np.ones((n, n, 4), np.float32)
    px[..., :3] = rgb[::-1]         # the mesh's V up: Blender's rows
    weightmaps._store(colour_img, px.reshape(-1, 4))
    return True
