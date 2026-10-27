"""Grass masks: a landscape material's LandscapeGrassOutput inputs evaluated per terrain vertex (numpy), stored as float
point attributes mp_grass_<name> for scatter layers to weigh by.

    weights = evaluate(obj, ["Grass01", "Snow"])     # {name: (n,) float32}
    report = write(obj, placement["MPGrass"])        # every output MPGrass lists (None: all the material has)

UE renders a grass map per landscape vertex into an 8-bit target, so values are per vertex and clamped to 0..1. The
graph is ue_graph's (load_graph, its static switch and function call rules); grass_eval evaluates each expression as
a numpy array instead of nodes. Inputs:
* world position in UE cm and vertex normals in UE space (FP mirrors Y; Blender metres x 100),
* layer weights: a map landscape's packed colour attributes (landscape_layers.table), a custom terrain's weightmap
  images through UV map mp_weights (weightmaps), both zeroed by the layer's hide key as the material does,
* textures: the game's (app.texture), or a custom terrain's painted biome map over the rectangle the sample reads
  (biome_maps, ue_graph.maptex),
* parameters and switches: the material's (app.get("material", mat["mp_path"])).
An expression without a rule here is reported (report["unknown"]) and reads 1; approximations in report["stand_ins"].

`outputs`: names, MPGrass's outputs ({"name", "input"?: {"expression", "output", "channels"?}}, see
GrassTypes.cs; without "input" the grass output's input of that name) or the whole MPGrass. None: every input of the
material's grass output."""
import numpy as np

from . import landscape_layers, weightmaps
from .grass_eval import DT, Evaluator, _bilinear, _pixels
from .ue_graph.core import T

PREFIX = "mp_grass_"


def attribute(name):
    return PREFIX + name


# ------------------------------------------------------------------ mesh inputs
def _per_vertex(mesh, values, domain):
    """A CORNER (loop) array averaged onto the vertices; POINT as is."""
    if domain != 'CORNER':
        return values
    idx = np.empty(len(mesh.loops), dtype=np.int32)
    mesh.loops.foreach_get("vertex_index", idx)
    n = len(mesh.vertices)
    out = np.zeros((n,) + values.shape[1:], dtype=np.float64)
    np.add.at(out, idx, values)
    count = np.bincount(idx, minlength=n).astype(np.float64)
    return (out / np.maximum(count, 1.0).reshape((-1,) + (1,) * (values.ndim - 1))).astype(DT)


def _uv(mesh, name):
    layer = mesh.uv_layers.get(name)
    if layer is None:
        return None
    uv = np.empty(len(mesh.loops) * 2, dtype=DT)
    layer.data.foreach_get("uv", uv)
    return _per_vertex(mesh, uv.reshape(-1, 2), 'CORNER')


class Terrain:
    """What the graph reads from a terrain mesh, per vertex (UE space)."""

    def __init__(self, obj):
        import bpy
        self.obj, mesh = obj, obj.data
        self.n = len(mesh.vertices)
        m = np.array(obj.matrix_world, dtype=np.float64)
        co = np.empty(self.n * 3, dtype=DT)
        mesh.vertices.foreach_get("co", co)
        world = co.reshape(-1, 3) @ m[:3, :3].T + m[:3, 3]
        self.position = (world * (100.0, -100.0, 100.0)).astype(DT)
        nor = np.empty(self.n * 3, dtype=DT)
        mesh.vertices.foreach_get("normal", nor)
        nor = nor.reshape(-1, 3) @ np.linalg.inv(m[:3, :3])        # normals go by the inverse transpose
        nor /= np.maximum(np.linalg.norm(nor, axis=1, keepdims=True), 1e-12)
        self.normal = (nor * (1.0, -1.0, 1.0)).astype(DT)
        self.actor = (np.array(obj.matrix_world.translation) * (100.0, -100.0, 100.0)).astype(DT)[None]
        self.world = bool(obj.get(landscape_layers.ALL))        # a mesh a landscape material was put on
        self.table, self.maps = landscape_layers.table(obj), weightmaps.images(obj)
        self.biome = {k: bpy.data.images.get(v) for k, v in dict(obj.get("mp_biome_maps") or {}).items()
                      if bpy.data.images.get(v) is not None}
        self._memo = {}

    def _once(self, key, make):
        if key not in self._memo:
            self._memo[key] = make()
        return self._memo[key]

    def layers(self):
        return landscape_layers.layers(self.obj)

    def painted(self, name):
        return self._once(("painted", name), lambda: (w := self.weight(name)) is not None and float(w.max()) > 0.0)

    def weight(self, name):
        """A layer's weight per vertex (n,), None where the mesh has no such layer."""
        return self._once(("layer", name), lambda: self._weight(name))

    def _weight(self, name):
        mesh = self.obj.data
        attribute, channel = self.table.get(name, (name, 0))
        if attribute in self.maps:
            uv = self.weights_uv()
            if uv is None:
                return None
            # Blender's image space: v up
            w = _bilinear(_pixels(self.maps[attribute]), uv[:, 0], 1.0 - uv[:, 1], wrap=False)[:, channel]
        else:
            a = mesh.color_attributes.get(attribute)
            if a is None or (name not in self.table and name == attribute and attribute.startswith(("COL0", "mp_"))):
                return None
            v = np.empty(len(a.data) * 4, dtype=DT)
            a.data.foreach_get("color", v)
            w = _per_vertex(mesh, v.reshape(-1, 4)[:, channel], a.domain)
        hidden = float(self.obj.get(landscape_layers.hide_key(name), 0.0) or 0.0)
        return (w * (1.0 - hidden)).astype(DT)

    def weights_uv(self):
        return self._once("weights uv", lambda: _uv(self.obj.data, weightmaps.UV))

    def uv(self, index):
        """UE's texture coordinate `index` (u, 1 - v); on a mesh a landscape material was put on, or without the UV map,
        the world in 1 m quads (env.uv)."""
        def make():
            got = None if self.world else _uv(self.obj.data, "UV%d" % index)
            if got is None:
                a, b = ((0, 1), (0, 2), (1, 2))[min(index, 2)]
                return (self.position[:, (a, b)] / 100.0).astype(DT)
            return np.stack([got[:, 0], 1.0 - got[:, 1]], axis=1).astype(DT)
        return self._once(("uv", index), make)


# ------------------------------------------------------------------ material inputs
class Source:
    """The material's graph, parameters and textures, from the app (app_client.AppClient or alike)."""
    asset_paths = True          # textures and collections by object path (ue_graph.statics.tex_key)

    def __init__(self, app, entry, pixels=None):
        from .env import match_names
        self.app, self.entry = app, match_names(dict(entry))
        self._pixels = {} if pixels is None else pixels      # shared by a terrain's materials

    def static_switch(self, name, default):
        return bool(self.entry.get("switches", {}).get(name, default))

    def static_mask(self, name, default):
        m = self.entry.get("masks", {}).get(name)
        return tuple(m) if m else None

    def texture_parameter(self, name, default):
        return self.entry.get("textures", {}).get(name, default)

    def function(self, name, objpath=None):
        return self.app.graph(objpath or name)

    def scalar(self, name, default):
        return float(self.entry.get("scalars", {}).get(name, default)) if name else float(default or 0.0)

    def vector(self, name, default):
        v = self.entry.get("vectors", {}).get(name, default) if name else default
        if isinstance(v, dict):
            v = [v.get(k, 0.0) for k in "RGBA"]
        v = [float(c) for c in v] + [0.0] * (4 - len(v))
        return tuple(v[:4])

    def collection(self, name, path):
        c = self.app.collection(path) if path else {}
        for kind in ("scalars", "vectors"):
            if name in c.get(kind, {}):
                v = c[kind][name]
                return (float(v),) if kind == "scalars" else tuple(float(x) for x in list(v) + [0.0] * (4 - len(v)))[:4]
        return None

    def pixels(self, key):
        """A game texture as (H, W, 4) float32 linear, or None."""
        if key not in self._pixels:
            self._pixels[key] = None
            try:
                rec = self.app.texture(key)
            except Exception:
                return None
            import bpy
            from .env import _load
            img = _load(rec["File"], int(rec.get("Width") or 0), int(rec.get("Height") or 0))
            try:
                img.colorspace_settings.name = "sRGB" if rec.get("Srgb") and not rec.get("Hdr") else "Non-Color"
            except TypeError:
                pass
            self._pixels[key] = _pixels(img)
            if not img.users:
                bpy.data.images.remove(img)
        return self._pixels[key]


# ------------------------------------------------------------------ API
def grass_inputs(g):
    """{name: FExpressionInput} of a graph's LandscapeGrassOutput, in its order."""
    x = next((x for x in g.o if T(x) == "LandscapeGrassOutput"), None)
    return {gt.get("Name"): gt.get("Input") for gt in ((x or {}).get("Properties") or {}).get("GrassTypes") or []}


def _app(app):
    if app is not None:
        return app
    from .app_client import AppClient
    from .hook import URL
    return AppClient(URL)


def _ref(spec):
    """MPGrass's {"expression", "output", "channels"?} as an FExpressionInput."""
    ref = {"ExpressionName": spec["expression"], "OutputIndex": int(spec.get("output") or 0)}
    if spec.get("channels"):
        ref["Mask"] = 1
        ref.update({k: int(bool(c)) for k, c in zip(("MaskR", "MaskG", "MaskB", "MaskA"), spec["channels"])})
    return ref


def _slots(obj, material):
    """[(material, vertex mask or None)]: `material` everywhere, else each slot's game material over the vertices of
    its faces (a landscape proxy's components each have their instance; a vertex between two takes the later slot)."""
    from .build import KEY_PATH
    if material is not None:
        return [(material, None)]
    mesh = obj.data
    mats = [s.material for s in obj.material_slots]
    used = [i for i, m in enumerate(mats) if m is not None and m.get(KEY_PATH)]
    if len(used) <= 1:
        mat = mats[used[0]] if used else obj.active_material
        return [(mat, None)]
    index = np.empty(len(mesh.polygons), dtype=np.int32)
    mesh.polygons.foreach_get("material_index", index)
    total = np.empty(len(mesh.polygons), dtype=np.int32)
    mesh.polygons.foreach_get("loop_total", total)
    per_loop = np.repeat(index, total)
    verts = np.empty(len(mesh.loops), dtype=np.int32)
    mesh.loops.foreach_get("vertex_index", verts)
    slot = np.full(len(mesh.vertices), -1, dtype=np.int32)
    np.maximum.at(slot, verts, per_loop)
    return [(mats[i], slot == i) for i in used if np.any(slot == i)]


def _evaluate(obj, outputs, material, app):
    from .build import KEY_PATH
    app = _app(app)
    terrain = Terrain(obj)
    if isinstance(outputs, dict):           # a whole MPGrass
        outputs = outputs.get("outputs")
    result, unknown, stand_ins, missing, pixels = {}, {}, set(), None, {}
    for mat, where in _slots(obj, material):
        if mat is None or not mat.get(KEY_PATH):
            raise ValueError("%s has no game material to read grass masks from" % obj.name)
        entry = app.get("material", mat[KEY_PATH])
        if not entry.get("graph"):
            raise ValueError("%s: the app has no graph for %s" % (obj.name, mat[KEY_PATH]))
        ev = Evaluator(Source(app, entry, pixels), terrain)
        g = ev.graph(app.local(entry["graph"]))
        known = grass_inputs(g)
        specs = [{"name": o} if isinstance(o, str) else o for o in (outputs if outputs is not None else known)]
        lacking = []
        for spec in specs:
            name = spec.get("name")
            ref = _ref(spec["input"]) if spec.get("input") else known.get(name)
            if ref is None:
                lacking.append(name)
                continue
            v = ev.grass(g, ref)
            if where is None:
                result[name] = v
            else:
                result.setdefault(name, np.zeros(terrain.n, dtype=DT))[where] = v[where]
        missing = lacking if missing is None else [n for n in missing if n in lacking]
        for k, c in ev.unknown.items():
            unknown[k] = unknown.get(k, 0) + c
        stand_ins |= ev.stand_ins
    report = {"unknown": dict(sorted(unknown.items())), "stand_ins": sorted(stand_ins), "missing": missing or []}
    return result, report


def evaluate(obj, outputs=None, material=None, app=None, report=None):
    """{name: (n,) float32 per vertex} of the grass masks of `material` (default: each slot's over its faces) over obj.
    `report` (a dict) gets {"unknown": {expression: count}, "stand_ins": [...], "missing": [names not output]}."""
    result, rep = _evaluate(obj, outputs, material, app)
    if report is not None:
        report.update(rep)
    return result


def write(obj, outputs=None, material=None, app=None):
    """evaluate() into float point attributes mp_grass_<name> on obj's mesh; returns the report (plus "written")."""
    result, report = _evaluate(obj, outputs, material, app)
    mesh = obj.data
    for name, values in result.items():
        a = mesh.attributes.get(attribute(name))
        if a is not None and (a.domain != 'POINT' or a.data_type != 'FLOAT'):
            mesh.attributes.remove(a)
            a = None
        if a is None:
            a = mesh.attributes.new(attribute(name), 'FLOAT', 'POINT')
        a.data.foreach_set("value", values)
    mesh.update()
    report["written"] = [attribute(n) for n in result]
    return report
