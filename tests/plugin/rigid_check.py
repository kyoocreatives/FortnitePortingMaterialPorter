"""Checks Fortnite's RigidBody chains: the physics asset and anim blueprint readers, the bodies, joints and contacts
against the engine's formulas, the node and the bake on a synthetic head and hair chain.

    blender -b --factory-startup --python-exit-code 1 -P tests/plugin/rigid_check.py -- <plugin parent>

<plugin parent> holds the plugin as package fpmp_baseline (see translator_test.py). Exit code 0 when every check passes."""
import json
import math
import os
import sys
import tempfile

import bpy  # noqa: F401
from mathutils import Quaternion, Vector

sys.path.insert(0, sys.argv[sys.argv.index("--") + 1])
import fpmp_baseline  # noqa: E402,F401
from fpmp_baseline.processing.context import dynamics_read as dr  # noqa: E402
from fpmp_baseline.processing.context import physics_read as pr  # noqa: E402

FAILS, PASSES = [], [0]
TMP = tempfile.mkdtemp()


def check(name, got, want, tol=1e-4):
    if isinstance(want, float):
        ok = got is not None and abs(got - want) <= tol
    elif isinstance(want, (tuple, list)) and want and all(isinstance(w, float) for w in want):
        ok = len(got) == len(want) and all(abs(g - w) <= tol for g, w in zip(got, want))
    else:
        ok = got == want
    if ok:
        PASSES[0] += 1
    else:
        FAILS.append(name)
        print("[rigid_check] FAIL %s: got %r, want %r" % (name, got, want))


def dump(path, exports):
    json.dump(exports, open(path, "w", encoding="utf-8"))
    return path


def body_setup(name, bone, kind="Simulated", radius=1.5, length=6.0, inst=None):
    return {"Type": "SkeletalBodySetup", "Name": name,
            "Properties": {"BoneName": bone, "PhysicsType": "EPhysicsType::PhysType_" + kind,
                           "AggGeom": {"SphylElems": [{"Center": {"X": length / 2 + radius, "Y": 0.0, "Z": 0.0},
                                                       "Rotation": {"Pitch": 90.0, "Yaw": 0.0, "Roll": 0.0},
                                                       "Radius": radius, "Length": length}]},
                           **({"DefaultInstance": inst} if inst else {})}}


PHYS = dump(os.path.join(TMP, "x.physics.json"), [
    {"Type": "PhysicsAsset", "Name": "X_Physics",
     "Properties": {"SkeletalBodySetups": [{"ObjectName": "SkeletalBodySetup'X_Physics:SkeletalBodySetup_1'"},
                                           {"ObjectName": "SkeletalBodySetup'X_Physics:SkeletalBodySetup_0'"}],
                    "SolverSettings": {"PositionIterations": 8, "bUseLinearJointSolver": False}},
     "CollisionDisableTable": [{"Key": {"Indices": [0, 1]}, "Value": False}]},
    body_setup("SkeletalBodySetup_0", "dyn_hair_1", inst={"MassInKgOverride": 2.0, "bOverrideMass": True, "AngularDamping": 15.0}),
    body_setup("SkeletalBodySetup_1", "head", kind="Kinematic"),
    {"Type": "PhysicsConstraintTemplate", "Name": "PhysicsConstraintTemplate_0",
     "Properties": {"DefaultInstance": {"ConstraintBone1": "dyn_hair_1", "ConstraintBone2": "head", "Pos2": {"X": 4.0, "Y": 0.0, "Z": 0.0},
                                        "ProfileInstance": {"ConeLimit": {"Swing1LimitDegrees": 30.0, "Swing1Motion": "EAngularConstraintMotion::ACM_Limited",
                                                                          "Swing2Motion": "EAngularConstraintMotion::ACM_Limited", "Damping": 20.0},
                                                            "TwistLimit": {"TwistMotion": "EAngularConstraintMotion::ACM_Locked"},
                                                            "AngularDrive": {"SlerpDrive": {"Stiffness": 100.0, "bEnablePositionDrive": True}},
                                                            "ProjectionAngularAlpha": 0.8}}}}])

# --- readers ---
asset = pr.asset(PHYS)
check("bodies in the asset's own order", [b["bone"] for b in asset["bodies"]], ["head", "dyn_hair_1"])
check("body types", [b["type"] for b in asset["bodies"]], ["Kinematic", "Simulated"])
hair = asset["bodies"][1]
check("mass override and damping, defaults elsewhere", (hair["mass"], hair["angular_damping"], hair["linear_damping"]), (2.0, 15.0, 0.01))
check("capsule read", (hair["shapes"][0]["kind"], hair["shapes"][0]["radius"], hair["shapes"][0]["length"]), ("capsule", 1.5, 6.0))
joint = asset["constraints"][0]
check("joint bones and frames", (joint["bone1"], joint["bone2"], joint["pos2"], joint["pri1"]), ("dyn_hair_1", "head", [4.0, 0.0, 0.0], [1.0, 0.0, 0.0]))
check("soft cone with its own damping, default stiffness", (joint["cone"]["soft"], joint["cone"]["stiffness"], joint["cone"]["damping"],
                                                            joint["cone"]["swing1"], joint["cone"]["swing2"]), (True, 50.0, 20.0, 30.0, 45.0))
check("locked twist", joint["twist"]["TwistMotion"], "Locked")
check("slerp drive", (joint["drive"]["slerp"]["stiffness"], joint["drive"]["slerp"]["damping"], joint["drive"]["slerp"]["position"]), (100.0, 1.0, True))
check("projection defaults and override", (joint["projection"]["linear_tolerance"], joint["projection"]["linear_alpha"],
                                           joint["projection"]["angular_alpha"]), (5.0, 1.0, 0.8))
check("disabled pairs by bone", asset["disabled"], [["head", "dyn_hair_1"]])
check("solver settings, defaults elsewhere", (asset["solver"]["position_iterations"], asset["solver"]["velocity_iterations"],
                                              asset["solver"]["projection_iterations"], asset["solver"]["linear_solver"],
                                              asset["solver"]["cull_distance"]), (8, 1, 1, False, 3.0))
check("a missing asset reads None", pr.asset(PHYS + ".none"), None)

ABP = dump(os.path.join(TMP, "x.animbp.json"), [
    {"Type": "AnimBlueprintGeneratedClass", "Name": "X_C",
     "ChildProperties": [{"Name": n} for n in ("AnimGraphNode_CopyPoseFromMesh", "AnimGraphNode_RigidBody", "AnimGraphNode_Root",
                                                "AnimGraphNode_Constraint", "AnimGraphNode_ModifyBone", "AnimGraphNode_TwoBoneIK")]},
    {"Type": "X_C", "Name": "Default__X_C",
     "Properties": {"AnimGraphNode_CopyPoseFromMesh": {},
                    "AnimGraphNode_RigidBody": {"ComponentPose": {"LinkID": 0}, "SimulationSpace": "ESimulationSpace::BaseBoneSpace",
                                                "BaseBoneRef": {"BoneName": "neck_02"}, "bOverrideWorldGravity": True,
                                                "OverrideWorldGravity": {"X": 0.0, "Y": 0.0, "Z": -980.0},
                                                "SimSpaceSettings": {"WorldAlpha": 1.0, "MaxAngularVelocity": 3.5}},
                    "AnimGraphNode_Constraint": {"ComponentPose": {"LinkID": 1}, "BoneToModify": {"BoneName": "dyn_hair_1"},
                                                 "ConstraintSetup": [{"TargetBone": {"BoneName": "head"}, "OffsetOption": "EConstraintOffsetOption::Offset_RefPose",
                                                                      "TransformType": "ETransformConstraintType::Parent",
                                                                      "PerAxis": {"bX": True, "bY": True, "bZ": True}}],
                                                 "ConstraintWeights": [0.5]},
                    "AnimGraphNode_ModifyBone": {"ComponentPose": {"LinkID": 3}, "BoneToModify": {"BoneName": "dyn_hair_1"},
                                                 "Translation": {"X": 0.5, "Y": 0.0, "Z": 2.5}, "Rotation": {"Pitch": 0.0, "Yaw": 0.0, "Roll": -10.0},
                                                 "TranslationMode": "EBoneModificationMode::BMM_Additive", "RotationMode": "EBoneModificationMode::BMM_Additive",
                                                 "TranslationSpace": "EBoneControlSpace::BCS_BoneSpace", "RotationSpace": "EBoneControlSpace::BCS_BoneSpace"},
                    "AnimGraphNode_TwoBoneIK": {"ComponentPose": {"LinkID": 4}},
                    "AnimGraphNode_Root": {"Result": {"LinkID": 5}}}}])
read = dr.nodes([ABP], [PHYS])
check("every replayed kind in graph order", [n["kind"] for n in read], ["rigid_body", "constraint", "modify_bone"])
rb = read[0]
check("rigid node settings", (rb["space"], rb["base_bone"], rb["gravity"], rb["sim_space"]["world_alpha"], rb["sim_space"]["max_angular_velocity"],
                              rb["sim_space"]["max_linear_velocity"]), ("BaseBoneSpace", "neck_02", [0.0, 0.0, -980.0], 1.0, 3.5, 10000.0))
check("rigid node carries its physics asset", [b["bone"] for b in rb["physics"]["bodies"]], ["head", "dyn_hair_1"])
cn = read[1]
check("constraint node", (cn["bone"], cn["setups"][0]["target"], cn["setups"][0]["type"], cn["setups"][0]["offset"], cn["weights"]),
      ("dyn_hair_1", "head", "Parent", "Offset_RefPose", [0.5]))
mb = read[2]
check("modify bone node", (mb["bone"], mb["translation"], mb["rotation"], mb["translation_mode"], mb["rotation_space"]),
      ("dyn_hair_1", [0.5, 0.0, 2.5], [0.0, 0.0, -10.0], "Additive", "BoneSpace"))
check("a rigid node without its asset is dropped", [n["kind"] for n in dr.nodes([ABP], [None])], ["constraint", "modify_bone"])

# --- bodies ---
from fpmp_baseline.processing.context import rigid_bodies as rbd  # noqa: E402

capsule = {"shapes": [{"kind": "capsule", "center": [4.5, 0.0, 0.0], "rotation": [90.0, 0.0, 0.0], "radius": 1.5, "length": 6.0}],
           "mass": None, "mass_scale": 1.0, "com_nudge": [0.0, 0.0, 0.0]}
m = rbd.mass_properties(capsule)
check("capsule mass from volume^0.75", m.mass, (math.pi * 2.25 * 8.0 * 0.001) ** 0.75)
check("capsule inertia across and along", sorted(m.inertia), sorted([m.mass * 2875.5 / 480.0, m.mass * 2875.5 / 480.0, m.mass * 2.25 * 114.0 / 240.0]), 1e-6)
check("capsule centre of mass", tuple(m.com), (4.5, 0.0, 0.0))
check("capsule axis follows its rotator (pitch 90 lies along X)", (round(abs((m.shapes[0].b - m.shapes[0].a).normalized().x), 6)), 1.0)
check("mass override", rbd.mass_properties(dict(capsule, mass=2.0)).mass, 2.0)
nudged = rbd.mass_properties(dict(capsule, com_nudge=[0.0, 0.0, 0.5]))
check("com nudge moves the centre", tuple(nudged.com), (4.5, 0.0, 0.5))
scale = rbd.conditioning(m, [(0.0, 0.0, 0.0), (9.0, 0.0, 0.0)])
check("conditioning shrinks a chain capsule's turning", all(0.25 < s < 0.5 for s in sorted(scale)[:2]) and max(scale) <= 1.0, True)
print("[rigid_check] conditioning", tuple(round(s, 3) for s in scale))

# --- joints ---
from fpmp_baseline.processing.context import rigid_joints as rj  # noqa: E402

DT = 1.0 / 60.0
LIMITED = "EAngularConstraintMotion::ACM_Limited"


def make_joint(profile, pos1=(0.0, 0.0, 0.0), pos2=(0.0, 0.0, 0.0), linear_free=False):
    if linear_free:
        profile = dict(profile, LinearLimit={"XMotion": "ELinearConstraintMotion::LCM_Free", "YMotion": "ELinearConstraintMotion::LCM_Free",
                                             "ZMotion": "ELinearConstraintMotion::LCM_Free"})
    c = pr._constraint({"DefaultInstance": {"ConstraintBone1": "c", "ConstraintBone2": "p", "Pos1": dict(zip("XYZ", pos1)),
                                            "Pos2": dict(zip("XYZ", pos2)), "ProfileInstance": profile}})
    return rj.Joint(rj.settings(c), 0, 1)


def pair(child_rot=Quaternion(), child_pos=Vector((0.0, 0.0, 0.0))):
    parent = rj.SolverBody(Vector(), Quaternion(), Vector(), Quaternion(), Vector(), Vector(), 0.0, Vector(), False)
    child = rj.SolverBody(child_pos.copy(), child_rot.copy(), child_pos.copy(), child_rot.copy(), Vector(), Vector(), 1.0,
                          Vector((1.0, 1.0, 1.0)), True)
    return [parent, child]


swing_only = {"ConeLimit": {"Swing1LimitDegrees": 30.0, "Swing1Motion": LIMITED}}
bodies = pair(Quaternion(Vector((0, 0, 1)), math.radians(40.0)))
j = make_joint(swing_only, linear_free=True)
j.init(DT, bodies)
row = j.rot_rows[0]
before = row.cx - row.limit
j.apply_position(DT, bodies)
after = row.cx + (bodies[1].dq - bodies[0].dq).dot(row.axis) - row.limit
check("a soft swing limit removes ~94% of its excess per pass", after / before, 1.0 - 1389.0 / (1.0 + 1389.0 + 83.33), 2e-3)

bodies = pair(Quaternion(Vector((0, 0, 1)), math.radians(40.0)) @ Quaternion(Vector((0, 1, 0)), math.radians(10.0)))
j = make_joint({"ConeLimit": {"Swing1LimitDegrees": 30.0, "Swing1Motion": LIMITED, "Swing2LimitDegrees": 20.0, "Swing2Motion": LIMITED}},
               linear_free=True)
j.init(DT, bodies)
j.apply_position(DT, bodies)
check("pyramid swings: only the limit that is past acts", (len(j.rot_rows), abs(bodies[1].dq.normalized().dot(j.rot_rows[1].axis)) > 0.99),
      (2, True))

bodies = pair(Quaternion(Vector((1, 0, 0)), math.radians(20.0)))
j = make_joint({"TwistLimit": {"TwistLimitDegrees": 10.0, "TwistMotion": LIMITED}}, linear_free=True)
j.init(DT, bodies)
check("twist angle and axis", (round(math.degrees(j.rot_rows[0].cx), 3), tuple(round(x, 4) for x in j.rot_rows[0].axis)), (20.0, (1.0, 0.0, 0.0)))

bodies = pair(child_pos=Vector((1.0, 0.0, 0.0)))
j = make_joint({})
j.init(DT, bodies)
j.apply_position(DT, bodies)
gap = (j.cx1 + bodies[1].dp + bodies[1].dq.cross(j.lin_rows[0].arm1)) - j.cx0
check("a locked joint pulls its child onto the connector in one pass", gap.length < 1e-9, True)

bodies = pair(child_pos=Vector((6.0, 0.0, 0.0)))
j = make_joint({})
j.init(DT, bodies)
j.init_projection(DT, bodies)
j.apply_teleport(bodies)
check("projection teleports a child over 5 cm", tuple(bodies[1].dp), (-6.0, 0.0, 0.0), 1e-6)
j.apply_projection(DT, bodies, last=True)
check("and passes 10% of the move into its velocity", tuple(bodies[1].v), (-6.0 * 0.1 / DT, 0.0, 0.0), 1e-4)

bodies = pair(Quaternion(Vector((0, 1, 0)), math.radians(30.0)))
j = make_joint({"TwistLimit": {"TwistMotion": LIMITED, "TwistLimitDegrees": 90.0}, "ConeLimit": {"Swing1Motion": LIMITED, "Swing2Motion": LIMITED,
               "Swing1LimitDegrees": 90.0, "Swing2LimitDegrees": 90.0}, "AngularDrive": {"SlerpDrive": {"Stiffness": 100.0, "bEnablePositionDrive": True}}},
               linear_free=True)
j.init(DT, bodies)
j.apply_position(DT, bodies)
turned = rj.integrate(bodies[1].q, bodies[1].dq, 1.0)
check("a slerp drive turns the child back toward its target", math.degrees(turned.angle) < 29.0, True)

m0, m1, i0, i1 = rj.condition(1.0 / 0.01, 1.0, Vector((1.0, 1.0, 1.0)), Vector((1.0, 1.0, 1.0)))
check("mass conditioning keeps the parent at 0.2 of the child", 1.0 / m0, 0.2, 1e-9)
check("inertia ratio capped at 5", tuple(rj._condition_inertia(Vector((1.0, 2.0, 50.0)))), (10.0, 10.0 + 40.0 / 49.0, 50.0), 1e-5)

# --- contacts ---
from fpmp_baseline.processing.context import rigid_contacts as rc  # noqa: E402

sphere = [(Vector(), Vector(), 2.0)]
a = rj.SolverBody(Vector(), Quaternion(), Vector(), Quaternion(), Vector(), Vector(), 1.0, Vector((1.0, 1.0, 1.0)), True)
b = rj.SolverBody(Vector((3.0, 0.0, 0.0)), Quaternion(), Vector((3.0, 0.0, 0.0)), Quaternion(), Vector(), Vector(), 1.0, Vector((1.0, 1.0, 1.0)), True)
found = rc.detect([a, b], [(0, 1)], [sphere, sphere], 3.0)
check("overlapping spheres make one contact", (len(found), round(found[0].phi, 6)), (1, -1.0))
rc.apply_position(found, [a, b])
gap = ((b.p + b.dp) - (a.p + a.dp)).length - 4.0
check("the contact pushes them apart", abs(gap) < 1e-6, True)
check("a pair not listed never touches", rc.detect([a, b], [], [sphere, sphere], 3.0), [])
far = rj.SolverBody(Vector((10.0, 0.0, 0.0)), Quaternion(), Vector((10.0, 0.0, 0.0)), Quaternion(), Vector(), Vector(), 1.0, Vector((1.0, 1.0, 1.0)), True)
check("beyond the cull distance: no contact", rc.detect([a, far], [(0, 1)], [sphere, sphere], 3.0), [])
p0, p1 = rc.closest(Vector((0, 0, 0)), Vector((10, 0, 0)), Vector((5, 3, -1)), Vector((5, 3, 1)))
check("closest points of crossing segments", (tuple(p0), tuple(p1)), ((5.0, 0.0, 0.0), (5.0, 3.0, 0.0)))

# --- node ---
from fpmp_baseline.processing.context import rigid_solver as rs  # noqa: E402


def body(bone, kind, length=6.0, radius=1.0, damping=(0.01, 0.0)):
    return {"bone": bone, "type": kind, "shapes": [{"kind": "capsule", "center": [length / 2 + radius, 0.0, 0.0], "rotation": [90.0, 0.0, 0.0],
                                                     "radius": radius, "length": length, "mass": True}],
            "mass": None, "mass_scale": 1.0, "inertia_scale": [1.0, 1.0, 1.0], "com_nudge": [0.0, 0.0, 0.0],
            "linear_damping": damping[0], "angular_damping": damping[1], "gravity": True, "position_iterations": 8, "velocity_iterations": 2}


def rigid_node(world_alpha=0.0, space="BaseBoneSpace"):
    hinge = pr._constraint({"DefaultInstance": {"ConstraintBone1": "hair", "ConstraintBone2": "head", "Pos2": {"X": 0.0, "Y": 0.0, "Z": 0.0},
                                                "ProfileInstance": {"TwistLimit": {"TwistMotion": "EAngularConstraintMotion::ACM_Locked"}}}})
    asset = {"bodies": [body("head", "Kinematic", radius=3.0), body("hair", "Simulated")], "constraints": [hinge], "disabled": [["head", "hair"]],
             "solver": {"position_iterations": 6, "velocity_iterations": 1, "projection_iterations": 1, "cull_distance": 3.0,
                        "max_depenetration": 0.0, "fixed_step": 0.0, "linear_solver": True}}
    n = dr.rigid({"SimulationSpace": "ESimulationSpace::" + space, "BaseBoneRef": {"BoneName": "head"},
                  "SimSpaceSettings": {"WorldAlpha": world_alpha}}, asset)
    return n


bones = {"head": None, "hair": "head"}
pose = {"head": (Quaternion(), Vector((0.0, 0.0, 100.0))), "hair": (Quaternion(), Vector((0.0, 0.0, 100.0)))}
sim = rs.RigidSim(rigid_node(), bones)
check("the reset frame passes the pose through", sim.evaluate(DT, pose, "head"), {})
for _ in range(240):
    out = sim.evaluate(DT, pose, "head")
tip = out["hair"][0] @ Vector((1.0, 0.0, 0.0))
check("a hair capsule settles hanging down", tip.z < -0.95, True)
check("its joint stays on the head", (out["hair"][1] - pose["head"][1]).length < 0.05, True)


def swing(world_alpha):
    s = rs.RigidSim(rigid_node(world_alpha), bones)
    hang = {"head": (Quaternion(), Vector((0.0, 0.0, 100.0))), "hair": (Quaternion(Vector((0, 1, 0)), math.radians(90.0)), Vector((0.0, 0.0, 100.0)))}
    s.evaluate(DT, hang, "head")
    for f in range(30):         # the head speeds up sideways; the chain is still in its own space
        moved = {k: (q, p + Vector((0.5 * 600.0 * (f * DT) ** 2, 0.0, 0.0))) for k, (q, p) in hang.items()}
        out = s.evaluate(DT, moved, "head")
    head = moved["head"][1]
    return math.degrees((out["hair"][0] @ Vector((1, 0, 0))).angle(Vector((0, 0, -1))))


still, pushed = swing(0.0), swing(1.0)
check("without world alpha the base's motion doesn't reach the chain", still < 0.5, True)
check("with world alpha 1 it swings the chain", pushed > 5.0, True)
print("[rigid_check] swing %.2f vs %.2f deg" % (still, pushed))

k = rs.RigidSim(rigid_node(), bones)
k.evaluate(DT, pose, "head")
head = k.actors[0]
head.x, head.r = Vector((0.0, 0.0, 0.0)), Quaternion()
head.target = (Vector((9.0, 0.0, 0.0)), Quaternion())
k._step(DT, 1.0 / 3.0, Vector(), None)
first = head.x.x
k._step(DT, 2.0 / 3.0, Vector(), None)
check("kinematic sub-steps move 1/3 then 7/9 of the way", (first / 9.0, head.x.x / 9.0), (1.0 / 3.0, 7.0 / 9.0), 1e-5)

t = rs.RigidSim(rigid_node(), bones)
t.evaluate(DT, pose, "head")
t.evaluate(1.0 / 60.0, pose, "head")
t.evaluate(1.0 / 30.0, pose, "head")
check("the step time follows the frame time slowly", t.step_time, 1.0 / 60.0 + (1.0 / 30.0 - 1.0 / 60.0) / 100.0, 1e-9)

# --- bone controls ---
from fpmp_baseline.processing.context import bone_controls as bc  # noqa: E402

rest = {"neck": (Quaternion(), Vector((0.0, 0.0, 150.0))), "spine": (Quaternion(), Vector((0.0, 0.0, 120.0))),
        "tail": (Quaternion(), Vector((0.0, -10.0, 130.0)))}
par = {"tail": "neck", "neck": "spine", "spine": None}
two = dr.constraint({"BoneToModify": {"BoneName": "tail"}, "ConstraintWeights": [0.5, 0.5],
                     "ConstraintSetup": [{"TargetBone": {"BoneName": n}, "OffsetOption": "EConstraintOffsetOption::Offset_RefPose",
                                          "TransformType": "ETransformConstraintType::Parent"} for n in ("neck", "spine")]})
ctl = bc.BoneControl(two, par, rest)
at_rest = ctl.evaluate(DT, dict(rest, tail=(Quaternion(Vector((1, 0, 0)), 0.5), Vector((5.0, 5.0, 5.0)))), "spine")["tail"]
check("a parent constraint at the reference pose puts the bone at its reference", (tuple(at_rest[1]), round(at_rest[0].angle, 6)), ((0.0, -10.0, 130.0), 0.0), 1e-4)
moved = ctl.evaluate(DT, dict(rest, neck=(Quaternion(), Vector((10.0, 0.0, 150.0)))), "spine")["tail"]
check("half weight on a moved target moves the bone halfway", tuple(moved[1]), (5.0, -10.0, 130.0), 1e-4)
check("a missing weight counts as zero", dr.constraint({"BoneToModify": {"BoneName": "tail"}, "ConstraintSetup": [{"TargetBone": {"BoneName": "neck"}}]})["weights"], [0.0])

nudge = dr.modify_bone({"BoneToModify": {"BoneName": "tail"}, "Translation": {"X": 0.0, "Y": 0.0, "Z": 2.0},
                        "TranslationMode": "EBoneModificationMode::BMM_Additive", "TranslationSpace": "EBoneControlSpace::BCS_BoneSpace"})
turned = (Quaternion(Vector((1, 0, 0)), math.radians(90.0)), Vector((0.0, 0.0, 0.0)))
out = bc.BoneControl(nudge, par, rest).evaluate(DT, {"tail": turned, "neck": rest["neck"]}, "spine")["tail"]
check("an additive bone-space move follows the bone's own axes", tuple(out[1]), (0.0, -2.0, 0.0), 1e-5)
spin = dr.modify_bone({"BoneToModify": {"BoneName": "tail"}, "Rotation": {"Pitch": 0.0, "Yaw": 90.0, "Roll": 0.0},
                       "RotationMode": "EBoneModificationMode::BMM_Replace", "RotationSpace": "EBoneControlSpace::BCS_ComponentSpace"})
out = bc.BoneControl(spin, par, rest).evaluate(DT, {"tail": turned, "neck": rest["neck"]}, "spine")["tail"]
check("a replaced component-space rotation", tuple(round(x, 5) for x in out[0] @ Vector((1, 0, 0))), (0.0, 1.0, 0.0))

# --- bake ---
from fpmp_baseline.processing.context import dynamics_bake as db  # noqa: E402

scene = bpy.context.scene
scene.render.fps, scene.render.fps_base = 30, 1.0
data = bpy.data.armatures.new("hairy")
arm = bpy.data.objects.new("hairy", data)
scene.collection.objects.link(arm)
bpy.context.view_layer.objects.active = arm
bpy.ops.object.mode_set(mode='EDIT')
eb = data.edit_bones
root = eb.new("root")
root.head, root.tail = (0, 0, 0), (0, 0, 0.1)
head = eb.new("head")
head.head, head.tail, head.parent = (0, 0, 1.0), (0, 0, 1.1), root
hair = eb.new("hair")
hair.head, hair.tail, hair.parent = (0.05, 0, 1.0), (0.15, 0, 1.0), head
bpy.ops.object.mode_set(mode='OBJECT')
for pb in arm.pose.bones:
    pb.rotation_mode = 'QUATERNION'
pb = arm.pose.bones["head"]
for f in (0, 60):
    pb.keyframe_insert("rotation_quaternion", frame=f)


def sphere_body(bone, kind, center):
    return {"bone": bone, "type": kind, "shapes": [{"kind": "sphere", "center": center, "rotation": [0.0, 0.0, 0.0], "radius": 1.0, "mass": True}],
            "mass": None, "mass_scale": 1.0, "inertia_scale": [1.0, 1.0, 1.0], "com_nudge": [0.0, 0.0, 0.0],
            "linear_damping": 0.01, "angular_damping": 0.0, "gravity": True, "position_iterations": 8, "velocity_iterations": 2}


pin = pr._constraint({"DefaultInstance": {"ConstraintBone1": "hair", "ConstraintBone2": "head", "Pos2": {"X": 5.0, "Y": 0.0, "Z": 0.0}}})
phys = {"bodies": [sphere_body("head", "Kinematic", [0.0, -5.0, 0.0]), sphere_body("hair", "Simulated", [0.0, -5.0, 0.0])],
        "constraints": [pin], "disabled": [["head", "hair"]],
        "solver": {"position_iterations": 6, "velocity_iterations": 1, "projection_iterations": 1, "cull_distance": 3.0,
                   "max_depenetration": 0.0, "fixed_step": 0.0, "linear_solver": True}}
rnode = dr.rigid({"SimulationSpace": "ESimulationSpace::ComponentSpace", "bOverrideWorldGravity": True,
                  "OverrideWorldGravity": {"X": 0.0, "Y": 0.0, "Z": -980.0}}, phys)
arm.data[db.KEY] = json.dumps({"scale": 0.01, "nodes": [rnode]})
check("a rigid node bakes its simulated body", db.bake(arm), (1, 61))
paths = sorted({fc.data_path for fc in db._curves(db._track(arm).strips[0].action)})
check("keys land on the body's twin", paths, ['pose.bones["DYN_hair"].location', 'pose.bones["DYN_hair"].rotation_quaternion'])
scene.frame_set(60)
tip = arm.pose.bones["hair"].tail
check("the hair falls and hangs below its joint", (tip.z < 0.95, abs(tip.x - 0.05) < 0.02), (True, True))
joint = arm.pose.bones["hair"].head
check("its joint stays on the head", (joint - Vector((0.05, 0.0, 1.0))).length < 0.002, True)

# --- review fixes ---
loose = dr.constraint({"BoneToModify": {"BoneName": "tail"}, "ConstraintWeights": [1.0],
                       "ConstraintSetup": [{"TargetBone": {"BoneName": "neck"}, "OffsetOption": "EConstraintOffsetOption::None",
                                            "TransformType": "ETransformConstraintType::Parent"}]})
moved_neck = (Quaternion(Vector((0, 0, 1)), 0.3), Vector((3.0, 0.0, 150.0)))
got = bc.BoneControl(loose, par, rest).evaluate(DT, dict(rest, neck=moved_neck), "spine")["tail"]
check("without an offset a parent constraint takes the target as is", (tuple(got[1]), round(got[0].rotation_difference(moved_neck[0]).angle, 5)),
      ((3.0, 0.0, 150.0), 0.0), 1e-4)

shapeless = rigid_node()
shapeless["physics"]["bodies"][1]["shapes"] = []
s = rs.RigidSim(shapeless, bones)
check("a simulated body without shapes is left to the pose", s.outputs(), [])

chain = rigid_node()
second = dict(chain["physics"]["bodies"][1], bone="hair2")
link = pr._constraint({"DefaultInstance": {"ConstraintBone1": "hair2", "ConstraintBone2": "hair", "Pos2": {"X": 8.0, "Y": 0.0, "Z": 0.0}}})
chain["physics"]["bodies"].insert(0, second)
chain["physics"]["constraints"].append(link)
s = rs.RigidSim(chain, {"head": None, "hair": "head", "hair2": "hair"})
s.evaluate(DT, dict(pose, hair2=(Quaternion(), Vector((8.0, 0.0, 100.0)))), "head")
out = s.evaluate(DT, dict(pose, hair2=(Quaternion(), Vector((8.0, 0.0, 100.0)))), "head")
check("bodies come back parents first", list(out), ["hair", "hair2"])

bodies = pair()
bodies[0].dynamic, bodies[0].inv_m, bodies[0].inv_il = True, 1.0, Vector((1.0, 1.0, 1.0))
bodies[1].dynamic, bodies[1].inv_m, bodies[1].inv_il = False, 0.0, Vector()
j = rj.Joint(rj.settings(pr._constraint({"DefaultInstance": {"ConstraintBone1": "c", "ConstraintBone2": "p",
                                                             "ProfileInstance": {"bParentDominates": True}}})), 0, 1)
try:
    j.init(DT, bodies)
    j.apply_position(DT, bodies)
    j.apply_velocity(bodies)
    ok = True
except ZeroDivisionError:
    ok = False
check("a joint with no movable mass does nothing instead of failing", ok, True)

q0, q1 = Quaternion((0.3, -0.5, 0.2), 0.7), Quaternion((-0.1, 0.4, 0.9), 1.3)
v0, v1 = Vector((q0.x, q0.y, q0.z)), Vector((q1.x, q1.y, q1.z))
c, d = v1 * q0.w + v0 * q1.w, q0.w * q1.w - v0.dot(v1)
want = {rj.TWIST: 0.5 * (v0 * v1.x + v1 * v0.x + Vector((d, c.z, -c.y))), rj.SWING2: 0.5 * (v0 * v1.y + v1 * v0.y + Vector((-c.z, d, c.x))),
        rj.SWING1: 0.5 * (v0 * v1.z + v1 * v0.z + Vector((c.y, -c.x, d)))}
got = rj.locked_axes(q0, q1)
check("locked rotation axes", all((got[k] - want[k]).length < 1e-6 for k in (0, 1, 2)), True)

# --- switches, per-node assets, per-state parameters ---
SWITCHED = dump(os.path.join(TMP, "sw.animbp.json"), [
    {"Type": "AnimBlueprintGeneratedClass", "Name": "S_C",
     "ChildProperties": [{"Name": n} for n in ("AnimGraphNode_CopyPoseFromMesh", "AnimGraphNode_RigidBody", "AnimGraphNode_RigidBody_1",
                                                "AnimGraphNode_BlendListByBool", "AnimGraphNode_Root")]},
    {"Type": "S_C", "Name": "Default__S_C",
     "Properties": {"AnimGraphNode_CopyPoseFromMesh": {},
                    "AnimGraphNode_RigidBody": {"ComponentPose": {"LinkID": 0}},
                    "AnimGraphNode_RigidBody_1": {"ComponentPose": {"LinkID": 0}},
                    "AnimGraphNode_BlendListByBool": {"BlendPose": [{"LinkID": 1}, {"LinkID": 2}]},
                    "AnimGraphNode_Root": {"Result": {"LinkID": 3}}}}])
PARAMS = dump(os.path.join(TMP, "x.dynparams.json"), [
    {"Type": "FortCharacterDynamicsParameters", "Name": "X_Parameter",
     "Properties": {"StateNames": ["OnGround_Standing", "EmoteOrMelee"],
                    "GravityOverrideParameters": [{"GravityOverride": {"X": 0.0, "Y": 0.0, "Z": -980.0}, "JointName": "root"},
                                                  {"GravityOverride": {"X": -980.0, "Y": 0.0, "Z": 0.0}, "JointName": "spine_05"}],
                    "RigidBodyAnimNodeParameters": [{"SimSpaceSettings": {"WorldAlpha": 1.0, "MaxAngularVelocity": 3.0}},
                                                    {"SimSpaceSettings": {"WorldAlpha": 0.8, "MaxAngularVelocity": 3.0}}]}}])
other = dump(os.path.join(TMP, "y.physics.json"), [{"Type": "PhysicsAsset", "Name": "Y", "Properties": {"SkeletalBodySetups": []}}])
read = dr.nodes([SWITCHED], [PHYS], [{"AnimGraphNode_RigidBody": other, "AnimGraphNode_RigidBody_1": PHYS}], [PARAMS])
check("a bool switch runs only its false side", len(read), 1)
check("each rigid node gets its own asset", [b["bone"] for b in read[0]["physics"]["bodies"]], ["head", "dyn_hair_1"])
st = read[0]["states"]
check("per-state gravity and joint", (st["EmoteOrMelee"]["gravity"], st["EmoteOrMelee"]["joint"], st["OnGround_Standing"]["gravity"]),
      ([-980.0, 0.0, 0.0], "spine_05", [0.0, 0.0, -980.0]))
check("per-state sim space with defaults", (st["EmoteOrMelee"]["sim_space"]["world_alpha"], st["EmoteOrMelee"]["sim_space"]["max_angular_velocity"],
                                            st["EmoteOrMelee"]["sim_space"]["damping_alpha"]), (0.8, 3.0, 1.0))

grav = rigid_node()
grav["states"] = {"EmoteOrMelee": {"gravity": [-980.0, 0.0, 0.0], "joint": "head", "sim_space": grav["sim_space"]}}
s = rs.RigidSim(grav, bones, state="EmoteOrMelee")
turned_head = (Quaternion(Vector((0, 1, 0)), math.radians(-90.0)), Vector((0.0, 0.0, 100.0)))   # head's -X now points down
hang = {"head": turned_head, "hair": (Quaternion(), Vector((0.0, 0.0, 100.0)))}
s.evaluate(DT, hang, "head")
check("a state's gravity turns with its joint", tuple(round(x, 3) for x in s._gravity(hang, (Quaternion(), Vector()))), (0.0, 0.0, -980.0))
CHAINED = dump(os.path.join(TMP, "ch.animbp.json"), [
    {"Type": "AnimBlueprintGeneratedClass", "Name": "C_C",
     "ChildProperties": [{"Name": n} for n in ("AnimGraphNode_CopyPoseFromMesh", "AnimGraphNode_RigidBody", "AnimGraphNode_ModifyBone",
                                                "AnimGraphNode_BlendListByBool", "AnimGraphNode_Root", "AnimGraphNode_ModifyBone_1")]},
    {"Type": "C_C", "Name": "Default__C_C",
     "Properties": {"AnimGraphNode_CopyPoseFromMesh": {},
                    "AnimGraphNode_RigidBody": {"ComponentPose": {"LinkID": 0}},
                    "AnimGraphNode_ModifyBone": {"ComponentPose": {"LinkID": 1}, "BoneToModify": {"BoneName": "on_true_side"}},
                    "AnimGraphNode_ModifyBone_1": {"ComponentPose": {"LinkID": 0}, "BoneToModify": {"BoneName": "on_false_side"}},
                    "AnimGraphNode_BlendListByBool": {"BlendPose": [{"LinkID": 2}, {"LinkID": 5}]},
                    "AnimGraphNode_Root": {"Result": {"LinkID": 3}}}}])
check("a whole chain behind the inactive side stays off", [n.get("bone") or n["kind"] for n in dr.nodes([CHAINED], [PHYS])], ["on_false_side"])

print("[rigid_check] %d passed, %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
