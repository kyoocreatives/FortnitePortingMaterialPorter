"""A Niagara system that was never cooked (a UEFN project's own), played from its module stacks.

An uncooked asset keeps no compiled scripts to run. What it keeps is the editor's: each emitter's graph (the modules of
each stage in order, as function calls on a chain of parameter maps), the value of every module input (each script's
rapid iteration parameters: "Constants.<Emitter>.<Module>.<Input>"), what is wired into an input instead (a dynamic
input such as a random range, or a curve, through the override node's pins), and the renderers. Niagara's standard
modules are worked out here from those, step by step over the particles; modules not known here are left out and named.

An emitter made here looks like a scripted one to what plays it (niagara.Emitter's name, layout, data and tick), as
niagara_stateless's do.
"""
import re
import struct

import numpy as np

from .niagara_vm import F, I

RANDOMS = 32
ATTRIBUTES = [("Position", "Vector3f"), ("Velocity", "Vector3f"), ("Color", "LinearColor"), ("SpriteSize", "Vector2f"),
              ("SpriteRotation", "NiagaraFloat"), ("Scale", "Vector3f"), ("MeshOrientation", "Quat4f"),
              ("DynamicMaterialParameter", "Vector4f"), ("DynamicMaterialParameter1", "Vector4f"),
              ("DynamicMaterialParameter2", "Vector4f"), ("DynamicMaterialParameter3", "Vector4f"),
              ("SubImageIndex", "NiagaraFloat"), ("NormalizedAge", "NiagaraFloat"), ("MaterialRandom", "NiagaraFloat"),
              ("RibbonWidth", "NiagaraFloat"), ("RibbonLinkOrder", "NiagaraFloat"), ("UniqueID", "NiagaraInt32"),
              ("RibbonID", "NiagaraInt32")]
STAGES = ("EmitterSpawnScript", "EmitterUpdateScript", "ParticleSpawnScript", "ParticleUpdateScript", "ParticleEventScript")
# floats (or int32s) per rapid iteration parameter type
WIDTHS = {"NiagaraFloat": 1, "NiagaraInt32": -1, "NiagaraBool": -1, "Vector2f": 2, "Vector3f": 3, "NiagaraPosition": 3,
          "Vector4f": 4, "LinearColor": 4, "Quat4f": 4, "NiagaraMatrix": 16, "NiagaraID": -2}
# modules that only send data elsewhere or keep engine state: nothing to see of their own
QUIET = {"SolveForcesAndVelocity", "ParticleState", "EmitterState", "SystemState", "GenerateDeathEvent", "ReceiveLocationEvent",
         "GenerateLocationEvent", "UpdateMeshOrientation", "SpriteFacingAndAlignment",
         "SpawnRate", "SpawnBurst_Instantaneous", "InitialDecalOrientation"}         # (the spawn modules are the emitter's own: _spawn_count)


# ---------------------------------------------------------------- the graph

def _type(typedef):
    obj = (typedef or {}).get("ClassStructOrEnum") or {}
    m = re.search(r"'([^']+)'", str(obj.get("ObjectName", "")))
    return m.group(1) if m else ""


def constants(script):
    """A script's rapid iteration parameters: {name: float, int or tuple of floats}."""
    return values((script or {}).get("props", {}).get("RapidIterationParameters") or {})


def values(store):
    """A parameter store's values (rapid iteration parameters, a system's user parameters): {name: number or tuple}."""
    raw = bytes(x & 255 for x in store.get("ParameterData") or []) if isinstance(store, dict) else b""
    found = {}
    for p in (store.get("SortedParameterOffsets") if isinstance(store, dict) else None) or []:
        width, at = WIDTHS.get(_type(p.get("TypeDef")), 0), int(p.get("Offset", 0))
        if width == 0 or len(raw) < at + 4 * abs(width):
            continue
        values = struct.unpack_from("<%d%s" % (abs(width), "i" if width < 0 else "f"), raw, at)
        found[p["Name"]] = values[0] if abs(width) == 1 else tuple(values)
    return found


def _parse(text):
    """A pin's default value: a number, a bool, a vector ("(X=1,Y=2)", "1,2,3"), an enumerator index ("NewEnumerator2")."""
    text = (text or "").strip()
    if not text:
        return None
    if text in ("true", "false"):
        return text == "true"
    m = re.fullmatch(r"NewEnumerator(\d+)", text)
    if m:
        return int(m.group(1))
    numbers = re.findall(r"-?\d+(?:\.\d+)?(?:[eE]-?\d+)?", text)
    if numbers and ("=" in text or "," in text):
        values = tuple(float(x) for x in numbers)
        return values[0] if len(values) == 1 else values
    try:
        return float(text)
    except ValueError:
        return text


class Module:
    """A function call in a stage: its name in the stack, the script it calls, its static switches and its inputs."""

    def __init__(self, node, overrides, graph):
        props = node["props"]
        self.name = str(props.get("FunctionDisplayName") or node["name"])
        path = str((props.get("FunctionScript") or {}).get("ObjectPath", ""))
        # an engine module by its script's name; a scratch pad (the asset's own module) by its name in the stack
        self.script = path.rsplit("/", 1)[-1].split(".")[0] if path.startswith("/Niagara/") else ""
        self.custom = bool(path) and not path.startswith("/Niagara/")
        # a Set Variables module: (attribute, type, default) it writes
        self.targets = [(str(t.get("Name")), _type(t.get("TypeDef")), d) for t, d in
                        zip(props.get("AssignmentTargets") or [], (props.get("AssignmentDefaultValues") or []) + [""] * 99)]
        if node["type"] == "NiagaraNodeAssignment":
            self.script, self.custom = "SetVariables", False
        self.switches = {p["name"]: _parse(p["value"]) for p in node.get("pins") or []
                         if p["dir"].endswith("Input") and p["name"] != "InputMap" and not p["links"] and p["value"]}
        self.inputs = {}        # input -> ("link", node path) | ("value", parsed)
        for name, pin in overrides.items():
            if name.startswith(self.name + "."):
                key = name[len(self.name) + 1:]
                self.inputs[key] = ("link", pin["links"][0]["node"], pin["links"][0]["pin"]) if pin["links"] else ("value", _parse(pin["value"]))
        self.graph = graph

    @property
    def kind(self):
        return self.script or self.name


class Graph:
    """The package's graph nodes by path, and each emitter's stages."""

    def __init__(self, exports):
        self.exports = exports
        self.nodes = {e["path"]: e for e in exports if e.get("path")}

    def _link(self, node, pin_name):
        for p in node.get("pins") or []:
            if p["name"] == pin_name and p["dir"].endswith("Input") and p["links"]:
                return p["links"][0]["node"]
        return None

    def stage(self, prefix, usage):
        """The modules of one stage (output node of that usage under `prefix`), in order, and its override pins."""
        out = next((n for path, n in self.nodes.items() if path.startswith(prefix) and n["type"] == "NiagaraNodeOutput"
                    and str(n["props"].get("ScriptType", "")).split("::")[-1].replace("Interpolated", "") == usage), None)
        if out is None:
            return []
        calls, overrides, at, seen = [], {}, self._link(out, "Out"), set()
        while at and at not in seen:
            seen.add(at)
            node = self.nodes.get(at)
            if node is None:
                break
            kind = node["type"]
            if kind in ("NiagaraNodeFunctionCall", "NiagaraNodeAssignment"):
                calls.append(node)
                at = self._link(node, "InputMap")
            elif kind == "NiagaraNodeParameterMapSet":
                for p in node.get("pins") or []:
                    if p["dir"].endswith("Input") and "." in p["name"]:
                        overrides[p["name"]] = p
                at = self._link(node, "Source")
            else:
                break
        return [Module(n, overrides, self) for n in reversed(calls)]

    def stages(self, prefix):
        return {usage: self.stage(prefix, usage) for usage in STAGES}


# ---------------------------------------------------------------- input values

class Particles:
    """What a module's input reads about the particles it runs on: their normalized age and random numbers."""

    def __init__(self, life, random):
        self.life, self.random = life, random
        self.n = len(life)


class Inputs:
    """A module's inputs as values per particle: its own constant, else what is wired in, else the given usual value."""

    def __init__(self, emitter, module):
        self.emitter, self.module = emitter, module
        self.slot = (hash(module.name) % (RANDOMS - 4))

    def _constant(self, owner, name):
        return self.emitter.values.get("Constants.%s.%s.%s" % (self.emitter.name, owner, name))

    def get(self, name, usual, particles, width=1, unknown=None):
        """`unknown`: the value when something not worked out here is wired in (else `usual`): 0 for a force, so that
        a gravity read from somewhere unknown doesn't become the default fall."""
        source = self.module.inputs.get(name)
        if source is not None and source[0] == "link":
            v = self._dynamic(source[1], particles, width, source[2])
            if v is not None:
                return v
            if unknown is not None:
                return _wide(unknown, particles.n, width)
        value = source[1] if source is not None and source[0] == "value" else self._constant(self.module.name, name)
        return _wide(value if value is not None else usual, particles.n, width)

    def curve(self, name, particles, width=1):
        """An input wired straight to a curve data interface, at the particles' normalized age; None if it isn't."""
        source = self.module.inputs.get(name)
        if source is None or source[0] != "link":
            return None
        fn = self._curve_at(source[1])
        return _wide(fn(particles.life), particles.n, width) if fn is not None else None

    def has(self, name):
        return name in self.module.inputs or self._constant(self.module.name, name) is not None

    def switch(self, name, usual=0):
        v = self.module.switches.get(name)
        return v if isinstance(v, (int, bool)) else usual

    def _dynamic(self, path, particles, width, pin=None):
        """A dynamic input's value per particle (random ranges, curves, arithmetic, user parameters); None when it isn't
        one known here."""
        node = self.module.graph.nodes.get(path)
        if node is None:
            return None
        if node["type"] == "NiagaraNodeInput":     # a data interface wired straight in: not a value
            return None
        if node["type"] == "NiagaraNodeParameterMapGet":
            # a parameter read: a user parameter's value as the asset sets it
            v = self.emitter.system.user_values.get(pin) if pin and pin.startswith("User.") else None
            return _wide(v, particles.n, width) if v is not None else None
        dyn = Module(node, {}, self.module.graph)
        # its own overrides: pins "<its name>.<input>" on an override node of the same graph
        graph = path.rsplit(".", 1)[0] + "."
        overrides = {p["name"]: p for n in self.module.graph.nodes.values() if n["type"] == "NiagaraNodeParameterMapSet"
                     and n["path"].startswith(graph) for p in n.get("pins") or [] if p["name"].startswith(dyn.name + ".")}
        dyn = Module(node, overrides, self.module.graph)
        own = Inputs(self.emitter, dyn)
        kind = dyn.kind
        r = particles.random[:, self.slot:self.slot + max(width, 1)]
        if kind.startswith(("RandomRange", "UniformRanged")):
            low = own.get("Minimum", 0.0, particles, width)
            high = own.get("Maximum", 1.0, particles, width)
            if r.shape[1] < width:
                r = np.repeat(r[:, :1], width, axis=1)
            return low + (high - low) * r[:, :width]
        if kind.endswith("FromCurve"):
            curve = self._curve(dyn)
            if curve is None:
                return None
            scale = own.get("Scale Curve", 1.0, particles, 1)
            return _wide(curve(particles.life), particles.n, width) * scale
        operands = own._operands(particles, width)
        if kind.startswith("Multiply") and operands:
            out = operands[0]
            for v in operands[1:]:
                out = out * v
            return out
        if kind.startswith("Add") and operands:
            return sum(operands[1:], operands[0])
        if kind.startswith("Subtract") and len(operands) == 2:
            return operands[0] - operands[1]
        if kind.startswith("Divide") and len(operands) == 2:
            return operands[0] / np.where(np.abs(operands[1]) > 1e-9, operands[1], 1e-9)
        if kind.startswith(("MakeFloatFrom", "Convert")) and operands:
            return operands[0]
        return None

    def _operands(self, particles, width):
        """An arithmetic dynamic input's operands (its inputs, in name order), each as values per particle."""
        names = set(self.module.inputs) | {k.split(".", 3)[3] for k in self.emitter.values
                                           if k.startswith("Constants.%s.%s." % (self.emitter.name, self.module.name))}
        return [self.get(n, 1.0, particles, width) for n in sorted(names) if "Seed" not in n]

    def _curve(self, dyn):
        """The curve data interface wired into a curve dynamic input (its graph input node's): a function of the curve
        index (normalized age)."""
        for kind, source in dyn.inputs.items():
            if source[0] == "link":
                fn = self._curve_at(source[1])
                if fn is not None:
                    return fn
        return None

    def _curve_at(self, path):
        """The curve data interface of a graph input node, as a function of x; None if it holds none."""
        from . import niagara
        node = self.module.graph.nodes.get(path)
        if node is not None and node["type"] == "NiagaraNodeInput" and node["props"].get("DataInterface"):
            curve = self.emitter.system.export(node["props"].get("DataInterface"))
            if curve is None:
                return None
            props = curve["props"]
            if "RedCurve" in props:
                channels = [props.get(k) for k in ("RedCurve", "GreenCurve", "BlueCurve", "AlphaCurve")]
            elif "XCurve" in props:
                channels = [props.get(k) for k in ("XCurve", "YCurve", "ZCurve", "WCurve") if k in props]
            else:
                channels = [props.get("Curve")]
            return lambda x: np.stack([niagara.rich(c, np.asarray(x, np.float64)) for c in channels], axis=1)
        return None


def _wide(value, n, width):
    if isinstance(value, np.ndarray):
        v = value if value.ndim == 2 else value[:, None]
        return v if v.shape[1] >= width else np.repeat(v[:, :1], width, axis=1)
    if isinstance(value, bool):
        value = 1.0 if value else 0.0
    if isinstance(value, (tuple, list)):
        v = np.asarray(value, np.float64)[:max(width, 1)]
        if len(v) < width:
            v = np.concatenate([v, np.repeat(v[-1:], width - len(v))])
        return np.tile(v, (n, 1))
    try:
        return np.full((n, width), float(value))
    except (TypeError, ValueError):
        return np.zeros((n, width))


def describe(exports):
    """Each emitter's stages as read: {emitter: {stage: [{name, kind, inputs: {input: value or {"random": [lo, hi]} or {"curve": n}}}]}}
    (what a check compares, outside a replay)."""
    from . import niagara
    system = niagara.System.__new__(niagara.System)
    system.exports = exports
    system.asset = next(e for e in exports if e["type"] == "NiagaraSystem")
    graph = Graph(exports)
    found = {}
    for handle in system.asset["props"].get("EmitterHandles") or []:
        export = system.export((handle.get("VersionedInstance") or {}).get("Emitter"))
        if export is None:
            continue
        values = {}
        for script in _scripts(system, export):
            values.update(constants(script))
        fake = type("E", (), {"name": handle["Name"], "values": values, "system": system})()
        stages = graph.stages(export["name"] + ".")
        found[handle["Name"]] = {}
        for usage, modules in stages.items():
            listed = []
            for m in modules:
                inputs = {}
                ins = Inputs(fake, m)
                names = set(m.inputs) | {k.split(".", 3)[3] for k in values if k.startswith("Constants.%s.%s." % (handle["Name"], m.name))}
                for name in sorted(names):
                    src = m.inputs.get(name)
                    if src and src[0] == "link":
                        node = graph.nodes.get(src[1]) or {}
                        dyn = Module(node, {}, graph) if node.get("type") == "NiagaraNodeFunctionCall" else None
                        if dyn and dyn.kind.startswith(("RandomRange", "UniformRanged")):
                            own = Inputs(fake, dyn)
                            lo, hi = own._constant(dyn.name, "Minimum"), own._constant(dyn.name, "Maximum")
                            inputs[name] = {"random": [list(lo) if isinstance(lo, tuple) else lo, list(hi) if isinstance(hi, tuple) else hi]}
                        else:
                            inputs[name] = {"link": dyn.kind if dyn else node.get("type")}
                    else:
                        v = src[1] if src else ins._constant(m.name, name)
                        inputs[name] = list(v) if isinstance(v, tuple) else v
                listed.append({"name": m.name, "kind": m.kind, "inputs": inputs, "switches": m.switches})
            found[handle["Name"]][usage] = listed
    return found


def _scripts(system, export):
    """An emitter's script exports (spawn, update, emitter spawn/update, event handlers) and the system's."""
    version = (export["props"].get("VersionData") or [{}])[0]
    refs = [version.get(k, {}).get("Script") for k in ("SpawnScriptProps", "UpdateScriptProps", "EmitterSpawnScriptProps", "EmitterUpdateScriptProps")]
    refs += [h.get("Script") for h in version.get("EventHandlerScriptProps") or []]
    refs += [system.asset["props"].get("SystemSpawnScript"), system.asset["props"].get("SystemUpdateScript")]
    return [s for s in (system.export(r) for r in refs if r) if s is not None]


def uncooked(system_props, export):
    """Whether a system has no compiled scripts to run (an editor asset): its spawn script has no cooked parameters."""
    if system_props.get("ScriptRuntimeCookedDataMap"):
        return False
    script = export(system_props.get("SystemSpawnScript"))
    return script is None or "ScriptExecutionParamStore" not in script["props"]


# ---------------------------------------------------------------- the emitter

def _noise(p, frequency, seed):
    """A smooth, divergence-free-looking field in [-1, 1]^3 (curl noise's look: swirling, not random per frame)."""
    k = 2 * np.pi / max(float(frequency), 1e-3)
    s = seed * 1.618
    x, y, z = p[:, 0] * k, p[:, 1] * k, p[:, 2] * k
    return np.stack([np.sin(y + s) * np.cos(z * 0.7 + s), np.sin(z + 2 * s) * np.cos(x * 0.7 + s), np.sin(x + 3 * s) * np.cos(y * 0.7 + s)], axis=1)


def _rotate(v, ypr):
    """Vectors turned by yaw/pitch/roll in degrees (UE's order)."""
    y, p, r = np.radians(ypr[0]), np.radians(ypr[1]), np.radians(ypr[2])
    if abs(y) + abs(p) + abs(r) < 1e-9:
        return v
    cy, sy, cp, sp, cr, sr = np.cos(y), np.sin(y), np.cos(p), np.sin(p), np.cos(r), np.sin(r)
    m = np.array([[cp * cy, cp * sy, sp], [sr * sp * cy - cr * sy, sr * sp * sy + cr * cy, -sr * cp], [-(cr * sp * cy + sr * sy), cy * sr - cr * sp * sy, cr * cp]])
    return v @ m


class Emitter:
    """An emitter of an uncooked system: its stages' modules worked out over its particles each tick."""

    def __init__(self, system, index, handle, export, version, graph):
        from . import niagara
        self.system, self.index, self.name, self.id = system, index, handle["Name"], handle["Id"]
        self.export, self.version = export, version
        self.local = bool(version.get("bLocalSpace", False))
        self.layout = niagara.Layout([{"Name": n, "TypeDef": {"ClassStructOrEnum": {"ObjectName": "Class'%s'" % t}}} for n, t in ATTRIBUTES])
        self.data = self.layout.buffer()
        self.state, self.age, self.spawned, self.born = niagara.ACTIVE, 0.0, 0, 0
        self.block = bytearray(niagara.EMITTER_SIZE)
        self.events, self.handlers, self.sent = {}, [], []
        self.values = {}
        for script in _scripts(system, export):
            self.values.update(constants(script))
        self.stages = graph.stages(export["name"] + ".")
        self.missing = sorted({m.kind + (" (custom)" if m.custom else "") for ms in self.stages.values() for m in ms
                               if m.kind not in PLAYED and m.name not in QUIET and m.kind not in QUIET and not _dynamic_kind(m.kind)})
        self.p = {}             # particle attributes, one row each
        self.left, self.next_id = 0.0, 0
        self.bursts_done = set()
        self.handler = None     # (source emitter id, spawn number) when it spawns from another's location events
        for h in version.get("EventHandlerScriptProps") or []:
            if "SpawnedParticles" in str(h.get("ExecutionMode")):
                self.handler = (h.get("SourceEmitterID"), max(int(h.get("SpawnNumber") or 1), 1))
        self.source = None

    # -- what the playback reads

    def fill(self):
        pass

    @property
    def pending(self):
        return self.system.asked == 0

    def attribute(self, name):
        v = self.layout.vars.get(name)
        if v is None:
            return None
        _, f0, i0, nf, ni = v
        rows = self.data.floats[f0:f0 + nf] if nf else self.data.ints[i0:i0 + ni]
        return rows[:, :self.data.count]

    # -- a tick

    def tick(self, dt):
        rng = self.system.rng
        loop = self._loop()
        self.age += dt
        born = self._spawn_count(dt, loop) if self.system.asked == 0 else 0
        self._age(dt)
        if self.handler is not None:
            self._from_events()
        if born:
            self._spawn(born, rng)
        self._update(dt)
        self.born = born
        self._write()

    def _loop(self):
        duration = self._emitter_value("EmitterState", "Loop Duration", 0.0)
        return int(self.age // duration) if duration > 0 else 0

    def _emitter_value(self, module, name, usual):
        """An emitter stage module's input (a constant, or what is wired in: a user parameter, arithmetic...)."""
        m = next((m for m in self.stages["EmitterUpdateScript"] + self.stages["EmitterSpawnScript"] if m.name == module), None)
        if m is None:
            v = self.values.get("Constants.%s.%s.%s" % (self.name, module, name))
            return float(v) if isinstance(v, (int, float)) else usual
        one = Particles(np.zeros(1), np.full((1, RANDOMS), 0.5))
        return float(Inputs(self, m).get(name, usual, one)[0, 0])

    def _spawn_count(self, dt, loop):
        n = 0.0
        once = self._emitter_switch("EmitterState", "Loop Behavior") == 1
        duration = self._emitter_value("EmitterState", "Loop Duration", 0.0)
        if once and duration > 0 and self.age > duration:
            return 0
        for m in self.stages["EmitterUpdateScript"] + self.stages["EmitterSpawnScript"]:
            kind = m.kind
            if kind.startswith("SpawnRate"):
                n += self._emitter_value(m.name, "SpawnRate", 0.0) * self._emitter_value(m.name, "Spawn Probability", 1.0) * dt
            elif kind.startswith("SpawnBurst"):
                at = self._emitter_value(m.name, "Spawn Time", 0.0)
                key = (m.name, loop)
                local = self.age - loop * duration if duration > 0 else self.age
                if key not in self.bursts_done and local >= at:
                    self.bursts_done.add(key)
                    n += self._emitter_value(m.name, "Spawn Count", 1.0)
        n += self.left
        whole = int(n)
        self.left = n - whole
        return whole

    def _emitter_switch(self, module, name):
        m = next((m for m in self.stages["EmitterUpdateScript"] + self.stages["EmitterSpawnScript"] if m.name == module), None)
        v = m.switches.get(name) if m else None
        return v if isinstance(v, int) else 0

    def _new(self, count, rng):
        p = {
            "Position": np.zeros((count, 3)), "Velocity": np.zeros((count, 3)), "Color": np.ones((count, 4)),
            "BaseColor": np.ones((count, 4)), "SpriteSize": np.full((count, 2), 50.0), "BaseSize": np.full((count, 2), 50.0),
            "SpriteRotation": np.zeros(count), "RotationRate": np.zeros(count), "Scale": np.ones((count, 3)),
            "BaseScale": np.ones((count, 3)), "Age": np.zeros(count), "Lifetime": np.full(count, 1.0), "Mass": np.ones(count),
            "RibbonWidth": np.full(count, 10.0), "BaseRibbonWidth": np.full(count, 10.0), "SubImageIndex": np.zeros(count),
            "Dynamic": np.ones((count, 4)), "Random": rng.random((count, RANDOMS)), "MaterialRandom": rng.random(count),
            "UniqueID": np.arange(self.next_id, self.next_id + count, dtype=np.int64), "RibbonID": np.zeros(count, np.int64),
            "Birth": np.full(count, self.age), "EventLeft": np.zeros(count),
        }
        self.next_id += count
        self.spawned += count
        return p

    def _spawn(self, count, rng, at=None, ribbon=None):
        new = self._new(count, rng)
        if at is not None:
            new["Position"] = at.copy()
        if ribbon is not None:
            new["RibbonID"] = ribbon.astype(np.int64)
        for m in self.stages["ParticleSpawnScript"]:
            self._module(m, new, spawn=True)
        if at is not None:
            for m in self.stages["ParticleEventScript"]:
                self._module(m, new, spawn=True, event_at=at)
        new["BaseColor"], new["BaseSize"], new["BaseScale"] = new["Color"].copy(), new["SpriteSize"].copy(), new["Scale"].copy()
        new["BaseRibbonWidth"] = new["RibbonWidth"].copy()
        self.p = new if not self.p else {k: np.concatenate([self.p[k], new[k]]) for k in self.p}

    def _from_events(self):
        source = self.source or next((e for e in self.system.emitters if e.id == self.handler[0]), None)
        self.source = source
        if source is None or not getattr(source, "sent", None):
            return
        at = np.concatenate([s[0] for s in source.sent])
        ids = np.concatenate([s[1] for s in source.sent])
        count = len(at) * self.handler[1]
        if count:
            self._spawn(count, self.system.rng, np.repeat(at, self.handler[1], axis=0), np.repeat(ids, self.handler[1]))

    def _age(self, dt):
        if not self.p:
            return
        self.p["Age"] = self.p["Age"] + dt
        alive = self.p["Age"] < self.p["Lifetime"]
        if not alive.all():
            self.p = {k: v[alive] for k, v in self.p.items()}

    def _update(self, dt):
        self.sent = []
        if not self.p or not len(self.p["Age"]):
            return
        p = self.p
        p["Color"], p["SpriteSize"], p["Scale"] = p["BaseColor"].copy(), p["BaseSize"].copy(), p["BaseScale"].copy()
        p["RibbonWidth"] = p["BaseRibbonWidth"].copy()
        p["Force"], p["Drag"], p["Limit"] = np.zeros((len(p["Age"]), 3)), np.zeros(len(p["Age"])), np.full(len(p["Age"]), np.inf)
        solved = False
        for m in self.stages["ParticleUpdateScript"]:
            if m.kind.startswith("SolveForcesAndVelocity"):
                self._solve(dt, Inputs(self, m), self._particles(p))
                solved = True
            else:
                self._module(m, p, dt=dt)
        if not solved:
            self._solve(dt, None, self._particles(p))
        p["SpriteRotation"] = p["SpriteRotation"] + p["RotationRate"] * dt
        for k in ("Force", "Drag", "Limit"):
            del p[k]

    def _particles(self, p):
        life = np.clip(p["Age"] / np.maximum(p["Lifetime"], 1e-6), 0.0, 1.0)
        return Particles(life, p["Random"])

    def _solve(self, dt, inputs, particles):
        p = self.p
        mass = np.maximum(p["Mass"], 1e-6)[:, None]
        p["Velocity"] = p["Velocity"] + p["Force"] / mass * dt
        p["Velocity"] = p["Velocity"] / (1.0 + p["Drag"][:, None] * dt)
        limit = inputs.get("Speed Limit", np.inf, particles)[:, 0] if inputs is not None and inputs.has("Speed Limit") else p["Limit"]
        speed = np.linalg.norm(p["Velocity"], axis=1)
        over = speed > limit
        if over.any():
            p["Velocity"][over] *= (limit[over] / speed[over])[:, None]
        p["Position"] = p["Position"] + p["Velocity"] * dt

    # -- the modules

    def _module(self, m, p, spawn=False, dt=0.0, event_at=None):
        play = PLAYED.get(m.kind) or PLAYED.get(m.name.rstrip("0123456789"))
        if play is None:
            return
        play(self, Inputs(self, m), p, self._particles(p), dt=dt, spawn=spawn, event_at=event_at)

    def _write(self):
        p = self.p
        n = len(p["Age"]) if p else 0
        data = self.data
        data.reserve(n)
        data.count = n
        if not n:
            return
        life = np.clip(p["Age"] / np.maximum(p["Lifetime"], 1e-6), 0.0, 1.0)
        put = self._put
        put("Position", p["Position"])
        put("Velocity", p["Velocity"])
        put("Color", p["Color"])
        put("SpriteSize", p["SpriteSize"])
        put("SpriteRotation", p["SpriteRotation"][:, None])
        put("Scale", p["Scale"])
        put("MeshOrientation", np.tile(np.array([0.0, 0.0, 0.0, 1.0]), (n, 1)))
        put("DynamicMaterialParameter", p["Dynamic"])
        for i in (1, 2, 3):
            put("DynamicMaterialParameter%d" % i, np.ones((n, 4)))
        put("SubImageIndex", p["SubImageIndex"][:, None])
        put("NormalizedAge", life[:, None])
        put("MaterialRandom", p["MaterialRandom"][:, None])
        put("RibbonWidth", p["RibbonWidth"][:, None])
        put("RibbonLinkOrder", -p["Birth"][:, None])       # a ribbon runs from its newest point to its oldest
        for name in ("UniqueID", "RibbonID"):
            _, _, i0, _, _ = self.layout.vars[name]
            data.ints[i0, :n] = p[name].astype(I)

    def _put(self, name, values):
        _, f0, _, nf, _ = self.layout.vars[name]
        self.data.floats[f0:f0 + nf, :len(values)] = np.asarray(values, F).T


def _dynamic_kind(kind):
    return kind.startswith(("RandomRange", "UniformRanged")) or kind.endswith("FromCurve")


# ---------------------------------------------------------------- Niagara's standard modules

def _initialize(e, i, p, q, **_):
    n = q.n
    if i.has("Lifetime Min") and (i.switch("Lifetime Mode", 1) == 1 or not i.has("Lifetime")):
        lo, hi = i.get("Lifetime Min", 1.0, q)[:, 0], i.get("Lifetime Max", 1.0, q)[:, 0]
        p["Lifetime"] = lo + (hi - lo) * q.random[:, 0]
    else:
        p["Lifetime"] = i.get("Lifetime", 1.0, q)[:, 0]
    if i.has("Color"):
        p["Color"] = i.get("Color", (1.0, 1.0, 1.0, 1.0), q, 4)
    if i.has("Mass Min"):
        lo, hi = i.get("Mass Min", 1.0, q)[:, 0], i.get("Mass Max", 1.0, q)[:, 0]
        p["Mass"] = lo + (hi - lo) * q.random[:, 1]
    elif i.has("Mass"):
        p["Mass"] = i.get("Mass", 1.0, q)[:, 0]
    position = i.get("Position", 0.0, q, 3) if i.has("Position") else np.zeros((n, 3))
    offset = i.get("Position Offset", 0.0, q, 3, unknown=0.0)
    p["Position"] = p["Position"] + position + offset
    # Sprite Size Mode: 0 unset, 1 uniform, 2 and up non-uniform (each a single value or a random range)
    mode = i.switch("Sprite Size Mode", 0)
    if mode == 1:
        if i.has("Uniform Sprite Size Max"):
            lo, hi = i.get("Uniform Sprite Size Min", 50.0, q)[:, 0], i.get("Uniform Sprite Size Max", 50.0, q)[:, 0]
            s = lo + (hi - lo) * q.random[:, 2]
        else:
            s = i.get("Uniform Sprite Size", 50.0, q)[:, 0]
        p["SpriteSize"] = np.stack([s, s], axis=1)
    elif mode >= 2:
        if i.has("Sprite Size Max"):
            lo, hi = i.get("Sprite Size Min", 50.0, q, 2), i.get("Sprite Size Max", 50.0, q, 2)
            p["SpriteSize"] = lo + (hi - lo) * q.random[:, 2:3]
        else:
            p["SpriteSize"] = i.get("Sprite Size", 50.0, q, 2)
    if i.has("Sprite Rotation Angle Max"):
        lo, hi = i.get("Sprite Rotation Angle Min", 0.0, q)[:, 0], i.get("Sprite Rotation Angle Max", 0.0, q)[:, 0]
        p["SpriteRotation"] = lo + (hi - lo) * q.random[:, 3]
    if i.has("Mesh Scale"):
        p["Scale"] = i.get("Mesh Scale", 1.0, q, 3)
    elif i.has("Uniform Mesh Scale Max"):
        lo, hi = i.get("Uniform Mesh Scale Min", 1.0, q)[:, 0], i.get("Uniform Mesh Scale Max", 1.0, q)[:, 0]
        s = lo + (hi - lo) * q.random[:, 4]
        p["Scale"] = np.stack([s, s, s], axis=1)
    if i.has("Ribbon Width"):
        p["RibbonWidth"] = i.get("Ribbon Width", 10.0, q)[:, 0]


def _shape(e, i, p, q, **_):
    n, r = q.n, q.random[:, 5:10]
    # Shape Primitive: 0 sphere, 1 cylinder, 2 box / plane, 3 torus, 4 ring / disc (read off the inputs each one sets);
    # below, 0 is box / plane and 5 the sphere
    kind = {0: 5, 2: 0}.get(i.switch("Shape Primitive", 0), i.switch("Shape Primitive", 0))
    angle = r[:, 0] * 2 * np.pi
    surface = bool(i.switch("Surface Only", False))
    if kind == 0 and i.switch("Box / Plane Mode", 0) == 1:      # plane
        size = i.get("Plane Size", 100.0, q, 2)
        mid = i.get("Plane Midpoint", 0.5, q, 2)
        local = np.concatenate([(r[:, 1:3] - mid) * size, np.zeros((n, 1))], axis=1)
    elif kind == 0:     # box
        size = i.get("Box Size", 100.0, q, 3)
        mid = i.get("Box Midpoint", 0.5, q, 3)
        local = (r[:, 1:4] - mid) * size
        if surface:
            face = (r[:, 4] * 3).astype(int)
            local[np.arange(n), face] = np.sign(r[:, 1] - 0.5) * size[np.arange(n), face] / 2
    elif kind == 1:     # cylinder
        radius = i.get("Cylinder Radius", 100.0, q)[:, 0] * (1.0 if surface else np.sqrt(r[:, 1]))
        height = i.get("Cylinder Height", 100.0, q)[:, 0]
        mid = i.get("Cylinder Height Midpoint", 0.5, q)[:, 0]
        local = np.stack([np.cos(angle) * radius, np.sin(angle) * radius, (r[:, 2] - mid) * height], axis=1)
    elif kind == 4:     # ring / disc
        radius = i.get("Ring Radius", 100.0, q)[:, 0]
        coverage = i.get("Disc Coverage", 0.0, q)[:, 0]
        radius = radius * np.sqrt(1 - coverage * (1 - r[:, 1]))
        local = np.stack([np.cos(angle) * radius, np.sin(angle) * radius, np.zeros(n)], axis=1)
    elif kind == 3:     # torus
        big, small = i.get("Large Radius", 100.0, q)[:, 0], i.get("Handle Radius", 10.0, q)[:, 0]
        a2 = r[:, 1] * 2 * np.pi
        ring = big + small * np.cos(a2)
        local = np.stack([np.cos(angle) * ring, np.sin(angle) * ring, small * np.sin(a2)], axis=1)
    else:               # sphere
        radius = i.get("Sphere Radius", 100.0, q)[:, 0] * (1.0 if surface else np.cbrt(r[:, 1]))
        z = r[:, 2] * 2 - 1
        flat = np.sqrt(np.maximum(1 - z * z, 0.0))
        local = np.stack([np.cos(angle) * flat, np.sin(angle) * flat, z], axis=1) * radius[:, None]
    local = local * i.get("Non Uniform Scale", 1.0, q, 3)
    local = _rotate(local, i.get("Yaw / Pitch / Roll", 0.0, q, 3)[0] * 360.0 if i.has("Yaw / Pitch / Roll") else np.zeros(3))
    p["Position"] = p["Position"] + local + (i.get("Offset", 0.0, q, 3, unknown=0.0) if i.has("Offset") else 0.0)


def _velocity(e, i, p, q, **_):
    n, r = q.n, q.random[:, 11:14]
    mode = i.switch("Velocity Mode", 0)
    if mode == 2 or (mode == 0 and i.has("Cone Angle") and "Velocity" not in i.module.inputs and not i.has("Velocity")):
        speed = i.get("Velocity Speed", 100.0, q)[:, 0]
        half = np.radians(i.get("Cone Angle", 45.0, q)[:, 0]) / 2
        z = 1 - r[:, 0] * (1 - np.cos(half))
        flat = np.sqrt(np.maximum(1 - z * z, 0.0))
        a = r[:, 1] * 2 * np.pi
        local = np.stack([np.cos(a) * flat, np.sin(a) * flat, z], axis=1)
        axis = i.get("Cone Axis", (0.0, 0.0, 1.0), q, 3)[0]
        local = _align(local, axis)
        v = local * speed[:, None]
    elif mode == 1:
        speed = i.get("Velocity Speed", 100.0, q)[:, 0]
        origin = i.get("Velocity Origin", 0.0, q, 3)
        away = p["Position"] - origin
        length = np.linalg.norm(away, axis=1, keepdims=True)
        away = np.where(length > 1e-6, away / np.maximum(length, 1e-9), r - 0.5)
        v = away * speed[:, None]
    else:
        v = i.get("Velocity", (0.0, 0.0, 100.0), q, 3, unknown=0.0)
    p["Velocity"] = p["Velocity"] + v * i.get("Velocity Speed Scale", 1.0, q)


def _align(v, axis):
    """Vectors around +Z turned to be around `axis`."""
    axis = np.asarray(axis, np.float64)
    norm = np.linalg.norm(axis)
    if norm < 1e-9:
        return v
    axis = axis / norm
    z = np.array([0.0, 0.0, 1.0])
    c = float(np.dot(z, axis))
    if c > 1 - 1e-9:
        return v
    if c < -1 + 1e-9:
        return v * np.array([1.0, -1.0, -1.0])
    k = np.cross(z, axis)
    s = np.linalg.norm(k)
    k = k / s
    K = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    m = np.eye(3) + K * s + K @ K * (1 - c)
    return v @ m.T


def _gravity(e, i, p, q, **_):
    p["Force"] += i.get("Gravity", (0.0, 0.0, -980.0), q, 3, unknown=0.0) * p["Mass"][:, None]


def _acceleration(e, i, p, q, **_):
    p["Force"] += i.get("Acceleration", 0.0, q, 3, unknown=0.0) * p["Mass"][:, None]


def _drag(e, i, p, q, **_):
    p["Drag"] += i.get("Drag", 1.0, q)[:, 0]


def _curl(e, i, p, q, **_):
    strength = i.get("Noise Strength", 100.0, q)[:, 0]
    frequency = float(i.get("Noise Frequency", 50.0, q)[0, 0])
    seed = float(i.get("Random Seed", 0.0, q)[0, 0])
    field = _noise(p["Position"] + e.age * 10.0, frequency, seed)
    p["Force"] += field * strength[:, None] * p["Mass"][:, None]


def _limit(e, i, p, q, **_):
    p["Limit"] = np.minimum(p["Limit"], i.get("Velocity Limit", np.inf, q)[:, 0])


def _scale_color(e, i, p, q, **_):
    scale = i.get("Scale RGBA", 1.0, q, 4) if i.has("Scale RGBA") else np.ones((q.n, 4))
    if i.has("Scale RGB"):
        scale[:, :3] = scale[:, :3] * i.get("Scale RGB", 1.0, q, 3)
    if i.has("Scale Alpha"):
        scale[:, 3] = scale[:, 3] * i.get("Scale Alpha", 1.0, q)[:, 0]
    p["Color"] = p["Color"] * scale


def _color(e, i, p, q, **_):
    color = i.get("Color", (1.0, 1.0, 1.0, 1.0), q, 4)
    if i.has("Scale Color"):
        color[:, :3] = color[:, :3] * i.get("Scale Color", 1.0, q, 3)
    if i.has("Scale Alpha"):
        color[:, 3] = color[:, 3] * i.get("Scale Alpha", 1.0, q)[:, 0]
    p["Color"] = color


def _scale_sprite(e, i, p, q, **_):
    scale = np.ones((q.n, 2))
    for name in ("Scale Factor", "Sprite Size Scale"):
        if i.has(name):
            scale = scale * i.get(name, 1.0, q, 2)
    if i.has("Uniform Scale Factor"):
        scale = scale * i.get("Uniform Scale Factor", 1.0, q)
    curve = i.curve("Uniform Curve Sprite Scale", q)
    if curve is not None:
        scale = scale * curve[:, :1] * i.get("Uniform Curve Scale", 1.0, q)
    curve = i.curve("Sprite Size Scale Curve", q, 2)
    if curve is not None:
        scale = scale * curve
    p["SpriteSize"] = p["SpriteSize"] * scale


def _scale_mesh(e, i, p, q, **_):
    for name in ("Scale Factor", "Mesh Scale"):
        if i.has(name):
            p["Scale"] = p["Scale"] * i.get(name, 1.0, q, 3)
            return


def _scale_ribbon(e, i, p, q, **_):
    if i.has("Ribbon Width Scale"):
        p["RibbonWidth"] = p["RibbonWidth"] * i.get("Ribbon Width Scale", 1.0, q)[:, 0]


def _rotation_rate(e, i, p, q, spawn=False, **_):
    p["RotationRate"] = i.get("Rotation Rate", 0.0, q)[:, 0]


def _dynamic_parameters(e, i, p, q, **_):
    for k in range(4):
        name = "Index 0 Param %d" % (k + 1)
        if name in i.module.inputs or i.has(name):
            p["Dynamic"][:, k] = i.get(name, 1.0, q)[:, 0]


def _subuv(e, i, p, q, **_):
    start, end = i.get("Start Frame", 0.0, q)[:, 0], i.get("End Frame", 0.0, q)[:, 0]
    if (end > start).any():
        p["SubImageIndex"] = start + (end - start) * q.life


def _location_event(e, i, p, q, dt=0.0, spawn=False, **_):
    if spawn or not dt:
        return
    rate = i.get("Event Send Rate", 30.0, q)[:, 0]
    chance = i.get("Event Probability", 1.0, q)[:, 0]
    p["EventLeft"] = p["EventLeft"] + rate * dt
    send = (p["EventLeft"] >= 1.0) & (e.system.rng.random(q.n) < chance)
    p["EventLeft"] = np.where(p["EventLeft"] >= 1.0, p["EventLeft"] - np.floor(p["EventLeft"]), p["EventLeft"])
    if send.any():
        e.sent.append((p["Position"][send].copy(), p["UniqueID"][send].copy()))


# what a Set Variables module's target attribute is here, and its width
TARGETS = {"Position": ("Position", 3), "Velocity": ("Velocity", 3), "Color": ("Color", 4), "SpriteSize": ("SpriteSize", 2),
           "SpriteRotation": ("SpriteRotation", 1), "Scale": ("Scale", 3), "Lifetime": ("Lifetime", 1), "Mass": ("Mass", 1),
           "RibbonWidth": ("RibbonWidth", 1), "SubImageIndex": ("SubImageIndex", 1), "DynamicMaterialParameter": ("Dynamic", 4)}


def _assign(e, i, p, q, **_):
    for target, _kind, default in i.module.targets:
        if not target.startswith("Particles."):
            continue
        key, width = TARGETS.get(target[len("Particles."):], (None, 0))
        if key is None:
            continue
        value = i.get(target, _parse(default) if default else 0.0, q, width)
        p[key] = value[:, 0] if width == 1 else value


def _receive_location(e, i, p, q, event_at=None, **_):
    if event_at is not None:
        p["Position"] = event_at.copy()


PLAYED = {
    "InitializeParticle": _initialize, "ShapeLocation": _shape, "SphereLocation": _shape, "BoxLocation": _shape,
    "CylinderLocation": _shape, "AddVelocity": _velocity, "AddVelocityInCone": _velocity, "GravityForce": _gravity,
    "AccelerationForce": _acceleration, "Drag": _drag, "CurlNoiseForce": _curl, "LimitVelocity": _limit,
    "ScaleColor": _scale_color, "Color": _color, "ScaleSpriteSize": _scale_sprite, "ScaleSpriteSizeBySpeed": _scale_sprite,
    "ScaleMeshSize": _scale_mesh, "ScaleRibbonWidth": _scale_ribbon, "SpriteRotationRate": _rotation_rate,
    "DynamicMaterialParameters": _dynamic_parameters, "SubUVAnimation": _subuv, "GenerateLocationEvent": _location_event,
    "ReceiveLocationEvent": _receive_location, "SetVariables": _assign,
}
