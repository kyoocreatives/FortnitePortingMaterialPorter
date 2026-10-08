"""A particle effect's CPU emitters, played in Blender.

The emitters' scripts are run over the scene's frame range (niagara.py) and each frame's result is
kept: a mesh of points per drawn piece holding every frame's particles, each point with its frame
and its particle's values. A geometry nodes modifier keeps the current frame's points and puts the
piece (the sprite's plane, the mesh renderer's mesh) on each: turned to the camera or along its
velocity as the renderer says, sized, and carrying the particle's colour and material values as
instance attributes, which the exact materials read.

The replay runs at the engine's 60 ticks a second or so (a whole number of ticks per frame). It
stops where the system completes; an effect that never completes fills the scene's frame range.

What the replay takes is kept with the effect (a text in the file), so it can be replayed over
another frame range (from the empty's Start Frame), with other user parameters (its User.*
properties), or on a character. An effect under an armature reads its bones and sockets frame by
frame (a contrail's hands and feet, a pickaxe's trail sockets), and one that moves (its own
animation, or its parent's) leaves its world-space particles where they were spawned, as a trail.
"""
import re
import base64
import json
import zlib

import bpy
import numpy as np

from . import effects, niagara

GROUP = "MP Effect Particles"
RIBBONS = "MP Effect Ribbons"
GROUP_VERSION = 9
# a decal's turn when the emitter sets none: the engine's FRotator(-90, 0, 90), projecting straight down (x, y, z, w)
DECAL_DOWN = (-0.5, 0.5, 0.5, 0.5)
# how a particle's piece is turned (the modifier's Turn), as the renderer says
TURN_OWN, TURN_CAMERA, TURN_CAMERA_VELOCITY, TURN_FACING, TURN_MESH_VELOCITY, TURN_MESH_CAMERA, TURN_FACING_ALIGNED = range(7)
FACINGS = "What a ribbon's width runs across: 0: the view (it faces the camera); 1: each particle's facing; " \
          "2: along each particle's side vector (a trail between two sockets)"
TURNS = "0: the particle's own rotation (a mesh); 1: a sprite facing the camera; 2: a sprite facing the camera, its length " \
        "along its velocity (or its own vector); 3: a sprite facing the particle's own direction; 4: a mesh, its X axis along the velocity; " \
        "5: a mesh, its X axis to the camera; 6: a sprite facing its own direction, its length along its own vector"
MOST_POINTS = 2_000_000     # over all frames, per effect
KEY_PROGRAM = "mp_effect_program"   # on an effect's empty: the text holding what its replay takes
KEY_SCALE = "mp_effect_scale"       # and the import scale (Blender units per UE unit)
KEY_ROOT = "mp_effect_root"         # on a particles object: its effect's empty
KEY_SOCKETS = "mp_effect_sockets"   # on a pickaxe trail's empty: the two sockets it runs between ("a,b")
# on an animation effect's empty: the frames after Start Frame it is played again on (one per notify;
# the first is 0), and per play how many frames a timed notify keeps it going (0: it plays out by itself)
KEY_REPEATS, KEY_LENGTHS = "mp_effect_repeats", "mp_effect_lengths"
KEY_START = "Start Frame"           # on an effect's empty: the scene frame its replay starts on (user-set)
KEY_LOOP = "Loop"                   # on an effect's empty: whether the replay restarts after its last frame (user-set)
LIGHTS_MAX = 32                     # max lights per light renderer (one per particle alive at once)


def store(root, exports, fields, scale, sockets=None):
    """Keep what the effect's replay takes with the effect (a text in the file), so it can be replayed.
    sockets: the skeleton's sockets ({lower-case name: its bone and place on it}), for an armature without them."""
    packed = base64.b64encode(zlib.compress(json.dumps({"Exports": exports, "Fields": fields or {}, "Sockets": sockets or {}}).encode("utf-8"))).decode("ascii")
    text = bpy.data.texts.new(root.name + " replay")
    text.use_fake_user = True
    text.write("\n".join(packed[i:i + 4000] for i in range(0, len(packed), 4000)))
    root[KEY_PROGRAM] = text.name
    root[KEY_SCALE] = float(scale)


def program(root):
    """What the effect's replay takes, as stored with it: (exports, fields, skeleton sockets), or None."""
    text = bpy.data.texts.get(str(root.get(KEY_PROGRAM) or ""))
    if text is None:
        return None
    data = json.loads(zlib.decompress(base64.b64decode(text.as_string().replace("\n", ""))).decode("utf-8"))
    return data["Exports"], data["Fields"], data.get("Sockets") or {}


def _ue(matrix, scale):
    """A Blender transform as UE's matrix: rows are the axes then the origin, Y flipped, UE units."""
    flip = np.diag([1.0, -1.0, 1.0, 1.0])
    m = flip @ np.array(matrix, np.float64) @ flip
    m[:3, 3] /= scale
    return m.T


def roots(objects):
    """The effects among the objects: each one's empty, found from the empty, a piece or its particles.
    With none, the effects on the objects (a pickaxe's own, a character's contrail)."""
    found = []
    for obj in objects:
        at = obj.get(KEY_ROOT) if obj.get(effects.KEY) == "Particles" else obj
        while at is not None and at.get(effects.KEY) != "System":
            at = at.parent
        if at is not None and at.get(KEY_PROGRAM) and at not in found:
            found.append(at)
    if not found:
        for obj in objects:
            found += [c for c in obj.children_recursive if c.get(effects.KEY) == "System" and c.get(KEY_PROGRAM) and c not in found]
    return found


def attach(root, rig):
    """Put an effect on an armature: at its origin, following it."""
    root.parent = rig
    root.parent_type = 'OBJECT'
    root.matrix_parent_inverse.identity()
    root.location = (0.0, 0.0, 0.0)
    root.rotation_euler = (0.0, 0.0, 0.0)


def rig_of(root):
    """The armature the effect is on (the nearest parent), or None."""
    parent = root.parent
    while parent is not None:
        if parent.type == 'ARMATURE':
            return parent
        parent = parent.parent
    return None


def holder_of(root):
    """What the effect reads bones and sockets from: its armature, else the mesh it is on (a static
    mesh's sockets), or None."""
    rig = rig_of(root)
    if rig is None and root.parent is not None and root.parent.type == 'MESH':
        return root.parent
    return rig


def _moves(root):
    """Whether anything could move the effect over the frames: a parent, or its own animation."""
    return root.parent is not None or root.animation_data is not None


def _animated(root):
    """Whether anything could move the effect or its armature's bones from frame to frame."""
    at = root
    while at is not None:
        data = at.animation_data
        if data is not None and (data.action is not None or len(data.nla_tracks) or len(data.drivers)):
            return True
        if len(at.constraints) or (at.type == 'ARMATURE' and any(len(b.constraints) for b in at.pose.bones)):
            return True
        at = at.parent
    return False


def _plain_world(obj):
    """An object's world matrix computed from its own transforms, or None where that wouldn't match the
    scene's (a parent on a bone or vertex, a constraint, an animation or a driver up the chain).
    matrix_world is stale for objects made since the last evaluation, and evaluating a scene of tens of
    thousands of objects once per placed effect costs most of a second each time."""
    chain = []
    at = obj
    while at is not None:
        data = at.animation_data
        animated = data is not None and (data.action is not None or len(data.nla_tracks) or len(data.drivers))
        if len(at.constraints) or animated or (at.parent is not None and at.parent_type != 'OBJECT'):
            return None
        chain.append(at)
        at = at.parent
    m = chain[-1].matrix_basis.copy()
    for at in reversed(chain[:-1]):
        m = m @ at.matrix_parent_inverse @ at.matrix_basis
    return m


class Stand:
    """Where the effect and its character stand, frame by frame: the effect empty's transform, the
    armature's, and the bones and sockets the effect's scripts read."""

    def __init__(self, scene, root, rig, reads, scale, sockets=None):
        self.scene, self.root, self.rig, self.scale = scene, root, rig, scale
        self.seen = {}      # frame -> its stand (an effect played several times asks for a frame again)
        self.animated = _animated(root)
        self.still = None if self.animated else False       # unmoving: one frame's stand serves them all
        self.bones = {}     # name read -> (pose bone, place on it for a socket the armature lacks, else None)
        if rig is not None:
            by_name = {b.name.lower(): b for b in rig.pose.bones} if rig.type == 'ARMATURE' else {}
            # each is read in the game's frame of its bone (a reoriented bone's isn't)
            for name in reads:
                socket = (sockets or {}).get(name)
                if name in by_name:
                    bone = by_name[name]
                    self.bones[name] = (bone, effects.ue_offset(bone.bone))
                elif socket is not None and str(socket.get("Bone")).lower() in by_name:
                    bone = by_name[str(socket["Bone"]).lower()]
                    self.bones[name] = (bone, effects.ue_offset(bone.bone) @ effects.socket_matrix(socket, scale))
                elif socket is not None and not socket.get("Bone"):     # a static mesh's socket: on the mesh itself
                    self.bones[name] = (None, effects.socket_matrix(socket, scale))

    def at(self, frame):
        """(owner matrix, character matrix, pose) on a scene frame, in UE terms."""
        if self.still:
            return self.still
        if frame in self.seen:
            return self.seen[frame]
        if self.still is None:
            self.scene.frame_set(frame)
        # a still effect with no bones to read: use its objects' own transforms, without evaluating the scene
        world = _plain_world(self.root) if self.still is False and self.rig is None and not self.bones else None
        if world is None:
            bpy.context.view_layer.update()
            world = self.root.matrix_world
        owner = _ue(world, self.scale)
        pose = {}
        for name, (bone, place) in self.bones.items():
            m = _ue(place if bone is None else bone.matrix @ place if place is not None else bone.matrix, self.scale)
            rows = m[:3, :3]
            pose[name] = (m[3, :3].copy(), niagara._quaternion(rows / np.maximum(np.linalg.norm(rows, axis=1, keepdims=True), 1e-9)))
        stand = (owner, _ue(self.rig.matrix_world, self.scale) if self.rig is not None else owner, pose)
        if self.still is False:
            self.still = stand
        self.seen[frame] = stand
        return stand


def _between(a, b, t):
    """The stand a fraction of the way between two frames' stands."""
    if t >= 1.0:
        return b
    pose = {}
    for name, (position, rotation) in b[2].items():
        p0, q0 = a[2].get(name, (position, rotation))
        q0 = -q0 if float(np.dot(q0, rotation)) < 0 else q0
        q = q0 + (rotation - q0) * t
        pose[name] = (p0 + (position - p0) * t, q / max(float(np.linalg.norm(q)), 1e-9))
    return a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, pose


def replay(system, fps, frames, stand=None, first=1, last=None, watch=()):
    """The system ticked through the frames: {emitter name: [(floats, ints) per frame]}. stand: where
    the effect and its character are on each scene frame (a Stand), for an effect that moves or sits
    on a character; without one it stays at its origin. last: the scene frame the game stops the
    effect on (it spawns no more from there). watch: system, emitter and user variables whose
    per-frame values are kept as system.history ({name: [floats per frame]})."""
    ticks = max(1, round(60.0 / fps))
    dt = 1.0 / (fps * ticks)
    props = system.props
    before = stand.at(first) if stand is not None else None
    if before is not None:
        system.place(*before)
        system.place(*before)       # a tick ago: the same
    warm = int(props.get("WarmupTickCount") or 0)
    for _ in range(min(warm, 600)):
        system.tick(float(props.get("WarmupTickDelta") or 1.0 / 15.0))
    tracks = {e.name: [] for e in system.emitters}
    system.history = {name: [] for name in watch}
    total = 0
    for frame in range(frames):
        now = stand.at(first + frame) if stand is not None else None
        if last is not None and first + frame >= last:
            system.deactivate()
        for tick in range(ticks):
            if now is not None:
                system.place(*_between(before, now, (tick + 1) / ticks))
            system.tick(dt)
        before = now
        for e in system.emitters:
            n = e.data.count
            tracks[e.name].append((e.data.floats[:, :n].copy(), e.data.ints[:, :n].copy()))
            total += n
        for name, values in system.history.items():
            values.append(_variable(system, name))
        if system.done or total > MOST_POINTS:
            break
    # frames after the last particle have nothing to play
    kept = max([i + 1 for frames in tracks.values() for i, f in enumerate(frames) if f[0].shape[1]] or [0])
    for frames in list(tracks.values()) + list(system.history.values()):
        del frames[kept:]
    return tracks


def _variable(system, name):
    """A system, emitter ("<emitter>.X") or user variable's current value as floats (a bool as 0 or 1);
    None if the system has no such variable."""
    v = system.read(name)
    if v is None and name in system.user.offsets:
        floats, _ = niagara.TYPES.get(system.user.offsets[name][1], (0, 1))
        raw = system.user.raw(name)
        v = np.frombuffer(raw, np.float32 if floats else np.int32)
    if v is None:
        return None
    v = np.asarray(v)
    if v.dtype.kind == "i":
        return np.where(v != 0, 1.0, 0.0) if len(v) == 1 and int(v[0]) in (0, -1, 1) else v.astype(np.float64)
    return v.astype(np.float64).copy()


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


def _bind(piece, carriers, bound, history, start, loop):
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


def _lights(piece, track, renderer, keep, scale, start, loop, parent, root):
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


def group():
    """The node group that plays a points mesh: the current frame's points with the piece on each."""
    g = bpy.data.node_groups.get(GROUP)
    if g is not None and g.get("mp_version") == GROUP_VERSION:
        return g
    g = bpy.data.node_groups.new(GROUP, 'GeometryNodeTree')
    g["mp_version"] = GROUP_VERSION
    g.is_modifier = True
    face = g.interface
    face.new_socket(name="Geometry", in_out='INPUT', socket_type='NodeSocketGeometry')
    face.new_socket(name="Geometry", in_out='OUTPUT', socket_type='NodeSocketGeometry')
    face.new_socket(name="Piece", in_out='INPUT', socket_type='NodeSocketObject', description="What each particle draws")
    face.new_socket(name="Start Frame", in_out='INPUT', socket_type='NodeSocketInt', description="The scene frame the effect starts on").default_value = 1
    face.new_socket(name="Frames", in_out='INPUT', socket_type='NodeSocketInt', description="How many frames were replayed").default_value = 1
    face.new_socket(name="Loop", in_out='INPUT', socket_type='NodeSocketBool', description="Start over after the last replayed frame")
    turn = face.new_socket(name="Turn", in_out='INPUT', socket_type='NodeSocketInt', description=TURNS)
    turn.min_value, turn.max_value = 0, 6
    face.new_socket(name="Piece Offset", in_out='INPUT', socket_type='NodeSocketVector',
                    description="Where the piece sits on its particle, in the particle's own space: a sprite's pivot, a mesh's pivot offset")
    face.new_socket(name="Piece Rotation", in_out='INPUT', socket_type='NodeSocketRotation', description="The renderer's own rotation of the piece")
    face.new_socket(name="Piece Scale", in_out='INPUT', socket_type='NodeSocketVector', description="The renderer's own scale of the piece").default_value = (1.0, 1.0, 1.0)
    face.new_socket(name="Depth Bias", in_out='INPUT', socket_type='NodeSocketFloat', description=DEPTH_BIAS)

    nodes, links = g.nodes, g.links
    column = [0]

    def node(kind, **settings):
        n = nodes.new(kind)
        n.location = (column[0], 0)
        column[0] += 190
        for k, v in settings.items():
            setattr(n, k, v)
        return n

    def attribute(name, kind):
        n = node("GeometryNodeInputNamedAttribute", data_type=kind)
        n.inputs["Name"].default_value = name
        return n.outputs[0]

    def math_(op, a, b):
        n = node("ShaderNodeMath", operation=op)
        for socket, v in zip(n.inputs, (a, b)):
            if hasattr(v, "is_linked"):
                links.new(v, socket)
            else:
                socket.default_value = v
        return n.outputs[0]

    inputs, outputs = node("NodeGroupInput"), nodes.new("NodeGroupOutput")
    # the replay frame the scene is on
    time = node("GeometryNodeInputSceneTime")
    index = math_('SUBTRACT', time.outputs["Frame"], inputs.outputs["Start Frame"])
    looped = math_('FLOORED_MODULO', index, inputs.outputs["Frames"])
    which = node("GeometryNodeSwitch", input_type='FLOAT')
    links.new(inputs.outputs["Loop"], which.inputs["Switch"])
    links.new(index, which.inputs["False"])
    links.new(looped, which.inputs["True"])
    other = node("FunctionNodeCompare", data_type='FLOAT', operation='NOT_EQUAL')
    links.new(attribute("mp_frame", 'INT'), other.inputs[0])
    links.new(which.outputs[0], other.inputs[1])
    other.inputs["Epsilon"].default_value = 0.5
    current = node("GeometryNodeDeleteGeometry", domain='POINT')
    links.new(inputs.outputs["Geometry"], current.inputs["Geometry"])
    links.new(other.outputs[0], current.inputs["Selection"])

    # the piece, with the renderer's rotation and scale
    piece = node("GeometryNodeObjectInfo", transform_space='ORIGINAL')
    links.new(inputs.outputs["Piece"], piece.inputs["Object"])
    placed = node("GeometryNodeTransform")
    links.new(piece.outputs["Geometry"], placed.inputs["Geometry"])
    links.new(inputs.outputs["Piece Offset"], placed.inputs["Translation"])
    links.new(inputs.outputs["Piece Rotation"], placed.inputs["Rotation"])
    links.new(inputs.outputs["Piece Scale"], placed.inputs["Scale"])
    # UE transforms a particle mesh's normals by its scale, not the inverse (a sphere flattened into a
    # card keeps a round one's falloff): these are the normals that result once the piece is scaled
    squared = node("ShaderNodeVectorMath", operation='MULTIPLY')
    links.new(inputs.outputs["Piece Scale"], squared.inputs[0])
    links.new(inputs.outputs["Piece Scale"], squared.inputs[1])
    bent = node("ShaderNodeVectorMath", operation='MULTIPLY')
    links.new(node("GeometryNodeInputNormal").outputs[0], bent.inputs[0])
    links.new(squared.outputs[0], bent.inputs[1])
    unit = node("ShaderNodeVectorMath", operation='NORMALIZE')
    links.new(bent.outputs[0], unit.inputs[0])
    shaded = node("GeometryNodeSetMeshNormal", mode='FREE', domain='CORNER')
    links.new(placed.outputs[0], shaded.inputs["Mesh"])
    links.new(unit.outputs[0], shaded.inputs["Custom Normal"])

    # how the piece is turned on its particle
    camera = node("GeometryNodeObjectInfo", transform_space='RELATIVE')
    links.new(node("GeometryNodeInputActiveCamera").outputs[0], camera.inputs["Object"])
    toward = node("ShaderNodeVectorMath", operation='SUBTRACT')
    links.new(camera.outputs["Location"], toward.inputs[0])
    links.new(node("GeometryNodeInputPosition").outputs[0], toward.inputs[1])
    velocity = attribute("mp_velocity", 'FLOAT_VECTOR')
    along = attribute("mp_align", 'FLOAT_VECTOR')      # what a sprite's length runs along: its velocity, or the emitter's vector
    own = attribute("mp_rotation", 'QUATERNION')
    spin = node("FunctionNodeAxisAngleToRotation")
    spin.inputs["Axis"].default_value = (0.0, 0.0, 1.0)
    links.new(attribute("mp_spin", 'FLOAT'), spin.inputs["Angle"])

    def spun(rotation):
        n = node("FunctionNodeRotateRotation", rotation_space='LOCAL')
        links.new(rotation, n.inputs[0])
        links.new(spin.outputs[0], n.inputs[1])
        return n.outputs[0]

    def aligned(axis, to, rotation=None, pivot='AUTO'):
        n = node("FunctionNodeAlignRotationToVector", axis=axis, pivot_axis=pivot)
        if rotation is not None:
            links.new(rotation, n.inputs["Rotation"])
        if hasattr(to, "is_linked"):
            links.new(to, n.inputs["Vector"])
        else:
            n.inputs["Vector"].default_value = to
        return n.outputs[0]

    def mesh_facing(direction):
        # mesh X along the direction, Z as far up as that allows, then the particle's own rotation
        n = node("FunctionNodeRotateRotation", rotation_space='LOCAL')
        links.new(aligned('Z', (0.0, 0.0, 1.0), aligned('X', direction), 'X'), n.inputs[0])
        links.new(own, n.inputs[1])
        return n.outputs[0]

    turns = [
        own,
        spun(camera.outputs["Rotation"]),                                   # a sprite: the camera's plane, spun
        aligned('Z', toward.outputs[0], aligned('Y', along), 'Y'),          # length along the velocity, face to the camera
        spun(aligned('Z', attribute("mp_facing", 'FLOAT_VECTOR'))),         # face along the particle's own direction
        mesh_facing(velocity),
        mesh_facing(toward.outputs[0]),
        aligned('Y', along, aligned('Z', attribute("mp_facing", 'FLOAT_VECTOR')), 'Z'),     # face along its own direction, length along its own vector
    ]
    rotation = node("GeometryNodeIndexSwitch", data_type='ROTATION')
    while len(rotation.index_switch_items) < len(turns):
        rotation.index_switch_items.new()
    links.new(inputs.outputs["Turn"], rotation.inputs["Index"])
    for i, turn in enumerate(turns):
        links.new(turn, rotation.inputs[i + 1])

    # each particle is drawn toward the camera by its camera offset
    nearer = node("ShaderNodeVectorMath", operation='NORMALIZE')
    links.new(toward.outputs[0], nearer.inputs[0])
    by = node("ShaderNodeVectorMath", operation='SCALE')
    links.new(nearer.outputs[0], by.inputs[0])
    links.new(attribute("mp_camera_offset", 'FLOAT'), by.inputs["Scale"])
    offset = node("GeometryNodeSetPosition")
    links.new(current.outputs[0], offset.inputs["Geometry"])
    links.new(by.outputs[0], offset.inputs["Offset"])
    instances = node("GeometryNodeInstanceOnPoints")
    links.new(offset.outputs[0], instances.inputs["Points"])
    links.new(shaded.outputs[0], instances.inputs["Instance"])
    links.new(rotation.outputs[0], instances.inputs["Rotation"])
    links.new(attribute("mp_scale", 'FLOAT_VECTOR'), instances.inputs["Scale"])
    # depth bias: each instance is scaled about the camera so it moves along its own view rays
    # (it looks the same, only nearer)
    gap = node("ShaderNodeVectorMath", operation='DISTANCE')
    links.new(node("GeometryNodeInputPosition").outputs[0], gap.inputs[0])
    links.new(camera.outputs["Location"], gap.inputs[1])
    nearer_by = math_('MAXIMUM', math_('SUBTRACT', 1.0, math_('DIVIDE', inputs.outputs["Depth Bias"], math_('MAXIMUM', gap.outputs["Value"], 0.01))), 0.05)
    biased = node("GeometryNodeScaleInstances")
    links.new(instances.outputs[0], biased.inputs["Instances"])
    links.new(nearer_by, biased.inputs["Scale"])
    links.new(camera.outputs["Location"], biased.inputs["Center"])
    biased.inputs["Local Space"].default_value = False
    outputs.location = (column[0], 0)
    links.new(biased.outputs[0], outputs.inputs[0])
    return g


def ribbons():
    """The node group that plays a ribbon's points mesh: the current frame's points strung into
    ribbons (one per ribbon ID, in link order), as wide as each particle says, facing the camera."""
    g = bpy.data.node_groups.get(RIBBONS)
    if g is not None and g.get("mp_version") == GROUP_VERSION:
        return g
    g = bpy.data.node_groups.new(RIBBONS, 'GeometryNodeTree')
    g["mp_version"] = GROUP_VERSION
    g.is_modifier = True
    face = g.interface
    face.new_socket(name="Geometry", in_out='INPUT', socket_type='NodeSocketGeometry')
    face.new_socket(name="Geometry", in_out='OUTPUT', socket_type='NodeSocketGeometry')
    face.new_socket(name="Material", in_out='INPUT', socket_type='NodeSocketMaterial')
    face.new_socket(name="Start Frame", in_out='INPUT', socket_type='NodeSocketInt', description="The scene frame the effect starts on").default_value = 1
    face.new_socket(name="Frames", in_out='INPUT', socket_type='NodeSocketInt', description="How many frames were replayed").default_value = 1
    face.new_socket(name="Loop", in_out='INPUT', socket_type='NodeSocketBool', description="Start over after the last replayed frame")
    facing = face.new_socket(name="Facing", in_out='INPUT', socket_type='NodeSocketInt', description=FACINGS)
    facing.min_value, facing.max_value = 0, 2
    shape = face.new_socket(name="Shape", in_out='INPUT', socket_type='NodeSocketInt',
                            description="What is swept along the ribbon: 0: a plane; 1: several planes turned about it; 2: a tube")
    shape.min_value, shape.max_value = 0, 2
    sides = face.new_socket(name="Sides", in_out='INPUT', socket_type='NodeSocketInt', description="How many planes (Shape 1), how many sides the tube has (Shape 2)")
    sides.min_value, sides.default_value = 1, 2
    face.new_socket(name="Depth Bias", in_out='INPUT', socket_type='NodeSocketFloat', description=DEPTH_BIAS)

    nodes, links = g.nodes, g.links
    column = [0]

    def node(kind, **settings):
        n = nodes.new(kind)
        n.location = (column[0], 0)
        column[0] += 190
        for k, v in settings.items():
            setattr(n, k, v)
        return n

    def attribute(name, kind):
        n = node("GeometryNodeInputNamedAttribute", data_type=kind)
        n.inputs["Name"].default_value = name
        return n.outputs[0]

    def math_(op, a, b):
        n = node("ShaderNodeMath", operation=op)
        links.new(a, n.inputs[0])
        links.new(b, n.inputs[1])
        return n.outputs[0]

    def vector(op, a, b=None):
        n = node("ShaderNodeVectorMath", operation=op)
        links.new(a, n.inputs[0])
        if b is not None:
            links.new(b, n.inputs[1])
        return n.outputs[0]

    inputs, outputs = node("NodeGroupInput"), nodes.new("NodeGroupOutput")
    time = node("GeometryNodeInputSceneTime")
    index = math_('SUBTRACT', time.outputs["Frame"], inputs.outputs["Start Frame"])
    looped = math_('FLOORED_MODULO', index, inputs.outputs["Frames"])
    which = node("GeometryNodeSwitch", input_type='FLOAT')
    links.new(inputs.outputs["Loop"], which.inputs["Switch"])
    links.new(index, which.inputs["False"])
    links.new(looped, which.inputs["True"])
    other = node("FunctionNodeCompare", data_type='FLOAT', operation='NOT_EQUAL')
    links.new(attribute("mp_frame", 'INT'), other.inputs[0])
    links.new(which.outputs[0], other.inputs[1])
    other.inputs["Epsilon"].default_value = 0.5
    current = node("GeometryNodeDeleteGeometry", domain='POINT')
    links.new(inputs.outputs["Geometry"], current.inputs["Geometry"])
    links.new(other.outputs[0], current.inputs["Selection"])
    cloud = node("GeometryNodeMeshToPoints")
    links.new(current.outputs[0], cloud.inputs[0])

    strung = node("GeometryNodePointsToCurves")
    links.new(cloud.outputs[0], strung.inputs["Points"])
    links.new(attribute("mp_ribbon", 'INT'), strung.inputs["Curve Group ID"])
    links.new(attribute("mp_order", 'FLOAT'), strung.inputs["Weight"])
    smooth = node("GeometryNodeCurveSplineType", spline_type='CATMULL_ROM')
    links.new(strung.outputs[0], smooth.inputs["Curve"])
    fine = node("GeometryNodeSetSplineResolution")
    links.new(smooth.outputs[0], fine.inputs[0])
    fine.inputs["Resolution"].default_value = 4
    wide = node("GeometryNodeSetCurveRadius")
    links.new(fine.outputs[0], wide.inputs["Curve"])
    width = attribute("mp_width", 'FLOAT')
    links.new(width, wide.inputs["Radius"])

    # the ribbon's width runs across what it faces: the camera, or the particles' facing
    camera = node("GeometryNodeObjectInfo", transform_space='RELATIVE')
    links.new(node("GeometryNodeInputActiveCamera").outputs[0], camera.inputs["Object"])
    toward = vector('SUBTRACT', camera.outputs["Location"], node("GeometryNodeInputPosition").outputs[0])
    tangent = node("GeometryNodeInputTangent").outputs[0]
    own = attribute("mp_facing", 'FLOAT_VECTOR')
    facing = node("GeometryNodeIndexSwitch", data_type='VECTOR')
    while len(facing.index_switch_items) < 3:
        facing.index_switch_items.new()
    links.new(inputs.outputs["Facing"], facing.inputs["Index"])
    links.new(vector('CROSS_PRODUCT', tangent, toward), facing.inputs[1])      # across the view
    links.new(vector('CROSS_PRODUCT', tangent, own), facing.inputs[2])         # across the particles' facing
    links.new(own, facing.inputs[3])                                           # along the particles' side vector
    side = vector('NORMALIZE', facing.outputs[0])
    turned = node("GeometryNodeSetCurveNormal")
    links.new(wide.outputs[0], turned.inputs["Curve"])
    if "Mode" in turned.inputs:
        turned.inputs["Mode"].default_value = 'Free'
    else:
        turned.mode = 'FREE'
    links.new(side, turned.inputs["Normal"])

    # the profile swept along it: a plane a unit wide, several planes turned about the ribbon, or a tube a unit across
    line = node("GeometryNodeCurvePrimitiveLine")
    line.inputs["Start"].default_value = (-0.5, 0.0, 0.0)
    line.inputs["End"].default_value = (0.5, 0.0, 0.0)
    spots = node("GeometryNodePoints")
    links.new(inputs.outputs["Sides"], spots.inputs["Count"])
    half = node("ShaderNodeValue")
    half.outputs[0].default_value = 3.141592653589793
    turn = node("ShaderNodeMath", operation='MULTIPLY')         # each plane: half a turn over the count, on from the last
    links.new(node("GeometryNodeInputIndex").outputs[0], turn.inputs[0])
    links.new(math_('DIVIDE', half.outputs[0], inputs.outputs["Sides"]), turn.inputs[1])
    about = node("ShaderNodeCombineXYZ")
    links.new(turn.outputs[0], about.inputs[2])
    planes = node("GeometryNodeInstanceOnPoints")
    links.new(spots.outputs[0], planes.inputs["Points"])
    links.new(line.outputs[0], planes.inputs["Instance"])
    links.new(about.outputs[0], planes.inputs["Rotation"])
    several = node("GeometryNodeRealizeInstances")
    links.new(planes.outputs[0], several.inputs[0])
    tube = node("GeometryNodeCurvePrimitiveCircle")
    tube.inputs["Radius"].default_value = 0.5
    least = node("ShaderNodeValue")
    least.outputs[0].default_value = 3.0
    links.new(math_('MAXIMUM', inputs.outputs["Sides"], least.outputs[0]), tube.inputs["Resolution"])
    profile = node("GeometryNodeIndexSwitch", data_type='GEOMETRY')
    while len(profile.index_switch_items) < 3:
        profile.index_switch_items.new()
    links.new(inputs.outputs["Shape"], profile.inputs["Index"])
    links.new(line.outputs[0], profile.inputs[1])
    links.new(several.outputs[0], profile.inputs[2])
    links.new(tube.outputs[0], profile.inputs[3])
    across = node("GeometryNodeCaptureAttribute", domain='POINT')
    across.capture_items.new('FLOAT', "V")
    links.new(profile.outputs[0], across.inputs[0])
    links.new(node("GeometryNodeSplineParameter").outputs["Factor"], across.inputs["V"])
    ribbon = node("GeometryNodeCurveToMesh")
    links.new(turned.outputs[0], ribbon.inputs["Curve"])
    links.new(across.outputs[0], ribbon.inputs["Profile Curve"])
    if "Scale" in ribbon.inputs:        # Blender 5: the profile's scale is the node's own input, not the curve's radius
        links.new(width, ribbon.inputs["Scale"])
    # two UV sets: U as each point carries it (the renderer's way of laying it along the ribbon),
    # V across, between the two edge values
    mapped = ribbon
    for name, carried in (("UV0", "mp_uv0"), ("UV1", "mp_uv1")):
        parts = node("ShaderNodeSeparateXYZ")
        links.new(attribute(carried, 'FLOAT_VECTOR'), parts.inputs[0])
        v = node("ShaderNodeMix", data_type='FLOAT')
        links.new(across.outputs["V"], v.inputs[0])
        links.new(parts.outputs[1], v.inputs[2])
        links.new(parts.outputs[2], v.inputs[3])
        uv = node("ShaderNodeCombineXYZ")
        links.new(parts.outputs[0], uv.inputs[0])
        links.new(v.outputs[0], uv.inputs[1])
        store = node("GeometryNodeStoreNamedAttribute", data_type='FLOAT2', domain='CORNER')
        store.inputs["Name"].default_value = name
        links.new(mapped.outputs[0], store.inputs[0])
        links.new(uv.outputs[0], store.inputs["Value"])
        mapped = store
    material = node("GeometryNodeSetMaterial")
    links.new(mapped.outputs[0], material.inputs[0])
    links.new(inputs.outputs["Material"], material.inputs["Material"])
    # depth bias: each vertex moves nearer along its own view ray
    eye = node("GeometryNodeObjectInfo", transform_space='RELATIVE')
    links.new(node("GeometryNodeInputActiveCamera").outputs[0], eye.inputs["Object"])
    to_eye = vector('NORMALIZE', vector('SUBTRACT', eye.outputs["Location"], node("GeometryNodeInputPosition").outputs[0]))
    step = node("ShaderNodeVectorMath", operation='SCALE')
    links.new(to_eye, step.inputs[0])
    links.new(inputs.outputs["Depth Bias"], step.inputs["Scale"])
    biased = node("GeometryNodeSetPosition")
    links.new(material.outputs[0], biased.inputs["Geometry"])
    links.new(step.outputs[0], biased.inputs["Offset"])
    outputs.location = (column[0], 0)
    links.new(biased.outputs[0], outputs.inputs[0])
    return g


def _set(modifier, tree, name, value):
    identifier = next(i.identifier for i in tree.interface.items_tree if i.item_type == 'SOCKET' and i.in_out == 'INPUT' and i.name == name)
    getattr(modifier.properties.inputs, identifier).value = value


DEPTH_BIAS = ("Metres the particles are drawn nearer the camera, along their view rays (they look the same). "
              "Under Cycles, a renderer drawn after others: UE and EEVEE draw blended layers in order, Cycles by depth")
DRAW_STEP = 0.03        # m: under Cycles each renderer is drawn this much nearer than the one before


def bias_by_order(obj, modifier, tree, scene):
    """The player's Depth Bias: draw order x DRAW_STEP while the scene renders with Cycles (a driver on
    the scene's ["mp_fx_cycles"], which the plugin maintains), 0 under EEVEE."""
    identifier = next((i.identifier for i in tree.interface.items_tree if i.item_type == 'SOCKET' and i.in_out == 'INPUT'
                       and i.name == "Depth Bias"), None)
    order = int(obj.get("mp_draw_order", 0))
    if identifier is None or order == 0:
        return
    socket = getattr(modifier.properties.inputs, identifier)
    fc = socket.driver_add("value")
    fc.keyframe_points.clear()
    for m in list(fc.modifiers):
        fc.modifiers.remove(m)
    d = fc.driver
    d.type = 'SCRIPTED'
    var = d.variables[0] if len(d.variables) else d.variables.new()
    var.name = "cycles"
    var.type = 'SINGLE_PROP'
    var.targets[0].id_type = 'SCENE'
    var.targets[0].id = scene
    var.targets[0].data_path = '["%s"]' % effects.FX_CYCLES
    d.expression = "cycles * %r" % round(order * DRAW_STEP, 4)


def _camera(scene, root, scale, world):
    """The scene camera as a script sees it: position, forward, up and right in UE axes and units,
    in world space or the effect's own space. None without a camera."""
    if scene.camera is None:
        return None
    camera, there = _plain_world(scene.camera), _plain_world(root)
    if camera is None or there is None:
        bpy.context.view_layer.update()
        camera, there = scene.camera.matrix_world, root.matrix_world
    m = camera if world else there.inverted() @ camera
    flip = lambda v: (v.x, -v.y, v.z)
    position = tuple(c / scale for c in flip(m.translation))
    return (position, flip(-m.col[2].xyz.normalized()), flip(m.col[1].xyz.normalized()), flip(m.col[0].xyz.normalized()))


def clear(root):
    """Remove an effect's played particles and show its pieces again, as imported."""
    for obj in [o for o in bpy.data.objects if o.get(effects.KEY) == "Particles" and o.get(KEY_ROOT) == root]:
        data = obj.data
        actions = [a.action for a in (obj.animation_data, getattr(data, "animation_data", None)) if a is not None and a.action is not None]
        bpy.data.objects.remove(obj, do_unlink=True)
        if data is not None and not data.users:
            (bpy.data.lights if isinstance(data, bpy.types.Light) else bpy.data.meshes).remove(data)
        for action in actions:
            if not action.users:
                bpy.data.actions.remove(action)
    for node in root.children:
        for piece in node.children:
            if piece.get(effects.KEY) in ("Sprite", "Mesh", "Ribbon", "Decal", "Light"):
                piece.hide_render = piece.hide_viewport = bool(piece.get(effects.KEY_SKIP))


def _enabled(system, emitter, renderer):
    """Whether a renderer draws: its Renderer Enabled binding's value (a system, emitter or user bool)
    as the replay left it. A renderer bound to nothing draws."""
    binding = renderer.get("RendererEnabledBinding") or {}
    name = str(binding.get("DataSetName") or (binding.get("ParamMapVariable") or {}).get("Name") or "")
    if not name or name == "None":
        return True
    if name.startswith("Emitter."):
        name = emitter.name + name[len("Emitter"):]
    value = system.read(name)
    if value is None and name in system.user.offsets:
        value = np.frombuffer(system.user.raw(name)[:4], np.int32)
    if value is None or not len(value):
        return True
    return int(np.asarray(value).view(np.int32)[0]) != 0


# user parameters the game sets in its front end (lobby, locker); on for an import
FRONT_END = {"user.bisfrontend", "user.bisfrontendpreview"}


def _user(root, system):
    """Put the system's user parameters and the parameter collection values it reads (the time of day)
    on the effect's empty as properties (first made from the asset's own values), and tell the
    system about them."""
    for name, kind, value in system.users():
        if name not in root:
            # shown as in the lobby and locker (as in the shop and locker pictures); some effects
            # play only there (Eternal Wanderer's hair globs)
            if name.lower() in FRONT_END:
                value = True
            root[name] = value
    # UE's names are case-insensitive (a glider's bisFullyDeployed is the bIsFullyDeployed the game sets)
    spelled = {n.lower(): n for n in list(system.user.offsets) + list(system.shared.offsets)}
    for name in [k for k in root.keys() if k.startswith(("User.", "NPC."))]:
        own = spelled.get(name.lower())
        if own is None:     # set by the export but not taken by this system
            del root[name]
            continue
        if own != name:
            root[own] = root[name]
            del root[name]
            name = own
        value = root[name]
        system.set_user(name, list(value) if hasattr(value, "__len__") else value)


def _together(runs, names):
    """Several plays of an effect as one: each frame holds all plays' particles."""
    length = max([offset + len(track) for offset, tracks in runs for track in tracks.values()] or [0])
    merged = {}
    for name in names:
        frames = []
        blank = next((t[name][0] for _, t in runs if t.get(name)), None)
        for frame in range(length if blank is not None else 0):
            parts = [(i, tracks[name][frame - offset]) for i, (offset, tracks) in enumerate(runs)
                     if name in tracks and 0 <= frame - offset < len(tracks[name])]
            parts = [(i, p) for i, p in parts if p[0].shape[1]]
            if not parts:
                frames.append((blank[0][:, :0], blank[1][:, :0], np.zeros(0, np.int32)))
                continue
            frames.append((np.concatenate([p[0] for _, p in parts], axis=1), np.concatenate([p[1] for _, p in parts], axis=1),
                           np.concatenate([np.full(p[0].shape[1], i, np.int32) for i, p in parts])))
        merged[name] = frames
    return merged


def _title(root):
    """An effect as the log names it: a pickaxe's own, with what it is."""
    role = root.get(effects.KEY_ROLE)
    return "%s (%s)" % (root.name, role) if role else root.name


def play(root):
    """Replay the effect under the root (emitter empties and their pieces) over the scene's frame
    range and make its pieces play. An effect under an armature reads that character's bones and
    sockets; one that moves leaves its world-space particles where they were spawned. Yields
    messages for the user."""
    stored = program(root)
    if stored is None:
        yield "%s: nothing to replay it from" % root.name
        return
    exports, fields, table = stored
    scale = float(root.get(KEY_SCALE, 0.01))
    drawn_order = {}        # per piece (its layers: Fire, Fire001...): the players in creation order
    scene = bpy.context.scene
    clear(root)
    sockets = [s for s in str(root.get(KEY_SOCKETS) or "").split(",") if s]
    system = niagara.System(exports, fields=fields, sockets=sockets)
    _user(root, system)
    rig = holder_of(root)
    fps = scene.render.fps / scene.render.fps_base
    # scene frame the effect starts on: the range's first, unless its Start Frame property says otherwise
    if KEY_START not in root:
        root[KEY_START] = scene.frame_start
    start = int(root[KEY_START])
    frames = max(1, scene.frame_end - start + 1)
    # loops, except one timed by an animation or played on a swing or an event
    if KEY_LOOP not in root:
        root[KEY_LOOP] = not root.get(KEY_REPEATS) and root.get(effects.KEY_ROLE) not in ("trail", "swing", "event", "impact")
    loop = bool(root[KEY_LOOP])
    stand = Stand(scene, root, rig, system.reads, scale, table) if _moves(root) else None
    camera = _camera(scene, root, scale, stand is not None)
    if camera is not None:
        system.camera = camera
    now = scene.frame_current
    # each play (one per animation notify, else the single one): its own run of the system, from its frame
    repeats = [int(x) for x in root.get(KEY_REPEATS) or [0]]
    lengths = [int(x) for x in root.get(KEY_LENGTHS) or []]
    # variables the renderers' materials are bound to, kept over the (first) replay
    watch = set()
    for node in root.children:
        emitter = next((e for e in system.emitters if e.name == node.get(effects.KEY_EMITTER)), None)
        for piece in node.children if emitter is not None else ():
            renderer = next((e["props"] for e in exports if e["name"] == piece.get(effects.KEY_RENDERER) and e["outer"] == emitter.export["name"]), None)
            watch.update(name for _, name, _ in bindings(renderer or {}, emitter))
    runs = []
    try:
        for i, offset in enumerate(repeats):
            if offset >= frames:
                break
            one = system
            if i:
                one = niagara.System(exports, fields=fields, seed=1 + i, sockets=sockets)
                _user(root, one)
                one.camera = system.camera
            last = start + offset + lengths[i] if i < len(lengths) and lengths[i] > 0 else None
            runs.append((offset, replay(one, fps, frames - offset, stand, start + offset, last, watch if i == 0 else ())))
    finally:
        if stand is not None and stand.animated:
            scene.frame_set(now)
    tracks = runs[0][1] if len(runs) == 1 else _together(runs, [e.name for e in system.emitters])
    emitters = {e.name: e for e in system.emitters}
    played, most, length = [], 0, 0
    bound_params, clear_ones = set(), []
    for node in root.children:
        emitter = emitters.get(node.get(effects.KEY_EMITTER))
        if emitter is None or not tracks.get(emitter.name):
            continue
        track = Track(emitter, tracks[emitter.name])
        if not track.total:
            continue
        if track.has("Color") and float(track.get("Color", (1, 1, 1, 1))[:, 3].max()) <= 1e-4:
            clear_ones.append(emitter.name)     # drawn, but see-through all along (the game raises the alpha)
        drawn = 0
        for piece in list(node.children):
            kind = piece.get(effects.KEY)
            renderer = next((e["props"] for e in exports if e["name"] == piece.get(effects.KEY_RENDERER) and e["outer"] == emitter.export["name"]), None)
            if kind not in ("Sprite", "Mesh", "Ribbon", "Decal", "Light") or renderer is None or piece.type not in ('MESH', 'LIGHT') or piece.get(effects.KEY_SKIP):
                continue
            if not _enabled(system, emitter, renderer):
                # a renderer the system switches off (a variant's, e.g. System.IsGold): hide its piece
                piece.hide_render = piece.hide_viewport = True
                continue
            keep = None
            if kind == "Mesh" and track.has(bound(renderer, "MeshIndexBinding", "MeshIndex")):
                keep = track.get(bound(renderer, "MeshIndexBinding", "MeshIndex"), (0,))[:, 0] == int(piece.get("mp_mesh_index", 0))
            elif kind == "Mesh" and int(piece.get("mp_mesh_index", 0)) > 0:
                continue        # without a mesh index every particle draws the first mesh
            # an emitter with several renderers says which draws each particle: the particle's
            # visibility tag is the renderer's (without the attribute every renderer draws it)
            tag = bound(renderer, "RendererVisibilityTagBinding", "VisibilityTag")
            if track.has(tag):
                mine = track.get(tag, (0,))[:, 0].astype(np.int64) == int(renderer.get("RendererVisibility", 0))
                keep = mine if keep is None else keep & mine
            if keep is not None and not keep.any():
                piece.hide_render = piece.hide_viewport = True      # none of this replay's particles belong to it
                continue
            if kind == "Light":
                made = _lights(piece, track, renderer, keep, scale, start, loop, node if stand is None or emitter.local else None, root)
                piece.hide_render = piece.hide_viewport = True
                drawn += bool(made)
                continue
            tree = ribbons() if kind == "Ribbon" else group()
            data = points(piece.name + " particles", track, renderer, kind, scale, keep)
            obj = bpy.data.objects.new(piece.name + " particles", data)
            # a moving effect's world-space particles stay where spawned, not under the effect
            if stand is None or emitter.local:
                obj.parent = node
            obj[effects.KEY] = "Particles"
            obj[KEY_ROOT] = root
            obj.visible_shadow = False
            # layers of one piece (a mesh's shells, a sprite's copies) are drawn in renderer order
            layer = re.sub(r"[\d.]+", "", piece.name)
            obj["mp_draw_order"] = drawn_order.get(layer, 0)
            drawn_order[layer] = obj["mp_draw_order"] + 1
            for collection in piece.users_collection:
                collection.objects.link(obj)
            modifier = obj.modifiers.new("Particles", 'NODES')
            modifier.node_group = tree
            _set(modifier, tree, "Start Frame", start)
            _set(modifier, tree, "Frames", len(track.frames))
            _set(modifier, tree, "Loop", loop)
            bias_by_order(obj, modifier, tree, scene)
            tied = bindings(renderer, emitter)
            if tied:
                bound_params.update(_bind(piece, (piece, obj), tied, system.history, start, loop))
            # the drawn piece, when selected in the viewport, shows the piece's materials (the same ones:
            # an edit there changes what the particles draw; the points themselves draw nothing)
            for slot in piece.material_slots:
                data.materials.append(slot.material)
            if kind == "Ribbon":
                _set(modifier, tree, "Material", piece.material_slots[0].material if piece.material_slots else None)
                facing = str(renderer.get("FacingMode"))
                _set(modifier, tree, "Facing", 2 if "CustomSideVector" in facing else 1 if "Custom" in facing else 0)
                shape = str(renderer.get("Shape"))
                _set(modifier, tree, "Shape", 1 if "MultiPlane" in shape else 2 if "Tube" in shape else 0)
                _set(modifier, tree, "Sides", int(renderer.get("TubeSubdivisions", 3)) if "Tube" in shape else int(renderer.get("MultiPlaneCount", 2)))
                piece.hide_render = True
                piece.hide_viewport = True
                drawn += 1
                continue
            _set(modifier, tree, "Piece", piece)
            if kind == "Sprite":
                lengthwise = any(x in str(renderer.get("Alignment")) for x in ("VelocityAligned", "CustomAlignment"))
                turn = (TURN_FACING_ALIGNED if lengthwise else TURN_FACING) if "CustomFacing" in str(renderer.get("FacingMode")) else \
                    TURN_CAMERA_VELOCITY if lengthwise else TURN_CAMERA
                # the pivot (UV space: 0.5, 0.5 is the middle; V runs down) is at the particle
                pivot = renderer.get("PivotInUVSpace") or {}
                _set(modifier, tree, "Piece Offset", (0.5 - float(pivot.get("X", 0.5)), float(pivot.get("Y", 0.5)) - 0.5, 0.0))
                for constraint in list(piece.constraints):      # the still piece's turn to the camera is the modifier's now
                    piece.constraints.remove(constraint)
            else:
                facing = str(renderer.get("FacingMode"))
                turn = TURN_MESH_VELOCITY if "Velocity" in facing else TURN_MESH_CAMERA if "Camera" in facing else TURN_OWN
                _set(modifier, tree, "Piece Rotation", piece.rotation_euler)
                _set(modifier, tree, "Piece Scale", piece.scale)
                # the renderer's pivot offset for this mesh (UE units and axes, in mesh space)
                listed = renderer.get("Meshes") or []
                at = int(piece.get("mp_mesh_index", 0))
                offset = (listed[at].get("PivotOffset") if at < len(listed) else None) or {}
                _set(modifier, tree, "Piece Offset", (float(offset.get("X", 0.0)) * scale, -float(offset.get("Y", 0.0)) * scale, float(offset.get("Z", 0.0)) * scale))
            _set(modifier, tree, "Turn", turn)
            # the piece itself, which the particles draw, is hidden
            piece.hide_render = True
            piece.hide_viewport = True
            drawn += 1
        if drawn:
            node.location = (0.0, 0.0, 0.0)     # played where the effect is, not in the row of pieces
            played.append(emitter.name)
            most = max(most, int(track.counts.max()))
            length = max(length, len(track.frames))
    # an effect on something (a character's contrail, a pickaxe's own): hide what isn't played
    worn = rig is not None or bool(root.get(effects.KEY_ROLE))
    if played or worn:
        # emitters left as pieces: in a row beside the effect, 2 m apart
        left = [node for node in root.children if node.get(effects.KEY_EMITTER) and node.get(effects.KEY_EMITTER) not in played]
        for at, node in enumerate(left):
            node.location = (0.0, 0.0, 0.0) if worn else (0.0, -200.0 * (at + 1) * scale, 0.0)
            for piece in node.children if worn else ():
                piece.hide_render = piece.hide_viewport = True
    if played:
        yield "%s: %s played %sover %d frames from frame %d (%d particles at most)%s" % (
            _title(root), ", ".join(played), "%d times " % len(runs) if len(runs) > 1 else "", length, start, most,
            ", on %s" % rig.name if rig is not None else "")
        if root.get(effects.KEY_ROLE) in ("trail", "swing") and stand is not None and stand.still:
            yield "%s: a %s shows on what moves: animate it, set the effect's Start Frame where it starts, then Replay Effect" % (
                _title(root), root[effects.KEY_ROLE])
    if bound_params:
        yield "%s: material parameters from the effect's values: %s" % (root.name, ", ".join(sorted(bound_params)[:8]))
    if clear_ones:
        # their colour alpha stays 0 (Salvador's flames wait on User.Dissolve Progress), so user
        # parameters at 0 are the likely switch
        zero = [name for name, kind, value in system.users() if name.startswith("User.") and kind in ("NiagaraFloat", "NiagaraBool", "NiagaraInt32")
                and not (value if not hasattr(value, "__len__") else any(value))]
        yield "%s: %s drawn see-through all along (their colour's alpha 0: waiting on something the game sets%s)" % (
            root.name, ", ".join(clear_ones[:6]), ": its %s at 0 - set on the effect's empty, then Replay Effect" % ", ".join(zero[:5]) if zero else "")
    approximate = [name for name in played if name in system.approximate and name not in system.gpu]
    if approximate:
        yield "%s: %s: stateless emitters, played from their settings (the engine's random draws apart)" % (root.name, ", ".join(approximate))
    gpu = [name for name in played if name in system.gpu]
    if gpu:
        guessed = [e.name for e in system.emitters if e.name in gpu and getattr(e, "guessed", False)]
        yield "%s: %s: GPU emitters, approximated (their counts, curves and materials the asset's; their particles held still around them, as where they go isn't kept%s)" % (
            root.name, ", ".join(gpu), "; %s spawned at a stand-in rate: what makes the game spawn it isn't in the replay" % ", ".join(guessed) if guessed else "")
    idle = [e.name for e in system.emitters if e.name not in played and not sum(f[0].shape[1] for f in tracks.get(e.name) or [])]
    if idle:
        # user parameters the game sets that are still off here (a burst count, a switch) are the likely wait
        off = [name for name, kind, value in system.users() if name.startswith("User.") and not (value if not hasattr(value, "__len__") else any(value))]
        yield "%s: %s spawned nothing in the replay (waiting on something the game sets%s, or on an emitter left out): %s" % (
            root.name, ", ".join(idle), ": its %s at 0 - set on the effect's empty, then Replay Effect" % ", ".join(off[:4]) if off else "",
            "hidden" if worn else "left as pieces")
    if system.reads and rig is None:
        yield "%s: reads a character's bones or sockets (%s): with none, each sits at the effect's origin. Select the effect and an armature, then Replay Effect" % (
            root.name, ", ".join(sorted(system.reads)[:6]))
    elif rig is not None and system.unresolved():
        yield "%s: %s has no bone or socket named %s: each sits at its origin" % (root.name, rig.name, ", ".join(system.unresolved()[:8]))
    if system.meshless:
        yield "%s: samples a static mesh's surface or sockets (in the game, the mesh it is on): with none here, as the engine without one, from the effect's origin" % root.name
    left = ["%s (%s)" % s for s in system.skipped]
    if left:
        yield "%s: %s: %s" % (root.name, "not played, their pieces hidden" if worn else "left as pieces", ", ".join(left))
