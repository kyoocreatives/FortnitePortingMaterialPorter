"""Transforms, normals and positions."""

from .core import BOUNDS_CENTRE, HEAD_SOCKET, PIXEL_NORMAL, POSITION_SPACES, VECTOR_SPACES, Val, comps3


class GeometryMixin:
    # ----------------------------------------------------------- spaces
    # UE space here, Blender's in the nodes: FP mirrors Y and scales by 0.01;
    # UE view space is x right, y up, z forward, Blender's camera -z forward.
    def to_blender(self, v, point=False, view=False):
        # (a shader's camera space is UE's view space: X right, Y up, Z forward - not the camera
        # object's, which looks down -Z)
        s = 0.01 if point else 1.0
        return self.vmath('MULTIPLY', self.as3(v), self.const((s, s, s) if view else (s, -s, s), 3), out_w=3)

    def from_blender(self, v, point=False, view=False):
        s = 100.0 if point else 1.0
        return self.vmath('MULTIPLY', v, self.const((s, s, s) if view else (s, -s, s), 3), out_w=3)

    def blender_transform(self, v, src, dst, point):
        n = self.node("ShaderNodeVectorTransform", "%s to %s" % (src.lower(), dst.lower()),
                      vector_type='POINT' if point else 'VECTOR', convert_from=src, convert_to=dst)
        self.link(v, n.inputs[0])
        return Val(n.outputs[0], 3)

    def tangent_frame(self):
        """UE's tangent basis in UE space: X along +U, Y along +V (UE's V runs
        down, Blender's up, so -bitangent), Z the normal."""
        def make():
            nb = self.to_blender(self.vertex_normal())
            tb = self.to_blender(self._hook("vertex_tangent", self._blender_tangent))
            # Blender's own bitangent where Blender gives the tangent: its sign turns with a mesh's
            # mirrored UV islands and a mirrored object, which normal x tangent misses
            bb = self.vmath('CROSS_PRODUCT', nb, tb, out_w=3) if hasattr(self.env, "vertex_tangent") \
                else self._blender_bitangent()
            return (self.from_blender(tb), self.from_blender(self.vmath('SCALE', bb, self.const(-1.0), out_w=3)),
                    self.from_blender(nb))
        return self.shared("tangent frame", make)

    def to_world(self, v, space, point):
        if space == "world":
            return v
        if space == "translated":
            return self.vmath('ADD', self.as3(v), self.env.camera_position(), out_w=3) if point else v
        if space == "local":
            return self.from_blender(self.blender_transform(self.to_blender(v, point), 'OBJECT', 'WORLD', point), point)
        if space == "view":
            return self.from_blender(self.blender_transform(self.to_blender(v, point, view=True), 'CAMERA', 'WORLD',
                                                            point), point)
        if space == "tangent":
            t, b, n = self.tangent_frame()
            x, y, z = self.comps(v)
            return self.vmath('ADD', self.vmath('ADD', self.vmath('SCALE', t, x, out_w=3),
                                                self.vmath('SCALE', b, y, out_w=3), out_w=3),
                              self.vmath('SCALE', n, z, out_w=3), label="tangent to world", out_w=3)
        return v

    def from_world(self, v, space, point, tile=None):
        if space == "world":
            return v
        if space == "translated":
            return self.vmath('SUBTRACT', self.as3(v), self.env.camera_position(), out_w=3) if point else v
        if space == "periodic":
            if tile is None:
                return v
            return self.binop('SUBTRACT', self.as3(v),
                              self.binop('MULTIPLY', tile, self.unary('FLOOR', self.binop('DIVIDE', self.as3(v), tile))))
        if space == "local":
            return self.from_blender(self.blender_transform(self.to_blender(v, point), 'WORLD', 'OBJECT', point), point)
        if space == "view":
            return self.from_blender(self.blender_transform(self.to_blender(v, point), 'WORLD', 'CAMERA', point),
                                     point, view=True)
        if space == "tangent":
            t, b, n = self.tangent_frame()
            w = self.as3(v)
            return self.combine([self.vmath('DOT_PRODUCT', w, e) for e in (t, b, n)])
        return v

    def transform(self, v, src, dst, point=False, tile=None):
        if src == dst:
            return v
        r = self.from_world(self.to_world(v, src, point), dst, point, tile)
        return self.with_alpha(r, v.a) if v.w == 4 and v.a is not None else r

    # --------------------------------------------------- Blender inputs
    def _geometry(self, socket):
        n = self.shared("geometry", lambda: self.node("ShaderNodeNewGeometry", "geometry"))
        return Val(n.outputs[socket], 1 if socket in ("Backfacing", "Pointiness", "Random Per Island") else 3)

    def _blender_tangent(self):
        """Blender's UV tangent, back in UE space. Where Blender gives none (Cycles works out a
        displacement without tangents) the mesh's own "mp_tangent" attribute, which an effect's
        mesh carries (none elsewhere: there it adds nothing)."""
        def make():
            t = Val(self.node("ShaderNodeTangent", "UV tangent", direction_type='UV_MAP').outputs[0], 3)
            baked = self.node("ShaderNodeAttribute", "baked tangent", attribute_type='GEOMETRY', attribute_name="mp_tangent")
            none = self.math('LESS_THAN', self.vmath('LENGTH', t, out_w=1), self.const(1e-4))
            return self.vmath('ADD', t, self.vmath('SCALE', Val(baked.outputs["Vector"], 3), none, out_w=3), out_w=3)
        return self.from_blender(self.shared("tangent", make))

    def _blender_bitangent(self):
        """Blender's UV bitangent (Blender world space), signed as its Normal Map node signs it:
        by the mesh's tangent handedness (a mirrored UV island) and the object's (negative scale;
        a prefab's mirrored roof piece lit its normal map upside down with normal x tangent).
        A Normal Map node reading +Y gives exactly that."""
        def make():
            nm = self.node("ShaderNodeNormalMap", "UV bitangent", space='TANGENT', uv_map="UV0")
            if hasattr(nm, "convention"):
                nm.convention = 'OPENGL'
            nm.inputs["Color"].default_value = (0.5, 1.0, 0.5, 1.0)
            return Val(nm.outputs["Normal"], 3)
        return self.shared("bitangent", make)

    def vertex_normal(self):
        return self._hook("vertex_normal", lambda: self.shared(
            "vertex normal", lambda: self.from_blender(self._geometry("Normal"))))

    def pixel_normal(self):
        """UE's PixelNormalWS: the material's own Normal as lit, its normal map applied (the vertex
        normal is VertexNormalWS). Where the env says how the Normal is read (env.normal_mode:
        (lit, tangent space)); else, and in an unlit material (UE compiles its Normal out), the
        vertex normal. In a function's group a socket the caller fills."""
        hooked = getattr(self.env, "pixel_normal", None)
        v = hooked() if hooked is not None else None
        if v is not None:
            return v
        mode = self._hook("normal_mode", lambda: None)
        if not mode or not mode[0]:
            return self.vertex_normal()
        if self.function is not None:
            return self.function.env_input(PIXEL_NORMAL, 'NodeSocketVector', (0.0, 0.0, 1.0),
                                           "UE PixelNormalWS: the material's normal, its normal map applied")
        src = getattr(self, "_normal_src", None)
        if src is None or getattr(self, "_normal_busy", False):
            # (asked while the Normal itself is made: UE won't compile that)
            return self.vertex_normal()

        def make():
            self._normal_busy = True
            try:
                n = src()
            except RuntimeError:
                n = None
            finally:
                self._normal_busy = False
            if n is None or n.const and tuple(comps3(n.s)) == (0.0, 0.0, 1.0):
                return None
            return self.from_blender(self.material_normal(n, mode[1]))
        got = self.shared("pixel normal", make)
        return got if got is not None else self.vertex_normal()

    def material_normal(self, n, tangent=True):
        """A material's Normal as Blender's world normal, one per tree (the BSDF and PixelNormalWS
        read the same node). UE's tangent space is DirectX (green down): a Normal Map node set so
        (Blender 5.2; before that, green was flipped here), or a world-space normal in UE space."""
        def make():
            if not tangent:
                return self.vmath('NORMALIZE', self.vmath('MULTIPLY', n, self.const((1.0, -1.0, 1.0), 3), out_w=3), out_w=3)
            nm = self.node("ShaderNodeNormalMap", "UE normal", space='TANGENT', uv_map="UV0")
            green = 0.5
            if hasattr(nm, "convention"):
                nm.convention = 'DIRECTX'
            else:
                green = -0.5
            col = self.vmath('ADD', self.vmath('MULTIPLY', n, self.const((0.5, green, 0.5), 3), out_w=3),
                             self.const((0.5, 0.5, 0.5), 3), out_w=3)
            self.link(col, nm.inputs["Color"])
            return Val(nm.outputs["Normal"], 3)
        return self.shared("material normal", make)

    def object_position(self, rel=False):
        """UE's Object Position: the centre of the object's bounds, not its pivot (that's Actor
        Position): a sprite's pivot is at its feet, its screen-space glow centred on its body.
        The centre (local, Blender metres) is a property the import sets on each object
        (mp_bounds_centre: the material stays shared); without it, the pivot. An outfit's parts
        take the body's bounds in Fortnite (a head's ObjectRadius is the whole character's:
        Cyclo's sprite scales by it over an ActorRadius of 97 cm), which the joined mesh's are.
        UE world, cm."""
        env = self.env

        def centre():
            n = self.node("ShaderNodeAttribute", "bounds centre", attribute_type='OBJECT',
                          attribute_name=BOUNDS_CENTRE)
            local = Val(n.outputs["Vector"], 3)
            world = self.from_blender(self.blender_transform(local, 'OBJECT', 'WORLD', True), True)
            return self.vmath('SUBTRACT', world, env.camera_position(), out_w=3) if rel else world
        return self._hook("object_position", centre, rel)

    def head_socket(self):
        """The character's head in UE world cm (HEAD_SOCKET: the import's armature head bone),
        else Object Position: a stand-in for a head socket position the game sets at run time."""
        def make():
            n = self.node("ShaderNodeAttribute", "head socket", attribute_type='OBJECT', attribute_name=HEAD_SOCKET)
            local = Val(n.outputs["Vector"], 3)
            has = self.binop('GREATER_THAN', self.vmath('LENGTH', local), self.const(1e-9))
            head = self.from_blender(self.blender_transform(local, 'OBJECT', 'WORLD', True), True)
            return self.lerp(self.object_position(), head, has, label="head socket")
        return self.shared("head socket", make)

    def local_position(self):
        def fallback():
            n = self.shared("texcoord", lambda: self.node("ShaderNodeTexCoord", "texture coordinate"))
            return self.shared("local position", lambda: self.from_blender(Val(n.outputs["Object"], 3), point=True))
        return self._hook("local_position", fallback)

    def preskinned_position(self):
        """UE's PreSkinnedPosition: the vertex where it is before skinning, which an animation
        doesn't move (a LEGO face's prints are projected from it). An env that knows the rest
        pose says; otherwise the local position (a static mesh's is the same)."""
        return self._hook("preskinned_position", self.local_position)

    def local_bounds(self):
        """(min, max), UE cm. Blender's shader nodes can't see an object's
        bounds: an env that knows the object says; otherwise +-1 m."""
        def fallback():
            self.warnings.append("object bounds assumed +-1 m")
            return self.const((-100.0, -100.0, -100.0), 3), self.const((100.0, 100.0, 100.0), 3)
        return self._hook("local_bounds", fallback)

    def evaluate_geometry(self, g, x, t, out, scope, p, P):
        env = self.env
        if t == "VertexNormalWS":
            return self.vertex_normal()
        if t == "PixelNormalWS":
            return self.pixel_normal()
        if t in ("VertexTangentWS",):
            return self.tangent_frame()[0]
        if t == "PixelDepth":
            return self.pixel_depth()
        if t == "ObjectPositionWS":
            return self.object_position("CameraRelative" in str(p.get("OriginType", "")))
        if t == "LocalPosition":
            return self.local_position()
        if t == "PreSkinnedPosition":
            return self.preskinned_position()
        if t == "PreSkinnedNormal":
            return self.vmath('NORMALIZE', self.from_world(self.vertex_normal(), "local", False), out_w=3)
        if t in ("PreSkinnedLocalBounds", "ObjectLocalBounds", "Bounds", "ObjectBounds"):
            # the pre-skinned ones are the mesh's own (a part's, when joined into a character), the
            # object's are the whole object's: env.preskinned_bounds
            mn, mx = self._hook("preskinned_bounds", self.local_bounds) if t == "PreSkinnedLocalBounds" \
                else self.local_bounds()
            full = self.vmath('SUBTRACT', mx, mn, out_w=3)
            if t == "ObjectBounds":
                return full
            return [self.vmath('SCALE', full, self.const(0.5), out_w=3), full, mn, mx][min(out, 3)]
        if t == "ObjectRadius":
            def fallback():
                mn, mx = self.local_bounds()
                return self.binop('MULTIPLY', self.vmath('DISTANCE', mn, mx), self.const(0.5))
            return self._hook("object_radius", fallback)
        if t == "ObjectOrientation":
            return self.vmath('NORMALIZE', self.to_world(self.const((0.0, 0.0, 1.0), 3), "local", False), out_w=3)
        if t == "ReflectionVectorWS":
            n = self.input(g, p.get("CustomWorldNormal"), scope, None)
            if n is not None and p.get("bNormalizeCustomWorldNormal", True):
                n = self.vmath('NORMALIZE', self.as3(n), out_w=3)
            return self.reflection(n)
        if t == "TwoSidedSign":
            return self._hook("two_sided_sign", lambda: self.binop(
                'SUBTRACT', self.const(1.0), self.binop('MULTIPLY', self._geometry("Backfacing"), self.const(2.0))))
        if t == "ScreenPosition":
            def fallback():
                n = self.shared("texcoord", lambda: self.node("ShaderNodeTexCoord", "texture coordinate"))
                u, v = self.comps(Val(n.outputs["Window"], 3))[:2]
                return self.combine([u, self.binop('SUBTRACT', self.const(1.0), v)])
            uv = self._hook("screen_uv", fallback)
            return uv if out == 0 else self.binop('MULTIPLY', uv, self.view_size())
        if t == "VertexInterpolator":
            return self.input(g, p.get("Input"), scope, self.const(0.0))
        if t == "PerInstanceRandom":
            def fallback():
                n = self.shared("object info", lambda: self.node("ShaderNodeObjectInfo", "object info"))
                return Val(n.outputs["Random"], 1)
            return self._hook("instance_random", fallback)
        if t in ("PerInstanceCustomData", "PerInstanceCustomData3Vector"):
            w = 3 if t.endswith("3Vector") else 1
            d = p.get("ConstDefaultValue", 0.0)
            dflt = self.const((d.get("R", 0.0), d.get("G", 0.0), d.get("B", 0.0)), 3) if isinstance(d, dict) \
                else self.const(float(d), w)
            dflt = self.input(g, p.get("DefaultValue"), scope, dflt)
            if w == 3:
                dflt = self.mask(dflt, [0, 1, 2]) if dflt.w >= 3 else self.combine([self.mask(dflt, [0])] * 3)
            else:
                dflt = self.mask(dflt, [0])
            # the instance's own floats where it has them (env), else the default
            return self._hook("instance_data", lambda: dflt, dflt, int(p.get("DataIndex", 0)), w)
        if t == "ViewSize":
            return self.view_size()
        if t == "SceneTexelSize":
            return self.binop('DIVIDE', self.const((1.0, 1.0), 2), self.view_size())
        if t == "ViewProperty":
            name = str(p.get("Property", "MEVP_FieldOfView")).split("::")[-1].replace("MEVP_", "")
            v = self.view_property(name)
            return v if out == 0 else self.binop('DIVIDE', self.const(1.0), v)
        if t == "IsOrthographic":
            return self._hook("is_orthographic", lambda: self.const(0.0))
        if t == "DeltaTime":
            return self._hook("delta_time", lambda: self.const(1.0 / 60.0))
        if t == "Transform":
            src = VECTOR_SPACES.get(str(p.get("TransformSourceType", "TRANSFORMSOURCE_Tangent")).split("_", 1)[-1], "tangent")
            dst = VECTOR_SPACES.get(str(p.get("TransformType", "TRANSFORM_World")).split("_", 1)[-1], "world")
            return self.transform(P("Input"), src, dst)
        if t == "TransformPosition":
            src = POSITION_SPACES.get(str(p.get("TransformSourceType", "TRANSFORMPOSSOURCE_Local")).split("_", 1)[-1], "local")
            dst = POSITION_SPACES.get(str(p.get("TransformType", "TRANSFORMPOSSOURCE_Local")).split("_", 1)[-1], "local")
            tile = self.input(g, p.get("PeriodicWorldTileSize"), scope, None)
            return self.transform(P("Input"), src, dst, point=True, tile=tile)
        return None
