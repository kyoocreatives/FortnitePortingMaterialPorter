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

print("[rig_check] %d passed, %d failed" % (PASSES[0], len(FAILS)))
if FAILS:
    sys.exit(1)
