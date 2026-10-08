"""What a replay keeps with the effect, where it sits, and the replay itself."""

import base64
import json
import zlib
import bpy
import numpy as np

from .. import effects, niagara


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


def can_move(root):
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


def plain_world(obj):
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
        world = plain_world(self.root) if self.still is False and self.rig is None and not self.bones else None
        if world is None:
            bpy.context.view_layer.update()
            world = self.root.matrix_world
        owner = _ue(world, self.scale)
        pose = {}
        for name, (bone, place) in self.bones.items():
            m = _ue(place if bone is None else bone.matrix @ place if place is not None else bone.matrix, self.scale)
            rows = m[:3, :3]
            pose[name] = (m[3, :3].copy(), niagara.quaternion(rows / np.maximum(np.linalg.norm(rows, axis=1, keepdims=True), 1e-9)))
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


DEPTH_BIAS = ("Metres the particles are drawn nearer the camera, along their view rays (they look the same). "
              "Under Cycles, a renderer drawn after others: UE and EEVEE draw blended layers in order, Cycles by depth")
