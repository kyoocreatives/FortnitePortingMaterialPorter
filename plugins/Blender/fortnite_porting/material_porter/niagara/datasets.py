"""Data set layouts and the parameter stores scripts read."""

import struct
import numpy as np

from .. import niagara_vm as vm
from ..niagara_vm import F, I, Unsupported


# a type's components in a data set: (floats, int32s)
TYPES = {
    "NiagaraFloat": (1, 0), "NiagaraInt32": (0, 1), "NiagaraBool": (0, 1),
    "Vector2f": (2, 0), "Vector3f": (3, 0), "NiagaraPosition": (3, 0), "Vector4f": (4, 0),
    "LinearColor": (4, 0), "Quat4f": (4, 0), "Matrix44f": (16, 0), "NiagaraMatrix": (16, 0),
    "NiagaraID": (0, 2), "NiagaraSpawnInfo": (2, 2), "NiagaraRandInfo": (0, 3),
}
# half-precision (compressed) attributes: own rows, after the floats
HALVES = {"NiagaraHalf": 1, "NiagaraHalfVector2": 2, "NiagaraHalfVector3": 3, "NiagaraHalfVector4": 4}
ACTIVE, INACTIVE, INACTIVE_CLEAR, COMPLETE, DISABLED = range(5)      # ENiagaraExecutionState
QUALITY = 3     # quality level replayed at (Engine.QualityLevel: 0 low to 4 cinematic)

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
    # UnderlyingType: 1 a class, 2 a struct, 3 an enum (older assets leave it out)
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
        # a half attribute reads as floats; its rows come after the float rows (the VM's half
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


def specifier_dict(listed):
    """A function's specifiers ({Key, Value} pairs) as a dict."""
    found = {}
    for item in listed or []:
        if isinstance(item, dict) and "Key" in item:
            found[str(item["Key"])] = str(item.get("Value"))
    return found


def zero_outputs(outputs):
    return lambda count, inputs: [np.zeros(count, I)] * outputs


def rich(curve, x):
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
