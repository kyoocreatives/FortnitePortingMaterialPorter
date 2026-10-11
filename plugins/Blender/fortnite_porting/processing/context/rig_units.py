"""The RigVM units a character's own rig functions use, written from their documented behaviour. Each takes the
machine, its named operands, the operand list and its instruction index (for state kept between runs). Memory holds
values as the dump writes them: vectors and quaternions as {"X", "Y", "Z"(, "W")}, transforms as {"Rotation",
"Translation", "Scale3D"}, element keys as "Bone/name" or {"Type", "Name"}. A blank input is zero (the dump leaves
zero values out), not the unit's editor default; a struct's blank fields are the struct's defaults."""
import copy
import math
import struct

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
    v = v * num(_get(m, a, "Scale")) + num(_get(m, a, "Bias"))
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
    s0, s1 = num(_get(m, a, "SourceMinimum")), num(_get(m, a, "SourceMaximum"))
    t0, t1 = num(_get(m, a, "TargetMinimum")), num(_get(m, a, "TargetMaximum"))
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


def mix_parents(gs, settings):
    """Weighted parent transforms [(transform, normalised weight)] mixed as ParentConstraint does: an average, or
    Shortest (each slerped in by its weight, the engine's quirk kept)."""
    shortest = str((settings or {}).get("InterpolationType", 0)).split("::")[-1] in ("1", "Shortest")
    q_mix, t_mix, acc = Quaternion((0.0, 0.0, 0.0, 0.0)), Vector(), 0.0
    for g, n in gs:
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
    return q_mix.normalized() if q_mix.magnitude > 1e-4 else Quaternion(), t_mix


def parent_constraint(m, a, ops, pc):
    """Weighted parents (offset from both rest poses when kept), mixed, filtered per axis in the child's parent
    space, eased in by the weight; scale is not carried."""
    from .deform_rig import ORDERS
    weight = num(_get(m, a, "Weight"))
    child = element(_get(m, a, "Child"))
    if weight < 1e-4 or child is None or not m.h.has(child):
        return
    keep = _get(m, a, "bMaintainOffset")
    parents = [(element(p.get("Item")), max(num(p.get("Weight"), 1.0), 0.0)) for p in _get(m, a, "Parents") or [] if isinstance(p, dict)]
    parents = [(n, w) for n, w in parents if n and m.h.has(n) and w >= 1e-4]
    total = sum(w for _, w in parents)
    if total <= 1e-4:
        return
    settings = _get(m, a, "AdvancedSettings") or {}
    gs = []
    for name, w in parents:
        g = m.h.get_global(name)
        if keep:
            g = compose(relative(m.h.get_global(child, True), m.h.get_global(name, True)), g)
            g = (g[0].normalized(), g[1])
        gs.append((g, w / total))
    mixed = mix_parents(gs, settings)
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
    initial, local = bool(_get(m, a, "bInitial")), int(num(_get(m, a, "Space"))) == 0
    _set(m, a, "Transform", transform_out(m.h.get_local(name, initial) if local else m.h.get_global(name, initial)))


def _write(m, name, value, local, weight):
    if weight < 1e-8 or name is None or not m.h.has(name):
        return
    if weight < 1.0 - 1e-8:
        value = _lerp(m.h.get_local(name) if local else m.h.get_global(name), value, weight)
    (m.h.set_local if local else m.h.set_global)(name, value)


def set_transform(m, a, ops, pc):
    if not _get(m, a, "bInitial"):
        _write(m, element(_get(m, a, "Item")), transform(_get(m, a, "Value")), int(num(_get(m, a, "Space"))) == 0,
               num(_get(m, a, "Weight")))


def quaternion_to_euler(m, a, ops, pc):
    """AnimationCore's euler: X, Y, Z turns (degrees) applied in the order's sequence (deform_rig._epic_quat's inverse)."""
    from .deform_rig import ORDERS
    order = _get(m, a, "RotationOrder", 0)
    order = int(num(order)) if not isinstance(order, str) else ORDERS.index(order.split("::")[-1]) if order.split("::")[-1] in ORDERS else 0
    e = epic_euler(quat(_get(m, a, "Value")), ORDERS[order] if 0 <= order < len(ORDERS) else "XYZ")
    _set(m, a, "Result", {"X": math.degrees(e[0]), "Y": math.degrees(e[1]), "Z": math.degrees(e[2])})


def _vector_unit(fn, names):
    def unit(m, a, ops, pc):
        _set(m, a, "Result", fn(*[_get(m, a, n) for n in names]))
    return unit


def _ints(fn):
    return _binary(lambda x, y: fn(int(num(x)), int(num(y))))


def _name(v):
    return "" if v in (None, "None") else str(v)


def item_exists(m, a, ops, pc):
    name = element(_get(m, a, "Item"))
    _set(m, a, "Exists", bool(name) and m.h.has(name))


def name_truncate(m, a, ops, pc):
    name, count = _name(_get(m, a, "Name")), max(int(num(_get(m, a, "Count"))), 0)
    if _get(m, a, "FromEnd"):
        keep, chopped = (name[:-count], name[-count:]) if count else (name, "")
    else:
        keep, chopped = name[count:], name[:count]
    _set(m, a, "Remainder", keep)
    _set(m, a, "Chopped", chopped)


def quat_slerp(m, a, ops, pc):
    q0, q1 = quat(_get(m, a, "A")), quat(_get(m, a, "B"))
    _set(m, a, "Result", quat_out(_lerp((q0, Vector()), (q1, Vector()), num(_get(m, a, "T")))[0]))


def make_relative(m, a, ops, pc):
    _set(m, a, "Local", transform_out(relative(transform(_get(m, a, "Global")), transform(_get(m, a, "Parent")))))


# --- more math

def to_euler_transform(m, a, ops, pc):
    """An FRotator (degrees) of the rotation, with its +-90 pitch cases; location and scale passed through."""
    t = _get(m, a, "Value") or {}
    q = quat(t.get("Rotation"))
    x, y, z, w = q.x, q.y, q.z, q.w
    st = z * x - w * y
    if st < -0.4999995:
        pitch, yaw, roll = -90.0, math.degrees(-2 * math.atan2(x, w)), 0.0
    elif st > 0.4999995:
        pitch, yaw, roll = 90.0, math.degrees(2 * math.atan2(x, w)), 0.0
    else:
        pitch = math.degrees(math.asin(2 * st))
        yaw = math.degrees(math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z)))
        roll = math.degrees(math.atan2(-2 * (w * x + y * z), 1 - 2 * (x * x + y * y)))
    yaw = yaw if -180.0 < yaw <= 180.0 else (yaw + 180.0) % 360.0 - 180.0
    _set(m, a, "Result", {"Location": t.get("Translation") or {"X": 0.0, "Y": 0.0, "Z": 0.0},
                          "Rotation": {"Pitch": pitch, "Yaw": yaw, "Roll": roll}, "Scale": t.get("Scale3D") or {"X": 1.0, "Y": 1.0, "Z": 1.0}})


def quat_from_euler(m, a, ops, pc):
    from .deform_rig import ORDERS, _epic_quat
    order = _get(m, a, "RotationOrder")
    order = int(num(order)) if not isinstance(order, str) else ORDERS.index(order.split("::")[-1]) if order.split("::")[-1] in ORDERS else 0
    _set(m, a, "Result", quat_out(_epic_quat(list(vec(_get(m, a, "Euler"))), order).to_quaternion()))


def quat_from_rotator(m, a, ops, pc):
    """Yaw about Z after pitch and roll, the two negated (the game's handedness)."""
    r = _get(m, a, "Value") or {}
    pitch, yaw, roll = (math.radians(num(r.get(k)) % 360.0) for k in ("Pitch", "Yaw", "Roll"))
    _set(m, a, "Result", quat_out(Quaternion((0, 0, 1), yaw) @ Quaternion((0, 1, 0), -pitch) @ Quaternion((1, 0, 0), -roll)))


def quat_scale(m, a, ops, pc):
    q = quat(_get(m, a, "Value"))
    axis = Vector((q.x, q.y, q.z))
    axis = axis.normalized() if axis.length_squared >= 1e-8 else Vector((1.0, 0.0, 0.0))
    angle = 2.0 * math.acos(max(-1.0, min(1.0, q.w)))      # w's sign kept: a negative one turns the long way
    _set(m, a, "Result", quat_out(Quaternion(axis, angle * num(_get(m, a, "Factor")))))


def _pow(x, y):
    try:
        return math.pow(num(x), num(y))
    except (ValueError, OverflowError, ZeroDivisionError):
        return float("nan") if num(x) < 0 else float("inf")


# --- arrays (dispatches: positional operands)

def _array(m, o):
    v = m.get(o)
    return v if isinstance(v, list) else []


def array_iterator(m, a, ops, pc):
    """One pass of a for-each: the next element and its index, or Completed past the end."""
    array, element_, index, count, ratio, block = ops[:6]
    items = _array(m, array)
    if m.get(block) in (None, "", "None", 0):
        m.set(block, "ExecuteContext")
        m.set(count, len(items))
        m.set(index, -1)
    i = int(num(m.get(index))) + 1
    m.set(index, i)
    m.set(ratio, i / (len(items) - 1) if len(items) > 1 else 0.0)
    if i >= len(items):
        m.set(block, "Completed")
    else:
        m.set(element_, copy.deepcopy(items[i]))


def _index(items, i):
    i = i + len(items) if i < 0 else i
    return i if 0 <= i < len(items) else None


def array_get(m, a, ops, pc):
    items = _array(m, ops[0])
    i = _index(items, int(num(m.get(ops[1]))))
    if i is not None:
        m.set(ops[2], copy.deepcopy(items[i]))


def array_set(m, a, ops, pc):
    items = list(_array(m, ops[0]))
    i = _index(items, int(num(m.get(ops[1]))))
    if i is not None:
        items[i] = copy.deepcopy(m.get(ops[2]))
        m.set(ops[0], items)


def array_set_num(m, a, ops, pc):
    items, n = list(_array(m, ops[0])), int(num(m.get(ops[1])))
    if n >= 0:
        m.set(ops[0], items[:n] + [None] * max(0, n - len(items)))


def array_add(m, a, ops, pc):
    items = list(_array(m, ops[0])) + [copy.deepcopy(m.get(ops[1]))]
    m.set(ops[0], items)
    if len(ops) > 2:
        m.set(ops[2], len(items) - 1)


def array_init(m, a, ops, pc):
    m.set(ops[0], [copy.deepcopy(m.get(ops[2])) for _ in range(max(0, int(num(m.get(ops[1])))))])


# --- items and names

def get_transform_array(m, a, ops, pc):
    initial, local = bool(_get(m, a, "bInitial")), int(num(_get(m, a, "Space"))) == 0
    out = []
    for item in _get(m, a, "Items") or []:
        name = element(item)
        t = (m.h.get_local(name, initial) if local else m.h.get_global(name, initial)) if name and m.h.has(name) else (Quaternion(), Vector())
        out.append(transform_out(t))
    _set(m, a, "Transforms", out)


def transforms_to_srt(m, a, ops, pc):
    items = _get(m, a, "Transforms") or []
    if items:       # an empty input leaves the outputs as they were
        for key, part in (("Translations", "Translation"), ("Rotations", "Rotation"), ("Scales", "Scale3D")):
            _set(m, a, key, [(t or {}).get(part) for t in items])


def get_parent(m, a, ops, pc):
    name = element(_get(m, a, "Child"))
    _set(m, a, "Parent", {"Type": "Bone", "Name": (m.h.parent.get(name) if name else None) or "None"})


def name_replace(m, a, ops, pc):
    import re
    name, old, new = _name(_get(m, a, "Name")), _name(_get(m, a, "Old")), _name(_get(m, a, "New"))
    _set(m, a, "Result", re.sub(re.escape(old), lambda _: new, name, flags=re.IGNORECASE) if name and old else name)


def f32(x):
    return struct.unpack("f", struct.pack("f", float(x)))[0]


def _mod(x, y):
    x, y = num(x), num(y)
    return 0.0 if abs(y) <= 1e-8 or y < 0.0 else math.fmod(x, y)


def kalman(m, a, ops, pc):
    """A moving average of the last BufferSize values (no time involved, whatever the name)."""
    v, size = num(_get(m, a, "Value")), int(num(_get(m, a, "BufferSize")))
    if size <= 0:
        _set(m, a, "Result", v)
        return
    s = m.state.get(pc)
    if s is None or len(s["buffer"]) != size:
        s = m.state[pc] = {"buffer": [None] * size, "last": -1}
    s["last"] = 0 if s["last"] >= size - 1 else s["last"] + 1
    s["buffer"][s["last"]] = v
    filled = s["buffer"][:s["buffer"].index(None)] if None in s["buffer"] else s["buffer"]
    _set(m, a, "Result", sum(filled) / len(filled))


def _clampf(v, lo, hi):
    return max(v, lo) if hi <= lo or hi < 1e-8 else min(max(v, lo), hi)


def _rescaled(p, lo, hi):
    return p * (_clampf(p.length, lo, hi) / p.length) if p.length > 1e-8 else p


def clamp_spatially(m, a, ops, pc):
    """Only the translation, clamped in Space: to a slab along an axis, a cylinder about it, a sphere or a capsule."""
    value = _get(m, a, "Value") or {}
    rot, pos = transform(value)
    s_rot, s_pos = transform(_get(m, a, "Space"))
    axis = {1: 0, 2: 1}.get(int(num(_get(m, a, "Axis"))), 2)
    unit = Vector([1.0 if i == axis else 0.0 for i in range(3)])
    lo, hi, kind = num(_get(m, a, "Minimum")), num(_get(m, a, "Maximum")), int(num(_get(m, a, "Type")))
    p, centre = s_rot.conjugated() @ (pos - s_pos), Vector()

    def cylinder(p, hi):
        q = p - unit * p[axis]
        return p if q.length <= 1e-8 else unit * p[axis] + _rescaled(q, lo, hi)
    if kind == 0:
        p[axis] = _clampf(p[axis], lo, hi)
    elif kind == 1:
        p = cylinder(p, hi)
    elif kind == 2:
        p = _rescaled(p, lo, hi)
    elif hi < 2 * lo:
        p = _rescaled(p, lo, 0.0)
    else:
        h, d = hi / 2 - lo, unit.dot(p)
        if abs(d) < h:
            p = cylinder(p, 0.0)
        else:       # the end cap: still clamped up to the full length, as the engine does
            centre = unit * math.copysign(h, d)
            p = _rescaled(p - centre, lo, hi)
    _set(m, a, "Result", dict(transform_out((rot, s_rot @ (p + centre) + s_pos)), Scale3D=value.get("Scale3D") or {"X": 1.0, "Y": 1.0, "Z": 1.0}))


def set_transform_array(m, a, ops, pc):
    items, values = _get(m, a, "Items") or [], _get(m, a, "Transforms") or []
    if len(items) != len(values) or _get(m, a, "bInitial"):
        return
    local, weight = int(num(_get(m, a, "Space"))) == 0, num(_get(m, a, "Weight"))
    for item, value in zip(items, values):
        _write(m, element(item), transform(value), local, weight)


ELEMENT_BITS = {"Bone": 1, "Null": 2, "Control": 4, "Curve": 8, "Physics": 16, "Reference": 32, "Connector": 64, "Socket": 128}


def set_pose(m, a, ops, pc):
    """A stored pose's locals or globals written back, in the pose's order, eased in by the weight (curves aside)."""
    w = min(max(num(_get(m, a, "Weight")), 0.0), 1.0)
    if w < 1e-8:
        return
    mask, local = int(num(_get(m, a, "ElementType"))), int(num(_get(m, a, "Space"))) == 0
    wanted = {element(i) for i in _get(m, a, "ItemsToSet") or []}
    for e in (_get(m, a, "Pose") or {}).get("Elements") or []:
        key = e.get("Key") or (e.get("Index") or {}).get("Key") or {}
        kind, name = str(key.get("Type", "Bone")).split("::")[-1], element(key)
        if not ELEMENT_BITS.get(kind, 0) & mask or (wanted and name not in wanted) or name is None or not m.h.has(name):
            continue
        _write(m, name, transform(e.get("LocalTransform" if local else "GlobalTransform")), local, w)


IGNORED = ("DebugLineNoSpace", "DebugLine", "DebugTransformMutableNoSpace", "DebugTransformMutableItemSpace", "DebugPoint",
           "SetCurveValue", "DISPATCH_RigDispatch_GetAnimAttribute", "DISPATCH_RigDispatch_SetAnimAttribute",
           "FortDeformRig", "DrawControlRigSpline", "HierarchySetShapeSettings", "SetShapeTransform")    # the deform drivers do the last


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
    "MathBoolOr": _binary(lambda x, y: bool(x) or bool(y)), "MathBoolAnd": _binary(lambda x, y: bool(x) and bool(y)),
    "MathBoolNot": _unary(lambda x: not x), "MathDoubleMax": _binary(lambda x, y: max(num(x), num(y))),
    "MathDoubleMin": _binary(lambda x, y: min(num(x), num(y))), "MathDoubleLess": _binary(lambda x, y: num(x) < num(y)),
    "MathDoubleGreater": _binary(lambda x, y: num(x) > num(y)), "MathDoubleNegate": _unary(lambda x: -num(x)),
    "MathIntLess": _ints(lambda x, y: x < y), "MathIntGreater": _ints(lambda x, y: x > y), "MathIntSub": _ints(lambda x, y: x - y),
    "MathIntAdd": _ints(lambda x, y: x + y), "MathIntToFloat": _unary(lambda x: float(int(num(x)))),
    "MathIntToDouble": _unary(lambda x: float(int(num(x)))), "MathFloatAdd": _binary(lambda x, y: num(x) + num(y)),
    "MathFloatSub": _binary(lambda x, y: num(x) - num(y)),
    "MathVectorMake": _vector_unit(lambda x, y, z: vec_out(Vector((num(x), num(y), num(z)))), ("X", "Y", "Z")),
    "MathVectorSub": _binary(lambda x, y: vec_out(vec(x) - vec(y))), "MathVectorAdd": _binary(lambda x, y: vec_out(vec(x) + vec(y))),
    "MathVectorCross": _binary(lambda x, y: vec_out(vec(x).cross(vec(y)))), "MathVectorLength": _unary(lambda x: vec(x).length),
    "MathVectorScale": _vector_unit(lambda v, f: vec_out(vec(v) * num(f)), ("Value", "Factor")),
    "MathVectorUnit": _unary(lambda x: vec_out(vec(x).normalized() if vec(x).length > 1e-8 else Vector())),
    "MathIntClamp": _vector_unit(lambda v, lo, hi: min(max(int(num(v)), int(num(lo))), int(num(hi))), ("Value", "Minimum", "Maximum")),
    "MathDoubleLerp": _vector_unit(lambda x, y, t: num(x) + (num(y) - num(x)) * num(t), ("A", "B", "T")),
    "MathQuaternionInverse": _unary(lambda q: quat_out(quat(q).conjugated())), "MathQuaternionSlerp": quat_slerp,
    "MathTransformMul": _binary(lambda x, y: transform_out(compose(transform(x), transform(y)))),
    "MathTransformMakeRelative": make_relative,
    "ItemExists": item_exists, "NameTruncate": name_truncate,
    "NameConcat": _binary(lambda x, y: _name(x) + _name(y)),
    "NameReplace": name_replace, "MathTransformToEulerTransform": to_euler_transform, "MathQuaternionFromEuler": quat_from_euler,
    "MathQuaternionFromRotatorV2": quat_from_rotator, "MathQuaternionScaleV2": quat_scale,
    "MathDoublePow": _binary(_pow), "MathDoubleDiv": _binary(lambda x, y: 0.0 if abs(num(y)) <= 1e-8 else num(x) / num(y)),
    "MathFloatDiv": _binary(lambda x, y: 0.0 if abs(num(y)) <= 1e-8 else num(x) / num(y)),
    "DISPATCH_RigVMDispatch_ArrayIterator": array_iterator, "DISPATCH_RigVMDispatch_ArrayGetAtIndex": array_get,
    "DISPATCH_RigVMDispatch_ArraySetAtIndex": array_set, "DISPATCH_RigVMDispatch_ArraySetNum": array_set_num,
    "DISPATCH_RigVMDispatch_ArrayAdd": array_add, "DISPATCH_RigVMDispatch_ArrayInit": array_init,
    "DISPATCH_RigVMDispatch_ArrayGetNum": lambda m, a, ops, pc: m.set(ops[1], len(_array(m, ops[0]))),
    "DISPATCH_RigVMDispatch_ArrayReset": lambda m, a, ops, pc: m.set(ops[0], []),
    "DISPATCH_RigVMDispatch_ArrayClone": lambda m, a, ops, pc: m.set(ops[1], copy.deepcopy(_array(m, ops[0]))),
    "GetTransformItemArray": get_transform_array, "MathTransformArrayToSRT": transforms_to_srt, "HierarchyGetParent": get_parent,
    "MathTransformTransformVector": _vector_unit(lambda t, v: vec_out(compose((Quaternion(), vec(v)), transform(t))[1]), ("Transform", "Location")),
    "DISPATCH_RigVMDispatch_CoreEquals": lambda m, a, ops, pc: m.set(ops[-1], m.get(ops[0]) == m.get(ops[1])),
    "MathDoubleArraySum": lambda m, a, ops, pc: _set(m, a, "Sum", sum(num(x) for x in (_get(m, a, "Array") or []))),
    "ItemArray": nothing,
    "SequenceExecution": nothing, "MathDoubleMod": _binary(_mod), "MathDoubleLessEqual": _binary(lambda x, y: num(x) <= num(y)),
    "MathFloatMax": _binary(lambda x, y: f32(max(num(x), num(y)))),
    "MathVectorDistance": _binary(lambda x, y: f32((vec(x) - vec(y)).length)), "MathVectorDot": _binary(lambda x, y: f32(vec(x).dot(vec(y)))),
    "MathTransformMake": lambda m, a, ops, pc: _set(m, a, "Result", {"Rotation": _get(m, a, "Rotation") or quat_out(Quaternion()),
                                                                       "Translation": _get(m, a, "Translation") or vec_out(Vector()),
                                                                       "Scale3D": _get(m, a, "Scale") or {"X": 0.0, "Y": 0.0, "Z": 0.0}}),
    "KalmanFloat": kalman, "MathTransformClampSpatially": clamp_spatially, "SetTransformItemArray": set_transform_array,
    "HierarchySetPoseItemArray": set_pose,
}
UNITS.update({name: nothing for name in IGNORED})
