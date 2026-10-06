"""Material Porter fork: a Niagara system replayed from its cooked asset.

What the engine does around the scripts, redone here: a system keeps one instance of a data set
its two scripts (spawn once, update every tick) write the emitters' state into (age, loops,
execution state, how many particles to spawn); each CPU emitter then runs its update script over
its particles and its spawn script over the new ones (niagara_vm runs the scripts). The scripts
read the engine's values (time step, the owner's transform, particle counts) from constant
blocks laid out as the engine's structs, their own parameters from a store the asset cooks with
each script, and their curves from data interfaces.

The asset comes as the app exports it: the package's exports, each {name, type, outer, props},
in the package's order (a reference's ObjectPath ends in the export's index).

GPU emitters keep no script to run (a compiled shader only): niagara_gpu stands in for them from
what the asset keeps (spawn counts, curves, renderers). Stateless emitters have no script either,
only settings: niagara_stateless works their particles out.
"""
import base64
import struct

import numpy as np

from . import niagara_vm as vm
from .niagara_vm import F, I, Unsupported

# a type's components in a data set: (floats, int32s)
TYPES = {
    "NiagaraFloat": (1, 0), "NiagaraInt32": (0, 1), "NiagaraBool": (0, 1),
    "Vector2f": (2, 0), "Vector3f": (3, 0), "NiagaraPosition": (3, 0), "Vector4f": (4, 0),
    "LinearColor": (4, 0), "Quat4f": (4, 0), "Matrix44f": (16, 0), "NiagaraMatrix": (16, 0),
    "NiagaraID": (0, 2), "NiagaraSpawnInfo": (2, 2), "NiagaraRandInfo": (0, 3),
}
# half-precision attributes (an emitter's compressed ones): their own rows, after the floats
HALVES = {"NiagaraHalf": 1, "NiagaraHalfVector2": 2, "NiagaraHalfVector3": 3, "NiagaraHalfVector4": 4}
ACTIVE, INACTIVE, INACTIVE_CLEAR, COMPLETE, DISABLED = range(5)      # ENiagaraExecutionState
QUALITY = 3     # the quality level replayed at (Engine.QualityLevel: 0 low to 4 cinematic)

# the engine's constant blocks (FNiagaraGlobalParameters, ...SystemParameters, ...OwnerParameters, ...EmitterParameters)
GLOBAL_SIZE, SYSTEM_SIZE, OWNER_SIZE, EMITTER_SIZE = 32, 64, 512, 32
NO_ID = "00000000-00000000-00000000-00000000"
IDENTITY = struct.pack("<16f", 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1)


def type_name(typedef):
    """'NiagaraFloat' of a variable's TypeDef; None for none."""
    ref = (typedef or {}).get("ClassStructOrEnum")
    if not ref:
        return None
    name = ref["ObjectName"]
    return name.split("'")[1] if "'" in name else name


def components(typedef):
    name = type_name(typedef)
    if name in TYPES:
        return TYPES[name]
    # (UnderlyingType: 1 a class, 2 a struct, 3 an enum; older assets leave it out)
    if typedef.get("UnderlyingType") == 3 or (typedef.get("UnderlyingType") != 2 and name and name.startswith("E") and name[1:2].isupper()):
        return (0, 1)       # an enum
    raise Unsupported("type %s in a data set" % name)


class Layout:
    """Where each variable of a data set sits: name -> (type, first float row, first int row, floats, ints)."""

    def __init__(self, variables):
        self.vars, self.floats, self.ints, self.halves = {}, 0, 0, 0
        half = []
        for v in variables:
            if v["Name"] in self.vars:
                continue
            kind = type_name(v["TypeDef"])
            if kind in HALVES:
                self.vars[v["Name"]] = None
                half.append((v["Name"], kind, self.halves, HALVES[kind]))
                self.halves += HALVES[kind]
                continue
            nf, ni = components(v["TypeDef"])
            self.vars[v["Name"]] = (kind, self.floats, self.ints, nf, ni)
            self.floats += nf
            self.ints += ni
        # a half attribute reads as floats: its rows come after the float rows (the VM's half
        # reads and writes count from there)
        for name, kind, at, n in half:
            self.vars[name] = (kind, self.floats + at, 0, n, 0)

    def buffer(self, capacity=0):
        b = vm.Buffer(self.floats + self.halves, self.ints, capacity)
        b.half_base = self.floats
        return b


class Store:
    """A script's parameters as cooked with it: a block of bytes, each parameter at its offset.
    A spawn script that interpolates has the block twice: this tick's values, then last tick's."""

    def __init__(self, cooked, copies=1):
        self.size = int(cooked.get("ParameterSize") or 0)
        data = bytes(cooked.get("ParameterData") or [])
        self.data = bytearray(data.ljust(self.size * copies, b"\0"))
        self.offsets, self.interfaces = {}, cooked.get("DataInterfaces") or []
        for p in cooked.get("SortedParameterOffsets") or []:
            if p["TypeDef"].get("UnderlyingType") == 1:     # a data interface: its offset is its index
                continue
            self.offsets[p["Name"]] = (p["Offset"], type_name(p["TypeDef"]))

    def put(self, name, fmt, *values):
        if name in self.offsets:
            struct.pack_into(fmt, self.data, self.offsets[name][0], *values)

    def raw(self, name):
        """A parameter's bytes."""
        offset, tname = self.offsets[name]
        nf, ni = TYPES.get(tname, (0, 1))
        return bytes(self.data[offset:offset + 4 * (nf + ni)])

    def keep(self):
        """This tick's values become last tick's."""
        if len(self.data) >= 2 * self.size:
            self.data[self.size:2 * self.size] = self.data[:self.size]


def _specifiers(listed):
    """A function's specifiers ({Key, Value} pairs) as a dict."""
    found = {}
    for item in listed or []:
        if isinstance(item, dict) and "Key" in item:
            found[str(item["Key"])] = str(item.get("Value"))
    return found


def _zeros(outputs):
    return lambda count, inputs: [np.zeros(count, I)] * outputs


def _rich(curve, x):
    """An editor curve (FRichCurve) at the times x: constant outside its keys, linear or cubic between."""
    keys = sorted((curve or {}).get("Keys") or [], key=lambda k: k.get("Time", 0.0))
    if not keys:
        return np.full(len(x), (curve or {}).get("DefaultValue", 0.0) if abs((curve or {}).get("DefaultValue", 0.0)) < 1e30 else 0.0, F)
    times = np.array([k.get("Time", 0.0) for k in keys], np.float64)
    values = np.array([k.get("Value", 0.0) for k in keys], np.float64)
    out = np.empty(len(x), np.float64)
    at = np.clip(np.searchsorted(times, x, side="right") - 1, 0, len(keys) - 1)
    for i in range(len(keys)):
        mine = at == i
        if not mine.any():
            continue
        if i == len(keys) - 1:
            out[mine] = values[i]
            continue
        span = times[i + 1] - times[i]
        t = np.clip((x[mine] - times[i]) / span, 0.0, 1.0) if span > 0 else np.zeros(int(mine.sum()))
        mode = str(keys[i].get("InterpMode"))
        if "Constant" in mode:
            out[mine] = values[i]
        elif "Cubic" in mode:
            p0, p3 = values[i], values[i + 1]
            p1 = p0 + keys[i].get("LeaveTangent", 0.0) * span / 3.0
            p2 = p3 - keys[i + 1].get("ArriveTangent", 0.0) * span / 3.0
            out[mine] = (1 - t) ** 3 * p0 + 3 * (1 - t) ** 2 * t * p1 + 3 * (1 - t) * t ** 2 * p2 + t ** 3 * p3
        else:
            out[mine] = values[i] + (values[i + 1] - values[i]) * t
    out[x < times[0]] = values[0]
    return out.astype(F)


class Curve:
    """A curve data interface, sampled the way the engine's CPU scripts do: from its baked table
    (one made from the curve's keys here, where the asset keeps none)."""

    OUTPUTS = {"NiagaraDataInterfaceCurve": ["Curve"], "NiagaraDataInterfaceVector2DCurve": ["XCurve", "YCurve"],
               "NiagaraDataInterfaceVectorCurve": ["XCurve", "YCurve", "ZCurve"],
               "NiagaraDataInterfaceVector4Curve": ["XCurve", "YCurve", "ZCurve", "WCurve"],
               "NiagaraDataInterfaceColorCurve": ["RedCurve", "GreenCurve", "BlueCurve", "AlphaCurve"]}

    def __init__(self, system, kind, props):
        channels = self.OUTPUTS[kind]
        self.width = len(channels)
        lut = props.get("ShaderLUT")
        if lut and props.get("bUseLUT", True):
            self.lut = np.asarray(lut, F).reshape(-1, self.width)
            self.start = F(props.get("LUTMinTime", 0.0))
            scale = props.get("LUTInvTimeRange", 1.0)
            self.scale = F(scale) if isinstance(scale, (int, float)) and abs(scale) < 1e30 else F(0.0)
        else:
            keyed = [k.get("Time", 0.0) for c in channels for k in (props.get(c) or {}).get("Keys") or []]
            first, last = (min(keyed), max(keyed)) if keyed else (0.0, 1.0)
            x = np.linspace(first, last, 256)
            self.lut = np.stack([_rich(props.get(c), x) for c in channels], axis=1)
            self.start = F(first)
            self.scale = F(1.0 / (last - first)) if last > first else F(0.0)
        self.last = F(len(self.lut) - 1)

    def function(self, name, specifiers, inputs, outputs):
        if not name.startswith("Sample"):
            raise Unsupported("Curve.%s" % name)

        def sample(count, inputs):
            x = np.clip(np.nan_to_num((vm.fl(inputs[-1]) - self.start) * self.scale * self.last), F(0), self.last)
            a = np.trunc(x)
            b = np.where(a < self.last, a + F(1), a)
            t = (x - a)[..., None]
            v = self.lut[a.astype(I)] * (F(1) - t) + self.lut[b.astype(I)] * t
            return [v[..., i] for i in range(self.width)]
        return sample


class Array:
    """An array data interface: the asset's values (one a script may add to or change while it plays)."""

    def __init__(self, system, kind, props):
        listed = next((v for k, v in props.items() if k.endswith("Data") and isinstance(v, list)), [])
        self.ints = kind.endswith(("Int32", "Bool", "UInt8", "NiagaraID"))
        rows = []
        for item in listed:
            if isinstance(item, dict):
                keys = [k for k in ("X", "Y", "Z", "W") if k in item] or [k for k in ("R", "G", "B", "A") if k in item]
                rows.append([float(item[k]) for k in keys])
            elif isinstance(item, bool):
                rows.append([-1 if item else 0])
            else:
                rows.append([item])
        # an empty one (a script fills it as it plays) is as wide as its type says, whatever asks first
        width = next((w for suffix, w in (("Float2", 2), ("Float3", 3), ("Position", 3), ("Float4", 4), ("Color", 4), ("Quat", 4),
                                          ("NiagaraID", 2), ("Matrix", 16)) if kind.endswith(suffix)), 1)
        self.rows = np.array(rows, I if self.ints else F).reshape(len(rows), -1) if rows else np.zeros((0, width), I if self.ints else F)
        # what reading an empty one gives (the engine's GetDefaultValue): white for a colour (a variant's
        # colour array the game fills: Cerberus's flames are white x their material's colours, not black),
        # the identity for a quaternion or a matrix, else zeros
        self.default = (1.0, 1.0, 1.0, 1.0) if kind.endswith("Color") else (0.0, 0.0, 0.0, 1.0) if kind.endswith("Quat") else \
            tuple(np.eye(4, dtype=float).ravel()) if kind.endswith("Matrix") else None

    def function(self, name, specifiers, inputs, outputs):
        kind = I if self.ints else F

        def values(args, width):
            """The last `width` inputs as (count, width)."""
            view = vm.it if self.ints else vm.fl
            return [view(a) for a in args[len(args) - width:]]

        if name in ("Length", "Num"):
            return lambda count, args: [np.full(count, len(self.rows), I)]
        if name == "IsValidIndex":
            return lambda count, args: [np.where((vm.it(args[-1]) >= 0) & (vm.it(args[-1]) < len(self.rows)), I(-1), I(0)) + np.zeros(count, I)]
        if name in ("LastIndex", "GetLastIndex"):
            return lambda count, args: [np.full(count, len(self.rows) - 1, I)]
        if name == "Get":
            def get(count, args):
                table = self.rows
                if not len(table):
                    if self.default is not None and len(self.default) == outputs:
                        return [np.full(count, v, kind) for v in self.default]
                    return [np.zeros(count, kind)] * outputs
                index = np.clip(np.broadcast_to(vm.it(args[-1]), (count,)), 0, len(table) - 1)
                return [table[index, i] for i in range(outputs)]
            return get
        if name in ("Add", "SetArrayElem", "Resize", "Clear", "RemoveLastElem", "Reset"):
            # the arguments: (the interface's own), whether to skip, (an index), the value
            def change(count, args):
                if name == "Clear" or name == "Reset":
                    self.rows = self.rows[:0]
                    return []
                if name == "Resize":
                    table, size = self.rows, max(int(np.max(vm.it(args[-1]))), 0)
                    grown = np.zeros((size, table.shape[1]), table.dtype)
                    grown[:min(size, len(table))] = table[:size]
                    self.rows = grown
                    return []
                if name == "RemoveLastElem":
                    table = self.rows
                    if not len(table):
                        return [np.zeros(count, kind)] * (outputs - 1) + [np.zeros(count, I)]
                    last, self.rows = table[-1], table[:-1]
                    return [np.full(count, last[i], kind) for i in range(outputs - 1)] + [np.full(count, -1, I)]
                width = self.width_of(args, name)
                skip_at = inputs - width - (2 if name == "SetArrayElem" else 1)
                table = self.rows
                skip = np.broadcast_to(vm.it(args[skip_at]), (count,)) != 0
                new = np.stack([np.broadcast_to(v, (count,)) for v in values(args, width)], axis=1)
                if name == "Add":
                    self.rows = np.concatenate([table, new[~skip].astype(table.dtype)])
                else:
                    index = np.broadcast_to(vm.it(args[skip_at + 1]), (count,))
                    ok = ~skip & (index >= 0) & (index < len(table))
                    table[index[ok]] = new[ok]
                return []
            return change
        raise Unsupported("Array.%s" % name)

    def width_of(self, args, name):
        """How many components a value has: the table's, else what the call's inputs leave."""
        if self.rows.shape[1]:
            return self.rows.shape[1]
        return max(len(args) - (3 if name == "SetArrayElem" else 2), 1)


class ParticleRead:
    """Another emitter's particles, read by a script (the emitter as it stands when the script runs)."""

    def __init__(self, system, kind, props):
        # the emitter it names (older assets: on the interface itself); none named: the script's own
        self.system, self.source = system, props.get("EmitterName") or (props.get("EmitterBinding") or {}).get("EmitterName")
        self.caller = None      # the emitter whose script is being bound (Script sets it)

    def function(self, name, specifiers, inputs, outputs):
        attribute = _specifiers(specifiers).get("Attribute")
        source = self.source if self.source and str(self.source) != "None" else self.caller
        reader = _Reader(self.system, source)

        def nothing(count):
            return [np.zeros(count, I)] * outputs

        if name == "Get Num Particles":
            return lambda count, args: [np.full(count, e.data.count if (e := reader.emitter()) else 0, I)]
        if name == "Get Num Spawned Particles":
            return lambda count, args: [np.full(count, e.born if (e := reader.emitter()) else 0, I)]
        if name == "GetLocalSpace":
            return lambda count, args: [np.full(count, -1 if (e := reader.emitter()) and e.local else 0, I)]
        if attribute and name.endswith("By Index"):
            def by_index(count, args):
                e = reader.emitter()
                rows = e.attribute(attribute) if e else None
                if rows is None or not rows.shape[1]:
                    return nothing(count)
                index = np.broadcast_to(vm.it(args[-1]), (count,))
                valid = (index >= 0) & (index < rows.shape[1])
                safe = np.clip(index, 0, rows.shape[1] - 1)
                return ([np.where(valid, I(-1), I(0))] + [np.where(valid, row[safe], row.dtype.type(0)) for row in rows])[:outputs]
            return by_index
        if attribute and name.endswith("By ID"):
            def by_id(count, args):
                e = reader.emitter()
                rows, ids = (e.attribute(attribute), e.attribute("ID")) if e else (None, None)
                if rows is None or ids is None or not rows.shape[1]:
                    return nothing(count)
                order = np.argsort(ids[0], kind="stable")
                wanted = np.broadcast_to(vm.it(args[-2]), (count,))
                at = np.clip(np.searchsorted(ids[0][order], wanted), 0, len(order) - 1)
                safe = order[at]
                valid = ids[0][safe] == wanted
                return ([np.where(valid, I(-1), I(0))] + [np.where(valid, row[safe], row.dtype.type(0)) for row in rows])[:outputs]
            return by_id
        raise Unsupported("ParticleRead.%s" % name)


class _Reader:
    """The emitter one bound function of a ParticleRead reads."""

    def __init__(self, system, source):
        self.system, self.source = system, source

    def emitter(self):
        return next((e for e in self.system.emitters if e.name == self.source), None)


class RendererInfo:
    """A renderer's settings, read by a script."""

    def __init__(self, system, kind, props):
        self.kind = kind
        target = system.export(props.get("SpriteRenderer") or props.get("MeshRenderer"))
        self.props = target["props"] if target else {}

    def function(self, name, specifiers, inputs, outputs):
        if name == "GetSubUVDetails" and outputs == 3:
            size = self.props.get("SubImageSize") or {"X": 1.0, "Y": 1.0}
            blend = I(-1) if self.props.get("bSubImageBlend") else I(0)
            return lambda count, args: [np.full(count, blend, I), np.full(count, size["X"], F), np.full(count, size["Y"], F)]
        if name == "GetNumMeshes":
            return lambda count, args: [np.full(count, len(self.props.get("Meshes") or []), I)]
        if name == "GetMeshLocalBounds" and outputs == 9 and self.props.get("MPBounds"):
            # a mesh's bounds with the renderer's scale of it (the app works them out): min, max, size
            boxes = np.array(self.props["MPBounds"], F).reshape(-1, 6)
            rows = np.concatenate([boxes, boxes[:, 3:] - boxes[:, :3]], axis=1)

            def bounds(count, args):
                index = np.clip(np.broadcast_to(vm.it(args[-1]), (count,)), 0, len(rows) - 1)
                return [rows[index, i] for i in range(9)]
            return bounds
        raise Unsupported("%s.%s" % (self.kind.replace("NiagaraDataInterface", ""), name))


def _quaternion(rows):
    """A rotation matrix (UE's: its rows the axes) as UE's quaternion (x, y, z, w)."""
    m = np.asarray(rows, np.float64)
    trace = m[0, 0] + m[1, 1] + m[2, 2]
    if trace > 0:
        s = np.sqrt(trace + 1.0)
        k = 0.5 / s
        return np.array([(m[1, 2] - m[2, 1]) * k, (m[2, 0] - m[0, 2]) * k, (m[0, 1] - m[1, 0]) * k, 0.5 * s])
    i = int(np.argmax([m[0, 0], m[1, 1], m[2, 2]]))
    j, k = (i + 1) % 3, (i + 2) % 3
    s = np.sqrt(max(m[i, i] - m[j, j] - m[k, k] + 1.0, 1e-20))
    q = np.zeros(4)
    q[i] = 0.5 * s
    f = 0.5 / s
    q[3] = (m[j, k] - m[k, j]) * f
    q[j] = (m[i, j] + m[j, i]) * f
    q[k] = (m[i, k] + m[k, i]) * f
    return q


def _multiply(a, b):
    """Quaternions (x, y, z, w; a one, b many): b's rotation, then a's."""
    ax, ay, az, aw = a
    bx, by, bz, bw = b[:, 0], b[:, 1], b[:, 2], b[:, 3]
    return np.stack([aw * bx + ax * bw + ay * bz - az * by, aw * by - ax * bz + ay * bw + az * bx,
                     aw * bz + ax * by - ay * bx + az * bw, aw * bw - ax * bx - ay * by - az * bz], axis=1)


class Skeleton:
    """A skeletal mesh's bones and sockets (or an actor's sockets), as the replay was told they
    stand (System.place: the character's pose). Without a character every one sits at the
    character's origin, unturned."""

    def __init__(self, system, kind, props):
        self.system = system
        self.reader = kind.endswith("SocketReader")
        self.bones = [] if self.reader else [str(b) for b in props.get("FilteredBones") or []]
        self.sockets = [str(b) for b in props.get("FilteredSockets") or []]
        self.names = self.bones + self.sockets
        system.reads.update(n.lower() for n in self.names if n.lower() != "none")
        system.reads.update(system.sockets)
        self.cached = None

    def held(self, pose, index):
        """A bone's or socket's (position, rotation) in a pose; a socket without its own name there: the one the game points it at."""
        name = self.names[index].lower()
        at = index - len(self.bones)
        found = pose.get(name)
        if found is None and 0 <= at < len(self.system.sockets):
            found = pose.get(self.system.sockets[at])
        return found

    def tables(self):
        """The bones' positions and rotations now and a tick ago: in the character's space, and in the world."""
        system = self.system
        if self.cached is not None and self.cached[0] == system.ticks:
            return self.cached[1]
        built = []
        for pose, component in ((system.pose, system.component), (system.pose_before, system.component_before)):
            held = [self.held(pose, i) for i in range(len(self.names))] or [None]
            position = np.array([h[0] if h else (0.0, 0.0, 0.0) for h in held], np.float64)
            rotation = np.array([h[1] if h else (0.0, 0.0, 0.0, 1.0) for h in held], np.float64)
            rows = component[:3, :3]
            unit = rows / np.maximum(np.linalg.norm(rows, axis=1, keepdims=True), 1e-9)
            built.append((position, rotation, position @ rows + component[3, :3], _multiply(_quaternion(unit), rotation)))
        self.cached = (system.ticks, built)
        return built

    def function(self, name, specifiers, inputs, outputs):
        bones, sockets = max(len(self.bones), 1), max(len(self.sockets), 1)
        if name.endswith("Count"):
            n = sockets if "Socket" in name and "OrBone" not in name else bones if "Socket" not in name else bones + sockets
            return lambda count, args: [np.full(count, n, I)]
        if name.startswith("Random"):
            first, n = (len(self.bones), sockets) if "Socket" in name and "OrBone" not in name else (0, bones if "Socket" not in name else len(self.names) or 1)
            return lambda count, args: [(first + self.system.rng.integers(0, n, count)).astype(I)]
        if name == "GetComponentToWorld":
            def component(count, args):
                m = self.system.component
                rows = m[:3, :3]
                scale = np.linalg.norm(rows, axis=1)
                q = _quaternion(rows / np.maximum(scale, 1e-9)[:, None])
                return [np.full(count, v, F) for v in (*m[3, :3], *q, *scale)][:outputs]
            return component
        if outputs == 1:
            # a filtered bone's or socket's index: bones first, then sockets
            first = len(self.bones) if name == "GetFilteredSocket" else 0
            return lambda count, args: [(first + np.broadcast_to(vm.it(args[-1]), (count,))).astype(I)]
        if outputs in (10, 13):
            interpolated = name.endswith("Interpolated")
            world = self.reader or "WS" in name
            takes_flag = name == "GetFilteredSocketTransform" and not self.reader      # its last input: whether in the world
            sockets_only = "Socket" in name and not self.reader

            def transform(count, args):
                now, before = self.tables()
                at = -2 if interpolated or takes_flag else -1
                index = np.broadcast_to(vm.it(args[at]), (count,)) + (len(self.bones) if sockets_only else 0)
                index = np.clip(index, 0, len(now[0]) - 1)
                pick = 2 if world or (takes_flag and np.any(vm.it(args[-1]) != 0)) else 0
                p1, q1, p0, q0 = now[pick][index], now[pick + 1][index], before[pick][index], before[pick + 1][index]
                t = np.broadcast_to(vm.fl(args[-1]), (count,))[:, None].astype(np.float64) if interpolated else 1.0
                position = p0 + (p1 - p0) * t
                q0 = np.where(np.sum(q0 * q1, axis=1, keepdims=True) < 0, -q0, q0)
                rotation = q0 + (q1 - q0) * t
                rotation = rotation / np.maximum(np.linalg.norm(rotation, axis=1, keepdims=True), 1e-9)
                velocity = (p1 - p0) / max(self.system.dt, 1e-6)
                columns = [position[:, 0], position[:, 1], position[:, 2], rotation[:, 0], rotation[:, 1], rotation[:, 2], rotation[:, 3],
                           np.ones(count), np.ones(count), np.ones(count), velocity[:, 0], velocity[:, 1], velocity[:, 2]]
                return [c.astype(F) for c in columns[:outputs]]
            return transform
        raise Unsupported("SkeletalMesh.%s" % name)


class Camera:
    """The camera, where a script asks: the scene's as the replay was told (System.camera), still."""

    def __init__(self, system, kind, props):
        self.system = system

    def function(self, name, specifiers, inputs, outputs):
        if name == "GetFieldOfView" and outputs == 1:
            return lambda count, args: [np.full(count, self.system.fov, F)]
        if not name.startswith("GetCameraProperties"):
            raise Unsupported("Camera.%s" % name)

        def properties(count, args):
            # position, forward, up, right, then what is left (a view offset): zeros
            flat = [v for vector in self.system.camera for v in vector]
            return ([np.full(count, v, F) for v in flat] + [np.zeros(count, F)] * outputs)[:outputs]
        return properties


class VectorField:
    """A vector field (a grid of vectors over a box), sampled as the engine's CPU scripts do:
    between the eight cells around a position, the grid repeating along the axes it tiles on."""

    def __init__(self, system, kind, props):
        path = str((props.get("Field") or {}).get("ObjectPath") or "").rpartition(".")[0]
        field = system.fields.get(path)
        if field is None:
            raise Unsupported("VectorField.SampleField (%s isn't with the export)" % (path.rsplit("/", 1)[-1] or "its field"))
        self.size = np.array(field["Size"], np.int64)
        cells = np.frombuffer(base64.b64decode(field["Data"]), np.float16)
        self.cells = cells.reshape(self.size[2], self.size[1], self.size[0], 4)[..., :3].astype(F)
        self.low = np.array(field["Min"], F)
        self.span = np.array(field["Max"], F) - self.low
        self.tiles = np.array([bool(props.get(k, False)) for k in ("bTileX", "bTileY", "bTileZ")])

    def function(self, name, specifiers, inputs, outputs):
        if name != "SampleField":
            raise Unsupported("VectorField.%s" % name)
        size = self.size

        def sample(count, args):
            position = np.stack([np.broadcast_to(vm.fl(a), (count,)) for a in args[-3:]], axis=1)
            with np.errstate(all="ignore"):
                p = np.nan_to_num((position - self.low) / self.span * size.astype(F) - F(0.5))
            first = np.floor(p)
            t = (p - first).astype(F)
            corners = []
            for index in (first.astype(np.int64), first.astype(np.int64) + 1):
                index = np.where(self.tiles, index % size, index)
                corners.append(np.clip(index, 0, size - 1))
            value = np.zeros((count, 3), F)
            for dz in (0, 1):
                for dy in (0, 1):
                    for dx in (0, 1):
                        weight = (t[:, 0] if dx else 1 - t[:, 0]) * (t[:, 1] if dy else 1 - t[:, 1]) * (t[:, 2] if dz else 1 - t[:, 2])
                        value += self.cells[corners[dz][:, 2], corners[dy][:, 1], corners[dx][:, 0]] * weight[:, None]
            return [value[:, i] for i in range(3)]
        return sample


class PlatformSet:
    """A set of platforms and quality levels: whether it holds here (the highest quality)."""

    def __init__(self, system, kind, props):
        self.active = bool(int((props.get("Platforms") or {}).get("QualityLevelMask", -1)) >> QUALITY & 1)

    def function(self, name, specifiers, inputs, outputs):
        return lambda count, args: [np.full(count, -1 if self.active else 0, I)] * outputs


class Distribution:
    """A weighted distribution array (Value, Weight entries): its alias table, as the asset built it
    (per entry: probability, alias, value, weight), which a script draws from with two random numbers."""

    def __init__(self, system, kind, props):
        raw = bytes(props.get("BuiltTableData") or [])
        n = len(raw) // 16
        self.probability = np.frombuffer(raw[:n * 16], F)[0::4].copy() if n else np.zeros(0, F)
        self.alias = np.frombuffer(raw[:n * 16], I)[1::4].copy() if n else np.zeros(0, I)

    def function(self, name, specifiers, inputs, outputs):
        if name in ("Length", "Num"):
            return lambda count, args: [np.full(count, len(self.probability), I)]
        if name == "GetProbabilityAlias" and outputs == 2:
            def entry(count, args):
                if not len(self.probability):
                    return [np.zeros(count, F), np.zeros(count, I)]
                index = np.clip(np.broadcast_to(vm.it(args[-1]), (count,)), 0, len(self.probability) - 1)
                return [self.probability[index], self.alias[index]]
            return entry
        raise Unsupported("ArrayDistribution.%s" % name)


class Nothing:
    """A data interface with nothing to answer here (sound, a world to collide with, a data channel
    written for the game to read): its functions give zeros."""

    def __init__(self, system, kind, props):
        pass

    def function(self, name, specifiers, inputs, outputs):
        return _zeros(outputs)


class NoMesh:
    """A static mesh to sample (its surface, its vertices, its sockets). None comes with the effect:
    the engine samples the mesh of the component the effect is on. Its functions answer as the
    engine's do with no mesh: counts 0, positions 0 - the particles start at the effect's origin -
    a socket's rotation none and its scale 1, a colour white."""

    def __init__(self, system, kind, props):
        self.system = system

    def function(self, name, specifiers, inputs, outputs):
        self.system.meshless = True

        def answer(count, args):
            out = [np.zeros(count, F) for _ in range(outputs)]
            if "Transform" in name and outputs >= 10:     # position, rotation (x y z w), scale[, velocity]
                out[6][:] = 1.0
                out[7][:] = out[8][:] = out[9][:] = 1.0
            elif "Color" in name and outputs == 4:
                for o in out:
                    o[:] = 1.0
            return out
        return answer


INTERFACES = {kind: Curve for kind in Curve.OUTPUTS}
INTERFACES["NiagaraDataInterfaceStaticMesh"] = NoMesh
INTERFACES["NiagaraDataInterfaceParticleRead"] = ParticleRead
INTERFACES["NiagaraDataInterfaceSpriteRendererInfo"] = RendererInfo
INTERFACES["NiagaraDataInterfaceMeshRendererInfo"] = RendererInfo
INTERFACES["NiagaraDataInterfaceCamera"] = Camera
INTERFACES["NiagaraDataInterfacePlatformSet"] = PlatformSet
INTERFACES["NiagaraDataInterfaceVectorField"] = VectorField
for _kind in ("SkeletalMesh", "SocketReader"):
    INTERFACES["NiagaraDataInterface" + _kind] = Skeleton
for _kind in ("Float", "Float2", "Float3", "Float4", "Position", "Color", "Quat", "Int32", "Bool"):
    INTERFACES["NiagaraDataInterfaceArray" + _kind] = Array
for _kind in ("AudioPlayer", "AudioOscilloscope", "AudioSpectrum", "Export", "DebugDraw", "CollisionQuery", "SimpleCounter", "DataChannelWrite"):
    INTERFACES["NiagaraDataInterface" + _kind] = Nothing
INTERFACES["NiagaraDataInterfaceArrayDistributionInt"] = Distribution


def _matrix_to_quaternion(count, args):
    """The engine's FQuat(FMatrix): a rotation matrix (16 floats, rows first) as a quaternion."""
    m = np.stack([np.broadcast_to(vm.fl(a), (count,)) for a in args[-16:]], axis=1).astype(np.float64).reshape(count, 4, 4)
    q = np.zeros((count, 4))
    trace = m[:, 0, 0] + m[:, 1, 1] + m[:, 2, 2]
    with np.errstate(all="ignore"):
        positive = trace > 0
        s = np.sqrt(np.where(positive, trace + 1.0, 1.0))
        q[positive, 3] = 0.5 * s[positive]
        k = 0.5 / s
        for axis, (a, b) in enumerate(((1, 2), (2, 0), (0, 1))):
            q[positive, axis] = ((m[:, a, b] - m[:, b, a]) * k)[positive]
        for i in range(3):
            j, k2 = (i + 1) % 3, (i + 2) % 3
            biggest = (m[:, i, i] >= m[:, j, j]) & (m[:, i, i] >= m[:, k2, k2]) if i == 0 else \
                      (m[:, 1, 1] > m[:, 0, 0]) & (m[:, 1, 1] >= m[:, 2, 2]) if i == 1 else \
                      (m[:, 2, 2] > m[:, 0, 0]) & (m[:, 2, 2] > m[:, 1, 1])
            mine = ~positive & biggest
            if not mine.any():
                continue
            s2 = np.sqrt(np.maximum(m[:, i, i] - m[:, j, j] - m[:, k2, k2] + 1.0, 1e-20))
            q[mine, i] = (0.5 * s2)[mine]
            f = 0.5 / s2
            q[mine, 3] = ((m[:, j, k2] - m[:, k2, j]) * f)[mine]
            q[mine, j] = ((m[:, i, j] + m[:, j, i]) * f)[mine]
            q[mine, k2] = ((m[:, i, k2] + m[:, k2, i]) * f)[mine]
    return [q[:, i].astype(F) for i in range(4)]


# functions a script calls that belong to no data interface (the engine's fast path library)
LIBRARY = {"FastMatrixToQuaternion": _matrix_to_quaternion}


class Script:
    """A compiled script with its parameters and the functions it calls."""

    def __init__(self, system, props, cooked, emitter=None, lenient=False):
        """lenient (the system's own scripts, which hold every emitter's emitter-level modules):
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
    """An emitter's answer to another's events: particles spawned per event, and a script run on those or on all."""

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
        """The source's events as they stand: (buffer, how many to answer)."""
        buffer = self.source.events.get(self.event) if self.source is not None else None
        if buffer is None:
            return None, 0
        return buffer, min(buffer.count, self.limit) if self.limit > 0 else buffer.count


class Emitter:
    """A CPU emitter of a system: its particles, and the scripts that make and move them."""

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
        # the events its update script sends (name: buffer, and the most per tick); its spawn script's go nowhere
        self.events, self.sends = {}, []
        for g in version["UpdateScriptProps"].get("EventGenerators") or []:
            buffer = Layout(g["DataSetCompiledData"]["Variables"]).buffer()
            self.events[str(g["ID"])] = buffer
            self.sends.append((buffer, int(g.get("MaxEventsPerFrame", 0))))
        self.scratch = [Layout(g["DataSetCompiledData"]["Variables"]).buffer() for g in version["SpawnScriptProps"].get("EventGenerators") or []]
        self.handlers = [Handler(system, self, handle, h) for h in version.get("EventHandlerScriptProps") or []]

    def fill(self):
        """The emitter's constant block, as it stands before a tick."""
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
                # runs on (the spawned ones, or every particle)
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
        # an event's particles are spawned mid-tick, then the handler's script runs on them with the event to read
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
    asset's own. sockets: the sockets a trail's filtered sockets stand for. strict: an emitter's script failing raises (tests) instead of leaving the emitter out."""

    def __init__(self, exports, seed=1, user=None, strict=False, fields=None, sockets=None):
        self.exports, self.strict = exports, strict
        # the sockets the game points a trail at (a pickaxe's first and second): what its filtered sockets,
        # in their order, stand for where the character has none of their own names
        self.sockets = [str(s).lower() for s in sockets or []]
        self.fields = fields or {}  # the vector fields its scripts sample, by package
        self.interfaces = {}        # export index: its data interface (one for all the scripts that call it)
        # the character the effect sits on: the bones and sockets its scripts read (lower case), and where each
        # stands now and a tick ago (name: (position, quaternion) in the character's space; place() tells)
        self.reads, self.pose, self.pose_before = set(), {}, {}
        self.component = self.component_before = np.eye(4)
        self.placed, self.dt = None, 1.0 / 60.0
        # where a script that asks finds the camera: position, forward, up, right (UE's axes, cm)
        self.camera = ((-500.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, 1.0, 0.0))
        self.fov = 80.0     # its field of view, degrees (the game's default)
        self.asset = next(e for e in exports if e["type"] == "NiagaraSystem")
        # the user-defined structs its data sets hold (the app lays each out: floats, ints)
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
        # time of day's tint a smoke takes): each one's own values, as the app sends them. A script's
        # cooked store holds placeholders for these (every time of day at once), not the values.
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
        self.asked = ACTIVE     # what the game asks of it (Engine.Owner.ExecutionState): deactivate() asks it to stop
        self.globals, self.block, self.owner = bytearray(GLOBAL_SIZE), bytearray(SYSTEM_SIZE), bytearray(OWNER_SIZE)
        for at in range(0, 384, 64):
            self.owner[at:at + 64] = IDENTITY
        struct.pack_into("<4f", self.owner, 384, 0, 0, 0, 1)                         # rotation
        struct.pack_into("<4f4f4f4f", self.owner, 432, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 1, 1, 1, 0)    # axes, scale
        self.previous = None
        self.unanswered = set()     # what its own scripts ask that answers zeros here ("Grid2DCollection.SetNumCells")
        self.meshless = False       # whether a script samples a static mesh (none here: NoMesh)
        self.spawn = self.script(props["SystemSpawnScript"], None)
        self.update = self.script(props["SystemUpdateScript"], None)
        self.spawn_inputs = Layout(compiled["SpawnInstanceParamsDataSetCompiledData"]["Variables"])
        self.update_inputs = Layout(compiled["UpdateInstanceParamsDataSetCompiledData"]["Variables"])
        self.handles = props.get("EmitterHandles") or []
        self.emitters, self.skipped = [], []        # skipped: (emitter name, why)
        self.approximate = []                       # the stateless emitters: played from their settings, the engine's random draws apart
        self.gpu = []                               # the GPU emitters among them: their motion a stand-in (niagara_gpu)
        for index, handle in enumerate(self.handles):
            if not handle.get("bIsEnabled", True):
                continue
            if "Stateless" in str(handle.get("EmitterMode")):
                # no script: its modules' settings, worked out (niagara_stateless)
                export = self.export(handle.get("StatelessEmitter"))
                if export is None:      # (one the cook left out: not for this platform)
                    continue
                try:
                    from . import niagara_stateless
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
                # no script to run (a compiled shader): what the asset keeps of it, the motion a stand-in (niagara_gpu)
                try:
                    from . import niagara_gpu
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
        """The bones and sockets its scripts read that the character's pose doesn't have."""
        names = set()
        for interface in self.interfaces.values():
            if isinstance(interface, Skeleton):
                names.update(interface.names[i] for i in range(len(interface.names))
                             if interface.held(self.pose, i) is None and interface.names[i].lower() != "none")
        return sorted(names)

    def _read(self):
        """The parameter collections' values its scripts read, by name."""
        scripts = [self.spawn, self.update] + [s for e in self.emitters for s in
                                               (getattr(e, "spawn", None), getattr(e, "update", None), *[h.script for h in getattr(e, "handlers", ())]) if s is not None]
        return {name for s in scripts for name in s.store.offsets if name in self.shared.offsets}

    def users(self):
        """What the game sets of it that a number or a few say: its user parameters, and the
        parameter collections' values its scripts read: [(name, type, value)]."""
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
        """A user parameter's (or a parameter collection's) value over the asset's own (a number, a bool, or floats)."""
        name = name if name.startswith(("User.", "NPC.")) else "User." + name
        # (UE's names don't mind case)
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
        """Where the effect stands for the next tick: its owner's transform and its character's (4x4,
        UE's: rows the axes then the origin, cm), and the character's pose ({bone or socket name,
        lower case: (position, quaternion)} in the character's space). What was told last becomes a tick ago's."""
        self.component_before, self.pose_before = self.component, self.pose
        if component is not None:
            self.component = np.asarray(component, np.float64).reshape(4, 4)
        if pose is not None:
            self.pose = pose
        if owner is not None:
            self.placed = np.asarray(owner, np.float64).reshape(4, 4)

    def deactivate(self):
        """The game stops the effect (a timed notify ends): its emitters spawn no more, its particles play out."""
        self.asked = INACTIVE

    def _owner(self, dt):
        """The owner's constant block, from where it was placed (its velocity: from where it was)."""
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
        """The export a reference points at, when it is in this package."""
        if not ref or not ref.get("ObjectPath"):
            return None
        path, _, index = ref["ObjectPath"].rpartition(".")
        if not index.isdigit() or int(index) >= len(self.exports) or path != self.asset["outer"]:
            return None
        return self.exports[int(index)]

    def interface(self, ref):
        """The data interface a reference points at, or None where there is no running it."""
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
        """A system data set variable's values (its floats, else its ints), or None."""
        v = self.layout.vars.get(name)
        if v is None:
            return None
        _, f0, i0, nf, ni = v
        return self.data.floats[f0:f0 + nf, 0] if nf else self.data.ints[i0:i0 + ni, 0]

    def bind(self, store):
        """A script's parameters that name a system data set variable or a user parameter take its value."""
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
        """The engine's values a system script reads per instance, as the asset binds them."""
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
