"""Live preview of the dynamic bones: while it is on, each frame played forward steps the replayed nodes one frame and
poses the simulated bones' twins directly; any other frame change restarts the simulation there. The baked Dynamics
layer stays muted meanwhile and comes back when it is turned off."""
from contextlib import contextmanager

import bpy
from mathutils import Quaternion, Vector

from .dynamics_bake import BAKED, KEY, TWIN, _track, _twins
from .dynamics_runner import Runner

LIVE = "fpmp_dynamics_live"
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


def _constraints(obj, on):
    for pb in obj.pose.bones:
        c = pb.constraints.get(BAKED)
        if c is not None:
            c.mute = not on


def set_live(obj, on):
    """Turn the live preview on or off for an armature with dynamic bone nodes."""
    _states.pop(obj.name_full, None)
    track = _track(obj)
    if on:
        run = Runner(obj)
        if not run.sims:
            return False
        _twins(obj, run.simulated)
        if track is not None:
            track.mute = True
        obj.data[LIVE] = True
        install()
        _step(obj, bpy.context.scene)
        _constraints(obj, True)
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


def _step(obj, scene):
    frame = scene.frame_current
    state = _states.get(obj.name_full)
    if state is None or frame != state["frame"] + 1:
        state = {"runner": Runner(obj)}
        pose = state["runner"].sample()
        cs = state["runner"].start(pose)
    else:
        pose = state["runner"].sample()
        cs = state["runner"].advance(state["pose"], pose)
    state["frame"], state["pose"] = frame, pose
    _states[obj.name_full] = state
    run = state["runner"]
    for name in run.simulated:
        twin = obj.pose.bones.get(TWIN + name)
        if twin is not None:
            twin.rotation_mode = 'QUATERNION'
            twin.location, twin.rotation_quaternion = run.basis(cs, name)


@bpy.app.handlers.persistent
def _before_frame(scene, depsgraph=None):
    # the frame is evaluated without the twins so the nodes read the animated pose
    for obj in _live_armatures(scene):
        _constraints(obj, False)


@bpy.app.handlers.persistent
def _after_frame(scene, depsgraph=None):
    for obj in _live_armatures(scene):
        _step(obj, scene)
        _constraints(obj, True)


def install():
    if _before_frame not in bpy.app.handlers.frame_change_pre:
        bpy.app.handlers.frame_change_pre.append(_before_frame)
    if _after_frame not in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.append(_after_frame)


def uninstall():
    for handlers, fn in ((bpy.app.handlers.frame_change_pre, _before_frame), (bpy.app.handlers.frame_change_post, _after_frame)):
        if fn in handlers:
            handlers.remove(fn)
