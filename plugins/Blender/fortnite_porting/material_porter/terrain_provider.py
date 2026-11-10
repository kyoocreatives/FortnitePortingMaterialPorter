"""Terrain provider API: another add-on that generates terrain meshes drives a landscape material's layer weights and
the scatter layers on them. Published at register as bpy.app.driver_namespace["fp_terrain_api"] (this module); nothing
here runs unless it is called. Contract: docs/terrain_provider.md.

Weights go where the mesh keeps its layers: a mesh a landscape material was put on paints them in weightmap images
(weightmaps, read through UV map mp_weights), others in packed colour channels (landscape_layers)."""
import sys
import time

import bpy
import numpy as np

from ..logger import Log
from . import landscape_layers, weightmaps

VERSION = 1
NAME = "fp_terrain_api"
DELAY = 0.25            # s of quiet before terrain_changed refreshes
MAX_WAIT = 1.0          # s at most between refreshes while changes keep coming
FRAME = "mp_weights_frame"      # {"set", "affine": local XY -> mp_weights UV (2x3), "n": vertex count projected for}
ROWS = 256              # texel rows rasterised at once
ADOPTED = "mp_terrain_adopted"      # adopt() marked the mesh a landscape (unadopt() takes back only that)
STATE = {"pending": set(), "first": 0.0}
_CACHE = {}         # material pointer: (what its layer names were read from, the names)


# ------------------------------------------------------------------ layers

def _layer_nodes(mat):
    """A material's landscape layer names, cached until its trees change (game_layers runs on an add-on's redraws)."""
    from .material_onto import _layers
    tree = mat.node_tree
    key = (mat.name_full, tree.name_full if tree else "", len(tree.nodes) if tree else 0, mat.get("mp_switches"))
    found = _CACHE.get(mat.as_pointer())
    if found is None or found[0] != key:
        if len(_CACHE) > 64:
            _CACHE.clear()
        found = _CACHE[mat.as_pointer()] = (key, frozenset(_layers(mat)))
    return found[1]


def _material(obj):
    """(the mesh's game landscape material, its layer names), else (None, set())."""
    from .build import KEY_PATH
    for slot in obj.material_slots:
        mat = slot.material
        if mat is not None and mat.get(KEY_PATH) and (names := _layer_nodes(mat)):
            return mat, set(names)
    return None, set()


def game_layers(obj):
    """The layers the mesh's game landscape material blends (and, on a mesh it was put on, every layer it has a
    channel for); [] without one."""
    mat, names = _material(obj)
    if mat is None:
        return []
    if obj.get(landscape_layers.ALL):
        names = names | set(landscape_layers.table(obj))
    return sorted(names)


def write_layers(obj, weights):
    """Per-vertex weights {layer: (vertex count,) floats} into the mesh's layer channels (made for new layers),
    normalised as the Normalize tool does: every texel or vertex's layers scaled to sum 1, left alone where none is
    painted. Layers not given keep their weights and are normalised with the rest."""
    n = len(obj.data.vertices)
    given = {}
    for name, w in weights.items():
        a = np.nan_to_num(np.asarray(w, dtype=np.float32).ravel())
        if len(a) != n:
            raise ValueError("%s: %d weights for layer %r, the mesh has %d vertices" % (obj.name, len(a), name, n))
        given[str(name)] = np.clip(a, 0.0, 1.0)
    if not given:
        return
    if weightmaps.images(obj):
        _write_images(obj, given)
    else:
        _write_attributes(obj, given)
    terrain_changed(obj)


def _scene(obj):
    return obj.users_scene[0] if obj.users_scene else bpy.context.scene


def _write_attributes(obj, given):
    me = obj.data
    table = landscape_layers.table(obj)
    missing = [layer for layer in given if layer not in table]
    if missing:
        first = next((me.color_attributes[a] for a, _ in table.values() if a in me.color_attributes), None)
        data_type, domain = (first.data_type, first.domain) if first is not None else ('FLOAT_COLOR', 'POINT')
        # a map's proxy keeps the scene-wide order (every proxy a layer in the same channel)
        names = landscape_layers.order(_scene(obj), missing) if obj.get(landscape_layers.MAP) else sorted(missing)
        table = landscape_layers._place(obj, names, data_type, domain)
    values = {a: landscape_layers._values(me.color_attributes[a]) for a in {a for a, _ in table.values()}
              if a in me.color_attributes}
    corners = None
    for layer, (a, c) in table.items():
        if layer in given and a in values:
            w = given[layer]
            if len(values[a]) != len(w):        # corner domain
                if corners is None:
                    corners = np.empty(len(me.loops), np.int32)
                    me.loops.foreach_get("vertex_index", corners)
                w = w[corners]
            values[a][:, c] = w
    _normalise([(values[a], c) for a, c in table.values() if a in values])
    for a, v in values.items():
        me.color_attributes[a].data.foreach_set("color", v.ravel())
    me.update()


def _normalise(channels):
    """[(array (k, 4), channel)] sharing rows: each row's channels scaled to sum 1 (rows summing 0 left alone)."""
    if not channels:
        return
    total = sum(v[..., c] for v, c in channels)
    scale = np.where(total > 1e-6, 1.0 / np.maximum(total, 1e-6), 1.0).astype(np.float32)
    for v, c in channels:
        v[..., c] *= scale


# ------------------------------------------------------------------ weightmap images

def _set_meshes(obj):
    name = obj.get(weightmaps.SET)
    found = [o for o in bpy.data.objects if o.type == 'MESH' and name and o.get(weightmaps.SET) == name]
    return found or [obj]


def _local(me):
    co = np.empty(len(me.vertices) * 3, np.float64)
    me.vertices.foreach_get("co", co)
    return co.reshape(-1, 3)


def _corners(me):
    c = np.empty(len(me.loops), np.int32)
    me.loops.foreach_get("vertex_index", c)
    return c


def _affine(obj):
    """The 2x3 map from local XY to UV map mp_weights, kept on the object. Worked out from the UV the first time, again
    when the weightmap set changed or the UV no longer fits it (a generator moved the vertices or laid the UV again:
    weights are written through the map the material reads them through). None without the UV map."""
    me = obj.data
    group = obj.get(weightmaps.SET) or ""
    frame = obj.get(FRAME)
    kept = None
    if frame is not None and frame.get("set") == group:
        kept = np.array(frame["affine"], np.float64).reshape(2, 3)
    uv_layer = me.uv_layers.get(weightmaps.UV)
    if uv_layer is None or not len(me.loops):
        return kept
    uv = np.empty(len(me.loops) * 2, np.float32)
    uv_layer.data.foreach_get("uv", uv)
    pick = np.unique(np.linspace(0, len(me.loops) - 1, min(len(me.loops), 4096)).astype(np.int64))
    xy = _local(me)[_corners(me)[pick], :2]
    uv = uv.reshape(-1, 2)[pick].astype(np.float64)
    A = np.column_stack([xy, np.ones(len(xy))])
    if kept is not None and np.abs(A @ kept.T - uv).max() < 1e-4:
        return kept
    a = np.linalg.lstsq(A, uv, rcond=None)[0].T
    if kept is not None and (np.abs(A @ a.T - uv).max() > 1e-3 or abs(np.linalg.det(a[:, :2])) < 1e-12):
        return kept     # a UV that isn't a top projection (a rebuilt mesh's empty one): laid again from the frame
    obj[FRAME] = {"set": group, "affine": [float(v) for v in a.ravel()], "n": len(me.vertices)}
    return a


def _reproject(obj):
    """UV map mp_weights (and the painted biome maps' UV maps) laid again from the kept frame after the mesh was
    rebuilt with another vertex count or without the UV map."""
    me = obj.data
    a = _affine(obj)
    frame = obj.get(FRAME)
    if a is None or frame is None or not len(me.loops):
        return
    if frame.get("n") == len(me.vertices) and weightmaps.UV in me.uv_layers:
        return
    uv_layer = me.uv_layers.get(weightmaps.UV) or me.uv_layers.new(name=weightmaps.UV)
    xy = _local(me)[_corners(me), :2]
    uv = xy @ a[:, :2].T + a[:, 2]
    uv_layer.data.foreach_set("uv", uv.astype(np.float32).ravel())
    frame["n"] = len(me.vertices)
    from . import biome_maps
    rects = biome_maps.maps(obj.active_material)
    for param in dict(obj.get(biome_maps.KEY) or {}):
        if param in rects:
            biome_maps._uv_maps([obj], param, rects[param]["rects"])


def _grid(xy):
    """(xs, ys, order) when the vertices form a full grid in local XY (order: vertex indices row by row), else None."""
    x, y = np.round(xy[:, 0], 5), np.round(xy[:, 1], 5)
    xs, ys = np.unique(x), np.unique(y)
    if len(xs) < 2 or len(ys) < 2 or len(xs) * len(ys) != len(xy):
        return None
    order = np.lexsort((x, y))
    if not (x[order].reshape(len(ys), len(xs)) == xs).all():
        return None
    return xs, ys, order


def _fill_between(a):
    """NaNs filled by linear interpolation along each column, then each row (the rest: the mean)."""
    out = a.copy()
    for b in (out.T, out):
        for line in b:
            known = ~np.isnan(line)
            if known.sum() >= 2 and not known.all():
                x = np.flatnonzero(known)
                line[~known] = np.interp(np.flatnonzero(~known), x, line[known])
            elif known.sum() == 1:
                line[~known] = line[known][0]
    return np.where(np.isnan(out), np.nanmean(a) if (~np.isnan(a)).any() else 0.0, out)


def _texels(obj, given, size):
    """Yields (row slice, column slice, {layer: values}) of the texels under the mesh: a grid's weights bilinearly
    sampled at each texel's centre, another mesh's averaged per texel and interpolated between."""
    me = obj.data
    a = _affine(obj)
    if a is None:
        raise ValueError("%s has weightmaps but no UV map %s" % (obj.name, weightmaps.UV))
    xy = _local(me)[:, :2]
    uv = xy @ a[:, :2].T + a[:, 2]
    lo, hi = uv.min(axis=0), uv.max(axis=0)
    c0, r0 = np.clip(np.ceil(lo * size - 0.5).astype(int), 0, size)
    c1, r1 = np.clip(np.floor(hi * size - 0.5).astype(int) + 1, 0, size)
    if c1 <= c0 or r1 <= r0:
        return
    grid = _grid(xy)
    if grid is None:
        cols = np.clip(np.rint(uv[:, 0] * size - 0.5).astype(int), c0, c1 - 1) - c0
        rows = np.clip(np.rint(uv[:, 1] * size - 0.5).astype(int), r0, r1 - 1) - r0
        shape = (r1 - r0, c1 - c0)
        cell = rows * shape[1] + cols
        count = np.bincount(cell, minlength=shape[0] * shape[1]).astype(np.float64)
        out = {}
        for layer, w in given.items():
            s = np.bincount(cell, weights=w, minlength=shape[0] * shape[1])
            v = np.where(count > 0, s / np.maximum(count, 1), np.nan).reshape(shape)
            out[layer] = _fill_between(v).astype(np.float32)
        yield slice(r0, r1), slice(c0, c1), out
        return
    xs, ys, order = grid
    planes = {layer: w[order].reshape(len(ys), len(xs)) for layer, w in given.items()}
    u = (np.arange(c0, c1) + 0.5) / size
    if abs(a[0, 1]) < 1e-9 and abs(a[1, 0]) < 1e-9:     # UV along the grid's axes (the usual): separable
        v = (np.arange(r0, r1) + 0.5) / size
        fx = np.interp((u - a[0, 2]) / a[0, 0], xs, np.arange(len(xs), dtype=np.float64))
        fy = np.interp((v - a[1, 2]) / a[1, 1], ys, np.arange(len(ys), dtype=np.float64))
        x0 = np.minimum(fx.astype(np.int64), len(xs) - 2)
        y0 = np.minimum(fy.astype(np.int64), len(ys) - 2)
        tx, ty = (fx - x0).astype(np.float32), (fy - y0).astype(np.float32)[:, None]
        along = {layer: g[:, x0] * (1 - tx) + g[:, x0 + 1] * tx for layer, g in planes.items()}
        for start in range(0, r1 - r0, 4 * ROWS):
            rows = slice(start, min(start + 4 * ROWS, r1 - r0))
            out = {layer: h[y0[rows]] * (1 - ty[rows]) + h[y0[rows] + 1] * ty[rows] for layer, h in along.items()}
            yield slice(r0 + rows.start, r0 + rows.stop), slice(c0, c1), out
        return
    inv = np.linalg.inv(a[:, :2])
    for start in range(r0, r1, ROWS):
        stop = min(start + ROWS, r1)
        v = (np.arange(start, stop) + 0.5) / size
        uu, vv = np.meshgrid(u, v)
        p = np.stack([uu - a[0, 2], vv - a[1, 2]], axis=-1) @ inv.T
        fx = np.interp(p[..., 0], xs, np.arange(len(xs), dtype=np.float64))
        fy = np.interp(p[..., 1], ys, np.arange(len(ys), dtype=np.float64))
        x0 = np.minimum(fx.astype(np.int64), len(xs) - 2)
        y0 = np.minimum(fy.astype(np.int64), len(ys) - 2)
        tx, ty = (fx - x0).astype(np.float32), (fy - y0).astype(np.float32)
        out = {}
        for layer, g in planes.items():
            top = g[y0, x0] * (1 - tx) + g[y0, x0 + 1] * tx
            bottom = g[y0 + 1, x0] * (1 - tx) + g[y0 + 1, x0 + 1] * tx
            out[layer] = top * (1 - ty) + bottom * ty
        yield slice(start, stop), slice(c0, c1), out


def _write_images(obj, given):
    table = landscape_layers.table(obj)
    missing = sorted(layer for layer in given if layer not in table)
    if missing:
        weightmaps.ensure(_set_meshes(obj), missing)        # as the Landscape panel adds a layer
        table = landscape_layers.table(obj)
    maps = weightmaps.images(obj)
    size = next(iter(maps.values())).size[0]
    pixels = {name: weightmaps._pixels(image).reshape(size, size, 4) for name, image in maps.items()}
    for rows, cols, values in _texels(obj, given, size):
        for layer, v in values.items():
            w, c = table[layer]
            if w in pixels:
                pixels[w][rows, cols, c] = v
        _normalise([(pixels[w][rows, cols], c) for w, c in table.values() if w in pixels])
    for name, px in pixels.items():
        maps[name].pixels.foreach_set(px.ravel())
        maps[name].update()


# ------------------------------------------------------------------ refresh

def terrain_changed(obj):
    """The mesh's geometry or weights changed: what depends on them is refreshed after DELAY s of quiet (at most
    MAX_WAIT s apart). Never raises."""
    try:
        STATE["pending"].add(obj.name)
        now = time.monotonic()
        if bpy.app.timers.is_registered(_tick):
            if now - STATE["first"] >= MAX_WAIT:
                return
            bpy.app.timers.unregister(_tick)
        else:
            STATE["first"] = now
        bpy.app.timers.register(_tick, first_interval=DELAY)
    except ReferenceError:
        pass
    except Exception as e:      # an external caller's call: never raise into it
        Log.warn("Terrain refresh not scheduled: %s" % e)


def _tick():
    flush()
    return None


def flush():
    """The pending refreshes run now (terrain_changed's timer runs this)."""
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    names, STATE["pending"] = STATE["pending"], set()
    done = []
    for name in sorted(names):
        obj = bpy.data.objects.get(name)
        if obj is None or obj.type != 'MESH':
            continue
        try:
            _refresh(obj)
            done.append(obj)
        except ReferenceError:
            pass
        except Exception as e:
            import traceback
            Log.error("Terrain refresh of %s failed: %s\n%s" % (name, e, traceback.format_exc()))
    if done:
        try:
            from . import scatter_biomes
            scatter_biomes.refresh()
        except Exception as e:
            Log.warn("Biome maps not refreshed: %s" % e)
        _editor()
    return len(done)


def _refresh(obj):
    from . import scatter
    if weightmaps.images(obj):
        _reproject(obj)
    for layer, record in scatter._records(obj).items():
        weight = record.get("weight")
        if weight and weight.get("layer") and scatter._modifier(obj, layer) is not None:
            try:        # the layer's channel read again (a new layer, a material put on since)
                scatter.set_weight(obj, layer, weight)
            except (KeyError, ValueError) as e:
                Log.warn("Scatter layer %s on %s: %s" % (record.get("label", layer), obj.name, e))
    _grass(obj)
    for image in weightmaps.images(obj).values():       # undo and saving keep a weightmap's packed file
        if image.is_dirty:
            image.pack()
    obj.update_tag(refresh={'OBJECT', 'DATA'})


def _grass(obj):
    """Grass masks (mp_grass_*) evaluated again from the new weights and positions (needs the app)."""
    from . import grass_masks
    names = [a.name[len(grass_masks.PREFIX):] for a in obj.data.attributes if a.name.startswith(grass_masks.PREFIX)]
    if not names or _material(obj)[0] is None:
        return
    try:
        grass_masks.write(obj, names)
    except Exception as e:
        Log.warn("Grass masks of %s not refreshed: %s" % (obj.name, e))


def _editor():
    """The Fortnite editor's Landscape tab reads its groups and stores again (when the editor is loaded)."""
    root = __name__.rsplit(".", 2)[0]
    for module, call in (("land_tab", lambda m: m.forget()), ("land_scatter", lambda m: m.invalidate(reload=True))):
        m = sys.modules.get(root + ".operator.fortnite_editor." + module)
        if m is not None:
            try:
                call(m)
            except Exception as e:
                Log.warn("Landscape tab not refreshed: %s" % e)


# ------------------------------------------------------------------ custom terrain

def adopt(obj):
    """The mesh shown as a custom landscape in the Landscape tab (idempotent)."""
    if obj.type != 'MESH':
        raise TypeError("%s is not a mesh" % obj.name)
    if not obj.get("mp_landscape"):
        obj["mp_landscape"] = 1.0
        obj[ADOPTED] = 1.0
    if weightmaps.images(obj):
        _affine(obj)
    _editor()


def unadopt(obj):
    """The mesh no longer listed as a custom landscape, when adopt() listed it (a landscape material put on it keeps it
    listed). Its weights and channels stay."""
    try:
        if obj.get(ADOPTED):
            del obj[ADOPTED]
            if obj.get("mp_landscape") and not obj.get(weightmaps.SET):
                del obj["mp_landscape"]
            _editor()
    except ReferenceError:
        pass


def on_resize(obj, old_n, new_n):
    """The mesh was rebuilt with another vertex count. The fork can't rebuild per-vertex data it doesn't own the
    source of (painted masks, packed layers), so the caller resamples them: always False. Its UV-derived data is laid
    again by the refresh this schedules."""
    terrain_changed(obj)
    return False


# ------------------------------------------------------------------ registration

def register():
    bpy.app.driver_namespace[NAME] = sys.modules[__name__]


def unregister():
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    STATE["pending"].clear()
    _CACHE.clear()
    if bpy.app.driver_namespace.get(NAME) is sys.modules[__name__]:
        del bpy.app.driver_namespace[NAME]
