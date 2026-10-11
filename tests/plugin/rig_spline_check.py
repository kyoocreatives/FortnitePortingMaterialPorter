"""Checks the Control Rig spline units characters' rigs run: building a spline from points, its queries, and fitting a
chain to it.

    blender -b --factory-startup --python-exit-code 1 -P tests/plugin/rig_spline_check.py -- <plugin parent>

<plugin parent> holds the plugin as package fpmp_baseline (see translator_test.py). Exit code 0 when every check passes."""
import copy
import sys

from mathutils import Quaternion, Vector

sys.path.insert(0, sys.argv[sys.argv.index("--") + 1])
from fpmp_baseline.processing.context import rig_splines as rs  # noqa: E402
from fpmp_baseline.processing.context.rig_node import Hierarchy, UNITS  # noqa: E402

FAILS, PASSES = [], [0]


def check(name, got, want, tol=1e-3):
    if isinstance(want, (tuple, list)):
        ok = len(got) == len(want) and all(abs(g - w) <= tol if isinstance(w, float) else g == w for g, w in zip(got, want))
    else:
        ok = abs(got - want) <= tol if isinstance(want, float) else got == want
    if ok:
        PASSES[0] += 1
    else:
        FAILS.append(name)
        print("[rig_spline_check] FAIL %s: got %r, want %r" % (name, got, want))


class Mini:
    def __init__(self, hierarchy=None):
        self.h, self.dt, self.state, self.out = hierarchy, 1 / 30, {}, {}

    def get(self, o):
        return o[1] if isinstance(o, tuple) and o[0] == "v" else self.out.get(o)

    def set(self, o, v):
        self.out[o] = v


OUTPUTS = ("Spline", "Position", "Transform", "U", "Length")


def call(unit, m, pc=0, **named):
    UNITS[unit](m, {k: (v if isinstance(v, str) and k in OUTPUTS else ("v", v)) for k, v in named.items()}, [], pc)


def p(x, y=0.0, z=0.0):
    return {"X": x, "Y": y, "Z": z}


def rounded(v):
    return tuple(round(v[k], 3) for k in "XYZ")


m = Mini()
line = [p(0.0), p(10.0), p(20.0), p(30.0)]
call("ControlRigSplineFromPoints", m, Points=line, SplineMode=1, bClosed=None, SamplesPerSegment=16, Compression=None, Stretch=None, Spline="s")
spline = m.out["s"]
check("a hermite spline samples (points - 1) * samples per segment", len(spline.samples), 48)
call("PositionFromControlRigSpline", m, Spline=spline, U=0.5, Position="pos")
check("half way along a straight one", rounded(m.out["pos"]), (15.0, 0.0, 0.0))
call("GetLengthControlRigSpline", m, Spline=spline, Length="len")
check("its length", m.out["len"], 30.0)
call("ClosestParameterFromControlRigSpline", m, Spline=spline, Position=p(15.0, 5.0), U="u")
check("the closest parameter to a point beside it", m.out["u"], 0.5)
call("TransformFromControlRigSpline2", m, Spline=spline, U=0.5, PrimaryAxis=p(1.0), SecondaryAxis=p(0.0, 1.0), Transform="t")
q = Quaternion([m.out["t"]["Rotation"][k] for k in "WXYZ"])
check("transform: the primary axis along the curve", tuple(q @ Vector((1, 0, 0))), (1.0, 0.0, 0.0))
check("the secondary onto the sample's Z", tuple(q @ Vector((0, 1, 0))), (0.0, 0.0, 1.0))
call("GetLengthAtParamControlRigSpline", m, Spline=spline, U=0.5, Length="lenhalf")
check("the length up to a parameter", m.out["lenhalf"], 15.0)
call("ParameterAtPercentage", m, Spline=spline, Percentage=0.25, U="uq")
check("the parameter a quarter of the length along", m.out["uq"], 0.25)
check("a spline is a handle: copies share it", copy.deepcopy(spline) is spline, True)

call("ControlRigSplineFromPoints", m, Points=line[:3], SplineMode=1, SamplesPerSegment=16, Spline="few")
call("PositionFromControlRigSpline", m, Spline=m.out["few"], U=0.5, Position="untouched")
check("under 4 points the spline is invalid and queries leave outputs", m.out.get("untouched"), None)

bent = [p(0.0), p(10.0), p(10.0, 10.0), p(0.0, 10.0)]
call("ControlRigSplineFromPoints", m, Points=bent, SplineMode=0, SamplesPerSegment=16, Spline="b")
call("PositionFromControlRigSpline", m, Spline=m.out["b"], U=0.0, Position="start")
start = rs.vec(m.out["start"])
check("an open b-spline starts near (not on) its first point", (start.length < 0.05, start.length > 0.0), (True, True))
call("PositionFromControlRigSpline", m, Spline=m.out["b"], U=0.5, Position="mid")
check("and passes inside its control polygon", rounded(m.out["mid"]), (7.5, 5.0, 0.0), 0.05)

# --- a chain fitted to the spline: three bones 5 apart along Z laid along X
parents = {"a": None, "b": "a", "c": "b", "tip": "c"}
rest = {"a": (Quaternion(), Vector()), "b": (Quaternion(), Vector((0, 0, 5))), "c": (Quaternion(), Vector((0, 0, 10))),
        "tip": (Quaternion(), Vector((0, 0, 12)))}


def fitted(alignment, length=20.0, **kw):
    h = Hierarchy(parents, rest, [])
    h.load(dict(rest))
    mm = Mini(h)
    pts = [p(length * i / 3.0) for i in range(4)]
    call("ControlRigSplineFromPoints", mm, Points=pts, SplineMode=1, SamplesPerSegment=16, Spline="s")
    args = dict(Items=["Bone/a", "Bone/b", "Bone/c"], Spline=mm.out["s"], Alignment=alignment, Minimum=None, Maximum=1.0,
                SamplingPrecision=12, PrimaryAxis=p(0.0, 0.0, 1.0), SecondaryAxis=None, PoleVectorPosition=None, Rotations=[],
                RotationEaseType=None, Weight=1.0, bPropagateToChildren=True)
    args.update(kw)
    call("FitChainToSplineCurveItemArray", mm, **args)
    return h


h = fitted(1)
check("stretched: the chain spans the curve", [round(h.get_global(n)[1].x, 2) for n in "abc"], [0.0, 10.0, 20.0])
check("each bone aims down the curve", tuple(h.get_global("a")[0] @ Vector((0, 0, 1))), (1.0, 0.0, 0.0))
check("a child follows its parent", tuple(round(c, 3) for c in h.get_global("tip")[1]), (22.0, 0.0, 0.0))
h = fitted(0)
check("front: bones keep their lengths", [round(h.get_global(n)[1].x, 2) for n in "abc"], [0.0, 5.0, 10.0])
h = fitted(0, length=6.0)
check("past the curve's end the chain runs on along it", [round(h.get_global(n)[1].x, 2) for n in "abc"], [0.0, 5.0, 10.0])
h = fitted(1, Weight=0.5)
check("half weight eases from the pose", round(h.get_global("c")[1].z, 2) > 0.0, True)

print("[rig_spline_check] %d passed, %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
