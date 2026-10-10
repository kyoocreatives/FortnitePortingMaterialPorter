"""The constraint units characters' rigs run besides ParentConstraint: position, rotation and aim with a local-space
offset (kept from both rest poses), the aim math they share, and an offset applied to an item. Same interface as
rig_units."""
import math

from mathutils import Euler, Quaternion, Vector

from .dynamics_solver import compose, relative
from .rig_units import _get, _lerp, element, epic_euler, num, transform, transform_out, vec


def _parents(m, a):
    weight = num(_get(m, a, "Weight"), 1.0)
    child = element(_get(m, a, "Child"))
    if weight < 1e-4 or child is None or not m.h.has(child):
        return None
    parents = [(element(p.get("Item")), max(num(p.get("Weight"), 1.0), 0.0)) for p in _get(m, a, "Parents") or [] if isinstance(p, dict)]
    parents = [(n, w) for n, w in parents if n and m.h.has(n) and w >= 1e-4]
    total = sum(w for _, w in parents)
    return (child, weight, [(n, w / total) for n, w in parents]) if total > 1e-4 else None


def _in_parent(m, child, point, initial=False):
    p = m.h.parent.get(child)
    return relative((Quaternion(), point), m.h.get_global(p, initial))[1] if p else point


def _finish(m, child, current, result, weight):
    if weight < 1.0 - 1e-4:
        result = _lerp(current, result, weight)
    m.h.set_local(child, result)


def _filter_order(settings):
    from .deform_rig import ORDERS
    order = (settings or {}).get("RotationOrderForFilter", 1)
    return ORDERS[int(num(order))] if not isinstance(order, str) else order.split("::")[-1]


def _filtered(rot, current, rf, order, closest=False):
    """Per-axis rotation filter through Euler angles; closest: start from whichever equal solution sits nearer the
    current angles (the aim constraint's way)."""
    if all(rf.get(k, True) for k in ("bX", "bY", "bZ")):
        return rot
    e_new, e_now = epic_euler(rot, order), epic_euler(current, order)
    if closest:
        first, mid, last = ("XYZ".index(c) for c in order)
        other = list(e_new)
        other[first], other[mid], other[last] = e_new[first] + math.pi, math.pi - e_new[mid], e_new[last] + math.pi
        other = [math.atan2(math.sin(x), math.cos(x)) for x in other]
        if sum(abs(x - y) for x, y in zip(other, e_now)) <= sum(abs(x - y) for x, y in zip(e_new, e_now)):
            e_new = other
    return Euler([e_new[i] if rf.get(k, True) else e_now[i] for i, k in enumerate(("bX", "bY", "bZ"))], order).to_quaternion()


def position_constraint(m, a, ops, pc):
    got = _parents(m, a)
    if got is None:
        return
    child, weight, parents = got
    current = m.h.get_local(child)
    offset = Vector()
    if _get(m, a, "bMaintainOffset", True):
        start = sum((m.h.get_global(n, True)[1] * w for n, w in parents), Vector())
        offset = m.h.get_local(child, True)[1] - _in_parent(m, child, start, True)
    p = _in_parent(m, child, sum((m.h.get_global(n)[1] * w for n, w in parents), Vector())) + offset
    f = _get(m, a, "Filter") or {}
    p = Vector([p[i] if f.get(k, True) else current[1][i] for i, k in enumerate(("bX", "bY", "bZ"))])
    _finish(m, child, current, (current[0], p), weight)


def _mix(quats, shortest):
    q_mix = Quaternion((0.0, 0.0, 0.0, 0.0))
    for q, n in quats:
        if shortest:
            q_mix = q.copy() if q_mix.magnitude == 0.0 else _lerp((q_mix, Vector()), (q, Vector()), n)[0]
        else:
            add = Quaternion([c * n for c in q])
            q_mix = Quaternion([x - y for x, y in zip(q_mix, add)]) if add.dot(q_mix) < 0.0 else Quaternion([x + y for x, y in zip(q_mix, add)])
    return q_mix.normalized() if q_mix.magnitude > 1e-8 else Quaternion()


def _local_rotation(m, child, rot, initial=False):
    parent = m.h.parent.get(child)
    return (m.h.get_global(parent, initial)[0].conjugated() @ rot) if parent else rot


def rotation_constraint(m, a, ops, pc):
    got = _parents(m, a)
    if got is None:
        return
    child, weight, parents = got
    settings = _get(m, a, "AdvancedSettings") or {}
    shortest = str(settings.get("InterpolationType", 0)).split("::")[-1] in ("1", "Shortest")
    current = m.h.get_local(child)
    r = _local_rotation(m, child, _mix([(m.h.get_global(n)[0], w) for n, w in parents], shortest))
    if _get(m, a, "bMaintainOffset", True):
        start = _local_rotation(m, child, _mix([(m.h.get_global(n, True)[0], w) for n, w in parents], shortest), True)
        r = r @ (start.conjugated() @ m.h.get_local(child, True)[0])     # applied first, in local space
    _finish(m, child, current, (_filtered(r, current[0], _get(m, a, "Filter") or {}, _filter_order(settings)), current[1]), weight)


def _between(a_, b_):
    a_ = a_.normalized() if a_.length > 1e-4 else a_
    b_ = b_.normalized() if b_.length > 1e-4 else b_
    w = 1.0 + a_.dot(b_)
    if w >= 1e-8:
        c = a_.cross(b_)
        return Quaternion((w, c.x, c.y, c.z)).normalized()
    n = Vector((0.0, 1.0, 0.0)) if abs(a_.x) > abs(a_.y) else Vector((1.0, 0.0, 0.0))
    return Quaternion(a_.cross(a_.cross(n)).normalized(), math.pi)


def _small(v):
    return max(abs(c) for c in v) <= 1e-4


def aim(rot, pos, primary, secondary, weight):
    """AimBoneMath on (rotation, position): the primary axis turned onto its target in world space, then the secondary
    onto its own, projected off the primary. Targets: (axis, point or direction, 'Location'/'Direction', weight) in
    global space."""
    if weight <= 1e-8 or (primary[3] <= 1e-8 and secondary[3] <= 1e-8):
        return rot
    pa = None
    for k, (axis, target, kind, w) in enumerate((primary, secondary)):
        if w <= 1e-8:
            continue
        t = target - pos if kind == "Location" else target.copy()
        if k == 1 and not _small(primary[0]):
            pa = (rot @ primary[0]).normalized()
            t = t - pa * t.dot(pa)
        if _small(t) or _small(axis):
            continue
        t = t.normalized()
        ax = (rot @ axis).normalized()
        if w * weight < 1.0 - 1e-8:
            t = ax.lerp(t, w * weight)
            t = t.normalized() if t.length > 1e-8 else t
        q = Quaternion(pa, math.pi) if k == 1 and pa is not None and ax.dot(t) + 1.0 < 1e-8 else _between(ax, t)
        rot = (q @ rot).normalized()
    return rot


def _kind(v, default):
    return "Direction" if str(v if v is not None else default).split("::")[-1] in ("0", "Direction") else "Location"


def _target(m, value, default_axis):
    v = value or {}
    kind = _kind(v.get("Kind"), "Location")
    target = vec(v.get("Target"), (1.0, 0.0, 0.0))
    space = element(v.get("Space"))
    if space and m.h.has(space):
        g = m.h.get_global(space)
        target = g[0] @ target if kind == "Direction" else g[0] @ target + g[1]
    return vec(v.get("Axis"), default_axis), target, kind, num(v.get("Weight"), 1.0)


def aim_bone_math(m, a, ops, pc):
    t = transform(_get(m, a, "InputTransform"))
    rot = aim(t[0], t[1], _target(m, _get(m, a, "Primary"), (1.0, 0.0, 0.0)), _target(m, _get(m, a, "Secondary"), (0.0, 0.0, 1.0)),
              num(_get(m, a, "Weight"), 1.0))
    m.set(a.get("Result"), transform_out((rot, t[1])))


def aim_constraint(m, a, ops, pc):
    got = _parents(m, a)
    if got is None:
        return
    child, weight, parents = got
    aim_axis, up_axis = vec(_get(m, a, "AimAxis"), (1.0, 0.0, 0.0)), vec(_get(m, a, "UpAxis"), (0.0, 0.0, 1.0))
    up = _get(m, a, "WorldUp") or {}
    up_kind, up_target, up_space = _kind(up.get("Kind"), "Direction"), vec(up.get("Target"), (0.0, 0.0, 1.0)), element(up.get("Space"))
    current = m.h.get_local(child)

    def aimed(initial):
        point = sum((m.h.get_global(n, initial)[1] * w for n, w in parents), Vector())
        g = m.h.get_global(child, initial)
        target = up_target
        if up_space and m.h.has(up_space):
            s_ = m.h.get_global(up_space, initial)
            # the rest pose maps the up target as a point whatever its kind (as the engine does)
            target = s_[0] @ up_target + s_[1] if initial or up_kind == "Location" else s_[0] @ up_target
        rot = aim(g[0], g[1], (aim_axis, point, "Location", 1.0), (up_axis, target, up_kind, 1.0), weight)
        return _local_rotation(m, child, rot, initial)
    r = aimed(False)
    if _get(m, a, "bMaintainOffset", True):
        r = r @ (aimed(True).conjugated() @ m.h.get_local(child, True)[0])
    rot = _filtered(r, current[0], _get(m, a, "Filter") or {}, _filter_order(_get(m, a, "AdvancedSettings")), closest=True)
    _finish(m, child, current, (rot, current[1]), weight)


def offset_transform(m, a, ops, pc):
    """The offset applied in the item's own space, eased in by the weight."""
    name, weight = element(_get(m, a, "Item")), num(_get(m, a, "Weight"), 1.0)
    if weight < 1e-8 or name is None or not m.h.has(name):
        return
    now = m.h.get_global(name)
    result = compose(transform(_get(m, a, "OffsetTransform")), now)
    m.h.set_global(name, _lerp(now, result, weight) if weight < 1.0 - 1e-8 else result)


UNITS = {"PositionConstraintLocalSpaceOffset": position_constraint, "RotationConstraintLocalSpaceOffset": rotation_constraint,
         "AimConstraintLocalSpaceOffset": aim_constraint, "AimBoneMath": aim_bone_math, "OffsetTransformForItem": offset_transform}
