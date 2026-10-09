"""Control rig for a LEGO Fortnite figure's armature.

Every figure shares one rigid, minifigure-like skeleton (FigureBody_r2: root > pelvis > leg_l/leg_r
and torso > arm > hand, neck_accessory > head > head_accessory, plus sockets and effect points), so
the rig is FK on the figure's own bones with shapes showing how each part turns:

- root: footprint on the ground (places and turns the figure);
- pelvis: plate at the hips (moves and turns the whole figure off the ground);
- torso, head: rings around them;
- legs: dial on the hip axis, rotating about it only;
- arms: dial on the shoulder axis; hands: ring at the wrist, rotating about it only;
- head_accessory (hair or hat): a box.
The bones keep their names, rest pose and hierarchy, so animations still play on them.
"""

import bpy
from mathutils import Vector

from . import rig_shapes, rig_style

KEY = "is_lego_rig"
NEEDED = ("root", "pelvis", "torso", "head", "leg_l", "leg_r", "arm_l", "arm_r", "hand_l", "hand_r")


def fits(obj):
    """A LEGO figure's skeleton."""
    return obj is not None and obj.type == 'ARMATURE' and all(n in obj.data.bones for n in NEEDED)


def create(obj):
    """Rig the armature object. Returns what it did, in a line."""
    from .creature_rig import align_shape
    armature = obj.data
    if armature.get(KEY):
        return "%s: already has a LEGO rig" % obj.name
    if not fits(obj):
        return "%s: not a LEGO figure's skeleton" % obj.name
    view_layer = bpy.context.view_layer
    for o in view_layer.objects:
        o.select_set(False)
    view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode='POSE')
    bones, pose = armature.bones, obj.pose.bones

    # Figure size from its meshes, else from its bones.
    inverse = obj.matrix_world.inverted()
    meshes = [o for o in bpy.data.objects if o.type == 'MESH' and any(m.type == 'ARMATURE' and m.object == obj for m in o.modifiers)]
    points = [inverse @ (o.matrix_world @ Vector(c)) for o in meshes for c in o.bound_box] or \
             [p for b in bones for p in (b.head_local, b.tail_local)]
    low, high = Vector([min(p[i] for p in points) for i in range(3)]), Vector([max(p[i] for p in points) for i in range(3)])
    size = high - low
    up, right = Vector((0.0, 0.0, 1.0)), Vector((-1.0, 0.0, 0.0))     # the figure faces -Y, so its right is -X
    forward = Vector((0.0, -1.0, 0.0))

    from . import face_board
    from ...material_porter import face_anim
    faces = face_anim.face_materials(obj)
    head_size = abs(bones["arm_l"].head_local.x) * 1.9
    has_board = False
    if faces:
        bpy.ops.object.mode_set(mode='EDIT')
        has_board = face_board.bones(armature.edit_bones, faces, "head", -right, up, head_size)
        bpy.ops.object.mode_set(mode='POSE')
        bones, pose = armature.bones, obj.pose.bones

    from math import pi
    rig_style.collections(armature)
    RING = (pi / 2.0, 0.0, 0.0)            # a kit circle lies in XY; this stands it around the bone's Y axis

    def control(name, role, shape=None, size=None, secondary=False, ring=False):
        bone = pose[name]
        if shape is not None:
            bone.custom_shape = rig_shapes.ensure(shape)
        bone.use_custom_shape_bone_size = False
        if size is not None:
            bone.custom_shape_scale_xyz = (size, size, size)
        if ring:
            bone.custom_shape_rotation_euler = RING
        rig_style.style(bone, role, secondary=secondary)
        rig_style.assign(armature, name, "Secondary" if secondary else "Controls")

    root = pose["root"]
    # Proportions come from the joints, since mesh bounds would include hands and hair.
    hip_x, shoulder_x = abs(bones["leg_l"].head_local.x), abs(bones["arm_l"].head_local.x)
    root.custom_shape = rig_shapes.footprint("CR_Footprint_" + obj.name, hip_x * 5.0, hip_x * 5.5)
    control("root", "main", size=1.0)
    align_shape(obj, root, x=-right, y=forward, z=up)
    rig_shapes.place(obj, root, Vector(((low.x + high.x) / 2.0, (low.y + high.y) / 2.0, low.z)))
    pelvis = pose["pelvis"]
    pelvis.custom_shape = rig_shapes.footprint("CR_Plate_" + obj.name, hip_x * 2.6, hip_x * 4.2)
    control("pelvis", "C", size=1.0)
    align_shape(obj, pelvis, x=-right, y=forward, z=up)
    # Rings around torso and head; their bones point up and a ring's axis is its bone's.
    torso, head = bones["torso"], bones["head"]
    control("torso", "C", "CR_Circle", shoulder_x * 1.3, ring=True)
    rig_shapes.place(obj, pose["torso"], (torso.head_local + torso.tail_local) / 2.0)
    head_width = shoulder_x * 1.9
    control("head", "C", "CR_Circle", head_width * 0.5, ring=True)
    rig_shapes.place(obj, pose["head"], head.head_local + up * head_width * 0.45)
    for side, out in (("l", -right), ("r", right)):
        role = side.upper()
        leg = bones["leg_" + side]
        hip = leg.head_local
        # dial on the hip axis (bone X), its tick down the leg
        control(leg.name, role, "CR_CircleTick", (hip.z - low.z) * 0.35)
        align_shape(obj, pose[leg.name], y=-up, z=leg.matrix_local.col[0].to_3d())
        rig_shapes.place(obj, pose[leg.name], hip + out * (abs(hip.x) * 0.9) - up * (hip.z - low.z) * 0.15)
        pose[leg.name].rotation_mode = 'XYZ'
        pose[leg.name].lock_rotation = (False, True, True)
        arm = bones["arm_" + side]
        control(arm.name, role, "CR_CircleTick", shoulder_x * 0.55)
        align_shape(obj, pose[arm.name], y=-up, z=arm.matrix_local.col[0].to_3d())
        rig_shapes.place(obj, pose[arm.name], arm.head_local + out * shoulder_x * 0.25)
        hand = bones["hand_" + side]
        control(hand.name, role, "CR_Circle", shoulder_x * 0.35, ring=True)
        pose[hand.name].rotation_mode = 'YXZ'
        pose[hand.name].lock_rotation = (True, False, True)
    if "head_accessory" in bones:
        control("head_accessory", "C", "CR_Box", shoulder_x * 0.4, secondary=True)
    if has_board:
        face_board.wire(obj, faces, head_size)
    for name in bones.keys():
        if not any(name in armature.collections[g].bones for g in rig_style.COLLECTIONS):
            rig_style.assign(armature, name, "Mechanics" if name.startswith("CR_") else "Game Bones")
    obj.show_in_front = True            # controls inside the body stay clickable
    armature[KEY] = True
    bpy.ops.object.mode_set(mode='OBJECT')
    return "%s: LEGO rig (%.0f cm tall)" % (obj.name, size.z * 100.0)
