"""One clothing asset simulated as Fortnite runs it: Chaos cloth's legacy PBD evolution (owner notes
helpers/chaos_cloth.md). Local space has world axes with its origin on the character; the reference bone's motion
reaches the cloth through the velocity scales; each frame skins the simulation mesh, integrates gravity, aerodynamics
and damping, then runs tethers once and the springs, max distance, backstop, anim drive and colliders per iteration.
Transforms are (rotation, position) pairs in the game's numbers (cm)."""
import math

import numpy as np
from mathutils import Quaternion, Vector

from .dynamics_solver import compose

MAX_DT = 1.0 / 30.0         # MaxPhysicsDeltaTime: the step is capped there, smoothed from there
SMOOTHING = 0.1
WORLD_GRAVITY = (0.0, 0.0, -980.0)
FLUID_DENSITY = 1.225e-6    # kg/cm^3
TELEPORT_DISTANCE = 300.0   # the mesh component's defaults (rotation off)


def stiffness(v, h, n):
    """An authored stiffness in [0, 1] as the PBD factor for a substep h run n times (120 Hz reference)."""
    v = np.asarray(v, np.float64)
    fit = (1000.0 ** v - 1.0) / 999.0
    k = 1.0 - np.power(np.clip(1.0 - fit, 0.0, 1.0), h * 120.0 / n)
    k = np.where(v <= 1e-8, 0.0, np.where(v >= 1.0 - 1e-8, 1.0, k))
    return float(k) if k.ndim == 0 else k


def table(pair, h, n):
    lo, hi = (min(max(float(x), 0.0), 1.0) for x in pair)
    return stiffness(np.array([lo + j / 15.0 * (hi - lo) for j in range(16)]), h, n)


def _authored(cloth, name, pair, verts, quantized=True, clamp=True, stiff=True):
    """A weighted value per constraint: low + (high - low) * the mean painted weight of its vertices (quantized to the
    engine's 16 steps, a stiffness's low and high kept in [0, 1]); low everywhere without a map or with low == high."""
    lo, hi = float(pair[0]), float(pair[1])
    verts = np.asarray(verts, np.int64).reshape(len(verts), -1) if len(verts) else np.zeros((0, 1), np.int64)
    m = cloth["maps"].get(name)
    if m is None or abs(hi - lo) <= 1e-4:
        return np.full(len(verts), lo)
    m = np.asarray(m, np.float64)
    w = (np.clip(m, 0.0, 1.0) if clamp else m)[verts].mean(axis=1)
    if quantized:
        if stiff:
            lo, hi = min(max(lo, 0.0), 1.0), min(max(hi, 0.0), 1.0)
        w = np.trunc(np.clip(w, 0.0, 1.0) * 15.0) / 15.0
    return lo + (hi - lo) * w


def colour(cons, dynamic):
    """Gauss-Seidel order: constraints in turn when there are 100 or fewer, else the engine's greedy colouring (each
    colour shares no dynamic vertex, so it runs at once)."""
    if len(cons) <= 100:
        return [np.array([i]) for i in range(len(cons))]
    used, colours = {}, []
    for c in cons:
        taken = set().union(*[used.get(v, set()) for v in c if dynamic[v]])
        k = 0
        while k in taken:
            k += 1
        colours.append(k)
        for v in c:
            if dynamic[v]:
                used.setdefault(v, set()).add(k)
    colours = np.array(colours)
    return [np.where(colours == k)[0] for k in range(colours.max() + 1)] if len(colours) else []


def springs(P, w, groups, a, b, rest, k):
    for g in groups:
        ia, ib = a[g], b[g]
        d = P[ia] - P[ib]
        length = np.linalg.norm(d, axis=1)
        denom = w[ia] + w[ib]
        ok = (denom > 0.0) & (length > 1e-8)
        s = np.where(ok, k[g] * (length - rest[g]) / np.where(ok, length * denom, 1.0), 0.0)[:, None] * d
        P[ia] -= w[ia][:, None] * s
        P[ib] += w[ib][:, None] * s


def area_springs(P, w, groups, tri, bary, rest, k):
    for g in groups:
        i1, i2, i3, b = tri[g, 0], tri[g, 1], tri[g, 2], bary[g]
        wq = (1.0 - b) * w[i3] + b * w[i2]
        d = P[i1] - (P[i3] + b[:, None] * (P[i2] - P[i3]))
        length = np.linalg.norm(d, axis=1)
        ok = ((w[i1] > 0.0) | (wq > 0.0)) & (length > 1e-8)
        delta = np.where(ok, k[g] * (length - rest[g]) / np.where(ok, length * (wq + w[i1]), 1.0), 0.0)[:, None] * d
        mu = (2.0 / (np.maximum(b, 1.0 - b) + 1.0))[:, None]
        P[i1] -= mu * w[i1][:, None] * delta
        P[i2] += mu * (w[i2] * b)[:, None] * delta
        P[i3] += mu * (w[i3] * (1.0 - b))[:, None] * delta


def _angles(p1, p2, p3, p4):
    c1, c2 = np.cross(p1 - p3, p2 - p3), np.cross(p2 - p4, p1 - p4)
    l1, l2 = np.linalg.norm(c1, axis=1), np.linalg.norm(c2, axis=1)
    n1 = c1 / np.where(l1 > 1e-8, l1, 1.0)[:, None]
    n2 = c2 / np.where(l2 > 1e-8, l2, 1.0)[:, None]
    e = p2 - p1
    le = np.linalg.norm(e, axis=1)
    e = e / np.where(le > 1e-8, le, 1.0)[:, None]
    cos = np.clip((n1 * n2).sum(1), -1.0, 1.0)
    sin = np.clip((np.cross(n2, n1) * e).sum(1), -1.0, 1.0)
    return np.arctan2(sin, cos), cos, sin, n1, n2, e, l1, l2


def bending_elements(P, w, groups, quad, rest, k):
    for g in groups:
        i1, i2, i3, i4 = quad[g, 0], quad[g, 1], quad[g, 2], quad[g, 3]
        p1, p2, p3, p4 = P[i1], P[i2], P[i3], P[i4]
        theta, cos, sin, n1, n2, e, l1, l2 = _angles(p1, p2, p3, p4)
        g1 = (cos[:, None] * np.cross(e, n2) - sin[:, None] * n2) / np.where(l1 > 1e-8, l1, np.inf)[:, None]
        g2 = (cos[:, None] * np.cross(n1, e) - sin[:, None] * n1) / np.where(l2 > 1e-8, l2, np.inf)[:, None]
        d13, d23 = np.cross(p2 - p3, g1), np.cross(g1, p1 - p3)
        d24, d14 = np.cross(p1 - p4, g2), np.cross(g2, p2 - p4)
        grads = (d13 + d14, d23 + d24, -d13 - d23, -d14 - d24)
        idx = (i1, i2, i3, i4)
        denom = sum(w[i] * (gr * gr).sum(1) for i, gr in zip(idx, grads))
        s = np.where(denom > 1e-8, np.clip(k[g] * (theta - rest[g]), -math.pi / 4, math.pi / 4) / np.where(denom > 1e-8, denom, 1.0), 0.0)
        for i, gr in zip(idx, grads):
            P[i] -= (s * w[i])[:, None] * gr


def max_distance(P, anchor, radius, dyn):
    d = P[dyn] - anchor[dyn]
    length = np.linalg.norm(d, axis=1)
    out = length ** 2 > radius[dyn] ** 2 + 1e-8
    i = dyn[out]
    P[i] = anchor[i] + (radius[i] / length[out])[:, None] * d[out]


def backstop(P, anchor, normal, radius, distance, dyn, legacy):
    a, n, r, D = anchor[dyn], normal[dyn], radius[dyn][:, None], distance[dyn][:, None]
    C = a - (D if legacy else r + D) * n
    d = P[dyn] - C
    length = np.linalg.norm(d, axis=1)
    tiny = length ** 2 < 1e-8
    inside = ~tiny & (length < r[:, 0])
    P[dyn[tiny]] = (a - ((D - r) if legacy else D) * n)[tiny]
    P[dyn[inside]] = C[inside] + (r[inside] / length[inside][:, None]) * d[inside]


def mapped(slots, pos, nrm):
    """The engine's render mapping (notes 6.2) for each slot: its influences' barycentric points (the third
    barycentric is 1 - b0 - b1) pushed along the particles' normals, weighted; (slots, 3) in the particles' space."""
    tri = np.asarray(slots["tri"], np.int64)
    if not len(tri):
        return np.zeros((0, 3))
    b, d, w = np.asarray(slots["bary"]), np.asarray(slots["dist"]), np.asarray(slots["weight"])
    coef = np.stack([b[..., 0], b[..., 1], 1.0 - b[..., 0] - b[..., 1]], axis=-1)
    points = (coef[..., None] * (pos[tri] + nrm[tri] * d[..., None, None])).sum(-2)
    return (w[..., None] * points).sum(1) / w.sum(1)[:, None]


class Collider:
    """A physics-asset shape on its bone; frame = the bone's (rotation, position) in the cloth's local space."""

    def __init__(self, shape, frame):
        self.shape, self.kind = shape, shape["kind"]
        self.old = self.frame = frame
        self.velocity, self.spin = Vector(), Vector()

    def move(self, frame):
        self.old, self.frame = self.frame, frame

    def at(self, alpha, h):
        """Its frame part-way through the frame's move (and its velocity over the substep)."""
        before = self.now if hasattr(self, "now") else self.old
        q = self.old[0].slerp(self.frame[0], alpha)
        self.now = (q, self.old[1].lerp(self.frame[1], alpha))
        self.velocity = (self.now[1] - before[1]) / h
        axis, angle = (before[0].conjugated() @ q).to_axis_angle() if h > 0 else (Vector((1, 0, 0)), 0.0)
        self.spin = (before[0] @ axis) * ((angle - 2 * math.pi if angle > math.pi else angle) / h)

    def distance(self, p):
        """Signed distance and outward normal (cloth local space) for points p (n, 3)."""
        q, t = getattr(self, "now", self.frame)
        R = np.array(q.to_matrix())
        local = (p - np.array(t)) @ R      # into the bone's frame
        s = self.shape
        if self.kind == "sphere":
            d = local - np.array(s["center"])
            length = np.linalg.norm(d, axis=1)
            phi, n = length - s["radius"], d
        elif self.kind in ("capsule", "tapered"):
            a, b = np.array(s["ends"][0]), np.array(s["ends"][1])
            axis = b - a
            height = np.linalg.norm(axis)
            axis = axis / height if height > 1e-8 else np.array([0.0, 0.0, 1.0])
            u = np.clip((local - a) @ axis, 0.0, height)
            d = local - (a + u[:, None] * axis)
            length = np.linalg.norm(d, axis=1)
            if self.kind == "capsule":
                radius = s["radius"]
            else:
                r0, r1 = s["radii"]
                radius = r0 + (r1 - r0) * (u / height) if height > 1e-8 else max(r0, r1)
            phi, n = length - radius, d
        else:
            qb = Quaternion(s["rotation"])
            Rb = np.array(qb.to_matrix())
            lb = (local - np.array(s["center"])) @ Rb
            half = np.array(s["half"])
            outside = np.abs(lb) - half
            out_len = np.linalg.norm(np.maximum(outside, 0.0), axis=1)
            inside = outside.max(axis=1)
            nb = np.where(np.abs(lb) - half > 0.0, np.maximum(outside, 0.0) * np.sign(lb), 0.0)
            face = np.argmax(outside, axis=1)
            inner = np.zeros_like(lb)
            inner[np.arange(len(lb)), face] = np.sign(lb[np.arange(len(lb)), face])
            n = np.where((out_len > 0.0)[:, None], nb, inner) @ Rb.T
            phi = np.where(out_len > 0.0, out_len, inside)
        length = np.linalg.norm(n, axis=1)
        n = np.where((length > 1e-8)[:, None], n / np.where(length > 1e-8, length, 1.0)[:, None], np.array([1.0, 0.0, 0.0]))
        return phi, n @ R.T


def collide(P, X, dyn, collider, thickness, friction, h):
    phi, n = collider.distance(P[dyn])
    pen = thickness - phi
    hit = pen > 0.0
    i, pen, n = dyn[hit], pen[hit], n[hit]
    P[i] += pen[:, None] * n
    if friction > 1e-4 and len(i):
        centre = np.array(getattr(collider, "now", collider.frame)[1])
        moving = np.array(collider.velocity) + np.cross(np.array(collider.spin), P[i] - centre)
        rel = (P[i] - X[i]) - moving * h
        t = rel - (rel * n).sum(1)[:, None] * n
        length = np.linalg.norm(t, axis=1)
        ok = length >= 1e-8
        P[i[ok]] -= (np.minimum(pen[ok] * friction, length[ok]) / length[ok])[:, None] * t[ok]


class Motion:
    """The part of the reference bone's move the cloth is carried by rigidly (p -> q p + t), and the turn left to inertia."""

    def __init__(self, q, t, fictitious=Vector()):
        self.q, self.t, self.fictitious = q, t, fictitious

    def __call__(self, p):
        return self.q @ p + self.t


def _effective(u, s):
    c = s * u
    return c if c <= 1.0 else min(max(s, 0.0), 1.0) * min(max(u, 0.0), 1.0)


def carried(old, new, lin, ang, s, l_old, space="ReferenceBone", component=Quaternion(), model="FullScale", fictitious=0.0, dt=0.0):
    """The engine's inertia transfer: old/new reference bone (world), linear and angular scales, solver velocity scale."""
    (r0, t0w), (r1, t1w) = old, new
    move = t1w - t0w
    q = r0.conjugated() @ r1
    axis, angle = q.to_axis_angle()
    if angle > math.pi:
        angle -= 2.0 * math.pi
    phi = axis * angle
    if space == "World":
        d, to_bone = move, r0.conjugated()
    elif space == "Component":
        d, to_bone = component.conjugated() @ move, r0.conjugated() @ component
    else:
        d, to_bone = r0.conjugated() @ move, Quaternion()
    applied = Vector([_effective(u, s) * x for u, x in zip(lin, d)])
    phi_a = phi * _effective(ang, s)
    D = to_bone @ (d - applied)
    removed = phi - phi_a
    rd = Quaternion(removed.normalized() if removed.length > 1e-4 else Vector((1.0, 0.0, 0.0)), removed.length)
    t0 = t0w - l_old
    g = r0 @ rd @ r0.conjugated()
    spin = {"FullScale": phi, "AppliedScale": phi_a, "Legacy": removed}.get(model, Vector())
    omega = (r1 @ spin) * (min(2.0, fictitious) / dt) if dt > 0.0 and model != "None" else Vector()
    return Motion(g, t0 + r0 @ D - g @ t0, omega)


def _edges(triangles, dynamic):
    seen, out = set(), []
    for t in triangles:
        for a, b in ((t[0], t[1]), (t[0], t[2]), (t[1], t[2])):
            key = (min(a, b), max(a, b))
            if key not in seen:
                seen.add(key)
                if dynamic[key[0]] or dynamic[key[1]]:
                    out.append(key)
    return out


def _elements(triangles, dynamic):
    """Pairs of triangles over a shared edge as (e0, e1, o_a, o_b), the unflipped side first."""
    edges = {}
    for t in triangles:
        for a, b, o in ((t[0], t[1], t[2]), (t[1], t[2], t[0]), (t[2], t[0], t[1])):
            edges.setdefault((min(a, b), max(a, b)), []).append((o, a > b))
    out = []
    for (e0, e1), opposite in edges.items():
        for i in range(len(opposite)):
            for j in range(i + 1, len(opposite)):
                x, y = opposite[i], opposite[j]
                if x[1] != y[1]:
                    first, second = (x, y) if not x[1] else (y, x)
                else:
                    first, second = (x, y) if x[0] < y[0] else (y, x)
                quad = (e0, e1, first[0], second[0])
                if any(dynamic[v] for v in quad):
                    out.append(quad)
    return out


def _areas(triangles, positions, w):
    seen, tri, bary, rest = set(), [], [], []
    for t in triangles:
        key = tuple(sorted(t))
        if key in seen or not any(w[v] > 0.0 for v in key):
            continue
        seen.add(key)
        x = [positions[v] for v in key]
        bs = []
        for i in range(3):
            j, k = (i + 1) % 3, (i + 2) % 3
            edge = x[k] - x[j]
            bs.append(float(np.clip(np.dot(edge, x[k] - x[i]) / max(np.dot(edge, edge), 1e-30), 0.0, 1.0)))
        pick = 0
        if abs(bs[2] - 0.5) < abs(bs[0] - 0.5) and abs(bs[2] - 0.5) < abs(bs[1] - 0.5):
            pick = 2
        elif abs(bs[1] - 0.5) < abs(bs[0] - 0.5) and abs(bs[1] - 0.5) < abs(bs[2] - 0.5):
            pick = 1
        i1, i2, i3 = key[pick], key[(pick + 1) % 3], key[(pick + 2) % 3]
        b = bs[pick]
        if w[i1] + (1.0 - b) * w[i3] + b * w[i2] < 1e-8:
            continue
        q = positions[i3] + b * (positions[i2] - positions[i3])
        tri.append((i1, i2, i3))
        bary.append(b)
        rest.append(float(np.linalg.norm(positions[i1] - q)))
    return np.array(tri, np.int64).reshape(-1, 3), np.array(bary), np.array(rest)


class ClothSim:
    """One clothing asset; rest: the skeleton's reference-pose component transforms by bone."""

    def __init__(self, cloth, rest, state=None, root=None):
        self.cloth, self.rest = cloth, rest
        settings = (cloth.get("by_state") or {}).get(state or "OnGround_Standing") or {}
        self.config = dict(cloth["config"], **{k: v for k, v in settings.items() if k in cloth["config"]})
        self.shared = dict(cloth["shared"], **{k: v for k, v in settings.items() if k in cloth["shared"]})
        self.fluid = settings.get("FluidDensity", FLUID_DENSITY)
        self.wind = None        # the runner's custom wind this frame (the cloth's world, cm/s): air its drag and lift feel
        self.state_gravity = {"gravity": settings["gravity"], "joint": settings["joint"]} if "gravity" in settings else None
        self.teleport_rotation = settings.get("teleport_rotation", 0.0)
        self.teleport_distance = settings.get("teleport_distance", TELEPORT_DISTANCE)
        self.root = root or next(iter(rest))
        self.reference = cloth["reference"] if cloth["reference"] in rest else self.root
        x = np.array(cloth["positions"], np.float64)
        self.n = n = len(x)
        self.bind, self.bind_normals = x, np.array(cloth["normals"] or [[0.0, 0.0, 1.0]] * n, np.float64)
        self.tri = np.array(cloth["triangles"], np.int64).reshape(-1, 3)
        md = cloth["maps"].get("MaxDistance")
        self.md = np.array(md, np.float64) if md is not None else np.zeros(n)
        self.dynamic = self.md >= 0.1 if md is not None else np.zeros(n, bool)
        self.dyn = np.where(self.dynamic)[0]
        self.mass = self._masses(x)
        self.w = np.where(self.dynamic, 1.0 / self.mass, 0.0)
        # a bone the armature lacks skins to the root, as the engine maps an unknown bone
        bones = sorted({b if b in rest else self.root for inf in cloth["influences"] for b, _ in inf})
        self.bones = bones
        index = {b: i for i, b in enumerate(bones)}
        self.inf_bone = np.zeros((n, 12), np.int64)
        self.inf_w = np.zeros((n, 12))
        for v, inf in enumerate(cloth["influences"]):
            for k, (b, wt) in enumerate(inf[:12]):
                self.inf_bone[v, k], self.inf_w[v, k] = index[b if b in rest else self.root], wt
        self._build(x)
        self.colliders = [(s, None) for s in cloth["colliders"] if s["bone"] in rest]
        self.frames = 0
        self.dt = MAX_DT

    def _masses(self, x):
        c = self.config
        a = np.cross(x[self.tri[:, 1]] - x[self.tri[:, 0]], x[self.tri[:, 2]] - x[self.tri[:, 0]])
        area = 0.5 * np.linalg.norm(a, axis=1)
        per = np.zeros(self.n)
        for k in range(3):
            np.add.at(per, self.tri[:, k], area / 3.0)
        mode = c["MassMode"]
        if mode == "UniformMass":
            m = np.where(per > 0.0, c["UniformMass"], 0.0)
        elif mode == "TotalMass":
            total = area.sum()
            m = per * c["TotalMass"] / total if total > 0.0 else np.ones(self.n)
        else:
            m = per * c["Density"] / 10000.0
        return np.maximum(m, max(c["MinPerParticleMass"], 1e-8))

    def _build(self, x):
        c, cloth, dyn = self.config, self.cloth, self.dynamic
        tris = self.tri.tolist()
        self.springs = []
        edges = _edges(tris, dyn)
        if any(c["EdgeStiffnessWeighted"]):
            self.springs.append(self._spring_set(edges, x, "EdgeStiffness", c["EdgeStiffnessWeighted"]))
        quads = _elements(tris, dyn)
        self.elements = None
        if any(c["BendingStiffnessWeighted"]):
            if c["bUseBendingElements"]:
                q = np.array(quads, np.int64).reshape(-1, 4)
                rest = _angles(x[q[:, 0]], x[q[:, 1]], x[q[:, 2]], x[q[:, 3]])[0] if len(q) else np.zeros(0)
                flat = _authored(cloth, "FlatnessRatio", c["FlatnessRatio"], q[:, :2], quantized=False, clamp=False)
                rest = np.clip((1.0 - np.clip(flat, 0.0, 1.0)) * rest, -math.pi, math.pi)
                self.elements = (q, rest, _authored(cloth, "BendingStiffness", c["BendingStiffnessWeighted"], q[:, :2]),
                                 colour(q.tolist(), dyn))
            else:
                seen, cross = set(), []
                for e0, e1, a, b in quads:
                    key = (min(a, b), max(a, b))
                    if key not in seen and (dyn[a] or dyn[b]):
                        seen.add(key)
                        cross.append(key)
                self.springs.append(self._spring_set(cross, x, "BendingStiffness", c["BendingStiffnessWeighted"]))
        self.area = None
        if any(c["AreaStiffnessWeighted"]):
            tri, bary, rest = _areas(tris, x, np.where(dyn, 1.0, 0.0))
            self.area = (tri, bary, rest, _authored(cloth, "AreaStiffness", c["AreaStiffnessWeighted"], tri), colour(tri.tolist(), dyn))
        maps = cloth["maps"]
        self.backstop = (np.array(maps["BackstopRadius"]), np.array(maps["BackstopDistance"])) \
            if "BackstopRadius" in maps and "BackstopDistance" in maps else None
        drive = c["AnimDriveStiffness"]
        self.drive = (_authored(cloth, "AnimDriveStiffness", drive, np.arange(self.n)),
                      _authored(cloth, "AnimDriveDamping", c["AnimDriveDamping"], np.arange(self.n))) if drive[0] > 0 or drive[1] > 0 else None
        self.tethers = []
        if any(c["TetherStiffness"]):
            for batch in cloth["tethers"]:
                b = np.array(batch, np.float64).reshape(-1, 3)
                s, e = b[:, 0].astype(np.int64), b[:, 1].astype(np.int64)
                scale = np.clip(_authored(cloth, "TetherScale", c["TetherScale"], e, stiff=False), 0.01, 10.0) \
                    if "TetherScale" in maps else np.full(len(e), min(max(c["TetherScale"][0], 0.01), 10.0))
                self.tethers.append((s, e, b[:, 2] * scale, _authored(cloth, "TetherStiffness", c["TetherStiffness"], e)))
        # aerodynamics per triangle, clamped to [0, 10]
        cd = np.clip(_authored(cloth, "Drag", c["Drag"], self.tri, quantized=False), 0.0, 10.0)
        cl = np.clip(_authored(cloth, "Lift", c["Lift"], self.tri, quantized=False), 0.0, 10.0)
        cdo = np.clip(_authored(cloth, "OuterDrag", c["OuterDrag"], self.tri, quantized=False), 0.0, 10.0) if c["bEnableOuterDrag"] else cd
        clo = np.clip(_authored(cloth, "OuterLift", c["OuterLift"], self.tri, quantized=False), 0.0, 10.0) if c["bEnableOuterLift"] else cl
        pressure = _authored(cloth, "Pressure", c["Pressure"], self.tri, quantized=False)
        on = any(np.any(v != 0.0) for v in (cd, cl, cdo, clo, pressure)) and not c["bUsePointBasedWindModel"]
        self.aero = (cd, cl, cdo, clo, pressure) if on else None

    def _spring_set(self, pairs, x, name, pair):
        p = np.array(pairs, np.int64).reshape(-1, 2)
        rest = np.linalg.norm(x[p[:, 0]] - x[p[:, 1]], axis=1)
        return p[:, 0], p[:, 1], rest, _authored(self.cloth, name, pair, p), colour(p.tolist(), self.dynamic)

    def bones_needed(self):
        joint = [self.state_gravity["joint"]] if self.state_gravity else []
        return set(self.bones) | {self.reference, self.root} | {s["bone"] for s, _ in self.colliders} | set(joint)

    # --- per frame ---
    def _world(self, cs, name, component):
        return compose(cs[name], component)

    def _skin(self, cs, component):
        """The simulation mesh skinned to the pose, in local space (world axes, origin on the component)."""
        R = np.zeros((len(self.bones), 3, 3))
        T = np.zeros((len(self.bones), 3))
        for i, b in enumerate(self.bones):
            (qc, tc), (qr, tr) = cs[b], self.rest[b]
            m = np.array((qc @ qr.conjugated()).to_matrix())
            R[i], T[i] = m, np.array(tc) - m @ np.array(tr)
        Rb, w = R[self.inf_bone], self.inf_w[:, :, None]
        p = (w * (np.einsum("nkij,nj->nki", Rb, self.bind) + T[self.inf_bone])).sum(1)
        nrm = (w * np.einsum("nkij,nj->nki", Rb, self.bind_normals)).sum(1)
        to_world = np.array(component[0].to_matrix())
        p, nrm = p @ to_world.T, nrm @ to_world.T
        length = np.linalg.norm(nrm, axis=1)
        nrm = np.where((length > 1e-12)[:, None], nrm / np.where(length > 1e-12, length, 1.0)[:, None], np.array([1.0, 0.0, 0.0]))
        return p, nrm

    def _gravity(self, cs, component):
        if self.state_gravity is not None:
            joint = cs.get(self.state_gravity["joint"])
            g = (joint[0] if joint else Quaternion()) @ Vector(self.state_gravity["gravity"])
            return np.array(component[0] @ g)
        c = self.config
        return np.array(c["Gravity"] if c["bUseGravityOverride"] else [x * c["GravityScale"] for x in WORLD_GRAVITY])

    def _teleport(self, cs, component):
        root = self._world(cs, self.root, component)
        last, self.last_root = getattr(self, "last_root", None), root
        if last is None:
            return False
        if (root[1] - last[1]).length > self.teleport_distance:
            return True
        if self.teleport_rotation > 0.0:
            m = (root[0] @ last[0].conjugated()).to_matrix()
            return (m[0][0] + m[1][1] + m[2][2] - 1.0) / 2.0 < math.cos(math.radians(self.teleport_rotation))
        return False

    def step(self, raw_dt, cs, component):
        """One game frame: the particles' world positions (cm) and render normals, each (n, 3)."""
        delta = min(raw_dt, MAX_DT)
        self.dt = dt = self.dt + SMOOTHING * (delta - self.dt)
        speed = delta / raw_dt * dt / delta if raw_dt > 0.0 else 0.0
        L = np.array(component[1])
        ref = self._world(cs, self.reference, component)
        anim, anim_n = self._skin(cs, component)
        reset = self.frames == 0 or self._teleport(cs, component)
        self.frames += 1
        if reset:
            self.L_old, self.ref_old = L, ref
            self.X, self.P, self.anim_old, self.V = anim.copy(), anim.copy(), anim.copy(), np.zeros((self.n, 3))
            self.anim_n_old = anim_n.copy()
            motion = carried(ref, ref, [1.0] * 3, 1.0, 1.0, Vector(self.L_old))
        else:
            c = self.config
            motion = carried(self.ref_old, ref, c["LinearVelocityScale"], c["AngularVelocityScale"], speed, Vector(self.L_old),
                             c["VelocityScaleSpace"], component[0], c["FictitiousForcesModel"], c["FictitiousAngularScale"], dt)
        shift = L - self.L_old
        G, Gt = np.array(motion.q.to_matrix()), np.array(motion.t)
        self.X = self.X @ G.T + Gt - shift
        self.V = self.V @ G.T
        self.anim_old = self.anim_old @ G.T + Gt - shift
        anim_vel = (anim - self.anim_old) / dt
        frames = []
        for shape, collider in self.colliders:
            frame = compose(cs[shape["bone"]], component)
            frame = (frame[0], frame[1] - Vector(L))
            if collider is None or reset:
                collider = Collider(shape, frame)
            else:
                old = collider.frame
                collider.old = (motion.q @ old[0], motion(old[1]) - Vector(shift))
                collider.frame = frame
                if hasattr(collider, "now"):
                    del collider.now
            frames.append((shape, collider))
        self.colliders = frames
        omega = np.array(motion.fictitious)
        ref_loc = np.array(ref[1]) - L
        self._substeps(dt, anim, anim_n, anim_vel, self._gravity(cs, component), omega, ref_loc)
        self.L_old, self.ref_old = L, ref
        self.anim_old, self.anim_n_old = anim, anim_n
        return self.X + L, self.normals()

    def _substeps(self, dt, anim, anim_n, anim_vel, g, omega, ref_loc):
        c, s = self.config, self.shared
        count = max(1, int(s["SubdivisionCount"]))
        h = dt / count
        iterations = int(min(max(math.floor(60.0 * dt * s["IterationCount"] + 0.5), 1), s["MaxIterationCount"]))
        damping = min(max(c["DampingCoefficient"], 0.0), 1.0)
        if damping > 1.0 - 1e-4:
            dpow = dint = 0.0
        elif damping > 1e-8:
            k = math.log(1.0 - damping) * 60.0
            dpow = math.exp(k * h)
            dint = (dpow - 1.0) / k
        else:
            dint = h
        dyn, w = self.dyn, self.w
        for i in range(count):
            alpha = (i + 1) / count
            target = alpha * anim + (1.0 - alpha) * self.anim_old
            target_n = alpha * anim_n + (1.0 - alpha) * self.anim_n_old
            target_n /= np.maximum(np.linalg.norm(target_n, axis=1), 1e-12)[:, None]
            X, V = self.X, self.V
            a = np.tile(g, (self.n, 1))
            if np.any(np.abs(omega) > 1e-4):
                a -= np.cross(omega, np.cross(omega, X - ref_loc))
            if self.aero is not None:
                a += self._aero(X, V)
            local = self._local_damping(X, V) if c["LocalDampingCoefficient"] > 0.0 else None
            P = X.copy()
            Vd = V[dyn] + a[dyn] * h
            if local is not None:
                xcm, vcm, spin = local
                cl = c["LocalDampingCoefficient"]
                Vd += cl * (vcm - Vd) + cl * np.cross(X[dyn] - xcm, spin)
            P[dyn] = X[dyn] + Vd * dint
            V[dyn] = Vd
            kin = ~self.dynamic
            P[kin], V[kin] = target[kin], anim_vel[kin]
            for _, collider in self.colliders:
                collider.at(alpha, h)
            for start, end, length, value in self.tethers:
                k = stiffness(value, h, 1)
                d = P[start] - P[end]
                dist = np.linalg.norm(d, axis=1)
                pull = np.where(dist > 1e-8, k * np.maximum(dist - length, 0.0) / np.where(dist > 1e-8, dist, 1.0), 0.0)
                P[end] += pull[:, None] * d * self.dynamic[end][:, None]
            springs_k = [(a_, b_, r_, stiffness(v_, h, iterations), g_) for a_, b_, r_, v_, g_ in self.springs]
            area_k = (self.area[0], self.area[1], self.area[2], stiffness(self.area[3], h, iterations), self.area[4]) if self.area else None
            elem_k = (self.elements[0], self.elements[1], stiffness(self.elements[2], h, iterations), self.elements[3]) if self.elements else None
            drive_k = (stiffness(self.drive[0], h, iterations), stiffness(self.drive[1], h, iterations)) if self.drive else None
            thickness, friction = c["CollisionThickness"], c["FrictionCoefficient"]
            for _ in range(iterations):
                for a_, b_, r_, k_, g_ in springs_k:
                    springs(P, w, g_, a_, b_, r_, k_)
                if elem_k is not None:
                    bending_elements(P, w, elem_k[3], elem_k[0], elem_k[1], elem_k[2])
                if area_k is not None:
                    area_springs(P, w, area_k[4], area_k[0], area_k[1], area_k[2], area_k[3])
                if self.cloth["maps"].get("MaxDistance") is not None:
                    max_distance(P, target, self.md, dyn)
                if self.backstop is not None:
                    backstop(P, target, target_n, self.backstop[0], self.backstop[1], dyn, c["bUseLegacyBackstop"])
                if drive_k is not None:
                    P[dyn] -= drive_k[0][dyn][:, None] * (P[dyn] - target[dyn])
                    P[dyn] -= drive_k[1][dyn][:, None] * ((P[dyn] - X[dyn]) - anim_vel[dyn] * h)
                for _, collider in self.colliders:
                    collide(P, X, dyn, collider, thickness, friction, h)
            self.V = (P - X) / h
            self.X = P

    def _aero(self, X, V):
        cd, cl, cdo, clo, pressure = self.aero
        t = self.tri
        x0, x1, x2 = X[t[:, 0]], X[t[:, 1]], X[t[:, 2]]
        N = np.cross(x2 - x0, x1 - x0)
        a2 = np.linalg.norm(N, axis=1)
        N = N / np.where(a2 > 1e-12, a2, 1.0)[:, None]
        v = -(V[t[:, 0]] + V[t[:, 1]] + V[t[:, 2]]) / 3.0       # the air against each triangle
        if self.wind is not None:
            v = v + self.wind
        vn = (v * N).sum(1)
        rho = 0.25 * self.fluid
        vv = (v * v).sum(1)
        front = vn >= 0.0
        F = np.where(front[:, None], ((cd - cl) * vn)[:, None] * v + (cl * vv)[:, None] * N,
                     ((clo - cdo) * vn)[:, None] * v - (clo * vv)[:, None] * N) * (rho * a2)[:, None]
        F += ((a2 / 2.0) * (pressure / 100.0))[:, None] * N
        out = np.zeros((self.n, 3))
        for k in range(3):
            np.add.at(out, t[:, k], F)
        return out * self.w[:, None]

    def _local_damping(self, X, V):
        d = self.dyn
        m = self.mass[d][:, None]
        total = m.sum()
        xcm, vcm = (m * X[d]).sum(0) / total, (m * V[d]).sum(0) / total
        r = X[d] - xcm
        L = np.cross(r, m * V[d]).sum(0)
        rr = (r * r).sum(1)
        inertia = (m[:, 0, None, None] * (rr[:, None, None] * np.eye(3) - r[:, :, None] * r[:, None, :])).sum(0)
        det = np.linalg.det(inertia)
        spin = np.linalg.solve(inertia, L) if np.isfinite(det) and det >= 1e-8 else np.zeros(3)
        return xcm, vcm, spin

    def normals(self):
        """Render normals: the unit face normals around each vertex, summed (render winding)."""
        t, X = self.tri, self.X
        f = np.cross(X[t[:, 1]] - X[t[:, 0]], X[t[:, 2]] - X[t[:, 0]])
        f /= np.maximum(np.linalg.norm(f, axis=1), 1e-12)[:, None]
        out = np.zeros((self.n, 3))
        for k in range(3):
            np.add.at(out, t[:, k], f)
        return out / np.maximum(np.linalg.norm(out, axis=1), 1e-12)[:, None]
