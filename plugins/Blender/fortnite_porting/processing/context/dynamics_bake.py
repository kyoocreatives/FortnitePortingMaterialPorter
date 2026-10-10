"""Bake the armature's replayed nodes (AnimDynamics, RigidBody and its controls) over its animation: sample it once per
frame, tick the nodes between frames, key the simulated bones' twins into a Dynamics NLA track."""
import math
import time

import bpy
import numpy as np

from ...logger import Log
from .dynamics_runner import FLIP, KEY, Runner  # noqa: F401 (FLIP: the devtools checks read it here)

TRACK = "Dynamics"
TWIN = "DYN_"
BAKED = "DYN bake"
GROUP = "Dynamics"


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


def bake(obj, rate=60):
    """Simulate the armature's dynamic bone nodes over its animation; (bones keyed, frames) or (0, 0)."""
    from .dynamics_live import paused
    if not obj.data.get(KEY) or frame_range(obj) is None:
        return 0, 0
    t0 = time.time()
    run = Runner(obj, rate)
    if not run.sims:
        return 0, 0
    with paused(obj):
        _drop(obj)      # never sample the last bake as input
        set_on(obj, False)
        start, end = frame_range(obj)
        scene = obj.users_scene[0] if obj.users_scene else bpy.context.scene
        keep = scene.frame_current
        poses = []
        for f in range(start, end + 1):
            scene.frame_set(f)
            poses.append(run.sample())
        scene.frame_set(keep)
    frames = [run.start(poses[0])]
    for i in range(1, len(poses)):
        frames.append(run.advance(poses[i - 1], poses[i]))
    _push_down(obj)
    _twins(obj, run.simulated)
    _write(obj, run, frames, start)
    set_on(obj, True)
    Log.info("[Material Porter] dynamics: %d bones over %d frames in %.1fs" % (len(run.simulated), len(frames), time.time() - t0))
    return len(run.simulated), len(frames)


def _write(obj, run, frames, start):
    ad = obj.animation_data or obj.animation_data_create()
    action = bpy.data.actions.new("%s %s" % (obj.name, TRACK))
    ad.action = action
    times = np.arange(start, start + len(frames), dtype=np.float32)
    for name in run.simulated:
        locs, rots, prev = [], [], None
        for cs in frames:
            loc, q = run.basis(cs, name)
            if prev is not None and q.dot(prev) < 0.0:      # no flips between keys
                q.negate()
            prev = q
            locs.append(loc)
            rots.append(q)
        base = 'pose.bones["%s"].' % (TWIN + name)
        for path, values in (("location", locs), ("rotation_quaternion", rots)):
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
    from .dynamics_live import is_live
    live = is_live(obj)
    row.operator("fpmp.dynamics_live", text="Live", icon='PLAY', depress=live).on = not live
    row.operator("fpmp.dynamics_bake", text="Simulate", icon='FILE_REFRESH')
    if states(obj):
        from .dynamics_runner import STATE
        layout.operator_menu_enum("fpmp.dynamics_state", "state", text=obj.data.get(STATE) or "OnGround_Standing", icon='ARMATURE_DATA')


def states(obj):
    """The movement states the armature's dynamics parameters define, in their order."""
    import json
    raw = obj.data.get(KEY)
    if not raw:
        return []
    for n in json.loads(raw)["nodes"]:
        if n.get("states"):
            return list(n["states"])
    return []
