"""A 3L head's RigLogic data: the DNA's names and RigLogic's runtime dump that UE caches in the cooked DNA asset
(spec 2026-10-10-riglogic-face). Fortnite's dump comes from a RigLogic older than OpenRigLogic v13.5 (no header, a
22-byte configuration, joint groups without a block height); all values big-endian, counts u32.

read(path) returns the Rig for LOD 0:
    gui, raw, joints: names; parents: joint hierarchy; raw_count;
    psds: [{output, inputs, weight}] (correctives: product of clamped inputs, capped at 1);
    channels: {(joint, attr9): [(input, coef)]}, attr9 = tx ty tz rx ry rz sx sy sz (cm, degrees, the dump's frame),
    inputs = raw controls then correctives; gui_to_raw: the dump's own GUI-to-raw table;
    rotation_sequence, rotation_signs, units."""
import struct


class RigLogicError(Exception):
    """The data isn't the RigLogic layout this reads (another version, a cut file)."""


EVAL_SLOTS = ("mlBehavior", "rbfBehavior", "bpcmJoints", "quaternionJoints", "twistSwingJoints",
              "mlJoints", "blendShapes", "animatedMaps", "psdNet")
EVAL_TYPES = {0: "Auto", 1: "Null", 2: "Concrete"}
ROT_TYPES = {3: "EulerAngles", 4: "Quaternions"}
ROT_SEQ = ("xyz", "xzy", "yxz", "yzx", "zxy", "zyx")


class Reader:
    def __init__(self, data, pos=0):
        self.d = data
        self.p = pos

    def _s(self, fmt):
        v = struct.unpack_from(fmt, self.d, self.p)
        self.p += struct.calcsize(fmt)
        return v

    def u8(self):
        return self._s(">B")[0]

    def u16(self):
        return self._s(">H")[0]

    def u32(self):
        return self._s(">I")[0]

    def i32(self):
        return self._s(">i")[0]

    def u64(self):
        return self._s(">Q")[0]

    def f32(self):
        return self._s(">f")[0]

    def arr(self, code, n):
        return list(self._s(">%d%s" % (n, code))) if n else []

    def vu16(self):
        return self.arr("H", self.u32())

    def vf32(self):
        return self.arr("f", self.u32())

    def mu16(self):
        return [self.vu16() for _ in range(self.u32())]

    def vec(self, fn):
        return [fn() for _ in range(self.u32())]

    def string(self):
        n = self.u32()
        if self.p + n > len(self.d):
            raise RigLogicError("string past the end")
        s = self.d[self.p:self.p + n].decode("utf-8", "replace")
        self.p += n
        return s


def _find_dna(data):
    """The DNA stream: its signature and version 2, with the descriptor first in its index."""
    i = data.find(b"DNA\x00\x02")
    while i >= 0:
        n = struct.unpack_from(">I", data, i + 7)[0] if i + 11 <= len(data) else 0
        if 0 < n < 64 and data[i + 11:i + 15] == b"desc":
            return i
        i = data.find(b"DNA\x00\x02", i + 1)
    raise RigLogicError("no DNA stream")


def _read_dna(data, base):
    r = Reader(data, base + 3)
    r.u16()
    r.u16()
    index = {}
    for _ in range(r.u32()):
        lid = data[r.p:r.p + 4].decode("ascii", "replace")
        r.p += 4
        r.u32()
        index[lid] = (r.u32(), r.u32())
    if "desc" not in index or "defn" not in index:
        raise RigLogicError("DNA without its descriptor or definition")
    off, size = index["desc"]
    r.p = base + off
    r.string()
    r.u16(), r.u16(), r.u16()
    r.vec(lambda: (r.string(), r.string()))
    units = (("cm", "m")[r.u16()], ("degrees", "radians")[r.u16()])
    if r.p + 10 > base + off + size:
        raise RigLogicError("DNA descriptor size")
    off, size = index["defn"]
    r.p = base + off
    for _ in range(4):                      # LOD mappings
        r.vu16()
        r.mu16()
    names = [r.vec(r.string) for _ in range(6)]
    r.vu16()
    r.vu16()
    parents = r.vu16()
    for _ in range(6):                      # neutral translations, rotations
        r.vf32()
    if r.p != base + off + size:
        raise RigLogicError("DNA definition size")
    return {"end": base + max(o + s for o, s in index.values()), "units": units, "gui": names[0], "raw": names[1],
            "joints": names[2], "parents": parents}


def _cond_table(r):
    """A conditional table (OpenRigLogic conditionaltable/ConditionalTable.h) without the trailing row count."""
    t = {"rangeMaps": r.vec(lambda: r.vec(lambda: (r.f32(), r.f32(), r.vu16())))}
    for k in ("intervalsRemaining", "inputIndices", "outputIndices"):
        t[k] = r.vu16()
    for k in ("fromValues", "toValues", "slopeValues", "cutValues"):
        t[k] = r.vf32()
    t["inputCount"], t["outputCount"] = r.u16(), r.u16()
    return t


def _padded_block_view(view_rows, padded_rows, block_h, pad_to):
    """OpenRigLogic types/PaddedBlockView.h: (rows, to the last full block, to the second last)."""
    ends_with_pad = (padded_rows % block_h) == pad_to
    in_last_block = ends_with_pad and (padded_rows - view_rows) < pad_to
    target = pad_to if in_last_block else block_h
    pv = -(-view_rows // target) * target
    mask_off = pv != view_rows and not in_last_block
    last = pv - (pv % block_h)
    return view_rows, last, last - block_h if mask_off else last


def _block_heights(groups, regions, lods):
    """The joint groups carry no block height here: the heights whose padding reproduces every stored LOD region."""
    found = []
    for bh in (8, 16, 32, 4):
        good = True
        for g in groups:
            if not g["rowCount"]:
                continue
            if g["rowCount"] % (bh // 2):
                good = False
                break
            for lod in range(lods):
                rows = regions[g["lodsOffset"] + lod]["rows"]
                if rows[0] and _padded_block_view(rows[0], g["rowCount"], bh, bh // 2) != tuple(rows):
                    good = False
                    break
            if not good:
                break
        if good:
            found.append(bh)
    return found


def _group_matrix(values, g, bh):
    """A joint group's rows x columns from its blocks (blocks of bh rows, a last one of bh/2, column-major inside)."""
    rows, cols = g["rowCount"], g["colCount"]
    m = [[0.0] * cols for _ in range(rows)]
    off, r0 = g["valuesOffset"], 0
    while r0 < rows:
        h = bh if rows - r0 >= bh else bh // 2
        for c in range(cols):
            for k in range(h):
                if r0 + k < rows:
                    m[r0 + k][c] = values[off + c * h + k]
        off += cols * h
        r0 += h
    return m


def _null(meta, slot):
    if meta["evaluators"].get(slot) not in (None, "Null"):
        raise RigLogicError("%s data present: not read" % slot)


def _read_dump(data, start):
    r = Reader(data, start)
    cfg = {"calculationType": r.u8()}
    for k in ("loadJoints", "loadBlendShapes", "loadAnimatedMaps", "loadML", "loadRBF", "loadTwistSwing"):
        cfg[k] = bool(r.u8())
    cfg["translationType"], cfg["rotationType"], cfg["scaleType"] = r.u8(), ROT_TYPES.get(r.u8()), r.u8()
    r.f32(), r.f32(), r.f32()
    meta = {"coordinateSystem": (r.u32(), r.u32(), r.u32()), "rotationSequence": ROT_SEQ[r.u32()],
            "rotationSigns": [r.i32() for _ in range(3)]}
    for k in ("lodCount", "guiControlCount", "rawControlCount", "psdControlCount", "mlControlCount",
              "rbfControlCount", "jointGroupCount", "jointAttributeCount", "blendShapeCount",
              "animatedMapCount", "mlTypeCount", "rbfSolverCount", "twistCount", "swingCount"):
        meta[k] = r.u16()
    meta["evaluators"] = {EVAL_SLOTS[i] if i < len(EVAL_SLOTS) else str(i): EVAL_TYPES.get(v, v)
                          for i, v in enumerate(r.vu16())}
    controls = {"registered": r.mu16(), "guiToRaw": _cond_table(r), "initial": r.vec(lambda: (r.u32(), r.f32()))}
    _null(meta, "mlBehavior")
    r.vu16()                                # ML mesh region counts
    _null(meta, "rbfBehavior")
    psd = None
    if meta["evaluators"].get("psdNet") == "Concrete":
        psd = {"inputLODs": r.mu16(), "outputLODs": r.mu16(), "inputs": r.vu16(),
               "psds": r.vec(lambda: {"offset": r.u64(), "size": r.u64(), "weight": r.f32()})}
        psd["min"], psd["max"] = r.u16(), r.u16()
    if meta["evaluators"].get("bpcmJoints") != "Concrete":
        raise RigLogicError("no joint matrix")
    js = {"values": r.vf32(), "inputIndices": r.vu16(), "outputIndices": r.vu16(),
          "lodRegions": r.vec(lambda: {"cols": [r.u32(), r.u32(), r.u32()], "rows": [r.u32(), r.u32(), r.u32()]}),
          "outputRotationIndices": r.vu16(), "outputRotationLODs": r.vu16()}
    fields = ("valuesOffset", "inputIndicesOffset", "outputIndicesOffset", "lodsOffset", "outputRotationIndicesOffset",
              "outputRotationLODsOffset", "valuesSize", "colCount", "rowCount")
    js["groups"] = r.vec(lambda: dict(zip(fields, (r.u32() for _ in fields))))
    for slot in ("quaternionJoints", "twistSwingJoints", "mlJoints"):
        _null(meta, slot)
    r.vf32()                                # neutral values
    r.mu16()                                # variable attribute indices
    r.mu16()                                # joint indices
    r.u16()                                 # joint group count
    if meta["evaluators"].get("blendShapes") == "Concrete":
        r.vu16(), r.vu16(), r.vu16()
    if meta["evaluators"].get("animatedMaps") == "Concrete":
        r.vu16()
        _cond_table(r)
    return {"end": r.p, "configuration": cfg, "metadata": meta, "controls": controls, "psd": psd, "joints": js}


def _joint_attr(output, per_joint):
    """An output index's (joint, attr9); None for a quaternion's w (rows only fill qx qy qz, as Euler degrees)."""
    j, a = divmod(output, per_joint)
    if per_joint == 10:
        if a == 6:
            return j, None
        if a > 6:
            a -= 1
    return j, a


def read(path):
    """The head's RigLogic data for LOD 0 (see the module docstring)."""
    with open(path, "rb") as f:
        data = f.read()
    try:
        rig = _rig(data)
    except (struct.error, IndexError, KeyError, ValueError, UnicodeDecodeError, MemoryError) as e:
        raise RigLogicError("%s: %s" % (type(e).__name__, e)) from e
    _check(rig)
    return rig


def _check(rig):
    """What the builder assumes (the probed heads): anything else would move the face wrong, so it fails loudly."""
    if rig["rotation_sequence"] != "xyz" or rig["rotation_signs"] != [1, 1, 1] or rig["units"] != ("cm", "degrees")             or rig["frame"] != (0, 3, 4):
        raise RigLogicError("unexpected rotation order, signs, units or frame")
    count = rig["raw_count"] + len(rig["psds"])
    if any(p["output"] != rig["raw_count"] + k or any(i >= count for i in p["inputs"]) for k, p in enumerate(rig["psds"])):
        raise RigLogicError("correctives not numbered after the raw controls")
    if any(j >= len(rig["joints"]) or any(i >= count for i, _ in terms) for (j, _), terms in rig["channels"].items()):
        raise RigLogicError("a joint row past the names")


def _rig(data):
    dna = _read_dna(data, _find_dna(data))
    dump = _read_dump(data, dna["end"])
    if dump["end"] != len(data):
        raise RigLogicError("%d bytes left after the dump" % (len(data) - dump["end"]))
    meta, cfg, js, psd = dump["metadata"], dump["configuration"], dump["joints"], dump["psd"]
    per_joint = cfg["translationType"] + (4 if cfg["rotationType"] == "Quaternions" else 3) + cfg["scaleType"]
    psds = [{"output": psd["min"] + i, "inputs": psd["inputs"][p["offset"]:p["offset"] + p["size"]], "weight": p["weight"]}
            for i, p in enumerate(psd["psds"])] if psd else []
    heights = _block_heights(js["groups"], js["lodRegions"], meta["lodCount"])
    if not heights:
        raise RigLogicError("no block height fits the joint groups")
    channels = {}
    for g in js["groups"]:
        if not g["rowCount"]:
            continue
        m = _group_matrix(js["values"], g, heights[0])
        region = js["lodRegions"][g["lodsOffset"]]
        ins = js["inputIndices"][g["inputIndicesOffset"]:g["inputIndicesOffset"] + g["colCount"]]
        outs = js["outputIndices"][g["outputIndicesOffset"]:g["outputIndicesOffset"] + g["rowCount"]]
        for row in range(region["rows"][0]):
            joint, attr = _joint_attr(outs[row], per_joint)
            if attr is not None:
                channels.setdefault((joint, attr), []).extend(
                    (ins[c], m[row][c]) for c in range(region["cols"][0]) if m[row][c] != 0.0)
    return {"gui": dna["gui"], "raw": dna["raw"], "joints": dna["joints"], "parents": dna["parents"],
            "raw_count": meta["rawControlCount"], "psds": psds, "channels": channels,
            "gui_to_raw": dump["controls"]["guiToRaw"], "rotation_sequence": meta["rotationSequence"],
            "rotation_signs": meta["rotationSigns"], "units": dna["units"], "frame": meta["coordinateSystem"]}


def inputs(rig, raw):
    """RigLogic's input vector: the raw controls (by name), then each corrective (product of clamped inputs, capped)."""
    index = {n: i for i, n in enumerate(rig["raw"])}
    buf = [0.0] * (rig["raw_count"] + len(rig["psds"]))
    for name, value in raw.items():
        buf[index[name]] = value
    for p in rig["psds"]:
        value = p["weight"]
        for i in p["inputs"]:
            value *= min(1.0, max(0.0, buf[i]))
        buf[p["output"]] = min(1.0, value)
    return buf


def evaluate(rig, raw):
    """Joint deltas (tx ty tz rx ry rz sx sy sz; cm, degrees; the dump's frame) for raw control values."""
    buf = inputs(rig, raw)
    out = {}
    for (joint, attr), terms in rig["channels"].items():
        out.setdefault(rig["joints"][joint], [0.0] * 9)[attr] += sum(buf[i] * c for i, c in terms)
    return out


def raw_from_gui(rig, gui):
    """Raw control values from GUI control values, through the dump's own GUI-to-raw table."""
    t = rig["gui_to_raw"]
    index = {n: i for i, n in enumerate(rig["gui"])}
    values = [0.0] * len(rig["gui"])
    for name, value in gui.items():
        values[index[name]] = value
    out, row = [0.0] * t["outputCount"], 0
    while row < len(t["outputIndices"]):
        v = values[t["inputIndices"][row]]
        if t["fromValues"][row] <= v <= t["toValues"][row]:
            out[t["outputIndices"][row]] += t["slopeValues"][row] * v + t["cutValues"][row]
            row += t["intervalsRemaining"][row]
        row += 1
    return {n: min(1.0, max(0.0, v)) for n, v in zip(rig["raw"], out)}
