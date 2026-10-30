"""The Landscape tab's Scatter mode: the shown group's scatter layers (material_porter/scatter.py: a Geometry Nodes
modifier per layer and piece) listed by grass type, then custom ones, then the map's placed foliage (points objects,
instances.py). The well shows the picked layer's weight (its paint mask x its source) and the brush paints the mask
(scatter_ops), or erases placed instances; the info column shows the layer's settings. A layer over a map's pieces is a
modifier per piece sharing a label: listed, shown, counted and edited as one (the first piece's values, copied to the
others by tick)."""
import json
import time

import bpy
import gpu
import numpy as np
from bpy.app.handlers import persistent
from gpu_extras.batch import batch_for_shader

from . import land_store
from .ui import paint
from .ui.rect import Rect

COUNT_EVERY = 0.3       # s between instance counts while the scene changes
TYPES_RETRY = 10.0      # s before the grass type list is asked again after a failure
MAX_DOTS = 60000        # placed instances drawn in the well at most (every n-th past that)
PLACED = "mp_placed"    # a points object's: what instances.py placed
GRASS = "mp_grass"      # a map landscape piece's MPGrass (JSON): its material's grass outputs and their types
WEIGHTED = ("Weight Source", "Source Attribute", "Source Channel", "Source Image")     # per piece: never copied

# store: the mode's land_store.MaskStore (base: the paint store it was made from); selected: the picked entry's key;
# rows: entries() cached; counts: {key: instances}; types: the game's grass types [{name, path, varieties}];
# loaded: what the store's layer was read from; synced: the first piece's values last copied; dots: placed positions;
# pending: {type name: meshes still importing}; before_hide: (group, keys showing when Hide All was pressed)
SCATTER = {"store": None, "base": None, "selected": None, "rows": None, "counts": {}, "stale": True, "counted": 0.0,
           "types": None, "types_asked": 0.0, "open": {}, "section": None, "synced": None, "dots": None,
           "pending": {}, "loaded": None, "before_hide": None}


def core():
    from ...material_porter import scatter
    return scatter


def is_on(context=None):
    return getattr((context or bpy.context).window_manager, "fp_land_mode", 'PAINT') == 'SCATTER'


def shown_group(context=None):
    from .land_tab import wanted_group
    return wanted_group(context or bpy.context)


def short(path):
    return (path or "").rsplit("/", 1)[-1].split(".")[0]


def layer_key(label, type_=""):
    """A layer's list key: game labels are unique within their grass type only."""
    return "L:" + label + ("@" + type_ if type_ else "")


# ------------------------------------------------------------------ the group's layers

def _tags(obj):
    """{layer id: modifier} of a piece's scatter modifiers."""
    sc = core()
    out = {}
    for m in obj.modifiers:
        if m.type != 'NODES' or m.node_group is None or not m.node_group.name.startswith(sc.GROUP):
            continue
        try:
            tag = sc._get(m, "Tag")
        except StopIteration:
            continue
        if tag and tag.startswith(sc.TAG):
            out[tag[len(sc.TAG):]] = m
    return out


def _xy_box(obj):
    """(min xy, max xy) of obj's world bounding box."""
    m = np.array(obj.matrix_world, np.float64)
    corners = np.array(obj.bound_box, np.float64) @ m[:3, :3].T + m[:3, 3]
    return corners[:, :2].min(axis=0), corners[:, :2].max(axis=0)


def placed_of(pieces, scene):
    """The placed foliage over the pieces: points objects whose world XY bounds meet the pieces'. A map's cells import
    into their own collections, apart from the terrain's level, so the collections don't tell."""
    if not pieces:
        return []
    boxes = [_xy_box(p) for p in pieces]
    lo, hi = np.min([b[0] for b in boxes], axis=0), np.max([b[1] for b in boxes], axis=0)
    out = []
    for o in scene.objects:
        if o.type != 'MESH' or o.get(PLACED) is None:
            continue
        a, b = _xy_box(o)
        if (a <= hi).all() and (b >= lo).all():
            out.append(o)
    return out


def entries(pieces, scene):
    """The group's layers, game ones grouped by type, then custom ones, then placed foliage: [{key, kind (game, custom,
    placed), label, type, variety, weight, members [(piece name, layer id, modifier name)], hidden, object (placed)}]."""
    sc = core()
    found, order = {}, []
    for obj in pieces:
        mods = _tags(obj)
        for layer, record in json.loads(obj.get(sc.KEY) or "{}").items():
            mod = mods.get(layer)
            if mod is None:     # its modifier removed by hand
                continue
            key = layer_key(record.get("label", layer), record.get("type") or "")
            e = found.get(key)
            if e is None:
                e = found[key] = {"key": key, "kind": "game" if record.get("source") == "game" else "custom",
                                  "label": record.get("label", layer), "type": record.get("type") or "",
                                  "variety": record.get("variety", 0), "weight": record.get("weight"), "members": [],
                                  "hidden": True}
                order.append(key)
            e["members"].append((obj.name, layer, mod.name))
            e["hidden"] = e["hidden"] and not mod.show_viewport
    game = [found[k] for k in order if found[k]["kind"] == "game"]
    types = list(dict.fromkeys(e["type"] for e in game))
    game.sort(key=lambda e: (types.index(e["type"]), e["variety"]))
    out = game + [found[k] for k in order if found[k]["kind"] == "custom"]
    for o in sorted(placed_of(pieces, scene), key=lambda o: o.name):
        info = o[PLACED]
        info = info.to_dict() if hasattr(info, "to_dict") else dict(info)
        out.append({"key": "P:" + o.name, "kind": "placed", "label": short(info.get("mesh")) or o.name, "type": "",
                    "members": [], "hidden": o.hide_viewport, "object": o.name, "info": info})
    return out


def rows(context=None):
    """entries() of the shown group, kept until a piece or placed object changes."""
    context = context or bpy.context
    name, pieces = shown_group(context)
    if SCATTER["rows"] is None or SCATTER["rows"][0] != name:
        SCATTER["rows"] = (name, entries(pieces, context.scene) if pieces else [])
    return SCATTER["rows"][1]


def watched():
    """Object names whose change changes the list (pieces and placed objects)."""
    found = SCATTER["rows"]
    if found is None:
        return set()
    name, pieces = shown_group()
    return {o.name for o in pieces} | {e["object"] for e in found[1] if e["kind"] == "placed"}


def selected(context=None):
    key = SCATTER["selected"]
    return next((e for e in rows(context) if e["key"] == key), None)


def modifiers(e):
    """[(piece, modifier)] of a layer entry, first piece first."""
    out = []
    for name, _layer, mod in e["members"]:
        obj = bpy.data.objects.get(name)
        m = obj.modifiers.get(mod) if obj is not None else None
        if m is not None:
            out.append((obj, m))
    return out


def invalidate(reload=False):
    """The list read again and counted again; `reload`: the picked layer's mask and source too (not during a stroke:
    its last dabs aren't written yet)."""
    SCATTER["rows"] = None
    SCATTER["stale"] = True
    if reload:
        SCATTER["loaded"] = None


def grass_payload(pieces):
    """The group's MPGrass ({"material", "outputs": [{"name", "type": [varieties], "input"}]}): a map piece's, else the
    app's for its material (land_grass_list); None without."""
    for p in pieces:
        if p.get(GRASS):
            try:
                return json.loads(p[GRASS])
            except (TypeError, ValueError):
                return None
    from . import land_grass_list
    return land_grass_list.answered(pieces)


def output_for(pieces, type_path):
    """The name of the group's grass output scattering this grass type, None when it has none."""
    found = grass_payload(pieces)
    want = (type_path or "").split(".")[0].lower()
    for out in (found or {}).get("outputs") or []:
        if any((v.get("type") or "").split(".")[0].lower() == want for v in out.get("type") or []):
            return out.get("name")
    return None


# ------------------------------------------------------------------ weights per vertex (the picture's)

def _first_corner(me):
    corners = np.empty(len(me.loops), np.int32)
    me.loops.foreach_get("vertex_index", corners)
    first = np.full(len(me.vertices), -1, np.int64)
    first[corners[::-1]] = np.arange(len(corners))[::-1]
    return first


def source_values(obj, weight):
    """A layer's source weight per vertex of obj as its modifier reads it (None: no source, 1)."""
    sc = core()
    if not weight:
        return None
    me = obj.data
    n = len(me.vertices)
    zeros = np.zeros(n, np.float32)
    try:
        inputs = sc._source(obj, weight)
    except (ValueError, KeyError):
        return zeros
    kind = inputs.get("Weight Source")
    if kind == "Attribute":     # a missing attribute reads 0 in the modifier too
        a = me.attributes.get(inputs["Source Attribute"])
        if a is not None and a.domain == 'POINT' and a.data_type == 'FLOAT':
            a.data.foreach_get("value", zeros)
        return zeros
    first = _first_corner(me)
    channel = int(inputs.get("Source Channel", 0))
    if kind == "Layer":
        a = me.color_attributes.get(inputs["Source Attribute"])
        if a is None:
            return zeros
        v = np.empty(len(a.data) * 4, np.float32)
        a.data.foreach_get("color", v)
        v = v.reshape(-1, 4)[:, channel]
        return v if a.domain == 'POINT' else np.where(first >= 0, v[np.maximum(first, 0)], 0.0).astype(np.float32)
    if kind == "Image":
        from ...material_porter import weightmaps
        image, uv = inputs["Source Image"], me.uv_layers.get(weightmaps.UV)
        if image is None or uv is None or not len(me.loops):
            return zeros
        uvs = np.empty(len(me.loops) * 2, np.float32)
        uv.data.foreach_get("uv", uvs)
        uvs = uvs.reshape(-1, 2)[np.maximum(first, 0)]
        w, h = image.size
        px = np.empty(w * h * 4, np.float32)
        image.pixels.foreach_get(px)
        px = px.reshape(h, w, 4)
        x = np.clip((uvs[:, 0] * w).astype(np.int64), 0, w - 1)
        y = np.clip((uvs[:, 1] * h).astype(np.int64), 0, h - 1)
        return np.where(first >= 0, px[y, x, channel], 0.0).astype(np.float32)
    return None


def describe(obj, weight):
    """A weight form in words."""
    if not weight:
        return "None"
    if weight.get("attribute"):
        name, grass = weight["attribute"], core().GRASS
        if name.startswith(grass):
            name = name[len(grass):]
        elif name.startswith("mp_"):
            return name
        return name + " mask"
    if weight.get("layer"):
        return "Layer " + weight["layer"]
    if weight.get("image"):
        return "%s %s" % (weight["image"], "RGBA"[int(weight.get("channel", 0))])
    return "?"


# ------------------------------------------------------------------ the store and the timer

def _load_layer():
    """The picked entry read into the store (its mask, its source), the picture redrawn."""
    from .land_tab import LAND
    store = SCATTER["store"]
    if store is None:
        return
    e = selected()
    if e is None or e["kind"] == "placed":
        store.set_layer(None, None)
    else:
        mask = core().MASK
        store.set_layer(e["label"], {name: mask + layer for name, layer, _ in e["members"]},
                        lambda obj, w=e["weight"]: source_values(obj, w))
    SCATTER["loaded"] = (e["key"], json.dumps(e["weight"])) if e is not None and e["kind"] != "placed" else \
        (e["key"] if e else None, None)
    SCATTER["dots"] = None
    LAND["dirty"] = True


def pick(key):
    SCATTER["selected"] = key
    SCATTER["synced"] = None
    _load_layer()


def tick():
    """The mode's store made and shown (and the paint store back in Paint mode), the picked layer read again when it
    changed, the other pieces following the first's settings, counts and the game's grass types fetched."""
    from . import land_tab
    from .land_tab import LAND
    context = bpy.context
    store = SCATTER["store"]
    if not is_on(context):
        if store is not None and LAND["store"] is store:
            biomes, wm = LAND["biomes"], context.window_manager
            land_tab.activate(biomes if biomes is not None and wm.fp_land_layer in biomes.grid.layers else LAND["layers"])
            land_tab.redraw_all()
        SCATTER["rows"] = None      # nothing listed: the scene's changes go unwatched until Scatter mode is back
        return
    base = LAND["layers"]
    if base is None:
        return
    if store is None or SCATTER["base"] is not base:
        name, pieces = shown_group(context)
        try:
            store = land_store.MaskStore(pieces, base)
        except (ValueError, KeyError, IndexError, MemoryError, ReferenceError) as e:
            LAND["message"] = "Couldn't read this landscape: %s" % (e or type(e).__name__)
            return
        g = store.grid
        from . import land_view
        store.shade = base.shade if store.grid.shape == base.grid.shape and isinstance(base, land_store.AttributeStore) \
            else land_view.hillshade(g.height, g.cell, g.filled, land_view.source(g))
        store.step = max(1, int(np.ceil(max(g.shape) / land_tab.MAX_PICTURE)))
        SCATTER.update(store=store, base=base, rows=None, stale=True, loaded=None)
    found = rows(context)
    if SCATTER["selected"] is None or selected(context) is None:
        SCATTER["selected"] = next((e["key"] for e in found if e["kind"] != "placed"), found[0]["key"] if found else None)
    e = selected(context)
    want = (e["key"], json.dumps(e["weight"]) if e["kind"] != "placed" else None) if e else (None, None)
    if SCATTER["loaded"] != want:
        _load_layer()
    if LAND["store"] is not store:
        land_tab.activate(store)
        land_tab.redraw_all()
    _sync(e)
    if SCATTER["stale"] and time.time() - SCATTER["counted"] >= COUNT_EVERY:
        SCATTER["counts"] = count_all(found)
        SCATTER.update(stale=False, counted=time.time())
        land_tab.redraw_all()
    _ask_types()
    from . import land_grass_list
    land_grass_list.ask(shown_group(context)[1])


def _sync(e):
    """The other pieces' modifiers take the first's settings (the info column edits the first), weights aside."""
    if e is None or len(e["members"]) < 2:
        return
    sc = core()
    mods = modifiers(e)
    if len(mods) < 2:
        return
    values = {k: v for k, v in sc._values(mods[0][1]).items() if k not in WEIGHTED + ("Mask", "Tag")}
    if values == SCATTER["synced"]:
        return
    SCATTER["synced"] = values
    changed = []
    for obj, mod in mods[1:]:
        for k, v in values.items():
            if sc._get(mod, k) != v:
                sc._set(mod, k, v)
                changed.append(obj)
    if changed:
        from .land_tab import refresh
        refresh(changed)


def count_all(found):
    """{key: instances shown} of the entries (a layer's over its pieces; placed: the points)."""
    sc = core()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    per_piece = {}
    for name in {n for e in found for n, _, _ in e["members"]}:
        obj = bpy.data.objects.get(name)
        if obj is None:
            continue
        geometry = bpy.types.GeometrySet.from_evaluated_object(obj.evaluated_get(depsgraph))     # owns the cloud
        cloud = geometry.instances_pointcloud()
        tags = {}
        if cloud is not None:
            for a in cloud.attributes:
                if a.name.startswith(sc.TAG):
                    marks = np.zeros(len(a.data), bool)
                    a.data.foreach_get("value", marks)
                    tags[a.name[len(sc.TAG):]] = int(marks.sum())
        per_piece[name] = tags
    out = {}
    for e in found:
        if e["kind"] == "placed":
            o = bpy.data.objects.get(e["object"])
            out[e["key"]] = len(o.data.vertices) if o is not None else 0
        else:
            out[e["key"]] = sum(per_piece.get(n, {}).get(layer, 0) for n, layer, _ in e["members"])
    return out


def _ask_types():
    """The game's grass types (the Add Grass Type search), asked once the app is up."""
    if SCATTER["types"] is not None or time.time() - SCATTER["types_asked"] < TYPES_RETRY:
        return
    from .state import client
    e = client()
    if e.state != "ready":
        return
    SCATTER["types_asked"] = time.time()

    def done(result):
        if isinstance(result, Exception) or not isinstance(result, list):
            return False
        SCATTER["types"] = result
    e.request("foliage-grass-types", done, query="")


# ------------------------------------------------------------------ placed foliage

def keep_points(me, keep):
    """A points mesh down to the vertices where `keep` (every point attribute kept with them)."""
    kinds = {'FLOAT': (1, np.float32, "value"), 'INT': (1, np.int32, "value"), 'BOOLEAN': (1, bool, "value"),
             'INT8': (1, np.int32, "value"), 'FLOAT_VECTOR': (3, np.float32, "vector"),
             'FLOAT_COLOR': (4, np.float32, "color"), 'BYTE_COLOR': (4, np.float32, "color"),
             'FLOAT2': (2, np.float32, "vector"), 'INT32_2D': (2, np.int32, "value"),
             'QUATERNION': (4, np.float32, "value"), 'FLOAT4X4': (16, np.float32, "value")}
    n = len(me.vertices)
    saved = []
    for a in me.attributes:
        if a.domain != 'POINT' or a.name == "position" or a.data_type not in kinds:
            continue
        width, dtype, field = kinds[a.data_type]
        v = np.empty(n * width, dtype)
        a.data.foreach_get(field, v)
        saved.append((a.name, a.data_type, field, v.reshape(n, width)[keep].ravel()))
    co = np.empty(n * 3, np.float32)
    me.vertices.foreach_get("co", co)
    co = co.reshape(n, 3)[keep]
    me.clear_geometry()
    me.vertices.add(len(co))
    me.vertices.foreach_set("co", co.ravel())
    for name, kind, field, v in saved:
        a = me.attributes.get(name) or me.attributes.new(name, kind, 'POINT')
        a.data.foreach_set(field, v)
    me.update()


def dots(e):
    """(object, world XY of its points) of a placed entry, kept until its point count changes."""
    obj = bpy.data.objects.get(e["object"]) if e else None
    if obj is None:
        return None, None
    n = len(obj.data.vertices)
    cached = SCATTER["dots"]
    if cached is None or cached[0] != obj.name or len(cached[1]) != n:
        co = np.empty(n * 3, np.float32)
        obj.data.vertices.foreach_get("co", co)
        m = np.array(obj.matrix_world, np.float64)
        SCATTER["dots"] = cached = (obj.name, (co.reshape(-1, 3) @ m[:3, :3].T + m[:3, 3])[:, :2])
    return obj, cached[1]


def draw_over(frame):
    """The picked placed foliage's instances as dots over the picture."""
    e = selected()
    if e is None or e["kind"] != "placed" or e["hidden"]:
        return
    _obj, xy = dots(e)
    if xy is None or not len(xy):
        return
    grid = SCATTER["store"].grid
    if len(xy) > MAX_DOTS:
        xy = xy[:: int(np.ceil(len(xy) / MAX_DOTS))]
    col = (xy[:, 0] - grid.origin[0]) / grid.cell + 0.5
    row = (xy[:, 1] - grid.origin[1]) / grid.cell + 0.5
    pts = np.stack([frame.ox + col * frame.scale, frame.oy + row * frame.scale], axis=1).astype(np.float32)
    from .grid_draw import _shader
    shader = _shader('UNIFORM_COLOR')
    shader.uniform_float("color", (0.55, 0.85, 0.45, 0.9))
    gpu.state.point_size_set(3.0)
    batch_for_shader(shader, 'POINTS', {"pos": pts}).draw(shader)
    gpu.state.point_size_set(1.0)


# ------------------------------------------------------------------ the list column

def _run(op, **props):
    def run(context):
        getattr(bpy.ops.fp, op)(**props)
    return run


def _fold(key):
    def run(context):
        SCATTER["open"][key] = not SCATTER["open"].get(key, True)
    return run


def list_rows(context=None, compact=False):
    """The list column's rows: (kind, payload). `compact` (icons only: no titles to tell groups apart): all open."""
    out = []
    for type_name, left in SCATTER["pending"].items():
        out.append(("note", "Importing %s (%d mesh%s)" % (type_name, left, "" if left == 1 else "es")))
    found = rows(context)
    game = [e for e in found if e["kind"] == "game"]
    if game:
        out.append(("title", "Game Grass"))
        for t in dict.fromkeys(e["type"] for e in game):
            members = [e for e in game if e["type"] == t]
            if compact or SCATTER["open"].get("type:" + t, True):
                out.append(("group", ("type:" + t, short(t) or "Grass", None)))
                bare = [_words(e["label"], False) for e in members]
                # variant letters kept where the type's names read the same without them (Gravel_A_A, Gravel_A_B)
                out += [("layer", (e, 1, bare.count(b) > 1)) for e, b in zip(members, bare)]
            else:       # folded: the type's shown instances
                counts = [0 if e["hidden"] else SCATTER["counts"].get(e["key"]) for e in members]
                total = None if None in counts else sum(counts)
                out.append(("group", ("type:" + t, short(t) or "Grass", amount(total))))
    for kind, title in (("custom", "Custom"), ("placed", "Placed Foliage")):
        members = [e for e in found if e["kind"] == kind]
        if members:
            out.append(("title", title))
            out += [("layer", (e, 0, False)) for e in members]
    return out


def side(ui, r):
    t = ui.t
    items = list_rows(compact=ui.compact)
    if not any(kind in ("layer", "group") for kind, _ in items):       # folded groups aren't empty
        _, pieces = shown_group()
        text = ("Nothing scattered here yet. Add Game Grass scatters this landscape's own grass; Add Grass Type or "
                "Add from Object any other (right column)" if grass_payload(pieces) else
                "Nothing scattered here yet: add a grass type or an object to scatter (right column)")
        y = r.top - t.row_pitch
        for kind, payload in items:
            paint.text_in(payload, Rect(r.x, y, r.w, t.row_pitch).pad(t.pad, 0), t.text3, t.text)
            y -= t.row_pitch
        for line in paint.wrap(text, t.text, r.w - 2 * t.pad, 6):
            paint.text_in(line, Rect(r.x + t.pad, y, r.w - 2 * t.pad, t.row_pitch), t.text3, t.text)
            y -= t.row_pitch - 6 * t.s
        return
    offset = ui.scroll("scatter:layers", r, len(items) * t.row_pitch)
    y = r.top + offset
    for kind, payload in items:
        y -= t.row_pitch
        if y > r.top or y + t.row_pitch < r.y:
            continue
        cell = Rect(r.x, y + (t.row_pitch - t.row) / 2, r.w - ui.bar_room, t.row)
        if kind == "title":
            paint.text_in(payload, cell.pad(t.pad, 0), t.text3, t.text)
        elif kind == "note":
            paint.text_in(payload, cell.pad(t.pad, 0), t.text3, t.caption)
        elif kind == "group":
            from . import land_select
            key, title, total = payload
            ui.group("scatter:g:" + key, cell, title, SCATTER["open"].get(key, True), _fold(key),
                     select=land_select.group_action(key), tip="Pick its layers (Ctrl: add them)")
            if total is not None:       # right-aligned with the layer rows' counts
                inner = cell.copy()
                inner.cut_right(cell.h)
                inner = inner.pad(t.pad, 0, t.gap, 0)
                paint.text_in(total, inner.cut_right(paint.measure(total, t.caption) + t.pad), t.text3, t.caption, "right")
        else:
            _layer(ui, cell, *payload)
    ui.end_scroll()


def _words(label, letters):
    """(words, copy number) of a label as fit_name reads it: no SM_ prefix, variant letters only with `letters`."""
    head, _, copy = label.rpartition(" ")
    if not (head and copy.isdigit()):
        head, copy = label, ""
    words = [w for w in head.split("_") if w and w != "SM" and (letters or not (len(w) == 1 and w.isalpha()))] or [head]
    return tuple(words), copy


def fit_name(label, size, width, letters=False):
    """A mesh name's words from its end, as many as fit (SM_Hermes_Grass_LeafyGrass_A_A: LeafyGrass): names of one
    family differ at their ends; prefixes tell nothing, nor do variant letters unless `letters` (siblings differ there)."""
    words, copy = _words(label, letters)
    tail = (" " + copy) if copy else ""
    # at least from the last word that isn't a letter: Gravel_A_B, not B
    least = len(words) - max((i for i, w in enumerate(words) if not (len(w) == 1 and w.isalpha())), default=len(words) - 1)
    best = "_".join(words[-least:])
    for k in range(least + 1, len(words) + 1):
        text = "_".join(words[-k:])
        if paint.measure(text + tail, size) > width:
            break
        best = text
    return paint.fit(best + tail, size, width)


def amount(n):
    if n is None:
        return "…"
    return "%d" % n if n < 1000 else ("%.1fk" % (n / 1000) if n < 100000 else "%dk" % (n // 1000))


def _layer(ui, r, e, depth, letters=False):
    """A layer's row: picks it; its instance count; its eye."""
    from . import land_select
    t = ui.t
    key = e["key"]
    lead = key == SCATTER["selected"]
    on = lead or key in land_select.keys()
    rid = "scatter:layer:" + key
    tip = "%s: %s, variety %d" % (e["label"], short(e["type"]), e["variety"] + 1) if e["kind"] == "game" else \
        "%s: %s" % (e["label"], "placed foliage " + e["object"] if e["kind"] == "placed" else "custom")
    if on and not lead:     # picked with the lead: the selection's fill, quieter (the brush paints the lead)
        paint.rect(r, t.selected[:3] + (t.selected[3] * 0.5,), t.r_small)
    if ui.compact:      # icons only: its initial centred, no count or eye
        ui.row(rid, r, fit_name(e["label"], t.text, r.w, letters), None, lead, land_select.pick_action(key), tip=tip)
        return
    ui.row(rid, r, "", None, lead, land_select.pick_action(key), tip=tip)
    inner = r.copy()
    eye = inner.cut_right(r.h)
    inner = inner.pad(t.pad + depth * t.big, 0, t.gap, 0)
    hidden = e["hidden"]
    if not hidden:
        text = amount(SCATTER["counts"].get(key))
        paint.text_in(text, inner.cut_right(paint.measure(text, t.caption) + t.pad), t.text3, t.caption, "right")
    color = t.text3 if hidden else (t.text1 if on or ui.hot(rid) else t.text2)
    paint.text_in(fit_name(e["label"], t.text, inner.w, letters), inner, color, t.text)
    ui.button("scatter:show:" + key, eye, icon="mdi_EyeOff" if hidden else "mdi_Eye", action=_run("scatter_show", key=key),
              style="flat", tip="Show again" if hidden else "Hide in the viewport and renders")


def _run_invoke(op):
    def run(context):
        getattr(bpy.ops.fp, op)('INVOKE_DEFAULT')
    return run


def _biomes(obj, e):
    """[(index, name)] a layer's instances can be set to: on a mesh painting no biome map of its own (its painted maps
    decide there), the biomes of the instance's material's mask."""
    from ...material_porter import biome_maps
    if obj.get(biome_maps.KEY):
        return []
    return core().biomes(obj, e["members"][0][1])


# ------------------------------------------------------------------ the biome pick (land_popups.py: the pop-ups)

class FP_OT_scatter_biome(bpy.types.Operator):
    """Take the grass colour from this biome"""
    bl_idname = "fp.scatter_biome"
    bl_label = "Scatter Biome"
    bl_options = {'INTERNAL', 'UNDO'}
    index: bpy.props.IntProperty()

    def execute(self, context):
        from . import land_select
        found = land_select.layers(context)
        if not found:
            return {'CANCELLED'}
        for name, layer, _mod in (m for e in found for m in e["members"]):
            obj = bpy.data.objects.get(name)
            if obj is not None:
                core().set_biome(obj, layer, self.index)
        SCATTER["synced"] = None
        invalidate()
        from .land_tab import refresh
        refresh()
        return {'FINISHED'}


def header(layout, context, narrow):
    """Scatter mode's header tools after the brush: the list's and the info column's essentials when they fold."""
    from . import tabs
    from .ui import blender as ui_blender
    found = rows(context)
    if found:
        hide = any(not e["hidden"] for e in found)
        layout.operator("fp.scatter_show_all", text="" if narrow else ("Hide All" if hide else "Show All"),
                        icon='HIDE_OFF' if hide else 'HIDE_ON')
    folded = ui_blender.folded(context, tabs.current(context.space_data))
    if "side" in folded:
        from . import land_select
        picked = land_select.entries(context)
        text = land_select.title(picked) if len(picked) > 1 else (picked[0]["label"] if picked else "Layer")
        layout.operator("fp.popup_open", text=text, icon='OUTLINER_OB_POINTCLOUD').name = "scatter_layers"
    if "info" in folded:
        layout.operator("fp.scatter_add_type", text="" if narrow else "Add Grass Type…", icon='ADD')
        layout.operator("fp.popup_open", text="" if narrow else "Material Grass…", icon='MATERIAL').name = "scatter_grass"


# ------------------------------------------------------------------ the scene changes elsewhere

@persistent
def _scene_changed(scene, depsgraph):
    if SCATTER["rows"] is None:
        return
    if not (depsgraph.id_type_updated('OBJECT') or depsgraph.id_type_updated('MESH')):
        return
    names = watched()
    for u in depsgraph.updates:
        obj = u.id.original if isinstance(u.id, bpy.types.Object) else None
        if obj is not None and obj.name in names:
            invalidate()
            from .land_tab import redraw_all
            redraw_all()
            return


@persistent
def _reset(*_):
    """An undo or another file: objects are new, the store's gone with the paint store."""
    SCATTER.update(store=None, base=None, rows=None, counts={}, stale=True, synced=None, dots=None, loaded=None,
                   before_hide=None)


HANDLERS = (("depsgraph_update_post", _scene_changed), ("undo_post", _reset), ("redo_post", _reset),
            ("load_post", _reset))
classes = (FP_OT_scatter_biome,)


def register():
    from . import land_popups
    for c in classes:
        bpy.utils.register_class(c)
    land_popups.register()
    for name, fn in HANDLERS:
        getattr(bpy.app.handlers, name).append(fn)


def unregister():
    for name, fn in HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if fn in handlers:
            handlers.remove(fn)
    from . import land_popups
    land_popups.unregister()
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
    _reset()
    SCATTER.update(selected=None, types=None, types_asked=0.0, pending={})
