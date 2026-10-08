"""A Niagara system's stateless emitters (UE 5.4's lightweight emitters), played.

A stateless emitter has no script: its modules are settings (a lifetime range, a colour curve, a
cone of velocities), and a particle's state at any age follows from them and the particle's random
numbers alone. That is worked out here module by module, for the modules the game's effects use.
The engine's random numbers aren't reproduced: each particle gets values from the same ranges and
curves, but not the same draw.

An emitter made here looks like a scripted one to what plays it (niagara.Emitter's name, layout,
data and tick).
"""
import numpy as np

from .niagara_vm import F

RANDOMS = 64        # per-particle random numbers, one slot per use

# what a stateless emitter gives its renderers
ATTRIBUTES = [("Position", "Vector3f"), ("Velocity", "Vector3f"), ("Color", "LinearColor"), ("SpriteSize", "Vector2f"),
              ("SpriteRotation", "NiagaraFloat"), ("Scale", "Vector3f"), ("MeshOrientation", "Quat4f"),
              ("DynamicMaterialParameter", "Vector4f"), ("DynamicMaterialParameter1", "Vector4f"),
              ("DynamicMaterialParameter2", "Vector4f"), ("DynamicMaterialParameter3", "Vector4f"),
              ("SubImageIndex", "NiagaraFloat"), ("NormalizedAge", "NiagaraFloat"), ("MaterialRandom", "NiagaraFloat"),
              ("RibbonWidth", "NiagaraFloat"), ("UniqueID", "NiagaraInt32")]


def _vector(value, width):
    """A setting's value as `width` floats: a number, or a struct of X Y Z W / R G B A / Pitch Yaw Roll."""
    if isinstance(value, dict):
        for keys in (("X", "Y", "Z", "W"), ("R", "G", "B", "A"), ("Roll", "Pitch", "Yaw")):
            if keys[0] in value or keys[1] in value:
                got = [float(value.get(k, 0.0)) for k in keys[:max(width, 1)]]
                return np.array((got + [got[-1]] * width)[:width], np.float64)
    if isinstance(value, (int, float)):
        return np.full(width, float(value))
    return None


def _bound(setting, system, width):
    """The value of a distribution bound to a system variable (data set value, else user parameter)
    as `width` floats; None if the system has no such value."""
    if system is None:
        return None
    name = str(((setting.get("ParameterBinding") or {}).get("Name")) or "")
    if not name or name == "None":
        return None
    v = system.read(name)
    if v is None and name in system.user.offsets:
        v = np.frombuffer(system.user.raw(name), np.float32)
    if v is None or not len(v):
        return None
    v = np.asarray(v, np.float64).ravel()
    return np.resize(v, width)


class Distribution:
    """A module's setting: a constant, a range each particle draws from, or a curve over its life."""

    def __init__(self, setting, usual, width, system=None):
        setting = setting or {}
        self.width = width
        mode = str(setting.get("Mode", "")).split("::")[-1]
        usual = np.broadcast_to(np.asarray(usual, np.float64), (width,))
        self.curve, self.low, self.high = None, usual, usual
        self.uniform = not mode.startswith("NonUniform")
        values = setting.get("Values")
        bound = _bound(setting, system, width) if mode == "Binding" else None
        if bound is not None:
            # a system value (System.BurstDelay, a User. parameter) as it is now
            self.low = self.high = bound
        elif "Min" in setting or "Max" in setting:
            low, high = _vector(setting.get("Min"), width), _vector(setting.get("Max"), width)
            ranged = "Constant" not in mode
            self.low = low if low is not None else (np.zeros(width) if ranged else high)
            self.high = high if high is not None else (np.zeros(width) if ranged else low)
            if not ranged:
                self.high = self.low
        elif values and ("Curve" in mode or "Gradient" in mode):
            self.curve = np.stack([_vector(v, width) for v in values])
            span = setting.get("ValuesTimeRange") or {}
            self.start, self.end = float(span.get("X", 0.0)), float(span.get("Y", 1.0))
        elif values:
            self.low = _vector(values[0], width)
            self.high = _vector(values[-1], width) if "Range" in mode else self.low

    @property
    def constant(self):
        return self.curve is None and np.array_equal(self.low, self.high)

    def at(self, age, random):
        """The setting per particle (particles x width). age: normalized ages; random: the draws for
        it (particles x width; a uniform range uses the first for every channel)."""
        if self.curve is not None:
            span = max(self.end - self.start, 1e-9)
            x = np.clip((age - self.start) / span, 0.0, 1.0) * (len(self.curve) - 1)
            a = np.minimum(x.astype(np.int64), len(self.curve) - 1)
            b = np.minimum(a + 1, len(self.curve) - 1)
            t = (x - a)[:, None]
            return self.curve[a] * (1 - t) + self.curve[b] * t
        t = random[:, :1] if self.uniform else random[:, :self.width]
        return self.low + (self.high - self.low) * t


def _rotation(degrees):
    """A UE rotator (roll, pitch, yaw in degrees, per particle) as matrices turning column vectors."""
    roll, pitch, yaw = (np.radians(degrees[:, i]) for i in range(3))
    cr, sr, cp, sp, cy, sy = np.cos(roll), np.sin(roll), np.cos(pitch), np.sin(pitch), np.cos(yaw), np.sin(yaw)
    m = np.empty((len(degrees), 3, 3))
    m[:, 0, 0], m[:, 1, 0], m[:, 2, 0] = cp * cy, cp * sy, sp                      # where X goes
    m[:, 0, 1], m[:, 1, 1], m[:, 2, 1] = sr * sp * cy - cr * sy, sr * sp * sy + cr * cy, -sr * cp
    m[:, 0, 2], m[:, 1, 2], m[:, 2, 2] = -(cr * sp * cy + sr * sy), cy * sr - cr * sp * sy, cr * cp
    return m


def _quaternion(m):
    """Rotation matrices as quaternions (x, y, z, w)."""
    w = np.sqrt(np.maximum(1.0 + m[:, 0, 0] + m[:, 1, 1] + m[:, 2, 2], 1e-12)) / 2
    return np.stack([(m[:, 2, 1] - m[:, 1, 2]) / (4 * w), (m[:, 0, 2] - m[:, 2, 0]) / (4 * w), (m[:, 1, 0] - m[:, 0, 1]) / (4 * w), w], axis=1)


class Emitter:
    """A stateless emitter of a system: its particles' state worked out from its modules at each tick."""

    def __init__(self, system, index, handle, export):
        from . import niagara
        self.system, self.index, self.name, self.id = system, index, handle["Name"], handle["Id"]
        self.export, self.version = export, export["props"]
        props = export["props"]
        self.local = True
        self.layout = niagara.Layout([{"Name": n, "TypeDef": {"ClassStructOrEnum": {"ObjectName": "Class'%s'" % t}}} for n, t in ATTRIBUTES])
        self.data = self.layout.buffer()
        self.state, self.age, self.spawned, self.born = niagara.ACTIVE, 0.0, 0, 0
        self.block = bytearray(niagara.EMITTER_SIZE)
        self.events, self.handlers = {}, []
        self.modules = {}
        for ref in props.get("Modules") or []:
            module = system.export(ref)
            if module is not None and module["props"].get("bModuleEnabled", True):
                self.modules[module["type"].replace("NiagaraStatelessModule_", "")] = module["props"]
        state = self.emitter_state = props.get("EmitterState") or {}
        self.loops = str(state.get("LoopBehavior", "Infinite")).split("::")[-1]
        self._timing()
        self.timed = False      # whether timing was re-read after the system's scripts ran (bound delay)
        self.count = int(state.get("LoopCount", 1))
        self.spawns = [s for s in props.get("SpawnInfos") or [] if s.get("bEnabled", True)]
        self.birth = np.zeros(0)                        # birth time per particle
        self.random = np.zeros((0, RANDOMS))
        self.left = 0.0                                 # fraction of a particle carried over by a rate

    def _timing(self, system=None):
        """The loop's duration and delay: settings, or values of the system they are bound to
        (Monster Smash's ground burst waits for System.BurstDelay, set by the system's script)."""
        state = self.emitter_state
        self.duration = float(Distribution(state.get("LoopDuration"), 1.0, 1, system).low[0]) or 1.0
        self.delay = float(Distribution(state.get("LoopDelay"), 0.0, 1, system).low[0]) if state.get("bLoopDelayEnabled") else 0.0

    def fill(self):
        pass

    @property
    def pending(self):
        """Whether it still has particles to spawn."""
        if self.system.asked != 0:      # the game stopped the effect
            return False
        if self.loops == "Infinite":
            return bool(self.spawns)
        loops = 1 if self.loops == "Once" else max(self.count, 1)
        return bool(self.spawns) and self.age <= loops * (self.duration + self.delay)

    def _born(self, since, until):
        """Birth times of the particles spawned in (since, until]."""
        times = []
        period = self.duration + self.delay
        first, last = int(since // period), int(until // period)
        for loop in range(max(first, 0), last + 1):
            if self.loops == "Once" and loop > 0 or self.loops == "Multiple" and loop >= self.count:
                break
            start = loop * period + self.delay
            for spawn in self.spawns:
                at = start + float(spawn.get("SpawnTime", 0.0))
                if "Burst" in str(spawn.get("Type")):
                    if since <= at < until:
                        amount = spawn.get("Amount") or {}
                        low, high = int(amount.get("Min", 1)), int(amount.get("Max", amount.get("Min", 1)))
                        times += [at] * int(self.system.rng.integers(min(low, high), max(low, high) + 1))
                else:
                    rate = float(Distribution(spawn.get("Rate"), 60.0, 1).low[0])
                    a, b = max(since, at), min(until, start + self.duration)
                    if b > a and rate > 0:
                        n = (b - a) * rate + self.left
                        whole = int(n)
                        self.left = n - whole
                        times += list(np.linspace(a, b, whole, endpoint=False) + (b - a) / max(whole, 1))
        return times

    def tick(self, dt):
        if not self.timed:
            self._timing(self.system)
            self.timed = True
        rng = self.system.rng
        born = self._born(self.age, self.age + dt) if self.system.asked == 0 else []
        self.age += dt
        self.born = len(born)
        if born:
            self.birth = np.concatenate([self.birth, born])
            self.random = np.concatenate([self.random, rng.random((len(born), RANDOMS))])
            self.spawned += len(born)
        self._state()

    def _setting(self, module, name, usual, width, age, slot):
        d = Distribution((self.modules.get(module) or {}).get(name), usual, width, self.system)
        return d.at(age, self.random[:, slot:slot + width])

    def _state(self):
        """Attributes of every live particle at the emitter's age."""
        has = self.modules.__contains__
        n = len(self.birth)
        if not n:
            self.data.count = 0
            return
        zero = np.zeros(n)
        lifetime = np.maximum(self._setting("InitializeParticle", "LifetimeDistribution", 1.0, 1, zero, 0)[:, 0], 1e-4)
        age = self.age - self.birth
        alive = age < lifetime
        if not alive.all():
            self.birth, self.random = self.birth[alive], self.random[alive]
            return self._state()
        life = age / lifetime
        # start position and speed
        position = self._setting("InitializeParticle", "InitialPositionDistribution", 0.0, 3, life, 1)
        if has("ShapeLocation"):
            position = position + self._shape()
        velocity = np.zeros((n, 3))
        if has("AddVelocity"):
            velocity = self._velocity(position, life)
        force = np.zeros((n, 3))
        if has("GravityForce"):
            force += self._setting("GravityForce", "GravityDistribution", (0.0, 0.0, -980.0), 3, life, 10)
        if has("AccelerationForce"):
            force += self._setting("AccelerationForce", "AccelerationDistribution", 0.0, 3, life, 13)
        drag = self._setting("Drag", "DragDistribution", 0.0, 1, life, 16) if has("Drag") else np.zeros((n, 1))
        # under a constant force and drag: v' = force - drag v
        t = age[:, None]
        slow = drag > 1e-6
        k = np.where(slow, drag, 1.0)
        fade = np.exp(-k * t)
        terminal = force / k
        position = position + np.where(slow, (velocity - terminal) * (1 - fade) / k + terminal * t, velocity * t + force * t * t / 2)
        velocity = np.where(slow, (velocity - terminal) * fade + terminal, velocity + force * t)

        color = self._setting("InitializeParticle", "ColorDistribution", 1.0, 4, life, 17)
        if has("ScaleColor"):
            color = color * self._setting("ScaleColor", "ScaleDistribution", 1.0, 4, life, 21)
        size = self._setting("InitializeParticle", "SpriteSizeDistribution", 10.0, 2, life, 25)
        if has("ScaleSpriteSize"):
            size = size * self._setting("ScaleSpriteSize", "ScaleDistribution", 1.0, 2, life, 27) * self._curve_scale("ScaleSpriteSize")
        spin = self._setting("InitializeParticle", "SpriteRotationDistribution", 0.0, 1, life, 29)
        if has("SpriteRotationRate"):
            spin = spin + self._setting("SpriteRotationRate", "RotationRateDistribution", 0.0, 1, life, 30) * t
        scale = self._setting("InitializeParticle", "MeshScaleDistribution", 1.0, 3, life, 31)
        if has("ScaleMeshSize"):
            scale = scale * self._setting("ScaleMeshSize", "ScaleDistribution", 1.0, 3, life, 34) * self._curve_scale("ScaleMeshSize")
        orientation = np.tile(np.array([0.0, 0.0, 0.0, 1.0]), (n, 1))
        if has("InitialMeshOrientation"):
            turn = self._setting("InitialMeshOrientation", "Rotation", 0.0, 3, life, 37)
            orientation = _quaternion(_rotation(turn[:, [0, 1, 2]]))

        data = self.data
        data.reserve(n)
        data.count = n
        put = self._put
        put("Position", position)
        put("Velocity", velocity)
        put("Color", color)
        put("SpriteSize", size)
        put("SpriteRotation", spin)
        put("Scale", scale)
        put("MeshOrientation", orientation)
        dynamic = self.modules.get("DynamicMaterialParameters") or {}
        for i in range(4):
            channels = dynamic.get("Parameter%d" % i) or {}
            value = np.stack([Distribution(channels.get(c + "ChannelDistribution"), 1.0, 1).at(life, self.random[:, 40 + 4 * i + j:41 + 4 * i + j])[:, 0]
                              for j, c in enumerate("XYZW")], axis=1)
            put("DynamicMaterialParameter%s" % (i or ""), value)
        frame = np.zeros((n, 1))
        if has("SubUVAnimation"):
            sub = self.modules["SubUVAnimation"]
            frames = float(sub.get("NumFrames", 16))
            if "Random" in str(sub.get("AnimationMode")):
                frame = np.floor(self.random[:, 63:64] * frames)
            else:
                frame = np.floor(np.clip(life[:, None], 0.0, 0.9999) * frames)
        put("SubImageIndex", frame)
        put("NormalizedAge", life[:, None])
        put("MaterialRandom", self.random[:, 62:63])
        put("RibbonWidth", self._setting("InitializeParticle", "RibbonWidthDistribution", 10.0, 1, life, 61))

    def _put(self, name, values):
        _, f0, _, nf, _ = self.layout.vars[name]
        self.data.floats[f0:f0 + nf, :len(values)] = np.asarray(values, F).T

    def _curve_scale(self, module):
        """A scale module's factor over its curve (ScaleCurveRange: floats as bytes)."""
        raw = ((self.modules.get(module) or {}).get("ScaleCurveRange") or {}).get("DefaultValue")
        return float(np.frombuffer(bytes(raw[:4]), F)[0]) if raw and len(raw) >= 4 else 1.0

    def _shape(self):
        """Where in its shape each particle starts, relative to the shape's position."""
        shape = self.modules["ShapeLocation"]
        kind = str(shape.get("ShapePrimitive", "Sphere")).split("::")[-1]
        n = len(self.birth)
        r = self.random[:, 4:9]
        angle = r[:, 0] * 2 * np.pi
        first = lambda name, usual: float(Distribution(shape.get(name), usual, 1).low[0])
        if kind == "Box":
            size = Distribution(shape.get("BoxSize"), 100.0, 3).low
            local = (r[:, :3] - 0.5) * size
        elif kind == "Plane":
            size = Distribution(shape.get("PlaneSize"), 100.0, 2).low
            local = np.concatenate([(r[:, :2] - 0.5) * size, np.zeros((n, 1))], axis=1)
        elif kind == "Cylinder":
            radius = first("CylinderRadius", 100.0) * np.sqrt(r[:, 1])
            height = first("CylinderHeight", 100.0)
            local = np.stack([np.cos(angle) * radius, np.sin(angle) * radius, (r[:, 2] - first("CylinderHeightMidpoint", 0.5)) * height], axis=1)
        elif kind == "Ring":
            radius = first("RingRadius", 100.0) * np.sqrt(1 - first("DiscCoverage", 0.0) * (1 - r[:, 1]))
            local = np.stack([np.cos(angle) * radius, np.sin(angle) * radius, np.zeros(n)], axis=1)
        else:
            d = Distribution(shape.get("SphereRadius"), 100.0, 1)
            radius = d.low[0] + (d.high[0] - d.low[0]) * r[:, 1]
            z = r[:, 2] * 2 - 1
            flat = np.sqrt(np.maximum(1 - z * z, 0.0))
            local = np.stack([np.cos(angle) * flat, np.sin(angle) * flat, z], axis=1) * radius[:, None]
        local = local * Distribution(shape.get("ShapeScale"), 1.0, 3).low
        return local + Distribution(shape.get("TransformOffset"), 0.0, 3).low

    def _velocity(self, position, life):
        """Each particle's initial velocity: straight, within a cone, or away from a point."""
        module = self.modules["AddVelocity"]
        kind = str(module.get("VelocityType", "Linear")).split("::")[-1]
        n = len(self.birth)
        r = self.random[:, 56:61]
        scale = lambda name: float(Distribution(module.get(name), 1.0, 1).low[0])
        if kind == "InCone":
            speed = Distribution(module.get("ConeVelocityDistribution"), 500.0, 1).at(life, r[:, :1])
            half = np.radians(float(module.get("ConeAngle", 45.0))) / 2
            z = 1 - r[:, 1] * (1 - np.cos(half))
            flat = np.sqrt(np.maximum(1 - z * z, 0.0))
            angle = r[:, 2] * 2 * np.pi
            local = np.stack([np.cos(angle) * flat, np.sin(angle) * flat, z], axis=1)
            turn = Distribution(module.get("ConeRotation"), 0.0, 3).low
            direction = np.einsum("nij,nj->ni", _rotation(np.tile(turn, (n, 1))), local)
            return direction * speed * scale("ConeVelocityScale")
        if kind == "FromPoint":
            speed = Distribution(module.get("PointVelocityDistribution"), 100.0, 1).at(life, r[:, :1])
            origin = _vector(module.get("PointOrigin"), 3)
            away = position - (origin if origin is not None else 0.0)
            length = np.linalg.norm(away, axis=1, keepdims=True)
            away = np.where(length > 1e-6, away, r[:, 1:4] - 0.5)       # a particle on the point itself: any direction
            return away / np.maximum(np.linalg.norm(away, axis=1, keepdims=True), 1e-9) * speed * scale("PointVelocityScale")
        return Distribution(module.get("LinearVelocityDistribution"), (0.0, 0.0, 100.0), 3).at(life, r[:, :3]) * scale("LinearVelocityScale")

    def attribute(self, name):
        v = self.layout.vars.get(name)
        if v is None:
            return None
        _, f0, i0, nf, ni = v
        rows = self.data.floats[f0:f0 + nf] if nf else self.data.ints[i0:i0 + ni]
        return rows[:, :self.data.count]
