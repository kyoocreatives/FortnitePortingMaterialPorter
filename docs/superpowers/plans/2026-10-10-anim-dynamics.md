# Dynamic Bones 1 (AnimDynamics) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fortnite's AnimDynamics `dyn_*` bones baked in Blender with the engine's own solver and the character's settings.

**Architecture:** The app dumps the part's anim blueprint; the plugin reads its AnimDynamics nodes (graph order,
defaults filled) onto the armature at import; a pure solver module steps them as UE 5.8 does; a bake samples the
animation once per frame, ticks the solver at 60 Hz on interpolated locals and keys the result into a `Dynamics` NLA
track. Emote import runs the bake; the Rig panel has Simulate and a Dynamics toggle.

**Tech Stack:** C# (CUE4Parse, FP exporter), Blender 5 Python (bpy, mathutils), headless test runner.

**Spec:** `docs/superpowers/specs/2026-10-10-anim-dynamics-design.md`

## Global Constraints

- No Epic source code in the public fork: the math is ours, written from `fpfork-private/devtools/helpers/animdynamics_algorithm.md`.
- Upstream FP files get one-line hooks marked `# MP` / `// MP` only.
- Comments: one or two short lines, only the why or a non-obvious fact. Files under ~900 lines.
- Headless isolated Blender only (`bash <scratchpad>/rigcheck.sh <test>.py`); never touch the user's Blender or the app on 24320.
- Game numbers inside the solver: cm, the game's axes (Blender's game frame through `FLIP`), quaternions as mathutils.
- Commits wait for the user's wording; no Co-Authored-By trailer.

## Review Focus

1. A bone the armature lacks, or a dump with no AnimDynamics: nothing baked, no error, one log line.
2. Re-bake: the old `Dynamics` track and action are replaced, never sampled as input.
3. An armature playing a plain action (no NLA): the action is pushed down so the `Dynamics` layer sits above it.
4. Quaternion sign flips between frames: keys stay continuous (no 360° spins).
5. Degenerate settings (zero-volume box, damping above 1): no NaN keys.

---

### Task 1: Solver (`processing/context/dynamics_solver.py`)

**Files:**
- Create: `plugins/Blender/fortnite_porting/processing/context/dynamics_solver.py`
- Test: `tests/plugin/dynamics_check.py`

**Interfaces:**
- Produces: `box_inertia(extents) -> Vector`; `find_between(a, b) -> Quaternion`; `Body`; `Linear`; `Angular`;
  `Spring`; `nailed`, `along`, `angular_range`, `cone`, `planar`, `spherical`; `physics_update(...)`;
  `chain_defs(node, parent_of) -> list[dict]`; `Sim(node, parent_of)` with `Sim.evaluate(dt, cs) -> {bone: (q, p)}`
  where `cs` maps bone names to component transforms `(Quaternion, Vector)` in game numbers.
- Node dict (from Task 2's reader): keys `bone, chain_end, space, relative_bone, bodies[], lin_damping, ang_damping,
  bias, gravity_scale, gravity_override, gravity_in_sim, lin_spring, ang_spring, external_force, space_alpha,
  max_ang_vel, max_ang_acc, external_ang_vel, planar[], spherical[], pre, post, alpha`. Body dict keys `bone, box,
  offset, lin_types[3], lin_min[3], lin_max[3], angular, twist, target_axis, cone, ang_min[3], ang_max[3], target[3],
  collision, radius`.

- [ ] **Step 1: Write the failing tests** (`tests/plugin/dynamics_check.py`, Blender headless like `deform_rig_check.py`):
  box inertia (15³ → 37.5, 7.5×4×4 → 2.6667/6.0208/6.0208, zero box → 1/6); `find_between` with a zero target is
  identity; one free-body step damps momentum by `(1-0.7)^(1/60)`; a nailed body offset 1 cm lands on its target in
  one step with its momentum removed; a body hanging off its joint along X under gravity settles at the Y max 2.5°
  (±0.5°); a 20° twist drops to 14.03° (±0.05°) after one step; zero-target spring adds nothing, a +Z target on an
  identity body adds `(0, -25·π/2·dt, 0)`; the inverse inertia of a 7.5×4×4 box turned 30° about Z is `Rᵀ I⁻¹ R`
  (its xy term has the sign of that form, not of `R I⁻¹ Rᵀ`); a frame dt of 0.1 steps 1/30 (free fall 1.0889 cm);
  a two-body chain under gravity keeps its joint within 0.5 cm for 1 s; the space spin of a root turning 1 rad/s
  about Z reads (0, 0, 1).
- [ ] **Step 2: Run** `bash rigcheck.sh dynamics_check.py`. Expected: FAIL (`dynamics_solver` missing).
- [ ] **Step 3: Implement the solver** (the module below, in full).
- [ ] **Step 4: Run** `bash rigcheck.sh dynamics_check.py`. Expected: all solver checks pass.

Solver code (module body):

```python
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
AXES = {"X": Vector((1, 0, 0)), "Y": Vector((0, 1, 0)), "Z": Vector((0, 0, 1))}
ZERO = Vector((0.0, 0.0, 0.0))


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
        q = [w, c.x, c.y, c.z]
    else:
        basis = Vector((0, 1, 0)) if abs(a.x) > abs(a.y) and abs(a.x) > abs(a.z) else Vector((-1, 0, 0))
        c = a.cross(basis)
        q = [0.0, c.x, c.y, c.z]
    s = sum(x * x for x in q)
    if s < 1e-8:
        return Quaternion()
    k = 1.0 / math.sqrt(s)
    return Quaternion([x * k for x in q])


def axis_angle(q):
    angle = 2.0 * math.acos(max(-1.0, min(1.0, q.w)))
    v = Vector((q.x, q.y, q.z))
    return (Vector((1, 0, 0)) if v.length_squared < 1e-8 else v.normalized()), angle


def box_inertia(extents):
    """Per-unit-mass inertia of a solid box with these full edge lengths (a zero box becomes a unit cube)."""
    a, b, c = extents
    if a * a + b * b + c * c <= 1e-8:
        a = b = c = 1.0
    return Vector(((b * b + c * c) / 12.0, (a * a + c * c) / 12.0, (a * a + b * b) / 12.0))


def _world_inverse(rot, inv_local):
    # the engine rotates the tensor as R^T I^-1 R (not R I^-1 R^T); identical for cubes only
    r = rot.to_matrix()
    return r.transposed() @ inv_local @ r


class Body:
    __slots__ = ("pos", "rot", "lin", "ang", "inv_local", "inv_world", "inertia", "lin_damp", "ang_damp",
                 "use_override", "override", "gravity_scale", "collision", "radius", "next_pos", "next_rot")

    def __init__(self, pos, rot, extents):
        moments = box_inertia(extents)
        self.pos, self.rot = pos.copy(), rot.copy()
        self.lin, self.ang = ZERO.copy(), ZERO.copy()
        self.inertia = Matrix.Diagonal(moments)
        self.inv_local = Matrix.Diagonal(Vector([1.0 / m for m in moments]))
        self.inv_world = self.inv_local.copy()      # unrotated until the first step, as in the engine
        self.lin_damp = self.ang_damp = None
        self.use_override, self.override, self.gravity_scale = False, ZERO.copy(), 1.0
        self.collision, self.radius = "CoM", 0.0
        self.next_pos, self.next_rot = self.pos, self.rot

    def at(self, local):
        return self.rot @ local + self.pos

    def spin(self):
        return self.inv_world @ self.ang

    def push(self, point, impulse):
        self.lin += impulse
        self.ang += point.cross(impulse)


class Linear:
    __slots__ = ("b0", "b1", "r0", "r1", "n", "target", "unbiased", "lo", "hi", "k", "sum")

    def __init__(self, b0, b1, p0, p1, n, target, unbiased=0.0, lo=-MAX, hi=MAX):
        self.b0, self.b1, self.n = b0, b1, n
        self.target, self.unbiased, self.lo, self.hi, self.sum = target, unbiased, min(lo, hi), max(lo, hi), 0.0
        self.r0 = b0.rot @ p0 if b0 else p0
        self.r1 = b1.rot @ p1 if b1 else p1
        d = 0.0
        for b, r in ((b0, self.r0), (b1, self.r1)):
            if b:
                d += 1.0 + (b.inv_world @ r.cross(n)).cross(r).dot(n)
        self.k = 1.0 / d

    def iterate(self, dt):
        v0 = self.b0.spin().cross(self.r0) + self.b0.lin if self.b0 else ZERO
        v1 = self.b1.spin().cross(self.r1) + self.b1.lin if self.b1 else ZERO
        impulse = (-self.target - (v1 - v0).dot(self.n)) * self.k
        impulse = min(self.hi * dt - self.sum, impulse)
        impulse = max(self.lo * dt - self.sum, impulse)
        if self.b0:
            self.b0.push(self.r0, self.n * -impulse)
        if self.b1:
            self.b1.push(self.r1, self.n * impulse)
        self.sum += impulse

    def unbias(self):
        self.target = self.unbiased


class Angular:
    __slots__ = ("b0", "b1", "axis", "target", "lo", "hi", "k", "sum")

    def __init__(self, b0, b1, axis, target=0.0, lo=-MAX, hi=MAX):
        self.b0, self.b1, self.axis, self.target, self.lo, self.hi, self.sum = b0, b1, axis, target, lo, hi, 0.0
        d = sum(axis.dot(b.inv_world @ axis) for b in (b0, b1) if b)
        self.k = 1.0 / d if d else math.inf

    def iterate(self, dt):
        if self.target == -MAX:
            return
        spin = (self.b1.spin().dot(self.axis) if self.b1 else 0.0) - (self.b0.spin().dot(self.axis) if self.b0 else 0.0)
        torque = (self.target - spin) * self.k
        torque = min(torque, self.hi * dt - self.sum)
        torque = max(torque, self.lo * dt - self.sum)
        if self.b0:
            self.b0.ang -= self.axis * torque
        if self.b1:
            self.b1.ang += self.axis * torque
        self.sum += torque

    def unbias(self):
        self.target = 0.0 if self.lo < 0 else min(self.target, 0.0)


class Spring:
    __slots__ = ("b0", "b1", "a0", "a1", "k_lin", "k_ang", "target", "axis", "frame", "linear", "angular")

    def __init__(self, b0, a0, b1, k_lin, k_ang, target, axis, frame, linear, angular):
        self.b0, self.a0, self.b1, self.a1 = b0, a0, b1, ZERO.copy()
        self.k_lin, self.k_ang, self.target, self.axis, self.frame = k_lin, k_ang, target, axis, frame
        self.linear, self.angular = linear, angular

    def apply(self, dt):
        if self.linear:
            p0 = self.b0.at(self.a0) if self.b0 else self.a0
            d = self.b1.at(self.a1) - p0
            impulse = safe_normal(d) * (-self.k_lin * d.length * dt)
            if self.b0:
                self.b0.push(self.b0.rot @ self.a0, -impulse)
            self.b1.push(self.b1.rot @ self.a1, impulse)
        if self.angular:
            axis, angle = axis_angle(find_between(self.b1.rot @ AXES[self.axis], self.frame @ self.target))
            added = axis * (-self.k_ang * angle) * dt
            if self.b0:
                self.b0.ang += added
            self.b1.ang -= added


def nailed(dt, out, b0, p0, b1, p1):
    gap = ((b1.at(p1)) - (b0.at(p0) if b0 else p0)) / dt
    for k, n in enumerate(AXES.values()):
        out.append(Linear(b0, b1, p0, p1, n.copy(), gap[k]))


def along(dt, out, b0, p0, b1, p1, axis, lo, hi):
    d = (b1.at(p1) - (b0.at(p0) if b0 else p0)).dot(axis)
    if abs(lo - hi) < 1e-8:
        out.append(Linear(b0, b1, p0, p1, axis, d / dt, d / dt))
    else:
        out.append(Linear(b0, b1, p0, p1, axis, (d - lo) / dt, (d - lo) / dt, 0.0, MAX))
        out.append(Linear(b0, b1, p0, p1, axis, (d - hi) / dt, (d - hi) / dt, -MAX, 0.0))


SWING = {"X": ("Y", "Z"), "Y": ("X", "Z"), "Z": ("X", "Y")}
INDEX = {"X": 0, "Y": 1, "Z": 2}


def angular_range(dt, out, b0, b1, frame, twist, lo_deg, hi_deg, bias):
    f0 = b0.rot @ frame if b0 else frame
    f1 = b1.rot
    rel = f0.conjugated() @ f1
    swing = find_between(AXES[twist], rel @ AXES[twist])
    turn = swing.conjugated() @ rel
    if swing.w < 0.0:
        swing.negate()
    if turn.w < 0.0:
        turn.negate()
    comp = {"X": swing.x, "Y": swing.y, "Z": swing.z}
    for slot, name in enumerate(SWING[twist]):
        i = INDEX[name]
        lo, hi = math.radians(lo_deg[i]), math.radians(hi_deg[i])
        s, axis = comp[name], f1 @ AXES[name]
        if lo == hi:
            # the engine biases the second swing axis and compares it with the raw angle
            target = 2.0 * (-s + math.sin(lo / 2.0)) / dt if slot == 0 else bias * 2.0 * (-s + lo) / dt
            out.append(Angular(b0, b1, axis, target))
        elif hi - lo < 2.0 * math.pi:
            out.append(Angular(b0, b1, axis, 2.0 * (-s + math.sin(lo / 2.0)) / dt, 0.0))
            out.append(Angular(b0, b1, -axis, 2.0 * (s - math.sin(hi / 2.0)) / dt, 0.0))
    twist_amount = {"X": turn.x, "Y": turn.y, "Z": turn.z}[twist]
    out.append(Angular(b0, b1, f1 @ AXES[twist], bias * 2.0 * -twist_amount / dt))


def cone(dt, out, b0, normal0, b1, limit_deg, bias):
    n0 = b0.rot @ normal0 if b0 else normal0
    n1 = b1.rot @ AXES["X"]
    axis = safe_normal(n1.cross(n0))
    angle = math.acos(max(0.0, min(1.0, n0.dot(n1))))
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
    lin_d = body.lin_damp if body.lin_damp is not None else DAMPING
    ang_d = body.ang_damp if body.ang_damp is not None else DAMPING
    # damping above 1 is a NaN in the engine; keep the body still instead
    body.lin *= math.pow(max(1.0 - lin_d, 0.0), dt)
    body.ang *= math.pow(max(1.0 - ang_d, 0.0), dt)
    force = body.override if body.use_override else down * GRAVITY * body.gravity_scale
    body.lin += force * dt
    body.inv_world = _world_inverse(body.rot, body.inv_local)


def _diff(q, inv_local, ang):
    qn = q.normalized()
    w = _world_inverse(qn, inv_local) @ ang * 0.5
    return Quaternion((0.0, w.x, w.y, w.z)) @ qn


def _rk4(q, inv_local, ang, dt):
    d1 = _diff(q, inv_local, ang)
    d2 = _diff(q + d1 * (dt / 2), inv_local, ang)
    d3 = _diff(q + d2 * (dt / 2), inv_local, ang)
    d4 = _diff(q + d3 * dt, inv_local, ang)
    return (q + d1 * (dt / 6) + d2 * (dt / 3) + d3 * (dt / 3) + d4 * (dt / 6)).normalized()


def physics_update(dt, bodies, linear, angular, springs, down, force, acc, ang_acc, ang_vel, pre, post):
    for b in bodies:
        _init_velocity(b, dt, down)
    if not nearly_zero(force):
        for b in bodies:
            b.lin += force * dt
    if not nearly_zero(acc):
        for b in bodies:
            b.lin += acc * dt
    if not (nearly_zero(ang_vel) and nearly_zero(ang_acc)):
        for b in bodies:
            fictitious = CORIOLIS * 2.0 * ang_vel.cross(b.lin) + CENTRIFUGAL * ang_vel.cross(ang_vel.cross(b.pos)) \
                + EULER * ang_acc.cross(b.pos)
            b.lin -= fictitious * dt
            b.ang -= (b.inertia @ ang_acc) * dt
    for s in springs:
        s.apply(dt)
    for _ in range(pre):
        for c in linear:
            c.iterate(dt)
        for c in angular:
            c.iterate(dt)
    for b in bodies:
        b.next_pos = b.pos + b.lin * dt
        b.next_rot = _rk4(b.rot, b.inv_local, b.ang, dt)
    for c in linear:
        c.unbias()
    for c in angular:
        c.unbias()
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
    names = [node["bone"]]
    end = node.get("chain_end")
    if end:
        walk, up = end, []
        while walk and walk != node["bone"]:
            up.insert(0, walk)
            walk = parent_of.get(walk)
        if walk == node["bone"]:
            names += up
    defs = node["bodies"]
    if defs and defs[0]["bone"] == node["bone"] and defs[-1]["bone"] == end:
        return defs
    proto = dict(defs[0]) if len(defs) == 1 else None
    kept = {d["bone"]: d for d in defs if d["bone"] in names}
    out = []
    for name in names:
        d = kept.get(name) or dict(proto or DEFAULT_BODY)
        out.append(dict(d, bone=name))
    return out


DEFAULT_BODY = {"bone": None, "box": [10.0, 10.0, 10.0], "offset": [0.0, 0.0, 0.0],
                "lin_types": ["Limited"] * 3, "lin_min": [0.0] * 3, "lin_max": [0.0] * 3,
                "angular": "Angular", "twist": "X", "target_axis": "X", "cone": 0.0,
                "ang_min": [0.0] * 3, "ang_max": [0.0] * 3, "target": [0.0] * 3, "collision": "CoM", "radius": 10.0}


class Sim:
    """One AnimDynamics node: evaluate() takes the pose reaching it (component transforms by bone name) and returns
    its bodies' bones."""

    def __init__(self, node, parent_of):
        self.node, self.defs, self.bodies = node, chain_defs(node, parent_of), None
        self.prev_space, self.prev_spin = Quaternion(), ZERO.copy()

    def bones(self):
        extra = [p["bone"] for p in self.node["planar"] + self.node["spherical"] if p.get("bone")]
        rel = [self.node["relative_bone"]] if self.node["space"] == "BoneRelative" and self.node.get("relative_bone") else []
        return [d["bone"] for d in self.defs] + extra + rel

    def _space(self, cs, root):
        space = self.node["space"]
        if space == "RootRelative":
            return cs[root]
        if space == "BoneRelative" and self.node.get("relative_bone") in cs:
            return cs[self.node["relative_bone"]]
        return Quaternion(), ZERO.copy()        # the component sits at the origin in a bake

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
        n, self.bodies = self.node, []
        for d in self.defs:
            q, p = relative(cs[d["bone"]], space)
            body = Body(p + q @ Vector(d["offset"]), q, d["box"])
            body.collision = d["collision"]
            box = [abs(x) for x in d["box"]]
            body.radius = {"CustomSphere": d["radius"], "InnerSphere": min(box) / 2.0,
                           "OuterSphere": max(box) / 2.0}.get(d["collision"], 0.0)
            body.lin_damp, body.ang_damp = n["lin_damping"], n["ang_damping"]
            body.gravity_scale = n["gravity_scale"]
            body.use_override = n["gravity_override"] is not None
            self.bodies.append(body)
        self.joints = []
        for i, (d, body) in enumerate(zip(self.defs, self.bodies)):
            if i == 0:
                self.joints.append((ZERO.copy(), -Vector(d["offset"])))
            else:
                prev = self.bodies[i - 1]
                mid = (body.pos - prev.pos) * 0.5
                self.joints.append((prev.rot.conjugated() @ mid, body.rot.conjugated() @ -mid))
        self.prev_space = space[0].copy()
        self.prev_spin = ZERO.copy()

    def _limits(self, dt, cs, space):
        n, linear, angular, springs = self.node, [], [], []
        bias = n["bias"] if n["bias"] is not None else BIAS
        for i, (d, body) in enumerate(zip(self.defs, self.bodies)):
            bone = relative(cs[d["bone"]], space)
            prev = self.bodies[i - 1] if i else None
            shape = (Quaternion(), self.joints[i][0]) if prev else bone
            joint = self.joints[i][1]
            locked = all(t != "Free" and lo - hi == 0.0 for t, lo, hi in zip(d["lin_types"], d["lin_min"], d["lin_max"]))
            if locked:
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
            spin, spin_acc = self._space_spin(space[0], dt)
            dt = min(dt, MAX_DT)
            inv = space[0].conjugated()
            down = inv @ Vector((0.0, 0.0, -1.0))
            force = Vector(n["external_force"])
            if not nearly_zero(force):
                force = inv @ force
            override = Vector(n["gravity_override"] or (0.0, 0.0, 0.0))
            if n["gravity_override"] is not None and not n["gravity_in_sim"]:
                override = inv @ override
            for b in self.bodies:
                b.override = override.copy()
            linear, angular, springs = self._limits(dt, cs, space)
            physics_update(dt, self.bodies, linear, angular, springs, down, force, ZERO.copy(), spin_acc, spin,
                           n["pre"], n["post"])
        else:
            self.prev_space = space[0].copy()
        out = {}
        for d, body in zip(self.defs, self.bodies):
            out[d["bone"]] = compose((body.rot.copy(), body.pos - body.rot @ Vector(d["offset"])), space)
        return out
```

Notes for the implementer: `physics_update` keeps mass 1 (momentum is velocity). A box with one zero edge has no
volume: `Body` must not divide by a zero moment — Task 1's test for it expects the reader (Task 2) to have replaced
such a box by the unit cube with one log line; the solver's `box_inertia` only handles the all-zero case as the
engine does.

- [ ] **Step 5: Commit** (wording to the user at the end of the plan; one commit for the whole feature).

### Task 2: Reader (`processing/context/dynamics_read.py`)

**Files:**
- Create: `plugins/Blender/fortnite_porting/processing/context/dynamics_read.py`
- Test: `tests/plugin/dynamics_check.py` (reader section)

**Interfaces:**
- Consumes: CUE4Parse package dumps (list of exports) at the meta paths.
- Produces: `nodes(paths) -> list[dict]` (node dicts as in Task 1, plus `"part": index`), `node(props) -> dict`.

- [ ] **Step 1: Failing test:** a synthetic dump whose class lists `[CopyPose, AD_B, Root, AD_A]` with
  `Root.Result -> 3 (AD_A)`, `AD_A.ComponentPose -> 1 (AD_B)`, `AD_B.ComponentPose -> 0`; `nodes([path])` returns
  B then A; A's absent box is `[10,10,10]`; enums lose their `Type::` prefix; `ang_damping` is 0.999 only when its flag
  is on, `lin_damping` None when off; a box with a zero edge becomes `[1,1,1]`; a missing file gives `[]`.
- [ ] **Step 2: Run**, expect FAIL (module missing).
- [ ] **Step 3: Implement:**

```python
"""Fortnite's AnimDynamics nodes from a part's anim blueprint dump (CUE4Parse JSON), in the order the graph runs them,
with the engine's defaults where the cooked asset leaves a value out."""
import json
import os

from ...logger import Log

NODE_DEFAULTS = {"space": "Component", "relative_bone": None, "gravity_scale": 1.0, "space_alpha": 0.0,
                 "max_ang_vel": 10000.0, "max_ang_acc": 10000.0, "external_ang_vel": [0.0, 0.0, 0.0],
                 "external_force": [0.0, 0.0, 0.0], "pre": 4, "post": 1, "alpha": 1.0}


def _enum(v, default):
    return str(v).split("::")[-1] if v is not None else default


def _vec(d, default):
    return [float(d.get(k, 0.0)) for k in "XYZ"] if isinstance(d, dict) else list(default)


def _bone(ref):
    name = (ref or {}).get("BoneName")
    return None if name in (None, "None") else name


def _body(d):
    setup = d.get("ConstraintSetup") or {}
    box = _vec(d.get("BoxExtents"), (10.0, 10.0, 10.0))
    if 0.0 in box and any(box):
        Log.warn("[Material Porter] dynamics: %s has a flat box, simulated as a unit cube" % _bone(d.get("BoundBone")))
        box = [1.0, 1.0, 1.0]
    return {"bone": _bone(d.get("BoundBone")), "box": box, "offset": _vec(d.get("LocalJointOffset"), (0, 0, 0)),
            "lin_types": [_enum(setup.get("Linear%sLimitType" % a), "Limited") for a in "XYZ"],
            "lin_min": _vec(setup.get("LinearAxesMin"), (0, 0, 0)), "lin_max": _vec(setup.get("LinearAxesMax"), (0, 0, 0)),
            "angular": _enum(setup.get("AngularConstraintType"), "Angular"),
            "twist": _enum(setup.get("TwistAxis"), "AxisX")[-1], "target_axis": _enum(setup.get("AngularTargetAxis"), "AxisX")[-1],
            "cone": float(setup.get("ConeAngle", 0.0)),
            "ang_min": _vec(setup.get("AngularLimitsMin"), (0, 0, 0)), "ang_max": _vec(setup.get("AngularLimitsMax"), (0, 0, 0)),
            "target": _vec(setup.get("AngularTarget"), (0, 0, 0)),
            "collision": _enum(d.get("CollisionType"), "CoM"), "radius": float(d.get("SphereCollisionRadius", 10.0))}


def _transform(t):
    r, p = (t or {}).get("Rotation") or {}, (t or {}).get("Translation") or {}
    return [[r.get("W", 1.0), r.get("X", 0.0), r.get("Y", 0.0), r.get("Z", 0.0)], _vec(p, (0, 0, 0))]


def node(p):
    """One AnimDynamics node's settings as plain data."""
    space = p.get("SimSpaceSettings") or {}
    out = dict(NODE_DEFAULTS)
    out.update({
        "bone": _bone(p.get("BoundBone")), "chain_end": _bone(p.get("ChainEnd")),
        "space": _enum(p.get("SimulationSpace"), "Component"), "relative_bone": _bone(p.get("RelativeSpaceBone")),
        "bodies": [_body(d) for d in p.get("PhysicsBodyDefinitions") or [{"BoundBone": p.get("BoundBone")}]],
        "lin_damping": float(p.get("LinearDampingOverride", 0.0)) if p.get("bOverrideLinearDamping") else None,
        "ang_damping": float(p.get("AngularDampingOverride", 0.0)) if p.get("bOverrideAngularDamping") else None,
        "bias": float(p.get("AngularBiasOverride", 0.0)) if p.get("bOverrideAngularBias") else None,
        "gravity_scale": float(p.get("GravityScale", 1.0)),
        "gravity_override": _vec(p.get("GravityOverride"), (0, 0, 0)) if p.get("bUseGravityOverride") else None,
        "gravity_in_sim": bool(p.get("bGravityOverrideInSimSpace", False)),
        "lin_spring": float(p.get("LinearSpringConstant", 0.0)) if p.get("bLinearSpring") else None,
        "ang_spring": float(p.get("AngularSpringConstant", 0.0)) if p.get("bAngularSpring") else None,
        "external_force": _vec(p.get("ExternalForce"), (0, 0, 0)),
        "space_alpha": float(space.get("SimSpaceAngularAlpha", 0.0)),
        "max_ang_vel": float(space.get("MaxAngularVelocity", 10000.0)),
        "max_ang_acc": float(space.get("MaxAngularAcceleration", 10000.0)),
        "external_ang_vel": _vec(space.get("ExternalAngularVelocity"), (0, 0, 0)),
        "planar": [{"bone": _bone(l.get("DrivingBone")), "plane": _transform(l.get("PlaneTransform"))}
                   for l in p.get("PlanarLimits") or []] if p.get("bUsePlanarLimit", True) else [],
        "spherical": [{"bone": _bone(l.get("DrivingBone")), "offset": _vec(l.get("SphereLocalOffset"), (0, 0, 0)),
                       "radius": float(l.get("LimitRadius", 30.0)), "type": _enum(l.get("LimitType"), "Outer")}
                      for l in p.get("SphericalLimits") or []] if p.get("bUseSphericalLimits") else [],
        "pre": int(p.get("NumSolverIterationsPreUpdate", 4)), "post": int(p.get("NumSolverIterationsPostUpdate", 1)),
        "alpha": float(p.get("Alpha", 1.0))})
    ignored = [k for k, on in (("wind", p.get("bEnableWind")), ("retargeting", (p.get("RetargetingSettings") or {}).get("bEnabled")),
                               ("alpha input", _enum(p.get("AlphaInputType"), "Float") != "Float")) if on]
    if ignored:
        Log.info("[Material Porter] dynamics: %s ignores %s" % (out["bone"], ", ".join(ignored)))
    return out


def _part(raw):
    cls = next((e for e in raw if e.get("Type") == "AnimBlueprintGeneratedClass"), None)
    cdo = next((e for e in raw if str(e.get("Name", "")).startswith("Default__")), None)
    if cls is None or cdo is None:
        return []
    names = [c.get("Name") for c in cls.get("ChildProperties") or [] if str(c.get("Name", "")).startswith("AnimGraphNode_")]
    props = cdo.get("Properties") or {}
    order, seen = [], set()

    def visit(name):            # inputs first: the order the graph evaluates
        if name in seen or name not in props:
            return
        seen.add(name)
        for v in props[name].values():
            if isinstance(v, dict) and isinstance(v.get("LinkID"), int) and 0 <= v["LinkID"] < len(names):
                visit(names[v["LinkID"]])
        order.append(name)

    for root in [n for n in names if n.startswith("AnimGraphNode_Root")]:
        visit(root)
    return [node(props[n]) for n in order if n.startswith("AnimGraphNode_AnimDynamics")]


def nodes(paths):
    """Every part's AnimDynamics nodes, parts in import order (the body's first: the others copy its pose)."""
    out = []
    for i, path in enumerate(paths):
        try:
            raw = json.load(open(path, encoding="utf-8")) if path and os.path.isfile(path) else []
        except ValueError as e:
            Log.warn("[Material Porter] dynamics: %s unreadable (%s)" % (path, e))
            continue
        for n in _part(raw):
            out.append(dict(n, part=i))
    return out
```

- [ ] **Step 4: Run**, expect the reader checks to pass. (Check `Log` has `warn`/`info`; use what `logger.py` provides.)

### Task 3: Bake (`processing/context/dynamics_bake.py`)

**Files:**
- Create: `plugins/Blender/fortnite_porting/processing/context/dynamics_bake.py`
- Test: `tests/plugin/dynamics_check.py` (bake section)

**Interfaces:**
- Consumes: `Sim`, `compose`, `relative` (Task 1); armature data key `fpmp_dynamics` (JSON list of Task 2 nodes).
- Produces: `KEY = "fpmp_dynamics"`, `TRACK = "Dynamics"`, `bake(obj, rate=60) -> (bones, frames)`,
  `frame_range(obj) -> (start, end) | None`, `set_on(obj, on)`, `is_on(obj)`, `ui(layout, obj)`.

- [ ] **Step 1: Failing tests** on a synthetic armature (root at origin; `upperarm` from (0,0,1) along +X under root;
  `dyn_tag` under `upperarm` at its tail pointing -Z), all quaternion rotation:
  - no `fpmp_dynamics`: `bake` returns `(0, 0)` and makes no track;
  - identity round trip: a node on `dyn_tag` with gravity override 0, linear locked, angular limits 0 on all axes,
    an action holding the rest pose 0..20 → a `Dynamics` track with one strip whose action only has
    `pose.bones["dyn_tag"]` location/rotation curves, every key ≈ rest (rotation within 1e-3, location within 1e-4 m);
  - lag: same node with limits ±30° and angular damping override 0.5, `upperarm` keyed 0° at 0 → 60° about Y at 10,
    held to 40: at frame 6 the baked `dyn_tag` direction differs from the unsimulated one by more than 1°, its head
    stays on the animated head within 1 mm; by frame 40 it is back within 2°;
  - re-bake replaces the track (still one `Dynamics` track, one strip);
  - `set_on(obj, False)` mutes the track; `is_on` reports it.
- [ ] **Step 2: Run**, expect FAIL.
- [ ] **Step 3: Implement:**

```python
"""Bake Fortnite's AnimDynamics over an armature's animation: sample it once per frame in the game's numbers, tick
the engine's solver between frames on interpolated locals, key the simulated bones into a Dynamics NLA track."""
import json
import math
import time

import bpy
from mathutils import Matrix, Quaternion, Vector

from ...logger import Log
from .dynamics_solver import Sim, compose, relative

KEY = "fpmp_dynamics"
TRACK = "Dynamics"
PREROLL = 1.0       # seconds held on the first pose: the game's character was already idling when the emote began
FLIP = Matrix(((1.0, 0.0, 0.0), (0.0, -1.0, 0.0), (0.0, 0.0, 1.0)))


def _track(obj):
    ad = obj.animation_data
    return next((t for t in ad.nla_tracks if t.name == TRACK), None) if ad else None


def is_on(obj):
    t = _track(obj)
    return t is not None and not t.mute


def set_on(obj, on):
    t = _track(obj)
    if t is not None:
        t.mute = not on


def frame_range(obj):
    ad = obj.animation_data
    if ad is None:
        return None
    strips = [s for t in ad.nla_tracks if t.name != TRACK and not t.mute for s in t.strips]
    if ad.action is not None:
        return int(math.floor(ad.action.frame_range[0])), int(math.ceil(ad.action.frame_range[1]))
    if strips:
        return int(math.floor(min(s.frame_start for s in strips))), int(math.ceil(max(s.frame_end for s in strips)))
    return None


def _to_game(m, offset, unit):
    """A pose bone's armature matrix -> its game transform (rotation, position in cm)."""
    g = m @ offset
    r = (FLIP @ g.to_3x3().normalized() @ FLIP).to_quaternion()
    return r, FLIP @ g.translation / unit


def _from_game(t, offset_inv, unit):
    r = FLIP @ t[0].to_matrix() @ FLIP
    m = r.to_4x4()
    m.translation = FLIP @ t[1] * unit
    return m @ offset_inv


def _blend(a, b, u):
    qb = -b[0] if a[0].dot(b[0]) < 0.0 else b[0]
    q = Quaternion([x + (y - x) * u for x, y in zip(a[0], qb)]).normalized()
    return q, a[1].lerp(b[1], u)


def _push_down(obj):
    """A plain action goes down into the NLA so the Dynamics layer can sit above it."""
    ad = obj.animation_data
    if ad is None or ad.action is None:
        return
    action, start = ad.action, int(ad.action.frame_range[0])
    track = ad.nla_tracks.new()
    track.name = action.name
    track.strips.new(action.name, start, action)
    ad.action = None


def bake(obj, rate=60):
    """Simulate the armature's dynamics nodes over its animation; (bones keyed, frames) or (0, 0)."""
    from ...material_porter.effects import ue_offset
    raw = obj.data.get(KEY)
    span = frame_range(obj)
    if not raw or span is None:
        return 0, 0
    t0 = time.time()
    bones = obj.data.bones
    parent_of = {b.name: b.parent.name if b.parent else None for b in bones}
    sims = [Sim(n, parent_of) for n in json.loads(raw) if n["bone"] in bones]
    sims = [s for s in sims if all(b in bones for b in s.bones())]
    if not sims:
        return 0, 0
    old = _track(obj)
    if old is not None:
        for s in old.strips:
            if s.action and s.action.users <= 1:
                bpy.data.actions.remove(s.action)
        obj.animation_data.nla_tracks.remove(old)
    root = next(b.name for b in bones if b.parent is None)
    need = set()
    for s in sims:
        for name in s.bones():
            while name and name not in need:
                need.add(name)
                name = parent_of[name]
    need.add(root)
    order = [b.name for b in bones if b.name in need]           # parents before children
    offsets = {n: ue_offset(bones[n]) for n in order}
    unit = 0.01 if obj.data.get("fpmp_scale") is None else obj.data["fpmp_scale"]
    scene = obj.users_scene[0] if obj.users_scene else bpy.context.scene
    keep_frame = scene.frame_current
    start, end = span

    locals_ = []
    for f in range(start, end + 1):
        scene.frame_set(f)
        cs = {n: _to_game(obj.pose.bones[n].matrix, offsets[n], unit) for n in order}
        locals_.append({n: relative(cs[n], cs[parent_of[n]]) if parent_of[n] in cs else cs[n] for n in order})
    scene.frame_set(keep_frame)

    fps = scene.render.fps / scene.render.fps_base
    steps = max(1, round(rate / fps))
    dt = 1.0 / (fps * steps)
    simulated = [d["bone"] for s in sims for d in s.defs]
    alpha = {d["bone"]: s.node["alpha"] for s in sims for d in s.defs}
    children = {n: [c for c in order if parent_of[c] == n] for n in order}

    def tick(loc):
        cs = {}
        for n in order:
            cs[n] = compose(loc[n], cs[parent_of[n]]) if parent_of[n] in cs else loc[n]
        for s in sims:
            out = s.evaluate(dt, cs, root)
            for name, t in out.items():
                a = alpha[name]
                if a < 1e-5:
                    continue
                if a < 1.0 - 1e-5:
                    par = cs.get(parent_of[name])
                    blended = _blend(loc[name], relative(t, par) if par else t, a)
                    t = compose(blended, par) if par else blended
                cs[name] = t
                stack = list(children[name])
                while stack:            # the bone's children follow it from their own locals
                    c = stack.pop()
                    cs[c] = compose(loc[c], cs[parent_of[c]])
                    stack += children[c]
        return cs

    for _ in range(round(PREROLL / dt)):
        cs = tick(locals_[0])
    frames = [cs]
    for i in range(1, len(locals_)):
        for k in range(1, steps + 1):
            cs = tick({n: _blend(locals_[i - 1][n], locals_[i][n], k / steps) for n in order})
        frames.append(cs)

    _push_down(obj)
    _write(obj, simulated, frames, start, offsets, unit, parent_of)
    Log.info("[Material Porter] dynamics: %d bones over %d frames in %.1fs" % (len(simulated), len(frames), time.time() - t0))
    return len(simulated), len(frames)


def _write(obj, simulated, frames, start, offsets, unit, parent_of):
    import numpy as np
    ad = obj.animation_data or obj.animation_data_create()
    action = bpy.data.actions.new("%s %s" % (obj.name, TRACK))
    ad.action = action
    inv = {n: o.inverted() for n, o in offsets.items()}
    curves = {}
    for name in simulated:
        bone, pb = obj.data.bones[name], obj.pose.bones[name]
        rest = bone.parent.matrix_local.inverted() @ bone.matrix_local if bone.parent else bone.matrix_local
        rest_inv = rest.inverted()
        rot_path = "rotation_quaternion" if pb.rotation_mode == 'QUATERNION' else "rotation_euler"
        locs, rots, prev = [], [], None
        for cs in frames:
            m = _from_game(cs[name], inv[name], unit)
            par = parent_of[name]
            basis = rest_inv @ (_from_game(cs[par], inv[par], unit).inverted() @ m if par in cs else m)
            loc, q, _ = basis.decompose()
            if prev is not None and q.dot(prev) < 0.0:
                q.negate()
            prev = q
            locs.append(loc)
            rots.append(q if rot_path == "rotation_quaternion" else q.to_euler(pb.rotation_mode))
        base = 'pose.bones["%s"].' % name
        for path, values, width in (("location", locs, 3), (rot_path, rots, len(rots[0]))):
            for i in range(width):
                fc = action.fcurve_ensure_for_datablock(obj, base + path, index=i)
                fc.keyframe_points.add(len(values))
                co = np.empty(2 * len(values), np.float32)
                co[0::2] = np.arange(start, start + len(values), dtype=np.float32)
                co[1::2] = [v[i] for v in values]
                fc.keyframe_points.foreach_set("co", co)
                fc.update()
    ad.action = None
    track = ad.nla_tracks.new()
    track.name = TRACK
    strip = track.strips.new(TRACK, start, action)
    strip.extrapolation = 'NOTHING'


def ui(layout, obj):
    """The panel's Dynamics row, on an armature with dynamics nodes."""
    if not obj.data.get(KEY):
        return
    row = layout.row(align=True)
    if _track(obj) is not None:
        on = is_on(obj)
        row.operator("fpmp.dynamics_on", text="Dynamics", icon='PHYSICS', depress=on).on = not on
    row.operator("fpmp.dynamics_bake", text="Simulate", icon='FILE_REFRESH')
```

- [ ] **Step 4: Run**, expect the bake checks to pass.

### Task 4: Wiring (import, emote import, Rig panel)

**Files:**
- Modify: `plugins/Blender/fortnite_porting/material_porter/mesh_hooks.py` (`_dynamics(ctx)` after `_correctives`)
- Modify: `plugins/Blender/fortnite_porting/material_porter/anim_hooks.py` (`end(armature)`)
- Modify: `plugins/Blender/fortnite_porting/processing/context/anim_context.py` (one `# MP` line at the end of `import_anim_data`)
- Modify: `plugins/Blender/fortnite_porting/operator/rig_ui.py` (two operators, `dynamics_bake.ui` in the panel)
- Test: `tests/plugin/dynamics_check.py` (wiring section)

- [ ] **Step 1: Failing tests:** `mesh_hooks._dynamics` on a fake ctx with one mesh whose meta names a synthetic
  dump stores `fpmp_dynamics` on its skeleton with only nodes whose bones exist, and nothing on a skeleton without
  them; `anim_hooks.end(arm)` bakes (a `Dynamics` track appears); the two operators are registered
  (`bpy.ops.fpmp.dynamics_bake`, `bpy.ops.fpmp.dynamics_on`) and run on the synthetic armature.
- [ ] **Step 2: Run**, expect FAIL.
- [ ] **Step 3: Implement:**

`mesh_hooks.py`:
```python
def _dynamics(ctx):
    """The parts' AnimDynamics nodes, kept on each skeleton that has their bones for the emote bakes."""
    from ..processing.context import dynamics_bake, dynamics_read
    paths = [m["Meta"]["AnimBlueprint"] for m in ctx.imported_meshes if isinstance(m.get("Meta"), dict) and m["Meta"].get("AnimBlueprint")]
    if not paths:
        return
    found = dynamics_read.nodes(paths)
    for skeleton in {m.get("Skeleton") for m in ctx.imported_meshes if _alive(m.get("Skeleton"))}:
        mine = [n for n in found if n["bone"] in skeleton.data.bones]
        if mine:
            skeleton.data[dynamics_bake.KEY] = json.dumps(mine)
            Log.info("%s: %d dynamic bones" % (skeleton.name, len(mine)))
```
called from `after_parts` right after `_correctives(ctx)` (add `import json` at the top if missing).

`anim_hooks.py`:
```python
def end(armature):
    """The character's dynamic bones follow the new animation."""
    from ..logger import Log
    from ..processing.context import dynamics_bake
    if armature is None or not armature.data.get(dynamics_bake.KEY):
        return
    try:
        dynamics_bake.bake(armature)
    except Exception as e:
        Log.error("[Material Porter] dynamics bake (%s: %s)" % (type(e).__name__, e))
```
`anim_context.py`, last line of `import_anim_data`:
```python
        from ...material_porter.anim_hooks import end; end(target_skeleton)     # MP: dynamic bones follow the animation
```
`rig_ui.py`: operators

```python
class FPMP_OT_DynamicsBake(bpy.types.Operator):
    bl_idname = "fpmp.dynamics_bake"
    bl_label = "Simulate Dynamics"
    bl_description = "Run Fortnite's dynamic bones over the current animation and key them in a Dynamics layer"
    bl_options = {'REGISTER', 'UNDO'}
    rate: bpy.props.IntProperty(name="Steps per Second", default=60, min=10, max=240)

    @classmethod
    def poll(cls, context):
        from ..processing.context import dynamics_bake
        obj = _rig(context)
        if obj is None or not obj.data.get(dynamics_bake.KEY):
            return False
        if dynamics_bake.frame_range(obj) is None:
            cls.poll_message_set("No animation on this armature")
            return False
        return True

    def execute(self, context):
        from ..processing.context import dynamics_bake
        bones, frames = dynamics_bake.bake(_rig(context), self.rate)
        self.report({'INFO'}, "%d dynamic bones over %d frames" % (bones, frames))
        return {'FINISHED'}


class FPMP_OT_DynamicsOn(bpy.types.Operator):
    bl_idname = "fpmp.dynamics_on"
    bl_label = "Dynamics On/Off"
    bl_description = "Play the simulated dynamic bones, or the animation's own"
    bl_options = {'REGISTER', 'UNDO'}
    on: bpy.props.BoolProperty(default=True)

    def execute(self, context):
        from ..processing.context import dynamics_bake
        dynamics_bake.set_on(_rig(context), self.on)
        return {'FINISHED'}
```
In `FPMP_PT_CreatureRig.draw`, after `deform_rig.ui(col, obj)`: `dynamics_bake.ui(col, obj)` (import at the top
with `deform_rig`). Add both classes to `classes`.

- [ ] **Step 4: Run** `dynamics_check.py` and the other suites (`rig_check`, `deform_rig_check`,
  `official_rig_check`, `metahuman_board_check`). Expected: all pass.

### Task 5: App export (`ExportContext.AnimBlueprint`)

**Files:**
- Create: `src/FortnitePorting.Exporting/MaterialPorter/ExportContext.AnimBlueprint.cs`
- Modify: `src/FortnitePorting.Exporting/Context/ExportContext.Fortnite.cs` (one `// MP` line after `DeformRig`)
- Modify: `src/FortnitePorting.Exporting/Models/ExportMeta.cs` (one `// MP` field)

- [ ] **Step 1: Implement** (mirrors `ExportContext.DeformRig.cs`):

```csharp
/// <summary>The part's anim blueprint dumped once next to it when it runs dynamic bones (AnimDynamics or
/// RigidBody nodes), for the plugin's dynamics bake; null otherwise.</summary>
public string? AnimBlueprint(UObject additionalData)
{
    if (!additionalData.TryGetValue(out FSoftObjectPath animClass, "AnimClass")
        || !animClass.TryLoad(out UClass animBlueprint)
        || animBlueprint.ClassDefaultObject is not { } defaults
        || !defaults.TryLoad(out var animDefaults)
        || !animDefaults.Properties.Any(p => p.Name.Text.StartsWith("AnimGraphNode_AnimDynamics")
                                             || p.Name.Text.StartsWith("AnimGraphNode_RigidBody"))
        || animBlueprint.Owner is not { } package)
        return null;
    var path = GetExportPath(animBlueprint, "animbp.json");
    try
    {
        if (!File.Exists(path))
            File.WriteAllText(path, JsonConvert.SerializeObject(package.GetExports(),
                new JsonSerializerSettings { ReferenceLoopHandling = ReferenceLoopHandling.Ignore }));
        return path;
    }
    catch (Exception e)     // a package that won't dump costs the dynamics, never the export
    {
        Serilog.Log.Warning("[Material Porter] {Anim}: anim blueprint not dumped: {Error}", animBlueprint.Name, e.Message);
        return null;
    }
}
```
`ExportMeta.cs`: `public string? AnimBlueprint;     // MP: the part's anim blueprint dump (dynamic bones)`.
`ExportContext.Fortnite.cs`: `exportPart.Meta.AnimBlueprint = AnimBlueprint(additionalData);     // MP`.
- [ ] **Step 2: Build** `bash testapp.sh test` (devtools). Expected: build OK, test instance up on 24322.
- [ ] **Step 3: Export** VibrantShell (`python fpdev.py export dyn_vs --type Outfit --path <VibrantShell CID>`):
  the payload's body part meta has `AnimBlueprint` pointing at an existing `.animbp.json` with 14 AnimDynamics nodes.

### Task 6: Real check (owner devtools)

**Files:**
- Create: `fpfork-private/devtools/dynamics_real.py`

- [ ] **Step 1:** Write the script: on the imported VibrantShell blend, import an emote payload onto the armature
  (`Importer.Import` with the armature active), then check: `fpmp_dynamics` holds 14 nodes in LinkID order
  (pendant 1, 2, 3 per side consecutive), a `Dynamics` track exists, every simulated bone's swing relative to its
  animated pose stays within its angular limits + 1°, its head within 0.5 cm of the animated head, no NaN, the bake
  time printed.
- [ ] **Step 2:** `python fpdev.py import dyn_vs`, export an emote payload, run
  `python fpdev.py script dyn_vs dynamics_real.py -- <emote payload>`. Expected: all checks OK, bake under 10 s for a
  20 s emote (report the number).
- [ ] **Step 3:** Commit both repos with the user's approved wording.
