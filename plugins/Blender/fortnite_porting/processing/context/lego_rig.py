"""Control rig for a LEGO Fortnite figure's armature, on the kit (rig_kit): the official rig's standard on the
minifigure skeleton every figure shares (FigureBody_r2: root > pelvis > leg_l/leg_r and torso > arm > hand,
neck_accessory > head > head_accessory, plus sockets and effect points).

All FK, each part turning only as a minifigure's does, its control showing how:
- global_ctrl: hexagon on the ground; root_ctrl: arrow ahead (places and turns the figure);
- pelvis_ctrl: octagon around the hips (the body: moves the whole figure off the ground);
- torso_ctrl: rounded square around the chest; head_ctrl: ring around the neck; hair or hat: a box around it;
- legs, arms: a dial outside the hip or shoulder, on the axis they swing about; hands: a ring around the wrist.
The game bones keep their names, rest pose and hierarchy and copy their controls (rig off: an animation plays).
"""

import bpy
from mathutils import Vector

from . import rig_kit, rig_style

KEY = "is_lego_rig"
NEEDED = ("root", "pelvis", "torso", "head", "leg_l", "leg_r", "arm_l", "arm_r", "hand_l", "hand_r")
CONTROLLED = ("root", "pelvis", "torso", "head", "head_accessory", "leg_l", "leg_r", "arm_l", "arm_r", "hand_l", "hand_r")
CTRL = "_ctrl"


def fits(obj):
    """A LEGO figure's skeleton."""
    return obj is not None and obj.type == 'ARMATURE' and all(n in obj.data.bones for n in NEEDED)


def create(obj):
    """Rig the armature object. Returns what it did, in a line."""
    armature = obj.data
    if armature.get(KEY):
        return "%s: already has a LEGO rig" % obj.name
    if not fits(obj):
        return "%s: not a LEGO figure's skeleton" % obj.name
    fit = rig_kit.Fit(obj)
    view_layer = bpy.context.view_layer
    for o in view_layer.objects:
        o.select_set(False)
    view_layer.objects.active = obj
    obj.select_set(True)
    bones = armature.bones
    up, forward = Vector((0.0, 0.0, 1.0)), Vector((0.0, -1.0, 0.0))     # the figure faces -Y: its left is +X
    left = Vector((1.0, 0.0, 0.0))
    low_high = fit.bounds() or (Vector([min(p[i] for b in bones for p in (b.head_local, b.tail_local)) for i in range(3)]),
                                Vector([max(p[i] for b in bones for p in (b.head_local, b.tail_local)) for i in range(3)]))
    low, high = low_high
    size = high - low
    shoulder_x = abs(bones["arm_l"].head_local.x)
    hip_x = abs(bones["leg_l"].head_local.x)
    ground = Vector(((low.x + high.x) / 2.0, (low.y + high.y) / 2.0, low.z))

    from . import face_board
    faces = face_board.faces(obj)
    head_size = shoulder_x * 1.9
    bpy.ops.object.mode_set(mode='EDIT')
    edit = armature.edit_bones
    has_board = False
    if faces:
        at = edit["head"].head + left * head_size * 1.4 + up * head_size * 0.2
        has_board = face_board.bones(edit, faces, "head", at, left, up, head_size)
    glob = rig_kit.new_bone(edit, "global_ctrl", ground, ground + forward * size.z * 0.1, None, up)
    controls = {}
    for name in CONTROLLED:
        if name not in edit:
            continue
        holder = edit[name].parent
        while holder is not None and holder.name not in controls:
            holder = holder.parent
        controls[name] = rig_kit.copy_bone(edit, edit[name], name + CTRL, edit[controls[holder.name]] if holder else glob).name
    rig_kit.new_bone(edit, rig_style.SETTINGS, ground, ground + up * size.z * 0.05, edit[controls["root"]])
    bpy.ops.object.mode_set(mode='POSE')

    rig_style.collections(armature)
    pose = obj.pose.bones
    W, C = rig_kit.WIDTHS, rig_kit.COLORS
    reach = max(Vector((size.x, size.y, 0.0)).length / 2.0, hip_x * 3.0)

    def radius(name, k, fallback):
        b = bones[name]
        return fit.radius([name], b.head_local, b.tail_local, fallback) * k

    rig_kit.look(obj, "global_ctrl", "Hexagon_Thin", reach * 1.6, C["root"], W["root"], "Controls")
    rig_kit.align(obj, pose["global_ctrl"], y=forward, z=up)
    rig_kit.look(obj, controls["root"], "Arrow_Thin", reach * 0.45, C["root"], W["root"], "Controls",
                 at=ground + forward * reach * 1.15)
    rig_kit.align(obj, pose[controls["root"]], y=forward, z=up)
    hips = bones["pelvis"].head_local
    rig_kit.look(obj, controls["pelvis"], "Octagon_Thin", hip_x * 2.6, C["body"], W["root"], "Controls", at=hips)
    rig_kit.align(obj, pose[controls["pelvis"]], y=forward, z=up)
    torso = bones["torso"]
    chest = torso.head_local + (torso.tail_local - torso.head_local) * 0.55
    rig_kit.look(obj, controls["torso"], "RoundedSquare_Thick", max(radius("torso", 1.15, shoulder_x), shoulder_x * 1.1),
                 rig_kit.spine_tone(0.6), W["limb"], "Controls", at=chest)
    rig_kit.align(obj, pose[controls["torso"]], y=forward, z=up)
    head = bones["head"]
    neck = head.head_local + up * head_size * 0.08
    rig_kit.look(obj, controls["head"], "Circle_Thick", max(radius("head", 1.15, head_size * 0.5), head_size * 0.5), C["neck"],
                 W["limb"], "Controls", at=neck)
    rig_kit.align(obj, pose[controls["head"]], y=forward, z=up)
    if "head_accessory" in controls:
        # around the hair or hat above the neck (long hair hangs down the back: the box would hold the figure)
        top = [p for p in fit.points.get("head_accessory", ()) if p.z >= bones["head"].head_local.z]
        box = (Vector([min(p[i] for p in top) for i in range(3)]), Vector([max(p[i] for p in top) for i in range(3)])) if len(top) > 8 else None
        if box is not None:
            centre, half = (box[0] + box[1]) / 2.0, (box[1] - box[0]) / 2.0
            r = max(half.x, half.y, half.z, 1e-3)
            rig_kit.look(obj, controls["head_accessory"], "Box_Thick", r * 1.1, C["C_digit"], W["digit"], "Secondary", at=centre,
                         stretch=(half.x / r, half.y / r, half.z / r))
        else:
            rig_kit.look(obj, controls["head_accessory"], "Box_Thick", shoulder_x * 0.4, C["C_digit"], W["digit"], "Secondary",
                         at=bones["head_accessory"].head_local)
        rig_kit.align(obj, pose[controls["head_accessory"]], x=left, y=forward, z=up)
    for side, out in (("l", left), ("r", -left)):
        role = side.upper()
        leg, arm, hand = bones["leg_" + side], bones["arm_" + side], bones["hand_" + side]
        # a dial outside the hip, on the axis the leg swings about (its bone X), the leg locked to it
        swing = leg.matrix_local.col[0].to_3d()
        at = leg.head_local + out * max(radius("leg_" + side, 1.3, hip_x), hip_x * 0.9) - up * leg.length * 0.25
        rig_kit.look(obj, controls[leg.name], "Circle_Thick", leg.length * 0.32, rig_kit.tone(role), W["limb"], "Controls", at=at)
        rig_kit.align(obj, pose[controls[leg.name]], y=-up, z=swing)
        pose[controls[leg.name]].rotation_mode = 'XYZ'
        pose[controls[leg.name]].lock_rotation = (False, True, True)
        swing = arm.matrix_local.col[0].to_3d()
        at = arm.head_local + out * max(radius("arm_" + side, 1.2, shoulder_x * 0.4), shoulder_x * 0.35)
        rig_kit.look(obj, controls[arm.name], "Circle_Thick", shoulder_x * 0.55, rig_kit.tone(role), W["limb"], "Controls", at=at)
        rig_kit.align(obj, pose[controls[arm.name]], y=-up, z=swing)
        rig_kit.look(obj, controls[hand.name], "Circle_Thick", max(radius("hand_" + side, 1.25, shoulder_x * 0.3), shoulder_x * 0.25),
                     rig_kit.tone(role), W["limb"], "Controls", ring=True, at=hand.head_local)
        pose[controls[hand.name]].rotation_mode = 'YXZ'
        pose[controls[hand.name]].lock_rotation = (True, False, True)
    for name, control in controls.items():
        rig_kit.follow(obj, name, control)
    if has_board:
        face_board.wire(obj, faces, head_size)
    for name in bones.keys():
        if not any(name in armature.collections[g].bones for g in rig_style.COLLECTIONS):
            rig_style.assign(armature, name, "Mechanics" if name.startswith("CR_") or name.endswith(CTRL) else "Game Bones")
    rig_kit.save(obj, {"follow": {c: b for b, c in controls.items()}, "limbs": [],
                       "controls": list(controls.values()) + ["global_ctrl"]})
    armature[rig_kit.ON] = True
    obj.show_in_front = True            # controls inside the body stay clickable
    armature[KEY] = True
    bpy.ops.object.mode_set(mode='OBJECT')
    return "%s: LEGO rig (%.0f cm tall)" % (obj.name, size.z * 100.0)
