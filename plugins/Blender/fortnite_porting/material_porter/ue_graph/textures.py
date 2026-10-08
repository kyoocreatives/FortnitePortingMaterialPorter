"""Texture sampling."""

import math

from .core import T, Val, short_name


class TexturesMixin:
    def side_output(self, path, type_name, pins):
        """The inputs of a material's side output node (e.g.
        VolumetricAdvancedMaterialOutput), each translated or its default."""
        g = self.graph(path)
        x = next((y for y in g.o if T(y) == type_name), None)
        p = (x or {}).get("Properties") or {}
        out = {}
        for pin, (const_key, default) in pins.items():
            dflt = p.get(const_key, default) if const_key else default
            out[pin] = self.input(g, p.get(pin), {}, self.const(float(dflt)))
        return out

    # ---------------------------------------------------------- textures
    def sample_volume(self, tname, coords, out):
        """A volume texture, as UE samples one: trilinear, wrapping on all three axes. The
        texture is a vertical stack of its slices (CUE4Parse decodes it that way, slice 0 at
        the top); eight nearest taps are weighted here, since a Linear image node would go
        through Eevee's mipmaps."""
        img, size, depth = self.env.volume(tname)
        if coords is None:
            coords = self.const((0.0, 0.0, 0.0), 3)
        c = self.comps(coords)
        dims = (float(size), float(size), float(depth))
        corners = []
        for i in range(3):
            fr = self.math('FRACT', c[i])
            xi = self.binop('SUBTRACT', self.binop('MULTIPLY', fr, self.const(dims[i])), self.const(0.5))
            lo = self.math('FLOOR', xi)
            f = self.binop('SUBTRACT', xi, lo)
            lo_w = self.math('FLOORED_MODULO', lo, self.const(dims[i]))
            hi_w = self.math('FLOORED_MODULO', self.binop('ADD', lo, self.const(1.0)), self.const(dims[i]))
            corners.append(((lo_w, self.binop('SUBTRACT', self.const(1.0), f)), (hi_w, f)))
        rows = float(size * depth)
        rgb, alpha = None, None
        for xv, xw in corners[0]:
            for yv, yw in corners[1]:
                for zv, zw in corners[2]:
                    row = self.binop('ADD', self.binop('MULTIPLY', zv, self.const(float(size))), yv)
                    u = self.binop('DIVIDE', self.binop('ADD', xv, self.const(0.5)), self.const(float(size)))
                    v = self.binop('SUBTRACT', self.const(1.0),
                                   self.binop('DIVIDE', self.binop('ADD', row, self.const(0.5)), self.const(rows)))
                    n = self.node("ShaderNodeTexImage", tname, interpolation='Closest', extension='EXTEND')
                    n.image = img
                    self.link(self.combine([u, v, self.const(0.0)]), n.inputs[0])
                    w = self.binop('MULTIPLY', self.binop('MULTIPLY', xw, yw), zw)
                    tap = self.vmath('SCALE', Val(n.outputs["Color"], 3), w, out_w=3)
                    ta = self.binop('MULTIPLY', Val(n.outputs["Alpha"], 1), w)
                    rgb = tap if rgb is None else self.vmath('ADD', rgb, tap, out_w=3)
                    alpha = ta if alpha is None else self.binop('ADD', alpha, ta)
        if out == 5:
            return self.with_alpha(rgb, alpha)
        if out == 0:
            return rgb
        if out == 4:
            return alpha
        return self.mask(rgb, [out - 1])

    # UE's TextureAddress -> the image node's extension (TA_Clamp clamps to the edge texel: EXTEND)
    EXTENSION = {"Wrap": 'REPEAT', "Clamp": 'EXTEND', "Mirror": 'MIRROR'}

    def address_mode(self, tname, own, address, img):
        """(extension, per-axis fixes) for a texture's addressing: its own (env.texture_address,
        AddressX/AddressY) through its own sampler, else the shared sampler's. One extension
        serves both axes; an axis addressed otherwise is fixed on the coordinate: clamp holds
        it between the edge texels' centres (bilinear filtering then never reaches across),
        mirror folds it (ping-pong)."""
        if address is None:
            address = self._hook("texture_address", lambda: ("Wrap", "Wrap"), tname) if own else ("Wrap", "Wrap")
        ax, ay = (tuple(address) + ("Wrap", "Wrap"))[:2]
        if ax == ay:
            return self.EXTENSION.get(ax, 'REPEAT'), ()
        ext = 'REPEAT' if "Wrap" in (ax, ay) else 'MIRROR'
        size = tuple(img.size) if img is not None else (0, 0)
        fixes = []
        for i, a in enumerate((ax, ay)):
            if a == "Clamp":
                fixes.append((i, "clamp", 0.5 / size[i] if size[i] else 0.0))
            elif a == "Mirror" and ext == 'REPEAT':
                fixes.append((i, "mirror", 0.0))
        return ext, tuple(fixes)

    def addressed(self, vec, fixes):
        if not fixes:
            return vec
        c = list(self.comps(vec))
        for i, kind, half in fixes:
            if kind == "clamp":
                c[i] = self.math('MAXIMUM', self.math('MINIMUM', c[i], self.const(1.0 - half)), self.const(half),
                                 label="clamp (TA_Clamp)")
            else:
                c[i] = self.math('PINGPONG', c[i], self.const(1.0), label="mirror (TA_Mirror)")
        return self.combine(c)

    def texture_closure(self, tname, sampler, img, own, address):
        """The closure sampling a 2D texture (Vector -> Color, Alpha), one per texture and sampler:
        at the material's root a closure zone around the texture's image node, in the Textures
        frame; in a function's group a Closure input its caller fills (env.texture_closures)."""
        spec = (str(tname), getattr(tname, "param", None), sampler or "", img.name if img is not None else "",
                bool(own), tuple(address) if address else None)
        if self.function is not None:
            return self.function.texture_input(spec, tname, sampler, img, own, address)
        # the env may take it outside this tree (a material's group: a Closure input, the image
        # node in the material's own tree - env.root_texture)
        outside = getattr(self.env, "root_texture", None)
        got = outside(spec, tname, sampler, img, own, address) if outside is not None else None
        if got is not None:
            return got
        return self.shared(("texture", spec), lambda: self._texture_zone(tname, img, own, address))

    def _texture_zone(self, tname, img, own, address):
        """A closure zone around a texture's image node (in the Textures section); its Closure socket."""
        nearest = own and self._hook("texture_nearest", lambda: False, tname)
        ext, fixes = self.address_mode(tname, own, address, img)
        label = getattr(tname, "param", None) or short_name(str(tname))
        with self.at("Textures"):
            cin = self.node("NodeClosureInput", label)
            cout = self.node("NodeClosureOutput", label)
            cout.input_items.new('VECTOR', "Vector")
            cout.output_items.new('RGBA', "Color")
            cout.output_items.new('FLOAT', "Alpha")
            cin.pair_with_output(cout)
            for z in (cin, cout):
                # the zone's ends collapsed (as FortnitePorting draws its texture closures)
                z.hide = True
                z.width = 100
            n = self.node("ShaderNodeTexImage", short_name(str(tname)), interpolation='Closest' if nearest else 'Linear',
                          extension=ext)
            n.image = img
            # collapsed and narrow: a column of textures, one row each (its label names the image)
            n.hide = True
            n.width = 160
            if getattr(tname, "param", None):
                n["mp_tex_param"] = tname.param     # which parameter's texture this is (another instance swaps it)
            self.link(self.addressed(Val(cin.outputs[0], 3), fixes), n.inputs[0])
            self.L.new(n.outputs["Color"], cout.inputs["Color"])
            self.L.new(n.outputs["Alpha"], cout.inputs["Alpha"])
        return cout.outputs[0]

    def sample_node(self, tname, coords, sampler, img=None, own=True, address=None):
        """A node reading a 2D texture at UE UVs, with outputs Color and Alpha: its image node, or
        with env.texture_closures an Evaluate Closure of the texture's closure. Unfiltered when the
        texture is, read through its own sampler (a LUT sampled at a texel's centre stays exact)."""
        if img is None:
            img = self.env.texture(tname, sampler)
        u, v = self.comps(coords)[:2]
        # UE UVs run top-left; Blender samples bottom-left
        vec = self.combine([u, self.binop('SUBTRACT', self.const(1.0), v), self.const(0.0)])
        if getattr(self.env, "texture_closures", False):
            n = self.node("NodeEvaluateClosure", getattr(tname, "param", None) or short_name(str(tname)))
            n.input_items.new('VECTOR', "Vector")
            n.output_items.new('RGBA', "Color")
            n.output_items.new('FLOAT', "Alpha")
            self.L.new(self.texture_closure(tname, sampler, img, own, address), n.inputs[0])
            self.link(vec, n.inputs["Vector"])
            if img is not None:
                n["mp_image"] = img.name        # the image it reads (env._images_upstream)
            return n
        nearest = own and self._hook("texture_nearest", lambda: False, tname)
        ext, fixes = self.address_mode(tname, own, address, img)
        n = self.node("ShaderNodeTexImage", short_name(tname), interpolation='Closest' if nearest else 'Linear', extension=ext)
        n.image = img
        if getattr(tname, "param", None):
            n["mp_tex_param"] = tname.param     # which parameter's texture this is (another instance swaps it)
        self.link(self.addressed(vec, fixes), n.inputs[0])
        return n

    def unpack_normal(self, rgb):
        """UnpackNormalMap, what a Normal sampler returns (BC5 on PC): xy = rg * 2 - 1,
        z = sqrt(saturate(1 - xy.xy)); alpha 1. (rgb, alpha)"""
        r, gr = self.comps(rgb)[:2]
        x = self.binop('SUBTRACT', self.binop('MULTIPLY', r, self.const(2.0)), self.const(1.0))
        y = self.binop('SUBTRACT', self.binop('MULTIPLY', gr, self.const(2.0)), self.const(1.0))
        d = self.binop('ADD', self.binop('MULTIPLY', x, x), self.binop('MULTIPLY', y, y))
        z = self.math('SQRT', self.saturate(self.binop('SUBTRACT', self.const(1.0), d)))
        return self.combine([x, y, z]), self.const(1.0)

    def sample(self, tname, coords, out, sampler, own=True, address=None):
        n = self.sample_node(tname, coords, sampler, own=own, address=address)
        if "Normal" in (sampler or ""):
            return self.rgba_out(*self.unpack_normal(Val(n.outputs["Color"], 3)), out)
        rgb = Val(n.outputs["Color"], 3)
        if out == 5:
            return self.with_alpha(rgb, Val(n.outputs["Alpha"], 1))
        if out == 0:
            return rgb
        if out == 4:
            return Val(n.outputs["Alpha"], 1)
        sep = self.node("ShaderNodeSeparateColor", "rgb")
        self.L.new(n.outputs["Color"], sep.inputs[0])
        return Val(sep.outputs[out - 1], 1)

    def sample_cube(self, tname, direction, out):
        """Equirect lookup in UE space: u = atan2(y, x) / 2 pi + 0.5, v = asin(z) / pi + 0.5."""
        img = self.env.cube(tname)
        d = self.vmath('NORMALIZE', direction, out_w=3)
        x, y, z = self.comps(d)
        u = self.binop('ADD', self.binop('DIVIDE', self.math('ARCTAN2', y, x), self.const(2 * math.pi)), self.const(0.5))
        v = self.binop('ADD', self.binop('DIVIDE', self.math('ARCSINE', z), self.const(math.pi)), self.const(0.5))
        n = self.node("ShaderNodeTexImage", tname, interpolation='Linear', extension='EXTEND')
        n.image = img
        self.link(self.combine([u, v, self.const(0.0)]), n.inputs[0])
        rgb = Val(n.outputs["Color"], 3)
        if out == 5:
            return self.with_alpha(rgb, Val(n.outputs["Alpha"], 1))
        if out == 0:
            return rgb
        if out == 4:
            return Val(n.outputs["Alpha"], 1)
        sep = self.node("ShaderNodeSeparateColor", "rgb")
        self.L.new(n.outputs["Color"], sep.inputs[0])
        return Val(sep.outputs[out - 1], 1)
