"""Checks the character Control Rig runner: the RigVM machine's flow (branches, calls, lazy branches, paths), the units
the skirt functions use, compiling a dump and the node in the dynamics runner.

    blender -b --factory-startup --python-exit-code 1 -P tests/plugin/rig_vm_check.py -- <plugin parent>

<plugin parent> holds the plugin as package fpmp_baseline (see translator_test.py). Exit code 0 when every check passes."""
import math
import sys

import bpy  # noqa: F401
from mathutils import Quaternion, Vector

sys.path.insert(0, sys.argv[sys.argv.index("--") + 1])
import fpmp_baseline  # noqa: E402,F401
from fpmp_baseline.processing.context import rig_vm  # noqa: E402
from fpmp_baseline.processing.context.rig_units import UNITS  # noqa: E402

FAILS, PASSES = [], [0]


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
        print("[rig_vm_check] FAIL %s: got %r, want %r" % (name, got, want))


W = lambda i, off=-1: [0, i, off]  # noqa: E731
L = lambda i, off=-1: [1, i, off]  # noqa: E731
X = lambda i: [2, i, -1]  # noqa: E731


def ex(f, *ops):
    return {"o": rig_vm.EXECUTE, "f": f, "a": list(ops)}


def program(code, literals, callables=(), variables=("Crouching",), lit_paths=(), start=0):
    return {"start": start, "code": code, "callables": list(callables), "literals": {str(k): v for k, v in literals.items()},
            "defaults": {}, "lit_paths": list(lit_paths), "work_paths": [], "ext_paths": [], "variables": list(variables),
            "units": {"MathDoubleMul": ["A", "B", "Result"], "ControlFlowBranch": ["Condition", "BlockToRun"],
                      "MathDoubleAbs": ["Value", "Result"]}, "bones": [], "nulls": [], "writes": []}


# --- flow
flow = program([
    ex("BeginExecution"),
    {"o": rig_vm.ZERO, "t": W(0)},
    ex("ControlFlowBranch", X(0), W(0)),
    {"o": rig_vm.JUMP_TO_BRANCH, "arg": W(0), "branches": {"True": 4, "False": 6, "Completed": 8}},
    ex("MathDoubleMul", L(0), L(1), W(1)),
    {"o": rig_vm.JUMP_BACK, "i": 3},
    {"o": rig_vm.COPY, "s": L(2), "t": W(1)},
    {"o": rig_vm.JUMP_BACK, "i": 5},
    {"o": rig_vm.CALL, "c": 0, "a": [L(0), W(2)]},
    {"o": rig_vm.COPY, "s": L(3, 0), "t": W(4)},
    {"o": rig_vm.EXIT},
    ex("MathDoubleMul", W(10), W(10), W(11)),
    {"o": rig_vm.RUN, "arg": W(12), "first": 14, "last": 14},
    {"o": rig_vm.RUN, "arg": W(12), "first": 14, "last": 14},
    ex("MathDoubleAbs", W(3), W(3)),
], {0: 2.0, 1: 3.0, 2: -1.0, 3: {"Translation": {"Z": 5.0}}},
    callables=[{"name": "Square", "first": 11, "last": 13, "args": [{"name": "x", "fwd": W(10), "dir": 0},
                                                                    {"name": "y", "fwd": W(11), "dir": 1}]}],
    lit_paths=["Translation.Z"])
m = rig_vm.Machine(flow, UNITS)
m.work[3] = -1.5
m.run(None, 1 / 60, {"Crouching": True})
check("a branch on a rig variable runs its true side", m.work.get(1), 6.0)
check("a call takes its argument and hands back its output", m.work.get(2), 4.0)
check("a lazy branch runs once per call", m.work.get(3), 1.5)
check("a path reads into a struct", m.work.get(4), 5.0)
m.reset({})
m.run(None, 1 / 60, {"Crouching": False})
check("and its false side otherwise", m.work.get(1), -1.0)

# --- compiling: a null the program names but the dumped hierarchy lacks is still placed
tiny = [{"Type": "ControlRigBlueprintGeneratedClass", "ChildProperties": []},
        {"Type": "RigVM", "ByteCodeStorage": {"Entries": ['(Name="Forwards Solve",InstructionIndex=0)'], "CallableInfos": [], "BranchInfos": [],
                                              "Instructions": [{"OpCode": 101, "FunctionIndex": 0, "Arguments": [{"MemoryType": 1, "RegisterIndex": 0, "RegisterOffset": 65535}]},
                                                               {"OpCode": 80}]},
         "LiteralMemoryStorage": {"PropertyDescs": [{"Name": "L0"}], "Values": {"L0": "Null/thigh_l_null"}, "PropertyPathDescriptions": []},
         "DefaultWorkMemoryStorage": {"PropertyDescs": [], "Values": {}, "PropertyPathDescriptions": []},
         "FunctionNamesStorage": ["FRigUnit_GetRelativeTransformForItem::Execute"],
         "LocalizedRegistry": {"Functions": [{"Name": "FRigUnit_GetRelativeTransformForItem::Execute", "Arguments": [{"LocalNameString": "Parent"}]}]}}]
check("a null named only by the program is placed", rig_vm.compile_rig(tiny)["nulls"], ["thigh_l_null"])


def stored(rot, pos):
    return {"Transform": {"Rotation": dict(zip("XYZW", rot)), "Translation": dict(zip("XYZ", pos))}, "bDirty": False}


ident = (0.0, 0.0, 0.0, 1.0)
control = {"LoadedKey": {"Type": 4, "Name": "chain_ctrl"}, "ParentConstraints": [{"ParentKey": {"Type": 1, "Name": "pelvis"}}],
           "PoseStorage": {"Initial": {"Local": stored(ident, (4.0, 0.0, 0.0))}},
           "Offset": {"Initial": {"Local": stored(ident, (0.0, 0.0, -90.0))}}}
spaces = rig_vm.compile_rig(tiny + [{"Type": "RigHierarchy", "Elements": [control]}])["spaces"]
check("a control's parent and rest come from the hierarchy", (spaces["chain_ctrl"]["parent"], spaces["chain_ctrl"]["local"]["Translation"]["X"]), ("pelvis", 4.0))

# --- the hierarchy a rig runs on
from fpmp_baseline.processing.context.rig_node import Hierarchy  # noqa: E402

turned = Quaternion(Vector((0, 0, 1)), math.radians(90.0))
parents = {"root": None, "thigh_l": "root", "skirt": "root"}
rest = {"root": (Quaternion(), Vector()), "thigh_l": (turned, Vector((0, 10, 0))), "skirt": (Quaternion(), Vector((0, 0, 5)))}
h = Hierarchy(parents, rest, ["thigh_l_null"])
lifted = (turned @ Quaternion(Vector((0, 1, 0)), math.radians(30.0)), Vector((0, 10, 0)))
h.load({"root": rest["root"], "thigh_l": lifted, "skirt": rest["skirt"]})
check("a null sits at its bone's rest beside it", tuple(h.get_global("thigh_l_null")[1]), (0.0, 10.0, 0.0))
check("a bone's local is taken from the pose", round(h.get_local("thigh_l")[0].rotation_difference(lifted[0]).angle, 6), 0.0)
h.set_local("root", (Quaternion(), Vector((1, 0, 0))))
check("moving a parent moves its children's globals", tuple(h.get_global("skirt")[1]), (1.0, 0.0, 5.0))
check("and the changed bones are known", sorted(h.changed), ["root"])
hc = Hierarchy({"pelvis": None}, {"pelvis": (Quaternion(), Vector((0, 0, 90)))}, [], spaces)
check("a control sits at its value after its offset, under its parent", tuple(hc.get_global("chain_ctrl")[1]), (4.0, 0.0, 0.0))
hc.load({"pelvis": (Quaternion(), Vector((0, 0, 100)))})
check("and follows its parent", tuple(hc.get_global("chain_ctrl")[1]), (4.0, 0.0, 10.0))

# --- units
from fpmp_baseline.processing.context import rig_units as ru  # noqa: E402


class Mini:
    """A machine stand-in: named operands map straight to values, results land in out."""
    def __init__(self, hierarchy, dt=1 / 30):
        self.h, self.dt, self.state, self.out = hierarchy, dt, {}, {}

    def get(self, o):
        return o[1] if isinstance(o, tuple) and o[0] == "v" else self.out.get(o)

    def set(self, o, v):
        self.out[o] = v


def call(unit, mini, pc=0, **named):
    ru.UNITS[unit](mini, {k: (v if isinstance(v, str) and k in ("Result", "Swing", "Twist", "RelativeTransform", "OutputParam", "Transform",
                                                                  "Remainder", "Chopped", "Exists", "Local")
                              else ("v", v)) for k, v in named.items()}, [], pc)


h2 = Hierarchy(parents, rest, [])
h2.load(dict(rest))
mini = Mini(h2)
spin = {"Rotation": {"X": 0.0, "Y": 0.0, "Z": math.sin(math.radians(45)), "W": math.cos(math.radians(45))}}
call("ModifyTransforms", mini, ItemToModify=[{"Item": {"Name": "skirt"}, "Transform": spin}], Weight=0.5, WeightMinimum=0.0,
     WeightMaximum=1.0, Mode=2)
check("an additive local at half weight turns half way", round(math.degrees(h2.get_local("skirt")[0].angle), 3), 45.0)
call("ModifyTransforms", mini, ItemToModify=[{"Item": {"Name": "skirt"}, "Transform": spin}], Weight=0.0, WeightMinimum=0.0,
     WeightMaximum=1.0, Mode=2)
check("at zero weight nothing moves", round(math.degrees(h2.get_local("skirt")[0].angle), 3), 45.0)

h3 = Hierarchy(parents, rest, [])
h3.load(dict(rest))
h3.set_local("thigh_l", (turned, Vector((0, 20, 0))))
call("ParentConstraint", Mini(h3), Child="Bone/skirt", bMaintainOffset=True, Parents=[{"Item": "Bone/thigh_l", "Weight": 1.0}], Weight=1.0)
check("a parent constraint keeps its rest offset and follows", tuple(round(c, 4) for c in h3.get_global("skirt")[1]), (0.0, 10.0, 5.0))

curve = {"EditorCurveData": {"Keys": [{"Time": 0.0, "Value": 0.0, "InterpMode": "RCIM_Cubic", "LeaveTangent": 0.0},
                                      {"Time": 1.0, "Value": 1.0, "ArriveTangent": 0.0}]}}
m4 = Mini(h3)
call("AnimEvalRichCurve", m4, Value=0.5, Curve=curve, SourceMinimum=0.0, SourceMaximum=1.0, TargetMinimum=0.0, TargetMaximum=2.0, Result="r")
check("a flat-tangent cubic is half way at the middle, remapped", m4.out["r"], 1.0)
call("AnimEvalRichCurve", m4, Value=5.0, Curve=curve, SourceMinimum=0.0, SourceMaximum=1.0, TargetMinimum=0.0, TargetMaximum=2.0, Result="r")
check("its input is clamped first", m4.out["r"], 2.0)
call("AlphaInterp", m4, pc=7, Value=1.0, Scale=1.0, bInterpResult=True, InterpSpeedIncreasing=10.0, InterpSpeedDecreasing=10.0, Result="r")
check("an interpolated alpha takes its first value as is", m4.out["r"], 1.0)
call("AlphaInterp", m4, pc=7, Value=0.0, Scale=1.0, bInterpResult=True, InterpSpeedIncreasing=10.0, InterpSpeedDecreasing=10.0, Result="r")
check("then eases toward the next", m4.out["r"], 1.0 - 10.0 / 30.0)

# Epic's euler: the middle angle from asin, so it stays within +-90 (mixing two branches flips a filtered bone)
from mathutils import Euler  # noqa: E402
q_wide = Euler((math.radians(170.0), math.radians(20.0), math.radians(-160.0)), "XZY").to_quaternion()
e_wide = ru.epic_euler(q_wide, "XZY")
check("euler middle angle within 90", abs(e_wide[2]) <= math.pi / 2 + 1e-6, True)
check("and it rebuilds the rotation", round(Euler(e_wide, "XZY").to_quaternion().rotation_difference(q_wide).angle, 5), 0.0)

# --- the smaller units characters' rigs run
m5 = Mini(h3)
call("MathBoolOr", m5, A=False, B=True, Result="r")
check("bool or", m5.out["r"], True)
call("MathIntClamp", m5, Value=7, Minimum=0, Maximum=3, Result="r")
check("int clamp", m5.out["r"], 3)
call("NameTruncate", m5, Name="thigh_l_ctrl", Count=5, FromEnd=True, Remainder="r", Chopped="c")
check("a name loses its end", (m5.out["r"], m5.out["c"]), ("thigh_l", "_ctrl"))
call("ItemExists", m5, Item={"Name": "skirt"}, Exists="r")
check("an item the rig has exists", m5.out["r"], True)
call("ItemExists", m5, Item={"Name": "thigh_l_ctrl"}, Exists="r")
check("one it lacks doesn't", m5.out["r"], False)
g = {"Rotation": {"X": 0.0, "Y": 0.0, "Z": 0.0, "W": 1.0}, "Translation": {"X": 5.0, "Y": 0.0, "Z": 0.0}}
pt = {"Rotation": {"X": 0.0, "Y": 0.0, "Z": 0.0, "W": 1.0}, "Translation": {"X": 2.0, "Y": 0.0, "Z": 0.0}}
call("MathTransformMakeRelative", m5, Global=g, Parent=pt, Local="r")
check("make relative writes its local", m5.out["r"]["Translation"]["X"], 3.0)

# --- lazy inputs: the If's inputs sit behind a jump and run when the If does
lazy_if = program([
    {"o": rig_vm.JUMP_FWD, "i": 3},
    ex("MathDoubleMul", L(0), L(1), W(1)),
    ex("MathDoubleAbs", L(2), W(2)),
    {"o": rig_vm.EXECUTE, "f": "DISPATCH_RigVMDispatch_If", "a": [X(0), W(1), W(2), W(3)]},
    {"o": rig_vm.EXIT},
], {0: 2.0, 1: 3.0, 2: -5.0}, lit_paths=[])
lazy_if = rig_vm.finish(lazy_if)
m9 = rig_vm.Machine(lazy_if, UNITS)
m9.run(None, 1 / 60, {"Crouching": True})
check("an If computes its skipped inputs", m9.work.get(3), 6.0)
m9.run(None, 1 / 60, {"Crouching": False})
check("and picks the other side", m9.work.get(3), 5.0)

# --- a for-each loop: the iterator re-runs after each pass, the body's lazy branch each pass
loop = program([
    {"o": rig_vm.ZERO, "t": W(0)},
    {"o": rig_vm.EXECUTE, "f": "DISPATCH_RigVMDispatch_ArrayIterator", "a": [L(0), W(1), W(2), W(3), W(4), W(0)]},
    {"o": rig_vm.JUMP_TO_BRANCH, "arg": W(0), "branches": {"ExecuteContext": 3, "Completed": 7}},
    {"o": rig_vm.BEGIN_BLOCK},
    {"o": rig_vm.RUN, "arg": W(9), "first": 8, "last": 8},
    {"o": rig_vm.END_BLOCK},
    {"o": rig_vm.JUMP_BACK, "i": 5},
    {"o": rig_vm.EXIT},
    ex("MathDoubleMul", W(1), W(5), W(5)),
], {0: [2.0, 3.0, 4.0]})
m6 = rig_vm.Machine(loop, UNITS)
m6.work[5] = 1.0
m6.run(None, 1 / 60, {})
check("a for-each runs its body once per element", (m6.work.get(5), m6.work.get(2), m6.work.get(1)), (24.0, 3, 4.0))

from fpmp_baseline.processing.context import rig_constraints as rc  # noqa: E402
units_all = dict(ru.UNITS, **rc.UNITS)


def call2(unit, mini, **named):
    units_all[unit](mini, {k: (v if isinstance(v, str) and k in ("Result",) else ("v", v)) for k, v in named.items()}, [], 0)


h4 = Hierarchy(parents, rest, [])
h4.load(dict(rest))
h4.set_local("thigh_l", (turned, Vector((0, 20, 0))))
call2("PositionConstraintLocalSpaceOffset", Mini(h4), Child="Bone/skirt", bMaintainOffset=True, Parents=[{"Item": "Bone/thigh_l", "Weight": 1.0}], Weight=1.0)
check("a position constraint keeps its rest offset", tuple(round(c, 4) for c in h4.get_global("skirt")[1]), (0.0, 10.0, 5.0))
h5 = Hierarchy(parents, rest, [])
h5.load(dict(rest))
spun = Quaternion(Vector((0, 0, 1)), math.radians(30.0))
h5.set_local("thigh_l", (turned @ spun, Vector((0, 10, 0))))
call2("RotationConstraintLocalSpaceOffset", Mini(h5), Child="Bone/skirt", bMaintainOffset=True, Parents=[{"Item": "Bone/thigh_l", "Weight": 1.0}], Weight=1.0)
check("a rotation constraint follows the parent's turn from rest", round(math.degrees(h5.get_local("skirt")[0].angle), 3), 30.0)
m7 = Mini(h5)
call2("AimBoneMath", m7, InputTransform={"Rotation": {"W": 1.0}, "Translation": {"X": 0.0}},
      Primary={"Weight": 1.0, "Axis": {"X": 1.0, "Y": 0.0, "Z": 0.0}, "Target": {"X": 0.0, "Y": 5.0, "Z": 0.0}, "Kind": "EControlRigVectorKind::Location"},
      Secondary={"Weight": 0.0}, Weight=1.0, Result="r")
check("aim turns the axis onto the target", tuple(round(c, 4) for c in ru.quat(m7.out["r"]["Rotation"]) @ Vector((1, 0, 0))), (0.0, 1.0, 0.0))
h6 = Hierarchy(parents, rest, [])
h6.load(dict(rest))
call2("OffsetTransformForItem", Mini(h6), Item="Bone/skirt", OffsetTransform={"Rotation": {"W": 1.0}, "Translation": {"X": 2.0, "Y": 0.0, "Z": 0.0}}, Weight=1.0)
check("an offset moves the item in its own space", tuple(round(c, 4) for c in h6.get_global("skirt")[1]), (2.0, 0.0, 5.0))
m8 = Mini(h6)
call2("MathDoubleDiv", m8, A=3.0, B=0.0, Result="r")
check("dividing by zero gives 0", m8.out["r"], 0.0)
call2("MathQuaternionFromRotatorV2", m8, Value={"Pitch": 0.0, "Yaw": 90.0, "Roll": 0.0}, Result="r")
check("a 90 yaw turns X onto Y", tuple(round(c, 4) for c in ru.quat(m8.out["r"]) @ Vector((1, 0, 0))), (0.0, 1.0, 0.0))

call2("MathTransformTransformVector", m8, Transform={"Rotation": {"X": -math.sin(math.pi / 4), "Y": 0.0, "Z": 0.0, "W": math.cos(math.pi / 4)},
                                                    "Translation": {"X": 1.0, "Y": 2.0, "Z": 3.0}}, Location={"X": 0.0, "Y": 0.0, "Z": 4.0}, Result="r")
check("a point through a transform (the engine's own case)", tuple(round(m8.out["r"][k], 4) for k in "XYZ"), (1.0, 6.0, 3.0))

from fpmp_baseline.processing.context import deform_rig  # noqa: E402
try:
    deform_rig._ellipse(0.1, 0.1, 0.0, 0.5)
    flat_ok = True
except ZeroDivisionError:
    flat_ok = False
check("a pose reader region scaled flat doesn't divide by zero", flat_ok, True)

def broken(m, a, ops, pc):
    raise ZeroDivisionError("float division by zero")


fragile = program([ex("Broken"), ex("MathDoubleMul", L(0), L(1), W(1)), {"o": rig_vm.EXIT}], {0: 2.0, 1: 3.0})
m10 = rig_vm.Machine(fragile, dict(UNITS, Broken=broken))
m10.run(None, 1 / 60, {})
check("a failing unit is noted and the rest still runs", (sorted(m10.failed), m10.work.get(1)), (["Broken"], 6.0))

# --- more of the units characters' rigs run (a top-level input left at zero reads None)
m11 = Mini(h6)
call2("MathDoubleMod", m11, A=-1.0, B=3.0, Result="r")
check("mod keeps the sign of A", m11.out["r"], -1.0)
call2("MathDoubleMod", m11, A=5.0, B=-2.0, Result="r")
check("mod by a negative is 0", m11.out["r"], 0.0)
call2("MathDoubleLessEqual", m11, A=2.0, B=2.0, Result="r")
check("less or equal", m11.out["r"], True)
call2("MathVectorDistance", m11, A={"X": 0.0, "Y": 0.0, "Z": 0.0}, B={"X": 3.0, "Y": 4.0, "Z": 0.0}, Result="r")
check("vector distance", m11.out["r"], 5.0)
call2("MathVectorDot", m11, A={"X": 1.0, "Y": 2.0, "Z": 3.0}, B={"X": 4.0, "Y": 5.0, "Z": 6.0}, Result="r")
check("vector dot", m11.out["r"], 32.0)
call2("MathFloatMax", m11, A=1.5, B=-2.0, Result="r")
check("float max", m11.out["r"], 1.5)
call2("MathTransformMake", m11, Translation={"X": 1.0, "Y": 2.0, "Z": 3.0}, Rotation=spin["Rotation"], Scale={"X": 1.0, "Y": 1.0, "Z": 1.0}, Result="r")
check("make a transform", (m11.out["r"]["Translation"]["Y"], round(m11.out["r"]["Rotation"]["Z"], 4)), (2.0, round(math.sin(math.radians(45)), 4)))
check("a sequence is a no-op (its chains follow inline)", "SequenceExecution" in units_all, True)
for v in (2.0, 4.0):
    call2("KalmanFloat", m11, Value=v, BufferSize=16, Result="r")
check("kalman: the mean of the values seen", m11.out["r"], 3.0)
call2("KalmanFloat", m11, Value=7.0, BufferSize=None, Result="r")
check("kalman without a buffer passes the value", m11.out["r"], 7.0)

def clamped(value, **kw):
    t = {"Rotation": {"W": 1.0}, "Translation": dict(zip("XYZ", value))}
    call2("MathTransformClampSpatially", m11, Value=t, Space={"Rotation": {"W": 1.0}, "Translation": {"X": 0.0}}, Result="r", **kw)
    return tuple(round(m11.out["r"]["Translation"][k], 4) for k in "XYZ")


check("sphere clamp pushes out to its minimum", clamped((0.0, 0.0, 1.0), Type=2, Minimum=5.0, Maximum=10.0), (0.0, 0.0, 5.0))
check("and pulls in to its maximum", clamped((20.0, 0.0, 0.0), Type=2, Minimum=5.0, Maximum=10.0), (10.0, 0.0, 0.0))
check("plane clamp on Z", clamped((1.0, 2.0, -3.0), Type=None, Axis=3, Minimum=None, Maximum=100.0), (1.0, 2.0, 0.0))
check("cylinder about X keeps the X part", clamped((7.0, 1.0, 0.0), Type=1, Axis=1, Minimum=4.0, Maximum=None), (7.0, 4.0, 0.0))

h7 = Hierarchy(parents, rest, [])
h7.load(dict(rest))
moved = {"Rotation": {"W": 1.0}, "Translation": {"X": 3.0, "Y": 0.0, "Z": 0.0}}
call2("SetTransformItemArray", Mini(h7), Items=["Bone/skirt"], Space=1, bInitial=None, Transforms=[moved, moved], Weight=1.0, bPropagateToChildren=True)
check("set transforms with a count mismatch does nothing", tuple(h7.get_global("skirt")[1]), (0.0, 0.0, 5.0))
call2("SetTransformItemArray", Mini(h7), Items=["Bone/skirt"], Space=1, bInitial=None, Transforms=[moved], Weight=1.0, bPropagateToChildren=True)
check("set transforms places each item", tuple(h7.get_global("skirt")[1]), (3.0, 0.0, 0.0))
pose = {"Elements": [{"Index": {"Key": {"Type": "ERigElementType::Bone", "Name": "skirt"}},
                     "LocalTransform": {"Rotation": {"W": 1.0}, "Translation": {"X": 0.0, "Y": 6.0, "Z": 0.0}}}]}
call2("HierarchySetPoseItemArray", Mini(h7), Pose=pose, ElementType=239, Space=None, ItemsToSet=[], Weight=1.0)
check("set pose writes the pose's locals", tuple(h7.get_local("skirt")[1]), (0.0, 6.0, 0.0))
pose["Elements"][0]["LocalTransform"]["Translation"]["Y"] = 9.0
call2("HierarchySetPoseItemArray", Mini(h7), Pose=pose, ElementType=239, Space=None, ItemsToSet=["Bone/root"], Weight=1.0)
check("only the items asked for", tuple(h7.get_local("skirt")[1]), (0.0, 6.0, 0.0))
call2("AimItem", Mini(h7), Item="Bone/skirt", Weight=1.0, Secondary={"Weight": 0.0},
      Primary={"Weight": 1.0, "Axis": {"X": 1.0, "Y": 0.0, "Z": 0.0}, "Target": {"X": 0.0, "Y": 6.0, "Z": 10.0}, "Kind": "EControlRigVectorKind::Location"})
check("aim item turns its axis onto the target", tuple(round(c, 4) for c in h7.get_global("skirt")[0] @ Vector((1, 0, 0))), (0.0, 0.0, 1.0))

# a blank top-level input is the property bag's zero, not the unit's editor default
h8 = Hierarchy(parents, rest, [])
h8.load(dict(rest))
h8.set_local("root", (Quaternion(), Vector((1, 0, 0))))
m12 = Mini(h8)
call("GetTransform", m12, Item="Bone/thigh_l", Space=None, bInitial=None, Transform="t")
check("a blank space is local", tuple(round(c, 4) for c in ru.vec(m12.out["t"]["Translation"])), (0.0, 10.0, 0.0))
call2("MathQuaternionFromEuler", m12, Euler={"X": 90.0, "Y": 0.0, "Z": 90.0}, RotationOrder=None, Result="r")
from fpmp_baseline.processing.context.deform_rig import _epic_quat  # noqa: E402
xyz = _epic_quat([90.0, 0.0, 90.0], 0).to_quaternion()
check("a blank rotation order is XYZ", round(ru.quat(m12.out["r"]).rotation_difference(xyz).angle, 5), 0.0)
h8.set_local("root", (Quaternion(), Vector()))
h8.set_local("thigh_l", (turned, Vector((0, 20, 0))))
call("ParentConstraint", Mini(h8), Child="Bone/skirt", bMaintainOffset=True, Parents=[{"Item": "Bone/thigh_l", "Weight": 1.0}], Weight=None)
check("a blank constraint weight leaves the child", tuple(round(c, 4) for c in h8.get_global("skirt")[1]), (0.0, 0.0, 5.0))

print("[rig_vm_check] %d passed, %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
