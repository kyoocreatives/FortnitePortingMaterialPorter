"""Custom HLSL nodes rebuilt as Blender nodes (CUSTOM_HASHES)."""

import math

from .core import Attrs, CUSTOM_LAZY, TexRef, Val, LazyInputs, custom_handler, linked


class CustomMixin:
    # ------------------------------------------------------------ custom HLSL
    # Custom nodes carry HLSL; each known snippet is rebuilt here by hand (whitespace-insensitive
    # match), anything else is reported and passes its first input through.
    def custom(self, g, x, scope, out=0):
        p = x.get("Properties") or {}
        handler = custom_handler(p)
        if handler in CUSTOM_LAZY:
            # reads only some inputs: the rest never become nodes
            ins = LazyInputs(lambda ref: self.input(g, ref, scope, self.const(0.0)),
                              {i.get("InputName"): i.get("Input") for i in p.get("Inputs", [])})
        else:
            ins = {i.get("InputName"): self.input(g, i.get("Input"), scope, self.const(0.0))
                   for i in p.get("Inputs", [])}
        if handler is not None:
            # a snippet's AdditionalOutputs are output indices 1.. (0: its return value)
            self.custom_out = out
            return getattr(self, handler)(ins, p)
        # unknown: its first numeric input, else zeros of its output type
        # (never a texture: those can't go on)
        self.warnings.append("unknown Custom node %s in %s -> first input" % (x.get("Name"), g.name))
        w = {"CMOT_Float1": 1, "CMOT_Float2": 2, "CMOT_Float3": 3, "CMOT_Float4": 4}.get(
            str(p.get("OutputType", "CMOT_Float3")).split("::")[-1], 3)
        first = next((v for v in ins.values() if not isinstance(v.s, (TexRef, Attrs))), None)
        return first if first is not None else self.const(0.0) if w == 1 else self.const((0.0,) * w, w)

    def _work(self, ins):
        """(bool)bDoWork as 0 / 1; None when it's a constant true."""
        w = ins.get("bDoWork", self.const(1.0))
        if w.const:
            return None if w.s else self.const(0.0)
        return self.binop('SUBTRACT', self.const(1.0), self.math('COMPARE', w, self.const(0.0), self.const(0.0)),
                          label="do work")

    def _tex_name(self, ins, key="Tex"):
        t = ins.get(key)
        return t.s.name if t is not None and isinstance(t.s, TexRef) else ""

    def custom_conditional_color(self, ins, p):
        # bDoWork ? Tex.Sample(UVs) : 0 (a skipped layer has zero weight
        # anyway; the branch only saves the fetch)
        n = self.sample_node(self._tex_name(ins), ins.get("UVs") or self.env.uv(0), "SAMPLERTYPE_Color")
        rgb, a = Val(n.outputs["Color"], 3), Val(n.outputs["Alpha"], 1)
        w = self._work(ins)
        if w is not None:
            rgb, a = self.vmath('SCALE', rgb, w, out_w=3), self.binop('MULTIPLY', a, w)
        return self.with_alpha(rgb, a)

    def custom_conditional_normal(self, ins, p):
        # UnpackNormalMap (BC5 on PC): xy = rg * 2 - 1, z = sqrt(saturate(1 - xy.xy)), w 1;
        # a skipped layer is (0, 0, 1, 0)
        n = self.sample_node(self._tex_name(ins), ins.get("UVs") or self.env.uv(0), "SAMPLERTYPE_Normal")
        normal, a = self.unpack_normal(Val(n.outputs["Color"], 3))
        w = self._work(ins)
        if w is not None:
            normal = self.lerp(self.const((0.0, 0.0, 1.0), 3), normal, w, label="layer normal")
            a = w
        return self.with_alpha(normal, a)

    def custom_uv_box(self, ins, p):
        # 1 inside [0, 1) on both UV axes, 0 outside
        u, v = self.comps(ins.get("uv", self.const((0.0, 0.0), 2)))[:2]
        inside = lambda c: self.binop('SUBTRACT', self.binop('SUBTRACT', self.const(1.0), self.math('LESS_THAN', c, self.const(0.0))),
                                      self.binop('SUBTRACT', self.const(1.0), self.math('LESS_THAN', c, self.const(1.0))))
        return self.binop('MULTIPLY', self.binop('MULTIPLY', inside(u), inside(v)), ins.get("color", self.const(0.0)),
                          label="uv box")

    def _in(self, ins, key, default=0.0):
        v = ins.get(key)
        return v if v is not None else self.const(default)

    def custom_identity(self, ins, p):
        return next(iter(ins.values()), self.const(0.0))

    def _exp3(self, v):
        """exp() per component of a float3."""
        return self.combine([self.math('EXPONENT', c) for c in self.comps(self.as3(v))])

    def custom_cloud_transmittance(self, ins, p):
        sigma_e = self.vmath('SCALE', self.vmath('ADD', self.as3(self._in(ins, "ScatteringCoeff")),
                                                 self.as3(self._in(ins, "AbsorptionCoeff")), out_w=3),
                             self._in(ins, "CloudDensity"), out_w=3)
        t = self._exp3(self.vmath('SCALE', sigma_e, self.binop('MULTIPLY', self._in(ins, "CloudHeight"), self.const(-1.0)), out_w=3))
        return self.saturate(self.vmath('DOT_PRODUCT', t, self.const((1.0 / 3.0,) * 3, 3)))

    def custom_cloud_light(self, ins, p):
        # sigmaS = Scattering * Density, sigmaE = sigmaS + Absorption * Density, T = exp(-Height * sigmaE);
        # phase: 0.3 uniform + 0.7 Schlick (k 0.8) over 4 pi; S = Illuminance * phase * sigmaS;
        # return max(0, (S - S T) / (sigmaE + 1e-6) * T)
        dens = self._in(ins, "CloudDensity")
        sigma_s = self.vmath('SCALE', self.as3(self._in(ins, "ScatteringCoeff")), dens, out_w=3)
        sigma_e = self.vmath('ADD', sigma_s, self.vmath('SCALE', self.as3(self._in(ins, "AbsorptionCoeff")), dens, out_w=3), out_w=3)
        t = self._exp3(self.vmath('SCALE', sigma_e, self.binop('MULTIPLY', self._in(ins, "CloudHeight"), self.const(-1.0)), out_w=3))
        to_cam = self.vmath('NORMALIZE', self.vmath('SUBTRACT', self.as3(self._in(ins, "CameraPos")),
                                                   self.as3(self._in(ins, "WorldPos")), out_w=3), out_w=3)
        phase_in = self.saturate(self.vmath('DOT_PRODUCT', self.as3(self._in(ins, "SunLightDir")),
                                            self.vmath('SCALE', to_cam, self.const(-1.0), out_w=3)))
        factor = self.binop('SUBTRACT', self.const(1.0), self.binop('MULTIPLY', phase_in, self.const(0.8)))
        schlick = self.binop('DIVIDE', self.const(1.0 - 0.64),
                             self.binop('ADD', self.binop('MULTIPLY', factor, factor), self.const(1e-6)))
        phase = self.binop('DIVIDE', self.binop('ADD', self.const(0.3), self.binop('MULTIPLY', schlick, self.const(0.7))),
                           self.const(4.0 * math.pi))
        s_ = self.vmath('MULTIPLY', self.as3(self._in(ins, "SunLightIlluminance")),
                        self.vmath('SCALE', sigma_s, phase, out_w=3), out_w=3)
        scatt = self.vmath('DIVIDE', self.vmath('SUBTRACT', s_, self.vmath('MULTIPLY', s_, t, out_w=3), out_w=3),
                           self.vmath('ADD', sigma_e, self.const((1e-6,) * 3, 3), out_w=3), out_w=3)
        return self.vmath('MAXIMUM', self.vmath('MULTIPLY', scatt, t, out_w=3), self.const((0.0,) * 3, 3), out_w=3)

    def custom_clouds_aerial(self, ins, p):
        ap = self._in(ins, "AerialPerspective")
        opacity = self.binop('SUBTRACT', self.const(1.0), self._in(ins, "CloudTransmittance"))
        return self.vmath('ADD', self.vmath('SCALE', self.as3(ap), opacity, out_w=3),
                          self.vmath('SCALE', self.as3(self._in(ins, "CloudLuminance")), self.alpha(ap), out_w=3), out_w=3)

    def custom_height_fog_coverage(self, ins, p):
        # depth = density / falloff * exp(-camZ falloff) * (1 - exp(-distance dirZ falloff)) / dirZ
        dz = self.binop('ADD', self.comps(self.as3(self._in(ins, "cameraDir")))[2], self.const(1e-5))
        fall = self._in(ins, "heightFalloff")
        cam_z = self.comps(self.as3(self._in(ins, "cameraPos")))[2]
        depth = self.binop('MULTIPLY', self.binop('DIVIDE', self._in(ins, "density"), fall),
                           self.math('EXPONENT', self.binop('MULTIPLY', self.binop('MULTIPLY', cam_z, fall), self.const(-1.0))))
        far = self.math('EXPONENT', self.binop('MULTIPLY', self.binop('MULTIPLY', self.binop('MULTIPLY', self._in(ins, "distance"), dz), fall),
                                                self.const(-1.0)))
        depth = self.binop('DIVIDE', self.binop('MULTIPLY', depth, self.binop('SUBTRACT', self.const(1.0), far)), dz)
        return self.binop('SUBTRACT', self.const(1.0), self.math('EXPONENT', self.binop('MULTIPLY', depth, self.const(-1.0))))

    def custom_smin(self, ins, p):
        a, b, k = self._in(ins, "a"), self._in(ins, "b"), self._in(ins, "k")
        h = self.saturate(self.binop('ADD', self.const(0.5), self.binop('MULTIPLY', self.const(0.5),
                                                                         self.binop('DIVIDE', self.binop('SUBTRACT', b, a), k))))
        return self.binop('SUBTRACT', self.lerp(b, a, h),
                          self.binop('MULTIPLY', k, self.binop('MULTIPLY', h, self.binop('SUBTRACT', self.const(1.0), h))))

    def custom_cave_type(self, ins, p):
        c = self._in(ins, "CaveType")
        half = self.binop('ADD', self.math('COMPARE', c, self.const(0.0), self.const(0.0)),
                          self.math('COMPARE', c, self.const(2.0), self.const(0.0)))
        return self.binop('SUBTRACT', self.const(1.0), self.binop('MULTIPLY', half, self.const(0.5)))

    def custom_one(self, ins, p):
        return self.const(1.0)

    def custom_white4(self, ins, p):
        return self.with_alpha(self.const((1.0, 1.0, 1.0), 3), self.const(1.0))

    def custom_dither5(self, ins, p):
        # Mod((uint)p.x + 2 * (uint)p.y, 5): pixel coordinates are positive,
        # so the casts are floors and Mod is floored
        x, y = self.comps(self._in(ins, "p"))[:2]
        k = self.binop('ADD', self.math('FLOOR', x), self.binop('MULTIPLY', self.math('FLOOR', y), self.const(2.0)))
        return self.math('FLOORED_MODULO', k, self.const(5.0), label="dither 5")

    def custom_primitive_index(self, ins, p):
        # a per-object integer; Blender has no primitive index: the object's
        # random value, spread over 16 bits (only ever hashed further)
        def fallback():
            self.warnings.append("stand-in: primitive index from the object's random value")
            n = self.shared("object info", lambda: self.node("ShaderNodeObjectInfo", "object info"))
            return self.math('FLOOR', self.binop('MULTIPLY', Val(n.outputs["Random"], 1), self.const(65535.0)))
        return self._hook("primitive_index", fallback)

    def custom_triplanar_normals(self, ins, p):
        # SelectAndTransformNormals: a tangent-space normal swizzled onto one
        # of three world planes, then normalized
        t = self.comps(self._in(ins, "unpackedNormalValue"))
        w = self.comps(self._in(ins, "worldNormal"))
        add, mul = (lambda a, b: self.binop('ADD', a, b)), (lambda a, b: self.binop('MULTIPLY', a, b))
        planes = [
            self.combine([mul(t[2], w[0]), add(t[0], w[1]), add(t[1], w[2])]),   # (tN.yx + wN.zy, tN.z wN.x).zyx
            self.combine([add(t[0], w[0]), mul(t[2], w[1]), add(t[1], w[2])]),   # (tN.xy + wN.xz, tN.z wN.y).xzy
            self.combine([add(t[0], w[0]), add(t[1], w[1]), mul(t[2], w[2])]),   # (tN.xy + wN.xy, tN.z wN.z)
        ]
        sel = self._in(ins, "selection")
        if sel.const:
            i = int(math.trunc(sel.s + 0.001))
            v = planes[i] if 0 <= i <= 2 else planes[2]
        else:
            idx = self.math('TRUNC', self.binop('ADD', sel, self.const(0.001)))
            v = planes[2]
            for i in (1, 0):
                v = self.select(self.math('COMPARE', idx, self.const(float(i)), self.const(0.5)), v, planes[i])
        return self.vmath('NORMALIZE', v, label="triplanar normal", out_w=3)

    def custom_rain_occluder(self, ins, p):
        # In == 0 ? 1 : OccluderOn
        zero = self.math('COMPARE', self.mask(self._in(ins, "In"), [0]), self.const(0.0), self.const(0.0))
        return self.select(zero if not zero.const else self.const(1.0 if zero.s else 0.0),
                           self._in(ins, "OccluderOn"), self.const(1.0))

    def _pick(self, sel, values):
        """values[round(sel)] for a runtime sel (a chain of compares)."""
        v = values[-1]
        for i in range(len(values) - 2, -1, -1):
            v = self.select(self.math('COMPARE', sel, self.const(float(i)), self.const(0.5)), v, values[i])
        return v

    def custom_select_0_15(self, ins, p):
        s = self.mask(self._in(ins, "Select"), [0])
        a, b = self._in(ins, "A"), self._in(ins, "B")
        if s.const:
            return a if 0 <= s.s <= 15 else b
        inside = self.binop('MULTIPLY', self.binop('SUBTRACT', self.const(1.0), self.math('LESS_THAN', s, self.const(0.0))),
                             self.binop('SUBTRACT', self.const(1.0), self.math('GREATER_THAN', s, self.const(15.0))))
        return self.select(inside, b, a)

    def custom_select4(self, ins, p):
        # inputs[clamp(int(selection - 1), 0, 3)]
        vals = [self._in(ins, k) for k in "ABCD"]
        s = self.mask(self._in(ins, "selection"), [0])
        if s.const:
            return vals[max(0, min(3, int(math.trunc(s.s - 1))))]
        i = self.math('MINIMUM', self.math('MAXIMUM', self.math('TRUNC', self.binop('SUBTRACT', s, self.const(1.0))),
                                                    self.const(0.0)), self.const(3.0))
        return self._pick(i, vals)

    def custom_select3(self, ins, p):
        # Select == 1 ? A : Select == 2 ? B : C
        s = self.mask(self._in(ins, "Select"), [0])
        a, b, c = self._in(ins, "A"), self._in(ins, "B"), self._in(ins, "C")
        if s.const:
            return a if s.s == 1 else b if s.s == 2 else c
        v = self.select(self.math('COMPARE', s, self.const(2.0), self.const(0.0)), c, b)
        return self.select(self.math('COMPARE', s, self.const(1.0), self.const(0.0)), v, a)

    def custom_select_eq4(self, ins, p):
        # if (Select == 1) A; else if (Select == 2) B; else if (Select == 3) C; else D
        s = self.mask(self._in(ins, "Select"), [0])
        a, b, c, d = (self._in(ins, k) for k in "ABCD")
        if s.const:
            return {1: a, 2: b, 3: c}.get(s.s, d)
        v = self.select(self.math('COMPARE', s, self.const(3.0), self.const(0.0)), d, c)
        v = self.select(self.math('COMPARE', s, self.const(2.0), self.const(0.0)), v, b)
        return self.select(self.math('COMPARE', s, self.const(1.0), self.const(0.0)), v, a)

    def custom_array_sample(self, ins, p):
        v = self.sample_array(self._tex_name(ins, "Tex"), self._in(ins, "UV"), "SAMPLERTYPE_Color")
        return Val(v.s, 3)

    def custom_array_conditional(self, ins, p):
        # Mask < 0.0001 ? Default : the array's texel (derivatives: mip 0)
        v = self.sample_array(self._tex_name(ins, "Texture"), self._in(ins, "UV"), "SAMPLERTYPE_Color")
        m = self.mask(self._in(ins, "Mask"), [0])
        off = self.math('LESS_THAN', m, self.const(0.0001)) if not m.const else self.const(1.0 if m.s < 0.0001 else 0.0)
        return self.select(off, v, self._in(ins, "Default"))

    def _number(self, v, default):
        """A scalar's number where building the material knows it: a constant, a parameter's own
        value (its group input's default, a Value node's); the default otherwise."""
        if v is None:
            return default
        if v.const:
            try:
                return float(v.s)
            except (TypeError, ValueError):
                return default
        node = v.s.node
        if node.type == 'VALUE':
            return float(node.outputs[0].default_value)
        if node.type == 'GROUP_INPUT':
            item = next((i for i in node.id_data.interface.items_tree
                         if i.item_type == 'SOCKET' and i.in_out == 'INPUT' and i.identifier == v.s.identifier), None)
            try:
                return float(item.default_value)
            except (AttributeError, TypeError, ValueError):
                return default
        return default

    def custom_raymarch_2d(self, ins, p):
        # a light march through a density texture, in the sprite's plane: step k (0 .. numSteps - 1)
        # moves the UV on by marchDir * (k * marchDistance / numSteps) / numSteps, reads the density
        # there (the texel over (numSteps - contrast), against ChannelSelect) and dims the light by it;
        # LO + LightColor * light * density comes out. Unrolled to NumSteps as known at build time.
        steps = int(round(self._number(ins.get("numSteps"), 16.0)))
        if steps > 64:
            self.warnings.append("approximate: a %d-step light march laid out as 64 steps" % steps)
        steps = max(1, min(steps, 64))
        count = self._in(ins, "numSteps", float(steps))
        tex, uv, march = self._tex_name(ins, "Tex"), ins.get("UV") or self.env.uv(0), self._in(ins, "marchDir")
        select = self._in(ins, "ChannelSelect", 1.0)
        # what one more step adds to the UV, over the step's number
        unit = self.binop('DIVIDE', self._in(ins, "marchDistance"), self.binop('MULTIPLY', count, count))
        stride = self.vmath('SCALE', self.zero_z(Val(march.s, 2)) if march.w >= 2 else self.combine([march, march, self.const(0.0)]), unit, out_w=2)
        thin = self.binop('SUBTRACT', count, self._in(ins, "contrast"))
        light = self._in(ins, "TransmittedLight", 1.0)
        light = self.mask(light, [0]) if light.w > 1 else light
        for k in range(steps):
            at = uv if k == 0 else self.vmath('ADD', uv, self.vmath('SCALE', stride, self.const(k * (k + 1) / 2.0), out_w=2), out_w=2)
            n = self.sample_node(tex, at, "SAMPLERTYPE_Color")
            texel = Val(n.outputs["Color"], 3)
            if select.w == 1:
                # (a scalar against the float4 texel: every channel)
                density = self.binop('MULTIPLY', self.binop('ADD', self.vmath('DOT_PRODUCT', texel, self.const((1.0, 1.0, 1.0), 3)),
                                                            Val(n.outputs["Alpha"], 1)), select)
            else:
                density = self.vmath('DOT_PRODUCT', texel, self.as3(select))
                if select.w == 4:
                    density = self.binop('ADD', density, self.binop('MULTIPLY', Val(n.outputs["Alpha"], 1), self.alpha(select)))
            density = self.math('DIVIDE', density, thin, clamp=True)
            light = self.binop('MULTIPLY', light, self.math('SUBTRACT', self.const(1.0), density, clamp=True))
        lit = self.binop('MULTIPLY', self.binop('MULTIPLY', self._in(ins, "LightColor", 1.0), light), self._in(ins, "density", 1.0))
        return self.binop('ADD', self._in(ins, "LO"), lit)

    def custom_pcg3d16(self, ins, p):
        # an integer hash of the cell: Blender's white noise per cell has the
        # same use (a random vector per integer point), not the same bits
        self.warnings.append("stand-in: pcg3d16 hash as white noise")
        n = self.node("ShaderNodeTexWhiteNoise", "hash (white noise)", noise_dimensions='3D')
        self.link(self.vmath('FLOOR', self.as3(self._in(ins, "x")), out_w=3), n.inputs["Vector"])
        return Val(n.outputs["Color"], 3)

    def custom_light_direction(self, ins, p):
        return self.env.light_direction()

    def custom_light_color(self, ins, p):
        return self.with_alpha(self.light_color(), self.const(1.0))

    def custom_world_to_clip(self, ins, p):
        # UE's WorldToClip: view space, the camera's projection, reversed-Z
        # infinite far plane (clip z = the near plane, 10 cm)
        view = self.comps(self.transform(self.as3(self._in(ins, "In")), "world", "view", point=True))
        tan = self.view_property("TanHalfFieldOfView")
        tx, ty = self.comps(tan)[:2]
        x = self.binop('DIVIDE', view[0], tx)
        y = self.binop('DIVIDE', view[1], ty)
        return self.with_alpha(self.combine([x, y, self.const(10.0)]), view[2])

    def custom_smoothstep(self, ins, p):
        return self.smoothstep(self._in(ins, "varMin"), self._in(ins, "varMax"), self._in(ins, "varA"))

    def custom_zero(self, ins, p):
        return self.const(0.0)

    def custom_nan(self, ins, p):
        return self.const(0.0)

    def custom_ggx_d(self, ins, p):
        # NoH = dot(normalize(N + L), N); a2 = R^4; d = (NoH * a2 - NoH) * NoH + 1; a2 / (PI d d)
        n, l = self.as3(self._in(ins, "Normal")), self.as3(self._in(ins, "LightVector"))
        r = self.mask(self._in(ins, "Roughness"), [0])
        noh = self.vmath('DOT_PRODUCT', self.vmath('NORMALIZE', self.vmath('ADD', n, l, out_w=3), out_w=3), n)
        r2 = self.binop('MULTIPLY', r, r)
        a2 = self.binop('MULTIPLY', r2, r2)
        d = self.binop('ADD', self.binop('MULTIPLY', self.binop('SUBTRACT', self.binop('MULTIPLY', noh, a2), noh), noh),
                       self.const(1.0))
        return self.divide(a2, self.binop('MULTIPLY', self.const(3.14159265), self.binop('MULTIPLY', d, d)))

    def custom_fp_select(self, ins, p):
        fp = self.mask(self._in(ins, "FP"), [0])
        wpo, wpo_fp = self._in(ins, "WPO"), self._in(ins, "WPO_FP")
        if fp.const:
            return wpo_fp if fp.s else wpo
        on = self.binop('SUBTRACT', self.const(1.0), self.math('COMPARE', fp, self.const(0.0), self.const(0.0)))
        return self.select(on, wpo, wpo_fp)

    def _culls(self, g, ref):
        """Whether an input is a Custom node giving a NaN (a vertex UE drops: custom_nan)."""
        if not linked(ref):
            return False
        x = g.by.get(g.key(ref))
        return x is not None and x["Type"].endswith("Custom") and custom_handler(x.get("Properties") or {}) == "custom_nan"

    def custom_sign(self, ins, p):
        return self.unary('SIGN', self._in(ins, "x"))

    def custom_length(self, ins, p):
        v = self._in(ins, "x")
        return self.math('ABSOLUTE', v) if v.w == 1 else self.vmath('LENGTH', self.as3(v))

    def custom_acos(self, ins, p):
        return self.math('ARCCOSINE', self.mask(self._in(ins, "a"), [0]))

    def custom_cube_corner(self, ins, p):
        # Center + float3(-Size / 2): a box's lower corner
        half = self.math('MULTIPLY', self.mask(self._in(ins, "Size"), [0]), self.const(0.5))
        return self.vmath('SUBTRACT', self.as3(self._in(ins, "Center")), self.combine([half, half, half]), out_w=3)

    def custom_rgb_to_hsv(self, ins, p):
        # the branchless conversion (K = 0, -1/3, 2/3, -1): two compare-and-swaps, then
        # H = |q.z + (q.w - q.y) / (6d + 1e-7)|, S = d / (q.x + 1e-7), V = q.x, d = q.x - min(q.w, q.y)
        r, g, b = self.comps(self.as3(self._in(ins, "c")))

        def step(edge, x):      # x >= edge
            return self.math('SUBTRACT', self.const(1.0), self.math('LESS_THAN', x, edge))
        s1 = step(b, g)
        px, py = self.lerp(b, g, s1), self.lerp(g, b, s1)
        pz, pw = self.lerp(self.const(-1.0), self.const(0.0), s1), self.lerp(self.const(2.0 / 3.0), self.const(-1.0 / 3.0), s1)
        s2 = step(px, r)
        qx, qy = self.lerp(px, r, s2), py
        qz, qw = self.lerp(pw, pz, s2), self.lerp(r, px, s2)
        d = self.math('SUBTRACT', qx, self.math('MINIMUM', qw, qy))
        e = self.const(1e-7)
        h = self.math('ABSOLUTE', self.math('ADD', qz, self.math('DIVIDE', self.math('SUBTRACT', qw, qy),
                                                                 self.math('ADD', self.math('MULTIPLY', d, self.const(6.0)), e))))
        s = self.math('DIVIDE', d, self.math('ADD', qx, e))
        return self.combine([h, s, qx])

    def custom_atan2(self, ins, p):
        return self.math('ARCTAN2', self.mask(self._in(ins, "y"), [0]), self.mask(self._in(ins, "x"), [0]))

    def custom_bounds_min(self, ins, p):
        return self.local_bounds()[0]

    def custom_bounds_max(self, ins, p):
        return self.local_bounds()[1]

    def custom_bounds_extent(self, ins, p):
        mn, mx = self.local_bounds()
        return self.vmath('SCALE', self.vmath('SUBTRACT', mx, mn, out_w=3), self.const(0.5), out_w=3)

    def custom_bounds_center(self, ins, p):
        mn, mx = self.local_bounds()
        return self.vmath('SCALE', self.vmath('ADD', mx, mn, out_w=3), self.const(0.5), out_w=3)

    def custom_tan_half_fov(self, ins, p):
        return self.mask(self.view_property("TanHalfFieldOfView"), [0])

    def custom_fov(self, ins, p):
        return self.mask(self.view_property("FieldOfView"), [0])

    def custom_bitfield(self, ins, p):
        # (uint(Scalar) >> BitIndex) & ((1 << BitCount) - 1), exact below 2^24
        v = self.math('TRUNC', self.mask(self._in(ins, "Scalar"), [0]))
        idx, cnt = self.mask(self._in(ins, "BitIndex"), [0]), self.mask(self._in(ins, "BitCount"), [0])
        shifted = self.math('FLOOR', self.binop('DIVIDE', v, self.math('POWER', self.const(2.0), idx)))
        return self.math('FLOORED_MODULO', shifted, self.math('POWER', self.const(2.0), cnt), label="bit field")

    def float_fields(self, f):
        """asuint(f) as its three fields: sign (1 bit), exponent (8), mantissa (23). Each is
        exact in float maths (a float can't hold all 32 bits, but every field fits in 24).
        Normal and subnormal floats (0 is all zeros)."""
        a = self.math('ABSOLUTE', f)
        sign = self.math('LESS_THAN', f, self.const(0.0))
        tiny = self.math('LESS_THAN', a, self.const(2.0 ** -126))
        e = self.math('FLOOR', self.math('LOGARITHM', self.math('MAXIMUM', a, self.const(2.0 ** -126)), self.const(2.0)))
        # log2 can land a hair off at exact powers of two: settle it
        e = self.binop('SUBTRACT', e, self.math('LESS_THAN', a, self.math('POWER', self.const(2.0), e)))
        e = self.binop('ADD', e, self.math('GREATER_THAN', a, self.binop('SUBTRACT', self.math(
            'POWER', self.const(2.0), self.binop('ADD', e, self.const(1.0))), self.const(0.0))))
        mant_n = self.binop('MULTIPLY', self.binop('SUBTRACT', self.binop('DIVIDE', a, self.math('POWER', self.const(2.0), e)),
                                                   self.const(1.0)), self.const(2.0 ** 23))
        mant_s = self.binop('MULTIPLY', self.binop('MULTIPLY', a, self.const(2.0 ** 100)), self.const(2.0 ** 49))
        exp = self.select(tiny, self.binop('ADD', e, self.const(127.0)), self.const(0.0))
        mant = self.math('ROUND', self.select(tiny, mant_n, mant_s))
        return sign, exp, mant

    def bits(self, f, index, count):
        """(asuint(f) >> index) & ((1 << count) - 1) for constant index and count, pieced
        together from the fields it spans."""
        sign, exp, mant = self.float_fields(f)
        high = self.binop('ADD', self.binop('MULTIPLY', sign, self.const(256.0)), exp)   # bits 23..31
        pieces = []
        if index < 23:
            take = min(count, 23 - index)
            low = self.math('FLOOR', self.binop('DIVIDE', mant, self.const(2.0 ** index)))
            pieces.append((self.math('FLOORED_MODULO', low, self.const(2.0 ** take)), 0))
        if index + count > 23:
            start = max(index, 23) - 23
            take = index + count - max(index, 23)
            hi = self.math('FLOORED_MODULO', self.math('FLOOR', self.binop('DIVIDE', high, self.const(2.0 ** start))),
                           self.const(2.0 ** take))
            pieces.append((hi, max(23 - index, 0)))
        v = None
        for piece, shift in pieces:
            piece = self.binop('MULTIPLY', piece, self.const(2.0 ** shift)) if shift else piece
            v = piece if v is None else self.binop('ADD', v, piece)
        return v if v is not None else self.const(0.0)

    def custom_bitfield_asuint(self, ins, p):
        idx, cnt = self.mask(self._in(ins, "BitIndex"), [0]), self.mask(self._in(ins, "BitCount"), [0])
        if not (idx.const and cnt.const):
            self.warnings.append("stand-in: asuint bit field with a varying range, on the value")
            return self.custom_bitfield(ins, p)
        return self.bits(self.mask(self._in(ins, "Scalar"), [0]), int(idx.s), int(cnt.s))

    def custom_packed_hsv(self, ins, p):
        # H: bits 20..31 / 360, S: bits 10..19 / 1023, V: bits 0..9 / 1023
        f = self.mask(self._in(ins, "PackedHSV"), [0])
        h = self.binop('DIVIDE', self.bits(f, 20, 12), self.const(360.0))
        s_ = self.binop('DIVIDE', self.bits(f, 10, 10), self.const(1023.0))
        v = self.binop('DIVIDE', self.bits(f, 0, 10), self.const(1023.0))
        return self.combine([h, s_, v])

    def custom_hue_to_rgb(self, ins, p):
        h = self.math('MODULO', self.mask(self._in(ins, "Hue"), [0]), self.const(1.0))
        h6 = self.binop('MULTIPLY', h, self.const(6.0))
        r = self.binop('SUBTRACT', self.math('ABSOLUTE', self.binop('SUBTRACT', h6, self.const(3.0))), self.const(1.0))
        g = self.binop('SUBTRACT', self.const(2.0), self.math('ABSOLUTE', self.binop('SUBTRACT', h6, self.const(2.0))))
        b = self.binop('SUBTRACT', self.const(2.0), self.math('ABSOLUTE', self.binop('SUBTRACT', h6, self.const(4.0))))
        return self.saturate(self.combine([r, g, b]))

    # ------------------------------------------------------------ water
    # The Water plugin draws each water body's surface mesh into its zone's water info
    # texture at run time (DrawWaterInfo: the vertex colour's flow, the surface height, the
    # ground height under it); materials decode it. A material on the surface mesh itself
    # reads the same things off the pixel: its height, its vertex colour, the depth the env knows.
    def custom_water_info_z(self, ins, p):
        # DecodeWaterInfoZHeight: the surface's own height here
        return self.comps(self.env.world_position(False))[2]

    def custom_water_info_ground(self, ins, p):
        # DecodeWaterInfoGroundHeight: the ground under the surface
        return self.binop('SUBTRACT', self.comps(self.env.world_position(False))[2], self.water_depth(),
                          label="ground under the water")

    def custom_water_info_velocity(self, ins, p):
        # DecodeWaterInfoVelocity(sample, MaxVelocity) = (sample.xy - 0.5) * 2 * MaxVelocity, of
        # DrawWaterInfo's (Flow velocity / (2 * its MaxVelocity) + 0.5): the surface's flow
        return self._flow_velocity(self.water_flow(), self.mask(self._in(ins, "MaxVelocity", 1024.0), [0]))

    def custom_water_flow(self, ins, p):
        return self._flow_velocity(self._in(ins, "Flow"), self.mask(self._in(ins, "MaxVelocity", 1024.0), [0]))

    def _flow_velocity(self, flow, max_velocity):
        """float Magnitude = Flow.x * MaxVelocity; float Direction = Flow.y * 2 * PI;
        return float2(Magnitude * cos(Direction), Magnitude * sin(Direction));"""
        speed, turn = self.comps(flow)[:2]
        mag = self.binop('MULTIPLY', speed, max_velocity)
        ang = self.binop('MULTIPLY', turn, self.const(2.0 * math.pi))
        return self.combine([self.binop('MULTIPLY', mag, self.math('COSINE', ang)),
                             self.binop('MULTIPLY', mag, self.math('SINE', ang))])

    def water_flow(self):
        """The surface's flow as DrawWaterInfo reads it: vertex colour R =
        speed / MaxFlowVelocity, G = direction / 2 pi (a river's info mesh
        carries it; still water's is black)."""
        return self._hook("water_flow", lambda: self.mask(self.env.vertex_color()[0], [0, 1]))

    def custom_water_body_fixed(self, ins, p):
        # FixedVelocity / FixedZHeight / FixedWaterDepth of the body's runtime
        # data (read only behind the Use Fixed switches; the snippet returns 0)
        self.warnings.append("stand-in: water body's fixed velocity / height / depth as 0")
        return self.const((0.0, 0.0, 0.0), 3) if getattr(self, "custom_out", 0) == 1 else self.const(0.0)

    def custom_water_zone(self, ins, p):
        # where the zone's water info texture lies: only that texture's UVs
        # read it, and the decodes above don't sample it
        self.warnings.append("stand-in: water zone (location 0, extent 1)")
        return [self.const((0.0, 0.0), 2), self.const((1.0, 1.0), 2), self.const((1.0, 1.0), 2),
                self.const(0.0)][min(getattr(self, "custom_out", 0), 3)]

    def custom_gerstner_waves(self, ins, p):
        # the body's waves are generated at run time (its wave asset's
        # generator): still water - no offset, the surface's own normal
        out = getattr(self, "custom_out", 0)
        self.warnings.append("stand-in: Gerstner waves as still water")
        if out in (1, 2, 3):
            return self.const((0.0, 0.0, 1.0), 3)
        return self.const((0.0, 0.0, 0.0), 3) if out else self.const(0.0)

    def custom_hole_clip(self, ins, p):
        # if (Mask == 0) clip(-1); return PassThru: the pixel isn't drawn
        # where the mask is 0 - kept for the material's opacity mask
        names = [i.get("InputName") for i in p.get("Inputs", [])]
        mask = next((n for n in names if "Mask" in str(n)), names[-1])
        through = next((n for n in names if n != mask), names[0])
        hole = self.math('COMPARE', self.mask(self._in(ins, mask), [0]), self.const(0.0), self.const(0.0))
        self.clip(self.binop('SUBTRACT', self.const(1.0), hole, label="not a hole"))
        return self._in(ins, through)

    def custom_hit_proxy_clip(self, ins, p):
        # the clip is only compiled into editor hit-proxy shaders
        return self._in(ins, "PassThrough")

    def custom_dynamic_bounds(self, ins, p):
        # the water's dynamic bounds are set at run time (none: nothing clipped)
        self.warnings.append("stand-in: water dynamic bounds clip() not applied")
        return self._in(ins, "Color")

    def clip(self, keep):
        """A clip() in the material's own graph: where `keep` is 0 the pixel
        isn't drawn (the build multiplies its opacity mask by clips)."""
        if self.function is None:
            self.clips.append(keep)
        else:
            self.warnings.append("clip() inside %s: the pixels it discards are drawn" % self.function.fname)

    def custom_one_minus_exp(self, ins, p):
        # return 1 - exp(-x), per component
        x = self._in(ins, "x")
        w = {"CMOT_Float1": 1, "CMOT_Float2": 2, "CMOT_Float3": 3}.get(
            str(p.get("OutputType", "")).split("::")[-1], min(x.w, 3))
        return self.combine([self.binop('SUBTRACT', self.const(1.0),
                                        self.math('EXPONENT', self.binop('MULTIPLY', c, self.const(-1.0))))
                             for c in self.comps(x)[:w]])

    def custom_euler_to_quat(self, ins, p):
        # roll, pitch, yaw (Euler.xyz, degrees) -> q (xyz, w)
        e = self.comps(self._in(ins, "Euler"))
        k = self.binop('DIVIDE', self.mask(self._in(ins, "M_PI", math.pi), [0]), self.const(360.0))
        roll, pitch, yaw = (self.binop('MULTIPLY', c, k) for c in e)
        c1, s1 = self.math('COSINE', yaw), self.math('SINE', yaw)
        c2, s2 = self.math('COSINE', pitch), self.math('SINE', pitch)
        c3, s3 = self.math('COSINE', roll), self.math('SINE', roll)

        def m3(a, b, c):
            return self.binop('MULTIPLY', self.binop('MULTIPLY', a, b), c)
        w = self.binop('ADD', m3(c1, c2, c3), m3(s1, s2, s3))
        x = self.binop('SUBTRACT', m3(c1, c2, s3), m3(s1, s2, c3))
        y = self.binop('ADD', m3(c1, s2, c3), m3(s1, c2, s3))
        z = self.binop('SUBTRACT', m3(s1, c2, c3), m3(c1, s2, s3))
        return self.with_alpha(self.combine([x, y, z]), w)

    def custom_clip(self, ins, p):
        # clip() discards the pixel: as a value, 0 there
        o, th = self.mask(self._in(ins, "Opacity"), [0]), self.mask(self._in(ins, "Threshold"), [0])
        self.warnings.append("stand-in: clip() as zero opacity")
        return self.binop('MULTIPLY', o, self.binop('SUBTRACT', self.const(1.0), self.math('LESS_THAN', o, th)))

    def custom_eye_adaptation_inverse(self, ins, p):
        # LightValue * exp(-Alpha * ln(Adaptation)) = LightValue * Adaptation ^ -Alpha
        lv, a, ad = self._in(ins, "LightValue"), self._in(ins, "Alpha"), self._in(ins, "Adaptation", 1.0)
        if ad.const and ad.s == 1.0:
            return lv
        scale = self.math('EXPONENT', self.binop('MULTIPLY', self.binop('MULTIPLY', a, self.const(-1.0)),
                                                 self.math('LOGARITHM', ad, self.const(math.e))))
        return self.binop('MULTIPLY', lv, scale)

    def custom_oct_decode(self, ins, p):
        o = self.comps(self._in(ins, "Oct"))
        x = self.binop('MULTIPLY', self.binop('ADD', o[0], o[1]), self.const(0.5))
        y = self.binop('MULTIPLY', self.binop('SUBTRACT', o[0], o[1]), self.const(0.5))
        z = self.binop('SUBTRACT', self.const(1.0), self.binop('ADD', self.math('ABSOLUTE', x), self.math('ABSOLUTE', y)))
        return self.vmath('NORMALIZE', self.combine([x, y, z]), out_w=3)

    def custom_oct_encode(self, ins, p):
        n = self.comps(self._in(ins, "N"))
        d = self.binop('ADD', self.binop('ADD', self.math('ABSOLUTE', n[0]), self.math('ABSOLUTE', n[1])),
                       self.math('ABSOLUTE', n[2]))
        x, y = self.binop('DIVIDE', n[0], d), self.binop('DIVIDE', n[1], d)
        return self.combine([self.binop('ADD', x, y), self.binop('SUBTRACT', x, y)])

    def custom_taa_params(self, ins, p):
        # (sample index, sample count): a still frame, one sample
        return self.const((0.0, 1.0), 2)

    def custom_refract(self, ins, p):
        # HLSL refract(i, n, eta) is Vector Math's Refract (eta on its Scale)
        n = self.node("ShaderNodeVectorMath", "refract", operation='REFRACT')
        self.link(self.as3(self._in(ins, "Ray")), n.inputs[0])
        self.link(self.as3(self._in(ins, "Normal")), n.inputs[1])
        self.link(self.mask(self._in(ins, "Index", 1.0), [0]), n.inputs["Scale"])
        return Val(n.outputs["Vector"], 3)

    def custom_main_pass(self, ins, p):
        return self._in(ins, "Main")

    def custom_height_band(self, ins, p):
        n = ins["NormAltitude"]
        keep = self.binop('MULTIPLY', self.binop('SUBTRACT', self.const(1.0), self.math('LESS_THAN', n, self.const(0.05))),
                          self.binop('SUBTRACT', self.const(1.0), self.math('GREATER_THAN', n, self.const(0.95))))
        return self.binop('MULTIPLY', ins["Density"], keep, label="layer 5%..95%")
