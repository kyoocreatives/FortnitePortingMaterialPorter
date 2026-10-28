"""Map-wide textures on scattered meshes (Hera's grass blades read the biome masks and grass colours at their world
XY, as the ground under them does): on a custom terrain they read the terrain's painted maps, as its material does
(biome_maps, ue_graph.maptex), so a blade's colour is the ground's grass colour under it.

A material reading such a texture off a landscape mixes the game's sample with a shared group's (maptex.instanced),
one group per texture parameter and rectangle, named PREFIX..., by its Painted output. refresh() fills the groups:
for each painted terrain set (the meshes sharing weightmaps) the set's maps over the terrain's weightmap UV, picked by
the instancer's SET property; and a scatter layer's chosen biome (instance attribute CHOICE: the mask's channel
1-based, its measured grass colour). Elsewhere (map landscapes, no choice) Painted is 0: the game's sample exactly.

The weightmap UV under a pixel: the instance's root UV (instance attribute UV, scatter.py) plus its offset from the
root in world XY times the terrain's UV per metre (SCALE)."""
import json
import zlib

import bpy
import numpy as np

from . import biome_colours, biome_maps, weightmaps
from .ue_graph import maptex
from .ue_graph.core import Val

PREFIX = "MP Biome · "
SET = "mp_biome_set"        # a painted terrain set's index, 1-based (an object property the instancer gives)
SCALE = "mp_biome_k"        # the terrain's weightmap UV per world metre
UV = "mp_biome_uv"          # per instance: the terrain's weightmap UV under its root
CHOICE = "mp_biome"         # per instance: the layer's chosen biome, 1-based (0: the world's)
MAPS = "mp_map_textures"    # build.KEY_MAPS


def group_for(param, found):
    """The shared group standing for a probed sample of `param` (unpainted until refresh())."""
    key = json.dumps([param, found[0], [round(float(c), 6) for c in found[1]]])
    name = "%s%s %08x" % (PREFIX, param[:32], zlib.crc32(key.encode()))
    g = bpy.data.node_groups.get(name)
    if g is None:
        g = bpy.data.node_groups.new(name, 'ShaderNodeTree')
        g.interface.new_socket("Color", in_out='OUTPUT', socket_type='NodeSocketColor')
        g.interface.new_socket("Alpha", in_out='OUTPUT', socket_type='NodeSocketFloat')
        g.interface.new_socket("Painted", in_out='OUTPUT', socket_type='NodeSocketFloat')
        g.nodes.new("NodeGroupOutput")
        g["mp_param"] = param
        g["mp_found"] = json.dumps([found[0], list(found[1])])
    return g


def groups():
    return [g for g in bpy.data.node_groups if g.name.startswith(PREFIX) and "mp_param" in g]


# ------------------------------------------------------------------ terrains

def _known(obj):
    mat = obj.active_material
    return json.loads(mat.get(MAPS) or "{}") if mat is not None else {}


def _sets():
    """[(set key, meshes)] of the painted terrains: meshes with a painted map that exists, by weightmap set."""
    found = {}
    for o in bpy.data.objects:
        if o.type != 'MESH' or not o.get(biome_maps.KEY):
            continue
        if not any(bpy.data.images.get(v) for v in dict(o[biome_maps.KEY]).values()):
            continue
        found.setdefault(o.get(weightmaps.SET) or o.name, []).append(o)
    return [(k, sorted(found[k], key=lambda o: o.name)) for k in sorted(found)]


def uv_per_metre(obj):
    """The weightmap UV a world metre spans (weightmaps._project: world XY over a square), 0 without the UV map."""
    me = obj.data
    layer = me.uv_layers.get(weightmaps.UV)
    if layer is None or not len(me.loops):
        return 0.0
    uv = np.empty(len(me.loops) * 2, dtype=np.float32)
    layer.data.foreach_get("uv", uv)
    corners = np.empty(len(me.loops), dtype=np.int32)
    me.loops.foreach_get("vertex_index", corners)
    co = np.empty(len(me.vertices) * 3, dtype=np.float32)
    me.vertices.foreach_get("co", co)
    m = np.array(obj.matrix_world)
    world = (co.reshape(-1, 3) @ m[:3, :3].T + m[:3, 3])[corners]
    uv = uv.reshape(-1, 2)
    axis = 0 if np.ptp(world[:, 0]) >= np.ptp(world[:, 1]) else 1
    span = float(np.ptp(world[:, axis]))
    return float(np.ptp(uv[:, axis])) / span if span > 1e-6 else 0.0


def _put(obj, key, value):
    if obj.get(key) != value:
        obj[key] = value


def _configs(sets):
    """Each set's maps as its material reads them (hook._set_layers): {biome_maps, biome_rects, biome_follow}."""
    out = []
    for index, (_key, meshes) in enumerate(sets, 1):
        obj = meshes[0]
        known = _known(obj)
        maps = {k: v for k, v in dict(obj.get(biome_maps.KEY) or {}).items() if bpy.data.images.get(v)}
        out.append({"index": index, "biome_maps": maps,
                    "biome_rects": {k: known[k]["rects"] for k in maps if k in known},
                    "biome_follow": biome_colours.config(obj, known)})
        k = round(uv_per_metre(obj), 9)
        for o in meshes:
            _put(o, SET, index)
            _put(o, SCALE, k)
    painted = {o.name for _k, meshes in sets for o in meshes}
    for o in bpy.data.objects:
        if o.get(SET) and o.name not in painted:
            _put(o, SET, 0)
    return out


# ------------------------------------------------------------------ chosen biomes

def _infos():
    """{param: info} of the map-wide textures any material in the file read (their rectangles and names agree)."""
    out = {}
    for mat in bpy.data.materials:
        raw = mat.get(MAPS)
        if raw:
            for param, info in json.loads(raw).items():
                if param not in out or info.get("labels") and not out[param].get("labels"):
                    out[param] = info
    return out


def _mask(infos):
    """(param, info) of the biome mask texture (four rectangles of masks), else None."""
    return next(((p, i) for p, i in infos.items() if not biome_maps.is_colour(p, i) and len(i["rects"]) == 4), None)


def _choice(g, infos):
    """What a chosen biome makes of this group's sample: {"mask": rectangle index} or {"colours": measured}."""
    param, found = g["mp_param"], json.loads(g["mp_found"])
    info = infos.get(param)
    if info is None:
        return None
    if not biome_maps.is_colour(param, info) and len(info["rects"]) == 4:
        rect = maptex.rect_of(found, info["rects"])
        return {"mask": info["rects"].index(rect)} if rect in info["rects"] else None
    mask = _mask(infos)
    if mask is None or len(info["rects"]) != 1:
        return None
    colours = g.get("mp_colours")
    if colours is None:
        measured = biome_colours.measure(mask[1], info)
        if measured is None:
            return None
        g["mp_colours"] = colours = json.dumps(measured)
    return {"colours": json.loads(colours)}


def choices(materials):
    """[(index, name)] of the biomes a scatter layer can choose (1-based, the mask's named channels) for these
    materials, [] when none reads a biome mask."""
    for mat in materials:
        if mat is None or not mat.get(MAPS):
            continue
        mask = _mask(json.loads(mat[MAPS]))
        if mask is None:
            continue
        labels = mask[1].get("labels") or {}
        out = []
        for key, name in labels.items():
            if name.startswith("Remove "):      # (Hera's masks clearing grass types and stripes: not biomes)
                continue
            rect, channel = (int(x) for x in key.split(","))
            out.append((rect * 4 + channel + 1, name))
        return sorted(out)
    return []


# ------------------------------------------------------------------ the groups

class _Env:
    """What maptex's readers ask of an env, inside a group: the instancer's properties and weightmap UV."""

    def __init__(self):
        self._once = {}
        self.probe = None
        self.tr = None

    def once(self, key, make):
        if key not in self._once:
            self._once[key] = make()
        return self._once[key]

    def attr(self, name, out="Fac"):
        n = self.once("attr " + name, lambda: self.tr.node("ShaderNodeAttribute", name, attribute_type='INSTANCER',
                                                           attribute_name=name))
        return Val(n.outputs[out], 3 if out == "Vector" else 1)

    def shown(self, key):
        return self.once("shown " + key, lambda: self.tr.binop('SUBTRACT', self.tr.const(1.0), self.attr(key)))

    def weights_uv(self):
        tr = self.tr

        def make():
            here = Val(tr.node("ShaderNodeNewGeometry", "geometry").outputs["Position"], 3)
            root = Val(tr.node("ShaderNodeObjectInfo", "instance").outputs["Location"], 3)
            offset = tr.vmath('SCALE', tr.vmath('SUBTRACT', here, root, out_w=3), self.attr(SCALE), out_w=3)
            return tr.vmath('ADD', self.attr(UV, "Vector"), offset, out_w=3)
        return self.once("weights uv", make)


def _fill(g, configs, choice):
    from .ue_graph.translator import Translator
    g.nodes.clear()
    env = _Env()
    tr = env.tr = Translator(g, env)
    param, found = g["mp_param"], json.loads(g["mp_found"])
    rgb, alpha, painted = tr.const((0.0, 0.0, 0.0), 3), tr.const(0.0), tr.const(0.0)

    def blend(rgb, alpha, painted, value, weight):
        mine, a = Val(value.s, 3), tr.alpha(value)
        return (tr.vmath('ADD', rgb, tr.vmath('SCALE', tr.vmath('SUBTRACT', mine, rgb, out_w=3), weight, out_w=3),
                         out_w=3),
                tr.binop('ADD', alpha, tr.binop('MULTIPLY', tr.binop('SUBTRACT', a, alpha), weight)),
                tr.binop('MAXIMUM', painted, weight))
    for cfg in configs:
        following = cfg["biome_follow"].get(param)
        image = cfg["biome_maps"].get(param)
        rect = maptex.rect_of(found, cfg["biome_rects"].get(param))
        if following is not None:
            value = maptex.follow(tr, following, 5)
        elif image is not None and rect is not None:
            value = maptex.sample(tr, image, rect, 5)
        else:
            continue
        here = tr.math('COMPARE', env.attr(SET), tr.const(float(cfg["index"])), tr.const(0.25))
        rgb, alpha, painted = blend(rgb, alpha, painted, value, here)
    if choice is not None:
        picked = env.attr(CHOICE)
        chosen = tr.math('GREATER_THAN', picked, tr.const(0.5))

        def biome(index):
            return tr.math('COMPARE', picked, tr.const(float(index)), tr.const(0.25))
        if "mask" in choice:
            ch = [biome(choice["mask"] * 4 + c + 1) for c in range(4)]
            value = tr.with_alpha(tr.combine(ch[:3]), ch[3])
        else:
            colours = choice["colours"]
            ground = tr.const(tuple(colours["ground"]), 3)
            mixed = ground
            for key, c in colours.items():
                if key == "ground":
                    continue
                rect, channel = (int(x) for x in key.split(","))
                step = tr.vmath('SCALE', tr.const(tuple(np.subtract(c, colours["ground"]).tolist()), 3),
                                biome(rect * 4 + channel + 1), out_w=3)
                mixed = tr.vmath('ADD', mixed, step, out_w=3)
            value = tr.with_alpha(mixed, tr.const(1.0))
        rgb, alpha, painted = blend(rgb, alpha, painted, value, chosen)
    out = tr.node("NodeGroupOutput", "output")
    tr.link(rgb, out.inputs["Color"])
    tr.link(alpha, out.inputs["Alpha"])
    tr.link(painted, out.inputs["Painted"])


def refresh():
    """Every group filled for the painted terrains and chosen biomes now (only those whose inputs changed)."""
    found = groups()
    sets = _sets()
    configs = _configs(sets)
    if not found:
        return 0
    infos = _infos()
    done = 0
    for g in found:
        choice = _choice(g, infos)
        sig = "%08x" % zlib.crc32(json.dumps([configs, choice], sort_keys=True, default=str).encode())
        if g.get("mp_sig") == sig:
            continue
        _fill(g, configs, choice)
        g["mp_sig"] = sig
        done += 1
    return done
