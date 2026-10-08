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

KEY = "is_lego_rig"
NEEDED = ("root", "pelvis", "torso", "head", "leg_l", "leg_r", "arm_l", "arm_r", "hand_l", "hand_r")
COLORS = {"C": (0.96, 0.79, 0.05), "L": (0.18, 0.55, 1.0), "R": (1.0, 0.23, 0.19), "accessory": (0.24, 0.86, 0.52)}


def fits(obj):
    """A LEGO figure's skeleton."""
    return obj is not None and obj.type == 'ARMATURE' and all(n in obj.data.bones for n in NEEDED)


def create(obj):
    """Rig the armature object. Returns what it did, in a line."""
    from ...utils import ensure_blend_data
    from . import rig_shapes
    from .creature_rig import align_shape, sized
    armature = obj.data
    if armature.get(KEY):
        return "%s: already has a LEGO rig" % obj.name
    if not fits(obj):
        return "%s: not a LEGO figure's skeleton" % obj.name
    ensure_blend_data()             # loads the control shapes (CTRL_Spine, CTRL_Box...)
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

    collections = {}
    for name, visible in (("LEGO Controls", True), ("LEGO Other", False)):
        collections[name] = armature.collections.get(name) or armature.collections.new(name)
        collections[name].is_visible = visible
    for collection in armature.collections:
        if collection.name not in collections:
            collection.is_visible = False       # hide the import's collections; the rig's decide what shows
    for bone in bones:
        collections["LEGO Other"].assign(bone)

    def control(name, color):
        collections["LEGO Controls"].assign(bones[name])
        rig_shapes.color(pose[name], COLORS[color])

    root = pose["root"]
    sized(root, "CTRL_Box", "THEME09", 0.1, wire=3.0)
    # Proportions come from the joints, since mesh bounds would include hands and hair.
    hip_x, shoulder_x = abs(bones["leg_l"].head_local.x), abs(bones["arm_l"].head_local.x)
    root.custom_shape = rig_shapes.footprint("CR_Footprint_" + obj.name, hip_x * 5.0, hip_x * 5.5)
    align_shape(obj, root, x=-right, y=forward, z=up)
    rig_shapes.place(obj, root, Vector(((low.x + high.x) / 2.0, (low.y + high.y) / 2.0, low.z)))
    control("root", "C")
    pelvis = pose["pelvis"]
    sized(pelvis, "CTRL_Box", "THEME09", 0.1, wire=3.0)
    pelvis.custom_shape = rig_shapes.footprint("CR_Plate_" + obj.name, hip_x * 2.6, hip_x * 4.2)
    align_shape(obj, pelvis, x=-right, y=forward, z=up)
    control("pelvis", "C")
    # Rings around torso and head; their bones point up and a ring's axis is its bone's.
    torso, head = bones["torso"], bones["head"]
    sized(pose["torso"], "CTRL_Spine", "THEME09", shoulder_x * 2.6, wire=3.0)
    rig_shapes.place(obj, pose["torso"], (torso.head_local + torso.tail_local) / 2.0)
    control("torso", "C")
    head_width = shoulder_x * 1.9
    sized(pose["head"], "CTRL_Spine", "THEME09", head_width, wire=3.0)
    rig_shapes.place(obj, pose["head"], head.head_local + up * head_width * 0.45)
    control("head", "C")
    for side, out in (("l", -right), ("r", right)):
        color = side.upper()
        leg = bones["leg_" + side]
        hip = leg.head_local
        sized(pose[leg.name], "CTRL_Spine", "THEME09", (hip.z - low.z) * 0.7, wire=3.0)
        align_shape(obj, pose[leg.name], y=leg.matrix_local.col[0].to_3d())            # bone X is the hip axis
        rig_shapes.place(obj, pose[leg.name], hip + out * (abs(hip.x) * 0.9) - up * (hip.z - low.z) * 0.15)
        pose[leg.name].rotation_mode = 'XYZ'
        pose[leg.name].lock_rotation = (False, True, True)
        control(leg.name, color)
        arm = bones["arm_" + side]
        sized(pose[arm.name], "CTRL_Spine", "THEME09", shoulder_x * 1.1, wire=3.0)
        align_shape(obj, pose[arm.name], y=arm.matrix_local.col[0].to_3d())
        rig_shapes.place(obj, pose[arm.name], arm.head_local + out * shoulder_x * 0.25)
        control(arm.name, color)
        hand = bones["hand_" + side]
        sized(pose[hand.name], "CTRL_Spine", "THEME09", shoulder_x * 0.7, wire=2.5)
        pose[hand.name].rotation_mode = 'YXZ'
        pose[hand.name].lock_rotation = (True, False, True)
        control(hand.name, color)
    if "head_accessory" in bones:
        sized(pose["head_accessory"], "CTRL_Box", "THEME09", shoulder_x * 0.8, wire=2.5)
        control("head_accessory", "accessory")
    armature[KEY] = True
    bpy.ops.object.mode_set(mode='OBJECT')
    return "%s: LEGO rig (%.0f cm tall)" % (obj.name, size.z * 100.0)
