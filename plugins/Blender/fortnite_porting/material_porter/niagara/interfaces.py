"""Data interfaces: curves, arrays, skeletons, cameras and the other inputs a script calls."""

import base64
import numpy as np

from .. import niagara_vm as vm
from ..niagara_vm import F, I, Unsupported

from .datasets import QUALITY, _rich, _specifiers, _zeros


class Curve:
    """A curve data interface, sampled like the engine's CPU scripts do: from its baked table
    (built from the curve's keys when the asset keeps none)."""

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
    """An array data interface: the asset's values, which a script may add to or change while it plays."""

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
        # an empty array (a script fills it while playing) is as wide as its type says, whatever asks first
        width = next((w for suffix, w in (("Float2", 2), ("Float3", 3), ("Position", 3), ("Float4", 4), ("Color", 4), ("Quat", 4),
                                          ("NiagaraID", 2), ("Matrix", 16)) if kind.endswith(suffix)), 1)
        self.rows = np.array(rows, I if self.ints else F).reshape(len(rows), -1) if rows else np.zeros((0, width), I if self.ints else F)
        # what reading an empty array gives (the engine's GetDefaultValue): white for a colour (a
        # variant colour array the game fills; Cerberus's flames are white x their material colours,
        # not black), identity for a quaternion or matrix, else zeros
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
    """Another emitter's particles, read by a script (as the emitter stands when the script runs)."""

    def __init__(self, system, kind, props):
        # the emitter it names (older assets: on the interface itself); if none, the script's own
        self.system, self.source = system, props.get("EmitterName") or (props.get("EmitterBinding") or {}).get("EmitterName")
        self.caller = None      # emitter whose script is being bound (Script sets it)

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
            # a mesh's bounds with the renderer's scale applied (the app computes them): min, max, size
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
    """A skeletal mesh's bones and sockets (or an actor's sockets), as the replay was told they stand
    (System.place: the character's pose). Without a character each sits at the character's origin,
    unrotated."""

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
        """A bone's or socket's (position, rotation) in a pose. A socket not named there uses the one the game points it at."""
        name = self.names[index].lower()
        at = index - len(self.bones)
        found = pose.get(name)
        if found is None and 0 <= at < len(self.system.sockets):
            found = pose.get(self.system.sockets[at])
        return found

    def tables(self):
        """The bones' positions and rotations now and a tick ago, in character space and in world space."""
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
            # index of a filtered bone or socket: bones first, then sockets
            first = len(self.bones) if name == "GetFilteredSocket" else 0
            return lambda count, args: [(first + np.broadcast_to(vm.it(args[-1]), (count,))).astype(I)]
        if outputs in (10, 13):
            interpolated = name.endswith("Interpolated")
            world = self.reader or "WS" in name
            takes_flag = name == "GetFilteredSocketTransform" and not self.reader      # last input: whether in world space
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
    """The camera a script asks for: the scene's, as the replay was told (System.camera), held still."""

    def __init__(self, system, kind, props):
        self.system = system

    def function(self, name, specifiers, inputs, outputs):
        if name == "GetFieldOfView" and outputs == 1:
            return lambda count, args: [np.full(count, self.system.fov, F)]
        if not name.startswith("GetCameraProperties"):
            raise Unsupported("Camera.%s" % name)

        def properties(count, args):
            # position, forward, up, right, then the rest (a view offset): zeros
            flat = [v for vector in self.system.camera for v in vector]
            return ([np.full(count, v, F) for v in flat] + [np.zeros(count, F)] * outputs)[:outputs]
        return properties


class VectorField:
    """A vector field (a grid of vectors over a box), sampled like the engine's CPU scripts do:
    between the eight cells around a position, repeating along the axes it tiles on."""

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
    """A set of platforms and quality levels: whether it holds here (at the highest quality)."""

    def __init__(self, system, kind, props):
        self.active = bool(int((props.get("Platforms") or {}).get("QualityLevelMask", -1)) >> QUALITY & 1)

    def function(self, name, specifiers, inputs, outputs):
        return lambda count, args: [np.full(count, -1 if self.active else 0, I)] * outputs


class Distribution:
    """A weighted distribution array (Value, Weight entries). Uses the alias table the asset built
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
    """A data interface with nothing to answer here (sound, world collision, a data channel written
    for the game to read): its functions give zeros."""

    def __init__(self, system, kind, props):
        pass

    def function(self, name, specifiers, inputs, outputs):
        return _zeros(outputs)


class NoMesh:
    """A static mesh to sample (surface, vertices, sockets). None comes with the effect: the engine
    samples the mesh of the component the effect is on. The functions answer like the engine's with
    no mesh: counts 0, positions 0 (particles start at the effect's origin), socket rotation none
    and scale 1, colour white."""

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
    """The engine's FQuat(FMatrix): a rotation matrix (16 floats, rows first) to a quaternion."""
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
