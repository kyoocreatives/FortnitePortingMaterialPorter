# RigLogic Face Bones Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Epic's face board on Fortnite 3L heads moves the `FACIAL_` bones through the head's own RigLogic data, as the game does.

**Architecture:** The app writes each 3L head's DNA export bytes to a `.rigdna` file named in the head's meta. The plugin reads it once (stdlib reader, every byte accounted for) and builds plain Blender drivers: knobs → 167 raw-control properties (Epic's decoded board formulas) → 328 corrective properties → chunked coefficient sums on mechanism bones in UE's frame, which the real facial bones copy.

**Tech Stack:** C# (.NET 10, CUE4Parse) for the export; Blender 5.2 Python (bpy, mathutils, stdlib) for the plugin; headless isolated Blender tests.

**Spec:** `docs/superpowers/specs/2026-10-10-riglogic-face-design.md`

## Global Constraints

- Upstream FP files get only one-line hooks marked `// MP` / `# MP`; fork code lives in fork files.
- Comments: one or two short lines, only the why or a non-obvious fact. Files under ~900 lines.
- Plugin Python: stdlib + bpy/mathutils only (no numpy).
- Every driver is a simple expression (`driver.is_simple_expression` true) or a SUM driver; expressions are at most 255 characters (Blender truncates longer ones).
- Motion is added to FP's rest pose (`ue_rest` frames), never to the DNA neutral.
- The reader consumes every byte of the dump or raises; on any failure the board falls back to today's shape-key mode, logged once.
- No game data in the repo: tests use a synthetic file from a test writer.
- Commits: short plain messages the user approves; identity kyoocreatives noreply; no Co-Authored-By; never stage `external/CUE4Parse`, `Fork.local.props`, `.superpowers/`, `CLAUDE.local.md`, out/ folders.
- Headless test runner: `bash <scratchpad>/rigcheck.sh <test file name>` (copies the plugin to `fpfork-private/devtools/out/baseline-plugin` as `fpmp_baseline`, runs isolated Blender; full log in `/tmp/rigcheck.log`).

## Review Focus

1. A head whose `.rigdna` is missing, truncated or written by another RigLogic version: the board still appears in shape-key mode, one log line, no exception (Task 4 test).
2. A joint channel with more terms than fit one expression: chunked, every expression ≤ 255 characters, the sum still exact (Task 3 test).
3. A DNA joint the armature lacks, or an armature facial bone the DNA doesn't drive: skipped quietly; driven bones still move (Task 3 test).
4. An emote imported after the board: the facial bones play their own keys (copies muted), as the shape keys do today (Task 4 test).
5. Building twice on the same armature (re-import into the same scene, a second hook call): no duplicate bones or drivers (Task 4 test).

---

## File Structure

- Create `plugins/Blender/fortnite_porting/processing/context/riglogic_read.py`: reader (DNA names + RigLogic dump) and pure-Python evaluation. One responsibility: bytes → `Rig` dict.
- Create `plugins/Blender/fortnite_porting/processing/context/riglogic_face.py`: builder (properties, correctives, mechanism bones, chunked drivers, copies) and `set_on`.
- Modify `plugins/Blender/fortnite_porting/processing/context/metahuman_board.py`: `add(obj, size=None, dna=None)` bones mode, live knobs from the rig, `set_on` covers bones.
- Modify `plugins/Blender/fortnite_porting/material_porter/mesh_hooks.py` (`_metahuman`): pass the head meta's `FaceDNA`.
- Create `tests/plugin/riglogic_writer.py` (test helper: writes a synthetic DNA + dump) and `tests/plugin/riglogic_face_check.py`.
- Create `src/FortnitePorting.Exporting/MaterialPorter/FaceDna.cs`: `UFortFaceDna` export class (raw bytes), its registration, and `ExportContext.FaceDna(USkeletalMesh)`.
- Modify `src/FortnitePorting.Exporting/Models/ExportMeta.cs` (`ExportHeadMeta`: `FaceDNA` field, `// MP`), `src/FortnitePorting.Exporting/Context/ExportContext.Fortnite.cs` (head branch, `// MP`), `src/FortnitePorting/Services/CUE4ParseService.cs` (registration, `// MP`).
- Devtools (fpfork-private): `devtools/riglogic/` keeps the spike reader as reference; `devtools/scripts/riglogic_face_real.py` real-asset check.

---

### Task 1: Reader (`riglogic_read.py`) with a synthetic writer

**Files:**
- Create: `plugins/Blender/fortnite_porting/processing/context/riglogic_read.py`
- Create: `tests/plugin/riglogic_writer.py`
- Create: `tests/plugin/riglogic_face_check.py`

**Interfaces:**
- Produces:
  - `riglogic_read.read(path: str) -> dict` (the `Rig`): keys `gui: list[str]`, `raw: list[str]`, `joints: list[str]`, `parents: list[int]`, `psds: list[{"output": int, "inputs": list[int], "weight": float}]`, `channels: dict[(int joint, int attr9), list[(int input, float coef)]]` (attr9 0-8 = tx ty tz rx ry rz sx sy sz; LOD 0), `gui_to_raw: dict` (the conditional table), `rotation_sequence: str`, `rotation_signs: list[int]`, `units: (str, str)`.
  - `riglogic_read.evaluate(rig, raw: dict[str, float]) -> dict[str, list[9 floats]]` (raw names as in `rig["raw"]`).
  - `riglogic_read.raw_from_gui(rig, gui: dict[str, float]) -> dict[str, float]`.
  - `riglogic_read.RigLogicError(Exception)`.
  - `tests/plugin/riglogic_writer.write(path, rig_spec) -> None`, where `rig_spec` = `{"gui": [...], "raw": [...], "joints": [...], "parents": [...], "psds": [(inputs, weight)], "rows": [(joint, attr9, {input: coef})], "gui_to_raw": [(gui, raw, from, to, slope, cut)]}`.

- [ ] **Step 1: Write the test writer** (`tests/plugin/riglogic_writer.py`): it writes, after 32 junk bytes and the 4-byte prefix `01 00 00 00`: a DNA v2.8 stream (`b"DNA"`, u16 2, u16 8, index table with `desc` and `defn` layers: each entry id 4 bytes, u32 version, u32 offset, u32 size, offsets relative to the `DNA` start) and right after it the dump, all big-endian, in exactly the layout `fpfork-private/devtools/riglogic/rl_dump.py` reads (`read_dna`, `read_dump`): configuration (22 bytes: calc type 4, six u8 flags 1, translationType 3, rotationType 4 (Quaternions), scaleType 3, three f32 0), metadata (coordinate system left/down/front as u32 0,3,4; rotation sequence u32 0; signs u32 1,1,1; counts as u16 in the order of `read_dump`; evaluators as a counted u16 vector of 9: ml 1, rbf 1, bpcm 2, quaternion 1, twistSwing 1, mlJoints 1, blendShapes 2, animatedMaps 2, psdNet 2), controls (registeredControls one LOD list of all raw indices; GUI→raw conditional table from `gui_to_raw` with `intervalsRemaining` 0, empty rangeMaps; empty initialValues), ml meshRegionCounts empty, PSDNet (inputLODs/outputLODs one LOD each, inputIndicesPerPSD flat, psds `(u64 offset, u64 size, f32 weight)`, psdMinIndex = raw count, psdMaxIndex = raw + psd count - 1), joints bpcm with one group holding all rows: block height 8 (rows padded with zero rows to a multiple of 4; values laid out per `_decode_group_matrix` inverse: blocks of 8 rows (4 for the last if fewer), column-major inside a block), lodRegions one per LOD (lodCount 1) computed with `rl_dump._padded_block_view(rows, padded_rows, 8, 4)` for rows and `(cols, cols, cols)` for columns, outputRotationIndices/LODs empty, then neutral values (10 per joint: 0,0,0, 0,0,0,1, 1,1,1), variableAttributeIndices one LOD list, jointIndices one LOD list, u16 group count; blend shapes (lods [0], empty in/out); animated maps (lods [0], empty conditional table). Output index for row (joint j, attr a) in the 10-attribute layout: `j*10 + (a if a < 6 else a + 1)` (attr 6 = qw is skipped); rotation rows hold Euler degrees in the qx/qy/qz slots.

```python
"""Writes a small synthetic DNA + RigLogic dump in the layout Fortnite's cooked heads use (see riglogic_read)."""
import struct


class W:
    def __init__(self):
        self.b = bytearray()

    def put(self, fmt, *v):
        self.b += struct.pack(">" + fmt, *v)

    def u8(self, v): self.put("B", v)
    def u16(self, v): self.put("H", v)
    def u32(self, v): self.put("I", v)
    def u64(self, v): self.put("Q", v)
    def f32(self, v): self.put("f", v)

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
    desc.string("synthetic"); desc.u16(0); desc.u16(0); desc.u16(0); desc.u32(0)
    desc.u16(0); desc.u16(0)                       # cm, degrees
    for axis in (0, 2, 4):                         # left, up, front
        desc.u16(axis)
    desc.u16(1); desc.u16(0); desc.string(""); desc.string("")
    for _ in range(4):                             # LOD mappings: one LOD, empty
        defn.vec("H", [0]); defn.u32(1); defn.vec("H", [])
    for names in (spec["gui"], spec["raw"], spec["joints"], [], [], []):
        defn.u32(len(names))
        for n in names:
            defn.string(n)
    defn.vec("H", []); defn.vec("H", [])
    defn.vec("H", spec["parents"])
    for _ in range(6):
        defn.vec("f", [0.0] * len(spec["joints"]))
    head = W()
    head.b += b"DNA"; head.u16(2); head.u16(8); head.u32(2)
    start = 3 + 4 + 4 + 2 * 16
    for lid, body, off in ((b"desc", desc, start), (b"defn", defn, start + len(desc.b))):
        head.b += lid; head.u32(1); head.u32(off); head.u32(len(body.b))
    return bytes(head.b + desc.b + defn.b)


def _cond(w, rows, inputs, outputs):
    w.u32(0)                                       # rangeMaps
    w.vec("H", [0] * len(rows))
    w.vec("H", [r[0] for r in rows]); w.vec("H", [r[1] for r in rows])
    for k in (2, 3, 4, 5):
        w.vec("f", [r[k] for r in rows])
    w.u16(inputs); w.u16(outputs)


def write(path, spec):
    raw_n, psd_n, joints = len(spec["raw"]), len(spec["psds"]), len(spec["joints"])
    rows = spec["rows"]
    inputs = sorted({i for _, _, coefs in rows for i in coefs})
    padded = -(-len(rows) // 4) * 4
    w = W()
    w.u8(4)
    for _ in range(6):
        w.u8(1)
    w.u8(3); w.u8(4); w.u8(3); w.f32(0.0); w.f32(0.0); w.f32(0.0)
    for v in (0, 3, 4, 0, 1, 1, 1):
        w.u32(v)
    for v in (1, len(spec["gui"]), raw_n, psd_n, 0, 0, 1, joints * 9, 0, 0, 0, 0, 0, 0):
        w.u16(v)
    w.vec("H", [1, 1, 2, 1, 1, 1, 2, 2, 2])
    w.u32(1); w.vec("H", list(range(raw_n)))
    _cond(w, spec["gui_to_raw"], len(spec["gui"]), raw_n)
    w.u32(0)                                       # initialValues
    w.vec("H", [])                                 # ml meshRegionCounts
    flat, psds = [], []
    for ins, weight in spec["psds"]:
        psds.append((len(flat), len(ins), weight))
        flat += ins
    w.u32(1); w.vec("H", sorted({i for ins, _ in spec["psds"] for i in ins}))
    w.u32(1); w.vec("H", list(range(raw_n, raw_n + psd_n)))
    w.vec("H", flat)
    w.u32(len(psds))
    for off, size, weight in psds:
        w.u64(off); w.u64(size); w.f32(weight)
    w.u16(raw_n); w.u16(raw_n + max(psd_n, 1) - 1)
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
    w.vec("H", []); w.vec("H", [])
    w.u32(1)
    for v in (0, 0, 0, 0, 0, 0, len(values), len(inputs), padded):
        w.u32(v)
    w.vec("f", [0, 0, 0, 0, 0, 0, 1, 1, 1, 1] * joints)
    w.u32(1); w.vec("H", list(range(joints * 9)))
    w.u32(1); w.vec("H", list(range(joints)))
    w.u16(1)
    w.vec("H", [0]); w.vec("H", []); w.vec("H", [])
    w.vec("H", [0]); _cond(w, [], 0, 0)
    with open(path, "wb") as f:
        f.write(bytes(32) + struct.pack("<I", 1) + _dna(spec) + bytes(w.b))
```

- [ ] **Step 2: Write the failing reader tests** (`tests/plugin/riglogic_face_check.py`, same harness style as `metahuman_board_check.py`: `check(name, got, want)`, a pass/fail summary line `[riglogic_face_check] N passed, M failed`, exit code):

```python
"""Checks the RigLogic face: the reader on a synthetic file, the bones it builds.

    blender -b --factory-startup --python-exit-code 1 -P tests/plugin/riglogic_face_check.py -- <plugin parent>"""
import os
import sys
import tempfile

import bpy

sys.path.insert(0, sys.argv[sys.argv.index("--") + 1])
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fpmp_baseline  # noqa: E402,F401
from fpmp_baseline.processing.context import riglogic_read as rr  # noqa: E402
import riglogic_writer  # noqa: E402

FAILS, PASSES = [], [0]


def check(name, got, want, tol=1e-4):
    ok = all(abs(g - w) <= tol for g, w in zip(got, want)) and len(got) == len(want) if isinstance(want, (tuple, list)) \
        and want and isinstance(want[0], float) else (abs(got - want) <= tol if isinstance(want, float) else got == want)
    if ok:
        PASSES[0] += 1
    else:
        FAILS.append(name)
        print("[riglogic_face_check] FAIL %s: got %r, want %r" % (name, got, want))


SPEC = {
    "gui": ["CTRL_C_jaw.ty", "CTRL_L_eye_blink.ty"],
    "raw": ["CTRL_expressions.jawOpen", "CTRL_expressions.eyeBlinkL", "CTRL_expressions.mouthFunnel"],
    "joints": ["FACIAL_C_FacialRoot", "FACIAL_C_Jaw", "FACIAL_L_Eyelid", "FACIAL_X_Missing"],
    "parents": [0, 0, 0, 0],
    "psds": [([0, 1], 1.0)],                          # input 3 = jawOpen * eyeBlinkL
    "rows": [(1, 3, {0: -20.0}), (1, 1, {0: 0.5}), (1, 2, {3: 2.0}), (2, 3, {1: -40.0, 3: 5.0}), (2, 6, {1: 0.1}),
             (3, 0, {0: 1.0})] + [(1, 5, {0: 0.01 * k}) for k in range(1, 2)],
    "gui_to_raw": [(0, 0, 0.0, 1.0, 1.0, 0.0), (1, 1, 0.0, 1.0, 1.0, 0.0)],
}
TMP = tempfile.mkdtemp()
PATH = os.path.join(TMP, "head.rigdna")
riglogic_writer.write(PATH, SPEC)

# --- reader
rig = rr.read(PATH)
check("names", (rig["gui"], rig["raw"], rig["joints"]), (SPEC["gui"], SPEC["raw"], SPEC["joints"]))
check("one corrective: jawOpen * eyeBlinkL", [(p["output"], p["inputs"], p["weight"]) for p in rig["psds"]], [(3, [0, 1], 1.0)])
check("jaw rotation row", rig["channels"][(1, 3)], [(0, -20.0)])
check("eyelid scale row (attr 6 = sx)", rig["channels"][(2, 6)], [(1, 0.1)])
out = rr.evaluate(rig, {"CTRL_expressions.jawOpen": 1.0, "CTRL_expressions.eyeBlinkL": 0.5})
check("evaluate: jaw", out["FACIAL_C_Jaw"][:6], [0.0, 0.5, 2.0 * 0.5, -20.0, 0.0, 0.01])
check("evaluate: eyelid with the corrective", out["FACIAL_L_Eyelid"][3], -40.0 * 0.5 + 5.0 * 0.5)
check("evaluate: rest is zero", all(v == 0.0 for vals in rr.evaluate(rig, {}).values() for v in vals), True)
check("GUI to raw", rr.raw_from_gui(rig, {"CTRL_C_jaw.ty": 0.7})["CTRL_expressions.jawOpen"], 0.7)
# a cut file fails loudly
with open(PATH, "rb") as f:
    data = f.read()
cut = os.path.join(TMP, "cut.rigdna")
with open(cut, "wb") as f:
    f.write(data[:-3])
try:
    rr.read(cut)
    check("a truncated dump raises", False, True)
except rr.RigLogicError:
    check("a truncated dump raises", True, True)
longer = os.path.join(TMP, "long.rigdna")
with open(longer, "wb") as f:
    f.write(data + b"\x00\x00")
try:
    rr.read(longer)
    check("trailing bytes raise (another layout)", False, True)
except rr.RigLogicError:
    check("trailing bytes raise (another layout)", True, True)

print("[riglogic_face_check] %d passed, %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
```

- [ ] **Step 3: Run it, watch it fail**

Run: `bash <scratchpad>/rigcheck.sh riglogic_face_check.py; grep -E "FAIL|passed|Error" /tmp/rigcheck.log`
Expected: `ImportError`/`ModuleNotFoundError` for `riglogic_read`.

- [ ] **Step 4: Write the reader.** Port `fpfork-private/devtools/riglogic/rl_dump.py` into `riglogic_read.py`: keep `Reader`, `_find_dna` (search `b"DNA\x00\x02"` with a `desc` first index entry), `read_dna` (names, parents from `jointHierarchy`, units, without asserts: raise `RigLogicError` on size mismatch), `_cond_table`, `_padded_block_view`, `_detect_block_height`, `_decode_group_matrix`, `read_dump` (raise `RigLogicError` instead of `NotImplementedError`/`assert`), `_out_to_joint_attr`, `_cond_forward`. New public surface:

```python
class RigLogicError(Exception):
    """The data isn't the RigLogic layout this reads (another version, a cut file)."""


def read(path):
    """The head's RigLogic data for LOD 0 (see the module docstring for the dict)."""
    with open(path, "rb") as f:
        data = f.read()
    try:
        dna = read_dna(data, _find_dna(data))
        dump = read_dump(data, dna["end"])
    except (struct.error, IndexError, KeyError, ValueError) as e:
        raise RigLogicError("%s: %s" % (type(e).__name__, e)) from e
    if dump["end"] != len(data):
        raise RigLogicError("%d bytes left after the dump" % (len(data) - dump["end"]))
    meta, cfg, defn = dump["metadata"], dump["configuration"], dna["definition"]
    per_joint = cfg["translationType"] + (4 if cfg["rotationType"] == "Quaternions" else 3) + cfg["scaleType"]
    raw_n = meta["rawControlCount"]
    psd = dump["psdNet"]
    psds = [{"output": psd["psdMinIndex"] + i, "inputs": psd["inputIndicesPerPSD"][p["offset"]:p["offset"] + p["size"]],
             "weight": p["weight"]} for i, p in enumerate(psd["psds"])] if psd else []
    js = dump["joints"]["bpcm"]
    heights = _detect_block_height(js["jointGroups"], js["lodRegions"], meta["lodCount"])
    if not heights:
        raise RigLogicError("no block height fits the joint groups")
    channels = {}
    for g in js["jointGroups"]:
        if not g["rowCount"]:
            continue
        m = _decode_group_matrix(js["values"], g, heights[0])
        region = js["lodRegions"][g["lodsOffset"]]
        ins = js["inputIndices"][g["inputIndicesOffset"]:g["inputIndicesOffset"] + g["colCount"]]
        outs = js["outputIndices"][g["outputIndicesOffset"]:g["outputIndicesOffset"] + g["rowCount"]]
        for row in range(region["rows"][0]):
            joint, attr = _out_to_joint_attr(outs[row], per_joint)
            if attr is not None:
                channels.setdefault((joint, attr), []).extend(
                    (ins[c], m[row][c]) for c in range(region["cols"][0]) if m[row][c] != 0.0)
    return {"gui": defn["guiControlNames"], "raw": defn["rawControlNames"], "joints": defn["jointNames"],
            "parents": defn["jointHierarchy"], "raw_count": raw_n, "psds": psds, "psd_lod0": psd and psd["outputLODs"][0],
            "channels": channels, "gui_to_raw": dump["controls"]["guiToRawMapping"],
            "rotation_sequence": meta["rotationSequence"], "rotation_signs": meta["rotationSigns"],
            "units": (dna["descriptor"]["translationUnit"], dna["descriptor"]["rotationUnit"])}


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
    index = {n: i for i, n in enumerate(rig["gui"])}
    values = [0.0] * len(rig["gui"])
    for name, value in gui.items():
        values[index[name]] = value
    return dict(zip(rig["raw"], _cond_forward(rig["gui_to_raw"], values)))
```

The module docstring lists the `Rig` keys (as in Interfaces) and says the layout is RigLogic's runtime dump as Fortnite cooks it (older than OpenRigLogic v13.5: see spec).

- [ ] **Step 5: Run the tests, watch them pass**

Run: `bash <scratchpad>/rigcheck.sh riglogic_face_check.py; grep -E "FAIL|passed" /tmp/rigcheck.log`
Expected: `[riglogic_face_check] 10 passed, 0 failed`.

- [ ] **Step 6: Real file check (devtools, not committed to fpfork):** `python -c "import sys; sys.path.insert(0, r'C:\Users\kyooc\Documents\Claude\fpfork\plugins\Blender\fortnite_porting\processing\context'); import riglogic_read as rr; r = rr.read(r'C:\Users\kyooc\Documents\Claude\fpfork-private\devtools\out\dna\F_MED_VibrantShell_Head_DNA.uasset'); print(len(r['gui']), len(r['raw']), len(r['joints']), len(r['psds']), len(r['channels']), sum(len(t) for t in r['channels'].values()))"`
Expected: `115 167 95 328 531 57965`.

---

### Task 2: App: the head's DNA bytes to the plugin

**Files:**
- Create: `src/FortnitePorting.Exporting/MaterialPorter/FaceDna.cs`
- Modify: `src/FortnitePorting.Exporting/Models/ExportMeta.cs` (`ExportHeadMeta`)
- Modify: `src/FortnitePorting.Exporting/Context/ExportContext.Fortnite.cs` (head branch, before `exportPart.Meta = meta;`)
- Modify: `src/FortnitePorting/Services/CUE4ParseService.cs:251` (after `SkippedExports.Register()`)

**Interfaces:**
- Produces: head meta JSON field `FaceDNA` (absolute path of a `.rigdna` file, or absent/null).

- [ ] **Step 1: Write the export class, registration and writer** (`FaceDna.cs`):

```csharp
using System.IO;
using System.Linq;
using CUE4Parse.UE4.Assets;
using CUE4Parse.UE4.Assets.Exports;
using CUE4Parse.UE4.Assets.Exports.SkeletalMesh;
using CUE4Parse.UE4.Assets.Readers;

namespace FortnitePorting.Exporting.MaterialPorter;

/// <summary>A head's DNA asset (Fortnite's export class "DNA") kept as bytes: the plugin reads its RigLogic data.</summary>
public class UFortFaceDna : UObject
{
    public byte[] RawData = [];

    public override void Deserialize(FAssetArchive Ar, long validPos)
    {
        base.Deserialize(Ar, validPos);
        RawData = Ar.ReadBytes((int) (validPos - Ar.Position));
    }

    public static void Register() => ObjectTypeRegistry.RegisterClass("DNA", typeof(UFortFaceDna));
}
```

and in the same file a partial of `ExportContext` (namespace `FortnitePorting.Exporting.Context`, matching `ExportContext.cs`):

```csharp
public partial class ExportContext
{
    /// <summary>The head's RigLogic data file (written once next to the mesh), or null for a head without one.</summary>
    public string? FaceDna(USkeletalMesh mesh)
    {
        foreach (var data in mesh.AssetUserData ?? [])
        {
            if (!data.TryLoad(out var userData) || userData.ExportType != "DNAAssetUserData") continue;
            if (!userData.TryGetValue(out UFortFaceDna dna, "DNAAsset")) continue;
            var path = GetExportPath(dna, "rigdna");
            if (!File.Exists(path)) File.WriteAllBytes(path, dna.RawData);
            return path;
        }
        return null;
    }
}
```

(Check the property name holding the DNA on `DNAAssetUserData` with `python fpdev.py dump <head mesh path> --grep DNA` before writing; use the name it shows.)

- [ ] **Step 2: Hooks.** `ExportMeta.cs`, in `ExportHeadMeta`: `public string? FaceDNA;     // MP: the head's RigLogic data (riglogic_face)`. `ExportContext.Fortnite.cs`, head branch before `exportPart.Meta = meta;`: `meta.FaceDNA = FaceDna(skeletalMesh);     // MP`. `CUE4ParseService.cs` after line 251: `FortnitePorting.Exporting.MaterialPorter.UFortFaceDna.Register();     // MP`.

- [ ] **Step 3: Build and check on the real head**

Run: `cd fpfork-private/devtools && bash testapp.sh test` then `python fpdev.py export mh_vs --type Outfit --path <VibrantShell outfit path used for mh_vs> --option RigType=3` and `python -c "import json; d=json.load(open('out/fpdev/mh_vs/payload.json')); print([m['Meta'].get('FaceDNA') for m in d['Exports'][0]['Meshes'] if m.get('Meta')])"`.
Expected: one head entry with a path ending `F_MED_VibrantShell_Head_DNA.rigdna`; `rr.read(that path)` gives `115 167 95 328 531 57965` (Task 1 Step 6 command with the new path).

---

### Task 3: Builder (`riglogic_face.py`)

**Files:**
- Create: `plugins/Blender/fortnite_porting/processing/context/riglogic_face.py`
- Modify: `tests/plugin/riglogic_face_check.py` (append the builder block before the summary)

**Interfaces:**
- Consumes: `riglogic_read.read`, `riglogic_read.evaluate`; `material_porter.effects.ue_rest`; `metahuman_board.expression(node, var)`, `metahuman_board._length`, board data `load()["curves"]`, knob bones `MB_<control>` with `unit(obj, control, axis)`.
- Produces:
  - `riglogic_face.build(obj, rig, formulas: dict[str raw name, node], var) -> int` (driven joints built). `formulas` maps raw names (`CTRL_expressions.jawOpen`) to board formula nodes; `var(control, axis)` is the board's knob variable factory for a driver (as `metahuman_board._drive` uses).
  - `riglogic_face.set_on(obj, on: bool)`.
  - Constants: `EXPR = "RL_Expressions"` (bone holding raw + corrective properties), `MCH = "RL_"`, `POSE = "RL_pose_"`, `COPY = "RL copy"`, `CHUNK = 20`.

- [ ] **Step 1: Write the failing builder tests** (append to `riglogic_face_check.py`; a synthetic armature with `head`, `FACIAL_C_FacialRoot`, `FACIAL_C_Jaw`, `FACIAL_L_Eyelid` (each 0.02 long, roll 0.3 so their frames aren't the armature's), and a direct test of `build` with formulas that read raw props set by hand instead of knobs):

```python
# --- builder
from mathutils import Euler, Matrix, Vector  # noqa: E402
from fpmp_baseline.processing.context import riglogic_face as rf  # noqa: E402
from fpmp_baseline.material_porter.effects import ue_rest  # noqa: E402

arm = bpy.data.objects.new("rl", bpy.data.armatures.new("rl"))
bpy.context.scene.collection.objects.link(arm)
bpy.context.view_layer.objects.active = arm
bpy.ops.object.mode_set(mode='EDIT')
for name, head, parent in (("head", (0, 0, 1.6), None), ("FACIAL_C_FacialRoot", (0, 0, 1.62), "head"),
                           ("FACIAL_C_Jaw", (0, -0.02, 1.58), "FACIAL_C_FacialRoot"),
                           ("FACIAL_L_Eyelid", (0.03, -0.08, 1.68), "FACIAL_C_FacialRoot"),
                           ("FACIAL_R_NotInDNA", (-0.03, -0.08, 1.68), "FACIAL_C_FacialRoot")):
    b = arm.data.edit_bones.new(name)
    b.head, b.tail, b.roll = head, Vector(head) + Vector((0, 0.02, 0.01)), 0.3
    if parent:
        b.parent = arm.data.edit_bones[parent]
bpy.ops.object.mode_set(mode='OBJECT')

# formulas: each raw control reads one hand-set property, so the test drives the raw values directly
for n in SPEC["raw"]:
    arm["test_" + n] = 0.0


def hand_var(driver, name):
    v = driver.variables.new()
    v.name, v.type = "h", 'SINGLE_PROP'
    v.targets[0].id, v.targets[0].data_path = arm, '["test_%s"]' % name
    return "h"


built = rf.build(arm, rig, {n: n for n in SPEC["raw"]}, hand_var)
check("driven joints built (the DNA joint the armature lacks skipped)", built, 2)
check("mechanism and pose bones", all(n in arm.data.bones for n in ("RL_FACIAL_C_Jaw", "RL_pose_FACIAL_C_Jaw", "RL_Expressions")), True)
check("an undriven facial bone left alone", len(arm.pose.bones["FACIAL_R_NotInDNA"].constraints), 0)


def ue_local(name):
    """The bone's posed frame, in the game's frame, relative to its parent's posed game frame."""
    b = arm.data.bones[name]
    pose = arm.pose.bones[name].matrix @ (b.matrix_local.inverted() @ ue_rest(b))
    parent = arm.pose.bones[b.parent.name].matrix @ (b.parent.matrix_local.inverted() @ ue_rest(b.parent))
    return parent.inverted() @ pose


rest_local = {n: ue_local(n).copy() for n in ("FACIAL_C_Jaw", "FACIAL_L_Eyelid")}
bpy.context.view_layer.update()
check("rest stays rest", all((ue_local(n).to_translation() - m.to_translation()).length < 1e-6 for n, m in rest_local.items()), True)
for raw, value in (("CTRL_expressions.jawOpen", 1.0), ("CTRL_expressions.eyeBlinkL", 0.5)):
    arm["test_" + raw] = value
arm.update_tag()
bpy.context.view_layer.update()
want = rr.evaluate(rig, {"CTRL_expressions.jawOpen": 1.0, "CTRL_expressions.eyeBlinkL": 0.5})
flip = Matrix(((1, 0, 0), (0, -1, 0), (0, 0, 1)))
for name in ("FACIAL_C_Jaw", "FACIAL_L_Eyelid"):
    t, r, s = want[name][0:3], want[name][3:6], want[name][6:9]
    expect_t = rest_local[name].to_translation() + flip @ Vector(t) / 100.0
    delta = Euler([math.radians(a) for a in (-r[0], r[1], -r[2])], 'XYZ').to_matrix()
    expect_r = rest_local[name].to_3x3() @ delta
    got = ue_local(name)
    check("%s moves as RigLogic says (translation)" % name, tuple(got.to_translation()), tuple(expect_t), tol=1e-5)
    check("%s moves as RigLogic says (rotation)" % name,
          got.to_quaternion().rotation_difference(expect_r.to_quaternion()).angle < 1e-4, True)
    check("%s scale" % name, tuple(got.to_scale()), tuple(1.0 + v for v in s), tol=1e-5)
drivers = arm.animation_data.drivers
check("every driver simple or a sum, no expression over 255 characters",
      all((d.driver.type == 'SUM' or d.driver.is_simple_expression) and len(d.driver.expression) <= 255 for d in drivers), True)
rf.set_on(arm, False)
bpy.context.view_layer.update()
check("off: the facial bones are free", all(c.mute for c in arm.pose.bones["FACIAL_C_Jaw"].constraints if c.name == rf.COPY), True)
rf.set_on(arm, True)
```

and a chunking test on a second synthetic file with one channel of 50 terms (rows `[(1, 0, {i: 0.01 for i in range(3)})]` is too short; use a spec with 60 raw controls `CTRL_expressions.k<i>` and one row `(1, 0, {i: 0.001 * (i + 1) for i in range(60)})`), built on a fresh copy of the armature, every raw set to 1: the jaw's translation x equals `sum(0.001 * (i + 1))` cm within 1e-5, and the channel needs more than one chunk property (`len([k for k in arm.pose.bones["RL_FACIAL_C_Jaw"].keys() if k.startswith("c")]) > 1`).

Add `import math` at the top of the test file.

- [ ] **Step 2: Run, watch it fail**

Run: `bash <scratchpad>/rigcheck.sh riglogic_face_check.py; grep -E "FAIL|passed|Error" /tmp/rigcheck.log`
Expected: `ModuleNotFoundError` for `riglogic_face`.

- [ ] **Step 3: Write the builder.**

```python
"""Epic's RigLogic on FP's facial bones, as plain drivers: knobs drive the raw controls (the board's formulas), those
drive the correctives, and both drive coefficient sums on mechanism bones in the game's frame that the facial bones
copy. Motion is added to FP's rest pose (spec 2026-10-10-riglogic-face)."""
import math

import bpy
from mathutils import Matrix

EXPR, MCH, POSE, COPY = "RL_Expressions", "RL_", "RL_pose_", "RL copy"
CHUNK = 20          # terms per chunk expression: Blender keeps 255 characters of an expression
MECHANICS = "Face Mechanics"
FLIP = Matrix(((1, 0, 0), (0, -1, 0), (0, 0, 1)))     # the dump's frame mirrors Y against the game's


def _num(v):
    return ("%.6g" % v).replace("e-0", "e-")


def _names():
    """a, b, ... z, aa, ab ... for driver variables."""
    i = 0
    while True:
        s, n = "", i
        while True:
            s = chr(97 + n % 26) + s
            n = n // 26 - 1
            if n < 0:
                break
        yield s
        i += 1


def _prop_var(driver, name, obj, path):
    v = driver.variables.new()
    v.name, v.type = name, 'SINGLE_PROP'
    v.targets[0].id, v.targets[0].data_path = obj, path
    return name


def _input_path(rig, i):
    name = rig["raw"][i] if i < rig["raw_count"] else "psd%d" % (i - rig["raw_count"])
    return 'pose.bones["%s"]["%s"]' % (EXPR, name)


def _properties(obj, rig, formulas, var):
    """The raw controls (from the board's formulas) and the correctives, as properties on one hidden bone."""
    pb = obj.pose.bones[EXPR]
    for name in rig["raw"]:
        pb[name] = 0.0
        if name in formulas:
            driver = pb.driver_add('["%s"]' % name).driver
            driver.type = 'SCRIPTED'
            driver.expression = formulas[name] if isinstance(formulas[name], str) else var(driver, formulas[name])
    for k, p in enumerate(rig["psds"]):
        name = "psd%d" % k
        pb[name] = 0.0
        driver = pb.driver_add('["%s"]' % name).driver
        driver.type = 'SCRIPTED'
        terms = ["min(1,max(0,%s))" % _prop_var(driver, v, obj, _input_path(rig, i)) for v, i in zip(_names(), p["inputs"])]
        driver.expression = "min(1,%s*%s)" % (_num(p["weight"]), "*".join(terms))
```

Note on `formulas`/`var` (as used by Task 4): `formulas[name]` is a board formula node and `var(driver, node)` returns the expression text after adding its knob variables to `driver` (Task 4 passes a closure over `metahuman_board.expression`). In the Task 3 test, `formulas[name]` is the raw name itself and `hand_var(driver, name)` adds one property variable; the builder always calls `var(driver, formulas[name])` (drop the `isinstance` branch above if it isn't needed).

Continue the module:

```python
def _bones(obj, rig):
    """Edit mode: the expressions bone, and per driven joint a mechanism bone at its game frame under the real
    parent with a child at the real bone's rest. Returns the driven joint names built."""
    from ...material_porter.effects import ue_rest
    edit = obj.data.edit_bones
    root = edit.get("FACIAL_C_FacialRoot") or edit["head"]
    expr = edit.new(EXPR)
    expr.head, expr.tail, expr.parent, expr.use_deform = root.head, root.head + (root.tail - root.head) * 0.5, root, False
    driven = sorted({rig["joints"][j] for j, _ in rig["channels"]})
    built = []
    for name in driven:
        if name not in edit or edit[name].parent is None:
            continue
        frame = ue_rest(obj.data.bones[name])
        mch = edit.new(MCH + name)
        mch.matrix = frame
        mch.length = edit[name].length
        mch.parent, mch.use_deform = edit[edit[name].parent.name], False
        pose = edit.new(POSE + name)
        pose.head, pose.tail, pose.roll = edit[name].head.copy(), edit[name].tail.copy(), edit[name].roll
        pose.parent, pose.use_deform = mch, False
        built.append(name)
    return built


def _sum(obj, pb, path, index, terms, offset, chunks):
    """Drive pb's channel (path, index) to offset + sum(coef * input), in chunk properties of CHUNK terms."""
    parts = []
    for start in range(0, len(terms), CHUNK):
        key = "c%d" % chunks[0]
        chunks[0] += 1
        pb[key] = 0.0
        driver = pb.driver_add('["%s"]' % key).driver
        driver.type = 'SCRIPTED'
        names = _names()
        driver.expression = "+".join("%s*%s" % (_prop_var(driver, next(names), obj, path_of), _num(c))
                                     for path_of, c in terms[start:start + CHUNK]).replace("+-", "-")
        parts.append(key)
    driver = pb.driver_add(path, index).driver
    driver.type = 'SCRIPTED'
    names = _names()
    driver.expression = "+".join([_num(offset)] + [_prop_var(driver, next(names), obj, 'pose.bones["%s"]["%s"]' % (pb.name, k))
                                                   for k in parts])


def build(obj, rig, formulas, var):
    """Build RigLogic's face on the armature (see the module docstring). Returns the driven joints built."""
    from ...material_porter.effects import ue_rest
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')
    built = _bones(obj, rig)
    bpy.ops.object.mode_set(mode='POSE')
    group = obj.data.collections.get("Mechanics") or obj.data.collections.get(MECHANICS) or obj.data.collections.new(MECHANICS)
    group.is_visible = False
    for name in [EXPR] + [MCH + n for n in built] + [POSE + n for n in built]:
        for c in obj.data.bones[name].collections:
            c.unassign(obj.data.bones[name])
        group.assign(obj.data.bones[name])
    _properties(obj, rig, formulas, var)
    index = {n: i for i, n in enumerate(rig["joints"])}
    for name in built:
        b = obj.data.bones[name]
        local = (ue_rest(b.parent).inverted() @ ue_rest(b)).to_3x3()
        to_bone = local.transposed() @ FLIP / 100.0          # parent-frame cm -> the bone's own frame, metres
        pb = obj.pose.bones[MCH + name]
        pb.rotation_mode = 'XYZ'
        rows = {a: rig["channels"].get((index[name], a), []) for a in range(9)}
        chunks = [0]
        for axis in range(3):
            terms = {}
            for k in range(3):
                for i, c in rows[k]:
                    terms[i] = terms.get(i, 0.0) + to_bone[axis][k] * c
            if any(terms.values()):
                _sum(obj, pb, "location", axis, [(_input_path(rig, i), c) for i, c in terms.items() if c], 0.0, chunks)
        for axis, sign in ((0, -1.0), (1, 1.0), (2, -1.0)):          # Euler degrees, mirrored like FLIP
            if rows[3 + axis]:
                _sum(obj, pb, "rotation_euler", axis,
                     [(_input_path(rig, i), sign * math.radians(c)) for i, c in rows[3 + axis]], 0.0, chunks)
        for axis in range(3):
            if rows[6 + axis]:
                _sum(obj, pb, "scale", axis, [(_input_path(rig, i), c) for i, c in rows[6 + axis]], 1.0, chunks)
        copy = obj.pose.bones[name].constraints.new('COPY_TRANSFORMS')
        copy.name, copy.target, copy.subtarget = COPY, obj, POSE + name
    bpy.ops.object.mode_set(mode='OBJECT')
    return len(built)


def set_on(obj, on):
    """The board drives the facial bones (on), or they play their own keys (off)."""
    for pb in obj.pose.bones:
        for c in pb.constraints:
            if c.name == COPY:
                c.mute = not on
    for fc in (obj.animation_data.drivers if obj.animation_data else []):
        if fc.data_path.startswith('pose.bones["%s' % MCH):
            fc.mute = not on
```

(`_sum`'s chunk expression uses the variable letters; one chunk of 20 terms is at most `20 * (2 + 1 + 12 + 1)` ≈ 320 characters with the longest `_num`: if a test shows an expression over 255, lower `CHUNK` to 14 and say so in the ledger.)

- [ ] **Step 4: Run, watch it pass**

Run: `bash <scratchpad>/rigcheck.sh riglogic_face_check.py; grep -E "FAIL|passed" /tmp/rigcheck.log`
Expected: `[riglogic_face_check] N passed, 0 failed` (every check above).

---

### Task 4: The board's bones mode and the import hook

**Files:**
- Modify: `plugins/Blender/fortnite_porting/processing/context/metahuman_board.py` (`add`, `set_on`)
- Modify: `plugins/Blender/fortnite_porting/material_porter/mesh_hooks.py` (`_metahuman`)
- Modify: `tests/plugin/riglogic_face_check.py` (board block)

**Interfaces:**
- Consumes: `riglogic_read.read`, `RigLogicError`; `riglogic_face.build`, `riglogic_face.set_on`.
- Produces: `metahuman_board.add(obj, size=None, dna=None) -> int` (drivers made; in bones mode the raw controls driven); armature data flags `fpmp_metahuman_board_bones` (bool).

- [ ] **Step 1: Failing tests** (append): a 3L head built as `metahuman_board_check.head_3l` does (copy that helper into the test: armature with `root`, `head`, `FACIAL_C_FacialRoot`, plus `FACIAL_C_Jaw` under the root; mesh with the board's 73 first keys) and a synthetic DNA whose raw names are the board curves' names with `.` (`CTRL_expressions.jawOpen`) and one row driving `FACIAL_C_Jaw` rx from `jawOpen`:

```python
# --- the board in bones mode
from fpmp_baseline.processing.context import face_board, metahuman_board as mb  # noqa: E402
face_board.register()
board_spec = dict(SPEC, raw=["CTRL_expressions.jawOpen", "CTRL_expressions.eyeBlinkL", "CTRL_expressions.mouthFunnel"],
                  joints=["FACIAL_C_FacialRoot", "FACIAL_C_Jaw"], parents=[0, 0], rows=[(1, 3, {0: -20.0})], psds=[([0, 1], 1.0)])
dna = os.path.join(TMP, "board.rigdna")
riglogic_writer.write(dna, board_spec)
head, mesh = head_3l("bones")
made = mb.add(head, dna=dna)
check("bones mode: shape keys not driven", mesh.data.shape_keys.animation_data is None or
      len(mesh.data.shape_keys.animation_data.drivers) == 0, True)
check("bones mode flag", head.data.get("fpmp_metahuman_board_bones"), True)
check("bones mode: knobs for what the raw controls read", "MB_CTRL_C_jaw" in head.data.bones, True)
knob = head.pose.bones["MB_CTRL_C_jaw"]
knob.location.z = mb.unit(head, "CTRL_C_jaw", "y")
head.update_tag()
bpy.context.view_layer.update()
jaw_open = head.pose.bones["RL_Expressions"]["CTRL_expressions.jawOpen"]
check("the jaw knob drives jawOpen as the board formula says", round(jaw_open, 3),
      round(mb.evaluate(mb.load()["curves"]["CTRL_expressions_jawOpen"], {("CTRL_C_jaw", "y"): 1.0}), 3))
check("and the jaw bone turns", head.pose.bones["FACIAL_C_Jaw"].matrix.to_quaternion().angle > 0.1, True)
# Review Focus 4: an emote import frees the bones
from fpmp_baseline.material_porter import anim_hooks  # noqa: E402
anim_hooks.begin(head)
check("an animation import frees the facial bones", all(c.mute for c in head.pose.bones["FACIAL_C_Jaw"].constraints), True)
# Review Focus 5: a second add builds nothing
check("a second add builds nothing", mb.add(head, dna=dna), 0)
check("one mechanism bone per joint", sum(1 for b in head.data.bones if b.name == "RL_FACIAL_C_Jaw"), 1)
# Review Focus 1: a bad file falls back to shape keys
bad = os.path.join(TMP, "bad.rigdna")
with open(bad, "wb") as f:
    f.write(b"not a dna")
fallback, fallback_mesh = head_3l("fallback")
check("an unreadable DNA falls back to shape keys", mb.add(fallback, dna=bad), 73)
check("fallback flag off", bool(fallback.data.get("fpmp_metahuman_board_bones")), False)
```

- [ ] **Step 2: Run, watch it fail**

Expected: `TypeError: add() got an unexpected keyword argument 'dna'`.

- [ ] **Step 3: Implement.** In `metahuman_board.py`:
  - `BONES = "fpmp_metahuman_board_bones"`.
  - `add(obj, size=None, dna=None)`: before computing `live`, read the rig when `dna` is given:

```python
    rig = None
    if dna:
        from . import riglogic_read
        try:
            rig = riglogic_read.read(dna)
        except (OSError, riglogic_read.RigLogicError) as e:
            print("[MetaHuman board] %s: RigLogic data unreadable (%s), shape keys instead" % (obj.name, e))
    if rig is not None:
        # every raw control the board has a formula for; its knobs are the ones those formulas read
        formulas = {n: data["curves"][n.replace(".", "_")] for n in rig["raw"] if n.replace(".", "_") in data["curves"]}
        live = set().union(*(_read(node, set()) for node in formulas.values()))
    else:
        live = set().union(*(_read(data["curves"]["CTRL_expressions_" + k.name], set()) for _, k in keys))
```

  - After the knobs are built (still before the shape-key driver loop): in bones mode, build and skip the shape-key drivers:

```python
    if rig is not None:
        from . import riglogic_face
        built = {b.name[len(PREFIX):] for b in obj.data.bones if b.name.startswith(PREFIX)}
        axes = {c["name"]: c["axes"] for c in data["controls"]}

        def var(driver, node):
            return _expression(obj, driver, node, s, axes, built)

        riglogic_face.build(obj, rig, formulas, var)
        obj.data[MARK], obj.data[BONES], obj.data[MESHES] = True, True, sorted({m.name for m, _ in keys})
        return len(formulas)
```

  - Refactor `_drive(obj, key, node, s, axes, built)` so its body (variables + expression) is a helper `_expression(obj, driver, node, s, axes, built) -> str` that adds the knob variables to `driver` and returns the expression; `_drive` becomes `driver = key.driver_add("value").driver; driver.type = 'SCRIPTED'; driver.expression = _expression(obj, driver, node, s, axes, built)`.
  - `set_on(obj, on)`: after the shape-key loop, `if obj.data.get(BONES): from . import riglogic_face; riglogic_face.set_on(obj, on)`.
  - `mesh_hooks._metahuman`: pass the head meta's DNA:

```python
            if metahuman_board.fits(skeleton):
                dna = next((m["Meta"].get("FaceDNA") for m in ctx.imported_meshes
                            if m.get("Skeleton") == skeleton and m.get("Meta") and m["Meta"].get("FaceDNA")), None)
                Log.info("%s: MetaHuman board, %d drivers" % (skeleton.name, metahuman_board.add(skeleton, dna=dna)))
```

- [ ] **Step 4: Run, watch it pass; all suites**

Run: `for s in riglogic_face_check metahuman_board_check official_rig_check rig_check flipbook_face_check; do bash <scratchpad>/rigcheck.sh $s.py >/dev/null 2>&1; grep -E "FAIL|passed" /tmp/rigcheck.log; done`
Expected: every suite `0 failed`.

---

### Task 5: Real head

**Files:**
- Create (fpfork-private): `devtools/scripts/riglogic_face_real.py`

- [ ] **Step 1: Write the check script** (fpdev script, runs inside the imported `mh_vs.blend`): for each GUI pose (`CTRL_C_jaw.ty=1`, `CTRL_L_mouth_cornerPull.ty=1`, `CTRL_L_eye_blink.ty=1`, `CTRL_C_tongue_move.ty=1` or the first tongue control the DNA lists): set the knob (`metahuman_board.unit`), update, and print per moved joint the posed game-frame local transform against `riglogic_read.evaluate(rig, riglogic_read.raw_from_gui(rig, pose))` applied to the rest (as the Task 3 test computes `expect_t`/`expect_r`), worst translation (cm) and rotation (degrees); then the head mesh's vertex motion vs the matching shape key alone (cosine similarity as in `scripts/riglogic_jaw_test.py`, the shape key set by hand with the board off); and the board build time (`time.perf_counter` around `metahuman_board.add` on a fresh import is measured by the import log; print the `Log.info` line).

- [ ] **Step 2: Run**

Run: `python fpdev.py import mh_vs` (after the Task 2 export) then `python fpdev.py script mh_vs scripts/riglogic_face_real.py`.
Expected: worst error under 0.01 cm and 0.1 degrees for the board formula path where it equals the dump's GUI table (report any control where the board formula and the dump's GUI→raw table differ); similarity at least 0.9 for jaw, smile, blink; the tongue control moves tongue joints.

- [ ] **Step 3: Commit** (after the user approves the message; fpfork files by name: the two new plugin modules, `metahuman_board.py`, `mesh_hooks.py`, the test files, the C# files; fpfork-private: the devtools script and `devtools/riglogic/`).
