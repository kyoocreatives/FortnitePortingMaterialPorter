"""Checks the fork's rig kit and the vehicle rig on a synthetic car skeleton (no game data needed).

    blender -b --factory-startup --python-exit-code 1 -P tests/plugin/rig_check.py -- <plugin parent>

<plugin parent> holds the plugin as package fpmp_baseline (see translator_test.py). Exit code 0 when every check passes."""
import sys
from types import SimpleNamespace

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
                ("CR_Body", "Controls"), ("CR_Settings", "Mechanics"), ("CR_Wheel_wheel_disc_fr_l", "Controls"),
                ("CR_Arch_wheel_disc_fr_l", "Secondary"), ("CR_Ground_wheel_disc_fr_l", "Mechanics"),
                ("CR_Lift_wheel_disc_fr_l", "Mechanics"), ("CR_Suspension", "Mechanics"), ("CR_Lean", "Mechanics"),
                ("CR_Body_Follow", "Mechanics"), ("wheel_disc_fr_l", "Game Bones"), ("frame", "Game Bones")):
    check("%s in %s" % (n, want), member(n), want)
pose = v.pose.bones
from fpmp_baseline.processing.context import rig_kit  # noqa: E402
check("left wheel Epic blue", tuple(pose["CR_Wheel_wheel_disc_fr_l"].color.custom.normal), rig_kit.COLORS["L"], tol=0.01)
check("right wheel Epic red", tuple(pose["CR_Wheel_wheel_disc_fr_r"].color.custom.normal), rig_kit.COLORS["R"], tol=0.01)
check("main Epic yellow", tuple(pose["CR_Main"].color.custom.normal), rig_kit.COLORS["root"], tol=0.01)
check("drive, steer, drift each their colour", len({tuple(round(c, 2) for c in pose[n].color.custom.normal)
      for n in ("CR_Main", "CR_Drive", "CR_Steer", "CR_Drift")}), 4)
check("body a box", pose["CR_Body"].custom_shape.name, "OR_Box_Thick")
check("drive Epic's arrow", pose["CR_Drive"].custom_shape.name, "OR_Arrow_Thick")
check("main thick", pose["CR_Main"].custom_shape_wire_width, 3.5)
check("arch thin", pose["CR_Arch_wheel_disc_fr_l"].custom_shape_wire_width, 1.5)
check("wheel ring has a tick", pose["CR_Wheel_wheel_disc_fr_l"].custom_shape.name, "CR_CircleTick")
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
pose["CR_Drive"].hide = True
bpy.ops.fpmp.rig_select_controls()
check("hidden group not selected", pose["CR_Arch_wheel_disc_fr_l"].select, False)
check("hidden bone not selected", pose["CR_Drive"].select, False)
check("shown controls selected", pose["CR_Main"].select, True)
v.data.collections["Secondary"].is_visible = True
pose["CR_Drive"].hide = False
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
check("centre wheel is a centre control", tuple(tr.pose.bones["CR_Wheel_wheel_disc_fr"].color.custom.normal), rig_kit.COLORS["C"], tol=0.01)
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


def quadruped_bones(name, eyes=True, tail_on_root=False, scapula=False):
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
    return obj


def quadruped(name, **options):
    obj = quadruped_bones(name, **options)
    creature_rig.create(obj)
    return obj


q = quadruped("quad")
qp = q.pose.bones
qg = {c.name: c for c in q.data.collections}
from fpmp_baseline.processing.context import rig_kit  # noqa: E402
in_group = lambda n: next((g for g in rig_style.COLLECTIONS[:2] + (rig_style.FK,) + rig_style.COLLECTIONS[2:] if g in qg and n in qg[g].bones), None)
check("creature collections", [c.name for c in q.data.collections if c.name in qg and c.name in
      rig_style.COLLECTIONS + (rig_style.FK,)], ["Controls", "Secondary", "FK", "Mechanics", "Game Bones"])
for n, want in (("global_ctrl", "Controls"), ("root_ctrl", "Controls"), ("body_ctrl", "Controls"), ("pelvis_ctrl", "Controls"),
                ("spine_01_ctrl", "Controls"), ("head_ctrl", "Controls"), ("tail_02_ctrl", "Controls"),
                ("thigh_l_ik_ctrl", "Controls"), ("thigh_l_pv_ctrl", "Controls"), ("CR_Eyes", "Controls"),
                ("CR_Eye_eye_l", "Controls"), ("jaw_ctrl", "Secondary"), ("toe_l_ctrl", "Secondary"),
                ("thigh_l_ctrl", "FK"), ("calf_l_ctrl", "FK"), ("foot_l_ctrl", "FK"), ("CR_Settings", "Mechanics"),
                ("MCH_thigh_l_ik", "Mechanics"), ("thigh_l", "Game Bones"), ("pelvis", "Game Bones")):
    check("%s in %s" % (n, want), in_group(n), want)
check("global yellow", tuple(qp["global_ctrl"].color.custom.normal), rig_kit.COLORS["root"], tol=0.01)
check("global is Epic's hexagon", qp["global_ctrl"].custom_shape.name, "OR_Hexagon_Thin")
check("body is Epic's octagon", qp["body_ctrl"].custom_shape.name, "OR_Octagon_Thin")
check("foot IK is a box", qp["thigh_l_ik_ctrl"].custom_shape.name, "OR_Box_Thick")
check("pole is a sphere", qp["thigh_r_pv_ctrl"].custom_shape.name, "OR_Sphere_Solid")
check("left foot IK Epic blue", tuple(qp["thigh_l_ik_ctrl"].color.custom.normal), rig_kit.COLORS["L"], tol=0.01)
check("right pole Epic red", tuple(qp["thigh_r_pv_ctrl"].color.custom.normal), rig_kit.COLORS["R"], tol=0.01)
check("spine square", qp["spine_01_ctrl"].custom_shape.name, "OR_RoundedSquare_Thick")
check("toe an arrow", qp["toe_l_ctrl"].custom_shape.name, "OR_Arrow_Thick")
check("face thin", qp["jaw_ctrl"].custom_shape_wire_width, 1.5)
check("pole line unselectable", q.data.bones["MCH_thigh_l_pole_line"].hide_select, True)
check("creature settings", sorted(k for k in qp["CR_Settings"].keys() if not k.startswith("_")),
      ["eyes_aim", "ik_fthigh_l", "ik_fthigh_r", "ik_thigh_l", "ik_thigh_r"])
check("creature drawn in front", q.show_in_front, True)
check("FK rings hidden while IK", (qp["thigh_l_ctrl"].hide, qp["thigh_l_ik_ctrl"].hide), (True, False))


def world(obj, bone):
    return obj.matrix_world @ obj.pose.bones[bone].matrix


def game_bones(obj):
    return [b for b in obj.data.bones if not b.name.endswith("_ctrl") and not b.name.startswith(("MCH_", "CR_"))]


def rest_kept(obj):
    """Worst distance of a game bone from its rest (the rig must change nothing at rest)."""
    bpy.context.view_layer.update()
    return max(((obj.pose.bones[b.name].matrix.to_translation() - b.matrix_local.to_translation()).length
                for b in game_bones(obj)), default=0.0)


def turned(obj):
    worst = 0.0
    for b in game_bones(obj):
        diff = obj.pose.bones[b.name].matrix.to_quaternion().rotation_difference(b.matrix_local.to_quaternion()).angle
        worst = max(worst, min(diff, 6.2831853 - diff))
    return worst


check("rest kept: no bone moved", rest_kept(q) < 1e-4, True)
check("rest kept: no bone turned", turned(q) < 1e-3, True)
# IK moves the foot; IK 0 lets it go
rest = world(q, "foot_l").to_translation()
qp["thigh_l_ik_ctrl"].location = (0.0, 0.0, 0.1)
bpy.context.view_layer.update()
followed = (world(q, "foot_l").to_translation() - rest).length
qp["CR_Settings"]["ik_thigh_l"] = 0.0
q.update_tag()
bpy.context.view_layer.update()
left_alone = (world(q, "foot_l").to_translation() - rest).length
check("IK moves the foot", followed > 0.02, True)
check("IK 0 lets it go", left_alone < 1e-3, True)
qp["thigh_l_ik_ctrl"].location = (0.0, 0.0, 0.0)
qp["CR_Settings"]["ik_thigh_l"] = 1.0
q.update_tag()
# switching keeps the pose: FK bent, to IK; IK moved, to FK
rig_kit.switch(q, "thigh_l", ik=False, match=True)
qp["thigh_l_ctrl"].rotation_mode = 'XYZ'
qp["thigh_l_ctrl"].rotation_euler = (0.4, 0.1, 0.2)
qp["calf_l_ctrl"].rotation_mode = 'XYZ'
qp["calf_l_ctrl"].rotation_euler = (-0.5, 0.0, 0.0)
bpy.context.view_layer.update()
fk_foot, fk_calf = world(q, "foot_l").copy(), world(q, "calf_l").copy()
rig_kit.switch(q, "thigh_l", ik=True, match=True)
check("FK to IK keeps the foot", (world(q, "foot_l").to_translation() - fk_foot.to_translation()).length < 2e-3, True)
check("FK to IK keeps the foot's turn", world(q, "foot_l").to_quaternion().rotation_difference(fk_foot.to_quaternion()).angle < 2e-2, True)
check("FK to IK keeps the knee", (world(q, "calf_l").to_translation() - fk_calf.to_translation()).length < 5e-3, True)
qp["thigh_l_ik_ctrl"].location = qp["thigh_l_ik_ctrl"].location + Vector((0.03, 0.0, 0.05))
bpy.context.view_layer.update()
ik_foot, ik_calf = world(q, "foot_l").copy(), world(q, "calf_l").copy()
rig_kit.switch(q, "thigh_l", ik=False, match=True)
check("IK to FK keeps the foot", (world(q, "foot_l").to_translation() - ik_foot.to_translation()).length < 1e-4, True)
check("IK to FK keeps the calf", (world(q, "calf_l").to_translation() - ik_calf.to_translation()).length < 1e-4, True)
check("FK shown, IK hidden", (qp["thigh_l_ctrl"].hide, qp["thigh_l_ik_ctrl"].hide), (False, True))
# the rig off: the game bones play their own keys; Bake to Controls brings an animation onto the controls
qa = quadruped("quad_anim")
qa.animation_data_create()
qa.animation_data.action = bpy.data.actions.new("quad_anim keys")
for f, k in ((1, 0.0), (5, 1.0)):
    for n, r in (("spine_01", (0.2, 0.0, 0.1)), ("thigh_l", (0.4, 0.0, 0.0)), ("calf_l", (-0.6, 0.0, 0.0)), ("tail_02", (0.0, 0.3, 0.0)),
                 ("jaw", (0.3, 0.0, 0.0))):
        pb = qa.pose.bones[n]
        pb.rotation_mode = 'XYZ'
        pb.rotation_euler = tuple(v * k for v in r)
        pb.keyframe_insert("rotation_euler", frame=f)
rig_kit.on_animation_import(qa)
bpy.context.scene.frame_set(5)
played = {n: world(qa, n).copy() for n in ("spine_02", "foot_l", "tail_03", "jaw", "head")}
check("rig off: the animation plays", (played["foot_l"].to_translation() - (qa.matrix_world @ qa.data.bones["foot_l"].head_local)).length > 0.02, True)
rig_kit.bake_to_controls(qa, 1, 5)
bpy.context.scene.frame_set(5)
check("bake: rig on", bool(qa.data[rig_kit.ON]), True)
check("bake: every bone lands", max((world(qa, n).to_translation() - m.to_translation()).length for n, m in played.items()) < 1e-4, True)
check("bake: and turns as played", max(world(qa, n).to_quaternion().rotation_difference(m.to_quaternion()).angle
                                        for n, m in played.items()) < 1e-3, True)
check("bake: bones' keys kept in a track", [t.name for t in qa.animation_data.nla_tracks], ["RK bones"])
# the panel's operators: one click switches several limbs, keeping the pose
from fpmp_baseline.operator import rig_ui  # noqa: E402
from fpmp_baseline.processing.context import kit_panel  # noqa: E402
try:
    rig_ui.register()
except ValueError:
    pass                # registered earlier in this run
bpy.context.view_layer.objects.active = q
bpy.ops.fpmp.kit_switch(limbs="thigh_l,thigh_r", ik=True)
check("panel: one click switches both", (qp["CR_Settings"]["ik_thigh_l"], qp["CR_Settings"]["ik_thigh_r"]), (1.0, 1.0))
check("panel rows: all, front, hind, then each limb", [label for label, _ in kit_panel.groups(q)][:4], ["All", "Front Legs", "Hind Legs", "Front Leg L"])
bpy.ops.fpmp.kit_rig_on(on=False)
check("panel: rig off mutes the kit's constraints", all(c.mute for pb in qp for c in pb.constraints if c.name.startswith("RK ")), True)
bpy.ops.fpmp.kit_rig_on(on=True)
q2 = quadruped("quad_no_eyes", eyes=False)
check("no eyes: no eyes_aim", "eyes_aim" in q2.pose.bones["CR_Settings"], False)
q3 = quadruped("quad_tail_on_root", tail_on_root=True)
check("tail on the root: under the body", q3.pose.bones["tail_01_ctrl"].parent.name, "body_ctrl")
check("tail on the root: rest kept", rest_kept(q3) < 1e-4, True)
q4 = quadruped("quad_scapula", scapula=True)
check("shoulder blade a control", next((g for g in ("Controls", "Secondary") if "scapula_l_ctrl" in q4.data.collections[g].bones), None), "Controls")
check("scapula: rest kept", rest_kept(q4) < 1e-4, True)


def cow(name):
    """The LEGO cow's layout: the head off the body's branch, the pelvis and chest beside it; legs of two bones."""
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
    root = bone("root", (0, 0, 0), (0, 0, 0.6))
    body = bone("body", (0, 0, 0.67), (0, 0, 0.46), root)
    upper = bone("upper_body", (0, 0, 0.51), (0, 0.1, 0.75), body)
    neck = bone("neck", (0, -0.32, 0.51), (0, -0.4, 0.75), upper)
    bone("head", (0, -0.46, 0.75), (0, -0.6, 0.75), neck)
    bone("tail1", (0, 0.44, 0.75), (0, 0.52, 0.75), upper)
    chest = bone("chest", (0, -0.08, 0.45), (0, -0.3, 0.45), body)
    pelvis = bone("pelvis", (0, 0.08, 0.45), (0, 0.09, 0.45), body)
    for side, x in (("l", 0.16), ("r", -0.16)):
        for leg, y, parent in (("legA", -0.32, chest), ("legB", 0.32, pelvis)):
            upper_leg = bone("%s_01_%s" % (leg, side), (x, y, 0.43), (x, y, 0.19), parent)
            bone("%s_02_%s" % (leg, side), (x, y, 0.19), (x, y, 0.0), upper_leg)
    bpy.ops.object.mode_set(mode='OBJECT')
    creature_rig.create(obj)
    return obj


qc2 = cow("cow")
check("cow: spine found through the head", [n for n in ("body", "upper_body", "neck", "head") if n + "_ctrl" in qc2.pose.bones],
      ["body", "upper_body", "neck", "head"])
check("cow: two-bone legs get IK", sorted(k for k in qc2.pose.bones["CR_Settings"].keys() if k.startswith("ik_")),
      ["ik_legA_01_l", "ik_legA_01_r", "ik_legB_01_l", "ik_legB_01_r"])
check("cow: rest kept", rest_kept(qc2) < 1e-4, True)
check("cow: rest kept, no turn", turned(qc2) < 1e-3, True)
qc2.pose.bones["legB_01_l_ik_ctrl"].location = (0.0, 0.05, 0.08)
bpy.context.view_layer.update()
tip = qc2.matrix_world @ qc2.pose.bones["legB_02_l"].tail
target = qc2.matrix_world @ qc2.pose.bones["legB_01_l_ik_ctrl"].head
check("cow: the hoof reaches its IK", (tip - target).length < 2e-3, True)


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
check("flat skeleton has its settings", "CR_Settings" in s.pose.bones, True)
check("no IK: no FK group", "FK" in s.data.collections, False)
check("flat skeleton controls are visible", s.pose.bones["global_ctrl"].custom_shape_scale_xyz[0] * 0.05 > 0.05, True)

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
for n, want in (("global_ctrl", "Controls"), ("root_ctrl", "Controls"), ("pelvis_ctrl", "Controls"), ("torso_ctrl", "Controls"),
                ("head_ctrl", "Controls"), ("leg_l_ctrl", "Controls"), ("arm_r_ctrl", "Controls"), ("hand_l_ctrl", "Controls"),
                ("head_accessory_ctrl", "Secondary"), ("neck_accessory", "Game Bones"), ("leg_l", "Game Bones")):
    check("lego %s in %s" % (n, want), member_of(n), want)
check("lego global yellow", tuple(fp["global_ctrl"].color.custom.normal), rig_kit.COLORS["root"], tol=0.01)
check("lego body octagon", fp["pelvis_ctrl"].custom_shape.name, "OR_Octagon_Thin")
check("lego left leg Epic blue", tuple(fp["leg_l_ctrl"].color.custom.normal), rig_kit.COLORS["L"], tol=0.01)
check("lego right arm Epic red", tuple(fp["arm_r_ctrl"].color.custom.normal), rig_kit.COLORS["R"], tol=0.01)
check("lego leg dial", fp["leg_l_ctrl"].custom_shape.name, "OR_Circle_Thick")
check("lego torso square", fp["torso_ctrl"].custom_shape.name, "OR_RoundedSquare_Thick")
check("lego accessory box", fp["head_accessory_ctrl"].custom_shape.name, "OR_Box_Thick")
check("lego accessory thin", fp["head_accessory_ctrl"].custom_shape_wire_width, 1.5)
check("lego leg swings on one axis", tuple(fp["leg_l_ctrl"].lock_rotation), (False, True, True))
check("lego drawn in front", fig.show_in_front, True)
check("lego rest kept", rest_kept(fig) < 1e-4, True)
check("lego rest kept, no turn", turned(fig) < 1e-3, True)
# the dials sit outside their limbs, one on each side: they don't overlap
dial = lambda n: fig.matrix_world @ (fig.pose.bones[n].matrix @ Vector(fig.pose.bones[n].custom_shape_translation))
check("lego leg dials apart", (dial("leg_l_ctrl") - dial("leg_r_ctrl")).length > 0.25, True)
check("lego head ring above the shoulders", dial("head_ctrl").z > fig.data.bones["arm_l"].head_local.z, True)
fp["leg_l_ctrl"].rotation_euler = (0.5, 0.0, 0.0)
bpy.context.view_layer.update()
check("lego leg follows its control", fig.pose.bones["leg_l"].matrix.to_quaternion().rotation_difference(
      fig.data.bones["leg_l"].matrix_local.to_quaternion()).angle > 0.4, True)
fp["leg_l_ctrl"].rotation_euler = (0.0, 0.0, 0.0)
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

# flipbook faces: a creature with a flipbook head
def attach_face(obj, name, face):
    """A head mesh on `obj` whose material's group node has the `face` inputs (name: value)."""
    group = bpy.data.node_groups.new(name + " face", 'ShaderNodeTree')
    for key in face:
        group.interface.new_socket(key, in_out='INPUT', socket_type='NodeSocketFloat')
    group.interface.new_socket("Surface", in_out='OUTPUT', socket_type='NodeSocketShader')
    mat = bpy.data.materials.new("MP " + name + " face")
    node = mat.node_tree.nodes.new("ShaderNodeGroup")
    node.node_tree = group
    for key, value in face.items():
        node.inputs[key].default_value = value
    mesh = bpy.data.meshes.new(name + " head")
    mesh.from_pydata([(0, 0, 0.9), (0.1, 0, 0.9), (0, 0, 1.1)], [], [(0, 1, 2)])
    mesh.materials.append(mat)
    head = bpy.data.objects.new(name + " head", mesh)
    bpy.context.scene.collection.objects.link(head)
    head.modifiers.new("Armature", 'ARMATURE').object = obj
    return mat, node


FLIP = {"flipbook_face_L_eye_index": 0.0, "flipbook_face_R_eye_index": 0.0, "flipbook_face_mouth_index": 2.0,
        "FB_EyeColumnCount": 3.0, "FB_EyeRowCount": 3.0, "FB_EyeUVOffsetX": 0.24, "FB_EyeUVOffsetY": 0.03,
        "FB_MouthUVOffsetX": 0.0, "FB_MouthUVOffsetY": 0.0}
fl = bpy.data.objects.new("flipfig", bpy.data.armatures.new("flipfig"))
bpy.context.scene.collection.objects.link(fl)
fmat, fnode = attach_face(fl, "flipfig", FLIP)
fl_faces = face_board.faces(fl)
check("flipbook face found", [m.name for m, _ in fl_faces], ["MP flipfig face"])
check("flipbook kind", face_board.kind(fl_faces)["name"], "flipbook")
check("lego face still LEGO", face_board.kind(face_board.faces(lf))["name"], "lego")
check("flipbook counts", (face_board.count(fl_faces, "flipbook_face_l_eye_index"), face_board.count(fl_faces, "flipbook_face_mouth_index")), (9, 16))
# board on the quadruped's head, driven
qf = quadruped("quad_flip")
qmat, qnode = attach_face(qf, "quad_flip", FLIP)
creature_rig_board = face_board.add(qf, head="head")
check("board added to a rigged creature", creature_rig_board, True)
qfp = qf.pose.bones
check("flipbook sliders", sorted(n for n in qfp.keys() if n.startswith("CR_Face_") and not n.endswith("_UV")),
      ["CR_Face_EyeL", "CR_Face_EyeR", "CR_Face_Mouth"])
check("flipbook pads", sorted(n for n in qfp.keys() if n.endswith("_UV")), ["CR_Face_Eyes_UV", "CR_Face_Mouth_UV"])
fstep = face_board.step_of(qf)
qfp["CR_Face_EyeL"].location.x = 20 * fstep
qfp["CR_Face_Mouth"].location.x = 3 * fstep
qf.update_tag()
bpy.context.scene.frame_set(5)
check("eye slider stops at its count", qnode.inputs["flipbook_face_L_eye_index"].default_value, 8.0)
check("mouth slider: 2 + 3", qnode.inputs["flipbook_face_mouth_index"].default_value, 5.0)
qfp["CR_Face_Eyes_UV"].location.y = fstep
qf.update_tag()
bpy.context.scene.frame_set(6)
check("eyes pad moves the eye offset", abs(qnode.inputs["FB_EyeUVOffsetY"].default_value - 0.03) > 0.05, True)
# no head bone: the board sits above the model
nh = bpy.data.objects.new("nohead", bpy.data.armatures.new("nohead"))
bpy.context.scene.collection.objects.link(nh)
bpy.context.view_layer.objects.active = nh
bpy.ops.object.mode_set(mode='EDIT')
rb = nh.data.edit_bones.new("root")
rb.head, rb.tail = (0, 0, 0), (0, 0, 0.5)
bpy.ops.object.mode_set(mode='OBJECT')
attach_face(nh, "nohead", FLIP)
check("board without a head bone", face_board.add(nh, head=None), True)
check("board above the model", nh.data.bones["CR_FaceBoard"].head_local.z > 0.5, True)

# Tasty on the kit: a Tasty-like armature (FP's groups, shaped bones)
from fpmp_baseline.processing.context import tasty_style  # noqa: E402
ta = bpy.data.objects.new("tasty", bpy.data.armatures.new("tasty"))
bpy.context.scene.collection.objects.link(ta)
ta.data["is_tasty"] = True
bpy.context.view_layer.objects.active = ta
bpy.ops.object.mode_set(mode='EDIT')
for n, x in (("root", 0), ("ik_hand_l", 0.3), ("ik_hand_r", -0.3), ("spine_01", 0), ("R_eye", -0.03), ("dyn_hair_l", 0.05),
             ("upperarm_twist_01_l", 0.2), ("thigh_l", 0.1)):
    b = ta.data.edit_bones.new(n)
    b.head, b.tail = (x, 0, 1), (x, 0, 1.1)
bpy.ops.object.mode_set(mode='POSE')
cube = rig_shapes.ensure("CR_Box")
for g, members in (("Rig", ("root", "ik_hand_l", "ik_hand_r", "spine_01")), ("Face", ("R_eye",)), ("Dynamic", ("dyn_hair_l",)),
                   ("Twist", ("upperarm_twist_01_l",)), ("Base", ("thigh_l",)), ("Deform", ()), ("Sockets", ()), ("Extra", ())):
    c = ta.data.collections.new(g)
    for m in members:
        c.assign(ta.data.bones[m])
for n in ("root", "ik_hand_l", "ik_hand_r", "spine_01", "R_eye", "dyn_hair_l", "upperarm_twist_01_l"):
    ta.pose.bones[n].custom_shape = cube
    ta.pose.bones[n].custom_shape_wire_width = 4.0
bpy.ops.object.mode_set(mode='OBJECT')
check("tasty styled", tasty_style.style(ta), True)
check("tasty styled once", tasty_style.style(ta), False)
tp = ta.pose.bones
check("tasty visibility", {c.name: c.is_visible for c in ta.data.collections},
      {"Rig": True, "Face": True, "Dynamic": False, "Twist": False, "Base": False, "Deform": False, "Sockets": False, "Extra": False})
check("tasty root main", tuple(tp["root"].color.custom.normal), rig_style.COLORS["main"], tol=0.01)
check("tasty root width", tp["root"].custom_shape_wire_width, 3.5)
check("tasty left IK blue", tuple(tp["ik_hand_l"].color.custom.normal), rig_style.COLORS["L"], tol=0.01)
check("tasty right IK red", tuple(tp["ik_hand_r"].color.custom.normal), rig_style.COLORS["R"], tol=0.01)
check("tasty spine centre", tuple(tp["spine_01"].color.custom.normal), rig_style.COLORS["C"], tol=0.01)
check("tasty R_ prefix is right", tuple(tp["R_eye"].color.custom.normal), tuple(c + (1 - c) * 0.5 for c in rig_style.COLORS["R"]), tol=0.01)
check("tasty face thin", tp["R_eye"].custom_shape_wire_width, 1.5)
check("tasty twist keeps width", tp["upperarm_twist_01_l"].custom_shape_wire_width, 4.0)
check("tasty drawn in front", ta.show_in_front, True)
check("tasty controls", sorted(b.name for b in rig_style.controls(ta)), ["R_eye", "ik_hand_l", "ik_hand_r", "root", "spine_01"])

# creature_rig.create adds a board for a flipbook face
qb = quadruped("quad_board_on_create", eyes=False)
check("creature without a face: no board", "CR_FaceBoard" in qb.data.bones, False)
# a quadruped skeleton built, then a flipbook face attached BEFORE the rig is made
qc = quadruped_bones("quad_face_first")
attach_face(qc, "quad_face_first", FLIP)
creature_rig.create(qc)
check("creature rig adds the board", "CR_FaceBoard" in qc.data.bones, True)
# the app's face board button on an armature without a board
from fpmp_baseline.material_porter import mesh_hooks  # noqa: E402
qbtn = quadruped_bones("quad_face_button")
attach_face(qbtn, "quad_face_button", FLIP)
for o in bpy.context.view_layer.objects:
    o.select_set(False)
bpy.context.view_layer.objects.active = qbtn
qbtn.select_set(True)
check("face board button: handled", mesh_hooks.apply_rig(SimpleNamespace(options={"RigType": 1}), {"Apply": "FaceBoard"}), True)
check("face board button: the board built", "CR_FaceBoard" in qbtn.data.bones, True)
# Tasty: the hook's restyle + board, via tasty_style
tf = ta.copy(); tf.data = ta.data.copy(); bpy.context.scene.collection.objects.link(tf)
tf.data[tasty_style.MARK] = False
bpy.context.view_layer.objects.active = tf
bpy.ops.object.mode_set(mode='EDIT')
hb = tf.data.edit_bones.new("head")
hb.head, hb.tail = (0, 0, 1.5), (0, 0, 1.7)
bpy.ops.object.mode_set(mode='OBJECT')
attach_face(tf, "tasty_face", FLIP)
check("tasty board", tasty_style.add_face_board(tf), True)
check("tasty board once", tasty_style.add_face_board(tf), False)

# review fixes
# 2. an imported index past the slider's count stays as imported
over = quadruped("quad_over")
_, onode = attach_face(over, "quad_over", dict(FLIP, flipbook_face_mouth_index=20.0))
face_board.add(over, head="head")
over.update_tag()
bpy.context.scene.frame_set(7)
check("index past the count kept", onode.inputs["flipbook_face_mouth_index"].default_value, 20.0)
# 3. two face materials keep their own imported indices
two = quadruped("quad_two")
_, n1 = attach_face(two, "quad_two_a", dict(FLIP, flipbook_face_mouth_index=1.0))
_, n2 = attach_face(two, "quad_two_b", dict(FLIP, flipbook_face_mouth_index=4.0))
face_board.add(two, head="head")
two.update_tag()
bpy.context.scene.frame_set(8)
check("each face keeps its own index", (n1.inputs["flipbook_face_mouth_index"].default_value, n2.inputs["flipbook_face_mouth_index"].default_value), (1.0, 4.0))
# 6. Tasty: groups the restyle doesn't know stay as they were
tu = bpy.data.objects.new("tasty_unknown", bpy.data.armatures.new("tasty_unknown"))
bpy.context.scene.collection.objects.link(tu)
tu.data["is_tasty"] = True
tu.data.collections.new("Rig2").is_visible = True
bpy.context.view_layer.update()
tasty_style.style(tu)
check("unknown Tasty group left shown", tu.data.collections["Rig2"].is_visible, True)
# 7. side from a token in the middle of a name
check("FACIAL_L_Eye is left", tasty_style.side("FACIAL_L_Eye"), "L")
check("FACIAL_R_EyelidUpperA is right", tasty_style.side("FACIAL_R_EyelidUpperA"), "R")
check("spine_01 stays centre", tasty_style.side("spine_01"), "C")
# 1. a Tasty restyle failure is logged, never breaks the import
from fpmp_baseline.material_porter import mesh_hooks  # noqa: E402
broken = bpy.data.objects.new("tasty_broken", bpy.data.armatures.new("tasty_broken"))
bpy.context.scene.collection.objects.link(broken)
broken.data["is_tasty"] = True
bpy.context.view_layer.update()
real_add = tasty_style.add_face_board
tasty_style.add_face_board = lambda obj: 1 / 0
try:
    mesh_hooks._tasty(broken)
    survived = True
except Exception:
    survived = False
tasty_style.add_face_board = real_add
check("Tasty restyle failure doesn't break the import", survived, True)

# the flipbook board clears the head mesh, however short the head bone
clear = bpy.data.objects.new("clearfig", bpy.data.armatures.new("clearfig"))
bpy.context.scene.collection.objects.link(clear)
bpy.context.view_layer.objects.active = clear
bpy.ops.object.mode_set(mode='EDIT')
hb = clear.data.edit_bones.new("head")
hb.head, hb.tail = (0, 0, 1.0), (0, 0, 1.01)
bpy.ops.object.mode_set(mode='OBJECT')
attach_face(clear, "clearfig", FLIP)
bpy.data.objects["clearfig head"].data.vertices[1].co = (0.3, 0.0, 1.05)
face_board.add(clear, head="head")
check("the flipbook board starts past the head mesh", clear.data.bones[face_board.BOARD].head_local.x > 0.3, True)
check("a short head bone's board is sized by the head mesh", round(face_board.step_of(clear), 4),
      round(face_board.clearance(clear, "head", (1.0, 0.0, 0.0)) * face_board.STEP_OF_HEAD, 4))

# a toon face (Peely's): its material picks the mouth cell and the eye and brow pieces from the face's curves;
# sliders step through the states the game tells apart
TOON = {k: 0.0 for k in ("R_lip_corner_narrow_pose", "R_frown_pose", "R_Frown_pose", "R_smile_pose", "Jaw_open_Pose",
                         "R_blink_pose", "R_squint_inner_pose", "R_brow_up_pose", "C_glabella_up_pose", "C_glabella_down_pose",
                         "Default Face", "SubImages")}
TOON.update({"Default Face": 1.0, "SubImages": 4.0})
tf = bpy.data.objects.new("toonfig", bpy.data.armatures.new("toonfig"))
bpy.context.scene.collection.objects.link(tf)
bpy.context.view_layer.objects.active = tf
bpy.ops.object.mode_set(mode='EDIT')
hb = tf.data.edit_bones.new("head")
hb.head, hb.tail = (0, 0, 1.0), (0, 0, 1.2)
bpy.ops.object.mode_set(mode='OBJECT')
tmat, tnode = attach_face(tf, "toonfig", TOON)
check("toon kind", face_board.kind(face_board.faces(tf))["name"], "toon")
check("toon board added", face_board.add(tf, head="head"), True)
tp = tf.pose.bones
check("toon sliders", sorted(n for n in tp.keys() if n.startswith("CR_Face_")), ["CR_Face_Brows", "CR_Face_Eyes", "CR_Face_Mouth"])
tstep = face_board.step_of(tf)


def toon_values(mouth, eyes=0, brows=0):
    tp["CR_Face_Mouth"].location.x, tp["CR_Face_Eyes"].location.x, tp["CR_Face_Brows"].location.x =         mouth * tstep, eyes * tstep, brows * tstep
    tf.update_tag()
    bpy.context.view_layer.update()
    return {k: round(tnode.inputs[k].default_value) for k in TOON if k != "SubImages"}


on = lambda d: sorted(k for k, v in d.items() if v and k != "Default Face")      # noqa: E731
check("toon rest: no curve set", on(toon_values(0)), [])
check("toon rest: the imported mouth", tnode.inputs["Default Face"].default_value, 1.0)
check("toon mouth steps through every cell of the sheet", round(toon_values(9)["Default Face"]), 10)
check("toon mouth stops at the sheet's last cell", round(toon_values(40)["Default Face"]), 15)
check("toon blink and angry brows", on(toon_values(0, 1, 3)), ["C_glabella_down_pose", "R_blink_pose"])
check("toon eyes and brows stop at their last state", on(toon_values(0, 9, 9)), ["C_glabella_down_pose", "R_squint_inner_pose"])

# a flipbook face whose pupils follow the look curves (Peabody's): a Look pad, the mouth sheet's own size
PEAS = {"flipbook_face_mouth_index": 0.0, "Mouth_SubimagesX": 4.0, "Mouth_SubimagesY": 2.0,
        "look_left_pose": 0.0, "look_right_pose": 0.0, "look_up_pose": 0.0, "look_down_pose": 0.0}
pf = bpy.data.objects.new("peasfig", bpy.data.armatures.new("peasfig"))
bpy.context.scene.collection.objects.link(pf)
bpy.context.view_layer.objects.active = pf
bpy.ops.object.mode_set(mode='EDIT')
hb = pf.data.edit_bones.new("head")
hb.head, hb.tail = (0, 0, 1.0), (0, 0, 1.2)
bpy.ops.object.mode_set(mode='OBJECT')
pmat, pnode = attach_face(pf, "peasfig", PEAS)
check("the mouth sheet's own size", face_board.count(face_board.faces(pf), "flipbook_face_mouth_index"), 8)
face_board.add(pf, head="head")
check("peas controls", sorted(n for n in pf.pose.bones.keys() if n.startswith("CR_Face_")), ["CR_Face_Look", "CR_Face_Mouth"])
pstep = face_board.step_of(pf)


def look(x, y):
    pf.pose.bones["CR_Face_Look"].location.x, pf.pose.bones["CR_Face_Look"].location.y = x * pstep, y * pstep
    pf.update_tag()
    bpy.context.view_layer.update()
    return {k[5:-5]: round(pnode.inputs[k].default_value, 2) for k in PEAS if k.startswith("look_")}


check("look rest", look(0, 0), {"left": 0.0, "right": 0.0, "up": 0.0, "down": 0.0})
check("look left and up", look(1, 0.5), {"left": 1.0, "right": 0.0, "up": 0.5, "down": 0.0})
check("look right and down, held at full", look(-3, -1), {"left": 0.0, "right": 1.0, "up": 0.0, "down": 1.0})

print("[rig_check] %d passed, %d failed" % (PASSES[0], len(FAILS)))
if FAILS:
    sys.exit(1)
