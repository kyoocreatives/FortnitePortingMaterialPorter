"""A physics asset body as Chaos immediate mode builds it: shapes in bone space, mass from volume (to the 0.75 power),
centre of mass, principal inertia and its frame, and the inertia conditioning the linear joint solver adds."""
import math

import numpy as np
from mathutils import Matrix, Quaternion, Vector

DENSITY = 0.001         # kg per cm^3, the default physical material
MASS_POWER = 0.75
MIN_MASS = 0.001


def rotator(pitch, yaw, roll):
    """An FRotator in the game's numbers: yaw about Z, then pitch and roll with the engine's signs."""
    return (Quaternion((0.0, 0.0, 1.0), math.radians(yaw)) @ Quaternion((0.0, 1.0, 0.0), math.radians(-pitch))
            @ Quaternion((1.0, 0.0, 0.0), math.radians(-roll)))


class Shape:
    """A capsule (segment a-b with radius), sphere (a == b) or box, in bone space."""
    __slots__ = ("kind", "a", "b", "radius", "center", "rot", "half", "mass")

    def __init__(self, data):
        self.kind, self.mass = data["kind"], data.get("mass", True)
        self.center = Vector(data["center"])
        self.rot = rotator(*data["rotation"])
        self.radius, self.half = max(data.get("radius", 0.0), 1e-4), None
        if self.kind == "box":
            self.half = Vector([max(s / 2.0, 1e-4) for s in data["size"]])
            # boxes collide as their bounding capsule (along their longest axis)
            k = max(range(3), key=lambda i: self.half[i])
            axis = self.rot @ Vector([1.0 if i == k else 0.0 for i in range(3)])
            r = math.sqrt(sum(self.half[i] ** 2 for i in range(3) if i != k))
            reach = max(self.half[k] - r, 0.0)
            self.a, self.b, self.radius = self.center - axis * reach, self.center + axis * reach, r
            return
        half = max(data.get("length", 0.0) / 2.0, 1e-4) if self.kind == "capsule" else 0.0
        if self.kind == "capsule" and half <= 1e-4:
            self.kind = "sphere"
        axis = self.rot @ Vector((0.0, 0.0, 1.0))
        self.a, self.b = self.center - axis * half, self.center + axis * half

    def volume_inertia(self):
        """Volume and the inertia tensor per unit mass about the shape's centre, bone axes."""
        r = self.radius
        if self.kind == "box":
            a, b, c = (2.0 * h for h in self.half)
            local = Matrix.Diagonal(Vector(((b * b + c * c) / 12.0, (a * a + c * c) / 12.0, (a * a + b * b) / 12.0)))
            vol = a * b * c
        elif self.kind == "sphere":
            local = Matrix.Diagonal(Vector([0.4 * r * r] * 3))
            vol = 4.0 / 3.0 * math.pi * r ** 3
        else:
            h = (self.b - self.a).length
            vol = math.pi * r * r * (h + 4.0 * r / 3.0)
            across = (5 * h ** 3 + 20 * h * h * r + 45 * h * r * r + 32 * r ** 3) / (60 * h + 80 * r)
            along = r * r * (15 * h + 16 * r) / (30 * h + 40 * r)
            local = Matrix.Diagonal(Vector((across, across, along)))
        rot = self.rot.to_matrix()
        return vol, rot @ local @ rot.transposed()

    def bounds(self):
        if self.half is not None:
            corners = [self.center + self.rot @ Vector((sx * self.half.x, sy * self.half.y, sz * self.half.z))
                       for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)]
            return Vector([min(c[i] for c in corners) for i in range(3)]), Vector([max(c[i] for c in corners) for i in range(3)])
        lo = Vector([min(self.a[i], self.b[i]) - self.radius for i in range(3)])
        hi = Vector([max(self.a[i], self.b[i]) + self.radius for i in range(3)])
        return lo, hi


class Mass:
    """A body's mass properties: centre of mass and principal frame (bone space), inverse mass and inertia."""
    __slots__ = ("mass", "inv_mass", "com", "rom", "inertia", "inv_inertia", "shapes", "lo", "hi")


def mass_properties(body):
    """Mass, CoM and principal inertia of a physics asset body (from physics_read)."""
    shapes = [Shape(s) for s in body["shapes"]]
    out = Mass()
    out.shapes = shapes
    weighed = [s for s in shapes if s.mass] or shapes
    parts = [(s,) + s.volume_inertia() for s in weighed]
    raw = sum(v for _, v, _ in parts) * DENSITY
    com = sum((s.center * v for s, v, _ in parts), Vector()) / max(sum(v for _, v, _ in parts), 1e-12)
    tensor = Matrix(((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)))
    for s, v, inertia in parts:         # parallel axis about the combined centre
        m, d = v * DENSITY, s.center - com
        shift = Matrix.Diagonal(Vector([d.length_squared] * 3)) - Matrix(((d.x * d.x, d.x * d.y, d.x * d.z), (d.y * d.x, d.y * d.y, d.y * d.z),
                                                                          (d.z * d.x, d.z * d.y, d.z * d.z)))
        tensor += (inertia + shift) * m
    mass = max(body["mass"], MIN_MASS) if body.get("mass") is not None else max(body.get("mass_scale", 1.0) * raw ** MASS_POWER, MIN_MASS)
    tensor = tensor * (mass / raw if raw > 0 else 0.0)
    nudge = Vector(body.get("com_nudge") or (0.0, 0.0, 0.0))
    if nudge.length_squared > 0.0:
        com = com + nudge
        tensor = tensor + Matrix.Diagonal(Vector([mass * nudge[i] ** 2 for i in range(3)]))
    off = sum(tensor[i][j] ** 2 for i in range(3) for j in range(3) if i != j)
    trace = tensor[0][0] + tensor[1][1] + tensor[2][2]
    if off < 1e-8 * trace * trace:
        rom, moments = Quaternion(), Vector((tensor[0][0], tensor[1][1], tensor[2][2]))
    else:
        values, vectors = np.linalg.eigh(np.array(tensor))
        basis = Matrix(vectors.tolist())
        if basis.determinant() < 0.0:
            basis.col[2] = -basis.col[2]
        rom, moments = basis.to_quaternion(), Vector(values.tolist())
    out.mass, out.inv_mass, out.com, out.rom = mass, 1.0 / mass, com, rom
    out.inertia = moments
    out.inv_inertia = Vector([1.0 / m if m > 0.0 else 0.0 for m in moments])
    out.lo = Vector([min(s.bounds()[0][i] for s in shapes) for i in range(3)]) if shapes else Vector()
    out.hi = Vector([max(s.bounds()[1][i] for s in shapes) for i in range(3)]) if shapes else Vector()
    return out


def conditioning(m, connectors, distance=10.0, rotation_ratio=2.0):
    """Per-axis scale of the inverse inertia so constraint corrections stay mostly linear (connectors in bone space)."""
    inv_rom = m.rom.conjugated()
    corners = [Vector((x, y, z)) for x in (m.lo.x, m.hi.x) for y in (m.lo.y, m.hi.y) for z in (m.lo.z, m.hi.z)]
    local = [inv_rom @ (c - m.com) for c in corners]
    extents = Vector([0.5 * (max(c[i] for c in local) - min(c[i] for c in local)) for i in range(3)])
    for c in connectors:
        arm = inv_rom @ (Vector(c) - m.com)
        extents = Vector([max(extents[i], abs(arm[i])) for i in range(3)])
    if not (m.inv_mass > 1e-20 and min(m.inv_inertia) > 1e-4 and min(extents) > 1e-8):
        return Vector((1.0, 1.0, 1.0))
    sq = Vector([e * e for e in extents])
    r = m.inv_inertia / m.inv_mass
    rows = (sq.x * Vector((0.0, r.y, r.z)), sq.y * Vector((r.x, 0.0, r.z)), sq.z * Vector((r.x, r.y, 0.0)))
    ratio = Vector([max(row[i] for row in rows) for i in range(3)])
    limit = min(max(extents) / distance, 1.0) * rotation_ratio
    return Vector([min(limit / ratio[i], 1.0) if ratio[i] > 0.0 else 1.0 for i in range(3)])
