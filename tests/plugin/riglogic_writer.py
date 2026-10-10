"""Writes a small synthetic DNA + RigLogic dump in the layout Fortnite's cooked heads use (see riglogic_read), for tests:
no game data in the repo."""
import struct


class W:
    def __init__(self):
        self.b = bytearray()

    def put(self, fmt, *v):
        self.b += struct.pack(">" + fmt, *v)

    def u8(self, v):
        self.put("B", v)

    def u16(self, v):
        self.put("H", v)

    def u32(self, v):
        self.put("I", v)

    def u64(self, v):
        self.put("Q", v)

    def f32(self, v):
        self.put("f", v)

    def vec(self, code, items):
        self.u32(len(items))
        for x in items:
            self.put(code, x)

    def string(self, s):
        data = s.encode()
        self.u32(len(data))
        self.b += data


def _padded_block_view(view_rows, padded_rows, block_h, pad_to):
    ends_with_pad = (padded_rows % block_h) == pad_to
    in_last_block = ends_with_pad and (padded_rows - view_rows) < pad_to
    target = pad_to if in_last_block else block_h
    pv = -(-view_rows // target) * target
    mask_off = pv != view_rows and not in_last_block
    last = pv - (pv % block_h)
    return view_rows, last, last - block_h if mask_off else last


def _dna(spec):
    desc, defn = W(), W()
    desc.string("synthetic")
    for v in (0, 0, 0):                            # archetype, gender, age
        desc.u16(v)
    desc.u32(0)                                    # metadata pairs
    desc.u16(0)                                    # cm
    desc.u16(0)                                    # degrees
    for axis in (0, 2, 4):                         # left, up, front
        desc.u16(axis)
    desc.u16(1)                                    # LOD count
    desc.u16(0)                                    # max LOD
    desc.string("")
    desc.string("")
    for _ in range(4):                             # LOD mappings: one LOD, empty
        defn.vec("H", [0])
        defn.u32(1)
        defn.vec("H", [])
    for names in (spec["gui"], spec["raw"], spec["joints"], [], [], []):
        defn.u32(len(names))
        for n in names:
            defn.string(n)
    defn.vec("H", [])
    defn.vec("H", [])
    defn.vec("H", spec["parents"])
    for _ in range(6):
        defn.vec("f", [0.0] * len(spec["joints"]))
    head = W()
    head.b += b"DNA"
    head.u16(2)
    head.u16(8)
    head.u32(2)
    start = 3 + 4 + 4 + 2 * 16
    for lid, body, off in ((b"desc", desc, start), (b"defn", defn, start + len(desc.b))):
        head.b += lid
        head.u32(1)
        head.u32(off)
        head.u32(len(body.b))
    return bytes(head.b + desc.b + defn.b)


def _cond(w, rows, inputs, outputs):
    w.u32(0)                                       # rangeMaps
    w.vec("H", [0] * len(rows))
    w.vec("H", [r[0] for r in rows])
    w.vec("H", [r[1] for r in rows])
    for k in (2, 3, 4, 5):
        w.vec("f", [r[k] for r in rows])
    w.u16(inputs)
    w.u16(outputs)


def write(path, spec):
    """spec: gui, raw, joints, parents (name lists); psds [(input indices, weight)]; rows [(joint, attr9, {input: coef})]
    (attr9: tx ty tz rx ry rz sx sy sz; rotations in degrees); gui_to_raw [(gui, raw, from, to, slope, cut)]."""
    raw_n, psd_n, joints = len(spec["raw"]), len(spec["psds"]), len(spec["joints"])
    rows = spec["rows"]
    inputs = sorted({i for _, _, coefs in rows for i in coefs})
    padded = -(-len(rows) // 4) * 4
    w = W()
    w.u8(4)                                        # AnyVector
    for _ in range(6):
        w.u8(1)
    for v in (3, 4, 3):                            # translation vector, quaternions, scale vector
        w.u8(v)
    for _ in range(3):
        w.f32(0.0)
    for v in (0, 3, 4, 0) + tuple(spec.get("signs", (1, 1, 1))):      # left, down, front; xyz; signs
        w.u32(v)
    for v in (1, len(spec["gui"]), raw_n, psd_n, 0, 0, 1, joints * 9, 0, 0, 0, 0, 0, 0):
        w.u16(v)
    w.vec("H", [1, 1, 2, 1, 1, 1, 2, 2, 2])
    w.u32(1)
    w.vec("H", list(range(raw_n)))
    _cond(w, spec["gui_to_raw"], len(spec["gui"]), raw_n)
    w.u32(0)                                       # initial values
    w.vec("H", [])                                 # ML mesh region counts
    flat, psds = [], []
    for ins, weight in spec["psds"]:
        psds.append((len(flat), len(ins), weight))
        flat += ins
    w.u32(1)
    w.vec("H", sorted({i for ins, _ in spec["psds"] for i in ins}))
    w.u32(1)
    w.vec("H", list(range(raw_n, raw_n + psd_n)))
    w.vec("H", flat)
    w.u32(len(psds))
    for off, size, weight in psds:
        w.u64(off)
        w.u64(size)
        w.f32(weight)
    w.u16(spec.get("psd_min", raw_n))
    w.u16(raw_n + max(psd_n, 1) - 1)
    matrix = [[coefs.get(i, 0.0) for i in inputs] for _, _, coefs in rows] + [[0.0] * len(inputs)] * (padded - len(rows))
    values, r0 = [], 0
    while r0 < padded:
        h = 8 if padded - r0 >= 8 else 4
        for c in range(len(inputs)):
            values += [matrix[r0 + k][c] for k in range(h)]
        r0 += h
    w.vec("f", values)
    w.vec("H", inputs)
    w.vec("H", [j * 10 + (a if a < 6 else a + 1) for j, a, _ in rows] + [0] * (padded - len(rows)))
    w.u32(1)
    for v in (len(inputs),) * 3 + _padded_block_view(len(rows), padded, 8, 4):
        w.u32(v)
    w.vec("H", [])
    w.vec("H", [])
    w.u32(1)
    for v in (0, 0, 0, 0, 0, 0, len(values), len(inputs), padded):
        w.u32(v)
    w.vec("f", [0, 0, 0, 0, 0, 0, 1, 1, 1, 1] * joints)
    w.u32(1)
    w.vec("H", list(range(joints * 9)))
    w.u32(1)
    w.vec("H", list(range(joints)))
    w.u16(1)
    w.vec("H", [0])                                # blend shapes: LODs, inputs, outputs
    w.vec("H", [])
    w.vec("H", [])
    w.vec("H", [0])                                # animated maps
    _cond(w, [], 0, 0)
    with open(path, "wb") as f:
        f.write(bytes(32) + struct.pack("<I", 1) + _dna(spec) + bytes(w.b))
