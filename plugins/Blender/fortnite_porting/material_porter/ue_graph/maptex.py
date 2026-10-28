"""Map textures: texture parameters a landscape material reads across the whole map through a world projection (Hera's
BiomeTexture, Athena's BiomeColormap and PaintLayerMasks...).

On a mesh a landscape material was put on (entry["landscape_world"]) the projection lands wherever the mesh is on the
game's map. Each such sample is found by translating its coordinates with the world pinned to a few points (env.probe:
the translator folds what's constant): coordinates that stay in [0, 1] at the map's far corners cover a rectangle of
the texture (a quad of Hera's four); unclamped ones spanning over a kilometre cover it all. The rectangles are recorded
(env.map_textures); where the mesh has a painted map of its own (entry["biome_maps"]: parameter -> image) the sample
reads that image across the mesh instead, over the same rectangle, through the weightmaps' UV map."""
import re
import zlib

import bpy

from .core import Val

FAR = 1e9           # cm: past any map's edge
KM = 1e5            # cm


def probe(tr, g, ref, scope):
    """How a sample's coordinates follow the world: ("rect", (u0, v0, u1, v1), per_km) when clamped at the map's edges
    (UE's UV space; per_km: the texture's UVs a kilometre spans), ("corner", (u, v), per_km) when they run on unclamped
    from (u, v) at the world's origin, spanning over a kilometre (a quad of a map-wide texture: its rectangle is worked
    out from its texture's other corners, finish());
    None when they don't follow the world, aren't known at build time, or tile."""
    env = tr.env
    if ref is None or not hasattr(env, "probe"):
        return None
    points = ((-FAR, -FAR, 0.0), (FAR, FAR, 0.0), (0.0, 0.0, 0.0), (KM, 0.0, 0.0), (0.0, KM, 0.0))
    got, saved = [], env.probe
    try:
        for i, w in enumerate(points):
            env.probe = w
            v = tr.input(g, ref, dict(scope, _id=("map probe", i, scope.get("_id"))), None)
            if v is None or not v.const or not isinstance(v.s, (tuple, list)):
                return None
            got.append(tuple(float(c) for c in v.s[:2]))
    except Exception:
        return None
    finally:
        env.probe = saved
    lo, hi, o, x1, y1 = got
    per_km = max(abs(x1[0] - o[0]), abs(y1[1] - o[1]))      # the texture's UVs a kilometre spans: its scale
    if abs(x1[0] - o[0]) + abs(x1[1] - o[1]) < 1e-9 or abs(y1[0] - o[0]) + abs(y1[1] - o[1]) < 1e-9:
        return None                                     # no world in it
    if all(0.0 <= c <= 1.0 for c in lo + hi) and abs(hi[0] - lo[0]) > 1e-6 and abs(hi[1] - lo[1]) > 1e-6:
        return "rect", (lo[0], lo[1], hi[0], hi[1]), per_km     # clamped at the map's edges: its rectangle
    if abs(x1[0] - o[0]) < 1.0 and abs(y1[1] - o[1]) < 1.0 and all(0.0 <= c < 1.0 for c in o):
        return "corner", o, per_km                      # a kilometre crosses under one tile: map-wide
    return None


def record(env, param, texture, found, g):
    textures = env.__dict__.setdefault("map_textures", {})
    entry = textures.setdefault(param, {"texture": str(texture), "rects": [], "corners": []})
    # the function sampling it (functions.function_graph): where label() looks for its named masks
    source = (getattr(env, "function_paths", None) or {}).get(g.path)
    if source and not entry.get("source"):
        entry["source"] = source
    kind, value, per_km = found
    if per_km > 1e-9:
        entry.setdefault("per_km", round(per_km, 6))
    value = [round(c, 6) for c in value]
    key = "rects" if kind == "rect" else "corners"
    if value not in entry[key]:
        entry[key].append(value)


def finish(textures):
    """Each texture's corners turned into rectangles: from each corner to the next corner along each axis (Hera's
    four quads start at 0 and 0.5), the last to 1."""
    for entry in textures.values():
        corners = entry.pop("corners", [])
        for axis_u, axis_v in corners:
            us = sorted({c[0] for c in corners} | {1.0})
            vs = sorted({c[1] for c in corners} | {1.0})
            u1 = next(u for u in us if u > axis_u)
            v1 = next(v for v in vs if v > axis_v)
            rect = [axis_u, axis_v, round(u1, 6), round(v1, 6)]
            if rect not in entry["rects"]:
                entry["rects"].append(rect)
        # a read over several rectangles (Helios's whole map besides its quads) is painted through them
        rects = entry["rects"]
        entry["rects"] = sorted((r for r in rects if not any(o != r and _inside(o, r) for o in rects)),
                                key=lambda r: (r[1], r[0]))
    return textures


def _inside(a, b):
    return b[0] <= a[0] + 1e-6 and b[1] <= a[1] + 1e-6 and a[2] <= b[2] + 1e-6 and a[3] <= b[3] + 1e-6


QUAD = re.compile(r"quad_?([a-d])$", re.I)       # a mask texture's quarters, A-D in their rectangles' order


def label(textures, graphs, fetch=None):
    """Names for a mask texture's channels, from the game's own function splitting it into named masks (Hera's
    MF_HeraBiomeMasks_Isolated: output "00_Chaparral" is quarter A's red): entry["labels"] {"rect,channel": name}.
    That function is among the material's graphs, or beside the one sampling the texture (Helios's
    MF_TerrainMasksIsolated by MF_TerrainMasks), got through `fetch` (object path -> Graph or None)."""
    graphs = list(graphs)
    for entry in textures.values():
        if fetch is not None and entry.get("source"):
            for path in _isolated(entry["source"]):
                g = fetch(path)
                if g is not None:
                    graphs.insert(0, g)
                    break
    names = {}
    for g in graphs:
        for pair, name in _named(g).items():
            names.setdefault(pair, name)
    for entry in textures.values():
        if len(entry["rects"]) == 4 and names:
            entry["labels"] = {"%d,%d" % ("ABCD".index(q), c): n for (q, c), n in sorted(names.items())}
    return textures


def _isolated(source):
    """Object paths the named-masks function beside `source` may have."""
    folder, _, leaf = source.rpartition("/")
    fname = leaf.split(".")[0]
    joined = "MF_" + fname[3:].replace("_", "") if fname.startswith("MF_") else fname
    names = dict.fromkeys((fname + "Isolated", fname + "_Isolated", joined + "Isolated", joined + "_Isolated"))
    return ["%s/%s.0" % (folder, n) for n in names]


def _named(g):
    """{(quarter, channel): name} one graph names: an output reading one channel names it; then an output reading
    several names the one of them left (Hera's Desert: alpha, less where others are). A graph naming fewer than
    four reads them for something else."""
    quads = {x["Properties"].get("Id"): QUAD.search(str(x["Properties"].get("InputName", ""))).group(1).upper()
             for x in g.inputs.values() if QUAD.search(str(x["Properties"].get("InputName", "")))}
    if not quads:
        return {}
    outs = []
    for x in g.outputs.values():
        p = x.get("Properties") or {}
        name = _pretty(p.get("OutputName"))
        read = {r for r in _reads(g, p.get("A"), quads, set()) if r[1] is not None}
        if name and read:
            outs.append((name, read))
    named = {}
    for name, read in outs:
        if len(read) == 1:
            named.setdefault(next(iter(read)), name)
    for name, read in outs:
        left = read - set(named)
        if len(read) > 1 and len(left) == 1 and name not in named.values():
            named[next(iter(left))] = name
    return named if len(named) >= 4 else {}


def _pretty(name):
    name = re.sub(r"^\d+_", "", str(name or ""))
    name = re.sub(r"^Biome_", "", name).replace("_", " ")
    name = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name).strip()
    return None if name.lower() in ("", "none") else name


def _reads(g, ref, quads, seen):
    """The (quarter, channel) pairs an expression reads; channel None for a whole quarter."""
    from .core import T
    from .translator import Translator
    if not isinstance(ref, dict) or ref.get("ExpressionName") in (None, "None"):
        return set()
    key = g.key(ref)
    x = g.by.get(key)
    if x is None:
        return set()
    t, p = T(x), x.get("Properties") or {}
    channels = [c for c, k in enumerate(("MaskR", "MaskG", "MaskB", "MaskA")) if ref.get(k)] if ref.get("Mask") else None
    if t == "FunctionInput":
        q = quads.get(p.get("Id"))
        found = {(q, None)} if q else set()
    elif (key, ref.get("OutputIndex", 0)) in seen:
        return set()
    else:
        seen.add((key, ref.get("OutputIndex", 0)))
        if t == "NamedRerouteUsage":
            decl = Translator.declaration(g, p)
            found = _reads(g, (decl or {}).get("Properties", {}).get("Input"), quads, seen)
        elif t == "MaterialFunctionCall":
            ins = [fi.get("Input") for fi in p.get("FunctionInputs", [])]
            found = set().union(*[_reads(g, r, quads, seen) for r in ins]) if ins else set()
            if str((p.get("MaterialFunction") or {}).get("ObjectName", "")).endswith("BreakOutFloat4Components'"):
                channels = [ref.get("OutputIndex", 0) or 0]
        elif t == "ComponentMask":
            found = _reads(g, p.get("Input"), quads, seen)
            channels = [c for c, k in enumerate("RGBA") if p.get(k)]
        else:
            found = set().union(*[_reads(g, v, quads, seen) for v in p.values() if isinstance(v, dict)]) or set()
    if channels and len(channels) == 1:
        found = {(q, channels[0] if c is None else c) for q, c in found}
    return found


def rect_of(found, rects):
    """The rectangle a probed sample reads among its texture's: its own (clamped), else the one starting at its
    corner."""
    kind, value = found[:2]
    if kind == "rect":
        return value
    return next((r for r in rects or () if abs(r[0] - value[0]) < 1e-4 and abs(r[1] - value[1]) < 1e-4), None)


def hide_key(image, rect, channel):
    """The mesh's custom property hiding one channel of a painted map's rectangle (a biome mask) in its material
    (the image by a hash: property names stop at 63 characters)."""
    return "mp_hide_%08x_%d_%d_%d" % (zlib.crc32(image.encode()), round(rect[0] * 1000), round(rect[1] * 1000), channel)


def _weights_uv(tr):
    """Where the pixel is on the weightmaps' UV map: the mesh's own, or the env's (scatter_biomes: an instance's
    terrain's)."""
    own = getattr(tr.env, "weights_uv", None)
    if own is not None:
        return own()
    uv = tr.env.once("weights uv", lambda: tr.node("ShaderNodeUVMap", "weightmap UVs", uv_map="mp_weights"))
    return Val(uv.outputs["UV"], 3)


def _read(tr, image, rect):
    """The mesh's own map (`image`) over `rect`, read through the weightmaps' UV map: (colour, alpha), each channel
    zeroed while hidden (hide_key)."""
    u, v = tr.comps(_weights_uv(tr))[:2]
    u0, v0, u1, v1 = rect
    cu = tr.binop('ADD', tr.const(u0), tr.binop('MULTIPLY', u, tr.const(u1 - u0)))
    cv = tr.binop('ADD', tr.const(v0), tr.binop('MULTIPLY', v, tr.const(v1 - v0)))
    # UE's v runs down
    n = tr.node("ShaderNodeTexImage", "map " + image, interpolation='Linear', extension='EXTEND')
    n.image = bpy.data.images.get(image)
    tr.link(tr.combine([cu, tr.binop('SUBTRACT', tr.const(1.0), cv)]), n.inputs["Vector"])
    shown = [tr.env.shown(hide_key(image, rect, c)) for c in range(4)]
    rgb = tr.vmath('MULTIPLY', Val(n.outputs["Color"], 3), tr.combine(shown[:3]), out_w=3)
    return rgb, tr.binop('MULTIPLY', Val(n.outputs["Alpha"], 1), shown[3])


def _outputs(tr, rgb, alpha, out):
    if out == 5:
        return tr.with_alpha(rgb, alpha)
    if out == 0:
        return rgb
    if out == 4:
        return alpha
    return tr.comps(rgb)[out - 1]


def sample(tr, image, rect, out):
    """The mesh's own map (`image`) over `rect`: the sample's outputs."""
    rgb, alpha = _read(tr, image, rect)
    return _outputs(tr, rgb, alpha, out)


def instanced(tr, param, found, game, out):
    """A map-wide sample on a mesh scattered over a terrain (scatter_biomes): the terrain's painted map where it
    paints this texture, or the layer's chosen biome, else the game's (`game`), mixed by the shared group's
    Painted (0 on map landscapes: the game's exactly)."""
    from .. import scatter_biomes
    g = scatter_biomes.group_for(param, found)
    tr.env.uses_biomes = True       # (the build fills the groups after)

    def make():
        n = tr.node("ShaderNodeGroup", g.name)
        n.node_tree = g
        return n
    n = tr.env.once("biome " + g.name, make)
    painted = Val(n.outputs["Painted"], 1)
    mine = _outputs(tr, Val(n.outputs["Color"], 3), Val(n.outputs["Alpha"], 1), out)
    rgb = mine if out != 5 else Val(mine.s, 3)

    def mix(a, b):
        return tr.binop('ADD', a, tr.binop('MULTIPLY', tr.binop('SUBTRACT', b, a), painted))
    if out == 5:
        return tr.with_alpha(mix(Val(game.s, 3), rgb), mix(tr.alpha(game), mine.a))
    return mix(game, rgb)


def follow(tr, cfg, out):
    """A colour map following the mesh's biome masks (biome_colours.config): the ground's colour blended toward each
    biome's by its painted mask, in the shader (painting the masks shows at once)."""
    colours, total, mixed = cfg["colours"], None, None
    for i, rect in enumerate(cfg["rects"]):
        keys = [c for c in range(4) if "%d,%d" % (i, c) in colours]
        if not keys:
            continue
        rgb, alpha = _read(tr, cfg["mask"], rect)
        channels = tr.comps(rgb)[:3] + [alpha]
        for c in keys:
            w = channels[c]
            term = tr.vmath('SCALE', tr.const(tuple(colours["%d,%d" % (i, c)]), 3), w, out_w=3)
            mixed = term if mixed is None else tr.vmath('ADD', mixed, term, out_w=3)
            total = w if total is None else tr.binop('ADD', total, w)
    ground = tr.const(tuple(colours["ground"]), 3)
    if total is None:
        return _outputs(tr, ground, tr.const(1.0), out)
    # weights over 1 are scaled down to 1, as biome_colours.bake
    norm = tr.binop('MAXIMUM', total, tr.const(1.0))
    left = tr.binop('SUBTRACT', tr.const(1.0), tr.binop('DIVIDE', total, norm))
    rgb = tr.vmath('ADD', tr.vmath('SCALE', ground, left, out_w=3),
                   tr.vmath('SCALE', mixed, tr.binop('DIVIDE', tr.const(1.0), norm), out_w=3), out_w=3)
    return _outputs(tr, rgb, tr.const(1.0), out)


def paint_uv(rect, u, v):
    """Where a point of the weightmaps' UV map (u, v) reads the map image over `rect` (Blender's image space): what the
    UV map painting that rectangle holds."""
    u0, v0, u1, v1 = rect
    return u0 + u * (u1 - u0), 1.0 - (v0 + v * (v1 - v0))
