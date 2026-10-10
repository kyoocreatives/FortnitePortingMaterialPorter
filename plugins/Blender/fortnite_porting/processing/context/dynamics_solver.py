"""Fortnite's AnimDynamics bodies stepped as the engine steps them, in the game's numbers (cm, its axes, mathutils
quaternions). No bpy: the bake hands it component transforms and keys what it returns."""
import math

from mathutils import Matrix, Quaternion, Vector

MAX = 3.4028234663852886e38
BIAS = 0.3              # the engine's joint bias
DAMPING = 0.7           # momentum fraction lost per second unless overridden
MAX_DT = 1.0 / 30.0     # the engine's longest step
CORIOLIS, CENTRIFUGAL, EULER = 0.5, 1.0, 1.0
GRAVITY = 980.0
AXES = {"X": Vector((1.0, 0.0, 0.0)), "Y": Vector((0.0, 1.0, 0.0)), "Z": Vector((0.0, 0.0, 1.0))}
ZERO = Vector((0.0, 0.0, 0.0))
SWING = {"X": ("Y", "Z"), "Y": ("X", "Z"), "Z": ("X", "Y")}
DEFAULT_BODY = {"bone": None, "box": [10.0, 10.0, 10.0], "offset": [0.0, 0.0, 0.0],
                "lin_types": ["Limited"] * 3, "lin_min": [0.0] * 3, "lin_max": [0.0] * 3,
                "angular": "Angular", "twist": "X", "target_axis": "X", "cone": 0.0,
                "ang_min": [0.0] * 3, "ang_max": [0.0] * 3, "target": [0.0] * 3, "collision": "CoM", "radius": 10.0}


def compose(a, b):
    """The engine's a*b on (rotation, position): a expressed in b's frame."""
    return b[0] @ a[0], b[0] @ a[1] + b[1]


def relative(t, r):
    """t in r's frame (t * r^-1)."""
    inv = r[0].conjugated()
    return inv @ t[0], inv @ (t[1] - r[1])


def safe_normal(v):
    s = v.length_squared
    return v.copy() if s == 1.0 else (ZERO.copy() if s < 1e-8 else v / math.sqrt(s))


def nearly_zero(v, tol=1e-4):
    return abs(v.x) <= tol and abs(v.y) <= tol and abs(v.z) <= tol


def find_between(a, b):
    """Shortest turn from a to b, the engine's way: antiparallel picks a fixed side, a zero vector gives identity."""
    norm = math.sqrt(a.length_squared * b.length_squared)
    w = norm + a.dot(b)
    if w >= 1e-6 * norm:
        c = a.cross(b)
    else:
        w = 0.0
        c = a.cross(Vector((0.0, 1.0, 0.0)) if abs(a.x) > abs(a.y) and abs(a.x) > abs(a.z) else Vector((-1.0, 0.0, 0.0)))
    q = (w, c.x, c.y, c.z)
    s = sum(x * x for x in q)
    if s < 1e-8:
        return Quaternion()
    k = 1.0 / math.sqrt(s)
    return Quaternion([x * k for x in q])


def axis_angle(q):
    angle = 2.0 * math.acos(max(-1.0, min(1.0, q.w)))
    v = Vector((q.x, q.y, q.z))
    return (AXES["X"].copy() if v.length_squared < 1e-8 else v.normalized()), angle


def box_inertia(extents):
    """Per-unit-mass inertia of a solid box with these full edge lengths (a zero box becomes a unit cube)."""
    a, b, c = extents
    if a * a + b * b + c * c <= 1e-8:
        a = b = c = 1.0
    return Vector(((b * b + c * c) / 12.0, (a * a + c * c) / 12.0, (a * a + b * b) / 12.0))


def _world_inverse(rot, inv_local):
    # the engine turns the tensor as R^T I^-1 R (not R I^-1 R^T); the same for cubes only
    r = rot.to_matrix()
    return r.transposed() @ inv_local @ r


class Body:
    """A mass-1 box: momentum is velocity. `w` caches the spin (inverse inertia times angular momentum) while the
    constraints iterate, so each pass updates it instead of recomputing it."""
    __slots__ = ("pos", "rot", "lin", "ang", "inv_local", "inv_diag", "inv_world", "inertia", "lin_damp", "ang_damp",
                 "use_override", "override", "gravity_scale", "collision", "radius", "next_pos", "next_rot", "w")

    def __init__(self, pos, rot, extents):
        moments = box_inertia(extents)
        self.pos, self.rot = pos.copy(), rot.copy()
        self.lin, self.ang = ZERO.copy(), ZERO.copy()
        self.inertia = Matrix.Diagonal(moments)
        self.inv_diag = Vector([1.0 / m for m in moments])
        self.inv_local = Matrix.Diagonal(self.inv_diag)
        self.inv_world = self.inv_local.copy()      # unturned until the first step, as in the engine
        self.lin_damp = self.ang_damp = None
        self.use_override, self.override, self.gravity_scale = False, ZERO.copy(), 1.0
        self.collision, self.radius = "CoM", 0.0
        self.next_pos, self.next_rot = self.pos, self.rot
        self.w = ZERO.copy()

    def at(self, local):
        return self.rot @ local + self.pos

    def spin(self):
        return self.inv_world @ self.ang

    def push(self, point, impulse):
        self.lin += impulse
        self.ang += point.cross(impulse)


class Linear:
    """Keeps a point pair's speed along a normal at a target, impulses clamped in total."""
    __slots__ = ("b0", "b1", "r0", "r1", "n", "target", "unbiased", "lo", "hi", "k", "sum", "c0", "c1", "w0", "w1")

    def __init__(self, b0, b1, p0, p1, n, target, unbiased=0.0, lo=-MAX, hi=MAX):
        self.b0, self.b1, self.n = b0, b1, n
        self.target, self.unbiased, self.lo, self.hi, self.sum = target, unbiased, min(lo, hi), max(lo, hi), 0.0
        self.r0 = b0.rot @ p0 if b0 else p0
        self.r1 = b1.rot @ p1 if b1 else p1
        d = 0.0
        if b0:
            d += 1.0 + (b0.inv_world @ self.r0.cross(n)).cross(self.r0).dot(n)
        if b1:
            d += 1.0 + (b1.inv_world @ self.r1.cross(n)).cross(self.r1).dot(n)
        self.k = 1.0 / d

    def prepare(self):
        """Lever terms for the passes: (w x r).n = w.(r x n), and how an impulse turns each body's spin."""
        n = self.n
        if self.b0:
            self.c0 = self.r0.cross(n)
            self.w0 = self.b0.inv_world @ self.c0
        if self.b1:
            self.c1 = self.r1.cross(n)
            self.w1 = self.b1.inv_world @ self.c1

    def iterate(self, dt):
        b0, b1, n = self.b0, self.b1, self.n
        rel = 0.0
        if b1:
            rel += b1.w.dot(self.c1) + b1.lin.dot(n)
        if b0:
            rel -= b0.w.dot(self.c0) + b0.lin.dot(n)
        impulse = (-self.target - rel) * self.k
        impulse = min(self.hi * dt - self.sum, impulse)
        impulse = max(self.lo * dt - self.sum, impulse)
        if b0:
            b0.lin -= n * impulse
            b0.ang -= self.c0 * impulse
            b0.w -= self.w0 * impulse
        if b1:
            b1.lin += n * impulse
            b1.ang += self.c1 * impulse
            b1.w += self.w1 * impulse
        self.sum += impulse

    def unbias(self):
        self.target = self.unbiased


class Angular:
    """Keeps the relative spin about an axis at a target, torques clamped in total."""
    __slots__ = ("b0", "b1", "axis", "target", "lo", "hi", "k", "sum", "w0", "w1")

    def __init__(self, b0, b1, axis, target=0.0, lo=-MAX, hi=MAX):
        self.b0, self.b1, self.axis, self.target, self.lo, self.hi, self.sum = b0, b1, axis, target, lo, hi, 0.0
        d = 0.0
        if b0:
            d += axis.dot(b0.inv_world @ axis)
        if b1:
            d += axis.dot(b1.inv_world @ axis)
        self.k = 1.0 / d if d else 0.0

    def prepare(self):
        if self.b0:
            self.w0 = self.b0.inv_world @ self.axis
        if self.b1:
            self.w1 = self.b1.inv_world @ self.axis

    def iterate(self, dt):
        if self.target == -MAX:
            return
        b0, b1, axis = self.b0, self.b1, self.axis
        spin = (b1.w.dot(axis) if b1 else 0.0) - (b0.w.dot(axis) if b0 else 0.0)
        torque = (self.target - spin) * self.k
        torque = min(torque, self.hi * dt - self.sum)
        torque = max(torque, self.lo * dt - self.sum)
        if b0:
            b0.ang -= axis * torque
            b0.w -= self.w0 * torque
        if b1:
            b1.ang += axis * torque
            b1.w += self.w1 * torque
        self.sum += torque

    def unbias(self):
        # a one-sided limit keeps its allowed approach speed
        self.target = 0.0 if self.lo < 0 else min(self.target, 0.0)


class Spring:
    __slots__ = ("b0", "b1", "a0", "a1", "k_lin", "k_ang", "target", "axis", "frame", "linear", "angular")

    def __init__(self, b0, a0, b1, k_lin, k_ang, target, axis, frame, linear, angular):
        self.b0, self.a0, self.b1, self.a1 = b0, a0, b1, ZERO.copy()
        self.k_lin, self.k_ang, self.target, self.axis, self.frame = k_lin, k_ang, target, axis, frame
        self.linear, self.angular = linear, angular

    def apply(self, dt):
        if self.linear:
            d = self.b1.at(self.a1) - (self.b0.at(self.a0) if self.b0 else self.a0)
            impulse = safe_normal(d) * (-self.k_lin * d.length * dt)
            if self.b0:
                self.b0.push(self.b0.rot @ self.a0, -impulse)
            self.b1.push(self.b1.rot @ self.a1, impulse)
        if self.angular and self.target.length_squared > 0.0:
            # a zero target turns nothing: the engine's turn to a zero vector is the identity
            axis, angle = axis_angle(find_between(self.b1.rot @ AXES[self.axis], self.frame @ self.target))
            added = axis * (-self.k_ang * angle) * dt
            if self.b0:
                self.b0.ang += added
            self.b1.ang -= added


def nailed(dt, out, b0, p0, b1, p1):
    gap = (b1.at(p1) - (b0.at(p0) if b0 else p0)) / dt
    for k, n in enumerate(AXES.values()):        # the axes are only read
        out.append(Linear(b0, b1, p0, p1, n, gap[k]))


def along(dt, out, b0, p0, b1, p1, axis, lo, hi):
    d = (b1.at(p1) - (b0.at(p0) if b0 else p0)).dot(axis)
    if abs(lo - hi) < 1e-8:
        out.append(Linear(b0, b1, p0, p1, axis, d / dt, d / dt))
    else:
        out.append(Linear(b0, b1, p0, p1, axis, (d - lo) / dt, (d - lo) / dt, 0.0, MAX))
        out.append(Linear(b0, b1, p0, p1, axis, (d - hi) / dt, (d - hi) / dt, -MAX, 0.0))


def angular_range(dt, out, b0, b1, frame, twist, lo_deg, hi_deg, bias):
    """Swing limits on the two axes off the twist axis, measured in the joint frame and pushed about the body's own
    axes; twist always held at zero, softly."""
    f0 = b0.rot @ frame if b0 else frame
    f1 = b1.rot
    rel = f0.conjugated() @ f1
    swing = find_between(AXES[twist], rel @ AXES[twist])
    turn = swing.conjugated() @ rel
    if swing.w < 0.0:
        swing.negate()
    if turn.w < 0.0:
        turn.negate()
    for slot, name in enumerate(SWING[twist]):
        i = "XYZ".index(name)
        lo, hi = math.radians(lo_deg[i]), math.radians(hi_deg[i])
        s, axis = getattr(swing, name.lower()), f1 @ AXES[name]
        if lo == hi:
            # the engine biases the second axis and compares it with the raw angle
            target = 2.0 * (-s + math.sin(lo / 2.0)) / dt if slot == 0 else bias * 2.0 * (-s + lo) / dt
            out.append(Angular(b0, b1, axis, target))
        elif hi - lo < 2.0 * math.pi:
            out.append(Angular(b0, b1, axis, 2.0 * (-s + math.sin(lo / 2.0)) / dt, 0.0))
            out.append(Angular(b0, b1, -axis, 2.0 * (s - math.sin(hi / 2.0)) / dt, 0.0))
    out.append(Angular(b0, b1, f1 @ AXES[twist], bias * 2.0 * -getattr(turn, twist.lower()) / dt))


def cone(dt, out, b0, normal0, b1, limit_deg, bias):
    n0 = b0.rot @ normal0 if b0 else normal0
    n1 = b1.rot @ AXES["X"]
    angle = math.acos(max(0.0, min(1.0, n0.dot(n1))))
    axis = safe_normal(n1.cross(n0))
    if axis.length_squared == 0.0:      # aligned: the engine's limit has no axis and pushes nothing
        return
    target = bias if limit_deg == 0.0 else (angle - math.radians(limit_deg)) / dt
    out.append(Angular(b0, b1, axis, target, 0.0 if limit_deg > 0.0 else -MAX, MAX))


def planar(dt, out, body, plane):
    normal = plane[0] @ AXES["Z"]
    d = normal.dot(body.pos) - normal.dot(plane[1])
    if body.collision != "CoM":
        d -= body.radius
    out.append(Linear(None, body, plane[1], ZERO.copy(), safe_normal(normal), d / dt, d / dt, 0.0, MAX))


def spherical(dt, out, body, center, radius, inner):
    gap = body.pos - center
    d = gap.length - radius
    if body.collision != "CoM":
        d += body.radius if inner else -body.radius
    out.append(Linear(None, body, center, ZERO.copy(), safe_normal(gap), d / dt, d / dt,
                      -MAX if inner else 0.0, 0.0 if inner else MAX))


def _init_velocity(body, dt, down):
    lin_d = DAMPING if body.lin_damp is None else body.lin_damp
    ang_d = DAMPING if body.ang_damp is None else body.ang_damp
    # damping above 1 is a NaN in the engine; the body stays still instead
    body.lin *= math.pow(max(1.0 - lin_d, 0.0), dt)
    body.ang *= math.pow(max(1.0 - ang_d, 0.0), dt)
    body.lin += (body.override if body.use_override else down * GRAVITY * body.gravity_scale) * dt
    body.inv_world = _world_inverse(body.rot, body.inv_local)


def _diff(q, inv_diag, ang):
    qn = q.normalized()
    # R^T I^-1 R applied as rotations: turn back by q, scale by the local inverse inertia, turn forward
    w = qn.conjugated() @ (inv_diag * (qn @ ang)) * 0.5
    return Quaternion((0.0, w.x, w.y, w.z)) @ qn


def _rk4(q, inv_diag, ang, dt):
    d1 = _diff(q, inv_diag, ang)
    d2 = _diff(q + d1 * (dt / 2), inv_diag, ang)
    d3 = _diff(q + d2 * (dt / 2), inv_diag, ang)
    d4 = _diff(q + d3 * dt, inv_diag, ang)
    return (q + d1 * (dt / 6) + d2 * (dt / 3) + d3 * (dt / 3) + d4 * (dt / 6)).normalized()


def _passes(body, linear, angular, dt, count):
    """Linear.iterate and Angular.iterate for a node with one body (every row acts on it alone), in one loop: the
    same arithmetic without the per-row calls."""
    lin, ang, w = body.lin, body.ang, body.w
    lrows = [(c, c.n, c.c1, c.w1, c.k) for c in linear]
    arows = [(c, c.axis, c.w1, c.k) for c in angular]
    for _ in range(count):
        for c, n, c1, w1, k in lrows:
            impulse = (-c.target - (w.dot(c1) + lin.dot(n))) * k
            top = c.hi * dt - c.sum
            if impulse > top:
                impulse = top
            bottom = c.lo * dt - c.sum
            if impulse < bottom:
                impulse = bottom
            lin += n * impulse
            ang += c1 * impulse
            w += w1 * impulse
            c.sum += impulse
        for c, axis, w1, k in arows:
            target = c.target
            if target == -MAX:
                continue
            torque = (target - w.dot(axis)) * k
            top = c.hi * dt - c.sum
            if torque > top:
                torque = top
            bottom = c.lo * dt - c.sum
            if torque < bottom:
                torque = bottom
            ang += axis * torque
            w += w1 * torque
            c.sum += torque


def physics_update(dt, bodies, linear, angular, springs, down, force, acc, ang_acc, ang_vel, pre, post):
    """One engine step: forces, springs, biased passes, integrate, the bias taken back out of the momentum."""
    for b in bodies:
        _init_velocity(b, dt, down)
    if not nearly_zero(force):
        for b in bodies:
            b.lin += force * dt
    if not nearly_zero(acc):
        for b in bodies:
            b.lin += acc * dt
    if not (nearly_zero(ang_vel) and nearly_zero(ang_acc)):
        for b in bodies:        # the turning space's Coriolis, centrifugal and Euler forces
            push = CORIOLIS * 2.0 * ang_vel.cross(b.lin) + CENTRIFUGAL * ang_vel.cross(ang_vel.cross(b.pos)) \
                + EULER * ang_acc.cross(b.pos)
            b.lin -= push * dt
            b.ang -= (b.inertia @ ang_acc) * dt
    for s in springs:
        s.apply(dt)
    for b in bodies:
        b.w = b.inv_world @ b.ang
    for c in linear:
        c.prepare()
    for c in angular:
        c.prepare()
    single = bodies[0] if len(bodies) == 1 else None
    if single:
        _passes(single, linear, angular, dt, pre)
    else:
        for _ in range(pre):
            for c in linear:
                c.iterate(dt)
            for c in angular:
                c.iterate(dt)
    for b in bodies:
        b.next_pos = b.pos + b.lin * dt
        b.next_rot = _rk4(b.rot, b.inv_diag, b.ang, dt)
    for c in linear:
        c.unbias()
    for c in angular:
        c.unbias()
    if single:
        _passes(single, linear, angular, dt, post)
    else:
        for _ in range(post):
            for c in linear:
                c.iterate(dt)
            for c in angular:
                c.iterate(dt)
    for b in bodies:
        b.pos, b.rot = b.next_pos, b.next_rot
        b.inv_world = _world_inverse(b.rot, b.inv_local)


def chain_defs(node, parent_of):
    """The node's bodies from its bound bone down to its chain end, as the engine rebuilds them."""
    names, end = [node["bone"]], node.get("chain_end")
    if end:
        walk, below = end, []
        while walk and walk != node["bone"]:
            below.insert(0, walk)
            walk = parent_of.get(walk)
        if walk == node["bone"]:
            names += below
    defs = node["bodies"]
    if defs and defs[0]["bone"] == node["bone"] and defs[-1]["bone"] == end:
        return defs
    proto = defs[0] if len(defs) == 1 else DEFAULT_BODY
    kept = {d["bone"]: d for d in defs if d["bone"] in names}
    return [dict(kept.get(name) or proto, bone=name) for name in names]


class Sim:
    """One AnimDynamics node: evaluate() takes the pose reaching it (component transforms by bone name) and returns
    its bodies' bones."""

    def __init__(self, node, parent_of):
        self.node, self.defs, self.bodies, self.joints = node, chain_defs(node, parent_of), None, []
        self.prev_space, self.prev_spin = Quaternion(), ZERO.copy()

    def bones(self):
        n = self.node
        driving = [lim["bone"] for lim in n["planar"] + n["spherical"] if lim.get("bone")]
        rel = [n["relative_bone"]] if n["space"] == "BoneRelative" and n.get("relative_bone") else []
        return [d["bone"] for d in self.defs] + driving + rel

    def outputs(self):
        return [d["bone"] for d in self.defs]

    def _space(self, cs, root):
        space = self.node["space"]
        if space == "RootRelative":
            return cs[root]
        if space == "BoneRelative" and self.node.get("relative_bone") in cs:
            return cs[self.node["relative_bone"]]
        return Quaternion(), ZERO.copy()        # the component sits unturned at the origin in a bake

    def _space_spin(self, space_rot, dt):
        n = self.node
        if n["space_alpha"] == 0.0 or dt < 1e-8:
            return ZERO.copy(), ZERO.copy()
        if n["space"] == "World":
            return Vector(n["external_ang_vel"]), ZERO.copy()
        cur, prev = space_rot, self.prev_space
        r1 = -cur if cur.dot(prev) < 0.0 else cur
        w = ((r1 - prev) * (1.0 / dt)) @ prev.conjugated()
        spin = Vector((w.x, w.y, w.z)) * 2.0
        acc = (spin - self.prev_spin) / dt
        self.prev_spin, self.prev_space = spin, cur.copy()
        if spin.length > n["max_ang_vel"]:
            spin = spin.normalized() * n["max_ang_vel"]
        spin = spin + Vector(n["external_ang_vel"])
        if acc.length > n["max_ang_acc"]:
            acc = acc.normalized() * n["max_ang_acc"]
        inv = cur.conjugated()
        return (inv @ spin) * n["space_alpha"], (inv @ acc) * n["space_alpha"]

    def _init(self, cs, space):
        n, self.bodies, self.joints = self.node, [], []
        for d in self.defs:
            q, p = relative(cs[d["bone"]], space)
            body = Body(p + q @ Vector(d["offset"]), q, d["box"])
            box = [abs(x) for x in d["box"]]
            body.collision = d["collision"]
            body.radius = {"CustomSphere": d["radius"], "InnerSphere": min(box) / 2.0, "OuterSphere": max(box) / 2.0}.get(d["collision"], 0.0)
            body.lin_damp, body.ang_damp = n["lin_damping"], n["ang_damping"]
            body.gravity_scale, body.use_override = n["gravity_scale"], n["gravity_override"] is not None
            self.bodies.append(body)
        for i, (d, body) in enumerate(zip(self.defs, self.bodies)):
            if i == 0:
                self.joints.append((ZERO.copy(), -Vector(d["offset"])))
            else:           # chained bodies meet halfway between their centres
                prev = self.bodies[i - 1]
                mid = (body.pos - prev.pos) * 0.5
                self.joints.append((prev.rot.conjugated() @ mid, body.rot.conjugated() @ -mid))
        self.prev_space = space[0].copy()

    def _limits(self, dt, cs, space):
        n, linear, angular, springs = self.node, [], [], []
        bias = BIAS if n["bias"] is None else n["bias"]
        for i, (d, body) in enumerate(zip(self.defs, self.bodies)):
            bone = relative(cs[d["bone"]], space)
            prev = self.bodies[i - 1] if i else None
            shape = (Quaternion(), self.joints[i][0]) if prev else bone
            joint = self.joints[i][1]
            if all(t != "Free" and lo - hi == 0.0 for t, lo, hi in zip(d["lin_types"], d["lin_min"], d["lin_max"])):
                nailed(dt, linear, prev, shape[1], body, joint)
            else:
                for k, name in enumerate("XYZ"):
                    if d["lin_types"][k] != "Free":
                        along(dt, linear, prev, shape[1], body, joint, shape[0] @ AXES[name], d["lin_min"][k], d["lin_max"][k])
            if d["angular"] == "Angular":
                angular_range(dt, angular, prev, body, shape[0], d["twist"], d["ang_min"], d["ang_max"], bias)
            else:
                cone(dt, angular, prev, bone[0] @ AXES["X"], body, d["cone"], bias)
            for lim in n["planar"]:
                plane = (Quaternion(lim["plane"][0]), Vector(lim["plane"][1]))
                if lim.get("bone") in cs:
                    plane = compose(plane, relative(cs[lim["bone"]], space))
                planar(dt, linear, body, plane)
            for lim in n["spherical"]:
                center = (Quaternion(), Vector(lim["offset"]))
                if lim.get("bone") in cs:
                    center = compose(center, relative(cs[lim["bone"]], space))
                spherical(dt, linear, body, center[1], lim["radius"], lim["type"] == "Inner")
            if n["ang_spring"] is not None or n["lin_spring"] is not None:
                springs.append(Spring(prev, shape[1], body, n["lin_spring"] or 0.0, n["ang_spring"] or 0.0,
                                      safe_normal(Vector(d["target"])), d["target_axis"], shape[0],
                                      n["lin_spring"] is not None, n["ang_spring"] is not None))
        return linear, angular, springs

    def evaluate(self, dt, cs, root):
        """Step by the frame's dt (clamped as the engine does) and return {bone: component transform}."""
        n = self.node
        space = self._space(cs, root)
        if self.bodies is None:
            self._init(cs, space)
        if dt > 0.0:
            spin, spin_acc = self._space_spin(space[0], dt)      # from the frame's own dt, before the clamp
            dt = min(dt, MAX_DT)
            inv = space[0].conjugated()
            force = Vector(n["external_force"])
            if not nearly_zero(force):
                force = inv @ force
            override = Vector(n["gravity_override"] or (0.0, 0.0, 0.0))
            if n["gravity_override"] is not None and not n["gravity_in_sim"]:
                override = inv @ override
            for b in self.bodies:
                b.override = override
            linear, angular, springs = self._limits(dt, cs, space)
            physics_update(dt, self.bodies, linear, angular, springs, inv @ Vector((0.0, 0.0, -1.0)), force, ZERO,
                           spin_acc, spin, n["pre"], n["post"])
        else:
            self.prev_space = space[0].copy()
        return {d["bone"]: compose((b.rot.copy(), b.pos - b.rot @ Vector(d["offset"])), space)
                for d, b in zip(self.defs, self.bodies)}
