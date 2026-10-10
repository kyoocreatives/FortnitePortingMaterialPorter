"""Fortnite's RigidBody anim node replayed with the engine's Chaos immediate step: physics asset bodies (simulated ones
move, the others follow the pose), joints, contacts, the moving simulation space. Game numbers (cm, kg, s); no bpy."""
import math

from mathutils import Quaternion, Vector

from ...logger import Log
from . import rigid_contacts as contacts
from .dynamics_solver import compose, relative
from .rigid_bodies import conditioning, mass_properties
from .rigid_joints import Joint, SolverBody, angvel, integrate, settings

MAX_STEP = 1.0 / 30.0
MAX_STEPS = 4
STEP_AVERAGE = 100.0
CORIOLIS, CENTRIFUGAL, EULER = 0.5, 1.0, 1.0
ZERO = Vector((0.0, 0.0, 0.0))


def _clamp(v, most):
    return v.normalized() * most if v.length > most else v


class Actor:
    """One physics asset body: mass properties and its state (centre of mass frame, simulation space)."""

    def __init__(self, index, body, simulated):
        self.index, self.bone, self.simulated = index, body["bone"], simulated
        self.mass = mass_properties(body)
        self.lin_damp, self.ang_damp, self.gravity = body["linear_damping"], body["angular_damping"], body["gravity"]
        self.inv_il = self.mass.inv_inertia.copy()
        self.x, self.r, self.v, self.w = ZERO.copy(), Quaternion(), ZERO.copy(), ZERO.copy()
        self.target = None
        inv_rom = self.mass.rom.conjugated()
        self.segments = [(inv_rom @ (s.a - self.mass.com), inv_rom @ (s.b - self.mass.com), s.radius) for s in self.mass.shapes]

    def place(self, t):
        """The CoM frame of the body for a bone transform (simulation space)."""
        return t[1] + t[0] @ self.mass.com, t[0] @ self.mass.rom

    def bone_transform(self):
        rot = self.r @ self.mass.rom.conjugated()
        return rot, self.x - rot @ self.mass.com


class RigidSim:
    """One RigidBody node: evaluate() takes the pose reaching it and returns its simulated bodies' bones."""

    def __init__(self, node, parent_of):
        self.node = node
        asset = node["physics"]
        default = node.get("use_default_as_simulated", False)
        bodies = [b for b in asset["bodies"] if b["bone"] in parent_of]
        self.actors = [Actor(i, b, b["type"] == "Simulated" or (default and b["type"] == "Default")) for i, b in enumerate(bodies)]
        for a in self.actors:
            if a.simulated and (not a.mass.shapes or min(a.mass.inv_inertia) <= 0.0):
                Log.info("[Material Porter] dynamics: %s has no shape the bake knows, it follows the pose" % a.bone)
                a.simulated = False
        by_bone = {a.bone: a for a in self.actors}
        depth = {}
        for bone in parent_of:
            d, walk = 0, parent_of[bone]
            while walk:
                d, walk = d + 1, parent_of.get(walk)
            depth[bone] = d
        self.depth = depth
        self.solver = asset["solver"]
        self.joints, ignore = [], set()
        for c in asset["constraints"]:
            child, parent = by_bone.get(c["bone1"]), by_bone.get(c["bone2"])
            if child is None or parent is None or not (child.simulated or parent.simulated):
                continue
            joint = Joint(settings(c), parent.index, child.index)
            joint.order = max(parent.index, child.index)
            self.joints.append(joint)
            if c["disable_collision"]:
                ignore.add(frozenset((parent.index, child.index)))
        self._sort()
        if self.solver["linear_solver"]:
            for a in self.actors:
                if a.simulated:
                    arms = [j.s["frame0"][1] if j.parent == a.index else j.s["frame1"][1] for j in self.joints
                            if a.index in (j.parent, j.child) and any(m != 0 for m in j.s["linear"])]
                    a.inv_il = Vector([x * s for x, s in zip(a.mass.inv_inertia, conditioning(a.mass, arms))])
        for j in self.joints:       # frames from bone space to each body's CoM frame
            for side, actor in (("frame0", self.actors[j.parent]), ("frame1", self.actors[j.child])):
                rot, pos = j.s[side]
                inv = actor.mass.rom.conjugated()
                j.s[side] = (inv @ rot, inv @ (pos - actor.mass.com))
        disabled = {frozenset((by_bone[a].index, by_bone[b].index)) for a, b in asset["disabled"] if a in by_bone and b in by_bone}
        n = len(self.actors)
        self.pairs = [(i, k) for i in range(n) for k in range(i + 1, n)
                      if (self.actors[i].simulated or self.actors[k].simulated)
                      and frozenset((i, k)) not in disabled and frozenset((i, k)) not in ignore
                      and self.actors[i].segments and self.actors[k].segments]
        self.started, self.step_time = False, None
        self.space_hist = None

    def _sort(self):
        """Joints by level (distance from a kinematic body), then insertion order: chains solve root to tip."""
        self.joints.sort(key=lambda j: j.order)
        level, frontier = {}, set()
        for k, j in enumerate(self.joints):
            if not (self.actors[j.parent].simulated and self.actors[j.child].simulated):
                level[k] = 0
                frontier |= {j.parent, j.child}
        depth = 0
        while len(level) < len(self.joints):
            depth += 1
            grown = {k for k, j in enumerate(self.joints) if k not in level and (j.parent in frontier or j.child in frontier)}
            if not grown:
                grown = {k for k in range(len(self.joints)) if k not in level}
            for k in grown:
                level[k] = depth
                frontier |= {self.joints[k].parent, self.joints[k].child}
        order = sorted(range(len(self.joints)), key=lambda k: (level[k], k))
        self.joints = [self.joints[k] for k in order]

    def bones(self):
        base = [self.node["base_bone"]] if self.node["space"] == "BaseBoneSpace" and self.node.get("base_bone") else []
        return [a.bone for a in self.actors] + base

    def outputs(self):
        return [a.bone for a in self.actors if a.simulated]

    # --- spaces ---
    def _space(self, cs, root):
        if self.node["space"] == "BaseBoneSpace":
            return cs.get(self.node.get("base_bone") or root, (Quaternion(), ZERO.copy()))
        return Quaternion(), ZERO.copy()        # component and world space: the component sits at the origin

    def _motion(self, space, dt):
        """The moving space's velocity and acceleration (linear, angular) in simulation space."""
        st = self.node["sim_space"]
        if st["world_alpha"] == 0.0 or dt < 1e-8:
            return None
        if self.node["space"] == "WorldSpace":
            return Vector(st["linear_velocity"]), Vector(st["angular_velocity"]), ZERO.copy(), ZERO.copy()
        (r1, p1), (r2, p2), prev_dt = self.space_hist
        vs = (space[1] - p1) / dt
        vs_prev = (p1 - p2) / prev_dt
        acc = (vs - vs_prev) / dt
        ws = angvel(r1, space[0], dt)
        ws_prev = angvel(r2, r1, prev_dt)
        alpha = (ws - ws_prev) / dt
        vs.z *= st["velocity_scale_z"]
        acc.z *= st["velocity_scale_z"]
        vs = _clamp(vs, st["max_linear_velocity"]) + Vector(st["linear_velocity"])
        ws = _clamp(ws, st["max_angular_velocity"]) + Vector(st["angular_velocity"])
        acc = _clamp(acc, st["max_linear_acceleration"])
        alpha = _clamp(alpha, st["max_angular_acceleration"])
        inv = space[0].conjugated()
        self.space_hist = ((space[0].copy(), space[1].copy()), (r1, p1), dt)
        k = st["world_alpha"]
        return (inv @ vs) * k, (inv @ ws) * k, (inv @ acc) * k, (inv @ alpha) * k

    # --- evaluation ---
    def evaluate(self, dt, cs, root):
        space = self._space(cs, root)
        sim = {a.bone: relative(cs[a.bone], space) for a in self.actors}
        if not self.started:
            # the reset frame: bodies at the pose, at rest; nothing is simulated so the pose passes through
            for a in self.actors:
                a.x, a.r = a.place(sim[a.bone])
                a.v, a.w, a.target = ZERO.copy(), ZERO.copy(), None
            self.space_hist = ((space[0].copy(), space[1].copy()), (space[0].copy(), space[1].copy()), 1.0 / 30.0)
            self.started = True
            return {}
        for a in self.actors:
            if not a.simulated:
                a.target = a.place(sim[a.bone])
        world_gravity = Vector(self.node["gravity"]) if self.node["gravity"] is not None else Vector((0.0, 0.0, -980.0))
        gravity = space[0].conjugated() @ world_gravity
        motion = self._motion(space, dt)
        delta = min(dt, MAX_STEP * MAX_STEPS)
        if self.step_time is None:
            self.step_time = min(delta, MAX_STEP)
        else:
            self.step_time = min(self.step_time + (delta - self.step_time) / STEP_AVERAGE, MAX_STEP)
        steps = max(1, min(MAX_STEPS, round(delta / self.step_time)))
        for k in range(steps):
            self._step(self.step_time, (k + 1) / steps, gravity, motion)
        out = {}
        for a in sorted(self.actors, key=lambda a: self.depth.get(a.bone, 0)):      # parents first, as the engine writes them
            if a.simulated:
                out[a.bone] = compose(a.bone_transform(), space)
        return out

    def _step(self, h, frac, gravity, motion):
        drag = Vector(self.node["sim_space"]["drag"]) if motion else ZERO
        damping_alpha = self.node["sim_space"]["damping_alpha"]
        for a in self.actors:
            if not a.simulated:
                continue
            dv = gravity * h if a.gravity else ZERO.copy()
            dw = ZERO.copy()
            target_v, target_w = ZERO.copy(), ZERO.copy()
            if motion:
                vs, ws, acc, alpha = motion
                wxr = ws.cross(a.x)
                push = acc + Vector([drag[i] * (a.v + vs + wxr)[i] for i in range(3)]) + CORIOLIS * 2.0 * ws.cross(a.v) \
                    + CENTRIFUGAL * ws.cross(wxr) + EULER * alpha.cross(a.x)
                dv -= push * h
                dw -= alpha * h
                target_v, target_w = -damping_alpha * vs, -damping_alpha * ws
            a.v = (a.v + dv).lerp(target_v, min(1.0, a.lin_damp * h))
            a.w = (a.w + dw).lerp(target_w, min(1.0, a.ang_damp * h))
        bodies = []
        for a in self.actors:
            if a.simulated:
                bodies.append(SolverBody(a.x.copy(), a.r.copy(), a.x + a.v * h, integrate(a.r, a.w, h), a.v.copy(), a.w.copy(),
                                         a.mass.inv_mass, a.inv_il, True))
                continue
            if a.target is not None:
                # quirk kept: the fraction applies to the already moved pose, so 3+ sub-steps don't move linearly
                tx, tr = a.target
                nx, nr = (tx.copy(), tr.copy()) if frac >= 1.0 - 1e-4 else (a.x.lerp(tx, frac), a.r.slerp(tr, frac))
                a.v = (nx - a.x) / h if (nx - a.x).length > 1e-8 else ZERO.copy()
                a.w = angvel(a.r, nr, h) if a.r.rotation_difference(nr).angle > 1e-6 else ZERO.copy()
                a.x, a.r = nx, nr
                if frac >= 1.0 - 1e-4:
                    a.target = None
            else:
                a.v, a.w = ZERO.copy(), ZERO.copy()
            bodies.append(SolverBody(a.x - a.v * h, integrate(a.r, -a.w, h), a.x.copy(), a.r.copy(), a.v.copy(), a.w.copy(),
                                     0.0, ZERO.copy(), False))
        for j in self.joints:
            j.init(h, bodies)
        touching = contacts.detect(bodies, self.pairs, [a.segments for a in self.actors], self.solver["cull_distance"])
        for _ in range(self.solver["position_iterations"]):
            for j in self.joints:
                j.apply_position(h, bodies)
            contacts.apply_position(touching, bodies)
        for b in bodies:
            if b.dynamic:
                b.v, b.w = b.v + b.dp / h, b.w + b.dq / h
        its = self.solver["velocity_iterations"]
        for it in range(its):
            for j in self.joints:
                j.apply_velocity(bodies)
            contacts.apply_velocity(touching, bodies, h, gravity.length, it == its - 1)
        for b in bodies:
            b.apply_corrections()
        for j in self.joints:
            j.init_projection(h, bodies)
        its = self.solver["projection_iterations"]
        for it in range(its):
            for j in self.joints:
                if it == 0:
                    j.apply_teleport(bodies)
                j.apply_projection(h, bodies, it == its - 1)
        for a, b in zip(self.actors, bodies):
            if a.simulated:
                a.x, a.r = b.corrected()
                a.v, a.w = b.v, b.w
