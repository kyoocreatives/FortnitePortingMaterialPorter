"""Checks the official Fortnite rig (FN_Mannequin_ControlRig rebuilt with constraints) on a synthetic mannequin.

    blender -b --factory-startup --python-exit-code 1 -P tests/plugin/official_rig_check.py -- <plugin parent>

<plugin parent> holds the plugin as package fpmp_baseline (see translator_test.py). Exit code 0 when every check passes."""
import sys
from types import SimpleNamespace

import bpy
from mathutils import Matrix, Vector

sys.path.insert(0, sys.argv[sys.argv.index("--") + 1])
import fpmp_baseline  # noqa: E402,F401
from fpmp_baseline.processing.context import official_rig  # noqa: E402

FAILS, PASSES = [], [0]


def check(name, got, want, tol=1e-3):
    ok = all(abs(g - w) <= tol for g, w in zip(got, want)) if isinstance(want, tuple) else \
        (abs(got - want) <= tol if isinstance(want, float) else got == want)
    if ok:
        PASSES[0] += 1
    else:
        FAILS.append(name)
        print("[official_rig_check] FAIL %s: got %r, want %r" % (name, got, want))


# where the main bones sit (metres, facing -Y; knees and elbows slightly bent so IK has a plane)
PLACES = {"root": (0, 0, 0), "pelvis": (0, 0, 1.0), "spine_01": (0, 0, 1.08), "spine_02": (0, 0, 1.16), "spine_03": (0, 0, 1.24),
          "spine_04": (0, 0, 1.32), "spine_05": (0, 0, 1.40), "neck_01": (0, 0, 1.50), "neck_02": (0, 0, 1.55), "head": (0, 0, 1.62),
          "thigh_l": (0.1, 0, 0.95), "calf_l": (0.1, -0.03, 0.52), "foot_l": (0.1, 0, 0.1), "ball_l": (0.1, -0.12, 0.02),
          "clavicle_l": (0.04, 0, 1.42), "upperarm_l": (0.18, 0, 1.42), "lowerarm_l": (0.46, 0.03, 1.42), "hand_l": (0.72, 0, 1.42)}


def place(name):
    if name in PLACES:
        return Vector(PLACES[name])
    if name.endswith("_r") and name[:-2] + "_l" in PLACES:
        x, y, z = PLACES[name[:-2] + "_l"]
        return Vector((-x, y, z))
    return None


def mannequin(name="mannequin", drop=(), fp_tails=False):
    """An armature with the data's skeleton; bones without a place sit 3 cm past their parent. `drop`: left out."""
    data = official_rig.load()
    obj = bpy.data.objects.new(name, bpy.data.armatures.new(name))
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')
    edit = obj.data.edit_bones
    for b in data["skeleton"]:
        if b["name"] in drop or b["name"] in edit or (b["parent"] and b["parent"] not in edit):
            continue
        bone = edit.new(b["name"])
        parent = edit.get(b["parent"]) if b["parent"] else None
        bone.head = place(b["name"]) or ((parent.head if parent else Vector((0, 0, 0))) + Vector((0, 0, 0.03)))
        bone.tail = bone.head + Vector((0, 0, 0.05))
        bone.parent = parent
    for b in ([] if fp_tails else edit):      # chains point at their child (FP's own tails don't: fp_tails)
        kids = [k for k in b.children if place(k.name) is not None]
        if kids and (kids[0].head - b.head).length > 1e-4:
            b.tail = kids[0].head
    bpy.ops.object.mode_set(mode='OBJECT')
    return obj


def follows(obj, bone, control):
    """The bone sits where its control's follower is: the control's pose times its rest offset to the bone."""
    b = obj.data.bones
    want = world(obj, control) @ b[control].matrix_local.inverted() @ b[bone].matrix_local
    return same_rot(world(obj, bone), want)


data = official_rig.load()
man = mannequin()
check("fits a mannequin", official_rig.fits(man), True)
summary = official_rig.create(man)
check("every control built", {c["name"] for c in data["controls"]} <= set(man.pose.bones.keys()), True)
check("every space built", {s["name"] for s in data["spaces"]} <= set(man.pose.bones.keys()), True)
fc = next(c for c in data["controls"] if c["name"] == "foot_l_ik_ctrl")
pb = man.pose.bones["foot_l_ik_ctrl"]
check("Epic's shape", pb.custom_shape.name, "OR_" + fc["shape"])
check("Epic's colour", tuple(pb.color.custom.normal), tuple(fc["color"]), tol=0.01)
check("on its bone", tuple(man.data.bones["foot_l_ik_ctrl"].head_local), tuple(man.data.bones["foot_l"].head_local))
check("at Epic's frame (world-aligned IK foot)", max(abs(x) for r in (man.data.bones["foot_l_ik_ctrl"].matrix_local.to_3x3()
                                                          - Matrix.Identity(3)) for x in r) < 1e-3, True)
check("parent as the data", man.data.bones["foot_l_ik_ctrl"].parent.name, fc["parent"])
heel = next(c for c in data["controls"] if c["name"] == "heel_l_ctrl")
ratio = man.data.bones["head"].head_local.z / data["height"]
check("off-bone control at its offset", tuple(man.data.bones["heel_l_ctrl"].head_local),
      tuple(man.data.bones[heel["bone"]].head_local + Vector(heel["offset"]) * ratio))
knee, hip, ankle = (man.data.bones[b].head_local for b in ("calf_l", "thigh_l", "foot_l"))
plane = (knee - hip).cross(ankle - hip)
check("leg pole in the leg's bend plane", abs((man.data.bones["leg_l_pv_ik_ctrl"].head_local - hip).dot(plane.normalized())) < 1e-4, True)
check("root width", man.pose.bones["global_ctrl"].custom_shape_wire_width, 7.0)
check("digit width", man.pose.bones["index_01_l_ctrl"].custom_shape_wire_width, 3.0)


def widget(obj, name):
    """A control's drawn shape in the world: its points."""
    p = obj.pose.bones[name]
    m = obj.matrix_world @ p.matrix @ Matrix.LocRotScale(p.custom_shape_translation, p.custom_shape_rotation_euler.to_quaternion(),
                                                        p.custom_shape_scale_xyz)
    return [m @ v.co for v in p.custom_shape.data.vertices] if p.custom_shape else []


bpy.context.view_layer.update()
root_pts = widget(man, "root_ctrl")
# the mannequin faces -Y: the arrow's front-most point is its tip (one point, on the middle line), not the shaft's end
check("the root arrow points forward", abs(min(root_pts, key=lambda v: v.y).x) < 1e-4, True)
unmirrored = []
for name in man.pose.bones.keys():
    twin = name.replace("_l_", "_r_") if "_l_" in name else name[:-2] + "_r" if name.endswith("_l") else None
    if twin and twin in man.pose.bones and widget(man, name):
        right = widget(man, twin)
        if max(min((Vector((-v.x, v.y, v.z)) - w).length for w in right) for v in widget(man, name)) > 1e-3:
            unmirrored.append(name)
check("every widget mirrors its other side", unmirrored, [])
check("controls collection", "foot_l_ik_ctrl" in man.data.collections["Controls"].bones, True)
check("spaces in Mechanics", "chest_space" in man.data.collections["Mechanics"].bones, True)
check("game bones hidden", man.data.collections["Game Bones"].is_visible, False)
check("a switch is a property (legs start in IK, Epic's default)", man.pose.bones["leg_l_fk_ik_switch"].get("leg_l_fk_ik_switch"), 1.0)
check("marked", (man.data.get("fpmp_official_rig"), man.data.get("fpmp_official_rig_on")), (True, True))
n_bones = len(man.pose.bones)
check("a second build adds nothing", [official_rig.create(man), len(man.pose.bones)], ["already built", n_bones])
# a skeleton missing some bones: their controls are skipped, the rest builds (Review Focus 1)
small = mannequin("small", drop=("index_01_l", "index_02_l", "index_03_l"))
official_rig.create(small)
check("missing bones skip their controls", ("index_01_l_ctrl" in small.pose.bones, "foot_l_ik_ctrl" in small.pose.bones), (False, True))
blob = bpy.data.objects.new("blob", bpy.data.armatures.new("blob"))
check("a non-humanoid doesn't fit", official_rig.fits(blob), False)

# the import hook: builds on a fitting skeleton, logs a failure, leaves creatures to their rigs
from fpmp_baseline.material_porter import mesh_hooks  # noqa: E402
from fpmp_baseline.processing.enums import EExportType, ERigType  # noqa: E402
hooked = mannequin("hooked")
mesh_hooks._official(SimpleNamespace(imported_meshes=[{"Skeleton": hooked, "Mesh": None}], type=EExportType.OUTFIT, options={}))
check("the hook builds the rig", bool(hooked.data.get("fpmp_official_rig")), True)
check("no hand controls unless asked", "fingers_l_ctrl" in hooked.pose.bones, False)
hooked_hands = mannequin("hooked_hands")
mesh_hooks._official(SimpleNamespace(imported_meshes=[{"Skeleton": hooked_hands, "Mesh": None}], type=EExportType.OUTFIT,
                                     options={"OfficialRigHandControls": True}))
check("the app option adds the hand controls", "fingers_l_ctrl" in hooked_hands.pose.bones, True)
broken = mannequin("broken")
real = official_rig.create
official_rig.create = lambda obj, data=None: 1 / 0
try:
    mesh_hooks._official(SimpleNamespace(imported_meshes=[{"Skeleton": broken, "Mesh": None}], type=EExportType.OUTFIT, options={}))
    survived = True
except Exception:
    survived = False
official_rig.create = real
check("a rig failure doesn't break the import", [survived, bpy.context.mode], [True, "OBJECT"])


def quadruped_bones(name):
    obj = bpy.data.objects.new(name, bpy.data.armatures.new(name))
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')
    edit = obj.data.edit_bones

    def bone(n, head, tail, parent=None):
        b = edit.new(n)
        b.head, b.tail, b.parent = head, tail, parent
        return b
    root = bone("root", (0, 0, 0), (0, 0.1, 0))
    pelvis = bone("pelvis", (0, 0.4, 0.6), (0, 0.2, 0.6), root)
    spine1 = bone("spine_01", (0, 0.2, 0.6), (0, 0.0, 0.62), pelvis)
    spine2 = bone("spine_02", (0, 0.0, 0.62), (0, -0.3, 0.65), spine1)
    neck = bone("neck", (0, -0.3, 0.65), (0, -0.45, 0.8), spine2)
    bone("head", (0, -0.45, 0.8), (0, -0.6, 0.85), neck)
    for side, x in (("l", -0.12), ("r", 0.12)):
        for prefix, y, parent in (("", 0.35, pelvis), ("f", -0.2, spine2)):
            thigh = bone(prefix + "thigh_" + side, (x, y, 0.55), (x, y - 0.05, 0.3), parent)
            calf = bone(prefix + "calf_" + side, (x, y - 0.05, 0.3), (x, y, 0.08), thigh)
            foot = bone(("paw_" if prefix else "foot_") + side, (x, y, 0.08), (x, y - 0.05, 0.02), calf)
            bone(prefix + "toe_" + side, (x, y - 0.05, 0.02), (x, y - 0.1, 0.0), foot)
    bpy.ops.object.mode_set(mode='OBJECT')
    return obj


quad = quadruped_bones("quad_official")
ctx = SimpleNamespace(imported_meshes=[{"Skeleton": quad, "Mesh": None}], type=EExportType.WILDLIFE, mp_deferred_effects=None)
mesh_hooks.after_parts(ctx, ERigType.OFFICIAL, ERigType.TASTY)
check("Official still rigs a creature (Review Focus 5)", bool(quad.data.get("is_creature_rig")), True)
check("Official doesn't put the body rig on a creature", bool(quad.data.get("fpmp_official_rig")), False)

# --- solving
def world(obj, bone):
    bpy.context.view_layer.update()
    return obj.matrix_world @ obj.pose.bones[bone].matrix


def head(obj, bone):
    return world(obj, bone).to_translation()


def same_rot(a, b, tol=1e-2):
    return a.to_quaternion().rotation_difference(b.to_quaternion()).angle < tol


rig = mannequin("solve")
official_rig.create(rig)
pb = rig.pose.bones
check("constraints named OR", all(c.name.startswith(official_rig.PREFIX) for b in pb for c in b.constraints), True)
# FK
official_rig.set_switch(rig, "arm_l_fk_ik_switch", ik=False)
pb["upperarm_l_fk_ctrl"].rotation_mode = 'XYZ'
pb["upperarm_l_fk_ctrl"].rotation_euler = (0.0, 0.0, 0.6)
check("FK control turns its bone", follows(rig, "upperarm_l", "upperarm_l_fk_ctrl"), True)
check("FK turns the bone at all", same_rot(world(rig, "upperarm_l"), rig.data.bones["upperarm_l"].matrix_local), False)
pb["upperarm_l_fk_ctrl"].rotation_euler = (0.0, 0.0, 0.0)
# IK with the reverse foot
official_rig.set_switch(rig, "leg_l_fk_ik_switch", ik=True)
eff = head(rig, "foot_l_ik_ctrl").copy()
pb["foot_l_ik_ctrl"].location = (0.0, 0.0, 0.0)
goal = eff + Vector((0.0, -0.1, 0.15))
pb["foot_l_ik_ctrl"].matrix = Matrix.Translation(goal) @ Matrix.Translation(-eff) @ pb["foot_l_ik_ctrl"].matrix
check("IK foot reaches its control", tuple(head(rig, "foot_l")), tuple(goal), tol=3e-3)
knee, hip = head(rig, "calf_l"), head(rig, "thigh_l")
check("knee bends toward the pole", (knee - (hip + head(rig, "foot_l")) / 2).dot(head(rig, "leg_l_pv_ik_ctrl") - (hip + head(rig, "foot_l")) / 2) > 0, True)
# switch back to FK: the limb stays where IK left it, and the IK controls hide
thigh_before = world(rig, "thigh_l").copy()
official_rig.set_switch(rig, "leg_l_fk_ik_switch", ik=False)
check("switching keeps the limb (Review Focus 4)", (world(rig, "thigh_l").to_translation() - thigh_before.to_translation()).length < 1e-3
      and same_rot(world(rig, "thigh_l"), thigh_before), True)
check("inactive IK controls hide", rig.pose.bones["foot_l_ik_ctrl"].hide, True)
check("active FK controls show", rig.pose.bones["thigh_l_fk_ctrl"].hide, False)
# spaces follow their result bone: the chest space rides the spine top
before = head(rig, "clavicle_l_ctrl").copy()
official_rig.set_switch(rig, "spine_fk_ik_switch", ik=False)
pb["spine_01_ctrl"].rotation_mode = 'XYZ'
pb["spine_01_ctrl"].rotation_euler = (0.3, 0.0, 0.0)
check("chest space follows the spine", (head(rig, "clavicle_l_ctrl") - before).length > 0.02, True)
pb["spine_01_ctrl"].rotation_euler = (0.0, 0.0, 0.0)
# spine IK: the chest control pulls the top of the spine
official_rig.set_switch(rig, "spine_fk_ik_switch", ik=True)
top = head(rig, "spine_05").copy()
pb["chest_ctrl"].location = (0.0, 0.0, 0.0)
pb["chest_ctrl"].matrix = Matrix.Translation((0.06, 0.0, 0.0)) @ pb["chest_ctrl"].matrix
check("spine IK follows the chest", head(rig, "spine_05").x - top.x > 0.02, True)
# head IK: the head turns with its IK control
official_rig.set_switch(rig, "neck_fk_ik_switch", ik=True)
pb["head_ik_ctrl"].rotation_mode = 'XYZ'
pb["head_ik_ctrl"].rotation_euler = (0.0, 0.0, 0.5)
check("head IK turns the head", follows(rig, "head", "head_ik_ctrl"), True)
# fingers copy their controls
pb["index_01_l_ctrl"].rotation_mode = 'XYZ'
pb["index_01_l_ctrl"].rotation_euler = (0.4, 0.0, 0.0)
check("finger follows its control", follows(rig, "index_01_l", "index_01_l_ctrl"), True)

# Epic's limits: locked channels, float ranges
check("hips tangent rotation locked", tuple(rig.pose.bones["hips_tan_ctrl"].lock_rotation), (True, True, True))
check("softness ranges 0 to 2", rig.pose.bones["leg_l_softness"].id_properties_ui("leg_l_softness").as_dict()["max"], 2.0)

# Rig on/off, the panel's control set, emotes
off = mannequin("onoff")
official_rig.create(off)
ours = lambda o: [c for b in o.pose.bones for c in b.constraints if c.name.startswith(official_rig.PREFIX)]
official_rig.set_on(off, False)
check("rig off mutes every OR constraint", all(c.mute for c in ours(off)), True)
check("rig off is marked", off.data["fpmp_official_rig_on"], False)
official_rig.set_on(off, True)
check("rig on unmutes", any(c.mute for c in ours(off)), False)
from fpmp_baseline.processing.context import rig_style  # noqa: E402
check("Select Controls finds the official controls", "foot_l_ik_ctrl" in {p.name for p in rig_style.controls(off)}, True)
official_rig.on_animation_import(off)
check("an emote switches the rig off", off.data["fpmp_official_rig_on"], False)
official_rig.on_animation_import(blob)      # not an official rig: nothing
check("other armatures untouched", "fpmp_official_rig_on" in blob.data, False)

# Bake to Controls: an animation on the bones lands on the controls
bk = mannequin("bake")
official_rig.create(bk)
official_rig.on_animation_import(bk)        # as an emote import does
bk.animation_data_create()
pb = bk.pose.bones
for f, angle in ((1, 0.0), (5, 0.5)):
    for name in ("upperarm_l", "thigh_l", "spine_02", "index_01_l"):
        pb[name].rotation_mode = 'XYZ'
        pb[name].rotation_euler = (angle, 0.0, angle * 0.5)
        pb[name].keyframe_insert("rotation_euler", frame=f)
    pb["spine_02"].scale = (1.0 + angle * 0.1,) * 3     # emotes scale bones slightly
    pb["spine_02"].keyframe_insert("scale", frame=f)
bpy.context.scene.frame_set(1)
first = {n: world(bk, n).copy() for n in ("spine_03", "clavicle_l")}
bpy.context.scene.frame_set(5)
keyed = {n: world(bk, n).copy() for n in ("upperarm_l", "thigh_l", "spine_02", "hand_l", "foot_l", "index_01_l", "clavicle_l")}
check("bake covers the range", official_rig.bake_to_controls(bk, 1, 5), 5)
check("rig on after the bake", bk.data["fpmp_official_rig_on"], True)
bpy.context.scene.frame_set(1)
bpy.context.scene.frame_set(5)
for n, m in keyed.items():
    check("baked %s lands" % n, (world(bk, n).to_translation() - m.to_translation()).length < 2e-3 and same_rot(world(bk, n), m, 2e-2), True)
bpy.context.scene.frame_set(1)
check("scale baked too: the first frame lands", all((world(bk, n).to_translation() - m.to_translation()).length < 2e-3 for n, m in first.items()), True)
check("controls keyed", bk.animation_data.action is not None and any("foot_l_fk_ctrl" in fc.data_path for fc in
      [fc for layer in bk.animation_data.action.layers for strip in layer.strips for bag in strip.channelbags for fc in bag.fcurves]), True)
check("bones' own keys kept under the controls", [t.name for t in bk.animation_data.nla_tracks if not t.mute], ["OR bones"])
check("switches set to FK", bk.pose.bones["leg_l_fk_ik_switch"]["leg_l_fk_ik_switch"], 0.0)
official_rig.on_animation_import(bk)        # a second emote after a bake (Review Focus 3)
check("a second emote switches it off again", bk.data["fpmp_official_rig_on"], False)

# IK at rest keeps every limb at rest (the poles sit in each limb's own bend plane), with FP's bone tails too
limbs = {"arm_l_fk_ik_switch": ("upperarm_l", "lowerarm_l", "hand_l"), "arm_r_fk_ik_switch": ("upperarm_r", "lowerarm_r", "hand_r"),
         "leg_l_fk_ik_switch": ("thigh_l", "calf_l", "foot_l"), "leg_r_fk_ik_switch": ("thigh_r", "calf_r", "foot_r")}
for rest_ik in (mannequin("rest_ik"), mannequin("rest_ik_fp", fp_tails=True)):
    official_rig.create(rest_ik)
    for switch, bones in limbs.items():
        before = {b: world(rest_ik, b).copy() for b in bones}
        for match in (False, True):
            official_rig.set_switch(rest_ik, switch, ik=True, match=match)
            check("%s %s IK at rest keeps the limb (match %s)" % (rest_ik.name, switch, match),
                  all((world(rest_ik, b).to_translation() - m.to_translation()).length < 1e-3 and same_rot(world(rest_ik, b), m, 1e-2)
                      for b, m in before.items()), True)
            official_rig.set_switch(rest_ik, switch, ik=False, match=False)

# --- review fixes
# 1. an outfit imported with its lobby pose keeps it: the rig starts off when the bones are already animated
posed = mannequin("posed")
posed.animation_data_create()
posed.pose.bones["upperarm_l"].rotation_mode = 'XYZ'
posed.pose.bones["upperarm_l"].rotation_euler = (0.0, 0.0, 0.5)
posed.pose.bones["upperarm_l"].keyframe_insert("rotation_euler", frame=1)
official_rig.create(posed)
check("an animated skeleton gets the rig off", posed.data["fpmp_official_rig_on"], False)
# 2. spine IK at rest leaves the spine at rest
sp = mannequin("spine_rest")
official_rig.create(sp)
rest = {n: world(sp, n).copy() for n in ("spine_01", "spine_03", "spine_05")}
official_rig.set_switch(sp, "spine_fk_ik_switch", ik=True, match=False)
check("spine IK at rest keeps the spine", all((world(sp, n).to_translation() - m.to_translation()).length < 2e-3 and same_rot(world(sp, n), m, 2e-2)
                                               for n, m in rest.items()), True)
# 3. the reverse-foot pivots work, and hide with the IK controls
ft = mannequin("foot")
official_rig.create(ft)
official_rig.set_switch(ft, "leg_l_fk_ik_switch", ik=True)
before = head(ft, "foot_l").copy()
ft.pose.bones["heel_l_ctrl"].rotation_mode = 'XYZ'
ft.pose.bones["heel_l_ctrl"].rotation_euler = (0.4, 0.0, 0.0)
check("heel pivot moves the foot", (head(ft, "foot_l") - before).length > 0.005, True)
ft.pose.bones["heel_l_ctrl"].rotation_euler = (0.0, 0.0, 0.0)
official_rig.set_switch(ft, "leg_l_fk_ik_switch", ik=False)
check("reverse-foot controls hide in FK", (ft.pose.bones["heel_l_ctrl"].hide, ft.pose.bones["tip_l_ctrl"].hide), (True, True))
# 4. FK to IK keeps a posed limb, ball included; spine and neck too
mt = mannequin("match")
official_rig.create(mt)
mp = mt.pose.bones
for n, r in (("thigh_l_fk_ctrl", (0.3, 0.0, 0.1)), ("calf_l_fk_ctrl", (-0.6, 0.0, 0.0)), ("ball_l_fk_ctrl", (0.4, 0.0, 0.0)),
             ("spine_01_ctrl", (0.2, 0.0, 0.0)), ("spine_02_ctrl", (0.2, 0.0, 0.0)), ("neck_01_ctrl", (0.3, 0.0, 0.0))):
    mp[n].rotation_mode = 'XYZ'
    mp[n].rotation_euler = r
posed_bones = {n: world(mt, n).copy() for n in ("foot_l", "ball_l", "spine_05", "head")}
official_rig.set_switch(mt, "leg_l_fk_ik_switch", ik=True)
official_rig.set_switch(mt, "neck_fk_ik_switch", ik=True)
for n in ("foot_l", "ball_l", "head"):
    check("FK to IK keeps %s" % n, (world(mt, n).to_translation() - posed_bones[n].to_translation()).length < 2e-3
          and same_rot(world(mt, n), posed_bones[n], 2e-2), True)
official_rig.set_switch(mt, "spine_fk_ik_switch", ik=True)
check("FK to IK keeps the spine top close", (world(mt, "spine_05").to_translation() - posed_bones["spine_05"].to_translation()).length < 0.02, True)
# 5. the hips stay posable in FK
official_rig.set_switch(mt, "spine_fk_ik_switch", ik=False)
check("hips control shows in FK", mt.pose.bones["hips_ctrl"].hide, False)

# --- second round (user report: switching moves the body, spine IK misbehaves)
import math  # noqa: E402
from mathutils import Quaternion  # noqa: E402


def turn(obj, control, axis, angle):
    """Turn a control by `angle` around a world axis through its head."""
    pbone = obj.pose.bones[control]
    bpy.context.view_layer.update()
    pivot = pbone.matrix.to_translation()
    pbone.matrix = Matrix.Translation(pivot) @ Quaternion(axis, angle).to_matrix().to_4x4() @ Matrix.Translation(-pivot) @ pbone.matrix
    bpy.context.view_layer.update()


def yaw(obj, bone, rest):
    """How far a bone turned around world Z from `rest` (radians, signed)."""
    axis, angle = (world(obj, bone).to_quaternion() @ rest.to_quaternion().inverted()).to_axis_angle()
    angle = angle if angle <= math.pi else angle - 2 * math.pi
    return angle * (1.0 if axis.z >= 0 else -1.0)


# 1. a limb posed the way a joint bends (twisted root, mid bent on its hinge) switches to IK without moving, FP tails too
for obj in (mannequin("bend"), mannequin("bend_fp", fp_tails=True)):
    official_rig.create(obj)
    for switch, (r, m, e) in limbs.items():
        bones = obj.data.bones
        hinge = (bones[m].head_local - bones[r].head_local).cross(bones[e].head_local - bones[m].head_local)
        turn(obj, r + "_fk_ctrl", (bones[m].head_local - bones[r].head_local).normalized(), 0.4)
        bpy.context.view_layer.update()
        turn(obj, m + "_fk_ctrl", (obj.pose.bones[r].matrix.to_3x3() @ bones[r].matrix_local.to_3x3().inverted() @ hinge).normalized(), 0.5)
        posed_limb = {b: world(obj, b).copy() for b in (r, m, e)}
        official_rig.set_switch(obj, switch, ik=True)
        check("%s %s posed FK to IK keeps the limb" % (obj.name, switch),
              all((world(obj, b).to_translation() - w.to_translation()).length < 2e-3 and same_rot(world(obj, b), w, 2e-2)
                  for b, w in posed_limb.items()), True)
        official_rig.set_switch(obj, switch, ik=False)
# 2. spine IK: the hips' turn fades up the spine, the chest's turn grows up to it, raising the chest bends nothing hard
for obj in (mannequin("spine_ik"), mannequin("spine_ik_fp", fp_tails=True)):
    official_rig.create(obj)
    official_rig.set_switch(obj, "spine_fk_ik_switch", ik=True, match=False)
    chain = ["spine_01", "spine_02", "spine_03", "spine_04", "spine_05"]
    rest = {b: world(obj, b).copy() for b in chain}
    for control, label, rising in (("hips_ctrl", "hips", False), ("chest_ctrl", "chest", True)):
        turn(obj, control, Vector((0, 0, 1)), 0.5)
        turns = [yaw(obj, b, rest[b]) for b in chain]
        steps = [b - a for a, b in zip(turns, turns[1:])]
        check("%s: the %s turn spreads evenly up the spine %s" % (obj.name, label, [round(t, 2) for t in turns]),
              all(s >= -1e-3 for s in steps) if rising else all(s <= 1e-3 for s in steps), True)
        if rising:
            check("%s: the spine top turns with the chest" % obj.name, round(turns[-1], 2), 0.5, tol=0.03)
        turn(obj, control, Vector((0, 0, 1)), -0.5)
    obj.pose.bones["chest_ctrl"].location = (0.0, 0.0, 0.0)
    bpy.context.view_layer.update()
    pivot = obj.pose.bones["chest_ctrl"].matrix.to_translation()
    obj.pose.bones["chest_ctrl"].matrix = Matrix.Translation(Vector((0.08, 0.0, 0.05))) @ obj.pose.bones["chest_ctrl"].matrix
    bpy.context.view_layer.update()
    bends = [math.degrees(rest[b].to_quaternion().rotation_difference(world(obj, b).to_quaternion()).angle) for b in chain]
    check("%s: moving the chest bends the spine smoothly %s" % (obj.name, [round(b) for b in bends]),
          max(bends) < 25.0 and all(abs(b - a) < 10.0 for a, b in zip(bends, bends[1:])), True)
    check("%s: the spine leans toward the moved chest" % obj.name, head(obj, "spine_05").x - rest["spine_05"].to_translation().x > 0.03, True)

# --- exact frames (spec 2026-10-10-official-rig-exact)
ex = mannequin("exact_fp", fp_tails=True)
official_rig.create(ex)
W = Matrix.Identity(3)
FLIPX = Matrix(((-1, 0, 0), (0, 1, 0), (0, 0, 1)))


def rest3(obj, name):
    return obj.data.bones[name].matrix_local.to_3x3().normalized()


def close3(a, b, tol=1e-3):
    return max(abs(x) for r in (a - b) for x in r) < tol


check("IK foot control world-aligned (Epic's frame)", close3(rest3(ex, "foot_l_ik_ctrl"), W), True)
check("hips and chest controls world-aligned", (close3(rest3(ex, "hips_ctrl"), W), close3(rest3(ex, "chest_ctrl"), W)), (True, True))
for left in ("foot_l_ik_ctrl", "hand_l_ik_ctrl", "clavicle_l_ctrl", "upperarm_l_fk_ctrl", "thigh_l_fk_ctrl", "heel_l_ctrl"):
    right = left.replace("_l_", "_r_")
    check("%s mirrors %s" % (right, left), close3(FLIPX @ rest3(ex, left) @ FLIPX, rest3(ex, right)), True)
# rest stays rest through the followers, both mannequins, every deform bone
for obj in (mannequin("exact_rest"), ex):
    if not obj.data.get("fpmp_official_rig"):
        official_rig.create(obj)
    bpy.context.view_layer.update()
    moved = [b.name for b in obj.data.bones if b.use_deform and not b.name.startswith("OR") and
             ((world(obj, b.name).to_translation() - (obj.matrix_world @ b.matrix_local).to_translation()).length > 1e-3 or
              not same_rot(world(obj, b.name), obj.matrix_world @ b.matrix_local, 1e-2))]
    check("%s: rest stays rest" % obj.name, moved, [])
# a mirrored right control turned the mirrored way moves the right limb as the mirror of the left (Review Focus 5)
for side, sign in (("l", 1.0), ("r", -1.0)):
    official_rig.set_switch(ex, "arm_%s_fk_ik_switch" % side, ik=False)
    pbn = ex.pose.bones["upperarm_%s_fk_ctrl" % side]
    pbn.rotation_mode = 'XYZ'
    pbn.rotation_euler = (0.3, 0.0, 0.2 * sign)
bpy.context.view_layer.update()
hl, hr = head(ex, "hand_l"), head(ex, "hand_r")
check("mirrored controls move mirrored limbs", (hl - Vector((-hr.x, hr.y, hr.z))).length < 2e-3, True)
for side in "lr":
    ex.pose.bones["upperarm_%s_fk_ctrl" % side].rotation_euler = (0.0, 0.0, 0.0)

# --- Epic's settings
st = mannequin("settings")
official_rig.create(st)
names = [s["name"] for s in data["settings"] if s["name"] in st.pose.bones]
check("setting controls hidden, no shape", all(st.pose.bones[n].custom_shape is None and
      [c.name for c in st.data.bones[n].collections] == ["Mechanics"] for n in names), True)
check("Epic's defaults", [official_rig.setting(st, n) for n in ("leg_l_fk_ik_switch", "arm_l_fk_ik_switch", "arm_l_fk_local",
      "roll_blend_l_ctrl", "arm_l_stretch_switch")], [1.0, 0.0, 1.0, 1.0, 0.0])
# stretch: a hand target 1.5 lengths out is reached with stretch on, not off; capped at 2
official_rig.set_switch(st, "arm_l_fk_ik_switch", ik=True)
L = (head(st, "lowerarm_l") - head(st, "upperarm_l")).length + (head(st, "hand_l") - head(st, "lowerarm_l")).length
root = head(st, "upperarm_l").copy()
direction = (head(st, "hand_l") - root).normalized()
hand_rot = st.pose.bones["hand_l_ik_ctrl"].matrix.to_3x3().to_4x4()
for far, stretch, reach in ((1.5, 0.0, False), (1.5, 1.0, True), (2.5, 1.0, False)):
    official_rig.set_setting(st, "arm_l_stretch_switch", stretch)
    st.pose.bones["hand_l_ik_ctrl"].matrix = Matrix.Translation(root + direction * L * far) @ hand_rot
    got = (head(st, "hand_l") - root).length
    check("stretch %s at %.1f L: reached %s" % (stretch, far, reach), abs(got - L * far) < 2e-3, reach)
    if far == 2.5:
        check("stretch capped at 2 L", abs(got - 2.0 * L) < 2e-3, True)
check("stretching moves bones, never scales them (the hand keeps its size)", round(world(st, "hand_l").to_scale().length, 4),
      round(Vector((1, 1, 1)).length, 4))
# softness: the chain scale equals Epic's formula at a sampled distance
official_rig.set_setting(st, "arm_l_stretch_switch", 0.0)
official_rig.set_setting(st, "arm_l_softness", 1.0)
d = 0.995 * L
st.pose.bones["hand_l_ik_ctrl"].matrix = Matrix.Translation(root + direction * d) @ hand_rot
bpy.context.view_layer.update()
sd = L * 1.0 * 0.02
da = L - sd
s_want = max(d / (da + sd * (1 - math.exp(-(d - da) / sd))), 1.0)
check("softness scales the chain as Epic's formula", round(st.pose.bones["OR_MCH_ik_upperarm_l"].scale.y, 4), round(s_want, 4))
# full reach with softness 0 and stretch off: no stretch (Review Focus 4)
official_rig.set_setting(st, "arm_l_softness", 0.0)
st.pose.bones["hand_l_ik_ctrl"].matrix = Matrix.Translation(root + direction * L) @ hand_rot
bpy.context.view_layer.update()
check("no softness, no stretch: the chain keeps its length", round(st.pose.bones["OR_MCH_ik_upperarm_l"].scale.y, 6), 1.0)
# FK local: the arm space keeps the body's rotation (on) or rides the clavicle (off)
official_rig.set_switch(st, "arm_l_fk_ik_switch", ik=False)
st.pose.bones["clavicle_l_ctrl"].rotation_mode = 'XYZ'
for on in (1.0, 0.0):
    official_rig.set_setting(st, "arm_l_fk_local", on)

    def relative():
        bpy.context.view_layer.update()
        return (st.pose.bones["clavicle_l_ctrl"].matrix.inverted() @ st.pose.bones["upperarm_l_fk_ctrl_space"].matrix).to_quaternion()
    before = relative()
    st.pose.bones["clavicle_l_ctrl"].rotation_euler = (0.0, 0.0, 0.4)
    changed = before.rotation_difference(relative()).angle > 0.3
    st.pose.bones["clavicle_l_ctrl"].rotation_euler = (0.0, 0.0, 0.0)
    check("FK local %s: the arm space keeps the body's rotation, not the clavicle's" % on, changed, on == 1.0)

# --- foot roll (Epic: heel up to 180, ball up to 40 x roll blend, then the toe tip)
fr = mannequin("roll")
official_rig.create(fr)
check("Epic's rotation channels: foot roll turns only on its yaw (Z)", tuple(fr.pose.bones["foot_roll_l_ctrl"].lock_rotation),
      (True, True, False))


def roll(deg, blend=1.0):
    """Epic's yaw on the left foot roll control (its local Z, Blender's sign mirrored), and the roll blend."""
    official_rig.set_setting(fr, "roll_blend_l_ctrl", blend)
    pbn = fr.pose.bones["foot_roll_l_ctrl"]
    pbn.rotation_mode = 'XYZ'
    pbn.rotation_euler = (0.0, 0.0, -math.radians(deg))
    fr.update_tag()
    bpy.context.view_layer.update()
    return {k: math.degrees(fr.pose.bones["OR_MCH_roll_" + n].matrix_basis.to_quaternion().angle)
            for k, n in (("heel", "heel_l"), ("ball", "foot_bk1_l"), ("tip", "tip_l"))}


rest_heel = head(fr, "foot_ik_l").copy()
for deg, blend, want in ((30, 1.0, (30, 0, 0)), (-30, 1.0, (0, 30, 0)), (-60, 1.0, (0, 40, 20)), (-30, 0.5, (0, 20, 10))):
    got = roll(deg, blend)
    check("foot roll %d blend %.1f: heel %d ball %d tip %d" % ((deg, blend) + want),
          all(abs(got[k] - w) < 0.5 for k, w in zip(("heel", "ball", "tip"), want)), True)
    if (deg, blend) in ((30, 1.0), (-30, 1.0)):
        # the turn's sense about the roll axis: Epic's (heel +yaw, ball -yaw) mirrored for Blender's axes (z = -yaw)
        axis = fr.data.bones["foot_roll_l_ctrl"].matrix_local.to_3x3().col[2].normalized()
        name = "OR_MCH_roll_heel_l" if deg > 0 else "OR_MCH_roll_foot_bk1_l"
        q = (fr.pose.bones[name].matrix.to_3x3() @ fr.data.bones[name].matrix_local.to_3x3().inverted()).to_quaternion()
        a, g = q.to_axis_angle()
        check("roll %d turns %s by Epic's sense (mirrored)" % (deg, name), round(a.dot(axis) * (1 if g > 0 else -1)),
              -1 if deg > 0 else 1)
roll(0.0)
check("no roll: the foot at rest", (head(fr, "foot_ik_l") - rest_heel).length < 1e-4, True)
# the right foot rolls as the mirror of the left (its roll axis is the left's mirrored)
for deg in (30, -30, -60):
    for side, sign in (("l", 1.0), ("r", -1.0)):
        fr.pose.bones["foot_roll_%s_ctrl" % side].rotation_mode = 'XYZ'
        fr.pose.bones["foot_roll_%s_ctrl" % side].rotation_euler = (0.0, 0.0, -math.radians(deg) * sign)
    fr.update_tag()
    off = max((head(fr, n + "_l") * Vector((-1, 1, 1)) - head(fr, n + "_r")).length for n in ("foot_ik", "ball", "foot"))
    check("roll %d: the right foot mirrors the left" % deg, off < 1e-4, True)
for side in "lr":
    fr.pose.bones["foot_roll_%s_ctrl" % side].rotation_euler = (0.0, 0.0, 0.0)
# a missing bone switches off only its own part: no ball, the leg's FK still works and the leg starts in FK
nb = mannequin("no_ball", drop=("ball_l",))
official_rig.create(nb)
check("no ball: the left leg starts in FK", official_rig.setting(nb, "leg_l_fk_ik_switch"), 0.0)
calf_before = head(nb, "calf_l").copy()
nb.pose.bones["thigh_l_fk_ctrl"].rotation_mode = 'XYZ'
nb.pose.bones["thigh_l_fk_ctrl"].rotation_euler = (0.4, 0.0, 0.0)
check("no ball: the thigh FK control still moves the leg", (head(nb, "calf_l") - calf_before).length > 0.02, True)

# --- spine stretch, neck IK, head stretch
sn = mannequin("stretchy")
official_rig.create(sn)
official_rig.set_switch(sn, "spine_fk_ik_switch", ik=True, match=False)
rest_len = (head(sn, "spine_05") - head(sn, "spine_01")).length
for on in (0.0, 1.0):
    official_rig.set_setting(sn, "spine_stretch_switch", on)
    sn.pose.bones["chest_ctrl"].location = (0.0, 0.0, 0.0)
    bpy.context.view_layer.update()
    sn.pose.bones["chest_ctrl"].matrix = Matrix.Translation((0.0, 0.0, 0.1)) @ sn.pose.bones["chest_ctrl"].matrix
    grown = (head(sn, "spine_05") - head(sn, "spine_01")).length - rest_len
    if on:
        check("spine stretch on: the spine top reaches its point", (head(sn, "spine_05") - head(sn, "OR_MCH_pt_spine_05")).length < 0.01, True)
    else:
        check("spine stretch off: the spine keeps its length", grown < 0.01, True)
sn.pose.bones["chest_ctrl"].location = (0.0, 0.0, 0.0)
official_rig.set_setting(sn, "spine_stretch_switch", 0.0)
# neck IK: the neck follows the head control; at rest it is the rest
neck_rest = world(sn, "neck_02").copy()
official_rig.set_switch(sn, "neck_fk_ik_switch", ik=True, match=False)
check("neck IK at rest is the rest", (world(sn, "neck_02").to_translation() - neck_rest.to_translation()).length < 1e-3
      and same_rot(world(sn, "neck_02"), neck_rest, 1e-2), True)
sn.pose.bones["head_ik_ctrl"].matrix = Matrix.Translation((0.0, -0.03, -0.03)) @ sn.pose.bones["head_ik_ctrl"].matrix     # within reach
check("neck IK bends the neck toward the head control", not same_rot(world(sn, "neck_01"), sn.matrix_world @ sn.data.bones["neck_01"].matrix_local, 0.05), True)
check("neck IK puts the head on its control", (head(sn, "head") - head(sn, "head_ik_ctrl")).length < 2e-3, True)
sn.pose.bones["head_ik_ctrl"].matrix = Matrix.Translation((0.0, 0.0, 0.08)) @ sn.pose.bones["head_ik_ctrl"].matrix
check("head stretch off: past the neck's reach the head stays on the neck", (head(sn, "head") - head(sn, "head_ik_ctrl")).length > 0.02, True)
official_rig.set_setting(sn, "head_stretch", 1.0)
bpy.context.view_layer.update()
check("head stretch on: the head reaches its control", (head(sn, "head") - head(sn, "head_ik_ctrl")).length < 0.01, True)

# --- panel operators: a setting, body controls
from fpmp_baseline.operator import rig_ui  # noqa: E402
try:
    rig_ui.register()
except ValueError:
    pass                # registered earlier in this run
pn = mannequin("panel")
official_rig.create(pn)
bpy.context.view_layer.objects.active = pn
bpy.ops.fpmp.official_rig_setting(name="arm_l_stretch_switch", value=1.0)
check("the setting operator sets the value", official_rig.setting(pn, "arm_l_stretch_switch"), 1.0)
BODY = [n for n in ("global_ctrl", "root_ctrl", "body_offset_ctrl", "body_ctrl", "hips_ctrl", "neck_01_ctrl", "neck_02_ctrl",
                    "index_01_l_ctrl") if n in pn.pose.bones]
bpy.ops.fpmp.official_rig_body_controls(show=False)
check("body controls hidden", [n for n in BODY if not pn.pose.bones[n].hide], [])
check("the body controls value follows", official_rig.setting(pn, "ShowBodyControls"), 0.0)
official_rig.set_switch(pn, "neck_fk_ik_switch", ik=True)
bpy.ops.fpmp.official_rig_body_controls(show=True)
check("body controls back, except what the FK/IK state hides", [n for n in BODY if pn.pose.bones[n].hide], ["neck_01_ctrl", "neck_02_ctrl"])


class Layout:
    """Stands in for a panel layout (headless Blender draws no panels): records what the rig's ui() puts in it."""
    def __init__(self, log):
        self.log = log

    def column(self, **_):
        return self

    row = column

    def label(self, text=""):
        self.log.append(("label", text))

    def operator(self, idname, text="", **_):
        self.log.append(("op", idname, text))
        return SimpleNamespace()

    def prop(self, owner, path, text=""):
        self.log.append(("prop", path, text))


drawn = []
official_rig.ui(Layout(drawn), pn)
texts = [d[-1] for d in drawn]
check("the panel draws every part's switches and settings", all(t in texts for t in ("Stretch", "Soft", "Local", "Roll L",
      "Body Controls", "Bake to Controls")), True)

# --- Bake to Controls then IK (user report: IK after a bake twisted the limbs, broke the spine and neck)
# off-hinge elbows and knees (twisting forearms, as mocap has), a moving pelvis, every spine and neck bone turned
PARTS = {"arm_l_fk_ik_switch": ("upperarm_l", "lowerarm_l", "hand_l"), "arm_r_fk_ik_switch": ("upperarm_r", "lowerarm_r", "hand_r"),
         "leg_l_fk_ik_switch": ("thigh_l", "calf_l", "foot_l", "ball_l"), "leg_r_fk_ik_switch": ("thigh_r", "calf_r", "foot_r", "ball_r"),
         "spine_fk_ik_switch": ("spine_01", "spine_02", "spine_03", "spine_04", "spine_05"),
         "neck_fk_ik_switch": ("neck_01", "neck_02", "head")}
TURNS = {"pelvis": (0.3, 0.2, 0.4), "spine_01": (0.2, 0.1, -0.1), "spine_02": (0.15, -0.2, 0.1), "spine_03": (0.1, 0.1, 0.2),
         "spine_04": (-0.1, 0.05, 0.1), "spine_05": (0.05, 0.2, -0.1), "neck_01": (0.2, 0.1, 0.3), "neck_02": (-0.1, 0.2, 0.1),
         "head": (0.3, -0.2, 0.2), "upperarm_l": (0.5, 0.3, 0.6), "lowerarm_l": (0.2, 0.4, 0.8), "hand_l": (0.3, 0.2, -0.4),
         "upperarm_r": (-0.4, 0.5, 0.2), "lowerarm_r": (0.1, -0.6, 0.5), "hand_r": (-0.2, 0.3, 0.3),
         "thigh_l": (0.6, 0.2, 0.3), "calf_l": (0.7, 0.3, 0.1), "foot_l": (0.2, 0.3, 0.1), "ball_l": (0.3, 0.0, 0.0),
         "thigh_r": (-0.3, 0.4, 0.2), "calf_r": (0.9, -0.2, 0.3), "foot_r": (-0.2, 0.1, 0.3)}
bi = mannequin("bake_ik")
official_rig.create(bi)
official_rig.on_animation_import(bi)
bi.animation_data_create()
for f in (1, 2, 3, 4):
    k = (f - 1) / 1.5
    for n, (x, y, z) in TURNS.items():
        bi.pose.bones[n].rotation_mode = 'XYZ'
        bi.pose.bones[n].rotation_euler = (x * k, y * k, z * k)
        bi.pose.bones[n].keyframe_insert("rotation_euler", frame=f)
    bi.pose.bones["pelvis"].location = (0.1 * k, -0.2 * k, 0.15 * k)
    bi.pose.bones["pelvis"].keyframe_insert("location", frame=f)
baked = {}
for f in (1, 2, 3, 4):
    bpy.context.scene.frame_set(f)
    baked[f] = {n: world(bi, n).copy() for bones in PARTS.values() for n in bones}
official_rig.bake_to_controls(bi, 1, 4)


def ik_off_by(obj, switch):
    """Worst (distance, angle) of a part's bones in IK, unmatched, against the baked animation over its frames."""
    official_rig.set_switch(obj, switch, ik=True, match=False)
    worst = [0.0, 0.0]
    for f in (1, 2, 3, 4):
        bpy.context.scene.frame_set(f)
        for n in PARTS[switch]:
            have, want = world(obj, n), baked[f][n]
            turn = have.to_quaternion().rotation_difference(want.to_quaternion()).angle
            worst = [max(worst[0], (have.to_translation() - want.to_translation()).length), max(worst[1], min(turn, 2 * math.pi - turn))]
    official_rig.set_switch(obj, switch, ik=False, match=False)
    return worst


for switch in PARTS:
    off = ik_off_by(bi, switch)
    check("after a bake, %s in IK plays the animation" % switch, off[0] < 1e-4 and off[1] < 1e-3, True)
    if off[0] >= 1e-4 or off[1] >= 1e-3:
        print("   %s off by %.2e m, %.3f deg" % (switch, off[0], math.degrees(off[1])))
for name in ("arm_l_stretch_switch", "leg_r_stretch_switch", "spine_stretch_switch", "head_stretch"):
    official_rig.set_setting(bi, name, 1.0)
for switch in ("arm_l_fk_ik_switch", "leg_r_fk_ik_switch", "spine_fk_ik_switch", "neck_fk_ik_switch"):
    off = ik_off_by(bi, switch)
    check("after a bake, %s in IK with stretch on plays the animation" % switch, off[0] < 1e-4 and off[1] < 1e-3, True)
bpy.context.scene.frame_set(4)
check("the bake still ends in FK, on the animation", all((world(bi, n).to_translation() - m.to_translation()).length < 1e-4
      and same_rot(world(bi, n), m, 1e-3) for n, m in baked[4].items()), True)

# --- extra hand controls (an app option; not Epic's): curl, spread, palm cup, per-finger curl
from fpmp_baseline.processing.context import official_hands  # noqa: E402
LONG = ("index", "middle", "ring", "pinky")


def handy(name, layer):
    """A mannequin with a real left hand (palm down along +X, fingers slightly bent at rest), its rig built."""
    obj = mannequin(name)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')
    eb = obj.data.edit_bones
    wrist = Vector((0.72, 0.0, 1.42))
    for i, f in enumerate(LONG):
        y = -0.025 + i * 0.016
        eb["%s_metacarpal_l" % f].head = wrist + Vector((0.01, y * 0.5, 0.0))
        joints = [Vector((0.80, y, 1.42)), Vector((0.84, y, 1.417)), Vector((0.87, y, 1.412)), Vector((0.895, y, 1.405))]
        eb["%s_metacarpal_l" % f].tail = joints[0]
        for k in range(3):
            eb["%s_0%d_l" % (f, k + 1)].head, eb["%s_0%d_l" % (f, k + 1)].tail = joints[k], joints[k + 1]
    thumb = [Vector((0.74, -0.03, 1.41)), Vector((0.765, -0.05, 1.40)), Vector((0.785, -0.06, 1.395)), Vector((0.80, -0.065, 1.39))]
    for k in range(3):
        eb["thumb_0%d_l" % (k + 1)].head, eb["thumb_0%d_l" % (k + 1)].tail = thumb[k], thumb[k + 1]
    eb["hand_l"].tail = Vector((0.80, 0.0, 1.42))
    bpy.ops.object.mode_set(mode='OBJECT')
    official_rig.create(obj)
    if layer:
        check("hand controls built", official_hands.build(obj) > 0, True)
    return obj


def tip(obj, finger):
    return head(obj, "%s_03_l" % finger) + (world(obj, "%s_03_l" % finger).to_3x3() @ Vector((0, obj.data.bones["%s_03_l" % finger].length, 0)))


def turn_ctrl(obj, name, euler):
    pose_bone = obj.pose.bones[name]
    pose_bone.rotation_mode = 'XYZ'
    pose_bone.rotation_euler = euler
    bpy.context.view_layer.update()


plain, hands = handy("hands_plain", False), handy("hands_on", True)
check("without the option there are no hand controls", "fingers_l_ctrl" in plain.pose.bones, False)
fingers = [b.name for b in hands.data.bones if b.name.endswith("_l") and b.name.split("_")[0] in LONG + ("thumb",)]
check("at rest the layer changes nothing", all((world(hands, n).to_translation() - world(plain, n).to_translation()).length < 1e-5
      and same_rot(world(hands, n), world(plain, n), 1e-4) for n in fingers), True)
rest_tips = {f: tip(hands, f) for f in LONG}
turn_ctrl(hands, "fingers_l_ctrl", (math.radians(90), 0.0, 0.0))
wrist = head(hands, "hand_l")
check("Fingers X closes every finger into a fist", all((tip(hands, f) - wrist).length < (rest_tips[f] - wrist).length - 0.04
      for f in LONG), True)


def bend(obj, a, b):
    return math.degrees((world(obj, a).to_3x3().col[1]).angle(world(obj, b).to_3x3().col[1]))


check("a full curl stays within each knuckle's reach", all(bend(hands, "%s_metacarpal_l" % f, "%s_01_l" % f) <= 91
      and bend(hands, "%s_01_l" % f, "%s_02_l" % f) <= 106 and bend(hands, "%s_02_l" % f, "%s_03_l" % f) <= 81 for f in LONG), True)
turn_ctrl(hands, "fingers_l_ctrl", (0.0, 0.0, 0.0))
gap = (rest_tips["index"] - rest_tips["pinky"]).length
turn_ctrl(hands, "fingers_l_ctrl", (0.0, 0.0, math.radians(30)))
check("Fingers Z spreads the fingers", (tip(hands, "index") - tip(hands, "pinky")).length > gap + 0.01, True)
check("and the middle finger stays", (tip(hands, "middle") - rest_tips["middle"]).length < 1e-3, True)
turn_ctrl(hands, "fingers_l_ctrl", (0.0, 0.0, 0.0))
meta_rest = {f: world(hands, "%s_metacarpal_l" % f).copy() for f in ("index", "pinky")}
turn_ctrl(hands, "palm_l_ctrl", (math.radians(40), 0.0, 0.0))
cup = {f: math.degrees(world(hands, "%s_metacarpal_l" % f).to_quaternion().rotation_difference(meta_rest[f].to_quaternion()).angle)
       for f in meta_rest}
check("Palm X cups the hand, the little finger's side most", cup["pinky"] > 10 and cup["index"] < 1, True)
turn_ctrl(hands, "palm_l_ctrl", (0.0, 0.0, 0.0))
turn_ctrl(hands, "ring_l_curl_ctrl", (math.radians(60), 0.0, 0.0))
check("a finger's curl control curls that finger only", tip(hands, "ring").z < rest_tips["ring"].z - 0.01
      and (tip(hands, "middle") - rest_tips["middle"]).length < 1e-4, True)
turn_ctrl(hands, "ring_l_curl_ctrl", (0.0, 0.0, 0.0))
before = world(hands, "index_02_l").copy()
turn_ctrl(hands, "index_02_l_ctrl", (0.0, 0.0, math.radians(30)))
check("Epic's finger control still turns its bone", not same_rot(world(hands, "index_02_l"), before, 0.1), True)
turn_ctrl(hands, "index_02_l_ctrl", (0.0, 0.0, 0.0))
check("hand controls sit with the rig's controls", all(n in hands.data.collections["Controls"].bones
      for n in ("fingers_l_ctrl", "palm_l_ctrl", "index_l_curl_ctrl")), True)
# held prop: a handle through the fist; the finger joints stop on its surface instead of going through it
from fpmp_baseline.processing.context import official_grip  # noqa: E402
official_grip.register()
turn_ctrl(hands, "fingers_l_ctrl", (math.radians(90), 0.0, 0.0))
joints = lambda: [head(hands, "%s_0%d_l" % (f, k)) for f in LONG for k in (2, 3)] + [tip(hands, f) for f in LONG]
fist = joints()
centre = sum((head(hands, "%s_03_l" % f) for f in LONG), Vector()) / 4
radius = 0.012
bpy.ops.mesh.primitive_cylinder_add(radius=radius, depth=0.2, location=centre, rotation=(math.pi / 2, 0.0, 0.0), vertices=64)
handle = bpy.context.active_object
handle.name = "handle"
bpy.context.view_layer.objects.active = hands
off_axis = lambda p: math.hypot(p.x - centre.x, p.z - centre.z)
check("without a held prop the fist goes through the handle", min(off_axis(p) for p in fist) < radius - 0.002, True)
hands.pose.bones["fingers_l_ctrl"].fpmp_held_prop = handle
bpy.context.view_layer.update()
held = joints()
check("with it, every finger joint stays outside the handle", min(off_axis(p) for p in held) > radius - 1e-3, True)
hands.pose.bones["fingers_l_ctrl"]["finger_wrap"] = 1.0
hands.update_tag()
bpy.context.view_layer.update()
check("Finger Wrap closes the finger joints onto it", max(off_axis(p) for p in joints()[:8]) < radius + 0.015, True)
hands.pose.bones["fingers_l_ctrl"]["finger_wrap"] = 0.0
hands.update_tag()
hands.pose.bones["fingers_l_ctrl"].fpmp_held_prop = None
bpy.context.view_layer.update()
check("taking the prop away gives the plain fist back", max((a - b).length for a, b in zip(joints(), fist)) < 1e-4, True)
turn_ctrl(hands, "fingers_l_ctrl", (0.0, 0.0, 0.0))
held_ui = []
official_rig.ui(Layout(held_ui), hands)
check("the rig panel offers the held prop per hand", [d[-1] for d in held_ui if d[0] == "prop" and d[-1].startswith("Held")],
      ["Held L", "Held R"])
check("the grip changes nothing at rest", all((world(hands, n).to_translation() - world(plain, n).to_translation()).length < 1e-5
      for n in fingers), True)
# Bake to Controls with the layer: the bones land, the hand controls are keyed at rest
official_rig.on_animation_import(hands)
hands.animation_data_create()
for f, angle in ((1, 0.0), (3, 0.6)):
    for n in ("index_02_l", "pinky_01_l", "thumb_02_l"):
        hands.pose.bones[n].rotation_mode = 'XYZ'
        hands.pose.bones[n].rotation_euler = (angle, 0.0, angle * 0.3)
        hands.pose.bones[n].keyframe_insert("rotation_euler", frame=f)
bpy.context.scene.frame_set(3)
want_hand = {n: world(hands, n).copy() for n in ("index_02_l", "pinky_01_l", "thumb_02_l", "index_03_l")}
official_rig.bake_to_controls(hands, 1, 3)
bpy.context.scene.frame_set(3)
check("with hand controls, the bake lands", all((world(hands, n).to_translation() - m.to_translation()).length < 1e-4
      and same_rot(world(hands, n), m, 1e-3) for n, m in want_hand.items()), True)
paths = {fc.data_path for layer in hands.animation_data.action.layers for strip in layer.strips for bag in strip.channelbags
         for fc in bag.fcurves}
check("and the hand controls are keyed at rest", 'pose.bones["fingers_l_ctrl"].rotation_quaternion' in paths
      or 'pose.bones["fingers_l_ctrl"].rotation_euler' in paths, True)

print("[official_rig_check] %d passed, %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
