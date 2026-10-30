"""grass_masks (material_porter/grass_masks.py): grass masks evaluated per vertex from a fixture landscape material built
like M_Hera_Base's grass inputs (painted layers, MF_CliffSlopeMask, a world-space biome texture behind a static switch,
MF_Helios_LS_GrassSwitch's Z limit), on terrains whose expected masks are worked out by hand. With the test instance
up (24322), the real MI_Hera_Base_V2 too: no unknown expressions, nothing below Z -1200.

    python plugin_test.py tests/plugin/grass_masks_check.py --name grass_masks
"""
import json
import math
import os
import sys
import tempfile

import bpy
import numpy as np

sys.path.insert(0, sys.argv[sys.argv.index("--") + 1])
import fpmp_baseline  # noqa: E402,F401
from fpmp_baseline.material_porter import grass_masks, landscape_layers, weightmaps  # noqa: E402

FAILS, PASSES = [], [0]
TMP = tempfile.mkdtemp(prefix="grass_masks_")


def check(name, ok, detail=""):
    if ok:
        PASSES[0] += 1
    else:
        FAILS.append(name)
        print("[grass_masks_check] FAIL %s %s" % (name, detail))


def close(name, got, want, tol=5e-4):
    got, want = np.asarray(got, np.float64), np.broadcast_to(np.asarray(want, np.float64), np.shape(got))
    err = float(np.max(np.abs(got - want))) if got.size else 0.0
    check(name, err <= tol, "max error %.5f (got %s, want %s)" % (err, got[:6], want[:6]))


# ------------------------------------------------------------------ fixture graphs (ue_graph's dump format)
class G:
    def __init__(self):
        self.o, self.n = [], 0

    def x(self, t, **props):
        name = "MaterialExpression%s_%d" % (t, self.n)
        self.n += 1
        self.o.append({"Type": "MaterialExpression" + t, "Name": name, "Properties": props})
        return name


def ref(name, out=0, mask=None):
    r = {"ExpressionName": name, "OutputIndex": out}
    if mask:
        r.update(Mask=1, **{"Mask" + c: int(c in mask) for c in "RGBA"})
    return r


def save(g, name):
    path = os.path.join(TMP, name + ".json")
    json.dump(g.o, open(path, "w", encoding="utf-8"))
    return path


def function_graphs():
    """MF_CliffSlopeMask and MF_Helios_LS_GrassSwitch as the game has them."""
    cliff = G()
    nrm = cliff.x("VertexNormalWS")
    m = cliff.x("ComponentMask", Input=ref(nrm), B=True)
    mul = cliff.x("Multiply", A=ref(cliff.x("OneMinus", Input=ref(cliff.x("Abs", Input=ref(m))))), B=ref(cliff.x("Constant", R=1.4)))
    cliff.x("FunctionOutput", Id="cliff-out", A=ref(cliff.x("Saturate", Input=ref(mul))))
    sw = G()
    fin = sw.x("FunctionInput", Id="sw-in", InputType="EFunctionInputType::FunctionInput_Scalar",
               bUsePreviewValueAsDefault=True, Preview=ref(sw.x("Constant")))
    lim = sw.x("FunctionInput", Id="sw-z", InputName="ZSpawnLimit", InputType="EFunctionInputType::FunctionInput_Scalar",
               bUsePreviewValueAsDefault=True, Preview=ref(sw.x("Constant", R=-1200.0)))
    wp = sw.x("WorldPosition")
    sat = sw.x("Saturate", Input=ref(sw.x("Subtract", A=ref(wp, 2, "B"), B=ref(lim))))
    m0 = sw.x("Multiply", A=ref(sw.x("Multiply", A=ref(fin), B=ref(sat))), B=ref(sw.x("LandscapeVisibilityMask")))
    on = sw.x("StaticSwitch", A=ref(m0), B=ref(sw.x("Constant")), Value=ref(sw.x("StaticBool", Value=True)))
    sw.x("FunctionOutput", Id="sw-out", A=ref(on))
    return {"/Fixture/MF_CliffSlopeMask.0": save(cliff, "MF_CliffSlopeMask"),
            "/Fixture/MF_Helios_LS_GrassSwitch.0": save(sw, "MF_Helios_LS_GrassSwitch")}


def call(g, fname, out_id, inputs=()):
    return g.x("MaterialFunctionCall", MaterialFunction={"ObjectName": "MaterialFunction'%s'" % fname,
                                                         "ObjectPath": "/Fixture/%s.0" % fname},
               FunctionInputs=[{"ExpressionInputId": i, "Input": r} for i, r in inputs],
               FunctionOutputs=[{"ExpressionOutputId": out_id}])


def material_graph():
    """Grass01 = switch((1 - max(Gravel, Snow)) * (1 - cliff) * (UseBiomeGrass ? biome.R : 1))
    Snow    = switch(If(Z >= 5000 cm, Snow * (1 - cliff), 0))
    Gravel  = switch(Gravel * GravelDensity)
    Weird   = Gravel * Noise (no rule: reported, 1)
    biome UV = (world XY - BiomeTextureWorldUV.B) / BiomeTextureWorldUV.A (Hera's map-wide projection)"""
    g = G()
    gravel, snow = g.x("LandscapeLayerSample", ParameterName="Gravel"), g.x("LandscapeLayerSample", ParameterName="Snow")
    cliff = call(g, "MF_CliffSlopeMask", "cliff-out")
    flat = g.x("OneMinus", Input=ref(cliff))
    worlduv = g.x("VectorParameter", ParameterName="BiomeTextureWorldUV", DefaultValue={"R": 0, "G": 0, "B": 0, "A": 1})
    wp = g.x("WorldPosition")
    uv = g.x("Divide", A=ref(g.x("Subtract", A=ref(wp, 0, "RG"), B=ref(worlduv, 3, "B"))), B=ref(worlduv, 4, "A"))
    biome = g.x("TextureSampleParameter2D", ParameterName="BiomeTexture", Coordinates=ref(uv),
                Texture={"ObjectName": "Texture2D'T_Biome'", "ObjectPath": "/Fixture/T_Biome.0"})
    use = g.x("StaticSwitchParameter", ParameterName="UseBiomeGrass", DefaultValue=False,
              A=ref(biome, 1, "R"), B=ref(g.x("Constant", R=1.0)))
    bare = g.x("OneMinus", Input=ref(g.x("Max", A=ref(gravel), B=ref(snow))))
    grass = g.x("Multiply", A=ref(g.x("Multiply", A=ref(bare), B=ref(flat))), B=ref(use))

    def switch(r):
        return ref(call(g, "MF_Helios_LS_GrassSwitch", "sw-out", [("sw-in", r)]))
    high = g.x("If", A=ref(wp, 2, "B"), B=ref(g.x("Constant", R=5000.0)),
               AGreaterThanB=ref(g.x("Multiply", A=ref(snow), B=ref(flat))), ALessThanB=ref(g.x("Constant")))
    density = g.x("ScalarParameter", ParameterName="GravelDensity", DefaultValue=1.0)
    gravel_out = g.x("Multiply", A=ref(gravel), B=ref(density))
    weird = g.x("Multiply", A=ref(gravel), B=ref(g.x("Noise")))
    g.x("LandscapeGrassOutput", GrassTypes=[{"Name": n, "Input": r} for n, r in (
        ("Grass01", switch(ref(grass))), ("Snow", switch(ref(high))), ("Gravel", switch(ref(gravel_out))),
        ("Weird", switch(ref(weird))))])
    return save(g, "M_Fixture")


def biome_png():
    """4 x 4, red 1 in the left two columns (u < 0.5), 0 in the right two."""
    img = bpy.data.images.new("T_Biome", 4, 4, alpha=True)
    px = np.zeros((4, 4, 4), np.float32)
    px[:, :2, 0] = 1.0
    px[..., 3] = 1.0
    img.pixels.foreach_set(px.ravel())
    path = os.path.join(TMP, "T_Biome.png")
    img.filepath_raw, img.file_format = path, "PNG"
    img.save()
    bpy.data.images.remove(img)
    return path


class FakeApp:
    """app_client.AppClient's routes over the fixture files."""

    def __init__(self, entry, graphs, textures):
        self.entry, self.graphs, self.textures = entry, graphs, textures

    def get(self, route, path):
        assert route == "material", route
        return dict(self.entry)

    def local(self, path):
        return path

    def graph(self, path):
        return self.graphs[path]

    def texture(self, path):
        return {"File": self.textures[path], "Srgb": False, "Width": 4, "Height": 4}

    def collection(self, path):
        return {}


# ------------------------------------------------------------------ terrains
MAP_UV = (-130048.0, 260096.0)      # Hera's BiomeTextureWorldUV: origin, size (cm)


def biome_x(u):
    """Blender metres x where the biome texture's u is `u`."""
    return (MAP_UV[0] + u * MAP_UV[1]) / 100.0


def grid(name, x0, y0, z, n=5, spacing=4.0, tilt=0.0):
    """n x n vertices from (x0, y0) metres; z: height (m) per (i, j); tilt: the object rotated about X (degrees)."""
    verts = [(i * spacing, j * spacing, z(i, j)) for j in range(n) for i in range(n)]
    faces = [(j * n + i, j * n + i + 1, (j + 1) * n + i + 1, (j + 1) * n + i) for j in range(n - 1) for i in range(n - 1)]
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    obj = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(obj)
    obj.location = (x0, y0, 0.0)
    obj.rotation_euler = (math.radians(tilt), 0.0, 0.0)
    mat = bpy.data.materials.get("MI_Fixture") or bpy.data.materials.new("MI_Fixture")
    mat["mp_path"] = "/Fixture/MI_Fixture.MI_Fixture"
    me.materials.append(mat)
    obj["mp_landscape"] = 1.0
    bpy.context.view_layer.update()
    return obj


def paint(obj, weights, data_type='FLOAT_COLOR', domain='POINT'):
    """Packed layers as a map import keeps them: {layer: (n,) per vertex} in Attribute1's channels."""
    me = obj.data
    a = me.color_attributes.new("Attribute1", data_type, domain)
    v = np.zeros((len(me.vertices), 4), np.float32)
    v[:, 3] = 1.0
    table = {}
    for k, (layer, w) in enumerate(sorted(weights.items())):
        v[:, k] = w
        table[layer] = "Attribute1.%s" % "RGB"[k]
    if domain == 'CORNER':
        idx = np.empty(len(me.loops), np.int32)
        me.loops.foreach_get("vertex_index", idx)
        v = v[idx]
    a.data.foreach_set("color", v.ravel())
    obj[landscape_layers.KEY] = table


def world_z(obj):
    co = np.empty(len(obj.data.vertices) * 3, np.float32)
    obj.data.vertices.foreach_get("co", co)
    m = np.array(obj.matrix_world)
    return (co.reshape(-1, 3) @ m[:3, :3].T + m[:3, 3])[:, 2]


def expected(gravel, snow, z_m, nz, biome):
    cliff = np.clip((1.0 - np.abs(nz)) * 1.4, 0.0, 1.0)
    keep = np.clip(z_m * 100.0 + 1200.0, 0.0, 1.0)
    return {"Grass01": keep * (1 - np.maximum(gravel, snow)) * (1 - cliff) * biome,
            "Snow": keep * np.where(z_m * 100.0 >= 5000.0, snow * (1 - cliff), 0.0),
            "Gravel": keep * gravel * 0.5,
            "Weird": keep * gravel}


def main():
    graphs = function_graphs()
    entry = {"graph": material_graph(), "switches": {"UseBiomeGrass": True}, "scalars": {"GravelDensity": 0.5},
             "vectors": {"BiomeTextureWorldUV": [0.0, 0.0, MAP_UV[0], MAP_UV[1]]}, "textures": {}}
    app = FakeApp(entry, graphs, {"/Fixture/T_Biome.T_Biome": biome_png()})
    n = 5
    ij = [(i, j) for j in range(n) for i in range(n)]

    # 1. flat, over the biome's red half (u 0.2: texel centres 0.125..0.375 read 1), heights across the Z limit and
    #    the snow line; layers per vertex in float point attributes
    heights = {0: -20.0, 1: -11.995, 2: 0.0, 3: 49.0, 4: 60.0}         # per row j (m); -11.995 m = -1199.5 cm: 0.5
    flat = grid("flat", biome_x(0.2), 0.0, lambda i, j: heights[j], spacing=4.0)
    gravel = np.array([0.25 * i for i, j in ij], np.float32)            # 0 .. 1 along x
    snow = np.array([1.0 - 0.25 * i if j >= 3 else 0.0 for i, j in ij], np.float32)
    paint(flat, {"Gravel": gravel, "Snow": snow})
    # (rows step in height: the vertices' normals tilt there; the expected masks read Blender's normals)
    nz = np.array([v.normal.z for v in flat.data.vertices])
    report = {}
    got = grass_masks.evaluate(flat, None, app=app, report=report)
    want = expected(gravel, snow, world_z(flat), nz, 1.0)
    check("all outputs", sorted(got) == ["Grass01", "Gravel", "Snow", "Weird"], sorted(got))
    for name in ("Grass01", "Snow", "Gravel", "Weird"):
        close("flat %s" % name, got[name], want[name])
    check("half at -1199.5 cm", abs(float(got["Gravel"][1 * n + 4]) - 0.25) < 1e-3, got["Gravel"][n:2 * n])
    check("nothing below -1200", float(got["Grass01"][:n].max()) == 0.0)
    check("Noise reported", any(k.startswith("Noise") for k in report["unknown"]), report["unknown"])
    check("only Noise unknown", len(report["unknown"]) == 1, report["unknown"])

    # 2. a 45 degree slope (the object turned, its mesh flat), over the biome's empty half: cliff 0.41, biome 0
    slope = grid("slope", biome_x(0.7), 0.0, lambda i, j: 0.0, tilt=45.0)
    slope.location.z = 100.0
    bpy.context.view_layer.update()
    paint(slope, {"Gravel": np.full(n * n, 0.2, np.float32), "Snow": np.zeros(n * n, np.float32)})
    got = grass_masks.evaluate(slope, ["Grass01", "Gravel"], app=app)
    close("slope Grass01 (biome 0)", got["Grass01"], 0.0)
    slope_b = grid("slope_b", biome_x(0.2), 0.0, lambda i, j: 0.0, tilt=45.0)
    slope_b.location.z = 100.0
    bpy.context.view_layer.update()
    paint(slope_b, {"Gravel": np.full(n * n, 0.2, np.float32), "Snow": np.zeros(n * n, np.float32)})
    got = grass_masks.evaluate(slope_b, ["Grass01"], app=app)
    close("slope Grass01 (biome 1)", got["Grass01"], 0.8 * (1 - (1 - math.cos(math.radians(45))) * 1.4))

    # 3. byte colours on corners (a map import's), static switch at its default: biome off
    corners = grid("corners", biome_x(0.7), 0.0, lambda i, j: 2.0)
    paint(corners, {"Gravel": np.full(n * n, 0.6, np.float32), "Snow": np.zeros(n * n, np.float32)}, 'BYTE_COLOR', 'CORNER')
    plain = FakeApp(dict(entry, switches={}), graphs, app.textures)
    got = grass_masks.evaluate(corners, ["Grass01", "Gravel"], app=plain)
    close("corners Grass01 (switch off)", got["Grass01"], 0.4, 0.01)
    close("corners Gravel", got["Gravel"], 0.3, 0.01)

    # 4. a custom terrain: weightmap images through UV mp_weights; a hidden layer reads 0
    custom = grid("custom", biome_x(0.2), 0.0, lambda i, j: 3.0)
    weightmaps.ensure([custom], ["Gravel", "Snow"])
    weightmaps.fill(custom, "Gravel", 0.6)
    got = grass_masks.evaluate(custom, ["Grass01", "Gravel"], app=app)
    close("custom Gravel", got["Gravel"], 0.3, 0.005)
    close("custom Grass01", got["Grass01"], 0.4, 0.005)
    custom[landscape_layers.hide_key("Gravel")] = 1.0
    got = grass_masks.evaluate(custom, ["Gravel"], app=app)
    close("custom Gravel hidden", got["Gravel"], 0.0)
    del custom[landscape_layers.hide_key("Gravel")]

    # 5. MPGrass's form (GrassTypes.cs): an output by its input's expression; write() stores point attributes
    gx = next(x for x in json.load(open(entry["graph"])) if x["Type"].endswith("LandscapeGrassOutput"))
    gin = gx["Properties"]["GrassTypes"][2]["Input"]
    mp = {"material": "/Fixture/M_Fixture", "outputs": [
        {"name": "Gravel", "type": [], "input": {"expression": gin["ExpressionName"], "output": 0}},
        {"name": "Grass01", "type": []}, {"name": "Nope", "type": []}]}
    rep = grass_masks.write(flat, mp, app=app)
    attr = flat.data.attributes.get("mp_grass_Gravel")
    check("attribute written", attr is not None and attr.domain == 'POINT' and attr.data_type == 'FLOAT')
    if attr is not None:
        v = np.empty(len(attr.data), np.float32)
        attr.data.foreach_get("value", v)
        close("written Gravel", v, expected(gravel, snow, world_z(flat), nz, 1.0)["Gravel"])
    check("missing output reported", rep["missing"] == ["Nope"], rep)
    check("written list", rep["written"] == ["mp_grass_Gravel", "mp_grass_Grass01"], rep.get("written"))

    real_hera()


def real_hera():
    """The real MI_Hera_Base_V2 through the test instance, when it answers."""
    from fpmp_baseline.material_porter.app_client import AppClient
    app = AppClient(os.environ.get("MATERIAL_PORTER_BRIDGE", "http://localhost:24322"))
    try:
        app.get("material", "/Hera/Environment/Terrain/Landscape/Materials/MI_Hera_Base_V2.MI_Hera_Base_V2")
    except Exception as e:
        print("[grass_masks_check] real Hera skipped: %s" % str(e)[:120])
        return
    n = 9
    obj = grid("hera", 30.0, -40.0, lambda i, j: -14.0 + 0.5 * j, n=n, spacing=3.0)     # a gentle slope over -12 m
    obj.active_material["mp_path"] = "/Hera/Environment/Terrain/Landscape/Materials/MI_Hera_Base_V2.MI_Hera_Base_V2"
    gravel = np.array([1.0 if i < 3 else 0.0 for j in range(n) for i in range(n)], np.float32)
    paint(obj, {"Gravel": gravel})
    report = {}
    got = grass_masks.evaluate(obj, None, app=app, report=report)
    check("hera 15 outputs", len(got) == 15, sorted(got))
    check("hera no unknown", not report["unknown"], report["unknown"])
    z = world_z(obj)
    below = z * 100.0 <= -1200.0
    check("hera nothing below -1200", all(float(v[below].max()) == 0.0 for v in got.values()))
    above = z * 100.0 >= -1199.0
    check("hera Gravel on gravel", float(got["Gravel"][above & (gravel > 0)].min()) > 0.0, got["Gravel"][above][:9])
    check("hera Grass01 not on gravel", float(got["Grass01"][above & (gravel > 0)].max()) < 0.5)
    print("[grass_masks_check] hera means: %s; stand-ins %s" % (
        ", ".join("%s %.2f" % (k, float(v[above].mean())) for k, v in got.items()), report["stand_ins"]))


main()
print("[grass_masks_check] %d passed, %d failed" % (PASSES[0], len(FAILS)))
if FAILS:
    sys.exit(1)
