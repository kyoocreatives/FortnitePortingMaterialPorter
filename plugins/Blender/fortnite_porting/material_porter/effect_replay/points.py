"""Per-frame particle points and the values bound to them."""

import bpy
import numpy as np

from .. import effects

from .stand import DECAL_DOWN, KEY_ROOT, LIGHTS_MAX


# a material parameter's value type in a renderer binding: its component count
BOUND_WIDTHS = {"NiagaraFloat": 1, "NiagaraBool": 1, "NiagaraInt32": 1, "Vector2f": 2, "Vector3f": 3, "NiagaraPosition": 3,
                "Vector4f": 4, "LinearColor": 4}


def bindings(renderer, emitter):
    """A renderer's material parameters bound to system values (MaterialParameters AttributeBindings
    to a System., emitter or User. float or vector): [(parameter, variable, components)]. A curve
    exposed as a texture is resolved by the app, and a texture isn't a value."""
    out = []
    for b in (renderer.get("MaterialParameters") or {}).get("AttributeBindings") or []:
        var = b.get("ResolvedNiagaraVariable") or b.get("NiagaraVariable") or {}
        name, param = str(var.get("Name") or ""), str(b.get("MaterialParameterName") or "")
        kind = str(((var.get("TypeDef") or {}).get("ClassStructOrEnum") or {}).get("ObjectName") or "")
        width = BOUND_WIDTHS.get(kind.split("'")[1] if "'" in kind else kind)
        if not name or not param or param == "None" or width is None:
            continue
        if name.startswith("Emitter."):
            name = emitter.name + name[len("Emitter"):]
        out.append((param, name, width))
    return out


def bind_parameters(piece, carriers, bound, history, start, loop):
    """Key a renderer's bound material parameters over the replay (where they change) on the piece and
    what draws it, as mp_bind_<parameter>. An Attribute node feeds that to the parameter's input on
    the piece's material, which is its own copy since other pieces may draw the same material with
    other values. Returns the parameters bound."""
    done = []
    # the particles draw the piece's mesh with its materials, so the piece gets a mesh of its own
    if piece.data is not None and piece.data.users > 1:
        piece.data = piece.data.copy()
    for slot in piece.material_slots:
        material = slot.material
        if material is None or not material.use_nodes:
            continue
        if material.get("mp_bound_for") != piece.name:
            material = material.copy()
            material["mp_bound_for"] = piece.name
            slot.material = material
        tree = material.node_tree
        groups = [n for n in tree.nodes if n.type == 'GROUP' and n.node_tree is not None]
        for param, name, width in bound:
            values = [v for v in history.get(name) or [] if v is not None]
            sockets = [(g, i) for g in groups for i in g.inputs if i.name.lower() == param.lower()]
            if not values or not sockets:
                continue
            key = "mp_bind_" + param
            rows = np.array([np.resize(v, width) if len(v) else np.zeros(width) for v in values], np.float64)
            for carrier in carriers:
                carrier[key] = float(rows[-1][0]) if width == 1 else [float(x) for x in rows[-1]]
                if len(rows) > 1 and np.ptp(rows, axis=0).max() > 1e-6:
                    for c in range(width):
                        _keys(carrier, '["%s"]' % key, c if width > 1 else 0, range(start, start + len(rows)), rows[:, c], loop)
            attribute = tree.nodes.get(key) or tree.nodes.new("ShaderNodeAttribute")
            attribute.name = attribute.label = key
            attribute.attribute_type, attribute.attribute_name = 'OBJECT', key
            for group, socket in sockets:
                attribute.location = (group.location.x - 260, group.location.y - 60 * len(done))
                tree.links.new(attribute.outputs["Fac" if width == 1 else "Color"], socket)
                alpha = group.inputs.get(socket.name + " (A)")
                if alpha is not None and width == 4:
                    tree.links.new(attribute.outputs["Alpha"], alpha)
            done.append(param)
    return sorted(set(done))


class Track:
    """An emitter's particles over the frames, one attribute at a time."""

    def __init__(self, emitter, frames):
        self.layout, self.frames = emitter.layout, frames
        self.counts = np.array([f[0].shape[1] for f in frames], np.int64)
        self.total = int(self.counts.sum())
        self.frame = np.repeat(np.arange(len(frames), dtype=np.int32), self.counts)
        # which play of the effect each particle belongs to (an animation can play it several times)
        self.run = np.concatenate([f[2] if len(f) > 2 else np.zeros(f[0].shape[1], np.int32) for f in frames]) if frames else np.zeros(0, np.int32)

    def has(self, name):
        return name in self.layout.vars

    def get(self, name, default):
        """The attribute over all frames (particles x components); the default if the emitter has none."""
        v = self.layout.vars.get(name)
        if v is None:
            return np.tile(np.asarray(default, np.float32), (self.total, 1))
        _, f0, i0, nf, ni = v
        rows = [f[0][f0:f0 + nf] if nf else f[1][i0:i0 + ni] for f in self.frames]
        return np.concatenate(rows, axis=1).T


def bound(renderer, binding, default):
    """The particle attribute a renderer reads for a binding: its own choice, else the usual one."""
    name = ((renderer.get(binding) or {}).get("ParamMapVariable") or {}).get("Name") or ""
    return name[len("Particles."):] if name.startswith("Particles.") else default


def _ribbon_runs(frame, ribbon, order, position):
    """The points of every frame's ribbons in the order each ribbon runs through them: (the sort,
    point index in its ribbon, ribbon point count, distance along the ribbon, ribbon length),
    in UE units."""
    n = len(frame)
    if not n:
        empty = np.zeros(0, np.float32)
        return np.zeros(0, np.int64), empty, empty, empty, empty
    sort = np.lexsort((order, ribbon, frame))
    f, r, p = frame[sort], ribbon[sort], position[sort].astype(np.float64)
    first = np.ones(n, bool)
    first[1:] = (f[1:] != f[:-1]) | (r[1:] != r[:-1])
    step = np.zeros(n)
    step[1:] = np.linalg.norm(p[1:] - p[:-1], axis=1)
    step[first] = 0.0
    run = np.cumsum(first) - 1                      # ribbon each sorted point belongs to
    along = np.cumsum(step)
    start = along[first][run]
    along = along - start
    counts = np.bincount(run)
    index = np.arange(n) - np.nonzero(first)[0][run]
    length = np.zeros(len(counts))
    np.maximum.at(length, run, along)
    return sort, index.astype(np.float32), counts[run].astype(np.float32), along.astype(np.float32), length[run].astype(np.float32)


def _ribbon_uv(track, renderer, which, runs, pick):
    """A ribbon's UV set (0 or 1) at every point: (U, V at one edge, V at the other). U runs from the
    ribbon's first point in link order (the youngest if there is no link order) over the whole ribbon
    (by length, or evenly per point) or tiled every Tiling Length, then the renderer's scale and
    offset, and the emitter's own U and V range if it gives one."""
    sort, index, count, along, length = runs
    settings = renderer.get("UV%dSettings" % which) or {}
    mode = str(settings.get("DistributionMode", "ScaledUsingRibbonSegmentLength"))
    tiling = float(settings.get("TilingLength", 100.0)) or 100.0
    n = len(sort)
    u = np.zeros(n, np.float32)
    with np.errstate(all="ignore"):
        if "TiledFromStart" in mode and track.has(bound(renderer, "RibbonUVDistance", "RibbonUVDistance")):
            u = pick(track.get(bound(renderer, "RibbonUVDistance", "RibbonUVDistance"), (0,)))[:, 0][sort] / tiling
        elif "Tiled" in mode:
            u = along / tiling
        elif "ScaledUniformly" in mode:
            u = np.where(count > 1, index / np.maximum(count - 1, 1), 0.0)
        else:
            u = np.where(length > 0, along / np.maximum(length, 1e-9), 0.0)
    scale, offset = settings.get("Scale") or {}, settings.get("Offset") or {}
    sx, sy, ox, oy = float(scale.get("X", 1.0)), float(scale.get("Y", 1.0)), float(offset.get("X", 0.0)), float(offset.get("Y", 0.0))
    out = np.zeros((n, 3), np.float32)
    out[sort, 0] = np.nan_to_num(u) * sx + ox
    out[:, 1], out[:, 2] = oy, sy + oy
    given = bound(renderer, "U%dOverrideBinding" % which, "U%dOverride" % which)
    if settings.get("bEnablePerParticleUOverride") and track.has(given):
        out[:, 0] = pick(track.get(given, (0,)))[:, 0]
    given = bound(renderer, "V%dRangeOverrideBinding" % which, "V%dRangeOverride" % which)
    if settings.get("bEnablePerParticleVRangeOverride") and track.has(given):
        out[:, 1:3] = pick(track.get(given, (0, 1)))[:, :2]
    return out


def _attribute(mesh, name, kind, values):
    field = {"FLOAT_VECTOR": "vector", "FLOAT_COLOR": "color"}.get(kind, "value")
    a = mesh.attributes.new(name, kind, 'POINT')
    a.data.foreach_set(field, np.ascontiguousarray(values).ravel())


def points(name, track, renderer, kind, scale, keep=None):
    """A mesh of points: every frame's particles, with what the piece's instances (a ribbon's curve)
    and materials take from each."""
    pick = (lambda a: a[keep]) if keep is not None else (lambda a: a)
    flip = np.array([1.0, -1.0, 1.0], np.float32)       # UE's Y is flipped
    position = pick(track.get(bound(renderer, "PositionBinding", "Position"), (0, 0, 0))) * flip * scale
    mesh = bpy.data.meshes.new(name)
    mesh.vertices.add(len(position))
    mesh.vertices.foreach_set("co", np.ascontiguousarray(position, np.float32).ravel())
    _attribute(mesh, "mp_frame", 'INT', pick(track.frame))
    _attribute(mesh, "mp_velocity", 'FLOAT_VECTOR', pick(track.get(bound(renderer, "VelocityBinding", "Velocity"), (0, 0, 0))) * flip)
    if kind == "Ribbon":
        # a ribbon runs through its particles in link order (by age if none), one ribbon per ID
        _attribute(mesh, "mp_width", 'FLOAT', pick(track.get(bound(renderer, "RibbonWidthBinding", "RibbonWidth"), (1,))) * scale)
        order = bound(renderer, "RibbonLinkOrderBinding", "RibbonLinkOrder")
        _attribute(mesh, "mp_order", 'FLOAT', pick(track.get(order if track.has(order) else bound(renderer, "NormalizedAgeBinding", "NormalizedAge"), (0,))))
        ribbon = pick(track.get(bound(renderer, "RibbonIdBinding", "RibbonID"), (0,)))[:, 0].astype(np.int64)
        _attribute(mesh, "mp_ribbon", 'INT', (ribbon % 100003 + pick(track.run) * 100003).astype(np.int32))      # each play gets its own ribbons
        _attribute(mesh, "mp_facing", 'FLOAT_VECTOR', pick(track.get(bound(renderer, "RibbonFacingBinding", "RibbonFacing"), (0, 0, 1))) * flip)
        # two UV sets as the renderer lays them along the ribbon: per point (U, V at one edge, V at the other)
        groups = _ribbon_runs(pick(track.frame), (ribbon % 100003 + pick(track.run) * 100003),
                              pick(track.get(order if track.has(order) else bound(renderer, "NormalizedAgeBinding", "NormalizedAge"), (0,)))[:, 0], position / scale)
        for i in (0, 1):
            _attribute(mesh, "mp_uv%d" % i, 'FLOAT_VECTOR', _ribbon_uv(track, renderer, i, groups, pick))
    elif kind == "Sprite":
        size = pick(track.get(bound(renderer, "SpriteSizeBinding", "SpriteSize"), (50, 50)))
        _attribute(mesh, "mp_size", 'FLOAT_VECTOR', np.concatenate([size, np.zeros((len(size), 1), np.float32)], axis=1))   # UE units, for the materials
        size = size * scale
        _attribute(mesh, "mp_facing", 'FLOAT_VECTOR', pick(track.get(bound(renderer, "SpriteFacingBinding", "SpriteFacing"), (1, 0, 0))) * flip)
        _attribute(mesh, "mp_scale", 'FLOAT_VECTOR', np.concatenate([size, np.ones((len(size), 1), np.float32)], axis=1))
        _attribute(mesh, "mp_spin", 'FLOAT', -np.radians(pick(track.get(bound(renderer, "SpriteRotationBinding", "SpriteRotation"), (0,)))))
        # what the length runs along: the emitter's own vector (Custom Alignment), else the velocity
        custom = bound(renderer, "SpriteAlignmentBinding", "SpriteAlignment")
        if "CustomAlignment" in str(renderer.get("Alignment")) and track.has(custom):
            _attribute(mesh, "mp_align", 'FLOAT_VECTOR', pick(track.get(custom, (0, 0, 1))) * flip)
        else:
            _attribute(mesh, "mp_align", 'FLOAT_VECTOR', pick(track.get(bound(renderer, "VelocityBinding", "Velocity"), (0, 0, 0))) * flip)
    elif kind == "Decal":
        # its box's half size (UE units) over the 1 m quad, turned as it projects (the engine's own
        # turn when the emitter sets none: straight down)
        _attribute(mesh, "mp_scale", 'FLOAT_VECTOR', pick(track.get(bound(renderer, "DecalSizeBinding", "DecalSize"), (50, 50, 50))) * 2.0 * scale)
        q = pick(track.get(bound(renderer, "DecalOrientationBinding", "DecalOrientation"), DECAL_DOWN)).astype(np.float32)
        _attribute(mesh, "mp_decal_fade", 'FLOAT', pick(track.get(bound(renderer, "DecalFadeBinding", "DecalFade"), (1,))))
    else:
        _attribute(mesh, "mp_scale", 'FLOAT_VECTOR', pick(track.get(bound(renderer, "ScaleBinding", "Scale"), (1, 1, 1))))
        q = pick(track.get(bound(renderer, "MeshOrientationBinding", "MeshOrientation"), (0, 0, 0, 1))).astype(np.float32)
    if kind in ("Decal", "Mesh"):
        length = np.linalg.norm(q, axis=1, keepdims=True)
        q = np.where(length > 1e-8, q / np.maximum(length, 1e-8), np.array([0, 0, 0, 1], np.float32))
        # UE's (x, y, z, w) to Blender's (w, x, y, z), Y flipped
        _attribute(mesh, "mp_rotation", 'QUATERNION', np.stack([q[:, 3], -q[:, 0], q[:, 1], -q[:, 2]], axis=1))
    # what the materials read
    _attribute(mesh, "mp_particle", 'FLOAT', np.ones(len(position), np.float32))
    color = ("DecalColorBinding", "Color") if kind == "Decal" else ("ColorBinding", "Color")
    _attribute(mesh, "mp_particle_color", 'FLOAT_COLOR', pick(track.get(bound(renderer, *color), (1, 1, 1, 1))))
    flags, valid = [], int(renderer.get("MaterialParamValidMask") or 0)     # four bits per parameter: the channels the emitter writes
    for i, (binding, usual) in enumerate((("DynamicMaterialBinding", "DynamicMaterialParameter"), ("DynamicMaterial1Binding", "DynamicMaterialParameter1"),
                                         ("DynamicMaterial2Binding", "DynamicMaterialParameter2"), ("DynamicMaterial3Binding", "DynamicMaterialParameter3"))):
        attribute = bound(renderer, binding, usual)
        flags.append(1.0 if track.has(attribute) and valid >> 4 * i & 15 else 0.0)
        if flags[-1]:
            _attribute(mesh, "mp_dynamic%d" % i, 'FLOAT_COLOR', pick(track.get(attribute, (1, 1, 1, 1))))
    _attribute(mesh, "mp_dynamic", 'FLOAT_COLOR', np.tile(np.array(flags, np.float32), (len(position), 1)))
    _attribute(mesh, "mp_subimage", 'FLOAT', pick(track.get(bound(renderer, "SubImageIndexBinding", "SubImageIndex"), (0,))))
    # a random number per particle (the materials' Particle Random: constant over its life)
    _attribute(mesh, "mp_random", 'FLOAT', pick(track.get(bound(renderer, "MaterialRandomBinding", "MaterialRandom"), (0,))))
    if kind != "Ribbon":
        # how far it is drawn toward the camera from its position (a glow in front of what it sits in)
        _attribute(mesh, "mp_camera_offset", 'FLOAT', pick(track.get(bound(renderer, "CameraOffsetBinding", "CameraOffset"), (0,))) * scale)
    _attribute(mesh, "mp_age", 'FLOAT', pick(track.get(bound(renderer, "NormalizedAgeBinding", "NormalizedAge"), (0,))))
    return mesh


def _keys(target, path, index, frames, values, loop):
    """An animation curve of a property over the replay's frames (each value held until the next);
    looped, it restarts after the last one."""
    ad = target.animation_data or target.animation_data_create()
    if ad.action is None:
        ad.action = bpy.data.actions.new("%s replay" % target.name)
    curve = ad.action.fcurve_ensure_for_datablock(target, path, index=index)
    frames, values = list(frames), list(values)
    if loop and frames:
        frames.append(frames[-1] + 1)     # back to the first value one frame on: one period of the whole replay
        values.append(values[0])
    curve.keyframe_points.add(len(frames))
    curve.keyframe_points.foreach_set("co", np.stack([np.asarray(frames, np.float32), np.asarray(values, np.float32)], axis=1).ravel())
    curve.keyframe_points.foreach_set("interpolation", np.full(len(frames), bpy.types.Keyframe.bl_rna.properties["interpolation"].enum_items["CONSTANT"].value, np.int32))
    curve.update()
    if loop:
        curve.modifiers.new('CYCLES')


def particle_lights(piece, track, renderer, keep, scale, start, loop, parent, root):
    """A light renderer's particles as point lights, one per particle alive at once (up to LIGHTS_MAX),
    keyed frame by frame: position, colour (Color, scaled by alpha if the renderer says so, plus
    ColorAdd) and reach (LightRadius x RadiusScale). The power matches what UE's light gives a
    surface: at a third of the way out for a light with an exponent falloff, anywhere for an inverse
    square one (UE's particle light colour is per cm², Blender's watts per m²). Returns the lights."""
    flip = np.array([1.0, -1.0, 1.0], np.float32)
    frame = track.frame if keep is None else track.frame[keep]
    pick = (lambda a: a) if keep is None else (lambda a: a[keep])
    if not len(frame):
        return []
    position = pick(track.get(bound(renderer, "PositionBinding", "Position"), (0, 0, 0))) * flip * scale
    color = pick(track.get(bound(renderer, "ColorBinding", "Color"), (1, 1, 1, 1))).astype(np.float64)
    rgb = color[:, :3] * (color[:, 3:4] if renderer.get("bAlphaScalesBrightness") else 1.0)
    add = renderer.get("ColorAdd") or {}
    rgb = np.maximum(rgb + np.array([add.get("X", 0.0), add.get("Y", 0.0), add.get("Z", 0.0)]), 0.0)
    radius = pick(track.get(bound(renderer, "RadiusBinding", "LightRadius"), (100,)))[:, 0] * float(renderer.get("RadiusScale", 1.0)) * scale
    exponent = pick(track.get(bound(renderer, "LightExponentBinding", "LightExponent"), (float(renderer.get("DefaultExponent", 1.0)),)))[:, 0]
    enabled = pick(track.get(bound(renderer, "LightRenderingEnabledBinding", "LightEnabled"), (1,)))[:, 0] != 0
    radius = np.maximum(radius, 1e-3)
    if renderer.get("bUseInverseSquaredFalloff", True):
        falloff = np.full(len(radius), 4.0 * np.pi / 1e4)      # C / d(cm)^2 = W / (4 pi d(m)^2)
    else:
        falloff = 4.0 * np.pi * (radius / 3.0) ** 2 * (8.0 / 9.0) ** np.maximum(exponent, 0.0)
    strength = rgb.max(axis=1)
    power = np.where(enabled, strength * falloff, 0.0)
    tint = rgb / np.maximum(strength, 1e-6)[:, None]
    # each frame's particles in order: the n-th alive particle drives the n-th light
    order = np.argsort(frame, kind="stable")
    first = np.searchsorted(frame[order], np.arange(len(track.frames)))
    counts = np.bincount(frame, minlength=len(track.frames))
    count = int(min(counts.max(), LIGHTS_MAX))
    made = []
    for slot in range(count):
        light = bpy.data.lights.new("%s %d" % (piece.name, slot + 1), 'POINT')
        light.shadow_soft_size = 0.05
        light.use_custom_distance = True
        light.diffuse_factor = float(renderer.get("DiffuseScale", 1.0))
        light.specular_factor = float(renderer.get("SpecularScale", 1.0))
        obj = bpy.data.objects.new(light.name, light)
        obj[effects.KEY] = "Particles"
        obj[KEY_ROOT] = root
        obj.parent = parent
        for collection in piece.users_collection:
            collection.objects.link(obj)
        at = [first[f] + slot if slot < counts[f] else -1 for f in range(len(track.frames))]
        rows = [order[i] if i >= 0 else -1 for i in at]
        frames = [start + f for f in range(len(track.frames))]
        for axis in range(3):
            _keys(obj, "location", axis, frames, [position[r, axis] if r >= 0 else 0.0 for r in rows], loop)
        _keys(light, "energy", 0, frames, [power[r] if r >= 0 else 0.0 for r in rows], loop)
        _keys(light, "cutoff_distance", 0, frames, [radius[r] if r >= 0 else 1.0 for r in rows], loop)
        for channel in range(3):
            _keys(light, "color", channel, frames, [tint[r, channel] if r >= 0 else 1.0 for r in rows], loop)
        made.append(obj)
    return made
