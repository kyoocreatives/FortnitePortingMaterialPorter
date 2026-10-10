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
    __slots__ = ("i", "j", "n", "phi", "r0", "r1", "k", "push", "impulse", "vn0", "ia0", "ia1", "inv_m0", "inv_m1", "inv_i0", "inv_i1")


def detect(bodies, pairs, segments, cull):
    """Contacts for the body pairs from the predicted poses; segments[i]: (a, b, radius) per shape, CoM frame."""
    out = []
    for i, j in pairs:
        b0, b1 = bodies[i], bodies[j]
        best = None
        for a0, e0, r0 in segments[i]:
            wa0, we0 = b0.p + b0.q @ a0, b0.p + b0.q @ e0
            for a1, e1, r1 in segments[j]:
                wa1, we1 = b1.p + b1.q @ a1, b1.p + b1.q @ e1
                # no closer than their centres minus half lengths and radii: past the cull distance, skip the maths
                reach = ((wa0 - we0).length + (wa1 - we1).length) * 0.5 + r0 + r1 + cull
                if ((wa0 + we0) - (wa1 + we1)).length * 0.5 > reach:
                    continue
                p0, p1 = closest(wa0, we0, wa1, we1)
                gap = p0 - p1
                dist = gap.length
                phi = dist - r0 - r1
                if best is None or phi < best[0]:
                    n = gap / dist if dist > 1e-9 else Vector((0.0, 0.0, 1.0))
                    best = (phi, n, p0 - n * r0, p1 + n * r1)
        if best is None or best[0] >= cull:
            continue
        c = Contact()
        c.i, c.j, (c.phi, c.n, s0, s1) = i, j, best
        c.r0, c.r1 = s0 - b0.p, s1 - b1.p
        c.inv_m0 = b0.inv_m if b0.dynamic else 0.0
        c.inv_m1 = b1.inv_m if b1.dynamic else 0.0
        c.inv_i0 = world_inverse(b0.q, b0.inv_il) if b0.dynamic else None
        c.inv_i1 = world_inverse(b1.q, b1.inv_il) if b1.dynamic else None
        a0, a1 = c.r0.cross(c.n), c.r1.cross(c.n)
        c.ia0 = c.inv_i0 @ a0 if c.inv_i0 is not None else Vector()
        c.ia1 = c.inv_i1 @ a1 if c.inv_i1 is not None else Vector()
        c.k = c.inv_m0 + c.inv_m1 + a0.dot(c.ia0) + a1.dot(c.ia1)
        c.push, c.impulse = 0.0, 0.0
        c.vn0 = c.n.dot((b0.v + b0.w.cross(c.r0)) - (b1.v + b1.w.cross(c.r1)))
        out.append(c)
    return out


def apply_position(contacts, bodies):
    for c in contacts:
        if c.k <= 0.0:
            continue
        b0, b1 = bodies[c.i], bodies[c.j]
        e = c.phi + c.n.dot(b0.dp - b1.dp) + b0.dq.dot(c.r0.cross(c.n)) - b1.dq.dot(c.r1.cross(c.n))
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
