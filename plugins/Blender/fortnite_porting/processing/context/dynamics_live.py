"""Live preview of the dynamic bones: while it is on, each frame played forward steps the replayed nodes one frame and
poses the simulated bones' twins directly; any other frame change restarts the simulation there. During playback a
frame's result is shown from the next frame on (so Blender evaluates the character once per frame); scrubbing,
stepping and stopping show it on its own frame. The baked Dynamics layer stays muted meanwhile."""
from contextlib import contextmanager

import bpy
from mathutils import Quaternion, Vector

from .dynamics_bake import BAKED, GROUP, KEY, TWIN, _curves, _track, _twins
from .dynamics_runner import Runner

LIVE = "fpmp_dynamics_live"
INPUT = "DYNIN_"        # a hidden copy of a constrained bone without the twin: its evaluated pose is the input
_states = {}            # armature -> {"runner", "frame", "pose"}
_paused = set()


def is_live(obj):
    return bool(obj.data.get(LIVE))


@contextmanager
def paused(obj):
    """No live steps while something else (the bake) moves the frame."""
    _paused.add(obj.name_full)
    try:
        yield
    finally:
        _paused.discard(obj.name_full)


def _constraints(obj, on, names=None):
    """The twins' last constraints on (or off) for the named simulated bones, all of them by default."""
    for pb in obj.pose.bones:
        if names is None or pb.name in names:
            c = pb.constraints.get(BAKED)
            if c is not None:
                c.mute = not on


def _modes(obj, run):
    """How live mode reads each simulated bone's input pose without turning its twin off (which costs Blender a
    second evaluation of the whole character): from its own animated basis, from the bone the deform rig copies it
    from, or None when only turning the twin off can tell."""
    modes = {}
    for name in run.simulated:
        pb = obj.pose.bones[name]
        stack = [c for c in pb.constraints if c.name != BAKED and not c.mute and c.influence > 0.0]
        bone = pb.bone
        if not stack and bone.use_inherit_rotation and bone.inherit_scale == 'FULL' and bone.use_local_location:
            rest = bone.parent.matrix_local.inverted() @ bone.matrix_local if bone.parent else bone.matrix_local
            modes[name] = ("basis", rest)
            continue
        last = stack[-1] if stack else None
        if last is not None and last.type == 'COPY_TRANSFORMS' and last.target == obj and last.subtarget                 and last.influence == 1.0 and last.mix_mode == 'REPLACE' and last.owner_space == 'WORLD' and last.target_space == 'WORLD':
            modes[name] = ("copy", last.subtarget)
            continue
        modes[name] = ("copy", INPUT + name) if INPUT + name in obj.pose.bones else None
    return modes


def needs_toggle(run):
    return {n for n, m in _modes(run.obj, run).items() if m is None}


def _animated(obj, name):
    """Whether something keys or drives the bone or its constraints (a static copy wouldn't follow; a driver copying
    its basis reads it back slightly off, enough for contacts to drift)."""
    paths = tuple('pose.bones["%s"].%s' % (name, p) for p in ("location", "rotation", "scale", "constraints"))
    ad = obj.animation_data
    if ad is None:
        return False
    actions = [ad.action] + [s.action for t in ad.nla_tracks for s in t.strips]
    curves = list(ad.drivers) + [fc for a in actions if a is not None for fc in _curves(a)]
    return any(fc.data_path.startswith(paths) for fc in curves)


def _input_twins(obj, names):
    """Gives each named bone an input twin: same parent, rest and basis, a copy of its constraints; animated ones keep
    toggling. Returns the bones that got one."""
    names = [n for n in names if not _animated(obj, n)]
    missing = [n for n in names if INPUT + n not in obj.data.bones]
    if missing:
        layer = bpy.context.view_layer
        active, mode = layer.objects.active, obj.mode
        layer.objects.active = obj
        bpy.ops.object.mode_set(mode='EDIT')
        edit = obj.data.edit_bones
        for n in missing:
            src, t = edit[n], edit.new(INPUT + n)
            t.head, t.tail, t.roll, t.parent = src.head.copy(), src.tail.copy(), src.roll, src.parent
            t.use_connect, t.use_deform, t.use_inherit_rotation = src.use_connect, False, src.use_inherit_rotation
            t.inherit_scale, t.use_local_location = src.inherit_scale, src.use_local_location
        bpy.ops.object.mode_set(mode=mode if mode in ('POSE', 'OBJECT') else 'OBJECT')
        layer.objects.active = active
        group = obj.data.collections.get(GROUP) or obj.data.collections.new(GROUP)
        group.is_visible = False
        for n in missing:
            bone = obj.data.bones[INPUT + n]
            for c in list(bone.collections):
                c.unassign(bone)
            group.assign(bone)
    for n in names:         # copied fresh: they may have changed since
        twin = obj.pose.bones[INPUT + n]
        twin.matrix_basis = obj.pose.bones[n].matrix_basis
        for c in list(twin.constraints):
            twin.constraints.remove(c)
        for c in obj.pose.bones[n].constraints:
            if c.name != BAKED:
                twin.constraints.copy(c)
    return set(names)


def _inputs(obj, modes):
    pose, out = obj.pose.bones, {}
    for name, mode in modes.items():
        if mode is None:
            continue
        pb = pose[name]
        if mode[0] == "copy":
            out[name] = pose[mode[1]].matrix
        else:
            out[name] = (pb.parent.matrix @ mode[1] if pb.parent else mode[1]) @ pb.matrix_basis
    return out


def set_live(obj, on):
    """Turn the live preview on or off for an armature with dynamic bone nodes."""
    _states.pop(obj.name_full, None)
    track = _track(obj)
    if on:
        run = Runner(obj)
        if not run.sims:
            return False
        _twins(obj, run.simulated)
        for name in run.simulated:
            obj.pose.bones[TWIN + name].rotation_mode = 'QUATERNION'
        if track is not None:
            track.mute = True
        obj.data[LIVE] = True
        install()
        _constraints(obj, True)
        toggled = needs_toggle(run)
        toggled -= _input_twins(obj, toggled)
        if toggled:
            _constraints(obj, False, toggled)
            bpy.context.view_layer.update()
        _step(obj, bpy.context.scene)
        _write(obj)
        _constraints(obj, True, toggled)
        return True
    if LIVE in obj.data:
        del obj.data[LIVE]
    for pb in obj.pose.bones:
        if pb.name.startswith(TWIN):
            pb.location, pb.rotation_quaternion = Vector(), Quaternion()
    if track is not None:
        track.mute = False
    _constraints(obj, track is not None)
    return True


def _live_armatures(scene):
    return [o for o in scene.objects if o.type == 'ARMATURE' and o.data.get(LIVE) and o.data.get(KEY)
            and o.name_full not in _paused]


def _fresh(obj):
    run = Runner(obj)
    modes = _modes(obj, run)
    names = [pb.name for pb in obj.pose.bones]
    slots = [names.index(TWIN + n) for n in run.simulated]
    return {"runner": run, "modes": modes, "toggled": {n for n, m in modes.items() if m is None}, "slots": slots}


def _step(obj, scene):
    frame = scene.frame_current
    state = _states.get(obj.name_full)
    if state is None or frame != state["frame"] + 1:
        state = _fresh(obj)
        run = state["runner"]
        pose = run.sample(_inputs(obj, state["modes"]))
        cs = run.start(pose)
    else:
        run = state["runner"]
        pose = run.sample(_inputs(obj, state["modes"]))
        cs = run.advance(state["pose"], pose)
    state["frame"], state["pose"] = frame, pose
    state["pending"] = [(slot,) + run.basis(cs, name) for name, slot in zip(run.simulated, state["slots"])]
    _states[obj.name_full] = state


def _write(obj):
    """The twins' poses from the last step, every twin in two calls (per-bone writes each tag the armature)."""
    state = _states.get(obj.name_full)
    if not state or not state.get("pending"):
        return
    bones = obj.pose.bones
    locs, rots = [0.0] * (3 * len(bones)), [0.0] * (4 * len(bones))
    bones.foreach_get("location", locs)
    bones.foreach_get("rotation_quaternion", rots)
    for slot, loc, q in state["pending"]:
        locs[3 * slot:3 * slot + 3] = loc
        rots[4 * slot:4 * slot + 4] = q
    bones.foreach_set("location", locs)
    bones.foreach_set("rotation_quaternion", rots)
    state["pending"] = None


def _playing():
    screen = bpy.context.screen
    return bool(screen and screen.is_animation_playing)


@bpy.app.handlers.persistent
def _before_frame(scene, depsgraph=None):
    for obj in _live_armatures(scene):
        # while playing, the last frame's result goes in now and evaluates with this frame: one evaluation per frame
        _write(obj)
        # only bones whose input can't be read otherwise are evaluated without their twin
        state = _states.get(obj.name_full)
        if state is None or state["toggled"]:
            _constraints(obj, False, None if state is None else state["toggled"])


@bpy.app.handlers.persistent
def _after_frame(scene, depsgraph=None):
    for obj in _live_armatures(scene):
        had = _states.get(obj.name_full)
        _step(obj, scene)
        toggled = _states[obj.name_full]["toggled"]
        if had is None or toggled:
            _constraints(obj, True, None if had is None else toggled)
        if had is None or toggled or not _playing():
            _write(obj)         # scrubbing, stepping or paused: exact on this frame


@bpy.app.handlers.persistent
def _playback_ended(scene, depsgraph=None):
    for obj in _live_armatures(scene):
        _write(obj)


def install():
    if _before_frame not in bpy.app.handlers.frame_change_pre:
        bpy.app.handlers.frame_change_pre.append(_before_frame)
    if _after_frame not in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.append(_after_frame)
    if _playback_ended not in bpy.app.handlers.animation_playback_post:
        bpy.app.handlers.animation_playback_post.append(_playback_ended)


def uninstall():
    for handlers, fn in ((bpy.app.handlers.frame_change_pre, _before_frame), (bpy.app.handlers.frame_change_post, _after_frame),
                         (bpy.app.handlers.animation_playback_post, _playback_ended)):
        if fn in handlers:
            handlers.remove(fn)
