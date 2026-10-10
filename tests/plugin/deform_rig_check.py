"""Checks Fortnite's deform correctives: the rig reader on a synthetic dump, the bones the builder makes.

    blender -b --factory-startup --python-exit-code 1 -P tests/plugin/deform_rig_check.py -- <plugin parent>

<plugin parent> holds the plugin as package fpmp_baseline (see translator_test.py). Exit code 0 when every check passes."""
import json
import math
import os
import sys
import tempfile

import bpy

sys.path.insert(0, sys.argv[sys.argv.index("--") + 1])
import fpmp_baseline  # noqa: E402,F401
from fpmp_baseline.processing.context import deform_rig_read as dr  # noqa: E402

FAILS, PASSES = [], [0]


def check(name, got, want, tol=1e-4):
    if isinstance(want, float):
        ok = abs(got - want) <= tol
    elif isinstance(want, (tuple, list)) and want and all(isinstance(w, float) for w in want):
        ok = len(got) == len(want) and all(abs(g - w) <= tol for g, w in zip(got, want))
    else:
        ok = got == want
    if ok:
        PASSES[0] += 1
    else:
        FAILS.append(name)
        print("[deform_rig_check] FAIL %s: got %r, want %r" % (name, got, want))


def guid(name, n):
    return "%s_%d_%s" % (name, n, "0123456789ABCDEF0123456789ABCDEF")


def tf(t=(0.0, 0.0, 0.0), s=(1.0, 1.0, 1.0)):
    return {"Rotation": {"X": 0.0, "Y": 0.0, "Z": 0.0, "W": 1.0}, "Translation": dict(zip("XYZ", t)),
            "Scale3D": dict(zip("XYZ", s))}


def dump(call=True, extras=True):
    """A minimal CUE4Parse dump of a deform Control Rig: the forward solve calls FN_DeformRig_FNC with its Bool and Value
    literals, then one extra: a spherical pose reader weighting an additive rotation on a bone."""
    lit = [("bools", {guid("elbow", 3): False, guid("knee", 9): True}),
           ("values", {guid("elbow_in", 30): tf((0.0, 7.0, 0.0), (0.5, 1.0, 1.0)), guid("knee_in", 31): tf((0.0, -12.0, 0.0))}),
           ("driver", "Bone/hand_l"), ("axis", {"X": 1.0, "Y": 0.0, "Z": 0.0}), ("offset", {"X": 90.0, "Y": 0.0, "Z": 0.0}),
           ("region", 0.1), ("falloff", 0.4), ("scales", {"PositiveWidth": 1.0, "NegativeWidth": 2.0, "PositiveHeight": 1.5,
                                                          "NegativeHeight": 1.5}),
           ("unit", {"PositiveWidth": 1.0, "NegativeWidth": 1.0, "PositiveHeight": 1.0, "NegativeHeight": 1.0}),
           ("euler", {"X": 0.0, "Y": -10.0, "Z": 0.0}), ("order", 4),
           ("items", [{"Item": {"Type": "ERigElementType::First", "Name": "dyn_bracelet_1_l"}, "Transform": tf()}]),
           ("parent", "None/None"), ("flip", False)]
    work = ["spr_out", "quat", "modify_items"]
    L = {n: i for i, (n, _) in enumerate(lit)}
    Wi = {n: i for i, n in enumerate(work)}

    def l(name):
        return {"MemoryType": 1, "RegisterIndex": L[name], "RegisterOffset": 65535}

    def w(name, seg=None):
        return {"MemoryType": 0, "RegisterIndex": Wi[name], "RegisterOffset": 65535 if seg is None else 0}

    functions = [{"Name": "FRigUnit_SphericalPoseReader::Execute", "Arguments": [{"LocalNameString": n} for n in (
                     "OutputParam", "DriverItem", "DriverAxis", "RotationOffset", "ActiveRegionSize", "ActiveRegionScaleFactors",
                     "FalloffSize", "FalloffRegionScaleFactors", "FlipWidthScaling", "FlipHeightScaling", "OptionalParentItem")]},
                 {"Name": "FRigVMFunction_MathQuaternionFromEuler::Execute", "Arguments": [{"LocalNameString": n} for n in (
                     "Euler", "RotationOrder", "Result")]},
                 {"Name": "FRigUnit_ModifyTransforms::Execute", "Arguments": [{"LocalNameString": n} for n in (
                     "ItemToModify", "Weight")]}]
    instructions = [{"OpCode": 99}]
    if call:
        instructions.append({"OpCode": 104, "CallableIndex": 0, "Arguments": [l("bools"), l("values")]})
    if extras:
        instructions += [
            {"OpCode": 101, "FunctionIndex": 0, "Arguments": [w("spr_out"), l("driver"), l("axis"), l("offset"), l("region"), l("scales"),
                                                               l("falloff"), l("unit"), l("flip"), l("flip"), l("parent")]},
            {"OpCode": 101, "FunctionIndex": 1, "Arguments": [l("euler"), l("order"), w("quat")]},
            {"OpCode": 68, "Target": w("modify_items"), "Source": l("items")},
            {"OpCode": 68, "Target": w("modify_items", "0.Transform.Rotation"), "Source": w("quat")},
            {"OpCode": 101, "FunctionIndex": 2, "Arguments": [w("modify_items"), w("spr_out")]}]
    instructions.append({"OpCode": 80})
    vm = {"Type": "RigVM",
          "LiteralMemoryStorage": {"PropertyDescs": [{"Name": n} for n, _ in lit], "PropertyPathDescriptions": [],
                                   "Values": dict(lit)},
          "DefaultWorkMemoryStorage": {"PropertyDescs": [{"Name": n} for n in work],
                                       "PropertyPathDescriptions": [{"SegmentPath": "0.Transform.Rotation"}]},
          "FunctionNamesStorage": [f["Name"] for f in functions], "LocalizedRegistry": {"Functions": functions},
          "ByteCodeStorage": {"Instructions": instructions, "BranchInfos": [], "Entries": ['(Name="Forwards Solve",InstructionIndex=0)'],
                              "CallableInfos": [{"Name": "lib:FN_DeformRig_FNC", "FirstInstruction": 9, "LastInstruction": 9,
                                                 "Arguments": [{"Name": "DeformRig_Bool"}, {"Name": "DeformRig_Value"}]}]}}
    return [vm]


TMP = tempfile.mkdtemp()


def write(name, content):
    path = os.path.join(TMP, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(content, f)
    return path


# --- reader
got = dr.read(write("rig.json", dump()))
check("the character's part switches", (got["switches"].get("elbow"), got["switches"].get("knee")), (False, True))
check("the character's values (Blueprint suffixes dropped)", got["values"]["elbow_in"]["t"], [0.0, 7.0, 0.0])
check("value scale", got["values"]["elbow_in"]["s"], [0.5, 1.0, 1.0])
extra = got["extras"][0] if got["extras"] else {}
check("an extra: an additive rotation on its bone", (extra.get("op"), extra.get("bone")), ("modify", "dyn_bracelet_1_l"))
check("its rotation as Epic's Euler and order", (extra.get("euler"), extra.get("order")), ([0.0, -10.0, 0.0], 4))
check("weighted by a spherical pose reader", (extra.get("weight") or {}).get("driver"), "hand_l")
check("the reader's region factors", (extra.get("weight") or {}).get("region_scale"), [1.0, 2.0, 1.5, 1.5])
lib = dr.library()
fallback = dr.tuning(write("nocall.json", dump(call=False)))
check("no FortDeformRig call: the library values", fallback["values"]["elbow_in"]["t"], lib["values"]["elbow_in"]["t"])
check("a missing file: the library values", dr.tuning(os.path.join(TMP, "missing.json"))["values"], lib["values"])
check("library switches when none read", dr.tuning(None)["switches"], lib["switches"])
# tuning keeps library values for fields the character doesn't set
merged = dr.tuning(write("rig2.json", dump()))
check("tuning: the character's value where set", merged["values"]["elbow_in"]["t"], [0.0, 7.0, 0.0])
check("tuning: the library's elsewhere", merged["values"]["wrist_up"]["t"], lib["values"]["wrist_up"]["t"])

# --- builder
from mathutils import Matrix, Quaternion, Vector  # noqa: E402
from fpmp_baseline.processing.context import deform_rig  # noqa: E402
from fpmp_baseline.material_porter.effects import ue_offset  # noqa: E402

PLACES = {"pelvis": ((0, 0, 1.0), None), "spine_03": ((0, 0, 1.25), "pelvis"), "spine_04": ((0, 0, 1.33), "spine_03"),
          "clavicle_l": ((0.04, 0, 1.42), "spine_04"), "upperarm_l": ((0.18, 0, 1.42), "clavicle_l"),
          "upperarm_twist_01_l": ((0.3, 0, 1.42), "upperarm_l"), "lowerarm_l": ((0.46, 0.0, 1.42), "upperarm_l"),
          "lowerarm_twist_01_l": ((0.55, 0, 1.42), "lowerarm_l"), "hand_l": ((0.72, 0, 1.42), "lowerarm_l"),
          "thigh_l": ((0.1, 0, 0.95), "pelvis"), "calf_l": ((0.1, -0.03, 0.52), "thigh_l"), "foot_l": ((0.1, 0, 0.1), "calf_l")}
DEFORM = {"elbow_in": "lowerarm", "elbow_out": "lowerarm", "upperarm_up": "upperarm", "knee_in": "calf", "wrist_up": "hand"}


def skeleton(name, drop=()):
    """The left side and centre, then the right as Epic builds it: mirrored across X with every axis negated."""
    obj = bpy.data.objects.new(name, bpy.data.armatures.new(name))
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')
    e = obj.data.edit_bones
    for n, (p, parent) in PLACES.items():
        b = e.new(n)
        b.head, b.tail, b.roll = p, Vector(p) + Vector((0.0, 0.0, 0.06)), 0.3
        if parent:
            b.parent = e[parent]
    for part, limb in DEFORM.items():
        if part in drop:
            continue
        at = e[limb + "_l"]
        base = e.new("deform_%s_base_l" % part)
        base.head, base.tail, base.roll, base.parent = at.head.copy(), at.head + Vector((0, 0, 0.03)), 0.2, at
        child = e.new("deform_%s_l" % part)
        child.head, child.tail, child.parent = base.head + Vector((0, -0.02, 0)), base.head + Vector((0, -0.02, 0.03)), base
    m = Matrix(((-1, 0, 0), (0, 1, 0), (0, 0, 1)))
    for b in [b for b in e if b.name.endswith("_l")]:
        r = e.new(b.name[:-2] + "_r")
        r.head = m @ b.head
        r.tail = r.head - m @ (b.tail - b.head)
        r.align_roll(-(m @ b.z_axis))
    for b in [b for b in e if b.name.endswith("_r")]:
        parent = e[b.name[:-2] + "_l"].parent
        b.parent = e.get(parent.name[:-2] + "_r") if parent.name.endswith("_l") else parent
    bpy.ops.object.mode_set(mode='OBJECT')
    return obj


def world(obj, name):
    bpy.context.view_layer.update()
    return obj.matrix_world @ obj.pose.bones[name].matrix


def turn(obj, name, axis, deg):
    """Turn a bone by deg about an axis of its game frame (its rest, FP's ue_rest)."""
    pb = obj.pose.bones[name]
    off = ue_offset(obj.data.bones[name]).to_3x3()
    pb.rotation_mode = 'QUATERNION'
    pb.rotation_quaternion = Quaternion(off @ Vector(axis), math.radians(deg))
    obj.update_tag()
    bpy.context.view_layer.update()


lib = dr.library()
tun = dr.tuning(None)
# Epic's spherical pose reader on the upper arm's reader: the driver's direction in the sphere's frame (its Z is the
# region's centre) -> the output, as the engine gives it (checked against UE 5.8)
UP = next(p["weight"] for p in lib["parts"] if p["bone"] == "deform_upperarm_up_l")
for deg, az, want in ((0, 0, 1.0), (180, 0, 0.0), (90, 0, 0.0), (60, 0, 0.666667), (70, 0, 0.444444), (70, 180, 0.555556),
                      (75, 90, 0.416667), (80, 270, 0.277778), (65, 45, 0.611028), (85, 135, 0.138889)):
    t, a = math.radians(deg), math.radians(az)
    n = (math.sin(t) * math.cos(a), math.sin(t) * math.sin(a), math.cos(t))
    check("Epic's reader at %d deg, azimuth %d" % (deg, az), round(deform_rig.spr_value(n, UP), 5), round(want, 5))
sk = skeleton("deform")
rest = {b.name: (sk.matrix_world @ b.matrix_local).copy() for b in sk.data.bones}
built = deform_rig.build(sk, lib, tun)
check("correctives built", built > 0, True)
moved = [n for n, m in rest.items() if n in sk.pose.bones and
         ((world(sk, n).to_translation() - m.to_translation()).length > 1e-4 or
          world(sk, n).to_quaternion().rotation_difference(m.to_quaternion()).angle > 1e-3)]
check("rest stays rest", moved, [])
FLIP = Matrix(((1, 0, 0), (0, -1, 0), (0, 0, 1)))


def offset_ue(obj, name):
    """A corrective's pose offset (its basis location) in its game frame, in Epic's units and axes (cm)."""
    pb = obj.pose.bones[name]
    game = ue_offset(obj.data.bones[name]).to_3x3().inverted() @ pb.location
    return FLIP @ game * 100.0


elbow_value = Vector(tun["values"]["elbow_in"]["t"])
peak = 0.0
for deg in (-120, -60, 0, 60, 120):
    turn(sk, "lowerarm_l", (0, 0, 1), deg)
    pb = sk.pose.bones["deform_elbow_in_l"]
    w_ref = deform_rig.reference_weight(sk, pb.get("dr_spec0"))
    check("elbow %d: the driven weight is Epic's" % deg, round(pb["dr_w0"], 4), round(w_ref, 4))
    check("elbow %d: the corrective moves by weight x value" % deg, tuple(offset_ue(sk, "deform_elbow_in_l")),
          tuple(elbow_value * w_ref), tol=1e-3)
    peak = max(peak, w_ref)
check("a bent elbow reaches the corrective", peak > 0.9, True)
turn(sk, "lowerarm_l", (0, 0, 1), 0)
for axis in ((0, 0, 1), (0, 1, 0)):
    for deg in (-80, -30, 30, 80):
        turn(sk, "upperarm_l", axis, deg)
        pb = sk.pose.bones["deform_upperarm_up_l"]
        check("shoulder %s %d: the cone reader's weight is Epic's" % (axis, deg), round(pb["dr_w0"], 3),
              round(deform_rig.reference_weight(sk, pb.get("dr_spec0")), 3))
turn(sk, "upperarm_l", (0, 0, 1), 0)
# the base bone blends its two sources: elbow_in base = 0.6 upper arm, 0.4 forearm
turn(sk, "lowerarm_l", (0, 0, 1), 60)
base_turn = (world(sk, "deform_elbow_in_base_l").to_quaternion().rotation_difference(rest["deform_elbow_in_base_l"].to_quaternion())).angle
check("the base takes 40% of the forearm's turn", round(math.degrees(base_turn), 1), 24.0, tol=1.0)
# the right side mirrors: with Epic's right axes, the mirrored pose is the same local turn
for limb, part, axis in (("lowerarm", "deform_elbow_in", (0, 0, 1)), ("upperarm", "deform_upperarm_up", (0, 1, 0)),
                         ("hand", "deform_wrist_up", (0, 0, 1))):
    moves = 0.0
    for deg in (-110, -70, 70, 110):
        for side in "lr":
            turn(sk, limb + "_" + side, axis, deg)
        wl, wr = sk.pose.bones[part + "_l"]["dr_w0"], sk.pose.bones[part + "_r"]["dr_w0"]
        check("mirrored %s %s %d: equal weights" % (part, axis, deg), round(wr, 4), round(wl, 4))
        check("mirrored %s %s %d: mirrored offsets" % (part, axis, deg), tuple(offset_ue(sk, part + "_r")),
              tuple(-offset_ue(sk, part + "_l")), tol=1e-3)
        moves = max(moves, wl)
    check("mirrored %s %s: the weight moves" % (part, axis), moves > 0.05, True)
    for side in "lr":
        turn(sk, limb + "_" + side, axis, 0)
# off: the correctives rest
deform_rig.set_on(sk, False)
bpy.context.view_layer.update()
check("off: the corrective rests", tuple(offset_ue(sk, "deform_elbow_in_l")), (0.0, 0.0, 0.0), tol=1e-6)
deform_rig.set_on(sk, True)
drivers = sk.animation_data.drivers
check("every driver simple, at most 255 characters", all(d.driver.is_simple_expression and len(d.driver.expression) <= 255
                                                       for d in drivers), True)
# a skeleton without the knee correctives builds the rest (Review Focus 2)
nk = skeleton("no_knee", drop=("knee_in",))
check("a missing part is skipped", deform_rig.build(nk, lib, tun) > 0 and "DR_read_calf_l" not in nk.data.bones, True)
check("a second build adds nothing (Review Focus 5)", deform_rig.build(nk, lib, tun), 0)
# every part switched on (as VibrantShell's rig has it): parts without a base bone build too
DEFORM["bicep"] = "upperarm"
on = skeleton("all_on")
bpy.context.view_layer.objects.active = on
bpy.ops.object.mode_set(mode='EDIT')
for side, sx in (("l", 1.0), ("r", -1.0)):
    aim = on.data.edit_bones.new("deform_pec_aim_" + side)
    aim.head, aim.tail, aim.parent = (0.08 * sx, -0.08, 1.33), (0.08 * sx, -0.08, 1.37), on.data.edit_bones["spine_04"]
    pec = on.data.edit_bones.new("deform_pec_" + side)
    pec.head, pec.tail, pec.parent = (0.1 * sx, -0.1, 1.33), (0.1 * sx, -0.1, 1.36), aim
bpy.ops.object.mode_set(mode='OBJECT')
check("all parts on: the bicep builds", deform_rig.build(on, lib, dict(tun, switches={k: True for k in tun["switches"]})) > 0
      and "deform_bicep_l" in on.data[deform_rig.MARK], True)
check("the pec aims at the upper arm", [c.subtarget for c in on.pose.bones["deform_pec_aim_l"].constraints if c.type == 'DAMPED_TRACK'],
      ["upperarm_l"])

# --- extras: a character's own SPR-weighted turn and slide on a bone, after the library parts
from types import SimpleNamespace  # noqa: E402
from fpmp_baseline.material_porter import mesh_hooks  # noqa: E402
ex = skeleton("extras")
bpy.ops.object.mode_set(mode='EDIT')
b = ex.data.edit_bones.new("dyn_bracelet_1_l")
b.head, b.tail, b.parent = (0.7, 0, 1.42), (0.7, 0, 1.46), ex.data.edit_bones["hand_l"]
bpy.ops.object.mode_set(mode='OBJECT')
cone = dict(lib["parts"][[p["bone"] for p in lib["parts"]].index("deform_wrist_up_l")]["weight"])
extra = {"op": "modify", "bone": "dyn_bracelet_1_l", "t": [0.0, 0.0, 5.0], "s": [1.0, 1.0, 1.0], "euler": [0.0, -10.0, 0.0],
         "order": 0, "weight": cone}
deform_rig.build(ex, lib, dict(tun, extras=[extra]))
moved = 0.0
for deg in (-90, -45, 45, 90):
    turn(ex, "hand_l", (0, 0, 1), deg)
    pb = ex.pose.bones["dyn_bracelet_1_l"]
    k = len([key for key in pb.keys() if key.startswith("dr_spec")]) - 1
    w_ref = deform_rig.reference_weight(ex, pb["dr_spec%d" % k])
    check("extra %d: its weight is the reader's" % deg, round(pb["dr_w%d" % k], 3), round(w_ref, 3))
    check("extra %d: it slides by weight x value" % deg, tuple(offset_ue(ex, "dyn_bracelet_1_l")), (0.0, 0.0, 5.0 * w_ref), tol=1e-3)
    check("extra %d: it turns by weight x angle" % deg, round(math.degrees(pb.rotation_euler.to_quaternion().angle), 2),
          round(10.0 * w_ref, 2), tol=0.05)
    moved = max(moved, w_ref)
check("the extra's reader moves", moved > 0.05, True)
check("no reader driver broke on the way (a division by zero breaks one for good)",
      [d.data_path for d in ex.animation_data.drivers if not d.driver.is_valid], [])

# --- the import hook: a part's meta names its rig dump; keyed limbs drive the correctives
hooked = skeleton("hooked")
rig_file = write("hooked.deformrig.json", dump())
mesh_hooks._correctives(SimpleNamespace(imported_meshes=[{"Skeleton": hooked, "Mesh": None, "Meta": {"DeformRig": rig_file}}]))
check("the hook builds the correctives", bool(hooked.data.get(deform_rig.MARK)), True)
check("the character's switch: elbow off", "deform_elbow_in_l" in hooked.data[deform_rig.MARK], False)
hooked.animation_data_create()
hooked.animation_data.action = bpy.data.actions.new("bend")
calf = hooked.pose.bones["calf_l"]
calf.rotation_mode = 'QUATERNION'
for frame, deg in ((1, 0), (10, -80)):
    calf.rotation_quaternion = Quaternion((0, 0, 1), math.radians(deg))
    calf.keyframe_insert("rotation_quaternion", frame=frame)
bpy.context.scene.frame_set(10)
knee = hooked.pose.bones["deform_knee_in_l"]
check("keyed limbs drive the corrective", round(knee["dr_w0"], 4), round(deform_rig.reference_weight(hooked, knee["dr_spec0"]), 4))
check("keyed bend reaches it", knee["dr_w0"] > 0.5, True)
counts = (len(hooked.data.bones), len(hooked.animation_data.drivers))
mesh_hooks._correctives(SimpleNamespace(imported_meshes=[{"Skeleton": hooked, "Mesh": None, "Meta": {"DeformRig": rig_file}}]))
check("a second hook call adds nothing", (len(hooked.data.bones), len(hooked.animation_data.drivers)), counts)
plain = bpy.data.objects.new("plain", bpy.data.armatures.new("plain"))
bpy.context.scene.collection.objects.link(plain)
mesh_hooks._correctives(SimpleNamespace(imported_meshes=[{"Skeleton": plain, "Mesh": None, "Meta": None}]))
check("no deform bones: nothing", plain.data.get(deform_rig.MARK), None)

# --- review fixes
# an extra on a bone the library also blends: Epic adds it after the blend
cb = skeleton("constrained_extra")
deform_rig.build(cb, lib, dict(tun, extras=[{"op": "modify", "bone": "deform_elbow_in_base_l", "t": [0.0, 0.0, 5.0],
                                             "s": [1.0, 1.0, 1.0], "euler": None, "order": 0,
                                             "weight": {"type": "const", "value": 1.0}}]))
before = (cb.matrix_world @ cb.data.bones["deform_elbow_in_base_l"].matrix_local).to_translation()
check("an extra on a blended bone still slides it", round((world(cb, "deform_elbow_in_base_l").to_translation() - before).length, 4), 0.05)
# FP's Scale Down off: an import at 1 unit per cm slides by cm
big = skeleton("unscaled")
deform_rig.build(big, lib, tun, scale=1.0)
turn(big, "lowerarm_l", (0, 0, 1), -120)
check("unscaled imports slide in cm", round(big.pose.bones["deform_elbow_in_l"].location.length, 3), 9.0)
# a dump whose forward solve can't be found reads as the library, whatever the error
broken = dump()
broken[0]["ByteCodeStorage"]["Entries"] = ['(Name="Forwards Solve")']
check("an unreadable dump: the library values", dr.tuning(write("broken.json", broken))["values"], lib["values"])


def extra_dump(mode=None, mul=None):
    """dump() with a ModifyTransforms mode literal, or its weight a constant product."""
    d = dump()
    vm = d[0]
    lits = vm["LiteralMemoryStorage"]
    for name, value in (("mode", mode), ("a", None if mul is None else mul[0]), ("b", None if mul is None else mul[1])):
        lits["PropertyDescs"].append({"Name": name})
        lits["Values"][name] = value
    n = len(lits["PropertyDescs"])
    work = vm["DefaultWorkMemoryStorage"]["PropertyDescs"]
    work.append({"Name": "product"})
    functions = vm["LocalizedRegistry"]["Functions"]
    functions[2]["Arguments"].append({"LocalNameString": "Mode"})
    functions.append({"Name": "FRigVMFunction_MathFloatMul::Execute", "Arguments": [{"LocalNameString": x} for x in ("A", "B", "Result")]})
    vm["FunctionNamesStorage"].append(functions[-1]["Name"])
    code = vm["ByteCodeStorage"]["Instructions"]
    modify = code[-2]
    modify["Arguments"].append({"MemoryType": 1, "RegisterIndex": n - 3, "RegisterOffset": 65535})
    if mul is not None:
        code.insert(-2, {"OpCode": 101, "FunctionIndex": 3, "Arguments": [
            {"MemoryType": 1, "RegisterIndex": n - 2, "RegisterOffset": 65535}, {"MemoryType": 1, "RegisterIndex": n - 1, "RegisterOffset": 65535},
            {"MemoryType": 0, "RegisterIndex": len(work) - 1, "RegisterOffset": 65535}]})
        modify["Arguments"][1] = {"MemoryType": 0, "RegisterIndex": len(work) - 1, "RegisterOffset": 65535}
    return d


check("an additive modify reads", len(dr.read(write("add.json", extra_dump(mode=2)))["extras"]), 1)
check("an override modify stops the extras", dr.read(write("override.json", extra_dump(mode=0)))["extras"], [])
check("a constant product weight is a constant", dr.read(write("mul.json", extra_dump(mul=(0.5, 2.0))))["extras"][0]["weight"],
      {"type": "const", "value": 1.0})
# several parts with rigs: the one that calls FortDeformRig tunes the body
two = skeleton("two_rigs")
mesh_hooks._correctives(SimpleNamespace(imported_meshes=[{"Skeleton": None, "Mesh": None, "Meta": {"DeformRig": write("face.json", dump(call=False))}},
                                                         {"Skeleton": two, "Mesh": None, "Meta": {"DeformRig": rig_file}}]))
check("the body's rig is the one read", "deform_elbow_in_l" in two.data[deform_rig.MARK], False)


class Layout:
    """Stands in for a panel layout (headless Blender draws no panels)."""
    def __init__(self, log):
        self.log = log

    def column(self, **_):
        return self

    row = column

    def operator(self, idname, text="", **_):
        self.log.append((idname, text))
        return SimpleNamespace()


drawn = []
deform_rig.ui(Layout(drawn), hooked)
check("the panel shows Correctives", [t for _, t in drawn], ["Correctives"])
drawn = []
deform_rig.ui(Layout(drawn), plain)
check("no correctives, no row", drawn, [])

print("[deform_rig_check] %d passed, %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
