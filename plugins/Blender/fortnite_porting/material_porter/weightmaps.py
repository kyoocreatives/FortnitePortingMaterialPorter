"""Landscape weightmaps for meshes a landscape material was put on: its layers painted into images, three to an image
(Weightmap1's red, green and blue...), as UE paints a landscape's weightmaps.

Weights in images keep their edges whatever the mesh's density, so a material's height blends break them up as in
game (painted as vertex colours, a stroke spreads over the faces around it). The meshes a material was put on together
share one set of images, read through UV map "mp_weights": a top projection over their combined world bounds, about
four texels a metre. obj["mp_layers"] maps each layer to its channel ("Weightmap1.R"), obj["mp_weightmaps"] each
weightmap to its image; the images are packed into the .blend."""
import uuid

import bpy
import numpy as np

from . import landscape_layers

KEY = "mp_weightmaps"
SET = "mp_weight_set"
UV = "mp_weights"
PREFIX = "Weightmap"
TEXELS_PER_METRE = 4.0
SIZES = (256, 4096)


def images(obj):
    """{weightmap: image} of a mesh painted in images; empty for one painted in colour attributes (or none)."""
    return {name: bpy.data.images.get(image) for name, image in dict(obj.get(KEY) or {}).items()
            if bpy.data.images.get(image) is not None}


def _pixels(image):
    px = np.empty(image.size[0] * image.size[1] * 4, dtype=np.float32)
    image.pixels.foreach_get(px)
    return px.reshape(-1, 4)


def _store(image, px):
    image.pixels.foreach_set(px.ravel())
    image.update()
    image.pack()


def weight(obj, layer):
    """A layer's total weight in its weightmap, or None for a layer not painted in images."""
    where = landscape_layers.table(obj).get(layer)
    maps = images(obj)
    if where is None or where[0] not in maps:
        return None
    return float(_pixels(maps[where[0]])[:, where[1]].sum())


def _project(meshes, lo, size):
    """UV map UV on each mesh: world XY over the square from `lo`, `size` metres a side."""
    for o in meshes:
        me = o.data
        uv = me.uv_layers.get(UV) or me.uv_layers.new(name=UV)
        co = np.empty(len(me.vertices) * 3, dtype=np.float32)
        me.vertices.foreach_get("co", co)
        m = np.array(o.matrix_world)
        world = co.reshape(-1, 3) @ m[:3, :3].T + m[:3, 3]
        corners = np.empty(len(me.loops), dtype=np.int32)
        me.loops.foreach_get("vertex_index", corners)
        xy = (world[corners, :2] - np.array(lo[:2])) / size
        uv.data.foreach_set("uv", xy.astype(np.float32).ravel())


def ensure(meshes, layers):
    """Weightmaps holding `layers` for these meshes (shared), made or extended; returns {layer: (weightmap, channel)}.
    New layers are unpainted."""
    sets = {o.get(SET) for o in meshes}
    if len(sets) == 1 and None not in sets and images(meshes[0]):
        name = next(iter(sets))
        packed = landscape_layers.table(meshes[0])
        maps = dict(meshes[0].get(KEY) or {})
        size = next(iter(images(meshes[0]).values())).size[0]
    else:
        name, packed, maps = "Landscape %s" % uuid.uuid4().hex[:6], {}, {}
        points = []
        for o in meshes:
            points += [o.matrix_world @ v.co for v in o.data.vertices]
        lo = [min(p[i] for p in points) for i in range(3)]
        hi = [max(p[i] for p in points) for i in range(3)]
        extent = max(hi[0] - lo[0], hi[1] - lo[1], 1e-3)
        size = int(2 ** round(np.log2(min(max(extent * TEXELS_PER_METRE, SIZES[0]), SIZES[1]))))
        _project(meshes, lo, extent)
    used = set(packed.values())
    slots = ((PREFIX + str(i), c) for i in range(1, 100) for c in range(3))
    for layer in sorted(layers):
        if layer in packed:
            continue
        slot = next(s for s in slots if s not in used)
        used.add(slot)
        packed[layer] = slot
        if slot[0] not in maps:
            image = bpy.data.images.new("%s %s" % (name, slot[0]), size, size, alpha=False, float_buffer=False)
            image.colorspace_settings.name = 'Non-Color'
            black = np.zeros((size * size, 4), dtype=np.float32)
            black[:, 3] = 1.0
            _store(image, black)        # written, then packed (a generated image packs as nothing)
            maps[slot[0]] = image.name
    for o in meshes:
        o[SET] = name
        o[KEY] = maps
        o[landscape_layers.KEY] = {layer: "%s.%s" % (w, landscape_layers.CHANNELS[c]) for layer, (w, c) in packed.items()}
        o[landscape_layers.ALL] = 1.0
        o["mp_landscape"] = 1.0
    return packed


# painting tools (the Landscape panel's)

def fill(obj, layer, value=1.0):
    """This layer everywhere at `value`; at 1 the others are cleared, as UE's fill does."""
    packed, maps = landscape_layers.table(obj), images(obj)
    target = packed.get(layer)
    if target is None or target[0] not in maps:
        return
    for name, image in maps.items():
        px = _pixels(image)
        changed = False
        for other, (w, c) in packed.items():
            if w != name:
                continue
            if other == layer:
                px[:, c] = value
                changed = True
            elif value >= 1.0:
                px[:, c] = 0.0
                changed = True
        if changed:
            _store(image, px)


def normalize(obj):
    """Every texel's layers scaled to sum to 1 (left alone where nothing is painted), as UE's weight painting keeps
    them."""
    packed, maps = landscape_layers.table(obj), images(obj)
    pixels = {name: _pixels(image) for name, image in maps.items()}
    if not pixels:
        return
    total = sum(pixels[w][:, c] for w, c in packed.values() if w in pixels)
    scale = np.where(total > 1e-6, 1.0 / np.maximum(total, 1e-6), 1.0)
    for w, c in packed.values():
        if w in pixels:
            pixels[w][:, c] *= scale
    for name, px in pixels.items():
        _store(maps[name], px)
