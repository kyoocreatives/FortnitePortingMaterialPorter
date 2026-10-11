"""Units that move with time: a quaternion spring, a transform delayed through a buffer, and the harmonics that wave
an item or a chain (with a pendulum) - each as the engine computes it (UE 5.8 source), its state kept per
instruction between runs. Same interface as rig_units."""
import math

from mathutils import Quaternion, Vector

from .dynamics_solver import compose
from .rig_units import _curve, _get, _lerp, _set, element, num, quat, quat_out, transform, transform_out, vec, vec_out

# FMath::PerlinNoise1D's permutation (its 512 entries repeat these 256) and gradient scales
PERM = [
        63, 9, 212, 205, 31, 128, 72, 59, 137, 203, 195, 170, 181, 115, 165, 40, 116, 139, 175, 225, 132, 99, 222, 2, 41, 15, 197, 93, 169, 90, 228, 43,
        221, 38, 206, 204, 73, 17, 97, 10, 96, 47, 32, 138, 136, 30, 219, 78, 224, 13, 193, 88, 134, 211, 7, 112, 176, 19, 106, 83, 75, 217, 85, 0,
        98, 140, 229, 80, 118, 151, 117, 251, 103, 242, 81, 238, 172, 82, 110, 4, 227, 77, 243, 46, 12, 189, 34, 188, 200, 161, 68, 76, 171, 194, 57, 48,
        247, 233, 51, 105, 5, 23, 42, 50, 216, 45, 239, 148, 249, 84, 70, 125, 108, 241, 62, 66, 64, 240, 173, 185, 250, 49, 6, 37, 26, 21, 244, 60,
        223, 255, 16, 145, 27, 109, 58, 102, 142, 253, 120, 149, 160, 124, 156, 79, 186, 135, 127, 14, 121, 22, 65, 54, 153, 91, 213, 174, 24, 252, 131, 192,
        190, 202, 208, 35, 94, 231, 56, 95, 183, 163, 111, 147, 25, 67, 36, 92, 236, 71, 166, 1, 187, 100, 130, 143, 237, 178, 158, 104, 184, 159, 177, 52,
        214, 230, 119, 87, 114, 201, 179, 198, 3, 248, 182, 39, 11, 152, 196, 113, 20, 232, 69, 141, 207, 234, 53, 86, 180, 226, 74, 150, 218, 29, 133, 8,
        44, 123, 28, 146, 89, 101, 154, 220, 126, 155, 122, 210, 168, 254, 162, 129, 33, 18, 209, 61, 191, 199, 157, 245, 55, 164, 167, 215, 246, 144, 107, 235]
GRAD1 = (-8 / 8, -7 / 8, -6 / 8, -5 / 8, -4 / 8, -3 / 8, -2 / 8, -1 / 8, 1 / 8, 2 / 8, 3 / 8, 4 / 8, 5 / 8, 6 / 8, 7 / 8, 8 / 8)
MASS = 1.0          # RigUnitSpringInterpConstants::Mass


def perlin(x):
    xf = math.floor(x)
    xi = int(xf) & 255
    x -= xf
    u = x * x * x * (x * (x * 6.0 - 15.0) + 10.0)
    ga, gb = GRAD1[PERM[xi] & 15] * x, GRAD1[PERM[(xi + 1) & 255] & 15] * (x - 1.0)
    return 2.0 * (ga + (gb - ga) * u)


def ease(v, kind):
    """FRigVMMathLibrary::EaseFloat for Linear, the only easing Fortnite's rigs use (survey 2026-10-11)."""
    return v


def _mul(a, b):
    return Vector((a.x * b.x, a.y * b.y, a.z * b.z))


def _safe(v):
    return v.normalized() if v.length_squared > 1e-8 else Vector()


def _slerp(q0, q1, t):
    return q0.slerp(q1 if q0.dot(q1) >= 0.0 else -q1, t).normalized()


def quat_between(a, b):
    """FRigVMMathLibrary::FindQuatBetweenVectors: the turn taking a onto b (a half turn when they're opposite)."""
    a, b = _safe(a), _safe(b)
    w = 1.0 + a.dot(b)
    if w < 1e-8:            # opposite: a half turn about an axis across a (its length unnormalised, as UE builds it)
        c = -a.cross(b)
        r = Quaternion((2.0 - w, c.x, c.y, c.z)).normalized()
        normal = Vector((0.0, 1.0, 0.0)) if abs(a.x) > abs(a.y) else Vector((1.0, 0.0, 0.0))
        tau = a.cross(a.cross(normal))
        r = r @ Quaternion((math.cos(math.pi / 2), tau.x, tau.y, tau.z))
    else:
        c = a.cross(b)
        r = Quaternion((w, c.x, c.y, c.z))
    return r.normalized() if r.magnitude > 1e-12 else Quaternion()


def _inv_exp(x):
    return 1.0 / (1.0 + 1.00746054 * x + 0.45053901 * x * x + 0.25724632 * x * x * x)


def spring_damper(x, v, target, target_rate, dt, frequency, damping):
    """FMath::SpringDamper on a vector or a quaternion's four numbers: the value and its rate after dt."""
    if dt <= 0.0:
        return x, v
    w = frequency * 2.0 * math.pi
    if w < 1e-8:
        return x + v * dt, v
    if damping < 1e-8:
        err, b = x - target, v * (1.0 / w)
        s, c = math.sin(w * dt), math.cos(w * dt)
        return target + err * c + b * s, v * c - err * (w * s)
    adjusted = target + target_rate * (damping * 2.0 / w)
    err = x - adjusted
    if damping > 1.0:
        wd = w * math.sqrt(damping * damping - 1.0)
        c2 = (v + err * (w * damping - wd)) * (-1.0 / (2.0 * wd))
        c1 = err - c2
        a1, a2 = wd - damping * w, -(wd + damping * w)
        e1, e2 = _inv_exp(-a1 * dt), _inv_exp(-a2 * dt)
        return adjusted + c1 * e1 + c2 * e2, c1 * (e1 * a1) + c2 * (e2 * a2)
    if damping < 1.0:
        wd = w * math.sqrt(1.0 - damping * damping)
        b = (v + err * (damping * w)) * (1.0 / wd)
        s, c = math.sin(wd * dt), math.cos(wd * dt)
        e = _inv_exp(damping * w * dt)
        value = (err * c + b * s) * e
        rate = value * (-damping * w) + (b * (wd * c) - err * (wd * s)) * e
        return value + adjusted, rate
    c2 = v + err * w
    e = _inv_exp(w * dt)
    return adjusted + (err + c2 * dt) * e, (c2 - err * w - c2 * (w * dt)) * e


def spring_interp_quat(m, a, ops, pc):
    """SpringInterpQuaternionV2: Strength is a frequency (Hz); the spring snaps to its first target unless it follows
    Current; Torque adds to its angular velocity."""
    s = m.state.setdefault(pc, {"sim": Quaternion(), "ang": Vector(), "prev": None})
    stiffness = (num(_get(m, a, "Strength")) * 2.0 * math.pi) ** 2
    s["ang"] = s["ang"] + vec(_get(m, a, "Torque")) * (m.dt / MASS)
    use_current = bool(_get(m, a, "bUseCurrentInput"))
    current, target = quat(_get(m, a, "Current")) if use_current else s["sim"].copy(), quat(_get(m, a, "Target"))
    if target.dot(current) < 0.0:
        target = -target
    velocity = (current @ Quaternion((0.0, s["ang"].x, s["ang"].y, s["ang"].z))) * 0.5
    if (not use_current or _get(m, a, "bInitializeFromTarget")) and s["prev"] is None:
        current = target.copy()
    if m.dt > 1e-8:
        rate = (target - s["prev"]) * (num(_get(m, a, "TargetVelocityAmount")) / m.dt) if s["prev"] is not None else target * 0.0
        current, velocity = spring_damper(current, velocity, target, rate, m.dt, math.sqrt(stiffness / MASS) / (2.0 * math.pi),
                                          num(_get(m, a, "CriticalDamping")))
        s["prev"] = target.copy()
    current = current.normalized()
    vq = current.conjugated() @ (velocity * 2.0)
    s["sim"], s["ang"] = current, Vector((vq.x, vq.y, vq.z))
    for name, value in (("Result", quat_out(current)), ("SimulatedResult", quat_out(current)), ("AngularVelocity", vec_out(s["ang"]))):
        _set(m, a, name, value)


def time_offset_transform(m, a, ops, pc):
    """The value SecondsAgo, from a buffer of past values spread over TimeRange (UE's buffer, quirks kept)."""
    value = transform(_get(m, a, "Value"))
    s = m.state.setdefault(pc, {"buf": [], "dts": [], "last": 0, "upper": 0})
    size, time_range = int(num(_get(m, a, "BufferSize"))), num(_get(m, a, "TimeRange"))
    if size <= 0 or time_range <= 0.0:
        _set(m, a, "Result", transform_out(value))
        return
    size = min(max(size, 2), 512)
    s["buf"] += [value] * (size - len(s["buf"]))
    s["dts"] += [0.0] * (size - len(s["dts"]))
    ago, upper, result = num(_get(m, a, "SecondsAgo")), s["upper"], value
    if upper:
        a_t, b_t, t, i = value, value, 0.0, s["last"]
        if ago < s["dts"][i] or upper == 1:
            b_t, t = s["buf"][i], ago / s["dts"][i]
        else:
            acc = 0.0
            for _ in range(upper):
                b_t = s["buf"][i]
                if acc >= ago:
                    t = 1.0 - (acc - ago) / s["dts"][i]
                    break
                acc += s["dts"][i]
                a_t = b_t
                i = (i - 1 + upper) % upper
        result = _lerp(a_t, b_t, t)
    if m.dt > 1e-8:
        if upper == 0 or s["dts"][s["last"]] > time_range / (size - 1) - m.dt * 0.5:
            s["last"] = s["upper"] if s["upper"] < size else (s["last"] + 1) % s["upper"]
            s["upper"] = min(s["upper"] + 1, size)
            s["buf"][s["last"]], s["dts"][s["last"]] = value, m.dt
        else:
            s["dts"][s["last"]] += m.dt
    _set(m, a, "Result", transform_out(result))


def _order(v):
    from .deform_rig import ORDERS
    if isinstance(v, str):
        return ORDERS.index(v.split("::")[-1]) if v.split("::")[-1] in ORDERS else 0
    return int(num(v))


def item_harmonics(m, a, ops, pc):
    """Each target turned (in its own space) by a sine wave per axis, eased by its ratio; the wave moves by WaveSpeed."""
    from .deform_rig import _epic_quat
    targets = _get(m, a, "Targets") or []
    s = m.state.get(pc)
    if s is None or s["n"] != len(targets):
        s = m.state[pc] = {"n": len(targets), "time": Vector()}
    lo, hi = num(_get(m, a, "WaveMinimum")), num(_get(m, a, "WaveMaximum"))
    freq, amp, off = vec(_get(m, a, "WaveFrequency")), vec(_get(m, a, "WaveAmplitude")), vec(_get(m, a, "WaveOffset"))
    noise_size, order = vec(_get(m, a, "WaveNoise")), _order(_get(m, a, "RotationOrder"))
    for target in targets:
        name = element(target.get("Item"))
        if name is None or not m.h.has(name):
            continue
        ratio = num(target.get("Ratio"))
        e = lo + (hi - lo) * ease(min(max(ratio, 0.0), 1.0), _get(m, a, "WaveEase"))
        u = s["time"] + freq * ratio
        u = u + _mul(Vector((perlin(u.x + 132.4), perlin(u.y + 9.2), perlin(u.z + 217.9))), noise_size) * 2.0
        angles = _mul(Vector((math.sin(u.x + off.x), math.sin(u.y + off.y), math.sin(u.z + off.z))), amp) * e
        rot, pos = m.h.get_global(name)
        m.h.set_global(name, ((rot @ _epic_quat(list(angles), order).to_quaternion()).normalized(), pos))
    s["time"] = s["time"] + vec(_get(m, a, "WaveSpeed")) * m.dt


def _chain(m, root):
    items = [root]
    while True:
        children = [c for c, p in m.h.parent.items() if p == items[-1]]
        if not children:
            return items
        items.append(children[0])


def chain_harmonics(m, a, ops, pc):
    """ChainHarmonicsPerItem: down the chain from ChainRoot, each item reaches, waves and swings as a pendulum (its
    tip kept at its length), eased by its place in the chain; each change carries to the items after it."""
    s = m.state.get(pc)
    if s is None:
        root = element(_get(m, a, "ChainRoot"))
        items = _chain(m, root) if root and m.h.has(root) else []
        if len(items) < 2:
            return
        n = len(items)
        local_tip = [m.h.get_local(i)[1].copy() for i in items]
        tip = local_tip[1:] + [local_tip[-1]]
        pos = []
        for i, name in enumerate(items):
            rot, loc = m.h.get_global(name)
            pos.append(rot @ tip[i] + loc)
        s = m.state[pc] = {"items": items, "ratio": [i / (n - 1) for i in range(n)], "local_tip": local_tip, "pos": pos,
                           "vel": [Vector() for _ in items], "time": Vector()}
    reach, wave, pend = _get(m, a, "Reach") or {}, _get(m, a, "Wave") or {}, _get(m, a, "Pendulum") or {}
    parent = m.h.parent.get(s["items"][0])
    p_rot, p_loc = m.h.get_global(parent) if parent else (Quaternion(), Vector())
    lerp = lambda x0, x1, t: x0 + (x1 - x0) * t  # noqa: E731
    for i, name in enumerate(s["items"]):
        ratio = s["ratio"][i]
        l_rot, l_loc = m.h.get_local(name)
        rot, loc = compose((l_rot, l_loc), (p_rot, p_loc))
        if reach.get("bEnabled"):
            e = ease(lerp(num(reach.get("ReachMinimum")), num(reach.get("ReachMaximum")), ratio), reach.get("ReachEase"))
            axis = p_rot @ vec(reach.get("ReachAxis"))
            direction = axis.lerp(_safe(vec(reach.get("ReachTarget")) - loc), e)
            rot = (quat_between(axis, direction) @ rot).normalized()
        if wave.get("bEnabled"):
            e = ease(lerp(num(wave.get("WaveMinimum")), num(wave.get("WaveMaximum")), ratio), wave.get("WaveEase"))
            u = s["time"] + vec(wave.get("WaveFrequency")) * ratio
            u = u + _mul(Vector((perlin(u.x + 132.4), perlin(u.y + 9.2), perlin(u.z + 217.9))), vec(wave.get("WaveNoise"))) * 2.0
            off = vec(wave.get("WaveOffset"))
            angles = _mul(Vector((math.sin(u.x + off.x), math.sin(u.y + off.y), math.sin(u.z + off.z))), vec(wave.get("WaveAmplitude")))
            angles = angles * (e * _curve(_get(m, a, "WaveCurve"), ratio))
            for k, axis in enumerate(((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))):
                rot = rot @ Quaternion(axis, angles[k])
            rot = rot.normalized()
        if pend.get("bEnabled"):
            unsimulated, tip = rot, s["local_tip"][i]
            e = ease(lerp(num(pend.get("PendulumMinimum")), num(pend.get("PendulumMaximum")), ratio), pend.get("PendulumEase"))
            up = p_rot @ vec(pend.get("UnwindAxis"))
            velocity = vec(pend.get("PendulumGravity")) + (p_rot @ tip) * num(pend.get("PendulumStiffness"))
            if m.dt > 0.0:
                v = s["vel"][i].lerp(velocity, min(max(num(pend.get("PendulumBlend")), 0.0), 0.999)) * num(pend.get("PendulumDrag"))
                before = s["pos"][i]
                p = before + v * m.dt
                s["pos"][i] = loc + _safe(p - loc) * tip.length
                s["vel"][i] = (s["pos"][i] - before) / m.dt
            rot = (quat_between(rot @ tip, s["pos"][i] - loc) @ rot).normalized()
            current_up = rot @ vec(pend.get("UnwindAxis"))
            d = current_up.dot(_safe(rot @ tip))
            current_up = Vector((current_up.x - d, current_up.y - d, current_up.z - d))     # a scalar off each axis, as written
            current_up = up.lerp(current_up, lerp(num(pend.get("UnwindMinimum")), num(pend.get("UnwindMaximum")), ratio))
            rot = (quat_between(current_up, up) @ rot).normalized()
            rot = _slerp(unsimulated, rot, min(max(e, 0.0), 1.0))
        m.h.set_global(name, (rot, loc))
        p_rot, p_loc = rot, loc
    s["time"] = s["time"] + vec(_get(m, a, "Speed")) * m.dt


UNITS = {"SpringInterpQuaternionV2": spring_interp_quat, "TimeOffsetTransform": time_offset_transform,
         "ItemHarmonics": item_harmonics, "ChainHarmonicsPerItem": chain_harmonics}
