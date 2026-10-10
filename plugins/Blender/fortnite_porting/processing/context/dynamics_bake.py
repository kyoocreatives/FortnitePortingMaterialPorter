"""Bake Fortnite's AnimDynamics over an armature's animation: sample it once per frame in the game's numbers, tick
the engine's solver between frames on interpolated locals, key the simulated bones into a Dynamics NLA track."""
import json
import math
import time

import bpy
import numpy as np
from mathutils import Matrix, Quaternion

from ...logger import Log
from .dynamics_solver import Sim, compose, relative

KEY = "fpmp_dynamics"
TRACK = "Dynamics"
TWIN = "DYN_"
BAKED = "DYN bake"
GROUP = "Dynamics"
PREROLL = 1.0       # seconds held on the first pose: in game the character was already idling when the emote began
FLIP = Matrix(((1.0, 0.0, 0.0), (0.0, -1.0, 0.0), (0.0, 0.0, 1.0)))     # the game's axes vs Blender's


def _track(obj):
    ad = obj.animation_data
    return next((t for t in ad.nla_tracks if t.name == TRACK), None) if ad else None


def _curves(action):
    return [fc for layer in action.layers for strip in layer.strips for bag in strip.channelbags for fc in bag.fcurves]


def is_on(obj):
    t = _track(obj)
    return t is not None and not t.mute


def set_on(obj, on):
    t = _track(obj)
    if t is not None:
        t.mute = not on
    for pb in obj.pose.bones:
        c = pb.constraints.get(BAKED)
        if c is not None:
            c.mute = not on


def _twins(obj, names):
    """Each simulated bone takes its baked pose from a hidden twin through its last constraint: in game the simulation
    overrides whatever the Control Rig gave the bone, so it has to come after the bone's own constraints."""
    missing = [n for n in names if TWIN + n not in obj.data.bones]
    if missing:
        layer = bpy.context.view_layer
        active, mode = layer.objects.active, obj.mode
        layer.objects.active = obj
        bpy.ops.object.mode_set(mode='EDIT')
        edit = obj.data.edit_bones
        for n in missing:
            t = edit.new(TWIN + n)
            t.head, t.tail, t.roll = edit[n].head.copy(), edit[n].tail.copy(), edit[n].roll
            t.parent, t.use_deform = edit[n].parent, False
        bpy.ops.object.mode_set(mode=mode if mode in ('POSE', 'OBJECT') else 'OBJECT')
        layer.objects.active = active
        group = obj.data.collections.get(GROUP) or obj.data.collections.new(GROUP)
        group.is_visible = False
        for n in missing:
            bone = obj.data.bones[TWIN + n]
            for c in list(bone.collections):
                c.unassign(bone)
            group.assign(bone)
    for n in names:
        pb = obj.pose.bones[n]
        c = pb.constraints.get(BAKED)
        if c is None:
            c = pb.constraints.new('COPY_TRANSFORMS')
            c.name, c.target, c.subtarget = BAKED, obj, TWIN + n
        last = len(pb.constraints) - 1
        if pb.constraints.find(BAKED) != last:
            pb.constraints.move(pb.constraints.find(BAKED), last)


def frame_range(obj):
    """The animation's frames: its NLA strips' (one pass of a looped one; the Dynamics layer aside) and its action's;
    None without either."""
    ad = obj.animation_data
    if ad is None:
        return None
    spans = [(s.frame_start, s.frame_start + (s.action_frame_end - s.action_frame_start) * s.scale)
             for t in ad.nla_tracks if t.name != TRACK and not t.mute for s in t.strips]
    if ad.action is not None:
        spans.append(tuple(ad.action.frame_range))
    if not spans:
        return None
    return int(math.floor(min(a for a, _ in spans))), int(math.ceil(max(b for _, b in spans)))


def _to_game(m, offset, unit):
    """A pose bone's armature matrix -> its game transform (rotation, position in cm)."""
    g = m @ offset
    return (FLIP @ g.to_3x3().normalized() @ FLIP).to_quaternion(), FLIP @ g.translation / unit


def _from_game(t, offset_inv, unit):
    m = (FLIP @ t[0].to_matrix() @ FLIP).to_4x4()
    m.translation = FLIP @ t[1] * unit
    return m @ offset_inv


def _blend(a, b, u):
    """Between two keys as the engine interpolates them: normalized lerp on the short way, linear position."""
    qb = -b[0] if a[0].dot(b[0]) < 0.0 else b[0]
    return Quaternion([x + (y - x) * u for x, y in zip(a[0], qb)]).normalized(), a[1].lerp(b[1], u)


def _push_down(obj):
    """A plain action goes down into the NLA so the Dynamics layer can sit above it."""
    ad = obj.animation_data
    if ad is None or ad.action is None:
        return
    action, slot = ad.action, ad.action_slot
    track = ad.nla_tracks.new()
    track.name = action.name
    track.strips.new(action.name, int(action.frame_range[0]), action).action_slot = slot     # as Blender's Push Down
    ad.action = None


def _drop(obj):
    old = _track(obj)
    if old is None:
        return
    actions = [s.action for s in old.strips if s.action]
    obj.animation_data.nla_tracks.remove(old)
    for a in actions:
        if not a.users:
            bpy.data.actions.remove(a)


def _node(n, parent_of, bones, unit):
    """The replayer for one stored node, by its kind; None when the armature lacks what it needs."""
    from ...material_porter.effects import ue_offset
    from .bone_controls import BoneControl
    from .rigid_solver import RigidSim
    kind = n.get("kind", "anim_dynamics")
    if kind == "anim_dynamics":
        return Sim(n, parent_of) if n["bone"] in bones else None
    if kind == "rigid_body":
        return RigidSim(n, parent_of)
    if n["bone"] not in bones:
        return None
    named = [n["bone"]] + [s["target"] for s in n.get("setups", []) if s["target"] in bones]
    rest = {b: _to_game(bones[b].matrix_local, ue_offset(bones[b]), unit) for b in named}
    return BoneControl(n, parent_of, rest)


def bake(obj, rate=60):
    """Simulate the armature's dynamics nodes over its animation; (bones keyed, frames) or (0, 0)."""
    from ...material_porter.effects import ue_offset
    raw = obj.data.get(KEY)
    if not raw or frame_range(obj) is None:
        return 0, 0
    t0 = time.time()
    stored = json.loads(raw)
    bones = obj.data.bones
    parent_of = {b.name: b.parent.name if b.parent else None for b in bones}
    unit = stored.get("scale", 0.01)
    sims = [s for s in (_node(n, parent_of, bones, unit) for n in stored["nodes"]) if s is not None]
    sims = [s for s in sims if s.outputs() and all(name in bones for name in s.bones())]
    if not sims:
        return 0, 0
    _drop(obj)      # never sample the last bake as input
    set_on(obj, False)
    start, end = frame_range(obj)
    root = next(b.name for b in bones if b.parent is None)
    need = {root}
    for s in sims:
        for name in s.bones():
            while name and name not in need:
                need.add(name)
                name = parent_of[name]
    depth = {}
    for b in bones:
        depth[b.name] = depth[b.parent.name] + 1 if b.parent else 0
    order = sorted(need, key=lambda n: depth[n])        # parents before children
    offsets = {n: ue_offset(bones[n]) for n in order}
    scene = obj.users_scene[0] if obj.users_scene else bpy.context.scene

    keep = scene.frame_current
    locals_ = []
    for f in range(start, end + 1):
        scene.frame_set(f)
        cs = {n: _to_game(obj.pose.bones[n].matrix, offsets[n], unit) for n in order}
        locals_.append({n: relative(cs[n], cs[parent_of[n]]) if parent_of[n] in cs else cs[n] for n in order})
    scene.frame_set(keep)

    fps = scene.render.fps / scene.render.fps_base
    steps = max(1, round(rate / fps))
    dt = 1.0 / (fps * steps)
    simulated = list(dict.fromkeys(b for s in sims for b in s.outputs()))
    alpha = {b: s.node.get("alpha", 1.0) for s in sims for b in s.outputs()}
    children = {n: [c for c in order if parent_of[c] == n] for n in order}

    def tick(loc):
        loc, cs = dict(loc), {}         # the locals reaching each node: earlier nodes' results included
        for n in order:
            cs[n] = compose(loc[n], cs[parent_of[n]]) if parent_of[n] in cs else loc[n]
        for s in sims:
            for name, t in s.evaluate(dt, cs, root).items():
                a, par = alpha[name], cs.get(parent_of[name])
                if a < 1e-5:
                    continue
                local = relative(t, par) if par else t
                if a < 1.0 - 1e-5:      # the engine blends a partial node in the bone's local space
                    local = _blend(loc[name], local, a)
                loc[name] = local
                cs[name] = compose(local, par) if par else local
                stack = list(children[name])
                while stack:            # its children follow from their own locals
                    c = stack.pop()
                    cs[c] = compose(loc[c], cs[parent_of[c]])
                    stack += children[c]
        return cs

    for _ in range(round(PREROLL / dt)):
        cs = tick(locals_[0])
    frames = [cs]
    for i in range(1, len(locals_)):
        for k in range(1, steps + 1):
            cs = tick({n: _blend(locals_[i - 1][n], locals_[i][n], k / steps) for n in order})
        frames.append(cs)

    _push_down(obj)
    _twins(obj, simulated)
    _write(obj, simulated, frames, start, offsets, unit, parent_of)
    set_on(obj, True)
    Log.info("[Material Porter] dynamics: %d bones over %d frames in %.1fs" % (len(simulated), len(frames), time.time() - t0))
    return len(simulated), len(frames)


def _write(obj, simulated, frames, start, offsets, unit, parent_of):
    ad = obj.animation_data or obj.animation_data_create()
    action = bpy.data.actions.new("%s %s" % (obj.name, TRACK))
    ad.action = action
    inv = {n: o.inverted() for n, o in offsets.items()}
    times = np.arange(start, start + len(frames), dtype=np.float32)
    for name in simulated:
        bone, pb = obj.data.bones[name], obj.pose.bones[TWIN + name]
        rest = bone.parent.matrix_local.inverted() @ bone.matrix_local if bone.parent else bone.matrix_local
        rest_inv, par = rest.inverted(), parent_of[name]
        quaternion = pb.rotation_mode == 'QUATERNION'
        locs, rots, prev = [], [], None
        for cs in frames:
            m = _from_game(cs[name], inv[name], unit)
            loc, q, _ = (rest_inv @ (_from_game(cs[par], inv[par], unit).inverted() @ m if par in cs else m)).decompose()
            if prev is not None and q.dot(prev) < 0.0:      # no flips between keys
                q.negate()
            prev = q
            locs.append(loc)
            rots.append(q if quaternion else q.to_euler(pb.rotation_mode))
        base = 'pose.bones["%s"].' % (TWIN + name)
        for path, values in (("location", locs), ("rotation_quaternion" if quaternion else "rotation_euler", rots)):
            for i in range(len(values[0])):
                fc = action.fcurve_ensure_for_datablock(obj, base + path, index=i)
                fc.keyframe_points.add(len(values))
                co = np.empty(2 * len(values), np.float32)
                co[0::2], co[1::2] = times, [v[i] for v in values]
                fc.keyframe_points.foreach_set("co", co)
                fc.update()
    ad.action = None
    track = ad.nla_tracks.new()
    track.name = TRACK
    track.strips.new(TRACK, start, action).extrapolation = 'NOTHING'


def ui(layout, obj):
    """The panel's Dynamics row, on an armature with dynamics nodes."""
    if not obj.data.get(KEY):
        return
    row = layout.row(align=True)
    if _track(obj) is not None:
        on = is_on(obj)
        row.operator("fpmp.dynamics_on", text="Dynamics", icon='PHYSICS', depress=on).on = not on
    row.operator("fpmp.dynamics_bake", text="Simulate", icon='FILE_REFRESH')
