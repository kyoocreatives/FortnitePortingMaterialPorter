"""A Tasty-rigged armature (FP's or upstream FP's) turned into one with the official rig, keeping the pose it shows.

Read from the armature itself, not from Tasty's code (it differs between FP versions). Tasty's own: bones without FP's
import data (orig_quat) that sit only in Tasty's bone groups, constraints with Blender's default names (the fork's own
are prefixed), and the drivers that hide bones, read Tasty's IK switches or point at what goes. The fork's extras (face
board, deform correctives, dynamics, sockets) stay. The bones are posed as they looked, then the official rig is built
and the frame is baked onto its controls."""
import re

import bpy

from . import official_rig

TASTY_PROPS = ("is_tasty", "fpmp_tasty_styled")
TASTY_GROUPS = {"Base", "Rig", "Face", "Twist", "Dynamic", "Deform", "Extra"}
FORK_CONSTRAINTS = ("OR ", "RK ", "MB ", "DR ", "FR ", "MP ", "RL ")      # the fork's constraint names: kept
IMPORTED = "orig_quat"      # FP's importer keeps each game bone's UE rest on it
KEPT_TRACK = "Tasty pose (before conversion)"


def can_convert(obj):
    return (obj is not None and obj.type == 'ARMATURE' and bool(obj.data.get("is_tasty"))
            and not obj.data.get(official_rig.MARK))


def _tasty_bones(obj):
    """The bones Tasty added: no import data, only in Tasty's groups, weighting no mesh."""
    weighted = {g.name for o in obj.children_recursive if o.type == 'MESH' for g in o.vertex_groups}
    return {b.name for b in obj.data.bones if IMPORTED not in b and b.name not in weighted
            and all(c.name in TASTY_GROUPS for c in b.collections)}


def _tasty_driver(fc, gone_bones, gone_constraints):
    path = fc.data_path
    bone = re.match(r'pose\.bones\["([^"]+)"\]', path)
    if bone and bone.group(1) in gone_bones or path.endswith(".hide"):
        return True
    if any('constraints["%s"]' % c in path for c in gone_constraints):
        return True
    for var in fc.driver.variables:
        for t in var.targets:
            if "use_ik_" in (t.data_path or "") or (t.bone_target and t.bone_target in gone_bones):
                return True
    return False


def _depth(bone):
    d = 0
    while bone.parent is not None:
        bone, d = bone.parent, d + 1
    return d


def convert(obj, hands=False):
    """Converts `obj` in place; returns a short report. The scene frame's pose is the one kept."""
    view_layer = bpy.context.view_layer
    view_layer.update()
    pose = {pb.name: pb.matrix.copy() for pb in obj.pose.bones}       # what the armature shows now, Tasty's IK included
    gone = _tasty_bones(obj)
    if not official_rig.fits(obj):
        raise ValueError("the official rig fits the humanoid skeleton (pelvis, spine, arms, legs); this armature isn't one")
    # the old animation set aside (a muted NLA track): a frame change would otherwise pose the bones from it
    ad = obj.animation_data
    if ad is not None and ad.action is not None:
        track = ad.nla_tracks.new()
        track.name = KEPT_TRACK
        track.strips.new(ad.action.name, int(ad.action.frame_range[0]), ad.action)
        track.mute = True
        ad.action = None
    gone_constraints = set()
    for pb in obj.pose.bones:
        for c in list(pb.constraints):
            if not c.name.startswith(FORK_CONSTRAINTS):
                gone_constraints.add(c.name)
                pb.constraints.remove(c)
    for owner in (obj, obj.data):
        if owner.animation_data is not None:
            for fc in [fc for fc in owner.animation_data.drivers if _tasty_driver(fc, gone, gone_constraints)]:
                owner.animation_data.drivers.remove(fc)
    for pb in obj.pose.bones:
        if IMPORTED in pb.bone:         # Tasty styled and hid game bones; the fork's extras keep their look
            pb.custom_shape = None
            pb.color.palette = 'DEFAULT'
            pb.hide = pb.bone.hide = False
    if bpy.context.object is not None and bpy.context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')
    edit = obj.data.edit_bones
    removed = [b.name for b in edit if b.name in gone]
    for name in removed:
        edit.remove(edit[name])
    bpy.ops.object.mode_set(mode='OBJECT')
    for name in TASTY_GROUPS:       # Tasty's groups; the fork's stay
        collection = obj.data.collections_all.get(name)
        if collection is not None:
            obj.data.collections.remove(collection)
    for key in [k for k in obj.keys() if k.startswith("use_ik_") or k in TASTY_PROPS]:
        del obj[key]
    for key in [k for k in obj.data.keys() if k in TASTY_PROPS]:
        del obj.data[key]
    # the game bones posed as they looked, parents first (a bone's pose is set against its parent's)
    pbs = sorted(obj.pose.bones, key=lambda p: _depth(p.bone))
    for pb in pbs:
        pb.matrix_basis.identity()
    level = None
    for pb in pbs:
        d = _depth(pb.bone)
        if d != level:
            view_layer.update()
            level = d
        if pb.name in pose:
            pb.matrix = pose[pb.name]
    view_layer.update()
    summary = official_rig.create(obj)
    if hands:
        from . import official_hands
        official_hands.build(obj)
    frame = bpy.context.scene.frame_current
    official_rig.bake_to_controls(obj, frame, frame)
    return "%s; %d Tasty bones removed, pose baked on frame %d" % (summary, len(removed), frame)
