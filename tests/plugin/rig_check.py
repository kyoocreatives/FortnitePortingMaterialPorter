"""Checks the fork's rig kit and the vehicle rig on a synthetic car skeleton (no game data needed).

    blender -b --factory-startup --python-exit-code 1 -P tests/plugin/rig_check.py -- <plugin parent>

<plugin parent> holds the plugin as package fpmp_baseline (see translator_test.py). Exit code 0 when every check passes."""
import sys

import bpy
from mathutils import Vector

sys.path.insert(0, sys.argv[sys.argv.index("--") + 1])
import fpmp_baseline  # noqa: E402,F401
from fpmp_baseline.processing.context import rig_shapes  # noqa: E402

FAILS = []
PASSES = [0]


def check(name, got, want, tol=1e-4):
    ok = all(abs(g - w) <= tol for g, w in zip(got, want)) if isinstance(want, tuple) else \
        (abs(got - want) <= tol if isinstance(want, float) else got == want)
    if ok:
        PASSES[0] += 1
    else:
        FAILS.append(name)
        print("[rig_check] FAIL %s: got %r, want %r" % (name, got, want))


# shapes
for name in ("CR_Circle", "CR_CircleTick", "CR_Square", "CR_Box", "CR_Diamond", "CR_Gear"):
    shape = rig_shapes.ensure(name)
    reach = max(v.co.length for v in shape.data.vertices)
    check("%s exists with edges" % name, len(shape.data.edges) > 3, True)
    check("%s is unit size" % name, 0.9 <= reach <= 1.8, True)
check("circle is smooth", len(rig_shapes.ensure("CR_Circle").data.vertices), 32)
check("tick reaches past the circle", max(v.co.y for v in rig_shapes.ensure("CR_CircleTick").data.vertices) > 1.1, True)
check("box is 3D", max(abs(v.co.z) for v in rig_shapes.ensure("CR_Box").data.vertices), 1.0)

# the kit, on a bare armature
from fpmp_baseline.processing.context import rig_style  # noqa: E402

arm_data = bpy.data.armatures.new("kit")
kit = bpy.data.objects.new("kit", arm_data)
bpy.context.scene.collection.objects.link(kit)
bpy.context.view_layer.objects.active = kit
bpy.ops.object.mode_set(mode='EDIT')
for name, x in (("CR_Settings", 0.0), ("hand_l", -1.0), ("hand_r", 1.0)):
    b = arm_data.edit_bones.new(name)
    b.head, b.tail = (x, 0.0, 0.0), (x, 0.2, 0.0)
bpy.ops.object.mode_set(mode='POSE')
arm_data.collections.new("FromTheGame")
groups = rig_style.collections(arm_data)
check("kit collections in order", [c.name for c in arm_data.collections if c.name in rig_style.COLLECTIONS], list(rig_style.COLLECTIONS))
check("kit visibility", [groups[n].is_visible for n in rig_style.COLLECTIONS], [True, True, False, False])
check("game's collections hidden", arm_data.collections["FromTheGame"].is_visible, False)
rig_style.assign(arm_data, "hand_l", "Controls")
rig_style.assign(arm_data, "hand_l", "Secondary")
check("assign moves, never doubles", [n for n in rig_style.COLLECTIONS if "hand_l" in groups[n].bones], ["Secondary"])
centre, left = Vector((0.0, 0.0, 0.0)), Vector((-1.0, 0.0, 0.0))
check("side left", rig_style.side_of(Vector((-1.0, 0.0, 0.0)), centre, left, 2.0), "L")
check("side right", rig_style.side_of(Vector((1.0, 0.0, 0.0)), centre, left, 2.0), "R")
check("side centre", rig_style.side_of(Vector((0.1, 0.0, 0.0)), centre, left, 2.0), "C")
pb = kit.pose.bones["hand_l"]
rig_style.style(pb, "L")
check("left colour", tuple(pb.color.custom.normal), (0.15, 0.45, 1.0), tol=0.01)
check("primary width", pb.custom_shape_wire_width, 2.5)
rig_style.style(pb, "L", secondary=True)
check("secondary tint", tuple(pb.color.custom.normal), (0.575, 0.725, 1.0), tol=0.01)
check("secondary width", pb.custom_shape_wire_width, 1.5)
rig_style.style(kit.pose.bones["hand_r"], "main")
check("main width", kit.pose.bones["hand_r"].custom_shape_wire_width, 3.5)
rig_style.add_settings(kit, [("spin", 1.0, "Wheels spin"), ("lean", 0.0, "Lean")])
check("settings on the bone", (kit.pose.bones["CR_Settings"]["spin"], kit.pose.bones["CR_Settings"]["lean"]), (1.0, 0.0))
check("setting path", rig_style.setting_path("spin"), 'pose.bones["CR_Settings"]["spin"]')
check("settings owner", rig_style.settings_owner(kit)[0] == kit.pose.bones["CR_Settings"], True)
check("settings keyable", kit.keyframe_insert(rig_style.setting_path("spin")), True)
con = kit.pose.bones["hand_r"].constraints.new('COPY_LOCATION')
rig_style.driven(kit, con, "lean")
kit.pose.bones["CR_Settings"]["lean"] = 0.25
bpy.context.view_layer.update()
check("driven influence follows the setting", con.influence, 0.25)
check("controls are the shown groups", sorted(b.name for b in rig_style.controls(kit)), ["hand_l"])
bpy.ops.object.mode_set(mode='OBJECT')

# the vehicle rig on a synthetic car: root > frame > body, a differential per axle, axle pivots, front steering
from fpmp_baseline.processing.context import vehicle_rig  # noqa: E402


def car(name, steering=True, body=True, bare_rear=False):
    data = bpy.data.armatures.new(name)
    obj = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')
    edit = data.edit_bones

    def bone(n, head, tail, parent=None):
        b = edit.new(n)
        b.head, b.tail, b.parent = head, tail, parent
        return b
    root = bone("root", (0, 0, 0), (0, 0.3, 0))
    frame = bone("frame", (0, 0, 0.4), (0, 0.3, 0.4), root)
    top = bone("body", (0, 0, 0.7), (0, 0.3, 0.7), frame) if body else frame
    for tag, y in (("fr", 1.3), ("bk", -1.3)):
        diff = bone(("front" if tag == "fr" else "rear") + "_differential", (0, y, 0.35), (0, y + 0.1, 0.35), frame)
        for side, x in (("l", -0.8), ("r", 0.8)):          # forward +Y: left is -X
            if bare_rear and tag == "bk":
                bone("wheel_disc_%s_%s" % (tag, side), (x, y, 0.35), (x * 1.1, y, 0.35), diff)
                continue
            pivot = bone("axle_pivot_%s_%s" % (tag, side), (x * 0.8, y, 0.35), (x * 0.8, y, 0.45), diff)
            hub = bone("wheel_steering_%s_%s" % (tag, side), (x, y, 0.35), (x, y, 0.45), pivot) if steering and tag == "fr" else pivot
            bone("wheel_disc_%s_%s" % (tag, side), (x, y, 0.35), (x * 1.1, y, 0.35), hub)
            bone("shock_up_%s_%s" % (tag, side), (x * 0.7, y, 0.7), (x * 0.7, y, 0.8), top)
    bpy.ops.object.mode_set(mode='OBJECT')
    vehicle_rig.create(obj)
    return obj


v = car("car")
groups = {c.name: c for c in v.data.collections}
member = lambda n: next((g for g in rig_style.COLLECTIONS if g in groups and n in groups[g].bones), None)
check("vehicle collections", [n for n in rig_style.COLLECTIONS if n in groups], list(rig_style.COLLECTIONS))
for n, want in (("CR_Main", "Controls"), ("CR_Drive", "Controls"), ("CR_Steer", "Controls"), ("CR_Drift", "Controls"),
                ("CR_Body", "Controls"), ("CR_Settings", "Controls"), ("CR_Wheel_wheel_disc_fr_l", "Controls"),
                ("CR_Arch_wheel_disc_fr_l", "Secondary"), ("CR_Ground_wheel_disc_fr_l", "Mechanics"),
                ("CR_Lift_wheel_disc_fr_l", "Mechanics"), ("CR_Suspension", "Mechanics"), ("CR_Lean", "Mechanics"),
                ("CR_Body_Follow", "Mechanics"), ("wheel_disc_fr_l", "Game Bones"), ("frame", "Game Bones")):
    check("%s in %s" % (n, want), member(n), want)
pose = v.pose.bones
check("left wheel blue", tuple(pose["CR_Wheel_wheel_disc_fr_l"].color.custom.normal), rig_style.COLORS["L"], tol=0.01)
check("right wheel red", tuple(pose["CR_Wheel_wheel_disc_fr_r"].color.custom.normal), rig_style.COLORS["R"], tol=0.01)
check("main orange", tuple(pose["CR_Main"].color.custom.normal), rig_style.COLORS["main"], tol=0.01)
check("main thick", pose["CR_Main"].custom_shape_wire_width, 3.5)
check("arch thin", pose["CR_Arch_wheel_disc_fr_l"].custom_shape_wire_width, 1.5)
check("wheel ring has a tick", pose["CR_Wheel_wheel_disc_fr_l"].custom_shape.name, "CR_CircleTick")
check("settings gear", pose["CR_Settings"].custom_shape.name, "CR_Gear")
check("settings defaults", tuple(pose["CR_Settings"][k] for k in ("auto_wheels", "auto_steer", "countersteer", "suspension", "lean")),
      (1.0, 1.0, 1.0, 1.0, 0.0))
check("no settings left on the object", any(k in v for k in ("auto_wheels", "lean")), False)
check("drawn in front", v.show_in_front, True)
# a setting turns its behaviour off: driving no longer spins the wheel
spin = lambda: pose["wheel_disc_fr_l"].matrix.to_euler()
bpy.context.view_layer.update()
rest = spin()
pose["CR_Drive"].location.y = 1.0
bpy.context.view_layer.update()
turned = (spin().to_matrix() @ rest.to_matrix().inverted()).to_quaternion().angle
pose["CR_Settings"]["auto_wheels"] = 0.0
v.update_tag()          # a property set from Python doesn't tag the depsgraph (the panel's slider does)
bpy.context.view_layer.update()
still = (spin().to_matrix() @ rest.to_matrix().inverted()).to_quaternion().angle
check("driving spins the wheel", turned > 0.5, True)
check("Wheels Spin 0 stops it", still < 1e-3, True)
pose["CR_Drive"].location.y = 0.0
pose["CR_Settings"]["auto_wheels"] = 1.0
# arch still carries the body's corner parts
before = (v.matrix_world @ pose["shock_up_fr_l"].head).z
pose["CR_Arch_wheel_disc_fr_l"].location = (0.0, 0.0, 0.1)    # the arch moves along its local Z
bpy.context.view_layer.update()
check("arch lifts the shock top", abs((v.matrix_world @ pose["shock_up_fr_l"].head).z - before) > 0.05, True)
pose["CR_Arch_wheel_disc_fr_l"].location = (0.0, 0.0, 0.0)
# variants: no steering, no body, wheels without an arch
for label, kwargs in (("no steering", {"steering": False}), ("no body", {"body": False}), ("bare rear", {"bare_rear": True})):
    w = car("car_" + label.replace(" ", "_"), **kwargs)
    check("%s: rig made" % label, bool(w.data.get(vehicle_rig.KEY)), True)
    check("%s: settings" % label, "auto_wheels" in w.pose.bones["CR_Settings"], True)
    unplaced = [b.name for b in w.data.bones if not any(b.name in c.bones for c in w.data.collections if c.name in rig_style.COLLECTIONS)]
    check("%s: every bone in a kit collection" % label, unplaced, [])

# panel operators
from fpmp_baseline.operator import rig_ui  # noqa: E402
rig_ui.register()
bpy.context.view_layer.objects.active = v
bpy.ops.object.mode_set(mode='POSE')
check("select controls", bpy.ops.fpmp.rig_select_controls(), {'FINISHED'})
chosen = sorted(b.name for b in v.pose.bones if b.select)
check("selected exactly the controls", chosen, sorted(b.name for b in rig_style.controls(v)))
for b in v.pose.bones:
    b.location = (0.1, 0.2, 0.3)
    b.scale = (2.0, 2.0, 2.0)
pose["CR_Main"].rotation_mode = 'QUATERNION'
pose["CR_Main"].rotation_quaternion = (0.7, 0.7, 0.0, 0.0)
pose["CR_Drive"].rotation_mode = 'AXIS_ANGLE'
pose["CR_Drive"].rotation_axis_angle = (0.5, 0.0, 0.0, 1.0)
pose["CR_Steer"].rotation_euler = (0.0, 0.4, 0.0)
check("reset all", bpy.ops.fpmp.rig_reset_pose(scope='ALL'), {'FINISHED'})
check("controls back at rest", all(tuple(b.location) == (0, 0, 0) and tuple(b.scale) == (1, 1, 1) for b in rig_style.controls(v)), True)
check("quaternion reset", tuple(pose["CR_Main"].rotation_quaternion), (1.0, 0.0, 0.0, 0.0))
check("axis-angle reset", tuple(pose["CR_Drive"].rotation_axis_angle), (0.0, 0.0, 1.0, 0.0))
check("euler reset", tuple(pose["CR_Steer"].rotation_euler), (0.0, 0.0, 0.0))
check("game bones untouched", tuple(pose["frame"].location), (0.1, 0.2, 0.3))
for b in rig_style.controls(v):
    b.location = (0.0, 0.5, 0.0)
for b in v.pose.bones:
    b.select = b.name == "CR_Drive"
bpy.ops.fpmp.rig_reset_pose(scope='SELECTED')
check("reset selected: the selected one", tuple(pose["CR_Drive"].location), (0.0, 0.0, 0.0))
check("reset selected: not the others", tuple(pose["CR_Main"].location), (0.0, 0.5, 0.0))
bpy.ops.object.mode_set(mode='OBJECT')
# a rig built before the kit: settings on the object, no settings bone
old = bpy.data.objects.new("old_rig", bpy.data.armatures.new("old_rig"))
old.data["is_vehicle_rig"] = True
old["auto_wheels"] = 1.0
owner, path = rig_style.settings_owner(old)
check("old rig's settings on the object", owner == old, True)
check("old rig's settings path", path % "auto_wheels", '["auto_wheels"]')

# review fixes
# 1. rigs not on the kit yet (creature, LEGO, older vehicles): their control groups still count
legacy = bpy.data.objects.new("legacy", bpy.data.armatures.new("legacy"))
bpy.context.scene.collection.objects.link(legacy)
bpy.context.view_layer.objects.active = legacy
bpy.ops.object.mode_set(mode='EDIT')
for n in ("paw_l", "jaw"):
    b = legacy.data.edit_bones.new(n)
    b.head, b.tail = (0, 0, 0), (0, 0.2, 0)
bpy.ops.object.mode_set(mode='POSE')
legacy.data.collections.new("Creature Controls").assign(legacy.data.bones["paw_l"])
legacy.data.collections.new("Creature Other").assign(legacy.data.bones["jaw"])
legacy.data["is_creature_rig"] = True
check("legacy rig's controls", [b.name for b in rig_style.controls(legacy)], ["paw_l"])
bpy.ops.fpmp.rig_select_controls()
check("legacy select controls", sorted(b.name for b in legacy.pose.bones if b.select), ["paw_l"])
bpy.ops.object.mode_set(mode='OBJECT')
# 2. select controls skips what the user hides
bpy.context.view_layer.objects.active = v
bpy.ops.object.mode_set(mode='POSE')
v.data.collections["Secondary"].is_visible = False
v.data.bones["CR_Drive"].hide = True
bpy.ops.fpmp.rig_select_controls()
check("hidden group not selected", pose["CR_Arch_wheel_disc_fr_l"].select, False)
check("hidden bone not selected", pose["CR_Drive"].select, False)
check("shown controls selected", pose["CR_Main"].select, True)
v.data.collections["Secondary"].is_visible = True
v.data.bones["CR_Drive"].hide = False
bpy.ops.object.mode_set(mode='OBJECT')
# 3. a wheel on the centre line (trike front) is a centre control
def trike():
    data = bpy.data.armatures.new("trike")
    obj = bpy.data.objects.new("trike", data)
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')
    edit = data.edit_bones

    def bone(n, head, tail, parent=None):
        b = edit.new(n)
        b.head, b.tail, b.parent = head, tail, parent
        return b
    root = bone("root", (0, 0, 0), (0, 0.3, 0))
    frame = bone("frame", (0, 0, 0.4), (0, 0.3, 0.4), root)
    diff = bone("front_differential", (0, 1.3, 0.35), (0, 1.4, 0.35), frame)
    pivot = bone("axle_pivot_fr", (0, 1.3, 0.35), (0, 1.3, 0.45), diff)
    steer = bone("wheel_steering_fr", (0, 1.3, 0.35), (0, 1.3, 0.45), pivot)
    bone("wheel_disc_fr", (0, 1.3, 0.35), (0.1, 1.3, 0.35), steer)
    rear = bone("rear_differential", (0, -1.3, 0.35), (0, -1.2, 0.35), frame)
    for side, x in (("l", -0.8), ("r", 0.8)):
        p = bone("axle_pivot_bk_" + side, (x * 0.8, -1.3, 0.35), (x * 0.8, -1.3, 0.45), rear)
        bone("wheel_disc_bk_" + side, (x, -1.3, 0.35), (x * 1.1, -1.3, 0.35), p)
    bpy.ops.object.mode_set(mode='OBJECT')
    vehicle_rig.create(obj)
    return obj
tr = trike()
check("centre wheel is a centre control", tuple(tr.pose.bones["CR_Wheel_wheel_disc_fr"].color.custom.normal), rig_style.COLORS["C"], tol=0.01)
# 4. the body control is a primary width (spec table), drawn in the secondary tint
check("body control width", pose["CR_Body"].custom_shape_wire_width, 2.5)
# 5. a setting's reset value is its default
check("setting default for reset", pose["CR_Settings"].id_properties_ui("auto_wheels").as_dict().get("default"), 1.0)

# kit: optional FK group
fk_rig = bpy.data.objects.new("fk_rig", bpy.data.armatures.new("fk_rig"))
bpy.context.scene.collection.objects.link(fk_rig)
fk_groups = rig_style.collections(fk_rig.data, fk=True)
check("fk order", [c.name for c in fk_rig.data.collections], ["Controls", "Secondary", "FK", "Mechanics", "Game Bones"])
check("fk hidden", fk_groups["FK"].is_visible, False)
check("vehicle has no fk", "FK" in v.data.collections, False)
arrow = rig_shapes.ensure("CR_CircleArrow")
check("circle arrow points ahead", max(p.co.y for p in arrow.data.vertices) > 1.1, True)

# the creature rig on a synthetic quadruped (facing -Y, as UE)
from fpmp_baseline.processing.context import creature_rig  # noqa: E402


def quadruped(name, eyes=True, tail_on_root=False, scapula=False):
    data = bpy.data.armatures.new(name)
    obj = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')
    edit = data.edit_bones

    def bone(n, head, tail, parent=None):
        b = edit.new(n)
        b.head, b.tail, b.parent = head, tail, parent
        return b
    root = bone("root", (0, 0, 0), (0, 0.1, 0))
    pelvis = bone("pelvis", (0, 0.4, 0.6), (0, 0.2, 0.6), root)
    spine1 = bone("spine_01", (0, 0.2, 0.6), (0, 0.0, 0.62), pelvis)
    spine2 = bone("spine_02", (0, 0.0, 0.62), (0, -0.3, 0.65), spine1)
    neck = bone("neck", (0, -0.3, 0.65), (0, -0.45, 0.8), spine2)
    head = bone("head", (0, -0.45, 0.8), (0, -0.6, 0.85), neck)
    bone("jaw", (0, -0.5, 0.75), (0, -0.65, 0.72), head)
    if eyes:
        for side, x in (("l", -0.05), ("r", 0.05)):
            bone("eye_" + side, (x, -0.58, 0.85), (x, -0.62, 0.85), head)
    for side, x in (("l", -0.12), ("r", 0.12)):
        for prefix, y, parent in (("", 0.35, pelvis), ("f", -0.2, spine2)):
            if scapula and prefix:
                parent = bone("scapula_" + side, (x * 0.5, y, 0.62), (x, y, 0.55), parent)
            thigh = bone(prefix + "thigh_" + side, (x, y, 0.55), (x, y - 0.05, 0.3), parent)
            calf = bone(prefix + "calf_" + side, (x, y - 0.05, 0.3), (x, y, 0.08), thigh)
            foot = bone(("paw_" if prefix else "foot_") + side, (x, y, 0.08), (x, y - 0.05, 0.02), calf)
            bone(prefix + "toe_" + side, (x, y - 0.05, 0.02), (x, y - 0.1, 0.0), foot)
    t1 = bone("tail_01", (0, 0.4, 0.6), (0, 0.6, 0.55), root if tail_on_root else pelvis)
    t2 = bone("tail_02", (0, 0.6, 0.55), (0, 0.8, 0.5), t1)
    bone("tail_03", (0, 0.8, 0.5), (0, 1.0, 0.45), t2)
    bpy.ops.object.mode_set(mode='OBJECT')
    creature_rig.create(obj)
    return obj


q = quadruped("quad")
qp = q.pose.bones
qg = {c.name: c for c in q.data.collections}
in_group = lambda n: next((g for g in rig_style.COLLECTIONS[:2] + (rig_style.FK,) + rig_style.COLLECTIONS[2:] if g in qg and n in qg[g].bones), None)
check("creature collections", [c.name for c in q.data.collections if c.name in qg and c.name in
      rig_style.COLLECTIONS + (rig_style.FK,)], ["Controls", "Secondary", "FK", "Mechanics", "Game Bones"])
check("creature visibility", [qg[n].is_visible for n in ("Controls", "Secondary", "FK", "Mechanics", "Game Bones")],
      [True, True, False, False, False])
for n, want in (("root", "Controls"), ("pelvis", "Controls"), ("spine_01", "Controls"), ("head", "Controls"),
                ("tail_02", "Controls"), ("CR_IK_foot_l", "Controls"), ("CR_Pole_thigh_l", "Controls"),
                ("CR_Eyes", "Controls"), ("CR_Settings", "Controls"), ("jaw", "Secondary"), ("toe_l", "Secondary"),
                ("thigh_l", "FK"), ("calf_l", "FK"), ("foot_l", "FK"), ("CR_Eye_eye_l", "Mechanics")):
    check("%s in %s" % (n, want), in_group(n), want)
check("root is main", tuple(qp["root"].color.custom.normal), rig_style.COLORS["main"], tol=0.01)
check("root shape", qp["root"].custom_shape.name, "CR_CircleArrow")
check("left foot IK blue", tuple(qp["CR_IK_foot_l"].color.custom.normal), rig_style.COLORS["L"], tol=0.01)
check("right pole red", tuple(qp["CR_Pole_thigh_r"].color.custom.normal), rig_style.COLORS["R"], tol=0.01)
check("pole is a diamond", qp["CR_Pole_thigh_r"].custom_shape.name, "CR_Diamond")
check("spine centre yellow", tuple(qp["spine_01"].color.custom.normal), rig_style.COLORS["C"], tol=0.01)
check("face thin", qp["jaw"].custom_shape_wire_width, 1.5)
check("creature settings", sorted(k for k in qp["CR_Settings"].keys() if not k.startswith("_")),
      ["eyes_aim", "ik_fthigh_l", "ik_fthigh_r", "ik_thigh_l", "ik_thigh_r"])
check("no settings left on the object", any(k.startswith("ik_") or k == "eyes_aim" for k in q.keys()), False)
check("creature drawn in front", q.show_in_front, True)
# IK at 0 lets the foot go: moving the IK target no longer moves the foot
bpy.context.view_layer.update()
rest = (q.matrix_world @ qp["foot_l"].head).copy()
qp["CR_IK_foot_l"].location = (0.0, 0.0, 0.1)
bpy.context.view_layer.update()
followed = ((q.matrix_world @ qp["foot_l"].head) - rest).length
qp["CR_Settings"]["ik_thigh_l"] = 0.0
q.update_tag()
bpy.context.view_layer.update()
left_alone = ((q.matrix_world @ qp["foot_l"].head) - rest).length
check("IK moves the foot", followed > 0.02, True)
check("IK 0 lets it go", left_alone < 1e-3, True)
qp["CR_IK_foot_l"].location = (0.0, 0.0, 0.0)
qp["CR_Settings"]["ik_thigh_l"] = 1.0
q2 = quadruped("quad_no_eyes", eyes=False)
check("no eyes: no eyes_aim", "eyes_aim" in q2.pose.bones["CR_Settings"], False)

q3 = quadruped("quad_tail_on_root", tail_on_root=True)
check("root keeps its shape when a limb hangs off it", q3.pose.bones["root"].custom_shape.name, "CR_CircleArrow")
check("root keeps its width when a limb hangs off it", q3.pose.bones["root"].custom_shape_wire_width, 3.5)

# review fixes
q4 = quadruped("quad_scapula", scapula=True)
check("shoulder blade is secondary", next((g for g in ("Controls", "Secondary") if "scapula_l" in q4.data.collections[g].bones), None), "Secondary")


def snake(name):
    """A flat chain: every bone at z=0 (a fish or snake authored on the ground)."""
    data = bpy.data.armatures.new(name)
    obj = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')
    parent = data.edit_bones.new("root")
    parent.head, parent.tail = (5.0, -50.0, 0), (5.0, -50.1, 0)
    for i, n in enumerate(("pelvis", "spine_01", "spine_02", "spine_03", "head")):
        b = data.edit_bones.new(n)
        b.head, b.tail, b.parent = (5.0, -50.1 - 0.2 * i, 0), (5.0, -50.3 - 0.2 * i, 0), parent      # off the origin: float precision
        parent = b
    bpy.ops.object.mode_set(mode='OBJECT')
    creature_rig.create(obj)
    return obj


s = snake("snake")
check("flat skeleton rigs", bool(s.data.get(creature_rig.KEY)), True)
check("flat skeleton has its gear", "CR_Settings" in s.pose.bones, True)
check("no IK: no FK group", "FK" in s.data.collections, False)
check("flat skeleton controls are visible", s.pose.bones["root"].custom_shape_scale_xyz[0] > 0.05, True)

# the LEGO figure on the kit: the shared minifigure skeleton, facing -Y (its right is -X)
from fpmp_baseline.processing.context import lego_rig  # noqa: E402


def figure(name, face=None, panel=False, material=None):
    """A LEGO figure skeleton; `face`: {input name: value} for a head mesh whose material's group node has them."""
    data = bpy.data.armatures.new(name)
    obj = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')
    edit = data.edit_bones

    def bone(n, head, tail, parent=None):
        b = edit.new(n)
        b.head, b.tail, b.parent = head, tail, parent
        return b
    root = bone("root", (0, 0, 0), (0, 0, 0.1))
    pelvis = bone("pelvis", (0, 0, 0.45), (0, 0, 0.5), root)
    for side, x in (("l", 0.1), ("r", -0.1)):
        bone("leg_" + side, (x, 0, 0.45), (x, 0, 0.05), pelvis)
    torso = bone("torso", (0, 0, 0.5), (0, 0, 0.85), pelvis)
    for side, x in (("l", 0.2), ("r", -0.2)):
        arm = bone("arm_" + side, (x, 0, 0.8), (x * 1.1, 0, 0.6), torso)
        bone("hand_" + side, (x * 1.1, 0, 0.6), (x * 1.1, -0.05, 0.5), arm)
    neck = bone("neck_accessory", (0, 0, 0.85), (0, 0, 0.9), torso)
    head = bone("head", (0, 0, 0.9), (0, 0, 1.15), neck)
    bone("head_accessory", (0, 0, 1.15), (0, 0, 1.25), head)
    bpy.ops.object.mode_set(mode='OBJECT')
    if material is not None:
        mesh = bpy.data.meshes.new(name + " head")
        mesh.from_pydata([(0, 0, 0.9), (0.1, 0, 0.9), (0, 0, 1.1)], [], [(0, 1, 2)])
        mesh.materials.append(material)
        head_obj = bpy.data.objects.new(name + " head", mesh)
        bpy.context.scene.collection.objects.link(head_obj)
        head_obj.modifiers.new("Armature", 'ARMATURE').object = obj
    elif face is not None:
        group = bpy.data.node_groups.new(name + " face", 'ShaderNodeTree')
        for key in face:
            group.interface.new_socket(key, in_out='INPUT', socket_type='NodeSocketFloat')
        group.interface.new_socket("Surface", in_out='OUTPUT', socket_type='NodeSocketShader')
        if panel:
            # the importer files a material's inputs into panels (Pre FX...)
            box = group.interface.new_panel("Pre FX")
            for item in [i for i in group.interface.items_tree if i.item_type == 'SOCKET' and i.in_out == 'INPUT'][:2]:
                group.interface.move_to_parent(item, box, 0)
        mat = bpy.data.materials.new("MP MI_Head_" + name)
        node = mat.node_tree.nodes.new("ShaderNodeGroup")
        node.node_tree = group
        for key, value in face.items():
            node.inputs[key].default_value = value
        mesh = bpy.data.meshes.new(name + " head")
        mesh.from_pydata([(0, 0, 0.9), (0.1, 0, 0.9), (0, 0, 1.1)], [], [(0, 1, 2)])
        mesh.materials.append(mat)
        head_obj = bpy.data.objects.new(name + " head", mesh)
        bpy.context.scene.collection.objects.link(head_obj)
        head_obj.modifiers.new("Armature", 'ARMATURE').object = obj
    lego_rig.create(obj)
    return obj


fig = figure("fig")
fp = fig.pose.bones
fgroups = {c.name: c for c in fig.data.collections}
member_of = lambda n: next((g for g in rig_style.COLLECTIONS if g in fgroups and n in fgroups[g].bones), None)
check("lego collections", [c.name for c in fig.data.collections if c.name in rig_style.COLLECTIONS], list(rig_style.COLLECTIONS))
for n, want in (("root", "Controls"), ("pelvis", "Controls"), ("torso", "Controls"), ("head", "Controls"),
                ("leg_l", "Controls"), ("arm_r", "Controls"), ("hand_l", "Controls"), ("head_accessory", "Secondary"),
                ("neck_accessory", "Game Bones")):
    check("lego %s in %s" % (n, want), member_of(n), want)
check("lego root main", tuple(fp["root"].color.custom.normal), rig_style.COLORS["main"], tol=0.01)
check("lego left leg blue", tuple(fp["leg_l"].color.custom.normal), rig_style.COLORS["L"], tol=0.01)
check("lego right arm red", tuple(fp["arm_r"].color.custom.normal), rig_style.COLORS["R"], tol=0.01)
check("lego leg dial ticks", fp["leg_l"].custom_shape.name, "CR_CircleTick")
check("lego torso ring", fp["torso"].custom_shape.name, "CR_Circle")
check("lego accessory box", fp["head_accessory"].custom_shape.name, "CR_Box")
check("lego accessory thin", fp["head_accessory"].custom_shape_wire_width, 1.5)
check("lego leg swings on one axis", tuple(fp["leg_l"].lock_rotation), (False, True, True))
check("lego drawn in front", fig.show_in_front, True)
check("no face material, no board", "CR_FaceBoard" in fp, False)

# the face board
from fpmp_baseline.processing.context import face_board  # noqa: E402
face_board.register()
lf = figure("faced", face={"MouthPose": 12.0, "EyeLeftPose": 0.0, "EyeRightPose": 0.0, "EyeLeftU": 0.1, "EyeLeftV": 0.02,
                           "MouthU": 0.0, "MouthV": -0.2})
lp = lf.pose.bones
mat = bpy.data.materials["MP MI_Head_faced"]
grp = next(n for n in mat.node_tree.nodes if n.type == 'GROUP')
check("board made", "CR_FaceBoard" in lp, True)
check("present sliders only", sorted(n for n in lp.keys() if n.startswith("CR_Face_") and not n.endswith("_UV")),
      ["CR_Face_EyeLeftPose", "CR_Face_EyeRightPose", "CR_Face_MouthPose"])
check("pads for present pairs", sorted(n for n in lp.keys() if n.endswith("_UV")), ["CR_Face_EyeLeft_UV", "CR_Face_Mouth_UV"])
lf.update_tag()
bpy.context.view_layer.update()
check("imported expression kept at rest", grp.inputs["MouthPose"].default_value, 12.0)
board_bone, mouth_bone = lf.data.bones["CR_FaceBoard"], lf.data.bones["CR_Face_MouthPose"]
along = (mouth_bone.head_local - board_bone.head_local).dot(board_bone.matrix_local.col[0].to_3d())
check("slider rests at its expression along the board", round(along / face_board.step_of(lf), 3), 12.0)
check("rows don't overlap", face_board.ROW >= 2.0 * face_board.SIZE, True)
step = face_board.step_of(lf)
lp["CR_Face_MouthPose"].location.x = 3 * step
lf.update_tag()
bpy.context.view_layer.update()
check("slider: 3 steps = 15", grp.inputs["MouthPose"].default_value, 15.0)
lp["CR_Face_MouthPose"].location.x = 9 * step
lf.update_tag()
bpy.context.view_layer.update()
check("slider clamps at 15", grp.inputs["MouthPose"].default_value, 15.0)
lp["CR_Face_EyeLeft_UV"].location.x = step
lf.update_tag()
bpy.context.view_layer.update()
check("pad moves the eye", abs(grp.inputs["EyeLeftU"].default_value - 0.1) > 0.05, True)
# off: the emote's keys play
grp.inputs["MouthPose"].default_value = 2.0
grp.inputs["MouthPose"].keyframe_insert("default_value", frame=1)
face_board.set_on(lf, False)
mat.node_tree.update_tag()
bpy.context.scene.frame_set(1)
check("board off: keys play", grp.inputs["MouthPose"].default_value, 2.0)
face_board.set_on(lf, True)
check("board on: drivers live", all(not fc.mute for fc in mat.node_tree.animation_data.drivers), True)
lf.fpmp_face_board = False
check("toggle off mutes", all(fc.mute for fc in mat.node_tree.animation_data.drivers), True)
lf.fpmp_face_board = True
check("toggle on unmutes", all(not fc.mute for fc in mat.node_tree.animation_data.drivers), True)
# the face material's sockets move after rigging (the importer files them into panels): drivers still find theirs
grp.node_tree.interface.new_socket("Inserted", in_out='INPUT', socket_type='NodeSocketFloat')
grp.node_tree.interface.move(grp.node_tree.interface.items_tree["Inserted"], 0)
lp["CR_Face_MouthPose"].location.x = -2 * step
lp["CR_Face_EyeLeft_UV"].location.x = 0.0
lf.update_tag()
bpy.context.scene.frame_set(2)
check("after a socket reorder the mouth slider drives MouthPose", grp.inputs["MouthPose"].default_value, 10.0)
check("after a socket reorder the eye pose is untouched", grp.inputs["EyeLeftPose"].default_value, 0.0)

pf = figure("paneled", face={"Other": 1.0, "MouthPose": 4.0, "EyeLeftPose": 0.0}, panel=True)
pgrp = next(n for n in bpy.data.materials["MP MI_Head_paneled"].node_tree.nodes if n.type == 'GROUP')
pf.pose.bones["CR_Face_MouthPose"].location.x = face_board.step_of(pf)
pf.update_tag()
bpy.context.scene.frame_set(3)
check("panelled face: mouth slider drives MouthPose", pgrp.inputs["MouthPose"].default_value, 5.0)
check("panelled face: eye untouched", pgrp.inputs["EyeLeftPose"].default_value, 0.0)

# the same figure imported twice shares its face material: the last rigged drives it, one variable per driver
shared_mat = bpy.data.materials["MP MI_Head_paneled"]
second = figure("paneled_again", material=shared_mat)
check("shared face: one variable per driver", all(len(fc.driver.variables) == 1 for fc in shared_mat.node_tree.animation_data.drivers), True)
second.pose.bones["CR_Face_MouthPose"].location.x = 2 * face_board.step_of(second)
second.update_tag()
bpy.context.scene.frame_set(4)
check("shared face follows the last rigged", pgrp.inputs["MouthPose"].default_value, 7.0)   # rests at the 5 the face showed when rigged
pf.fpmp_face_board = False
check("first figure's toggle doesn't error or mute the second's", all(not fc.mute for fc in shared_mat.node_tree.animation_data.drivers), True)

print("[rig_check] %d passed, %d failed" % (PASSES[0], len(FAILS)))
if FAILS:
    sys.exit(1)
