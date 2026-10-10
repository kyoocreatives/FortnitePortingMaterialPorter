"""The RigVM units a character's own rig functions use, written from their documented behaviour. Each takes the
machine, its named operands, the operand list and its instruction index (for state kept between runs). Memory holds
values as the dump writes them: vectors and quaternions as {"X", "Y", "Z"(, "W")}, transforms as {"Rotation",
"Translation", "Scale3D"}, element keys as "Bone/name" or {"Type", "Name"}."""
import math

from mathutils import Quaternion, Vector

from .dynamics_solver import compose, relative
from .rig_vm import identity


def num(v, default=0.0):
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    return float(v) if isinstance(v, (int, float)) else default


def vec(v, default=(0.0, 0.0, 0.0)):
    if isinstance(v, dict):
        return Vector([num(v.get(k)) for k in "XYZ"])
    return Vector(default)


def quat(v):
    if isinstance(v, dict) and any(k in v for k in "XYZW"):
        q = Quaternion((num(v.get("W"), 0.0), num(v.get("X")), num(v.get("Y")), num(v.get("Z"))))
        return q.normalized() if q.magnitude > 1e-8 else Quaternion()
    return Quaternion()


def vec_out(v):
    return {"X": v.x, "Y": v.y, "Z": v.z}


def quat_out(q):
    return {"X": q.x, "Y": q.y, "Z": q.z, "W": q.w}


def transform(v):
    """(rotation, translation) of a transform value; scale is not carried (BoneControl's convention)."""
    v = v or {}
    return quat(v.get("Rotation")), vec(v.get("Translation"))


def transform_out(t):
    return {"Rotation": quat_out(t[0]), "Translation": vec_out(t[1]), "Scale3D": {"X": 1.0, "Y": 1.0, "Z": 1.0}}


def element(v):
    if isinstance(v, dict):
        v = v.get("Name")
    if not isinstance(v, str):
        return None
    name = v.split("/", 1)[-1]
    return None if name in ("None", "") else name


def _get(m, a, name, default=None):
    v = m.get(a.get(name))
    return default if v is None else v


def _set(m, a, name, value):
    m.set(a.get(name), value)


def epic_euler(q, order):
    """Euler angles (radians, by X, Y, Z) for turns applied in order's sequence, on Epic's branch: the middle angle
    from asin, within +-90 (mathutils may take the other, equal one; mixing branches flips a filtered bone)."""
    e = list(q.to_matrix().to_euler(order))
    first, mid, last = ("XYZ".index(c) for c in order)
    if abs(e[mid]) > math.pi / 2:
        e[first], e[mid], e[last] = e[first] + math.pi, math.pi - e[mid], e[last] + math.pi
    return [math.atan2(math.sin(a), math.cos(a)) for a in e]


# --- math

def _unary(fn):
    def unit(m, a, ops, pc):
        _set(m, a, "Result", fn(_get(m, a, "Value")))
    return unit


def _binary(fn):
    def unit(m, a, ops, pc):
        _set(m, a, "Result", fn(_get(m, a, "A"), _get(m, a, "B")))
    return unit


def remap(m, a, ops, pc):
    v, s0, s1 = num(_get(m, a, "Value")), num(_get(m, a, "SourceMinimum")), num(_get(m, a, "SourceMaximum"))
    t0, t1 = num(_get(m, a, "TargetMinimum")), num(_get(m, a, "TargetMaximum"))
    u = 0.0 if abs(s1 - s0) < 1e-8 else (v - s0) / (s1 - s0)
    if _get(m, a, "bClamp"):
        u = min(max(u, 0.0), 1.0)
    _set(m, a, "Result", t0 + (t1 - t0) * u)


def clamp(m, a, ops, pc):
    lo, hi = num(_get(m, a, "Minimum")), num(_get(m, a, "Maximum"))
    v = num(_get(m, a, "Value"))
    _set(m, a, "Result", min(max(v, lo), hi) if lo <= hi else min(max(v, hi), lo))


# --- dispatches (positional operands)

def if_(m, a, ops, pc):
    cond, yes, no, out = ops[0], ops[1], ops[2], ops[3]
    m.set(out, m.get(yes) if m.get(cond) else m.get(no))


def select_int32(m, a, ops, pc):
    index, values, out = int(num(m.get(ops[0]))), ops[1:-1], ops[-1]
    if values:
        m.set(out, m.get(values[index if 0 <= index < len(values) else 0]))


def cast_enum(m, a, ops, pc):
    v = m.get(ops[0])
    if isinstance(v, str):
        v = v.split("::")[-1]
        v = {"X": 0, "Y": 1, "Z": 2}.get(v, 0)
    m.set(ops[-1], int(num(v)))


# --- flow

def branch(m, a, ops, pc):
    """Run through twice per pass: first it picks the side, back from that side it says the branch is done."""
    current = _get(m, a, "BlockToRun")
    _set(m, a, "BlockToRun", "Completed" if current not in (None, "", "None", 0) else ("True" if _get(m, a, "Condition") else "False"))


def nothing(m, a, ops, pc):
    pass


# --- hierarchy readers

SCALES = ("PositiveWidth", "NegativeWidth", "PositiveHeight", "NegativeHeight")


def _relative_to_parent(h, item, parent, initial):
    own = h.get_global(item, initial)
    return relative(own, h.get_global(parent, initial)) if parent else own


def spherical_pose_reader(m, a, ops, pc):
    """deform_rig's exact reader: the driver's axis, turned from rest under its (optional) parent, in the sphere's
    frame."""
    from .deform_rig import _offset, spr_value
    driver = element(_get(m, a, "DriverItem"))
    if driver is None or not m.h.has(driver):
        _set(m, a, "OutputParam", 0.0)
        return
    parent = element(_get(m, a, "OptionalParentItem")) or m.h.parent.get(driver)
    turn = _relative_to_parent(m.h, driver, parent, True)[0].conjugated() @ _relative_to_parent(m.h, driver, parent, False)[0]
    factors = lambda v: [num((v or {}).get(k), 1.0) for k in SCALES]  # noqa: E731
    weight = {"region": num(_get(m, a, "ActiveRegionSize")), "region_scale": factors(_get(m, a, "ActiveRegionScaleFactors")),
              "falloff": num(_get(m, a, "FalloffSize")), "falloff_scale": factors(_get(m, a, "FalloffRegionScaleFactors")),
              "flip_width": bool(_get(m, a, "FlipWidthScaling")), "flip_height": bool(_get(m, a, "FlipHeightScaling"))}
    axis = _get(m, a, "DriverAxis")
    n = _offset(list(vec(_get(m, a, "RotationOffset")))).transposed() @ turn.to_matrix() @ (vec(axis) if axis else Vector((1.0, 0.0, 0.0)))
    _set(m, a, "OutputParam", spr_value(n, weight))


def get_relative_transform(m, a, ops, pc):
    child, parent = element(_get(m, a, "Child")), element(_get(m, a, "Parent"))
    c = m.h.get_global(child, bool(_get(m, a, "bChildInitial")))
    p = m.h.get_global(parent, bool(_get(m, a, "bParentInitial")))
    _set(m, a, "RelativeTransform", transform_out(relative(c, p)))


def _lerp(t0, t1, w):
    q1 = t1[0] if t0[0].dot(t1[0]) >= 0.0 else -t1[0]
    return t0[0].slerp(q1, w).normalized(), t0[1].lerp(t1[1], w)


MODES = {0: "OverrideLocal", 1: "OverrideGlobal", 2: "AdditiveLocal", 3: "AdditiveGlobal"}


def modify_transforms(m, a, ops, pc):
    mode = _get(m, a, "Mode", 0)
    mode = MODES.get(int(num(mode)), "OverrideLocal") if not isinstance(mode, str) else mode.split("::")[-1]
    w = num(_get(m, a, "Weight"))
    lo, hi = num(_get(m, a, "WeightMinimum")), num(_get(m, a, "WeightMaximum"))
    lo, hi = min(lo, hi), max(lo, hi)
    if w <= lo + 1e-8 or hi - lo <= 1e-8:
        return
    w = min(max((w - lo) / (hi - lo), 0.0), 1.0)
    blend = w < 1.0 - 1e-8
    for entry in _get(m, a, "ItemToModify") or []:
        name = element(entry.get("Item"))
        if name is None or not m.h.has(name):
            continue
        if mode.startswith("Additive") and identity(entry.get("Transform")):
            continue
        t = transform(entry.get("Transform"))
        local = mode in ("OverrideLocal", "AdditiveLocal")
        now = m.h.get_local(name) if local else m.h.get_global(name)
        if mode.startswith("Override"):
            result = _lerp(now, t, w) if blend else t
        else:       # the delta eased in from identity: before the local, or after the global
            delta = _lerp((Quaternion(), Vector()), t, w) if blend else t
            result = compose(delta, now) if local else compose(now, delta)
        (m.h.set_local if local else m.h.set_global)(name, result)


def alpha_interp(m, a, ops, pc):
    """Map, scale and bias, clamp, then ease toward the result from last run's (its first run takes it as is)."""
    v = num(_get(m, a, "Value"))
    if _get(m, a, "bMapRange"):
        r_in, r_out = _get(m, a, "InRange") or {}, _get(m, a, "OutRange") or {}
        i0, i1 = num(r_in.get("Min")), num(r_in.get("Max"))
        u = 0.0 if abs(i1 - i0) < 1e-8 else min(max((v - i0) / (i1 - i0), 0.0), 1.0)
        v = num(r_out.get("Min")) + (num(r_out.get("Max")) - num(r_out.get("Min"))) * u
    v = v * num(_get(m, a, "Scale"), 1.0) + num(_get(m, a, "Bias"))
    if _get(m, a, "bClampResult"):
        v = min(max(v, num(_get(m, a, "ClampMin"))), num(_get(m, a, "ClampMax")))
    if _get(m, a, "bInterpResult"):
        last = m.state.get(pc)
        if last is not None:
            speed = num(_get(m, a, "InterpSpeedIncreasing" if v >= last else "InterpSpeedDecreasing"))
            if speed > 0.0 and abs(v - last) > 1e-4:
                v = last + (v - last) * min(max(m.dt * speed, 0.0), 1.0)
        m.state[pc] = v
    _set(m, a, "Result", v)


def _curve(curve, t):
    keys = sorted(((curve or {}).get("EditorCurveData") or {}).get("Keys") or [], key=lambda k: num(k.get("Time")))
    if not keys:
        return num(((curve or {}).get("EditorCurveData") or {}).get("DefaultValue"))
    if t <= num(keys[0].get("Time")):
        return num(keys[0].get("Value"))
    if t >= num(keys[-1].get("Time")):
        return num(keys[-1].get("Value"))
    for k0, k1 in zip(keys, keys[1:]):
        t0, t1 = num(k0.get("Time")), num(k1.get("Time"))
        if t0 <= t <= t1:
            v0, v1, span = num(k0.get("Value")), num(k1.get("Value")), t1 - t0
            u = 0.0 if span < 1e-8 else (t - t0) / span
            mode = str(k0.get("InterpMode", "RCIM_Linear"))
            if mode.endswith("Constant"):
                return v0
            if mode.endswith("Linear"):
                return v0 + (v1 - v0) * u
            p1 = v0 + num(k0.get("LeaveTangent")) * span / 3.0
            p2 = v1 - num(k1.get("ArriveTangent")) * span / 3.0
            return ((1 - u) ** 3) * v0 + 3 * ((1 - u) ** 2) * u * p1 + 3 * (1 - u) * u * u * p2 + u ** 3 * v1
    return num(keys[-1].get("Value"))


def anim_eval_rich_curve(m, a, ops, pc):
    v = num(_get(m, a, "Value"))
    s0, s1 = num(_get(m, a, "SourceMinimum")), num(_get(m, a, "SourceMaximum"), 1.0)
    t0, t1 = num(_get(m, a, "TargetMinimum")), num(_get(m, a, "TargetMaximum"), 1.0)
    u = (1.0 if v >= s1 else 0.0) if abs(s1 - s0) < 1e-8 else min(max((v - s0) / (s1 - s0), 0.0), 1.0)
    _set(m, a, "Result", t0 + (t1 - t0) * _curve(_get(m, a, "Curve"), u))


def swing_twist(m, a, ops, pc):
    """Input = Swing * Twist with the twist about TwistAxis."""
    q = quat(_get(m, a, "Input"))
    axis = vec(_get(m, a, "TwistAxis"), (1.0, 0.0, 0.0))
    if max(abs(c) for c in axis) <= 1e-4:
        _set(m, a, "Swing", quat_out(Quaternion()))
        _set(m, a, "Twist", quat_out(Quaternion()))
        return
    axis = axis.normalized()
    proj = axis * Vector((q.x, q.y, q.z)).dot(axis)
    twist = Quaternion((q.w, proj.x, proj.y, proj.z))
    twist = twist.normalized() if twist.magnitude > 1e-8 else Quaternion()
    swing = q @ twist.conjugated()
    _set(m, a, "Swing", quat_out(swing))
    _set(m, a, "Twist", quat_out(twist))


def parent_constraint(m, a, ops, pc):
    """Weighted parents (offset from both rest poses when kept), mixed, filtered per axis in the child's parent
    space, eased in by the weight; scale is not carried."""
    from .deform_rig import ORDERS
    weight = num(_get(m, a, "Weight"), 1.0)
    child = element(_get(m, a, "Child"))
    if weight < 1e-4 or child is None or not m.h.has(child):
        return
    keep = _get(m, a, "bMaintainOffset", True)
    parents = [(element(p.get("Item")), max(num(p.get("Weight"), 1.0), 0.0)) for p in _get(m, a, "Parents") or [] if isinstance(p, dict)]
    parents = [(n, w) for n, w in parents if n and m.h.has(n) and w >= 1e-4]
    total = sum(w for _, w in parents)
    if total <= 1e-4:
        return
    settings = _get(m, a, "AdvancedSettings") or {}
    shortest = str(settings.get("InterpolationType", 0)).split("::")[-1] in ("1", "Shortest")
    q_mix, t_mix, acc = Quaternion((0.0, 0.0, 0.0, 0.0)), Vector(), 0.0
    for name, w in parents:
        n = w / total
        g = m.h.get_global(name)
        if keep:
            g = compose(relative(m.h.get_global(child, True), m.h.get_global(name, True)), g)
            g = (g[0].normalized(), g[1])
        if shortest:
            acc += n
            if q_mix.magnitude == 0.0:
                q_mix, t_mix = g[0].copy(), g[1].copy()
            else:
                t_mix = t_mix.lerp(g[1], n / acc)
                q_mix = _lerp((q_mix, Vector()), (g[0], Vector()), n)[0]
        else:
            q = Quaternion([c * n for c in g[0]])
            q_mix = Quaternion([x - y for x, y in zip(q_mix, q)]) if q.dot(q_mix) < 0.0 else Quaternion([x + y for x, y in zip(q_mix, q)])
            t_mix = t_mix + g[1] * n
    mixed = (q_mix.normalized() if q_mix.magnitude > 1e-4 else Quaternion(), t_mix)
    parent = m.h.parent.get(child)
    local = relative(mixed, m.h.get_global(parent)) if parent else mixed
    current = m.h.get_local(child)
    f = _get(m, a, "Filter") or {}
    tf, rf = f.get("TranslationFilter") or {}, f.get("RotationFilter") or {}
    pos = Vector([local[1][i] if tf.get(k, True) else current[1][i] for i, k in enumerate(("bX", "bY", "bZ"))])
    rot = local[0]
    if not all(rf.get(k, True) for k in ("bX", "bY", "bZ")):
        order = settings.get("RotationOrderForFilter", 1)
        order = ORDERS[int(num(order))] if not isinstance(order, str) else order.split("::")[-1]
        e_new, e_now = epic_euler(local[0], order), epic_euler(current[0], order)
        e = [e_new[i] if rf.get(k, True) else e_now[i] for i, k in enumerate(("bX", "bY", "bZ"))]
        from mathutils import Euler
        rot = Euler(e, order).to_quaternion().normalized()
    result = (rot, pos)
    if weight < 1.0 - 1e-4:
        result = _lerp(current, result, weight)
    m.h.set_local(child, result)


def get_transform(m, a, ops, pc):
    name = element(_get(m, a, "Item"))
    if name is None or not m.h.has(name):
        return
    initial, local = bool(_get(m, a, "bInitial")), int(num(_get(m, a, "Space", 1))) == 0
    _set(m, a, "Transform", transform_out(m.h.get_local(name, initial) if local else m.h.get_global(name, initial)))


def set_transform(m, a, ops, pc):
    name, weight = element(_get(m, a, "Item")), num(_get(m, a, "Weight"), 1.0)
    if weight < 1e-8 or name is None or not m.h.has(name) or _get(m, a, "bInitial"):
        return
    local = int(num(_get(m, a, "Space", 1))) == 0
    value = transform(_get(m, a, "Value"))
    if weight < 1.0 - 1e-8:
        value = _lerp(m.h.get_local(name) if local else m.h.get_global(name), value, weight)
    (m.h.set_local if local else m.h.set_global)(name, value)


def quaternion_to_euler(m, a, ops, pc):
    """AnimationCore's euler: X, Y, Z turns (degrees) applied in the order's sequence (deform_rig._epic_quat's inverse)."""
    from .deform_rig import ORDERS
    order = _get(m, a, "RotationOrder", 0)
    order = int(num(order)) if not isinstance(order, str) else ORDERS.index(order.split("::")[-1]) if order.split("::")[-1] in ORDERS else 0
    e = epic_euler(quat(_get(m, a, "Value")), ORDERS[order] if 0 <= order < len(ORDERS) else "XYZ")
    _set(m, a, "Result", {"X": math.degrees(e[0]), "Y": math.degrees(e[1]), "Z": math.degrees(e[2])})


UNITS = {
    "MathDoubleMul": _binary(lambda x, y: num(x) * num(y)), "MathFloatMul": _binary(lambda x, y: num(x) * num(y)),
    "MathDoubleAdd": _binary(lambda x, y: num(x) + num(y)), "MathDoubleSub": _binary(lambda x, y: num(x) - num(y)),
    "MathDoubleAbs": _unary(lambda x: abs(num(x))), "MathBoolToFloat": _unary(lambda x: 1.0 if x else 0.0),
    "MathBoolToInteger": _unary(lambda x: 1 if x else 0), "MathDoubleRemap": remap, "MathFloatRemap": remap,
    "MathDoubleClamp": clamp, "MathFloatClamp": clamp,
    "DISPATCH_RigVMDispatch_If": if_, "DISPATCH_RigVMDispatch_SelectInt32": select_int32,
    "DISPATCH_RigVMDispatch_CastEnumToInt": cast_enum, "DISPATCH_RigVMDispatch_Print": nothing,
    "ControlFlowBranch": branch, "Sequence": nothing, "BeginExecution": nothing, "PrepareForExecution": nothing,
    "SphericalPoseReader": spherical_pose_reader, "GetRelativeTransformForItem": get_relative_transform,
    "ModifyTransforms": modify_transforms, "AlphaInterp": alpha_interp, "AnimEvalRichCurve": anim_eval_rich_curve,
    "MathQuaternionSwingTwist": swing_twist, "MathQuaternionToEuler": quaternion_to_euler,
    "ParentConstraint": parent_constraint, "GetTransform": get_transform, "SetTransform": set_transform,
}
