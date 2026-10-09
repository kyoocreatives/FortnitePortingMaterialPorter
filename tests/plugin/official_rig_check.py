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
check("same rest as its bone", all(abs(a - b) < 1e-4 for ra, rb in zip(man.data.bones["foot_l_ik_ctrl"].matrix_local,
                                                                       man.data.bones["foot_l"].matrix_local) for a, b in zip(ra, rb)), True)
check("parent as the data", man.data.bones["foot_l_ik_ctrl"].parent.name, fc["parent"])
heel = next(c for c in data["controls"] if c["name"] == "heel_l_ctrl")
ratio = man.data.bones["head"].head_local.z / data["height"]
check("off-bone control at its offset", tuple(man.data.bones["heel_l_ctrl"].head_local),
      tuple(man.data.bones[heel["bone"]].head_local + Vector(heel["offset"]) * ratio))
knee, hip, ankle = (man.data.bones[b].head_local for b in ("calf_l", "thigh_l", "foot_l"))
plane = (knee - hip).cross(ankle - hip)
check("leg pole in the leg's bend plane", abs((man.data.bones["leg_l_pv_ik_ctrl"].head_local - hip).dot(plane.normalized())) < 1e-4, True)
check("root width", man.pose.bones["global_ctrl"].custom_shape_wire_width, 3.5)
check("digit width", man.pose.bones["index_01_l_ctrl"].custom_shape_wire_width, 1.5)
check("controls collection", "foot_l_ik_ctrl" in man.data.collections["Controls"].bones, True)
check("spaces in Mechanics", "chest_space" in man.data.collections["Mechanics"].bones, True)
check("game bones hidden", man.data.collections["Game Bones"].is_visible, False)
check("a switch is a property", man.pose.bones["leg_l_fk_ik_switch"].get("leg_l_fk_ik_switch"), 0.0)
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
mesh_hooks._official(SimpleNamespace(imported_meshes=[{"Skeleton": hooked, "Mesh": None}], type=EExportType.OUTFIT))
check("the hook builds the rig", bool(hooked.data.get("fpmp_official_rig")), True)
broken = mannequin("broken")
real = official_rig.create
official_rig.create = lambda obj, data=None: 1 / 0
try:
    mesh_hooks._official(SimpleNamespace(imported_meshes=[{"Skeleton": broken, "Mesh": None}], type=EExportType.OUTFIT))
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
check("FK control turns its bone", same_rot(world(rig, "upperarm_l"), world(rig, "upperarm_l_fk_ctrl")), True)
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
check("head IK turns the head", same_rot(world(rig, "head"), world(rig, "head_ik_ctrl")), True)
# fingers copy their controls
pb["index_01_l_ctrl"].rotation_mode = 'XYZ'
pb["index_01_l_ctrl"].rotation_euler = (0.4, 0.0, 0.0)
check("finger follows its control", same_rot(world(rig, "index_01_l"), world(rig, "index_01_l_ctrl")), True)

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

print("[official_rig_check] %d passed, %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
