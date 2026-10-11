"""Checks Fortnite's Chaos cloth replay: the cloth file reader, the solver against the engine's formulas, the render
mapping and the bake on a synthetic skirt.

    blender -b --factory-startup --python-exit-code 1 -P tests/plugin/cloth_check.py -- <plugin parent>

<plugin parent> holds the plugin as package fpmp_baseline (see translator_test.py). Exit code 0 when every check passes."""
import os
import sys
import tempfile

import bpy
import numpy as np

sys.path.insert(0, sys.argv[sys.argv.index("--") + 1])
import fpmp_baseline  # noqa: E402,F401
from fpmp_baseline.processing.context import cloth_render as cr  # noqa: E402

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
        print("[cloth_check] FAIL %s: got %r, want %r" % (name, got, want))


def evaluated(obj, frame):
    bpy.context.scene.frame_set(frame)
    mesh = obj.evaluated_get(bpy.context.evaluated_depsgraph_get()).data
    return [tuple(round(c, 4) for c in v.co) for v in mesh.vertices]


# --- render mapping ---
scene = bpy.context.scene
me = bpy.data.meshes.new("skirt")
me.from_pydata([(5, 5, 5), (6, 6, 6), (7, 7, 7), (8, 8, 8)], [], [])
render = bpy.data.objects.new("skirt", me)
scene.collection.objects.link(render)


def entry(bary, dist, tri, flag=0):
    return list(bary) + [dist] + [0.0, 0.0, 1.0, 0.0] * 2 + list(tri) + [flag, 1.0]


# on the second asset's triangle: vertex 1 all cloth, vertex 2 flagged as never simulated, vertex 3 half blended;
# vertex 0 outside the section
sections = [{"base": 1, "count": 3, "asset": 1, "mapping": [entry((0.2, 0.3, 0.9), 10.0, (0, 1, 2)), entry((1.0, 0.0, 0.0), 0.0, (0, 1, 2), 0xFFFF),
                                                             entry((0.0, 0.0, 1.0), 0.0, (0, 1, 2), 32768)]}]
slots = cr.attach(render, sections, [0, 3])
check("simulated vertices get slots", cr._ints(me, "cloth_slot"), [-1, 0, -1, 1])
check("their simulation vertices, offset by asset", slots["tri"], [[[3, 4, 5]], [[3, 4, 5]]])
two = [{"base": 0, "count": 1, "asset": 0, "mapping": [entry((1.0, 0.0, 0.0), 0.0, (0, 1, 2)) + [], entry((0.0, 1.0, 0.0), 0.0, (0, 1, 2))]}]
two[0]["mapping"][0][16], two[0]["mapping"][1][16] = 0.25, 0.75
wide = bpy.data.objects.new("wide", bpy.data.meshes.new("wide"))
wide.data.from_pydata([(0, 0, 0)], [], [])
multi = cr.attach(wide, two, [0])
check("several influences per vertex keep their weights", multi["weight"], [[0.25, 0.75]])

joined = bpy.data.objects.new("joined", bpy.data.meshes.new("joined"))
joined.data.from_pydata([(0, 0, 0)] * 3, [], [])
cr.attach(joined, [{"base": 1, "count": 1, "asset": 0, "mapping": [entry((1.0, 0.0, 0.0), 0.0, (0, 1, 2))]}], [0], 2)
check("a part marks its cloth vertices", cr.parts(joined), [2])
cr.relink(joined, {2: 7})
check("relinking moves a part's slots to their place in the joined cache", cr._ints(joined.data, "cloth_slot"), [-1, 7, -1])
both_, offs = cr.join([(1, slots, 6), (2, multi, 3)])
check("joined slots: later particles shifted, fewer influences padded", (offs, both_["tri"][2], both_["weight"][0]),
      ({1: 0, 2: 2}, [[6, 7, 8], [6, 7, 8]], [1.0, 0.0]))

cache = cr.cache(render, 2)
pos = np.array([[(0.3, 0.5, 0.1), (0.0, 1.0, 0.0)], [(0.3, 0.5, 1.1), (0.0, 1.0, 0.0)]], np.float32)
cr.write(cache, 1, pos)
check("a mapped vertex sits on its slot", evaluated(render, 1)[1], (0.3, 0.5, 0.1))
check("it follows the cache frame by frame", evaluated(render, 2)[1], (0.3, 0.5, 1.1))
check("the other vertices stay skinned", evaluated(render, 2)[0:3:2], [(5.0, 5.0, 5.0), (7.0, 7.0, 7.0)])
check("a partly static triangle blends cloth and skinning", evaluated(render, 1)[3], (4.0, 4.5, 4.0), 1e-3)
check("a frame outside the cache shows the skinned mesh", evaluated(render, 3)[1], (6.0, 6.0, 6.0))
cr.write(cr.cache(render, 2, live=True), 3, np.array([[(0.3, 0.5, 2.1), (0.0, 1.0, 0.0)]], np.float32))
check("the live cache shows its one frame", evaluated(render, 3)[1], (0.3, 0.5, 2.1))
cr.show(render, False)
check("the bake's cache is shown again, untouched", (evaluated(render, 2)[1], evaluated(render, 3)[1]), ((0.3, 0.5, 1.1), (6.0, 6.0, 6.0)))
cr.set_on(render, False)
check("switched off, the mesh is skinned", evaluated(render, 1)[1], (6.0, 6.0, 6.0))
cr.set_on(render, True)

# --- reader ---
import json  # noqa: E402
from fpmp_baseline.processing.context import cloth_read as rd  # noqa: E402


def vec(x, y, z):
    return {"X": x, "Y": y, "Z": z}


PHYS = os.path.join(TMP, "colliders.physics.json")
json.dump([{"Type": "PhysicsAsset", "Name": "Colliders", "Properties": {}},
           {"Type": "SkeletalBodySetup", "Name": "b0", "Properties": {"BoneName": "pelvis", "AggGeom": {
               "SphereElems": [{"Center": vec(1, 2, 3), "Radius": 4.0}],
               "SphylElems": [{"Center": vec(0, 0, 0), "Rotation": {"Pitch": 0.0, "Yaw": 0.0, "Roll": 0.0}, "Radius": 2.0, "Length": 10.0}],
               "TaperedCapsuleElems": [{"Center": vec(0, 0, 0), "Rotation": {"Pitch": 0.0, "Yaw": 0.0, "Roll": 0.0}, "Radius0": 3.0,
                                        "Radius1": 1.0, "Length": 8.0, "bOneSidedCollision": True}]}}},
           {"Type": "SkeletalBodySetup", "Name": "b1", "Properties": {"BoneName": "floor", "AggGeom": {
               "BoxElems": [{"Center": vec(0, 0, -5), "X": 100.0, "Y": 100.0, "Z": 10.0}]}}}], open(PHYS, "w"))
CLOTH = os.path.join(TMP, "skirt.cloth.json")
mesh_data = {"Vertices": [vec(0, 0, 0), vec(10, 0, 0), vec(0, 10, 0), vec(10, 10, 0)], "Normals": [vec(0, 0, 1)] * 4,
             "Indices": [0, 1, 2, 2, 1, 3],
             "WeightMaps": [{"Key": "1", "Value": {"Values": [0.0, 0.0, 5.0, 5.0]}}, {"Key": "8", "Value": {"Values": [1.0]}}],
             "BoneData": [{"NumInfluences": 2, "BoneIndices": 0, "BoneIndices[1]": 1, "BoneWeights": 0.75, "BoneWeights[1]": 0.25}] * 4,
             "GeodesicTethers": {"Tethers": [[{"Item1": 0, "Item2": 2, "Item3": 10.0}]]}}
json.dump({"Assets": ["Skirt_Cloth"], "PhysicsAssets": [PHYS],
           "Exports": [{"Type": "ChaosClothConfig", "Name": "ChaosClothConfig", "Properties": {
                            "Density": 1.0, "TetherScale": {"Low": 1.05, "High": 1.05}, "AnimDriveStiffness": {"Low": 0.1},
                            "FictitiousForcesModel": "EChaosClothingSimulationSolverFictitiousForcesModel::Legacy",
                            "LinearVelocityScale": vec(0.6, 0.6, 0.5)}},
                       {"Type": "ChaosClothSharedSimConfig", "Name": "Shared", "Properties": {"IterationCount": 2}},
                       {"Type": "ClothingAssetCommon", "Name": "Skirt_Cloth", "Properties": {
                            "ClothConfigs": [{"Key": "ChaosClothConfig", "Value": {"ObjectName": "ChaosClothConfig'M:Skirt_Cloth.ChaosClothConfig'"}},
                                             {"Key": "ChaosClothSharedSimConfig", "Value": {"ObjectName": "ChaosClothSharedSimConfig'M:Shared'"}}],
                            "LodData": [{"PhysicalMeshData": mesh_data}], "LodMap": [0, 0],
                            "UsedBoneNames": ["spine_01", "pelvis"], "UsedBoneIndices": [2, 1], "ReferenceBoneIndex": 1}}],
           "Lods": [{"NumVertices": 10, "Sections": [{"BaseVertexIndex": 4, "NumVertices": 1, "Asset": 0, "AssetLod": 0,
                                                       "Mapping": [entry((1.0, 0.0, 0.0), 0.0, (0, 1, 2))]}]},
                    {"NumVertices": 5, "Sections": []}]}, open(CLOTH, "w"))
parents = {"root": None, "pelvis": "root", "spine_01": "pelvis", "floor": "root"}
got = rd.read(CLOTH, 10, parents)
c = got["clothes"][0]
check("the LOD with the imported vertex count, its sections", [(s["base"], s["count"], s["asset"]) for s in got["sections"]], [(4, 1, 0)])
check("no cloth for a vertex count no LOD has", rd.read(CLOTH, 7, parents), None)
check("simulation mesh", (len(c["positions"]), c["triangles"], c["positions"][3]), (4, [[0, 1, 2], [2, 1, 3]], [10.0, 10.0, 0.0]))
check("weight maps by name, a wrong-length one dropped", sorted(c["maps"]), ["MaxDistance"])
check("bone weights by bone name", c["influences"][0], [["spine_01", 0.75], ["pelvis", 0.25]])
check("the reference bone by skeleton index", c["reference"], "pelvis")
cfg = c["config"]
check("dump values over the engine's defaults", (cfg["Density"], cfg["TetherScale"], cfg["AnimDriveStiffness"], cfg["Drag"],
                                                   cfg["FictitiousForcesModel"], cfg["LinearVelocityScale"], cfg["FrictionCoefficient"]),
      (1.0, [1.05, 1.05], [0.1, 1.0], [0.035, 1.0], "Legacy", [0.6, 0.6, 0.5], 0.8))
check("the shared config with its defaults", c["shared"], {"IterationCount": 2, "MaxIterationCount": 10, "SubdivisionCount": 1})
check("geodesic tethers by default", c["tethers"], [[[0, 2, 10.0]]])
shapes = [(s["bone"], s["kind"]) for s in c["colliders"]]
check("collider shapes in the engine's order", shapes, [("pelvis", "capsule"), ("pelvis", "tapered"), ("pelvis", "sphere"), ("floor", "box")])
tapered = c["colliders"][1]
check("a tapered capsule's ends and radii", (tapered["ends"], tapered["radii"], tapered["one_sided"]), ([[0.0, 0.0, 4.0], [0.0, 0.0, -4.0]], [3.0, 1.0], True))
check("a sphyl's end spheres", c["colliders"][0]["ends"], [[0.0, 0.0, 5.0], [0.0, 0.0, -5.0]])

# --- per-state settings from the anim blueprint ---
from fpmp_baseline.processing.context import anim_script as ans  # noqa: E402
from fpmp_baseline.processing.context import dynamics_read as dr  # noqa: E402


def var(name, local=False):
    return {"Token": "EX_LocalVariable" if local else "EX_InstanceVariable", "Variable": {"Property": {"Name": name}}}


library = {"Token": "EX_ObjectConst", "Value": {"ObjectName": "FortAnimationBPFunctionLibrary_C'Default__FortAnimationBPFunctionLibrary_C'"}}
mesh_call = {"Token": "EX_Context", "StatementIndex": 0, "ObjectExpression": var("Mesh", True), "ContextExpression": {
    "Token": "EX_FinalFunction", "Function": {"ObjectName": "Function'SkeletalMeshComponent:SetTeleportRotationThreshold'"},
    "Parameters": [{"Token": "EX_FloatConst", "Value": 15.0}]}}
controller = {"Token": "EX_Context", "StatementIndex": 10, "ObjectExpression": library, "ContextExpression": {
    "Token": "EX_LocalVirtualFunction", "Function": "Cloth Controller Function DEPRECATED",
    "Parameters": [{"Token": "EX_Self"}, {"Token": "EX_StringConst", "Value": "Skirt_Cloth"}, var("Cloth Paramaters_0"),
                   var("Cloth Core Settings_0")]}}
cloth_off = {"Token": "EX_Let", "StatementIndex": 20, "Variable": var("Cloth Off"), "Expression": {
    "Token": "EX_CallMath", "Function": {"ObjectName": "Function'KismetMathLibrary:Not_PreBool'"}, "Parameters": [var("bClothEnabled")]}}


def row(lin, joint, gravity):
    return {"LinearVelocityScale_7_A": vec(*lin), "AngularVelocityScale_6_B": 0.2, "FictitiousAngularScale_10_C": 0.1,
            "GravityOverride_15_D": vec(*gravity), "DragCoefficient_23_E": {"X": 0.0, "Y": 0.3},
            "LiftCoefficient_24_F": {"X": 0.0, "Y": 0.2}, "JointName_27_G": joint}


core = {"EdgeStiffness_28_A": {"X": 1.0, "Y": 1.0}, "BendingStiffness_29_B": {"X": 0.5, "Y": 1.0}, "AreaStiffness_30_C": {"X": 1.0, "Y": 1.0},
        "TetherStiffness_24_D": {"X": 1.0, "Y": 1.0}, "CollisionThickness_14_E": 0.0, "FrictionCoefficient_15_F": 0.3,
        "SelfCollisionThickness_16_G": 0.0, "DampingCoefficient_17_H": 0.1, "LocalDampingCoefficient_33_I": 0.1,
        "AnimDriveStiffness_27_J": {"X": 0.0, "Y": 1.0}, "AnimDriveDamping_26_K": {"X": 0.0, "Y": 1.0},
        "NumberIterations_21_L": 2, "NumberSubsteps_23_M": 1}
SCRIPT = os.path.join(TMP, "skirt.animscript.json")
json.dump([{"Type": "AnimBlueprintGeneratedClass", "Name": "S_C", "ChildProperties": [{"Name": "bClothEnabled", "Type": "BoolProperty"}]},
           {"Type": "S_C", "Name": "Default__S_C", "Properties": {
               "Cloth Paramaters_0": {"OnGround_Standing_15_X": row((0.45, 1.0, 1.0), "root", (0.0, -100.0, -980.0)),
                                      "EmoteOrMelee_25_Y": row((0.05, 0.05, 0.05), "pelvis", (-980.0, -500.0, 0.0))},
               "Cloth Core Settings_0": core}},
           {"Type": "Function", "Name": "BlueprintUpdateAnimation", "ScriptBytecode": [mesh_call, controller, cloth_off,
                                                                                         {"Token": "EX_Return", "StatementIndex": 30}]}],
          open(SCRIPT, "w"))
st = rd.states(SCRIPT, "Skirt_Cloth")
emote = st["EmoteOrMelee"]
check("a state's row: velocity scales, gravity in its joint, drag and lift",
      (emote["LinearVelocityScale"], emote["AngularVelocityScale"], emote["FictitiousAngularScale"], emote["gravity"], emote["joint"],
       emote["Drag"], emote["Lift"], emote["FluidDensity"]),
      ([0.05, 0.05, 0.05], 0.2, 0.1, [-980.0, -500.0, 0.0], "pelvis", [0.0, 0.3], [0.0, 0.2], 1e-6))
check("the core settings over the asset's", (emote["BendingStiffnessWeighted"], emote["FrictionCoefficient"], emote["CollisionThickness"],
                                               emote["DampingCoefficient"], emote["LocalDampingCoefficient"], emote["AnimDriveStiffness"],
                                               emote["TetherScale"], emote["IterationCount"], emote["SubdivisionCount"]),
      ([0.5, 1.0], 0.3, 0.0, 0.1, 0.1, [0.0, 1.0], [1.0, 1.0], 2, 1))
check("a state without a row takes standing's", st["Swimming"]["LinearVelocityScale"], [0.45, 1.0, 1.0])
check("the mesh's teleport rotation threshold", emote["teleport_rotation"], 15.0)
check("another asset gets none", rd.states(SCRIPT, "Cape_Cloth"), {})
script = ans.load(SCRIPT)
check("with cloth on, the blueprint's cloth switch reads it", (dr._by_state(script, None, cloth=True)["EmoteOrMelee"]["Cloth Off"],
                                                               dr._by_state(script, None)["EmoteOrMelee"]["Cloth Off"]), (False, True))

# --- solver ---
import math  # noqa: E402
from mathutils import Quaternion, Vector  # noqa: E402
from fpmp_baseline.processing.context import cloth_solver as cs_  # noqa: E402

H = 1.0 / 60.0
fit = (1000.0 ** 0.5 - 1.0) / 999.0
check("PBD stiffness: 1000^v fit, 120 Hz exponent over the iterations", cs_.stiffness(0.5, H, 1), 1.0 - (1.0 - fit) ** 2.0, 1e-9)
check("stiffness ends", (cs_.stiffness(0.0, H, 2), cs_.stiffness(1.0, H, 2)), (0.0, 1.0))
check("a weighted table: 16 entries from low to high", cs_.table([0.0, 1.0], H, 1)[15], 1.0)

square = {"positions": [[0.0, 0.0, 0.0], [10.0, 0.0, 0.0], [0.0, 10.0, 0.0], [10.0, 10.0, 0.0]], "normals": [[0.0, 0.0, 1.0]] * 4,
          "triangles": [[0, 1, 2], [2, 1, 3]], "influences": [[["root", 1.0]]] * 4, "maps": {"MaxDistance": [0.0, 0.0, 50.0, 50.0]},
          "reference": "root", "config": dict(rd.CONFIG), "shared": dict(rd.SHARED), "tethers": [], "colliders": []}
sim = cs_.ClothSim(square, {"root": (Quaternion(), Vector())})
check("masses from triangle areas and density (kg/m2)", round(float(sim.mass[1]), 9), round(100.0 / 3.0 * 0.35 / 10000.0, 9))
check("kinematic below 0.1 max distance", sim.w.tolist()[:2], [0.0, 0.0])
check("no max distance map: all kinematic", cs_.ClothSim(dict(square, maps={}), {"root": (Quaternion(), Vector())}).w.tolist(), [0.0] * 4)

loose = dict(square, config=dict(rd.CONFIG, EdgeStiffnessWeighted=[0.0, 0.0], BendingStiffnessWeighted=[0.0, 0.0],
                                 AreaStiffnessWeighted=[0.0, 0.0], Drag=[0.0, 0.0], Lift=[0.0, 0.0], Gravity=[0.0, 0.0, -980.0],
                                 bUseGravityOverride=True))
rest = {"root": (Quaternion(), Vector())}
sim = cs_.ClothSim(loose, rest)
pos, _ = sim.step(H, rest, (Quaternion(), Vector()))
dt1 = 1.0 / 30.0 + 0.1 * (H - 1.0 / 30.0)          # the engine's smoothed step, from 1/30
kd = math.log(1.0 - 0.01) * 60.0
check("a free particle falls with the engine's damping and smoothed step", float(pos[2][2]),
      -980.0 * dt1 * (math.exp(kd * dt1) - 1.0) / kd, 1e-4)
check("a kinematic particle stays on its animated position", pos[0].tolist(), [0.0, 0.0, 0.0])
calm = dict(loose["config"], Gravity=[0.0, 0.0, 0.0])
for drag, name in ((1.0, "a cloth with drag drifts downwind"), (0.0, "one without drag and lift ignores the wind")):
    blown = cs_.ClothSim(dict(loose, config=dict(calm, Drag=[drag, drag])), rest)
    blown.wind = np.array([0.0, 0.0, 500.0])
    for _ in range(5):
        pos, _ = blown.step(H, rest, (Quaternion(), Vector()))
    check(name, bool(pos[2][2] > 1e-3), drag > 0.0)

P = np.array([[0.0, 0.0, 0.0], [12.0, 0.0, 0.0]])
cs_.springs(P, np.array([0.0, 1.0]), [np.array([0])], np.array([0]), np.array([1]), np.array([10.0]), np.array([1.0]))
check("a spring at stiffness 1 restores its rest length", P[1].tolist(), [10.0, 0.0, 0.0])
P = np.array([[0.0, 0.0, 20.0]])
cs_.max_distance(P, np.array([[0.0, 0.0, 0.0]]), np.array([5.0]), np.array([0]))
check("max distance clamps a particle to its sphere", P[0].tolist(), [0.0, 0.0, 5.0])
P = np.array([[0.0, 0.0, -1.0]])
cs_.backstop(P, np.array([[0.0, 0.0, 0.0]]), np.array([[0.0, 0.0, 1.0]]), np.array([10.0]), np.array([0.0]), np.array([0]), False)
check("backstop keeps a particle outside its sphere behind the surface", round(float(P[0][2]), 6), 0.0)

capsule = {"kind": "capsule", "ends": [[0.0, 0.0, -10.0], [0.0, 0.0, 10.0]], "radius": 5.0}
P, X = np.array([[3.0, 0.0, 0.0]]), np.array([[3.0, 0.0, 0.0]])
cs_.collide(P, X, np.array([0]), cs_.Collider(capsule, (Quaternion(), Vector())), 1.0, 0.0, H)
check("a capsule pushes a particle out to radius plus thickness", P[0].tolist(), [6.0, 0.0, 0.0])
P, X = np.array([[4.0, 0.0, 2.0]]), np.array([[4.0, 0.0, 0.0]])
cs_.collide(P, X, np.array([0]), cs_.Collider(capsule, (Quaternion(), Vector())), 0.0, 0.5, H)
check("friction takes back up to pen * mu of the sliding", P[0].tolist(), [5.0, 0.0, 1.5])
tapered = {"kind": "tapered", "ends": [[0.0, 0.0, 10.0], [0.0, 0.0, -10.0]], "radii": [4.0, 2.0], "one_sided": False, "plane": [1.0, 0.0, 0.0]}
P = np.array([[1.0, 0.0, 5.0]])
cs_.collide(P, P.copy(), np.array([0]), cs_.Collider(tapered, (Quaternion(), Vector())), 0.0, 0.0, H)
check("a tapered capsule's radius a quarter down from its first end", P[0].tolist(), [3.5, 0.0, 5.0])

particles = np.array([[0, 0, 0], [0, 0, 0], [0, 0, 0], [0, 0, 0], [1, 0, 0], [0, 1, 0]], np.float64)
up = np.tile([0.0, 0.0, 1.0], (6, 1))
check("render mapping: third barycentric 1 - b0 - b1, offset along the normals", cs_.mapped(slots, particles, up)[0].tolist(), [0.3, 0.5, 10.0])
check("several influences average by weight", cs_.mapped(multi, particles[3:], up[3:])[0].tolist(), [0.75, 0.0, 0.0])

moved = (Quaternion(), Vector((10.0, 0.0, 0.0)))
for lin, want in ((0.0, 10.0), (1.0, 0.0)):
    g = cs_.carried((Quaternion(), Vector()), moved, [lin] * 3, 0.0, 1.0, Vector())
    check("velocity scale %g carries the cloth %g cm with the bone" % (lin, want), round(g(Vector((1.0, 2.0, 3.0))).x - 1.0, 6), want)
spun = (Quaternion((0.0, 0.0, 1.0), math.radians(90.0)), Vector())
g = cs_.carried((Quaternion(), Vector()), spun, [1.0] * 3, 0.0, 1.0, Vector())
check("angular scale 0 turns the cloth with the bone", tuple(round(c, 6) for c in g(Vector((1.0, 0.0, 0.0)))), (0.0, 1.0, 0.0))

swing = cs_.ClothSim(dict(loose, by_state={"OnGround_Standing": {"teleport_rotation": 15.0}}), rest)
swing.step(H, rest, (Quaternion(), Vector()))
swing.step(H, rest, (Quaternion(), Vector()))
before = float(np.abs(swing.V).max())
turned = {"root": (Quaternion((0.0, 0.0, 1.0), math.radians(20.0)), Vector())}
swing.step(H, turned, (Quaternion(), Vector()))
check("a root turn past the teleport threshold resets the cloth", float(np.abs(swing.V).max()) < 0.6 * before, True)

# --- bake ---
from fpmp_baseline.processing.context import dynamics_bake as db  # noqa: E402

scene.render.fps, scene.render.fps_base = 30, 1.0
arm = bpy.data.objects.new("skirted", bpy.data.armatures.new("skirted"))
scene.collection.objects.link(arm)
bpy.context.view_layer.objects.active = arm
bpy.ops.object.mode_set(mode='EDIT')
root = arm.data.edit_bones.new("root")
root.head, root.tail = (0, 0, 0), (0, 0, 0.1)
bpy.ops.object.mode_set(mode='OBJECT')
arm.pose.bones["root"].rotation_mode = 'QUATERNION'
for f in (1, 30):
    arm.pose.bones["root"].keyframe_insert("location", frame=f)
dress = bpy.data.meshes.new("dress")
dress.from_pydata([(0.0, 0.0, 1.0), (0.05, 0.0, 1.0)], [], [])
worn = bpy.data.objects.new("dress", dress)
scene.collection.objects.link(worn)
worn.parent = arm
skirt = dict(square, positions=[[0.0, 0.0, 100.0], [10.0, 0.0, 100.0], [0.0, 10.0, 100.0], [10.0, 10.0, 100.0]], influences=[[["root", 1.0]]] * 4)
worn_slots = cr.attach(worn, [{"base": 0, "count": 1, "asset": 0, "mapping": [entry((0.0, 0.0, 1.0), 0.0, (0, 1, 3))]}], [0])
arm.data[db.KEY] = json.dumps({"scale": 0.01, "nodes": [], "cloth": [{"id": cr.tag(worn), "mesh": worn.name, "clothes": [skirt], "slots": worn_slots}]})
worn.name = "dress renamed"
check("a cloth-only armature bakes (its mesh renamed since import)", db.bake(arm)[1], 30)
ys = [evaluated(worn, f)[0] for f in (1, 30)]
check("the cloth vertex hangs down from its pinned edge", (ys[1][2] < 0.99, abs(ys[1][1]) < 0.11), (True, True))
check("unmapped vertices stay skinned", evaluated(worn, 30)[1], (0.05, 0.0, 1.0))
check("a cloth-only rig has the Dynamics toggle", db.is_on(arm), True)
db.set_on(arm, False)
check("the Dynamics toggle turns the cloth off too", (evaluated(worn, 30)[0], db.is_on(arm)), ((0.0, 0.0, 1.0), False))
db.set_on(arm, True)
arm.location.x = 3.0
bpy.context.view_layer.update()
check("cloth results land in the mesh's space wherever the armature stands",
      tuple(round(c, 4) for c in cr._to_mesh(worn, arm, np.array([[10.0, 20.0, 30.0]]), 0.01)[0]), (0.1, -0.2, 0.3))
arm.location.x = 0.0
bpy.context.view_layer.update()
baked = evaluated(worn, 30)[0]
from fpmp_baseline.processing.context import dynamics_live as dl  # noqa: E402
check("live preview starts on a cloth-only armature", dl.set_live(arm, True), True)
for f in range(2, 12):
    scene.frame_set(f)
check("live writes each frame's cloth", evaluated(worn, 11)[0][2] < 0.999, True)
dl.set_live(arm, False)
check("after live, the bake's cloth is back", evaluated(worn, 30)[0], baked)

ghost = cs_.ClothSim(dict(loose, influences=[[["ghost", 1.0]]] * 4, reference="ghost"), rest)
check("a missing bone skins to the root and stands in as reference", ghost.step(H, rest, (Quaternion(), Vector()))[0][1].tolist(), [10.0, 0.0, 0.0])
far = cs_.ClothSim(dict(loose, by_state={"OnGround_Standing": {"teleport_distance": 5.0}}), rest)
far.step(H, rest, (Quaternion(), Vector()))
far.step(H, rest, (Quaternion(), Vector()))
far.step(H, rest, (Quaternion(), Vector()))
before = float(np.abs(far.V).max())
far.step(H, {"root": (Quaternion(), Vector((10.0, 0.0, 0.0)))}, (Quaternion(), Vector()))
check("the blueprint's teleport distance resets the cloth", float(np.abs(far.V).max()) < 0.6 * before, True)
scaled = cs_._authored(dict(square, maps={"TetherScale": [1.0] * 4}), "TetherScale", [1.0, 2.0], np.array([0]), stiff=False)
check("a tether scale above 1 survives its weight map", scaled.tolist(), [2.0])
row = rd._settings({"LinearVelocityScale_7_A": vec(0.1, 0.1, 0.1)}, {})
check("a row without gravity or angular fields leaves the asset's", sorted(row), ["FluidDensity", "LinearVelocityScale", "TetherScale"])
twin = {"Type": "ChaosClothConfig", "Name": "ChaosClothConfig", "Outer": {"ObjectName": "ClothingAssetCommon'M:Cape_Cloth'"}, "Properties": {"Density": 9.0}}
own = {"Type": "ChaosClothConfig", "Name": "ChaosClothConfig", "Outer": {"ObjectName": "ClothingAssetCommon'M:Skirt_Cloth'"}, "Properties": {"Density": 2.0}}
asset = {"Name": "Skirt_Cloth", "Properties": {"ClothConfigs": [{"Key": "ChaosClothConfig", "Value": {"ObjectName": "ChaosClothConfig'M:Skirt_Cloth.ChaosClothConfig'"}}],
                                               "LodData": [{"PhysicalMeshData": mesh_data}], "UsedBoneNames": ["pelvis"], "UsedBoneIndices": [1], "ReferenceBoneIndex": 1}}
check("each asset reads its own config", rd._cloth(asset, [twin, own], None, 0, parents)["config"]["Density"], 2.0)

print("[cloth_check] %d passed, %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
