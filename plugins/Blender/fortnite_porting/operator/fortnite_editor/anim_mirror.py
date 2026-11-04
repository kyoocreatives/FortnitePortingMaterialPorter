"""Mirrored animations: what an armature plays in the preview baked frame by frame into an action mirrored across its
X (left-right) axis. Each bone takes its mirror twin's motion as an armature-space change from rest (D = pose @
rest⁻¹, mirrored S D S), so it doesn't depend on how the skeleton's bone axes are rolled."""
import re

import numpy as np
from mathutils import Matrix

from . import preview_scene

S = Matrix.Scale(-1, 4, (1, 0, 0))
_SIDES = ((r"_l(?=$|_)", "_r"), (r"_r(?=$|_)", "_l"), (r"_L(?=$|_)", "_R"), (r"_R(?=$|_)", "_L"),
          (r"Left", "Right"), (r"Right", "Left"))


def twin(name):
    """The name of a bone's mirror twin (itself when it has none): hand_l <-> hand_r, Left <-> Right."""
    for pattern, other in _SIDES:
        swapped, n = re.subn(pattern, "\0", name)
        if n:
            return swapped.replace("\0", other)
    return name


def bake(arm, start, end, name):
    """A new action: `arm`'s animation over [start, end] (preview frames) mirrored. Steps the preview scene."""
    import bpy
    bones = list(arm.pose.bones)
    rest = {b.name: b.bone.matrix_local.copy() for b in bones}
    parents = {b.name: b.parent.name if b.parent else None for b in bones}
    frames = list(range(int(start), int(end) + 1))
    poses = []
    for f in frames:
        preview_scene.step(float(f))
        graph = preview_scene.get().view_layers[0].depsgraph
        ev = arm.evaluated_get(graph)
        poses.append({b.name: b.matrix.copy() for b in ev.pose.bones})
    action = bpy.data.actions.new(name)
    preview_scene.track([action])
    ad = arm.animation_data or arm.animation_data_create()
    playing, ad.action = ad.action, action        # (its curves are made for the datablock it's assigned to)
    curves = {}
    for b in bones:
        b.rotation_mode = 'QUATERNION'
        path = 'pose.bones["%s"].' % b.name
        for prop, n in (("location", 3), ("rotation_quaternion", 4), ("scale", 3)):
            for i in range(n):
                curves[(b.name, prop, i)] = action.fcurve_ensure_for_datablock(arm, path + prop, index=i,
                                                                               group_name=b.name)
    ad.action = playing
    values = {k: np.empty(len(frames) * 2, np.float32) for k in curves}
    last = {}
    for fi, (f, pose) in enumerate(zip(frames, poses)):
        mirrored = {}
        for b in bones:
            src = twin(b.name) if twin(b.name) in pose else b.name
            delta = pose[src] @ rest[src].inverted()
            mirrored[b.name] = (S @ delta @ S) @ rest[b.name]
        for b in bones:
            p = parents[b.name]
            if p is None:
                basis = rest[b.name].inverted() @ mirrored[b.name]
            else:
                basis = (rest[p].inverted() @ rest[b.name]).inverted() @ (mirrored[p].inverted() @ mirrored[b.name])
            loc, rot, scale = basis.decompose()
            if b.name in last and last[b.name].dot(rot) < 0:
                rot.negate()        # the shorter way from the frame before: no flips between keys
            last[b.name] = rot
            for prop, vec in (("location", loc), ("rotation_quaternion", rot), ("scale", scale)):
                for i, v in enumerate(vec):
                    values[(b.name, prop, i)][fi * 2:fi * 2 + 2] = (f, v)
    for key, fc in curves.items():
        fc.keyframe_points.add(len(frames))
        fc.keyframe_points.foreach_set("co", values[key])
        fc.update()
    return action


def mirror_track(arm, start, end):
    """`arm` plays the mirrored bake of what it plays now (one strip, looped as its first was)."""
    ad = arm.animation_data
    if ad is None or not ad.nla_tracks:
        return None
    loop = any(s.repeat > 1 for t in ad.nla_tracks for s in t.strips)
    action = bake(arm, start, end, "%s Mirrored" % arm.name)
    for track in list(ad.nla_tracks):
        ad.nla_tracks.remove(track)
    track = ad.nla_tracks.new()
    track.name = "Sections"
    strip = track.strips.new(action.name, int(start), action)
    strip.repeat = 999.0 if loop else 1.0
    return action
