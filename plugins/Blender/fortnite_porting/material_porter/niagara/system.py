"""Scripts, emitters and the system that ticks them."""

import struct
import numpy as np

from .. import niagara_vm as vm
from ..niagara_vm import F, I, Unsupported

from .datasets import (
    ACTIVE,
    COMPLETE,
    DISABLED,
    EMITTER_SIZE,
    GLOBAL_SIZE,
    IDENTITY,
    INACTIVE,
    INACTIVE_CLEAR,
    Layout,
    NO_ID,
    OWNER_SIZE,
    QUALITY,
    SYSTEM_SIZE,
    Store,
    TYPES,
    _zeros,
)
from .interfaces import INTERFACES, LIBRARY, Skeleton, _quaternion


class Script:
    """A compiled script with its parameters and the functions it calls."""

    def __init__(self, system, props, cooked, emitter=None, lenient=False):
        """lenient (for the system's own scripts, which hold every emitter's emitter-level modules):
        what can't be answered here (a grid a GPU emitter simulates on, a data channel, a mesh to
        sample) answers zeros instead of stopping the whole system."""
        data = props.get("CachedScriptVM") or {}
        code = (data.get("ByteCode") or {}).get("Data")
        if not code:
            raise Unsupported("no compiled script")
        self.interpolated = "Interpolated" in str(props.get("Usage"))
        self.literals = bytes(data.get("ScriptLiterals") or [])
        self.registers = int(data.get("NumTempRegisters") or 0)
        self.attributes = data.get("Attributes") or []
        called = data.get("CalledVMExternalFunctions") or []
        self.program = vm.decode(bytes(code), [(len(f.get("InputParamLocations") or []), int(f.get("NumOutputs") or 0)) for f in called])
        self.store = Store(cooked.get("CookedExecutionParameterStore") or {}, 2 if self.interpolated else 1)
        resolved = (cooked.get("CookedScriptRuntimeCompiledData") or {}).get("ResolvedDataInterfaces") or []
        self.functions = []
        for f in called:
            owner = next((d for d in resolved if f.get("OwnerName") in (d.get("CompileName"), d.get("Name"))), None)
            if owner is None and f.get("Name") in LIBRARY:
                self.functions.append(LIBRARY[f["Name"]])
                continue
            interface = system.interface((owner or {}).get("ResolvedDataInterface"))
            outputs = int(f.get("NumOutputs") or 0)
            if interface is None:
                kind = str(((owner or {}).get("ResolvedDataInterface") or {}).get("ObjectName") or f.get("OwnerName")).split("'")[0]
                if lenient:
                    system.unanswered.add("%s.%s" % (kind.replace("NiagaraDataInterface", ""), f.get("Name")))
                    self.functions.append(_zeros(outputs))
                    continue
                raise Unsupported("%s.%s" % (kind.replace("NiagaraDataInterface", ""), f.get("Name")))
            if hasattr(interface, "caller"):
                interface.caller = emitter
            try:
                self.functions.append(interface.function(f["Name"], f.get("FunctionSpecifiers") or [],
                                                         len(f.get("InputParamLocations") or []), outputs))
            except Unsupported as e:
                if not lenient:
                    raise
                system.unanswered.add(str(e))
                self.functions.append(_zeros(outputs))

    def run(self, count, blocks, sets, rng, ids=None):
        return vm.run(self.program, self.registers, count, b"".join(blocks) + self.literals, sets, self.functions, rng, ids)


class Ids:
    """Persistent particle IDs: an index that counts up, tagged with the tick it was given on."""

    def __init__(self):
        self.next, self.tag = 0, 0

    def take(self, count):
        self.next += count
        return self.next - count


class Handler:
    """An emitter's answer to another's events: particles spawned per event, and a script run on them or on all."""

    def __init__(self, system, emitter, handle, props):
        self.script = system.script(props["Script"], handle["Id"])
        self.spawns = "SpawnedParticles" in str(props.get("ExecutionMode"))
        self.every = "EveryParticle" in str(props.get("ExecutionMode"))
        self.number = int(props.get("SpawnNumber", 0))
        self.least = int(props.get("MinSpawnNumber", 0)) if props.get("bRandomSpawnNumber") else self.number
        self.limit = int(props.get("MaxEventsPerFrame", 0))
        self.source_id, self.event = props.get("SourceEmitterID"), str(props.get("SourceEventName"))
        self.source = None

    def events(self):
        """The source's events as they stand: (buffer, number to answer)."""
        buffer = self.source.events.get(self.event) if self.source is not None else None
        if buffer is None:
            return None, 0
        return buffer, min(buffer.count, self.limit) if self.limit > 0 else buffer.count


class Emitter:
    """A CPU emitter of a system: its particles and the scripts that create and move them."""

    def __init__(self, system, index, handle, export, version):
        self.system, self.index, self.name, self.id = system, index, handle["Name"], handle["Id"]
        self.export, self.version = export, version
        self.local = bool(version.get("bLocalSpace", False))
        self.seed = int(version.get("RandomSeed", 0))
        self.spawn = system.script(version["SpawnScriptProps"]["Script"], handle["Id"])
        self.update = system.script(version["UpdateScriptProps"]["Script"], handle["Id"])
        self.layout = Layout(self.update.attributes + self.spawn.attributes)
        self.data, self.next = self.layout.buffer(), self.layout.buffer()
        self.spawned, self.born, self.age, self.state = 0, 0, 0.0, ACTIVE
        self.instance_seed = int(system.rng.integers(0, 2 ** 31 - 1))
        self.block = bytearray(EMITTER_SIZE)
        self.previous = None
        self.ids = Ids()
        self.infos = [n for n, v in system.layout.vars.items() if v[0] == "NiagaraSpawnInfo" and n.startswith(self.name + ".")]
        # events its update script sends (name: buffer, max per tick); its spawn script's events go nowhere
        self.events, self.sends = {}, []
        for g in version["UpdateScriptProps"].get("EventGenerators") or []:
            buffer = Layout(g["DataSetCompiledData"]["Variables"]).buffer()
            self.events[str(g["ID"])] = buffer
            self.sends.append((buffer, int(g.get("MaxEventsPerFrame", 0))))
        self.scratch = [Layout(g["DataSetCompiledData"]["Variables"]).buffer() for g in version["SpawnScriptProps"].get("EventGenerators") or []]
        self.handlers = [Handler(system, self, handle, h) for h in version.get("EventHandlerScriptProps") or []]

    def fill(self):
        """The emitter's constant block as it stands before a tick."""
        struct.pack_into("<iiffii", self.block, 0, self.data.count, self.spawned, 1.0, self.age, self.seed, self.instance_seed)

    def tick(self, dt):
        system = self.system
        state = system.read(self.name + ".ExecutionState")
        self.state = int(state[0]) if state is not None else ACTIVE
        if self.state in (INACTIVE_CLEAR, DISABLED):
            self.data.count = 0
        spawns, answers = [], []       # (count, start, interval, group); (handler, events, event index, count)
        if self.state == ACTIVE and system.asked == ACTIVE:
            for name in self.infos:
                _, f0, i0, _, _ = system.layout.vars[name]
                count = int(system.data.ints[i0, 0])
                if count > 0:
                    spawns.append((count, float(system.data.floats[f0, 0]), float(system.data.floats[f0 + 1, 0]), int(system.data.ints[i0 + 1, 0])))
            for h in self.handlers:
                # an event spawns its Spawn Number of particles whatever the handler's script then
                # runs on (the spawned ones or every particle)
                events, count = h.events()
                for j in range(count):
                    n = h.number if h.least >= h.number else int(system.rng.integers(h.least, h.number + 1))
                    if n > 0:
                        answers.append((h, events, j, n))
        existing = self.data.count
        self.next.reserve(existing + sum(s[0] for s in spawns) + sum(a[3] for a in answers))
        if self.previous is None:
            self.previous = bytes(self.block)
        for script in [self.update, self.spawn] + [h.script for h in self.handlers]:
            system.bind(script.store)

        def blocks(script):
            now = [system.globals, system.block, system.owner, bytes(self.block), bytes(script.store.data[:script.store.size])]
            if not script.interpolated:
                return now
            return now + [system.previous[0], system.previous[1], system.previous[2], self.previous, bytes(script.store.data[script.store.size:])]

        alive = 0
        if existing:
            self.update.store.put("Engine.ExecutionCount", "<i", existing)
            binding = vm.Binding(self.data, 0, self.next, 0)
            sent = []
            for buffer, _ in self.sends:
                buffer.reserve(existing)
                sent.append(vm.Binding(target=buffer))
            self.update.run(existing, blocks(self.update), [binding] + sent, system.rng, self.ids)
            alive = binding.written
            for (buffer, most), b in zip(self.sends, sent):
                buffer.count = min(b.written, most) if most > 0 else b.written
        else:
            for buffer, _ in self.sends:
                buffer.count = 0
        self.ids.tag += 1
        first = not self.spawned
        self.born = 0

        def spawn(count, start, interval, group):
            nonlocal alive, first
            store = self.spawn.store
            store.put(self.name + ".SpawnInterval", "<f", interval)
            store.put(self.name + ".InterpSpawnStartDt", "<f", start)
            store.put(self.name + ".SpawnGroup", "<i", group)
            store.put("Engine.ExecutionCount", "<i", count)
            if first:
                store.keep()
                first = False
            struct.pack_into("<i", self.block, 4, self.spawned)
            self.spawned += count
            binding = vm.Binding(self.next, alive, self.next, alive)
            scratch = []
            for buffer in self.scratch:
                buffer.reserve(count)
                scratch.append(vm.Binding(target=buffer))
            self.spawn.run(count, blocks(self.spawn), [binding] + scratch, system.rng, self.ids)
            alive += binding.written
            self.born += binding.written
            return binding.written

        for s in spawns:
            spawn(*s)
        # an event's particles are spawned mid-tick, then the handler's script runs on them reading the event
        ran = []
        for h, events, j, n in answers:
            start = alive
            ran.append((h, events, j, start, spawn(n, dt * 0.5, 0.0, 0)))
        for h, events, j, start, n in ran:
            if n and h.spawns:
                h.script.store.put("Engine.ExecutionCount", "<i", n)
                h.script.run(n, blocks(h.script), [vm.Binding(self.next, start, self.next, start), vm.Binding(events, j)], system.rng, self.ids)
        for h in self.handlers:
            events, count = h.events()
            if h.every and alive:
                for j in range(count):
                    h.script.store.put("Engine.ExecutionCount", "<i", alive)
                    h.script.run(alive, blocks(h.script), [vm.Binding(self.next, 0, self.next, 0), vm.Binding(events, j)], system.rng, self.ids)
        self.next.count = alive
        self.data, self.next = self.next, self.data
        self.spawn.store.keep()
        self.previous = bytes(self.block)
        self.age += dt

    def attribute(self, name):
        """A particle attribute's rows (components x particles), or None."""
        v = self.layout.vars.get(name)
        if v is None:
            return None
        _, f0, i0, nf, ni = v
        rows = self.data.floats[f0:f0 + nf] if nf else self.data.ints[i0:i0 + ni]
        return rows[:, :self.data.count]


class System:
    """A Niagara system, ticked. exports: the package's exports; fields: the vector fields they
    sample, by package (both as the app exports them). user: {parameter name: floats} over the
    asset's own. sockets: the sockets a trail's filtered sockets stand for. strict: an emitter's
    script failure raises (tests) instead of leaving the emitter out."""

    def __init__(self, exports, seed=1, user=None, strict=False, fields=None, sockets=None):
        self.exports, self.strict = exports, strict
        # the sockets the game points a trail at (a pickaxe's first and second): what its filtered
        # sockets, in order, stand for where the character has none of their own names
        self.sockets = [str(s).lower() for s in sockets or []]
        self.fields = fields or {}  # vector fields its scripts sample, by package
        self.interfaces = {}        # export index -> data interface (shared by all scripts that call it)
        # the character the effect sits on: the bones and sockets its scripts read (lower case) and
        # where each stands now and a tick ago (name: (position, quaternion) in character space; see place())
        self.reads, self.pose, self.pose_before = set(), {}, {}
        self.component = self.component_before = np.eye(4)
        self.placed, self.dt = None, 1.0 / 60.0
        # the camera a script asks for: position, forward, up, right (UE's axes, cm)
        self.camera = ((-500.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, 1.0, 0.0))
        self.fov = 80.0     # field of view, degrees (the game's default)
        self.asset = next(e for e in exports if e["type"] == "NiagaraSystem")
        # user-defined structs its data sets hold (the app lays each out: floats, ints)
        for e in exports:
            if e["type"] == "MPStruct":
                TYPES[e["name"]] = (int(e["props"]["Floats"]), int(e["props"]["Ints"]))
        props = self.props = self.asset["props"]
        self.rng = np.random.default_rng(seed)
        self.seed = seed
        compiled = props.get("SystemCompiledData") or {}
        self.compiled = compiled
        self.layout = Layout(compiled["DataSetCompiledData"]["Variables"])
        self.data, self.next = self.layout.buffer(1), self.layout.buffer(1)
        self.user = Store(compiled.get("InstanceParamStore") or {})
        # the parameter collections its scripts read (NPC.FortniteNPC.FortniteActiveTimeOfDay: which
        # time of day's tint a smoke takes), with the values the app sends. A script's cooked store
        # holds placeholders for these (every time of day at once), not the values.
        self.shared = Store({})
        for e in exports:
            if e["type"] == "MPCollection":
                one = Store(e["props"].get("ParameterStorage") or {})
                for name, (offset, tname) in one.offsets.items():
                    self.shared.offsets.setdefault(name, (len(self.shared.data) + offset, tname))
                self.shared.data += one.data
                self.shared.size = len(self.shared.data)
        for name, value in (user or {}).items():
            self.set_user(name, value)
        self.cooked = {(k["Key"]["EmitterHandleId"], k["Key"]["ScriptUsage"].split("::")[-1], k["Key"].get("ScriptUsageId") or NO_ID): k["Value"]
                       for k in props.get("ScriptRuntimeCookedDataMap") or []}
        self.age, self.ticks, self.state = 0.0, 0, ACTIVE
        self.asked = ACTIVE     # what the game asks of it (Engine.Owner.ExecutionState); deactivate() asks it to stop
        self.globals, self.block, self.owner = bytearray(GLOBAL_SIZE), bytearray(SYSTEM_SIZE), bytearray(OWNER_SIZE)
        for at in range(0, 384, 64):
            self.owner[at:at + 64] = IDENTITY
        struct.pack_into("<4f", self.owner, 384, 0, 0, 0, 1)                         # rotation
        struct.pack_into("<4f4f4f4f", self.owner, 432, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 1, 1, 1, 0)    # axes, scale
        self.previous = None
        self.unanswered = set()     # what its scripts ask for that answers zeros here ("Grid2DCollection.SetNumCells")
        self.meshless = False       # whether a script samples a static mesh (none here: NoMesh)
        self.spawn = self.script(props["SystemSpawnScript"], None)
        self.update = self.script(props["SystemUpdateScript"], None)
        self.spawn_inputs = Layout(compiled["SpawnInstanceParamsDataSetCompiledData"]["Variables"])
        self.update_inputs = Layout(compiled["UpdateInstanceParamsDataSetCompiledData"]["Variables"])
        self.handles = props.get("EmitterHandles") or []
        for h in self.handles:     # an older build's dump (28.00) names the handle's guid "ID"
            if isinstance(h, dict) and "Id" not in h and "ID" in h:
                h["Id"] = h["ID"]
        self.emitters, self.skipped = [], []        # skipped: (emitter name, why)
        self.approximate = []                       # stateless emitters: played from their settings, apart from the engine's random draws
        self.gpu = []                               # GPU emitters among them: their motion is a stand-in (niagara_gpu)
        for index, handle in enumerate(self.handles):
            if not handle.get("bIsEnabled", True):
                continue
            if "Stateless" in str(handle.get("EmitterMode")):
                # no script: the module settings are worked out (niagara_stateless)
                export = self.export(handle.get("StatelessEmitter"))
                if export is None:      # the cook left it out: not for this platform
                    continue
                try:
                    from .. import niagara_stateless
                    self.emitters.append(niagara_stateless.Emitter(self, index, handle, export))
                    self.approximate.append(handle["Name"])
                except Exception as e:
                    if strict:
                        raise
                    self.skipped.append((handle["Name"], "stateless: %s" % e))
                continue
            export = self.export((handle.get("VersionedInstance") or {}).get("Emitter"))
            versions = (export or {}).get("props", {}).get("VersionData") or []
            if not versions:
                continue
            wanted = handle["VersionedInstance"].get("Version")
            version = next((v for v in versions if (v.get("Version") or {}).get("VersionGuid") == wanted), versions[0])
            if "GPU" in str(version.get("SimTarget")):
                # no script to run (a compiled shader): use what the asset keeps; the motion is a stand-in (niagara_gpu)
                try:
                    from .. import niagara_gpu
                    self.emitters.append(niagara_gpu.Emitter(self, index, handle, export, version))
                    self.approximate.append(handle["Name"])
                    self.gpu.append(handle["Name"])
                except Exception as e:
                    if strict:
                        raise
                    self.skipped.append((handle["Name"], "GPU: %s" % e))
                continue
            try:
                self.emitters.append(Emitter(self, index, handle, export, version))
            except Unsupported as e:
                self.skipped.append((handle["Name"], str(e)))
        for e in self.emitters:
            for h in e.handlers:
                h.source = next((o for o in self.emitters if o.id == h.source_id), None)

    def unresolved(self):
        """Bones and sockets its scripts read that the character's pose lacks."""
        names = set()
        for interface in self.interfaces.values():
            if isinstance(interface, Skeleton):
                names.update(interface.names[i] for i in range(len(interface.names))
                             if interface.held(self.pose, i) is None and interface.names[i].lower() != "none")
        return sorted(names)

    def _read(self):
        """Values of the parameter collections its scripts read, by name."""
        scripts = [self.spawn, self.update] + [s for e in self.emitters for s in
                                               (getattr(e, "spawn", None), getattr(e, "update", None), *[h.script for h in getattr(e, "handlers", ())]) if s is not None]
        return {name for s in scripts for name in s.store.offsets if name in self.shared.offsets}

    def users(self):
        """What the game sets, as a number or a few: the user parameters and the parameter
        collections' values its scripts read. Returns [(name, type, value)]."""
        found = []
        read = self._read()
        for store in (self.user, self.shared):
            for name, (offset, tname) in sorted(store.offsets.items()):
                if store is self.shared and name not in read:
                    continue
                nf, ni = TYPES.get(tname, (0, 0))
                if tname == "NiagaraBool":
                    found.append((name, tname, struct.unpack_from("<i", store.data, offset)[0] != 0))
                elif ni == 1 and not nf:
                    found.append((name, tname, struct.unpack_from("<i", store.data, offset)[0]))
                elif nf and not ni:
                    values = struct.unpack_from("<%df" % nf, store.data, offset)
                    found.append((name, tname, values[0] if nf == 1 else list(values)))
        return found

    def set_user(self, name, value):
        """Override a user parameter's (or parameter collection's) asset value (a number, a bool or floats)."""
        name = name if name.startswith(("User.", "NPC.")) else "User." + name
        # UE's names are case-insensitive
        name = next((n for n in list(self.user.offsets) + list(self.shared.offsets) if n.lower() == name.lower()), name)
        store = self.user if name in self.user.offsets else self.shared if name in self.shared.offsets else None
        if store is None:
            return
        tname = store.offsets[name][1]
        nf, ni = TYPES.get(tname, (0, 0))
        if tname == "NiagaraBool":
            store.put(name, "<i", -1 if value else 0)
        elif ni == 1 and not nf:
            store.put(name, "<i", int(value))
        elif nf:
            values = list(value) if hasattr(value, "__len__") else [value]
            store.put(name, "<%df" % nf, *[float(v) for v in (values + [0.0] * nf)[:nf]])

    def place(self, owner=None, component=None, pose=None):
        """Where the effect stands for the next tick: the owner's transform and the character's (4x4,
        UE's: rows the axes then the origin, cm), and the character's pose ({lower-case bone or
        socket name: (position, quaternion)} in character space). What was told last becomes the
        previous tick's."""
        self.component_before, self.pose_before = self.component, self.pose
        if component is not None:
            self.component = np.asarray(component, np.float64).reshape(4, 4)
        if pose is not None:
            self.pose = pose
        if owner is not None:
            self.placed = np.asarray(owner, np.float64).reshape(4, 4)

    def deactivate(self):
        """The game stops the effect (a timed notify ends): emitters spawn no more and particles play out."""
        self.asked = INACTIVE

    def _owner(self, dt):
        """The owner's constant block from where it was placed (velocity from where it was before)."""
        m = self.placed
        if m is None:
            return
        rows = m[:3, :3]
        scale = np.linalg.norm(rows, axis=1)
        unit = rows / np.maximum(scale, 1e-9)[:, None]
        plain = np.eye(4)
        plain[:3, :3], plain[3, :3] = unit, m[3, :3]
        inverse, plain_inverse = np.linalg.inv(m), np.linalg.inv(plain)
        before = struct.unpack_from("<3f", self.owner, 400)
        velocity = (m[3, :3] - before) / dt if self.ticks else np.zeros(3)
        for at, matrix in enumerate((m, inverse, m.T, inverse.T, plain, plain_inverse)):
            self.owner[64 * at:64 * at + 64] = matrix.astype(F).tobytes()
        struct.pack_into("<4f", self.owner, 384, *_quaternion(unit))
        for at, v in ((400, m[3, :3]), (416, velocity), (432, unit[0]), (448, unit[1]), (464, unit[2]), (480, scale)):
            struct.pack_into("<3f", self.owner, at, *v)

    def export(self, ref):
        """The export a reference points at, if it is in this package."""
        if not ref or not ref.get("ObjectPath"):
            return None
        path, _, index = ref["ObjectPath"].rpartition(".")
        if not index.isdigit() or int(index) >= len(self.exports) or path != self.asset["outer"]:
            return None
        return self.exports[int(index)]

    def interface(self, ref):
        """The data interface a reference points at, or None if it can't run."""
        export = self.export(ref)
        if export is None or export["type"] not in INTERFACES:
            return None
        index = self.exports.index(export)
        if index not in self.interfaces:
            self.interfaces[index] = INTERFACES[export["type"]](self, export["type"], export["props"])
        return self.interfaces[index]

    def script(self, ref, handle):
        export = self.export(ref)
        if export is None:
            raise Unsupported("script not in the package")
        usage = str(export["props"].get("Usage")).split("::")[-1]
        cooked = self.cooked.get((handle or NO_ID, usage, export["props"].get("UsageId") or NO_ID))
        if cooked is None and "ScriptExecutionParamStore" not in export["props"]:
            raise Unsupported("the asset isn't cooked (its scripts' parameters aren't laid out)")
        if cooked is None:       # older assets keep the store on the script itself
            cooked = {"CookedExecutionParameterStore": export["props"].get("ScriptExecutionParamStore") or {},
                      "CookedScriptRuntimeCompiledData": {"ResolvedDataInterfaces": export["props"].get("ResolvedDataInterfaces") or []}}
        return Script(self, export["props"], cooked, next((h["Name"] for h in self.props.get("EmitterHandles") or [] if handle and h.get("Id") == handle), None),
                      lenient=handle is None)

    def read(self, name):
        """A system data set variable's values (floats, else ints), or None."""
        v = self.layout.vars.get(name)
        if v is None:
            return None
        _, f0, i0, nf, ni = v
        return self.data.floats[f0:f0 + nf, 0] if nf else self.data.ints[i0:i0 + ni, 0]

    def bind(self, store):
        """Script parameters that name a system data set variable or a user parameter take its value."""
        for name, (offset, tname) in store.offsets.items():
            v = self.layout.vars.get(name)
            if v is not None:
                _, f0, i0, nf, ni = v
                store.data[offset:offset + 4 * nf] = self.data.floats[f0:f0 + nf, 0].tobytes()
                store.data[offset + 4 * nf:offset + 4 * (nf + ni)] = self.data.ints[i0:i0 + ni, 0].tobytes()
            elif name in self.user.offsets:
                raw = self.user.raw(name)
                store.data[offset:offset + len(raw)] = raw
            elif name in self.shared.offsets:
                raw = self.shared.raw(name)
                store.data[offset:offset + len(raw)] = raw

    def _inputs(self, layout, prefix):
        """The engine values a system script reads per instance, as the asset binds them."""
        buffer = layout.buffer(1)
        buffer.count = 1

        def copy(binding, block):
            for b in (binding or {}).get("FloatOffsets") or []:
                buffer.floats[b["DataSetComponentOffset"], 0] = struct.unpack_from("<f", block, b["ParameterOffset"])[0]
            for b in (binding or {}).get("Int32Offsets") or []:
                buffer.ints[b["DataSetComponentOffset"], 0] = struct.unpack_from("<i", block, b["ParameterOffset"])[0]

        copy(self.compiled.get(prefix + "GlobalBinding"), self.globals)
        copy(self.compiled.get(prefix + "SystemBinding"), self.block)
        copy(self.compiled.get(prefix + "OwnerBinding"), self.owner)
        blocks = {e.index: e.block for e in self.emitters}
        for index, binding in enumerate(self.compiled.get(prefix + "EmitterBindings") or []):
            block = blocks.get(index)
            if block is None:       # an emitter not run here: no particles, spawn scale 1
                block = struct.pack("<iiffii", 0, 0, 1.0, self.age, 0, 0).ljust(EMITTER_SIZE, b"\0")
            copy(binding, block)
        for name, (_, f0, i0, nf, ni) in layout.vars.items():
            if name in self.user.offsets:
                raw = self.user.raw(name)
                if nf:
                    buffer.floats[f0:f0 + nf, 0] = np.frombuffer(raw, F)
                else:
                    buffer.ints[i0:i0 + ni, 0] = np.frombuffer(raw, I)
        return buffer

    def tick(self, dt):
        self.dt = dt
        self._owner(dt)
        struct.pack_into("<5fi", self.globals, 0, dt, dt, 1.0 / dt, self.age, self.age, QUALITY)
        # time since rendered, LOD distance and its fraction, age, execution state, ticks, emitters, alive emitters, significance, seed
        alive = sum(1 for e in self.emitters if e.state != COMPLETE)
        struct.pack_into("<4fI5i", self.block, 0, 0.0, 0.0, 0.0, self.age, self.asked, self.ticks, len(self.handles), alive, 0, self.seed)
        for e in self.emitters:
            e.fill()
        if self.previous is None:
            self.previous = (bytes(self.globals), bytes(self.block), bytes(self.owner))
        if self.ticks == 0:
            binding = vm.Binding(target=self.data)
            self.spawn.run(1, [self.globals, bytes(self.spawn.store.data)], [binding, vm.Binding(self._inputs(self.spawn_inputs, "SpawnInstance"))], self.rng)
            self.data.count = 1
        binding = vm.Binding(self.data, 0, self.next, 0)
        self.update.run(1, [self.globals, bytes(self.update.store.data)], [binding, vm.Binding(self._inputs(self.update_inputs, "UpdateInstance"))], self.rng)
        if binding.written:
            self.data, self.next = self.next, self.data
            self.data.count = 1
        state = self.read("System.ExecutionState")
        self.state = int(state[0]) if state is not None else ACTIVE
        for e in list(self.emitters):
            try:
                e.tick(dt)
            except Exception as x:      # one emitter's script failing leaves the others playing
                if self.strict:
                    raise
                self.emitters.remove(e)
                self.skipped.append((e.name, "its script failed: %s" % x))
        self.previous = (bytes(self.globals), bytes(self.block), bytes(self.owner))
        self.age += dt
        self.ticks += 1

    @property
    def done(self):
        """Nothing left to see: the system is no longer active and no particle lives."""
        return (self.state != ACTIVE or self.asked != ACTIVE) and all(e.data.count == 0 and not getattr(e, "pending", False) for e in self.emitters)
