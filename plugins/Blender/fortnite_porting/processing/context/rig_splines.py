"""The Control Rig spline units characters' rigs run (hair and skirt chains laid along a curve): a spline built from
points or transforms as a dense table of samples every query reads, and a chain of bones fitted to it. Same interface
as rig_units."""
import math

from mathutils import Matrix, Quaternion, Vector

from .dynamics_solver import compose
from .rig_units import _get, _lerp, _set, element, f32, num, transform, transform_out, vec, vec_out


class Spline:
    """Shared spline data: copying the handle shares it, as the engine's does."""

    def __init__(self, controls, mode, closed, per_segment):
        self.controls, self.mode, self.closed = controls, mode, closed
        self.samples, self.lengths = [], []
        n = len(controls)
        if n < 4 or per_segment < 1:
            return
        points = [t[1] for t in controls]
        curve = _hermite(points, closed) if mode == 1 else _bspline(points, closed)
        count = (n - 1) * per_segment
        du, u, params = f32(1.0 / (count - 1)), 0.0, []
        for _ in range(count):
            params.append(u)
            u = f32(u + du)
        self.samples = [(Quaternion(), curve(u)) for u in params]
        for i, u in enumerate(params):      # X down the curve, Y toward the controls' turned +Y
            t = self.tangent(u)
            t = t.normalized() if t.length > 1e-8 else Vector((1.0, 0.0, 0.0))
            a = int(u * (n - 1))
            a = min(a, n - 2)
            w = (u - a / (n - 1)) * (n - 1)
            up = controls[a][0].slerp(controls[a + 1][0], w) @ Vector((0.0, 1.0, 0.0))
            side = t.cross(up)
            side = side.normalized() if side.length > 1e-8 else Vector((0.0, 0.0, 1.0))
            up = side.cross(t).normalized()
            self.samples[i] = (Matrix((t, up, side)).transposed().to_quaternion(), self.samples[i][1])
        self.lengths = [0.0]
        for (_, p0), (_, p1) in zip(self.samples, self.samples[1:]):
            self.lengths.append(self.lengths[-1] + (p1 - p0).length)

    def __deepcopy__(self, memo):
        return self

    def valid(self):
        return len(self.samples) > 1

    def _at(self, u):
        f = min(max(num(u), 0.0), 1.0) * (len(self.samples) - 1)
        i = min(int(math.floor(f)), len(self.samples) - 1)
        return i, min(i + 1, len(self.samples) - 1), f - i

    def position(self, u):
        i, j, t = self._at(u)
        return self.samples[i][1].lerp(self.samples[j][1], t)

    def transform(self, u):
        i, j, t = self._at(u)
        return _lerp(self.samples[i], self.samples[j], t)

    def tangent(self, u):
        """The chord at u turned part way toward the next one; not normalised (its length is the chord's)."""
        pts = [s[1] for s in self.samples]
        i, _, t = self._at(u)
        i = min(i, len(pts) - 2)
        c0 = pts[i + 1] - pts[i]
        if i + 2 > len(pts) - 1:
            return c0
        turn = Quaternion().slerp(between(c0, pts[i + 2] - pts[i + 1]), t)
        return turn @ c0

    def length(self):
        return self.lengths[-1] if self.lengths else 0.0

    def closest(self, pos):
        best, param, last = None, 0.0, len(self.samples) - 1
        for i in range(last):
            p0, p1 = self.samples[i][1], self.samples[i + 1][1]
            v = p1 - p0
            t = -(p0 - pos).dot(v) / v.length_squared if v.length_squared > 1e-12 else 0.0
            if 0.0 < t < 1.0:
                d, u = (p0 - pos).cross(v).length / v.length, (i + t) / last
            else:
                end = i if t <= 0.0 else i + 1
                d, u = (self.samples[end][1] - pos).length, end / last
            if best is None or d < best:
                best, param = d, u
        return param


def _hermite(points, closed):
    """Catmull-Rom (tension 0.5) through the points: open ends padded with mirrored phantoms."""
    n = len(points)
    if closed:
        padded, segs = [points[(n - 1 + k) % n] for k in range(n + 4)], n
    else:
        padded, segs = [2 * points[0] - points[1]] + points + [2 * points[-1] - points[-2]], n - 1

    def at(u):
        s = min(int(math.floor(u * segs)), segs - 1)
        t = min(max(u * segs - s, 0.0), 1.0)
        p0, p1, p2, p3 = padded[s:s + 4]
        m1, m2 = 0.5 * (p2 - p0), 0.5 * (p3 - p1)
        t2, t3 = t * t, t * t * t
        return (2 * t3 - 3 * t2 + 1) * p1 + (t3 - 2 * t2 + t) * m1 + (-2 * t3 + 3 * t2) * p2 + (t3 - t2) * m2
    return at


def _bspline(points, closed, p=3):
    """A cubic B-spline by de Boor, clamped when open; its parameter kept just inside the knots (1e-4)."""
    if closed:
        points = [points[(len(points) - 1 + k) % len(points)] for k in range(len(points) + 3)]
    n = len(points) - 1
    m = n + p + 1
    if closed:
        knots = [i / m for i in range(m + 1)]
    else:
        knots = [0.0 if i <= p else 1.0 if i >= m - p else (i - p) / (m - 2 * p) for i in range(m + 1)]

    def at(u):
        lo, hi = knots[p] + 1e-4, knots[m - p] - 1e-4
        x = lo + min(max(u, 0.0), 1.0) * (hi - lo)
        k = next((i for i in range(m) if abs(knots[i] - x) <= 1e-8 or knots[i] < x < knots[i + 1]), m - p - 1)
        k = min(max(k, p), n)
        d = {j: points[j].copy() for j in range(k - p, k + 1)}
        for r in range(1, p + 1):
            if abs(knots[k - p + r] - x) <= 1e-8:
                continue
            for j in range(k, k - p + r - 1, -1):
                span = knots[j + p - r + 1] - knots[j]
                alpha = (x - knots[j]) / span if span > 1e-12 else 0.0
                d[j] = (1 - alpha) * d[j - 1] + alpha * d[j]
        return d[k]
    return at


def between(a, b):
    """The shortest turn taking direction a onto b (a half turn about a side axis when they're opposite)."""
    a = a.normalized() if a.length > 1e-8 else a
    b = b.normalized() if b.length > 1e-8 else b
    w = 1.0 + a.dot(b)
    if w < 1e-6:
        side = a.cross(Vector((0.0, 1.0, 0.0)) if abs(a.x) >= max(abs(a.y), abs(a.z)) else Vector((-1.0, 0.0, 0.0)))
        return Quaternion(side.normalized(), math.pi) if side.length > 1e-8 else Quaternion()
    c = a.cross(b)
    return Quaternion((w, c.x, c.y, c.z)).normalized()


def _small(v):
    return v.length_squared <= 1e-16


def _spline(m, a):
    s = _get(m, a, "Spline")
    return s if isinstance(s, Spline) and s.valid() else None


def from_points(m, a, ops, pc):
    controls = [(Quaternion(), vec(p)) for p in _get(m, a, "Points") or []]
    _set(m, a, "Spline", Spline(controls, int(num(_get(m, a, "SplineMode"))), bool(_get(m, a, "bClosed")),
                                int(num(_get(m, a, "SamplesPerSegment")))))


def from_transforms(m, a, ops, pc):
    controls = [transform(t) for t in _get(m, a, "Transforms") or []]
    _set(m, a, "Spline", Spline(controls, int(num(_get(m, a, "SplineMode"))), bool(_get(m, a, "bClosed")),
                                int(num(_get(m, a, "SamplesPerSegment")))))


def position(m, a, ops, pc):
    s = _spline(m, a)
    if s is not None:
        _set(m, a, "Position", vec_out(s.position(_get(m, a, "U"))))


def transform_at(m, a, ops, pc):
    """The sample frame with the given local axes turned onto its X, then Z."""
    s = _spline(m, a)
    if s is None:
        return
    rot, pos = s.transform(min(max(num(_get(m, a, "U")), 0.0), 1.0))
    for key, onto in (("PrimaryAxis", Vector((1.0, 0.0, 0.0))), ("SecondaryAxis", Vector((0.0, 0.0, 1.0)))):
        axis = vec(_get(m, a, key))
        if not _small(axis):
            rot = (between(rot @ axis, rot @ onto) @ rot).normalized()
    _set(m, a, "Transform", transform_out((rot, pos)))


def closest(m, a, ops, pc):
    s = _spline(m, a)
    if s is not None:
        _set(m, a, "U", s.closest(vec(_get(m, a, "Position"))))


def get_length(m, a, ops, pc):
    s = _spline(m, a)
    _set(m, a, "Length", s.length() if s else 0.0)


def _rotation_map(rotations, count):
    """Each chain item's pair of twist entries and blend (linear easing only)."""
    entries = sorted(((min(max(num(r.get("Ratio")), 0.0), 1.0), transform({"Rotation": r.get("Rotation")})[0]) for r in rotations),
                     key=lambda e: e[0])
    out = []
    for k in range(count):
        t = k / (count - 1) if count > 1 else 0.0
        if len(entries) < 2 or t <= entries[0][0]:
            out.append((0, 0, 0.0))
        elif t >= entries[-1][0]:
            out.append((len(entries) - 1, len(entries) - 1, 0.0))
        else:
            i = next(i for i in range(len(entries) - 1) if entries[i][0] <= t <= entries[i + 1][0])
            span = entries[i + 1][0] - entries[i][0]
            out.append((i, i + 1, (t - entries[i][0]) / span) if span > 1e-8 else (i, i, 0.0))
    return [e[1] for e in entries], out


def _set_global(h, name, t, propagate):
    kept = {} if propagate else {c: h.get_global(c) for c, p in h.parent.items() if p == name}
    h.set_global(name, t)
    for c, g in kept.items():
        h.set_global(c, g)


def fit_chain(m, a, ops, pc):
    """Bones laid along the curve at their first run's lengths (Front) or stretched over it, each aimed at the next."""
    items = [n for n in (element(i) for i in _get(m, a, "Items") or []) if n and m.h.has(n)]
    s, rotations = _spline(m, a), _get(m, a, "Rotations") or []
    work = m.state.get(pc)
    if work is None and len(items) >= 2:
        g = [m.h.get_global(n)[1] for n in items]
        segments = [0.0] + [(g[k - 1] - g[k]).length for k in range(1, len(items))]
        work = m.state[pc] = {"segments": segments, "chain": sum(segments), "map": _rotation_map(rotations, len(items)) if len(rotations) > 1 else None}
    if work is None or s is None or len(work["segments"]) != len(items):
        return
    lo, hi = num(_get(m, a, "Minimum")), num(_get(m, a, "Maximum"))
    count = min(max(int(num(_get(m, a, "SamplingPrecision"))), 4), 64)
    curve = [s.position(lo + (hi - lo) * i / (count - 1)) for i in range(count)]
    length = sum((curve[i] - curve[i - 1]).length for i in range(1, count))
    if work["chain"] < 1e-8 or length < 1e-8:
        return
    curve.append(curve[-1] + s.tangent(hi) * work["chain"])        # an unnormalised tangent: far past the end
    stretched = int(num(_get(m, a, "Alignment"))) == 1
    pos, c = [curve[0]], 1
    for k in range(1, len(items)):
        want = work["segments"][k] * (length / work["chain"] if stretched else 1.0)
        here = pos[-1]
        d = (curve[c] - here).length
        if d > want or c >= len(curve) - 1:
            pos.append(here.lerp(curve[c], want / d) if d > 1e-8 else here.copy())
            continue
        while c < len(curve) - 1 and (curve[c + 1] - here).length < want:
            c += 1
        if c >= len(curve) - 1:
            pos.append(here.lerp(curve[c], want / max((curve[c] - here).length, 1e-8)))
            continue
        da, db = (curve[c] - here).length, (curve[c + 1] - here).length
        pos.append(curve[c].copy() if abs(db - da) < 1e-8 else curve[c].lerp(curve[c + 1], (want - da) / (db - da)))
        c += 1
    primary, secondary, pole = vec(_get(m, a, "PrimaryAxis")), vec(_get(m, a, "SecondaryAxis")), vec(_get(m, a, "PoleVectorPosition"))
    weight = min(max(num(_get(m, a, "Weight")), 0.0), 1.0)
    before = [m.h.get_global(n) for n in items]
    propagate = bool(_get(m, a, "bPropagateToChildren")) and not rotations
    target = Vector()
    for k, name in enumerate(items):
        rot = m.h.get_global(name)[0]
        if k + 1 < len(items):
            target = pos[k + 1] - pos[k]
        if not _small(target) and not _small(primary):
            rot = (between(rot @ primary, target) @ rot).normalized()
        if not _small(secondary):
            v = pole - pos[k]
            pa = (rot @ primary).normalized() if not _small(primary) else Vector()
            v = v - pa * v.dot(pa)
            if not _small(v):
                rot = (between(rot @ secondary, v) @ rot).normalized()
        result = (rot, pos[k])
        if weight < 1.0 - 1e-8:
            result = _lerp(before[k], result, weight)
        _set_global(m.h, name, result, propagate)
    if rotations:
        _twist(m, items, rotations, work, weight, bool(_get(m, a, "bPropagateToChildren")))


def _twist(m, items, rotations, work, weight, propagate):
    """The rotation entries' local twists eased down the chain, each carried into the next child."""
    quats, mapping = work["map"] or ([transform({"Rotation": rotations[0].get("Rotation")})[0]], [(0, 0, 0.0)] * len(items))
    parent = m.h.parent.get(items[0])
    base = m.h.get_global(parent) if parent else (Quaternion(), Vector())
    locals_ = [m.h.get_local(n) for n in items]
    for k, name in enumerate(items):
        i, j, t = mapping[k]
        r = quats[j] if t > 1.0 - 1e-8 else quats[i] if t <= 1e-8 else quats[i].slerp(quats[j], t).normalized()
        base = compose(locals_[k], base)
        base = (base[0].slerp(base[0] @ r, weight), base[1])
        _set_global(m.h, name, base, propagate)


UNITS = {"ControlRigSplineFromPoints": from_points, "ControlRigSplineFromTransforms": from_transforms,
         "PositionFromControlRigSpline": position, "TransformFromControlRigSpline2": transform_at,
         "ClosestParameterFromControlRigSpline": closest, "GetLengthControlRigSpline": get_length,
         "FitChainToSplineCurveItemArray": fit_chain}
