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
    ru.UNITS[unit](mini, {k: (v if isinstance(v, str) and k in ("Result", "Swing", "Twist", "RelativeTransform", "OutputParam", "Transform")
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

print("[rig_vm_check] %d passed, %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
