"""Physics asset joints solved as Chaos's default ("linear", cached) joint solver does: rows built once per step from
the predicted poses, then position iterations, velocity rows and projection. Body 0 is the parent, body 1 the child."""
import math

from mathutils import Matrix, Quaternion, Vector

from .rigid_bodies import rotator

X, Y, Z = Vector((1.0, 0.0, 0.0)), Vector((0.0, 1.0, 0.0)), Vector((0.0, 0.0, 1.0))
ZERO = Vector((0.0, 0.0, 0.0))
TWIST, SWING2, SWING1 = 0, 1, 2
AXIS_OF = {TWIST: X, SWING2: Y, SWING1: Z}
SOFT_ANGULAR_STIFFNESS, SOFT_ANGULAR_DAMPING = 100000.0, 1000.0
SOFT_LINEAR_STIFFNESS, SOFT_LINEAR_DAMPING = 1.5, 1.2
DRIVE_ANGULAR_SCALE = 1.5
POSITION_TOLERANCE, ANGLE_TOLERANCE = 0.025, 0.001
MIN_PARENT_MASS_RATIO, MAX_INERTIA_RATIO = 0.2, 5.0
DEGENERATE = -0.998
VELOCITY_PROJECTION = 0.1
SMALL = 1e-8


def integrate(q, w, dt):
    """The engine's first-order rotation update by an angular velocity over dt."""
    dq = Quaternion((0.0, w.x, w.y, w.z)) @ q
    return Quaternion([a + 0.5 * b * dt for a, b in zip(q, dq)]).normalized()


def angvel(q0, q1, dt):
    """Angular velocity taking q0 to q1 in dt (finite difference of the quaternions)."""
    if q1.dot(q0) < 0.0:
        q1 = -q1
    d = Quaternion([(b - a) / dt for a, b in zip(q0, q1)]) @ q0.conjugated()
    return Vector((d.x, d.y, d.z)) * 2.0


def shortest(q, ref):
    return -q if q.dot(ref) < 0.0 else q


def asin5(x):
    x2 = x * x
    return x * (1.0 + x2 * (1.0 / 6.0 + x2 * (3.0 / 40.0)))


def world_inverse(q, inv_local):
    r = q.to_matrix()
    return r @ Matrix.Diagonal(inv_local) @ r.transposed()


class SolverBody:
    """A body's step state in its centre-of-mass frame: start (x, r), predicted (p, q), velocities, corrections."""
    __slots__ = ("x", "r", "p", "q", "v", "w", "inv_m", "inv_il", "dynamic", "dp", "dq")

    def __init__(self, x, r, p, q, v, w, inv_m, inv_il, dynamic):
        self.x, self.r, self.p, self.q, self.v, self.w = x, r, p, q, v, w
        self.inv_m, self.inv_il, self.dynamic = inv_m, inv_il, dynamic
        self.dp, self.dq = ZERO.copy(), ZERO.copy()

    def corrected(self):
        return self.p + self.dp, (integrate(self.q, self.dq, 1.0) if self.dynamic and self.dq.length_squared > 0.0 else self.q)

    def apply_corrections(self):
        self.p, self.q = self.corrected()
        self.dp, self.dq = ZERO.copy(), ZERO.copy()


def _frame(pos, pri, sec):
    """A constraint frame from its position and primary/secondary axes (twist X, Y, Z = X x Y)."""
    x, y = Vector(pri).normalized(), Vector(sec).normalized()
    m = Matrix((x, y, x.cross(y))).transposed()
    return m.to_quaternion(), Vector(pos)


def settings(c):
    """A physics asset constraint (physics_read) as the solver's joint settings, after the engine's sanitising."""
    motion = {"Free": 0, "Limited": 1, "Locked": 2}
    lin = c["linear"]
    linear = [motion.get(m, 2) for m in lin["motions"]]
    if lin["limit"] < 0.1:
        linear = [2 if m == 1 else m for m in linear]
    cone, twist = c["cone"], c["twist"]
    angular = [motion.get(twist["TwistMotion"], 0), motion.get(cone["Swing2Motion"], 0), motion.get(cone["Swing1Motion"], 0)]
    limits = [math.radians(twist["limit"]), math.radians(cone["swing2"]), math.radians(cone["swing1"])]
    soft = [twist["soft"], cone["soft"], cone["soft"]]
    for i in range(3):
        if angular[i] == 1 and limits[i] < 0.01:
            if soft[i]:
                limits[i] = 0.01
            else:
                angular[i] = 2
    twist_soft = twist["soft"] and angular[TWIST] != 2
    swing_soft = cone["soft"] and not (angular[SWING1] == 2 and angular[SWING2] == 2)
    drive = c["drive"]
    slerp = drive["mode"] == "SLERP"
    pick = drive["slerp"] if slerp else None
    proj = c["projection"]
    return {"linear": linear, "linear_limit": lin["limit"],
            "linear_soft": lin["soft"] and any(m == 1 for m in linear),
            "linear_k": lin["stiffness"] * SOFT_LINEAR_STIFFNESS, "linear_c": lin["damping"] * SOFT_LINEAR_DAMPING,
            "angular": angular, "limits": limits,
            "twist_soft": twist_soft, "twist_k": twist["stiffness"] * SOFT_ANGULAR_STIFFNESS if twist_soft else 0.0,
            "twist_c": twist["damping"] * SOFT_ANGULAR_DAMPING if twist_soft else 0.0,
            "swing_soft": swing_soft, "swing_k": cone["stiffness"] * SOFT_ANGULAR_STIFFNESS if swing_soft else 0.0,
            "swing_c": cone["damping"] * SOFT_ANGULAR_DAMPING if swing_soft else 0.0,
            "slerp": slerp and (pick["position"] or pick["velocity"]) and 2 not in angular,
            "twist_swing": (not slerp) and any(drive[k]["position"] or drive[k]["velocity"] for k in ("twist", "swing")),
            "drive_k": [DRIVE_ANGULAR_SCALE * d["stiffness"] if d["position"] else 0.0 for d in
                        ((pick,) * 3 if slerp else (drive["twist"], drive["swing"], drive["swing"]))],
            "drive_c": [DRIVE_ANGULAR_SCALE * d["damping"] if d["velocity"] else 0.0 for d in
                        ((pick,) * 3 if slerp else (drive["twist"], drive["swing"], drive["swing"]))],
            "drive_on": [True, True, True] if slerp else [drive["twist"]["position"] or drive["twist"]["velocity"],
                                                          drive["swing"]["position"] or drive["swing"]["velocity"],
                                                          drive["swing"]["position"] or drive["swing"]["velocity"]],
            "drive_target": rotator(*drive["target"]),
            "projection": proj["on"], "linear_alpha": proj["linear_alpha"] if proj["on"] else 0.0,
            "angular_alpha": proj["angular_alpha"] if proj["on"] else 0.0,
            "teleport": proj["linear_tolerance"] if proj["on"] else -1.0,
            "parent_scale": 0.0 if c["parent_dominates"] else 1.0, "mass_conditioning": c["mass_conditioning"],
            "frame0": _frame(c["pos2"], c["pri2"], c["sec2"]), "frame1": _frame(c["pos1"], c["pri1"], c["sec1"])}


class Row:
    """One linearised constraint row: axis, initial error, lever arms, mass terms, accumulated impulse."""
    __slots__ = ("axis", "cx", "arm0", "arm1", "ia0", "ia1", "im", "lam", "soft", "ks", "kd", "softim", "limit", "check",
                 "locked", "vx")

    def __init__(self, axis, cx, arm0=ZERO, arm1=ZERO, limit=0.0, check=False, locked=False):
        flip = cx < 0.0
        self.axis, self.cx = (-axis if flip else axis.copy()), abs(cx)
        self.arm0, self.arm1, self.limit, self.check, self.locked = arm0, arm1, limit, check, locked
        self.lam, self.soft, self.ks, self.kd, self.softim, self.vx = 0.0, False, 0.0, 0.0, 1.0, 0.0

    def mass(self, inv_m0, inv_i0, inv_m1, inv_i1, linear):
        if linear:
            a0, a1 = self.arm0.cross(self.axis), self.arm1.cross(self.axis)
            self.ia0, self.ia1 = inv_i0 @ a0, inv_i1 @ a1
            self.im = inv_m0 + a0.dot(self.ia0) + inv_m1 + a1.dot(self.ia1)
        else:
            self.ia0, self.ia1 = inv_i0 @ self.axis, inv_i1 @ self.axis
            self.im = self.axis.dot(self.ia0) + self.axis.dot(self.ia1)

    def soften(self, k, c, dt):
        """Acceleration-mode soft constraint: stiffness and damping scaled by the row's inverse mass."""
        scale = 1.0 / self.im if self.im > 0.0 else 0.0
        self.soft, self.ks, self.kd = True, scale * k * dt * dt, scale * c * dt
        self.softim = (self.ks + self.kd) * self.im + 1.0

    def excess(self, e, tolerance):
        """The error beyond the limit, or None when inside (or within tolerance)."""
        if not self.check:
            return e
        if e > self.limit:
            e -= self.limit
        elif e < -self.limit:
            e += self.limit
        else:
            return None
        return e if abs(e) > tolerance else None


class Joint:
    def __init__(self, s, parent, child):
        self.s, self.parent, self.child = s, parent, child          # parent/child: indices into the solver bodies
        self.lin_rows, self.rot_rows, self.drive_rows = [], [], []
        self.lin_simd = self.rot_simd = self.drive_simd = False

    # --- per-step state ---
    def _state(self, b0, b1):
        f0, f1 = self.s["frame0"], self.s["frame1"]
        self.cx0, self.cx1 = b0.p + b0.q @ f0[1], b1.p + b1.q @ f1[1]
        self.cr0, self.cr1 = b0.q @ f0[0], shortest(b1.q @ f1[0], b0.q @ f0[0])

    def _masses(self, b0, b1, projection=False):
        s = self.s
        if projection:
            self.inv_m0, self.inv_i0 = 0.0, Matrix.Diagonal(ZERO)
            self.inv_m1 = b1.inv_m
            self.inv_i1 = Matrix.Diagonal(Vector([min(b1.inv_il)] * 3))
            return
        m0, m1 = b0.inv_m * s["parent_scale"], b1.inv_m
        i0, i1 = b0.inv_il * s["parent_scale"], b1.inv_il.copy()
        if s["mass_conditioning"]:
            m0, m1, i0, i1 = condition(m0, m1, i0, i1)
        self.inv_m0 = m0 if b0.dynamic else 0.0
        self.inv_m1 = m1 if b1.dynamic else 0.0
        self.inv_i0 = world_inverse(b0.q, i0) if b0.dynamic else Matrix.Diagonal(ZERO)
        self.inv_i1 = world_inverse(b1.q, i1) if b1.dynamic else Matrix.Diagonal(ZERO)

    def init(self, dt, bodies, simd=True):
        b0, b1 = bodies[self.parent], bodies[self.child]
        f0, f1 = self.s["frame0"], self.s["frame1"]
        scale = min(1.0, 3600.0 * dt * dt)
        self.pos_tol, self.ang_tol = scale * POSITION_TOLERANCE, scale * ANGLE_TOLERANCE
        self.init_x0, self.init_x1 = b0.x + b0.r @ f0[1], b1.x + b1.r @ f1[1]
        ir0 = b0.r @ f0[0]
        ir1 = shortest(b1.r @ f1[0], ir0)
        self._state(b0, b1)
        self.wdt0, self.wdt1 = angvel(ir0, self.cr0, 1.0), angvel(ir1, self.cr1, 1.0)
        self._masses(b0, b1)
        self.simd = simd
        self._position_rows(dt, b0, b1, reset=True)
        self._rotation_rows(dt, reset=True)
        self._drive_rows(dt)

    def init_projection(self, dt, bodies):
        s = self.s
        has_lin = s["projection"] and (s["linear_alpha"] > 0.0 or s["teleport"] > 0.0)
        has_ang = s["projection"] and s["angular_alpha"] > 0.0
        if not (has_lin or has_ang):
            return
        b0, b1 = bodies[self.parent], bodies[self.child]
        self._state(b0, b1)
        self._masses(b0, b1, projection=True)
        if has_lin:
            self._position_rows(dt, b0, b1, reset=False)
        if has_ang:
            self._rotation_rows(dt, reset=False)

    # --- rows ---
    def _position_rows(self, dt, b0, b1, reset):
        s, lam = self.s, [r.lam for r in self.lin_rows] if not reset else None
        lin = s["linear"]
        self.lin_rows, self.lin_simd = [], self.simd and all(m == 2 for m in lin)
        if all(m == 0 for m in lin):
            return
        if any(m == 2 for m in lin):
            arm0, arm1 = self.cx1 - b0.p, self.cx1 - b1.p
            dx, axes = self.cx1 - self.cx0, self.cr0.to_matrix()
            cx = [0.0, 0.0, 0.0]
            for k in range(3):
                if lin[k] == 2:
                    cx[k] = dx.dot(axes.col[k])
                    arm0 = arm0 - axes.col[k] * cx[k]
            for k in range(3):
                if lin[k] == 2:
                    self.lin_rows.append(Row(axes.col[k].copy(), cx[k], arm0, arm1, locked=True))
        if any(m == 1 for m in lin):
            # spherical: the axis between the connectors plus two drift-stopping axes (other limited mixes as spherical)
            arm0, arm1 = self.cx1 - b0.p, self.cx1 - b1.p
            d = self.cx1 - self.cx0
            length = d.length
            a0 = d / length if length > SMALL else X.copy()
            a1 = a0.cross(X) if abs(abs(a0.dot(X)) - 1.0) > SMALL else (a0.cross(Y) if abs(abs(a0.dot(Y)) - 1.0) > SMALL else a0.cross(Z))
            a2 = a0.cross(a1)
            for axis, cxv in ((a0, length), (a1, 0.0), (a2, 0.0)):
                row = Row(axis, cxv, arm0, arm1, s["linear_limit"], check=True)
                self.lin_rows.append(row)
        for i, row in enumerate(self.lin_rows):
            row.mass(self.inv_m0, self.inv_i0, self.inv_m1, self.inv_i1, linear=True)
            if not row.locked and s["linear_soft"]:
                row.soften(s["linear_k"], s["linear_c"], dt)
            if lam is not None and i < len(lam):
                row.lam = lam[i]

    def _rotation_rows(self, dt, reset):
        s, lam = self.s, {r.vx: r.lam for r in self.rot_rows} if not reset else {}
        ang, limits = s["angular"], s["limits"]
        self.rot_rows, self.rot_simd = [], False
        if all(m == 0 for m in ang):
            return
        r01 = self.cr0.conjugated() @ self.cr1
        twist = Quaternion((r01.w, r01.x, 0.0, 0.0)).normalized() if r01.x != 0.0 else Quaternion()
        swing = r01 @ twist.conjugated()
        angle = 2.0 * math.acos(max(-1.0, min(1.0, twist.w)))
        if angle > math.pi:
            angle -= 2.0 * math.pi
        if twist.x < 0.0:
            angle = -angle
        r0s = self.cr0 @ swing
        swing1 = (r0s @ Z, 4.0 * math.atan2(swing.z, 1.0 + swing.w))
        swing2 = (r0s @ Y, 4.0 * math.atan2(swing.y, 1.0 + swing.w))
        twist_row = (self.cr1 @ X, angle)
        rows = {}
        self.rot_simd = self.simd and all(m == 1 for m in ang) and s["twist_soft"] and s["swing_soft"]
        if self.rot_simd:
            for index, (axis, a) in ((TWIST, twist_row), (SWING2, swing2), (SWING1, swing1)):
                rows[index] = Row(axis, a, limit=limits[index], check=True)
        else:
            degenerate = (self.cr0 @ X).dot(self.cr1 @ X) < DEGENERATE
            if ang[TWIST] == 1 and not degenerate:
                rows[TWIST] = Row(twist_row[0], twist_row[1], limit=limits[TWIST], check=True)
            s1, s2 = ang[SWING1], ang[SWING2]
            if s1 == 1 and s2 == 1:
                rows[SWING1] = Row(swing1[0], swing1[1], limit=limits[SWING1], check=True)
                rows[SWING2] = Row(swing2[0], swing2[1], limit=limits[SWING2], check=True)
            elif not degenerate and s1 == 1 and s2 != 1:
                rows[SWING1] = Row(swing1[0], swing1[1], limit=limits[SWING1], check=True) if s2 == 2 else \
                    self._dual_cone(SWING1, limits[SWING1])
            elif not degenerate and s2 == 1 and s1 != 1:
                rows[SWING2] = Row(swing2[0], swing2[1], limit=limits[SWING2], check=True) if s1 == 2 else \
                    self._dual_cone(SWING2, limits[SWING2])
            locked = [i for i in (TWIST, SWING1, SWING2) if ang[i] == 2]
            if locked:
                axes = locked_axes(self.cr0, self.cr1)
                comp = {TWIST: r01.x, SWING1: r01.z, SWING2: r01.y}
                for i in locked:
                    rows[i] = Row(axes[i], comp[i], locked=True)
        for index in (TWIST, SWING2, SWING1):
            row = rows.get(index)
            if row is None:
                continue
            row.vx = index          # the row's slot (kept for projection re-inits)
            row.mass(self.inv_m0, self.inv_i0, self.inv_m1, self.inv_i1, linear=False)
            soft = s["twist_soft"] if index == TWIST else s["swing_soft"]
            if soft and not row.locked:
                k, c = (s["twist_k"], s["twist_c"]) if index == TWIST else (s["swing_k"], s["swing_c"])
                row.soften(k, c, dt)
            row.lam = lam.get(index, 0.0)
            self.rot_rows.append(row)

    def _dual_cone(self, index, limit):
        other = Y if index == SWING1 else Z
        twist1, swing0 = self.cr1 @ X, self.cr0 @ other
        axis = swing0.cross(twist1)
        angle = 0.0
        if axis.length > 1e-4:
            axis.normalize()
            angle = math.asin(max(-1.0, min(1.0, -swing0.dot(twist1))))
        return Row(axis, angle, limit=limit, check=True)

    def _drive_rows(self, dt):
        s = self.s
        self.drive_rows, self.drive_simd = [], False
        if s["slerp"]:
            r01 = self.cr0.conjugated() @ self.cr1
            target = shortest(s["drive_target"], r01)
            err = target.conjugated() @ r01
            axes = self.cr1.to_matrix()
            for k, e in enumerate((err.x, err.y, err.z)):
                self.drive_rows.append(self._drive(axes.col[k].copy(), 2.0 * asin5(e), k, dt))
            self.drive_simd = self.simd
        elif s["twist_swing"]:
            err = (self.cr0 @ s["drive_target"]).conjugated() @ self.cr1
            twist_axis = err @ X
            values = {TWIST: 2.0 * err.x, SWING1: twist_axis.y, SWING2: -twist_axis.z}
            axes = {TWIST: self.cr1 @ X, SWING2: self.cr1 @ Y, SWING1: self.cr1 @ Z}
            locked = s["angular"]
            for k in (TWIST, SWING2, SWING1):
                if not s["drive_on"][k] or locked[k] == 2:
                    continue
                if (abs(values[k]) > self.ang_tol and s["drive_k"][k] > 0.0) or s["drive_c"][k] > 0.0:
                    self.drive_rows.append(self._drive(axes[k], values[k], k, dt))
            self.drive_simd = self.simd and len(self.drive_rows) == 3

    def _drive(self, axis, value, k, dt):
        row = Row(axis, 0.0)
        row.axis, row.cx = axis, value          # drives keep their signed error
        row.mass(self.inv_m0, self.inv_i0, self.inv_m1, self.inv_i1, linear=False)
        row.soften(self.s["drive_k"][k], self.s["drive_c"][k], dt)
        return row

    # --- position phase ---
    def apply_position(self, dt, bodies):
        b0, b1 = bodies[self.parent], bodies[self.child]
        self._apply_rotation(b0, b1)
        self._apply_linear(b0, b1)
        self._apply_drives(dt, b0, b1)

    def _push(self, b0, b1, row, lam, linear):
        if b0.dynamic:
            if linear:
                b0.dp = b0.dp + row.axis * (self.inv_m0 * lam)
            b0.dq = b0.dq + row.ia0 * lam
        if b1.dynamic:
            if linear:
                b1.dp = b1.dp - row.axis * (self.inv_m1 * lam)
            b1.dq = b1.dq - row.ia1 * lam

    def _apply_linear(self, b0, b1):
        rows = self.lin_rows
        if not rows:
            return
        if self.lin_simd:
            arm0, arm1 = rows[0].arm0, rows[0].arm1
            move = b1.dp - b0.dp + b1.dq.cross(arm1) - b0.dq.cross(arm0)
            lams = [(r.cx + move.dot(r.axis)) / r.im if r.im > 0.0 else 0.0 for r in rows]
            for r, lam in zip(rows, lams):
                r.lam += lam
            for r, lam in zip(rows, lams):
                self._push(b0, b1, r, lam, linear=True)
            return
        for r in rows:
            move = b1.dp - b0.dp + b1.dq.cross(r.arm1) - b0.dq.cross(r.arm0)
            e = r.excess(r.cx + move.dot(r.axis), self.pos_tol)
            if e is None:
                continue
            if r.soft:
                vdt = 0.0
                if r.kd > 1e-4:
                    c0 = self.cx0 + b0.dp + b0.dq.cross(r.arm0) - self.init_x0
                    c1 = self.cx1 + b1.dp + b1.dq.cross(r.arm1) - self.init_x1
                    vdt = (c0 - c1).dot(r.axis)
                lam = (r.ks * e - r.kd * vdt - r.lam) / r.softim
            elif r.im > 0.0:
                lam = e / r.im
            else:
                continue            # nothing on either side can move along this row
            r.lam += lam
            self._push(b0, b1, r, lam, linear=True)

    def _apply_rotation(self, b0, b1):
        rows = self.rot_rows
        if not rows:
            return
        if self.rot_simd:
            diff = b1.dq - b0.dq
            wdiff = self.wdt0 - (diff + self.wdt1)
            lams = []
            for r in rows:
                e = r.excess(r.cx + diff.dot(r.axis), self.ang_tol)
                lams.append(0.0 if e is None else (r.ks * e - (r.kd * r.axis.dot(wdiff) + r.lam)) / r.softim)
            if any(lams):
                for r, lam in zip(rows, lams):
                    r.lam += lam
                for r, lam in zip(rows, lams):
                    self._push(b0, b1, r, lam, linear=False)
            return
        for r in rows:
            e = r.excess(r.cx + (b1.dq - b0.dq).dot(r.axis), self.ang_tol)
            if e is None:
                continue
            if r.soft:
                wdt = 0.0
                if r.kd > 1e-4:
                    wdt = r.axis.dot((b0.dq + self.wdt0) - (b1.dq + self.wdt1))
                lam = (r.ks * e - r.kd * wdt - r.lam) / r.softim
            elif r.im > 0.0:
                lam = e / r.im
            else:
                continue
            r.lam += lam
            self._push(b0, b1, r, lam, linear=False)

    def _apply_drives(self, dt, b0, b1):
        rows = self.drive_rows
        if not rows:
            return
        diff = b1.dq - b0.dq
        wdiff = (b0.dq + self.wdt0) - (b1.dq + self.wdt1)
        lams = []
        for r in rows:
            if self.drive_simd:
                cx, cvdt = r.cx + diff.dot(r.axis), r.axis.dot(wdiff) + r.vx * dt
            else:
                diff, wdiff = b1.dq - b0.dq, (b0.dq + self.wdt0) - (b1.dq + self.wdt1)
                cx = r.cx + diff.dot(r.axis) if r.ks > 1e-4 else 0.0
                cvdt = (r.vx * dt + r.axis.dot(wdiff)) if r.kd > 1e-4 else 0.0
            lam = (r.ks * cx - r.kd * cvdt - r.lam) / r.softim
            if self.drive_simd:
                lams.append(lam)
                continue
            r.lam += lam
            self._drive_push(b0, b1, r, lam)
        for r, lam in zip(rows, lams):
            r.lam += lam
        for r, lam in zip(rows, lams):
            self._drive_push(b0, b1, r, lam)

    def _drive_push(self, b0, b1, r, lam):
        # drives turn the bodies only about the row axis
        if b0.dynamic:
            b0.dq = b0.dq + r.axis * (lam * r.axis.dot(r.ia0))
        if b1.dynamic:
            b1.dq = b1.dq - r.axis * (lam * r.axis.dot(r.ia1))

    # --- velocity phase ---
    def apply_velocity(self, bodies):
        b0, b1 = bodies[self.parent], bodies[self.child]
        if not self.rot_simd:
            for r in self.rot_rows:
                if not r.soft and abs(r.lam) > SMALL and r.im > 0.0:
                    lam = (b1.w - b0.w).dot(r.axis) / r.im
                    if b0.dynamic:
                        b0.w = b0.w + r.ia0 * lam
                    if b1.dynamic:
                        b1.w = b1.w - r.ia1 * lam
        rows = [r for r in self.lin_rows if not r.soft]
        if self.lin_simd:
            if not any(abs(r.lam) > SMALL for r in rows):
                return
            arm0, arm1 = rows[0].arm0, rows[0].arm1
            cv = (b1.v + b1.w.cross(arm1)) - (b0.v + b0.w.cross(arm0))
            lams = [cv.dot(r.axis) / r.im if r.im > 0.0 else 0.0 for r in rows]
            for r, lam in zip(rows, lams):
                self._velocity_push(b0, b1, r, lam)
            return
        for r in rows:
            if abs(r.lam) > SMALL and r.im > 0.0:
                cv = (b1.v + b1.w.cross(r.arm1)) - (b0.v + b0.w.cross(r.arm0))
                self._velocity_push(b0, b1, r, cv.dot(r.axis) / r.im)

    def _velocity_push(self, b0, b1, r, lam):
        if b0.dynamic:
            b0.v = b0.v + r.axis * (self.inv_m0 * lam)
            b0.w = b0.w + r.ia0 * lam
        if b1.dynamic:
            b1.v = b1.v - r.axis * (self.inv_m1 * lam)
            b1.w = b1.w - r.ia1 * lam

    # --- projection phase ---
    def apply_teleport(self, bodies):
        s, b1 = self.s, bodies[self.child]
        if not s["projection"] or not b1.dynamic or s["teleport"] <= 0.0:
            return
        for r in self.lin_rows:
            if r.soft:
                continue
            e = r.cx
            if r.check:
                if e > r.limit:
                    e -= r.limit
                elif e < -r.limit:
                    e += r.limit
                else:
                    continue
            if abs(e) > s["teleport"]:
                b1.dp = b1.dp - r.axis * e

    def apply_projection(self, dt, bodies, last):
        s = self.s
        b0, b1 = bodies[self.parent], bodies[self.child]
        if not s["projection"] or not b1.dynamic:
            return
        locked = all(m == 2 for m in s["linear"])
        alpha = s["angular_alpha"]
        if alpha > 0.0:
            diff, total = b1.dq - b0.dq, ZERO.copy()
            for r in self.rot_rows:
                if r.soft and not self.rot_simd:
                    continue
                if not self.rot_simd:
                    diff = b1.dq - b0.dq        # row by row, each sees the last one's turn
                e = r.excess(r.cx + diff.dot(r.axis), self.ang_tol)
                if e is None or r.axis.dot(r.ia1) <= 0.0:
                    continue
                dr = -r.ia1 * (alpha * e / r.axis.dot(r.ia1))
                if self.rot_simd:
                    total += dr
                    continue
                b1.dq = b1.dq + dr
                if locked and self.lin_rows:
                    b1.dp = b1.dp - alpha * dr.cross(self.lin_rows[min(r.vx, len(self.lin_rows) - 1)].arm1)
            if self.rot_simd and total.length_squared > 0.0:
                b1.dq = b1.dq + total
                if locked and self.lin_rows:
                    # the engine adds the arm correction once per linear row
                    b1.dp = b1.dp - alpha * total.cross(self.lin_rows[0].arm1) * len(self.lin_rows)
        alpha = s["linear_alpha"]
        if alpha > 0.0 and self.lin_rows:
            move_rows = [r for r in self.lin_rows if not r.soft]
            if self.lin_simd:
                arm0, arm1 = move_rows[0].arm0, move_rows[0].arm1
                move = b1.dp - b0.dp + b1.dq.cross(arm1) - b0.dq.cross(arm0)
                dp, dq = ZERO.copy(), ZERO.copy()
                for r in move_rows:
                    im = self.inv_m1 + r.arm1.cross(r.axis).dot(r.ia1)
                    lam = (r.cx + move.dot(r.axis)) / im
                    dp -= r.axis * (alpha * self.inv_m1 * lam)
                    dq -= r.ia1 * (alpha * lam)
                b1.dp, b1.dq = b1.dp + dp, b1.dq + dq
            else:
                for r in move_rows:
                    move = b1.dp - b0.dp + b1.dq.cross(r.arm1) - b0.dq.cross(r.arm0)
                    e = r.excess(r.cx + move.dot(r.axis), self.pos_tol)
                    if e is None:
                        continue
                    lam = e / (self.inv_m1 + r.arm1.cross(r.axis).dot(r.ia1))
                    b1.dp = b1.dp - r.axis * (alpha * self.inv_m1 * lam)
                    b1.dq = b1.dq - r.ia1 * (alpha * lam)
        if last:
            b1.v = b1.v + b1.dp * (VELOCITY_PROJECTION / dt)
            b1.w = b1.w + b1.dq * (VELOCITY_PROJECTION / dt)


def locked_axes(r0, r1):
    """For a locked rotation, how each vector component of r0^-1 r1 responds to a small turn: half the sum of the
    two vector parts weighted by each other's component, the scalar overlap along the axis, and the cross term."""
    a, b = Vector((r0.x, r0.y, r0.z)), Vector((r1.x, r1.y, r1.z))
    mixed = b * r0.w + a * r1.w
    overlap = r0.w * r1.w - a.dot(b)
    nudge = SMALL if abs(r0.w * r1.w + a.dot(b)) < SMALL else 0.0         # a half-turn swing has no unique axis
    out = {}
    for slot, k in ((TWIST, 0), (SWING2, 1), (SWING1, 2)):
        unit = Vector([1.0 if i == k else 0.0 for i in range(3)])
        out[slot] = 0.5 * (a * b[k] + b * a[k] + unit * overlap + mixed.cross(unit)) + unit * nudge
    return out


def _condition_inertia(i):
    lo, hi = min(i), max(i)
    if lo > 0.0 and hi / lo > MAX_INERTIA_RATIO:
        floor = hi / MAX_INERTIA_RATIO
        return Vector([floor + (hi - floor) * (x - lo) / (hi - lo) for x in i])
    return i


def condition(inv_m0, inv_m1, inv_i0, inv_i1):
    """The engine's per-joint mass conditioning: even out each inertia, keep the parent at least 0.2 of the child."""
    m0 = 1.0 / inv_m0 if inv_m0 > 0.0 else 0.0
    m1 = 1.0 / inv_m1 if inv_m1 > 0.0 else 0.0
    i0 = _condition_inertia(Vector([1.0 / x for x in inv_i0])) if inv_m0 > 0.0 else Vector()
    i1 = _condition_inertia(Vector([1.0 / x for x in inv_i1])) if inv_m1 > 0.0 else Vector()
    if inv_m0 > 0.0 and inv_m1 > 0.0:
        if m0 / m1 < MIN_PARENT_MASS_RATIO:
            m0 = m1 * MIN_PARENT_MASS_RATIO
        ratio = max(i0) / max(i1)
        if ratio < MIN_PARENT_MASS_RATIO:
            i0 = i0 * (MIN_PARENT_MASS_RATIO / ratio)
    return ((1.0 / m0 if m0 else 0.0), (1.0 / m1 if m1 else 0.0),
            (Vector([1.0 / x for x in i0]) if inv_m0 > 0.0 else Vector()), (Vector([1.0 / x for x in i1]) if inv_m1 > 0.0 else Vector()))
