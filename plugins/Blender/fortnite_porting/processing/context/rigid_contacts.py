"""Contacts between physics asset bodies (capsules and spheres, boxes as their bounding capsules): one point per body
pair at the closest points of their segments, pushed apart in the position phase, restitution and friction in the
velocity phase. The engine builds up to two points for near-parallel capsules; one is kept here."""
from mathutils import Vector

from .rigid_joints import world_inverse

RESTITUTION, FRICTION = 0.3, 0.7


def closest(a0, b0, a1, b1):
    """Closest points of segments a0-b0 and a1-b1."""
    d0, d1, r = b0 - a0, b1 - a1, a0 - a1
    e, f = d0.dot(d0), d1.dot(d1)
    if e <= 1e-12 and f <= 1e-12:
        return a0.copy(), a1.copy()
    if e <= 1e-12:
        return a0.copy(), a1 + d1 * max(0.0, min(1.0, d1.dot(r) / f))
    c = d0.dot(r)
    if f <= 1e-12:
        return a0 + d0 * max(0.0, min(1.0, -c / e)), a1.copy()
    b = d0.dot(d1)
    denom = e * f - b * b
    s = max(0.0, min(1.0, (b * d1.dot(r) - c * f) / denom)) if denom > 1e-12 else 0.0
    t = (b * s + d1.dot(r)) / f
    if t < 0.0:
        t, s = 0.0, max(0.0, min(1.0, -c / e))
    elif t > 1.0:
        t, s = 1.0, max(0.0, min(1.0, (b - c) / e))
    return a0 + d0 * s, a1 + d1 * t


class Contact:
    __slots__ = ("i", "j", "n", "phi", "r0", "r1", "k", "push", "impulse", "vn0", "ia0", "ia1", "inv_m0", "inv_m1", "inv_i0", "inv_i1",
                 "since")


ALIGNED, DEEP, RADIAL, T_GAP, SIN_MIN = 0.8, 0.05, 0.25, 0.2, 0.35     # the engine's capsule manifold tolerances


def _closest_points(wa0, we0, r0, wa1, we1, r1):
    p0, p1 = closest(wa0, we0, wa1, we1)
    gap = p0 - p1
    dist = gap.length
    n = gap / dist if dist > 1e-9 else Vector((0.0, 0.0, 1.0))
    return dist - r0 - r1, n, p0 - n * r0, p1 + n * r1, p0, p1


def _end_points(first, second, first_t, axes_dot, normal, cull, swap):
    """Up to two more points from the smaller capsule's cylinder toward the other, where it overlaps the other's
    segment (the engine's capsule one-shot manifold): (phi, n, point on body 0, point on body 1)."""
    (fc, fax, fhl, fr), (sc, sax, shl, sr) = first, second
    ortho = fax.cross(fax.cross(normal))
    if ortho.length_squared <= SIN_MIN ** 2:
        return []
    ortho = ortho.normalized()
    if ortho.dot(sc - fc) < 0.0:
        ortho = -ortho
    projected = 2.0 * fhl * axes_dot
    t_min = ((sc - shl * sax) - (fc + fhl * fax)).dot(sax) / projected
    t_max = ((sc + shl * sax) - (fc - fhl * fax)).dot(sax) / projected
    radial = RADIAL * sr / fhl
    radial = radial + (1.0 - radial) * axes_dot
    lo, hi = max(-1.0, t_min, -radial), min(1.0, t_max, radial)
    out = []
    for t in ([lo] if lo < first_t - T_GAP else []) + ([hi] if hi > first_t + T_GAP else []):
        pos = fc + (t * fhl) * fax + ortho * fr
        on_axis = closest(sc - shl * sax, sc + shl * sax, pos, pos)[0]
        dist = (pos - on_axis).length
        if dist < 1e-9:
            continue
        d = (pos - on_axis) / dist
        phi = dist - sr
        if phi < cull:
            other = on_axis + sr * d
            out.append((phi, -d, other, pos) if swap else (phi, d, pos, other))
    return out


def _manifold(wa0, we0, r0, dyn0, wa1, we1, r1, dyn1, cull):
    """The closest point plus, for capsules lying along each other or deep in each other, points at the ends."""
    phi, n, s0, s1, p0, p1 = _closest_points(wa0, we0, r0, wa1, we1, r1)
    out = [(phi, n, s0, s1)]
    hl0, hl1 = (we0 - wa0).length * 0.5, (we1 - wa1).length * 0.5
    if hl0 < 1e-6 or hl1 < 1e-6:
        return out
    c0, c1 = (wa0 + we0) * 0.5, (wa1 + we1) * 0.5
    ax0, ax1 = (we0 - wa0) / (2.0 * hl0), (we1 - wa1) / (2.0 * hl1)
    dot = ax0.dot(ax1)
    if dot < 0.0:
        ax0, dot = -ax0, -dot
    t0, t1 = (p0 - c0).dot(ax0) / hl0, (p1 - c1).dot(ax1) / hl1
    if (t0 < -1.0 + T_GAP and t1 > 1.0 - T_GAP) or (t1 < -1.0 + T_GAP and t0 > 1.0 - T_GAP):
        return out          # end to end, not side by side
    dyn_r0, dyn_r1 = r0 if dyn0 else float("inf"), r1 if dyn1 else float("inf")
    if dot <= ALIGNED and phi >= -DEEP * min(dyn_r0, dyn_r1):
        return out
    if dyn_r0 <= dyn_r1:
        return out + _end_points((c0, ax0, hl0, r0), (c1, ax1, hl1, r1), t0, dot, n, cull, False)
    return out + _end_points((c1, ax1, hl1, r1), (c0, ax0, hl0, r0), t1, dot, n, cull, True)


def detect(bodies, pairs, segments, cull, initial=None, h=0.0, push_out=0.0, manifold=True, corrected=False):
    """Contacts for the body pairs, one set per pair of their shapes, from the predicted poses (corrected: the poses with
    this step's corrections so far, as the engine re-detects each iteration without manifolds); segments[i]: (a, b,
    radius) per shape, CoM frame. manifold: capsules along each other also touch at their ends.
    initial: each contact point's overlap allowance, kept between steps (updated here): one that begins overlapping may
    stay that deep but no deeper (the engine's initial overlap, which RBAN never sets a speed to resolve). push_out: the
    asset's max depenetration velocity, capping each step's correction (0: none)."""
    out, kept = [], {}
    for i, j in pairs:
        b0, b1 = bodies[i], bodies[j]
        (p0_, q0_), (p1_, q1_) = (b0.corrected(), b1.corrected()) if corrected else ((b0.p, b0.q), (b1.p, b1.q))
        for sa, (a0, e0, r0) in enumerate(segments[i]):
            wa0, we0 = p0_ + q0_ @ a0, p0_ + q0_ @ e0
            for sb, (a1, e1, r1) in enumerate(segments[j]):
                wa1, we1 = p1_ + q1_ @ a1, p1_ + q1_ @ e1
                # no closer than their centres minus half lengths and radii: past the cull distance, skip the maths
                reach = ((wa0 - we0).length + (wa1 - we1).length) * 0.5 + r0 + r1 + cull
                if ((wa0 + we0) - (wa1 + we1)).length * 0.5 > reach:
                    continue
                points = _manifold(wa0, we0, r0, b0.dynamic, wa1, we1, r1, b1.dynamic, cull) if manifold else                     [_closest_points(wa0, we0, r0, wa1, we1, r1)[:4]]
                for slot, (phi, n, s0, s1) in enumerate(points):
                    if phi >= cull:
                        continue
                    c = Contact()
                    c.i, c.j, c.phi, c.n = i, j, phi, n
                    key = (i, j, sa, sb, slot)
                    if initial is not None:
                        before = initial.get(key)
                        allowed = min(c.phi, 0.0) if before is None else (max(c.phi, before) if before < 0.0 else 0.0)
                        kept[key] = allowed
                        c.phi -= allowed
                    if push_out > 0.0:
                        c.phi = max(c.phi, -push_out * h)
                    c.r0, c.r1 = s0 - p0_, s1 - p1_
                    c.inv_m0 = b0.inv_m if b0.dynamic else 0.0
                    c.inv_m1 = b1.inv_m if b1.dynamic else 0.0
                    c.inv_i0 = world_inverse(q0_, b0.inv_il) if b0.dynamic else None
                    c.inv_i1 = world_inverse(q1_, b1.inv_il) if b1.dynamic else None
                    ra, rb = c.r0.cross(c.n), c.r1.cross(c.n)
                    c.ia0 = c.inv_i0 @ ra if c.inv_i0 is not None else Vector()
                    c.ia1 = c.inv_i1 @ rb if c.inv_i1 is not None else Vector()
                    c.k = c.inv_m0 + c.inv_m1 + ra.dot(c.ia0) + rb.dot(c.ia1)
                    c.push, c.impulse = 0.0, 0.0
                    # the corrections already in the poses it was found at: only later ones change its depth
                    c.since = (b0.dp.copy(), b0.dq.copy(), b1.dp.copy(), b1.dq.copy()) if corrected else None
                    c.vn0 = c.n.dot((b0.v + b0.w.cross(c.r0)) - (b1.v + b1.w.cross(c.r1)))
                    out.append(c)
    if initial is not None:         # points out of reach start afresh next time
        initial.clear()
        initial.update(kept)
    return out


def apply_position(contacts, bodies):
    for c in contacts:
        if c.k <= 0.0:
            continue
        b0, b1 = bodies[c.i], bodies[c.j]
        dp0, dq0, dp1, dq1 = (b0.dp, b0.dq, b1.dp, b1.dq) if c.since is None else             (b0.dp - c.since[0], b0.dq - c.since[1], b1.dp - c.since[2], b1.dq - c.since[3])
        e = c.phi + c.n.dot(dp0 - dp1) + dq0.dot(c.r0.cross(c.n)) - dq1.dot(c.r1.cross(c.n))
        if e >= 0.0 and c.push <= 0.0:
            continue
        p = max(-e / c.k, -c.push)          # the net push-out never pulls
        c.push += p
        if b0.dynamic:
            b0.dp = b0.dp + c.n * (c.inv_m0 * p)
            b0.dq = b0.dq + c.ia0 * p
        if b1.dynamic:
            b1.dp = b1.dp - c.n * (c.inv_m1 * p)
            b1.dq = b1.dq - c.ia1 * p


def apply_velocity(contacts, bodies, h, gravity, last):
    for c in contacts:
        if c.push <= 0.0 or c.k <= 0.0:
            continue
        b0, b1 = bodies[c.i], bodies[c.j]
        rel = (b0.v + b0.w.cross(c.r0)) - (b1.v + b1.w.cross(c.r1))
        vn = c.n.dot(rel)
        target = -RESTITUTION * c.vn0 if -c.vn0 > 1.5 * gravity * h else 0.0
        j = max((target - vn) / c.k, -(c.impulse + c.push / h))
        c.impulse += j
        impulse = c.n * j
        if last:            # dynamic friction inside the cone of the normal impulse
            tangent = rel - c.n * vn
            speed = tangent.length
            if speed > 1e-9:
                t = tangent / speed
                kt = c.inv_m0 + c.inv_m1 + (c.r0.cross(t)).dot((c.inv_i0 @ c.r0.cross(t)) if c.inv_i0 is not None else Vector()) \
                    + (c.r1.cross(t)).dot((c.inv_i1 @ c.r1.cross(t)) if c.inv_i1 is not None else Vector())
                if kt > 0.0:
                    impulse = impulse - t * min(speed / kt, FRICTION * (c.impulse + c.push / h))
        if b0.dynamic:
            b0.v = b0.v + impulse * c.inv_m0
            b0.w = b0.w + c.inv_i0 @ c.r0.cross(impulse)
        if b1.dynamic:
            b1.v = b1.v - impulse * c.inv_m1
            b1.w = b1.w - c.inv_i1 @ c.r1.cross(impulse)
