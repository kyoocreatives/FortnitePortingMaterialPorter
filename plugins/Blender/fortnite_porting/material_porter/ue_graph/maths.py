"""Maths expressions."""

import math

from .core import Attrs, TexRef, Val, linked


class MathsMixin:
    def percomp(self, op, v, b=None):
        """A Math operation component by component (Vector Math lacks it)."""
        if v.w == 1:
            return self.math(op, v, b)
        parts = [self.math(op, c, b) for c in self.comps(v)[:v.w]]
        r = self.combine(parts)
        if v.w == 4:
            r = self.with_alpha(r, self.math(op, self.alpha(v), b))
        return r

    def periodic(self, v, period):
        """UE's Period pin (Sine, Cosine, Tangent): x * 2 pi / period, if > 0."""
        return self.binop('MULTIPLY', v, self.const(2 * math.pi / period)) if period > 0 else v

    def as3(self, v):
        """A value as a float3 (float1 broadcast, float2 z = 0, alpha dropped)."""
        if v.w == 1:
            return self.combine([v, v, v])
        return self.zero_z(Val(v.s, min(v.w, 3)))

    CONVERT_WIDTH = {"Scalar": 1, "Vector2": 2, "Vector3": 3, "Vector4": 4}

    def convert(self, g, x, out, scope):
        """UE 5.6's Convert (Break Float3, Make Float4...): each of an output's components is a component
        of one of the inputs (ConvertMappings), else the output's own default."""
        p = x.get("Properties") or {}
        ins, outs, maps = p.get("ConvertInputs") or [], p.get("ConvertOutputs") or [], p.get("ConvertMappings") or []
        if out >= len(outs):
            return self.const(0.0)
        width = lambda e: self.CONVERT_WIDTH.get(str((e or {}).get("Type", "")).split("::")[-1], 1)
        have = {}

        def component(i, c):
            if i >= len(ins):
                return None
            if i not in have:
                v = self.input(g, ins[i].get("ExpressionInput"), scope, None)
                if v is not None and isinstance(v.s, Attrs):
                    v = v.s.get("BaseColor")
                have[i] = v
            v = have[i]
            if v is None:
                d = ins[i].get("DefaultValue") or {}
                return self.const(float(d.get("RGBA"[min(c, 3)], 0.0)))
            if v.w == 1:
                return v
            if c < 3 and c < v.w:
                return self.comps(v)[c]
            if c == 3 and v.w == 4 and v.a is not None:
                return v.a
            return self.const(0.0)

        d = outs[out].get("DefaultValue") or {}
        parts = []
        for c in range(width(outs[out])):
            m = next((m for m in maps if (m.get("OutputIndex", 0) or 0) == out and (m.get("OutputComponentIndex", 0) or 0) == c), None)
            v = component(m.get("InputIndex", 0) or 0, m.get("InputComponentIndex", 0) or 0) if m is not None else None
            parts.append(v if v is not None else self.const(float(d.get("RGBA"[min(c, 3)], 0.0))))
        return self.combine(parts)

    def evaluate_math(self, g, x, t, out, scope, p, P):
        if t == "Clamp":
            v = P("Input")
            mn = self.input(g, p.get("Min"), scope, self.const(float(p.get("MinDefault", 0.0))))
            mx = self.input(g, p.get("Max"), scope, self.const(float(p.get("MaxDefault", 1.0))))
            mode = str(p.get("ClampMode", "CMODE_Clamp")).split("::")[-1]
            if mode == "CMODE_Clamp" and v.w == mn.w == mx.w == 1:
                return self.clamp(v, mn, mx)
            if mode != "CMODE_ClampMax":
                v = self.binop('MAXIMUM', v, mn, label="clamp min")
            if mode != "CMODE_ClampMin":
                v = self.binop('MINIMUM', v, mx, label="clamp max")
            return v
        if t == "If":
            # HLSL: A >= B ? Greater : Less; with an Equals pin,
            # |A - B| > threshold ? that : Equals
            a = self.mask(P("A"), [0])
            b = self.mask(self.input(g, p.get("B"), scope, self.const(float(p.get("ConstB", 0.0)))), [0])
            # a branch that culls (a NaN) isn't taken: the others stand in for it
            pins = {k: p.get(k) for k in ("AGreaterThanB", "ALessThanB", "AEqualsB")}
            culls = {k for k, r in pins.items() if self._culls(g, r)}
            if culls and len(culls) < sum(1 for r in pins.values() if linked(r)):
                keep = next(k for k in ("AEqualsB", "ALessThanB", "AGreaterThanB") if k not in culls and linked(pins[k]))
                for k in culls:
                    pins[k] = pins[keep]
                p = dict(p, **pins)
            if a.const and b.const:
                # the branch taken is known: the others aren't built
                k = "AGreaterThanB" if a.s >= b.s else "ALessThanB"
                if linked(p.get("AEqualsB")) and abs(a.s - b.s) <= float(p.get("EqualsThreshold", 0.00001)):
                    k = "AEqualsB"
                return self.input(g, p.get(k), scope, self.const(0.0))
            gt = self.input(g, p.get("AGreaterThanB"), scope, self.const(0.0))
            lt = self.input(g, p.get("ALessThanB"), scope, self.const(0.0))
            eq = self.input(g, p.get("AEqualsB"), scope, None)
            # (A < B picks Less: no 1 - x for A >= B)
            v = self.select(self.math('LESS_THAN', a, b, label="A < B"), gt, lt)
            if eq is not None:
                thr = float(p.get("EqualsThreshold", 0.00001))
                far = self.math('GREATER_THAN', self.math('ABSOLUTE', self.binop('SUBTRACT', a, b)),
                                self.const(thr), label="A != B")
                v = self.select(far, eq, v)
            return v
        if t == "IfThenElse":
            c = self.static_bool(g, p.get("Condition"), scope)
            if c is not None:
                return self.input(g, p.get("True" if c else "False"), scope, self.const(0.0))
            cv = self.mask(self.input(g, p.get("Condition"), scope, self.const(0.0)), [0])
            if cv.const:
                return self.input(g, p.get("True" if cv.s else "False"), scope, self.const(0.0))
            off = self.math('COMPARE', cv, self.const(0.0), self.const(0.0), label="condition = 0")
            no = self.input(g, p.get("False"), scope, self.const(0.0))
            return self.select(off, self.input(g, p.get("True"), scope, self.const(0.0)), no)
        if t in ("Fmod", "Modulo"):
            # HLSL fmod: truncated, the sign of A (Blender's Modulo)
            return self.binop('MODULO', P("A"), P("B"))
        if t == "Cosine":
            return self.unary('COSINE', self.periodic(P("Input"), float(p.get("Period", 1.0))))
        if t == "Tangent":
            return self.percomp('TANGENT', self.periodic(P("Input"), float(p.get("Period", 1.0))))
        if t in ("Arcsine", "ArcsineFast", "Arccosine", "ArccosineFast", "Arctangent", "ArctangentFast"):
            op = {"Arcs": "ARCSINE", "Arcc": "ARCCOSINE", "Arct": "ARCTANGENT"}[t[:4]]
            return self.percomp(op, P("Input"))
        if t in ("Round", "Sign"):
            return self.unary(t.upper(), P("Input"))
        if t == "Truncate":
            return self.percomp('TRUNC', P("Input"))
        if t == "TruncateLWC":
            return P("Input")
        if t in ("Exponential", "Exponential2"):
            v = P("Input")
            return self.percomp('EXPONENT', v) if t == "Exponential" else \
                self.percomp('POWER', self.const(2.0), v) if v.w == 1 else \
                self.vmath('POWER', self.const((2.0, 2.0, 2.0), v.w), v, out_w=v.w)
        if t in ("Logarithm", "Logarithm2", "Logarithm10"):
            v = self.input(g, p.get("X") if "X" in p else p.get("Input"), scope, self.const(1.0))
            base = {"Logarithm": math.e, "Logarithm2": 2.0, "Logarithm10": 10.0}[t]
            return self.percomp('LOGARITHM', v, self.const(base))
        if t == "Length":
            v = P("Input")
            if v.w == 1:
                return self.math('ABSOLUTE', v)
            n = self.vmath('LENGTH', self.zero_z(Val(v.s, min(v.w, 3))))
            if v.w == 4:
                n = self.math('SQRT', self.binop('ADD', self.binop('MULTIPLY', n, n),
                                                 self.binop('MULTIPLY', self.alpha(v), self.alpha(v))))
            return n
        if t == "Desaturation":
            c = self.as3(P("Input"))
            lf = p.get("LuminanceFactors") or {}
            lum = (lf.get("R", 0.3), lf.get("G", 0.59), lf.get("B", 0.11))
            grey = self.vmath('DOT_PRODUCT', c, self.const(lum, 3), label="luminance")
            fr = self.input(g, p.get("Fraction"), scope, None)
            return grey if fr is None else self.lerp(c, grey, fr, label="desaturate")
        if t == "Panner":
            uv = self.input(g, p.get("Coordinate"), scope, None) or self.env.uv(int(p.get("ConstCoordinate", 0)))
            tm = self.input(g, p.get("Time"), scope, None) or self.env.time()
            sp = self.input(g, p.get("Speed"), scope, None)
            if sp is not None:
                sx, sy = self.mask(sp, [0]), self.mask(sp, [1])
            else:
                sx, sy = self.const(float(p.get("SpeedX", 0.0))), self.const(float(p.get("SpeedY", 0.0)))
            ax, ay = self.binop('MULTIPLY', tm, sx), self.binop('MULTIPLY', tm, sy)
            if p.get("bFractionalPart"):
                ax, ay = self.math('FRACT', ax), self.math('FRACT', ay)
            return self.binop('ADD', self.combine([ax, ay]), uv, label="panner")
        if t == "Fresnel":
            # pow(max(|1 - max(0, N . C)|, 1e-4), E) * (1 - F0) + F0
            n = self.input(g, p.get("Normal"), scope, None) or self.pixel_normal()
            d = self.math('MAXIMUM', self.vmath('DOT_PRODUCT', self.as3(n), self.env.camera_vector()), self.const(0.0))
            base = self.math('MAXIMUM', self.math('ABSOLUTE', self.binop('SUBTRACT', self.const(1.0), d)),
                             self.const(0.0001))
            ex = self.mask(self.input(g, p.get("ExponentIn"), scope, self.const(float(p.get("Exponent", 5.0)))), [0])
            f0 = self.input(g, p.get("BaseReflectFractionIn"), scope,
                            self.const(float(p.get("BaseReflectFraction", 0.04))))
            pw = self.math('POWER', base, ex, label="fresnel")
            return self.binop('ADD', self.binop('MULTIPLY', pw, self.binop('SUBTRACT', self.const(1.0), f0)), f0)
        if t == "RotateAboutAxis":
            # UE's RotateAboutAxis (Common.ush): the offset to the rotated position
            axis = self.as3(self.input(g, p.get("NormalizedRotationAxis"), scope, self.const((0.0, 0.0, 1.0), 3)))
            ang = self.periodic(self.mask(self.input(g, p.get("RotationAngle"), scope, self.const(0.0)), [0]),
                                float(p.get("Period", 1.0)))
            pivot = self.as3(self.input(g, p.get("PivotPoint"), scope, self.const((0.0, 0.0, 0.0), 3)))
            pos = self.as3(self.input(g, p.get("Position"), scope, self.const((0.0, 0.0, 0.0), 3)))
            along = self.vmath('DOT_PRODUCT', axis, self.vmath('SUBTRACT', pos, pivot, out_w=3))
            closest = self.vmath('ADD', pivot, self.vmath('SCALE', axis, along, out_w=3), out_w=3)
            u = self.vmath('SUBTRACT', pos, closest, out_w=3)
            v = self.vmath('CROSS_PRODUCT', axis, u, out_w=3)
            r = self.vmath('ADD', self.vmath('SCALE', u, self.math('COSINE', ang), out_w=3),
                           self.vmath('SCALE', v, self.math('SINE', ang), out_w=3), out_w=3)
            return self.vmath('SUBTRACT', self.vmath('ADD', closest, r, out_w=3), pos, label="rotate about axis", out_w=3)
        if t == "DeriveNormalZ":
            xy = P("InXY")
            nx, ny = self.comps(xy)[:2]
            dot = self.binop('ADD', self.binop('MULTIPLY', nx, nx), self.binop('MULTIPLY', ny, ny))
            return self.combine([nx, ny, self.math('SQRT', self.saturate(self.binop('SUBTRACT', self.const(1.0), dot)))])
        if t == "BumpOffset":
            uv = self.input(g, p.get("Coordinate"), scope, None) or self.env.uv(int(p.get("ConstCoordinate", 0)))
            h = self.mask(self.input(g, p.get("Height"), scope, self.const(0.0)), [0])
            ratio = self.input(g, p.get("HeightRatioInput"), scope, None)
            ratio = self.const(float(p.get("HeightRatio", 0.05))) if ratio is None else self.mask(ratio, [0])
            ref = float(p.get("ReferencePlane", 0.5))
            off = self.binop('SUBTRACT', self.binop('MULTIPLY', ratio, h), self.binop('MULTIPLY', ratio, self.const(ref)))
            cam = self.comps(self.transform(self.env.camera_vector(), "world", "tangent"))
            return self.binop('ADD', self.binop('MULTIPLY', self.combine(cam[:2]), off), uv, label="bump offset")
        if t in ("HsvToRgb", "RgbToHsv"):
            v = P("Input")
            if t == "RgbToHsv":
                n = self.node("ShaderNodeSeparateColor", "RGB to HSV", mode='HSV')
                self.link(self.as3(v), n.inputs[0])
                r = self.combine([Val(n.outputs[i], 1) for i in range(3)])
            else:
                n = self.node("ShaderNodeCombineColor", "HSV to RGB", mode='HSV')
                for i, c in enumerate(self.comps(v)):
                    self.link(c, n.inputs[i])
                r = Val(n.outputs[0], 3)
            return self.with_alpha(r, self.alpha(v)) if v.w == 4 else r
        if t == "BlackBody":
            n = self.node("ShaderNodeBlackbody", "black body")
            self.link(self.mask(P("Temp"), [0]), n.inputs[0])
            return Val(n.outputs[0], 3)
        if t == "MaterialXRemap":
            v = P("Input")
            il = self.input(g, p.get("InputLow"), scope, self.const(float(p.get("InputLowDefault", 0.0))))
            ih = self.input(g, p.get("InputHigh"), scope, self.const(float(p.get("InputHighDefault", 1.0))))
            tl = self.input(g, p.get("TargetLow"), scope, self.const(float(p.get("TargetLowDefault", 0.0))))
            th = self.input(g, p.get("TargetHigh"), scope, self.const(float(p.get("TargetHighDefault", 1.0))))
            k = self.binop('DIVIDE', self.binop('SUBTRACT', th, tl), self.binop('SUBTRACT', ih, il))
            return self.binop('ADD', tl, self.binop('MULTIPLY', self.binop('SUBTRACT', v, il), k), label="remap")
        if t == "MaterialXAppend3Vector":
            return self.combine([self.mask(P(k), [0]) for k in "ABC"])
        if t == "TextureProperty":
            ref = self.input(g, p.get("TextureObject"), scope, None)
            img = self.env.texture(ref.s.name, "") if ref is not None and isinstance(ref.s, TexRef) else None
            w, h = (img.size[0], img.size[1]) if img is not None and img.size[0] else (1, 1)
            if str(p.get("Property", "TMTM_TextureSize")).endswith("TexelSize"):
                return self.const((1.0 / w, 1.0 / h), 2)
            return self.const((float(w), float(h)), 2)
        if t == "Noise":
            return self.noise(g, p, scope)
        if t == "VectorNoise":
            return self.vector_noise(g, p, scope, out)
        return None

    def noise(self, g, p, scope):
        """UE's Noise, approximated by Blender's fBM noise: same position scale,
        octaves and lacunarity, mapped onto OutputMin..OutputMax."""
        pos = self.input(g, p.get("Position"), scope, None) or self.env.world_position(False)
        pos = self.vmath('SCALE', self.as3(pos), self.const(float(p.get("Scale", 1.0))), out_w=3)
        n = self.node("ShaderNodeTexNoise", "noise (approximate)", noise_dimensions='3D')
        self.link(pos, n.inputs["Vector"])
        n.inputs["Scale"].default_value = 1.0
        n.inputs["Detail"].default_value = max(0, int(p.get("Levels", 6)) - 1)
        n.inputs["Roughness"].default_value = 0.5
        n.inputs["Lacunarity"].default_value = float(p.get("LevelScale", 2.0))
        f = Val(n.outputs["Factor"], 1)
        if p.get("bTurbulence", True):
            f = self.math('ABSOLUTE', self.binop('SUBTRACT', self.binop('MULTIPLY', f, self.const(2.0)), self.const(1.0)))
        lo, hi = float(p.get("OutputMin", -1.0)), float(p.get("OutputMax", 1.0))
        self.warnings.append("approximate Noise (Blender fBM)")
        return self.binop('ADD', self.const(lo), self.binop('MULTIPLY', f, self.const(hi - lo)))

    def vector_noise(self, g, p, scope, out):
        """UE's VectorNoise, approximated: cell noise is Blender's white noise
        per cell, Voronoi its Voronoi, the gradient kinds its noise colour."""
        fn = str(p.get("NoiseFunction", "VNF_CellnoiseALU")).split("::")[-1]
        pos = self.as3(self.input(g, p.get("Position"), scope, None) or self.env.world_position(False))
        self.warnings.append("approximate VectorNoise (%s)" % fn)
        if fn == "VNF_CellnoiseALU":
            n = self.node("ShaderNodeTexWhiteNoise", "cell noise", noise_dimensions='3D')
            self.link(self.vmath('FLOOR', pos, out_w=3), n.inputs["Vector"])
            return Val(n.outputs["Color"], 3)
        if fn == "VNF_VoronoiALU":
            n = self.node("ShaderNodeTexVoronoi", "voronoi", voronoi_dimensions='3D')
            self.link(pos, n.inputs["Vector"])
            n.inputs["Scale"].default_value = 1.0
            if out == 1:
                return Val(n.outputs["Distance"], 1)
            return self.with_alpha(Val(n.outputs["Position"], 3), Val(n.outputs["Distance"], 1))
        n = self.node("ShaderNodeTexNoise", "vector noise (approximate)", noise_dimensions='3D')
        self.link(pos, n.inputs["Vector"])
        n.inputs["Scale"].default_value = 1.0
        n.inputs["Detail"].default_value = 0.0
        return self.vmath('SUBTRACT', self.vmath('SCALE', Val(n.outputs["Color"], 3), self.const(2.0), out_w=3),
                          self.const((1.0, 1.0, 1.0), 3), out_w=3)
