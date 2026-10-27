"""The numpy evaluator behind grass_masks: ue_graph's graphs walked with ue_graph's rules (static switches, function
calls inlined with lazily bound inputs, Material Attributes made when read), each value a 2D array (rows: vertices or
1, columns: the value's width). An expression without a rule here is counted in Evaluator.unknown and reads 1."""
import math

import numpy as np

from .ue_graph.core import (ATTRIBUTES_INPUT, BREAK_ORDER, COMPILE_SWITCHES, INPUT_WIDTH, MAKE_PINS, T, UE_DEFAULTS,
                            LazyInput, attribute_default, attribute_name, linked, load_graph, short_name)
from .ue_graph.functions import FunctionsMixin
from .ue_graph.statics import StaticsMixin
from .ue_graph.translator import Translator

DT = np.float32
PROBE = ((-1e9, -1e9, 0.0), (1e9, 1e9, 0.0), (0.0, 0.0, 0.0), (1e5, 0.0, 0.0), (0.0, 1e5, 0.0))    # maptex.probe's


def _pixels(image):
    """(H, W, 4) float32, row 0 the top (UE's v = 0); sRGB bytes decoded to linear, as a sampler reads them."""
    w, h = image.size
    px = np.empty(w * h * 4, dtype=DT)
    image.pixels.foreach_get(px)
    px = px.reshape(h, w, 4)[::-1]
    if not image.is_float and image.colorspace_settings.name == "sRGB":
        c = px[..., :3]
        px = px.copy()
        px[..., :3] = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    return np.ascontiguousarray(px)


def _bilinear(px, u, v, wrap=True):
    """px (H, W, C) at UE uv (top left), bilinear: (n, C)."""
    h, w = px.shape[:2]
    x, y = u * w - 0.5, v * h - 0.5
    x0, y0 = np.floor(x), np.floor(y)
    fx, fy = (x - x0)[:, None], (y - y0)[:, None]
    x0, y0 = x0.astype(np.int64), y0.astype(np.int64)
    if wrap:
        xs, ys = (x0 % w, (x0 + 1) % w), (y0 % h, (y0 + 1) % h)
    else:
        xs, ys = (np.clip(x0, 0, w - 1), np.clip(x0 + 1, 0, w - 1)), (np.clip(y0, 0, h - 1), np.clip(y0 + 1, 0, h - 1))
    top = px[ys[0], xs[0]] * (1 - fx) + px[ys[0], xs[1]] * fx
    bottom = px[ys[1], xs[0]] * (1 - fx) + px[ys[1], xs[1]] * fx
    return (top * (1 - fy) + bottom * fy).astype(DT)


class Attrs:
    """Material Attributes: each attribute's value made when first read (thunks, else `derive(name)`, else the base's,
    else UE's default)."""

    def __init__(self, base=None, thunks=None, derive=None):
        self.base, self.thunks, self.derive, self.vals = base, dict(thunks or {}), derive, {}

    def get(self, name):
        if name not in self.vals:
            if name in self.thunks:
                v = self.thunks[name]()
            elif self.derive is not None:
                v = self.derive(name)
            elif self.base is not None:
                v = self.base.get(name)
            else:
                v = _const_of(attribute_default(name))
            self.vals[name] = v
        return self.vals[name]


def _const_of(val):
    """A ue_graph constant Val as a numpy value."""
    s = val.s
    return np.array([tuple(s)[:max(val.w, 1)] if isinstance(s, (tuple, list)) else (float(s),)], dtype=DT)


class Evaluator(StaticsMixin, FunctionsMixin):
    """One terrain's grass masks: ue_graph's graphs, numpy values (2D arrays, rows vertices or 1, columns width)."""
    declaration = staticmethod(Translator.declaration)

    def __init__(self, env, terrain, n=None, position=None):
        self.env, self.terrain = env, terrain
        self.n = terrain.n if n is None else n
        self.position = terrain.position if position is None else position
        self.graphs, self.cache, self.section = {}, {}, []
        self.warnings, self.unknown, self.stand_ins = [], {}, set()
        self._stack = []

    # plumbing ue_graph's mixins use
    def _hook(self, name, fallback, *args):
        f = getattr(self.env, name, None)
        if f is not None:
            v = f(*args)
            if v is not None:
                return v
        return fallback()

    def static_switch(self, name, default):
        return bool(self._hook("static_switch", lambda: default, name, default))

    def landscape_layer(self, name):
        return self.terrain.painted(name) if not self.terrain.world else name in self.terrain.layers()

    def graph(self, path):
        g = self.graphs.get(path)
        if g is None:
            g = self.graphs[path] = load_graph(path)
        return g

    # values
    @staticmethod
    def const(*c):
        return np.array([c], dtype=DT)

    def _unknown(self, what):
        self.unknown[what] = self.unknown.get(what, 0) + 1
        return self.const(1.0)

    @staticmethod
    def w(a):
        return a.shape[1]

    def pair(self, a, b):
        """Operands of a binary op: float1 broadcasts, else both cut to the narrower (UE would refuse)."""
        if a.shape[1] != b.shape[1] and a.shape[1] > 1 and b.shape[1] > 1:
            k = min(a.shape[1], b.shape[1])
            return a[:, :k], b[:, :k]
        return a, b

    def mask(self, a, idx):
        if a.shape[1] == 1:
            return np.repeat(a, len(idx), axis=1) if len(idx) > 1 else a
        return np.stack([a[:, i] if i < a.shape[1] else np.zeros(a.shape[0], DT) for i in idx], axis=1)

    def append(self, parts):
        rows = max(p.shape[0] for p in parts)
        return np.concatenate([np.broadcast_to(p, (rows, p.shape[1])) for p in parts], axis=1).astype(DT)

    def as3(self, a):
        if a.shape[1] == 1:
            return self.mask(a, [0, 0, 0])
        if a.shape[1] == 2:
            return self.append([a, self.const(0.0)])
        return a[:, :3]

    # evaluation (Translator's walk)
    def input(self, g, ref, scope, default):
        if not linked(ref):
            return default
        v = self.expr(g, g.key(ref), ref.get("OutputIndex", 0) or 0, scope)
        if ref.get("Mask") and isinstance(v, np.ndarray):
            v = self.mask(v, [i for i, k in enumerate(("MaskR", "MaskG", "MaskB", "MaskA")) if ref.get(k)])
        return v

    def value(self, g, ref, scope, default):
        """input() as a plain value (Material Attributes read as Base Color, as the translator's pin does)."""
        v = self.input(g, ref, scope, default)
        if isinstance(v, Attrs):
            self.stand_ins.add("Material Attributes read as a value: their Base Color")
            v = v.get("BaseColor")
        return v

    def pin(self, g, x, name, scope, fallback=0.0):
        p = x.get("Properties") or {}
        const_name = {"A": "ConstA", "B": "ConstB", "Alpha": "ConstAlpha", "Exponent": "ConstExponent", "X": "ConstX",
                      "Y": "ConstY", "Min": "ConstMin", "Max": "ConstMax", "Value": "ConstValue"}.get(name)
        d = p.get(const_name) if const_name and const_name in p and not isinstance(p.get(const_name), dict) \
            else UE_DEFAULTS.get((T(x), const_name), fallback)
        return self.value(g, p.get(name), scope, self.const(float(d)))

    def expr(self, g, name, out, scope):
        key = (g.path, name, out, scope.get("_id", 0))
        if key in self.cache:
            return self.cache[key]
        if key in self._stack:
            raise RuntimeError("cycle in %s at %s" % (g.name, name))
        self._stack.append(key)
        try:
            x = g.by[name]
            t = T(x)
            p = x.get("Properties") or {}
            if t in ("Reroute", "NamedRerouteDeclaration"):
                v = self.input(g, p.get("Input"), scope, self.const(0.0))
            elif t == "NamedRerouteUsage":
                decl = self.declaration(g, p)
                v = self.input(g, decl["Properties"].get("Input"), scope, self.const(0.0)) if decl else self.const(0.0)
            else:
                v = self.evaluate(g, x, t, out, scope, p)
        finally:
            self._stack.pop()
        if isinstance(v, np.ndarray) and v.dtype != DT:
            v = v.astype(DT)
        self.cache[key] = v
        return v

    def evaluate(self, g, x, t, out, scope, p):
        P = lambda n, **k: self.pin(g, x, n, scope, **k)      # noqa: E731
        V = lambda n, d: self.value(g, p.get(n), scope, d)     # noqa: E731
        c = self.const
        if t in ("Add", "Subtract", "Multiply", "Divide", "Max", "Min", "Fmod", "Modulo"):
            a, b = self.pair(P("A"), P("B"))
            with np.errstate(divide="ignore", invalid="ignore"):
                r = {"Add": np.add, "Subtract": np.subtract, "Multiply": np.multiply, "Divide": np.divide,
                     "Max": np.maximum, "Min": np.minimum, "Fmod": np.fmod, "Modulo": np.fmod}[t](a, b)
            return np.nan_to_num(r, nan=0.0, posinf=1e30, neginf=-1e30) if t in ("Divide", "Fmod", "Modulo") else r
        if t == "Power":
            a, b = self.pair(P("Base", fallback=1.0), P("Exponent"))
            with np.errstate(invalid="ignore", over="ignore"):
                return np.nan_to_num(np.power(np.maximum(a, 0.0), b))       # PositiveClampedPow
        if t == "LinearInterpolate":
            al = P("Alpha")
            if al.shape[0] == 1 and al.shape[1] == 1 and float(al[0, 0]) in (0.0, 1.0):
                return P("B" if al[0, 0] else "A")
            return self.lerp(P("A"), P("B"), al)
        if t == "OneMinus":
            return 1.0 - P("Input")
        if t == "Saturate":
            return np.clip(P("Input"), 0.0, 1.0)
        if t in ("Abs", "Floor", "Ceil", "Frac", "Round", "Sign", "Truncate"):
            f = {"Abs": np.abs, "Floor": np.floor, "Ceil": np.ceil, "Frac": lambda v: v - np.floor(v),
                 "Round": np.round, "Sign": np.sign, "Truncate": np.trunc}[t]
            return f(P("Input"))
        if t in ("Sine", "Cosine"):
            period = float(p.get("Period", 1.0))
            v = P("Input") * (2 * math.pi / period if period > 0 else 1.0)
            return np.sin(v) if t == "Sine" else np.cos(v)
        if t == "SquareRoot":
            return np.sqrt(np.maximum(P("Input"), 0.0))
        if t in ("Exponential", "Exponential2"):
            return np.exp(P("Input")) if t == "Exponential" else np.exp2(P("Input"))
        if t in ("Logarithm", "Logarithm2", "Logarithm10"):
            v = np.maximum(V("X" if "X" in p else "Input", c(1.0)), 1e-30)
            return {"Logarithm": np.log, "Logarithm2": np.log2, "Logarithm10": np.log10}[t](v)
        if t == "Clamp":
            v = P("Input")
            mode = str(p.get("ClampMode", "CMODE_Clamp")).split("::")[-1]
            if mode != "CMODE_ClampMax":
                v = np.maximum(*self.pair(v, V("Min", c(float(p.get("MinDefault", 0.0))))))
            if mode != "CMODE_ClampMin":
                v = np.minimum(*self.pair(v, V("Max", c(float(p.get("MaxDefault", 1.0))))))
            return v
        if t == "Step":
            y, xv = self.pair(P("Y"), P("X"))
            return (y >= xv).astype(DT)           # step(y, x) = x >= y
        if t == "SmoothStep":
            lo, hi, v = P("Min"), P("Max"), P("Value")
            with np.errstate(divide="ignore", invalid="ignore"):
                k = np.clip(np.nan_to_num((v - lo) / (hi - lo)), 0.0, 1.0)
            return k * k * (3.0 - 2.0 * k)
        if t == "InverseLinearInterpolate":
            a, b = V("A", c(float(p.get("ConstA", 0.0)))), V("B", c(float(p.get("ConstB", 1.0))))
            v = V("Value", c(float(p.get("ConstValue", 0.0))))
            with np.errstate(divide="ignore", invalid="ignore"):
                r = np.nan_to_num((v - a) / (b - a))
            return np.clip(r, 0.0, 1.0) if p.get("bClampResult") else r
        if t == "ConstantBiasScale":
            return (V("Input", c(0.0)) + float(p.get("Bias", 1.0))) * float(p.get("Scale", 0.5))
        if t == "If":
            a = self.mask(P("A"), [0])
            b = self.mask(V("B", c(float(p.get("ConstB", 0.0)))), [0])
            gt = V("AGreaterThanB", c(0.0))
            lt = V("ALessThanB", c(0.0))
            gt, lt = self.pair(gt, lt)
            r = np.where(a >= b, gt, lt)
            eq = V("AEqualsB", None)
            if eq is not None:
                r, eq = self.pair(r, eq)
                r = np.where(np.abs(a - b) > float(p.get("EqualsThreshold", 0.00001)), r, eq)
            return r
        if t in ("DotProduct", "CrossProduct", "Distance", "Normalize", "Length"):
            if t == "Normalize":
                v = self.as3(V("VectorInput", c(0.0)))
                return v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-12)
            if t == "Length":
                v = P("Input")
                return np.linalg.norm(v, axis=1, keepdims=True)
            a, b = self.pair(P("A"), P("B"))
            if t == "DotProduct":
                return np.sum(a * b, axis=1, keepdims=True)
            if t == "Distance":
                return np.linalg.norm(a - b, axis=1, keepdims=True)
            return np.cross(self.as3(a), self.as3(b))
        if t == "ComponentMask":
            return self.mask(P("Input"), [i for i, k in enumerate("RGBA") if p.get(k)])
        if t == "AppendVector":
            return self.append([P("A"), P("B")])
        if t == "Convert":
            return self.convert(g, x, out, scope, p)
        if t == "Constant":
            return c(p.get("R", 0.0))
        if t == "Constant2Vector":
            return c(p.get("R", 0.0), p.get("G", 0.0))
        if t in ("Constant3Vector", "Constant4Vector"):
            k = p.get("Constant", {})
            return c(*[k.get(ch, 0.0) for ch in ("RGBA" if t == "Constant4Vector" else "RGB")])
        if t == "Desaturation":
            v = self.as3(V("Input", c(0.0)))
            lf = p.get("LuminanceFactors") or {}
            lum = np.sum(v * (lf.get("R", 0.3), lf.get("G", 0.59), lf.get("B", 0.11)), axis=1, keepdims=True)
            f = V("Fraction", c(1.0))
            return v + (lum - v) * f
        if t == "SphereMask":
            a, b = self.pair(P("A"), P("B"))
            radius = V("Radius", c(float(p.get("AttenuationRadius", 256.0))))
            hard = V("Hardness", c(float(p.get("HardnessPercent", 100.0)) * 0.01))
            d = np.linalg.norm(a - b, axis=1, keepdims=True)
            nd = d / np.maximum(radius, 0.00001)
            return np.clip((1.0 - nd) / np.maximum(1.0 - hard, 0.00001), 0.0, 1.0)
        # compile time (ue_graph.statics)
        if t in ("StaticSwitch", "StaticSwitchParameter"):
            return self.input(g, p.get("A" if self.switch_value(g, x, scope) else "B"), scope, c(0.0))
        if t in ("StaticBool", "StaticBoolParameter"):
            on = bool(p.get("Value", False)) if t == "StaticBool" else \
                self.static_switch(p.get("ParameterName"), bool(p.get("DefaultValue", False)))
            return c(1.0 if on else 0.0)
        if t in COMPILE_SWITCHES or t == "LandscapeLayerSwitch":
            return self.input(g, self.switch_pin(x), scope, c(0.0))
        if t == "StaticComponentMaskParameter":
            dflt = tuple(bool(p.get("Default" + ch, False)) for ch in "RGBA")
            m = self._hook("static_mask", lambda: dflt, p.get("ParameterName"), dflt)
            idx = [i for i, on in enumerate(m) if on]
            return self.mask(V("Input", c(0.0)), idx) if idx else c(0.0)
        if t == "Switch":
            sv = V("SwitchValue", c(float(p.get("ConstSwitchValue", 0.0))))[:, :1]
            r = V("Default", c(float(p.get("ConstDefault", 0.0))))
            idx = np.floor(sv)
            for i, it in enumerate(p.get("Inputs") or []):
                v = self.value(g, it.get("Input"), scope, None)
                if v is not None and np.any(idx == i):
                    r, v = self.pair(r, v)
                    r = np.where(idx == i, v, r)
            return r
        if t == "IfThenElse":
            cond = self.static_bool(g, p.get("Condition"), scope)
            if cond is None:
                cond = bool(np.all(V("Condition", c(0.0))[:, 0] != 0.0))
            return self.input(g, p.get("True" if cond else "False"), scope, c(0.0))
        # parameters
        if t == "ScalarParameter":
            return c(self.env.scalar(p.get("ParameterName"), float(p.get("DefaultValue", 0.0))))
        if t in ("VectorParameter", "DoubleVectorParameter"):
            d = p.get("DefaultValue", {})
            rgba = self.env.vector(p.get("ParameterName"), (d.get("R", d.get("X", 0.0)), d.get("G", d.get("Y", 0.0)),
                                                            d.get("B", d.get("Z", 0.0)), d.get("A", d.get("W", 0.0))))
            return self.rgba(c(*rgba[:3]), c(rgba[3]), out)
        if t == "CollectionParameter":
            got = self.env.collection(p.get("ParameterName"), self.tex_key(p.get("Collection")))
            if got is None:
                return self._unknown("CollectionParameter %s" % p.get("ParameterName"))
            return c(got[0]) if len(got) == 1 else self.rgba(c(*got[:3]), c(got[3]), out)
        # the terrain
        if t == "WorldPosition":
            if "CameraRelative" in str(p.get("WorldPositionShaderOffset", "")):
                self.stand_ins.add("camera-relative world position: the camera at the origin")
            return self.position
        if t in ("ActorPositionWS", "ObjectPositionWS"):
            return self.terrain.actor
        if t in ("VertexNormalWS", "PixelNormalWS"):
            if t == "PixelNormalWS":
                self.stand_ins.add("pixel normal as the vertex normal")
            return self.terrain.normal if self.n == self.terrain.n else np.broadcast_to(c(0.0, 0.0, 1.0), (self.n, 3))
        if t == "TextureCoordinate":
            uv = self.uv(int(p.get("CoordinateIndex", 0)))
            return uv * (float(p.get("UTiling", 1.0)), float(p.get("VTiling", 1.0)))
        if t == "LandscapeLayerCoords":
            return self.layer_coords(p)
        if t in ("LandscapeLayerSample", "LandscapeLayerWeight", "LandscapeLayerBlend", "LandscapeVisibilityMask"):
            return self.landscape(g, t, scope, p)
        if t in ("TextureObject", "TextureObjectParameter"):
            return ("texture", self.tex_param(p, self.tex_key(p.get("Texture"))))
        if t in ("TextureSample", "TextureSampleParameter2D"):
            return self.texture(g, x, out, scope, p)
        # functions
        if t == "FunctionInput":
            return self.function_input(g, x, scope, p)
        if t == "FunctionOutput":
            return self.input(g, p.get("A"), scope, c(0.0))
        if t == "MaterialFunctionCall":
            return self.call(g, x, out, scope)
        v = self.evaluate_attributes(g, x, t, out, scope, p)
        if v is not None:
            return v
        return self._unknown("%s (%s)" % (t, g.name))

    # Material Attributes (ue_graph.attributes' rules), each attribute evaluated when read
    def attribute(self, g, ref, scope, name):
        def make():
            v = self.input(g, ref, scope, None)
            if isinstance(v, Attrs):
                v = v.get(name)
            return _const_of(attribute_default(name)) if v is None else v
        return make

    def attrs(self, g, ref, scope):
        v = self.input(g, ref, scope, None)
        return v if isinstance(v, Attrs) else Attrs()

    def evaluate_attributes(self, g, x, t, out, scope, p):
        if t == "MakeMaterialAttributes":
            return Attrs(None, {MAKE_PINS[k]: self.attribute(g, r, scope, MAKE_PINS[k])
                                for k, r in p.items() if k in MAKE_PINS and linked(r)})
        if t == "SetMaterialAttributes":
            ins = p.get("Inputs") or []
            base = self.attrs(g, ins[0], scope) if ins else Attrs()
            thunks = {}
            for k, guid in enumerate(p.get("AttributeSetTypes") or []):
                ref = ins[k + 1] if k + 1 < len(ins) else None
                if linked(ref):
                    name = attribute_name(guid, ref.get("InputName"))
                    thunks[name] = self.attribute(g, ref, scope, name)
            return Attrs(base, thunks)
        if t == "GetMaterialAttributes":
            a = self.attrs(g, p.get("MaterialAttributes"), scope)
            if out == 0:
                return a
            types, outs = p.get("AttributeGetTypes") or [], p.get("Outputs") or []
            if out - 1 >= len(types):
                return self.const(0.0)
            label = outs[out].get("OutputName") if out < len(outs) and isinstance(outs[out], dict) else None
            return a.get(attribute_name(types[out - 1], label))
        if t == "BreakMaterialAttributes":
            a = self.attrs(g, p.get("MaterialAttributes"), scope)
            return a.get(BREAK_ORDER[out]) if out < len(BREAK_ORDER) else self.const(0.0)
        if t == "BlendMaterialAttributes":
            a, b = self.attrs(g, p.get("A"), scope), self.attrs(g, p.get("B"), scope)
            alpha = lambda: self.mask(self.value(g, p.get("Alpha"), scope, self.const(0.0)), [0])   # noqa: E731
            mode = str(p.get("PixelAttributeBlendType", "EMaterialAttributeBlend::Blend")).split("::")[-1]
            return Attrs(None, {}, lambda name: a.get(name) if mode == "UseA" else b.get(name) if mode == "UseB"
                         else self.lerp(a.get(name), b.get(name), alpha()))
        if t == "ShadingModel":
            return self.const(1.0)
        return None

    def lerp(self, a, b, al):
        a, b = self.pair(a, b)
        a, al = self.pair(a, al)
        b, al = self.pair(b, al)
        return a + (b - a) * al

    def rgba(self, rgb, a, out):
        """A colour expression's outputs: RGB, R, G, B, A, RGBA."""
        if out == 5:
            return self.append([rgb, a])
        if out == 4:
            return a
        return rgb if out == 0 else rgb[:, out - 1:out]

    def convert(self, g, x, out, scope, p):
        ins, outs, maps = p.get("ConvertInputs") or [], p.get("ConvertOutputs") or [], p.get("ConvertMappings") or []
        if out >= len(outs):
            return self.const(0.0)
        width = {"Scalar": 1, "Vector2": 2, "Vector3": 3, "Vector4": 4}.get(str((outs[out] or {}).get("Type", "")).split("::")[-1], 1)
        d = outs[out].get("DefaultValue") or {}
        parts = []
        for k in range(width):
            m = next((m for m in maps if (m.get("OutputIndex", 0) or 0) == out and (m.get("OutputComponentIndex", 0) or 0) == k), None)
            v = None
            if m is not None and (m.get("InputIndex", 0) or 0) < len(ins):
                i, ci = m.get("InputIndex", 0) or 0, m.get("InputComponentIndex", 0) or 0
                v = self.value(g, ins[i].get("ExpressionInput"), scope, None)
                if v is None:
                    v = self.const(float((ins[i].get("DefaultValue") or {}).get("RGBA"[min(ci, 3)], 0.0)))
                else:
                    v = v if v.shape[1] == 1 else (v[:, ci:ci + 1] if ci < v.shape[1] else self.const(0.0))
            parts.append(v if v is not None else self.const(float(d.get("RGBA"[min(k, 3)], 0.0))))
        return self.append(parts)

    # functions (ue_graph.functions: inlined, inputs bound lazily)
    def call(self, g, x, out, scope):
        p = x["Properties"]
        found = self.function_graph(x)
        if found is None:
            return self._unknown("function %s (no graph)" % (p.get("MaterialFunction") or {}).get("ObjectName", "?"))
        fg = found[1]
        fo = self.function_output(fg, p.get("FunctionOutputs", []), out)
        if fo is None:
            return self.const(0.0)
        sub = {fi.get("ExpressionInputId"): LazyInput(g, fi.get("Input"), scope, self.section)
               for fi in p.get("FunctionInputs", [])}
        sub["_id"] = (id(x), g.path, scope.get("_id", 0))
        return self.input(fg, fo["Properties"].get("A"), sub, self.const(0.0))

    def function_input(self, g, x, scope, p):
        bound = scope.get(p.get("Id"))
        if isinstance(bound, LazyInput):
            bound = bound.get(self)
        if bound is not None:
            w = INPUT_WIDTH.get(p.get("InputType"))
            if isinstance(bound, np.ndarray) and w is not None and w < bound.shape[1]:
                bound = bound[:, :w]
            return bound
        if p.get("InputType") == ATTRIBUTES_INPUT:
            pv = self.input(g, p.get("Preview"), scope, None)
            return pv if isinstance(pv, Attrs) else Attrs()
        if p.get("bUsePreviewValueAsDefault"):
            pv = self.input(g, p.get("Preview"), scope, None)
            if pv is not None:
                return pv
            v = p.get("PreviewValue", {})
            w = {"EFunctionInputType::FunctionInput_Scalar": 1, "EFunctionInputType::FunctionInput_Vector2": 2,
                 "EFunctionInputType::FunctionInput_Vector4": 4}.get(p.get("InputType"), 3)
            return self.const(*[float(v.get(k, 0.0)) for k in "XYZW"][:w])
        return self.const(0.0)

    # landscape
    def uv(self, index):
        if self.n != self.terrain.n:            # a probe: the world in 1 m quads
            return self.position[:, :2] / 100.0
        return self.terrain.uv(index)

    def layer_coords(self, p):
        mapping = str(p.get("MappingType", ""))
        index = 1 if mapping.endswith("XZ") else 2 if mapping.endswith("YZ") else 0
        if self.terrain.world or self.n != self.terrain.n:
            a, b = ((0, 1), (0, 2), (1, 2))[index]
            base = self.position[:, (a, b)] / 100.0
        else:
            base = self.terrain.uv(index)
        scale = 1.0 / (float(p.get("MappingScale", 0.0)) or 1.0)
        rot = math.radians(float(p.get("MappingRotation", 0.0)))
        co, si = math.cos(rot) * scale, math.sin(rot) * scale
        u = base[:, 0] * co + base[:, 1] * si + float(p.get("MappingPanU", 0.0))
        v = -base[:, 0] * si + base[:, 1] * co + float(p.get("MappingPanV", 0.0))
        return np.stack([u, v], axis=1).astype(DT)

    def layer(self, name):
        """A layer's weight (n, 1), None for a layer the terrain doesn't have."""
        if not name or self.n != self.terrain.n:
            return None
        w = self.terrain.weight(name)
        return None if w is None else w[:, None]

    def landscape(self, g, t, scope, p):
        """renderer.landscape_layers' rules on the painted weights."""
        c = self.const
        if t == "LandscapeLayerSample":
            w = self.layer(p.get("ParameterName"))
            return w if w is not None else c(0.0)
        if t == "LandscapeVisibilityMask":
            w = self.layer("__LANDSCAPE_VISIBILITY__")
            return c(1.0) if w is None else 1.0 - w
        if t == "LandscapeLayerWeight":
            cb = p.get("ConstBase") or {}
            base = self.value(g, p.get("Base"), scope, None)
            if base is None:
                base = c(float(cb.get("X", 0.0)), float(cb.get("Y", 0.0)), float(cb.get("Z", 0.0)))
            w = self.layer(p.get("ParameterName"))
            if w is None:
                return base
            layer = self.value(g, p.get("Layer"), scope, None)
            if layer is None:
                return base
            base, layer = self.pair(base, layer)
            return base + layer * w
        terms, heights, alpha = [], [], []
        for layer in p.get("Layers") or []:
            w = self.layer(layer.get("LayerName"))
            if w is None:
                continue
            v = self.input(g, layer.get("LayerInput"), scope, None)
            if v is None:
                cl = layer.get("ConstLayerInput") or {}
                v = c(float(cl.get("X", 0.0)), float(cl.get("Y", 0.0)), float(cl.get("Z", 0.0)))
            kind = str(layer.get("BlendType", ""))
            if kind.endswith("HeightBlend"):
                h = self.value(g, layer.get("HeightInput"), scope, None)
                h = c(float(layer.get("ConstHeightInput", 0.5))) if h is None else h[:, :1]
                heights.append((v, np.clip(w * 2.0 - 1.0 + h, 0.0001, 1.0)))
            elif kind.endswith("AlphaBlend"):
                alpha.append((v, w))
            else:
                terms.append((v, w))
        if heights:
            total = sum(mw for _, mw in heights)
            terms += [(v, mw / total) for v, mw in heights]
        if any(isinstance(v, Attrs) for v, _ in terms + alpha):
            pick = lambda v, name: v.get(name) if isinstance(v, Attrs) else v      # noqa: E731
            return Attrs(None, {}, lambda name: self.blend([(pick(v, name), w) for v, w in terms],
                                                          [(pick(v, name), w) for v, w in alpha]))
        return self.blend(terms, alpha)

    def blend(self, terms, alpha):
        out = None
        for v, w in terms:
            out = v * w if out is None else sum(self.pair(out, v * w))
        out = self.const(0.0) if out is None else out
        for v, w in alpha:
            out = self.lerp(out, v, w)
        return out

    # textures
    def texture(self, g, x, out, scope, p):
        key = self.tex_param(p, self.tex_key(p.get("Texture")))
        ref = self.input(g, p.get("TextureObject"), scope, None)
        if isinstance(ref, tuple) and ref[0] == "texture":
            key = ref[1]
        coords = self.value(g, p.get("Coordinates"), scope, None)
        if coords is None:
            coords = self.uv(0)
        if coords.shape[1] == 1:
            coords = self.mask(coords, [0, 0])
        param = getattr(key, "param", None) or p.get("ParameterName")
        px = None
        painted = self.terrain.biome.get(param) if param and self.n == self.terrain.n else None
        if painted is not None:
            px = self.painted_map(g, p, scope, param, painted)
        if px is None:
            pixels = self.env.pixels(str(key)) if key else None
            if pixels is None:
                return self._unknown("texture %s (not found)" % short_name(str(key or "?")))
            src = str(p.get("SamplerSource", "")).split("::")[-1]
            wrap = not ("Clamp" in src or "Terrain" in src)
            px = _bilinear(pixels, coords[:, 0].astype(np.float64), coords[:, 1].astype(np.float64), wrap)
            if px.shape[0] == 1 and self.n > 1:
                px = np.broadcast_to(px, (self.n, 4))
        rgb, a = px[:, :3], px[:, 3:4]
        return self.rgba(rgb, a, out)

    def painted_map(self, g, p, scope, param, image):
        """A custom terrain's own map for a map-wide texture (maptex): the rectangle this sample reads, through UV map
        mp_weights; channels hidden by their hide keys read 0."""
        from .ue_graph import maptex
        from . import biome_maps
        found = self.probe(g, p.get("Coordinates"), scope)
        mat = self.terrain.obj.active_material
        rects = (biome_maps.maps(mat).get(param) or {}).get("rects")
        rect = maptex.rect_of(found, rects) if found is not None else None
        uv = self.terrain.weights_uv()
        if rect is None or uv is None:
            return None
        u0, v0, u1, v1 = rect
        px = _bilinear(_pixels(image), u0 + uv[:, 0] * (u1 - u0), v0 + uv[:, 1] * (v1 - v0), wrap=False)
        for ch in range(4):
            if self.terrain.obj.get(maptex.hide_key(image.name, rect, ch)):
                px[:, ch] = 0.0
        return px

    def probe(self, g, ref, scope):
        """ue_graph.maptex.probe's answer, the coordinates evaluated at its five world points."""
        if not linked(ref):
            return None
        sub = Evaluator(self.env, self.terrain, n=len(PROBE), position=np.array(PROBE, dtype=np.float64))
        sub.graphs = self.graphs
        try:
            v = sub.value(g, ref, dict(scope, _id=("probe", scope.get("_id"))), None)
        except Exception:
            return None
        if v is None or v.shape[1] < 2:
            return None
        v = np.broadcast_to(v[:, :2], (len(PROBE), 2)).astype(np.float64)
        lo, hi, o, x1, y1 = [tuple(r) for r in v]
        per_km = max(abs(x1[0] - o[0]), abs(y1[1] - o[1]))
        if abs(x1[0] - o[0]) + abs(x1[1] - o[1]) < 1e-9 or abs(y1[0] - o[0]) + abs(y1[1] - o[1]) < 1e-9:
            return None
        if all(0.0 <= k <= 1.0 for k in lo + hi) and abs(hi[0] - lo[0]) > 1e-6 and abs(hi[1] - lo[1]) > 1e-6:
            return "rect", (lo[0], lo[1], hi[0], hi[1]), per_km
        if abs(x1[0] - o[0]) < 1.0 and abs(y1[1] - o[1]) < 1.0 and all(0.0 <= k < 1.0 for k in o):
            return "corner", o, per_km
        return None

    def grass(self, g, ref):
        """One grass input, (n,) clamped 0..1."""
        v = self.value(g, ref, {}, self.const(0.0))
        v = np.clip(np.broadcast_to(v[:, 0], (self.n,)), 0.0, 1.0)
        return np.ascontiguousarray(v, dtype=DT)
