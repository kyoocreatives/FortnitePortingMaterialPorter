"""Renderer and scene inputs (view, depth, water, landscape layers)."""

import math

from .core import TexRef, Val, WATER_DEPTH_DEFAULT, SHELL_ATTRIBUTES, SHELL_VECTORS, render_tan_half_fov


class RendererMixin:
    def reflection(self, n=None):
        """reflect(-C, N) = 2 (N . C) N - C, N the pixel normal by default."""
        n = self.pixel_normal() if n is None else self.as3(n)
        c = self.env.camera_vector()
        d = self.vmath('DOT_PRODUCT', n, c)
        return self.vmath('SUBTRACT', self.vmath('SCALE', n, self.binop('MULTIPLY', d, self.const(2.0)), out_w=3), c,
                          label="reflection", out_w=3)

    def view_size(self):
        def fallback():
            try:
                import bpy
                r = bpy.context.scene.render
                k = r.resolution_percentage / 100.0
                return self.const((r.resolution_x * k, r.resolution_y * k), 2)
            except Exception:
                return self.const((1920.0, 1080.0), 2)
        return self._hook("view_size", fallback)

    def scene_depth_at(self, uv):
        """UE's CalcSceneDepth at a screen position (0-1, V down): view depth in cm of what the camera sees there,
        far (1e8) where nothing is. A Raycast from the camera through that position: EEVEE's marches its depth
        buffer, which blended surfaces stay out of, as UE's scene depth leaves out translucency (Cycles' rays
        hit them too). The field of view is the scene camera's when the material is built."""
        try:
            import bpy
            scene = bpy.context.scene
            tx, ty = render_tan_half_fov(scene.camera.data, scene.render)
        except Exception:
            tx, ty = 1.0, 9.0 / 16.0
        u, v = self.comps(uv)[:2]
        ndc_x = self.math('MULTIPLY_ADD', u, self.const(2.0), self.const(-1.0))
        ndc_y = self.math('MULTIPLY_ADD', v, self.const(-2.0), self.const(1.0))
        along = self.combine([self.math('MULTIPLY', ndc_x, self.const(tx)), self.math('MULTIPLY', ndc_y, self.const(ty)), self.const(1.0)])

        def camera():
            n = self.node("ShaderNodeVectorTransform", "camera position", vector_type='POINT', convert_from='CAMERA', convert_to='WORLD')
            n.inputs[0].default_value = (0.0, 0.0, 0.0)
            return Val(n.outputs[0], 3)
        origin = self.shared("camera position", camera)
        turn = self.node("ShaderNodeVectorTransform", "view to world", vector_type='VECTOR', convert_from='CAMERA', convert_to='WORLD')
        self.link(self.vmath('NORMALIZE', along, out_w=3), turn.inputs[0])     # (EEVEE's trace wants a unit direction)
        ray = self.node("ShaderNodeRaycast", "scene depth")
        self.link(origin, ray.inputs["Position"])
        self.link(Val(turn.outputs[0], 3), ray.inputs["Direction"])
        ray.inputs["Length"].default_value = 10000.0
        # the hit's view depth in cm, from where it is (EEVEE's marched hit distance is a few cm off; its position
        # is the depth buffer's); shader camera space looks down +Z

        def forward():
            n = self.node("ShaderNodeVectorTransform", "camera forward", vector_type='VECTOR', convert_from='CAMERA', convert_to='WORLD')
            n.inputs[0].default_value = (0.0, 0.0, 1.0)
            return Val(n.outputs[0], 3)
        ahead = self.vmath('DOT_PRODUCT', self.vmath('SUBTRACT', Val(ray.outputs["Hit Position"], 3), origin, out_w=3),
                           self.shared("camera forward", forward))
        depth = self.math('MULTIPLY', ahead, self.const(100.0))
        # depth where it hits, else far: added, not lerped (float32 rounds far - depth to 8 cm at 1e8)
        hit = Val(ray.outputs["Is Hit"], 1)
        return self.math('MULTIPLY_ADD', depth, hit, self.math('MULTIPLY', self.math('SUBTRACT', self.const(1.0), hit), self.const(1e8)))

    def view_property(self, name):
        """UE's ViewProperty: what a still frame has (no temporal jitter, full
        resolution), the camera's field of view."""
        v = self._hook("view_property", lambda: None, name)
        if v is not None:
            return v
        if name in ("ViewSize", "BufferSize"):
            return self.view_size()
        if name in ("FieldOfView", "TanHalfFieldOfView"):
            try:
                import bpy
                scene = bpy.context.scene
                tx, ty = render_tan_half_fov(scene.camera.data, scene.render)
                ax, ay = 2 * math.atan(tx), 2 * math.atan(ty)
            except Exception:
                ax, ay = math.radians(90.0), 2 * math.atan(math.tan(math.radians(45.0)) * 9 / 16)
            if name == "FieldOfView":
                return self.const((ax, ay), 2)
            return self.const((math.tan(ax / 2), math.tan(ay / 2)), 2)
        if name in ("WorldSpaceCameraPosition", "WorldSpaceViewPosition"):
            return self.env.camera_position()
        if name in ("ViewportOffset", "TemporalSampleOffset"):
            return self.const((0.0, 0.0), 2)
        return self.const({"TemporalSampleCount": 1.0, "ResolutionFraction": 1.0, "PreExposure": 1.0}.get(name, 0.0))

    def smoothstep(self, mn, mx, v):
        """HLSL's smoothstep(Min, Max, Value). Over an empty range (Min = Max: a softness of 0.5
        taken off both ends) (Value - Min) / 0 is an infinity the saturate turns into a hard edge,
        1 past Min and 0 before it; a Blender Map Range gives 0 everywhere (the embers drew
        nothing). Scalars: a Min and Max that are sockets are checked as the material runs."""
        w = max(mn.w, mx.w, v.w)
        if w == 1 and mn.const and mx.const and float(mn.s) == float(mx.s):
            return self.math('GREATER_THAN', v, mn)
        n = self.node("ShaderNodeMapRange", "smoothstep", interpolation_type='SMOOTHSTEP',
                      data_type='FLOAT' if w == 1 else 'FLOAT_VECTOR', clamp=True)
        if w == 1:
            self.link(v, n.inputs[0]); self.link(mn, n.inputs[1]); self.link(mx, n.inputs[2])
            n.inputs[3].default_value, n.inputs[4].default_value = 0.0, 1.0
            smooth = Val(n.outputs[0], 1)
            if mn.const and mx.const:
                return smooth
            # (1 where Min is Max) * (the hard edge - the Map Range's), added to the Map Range's
            empty = self.math('COMPARE', mn, mx, self.const(0.0))
            edge = self.math('GREATER_THAN', v, mn)
            return self.binop('ADD', smooth, self.binop('MULTIPLY', empty, self.binop('SUBTRACT', edge, smooth)))
        self.link(v, n.inputs[6]); self.link(mn, n.inputs[7]); self.link(mx, n.inputs[8])
        n.inputs[9].default_value = (0.0, 0.0, 0.0); n.inputs[10].default_value = (1.0, 1.0, 1.0)
        return Val(n.outputs[1], min(w, 3))

    def stand_in(self, what, v):
        self.warnings.append("stand-in: " + what)
        return v

    def light_color(self):
        return self._hook("light_color", lambda: self.stand_in("sun colour 1", self.const((1.0, 1.0, 1.0), 3)))

    def pixel_depth(self):
        def fallback():
            n = self.shared("camera data", lambda: self.node("ShaderNodeCameraData", "camera data"))
            return self.binop('MULTIPLY', Val(n.outputs["View Z Depth"], 1), self.const(100.0))
        return self._hook("pixel_depth", fallback)

    def water_depth(self):
        """How deep the water is under this pixel of its surface (UE cm: the surface's height less
        the ground's; below 0 where the ground rises over it; measured along the surface's normal
        where it isn't level). At run time the water info texture holds both heights; here the
        env's water_depth() answers, else WATER_DEPTH_DEFAULT."""
        return self.shared("water depth", lambda: self._hook("water_depth", lambda: self.stand_in(
            "water depth %g cm (the ground under the water unknown)" % WATER_DEPTH_DEFAULT,
            self.const(WATER_DEPTH_DEFAULT))))

    def water_path(self):
        """The view ray's way through the water to the ground behind it (UE cm, >= 0): the depth
        (along the surface's normal: down for level water) over the ray's steepness to the
        surface, the ground taken parallel to it (what SceneDepthWithoutWater - PixelDepth measures)."""
        def make():
            # level water (normal within 60 degrees of up) is measured straight down
            n = self.vertex_normal()
            level = self.math('GREATER_THAN', self.math('ABSOLUTE', self.comps(n)[2]), self.const(0.5 - 1e-6))
            n = self.lerp(n, self.const((0.0, 0.0, 1.0), 3), level, label="level: up")
            up = self.math('ABSOLUTE', self.vmath('DOT_PRODUCT', self.env.camera_vector(), n))
            path = self.binop('DIVIDE', self.water_depth(), self.math('MAXIMUM', up, self.const(0.02)))
            return self.math('MAXIMUM', path, self.const(0.0), label="water path")
        return self.shared("water path", make)

    def sample_array(self, tname, coords, sampler):
        """A texture array at (u, v, slice): the env's slices, the nearest one
        (as UE rounds the slice index); float4 with alpha."""
        slices = self._hook("texture_slices", lambda: None, tname, sampler)
        if not slices:
            slices = [self.env.texture(tname, sampler)]
            self.warnings.append("stand-in: texture array %s as its first slice" % tname)
        c = self.comps(self.as3(coords)) if coords is not None else [self.const(0.0)] * 3
        uv = self.combine(c[:2])
        vals = []
        for img in slices:
            n = self.sample_node(tname, uv, sampler, img=img)
            if "Normal" in (sampler or ""):
                vals.append(self.with_alpha(*self.unpack_normal(Val(n.outputs["Color"], 3))))
            else:
                vals.append(self.with_alpha(Val(n.outputs["Color"], 3), Val(n.outputs["Alpha"], 1)))
        if len(vals) == 1:
            return vals[0]
        z = c[2]
        if z.const:
            return vals[max(0, min(len(vals) - 1, int(math.floor(z.s + 0.5))))]
        return self._pick(self.math('ROUND', z), vals)

    def evaluate_renderer(self, g, x, t, out, scope, p, P):
        env = self.env
        if t == "EyeAdaptation":
            return self._hook("exposure", lambda: self.const(1.0))
        if t == "EyeAdaptationInverse":
            # LightValue * lerp(1, 1 / EyeAdaptation, Alpha): at exposure 1, LightValue
            lv = self.input(g, p.get("LightValueInput"), scope, self.const(1.0))
            ea = self._hook("exposure", lambda: self.const(1.0))
            if ea.const and ea.s == 1.0:
                return lv
            a = self.input(g, p.get("AlphaInput"), scope, self.const(1.0))
            return self.binop('MULTIPLY', lv, self.lerp(self.const(1.0), self.binop('DIVIDE', self.const(1.0), ea), a))
        if t == "AtmosphericLightVector":
            return env.light_direction()
        if t == "AtmosphericLightColor":
            return self.light_color()
        if t == "SkyAtmosphereLightIlluminance":
            return self.light_color()
        if t == "SkyLightEnvMapSample":
            d = self.input(g, p.get("Direction"), scope, None) or self.pixel_normal()
            r = self.input(g, p.get("Roughness"), scope, self.const(0.0))
            return self._hook("sky_light", lambda: self.stand_in("sky light as a flat grey", self.const((0.5, 0.5, 0.5), 3)),
                              d, r)
        if t in ("DDX", "DDY"):
            # no screen derivatives in Blender's shader nodes: a tiny step
            # (the sharp, infinitely-resolved limit of what they're used for)
            v = P("Value")
            return self.stand_in("%s as 1e-4" % t, self.const(0.0001) if v.w == 1 else self.const((0.0001,) * 3, v.w))
        if t == "DepthFade":
            # soft-particle fade against the scene's depth: InOpacity x saturate((scene depth - pixel
            # depth) / FadeDistance). Where the env can cast a ray behind this pixel (env.depth_behind:
            # cm along the view ray, and whether it hit; a see-through material) it fades by that,
            # else not at all
            opacity = self.input(g, p.get("InOpacity"), scope, self.const(float(p.get("InOpacityDefault", 1.0))))
            behind = getattr(self.env, "depth_behind", None)
            got = behind() if behind is not None else None
            if got is None:
                return self.stand_in("DepthFade as its opacity", opacity)
            distance, known = got
            fade = self.input(g, p.get("FadeDistance"), scope, self.const(float(p.get("FadeDistanceDefault", 100.0))))
            # UE divides by max(FadeDistance, 0.0001): a 0 fade distance leaves it all drawn
            # (Blender's x / 0 is 0, which hid it wherever anything was behind: Elite Jules' crown)
            fade = self.math('MAXIMUM', fade, self.const(0.0001))
            near = self.saturate(self.binop('DIVIDE', distance, fade, label="depth fade"))
            factor = self.math('ADD', self.const(1.0), self.math('MULTIPLY', self.math('SUBTRACT', near, self.const(1.0)), known))
            return self.binop('MULTIPLY', opacity, factor, label="depth fade")
        if t == "SceneDepth":
            # the depth of the surface behind this pixel: where the env can cast a ray behind it
            # (env.depth_behind: a see-through material), this pixel's depth carried on along the
            # view ray by the distance found; else far
            behind = getattr(self.env, "depth_behind", None)
            got = behind() if behind is not None else None
            if got is None:
                return self.stand_in("SceneDepth as far (1e6)", self.const(1e6))
            distance, known = got
            n = self.shared("camera data", lambda: self.node("ShaderNodeCameraData", "camera data"))
            along = self.math('MULTIPLY', Val(n.outputs["View Distance"], 1), self.const(100.0))
            scene = self.binop('MULTIPLY', self.pixel_depth(), self.binop('DIVIDE', self.math('ADD', along, distance), along))
            return self.math('ADD', self.const(1e6), self.math('MULTIPLY', self.math('SUBTRACT', scene, self.const(1e6)), known), label="scene depth")
        if t == "SceneDepthWithoutWater":
            # the scene behind the water: its depth along this pixel's view ray
            return self.binop('ADD', self.pixel_depth(), self.water_path(), label="scene depth without water")
        if t == "SceneColor":
            return self.stand_in("SceneColor as black", self.const((0.0, 0.0, 0.0), 3))
        if t == "SceneTexture":
            if out in (1, 2):
                size = self.view_size()
                return size if out == 1 else self.binop('DIVIDE', self.const((1.0, 1.0), 2), size)
            sid = str(p.get("SceneTextureId", "ESceneTextureId::PPI_SceneColor")).split("::")[-1]
            if sid == "PPI_WorldNormal":
                rgb = self.pixel_normal()
            else:
                v = {"PPI_Roughness": 0.5, "PPI_Specular": 0.5, "PPI_BaseColor": 0.5, "PPI_DiffuseColor": 0.5,
                     "PPI_SceneDepth": 1e6, "PPI_CustomDepth": 1e6, "PPI_Opacity": 1.0,
                     "PPI_AmbientOcclusion": 1.0}.get(sid, 0.0)
                rgb = self.const((v, v, v), 3)
            return self.stand_in("SceneTexture %s" % sid, self.with_alpha(rgb, self.const(1.0)))
        if t == "DistanceFieldApproxAO":
            return self.stand_in("distance-field AO as 1", self.const(1.0))
        if t == "RecordTextureStreamingInfo":
            # a streaming-statistics tap: hands its texture on
            return self.input(g, p.get("TextureObject"), scope, self.const(0.0))
        if t == "CurveAtlasRowParameter":
            # a row of a curve atlas, the row a scalar parameter (the instance
            # picks the curve): u = time, v = (row + 0.5) / atlas height
            atlas = self.tex_key(p.get("Atlas"))
            row = env.scalar(p.get("ParameterName"), float(p.get("DefaultValue", 0.0)))
            img = env.texture(atlas, "SAMPLERTYPE_LinearColor")
            h = float(img.size[1]) if img is not None and img.size[1] > 1 else 256.0
            time = self.mask(self.input(g, p.get("InputTime"), scope, self.const(0.0)), [0])
            v = self.binop('DIVIDE', self.binop('ADD', row, self.const(0.5)), self.const(h))
            n = self.sample_node(atlas, self.combine([time, v]), "SAMPLERTYPE_LinearColor", img=img,
                                 address=("Clamp", "Clamp"))
            rgb = Val(n.outputs["Color"], 3)
            if out == 4:
                return Val(n.outputs["Alpha"], 1)
            return rgb if out == 0 else self.mask(rgb, [out - 1])
        if t in ("TextureSampleParameterSubUV", "ParticleSubUV"):
            # a flipbook's first frame: a plain mesh has no particle to pick one
            tex = self.tex_param(p, self.tex_key(p.get("Texture")))
            ref = self.input(g, p.get("TextureObject"), scope, None)
            if ref is not None and isinstance(ref.s, TexRef):
                tex = ref.s.name
            coords = self.input(g, p.get("Coordinates"), scope, None) or env.uv(0)
            if not getattr(env, "entry", {}).get("sprite"):
                self.warnings.append("stand-in: %s sampled whole (no particle sub-image)" % t)
            return self.sample(tex, coords, out, p.get("SamplerType", ""))
        if t == "TextureSampleParameter2DArray":
            tex = self.tex_param(p, self.tex_key(p.get("Texture")))
            v = self.sample_array(tex, self.input(g, p.get("Coordinates"), scope, None), p.get("SamplerType", ""))
            return self.rgba_out(Val(v.s, 3), v.a, out)
        if t in ("RuntimeVirtualTextureSampleParameter", "RuntimeVirtualTextureSample"):
            # the terrain's virtual texture under the object: none here - a
            # neutral surface, its height far below (height blends stay off)
            vals = [self.const((0.5, 0.5, 0.5), 3), self.const(0.5), self.const(0.5), self.const((0.0, 0.0, 1.0), 3),
                    self.const(-1e6), self.const(0.0), self.const(0.0)]
            return self.stand_in("runtime virtual texture", vals[min(out, len(vals) - 1)])
        if t == "ParticleColor":
            rgb, a = self._hook("particle_color", lambda: (self.const((1.0, 1.0, 1.0), 3), self.const(1.0)))
            return self.rgba_out(rgb, a, out)
        if t == "DecalColor":
            # a decal's colour (a Niagara decal's: its particle's DecalColor), RGBA
            rgb, a = self._hook("particle_color", lambda: (self.const((1.0, 1.0, 1.0), 3), self.const(1.0)))
            return self.with_alpha(rgb, a)
        if t == "DecalLifetimeOpacity":
            # a decal's fade over its life (a Niagara decal's: its particle's DecalFade); 1 on anything else
            return self._hook("decal_fade", lambda: self.const(1.0))
        if t == "DynamicParameter":
            d = p.get("DefaultValue") or {}
            dflt = self.const((d.get("R", 1.0), d.get("G", 1.0), d.get("B", 1.0), d.get("A", 1.0)), 4)
            v = self._hook("dynamic_parameter", lambda: dflt, int(p.get("ParameterIndex", 0)), dflt)
            if out < 4:
                return self.mask(v, [out])
            return Val(v.s, 3) if out == 4 else v
        if t == "ParticleRandom":
            return self._hook("particle_random", lambda: self.evaluate_geometry(g, x, "PerInstanceRandom", out, scope, p, P))
        if t == "ParticlePositionWS":
            return env.actor_position("CameraRelative" in str(p.get("OriginType", "")))
        if t == "ParticleRadius":
            def object_radius():
                return self.evaluate_geometry(g, x, "ObjectRadius", out, scope, p, P)
            return self._hook("particle_radius", object_radius, object_radius)
        if t == "ParticleMacroUV":
            return self.evaluate_geometry(g, x, "ScreenPosition", 0, scope, p, P)
        if t == "ParticleRelativeTime":
            return self._hook("particle_time", lambda: self.stand_in("ParticleRelativeTime as 0", self.const(0.0)))
        if t == "ParticleSpeed":
            return self._hook("particle_speed", lambda: self.stand_in("ParticleSpeed as 0", self.const(0.0)))
        if t == "PerInstanceFadeAmount":
            return self.stand_in("%s as 0" % t, self.const(0.0))
        if t in SHELL_ATTRIBUTES:
            # the shell copy's layer (0 off the copies: the mesh itself, the base layer)
            name = SHELL_ATTRIBUTES[t]
            return self.shared(name, lambda: Val(self.node("ShaderNodeAttribute", name, attribute_type='GEOMETRY',
                                                           attribute_name=name).outputs["Fac"], 1))
        if t in ("ParticleMotionBlurFade", "SphericalParticleOpacity"):
            return self.stand_in("%s as 1" % t, self.const(1.0))
        if t == "ParticleSize":
            return self._hook("particle_size", lambda: self.stand_in("ParticleSize as 1 m", self.const((100.0, 100.0), 2)))
        if t == "ParticleSpriteRotation":
            # a sprite's turn: radians, then degrees (the outputs mask one each)
            return self._hook("particle_rotation", lambda: self.stand_in("ParticleSpriteRotation as 0", self.const((0.0, 0.0), 2)))
        if t == "ParticleDirection":
            return self._hook("particle_direction", lambda: self.stand_in("ParticleDirection as 0", self.const((0.0, 0.0, 0.0), 3)))
        if t in SHELL_VECTORS:
            # the shell copy's offset from its root (UE local cm): the material moves it there (WPO)
            name = SHELL_VECTORS[t]
            return self.shared(name, lambda: self.from_blender(Val(self.node(
                "ShaderNodeAttribute", name, attribute_type='GEOMETRY', attribute_name=name).outputs["Vector"], 3), point=True))
        if t == "SamplePhysicsVectorField":
            return self.stand_in("%s as 0" % t, self.const((0.0, 0.0, 0.0), 3))
        if t == "LandscapeLayerCoords":
            # UE: the landscape's own texture coordinates (quads, landscape-wide; an exported
            # landscape carries them as UV0, XZ/YZ mappings read UV1/UV2) over the mapping
            # scale, rotated, panned (UMaterialExpressionLandscapeLayerCoords::Compile)
            mapping = str(p.get("MappingType", ""))
            index = 1 if mapping.endswith("XZ") else 2 if mapping.endswith("YZ") else 0
            base = self._hook("landscape_uv", lambda: None, index)
            stand = base is None
            if stand:
                # not on a landscape: world XY in 1 m quads
                wp = self.comps(env.world_position(False))
                base = self.combine([self.binop('DIVIDE', wp[0], self.const(100.0)), self.binop('DIVIDE', wp[1], self.const(100.0))])
            bu, bv = self.comps(base)[:2]
            scale = 1.0 / (float(p.get("MappingScale", 0.0)) or 1.0)
            rot = math.radians(float(p.get("MappingRotation", 0.0)))
            c, s_ = math.cos(rot) * scale, math.sin(rot) * scale
            u = self.binop('ADD', self.binop('MULTIPLY', bu, self.const(c)), self.binop('MULTIPLY', bv, self.const(s_)))
            v = self.binop('ADD', self.binop('MULTIPLY', bu, self.const(-s_)), self.binop('MULTIPLY', bv, self.const(c)))
            u = self.binop('ADD', u, self.const(float(p.get("MappingPanU", 0.0))))
            v = self.binop('ADD', v, self.const(float(p.get("MappingPanV", 0.0))))
            uv = self.combine([u, v])
            return self.stand_in("landscape coordinates from world XY", uv) if stand else uv
        if t in ("LandscapeLayerSample", "LandscapeLayerWeight", "LandscapeVisibilityMask", "LandscapeLayerBlend"):
            return self.landscape_layers(g, t, scope, p)
        if t == "ScalarBlueNoise":
            n = self.node("ShaderNodeTexWhiteNoise", "blue noise (white)", noise_dimensions='2D')
            self.link(self.as3(self.evaluate_geometry(g, x, "ScreenPosition", 1, scope, p, P)), n.inputs["Vector"])
            return self.stand_in("blue noise as white noise", Val(n.outputs["Value"], 1))
        return None

    def layer_weight(self, name):
        """A landscape layer's painted weight here (env.landscape_weight: the
        exported landscape's colour layer of that name), else None."""
        return self._hook("landscape_weight", lambda: None, name) if name else None

    def landscape_layers(self, g, t, scope, p):
        """UE's landscape layer expressions, from the painted weights (layers
        a component doesn't paint weigh 0, as in UE)."""
        if t == "LandscapeLayerSample":
            w = self.layer_weight(p.get("ParameterName"))
            return w if w is not None else self.stand_in("landscape layer weight as 0", self.const(0.0))
        if t == "LandscapeVisibilityMask":
            w = self.layer_weight("__LANDSCAPE_VISIBILITY__")
            return self.const(1.0) if w is None else self.binop('SUBTRACT', self.const(1.0), w)
        if t == "LandscapeLayerWeight":
            # Base + Layer * weight (a layer not painted leaves Base)
            base = self.input(g, p.get("Base"), scope, None)
            if base is None:
                cb = p.get("ConstBase") or {}
                base = self.const((float(cb.get("X", 0.0)), float(cb.get("Y", 0.0)), float(cb.get("Z", 0.0))), 3)
            w = self.layer_weight(p.get("ParameterName"))
            if w is None:
                return base
            layer = self.input(g, p.get("Layer"), scope, None)
            if layer is None:
                return base
            return self.binop('ADD', base, self.binop('MULTIPLY', layer, w))
        # LandscapeLayerBlend: weight-blended layers add up; height-blended ones share
        # what their heights make of their weights; alpha-blended ones go over the result
        out, height_terms, alpha = None, [], []
        for layer in p.get("Layers") or []:
            name = layer.get("LayerName")
            w = self.layer_weight(name)
            if w is None:
                continue
            value = self.input(g, layer.get("LayerInput"), scope, None)
            if value is None:
                cl = layer.get("ConstLayerInput") or {}
                value = self.const((float(cl.get("X", 0.0)), float(cl.get("Y", 0.0)), float(cl.get("Z", 0.0))), 3)
            kind = str(layer.get("BlendType", ""))
            if kind.endswith("HeightBlend"):
                h = self.input(g, layer.get("HeightInput"), scope, None)
                if h is None:
                    h = self.const(float(layer.get("ConstHeightInput", 0.5)))
                h = self.mask(h, [0]) if h.w > 1 else h
                # clamp(lerp(-1, 1, weight) + height, 0.0001, 1)
                mw = self.math('ADD', self.math('MULTIPLY_ADD', w, self.const(2.0), self.const(-1.0)), h)
                mw = self.math('MAXIMUM', self.math('MINIMUM', mw, self.const(1.0)), self.const(0.0001))
                height_terms.append((value, mw))
            elif kind.endswith("AlphaBlend"):
                alpha.append((value, w))
            else:
                term = self.binop('MULTIPLY', value, w)
                out = term if out is None else self.binop('ADD', out, term)
        if height_terms:
            total = height_terms[0][1]
            for _, mw in height_terms[1:]:
                total = self.math('ADD', total, mw)
            for value, mw in height_terms:
                term = self.binop('MULTIPLY', value, self.math('DIVIDE', mw, total))
                out = term if out is None else self.binop('ADD', out, term)
        if out is None:
            out = self.const(0.0)
        for value, w in alpha:
            out = self.lerp(out, value, w)
        return out
