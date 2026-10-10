"""Checks Fortnite's dynamic bones: the AnimDynamics solver against the engine's formulas, the anim blueprint reader,
the bake on a synthetic armature, the import and panel wiring.

    blender -b --factory-startup --python-exit-code 1 -P tests/plugin/dynamics_check.py -- <plugin parent>

<plugin parent> holds the plugin as package fpmp_baseline (see translator_test.py). Exit code 0 when every check passes."""
import math
import sys

import bpy  # noqa: F401
from mathutils import Matrix, Quaternion, Vector

sys.path.insert(0, sys.argv[sys.argv.index("--") + 1])
import fpmp_baseline  # noqa: E402,F401
from fpmp_baseline.processing.context import dynamics_solver as ds  # noqa: E402

FAILS, PASSES = [], [0]
DT = 1.0 / 60.0
ZERO = Vector((0.0, 0.0, 0.0))


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
        print("[dynamics_check] FAIL %s: got %r, want %r" % (name, got, want))


def body_def(**kw):
    d = dict(ds.DEFAULT_BODY, bone="b", box=[15.0, 15.0, 15.0])
    d.update(kw)
    return d


def node(bodies=None, **kw):
    n = {"bone": "b", "chain_end": None, "space": "Component", "relative_bone": None, "bodies": bodies or [body_def()],
         "lin_damping": None, "ang_damping": None, "bias": None, "gravity_scale": 1.0, "gravity_override": [0.0, 0.0, 0.0],
         "gravity_in_sim": True, "lin_spring": None, "ang_spring": None, "external_force": [0.0, 0.0, 0.0],
         "space_alpha": 0.0, "max_ang_vel": 10000.0, "max_ang_acc": 10000.0, "external_ang_vel": [0.0, 0.0, 0.0],
         "planar": [], "spherical": [], "pre": 4, "post": 1, "alpha": 1.0}
    n.update(kw)
    return n


def still(body):
    body.use_override, body.override = True, ZERO.copy()
    return body


# --- solver ---
check("cube inertia", tuple(ds.box_inertia((15.0, 15.0, 15.0))), (37.5, 37.5, 37.5))
check("box inertia", tuple(ds.box_inertia((7.5, 4.0, 4.0))), (2.666667, 6.020833, 6.020833))
check("zero box is a unit cube", tuple(ds.box_inertia((0.0, 0.0, 0.0))), (1 / 6, 1 / 6, 1 / 6))
check("turn to nothing is identity", tuple(ds.find_between(Vector((1, 0, 0)), ZERO)), (1.0, 0.0, 0.0, 0.0))

b = still(ds.Body(ZERO, Quaternion(), (15, 15, 15)))
b.lin = Vector((10.0, 0.0, 0.0))
ds.physics_update(DT, [b], [], [], [], ZERO, ZERO, ZERO, ZERO, ZERO, 4, 1)
check("damping keeps (1-0.7)^dt", b.lin.x, 10.0 * 0.3 ** DT)

b = still(ds.Body(Vector((1.0, 0.0, 0.0)), Quaternion(), (15, 15, 15)))
lin = []
ds.nailed(DT, lin, None, ZERO.copy(), b, ZERO.copy())
ds.physics_update(DT, [b], lin, [], [], ZERO, ZERO, ZERO, ZERO, ZERO, 4, 1)
check("nailed body lands on its joint", tuple(b.pos), (0.0, 0.0, 0.0))
check("nailed correction leaves no momentum", tuple(b.lin), (0.0, 0.0, 0.0))

hang = node([body_def(offset=[10.0, 0.0, 0.0], ang_min=[-10.0, -10.0, -10.0], ang_max=[10.0, 2.5, 10.0])],
            gravity_override=[0.0, 0.0, -980.0])
sim = ds.Sim(hang, {})
cs = {"b": (Quaternion(), ZERO.copy())}
for _ in range(180):
    out = sim.evaluate(DT, cs, "b")
q = sim.bodies[0].rot
swing = ds.find_between(Vector((1, 0, 0)), q @ Vector((1, 0, 0)))
check("hanging body rests on its Y limit", math.degrees(2 * math.asin(swing.y)), 2.5, 0.5)
check("hanging body's joint stays on the bone", tuple(out["b"][1]), (0.0, 0.0, 0.0), 0.5)

twist = node([body_def(ang_min=[-45.0, -45.0, -45.0], ang_max=[45.0, 45.0, 45.0])])
sim = ds.Sim(twist, {})
sim.evaluate(0.0, cs, "b")
sim.bodies[0].rot = Quaternion((1, 0, 0), math.radians(20.0))
sim.evaluate(DT, cs, "b")
check("twist eases back by the 0.3 bias", math.degrees(sim.bodies[0].rot.angle), 20.0 - math.degrees(0.6 * math.sin(math.radians(10.0))), 0.05)

b = ds.Body(ZERO, Quaternion(), (15, 15, 15))
ds.Spring(None, ZERO.copy(), b, 0.0, 25.0, ZERO.copy(), "X", Quaternion(), False, True).apply(DT)
check("a zero-target spring does nothing", tuple(b.ang), (0.0, 0.0, 0.0))
ds.Spring(None, ZERO.copy(), b, 0.0, 25.0, Vector((0, 0, 1)), "X", Quaternion(), False, True).apply(DT)
check("a +Z target turns X up", tuple(b.ang), (0.0, -25.0 * math.pi / 2 * DT, 0.0))

inv = ds._world_inverse(Quaternion((0, 0, 1), math.radians(30.0)), Matrix.Diagonal(Vector([1 / m for m in ds.box_inertia((7.5, 4.0, 4.0))])))
c, s = math.cos(math.radians(30.0)), math.sin(math.radians(30.0))
check("inertia turned as R^T I^-1 R", inv[0][1], c * s * (1 / 6.020833 - 1 / 2.666667))

fall = node([body_def(lin_types=["Free"] * 3, ang_min=[-45.0] * 3, ang_max=[45.0] * 3)], gravity_override=[0.0, 0.0, -980.0])
out = ds.Sim(fall, {}).evaluate(0.1, cs, "b")
check("a long frame steps 1/30", out["b"][1].z, -980.0 / 900.0)

pair = {"a": (Quaternion(), ZERO.copy()), "b": (Quaternion(), Vector((10.0, 0.0, 0.0)))}
chain = node([body_def(bone="a", box=[5.0, 5.0, 5.0], offset=[5.0, 0.0, 0.0], ang_min=[-89.0] * 3, ang_max=[89.0] * 3)],
             bone="a", chain_end="b", gravity_override=[0.0, 0.0, -980.0])
sim = ds.Sim(chain, {"b": "a", "a": None})
check("a chain gets a body per bone", [d["bone"] for d in sim.defs], ["a", "b"])
for _ in range(180):
    sim.evaluate(DT, pair, "a")
b0, b1 = sim.bodies
check("a hanging chain closes its joint", (b0.at(sim.joints[1][0]) - b1.at(sim.joints[1][1])).length < 0.1, True)

spin = ds.Sim(node(space="RootRelative", space_alpha=1.0), {})
spin.prev_space = Quaternion()
w, _ = spin._space_spin(Quaternion((0, 0, 1), 0.01), 0.01)
check("a root turning 1 rad/s spins the space", tuple(w), (0.0, 0.0, 1.0), 1e-3)

# --- reader ---
from fpmp_baseline.processing.context import dynamics_read as dr  # noqa: E402

AD_A = {"BoundBone": {"BoneName": "dyn_a"}, "ComponentPose": {"LinkID": 1}, "SimulationSpace": "AnimPhysSimSpaceType::RootRelative",
        "bOverrideAngularDamping": True, "AngularDampingOverride": 0.999, "LinearDampingOverride": 0.5,
        "PhysicsBodyDefinitions": [{"BoundBone": {"BoneName": "dyn_a"}, "LocalJointOffset": {"X": -0.1, "Y": 0.0, "Z": 0.0},
                                    "ConstraintSetup": {"TwistAxis": "AnimPhysTwistAxis::AxisY",
                                                        "AngularLimitsMax": {"X": 10.0, "Y": 2.5, "Z": 10.0}}}]}
AD_B = {"BoundBone": {"BoneName": "dyn_b"}, "ComponentPose": {"LinkID": 0},
        "PhysicsBodyDefinitions": [{"BoundBone": {"BoneName": "dyn_b"}, "BoxExtents": {"X": 4.0, "Y": 0.0, "Z": 4.0}}]}
dump = [{"Type": "AnimBlueprintGeneratedClass", "Name": "X_AnimBP_C",
         "ChildProperties": [{"Name": n} for n in ("AnimGraphNode_CopyPoseFromMesh", "AnimGraphNode_AnimDynamics_1",
                                                    "UberGraphFrame", "AnimGraphNode_Root", "AnimGraphNode_AnimDynamics")]},
        {"Type": "X_AnimBP_C", "Name": "Default__X_AnimBP_C",
         "Properties": {"AnimGraphNode_CopyPoseFromMesh": {}, "AnimGraphNode_AnimDynamics_1": AD_B,
                        "AnimGraphNode_Root": {"Result": {"LinkID": 3}}, "AnimGraphNode_AnimDynamics": AD_A}}]
import json, os, tempfile  # noqa: E401,E402
dump_path = os.path.join(tempfile.mkdtemp(), "x.animbp.json")
json.dump(dump, open(dump_path, "w", encoding="utf-8"))
read = dr.nodes([dump_path])
check("nodes in graph order", [n["bone"] for n in read], ["dyn_b", "dyn_a"])
a, bnode = read[1], read[0]
check("absent box is the default", a["bodies"][0]["box"], [10.0, 10.0, 10.0])
check("enum prefix dropped", (a["space"], a["bodies"][0]["twist"]), ("RootRelative", "Y"))
check("absent space is Component", bnode["space"], "Component")
check("damping override with its flag", (a["ang_damping"], a["lin_damping"]), (0.999, None))
check("limits read, absent ones zero", (a["bodies"][0]["ang_max"], a["bodies"][0]["ang_min"]), ([10.0, 2.5, 10.0], [0.0, 0.0, 0.0]))
check("a flat box becomes a unit cube", bnode["bodies"][0]["box"], [1.0, 1.0, 1.0])
check("parts numbered", [n["part"] for n in read], [0, 0])
check("a missing dump reads nothing", dr.nodes([dump_path + ".none"]), [])

# --- bake ---
from fpmp_baseline.processing.context import dynamics_bake as db  # noqa: E402

scene = bpy.context.scene
scene.render.fps, scene.render.fps_base = 30, 1.0


def armature(name):
    data = bpy.data.armatures.new(name)
    obj = bpy.data.objects.new(name, data)
    scene.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')
    root = data.edit_bones.new("root")
    root.head, root.tail = (0, 0, 0), (0, 0, 0.1)
    arm = data.edit_bones.new("upperarm")
    arm.head, arm.tail, arm.parent = (0, 0, 1), (0.3, 0, 1), root
    tag = data.edit_bones.new("dyn_tag")
    tag.head, tag.tail, tag.parent = (0.3, 0, 1), (0.3, 0, 0.9), arm
    bpy.ops.object.mode_set(mode='OBJECT')
    for pb in obj.pose.bones:
        pb.rotation_mode = 'QUATERNION'
    return obj


def key_arm(obj, turns):
    pb = obj.pose.bones["upperarm"]
    for frame, deg in turns:
        pb.rotation_quaternion = Quaternion((0, 1, 0), math.radians(deg))
        pb.keyframe_insert("rotation_quaternion", frame=frame)


def tag_node(**kw):
    return node([body_def(bone="dyn_tag", box=[2.0, 2.0, 2.0], offset=[0.0, -0.1, 0.0], **kw.pop("body", {}))],
                bone="dyn_tag", space="RootRelative", **kw)


def store(obj, *nodes_):
    obj.data[db.KEY] = json.dumps({"scale": 0.01, "nodes": list(nodes_)})


def head_dir(obj, frame):
    scene.frame_set(frame)
    pb = obj.pose.bones["dyn_tag"]
    return pb.head.copy(), (pb.tail - pb.head).normalized()


plain = armature("plain")
key_arm(plain, [(0, 0.0), (20, 0.0)])
check("no dynamics, nothing baked", db.bake(plain), (0, 0))
check("no dynamics, no track", db._track(plain), None)

still_arm = armature("still")
key_arm(still_arm, [(0, 0.0), (20, 0.0)])
store(still_arm, tag_node(gravity_override=[0.0, 0.0, 0.0]))
check("a still pose bakes one bone over its frames", db.bake(still_arm), (1, 21))
track = db._track(still_arm)
check("one Dynamics strip", (track is not None, len(track.strips) if track else 0), (True, 1))
action = track.strips[0].action
paths = sorted({fc.data_path for fc in db._curves(action)})
check("keys only the simulated bone's twin", paths, ['pose.bones["DYN_dyn_tag"].location', 'pose.bones["DYN_dyn_tag"].rotation_quaternion'])
check("the bone takes it last", [c.name for c in still_arm.pose.bones["dyn_tag"].constraints], [db.BAKED])
worst = max(abs(kp.co[1] - (1.0 if (fc.data_path.endswith("quaternion") and fc.array_index == 0) else 0.0))
            for fc in db._curves(action) for kp in fc.keyframe_points)
check("a still pose round-trips to rest", worst < 1e-3, True)

swing = armature("swing")
key_arm(swing, [(0, 0.0), (10, 60.0), (60, 60.0)])
store(swing, tag_node(gravity_override=[0.0, 0.0, 0.0], ang_spring=25.0, ang_damping=0.9,
                      body={"ang_min": [-30.0] * 3, "ang_max": [30.0] * 3, "target": [1.0, 0.0, 0.0]}))
held = swing.pose.bones["dyn_tag"].constraints.new('LIMIT_ROTATION')     # holds the bone as the deform rig holds the bracelets
held.use_limit_x = held.use_limit_y = held.use_limit_z = True
held.owner_space = 'LOCAL'
db.bake(swing)
check("a constrained bone gets the bake after its constraint", [c.type for c in swing.pose.bones["dyn_tag"].constraints], ['LIMIT_ROTATION', 'COPY_TRANSFORMS'])
db.set_on(swing, False)
check("the toggle mutes", (db.is_on(swing), swing.pose.bones["dyn_tag"].constraints[db.BAKED].mute), (False, True))
anim6, anim60 = head_dir(swing, 6), head_dir(swing, 60)
db.set_on(swing, True)
sim6, sim60 = head_dir(swing, 6), head_dir(swing, 60)
check("the bone lags the swing", math.degrees(anim6[1].angle(sim6[1])) > 1.0, True)
check("its head stays on the animated head", (anim6[0] - sim6[0]).length < 0.001, True)
check("it settles back", math.degrees(anim60[1].angle(sim60[1])) < 2.0, True)
check("keys are finite", all(math.isfinite(kp.co[1]) for fc in db._curves(db._track(swing).strips[0].action) for kp in fc.keyframe_points), True)
db.bake(swing)
check("a re-bake replaces the track", [t.name for t in swing.animation_data.nla_tracks].count(db.TRACK), 1)

# --- wiring: import keeps the nodes, an emote import bakes, the panel's operators ---
from types import SimpleNamespace  # noqa: E402
from fpmp_baseline.material_porter import anim_hooks, mesh_hooks  # noqa: E402
from fpmp_baseline.operator import rig_ui  # noqa: E402

AD_A["BoundBone"]["BoneName"] = AD_A["PhysicsBodyDefinitions"][0]["BoundBone"]["BoneName"] = "dyn_tag"
json.dump(dump, open(dump_path, "w", encoding="utf-8"))
char = armature("char")
mesh_hooks._dynamics(SimpleNamespace(imported_meshes=[{"Meta": {"AnimBlueprint": dump_path}, "Skeleton": char}], scale=0.01))
kept = json.loads(char.data.get(db.KEY) or "{}")
check("import keeps the skeleton's nodes", ([n["bone"] for n in kept.get("nodes", [])], kept.get("scale")), (["dyn_tag"], 0.01))
bare = armature("bare")
bpy.context.view_layer.objects.active = bare
bpy.ops.object.mode_set(mode='EDIT')
bare.data.edit_bones.remove(bare.data.edit_bones["dyn_tag"])
bpy.ops.object.mode_set(mode='OBJECT')
mesh_hooks._dynamics(SimpleNamespace(imported_meshes=[{"Meta": {"AnimBlueprint": dump_path}, "Skeleton": bare}], scale=0.01))
check("a skeleton without the bones keeps nothing", db.KEY in bare.data, False)
key_arm(char, [(0, 0.0), (10, 30.0)])
anim_hooks.end(char)
check("an emote import bakes", db._track(char) is not None, True)
try:
    rig_ui.register()
except ValueError:
    pass
bpy.context.view_layer.objects.active = char
check("Simulate runs", bpy.ops.fpmp.dynamics_bake(), {'FINISHED'})
check("the toggle operator mutes", (bpy.ops.fpmp.dynamics_on(on=False), db.is_on(char)), ({'FINISHED'}, False))

# --- review fixes ---
for limit in (0.0, 20.0):
    try:
        ds.Sim(node([body_def(angular="Cone", cone=limit)]), {}).evaluate(DT, cs, "b")
        ok = True
    except ZeroDivisionError:
        ok = False
    check("a cone node steps (limit %g)" % limit, ok, True)

looped = armature("looped")
key_arm(looped, [(0, 0.0), (20, 30.0)])
db._push_down(looped)
looped.animation_data.nla_tracks[0].strips[0].repeat = 5.0
check("a looped strip counts one pass", db.frame_range(looped), (0, 20))
tweaked = armature("tweaked")
key_arm(tweaked, [(0, 0.0), (40, 30.0)])
db._push_down(tweaked)
key_arm(tweaked, [(10, 5.0)])
check("a tweak on top keeps the whole range", db.frame_range(tweaked), (0, 40))

src = armature("src")
key_arm(src, [(0, 0.0), (20, 30.0)])
renamed = armature("renamed")          # plays another rig's action through that rig's slot
renamed.animation_data_create()
renamed.animation_data.action = src.animation_data.action
renamed.animation_data.action_slot = slot = src.animation_data.action_slot
store(renamed, tag_node(gravity_override=[0.0, 0.0, 0.0]))
db.bake(renamed)
base = next(t for t in renamed.animation_data.nla_tracks if t.name != db.TRACK).strips[0]
check("the pushed-down animation keeps its slot", base.action_slot == slot and slot is not None, True)

nameless = {"ComponentPose": {"LinkID": 0}}
dump2 = [dict(dump[0]), {"Type": "X_AnimBP_C", "Name": "Default__X_AnimBP_C",
                          "Properties": {"AnimGraphNode_CopyPoseFromMesh": {}, "AnimGraphNode_AnimDynamics_1": nameless,
                                         "AnimGraphNode_Root": {"Result": {"LinkID": 1}}, "AnimGraphNode_AnimDynamics": AD_A}}]
nameless_path = dump_path + ".nameless.json"
json.dump(dump2, open(nameless_path, "w", encoding="utf-8"))
check("a node without a bone is dropped, an unlinked one still runs", [n["bone"] for n in dr.nodes([nameless_path])], ["dyn_tag"])

layered = [dict(dump[0], ChildProperties=[{"Name": n} for n in ("AnimGraphNode_CopyPoseFromMesh", "AnimGraphNode_LayeredBoneBlend",
                                                                  "AnimGraphNode_Root", "AnimGraphNode_AnimDynamics")]),
           {"Type": "X_AnimBP_C", "Name": "Default__X_AnimBP_C",
            "Properties": {"AnimGraphNode_CopyPoseFromMesh": {}, "AnimGraphNode_Root": {"Result": {"LinkID": 1}},
                           "AnimGraphNode_LayeredBoneBlend": {"BasePose": {"LinkID": 0}, "BlendPoses": [{"LinkID": 3}]},
                           "AnimGraphNode_AnimDynamics": dict(AD_A, ComponentPose={"LinkID": 0})}}]
layered_path = dump_path + ".layered.json"
json.dump(layered, open(layered_path, "w", encoding="utf-8"))
check("a node behind a layered blend is found", [n["bone"] for n in dr.nodes([layered_path])], ["dyn_tag"])

twice = armature("twice")
key_arm(twice, [(0, 0.0), (20, 30.0)])
once = tag_node(gravity_override=[0.0, 0.0, 0.0])
store(twice, once, once)
try:
    check("a bone listed twice bakes once", db.bake(twice)[0], 1)
except (RuntimeError, ValueError) as e:
    check("a bone listed twice bakes once (%s)" % e, False, True)

order = armature("order")
key_arm(order, [(0, 0.0), (10, 60.0), (30, 60.0)])
arm_node = node([body_def(bone="upperarm", ang_min=[0.0] * 3, ang_max=[0.0] * 3)], bone="upperarm", space="RootRelative",
                gravity_override=[0.0, 0.0, 0.0])
store(order, tag_node(gravity_override=[0.0, 0.0, 0.0], ang_damping=0.9, body={"ang_min": [-30.0] * 3, "ang_max": [30.0] * 3}), arm_node)
db.bake(order)
act = db._track(order).strips[0].action
w = [fc for fc in db._curves(act) if fc.data_path == 'pose.bones["DYN_dyn_tag"].rotation_quaternion' and fc.array_index == 0][0]
check("a later node on the parent keeps the child's simulation", min(kp.co[1] for kp in w.keyframe_points) < math.cos(math.radians(0.5)), True)

# --- live preview ---
from fpmp_baseline.processing.context import dynamics_live as dl  # noqa: E402
from fpmp_baseline.processing.context.dynamics_runner import Runner  # noqa: E402

live = armature("live")
key_arm(live, [(0, 0.0), (10, 60.0), (30, 60.0)])
store(live, tag_node(gravity_override=[0.0, 0.0, 0.0], ang_spring=25.0, ang_damping=0.9,
                     body={"ang_min": [-30.0] * 3, "ang_max": [30.0] * 3, "target": [1.0, 0.0, 0.0]}))
db.bake(live)
baked = [fc for fc in db._curves(db._track(live).strips[0].action) if fc.data_path.endswith("rotation_quaternion")]
want = tuple(fc.evaluate(20) for fc in sorted(baked, key=lambda fc: fc.array_index))
dl.set_live(live, True)
check("live mode mutes the baked layer", (db._track(live).mute, dl.is_live(live)), (True, True))
for f in range(0, 21):
    scene.frame_set(f)
twin = live.pose.bones["DYN_dyn_tag"].rotation_quaternion
check("playing forward live matches the bake", tuple(twin), want, 1e-4)
check("the bone follows its live twin", (live.pose.bones["dyn_tag"].tail - live.pose.bones["DYN_dyn_tag"].tail).length < 1e-5, True)
scene.frame_set(5)
check("a jump restarts the simulation there", dl._states[live.name_full]["frame"], 5)
dl.set_live(live, False)
check("live off brings the baked layer back", (db._track(live).mute, dl.is_live(live), live.name_full in dl._states), (False, False, False))
bpy.context.view_layer.objects.active = live
check("the Live operator turns it on", (bpy.ops.fpmp.dynamics_live(on=True), dl.is_live(live)), ({'FINISHED'}, True))
check("frame handlers installed", dl._after_frame in bpy.app.handlers.frame_change_post, True)


class Row:
    def __init__(self, log):
        self.log = log

    def row(self, **_):
        return self

    def operator(self, idname, text="", **kw):
        self.log.append(text)
        from types import SimpleNamespace
        return SimpleNamespace()


drawn = []
db.ui(Row(drawn), live)
check("the panel row offers Live", drawn, ["Dynamics", "Live", "Simulate"])
bpy.ops.fpmp.dynamics_live(on=False)
kept_nodes = live.data[db.KEY]
nodes_ = json.loads(kept_nodes)
nodes_["nodes"].append(dict(rnode_states := {"kind": "rigid_body", "states": {"OnGround_Standing": {}, "EmoteOrMelee": {}}, "physics": {"bodies": []}}))
live.data[db.KEY] = json.dumps(nodes_)
check("the outfit's states listed", db.states(live), ["OnGround_Standing", "EmoteOrMelee"])
bpy.context.view_layer.objects.active = live
check("the state operator sets it", (bpy.ops.fpmp.dynamics_state(state="EmoteOrMelee"), live.data.get("fpmp_dynamics_state")), ({'FINISHED'}, "EmoteOrMelee"))
live.data[db.KEY] = kept_nodes

# --- live without toggling ---
check("plain dyn bones need no toggling", dl.needs_toggle(Runner(live)), set())
copied = armature("copied")
key_arm(copied, [(0, 0.0), (10, 60.0), (30, 60.0)])
store(copied, tag_node(gravity_override=[0.0, 0.0, 0.0], ang_spring=25.0, ang_damping=0.9,
                       body={"ang_min": [-30.0] * 3, "ang_max": [30.0] * 3, "target": [1.0, 0.0, 0.0]}))
lock = copied.pose.bones["dyn_tag"].constraints.new('LIMIT_ROTATION')
lock.use_limit_x = lock.use_limit_y = lock.use_limit_z = True
lock.owner_space = 'LOCAL'
db.bake(copied)
baked = [fc for fc in db._curves(db._track(copied).strips[0].action) if fc.data_path.endswith("rotation_quaternion")]
want = tuple(fc.evaluate(20) for fc in sorted(baked, key=lambda fc: fc.array_index))
check("a bone with its own constraints can't be read from its basis alone", dl.needs_toggle(Runner(copied)), {"dyn_tag"})
dl.set_live(copied, True)
check("live reads it from an input twin instead of toggling", (dl._states[copied.name_full]["toggled"],
                                                               "DYNIN_dyn_tag" in copied.data.bones), (set(), True))
for f in range(0, 21):
    scene.frame_set(f)
check("and live still matches the bake there", tuple(copied.pose.bones["DYN_dyn_tag"].rotation_quaternion), want, 1e-4)
dl.set_live(copied, False)

# --- smooth playback: results land one frame later, exact again when playback stops ---
dl.set_live(live, True)
scene.frame_set(0)
dl._playing = lambda: True
for f in range(1, 11):
    scene.frame_set(f)
    now = tuple(live.pose.bones["DYN_dyn_tag"].rotation_quaternion)
    if f == 9:
        late = now
check("while playing, a frame shows the last frame's result", late != tuple(live.pose.bones["DYN_dyn_tag"].rotation_quaternion), True)
dl._playback_ended(scene)
dl._playing = lambda: False
exact = tuple(live.pose.bones["DYN_dyn_tag"].rotation_quaternion)
dl.set_live(live, False)
dl.set_live(live, True)
scene.frame_set(0)
for f in range(1, 11):
    scene.frame_set(f)
check("when playback stops the current frame's result lands", tuple(live.pose.bones["DYN_dyn_tag"].rotation_quaternion), exact, 1e-6)
dl.set_live(live, False)

# --- gravity fed by the movement state through Property Access ---
access = {"PathSegments": [{"Name": "GravityOverride"}, {"Name": "AnimGraphNode_AnimDynamics"}, {"Name": "GravityOverride"}],
          "SrcPaths": [{"PathSegmentStartIndex": 0, "PathSegmentCount": 1}],
          "DestPaths": [{"PathSegmentStartIndex": 1, "PathSegmentCount": 2}]}
bound = [dump[0], dict(dump[1], Properties=dict(dump[1]["Properties"], AnimGraphNode_AnimDynamics=dict(
             AD_A, bUseGravityOverride=True, GravityOverride={"X": 0.0, "Y": 0.0, "Z": 500.0})),
         SerializedSparseClassData={"AnimBlueprintExtension_PropertyAccess": {"Library": access}})]
bound_path = dump_path + ".bound.json"
json.dump(bound, open(bound_path, "w", encoding="utf-8"))
params_path = dump_path + ".dynparams.json"
json.dump([{"Type": "FortCharacterDynamicsParameters", "Name": "P", "Properties": {
    "StateNames": ["EmoteOrMelee"], "GravityOverrideParameters": [{"GravityOverride": {"X": 0.0, "Y": 0.0, "Z": -980.0}, "JointName": "root"}]}}],
          open(params_path, "w", encoding="utf-8"))
read = {n["bone"]: n for n in dr.nodes([bound_path], None, None, [params_path])}
check("a bound gravity override takes the states, an unbound one doesn't",
      ("states" in read["dyn_tag"], "states" in read["dyn_b"]), (True, False))
fed = node([body_def(lin_types=["Free"] * 3, ang_min=[-45.0] * 3, ang_max=[45.0] * 3)], gravity_override=[0.0, 0.0, 500.0])
fed["states"] = {"EmoteOrMelee": {"gravity": [0.0, 0.0, -980.0], "joint": "b", "sim_space": {}}}
out = ds.Sim(fed, {}, "EmoteOrMelee").evaluate(0.1, cs, "b")
check("the state's gravity replaces the cooked override", out["b"][1].z, -980.0 / 900.0)

# --- values the game binds, from running the anim blueprint's update ---
from fpmp_baseline.processing.context import anim_script  # noqa: E402


def var(name, local=False):
    return {"Token": "EX_LocalVariable" if local else "EX_InstanceVariable", "Variable": {"Property": {"Name": name}} if local else {"Path": [name]}}


def let(at, name, expr, local=False):
    return {"Token": "EX_Let", "StatementIndex": at, "Variable": var(name, local), "Expression": expr}


def math_(fn, *args):
    return {"Token": "EX_CallMath", "Function": {"ObjectName": "Class'KismetMathLibrary:%s'" % fn}, "Parameters": list(args)}


dbl = lambda v: {"Token": "EX_DoubleConst", "Value": v}  # noqa: E731
uber = [{"Token": "EX_PushExecutionFlow", "StatementIndex": 0, "PushingAddress": 90},
        {"Token": "EX_ComputedJump", "StatementIndex": 5, "CodeOffsetExpression": var("EntryPoint", True)},
        {"Token": "EX_JumpIfNot", "StatementIndex": 10, "CodeOffset": 30, "BooleanExpression": var("bIsInFrontEnd")},
        let(20, "Space", {"Token": "EX_ByteConst", "Value": 2}),
        {"Token": "EX_PopExecutionFlow", "StatementIndex": 25},
        let(30, "Space", {"Token": "EX_ByteConst", "Value": 3}),
        let(40, "Eased", math_("FInterpTo", var("Eased"), math_("Conv_BoolToDouble", var("bIsCrouching")), var("Dt", True), dbl(8.0))),
        let(50, "Dyn", math_("SelectFloat", dbl(0.5), dbl(1.0), math_("BooleanOR", var("bIsCrouching"), var("bIsSkyDiving")))),
        {"Token": "EX_PopExecutionFlow", "StatementIndex": 60},
        {"Token": "EX_Return", "StatementIndex": 90}]
update = [{"Token": "EX_LetValueOnPersistentFrame", "StatementIndex": 0, "DestinationProperty": {"Property": {"Name": "Dt"}},
           "AssignmentExpression": var("DeltaTimeX", True)},
          {"Token": "EX_LocalFinalFunction", "StatementIndex": 10, "Function": {"ObjectName": "Function'X_C:ExecuteUbergraph_X'"},
           "Parameters": [{"Token": "EX_IntConst", "Value": 10}]},
          {"Token": "EX_Return", "StatementIndex": 20}]
script_dump = [{"Type": "AnimBlueprintGeneratedClass", "Name": "X_C", "ChildProperties": [{"Name": "Eased", "Type": "DoubleProperty"}]},
               {"Type": "X_C", "Name": "Default__X_C", "Properties": {"Space": "AnimPhysSimSpaceType::BoneRelative"}},
               {"Type": "Function", "Name": "ExecuteUbergraph_X", "ScriptBytecode": uber},
               {"Type": "Function", "Name": "BlueprintUpdateAnimation", "ScriptBytecode": update}]
run = anim_script.Script(script_dump)
check("standing: the update's branch and values", [run.run(anim_script.inputs("OnGround_Standing"))[k] for k in ("Space", "Dyn", "Eased")],
      [3, 1.0, 0.0])
crouched = run.run({"bIsCrouching": True})
check("crouched: eased to 1", (crouched["Dyn"], round(crouched["Eased"], 3)), (0.5, 1.0))
check("front end: the other branch", run.run({"bIsInFrontEnd": True})["Space"], 2)

script_path = dump_path + ".animscript.json"
json.dump(script_dump, open(script_path, "w", encoding="utf-8"))
access2 = {"PathSegments": [{"Name": "Space"}, {"Name": "AnimGraphNode_AnimDynamics"}, {"Name": "SimulationSpace"},
                            {"Name": "Dyn"}, {"Name": "AnimGraphNode_AnimDynamics"}, {"Name": "Alpha"}],
           "SrcPaths": [{"PathSegmentStartIndex": 0, "PathSegmentCount": 1}, {"PathSegmentStartIndex": 3, "PathSegmentCount": 1}],
           "DestPaths": [{"PathSegmentStartIndex": 1, "PathSegmentCount": 2}, {"PathSegmentStartIndex": 4, "PathSegmentCount": 2}]}
scripted = [dump[0], dict(dump[1], SerializedSparseClassData={"AnimBlueprintExtension_PropertyAccess": {"Library": access2}})]
scripted_path = dump_path + ".scripted.json"
json.dump(scripted, open(scripted_path, "w", encoding="utf-8"))
tag = next(n for n in dr.nodes([scripted_path], scripts=[script_path]) if n["bone"] == "dyn_tag")
check("the bound values per state", (tag["by_state"]["OnGround_Standing"], tag["by_state"]["Front_End"]["space"]),
      ({"space": "RootRelative", "alpha": 1.0}, "World"))
from fpmp_baseline.processing.context.dynamics_runner import STATE as _STATE  # noqa: E402
bound_arm = armature("bound_arm")
store(bound_arm, dict(tag_node(gravity_override=[0.0, 0.0, 0.0]), alpha=1.0,
                      by_state={"OnGround_Standing": {"alpha": 1.0}, "Skydive_FloatingDown": {"alpha": 0.5}}))
bound_arm.data[_STATE] = "Skydive_FloatingDown"
check("the runner takes the state's values", Runner(bound_arm).alpha["dyn_tag"], 0.5)

def compose_cs(run, loc):
    out = {}
    for n in run.order:
        p = run.parent_of[n]
        out[n] = (out[p][0] @ loc[n][0], out[p][0] @ loc[n][1] + out[p][1]) if p in out else loc[n]
    return out


# --- a Control Rig turning a parent the dynamics don't simulate
turn_upper = {"Rotation": {"X": 0.0, "Y": 0.0, "Z": math.sin(math.radians(20)), "W": math.cos(math.radians(20))}}
rig_program = {"start": 0, "code": [{"o": 101, "f": "ModifyTransforms", "a": [[1, 0, -1], [1, 1, -1], [1, 2, -1], [1, 3, -1], [1, 4, -1]]},
                                    {"o": 80}],
               "callables": [], "units": {"ModifyTransforms": ["ItemToModify", "Weight", "WeightMinimum", "WeightMaximum", "Mode"]},
               "literals": {"0": [{"Item": {"Name": "upperarm"}, "Transform": turn_upper}], "1": 1.0, "2": 0.0, "3": 1.0, "4": 2},
               "defaults": {}, "lit_paths": [], "work_paths": [], "ext_paths": [], "variables": [], "bones": ["upperarm"],
               "nulls": [], "writes": ["upperarm"]}
rigged = armature("rigged")
store(rigged, {"kind": "control_rig", "program": rig_program, "alpha": 1.0, "variables": {}},
      tag_node(gravity_override=[0.0, 0.0, 0.0], ang_spring=5000.0))
run = Runner(rigged)
check("the rig hands on nothing it doesn't share with a simulation", [s.outputs() for s in run.sims if hasattr(s, "machine")], [[]])
pose_in = run.sample()
cs_out = run.tick(pose_in)
loc_t, q_t = run.basis(cs_out, "dyn_tag")
twin_world = rigged.pose.bones["upperarm"].matrix @ (rigged.data.bones["upperarm"].matrix_local.inverted() @ rigged.data.bones["dyn_tag"].matrix_local)     @ Matrix.LocRotScale(loc_t, q_t, None)
want_world = run_world = None
from fpmp_baseline.processing.context.dynamics_runner import from_game  # noqa: E402
want_world = from_game(cs_out["dyn_tag"], run.inv["dyn_tag"], run.unit)
check("a twin under a rig-turned parent keeps the turn", round((twin_world.to_quaternion().rotation_difference(want_world.to_quaternion())).angle, 4), 0.0)

# a bake keys each frame against that frame's parents, not the last one's
moving = armature("moving")
key_arm(moving, [(0, 0.0), (10, 80.0)])
store(moving, {"kind": "control_rig", "program": rig_program, "alpha": 1.0, "variables": {}},
      tag_node(gravity_override=[0.0, 0.0, 0.0], ang_spring=5000.0))
db.bake(moving)
db.set_on(moving, False)
scene.frame_set(0)
fresh = Runner(moving)
first = fresh.start(fresh.sample())
db.set_on(moving, True)
scene.frame_set(0)
baked0 = moving.pose.bones["dyn_tag"].matrix.to_quaternion()
check("the bake's first frame matches its own simulation",
      round(math.degrees(baked0.rotation_difference(from_game(first["dyn_tag"], fresh.inv["dyn_tag"], fresh.unit).to_quaternion()).angle), 1), 0.0, 0.5)

# each node's alpha is its own: a later switched-off control on the same bone doesn't switch off the simulation
shared = armature("shared")
store(shared, tag_node(gravity_override=[980.0, 0.0, 0.0], body={"ang_min": [-60.0] * 3, "ang_max": [60.0] * 3}),
      {"kind": "modify_bone", "bone": "dyn_tag", "translation": [0.0, 0.0, 0.0], "rotation": [0.0, 0.0, 0.0], "scale": [1.0, 1.0, 1.0],
       "translation_mode": "Ignore", "rotation_mode": "Ignore", "scale_mode": "Ignore", "translation_space": "ComponentSpace",
       "rotation_space": "ComponentSpace", "alpha": 0.0})
run = Runner(shared)
pose0 = run.sample()
cs_fall = run.start(pose0)
swing = math.degrees(cs_fall["dyn_tag"][0].rotation_difference(compose_cs(run, pose0)["dyn_tag"][0]).angle)
check("a switched-off control leaves the simulation's result", swing > 1.0, True)

print("[dynamics_check] %d passed, %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
