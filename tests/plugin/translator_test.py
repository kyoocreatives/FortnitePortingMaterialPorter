"""Numeric checks for the plugin's material_porter/ue_graph: small UE graphs, translated, and the
Blender nodes they become evaluated here in Python against UE's own formulas.

    blender -b --factory-startup --python-exit-code 1 -P tests/plugin/translator_test.py -- <plugin parent>

<plugin parent> is a folder holding the plugin as a package named fpmp_baseline (devtools/baseline.py makes one
from the fork's plugins/Blender/fortnite_porting, with the private overlay if any: `python baseline.py ... --only
suites` runs this). Exit code 0 when every check passes.

The evaluator covers the node types the translator emits for these graphs
(Math, Vector Math, Mix, Combine/Separate XYZ and Color, Value, groups), with
Blender's semantics (safe divide, floor(x + 0.5) rounding, fmod modulo).
Parameters come in as Value nodes, so the socket paths are exercised, not
just constant folding.
"""
import colorsys
import json
import math
import os
import sys
import tempfile

import bpy

sys.path.insert(0, sys.argv[sys.argv.index("--") + 1])
from fpmp_baseline.material_porter.ue_graph import ATTRIBUTE_OF_GUID, CUSTOM_HASHES, Translator, Val, merge_duplicates  # noqa: E402

GUID = {name: guid for guid, name in ATTRIBUTE_OF_GUID.items()}

TMP = tempfile.mkdtemp(prefix="ue_graph_test_")
FAILS = []
PASSES = [0]


# ------------------------------------------------------------ evaluator
def _vec(v):
    if isinstance(v, (int, float)):
        return (float(v),) * 3
    return tuple(float(c) for c in tuple(v)[:3])


def _num(v):
    if isinstance(v, (int, float)):
        return float(v)
    v = tuple(v)[:3]
    return sum(v) / 3.0


def ev_input(sock, ctx):
    if sock.is_linked:
        link = sock.links[0]
        v = ev(link.from_socket, ctx)
    else:
        v = sock.default_value
        v = float(v) if isinstance(v, (int, float)) else tuple(v)
    return _num(v) if sock.type == 'VALUE' else _vec(v)


def _safe_pow(a, b):
    if a >= 0:
        return a ** b
    return a ** b if float(b).is_integer() else 0.0


MATH = {
    'ADD': lambda a, b, c: a + b, 'SUBTRACT': lambda a, b, c: a - b, 'MULTIPLY': lambda a, b, c: a * b,
    'DIVIDE': lambda a, b, c: a / b if b else 0.0, 'MULTIPLY_ADD': lambda a, b, c: a * b + c,
    'POWER': lambda a, b, c: _safe_pow(a, b),
    'LOGARITHM': lambda a, b, c: math.log(a) / math.log(b) if a > 0 and b > 0 and b != 1 else 0.0,
    'SQRT': lambda a, b, c: math.sqrt(a) if a > 0 else 0.0, 'ABSOLUTE': lambda a, b, c: abs(a),
    'EXPONENT': lambda a, b, c: math.exp(a), 'MINIMUM': lambda a, b, c: min(a, b),
    'MAXIMUM': lambda a, b, c: max(a, b), 'LESS_THAN': lambda a, b, c: 1.0 if a < b else 0.0,
    'GREATER_THAN': lambda a, b, c: 1.0 if a > b else 0.0,
    'SIGN': lambda a, b, c: (a > 0) - (a < 0),
    'COMPARE': lambda a, b, c: 1.0 if abs(a - b) <= max(c, 1e-5) else 0.0,
    'ROUND': lambda a, b, c: math.floor(a + 0.5), 'FLOOR': lambda a, b, c: math.floor(a),
    'CEIL': lambda a, b, c: math.ceil(a), 'TRUNC': lambda a, b, c: float(math.trunc(a)),
    'FRACT': lambda a, b, c: a - math.floor(a), 'MODULO': lambda a, b, c: math.fmod(a, b) if b else 0.0,
    'FLOORED_MODULO': lambda a, b, c: a - math.floor(a / b) * b if b else 0.0,
    'SINE': lambda a, b, c: math.sin(a), 'COSINE': lambda a, b, c: math.cos(a),
    'TANGENT': lambda a, b, c: math.tan(a),
    'ARCSINE': lambda a, b, c: math.asin(max(-1.0, min(1.0, a))),
    'ARCCOSINE': lambda a, b, c: math.acos(max(-1.0, min(1.0, a))),
    'ARCTANGENT': lambda a, b, c: math.atan(a), 'ARCTAN2': lambda a, b, c: math.atan2(a, b),
}


def _cw(f, *vs):
    return tuple(f(*cs) for cs in zip(*vs))


def ev(sock, ctx=()):
    node = sock.node
    k = node.bl_idname

    def i(idx):
        return ev_input(node.inputs[idx], ctx)

    if k == "ShaderNodeValue":
        return float(node.outputs[0].default_value)
    if k == "ShaderNodeMath":
        v = float(MATH[node.operation](i(0), i(1), i(2)))
        return max(0.0, min(1.0, v)) if node.use_clamp else v
    if k == "ShaderNodeVectorMath":
        op, a, b = node.operation, i(0), i(1)
        if op == 'SCALE':
            s = ev_input(node.inputs["Scale"], ctx)
            return tuple(c * s for c in a)
        if op == 'DOT_PRODUCT':
            return sum(x * y for x, y in zip(a, b))
        if op == 'LENGTH':
            return math.sqrt(sum(x * x for x in a))
        if op == 'DISTANCE':
            return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))
        if op == 'NORMALIZE':
            n = math.sqrt(sum(x * x for x in a))
            return tuple(x / n for x in a) if n else (0.0, 0.0, 0.0)
        if op == 'CROSS_PRODUCT':
            return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])
        if op == 'MULTIPLY_ADD':
            return _cw(lambda x, y, z: x * y + z, a, b, i(2))
        scalar = {'ADD': 'ADD', 'SUBTRACT': 'SUBTRACT', 'MULTIPLY': 'MULTIPLY', 'DIVIDE': 'DIVIDE',
                  'MINIMUM': 'MINIMUM', 'MAXIMUM': 'MAXIMUM', 'FLOOR': 'FLOOR', 'CEIL': 'CEIL',
                  'FRACTION': 'FRACT', 'ABSOLUTE': 'ABSOLUTE', 'MODULO': 'MODULO', 'POWER': 'POWER',
                  'SIGN': 'SIGN', 'ROUND': 'ROUND', 'SINE': 'SINE', 'COSINE': 'COSINE', 'TANGENT': 'TANGENT'}[op]
        f = MATH[scalar]
        return _cw(lambda x, y: float(f(x, y, 0.0)), a, b)
    if k == "ShaderNodeMix":
        if node.data_type == 'FLOAT':
            t, a, b = i(0), i(2), i(3)
            if node.clamp_factor:
                t = max(0.0, min(1.0, t))
            return a + (b - a) * t
        a, b = i(4), i(5)
        t = i(1) if node.factor_mode == 'NON_UNIFORM' else (i(0),) * 3
        return _cw(lambda x, y, s: x + (y - x) * s, a, b, t)
    if k == "ShaderNodeClamp":
        v, lo, hi = i(0), i(1), i(2)
        return min(max(v, lo), hi) if node.clamp_type == 'MINMAX' else min(max(v, min(lo, hi)), max(lo, hi))
    if k == "ShaderNodeCombineXYZ":
        return (i(0), i(1), i(2))
    if k == "ShaderNodeSeparateXYZ":
        return i(0)["XYZ".index(sock.name)]
    if k == "ShaderNodeSeparateColor":
        c = i(0)
        c = colorsys.rgb_to_hsv(*c) if node.mode == 'HSV' else c
        return c[list(node.outputs).index(sock)]
    if k == "ShaderNodeCombineColor":
        c = (i(0), i(1), i(2))
        return colorsys.hsv_to_rgb(*c) if node.mode == 'HSV' else c
    if k == "ShaderNodeMapRange":
        def one(v, a, b, c, d):
            t = (v - a) / (b - a) if b != a else 0.0
            if node.clamp:
                t = max(0.0, min(1.0, t))
            if node.interpolation_type == 'SMOOTHSTEP':
                t = t * t * (3 - 2 * t)
            return c + (d - c) * t
        if node.data_type == 'FLOAT':
            return one(*(i(j) for j in range(5)))
        return _cw(one, i(6), i(7), i(8), i(9), i(10))
    if k == "ShaderNodeTexImage":
        px = tuple(node.image.pixels[:4])
        return px[3] if sock.name == "Alpha" else px[:3]
    if k == "NodeEvaluateClosure":
        cout, cctx = _closure_of(node.inputs[0], ctx)
        return ev_input(next(s for s in cout.inputs if s.name == sock.name), cctx)
    if k == "NodeClosureInput":
        return (0.5, 0.5, 0.0)      # the zone's argument: the 1x1 test images don't read it
    if k == "ShaderNodeGroup":
        go = next(n for n in node.node_tree.nodes if n.bl_idname == "NodeGroupOutput")
        inner = next(s for s in go.inputs if s.name == sock.name)
        return ev_input(inner, ctx + (node,))
    if k == "NodeGroupInput":
        return ev_input(ctx[-1].inputs[sock.name], ctx[:-1])
    raise RuntimeError("evaluator: no %s" % k)


def _closure_of(sock, ctx):
    """The Closure Output node a closure socket's link comes from, and the group context it's in."""
    link = sock.links[0]
    if link.from_node.bl_idname == "NodeGroupInput":
        return _closure_of(ctx[-1].inputs[link.from_socket.name], ctx[:-1])
    return link.from_node, ctx


def value(v):
    """A translated Val as numbers: float, or tuple of its width (4 with alpha)."""
    def one(x):
        return (float(x.s) if x.w == 1 else tuple(x.s)) if x.const else ev(x.s)
    r = one(v)
    if v.w == 1:
        return _num(r)
    r = _vec(r)[:min(v.w, 3)]
    if v.w == 4:
        r = r + ((one(v.a) if v.a is not None else float("nan")),)
    return r


# ------------------------------------------------------------ graphs
class G:
    def __init__(self):
        self.nodes, self.n = [], 0

    def add(self, t, _name=None, _outer=None, **props):
        self.n += 1
        name = _name or "MaterialExpression%s_%d" % (t, self.n)
        x = {"Type": "MaterialExpression" + t, "Name": name, "Properties": props}
        if _outer:
            x["Outer"] = {"ObjectName": _outer}
        self.nodes.append(x)
        return name

    def save(self, fname, out=None, pin="EmissiveColor"):
        nodes = list(self.nodes)
        if out is not None:
            nodes.append({"Type": "MaterialEditorOnlyData", "Name": "MaterialEditorOnlyData_0",
                          "Properties": {pin: out if isinstance(out, dict) else R(out)}})
        path = os.path.join(TMP, fname + ".json")
        json.dump(nodes, open(path, "w", encoding="utf-8"))
        return path


def R(name, out=0, mask=None):
    r = {"ExpressionName": name, "OutputIndex": out}
    if mask:
        r.update({"Mask": 1, "MaskR": int("R" in mask), "MaskG": int("G" in mask),
                  "MaskB": int("B" in mask), "MaskA": int("A" in mask)})
    return r


def scalar(g, v):
    """A parameter: a socket in the translated graph, not a constant."""
    return g.add("ScalarParameter", ParameterName="p%d" % (g.n + 1), DefaultValue=v)


def vector(g, rgba):
    return g.add("VectorParameter", ParameterName="v%d" % (g.n + 1),
                 DefaultValue={"R": rgba[0], "G": rgba[1], "B": rgba[2], "A": rgba[3] if len(rgba) > 3 else 1.0})


def const3(g, v):
    return g.add("Constant3Vector", Constant={"R": v[0], "G": v[1], "B": v[2], "A": 0.0})


def function(name, build):
    """A material function graph: build(g) returns the FunctionOutput's source."""
    g = G()
    src = build(g)
    g.add("FunctionOutput", Id="OUT_" + name, OutputName="Result", A=R(src))
    return g.save(name)


def fn_input(g, name, kind="Scalar", preview=0.0):
    return g.add("FunctionInput", Id="IN_" + name, InputName=name,
                 InputType="EFunctionInputType::FunctionInput_" + kind,
                 PreviewValue={"X": preview, "Y": 0.0, "Z": 0.0, "W": 0.0}, bUsePreviewValueAsDefault=True)


def call(g, fname, inputs):
    return g.add("MaterialFunctionCall",
                 MaterialFunction={"ObjectName": "MaterialFunction'%s'" % fname, "ObjectPath": "/Test/%s.0" % fname},
                 FunctionInputs=[{"ExpressionInputId": "IN_" + k, "Input": R(v)} for k, v in inputs.items()],
                 FunctionOutputs=[{"ExpressionOutputId": "OUT_" + fname}])


# ------------------------------------------------------------ env
class TestEnv:
    nest_functions = True
    function_prefix = "_t - "

    def __init__(self, **over):
        self.h = [None]
        self.over = over

    def current(self):
        return self.h[0]

    def set_current(self, tr):
        self.h[0] = tr

    @property
    def tr(self):
        return self.h[0]

    def _socket(self, v):
        if isinstance(v, (int, float)):
            n = self.tr.node("ShaderNodeValue", "param")
            n.outputs[0].default_value = v
            return Val(n.outputs[0], 1)
        n = self.tr.node("ShaderNodeCombineXYZ", "param")
        for k, c in enumerate(v[:3]):
            n.inputs[k].default_value = c
        return Val(n.outputs[0], 3)

    def scalar(self, name, default):
        return self._socket(float(default))

    def vector(self, name, rgba):
        return self._socket(tuple(rgba[:3])), self._socket(float(rgba[3]))

    def static_switch(self, name, default):
        return self.over.get(name, default)

    def camera_vector(self):
        return self.tr.const(self.over.get("camera", (0.6, 0.0, 0.8)), 3)

    def camera_position(self):
        return self.tr.const((0.0, 0.0, 0.0), 3)

    def world_position(self, camera_relative=False):
        return self.tr.const(self.over.get("world", (0.0, 0.0, 0.0)), 3)

    def pixel_depth(self):
        return self._socket(float(self.over["pixel_depth"])) if "pixel_depth" in self.over else None

    def water_depth(self):
        return self._socket(float(self.over["water_depth"])) if "water_depth" in self.over else None

    def water_flow(self):
        return self.tr.const(self.over["flow"], 2) if "flow" in self.over else None

    def vertex_color(self):
        return self.tr.const(self.over.get("vertex_color", (1.0, 1.0, 1.0)), 3), self.tr.const(1.0)

    def actor_position(self, camera_relative=False):
        return self.tr.const((0.0, 0.0, 0.0), 3)

    def vertex_normal(self):
        return self.tr.const(self.over.get("normal", (0.0, 0.0, 1.0)), 3)

    def vertex_tangent(self):
        return self.tr.const(self.over.get("tangent", (1.0, 0.0, 0.0)), 3)

    def time(self):
        return self._socket(float(self.over.get("time", 1.5)))

    def uv(self, index):
        return self.tr.const((0.25, 0.75), 2)

    def light_direction(self):
        return self.tr.const(self.over.get("light", (0.0, 0.6, 0.8)), 3)

    def local_bounds(self):
        return self.tr.const((-10.0, -20.0, 0.0), 3), self.tr.const((30.0, 40.0, 180.0), 3)

    def texture_slices(self, name, sampler):
        """"TA_..." names are arrays of three slices, 0.1 / 0.2 / 0.3 grey."""
        if not name.startswith("TA_"):
            return None
        return [self.texture("T_%.1f_%.1f_%.1f" % (k, k, k), sampler) for k in (0.1, 0.2, 0.3)]

    @property
    def texture_closures(self):
        return self.over.get("closures", False)

    def texture_address(self, name):
        """A texture's addressing, from tags after its colour: "T_1_1_1_CX" clamps X, "MY" mirrors Y."""
        tags = name.split("_")[4:]
        return tuple("Clamp" if "C" + a in tags else "Mirror" if "M" + a in tags else "Wrap" for a in "XY")

    def texture(self, name, sampler):
        """A 1x1 image of the colour the texture's name spells: "T_0.75_0.25_0.5"."""
        img = bpy.data.images.get("_t " + name)
        if img is None:
            c = [float(x) for x in name.split("_")[1:4]] + [1.0]
            img = bpy.data.images.new("_t " + name, 1, 1, float_buffer=True)
            img.colorspace_settings.name = "Non-Color"
            img.pixels[:] = c
        return img

    def function(self, name, objpath=None):
        p = os.path.join(TMP, name + ".json")
        return p if os.path.exists(p) else None

    def special_function(self, name, inputs):
        return None

    def group_input(self, name):
        return self.tr.const(0.0)


def run(g, out, **over):
    tree = bpy.data.node_groups.new("_test", "ShaderNodeTree")
    env = TestEnv(**over)
    tr = Translator(tree, env)
    env.h[0] = tr
    path = g.save("M_%d" % id(g), out)
    v = tr.material_output(path, "EmissiveColor")
    warnings = list(tr.warnings) + [w for ft in tr.functions.values() for w in ft.tr.warnings]
    return value(v), warnings, tr


def run_val(g, out, **over):
    """(the translated Val, the translator): for checks on the tree after it's built."""
    tree = bpy.data.node_groups.new("_test", "ShaderNodeTree")
    env = TestEnv(**over)
    tr = Translator(tree, env)
    env.h[0] = tr
    v = tr.material_output(g.save("M_%d" % id(g), out), "EmissiveColor")
    if not v.const:
        # what reads the result: a node no one reads is dead to merge_duplicates
        go = tree.nodes.new("NodeGroupOutput")
        tree.interface.new_socket("Out", in_out='OUTPUT', socket_type='NodeSocketFloat' if v.w == 1 else 'NodeSocketVector')
        tree.links.new(v.s, go.inputs[0])
    return v, tr


def count(tree, kind):
    return sum(1 for n in tree.nodes if n.bl_idname == kind)


def run_attrs(g, out, **over):
    """A Material Attributes material: its attributes as numbers."""
    tree = bpy.data.node_groups.new("_test", "ShaderNodeTree")
    env = TestEnv(**over)
    tr = Translator(tree, env)
    env.h[0] = tr
    path = g.save("M_%d" % id(g), out, pin="MaterialAttributes")
    vals = tr.material_attributes(path)
    return {k: value(v) for k, v in vals.items()}, tr


def check(name, got, want, tol=1e-4):
    ok = False
    try:
        if isinstance(want, tuple):
            ok = len(got) == len(want) and all(abs(a - b) <= tol for a, b in zip(got, want))
        else:
            ok = abs(got - want) <= tol
    except TypeError:
        ok = False
    if ok:
        PASSES[0] += 1
    else:
        FAILS.append("%s: got %r, want %r" % (name, got, want))
        print("FAIL", FAILS[-1])


def expr(build, **over):
    g = G()
    out = build(g)
    v, w, _ = run(g, out, **over)
    return v, w


# ------------------------------------------------------------ tests
# Clamp: default 0..1, the Min/MaxDefault, one-sided modes, vector input
check("Clamp default", expr(lambda g: g.add("Clamp", Input=R(scalar(g, 1.7))))[0], 1.0)
check("Clamp defaults 0.2..0.8", expr(lambda g: g.add("Clamp", Input=R(scalar(g, -1.0)), MinDefault=0.2, MaxDefault=0.8))[0], 0.2)
check("Clamp min only", expr(lambda g: g.add("Clamp", Input=R(scalar(g, 5.0)), ClampMode="EClampMode::CMODE_ClampMin"))[0], 5.0)
check("Clamp max only", expr(lambda g: g.add("Clamp", Input=R(scalar(g, -3.0)), ClampMode="EClampMode::CMODE_ClampMax"))[0], -3.0)
check("Clamp vector", expr(lambda g: g.add("Clamp", Input=R(const3(g, (-1.0, 0.5, 2.0)))))[0], (0.0, 0.5, 1.0))


# If: A >= B picks Greater; the Equals pin within the threshold; ConstB
def if_graph(a, b, with_eq, const_b=None):
    def build(g):
        props = dict(A=R(scalar(g, a)), AGreaterThanB=R(g.add("Constant", R=1.0)), ALessThanB=R(g.add("Constant", R=2.0)))
        if const_b is None:
            props["B"] = R(scalar(g, b))
        else:
            props["ConstB"] = const_b
        if with_eq:
            props["AEqualsB"] = R(g.add("Constant", R=3.0))
        return g.add("If", **props)
    return expr(build)[0]


check("If A=B no equals pin", if_graph(0.3, 0.3, False), 1.0)
check("If A=B equals pin", if_graph(0.3, 0.3, True), 3.0)
check("If A>B", if_graph(0.5, 0.3, True), 1.0)
check("If A<B", if_graph(0.1, 0.3, True), 2.0)
check("If ConstB", if_graph(0.1, None, False, const_b=0.5), 2.0)
check("If B default 0", if_graph(-0.1, None, False, const_b=None) if False else
      expr(lambda g: g.add("If", A=R(scalar(g, -0.1)), AGreaterThanB=R(g.add("Constant", R=1.0)),
                           ALessThanB=R(g.add("Constant", R=2.0))))[0], 2.0)
# a branch giving a NaN (a shell layer UE culls) isn't taken: the equal branch stands in
NAN_CODE = "const float fNaN = 0.0f / 0.0f;\nreturn fNaN;"
check("If NaN branch not taken",
      expr(lambda g: g.add("If", A=R(scalar(g, 0.9)), B=R(g.add("Constant", R=0.5)),
                           AGreaterThanB=R(g.add("Custom", Code=NAN_CODE, OutputType="ECustomMaterialOutputType::CMOT_Float1", Inputs=[])),
                           AEqualsB=R(g.add("Constant", R=3.0)), ALessThanB=R(g.add("Constant", R=3.0))))[0], 3.0)
# GGXSpecular (engine): GGX's distribution term, NoH = 1, roughness 0.5: 1 / (pi * 0.0625)
GGX_CODE = ("float NoH = dot( normalize(Normal + LightVector), Normal); float a2 = Pow4( Roughness ); "
            "float d = ( NoH * a2 - NoH ) * NoH + 1; // 2 mad return a2 / ( PI*d*d ); // 4 mul, 1 rcp")
check("GGX distribution",
      expr(lambda g: g.add("Custom", Code=GGX_CODE, OutputType="ECustomMaterialOutputType::CMOT_Float1", Inputs=[
          {"InputName": "Normal", "Input": R(vector(g, (0.0, 0.0, 1.0, 1.0)))},
          {"InputName": "LightVector", "Input": R(vector(g, (0.0, 0.0, 1.0, 1.0)))},
          {"InputName": "Roughness", "Input": R(scalar(g, 0.5))}]))[0], 1.0 / (math.pi * 0.0625), tol=1e-3)
# MF_FPMeshCameraOffset: the third person's offset unless FP is set
FP_CODE = "if(FP)\n{\n return WPO_FP;\n}\nreturn WPO;"
for fp, want in ((0.0, 2.0), (1.0, 7.0)):
    check("FP select %g" % fp,
          expr(lambda g: g.add("Custom", Code=FP_CODE, OutputType="ECustomMaterialOutputType::CMOT_Float1", Inputs=[
              {"InputName": "FP", "Input": R(scalar(g, fp))}, {"InputName": "WPO", "Input": R(scalar(g, 2.0))},
              {"InputName": "WPO_FP", "Input": R(scalar(g, 7.0))}]))[0], want)


# Static switches: defaults, instance overrides, a StaticBool feeding one
def sw(g, a=10.0, b=20.0, **props):
    return g.add("StaticSwitchParameter", ParameterName="UseA", A=R(g.add("Constant", R=a)),
                 B=R(g.add("Constant", R=b)), **props)


check("StaticSwitchParameter default false", expr(lambda g: sw(g))[0], 20.0)
check("StaticSwitchParameter default true", expr(lambda g: sw(g, DefaultValue=True))[0], 10.0)
check("StaticSwitchParameter override", expr(lambda g: sw(g, DefaultValue=True), UseA=False)[0], 20.0)
check("StaticSwitch from StaticBoolParameter", expr(lambda g: g.add(
    "StaticSwitch", A=R(g.add("Constant", R=1.0)), B=R(g.add("Constant", R=2.0)),
    Value=R(g.add("StaticBoolParameter", ParameterName="Flag"))), Flag=True)[0], 1.0)
check("StaticSwitch unlinked value", expr(lambda g: g.add(
    "StaticSwitch", A=R(g.add("Constant", R=1.0)), B=R(g.add("Constant", R=2.0)), DefaultValue=True))[0], 1.0)

# a function with a static bool input: one group per value it's called with
function("MF_Pick", lambda g: g.add("StaticSwitch", Value=R(fn_input(g, "UseX", "StaticBool")),
                                    A=R(g.add("Constant", R=10.0)), B=R(g.add("Constant", R=20.0))))


def two_calls(g):
    on = call(g, "MF_Pick", {"UseX": g.add("StaticBool", Value=True)})
    off = call(g, "MF_Pick", {})
    return g.add("Add", A=R(on), B=R(off))


v, w, tr = run(*(lambda g: (g, two_calls(g)))(G()))
check("function static variants", v, 30.0)
names = sorted(ft.tree.name for ft in tr.functions.values())
if names != ["_t - Pick", "_t - Pick [+UseX]"]:
    FAILS.append("variant groups: %r" % names)
else:
    PASSES[0] += 1

# static bools passed down through a second function
function("MF_Outer", lambda g: call(g, "MF_Pick", {"UseX": fn_input(g, "Flag", "StaticBool")}))
check("static bool through two functions", expr(lambda g: call(g, "MF_Outer", {"Flag": g.add("StaticBool", Value=True)}))[0], 10.0)
check("static bool preview default", expr(lambda g: call(g, "MF_Outer", {}))[0], 20.0)

# compile-time switches take the shipping branch
check("QualitySwitch Epic", expr(lambda g: g.add("QualitySwitch", Default=R(g.add("Constant", R=1.0)),
                                                 **{"Inputs[3]": R(g.add("Constant", R=4.0))}))[0], 4.0)
check("FeatureLevelSwitch default without SM6", expr(lambda g: g.add(
    "FeatureLevelSwitch", Default=R(g.add("Constant", R=1.0)), **{"Inputs[1]": R(g.add("Constant", R=5.0))}))[0], 1.0)
check("ShadingPathSwitch deferred", expr(lambda g: g.add(
    "ShadingPathSwitch", Default=R(g.add("Constant", R=1.0)), Inputs=R(g.add("Constant", R=6.0))))[0], 6.0)
check("ShaderStageSwitch pixel", expr(lambda g: g.add(
    "ShaderStageSwitch", PixelShader=R(g.add("Constant", R=7.0)), VertexShader=R(g.add("Constant", R=8.0))))[0], 7.0)


# Switch: floor(value) picks, out of range Default
def switch(val_node):
    def build(g):
        items = [{"InputName": "i%d" % k, "Input": R(g.add("Constant", R=10.0 * (k + 1)))} for k in range(3)]
        return g.add("Switch", SwitchValue=R(val_node(g)), Inputs=items, Default=R(g.add("Constant", R=99.0)))
    return expr(build)[0]


check("Switch 1.7", switch(lambda g: scalar(g, 1.7)), 20.0)
check("Switch out of range", switch(lambda g: scalar(g, 5.0)), 99.0)
check("Switch constant", switch(lambda g: g.add("Constant", R=2.0)), 30.0)

# masks
check("ChannelMaskParameter blue", expr(lambda g: g.add(
    "ChannelMaskParameter", ParameterName="Ch", MaskChannel="EChannelMaskParameterColor::Blue",
    Input=R(g.add("Constant4Vector", Constant={"R": 1.0, "G": 2.0, "B": 3.0, "A": 4.0}))))[0], 3.0)
check("ChannelMaskParameter alpha", expr(lambda g: g.add(
    "ChannelMaskParameter", ParameterName="Ch", MaskChannel="EChannelMaskParameterColor::Alpha",
    Input=R(g.add("Constant4Vector", Constant={"R": 1.0, "G": 2.0, "B": 3.0, "A": 4.0}))))[0], 4.0)
check("StaticComponentMaskParameter GA", expr(lambda g: g.add(
    "StaticComponentMaskParameter", ParameterName="M", DefaultG=True, DefaultA=True,
    Input=R(g.add("Constant4Vector", Constant={"R": 1.0, "G": 2.0, "B": 3.0, "A": 4.0}))))[0], (2.0, 4.0))

# Convert (UE 5.6's Break Float3 / Make Float2): outputs' components from the inputs' by ConvertMappings, else the default
CT = "EMaterialExpressionConvertType::"


def convert(g, ins, outs, maps):
    return g.add("Convert",
                 ConvertInputs=[{"ExpressionInput": R(n), "Type": CT + t, "DefaultValue": {"R": 0.0, "G": 0.0, "B": 0.0, "A": 1.0}} for n, t in ins],
                 ConvertOutputs=[{"Type": CT + t, "DefaultValue": d} for t, d in outs],
                 ConvertMappings=[{"InputIndex": a, "InputComponentIndex": b, "OutputIndex": c, "OutputComponentIndex": d} for a, b, c, d in maps])


def break3(g, out):
    c = convert(g, [(const3(g, (0.1, 0.2, 0.3)), "Vector3")], [("Scalar", {})] * 3, [(0, 0, 0, 0), (0, 1, 1, 0), (0, 2, 2, 0)])
    return R(c, out)


check("Convert break float3 G", expr(lambda g: break3(g, 1))[0], 0.2)
check("Convert break float3 B", expr(lambda g: break3(g, 2))[0], 0.3)
check("Convert make float2", expr(lambda g: R(convert(
    g, [(scalar(g, 0.7), "Scalar"), (scalar(g, 0.4), "Scalar")], [("Vector2", {})], [(0, 0, 0, 0), (1, 0, 0, 1)])))[0][:2], (0.7, 0.4))
check("Convert default fills unmapped", expr(lambda g: R(convert(
    g, [(scalar(g, 0.6), "Scalar")], [("Vector3", {"R": 0.0, "G": 0.5, "B": 0.25})], [(0, 0, 0, 0)])))[0], (0.6, 0.5, 0.25))

# plain maths
check("Fmod negative", expr(lambda g: g.add("Fmod", A=R(scalar(g, -5.5)), B=R(scalar(g, 2.0))))[0], -1.5)
check("Round vector", expr(lambda g: g.add("Round", Input=R(vector(g, (-1.6, 2.4, 0.4)))))[0], (-2.0, 2.0, 0.0))
check("Sign vector", expr(lambda g: g.add("Sign", Input=R(vector(g, (-1.6, 0.0, 2.0)))))[0], (-1.0, 0.0, 1.0))
check("Truncate vector", expr(lambda g: g.add("Truncate", Input=R(vector(g, (-1.6, 2.4, 0.4)))))[0], (-1.0, 2.0, 0.0))
check("Cosine period 4", expr(lambda g: g.add("Cosine", Input=R(scalar(g, 0.5)), Period=4.0))[0], math.cos(math.pi / 4))
check("Tangent period 360", expr(lambda g: g.add("Tangent", Input=R(scalar(g, 45.0)), Period=360.0))[0], 1.0)
check("Arccosine", expr(lambda g: g.add("Arccosine", Input=R(scalar(g, 0.5))))[0], math.pi / 3)
check("Exponential2", expr(lambda g: g.add("Exponential2", Input=R(scalar(g, 3.0))))[0], 8.0)
check("Logarithm2", expr(lambda g: g.add("Logarithm2", X=R(scalar(g, 8.0))))[0], 3.0)
check("Length float4", expr(lambda g: g.add("Length", Input=R(g.add(
    "Constant4Vector", Constant={"R": 1.0, "G": 2.0, "B": 2.0, "A": 4.0}))))[0], 5.0)
check("DeriveNormalZ", expr(lambda g: g.add("DeriveNormalZ", InXY=R(vector(g, (0.6, 0.0, 0.0)), mask="RG")))[0],
      (0.6, 0.0, 0.8))
check("MaterialXRemap", expr(lambda g: g.add("MaterialXRemap", Input=R(scalar(g, 0.5)), InputHighDefault=2.0,
                                             TargetLowDefault=10.0, TargetHighDefault=20.0))[0], 12.5)
check("Desaturation half", expr(lambda g: g.add("Desaturation", Input=R(vector(g, (1.0, 0.5, 0.25))),
                                                Fraction=R(scalar(g, 0.5))))[0], (0.81125, 0.56125, 0.43625))
check("Desaturation full", expr(lambda g: g.add("Desaturation", Input=R(vector(g, (1.0, 0.5, 0.25)))))[0], 0.6225)
c = (0.8, 0.3, 0.1)
check("HSV round trip", expr(lambda g: g.add("HsvToRgb", Input=R(g.add("RgbToHsv", Input=R(vector(g, c))))))[0], c)

# Panner: speeds, a speed vector, the fractional part
check("Panner", expr(lambda g: g.add("Panner", Coordinate=R(g.add("TextureCoordinate")), SpeedX=0.5, SpeedY=-0.25))[0],
      (1.0, 0.375))
check("Panner fractional", expr(lambda g: g.add("Panner", Coordinate=R(g.add("TextureCoordinate")), SpeedX=0.5,
                                                SpeedY=-0.25, bFractionalPart=True))[0], (1.0, 1.375))
check("Panner speed vector", expr(lambda g: g.add("Panner", Coordinate=R(g.add("TextureCoordinate")),
                                                  Speed=R(vector(g, (0.1, 0.2, 0.0)), mask="RG")))[0], (0.4, 1.05))

# Fresnel: pow(1 - max(0, N.C), E) (1 - F0) + F0
check("Fresnel default", expr(lambda g: g.add("Fresnel", Normal=R(vector(g, (0.6, 0.0, 0.8)))), camera=(0.0, 0.0, 1.0))[0],
      0.2 ** 5 * 0.96 + 0.04)
check("Fresnel pixel normal E2 F0 0", expr(lambda g: g.add("Fresnel", Exponent=2.0, BaseReflectFraction=0.0),
                                           camera=(0.6, 0.0, 0.8))[0], 0.2 ** 2)


# RotateAboutAxis against UE's Common.ush
def rotate_ref(axis, ang, pivot, pos):
    along = sum(a * (p - q) for a, p, q in zip(axis, pos, pivot))
    closest = tuple(q + a * along for q, a in zip(pivot, axis))
    u = tuple(p - c for p, c in zip(pos, closest))
    v = (axis[1] * u[2] - axis[2] * u[1], axis[2] * u[0] - axis[0] * u[2], axis[0] * u[1] - axis[1] * u[0])
    r = tuple(x * math.cos(ang) + y * math.sin(ang) for x, y in zip(u, v))
    return tuple(c + x - p for c, x, p in zip(closest, r, pos))


axis = (1 / 3.0, 2 / 3.0, 2 / 3.0)
check("RotateAboutAxis", expr(lambda g: g.add(
    "RotateAboutAxis", NormalizedRotationAxis=R(vector(g, axis)), RotationAngle=R(scalar(g, 0.1)),
    PivotPoint=R(vector(g, (10.0, -5.0, 3.0))), Position=R(vector(g, (40.0, 20.0, -7.0)))))[0],
    rotate_ref(axis, 0.1 * 2 * math.pi, (10.0, -5.0, 3.0), (40.0, 20.0, -7.0)))
check("RotateAboutAxis quarter turn", expr(lambda g: g.add(
    "RotateAboutAxis", NormalizedRotationAxis=R(const3(g, (0.0, 0.0, 1.0))), RotationAngle=R(scalar(g, 0.25)),
    PivotPoint=R(const3(g, (0.0, 0.0, 0.0))), Position=R(vector(g, (100.0, 0.0, 50.0)))))[0], (-100.0, 100.0, 0.0))

# tangent space: the basis, and a round trip through a tilted frame
check("Transform tangent to world", expr(lambda g: g.add("Transform", Input=R(vector(g, (0.2, 0.3, 0.9)))))[0],
      (0.2, 0.3, 0.9))
s2, s3 = 1 / math.sqrt(2), 1 / math.sqrt(3)
tilted = dict(normal=(s3, s3, s3), tangent=(s2, -s2, 0.0))
check("Transform tangent Z is the normal", expr(lambda g: g.add("Transform", Input=R(const3(g, (0.0, 0.0, 1.0)))),
                                                **tilted)[0], (s3, s3, s3))
check("Transform tangent X is the tangent", expr(lambda g: g.add("Transform", Input=R(const3(g, (1.0, 0.0, 0.0)))),
                                                 **tilted)[0], (s2, -s2, 0.0))
check("Transform tangent round trip", expr(lambda g: g.add(
    "Transform", TransformSourceType="EMaterialVectorCoordTransformSource::TRANSFORMSOURCE_World",
    TransformType="EMaterialVectorCoordTransform::TRANSFORM_Tangent",
    Input=R(g.add("Transform", Input=R(vector(g, (0.2, -0.5, 0.7)))))), **tilted)[0], (0.2, -0.5, 0.7))
check("BumpOffset", expr(lambda g: g.add("BumpOffset", Height=R(scalar(g, 0.7))))[0], (0.256, 0.75))
check("ReflectionVectorWS", expr(lambda g: g.add("ReflectionVectorWS"))[0], (-0.6, 0.0, 0.8))

# IfThenElse: a static condition picks at compile time, a runtime one per pixel
check("IfThenElse static", expr(lambda g: g.add("IfThenElse", Condition=R(g.add("StaticBool", Value=True)),
                                                **{"True": R(g.add("Constant", R=1.0)),
                                                   "False": R(g.add("Constant", R=2.0))}))[0], 1.0)
check("IfThenElse runtime", expr(lambda g: g.add("IfThenElse", Condition=R(scalar(g, 0.0)),
                                                 **{"True": R(g.add("Constant", R=1.0)),
                                                    "False": R(g.add("Constant", R=2.0))}))[0], 2.0)


# a branch whose selector is a constant: the side not taken is never built (no Sine node)
def pruned(name, build, want):
    g = G()
    dead = lambda: R(g.add("Sine", Input=R(scalar(g, 0.3))))
    v, _w, tr = run(g, build(g, dead))
    check(name, v, want)
    check(name + ": dead side not built", sum(1 for n in tr.tree.nodes
                                              if n.bl_idname == "ShaderNodeMath" and n.operation == 'SINE'), 0)


pruned("If const A >= B", lambda g, dead: g.add("If", A=R(g.add("Constant", R=0.9)), B=R(g.add("Constant", R=0.5)),
                                                AGreaterThanB=R(g.add("Constant", R=1.0)), ALessThanB=dead(),
                                                AEqualsB=dead()), 1.0)
pruned("If const A = B", lambda g, dead: g.add("If", A=R(g.add("Constant", R=0.5)), B=R(g.add("Constant", R=0.5)),
                                               AGreaterThanB=dead(), ALessThanB=dead(),
                                               AEqualsB=R(g.add("Constant", R=3.0))), 3.0)
pruned("IfThenElse const", lambda g, dead: g.add("IfThenElse", Condition=R(g.add("Constant", R=1.0)),
                                                 **{"True": R(g.add("Constant", R=4.0)), "False": dead()}), 4.0)
pruned("Lerp alpha 1", lambda g, dead: g.add("LinearInterpolate", A=dead(), B=R(const3(g, (0.1, 0.2, 0.3))),
                                             Alpha=R(g.add("Constant", R=1.0))), (0.1, 0.2, 0.3))
pruned("Lerp alpha 0", lambda g, dead: g.add("LinearInterpolate", A=R(const3(g, (0.6, 0.5, 0.4))), B=dead(),
                                             Alpha=R(g.add("Constant", R=0.0))), (0.6, 0.5, 0.4))
pruned("Switch const", lambda g, dead: g.add("Switch", SwitchValue=R(g.add("Constant", R=1.0)), Default=dead(),
                                             Inputs=[{"InputName": "a", "Input": dead()},
                                                     {"InputName": "b", "Input": R(g.add("Constant", R=7.0))}]), 7.0)
# a float1 side taken against a float3 one still comes out a float3 (UE's broadcast)
check("Lerp alpha 1, float1 against float3",
      expr(lambda g: g.add("LinearInterpolate", A=R(const3(g, (0.1, 0.2, 0.3))), B=R(g.add("Constant", R=0.25)),
                           Alpha=R(g.add("Constant", R=1.0))))[0], (0.25, 0.25, 0.25))
check("PerInstanceCustomData default", expr(lambda g: g.add("PerInstanceCustomData", DataIndex=3,
                                                            ConstDefaultValue=0.5))[0], 0.5)
check("PerInstanceCustomData default pin", expr(lambda g: g.add(
    "PerInstanceCustomData", DataIndex=3, DefaultValue=R(g.add("Constant", R=7.0))))[0], 7.0)
check("PerInstanceCustomData3Vector default", expr(lambda g: g.add(
    "PerInstanceCustomData3Vector", DataIndex=0, ConstDefaultValue={"R": 0.1, "G": 0.2, "B": 0.3, "A": 1.0}))[0], (0.1, 0.2, 0.3))
check("VertexInterpolator", expr(lambda g: g.add("VertexInterpolator", Input=R(scalar(g, 0.3))))[0], 0.3)

# ------------------------------------------------------ Material Attributes
def make(g, base=(0.2, 0.4, 0.6), rough=0.3, **pins):
    props = dict(BaseColor=R(vector(g, base)), Roughness=R(scalar(g, rough)))
    props.update({k: R(v) for k, v in pins.items()})
    return g.add("MakeMaterialAttributes", **props)


def set_attrs(g, base, **values):
    return g.add("SetMaterialAttributes", Inputs=[R(base)] + [dict(R(v), InputName=k) for k, v in values.items()],
                 AttributeSetTypes=[GUID[k] for k in values])


g = G()
a, tr = run_attrs(g, make(g))
check("Make base colour", a["BaseColor"], (0.2, 0.4, 0.6))
check("Make roughness", a["Roughness"], 0.3)
check("Make unset specular is UE's 0.5", a["Specular"], 0.5)
check("Make unset normal", a["Normal"], (0.0, 0.0, 1.0))
check("Make unset opacity", a["Opacity"], 1.0)
check("Make unset AO", a["AmbientOcclusion"], 1.0)

g = G()
a, _ = run_attrs(g, set_attrs(g, make(g), Roughness=scalar(g, 0.9)))
check("Set overrides", a["Roughness"], 0.9)
check("Set passes the rest", a["BaseColor"], (0.2, 0.4, 0.6))

# Get: output 0 is the attributes, then its AttributeGetTypes in order
def get_graph(out):
    g = G()
    get = g.add("GetMaterialAttributes", MaterialAttributes=R(make(g)),
                AttributeGetTypes=[GUID["Roughness"], GUID["BaseColor"]])
    return expr(lambda _g: None, ) if False else run(g, R(get, out))[0]


check("Get roughness", get_graph(1), 0.3)
check("Get base colour", get_graph(2), (0.2, 0.4, 0.6))

# Break: UE's fixed output order
def break_graph(out, **pins):
    g = G()
    b = g.add("BreakMaterialAttributes", MaterialAttributes=R(make(g, **{k: v(g) for k, v in pins.items()})))
    return run(g, R(b, out))[0]


check("Break 3 is roughness", break_graph(3), 0.3)
check("Break 5 is emissive", break_graph(5, EmissiveColor=lambda g: vector(g, (1.0, 2.0, 3.0))), (1.0, 2.0, 3.0))
check("Break 2 is specular (default)", break_graph(2), 0.5)

# Blend: per attribute lerp, UseA / UseB, the shading model picked at 0.5
def blend(alpha, **props):
    g = G()
    m1 = make(g, (0.0, 0.0, 0.0), 0.2, ShadingModel=g.add("ShadingModel", ShadingModel="EMaterialShadingModel::MSM_ClearCoat"))
    m2 = make(g, (1.0, 1.0, 1.0), 0.6, ShadingModel=g.add("ShadingModel", ShadingModel="EMaterialShadingModel::MSM_Subsurface"))
    return run_attrs(g, g.add("BlendMaterialAttributes", A=R(m1), B=R(m2), Alpha=R(scalar(g, alpha)), **props))[0]


a = blend(0.25)
check("Blend base colour", a["BaseColor"], (0.25, 0.25, 0.25))
check("Blend roughness", a["Roughness"], 0.3)
check("Blend shading model below 0.5 is A", a["ShadingModel"], 4.0)
check("Blend shading model at 0.7 is B", blend(0.7)["ShadingModel"], 2.0)
check("Blend UseB", blend(0.25, PixelAttributeBlendType="EMaterialAttributeBlend::UseB")["Roughness"], 0.6)

# If over Material Attributes: per attribute
g = G()
a, _ = run_attrs(g, g.add("If", A=R(scalar(g, 0.8)), ConstB=0.5, AGreaterThanB=R(make(g, (0.1, 0.1, 0.1), 0.11)),
                          ALessThanB=R(make(g, (0.9, 0.9, 0.9), 0.99))))
check("If on attributes", a["Roughness"], 0.11)

# UE compiles one property at a time: attributes wired into Base Color give their Base Color
g = G()
m = make(g, (0.3, 0.2, 0.1), 0.5)
a, _ = run_attrs(g, set_attrs(g, m, BaseColor=m))
check("attributes into an attribute pin", a["BaseColor"], (0.3, 0.2, 0.1))

# a function taking and returning Material Attributes: a socket per attribute
def tint_body(g):
    inp = fn_input(g, "Layer", "MaterialAttributes")
    bc = g.add("GetMaterialAttributes", MaterialAttributes=R(inp), AttributeGetTypes=[GUID["BaseColor"]])
    half = g.add("Multiply", A=R(bc, 1), ConstB=0.5)
    return set_attrs(g, inp, BaseColor=half)


function("MF_Tint", tint_body)
g = G()
a, tr = run_attrs(g, call(g, "MF_Tint", {"Layer": make(g, (0.4, 0.6, 0.8), 0.7)}))
check("function attributes: base colour halved", a["BaseColor"], (0.2, 0.3, 0.4))
check("function attributes: roughness through", a["Roughness"], 0.7)
check("function attributes: default specular", a["Specular"], 0.5)
ft = next(iter(tr.functions.values()))
socks = sorted(i.name for i in ft.tree.interface.items_tree if i.item_type == 'SOCKET')
# only what the function reads or changes: roughness passes by the group
if socks != ["Layer: Base Color", "Result: Base Color"]:
    FAILS.append("attribute sockets: %r" % socks)
else:
    PASSES[0] += 1

# plain pins read as attributes (a material without Material Attributes)
g = G()
g.save("M_plain", None)
tree = bpy.data.node_groups.new("_test", "ShaderNodeTree")
env = TestEnv()
tr = Translator(tree, env)
env.h[0] = tr
g2 = G()
bc = vector(g2, (0.5, 0.25, 0.125))
path = g2.save("M_plainpins", bc, pin="BaseColor")
vals = tr.material_attributes(path)
check("plain pin as attribute", value(vals["BaseColor"]), (0.5, 0.25, 0.125))
check("plain material default roughness", value(vals["Roughness"]), 0.5)

# the 4-layer bases' layer samplers (Custom HLSL)
COLOR = ("MaterialFloat4 Color = 0;\r\nBRANCH\r\nif ((bool)bDoWork)\r\n{\r\n\tColor = ProcessMaterialColorTextureLookup("
         "Texture2DSample(Tex, GetMaterialSharedSampler(TexSampler, View_MaterialTextureBilinearWrapedSampler), UVs));"
         "\r\n}\r\nreturn Color;")
NORMAL = ("MaterialFloat4 Normal = MaterialFloat4(0, 0, 1, 0);\r\nBRANCH\r\nif ((bool)bDoWork)\r\n{\r\n\tNormal = "
          "UnpackNormalMap(Texture2DSample(Tex, GetMaterialSharedSampler(TexSampler, View_MaterialTextureBilinearWrapedSampler), UVs));"
          "\r\n}\r\nreturn Normal;")


def layer_sample(code, work, tex="T_0.75_0.25_0.5"):
    def build(g):
        t = g.add("TextureObject", Texture={"ObjectName": "Texture2D'%s'" % tex})
        return g.add("Custom", Code=code, OutputType="ECustomMaterialOutputType::CMOT_Float4", Inputs=[
            {"InputName": "Tex", "Input": R(t)}, {"InputName": "UVs", "Input": R(g.add("TextureCoordinate"))},
            {"InputName": "bDoWork", "Input": R(scalar(g, work))}])
    return expr(build)


v, w = layer_sample(COLOR, 1.0)
check("layer colour sample", v, (0.75, 0.25, 0.5, 1.0))
check("layer colour skipped", layer_sample(COLOR, 0.0)[0], (0.0, 0.0, 0.0, 0.0))
check("layer normal unpacked", layer_sample(NORMAL, 1.0)[0], (0.5, -0.5, math.sqrt(0.5), 1.0))
check("layer normal skipped", layer_sample(NORMAL, 0.0)[0], (0.0, 0.0, 1.0, 0.0))
if any("unknown Custom" in x for x in w):
    FAILS.append("layer sampler snippet not recognised: %r" % w)

# a name used twice (a collapsed subgraph numbers its own): refs pick by path
g = G()
g.add("Constant", _name="MaterialExpressionConstant_1", _outer="Material'M_Dup'", R=1.0)
g.add("Constant", _name="MaterialExpressionConstant_1",
      _outer="MaterialGraph'M_Dup:MaterialGraph_0.MaterialGraphNode_Composite_4.Collapsed Nodes'", R=7.0)
ref = dict(R("MaterialExpressionConstant_1"), Expression={
    "ObjectName": "MaterialExpressionConstant'M_Dup:MaterialGraph_0.MaterialGraphNode_Composite_4.Collapsed Nodes."
                  "MaterialExpressionConstant_1'"})
check("duplicate names resolved by path", run(g, ref)[0], 7.0)

# ------------------------------------------------------ renderer stand-ins
check("EyeAdaptation is exposure 1", expr(lambda g: g.add("EyeAdaptation"))[0], 1.0)
check("EyeAdaptationInverse at exposure 1", expr(lambda g: g.add(
    "EyeAdaptationInverse", LightValueInput=R(scalar(g, 3.5)), AlphaInput=R(scalar(g, 1.0))))[0], 3.5)
check("AtmosphericLightVector is the sun", expr(lambda g: g.add("AtmosphericLightVector"))[0], (0.0, 0.6, 0.8))
v, w = expr(lambda g: g.add("DDX", Value=R(scalar(g, 5.0))))
check("DDX stand-in", v, 0.0001)
if not any(x.startswith("stand-in: DDX") for x in w):
    FAILS.append("DDX stand-in not reported: %r" % w)
check("DepthFade keeps its opacity", expr(lambda g: g.add("DepthFade", InOpacity=R(scalar(g, 0.4)),
                                                          FadeDistance=R(scalar(g, 50.0))))[0], 0.4)
g = G()
dp = g.add("DynamicParameter", DefaultValue={"R": 0.1, "G": 0.2, "B": 0.3, "A": 0.4})
check("DynamicParameter output 0", run(g, R(dp, 0))[0], 0.1)
g = G()
dp = g.add("DynamicParameter", DefaultValue={"R": 0.1, "G": 0.2, "B": 0.3, "A": 0.4})
check("DynamicParameter output 2", run(g, R(dp, 2))[0], 0.3)
g = G()
pc = g.add("ParticleColor")
check("ParticleColor on a mesh is white", run(g, R(pc, 0))[0], (1.0, 1.0, 1.0))
g = G()
st = g.add("SceneTexture", SceneTextureId="ESceneTextureId::PPI_WorldNormal")
check("SceneTexture world normal", run(g, R(st, 0), normal=(0.0, 1.0, 0.0))[0], (0.0, 1.0, 0.0, 1.0))
g = G()
ca = g.add("CurveAtlasRowParameter", ParameterName="Curve", DefaultValue=3.0, InputTime=R(scalar(g, 0.5)),
           Atlas={"ObjectName": "CurveLinearColorAtlas'T_0.9_0.5_0.1'"})
check("CurveAtlasRowParameter colour", run(g, R(ca, 0))[0], (0.9, 0.5, 0.1))
g = G()
ca = g.add("CurveAtlasRowParameter", ParameterName="Curve", DefaultValue=3.0, InputTime=R(scalar(g, 0.5)),
           Atlas={"ObjectName": "CurveLinearColorAtlas'T_0.9_0.5_0.1'"})
check("CurveAtlasRowParameter alpha", run(g, R(ca, 4))[0], 1.0)
g = G()
ts = g.add("TextureSample", Texture={"ObjectName": "Texture2D'T_0.2_0.4_0.6'"})
check("TextureSample RGBA keeps its alpha", run(g, R(ts, 5))[0], (0.2, 0.4, 0.6, 1.0))
# socket names past Blender's 63 bytes: cut and tagged, and still found
LONG_IN = "Use Diffuse Map as Base Color (Otherwise Black) and a lot more words"
LONG_OUT = "Panning Emissive Result With An Unreasonably Long Output Name Here"


def _long_fn(g):
    return g.add("Multiply", A=R(fn_input(g, LONG_IN, "Scalar", 0.0)), ConstB=3.0)


function("MF_LongNames", _long_fn)
_p = os.path.join(TMP, "MF_LongNames.json")
_d = json.load(open(_p, encoding="utf-8"))
next(x for x in _d if x["Type"] == "MaterialExpressionFunctionOutput")["Properties"]["OutputName"] = LONG_OUT
json.dump(_d, open(_p, "w", encoding="utf-8"))
g = G()
fc = call(g, "MF_LongNames", {LONG_IN: scalar(g, 0.5)})
check("long socket names (63-byte cut)", run(g, R(fc, 0))[0], 1.5)
# a Normal sampler returns UnpackNormalMap: rg * 2 - 1, z rebuilt, alpha 1
g = G()
ts = g.add("TextureSample", Texture={"ObjectName": "Texture2D'T_0.6_0.3_0.9'"}, SamplerType="EMaterialSamplerType::SAMPLERTYPE_Normal")
check("Normal sampler unpacks", run(g, R(ts, 5))[0], (0.2, -0.4, math.sqrt(1 - 0.2 ** 2 - 0.4 ** 2), 1.0))
g = G()
ts = g.add("TextureSample", Texture={"ObjectName": "Texture2D'T_0.5_0.5_1.0'"}, SamplerType="EMaterialSamplerType::SAMPLERTYPE_Normal")
check("flat normal map is +Z", run(g, R(ts, 0))[0], (0.0, 0.0, 1.0))
g = G()
ts = g.add("TextureSample", Texture={"ObjectName": "Texture2D'T_0.6_0.3_0.9'"}, SamplerType="EMaterialSamplerType::SAMPLERTYPE_Normal")
check("Normal sampler G output", run(g, R(ts, 2))[0], -0.4)
g = G()
vp = vector(g, (0.1, 0.2, 0.3, 0.7))
check("VectorParameter RGBA output", run(g, R(vp, 5))[0], (0.1, 0.2, 0.3, 0.7))
check("VectorParameter alpha output", run(*(lambda g: (g, R(vector(g, (0.1, 0.2, 0.3, 0.7)), 4)))(G()))[0], 0.7)
g = G()
vp = g.add("VectorParameter", ParameterName="unset")     # cooked: DefaultValue left out when (0, 0, 0, 0)
check("VectorParameter without DefaultValue is (0, 0, 0, 0)", run(g, R(vp, 5))[0], (0.0, 0.0, 0.0, 0.0))
g = G()
arr = g.add("TextureSampleParameter2DArray", ParameterName="A", Texture={"ObjectName": "Texture2DArray'TA_Grey'"},
            Coordinates=R(vector(g, (0.5, 0.5, 1.8))))
check("texture array picks the nearest slice", run(g, R(arr, 0))[0], (0.3, 0.3, 0.3))


# Custom HLSL rebuilt by hash: each against a Python reading of its code
def snippet(key, inputs, out_type="CMOT_Float3", **over):
    code = {"58c98080": "Mod( ((uint)(p.x) + 2 * (uint)(p.y)) , 5 )"}.get(key)
    def build(g):
        return g.add("Custom", Code="__hash__", OutputType="ECustomMaterialOutputType::" + out_type,
                     Inputs=[{"InputName": k, "Input": R(v(g))} for k, v in inputs.items()])
    # the snippets are looked up by hash: register this test's placeholder
    CUSTOM_HASHES[__import__("hashlib").sha1(b"__hash__").hexdigest()[:8]] = CUSTOM_HASHES[key]
    return expr(build, **over)[0]


check("dither 5", snippet("58c98080", {"p": lambda g: vector(g, (7.3, 3.9, 0.0))}, "CMOT_Float1"), 3.0)
check("select4 index 3", snippet("c98e3fcb", {"A": lambda g: scalar(g, 1.0), "B": lambda g: scalar(g, 2.0),
                                             "C": lambda g: scalar(g, 3.0), "D": lambda g: scalar(g, 4.0),
                                             "selection": lambda g: scalar(g, 3.4)}, "CMOT_Float4"), 3.0)
check("select4 clamps", snippet("c98e3fcb", {"A": lambda g: scalar(g, 1.0), "B": lambda g: scalar(g, 2.0),
                                            "C": lambda g: scalar(g, 3.0), "D": lambda g: scalar(g, 4.0),
                                            "selection": lambda g: scalar(g, 9.0)}, "CMOT_Float4"), 4.0)
check("select3 else", snippet("1e3af51b", {"A": lambda g: scalar(g, 1.0), "B": lambda g: scalar(g, 2.0),
                                          "C": lambda g: scalar(g, 3.0), "Select": lambda g: scalar(g, 5.0)}), 3.0)
check("select3 == 2", snippet("1e3af51b", {"A": lambda g: scalar(g, 1.0), "B": lambda g: scalar(g, 2.0),
                                          "C": lambda g: scalar(g, 3.0), "Select": lambda g: scalar(g, 2.0)}), 2.0)
check("select 0..15 outside", snippet("9f847d24", {"A": lambda g: scalar(g, 1.0), "B": lambda g: scalar(g, 2.0),
                                                  "Select": lambda g: scalar(g, 16.0)}), 2.0)
check("rain occluder at 0", snippet("5b88b88a", {"In": lambda g: scalar(g, 0.0),
                                                "OccluderOn": lambda g: scalar(g, 0.25)}), 1.0)
check("rain occluder off 0", snippet("5b88b88a", {"In": lambda g: scalar(g, 0.5),
                                                 "OccluderOn": lambda g: scalar(g, 0.25)}), 0.25)


def tri_ref(tN, wN, sel):
    i = int(math.trunc(sel + 0.001))
    if i == 0:
        v = (tN[2] * wN[0], tN[0] + wN[1], tN[1] + wN[2])
    elif i == 1:
        v = (tN[0] + wN[0], tN[2] * wN[1], tN[1] + wN[2])
    else:
        v = (tN[0] + wN[0], tN[1] + wN[1], tN[2] * wN[2])
    n = math.sqrt(sum(c * c for c in v))
    return tuple(c / n for c in v)


tN, wN = (0.2, -0.3, 0.9), (0.6, 0.0, 0.8)
for sel in (0.0, 1.0, 2.0):
    check("triplanar normals plane %d" % sel, snippet("b4a2625d", {
        "unpackedNormalValue": lambda g: vector(g, tN), "worldNormal": lambda g: vector(g, wN),
        "selection": lambda g, s=sel: scalar(g, s)}), tri_ref(tN, wN, sel))


def hue_ref(h):
    h = math.fmod(h, 1.0)
    r, gg, b = abs(h * 6 - 3) - 1, 2 - abs(h * 6 - 2), 2 - abs(h * 6 - 4)
    return tuple(max(0.0, min(1.0, c)) for c in (r, gg, b))


check("hue to rgb", snippet("1ec1b585", {"Hue": lambda g: scalar(g, 0.37)}), hue_ref(0.37))
check("bit field", snippet("fff78762", {"Scalar": lambda g: scalar(g, 45.0), "BitCount": lambda g: scalar(g, 3.0),
                                       "BitIndex": lambda g: scalar(g, 2.0)}, "CMOT_Float1"), float((45 >> 2) & 7))
n = (0.36, -0.48, 0.8)
check("octahedral round trip", snippet("287306e9", {"Oct": lambda g: g.add(
    "Custom", Code="__oct__", OutputType="ECustomMaterialOutputType::CMOT_Float2",
    Inputs=[{"InputName": "N", "Input": R(vector(g, n))}])}), n) if CUSTOM_HASHES.setdefault(
    __import__("hashlib").sha1(b"__oct__").hexdigest()[:8], "custom_oct_encode") else None
check("smoothstep snippet", snippet("2e04637e", {"varA": lambda g: scalar(g, 0.25), "varMin": lambda g: scalar(g, 0.0),
                                                "varMax": lambda g: scalar(g, 1.0)}), 0.25 * 0.25 * (3 - 0.5))
check("atan2 snippet", snippet("bce084b5", {"x": lambda g: scalar(g, -1.0), "y": lambda g: scalar(g, 1.0)},
                               "CMOT_Float1"), math.atan2(1.0, -1.0))
check("acos snippet", snippet("dd5fa5b0", {"a": lambda g: scalar(g, 0.3)}, "CMOT_Float1"), math.acos(0.3))
check("cube corner snippet", snippet("8fb148eb", {"Center": lambda g: vector(g, (1.0, 2.0, 3.0)),
                                                 "Size": lambda g: scalar(g, 4.0)}), (-1.0, 0.0, 1.0))


def hsv_ref(c):
    # RGBtoHSV's HLSL, read in Python
    r, g, b = c
    lerp = lambda x, y, t: x + (y - x) * t
    s1 = 1.0 if g >= b else 0.0
    p = (lerp(b, g, s1), lerp(g, b, s1), lerp(-1.0, 0.0, s1), lerp(2 / 3, -1 / 3, s1))
    s2 = 1.0 if r >= p[0] else 0.0
    q = (lerp(p[0], r, s2), p[1], lerp(p[3], p[2], s2), lerp(r, p[0], s2))
    d = q[0] - min(q[3], q[1])
    return abs(q[2] + (q[3] - q[1]) / (6 * d + 1e-7)), d / (q[0] + 1e-7), q[0]


for rgb in ((0.9, 0.2, 0.4), (0.1, 0.8, 0.3), (0.2, 0.3, 0.7), (0.5, 0.5, 0.5)):
    check("rgb to hsv %s" % (rgb,), snippet("41a47d1f", {"c": lambda g, v=rgb: vector(g, v)}), hsv_ref(rgb))


# SoftOutline samples the scene depth by Raycast (a render can check its values, not this evaluator): its rings are
# unrolled to the step counts, ring 0 being the pixel itself (1 + (6 - 1) * 4 rays)
def soft_outline(g):
    CUSTOM_HASHES[__import__("hashlib").sha1(b"__outline__").hexdigest()[:8]] = "custom_soft_outline"
    ins = {"Distance": 0.03, "DistanceSteps": 6.0, "RadialSteps": 4.0, "TempAARotation": 0.0, "DistanceMask": 1.0,
           "TempAADistance": 0.0, "RadialOffset": 0.5, "DivideF": 20.0, "PixelDepthOffset": 0.0}
    inputs = [{"InputName": k, "Input": R(g.add("Constant", R=v))} for k, v in ins.items()]
    inputs.append({"InputName": "PDepth", "Input": R(g.add("PixelDepth"))})
    return g.add("Custom", Code="__outline__", OutputType="ECustomMaterialOutputType::CMOT_Float1", Inputs=inputs)


g = G()
v, tr = run_val(g, soft_outline(g))
check("soft outline rays", count(tr.tree, "ShaderNodeRaycast"), 21.0)
check("soft outline built", float(not any("SoftOutline" in w or "unknown Custom" in w for w in tr.warnings)), 1.0)
check("eye adaptation inverse snippet", snippet("190f0d13", {"LightValue": lambda g: scalar(g, 4.0),
                                                            "Alpha": lambda g: scalar(g, 1.0),
                                                            "Adaptation": lambda g: scalar(g, 2.0)}), 2.0)
check("bounds centre", snippet("bcf2b682", {}), (10.0, 10.0, 90.0))
check("bounds extent", snippet("8ae85b7f", {}), (20.0, 30.0, 90.0))
check("shadow-pass switch takes Main", snippet("eda214ed", {"Main": lambda g: scalar(g, 0.7),
                                                           "Shadow": lambda g: scalar(g, 0.1)}), 0.7)

# asuint: a float's bit fields rebuilt in float maths, against struct
import struct  # noqa: E402


def as_bits(f):
    return struct.unpack("<I", struct.pack("<f", f))[0]


for packed in (struct.unpack("<f", struct.pack("<I", (211 << 20) | (517 << 10) | 900))[0], 1.0, 0.15625, 3.0e-39):
    b = as_bits(packed)
    check("packed HSV %r" % packed, snippet("0eafe77b", {"PackedHSV": lambda g, v=packed: scalar(g, v)}),
          ((b >> 20) / 360.0, ((b >> 10) & 1023) / 1023.0, (b & 1023) / 1023.0))
check("asuint bit field across fields", snippet("bd366923", {
    "Scalar": lambda g: scalar(g, -2.75), "BitCount": lambda g: g.add("Constant", R=12.0),
    "BitIndex": lambda g: g.add("Constant", R=18.0)}, "CMOT_Float1"), float((as_bits(-2.75) >> 18) & 4095))

# ------------------------------------------------------ water (MSM_SingleLayerWater)
# SceneDepthWithoutWater: the pixel's depth + the depth under the surface over the view's steepness
cam = (0.6, 0.0, 0.8)
check("SceneDepthWithoutWater from the env's depth", expr(lambda g: g.add("SceneDepthWithoutWater"),
                                                          camera=cam, pixel_depth=1000.0, water_depth=200.0)[0],
      1000.0 + 200.0 / 0.8)
check("SceneDepthWithoutWater default depth", expr(lambda g: g.add("SceneDepthWithoutWater"), camera=cam,
                                                   pixel_depth=500.0)[0], 500.0 + 300.0 / 0.8)
check("SceneDepthWithoutWater never before the surface", expr(lambda g: g.add("SceneDepthWithoutWater"), camera=cam,
                                                              pixel_depth=500.0, water_depth=-40.0)[0], 500.0)
check("SceneDepthWithoutWater, steep water: along its normal", expr(lambda g: g.add("SceneDepthWithoutWater"),
                                                                    camera=cam, normal=(1.0, 0.0, 0.0),
                                                                    pixel_depth=100.0, water_depth=30.0)[0],
      100.0 + 30.0 / 0.6)
check("SceneDepthWithoutWater, tilted level water: straight down", expr(
    lambda g: g.add("SceneDepthWithoutWater"), camera=cam, normal=(0.6, 0.0, 0.8), pixel_depth=100.0,
    water_depth=30.0)[0], 100.0 + 30.0 / 0.8)
check("SceneDepthWithoutWater minus PixelDepth", expr(lambda g: g.add(
    "Subtract", A=R(g.add("SceneDepthWithoutWater")), B=R(g.add("PixelDepth"))), camera=(0.0, 0.0, 1.0),
    pixel_depth=700.0, water_depth=150.0)[0], 150.0)
# the water info texture's decodes (WaterBodyData): the surface's own height, the ground under it
check("DecodeWaterInfoZHeight: the surface", snippet("766f134a", {"WaterInfoSample": lambda g: vector(g, (9, 9, 9)),
                                                                  "WaterZoneIndex": lambda g: scalar(g, 0.0)},
                                                     "CMOT_Float1", world=(10.0, 20.0, 1460.0)), 1460.0)
check("DecodeWaterInfoGroundHeight: surface - depth", snippet("58bda2dd", {
    "WaterInfoSample": lambda g: vector(g, (9, 9, 9)), "WaterZoneIndex": lambda g: scalar(g, 0.0)}, "CMOT_Float1",
    world=(10.0, 20.0, 1460.0), water_depth=120.0), 1340.0)
# DrawWaterInfo's flow (R: speed / MaxVelocity, G: angle / 2 pi), decoded as velocity
fl = (0.098, 0.3)
want_v = (fl[0] * 1024.0 * math.cos(fl[1] * 2 * math.pi), fl[0] * 1024.0 * math.sin(fl[1] * 2 * math.pi))
check("DecodeWaterInfoVelocity: the surface's flow", snippet("a8eed0bd", {
    "WaterInfoSample": lambda g: vector(g, (9, 9, 9)), "MaxVelocity": lambda g: scalar(g, 1024.0)}, "CMOT_Float2",
    flow=fl), want_v)
check("DecodeWaterInfoVelocity: vertex colour", snippet("a8eed0bd", {
    "WaterInfoSample": lambda g: vector(g, (9, 9, 9)), "MaxVelocity": lambda g: scalar(g, 1024.0)}, "CMOT_Float2",
    vertex_color=(fl[0], fl[1], 0.0)), want_v)
check("flow snippet", snippet("7462ade0", {"Flow": lambda g: vector(g, (0.5, 0.125, 0.0)),
                                          "MaxVelocity": lambda g: scalar(g, 200.0)}, "CMOT_Float2"),
      (100.0 * math.cos(math.pi / 4), 100.0 * math.sin(math.pi / 4)))
check("1 - exp(-x) float2", snippet("2e4092f5", {"x": lambda g: vector(g, (0.5, 2.0, 0.0))}, "CMOT_Float2"),
      (1 - math.exp(-0.5), 1 - math.exp(-2.0)))


def quat_ref(e):
    r, p, y = (math.radians(a) / 2 for a in e)
    c1, s1, c2, s2, c3, s3 = math.cos(y), math.sin(y), math.cos(p), math.sin(p), math.cos(r), math.sin(r)
    return (c1 * c2 * s3 - s1 * s2 * c3, c1 * s2 * c3 + s1 * c2 * s3, s1 * c2 * c3 - c1 * s2 * s3,
            c1 * c2 * c3 + s1 * s2 * s3)


check("Euler to quaternion", snippet("2658b7cc", {"M_PI": lambda g: g.add("Constant", R=math.pi),
                                                 "Euler": lambda g: vector(g, (30.0, -50.0, 120.0))}, "CMOT_Float4"),
      quat_ref((30.0, -50.0, 120.0)))


def custom_out(key, out, inputs, **over):
    """A snippet's AdditionalOutputs (output index 1..)."""
    CUSTOM_HASHES[__import__("hashlib").sha1(b"__hash__").hexdigest()[:8]] = CUSTOM_HASHES[key]
    g = G()
    c = g.add("Custom", Code="__hash__", OutputType="ECustomMaterialOutputType::CMOT_Float1",
              Inputs=[{"InputName": k, "Input": R(v(g))} for k, v in inputs.items()])
    return run(g, R(c, out), **over)[0]


waves = {"WaterBodyIndex": lambda g: scalar(g, 0.0), "WorldPosition": lambda g: vector(g, (5, 5, 5)),
         "Time": lambda g: scalar(g, 1.0)}
check("Gerstner waves: still water's normal", custom_out("92a4fca7", 1, waves), (0.0, 0.0, 1.0))
check("Gerstner waves: no offset", custom_out("92a4fca7", 4, waves), (0.0, 0.0, 0.0))
check("water zone extent", custom_out("0aad0772", 1, {"WaterZoneIndex": lambda g: scalar(g, 0.0)}), (1.0, 1.0))


# a hole's clip(): the value passes through, the material's clips get "not here"
def hole(mask_value):
    g = G()
    c = g.add("Custom", Code="__hash__", Inputs=[{"InputName": "NormalPassThru", "Input": R(vector(g, (0.1, 0.2, 0.9)))},
                                                  {"InputName": "HoleMask", "Input": R(scalar(g, mask_value))}])
    CUSTOM_HASHES[__import__("hashlib").sha1(b"__hash__").hexdigest()[:8]] = CUSTOM_HASHES["647da244"]
    v, _w, tr = run(g, R(c))
    return v, [value(k) for k in tr.clips]


v, clips = hole(0.0)
check("hole clip passes through", v, (0.1, 0.2, 0.9))
check("hole clip where the mask is 0", clips[0] if clips else None, 0.0)
check("hole clip elsewhere", (hole(0.7)[1] or [None])[0], 1.0)

# the water medium: SingleLayerWaterMaterialOutput beside the attributes
g = G()
g.add("SingleLayerWaterMaterialOutput", ScatteringCoefficients=R(vector(g, (0.0, 0.001, 0.002))),
      AbsorptionCoefficients=R(g.add("Divide", B=R(vector(g, (80.0, 1170.0, 2000.0))))),
      PhaseG=R(scalar(g, 0.3)))
a, tr = run_attrs(g, make(g))
check("water scattering", a["WaterScattering"], (0.0, 0.001, 0.002))
check("water absorption (1 / distance)", a["WaterAbsorption"], (1 / 80.0, 1 / 1170.0, 1 / 2000.0), tol=1e-7)
check("water phase G", a["WaterPhaseG"], 0.3)
check("water colour behind: 1 unlinked", a["WaterColorScaleBehindWater"], (1.0, 1.0, 1.0))
check("water attributes beside the others", a["Roughness"], 0.3)
g = G()
a, _ = run_attrs(g, make(g))
check("no water output: no medium", float("WaterAbsorption" in a), 0.0)

# the 2D light march (MF_Raymarched_Smoke_Func), laid out step by step: 4 steps through a 0.5 red
# texture, each dimming the light by 0.5 / (4 - 0); LO 0.1 + light colour 1 * light * density 2
def march(g):
    t = g.add("TextureObject", Texture={"ObjectName": "Texture2D'T_0.5_0.0_0.0'"})
    return g.add("Custom", Code="__hash__", Inputs=[
        {"InputName": "Tex", "Input": R(t)}, {"InputName": "marchDistance", "Input": R(scalar(g, 0.15))},
        {"InputName": "numSteps", "Input": R(scalar(g, 4.0))}, {"InputName": "TransmittedLight", "Input": R(scalar(g, 1.0))},
        {"InputName": "LO", "Input": R(const3(g, (0.1, 0.1, 0.1)))}, {"InputName": "density", "Input": R(scalar(g, 2.0))},
        {"InputName": "UV", "Input": R(g.add("TextureCoordinate"))}, {"InputName": "marchDir", "Input": R(g.add("TextureCoordinate"))},
        {"InputName": "LightColor", "Input": R(const3(g, (1.0, 1.0, 1.0)))}, {"InputName": "contrast", "Input": R(scalar(g, 0.0))},
        {"InputName": "ChannelSelect", "Input": R(const3(g, (1.0, 0.0, 0.0)))}])


CUSTOM_HASHES[__import__("hashlib").sha1(b"__hash__").hexdigest()[:8]] = CUSTOM_HASHES["5b90577e"]
check("2D light march, 4 steps", expr(march)[0], tuple([0.1 + (1.0 - 0.125) ** 4 * 2.0] * 3))

# UE's division by zero: an infinity of the numerator's sign (saturated: 1 or 0), not Blender's 0
check("Divide by a zero parameter, saturated", expr(lambda g: g.add("Saturate", Input=R(g.add("Divide", A=R(scalar(g, 3.0)), B=R(scalar(g, 0.0))))))[0], 1.0)
check("Divide a negative by zero, saturated", expr(lambda g: g.add("Saturate", Input=R(g.add("Divide", A=R(scalar(g, -3.0)), B=R(scalar(g, 0.0))))))[0], 0.0)
check("Divide zero by zero", expr(lambda g: g.add("Divide", A=R(scalar(g, 0.0)), B=R(scalar(g, 0.0))))[0], 0.0)
check("Divide by a parameter", expr(lambda g: g.add("Divide", A=R(scalar(g, 3.0)), B=R(scalar(g, 4.0))))[0], 0.75)
check("Divide a vector by a zero parameter, saturated",
      expr(lambda g: g.add("Saturate", Input=R(g.add("Divide", A=R(const3(g, (2.0, -1.0, 0.0))), B=R(scalar(g, 0.0))))))[0], (1.0, 0.0, 0.0))
# a vector divisor, per component (a sprite's mouth scaled by 0 through Scale UVs By Center)
check("Divide a vector by a vector with a zero, saturated",
      expr(lambda g: g.add("Saturate", Input=R(g.add("Divide", A=R(const3(g, (2.0, -1.0, 3.0))), B=R(vector(g, (0.0, 0.0, 4.0)), mask="RGB")))))[0], (1.0, 0.0, 0.75))

# fewer nodes for the same maths: what each builds, before merge_duplicates
def built(build):
    g = G()
    v, _w, tr = run(g, build(g))
    kinds = {}
    for n in tr.tree.nodes:
        k = n.bl_idname + ":" + getattr(n, "operation", "")
        kinds[k] = kinds.get(k, 0) + 1
    return v, kinds


v, k = built(lambda g: g.add("Divide", A=R(scalar(g, 3.0)), B=R(scalar(g, 4.0))))
check("Divide by a parameter: compare, multiply-add, divide", (v, sum(c for n, c in k.items() if n.startswith("ShaderNodeMath"))), (0.75, 3))
v, k = built(lambda g: g.add("Clamp", Input=R(scalar(g, 1.7)), MinDefault=0.2, MaxDefault=0.8))
check("Clamp: one Clamp node", (v, k.get("ShaderNodeClamp:", 0), k.get("ShaderNodeMath:MAXIMUM", 0)), (0.8, 1, 0))
v, k = built(lambda g: g.add("If", A=R(scalar(g, 0.1)), B=R(scalar(g, 0.3)), AGreaterThanB=R(g.add("Constant", R=1.0)),
                             ALessThanB=R(g.add("Constant", R=2.0))))
check("If: A < B picks, no 1 - x", (v, k.get("ShaderNodeMath:SUBTRACT", 0)), (2.0, 0))
v, k = built(lambda g: g.add("IfThenElse", Condition=R(scalar(g, 1.0)),
                             **{"True": R(g.add("Constant", R=1.0)), "False": R(g.add("Constant", R=2.0))}))
check("IfThenElse: no 1 - x", (v, k.get("ShaderNodeMath:SUBTRACT", 0)), (1.0, 0))
v, k = built(lambda g: (lambda s: g.add("Add", A=R(g.add("OneMinus", Input=R(s))), B=R(g.add("OneMinus", Input=R(s)))))(scalar(g, 0.25)))
check("one node for one value asked twice", (v, k.get("ShaderNodeMath:SUBTRACT", 0)), (1.5, 1))
g = G()
fv, _tr = run_val(g, g.add("OneMinus", Input=R(g.add("OneMinus", Input=R(scalar(g, 0.3))))))
check("1 - (1 - x) is x", (value(fv), float(fv.s.node.type == 'VALUE')), (0.3, 1.0))
g = G()
fv, tr = run_val(g, g.add("LinearInterpolate", A=R(scalar(g, 2.0)), B=R(scalar(g, 6.0)),
                          Alpha=R(g.add("OneMinus", Input=R(scalar(g, 0.25))))))
merge_duplicates(tr.tree)
check("Lerp by 1 - x: the sides swap, no 1 - x", (value(fv), count(tr.tree, "ShaderNodeMath")), (5.0, 0))
v, k = built(lambda g: g.add("Normalize", VectorInput=R(const3(g, (3.0, 0.0, 4.0)))))
check("a constant vector's maths folds", tuple(v) + (sum(c for n, c in k.items() if n.startswith("ShaderNodeVectorMath")),),
      (0.6, 0.0, 0.8, 0))

# UE's Power: pow(max(Base, 0), Exponent) - a negative base gives 0, not Blender's square
check("Power of a negative base", expr(lambda g: g.add("Power", Base=R(scalar(g, -2.0)), Exponent=R(scalar(g, 2.0))))[0], 0.0)
check("Power of a positive base", expr(lambda g: g.add("Power", Base=R(scalar(g, 3.0)), Exponent=R(scalar(g, 2.0))))[0], 9.0)

# HLSL's smoothstep over an empty range: a hard edge at Min, not Blender's 0
check("SmoothStep, Min = Max parameters, past it", expr(lambda g: g.add("SmoothStep", Min=R(scalar(g, 0.5)), Max=R(scalar(g, 0.5)), Value=R(scalar(g, 0.7))))[0], 1.0)
check("SmoothStep, Min = Max parameters, before it", expr(lambda g: g.add("SmoothStep", Min=R(scalar(g, 0.5)), Max=R(scalar(g, 0.5)), Value=R(scalar(g, 0.3))))[0], 0.0)
check("SmoothStep, parameters", expr(lambda g: g.add("SmoothStep", Min=R(scalar(g, 0.0)), Max=R(scalar(g, 1.0)), Value=R(scalar(g, 0.25))))[0], 0.25 * 0.25 * (3 - 2 * 0.25))

# a float2 against a scalar: the scalar spreads over X and Y only (the UV (0.25, 0.75) is 0.35355 from 0.5)
check("SphereMask float2 to scalar", expr(lambda g: g.add("SphereMask", A=R(g.add("TextureCoordinate")), B=R(scalar(g, 0.5)),
                                                          AttenuationRadius=0.5, HardnessPercent=0.0))[0], 1.0 - 0.3535534 / 0.5)
check("Distance float2 to scalar", expr(lambda g: g.add("Distance", A=R(g.add("TextureCoordinate")), B=R(scalar(g, 0.5))))[0], 0.3535534)

# textures through closures (env.texture_closures): the image node stays at the root, a
# function's group samples it through a Closure input (groups then hold no image: shared)
function("F_Samp", lambda g: g.add("TextureSampleParameter2D", ParameterName="Tex", Texture={"ObjectName": "Texture2D'T_0.2_0.4_0.6'"}))
g = G()
add = g.add("Add", A=R(call(g, "F_Samp", {})),
            B=R(g.add("TextureSampleParameter2D", ParameterName="Tex", Texture={"ObjectName": "Texture2D'T_0.2_0.4_0.6'"})))
v, tr = run_val(g, R(add), closures=True)
check("texture through a closure, in a function and at the root", value(v), (0.4, 0.8, 1.2))
ft = next(iter(tr.functions.values()))
check("closure: the function's group holds no image", count(ft.tree, "ShaderNodeTexImage"), 0)
check("closure: the function's group takes a Closure input", sum(1 for it in ft.tree.interface.items_tree
      if it.item_type == 'SOCKET' and it.socket_type == 'NodeSocketClosure'), 1)
check("closure: one image node at the root for both reads", count(tr.tree, "ShaderNodeTexImage"), 1)

# merge_duplicates: a texture read through three pins is one image node; the value stays
g = G()
ts = g.add("TextureSample", Texture={"ObjectName": "Texture2D'T_0.2_0.4_0.6'"})
s = g.add("Add", A=R(ts, 1), B=R(g.add("Add", A=R(ts, 2), B=R(g.add("Add", A=R(ts, 3), B=R(scalar(g, 1.0)))))))
v, tr = run_val(g, R(s))
check("merge: three image nodes before", count(tr.tree, "ShaderNodeTexImage"), 3)
merge_duplicates(tr.tree)
check("merge: one image node after", count(tr.tree, "ShaderNodeTexImage"), 1)
check("merge keeps the texture's value", value(v), 2.2)

# a call read through two outputs is one group node
g2 = G()
a = fn_input(g2, "A")
g2.add("FunctionOutput", Id="OUT_F_Two", OutputName="Double", A=R(g2.add("Multiply", A=R(a), ConstB=2.0)))
g2.add("FunctionOutput", Id="OUT_F_Two_b", OutputName="Plus", A=R(g2.add("Add", A=R(a), ConstB=1.0)))
g2.save("F_Two")
g = G()
c = g.add("MaterialFunctionCall", MaterialFunction={"ObjectName": "MaterialFunction'F_Two'", "ObjectPath": "/Test/F_Two.0"},
          FunctionInputs=[{"ExpressionInputId": "IN_A", "Input": R(scalar(g, 3.0))}],
          FunctionOutputs=[{"ExpressionOutputId": "OUT_F_Two"}, {"ExpressionOutputId": "OUT_F_Two_b"}])
v, tr = run_val(g, R(g.add("Add", A=R(c, 0), B=R(c, 1))))
check("a call read through two outputs: one group node, as built", count(tr.tree, "ShaderNodeGroup"), 1)
merge_duplicates(tr.tree)
check("merge: one group node per call after", count(tr.tree, "ShaderNodeGroup"), 1)
check("merge keeps the call's value", value(v), 10.0)

# ... also when each output reads a different input (each links its own, on the one node)
g2 = G()
a, b = fn_input(g2, "A"), fn_input(g2, "B")
g2.add("FunctionOutput", Id="OUT_F_Pair", OutputName="TwiceA", A=R(g2.add("Multiply", A=R(a), ConstB=2.0)))
g2.add("FunctionOutput", Id="OUT_F_Pair_b", OutputName="BPlus", A=R(g2.add("Add", A=R(b), ConstB=1.0)))
g2.save("F_Pair")
g = G()
c = g.add("MaterialFunctionCall", MaterialFunction={"ObjectName": "MaterialFunction'F_Pair'", "ObjectPath": "/Test/F_Pair.0"},
          FunctionInputs=[{"ExpressionInputId": "IN_A", "Input": R(scalar(g, 3.0))},
                          {"ExpressionInputId": "IN_B", "Input": R(scalar(g, 5.0))}],
          FunctionOutputs=[{"ExpressionOutputId": "OUT_F_Pair"}, {"ExpressionOutputId": "OUT_F_Pair_b"}])
v, tr = run_val(g, R(g.add("Add", A=R(c, 0), B=R(c, 1))))
check("outputs reading different inputs: one group node", count(tr.tree, "ShaderNodeGroup"), 1)
check("outputs reading different inputs: the value", value(v), 12.0)

# ... unless one output's input reads the other output: one node would loop
g2 = G()
g2.add("FunctionOutput", Id="OUT_F_Loop", OutputName="Five", A=R(g2.add("Add", A=R(scalar(g2, 4.0)), ConstB=1.0)))
g2.add("FunctionOutput", Id="OUT_F_Loop_b", OutputName="Use", A=R(g2.add("Add", A=R(fn_input(g2, "A")), ConstB=1.0)))
g2.save("F_Loop")
g = G()
c = g.add("MaterialFunctionCall", MaterialFunction={"ObjectName": "MaterialFunction'F_Loop'", "ObjectPath": "/Test/F_Loop.0"},
          FunctionInputs=[], FunctionOutputs=[{"ExpressionOutputId": "OUT_F_Loop"}, {"ExpressionOutputId": "OUT_F_Loop_b"}])
g.nodes[-1]["Properties"]["FunctionInputs"] = [{"ExpressionInputId": "IN_A", "Input": R(c, 0)}]
v, tr = run_val(g, R(c, 1))
merge_duplicates(tr.tree)
check("merge: an output feeding its own call's input keeps two group nodes", count(tr.tree, "ShaderNodeGroup"), 2)
check("merge: ... and its value", value(v), 6.0)

# a call folded at build reads its inputs in the function's order, not a set's (PYTHONHASHSEED
# picked which input the caller built first, and with it the trees: M_Cube_Cosmetics)
ORDER = ["Mid", "Alpha", "Zed", "Kilo", "Bravo", "Yankee", "Echo", "Golf"]
for k, n in enumerate(ORDER):
    function("F_K_" + n, lambda g, k=k: g.add("Constant", R=float(k)))


def _order_body(g):
    s = fn_input(g, ORDER[0])
    for n in ORDER[1:]:
        s = g.add("Add", A=R(s), B=R(fn_input(g, n)))
    return s


function("F_Order", _order_body)
v, w, tr = run(*(lambda g: (g, call(g, "F_Order", {n: call(g, "F_K_" + n, {}) for n in sorted(ORDER)})))(G()))
check("folded call: its value", v, float(sum(range(len(ORDER)))))
names = [ft.fname for ft in tr.functions.values()]
if names != ["F_Order"] + ["F_K_" + n for n in ORDER]:
    FAILS.append("folded call: inputs read in the function's order: %r" % names)
else:
    PASSES[0] += 1

# addressing: the texture's own (AddressX/Y), or a shared sampler's
for name, src, want_ext, want_fix in (("T_0.5_0.5_0.5", "", 'REPEAT', 0), ("T_0.5_0.5_0.5_CX_CY", "", 'EXTEND', 0),
                                      ("T_0.5_0.5_0.5_MX_MY", "", 'MIRROR', 0), ("T_0.5_0.5_0.5_CY", "", 'REPEAT', 1),
                                      ("T_0.5_0.5_0.5_MX_CY", "", 'MIRROR', 1), ("T_0.5_0.5_0.5_MX", "", 'REPEAT', 1),
                                      ("T_0.5_0.5_0.5_CX_CY", "SSM_Wrap_WorldGroupSettings", 'REPEAT', 0),
                                      ("T_0.5_0.5_0.5", "SSM_Clamp_WorldGroupSettings", 'EXTEND', 0)):
    for closures in (False, True):
        g = G()
        ts = g.add("TextureSample", Texture={"ObjectName": "Texture2D'%s'" % name},
                   SamplerSource=("ESamplerSourceMode::" + src) if src else "")
        v, tr = run_val(g, R(ts), closures=closures)
        img = next(n for n in tr.tree.nodes if n.bl_idname == "ShaderNodeTexImage")
        fixes = sum(1 for n in tr.tree.nodes if n.bl_idname == "ShaderNodeMath" and n.operation in ('MAXIMUM', 'PINGPONG')
                    and n.label in ("clamp (TA_Clamp)", "mirror (TA_Mirror)"))
        tag = "%s %s%s" % (name, src, " (closure)" if closures else "")
        check("address %s: extension %s" % (tag, img.extension), float(img.extension == want_ext), 1.0)
        if closures:
            # (on the test's constant UVs the plain path folds a clamp away)
            check("address %s: axis fixes" % tag, fixes, want_fix)

# a vector split and put back in order is the vector: the pair goes, its readers read the source
tree = bpy.data.node_groups.new("_test", "ShaderNodeTree")
tree.interface.new_socket("Out", in_out='OUTPUT', socket_type='NodeSocketFloat')
val = tree.nodes.new("ShaderNodeValue"); val.outputs[0].default_value = 2.0
src = tree.nodes.new("ShaderNodeCombineXYZ"); tree.links.new(val.outputs[0], src.inputs[1])
src.inputs[0].default_value, src.inputs[2].default_value = 1.0, 3.0
sep = tree.nodes.new("ShaderNodeSeparateXYZ"); tree.links.new(src.outputs[0], sep.inputs[0])
back = tree.nodes.new("ShaderNodeCombineXYZ")
for k in range(3):
    tree.links.new(sep.outputs[k], back.inputs[k])
ln = tree.nodes.new("ShaderNodeVectorMath"); ln.operation = 'LENGTH'; tree.links.new(back.outputs[0], ln.inputs[0])
go = tree.nodes.new("NodeGroupOutput"); tree.links.new(ln.outputs["Value"], go.inputs[0])
merge_duplicates(tree)
check("round trip: split + append gone", count(tree, "ShaderNodeSeparateXYZ") + count(tree, "ShaderNodeCombineXYZ"), 1)
check("round trip: the value stays", ev(ln.outputs["Value"]), math.sqrt(14.0))

# maths that hands its input on (x * 1, x + 0) goes; x * 2 stays
tree = bpy.data.node_groups.new("_test", "ShaderNodeTree")
tree.interface.new_socket("Out", in_out='OUTPUT', socket_type='NodeSocketFloat')
val = tree.nodes.new("ShaderNodeValue"); val.outputs[0].default_value = 3.0
m1 = tree.nodes.new("ShaderNodeMath"); m1.operation = 'MULTIPLY'; m1.inputs[0].default_value = 1.0
tree.links.new(val.outputs[0], m1.inputs[1])
m2 = tree.nodes.new("ShaderNodeMath"); m2.operation = 'ADD'; m2.inputs[1].default_value = 0.0
tree.links.new(m1.outputs[0], m2.inputs[0])
m3 = tree.nodes.new("ShaderNodeMath"); m3.operation = 'MULTIPLY'; m3.inputs[1].default_value = 2.0
tree.links.new(m2.outputs[0], m3.inputs[0])
go = tree.nodes.new("NodeGroupOutput"); tree.links.new(m3.outputs[0], go.inputs[0])
merge_duplicates(tree)
check("no-op maths: x * 1 and x + 0 gone, x * 2 stays", count(tree, "ShaderNodeMath"), 1)
check("no-op maths: the value stays", ev(m3.outputs[0]), 6.0)

# what's known as the tree is built is that value: a product with 0 (a hit's flash left off)
# takes what fed it away, a mix at factor 0 is its A, all-constant maths is its result
tree = bpy.data.node_groups.new("_test", "ShaderNodeTree")
tree.interface.new_socket("Out", in_out='OUTPUT', socket_type='NodeSocketVector')
tree.interface.new_socket("Base", in_out='INPUT', socket_type='NodeSocketVector')
gi = tree.nodes.new("NodeGroupInput")
glow = tree.nodes.new("ShaderNodeVectorMath"); glow.operation = 'NORMALIZE'; glow.inputs[0].default_value = (1.0, 2.0, 2.0)
val = tree.nodes.new("ShaderNodeValue"); val.outputs[0].default_value = 5.0
flash = tree.nodes.new("ShaderNodeVectorMath"); flash.operation = 'SCALE'; tree.links.new(val.outputs[0], flash.inputs["Scale"])
off = tree.nodes.new("ShaderNodeVectorMath"); off.operation = 'MULTIPLY'; off.inputs[1].default_value = (0.0, 0.0, 0.0)
tree.links.new(flash.outputs[0], off.inputs[0])
tree.links.new(glow.outputs[0], flash.inputs[0])
add = tree.nodes.new("ShaderNodeVectorMath"); add.operation = 'ADD'
tree.links.new(gi.outputs[0], add.inputs[0]); tree.links.new(off.outputs[0], add.inputs[1])
fac = tree.nodes.new("ShaderNodeMath"); fac.operation = 'MULTIPLY'; fac.inputs[0].default_value = 0.5; fac.inputs[1].default_value = 0.0
mix = tree.nodes.new("ShaderNodeMix"); mix.data_type = 'VECTOR'
tree.links.new(fac.outputs[0], mix.inputs["Factor"]); tree.links.new(add.outputs[0], mix.inputs[4]); tree.links.new(val.outputs[0], mix.inputs[5])
go = tree.nodes.new("NodeGroupOutput"); tree.links.new(mix.outputs[1], go.inputs[0])
merge_duplicates(tree)
check("settled: the flash, its product with 0 and the mix at 0 gone",
      count(tree, "ShaderNodeVectorMath") + count(tree, "ShaderNodeMix") + count(tree, "ShaderNodeMath") + count(tree, "ShaderNodeValue"), 0)
check("settled: the output reads the base itself", int(go.inputs[0].links[0].from_node.bl_idname == "NodeGroupInput"), 1)
tree = bpy.data.node_groups.new("_test", "ShaderNodeTree")
tree.interface.new_socket("Out", in_out='OUTPUT', socket_type='NodeSocketFloat')
a = tree.nodes.new("ShaderNodeMath"); a.operation = 'POWER'; a.inputs[0].default_value = 2.0; a.inputs[1].default_value = 3.0
b = tree.nodes.new("ShaderNodeMath"); b.operation = 'SUBTRACT'; b.use_clamp = True; b.inputs[0].default_value = 9.5
tree.links.new(a.outputs[0], b.inputs[1])
go = tree.nodes.new("NodeGroupOutput"); tree.links.new(b.outputs[0], go.inputs[0])
merge_duplicates(tree)
check("settled: constant maths folds to the group output's own value", (count(tree, "ShaderNodeMath"), round(go.inputs[0].default_value, 4)), (0, 1.0))

print("[translator_test] %d passed, %d failed" % (PASSES[0], len(FAILS)))
for f in FAILS:
    print("  FAIL", f)
if FAILS:
    sys.exit(1)
