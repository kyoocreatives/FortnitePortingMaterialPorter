"""Material Porter fork: a Niagara system's GPU emitters, approximated.

A GPU emitter's particles are simulated by a compute shader the cook keeps only compiled: no
script to run, no module settings. What the asset does keep is used: how many particles it spawns
when (its spawn info, which the system's own scripts work out on the CPU), its renderers and
materials, and the curves its modules sample (by name: a sprite scale curve, float curves feeding
the dynamic material parameter, a colour curve). The rest - how long a particle lives, where it
starts, how it moves - is a stand-in: particles rising and spreading from the emitter within its
fixed bounds. It shows what the effect is made of, not its exact motion.

An emitter made here looks like a scripted one to what plays it (niagara.Emitter's name, layout,
data and tick), as niagara_stateless's do.
"""
import numpy as np

from .niagara_vm import F
from .niagara_stateless import ATTRIBUTES

RANDOMS = 16
LIFETIME = (0.9, 1.6)       # s: a particle's life, a stand-in
SPEED = (25.0, 55.0)        # cm/s, away from the emitter (mostly up)
SIZE = (8.0, 16.0)          # cm: a sprite's width (its length along its velocity half again)
RATE = 30.0                 # /s: what it spawns when the system's scripts never ask it to (its trigger
                            # something the replay doesn't feed: Eternal Wanderer's hair globs)


def _sample(curve, x):
    """A curve data interface (niagara.Curve) at x, per channel."""
    lut, last = curve.lut, float(curve.last)
    at = np.clip((np.asarray(x, np.float64) - float(curve.start)) * float(curve.scale) * last, 0.0, last)
    lo = np.floor(at).astype(int)
    hi = np.minimum(lo + 1, int(last))
    f = (at - lo)[:, None]
    return lut[lo] * (1 - f) + lut[hi] * f


class Emitter:
    """A GPU emitter of a system, its particles worked out from what the asset keeps."""

    def __init__(self, system, index, handle, export, version):
        from . import niagara
        self.system, self.index, self.name, self.id = system, index, handle["Name"], handle["Id"]
        self.export, self.version = export, version
        self.local = bool(version.get("bLocalSpace", False))
        self.layout = niagara.Layout([{"Name": n, "TypeDef": {"ClassStructOrEnum": {"ObjectName": "Class'%s'" % t}}} for n, t in ATTRIBUTES])
        self.data = self.layout.buffer()
        self.state, self.age, self.spawned, self.born = niagara.ACTIVE, 0.0, 0, 0
        self.block = bytearray(niagara.EMITTER_SIZE)
        self.events, self.handlers = {}, []
        self.infos = [n for n, v in system.layout.vars.items() if v[0] == "NiagaraSpawnInfo" and n.startswith(self.name + ".")]
        bounds = version.get("FixedBounds") or {}
        lo, hi = bounds.get("Min") or {}, bounds.get("Max") or {}
        reach = min(abs(float(lo.get(k, -100.0))) for k in "XYZ") if lo else 100.0
        self.reach = max(min(reach, min(float(hi.get(k, 100.0)) for k in "XYZ") if hi else reach), 10.0)
        # the curves its modules sample, by what they're named after
        self.curves = {}
        for key, cooked in system.cooked.items():
            if key[0] != handle["Id"]:
                continue
            for d in (cooked.get("CookedScriptRuntimeCompiledData") or {}).get("ResolvedDataInterfaces") or []:
                curve = system.interface(d.get("ResolvedDataInterface"))
                if getattr(curve, "lut", None) is not None:
                    self.curves[str(d.get("Name", "")).split(self.name + ".", 1)[-1]] = curve
        self.birth = np.zeros(0)
        self.random = np.zeros((0, RANDOMS))
        self.asked = False          # whether the system's scripts ever spawned it
        self.guessed = False        # whether its spawn rate is the stand-in's (RATE)
        self.left = 0.0

    def fill(self):
        pass

    @property
    def pending(self):
        return self.system.asked == 0

    def _curve(self, name, life, width):
        for key, curve in self.curves.items():
            if key.startswith(name):
                v = _sample(curve, life)
                return v[:, :width] if v.shape[1] >= width else np.repeat(v[:, :1], width, axis=1)
        return None

    def tick(self, dt):
        from . import niagara
        system = self.system
        state = system.read(self.name + ".ExecutionState")
        self.state = int(state[0]) if state is not None else niagara.ACTIVE
        born = []
        if self.state == niagara.ACTIVE and system.asked == niagara.ACTIVE:
            for name in self.infos:
                _, f0, i0, _, _ = system.layout.vars[name]
                count = int(system.data.ints[i0, 0])
                if count > 0:
                    self.asked = True
                    start, interval = float(system.data.floats[f0, 0]), float(system.data.floats[f0 + 1, 0])
                    born += [self.age + min(dt, start + k * interval) for k in range(count)]
            if not self.asked:
                n = dt * RATE + self.left
                whole = int(n)
                self.left = n - whole
                born += list(self.age + dt * (np.arange(whole) + 1) / max(whole, 1))
                self.guessed = self.guessed or whole > 0
        self.age += dt
        self.born = len(born)
        if born:
            self.birth = np.concatenate([self.birth, born])
            self.random = np.concatenate([self.random, system.rng.random((len(born), RANDOMS))])
            self.spawned += len(born)
        self._state()

    def _state(self):
        n = len(self.birth)
        if not n:
            self.data.count = 0
            return
        r = self.random
        lifetime = LIFETIME[0] + (LIFETIME[1] - LIFETIME[0]) * r[:, 0]
        age = self.age - self.birth
        alive = age < lifetime
        if not alive.all():
            self.birth, self.random = self.birth[alive], self.random[alive]
            return self._state()
        life = age / lifetime
        # a start near the emitter, then away from it: up, spreading out
        angle = r[:, 1] * 2 * np.pi
        spread = 0.35 + 0.5 * r[:, 2]
        direction = np.stack([np.cos(angle) * spread, np.sin(angle) * spread, np.ones(n)], axis=1)
        direction /= np.linalg.norm(direction, axis=1, keepdims=True)
        speed = (SPEED[0] + (SPEED[1] - SPEED[0]) * r[:, 3]) * self.reach / 100.0
        velocity = direction * speed[:, None] * (1.0 - 0.5 * life)[:, None]       # slowing as it goes
        start = (r[:, 4:7] - 0.5) * 0.1 * self.reach
        position = start + direction * (speed * (age - 0.25 * age * life))[:, None]
        width = SIZE[0] + (SIZE[1] - SIZE[0]) * r[:, 7]
        size = np.stack([width, width * 1.5], axis=1)
        scale = self._curve("ScaleSpriteSize", life, 1)
        if scale is not None:
            size = size * scale
        color = np.ones((n, 4))
        rgb = self._curve("VectorFromCurve", life, 3)
        if rgb is not None:
            color[:, :3] = rgb
        color[:, 3] = np.clip((1.0 - life) / 0.25, 0.0, 1.0)     # fading out over its last quarter, a stand-in
        dynamic = np.ones((n, 4))
        for i, name in enumerate(("FloatFromCurve.", "FloatFromCurve001", "FloatFromCurve002", "FloatFromCurve003")):
            v = self._curve(name, life, 1)
            if v is not None:
                dynamic[:, i] = v[:, 0]
        data = self.data
        data.reserve(n)
        data.count = n
        put = self._put
        put("Position", position)
        put("Velocity", velocity)
        put("Color", color)
        put("SpriteSize", size)
        put("SpriteRotation", np.zeros((n, 1)))
        put("Scale", np.ones((n, 3)))
        put("MeshOrientation", np.tile(np.array([0.0, 0.0, 0.0, 1.0]), (n, 1)))
        put("DynamicMaterialParameter", dynamic)
        for i in (1, 2, 3):
            put("DynamicMaterialParameter%d" % i, np.ones((n, 4)))
        put("SubImageIndex", np.zeros((n, 1)))
        put("NormalizedAge", life[:, None])
        put("MaterialRandom", r[:, 8:9])
        put("RibbonWidth", np.full((n, 1), 10.0))

    def _put(self, name, values):
        _, f0, _, nf, _ = self.layout.vars[name]
        self.data.floats[f0:f0 + nf, :len(values)] = np.asarray(values, F).T

    def attribute(self, name):
        v = self.layout.vars.get(name)
        if v is None:
            return None
        _, f0, i0, nf, ni = v
        rows = self.data.floats[f0:f0 + nf] if nf else self.data.ints[i0:i0 + ni]
        return rows[:, :self.data.count]
