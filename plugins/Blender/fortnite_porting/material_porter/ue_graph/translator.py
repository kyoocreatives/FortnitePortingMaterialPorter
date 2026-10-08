"""Translates a UE material graph into Blender nodes: the base class (see the mixins for each kind of expression)."""

import contextlib
import math
from contextlib import contextmanager

from .core import (
    ATTRIBUTES_INPUT,
    Attrs,
    CARRIED,
    NORMAL_PASS,
    PIN_ATTRIBUTE,
    PIXEL_NORMAL,
    SECTION_KEY,
    T,
    TexRef,
    UE_DEFAULTS,
    Val,
    WATER_OUTPUTS,
    _Lazy,
    _MADE,
    _PY,
    _blender_vmath,
    _ident,
    _inner,
    attribute_default,
    linked,
    load_graph,
)
from .statics import StaticsMixin
from .attributes import AttributesMixin
from .maths import MathsMixin
from .geometry import GeometryMixin
from .renderer import RendererMixin
from .custom import CustomMixin
from .textures import TexturesMixin
from .functions import FunctionsMixin

class Translator(StaticsMixin, AttributesMixin, MathsMixin, GeometryMixin, RendererMixin, CustomMixin, TexturesMixin, FunctionsMixin):
    def __init__(self, tree, env, x0=0, y0=0, parent=None):
        self.tree, self.env = tree, env
        self.N, self.L = tree.nodes, tree.links
        self.cache = {}
        # a function call's group node, shared by every output read from it: (node, inputs linked)
        self.call_sites = {}
        self.shared_calls = set()       # names of the call nodes more than one output reads
        self.graphs = parent.graphs if parent else {}
        # material functions become shared node groups (env.nest_functions)
        self.functions = parent.functions if parent else {}
        # Material Attributes a function's group passes in and out: CARRIED, plus what this
        # material's build reads besides (a moving one's World Position Offset)
        self.carried = parent.carried if parent else CARRIED
        self.function = None
        self.x, self.y = x0, y0
        self.warnings = []
        # layout section new nodes go in (tools/layout.py): the material, then one frame
        # per called material function, nested as UE nests them
        self.section = []
        self._shared = {}
        self._made = _MADE.setdefault(tree.as_pointer(), {})
        if parent is None or parent.tree != tree:
            # a new tree can reuse a removed tree's address
            self._made.clear()
        # clip()s of the material's own graph: where one is 0 the pixel isn't drawn
        self.clips = []

    def activate(self):
        """Make this the translator the env builds nodes with; returns the previous one."""
        prev = getattr(self.env, "current", lambda: None)()
        if hasattr(self.env, "set_current"):
            self.env.set_current(self)
        return prev or self

    def solid(self, v):
        """A constant as a node, where a socket must be linked."""
        if v.w == 1:
            n = self.node("ShaderNodeValue", "constant")
            n.outputs[0].default_value = float(v.s)
            return Val(n.outputs[0], 1)
        n = self.node("ShaderNodeCombineXYZ", "constant")
        for i, c in enumerate(tuple(v.s)[:3]):
            n.inputs[i].default_value = c
        return Val(n.outputs[0], 3)

    @contextmanager
    def at(self, *path):
        """Make nodes under an absolute section path for a while."""
        saved = self.section
        self.section = list(path)
        try:
            yield
        finally:
            self.section = saved

    # ------------------------------------------------------------- nodes
    def node(self, kind, label="", **props):
        n = self.N.new(kind)
        n.location = (self.x, self.y)
        self.y -= 30
        n.label = label
        n[SECTION_KEY] = "/".join(self.section)
        for k, v in props.items():
            setattr(n, k, v)
        return n

    def reuse(self, key, make):
        """The value a node made for `key` (what it computes: kind, settings, inputs'
        _idents), else make()'s. Translation asks for one value in many places (a vector
        split per read, a mix per branch); this makes one node instead of many to fold away."""
        if key is None or None in key:
            return make()
        got = self._made.get(key)
        if got is None:
            got = self._made[key] = make()
        return got

    def link(self, val, sock):
        if val is None:
            return
        if isinstance(val.s, (Attrs, TexRef)):
            self.warnings.append("%s can't go into a socket (%s)" % (type(val.s).__name__, sock.name))
            return
        if val.const:
            v = val.s
            if sock.type == 'VECTOR' or sock.type == 'RGBA':
                if isinstance(v, (int, float)):
                    v = (v, v, v)
                v = tuple(v)[:3]
                if sock.type == 'RGBA':
                    v = v + (1.0,)
                sock.default_value = v
            else:
                sock.default_value = v if isinstance(v, (int, float)) else v[0]
        else:
            self.L.new(val.s, sock)

    def const(self, v, w=None):
        if isinstance(v, (int, float)):
            if w and w > 1:
                # a scalar asked for as a vector: broadcast (UE's cast)
                return self.const((float(v),) * (4 if w == 4 else 3), w)
            return Val(float(v), 1)
        if w == 1:
            return Val(float(tuple(v)[0]), 1)
        v = tuple(float(x) for x in v)
        if len(v) >= 4 and (w or 4) == 4:
            return Val(v[:3], 4, Val(v[3], 1))
        return Val(v + (0.0,) * (3 - len(v)) if len(v) < 3 else v[:3], w or len(v))

    def math(self, op, a, b=None, c=None, clamp=False, label=""):
        if a.const and (b is None or b.const) and (c is None or c.const) and not clamp and op in _PY:
            return Val(_PY[op](a.s, None if b is None else b.s), 1)
        if b is not None and c is None and not clamp:
            # x * 0, x * 1, x + 0, x - 0: known without a node (a weight of 0 drops its term)
            ka = a.s if a.const else None
            kb = b.s if b.const else None
            if op == 'MULTIPLY' and (ka == 0.0 or kb == 0.0):
                return Val(0.0, 1)
            if op == 'MULTIPLY' and kb == 1.0:
                return a
            if op == 'MULTIPLY' and ka == 1.0:
                return b
            if op in ('ADD', 'SUBTRACT') and kb == 0.0:
                return a
            if op == 'ADD' and ka == 0.0:
                return b
        flip = op == 'SUBTRACT' and c is None and not clamp and a.const and a.s == 1.0 and not b.const \
            and hasattr(b.s, "as_pointer")
        if flip:
            # 1 - (1 - x): x (a UV's v flipped into UE's space, then back for a texture)
            x = self._made.get(("1 - x", b.s.as_pointer()))
            if x is not None:
                return x

        def make():
            n = self.node("ShaderNodeMath", label or ("1 - x" if flip else op), operation=op, use_clamp=clamp)
            for i, v in enumerate((a, b, c)):
                if v is not None:
                    self.link(v, n.inputs[i])
            if flip and b.w == 1:
                self._made[("1 - x", n.outputs[0].as_pointer())] = b
            return Val(n.outputs[0], 1)
        return self.reuse(("math", op, clamp, _ident(a), _ident(b), _ident(c)), make)

    def vmath(self, op, a, b=None, c=None, label="", out_w=None):
        w = out_w or (a.w if op == 'SCALE' else max(x.w for x in (a, b, c) if x is not None))
        if c is None and all(x is None or x.const and isinstance(x.s, (int, float, tuple)) for x in (a, b)):
            # every input known: the result, as Blender's node computes it
            def vec(x):
                return None if x is None else (float(x.s),) * 3 if isinstance(x.s, (int, float)) else tuple(x.s)[:3]
            if op == 'SCALE':
                r = _blender_vmath(op, vec(a), float(b.s)) if isinstance(b.s, (int, float)) else None
            else:
                r = _blender_vmath(op, vec(a), vec(b))
            if isinstance(r, float):
                return Val(r, 1)
            if r is not None:
                return self.const(r, w)

        def make():
            n = self.node("ShaderNodeVectorMath", label or op, operation=op)
            if op == 'SCALE':
                # the factor has its own socket; inputs[1] is hidden for SCALE,
                # and a link to a hidden socket is silently ignored
                self.link(a, n.inputs[0])
                self.link(b, n.inputs["Scale"])
                return Val(n.outputs["Vector"], w)
            for i, v in enumerate((a, b, c)):
                if v is not None:
                    self.link(v, n.inputs[i])
            if op in ("DOT_PRODUCT", "LENGTH", "DISTANCE"):
                return Val(n.outputs["Value"], 1)
            return Val(n.outputs["Vector"], w)
        return self.reuse(("vmath", op, w, _ident(a), _ident(b), _ident(c)), make)

    def alpha(self, v):
        """The 4th component of a value: its own for a float4, the value
        itself for a float1 (UE broadcasts those)."""
        if v.w == 1:
            return v
        if v.a is not None:
            return v.a
        return self.const(0.0)

    def with_alpha(self, v, a):
        return Val(v.s, 4, a)

    def binop(self, op, a, b, label=""):
        w = max(a.w, b.w)
        if w == 1:
            return self.math(op, a, b, label=label)
        if w in (2, 3):
            def known(v, x):
                return v.const and all(float(c) == x for c in (v.s if isinstance(v.s, (tuple, list)) else (v.s,)))
            if op == 'MULTIPLY' and (known(a, 0.0) or known(b, 0.0)):
                return self.const((0.0,) * w, w)
            if op == 'MULTIPLY' and known(b, 1.0) and a.w == w:
                return a
            if op == 'MULTIPLY' and known(a, 1.0) and b.w == w:
                return b
            if op in ('ADD', 'SUBTRACT') and known(b, 0.0) and a.w == w:
                return a
            if op == 'ADD' and known(a, 0.0) and b.w == w:
                return b
        vop = {"FLOOR_DIVIDE": "DIVIDE"}.get(op, op)
        v = self.vmath(vop, a, b, label=label, out_w=w)
        if w == 4:
            v = self.with_alpha(v, self.math(op if op != "FLOOR_DIVIDE" else "DIVIDE", self.alpha(a), self.alpha(b)))
        return v

    def divide(self, a, b, label=""):
        """UE's A / B. Where B is 0 the GPU gives an infinity of A's sign, which a saturate
        turns into 1 or 0 (a fade over a length of 0 shows everything past its start); a
        Blender Divide gives 0 and the material drew nothing. A divisor that is a socket
        (a parameter, a function's input) is checked as the material runs; one known at
        build time and not zero is a plain Divide."""
        big = 1e18
        if b.const:
            values = b.s if isinstance(b.s, (tuple, list)) else (b.s,)
            if all(float(v) != 0.0 for v in values):
                return self.binop('DIVIDE', a, b, label=label)
            if b.w == 1:
                return self.binop('MULTIPLY', a, self.const(big), label=label)
        if b.w == 4:
            return self.binop('DIVIDE', a, b, label=label)
        # A over B, or over 1 / big where B is 0 (A's sign times big): B + (B is 0) / big
        if b.w != 1:
            # the same per component (a scale of 0 in Scale UVs By Center, a sprite's missing mouth,
            # pushes its UVs off the texture; Blender's 0 drew the mouth's middle over the whole
            # body): 1 - sign(|B|) is 1 where a component is 0
            zero = self.vmath('SUBTRACT', self.const(1.0, b.w),
                              self.vmath('SIGN', self.vmath('ABSOLUTE', b, out_w=b.w), out_w=b.w), out_w=b.w)
            safe = self.vmath('MULTIPLY_ADD', zero, self.const(1.0 / big, b.w), b, label="B, or tiny if 0", out_w=b.w)
            return self.binop('DIVIDE', a, safe, label=label)
        # Blender's compare is within 1e-5; a B that small gains 1 / big, nothing visible
        zero = self.math('COMPARE', b, self.const(0.0), self.const(0.0))
        safe = self.math('MULTIPLY_ADD', zero, self.const(1.0 / big), b, label="B, or tiny if 0")
        return self.binop('DIVIDE', a, safe, label=label)

    def unary(self, op, a):
        if a.w == 1:
            mop = {"FRACTION": "FRACT", "NORMALIZE": "SIGN"}.get(op, op)
            return self.math(mop, a)
        v = self.vmath(op, a, out_w=a.w)
        if a.w == 4:
            mop = {"FRACTION": "FRACT", "NORMALIZE": "SIGN"}.get(op, op)
            v = self.with_alpha(v, self.math(mop, self.alpha(a)))
        return v

    def saturate(self, a):
        if a.w == 1:
            return self.math('MULTIPLY', a, self.const(1.0), clamp=True, label="saturate")
        v = self.vmath('MINIMUM', self.vmath('MAXIMUM', a, self.const(0.0)), self.const(1.0), out_w=a.w)
        if a.w == 4:
            v = self.with_alpha(v, self.math('MULTIPLY', self.alpha(a), self.const(1.0), clamp=True))
        return v

    def clamp(self, v, mn, mx):
        """min(max(v, mn), mx) of floats: one Clamp node (its Min Max mode is that)."""
        if v.const and mn.const and mx.const:
            return Val(min(max(float(v.s), float(mn.s)), float(mx.s)), 1)

        def make():
            n = self.node("ShaderNodeClamp", "clamp", clamp_type='MINMAX')
            self.link(v, n.inputs["Value"]); self.link(mn, n.inputs["Min"]); self.link(mx, n.inputs["Max"])
            return Val(n.outputs[0], 1)
        return self.reuse(("clamp", _ident(v), _ident(mn), _ident(mx)), make)

    def lerp(self, a, b, t, label="lerp"):
        if isinstance(a.s, Attrs) or isinstance(b.s, Attrs):
            if t.const and t.w == 1 and t.s in (0.0, 1.0):
                return b if t.s else a
            return self._attrs_op(lambda x, y: self.lerp(x, y, t, label=label), a, b)
        w = max(a.w, b.w, t.w)
        if t.const and t.w == 1 and t.s in (0.0, 1.0) and (b if t.s else a).w == w:
            return b if t.s else a
        if a.const and b.const and a.w == b.w == w and a.s == b.s and (w != 4 or (a.a is not None and b.a is not None
                                                                                and a.a.const and b.a.const and a.a.s == b.a.s)):
            return a        # (between a value and itself: Hit Glow's colours, 0 and 0 at rest)
        if not a.const and w < 4 and a.w == b.w == w and _ident(a) == _ident(b):
            return a
        x = self._made.get(("1 - x", t.s.as_pointer())) if t.w == 1 and hasattr(t.s, "as_pointer") else None
        if x is not None:
            # lerp(a, b, 1 - x) is lerp(b, a, x): the 1 - x goes (a Step's, a OneMinus's)
            return self.lerp(b, a, x, label=label)

        def make():
            if w == 1:
                n = self.node("ShaderNodeMix", label, data_type='FLOAT', clamp_factor=False)
                self.link(t, n.inputs[0]); self.link(a, n.inputs[2]); self.link(b, n.inputs[3])
                return Val(n.outputs[0], 1)
            n = self.node("ShaderNodeMix", label, data_type='VECTOR', clamp_factor=False,
                          factor_mode='NON_UNIFORM' if t.w > 1 else 'UNIFORM')
            self.link(t, n.inputs[1] if t.w > 1 else n.inputs[0])
            self.link(a, n.inputs[4]); self.link(b, n.inputs[5])
            return Val(n.outputs[1], w)
        v = self.reuse(("lerp", w, _ident(a), _ident(b), _ident(t)), make)
        if w == 4:
            ta = self.alpha(t) if t.w > 1 else t
            v = self.with_alpha(v, self.lerp(self.alpha(a), self.alpha(b), ta))
        return v

    def rgba_out(self, rgb, a, out):
        """A colour expression's outputs: RGB, R, G, B, A, RGBA."""
        if out == 5:
            return self.with_alpha(rgb, a)
        if out == 4:
            return a
        return rgb if out == 0 else self.mask(rgb, [out - 1])

    def comps(self, v):
        """[x, y, z] floats of a value (width 1 broadcasts)."""
        if v.w == 1:
            return [v, v, v]
        if v.const:
            return [Val(c, 1) for c in v.s[:3]]
        # a vector put together here (UE's swizzles: append, then a mask): its parts as they were,
        # not a split of it, which drew a wire from where it was made to wherever it's read
        made = self._made.get(("parts", v.s.as_pointer())) if hasattr(v.s, "as_pointer") else None
        if made is not None:
            return list(made)

        def make():
            n = self.node("ShaderNodeSeparateXYZ", "split")
            self.link(v, n.inputs[0])
            return [Val(n.outputs[i], 1) for i in range(3)]
        return list(self.reuse(("split", _ident(v)), make))

    def combine(self, parts):
        parts = list(parts)
        if len(parts) == 1:
            return parts[0]
        if len(parts) > 3:
            v = self.combine(parts[:3])
            return Val(v.s, 4, parts[3] if parts[3].w == 1 else self.comps(parts[3])[0])
        if all(p.const for p in parts):
            return self.const(tuple(p.s for p in parts) + (0.0,) * (3 - len(parts)), len(parts))

        def make():
            n = self.node("ShaderNodeCombineXYZ", "append")
            for i, p in enumerate(parts):
                self.link(p, n.inputs[i])
            self._made[("parts", n.outputs[0].as_pointer())] = \
                [p if p.w == 1 else self.comps(p)[0] for p in parts] + [self.const(0.0)] * (3 - len(parts))
            return Val(n.outputs[0], len(parts))
        return self.reuse(("append",) + tuple(_ident(p) for p in parts), make)

    def mask(self, v, idx):
        """Components idx (0=R..3=A) of v."""
        if v.w == 1:
            return v if len(idx) == 1 else self.combine([v] * len(idx))
        cs = self.comps(v)
        parts = []
        for i in idx:
            if i < 3:
                parts.append(cs[i])
            elif v.a is not None:
                parts.append(v.a)
            else:
                self.warnings.append("alpha of a computed vector read as 1")
                parts.append(self.const(1.0))
        return self.combine(parts)

    def zero_z(self, v):
        return self.combine(self.comps(v)[:2] + [self.const(0.0)]) if v.w == 2 else v

    def flat(self, a, b):
        """Two operands of a length or distance. Where the wider is 2 wide, both with Z 0
        (a scalar against a float2 spreads over X and Y only)."""
        if max(a.w, b.w) != 2:
            return a, b
        return tuple(self.combine(self.comps(v)[:2] + [self.const(0.0)]) for v in (a, b))

    # -------------------------------------------------------- evaluation
    def graph(self, path):
        g = self.graphs.get(path)
        if g is None:
            g = self.graphs[path] = load_graph(path)
        return g

    def material_output(self, path, prop):
        g = self.graph(path)
        ed = g.editor_data()
        return self.input(g, ed["Properties"][prop], {}, None)

    def prune_unused(self):
        """Remove function groups no node uses (all their outputs went to the callers as
        pass-throughs or constants)."""
        import bpy
        while True:
            dead = [k for k, ft in self.functions.items() if ft.tree.users == 0]
            if not dead:
                return
            # one pass over the file for all of them (a removal each walked every datablock)
            bpy.data.batch_remove([self.functions.pop(k).tree for k in dead])

    def material_attributes(self, path, names=CARRIED):
        """A material's attributes Blender has a slot for, {name: Val}: from its
        MaterialAttributes pin, or its separate output pins (unlinked ones are UE's defaults)."""
        g = self.graph(path)
        pins = (g.editor_data() or {}).get("Properties") or {}
        self.carried = tuple(CARRIED) + tuple(n for n in names if n not in CARRIED)
        if linked(pins.get("MaterialAttributes")):
            v = self.input(g, pins["MaterialAttributes"], {}, None)
            attrs = v.s if v is not None and isinstance(v.s, Attrs) else Attrs()
            # what PixelNormalWS reads (pixel_normal): the Normal, made apart (a function's group
            # node would carry both the Normal and what reads it, a loop UE never has since it
            # compiles each property on its own); merge_duplicates folds what it can
            def normal():
                with self.normal_pass():
                    nv = self.input(g, pins["MaterialAttributes"], {"_id": PIXEL_NORMAL}, None)
                    return (nv.s if nv is not None and isinstance(nv.s, Attrs) else Attrs()).get("Normal")
            self._normal_src = normal
            out = {n: attrs.get(n) for n in names}
            out.update(self.water_outputs(g))
            self.prune_unused()
            return out
        out = {}
        npin = next((k for k, a in PIN_ATTRIBUTE.items() if a == "Normal" and linked(pins.get(k))), None)
        def normal():
            with self.normal_pass():
                return self.input(g, pins[npin], {"_id": PIXEL_NORMAL}, None)
        self._normal_src = normal if npin else (lambda: None)
        for n in names:
            pin = next((k for k, a in PIN_ATTRIBUTE.items() if a == n and linked(pins.get(k))), None)
            out[n] = self.input(g, pins[pin], {}, None) if pin else attribute_default(n)
        out.update(self.water_outputs(g))
        return out

    @contextlib.contextmanager
    def normal_pass(self):
        """The Normal's own pass in a frame of its own. What it doesn't share with the rest
        (merge_duplicates folds what it does) is a second copy of the functions on the way,
        because a group node can't feed its own PixelNormalWS input."""
        saved = self.section
        self.section = [NORMAL_PASS]
        try:
            yield
        finally:
            self.section = saved

    def water_outputs(self, g):
        """A water material's medium (its SingleLayerWaterMaterialOutput), {WATER_OUTPUTS name: Val};
        {} for a material without one."""
        x = next((x for x in g.o if T(x) == "SingleLayerWaterMaterialOutput"), None)
        if x is None:
            return {}
        p = x.get("Properties") or {}
        # a frame of its own: laid out apart from the attributes, its wires stay short
        with self.at("Water Medium"):
            return {name: self.input(g, p.get(pin), {}, self.const(d, 1 if isinstance(d, float) else 3))
                    for pin, name, d in WATER_OUTPUTS}

    def input(self, g, ref, scope, default):
        """Evaluate an FExpressionInput dict (or return default)."""
        if not (isinstance(ref, dict) and ref.get("ExpressionName") not in (None, "None")):
            return default
        v = self.expr(g, g.key(ref), ref.get("OutputIndex", 0) or 0, scope)
        if ref.get("Mask"):
            idx = [i for i, k in enumerate(("MaskR", "MaskG", "MaskB", "MaskA")) if ref.get(k)]
            v = self.mask(v, idx)
        return v

    def pin(self, g, x, name, scope, cls=None, fallback=0.0):
        p = x.get("Properties") or {}
        cls = cls or T(x)
        const_name = {"A": "ConstA", "B": "ConstB", "Alpha": "ConstAlpha", "Exponent": "ConstExponent",
                      "X": "ConstX", "Y": "ConstY", "Min": "ConstMin", "Max": "ConstMax", "Value": "ConstValue"}.get(name)
        dflt = p.get(const_name) if const_name and const_name in p and not isinstance(p.get(const_name), dict) \
            else UE_DEFAULTS.get((cls, const_name), fallback)
        v = self.input(g, p.get(name), scope, self.const(dflt))
        if isinstance(v.s, Attrs):
            # maths on Material Attributes: UE compiles it per property, this reads Base Color
            self.warnings.append("Material Attributes used as a value in %s -> Base Color" % x.get("Name"))
            v = v.s.get("BaseColor")
        return v

    def expr(self, g, name, out, scope):
        key = (g.path, name, out, scope.get("_id", 0))
        if key in self.cache:
            return self.cache[key]
        stack = self.__dict__.setdefault("_stack", [])
        if key in stack:
            cyc = [k[1].replace("MaterialExpression", "") for k in stack[stack.index(key):]]
            raise RuntimeError("cycle in %s: %s" % (g.name, " -> ".join(cyc)))
        stack.append(key)
        try:
            return self._expr(g, name, out, scope, key)
        finally:
            stack.pop()

    def _expr(self, g, name, out, scope, key):
        x = g.by[name]
        comment = g.comment_of(x) if g.comments and self.section else None
        if comment is None:
            return self._expr_in(g, x, name, out, scope, key)
        saved = self.section
        self.section = self.section[:1] + [comment]
        try:
            return self._expr_in(g, x, name, out, scope, key)
        finally:
            self.section = saved

    @staticmethod
    def declaration(g, p):
        """The NamedRerouteDeclaration a usage reads: by its GUID (unique),
        else by its object path."""
        if p.get("DeclarationGuid"):
            d = next((d for d in g.decl.values()
                      if d["Properties"].get("VariableGuid") == p.get("DeclarationGuid")), None)
            if d is not None:
                return d
        if "Declaration" in p:
            full = _inner(str(p["Declaration"].get("ObjectName", "")))
            return g.decl.get(full) or g.decl.get(full.split(":")[-1].split(".")[-1])
        return None

    def _expr_in(self, g, x, name, out, scope, key):
        t = T(x)
        # reroutes are transparent
        if t == "Reroute":
            v = self.input(g, x["Properties"].get("Input"), scope, self.const(0.0))
        elif t == "NamedRerouteUsage":
            decl = self.declaration(g, x["Properties"])
            v = self.input(g, decl["Properties"].get("Input"), scope, self.const(0.0)) if decl \
                else self.const(0.0)
        elif t == "NamedRerouteDeclaration":
            v = self.input(g, x["Properties"].get("Input"), scope, self.const(0.0))
        else:
            v = self.evaluate(g, x, t, out, scope)
        self.cache[key] = v
        return v

    def evaluate(self, g, x, t, out, scope):
        p = x.get("Properties") or {}
        env = self.env
        P = lambda n, **k: self.pin(g, x, n, scope, **k)

        if t in ("Add", "Subtract", "Multiply", "Divide", "Max", "Min"):
            op = {"Add": "ADD", "Subtract": "SUBTRACT", "Multiply": "MULTIPLY", "Divide": "DIVIDE",
                  "Max": "MAXIMUM", "Min": "MINIMUM"}[t]
            if t == "Divide":
                return self.divide(P("A"), P("B"), label=x["Name"][18:])
            return self.binop(op, P("A"), P("B"), label=x["Name"][18:])
        if t == "Power":
            # UE's Power is pow(max(Base, 0), Exponent) (PositiveClampedPow, since 4.16): a negative
            # base gives 0, where Blender's Power squares it (a variant sprite's sphere mask,
            # 1 - d / r squared, came out 1 far from the sphere)
            base, ex = P("Base", fallback=1.0), P("Exponent")
            if base.w == 1 and ex.w == 1:
                if not (base.const and float(base.s) >= 0.0):
                    base = self.math('MAXIMUM', base, self.const(0.0))
                return self.math('POWER', base, ex)
            if not (base.const and all(float(x) >= 0.0 for x in (base.s if isinstance(base.s, (tuple, list)) else (base.s,)))):
                base = self.binop('MAXIMUM', base, self.const(0.0))
            return self.vmath('POWER', base, ex, out_w=base.w)
        if t == "LinearInterpolate":
            # an Alpha known to be 0 or 1 picks a side and the other isn't built (only its width
            # could matter: a float1 side broadcasts to the other's, so that one is read)
            al = P("Alpha")
            if al.const and al.w == 1 and al.s in (0.0, 1.0):
                taken = P("B" if al.s else "A")
                if taken.w != 1 or not linked(p.get("A" if al.s else "B")):
                    return taken
            return self.lerp(P("A"), P("B"), al)
        if t == "OneMinus":
            a = P("Input")
            return self.binop('SUBTRACT', self.const(1.0), a) if a.w == 1 else \
                self.vmath('SUBTRACT', self.const((1.0, 1.0, 1.0), a.w), a, label="1 - x", out_w=a.w)
        if t == "Saturate":
            return self.saturate(P("Input"))
        if t in ("Abs", "Floor", "Ceil", "Frac"):
            return self.unary({"Abs": "ABSOLUTE", "Floor": "FLOOR", "Ceil": "CEIL", "Frac": "FRACTION"}[t], P("Input"))
        if t == "Sine":
            period = float(p.get("Period", 1.0))
            a = P("Input")
            return self.unary('SINE', self.binop('MULTIPLY', a, self.const(2 * math.pi / period)))
        if t == "SquareRoot":
            a = P("Input")
            return self.math('SQRT', a) if a.w == 1 else self.vmath('POWER', a, self.const(0.5), out_w=a.w)
        if t == "Normalize":
            return self.vmath('NORMALIZE', self.zero_z(P("VectorInput")), out_w=3)
        if t == "DotProduct":
            return self.vmath('DOT_PRODUCT', self.zero_z(P("A")), self.zero_z(P("B")))
        if t == "CrossProduct":
            return self.vmath('CROSS_PRODUCT', P("A"), P("B"), out_w=3)
        if t == "Distance":
            return self.vmath('DISTANCE', *self.flat(P("A"), P("B")))
        if t == "ComponentMask":
            idx = [i for i, k in enumerate("RGBA") if p.get(k)]
            return self.mask(P("Input"), idx)
        if t == "AppendVector":
            a, b = P("A"), P("B")
            parts = (self.comps(a)[:a.w] if a.w > 1 else [a]) + (self.comps(b)[:b.w] if b.w > 1 else [b])
            return self.combine(parts)
        if t == "Convert":
            return self.convert(g, x, out, scope)
        if t == "Constant":
            return self.const(p.get("R", 0.0))
        if t == "Constant2Vector":
            return self.const((p.get("R", 0.0), p.get("G", 0.0)), 2)
        if t in ("Constant3Vector", "Constant4Vector"):
            c = p.get("Constant", {})
            if t == "Constant4Vector":
                return self.const((c.get("R", 0.0), c.get("G", 0.0), c.get("B", 0.0), c.get("A", 0.0)), 4)
            return self.const((c.get("R", 0.0), c.get("G", 0.0), c.get("B", 0.0)), 3)
        if t == "Step":
            y, xv = P("Y"), P("X")
            w = max(y.w, xv.w)
            if w == 1:
                return self.binop('SUBTRACT', self.const(1.0), self.math('LESS_THAN', xv, y))
            parts = [self.binop('SUBTRACT', self.const(1.0), self.math('LESS_THAN', a, b))
                     for a, b in zip(self.comps(xv), self.comps(y))][:w]
            return self.combine(parts)
        if t == "SmoothStep":
            return self.smoothstep(P("Min"), P("Max"), P("Value"))
        if t == "Rotator":
            uv = self.input(g, p.get("Coordinate"), scope, None) or env.uv(0)
            tm = self.input(g, p.get("Time"), scope, None) or env.time()
            ang = self.binop('MULTIPLY', tm, self.const(float(p.get("Speed", 0.25))))
            cx, cy = float(p.get("CenterX", 0.5)), float(p.get("CenterY", 0.5))
            u, v = self.comps(uv)[:2]
            du, dv = self.binop('SUBTRACT', u, self.const(cx)), self.binop('SUBTRACT', v, self.const(cy))
            c, s = self.math('COSINE', ang), self.math('SINE', ang)
            nu = self.binop('ADD', self.binop('SUBTRACT', self.binop('MULTIPLY', c, du),
                                              self.binop('MULTIPLY', s, dv)), self.const(cx))
            nv = self.binop('ADD', self.binop('ADD', self.binop('MULTIPLY', s, du),
                                              self.binop('MULTIPLY', c, dv)), self.const(cy))
            return self.combine([nu, nv])
        if t == "Time":
            return env.time()
        if t == "CloudSampleAttribute":
            # 0 Altitude, 1 AltitudeInLayer, 2 NormAltitudeInLayer, 3 ShadowSampleDistance
            return env.cloud_sample(out)
        if t == "VolumetricAdvancedMaterialInput":
            # the material's own Conservative Density (its VolumetricAdvancedMaterialOutput's pin):
            # UE evaluates it first (empty space skipping) and hands it back here
            vamo = next((y for y in g.o if T(y) == "VolumetricAdvancedMaterialOutput"), None)
            pin = ((vamo or {}).get("Properties") or {}).get("ConservativeDensity")
            if pin is None or not linked(pin):
                return self.const((1.0, 1.0, 1.0), 3)
            return self.input(g, pin, scope, self.const(1.0))
        if t == "CameraPositionWS":
            return env.camera_position()
        if t == "SphereMask":
            # UMaterialExpressionSphereMask::Compile: hardness in percent
            a, b = P("A"), P("B")
            radius = self.input(g, p.get("Radius"), scope, self.const(float(p.get("AttenuationRadius", 256.0))))
            hard = self.input(g, p.get("Hardness"), scope, self.const(float(p.get("HardnessPercent", 100.0))))
            d = self.vmath('LENGTH', self.vmath('SUBTRACT', *self.flat(a, b), out_w=3), out_w=1) if max(a.w, b.w) > 1 \
                else self.math('ABSOLUTE', self.binop('SUBTRACT', a, b))
            nd = self.binop('DIVIDE', d, self.math('MAXIMUM', radius, self.const(0.00001)))
            soft = self.binop('SUBTRACT', self.const(1.0), self.binop('MULTIPLY', hard, self.const(0.01)))
            inv = self.binop('DIVIDE', self.const(1.0), self.math('MAXIMUM', soft, self.const(0.00001)))
            return self.math('MULTIPLY', self.binop('SUBTRACT', self.const(1.0), nd), inv, clamp=True, label="SphereMask")
        if t == "InverseLinearInterpolate":
            a = self.input(g, p.get("A"), scope, self.const(float(p.get("ConstA", 0.0))))
            b = self.input(g, p.get("B"), scope, self.const(float(p.get("ConstB", 1.0))))
            v = self.input(g, p.get("Value"), scope, self.const(float(p.get("ConstValue", 0.0))))
            r = self.binop('DIVIDE', self.binop('SUBTRACT', v, a), self.binop('SUBTRACT', b, a))
            return self.saturate(r) if p.get("bClampResult") else r
        if t == "ConstantBiasScale":
            v = self.input(g, p.get("Input"), scope, self.const(0.0))
            return self.binop('MULTIPLY', self.binop('ADD', v, self.const(float(p.get("Bias", 1.0)))),
                              self.const(float(p.get("Scale", 0.5))))
        if t == "Arctangent2Fast" or t == "Arctangent2":
            return self.math('ARCTAN2', P("Y"), P("X"))
        if t == "Custom":
            return self.custom(g, x, scope, out)
        if t in ("TextureObject", "TextureObjectParameter"):
            tex = str((p.get("Texture") or {}).get("ObjectName", "")) if isinstance(p.get("Texture"), dict) \
                else str(p.get("Texture", ""))
            return Val(TexRef(self.tex_param(p, self.tex_key(p.get("Texture"))), "Volume" in tex), 0)
        if t == "TextureCoordinate":
            uv = env.uv(int(p.get("CoordinateIndex", 0)))
            ut, vt = float(p.get("UTiling", 1.0)), float(p.get("VTiling", 1.0))
            if ut != 1.0 or vt != 1.0:
                uv = self.vmath('MULTIPLY', uv, self.const((ut, vt, 0.0), 2), out_w=2)
            return uv
        if t == "VertexColor":
            rgb, a = env.vertex_color()
            return self.rgba_out(rgb, a, out)
        if t == "CameraVectorWS":
            return env.camera_vector()
        if t == "SkyAtmosphereLightDirection":
            return env.light_direction()
        if t == "SkyAtmosphereViewLuminance":
            d = self.input(g, p.get("WorldDirection"), scope, None)
            if d is None:
                d = self.vmath('SCALE', env.camera_vector(), self.const(-1.0), out_w=3)
            return env.view_luminance(d)
        if t == "SkyAtmosphereLightDiskLuminance":
            # the sun's disc: its light over the disc's solid angle where the view ray is within it
            # (UE also dims it by the atmosphere's transmittance, which the light's colour carries
            # at sunset); the disc is DiskAngularDiameterOverride degrees across, else the light's
            # default source angle (0.5357)
            diam = self.input(g, p.get("DiskAngularDiameterOverride"), scope, None)
            half = self.binop('MULTIPLY', diam, self.const(math.pi / 360.0)) if diam is not None \
                else self.const(0.5357 * math.pi / 360.0)
            view = self.vmath('SCALE', env.camera_vector(), self.const(-1.0), out_w=3)
            cos_view = self.vmath('DOT_PRODUCT', self.vmath('NORMALIZE', view, out_w=3),
                                  self.vmath('NORMALIZE', env.light_direction(), out_w=3))
            cos_half = self.math('COSINE', half)
            inside = self.math('GREATER_THAN', cos_view, cos_half)
            solid = self.binop('MULTIPLY', self.const(2.0 * math.pi), self.binop('SUBTRACT', self.const(1.0), cos_half))
            lum = self.vmath('SCALE', self.light_color(), self.binop('DIVIDE', inside, self.math('MAXIMUM', solid, self.const(1e-9))), out_w=3)
            return lum
        if t == "SkyAtmosphereAerialPerspective":
            # the atmosphere between the camera and a point (in-scattered light, transmittance): the
            # env's, else none
            pos = self.input(g, p.get("WorldPosition"), scope, None)
            if pos is None:
                pos = env.world_position(False)
            hook = getattr(env, "aerial_perspective", None)
            if hook is not None:
                rgb, a = hook(pos)
                return self.rgba_out(rgb, a, out)
            return self.rgba_out(self.stand_in("SkyAtmosphereAerialPerspective as none", self.const((0.0, 0.0, 0.0), 3)),
                                 self.const(1.0), out)
        if t == "SkyAtmosphereDistantLightScatteredLuminance":
            # the atmosphere's light far away (UE's DistantSkyLightLut, its mean luminance): the sky straight up
            return env.view_luminance(self.const((0.0, 0.0, 1.0), 3))
        if t == "WorldPosition":
            rel = "CameraRelative" in str(p.get("WorldPositionShaderOffset", ""))
            return env.world_position(rel)
        if t == "ActorPositionWS":
            return env.actor_position("CameraRelative" in str(p.get("OriginType", "")))
        if t == "CollectionParameter":
            if getattr(env, "asset_paths", False):
                return env.collection(p.get("ParameterName"), self.tex_key(p.get("Collection")))
            return env.collection(p.get("ParameterName"))
        if t == "ScalarParameter":
            v = env.scalar(p.get("ParameterName"), float(p.get("DefaultValue", 0.0)))
            if p.get("bUseCustomPrimitiveData") and hasattr(env, "primitive_data"):
                # UE's Custom Primitive Data: the placed component's own value
                v = env.primitive_data(v, int(p.get("PrimitiveDataIndex") or 0), 1)
            return v
        if t in ("VectorParameter", "DoubleVectorParameter"):
            # no DefaultValue: the class default (0, 0, 0, 0), which cooking leaves out
            # ((0, 0, 0, 1) is stored). A double vector's is X Y Z W
            d = p.get("DefaultValue", {})
            rgba = (d.get("R", d.get("X", 0.0)), d.get("G", d.get("Y", 0.0)), d.get("B", d.get("Z", 0.0)),
                    d.get("A", d.get("W", 0.0)))
            rgb, a = env.vector(p.get("ParameterName"), rgba)
            if p.get("bUseCustomPrimitiveData") and hasattr(env, "primitive_data"):
                i = int(p.get("PrimitiveDataIndex") or 0)
                rgb, a = env.primitive_data(rgb, i, 3), env.primitive_data(a, i + 3, 1)
            return self.rgba_out(rgb, a, out)
        if t in ("TextureSample", "TextureSampleParameter2D", "TextureSampleParameterCube"):
            tex = (p.get("Texture") or {}).get("ObjectName", "") if isinstance(p.get("Texture"), dict) else str(p.get("Texture", ""))
            tname = self.tex_param(p, self.tex_key(p.get("Texture")))
            coords = self.input(g, p.get("Coordinates"), scope, None)
            ref = self.input(g, p.get("TextureObject"), scope, None)
            volume = "VolumeTexture" in tex
            if ref is not None and isinstance(ref.s, TexRef):
                tname, volume = ref.s.name, ref.s.volume or volume
            if volume:
                return self.sample_volume(tname, coords, out)
            if "TextureCube" in tex:
                return self.sample_cube(tname, coords if coords is not None else self.reflection(), out)
            if coords is None:
                coords = env.uv(0)
            # its own sampler (the default source) filters and addresses as the texture says; a
            # shared sampler (world group settings) wraps or clamps as its group does
            src = str(p.get("SamplerSource", "")).split("::")[-1]
            own = src in ("", "SSM_FromTextureAsset")
            address = None if own else ("Clamp", "Clamp") if "Clamp" in src or "Terrain" in src else ("Wrap", "Wrap")
            return self.sample(tname, coords, out, p.get("SamplerType", ""), own, address)
        if t == "FunctionInput" and scope.get("_fn") is not None:
            return scope["_fn"].ue_input(x)
        if t == "FunctionInput":
            bound = scope.get(p.get("Id"))
            if isinstance(bound, _Lazy):
                bound = bound.get(self)
            if bound is not None:
                return bound
            if p.get("InputType") == ATTRIBUTES_INPUT:
                pv = self.input(g, p.get("Preview"), scope, None)
                return pv if pv is not None and isinstance(pv.s, Attrs) else Val(Attrs(), 0)
            if p.get("bUsePreviewValueAsDefault"):
                pv = self.input(g, p.get("Preview"), scope, None)
                if pv is not None:
                    return pv
                v = p.get("PreviewValue", {})
                return self.const((v.get("X", 0.0), v.get("Y", 0.0), v.get("Z", 0.0)),
                                  {"EFunctionInputType::FunctionInput_Scalar": 1,
                                   "EFunctionInputType::FunctionInput_Vector2": 2}.get(p.get("InputType"), 3)) \
                    if p.get("InputType") != "EFunctionInputType::FunctionInput_Scalar" else self.const(v.get("X", 0.0))
            return self.const(0.0)
        if t == "FunctionOutput":
            return self.input(g, p.get("A"), scope, self.const(0.0))
        if t == "MaterialFunctionCall":
            return self.call(g, x, out, scope)
        for handler in (self.evaluate_static, self.evaluate_attributes, self.evaluate_math, self.evaluate_geometry,
                        self.evaluate_renderer):
            v = handler(g, x, t, out, scope, p, P)
            if v is not None:
                return v
        self.warnings.append("unsupported %s (%s) -> 0" % (t, x["Name"]))
        return self.const(0.0)

    # ------------------------------------------------------ compile time
    # Static bools never become nodes: UE picks the branch while compiling,
    # and so does the translator.
    def _hook(self, name, fallback, *args):
        """env.<name>(*args) when the env has it and answers, else fallback()."""
        f = getattr(self.env, name, None)
        if f is not None:
            v = f(*args)
            if v is not None:
                return v
        return fallback()
