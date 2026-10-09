# Flipbook Emote Faces Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An emote imported onto a flipbook-face character plays its brow, eye and mouth frames as in game, with the default idle's blinks and eye looks underneath.

**Architecture:** CUE4Parse learns to read Fortnite's RigVM bytecode (a patch file); the Flipbook2 Control Rig and the head AnimBP's facial blend are decoded once into a written rule table; the plugin ports those rules (`material_porter/flipbook_face.py`) and keys the face material's flipbook index inputs per frame from the emote's `*_pose` curves over the idle's. The app only adds curve modes for regular emotes and the idle anim to the emote payload.

**Tech Stack:** C# (.NET 10, CUE4Parse submodule + `patches/CUE4Parse/` overlay), Blender 5.2 Python plugin, headless isolated Blender tests, fpdev devtools.

**Spec:** `docs/superpowers/specs/2026-10-09-flipbook-emote-faces-design.md`

## Global Constraints

- Upstream FP files get only one-line hooks marked `// MP` / `# MP`; fork code goes in `*.MaterialPorter.cs` partials and `material_porter/`.
- CUE4Parse is never edited in place for commit: changed files are whole copies under `patches/CUE4Parse/` (same relative path), copied over `external/CUE4Parse` for the build. Never stage `external/CUE4Parse`.
- Comments: one or two short lines, only the why. Files under ~900 lines. No dead code.
- Headless Blender only with isolated `BLENDER_USER_CONFIG/SCRIPTS/EXTENSIONS`; never the user's Blender, never the app on 24320; test instance is 24322.
- Plugin test command (from `fpfork`), `TEST` the file name:
  `python -c "import sys; sys.path.insert(0, r'C:\Users\kyooc\Documents\Claude\fpfork-private\devtools'); import baseline; baseline.copy_plugin(r'C:\Users\kyooc\Documents\Claude\fpfork')"` then
  `BLENDER_USER_CONFIG=$SB/config BLENDER_USER_SCRIPTS=$SB/scripts BLENDER_USER_EXTENSIONS=$SB/ext "C:/Program Files (x86)/Steam/steamapps/common/Blender/blender.exe" -b --factory-startup --python-exit-code 1 -P tests/plugin/TEST -- "C:/Users/kyooc/Documents/Claude/fpfork-private/devtools/out/baseline-plugin"` (`$SB` a scratch folder).
- Never print secrets (AES keys, deviceAuth.json, `.data/islands.json`, AppSettingsV4.json); filter logs with `grep -viE "aes|key"`.
- Commits: short plain messages approved by the user; kyoocreatives noreply identity; no Co-Authored-By trailer.
- Rules written into the plugin come from the decoded rig (Appendix A of the spec), never guessed.

## Review Focus

1. An emote with no face curves at all (Floss) on a flipbook outfit: the face plays the idle (blinks), not a frozen or zeroed face.
2. A target armature without flipbook faces (normal outfit, LEGO figure): the flipbook path does nothing and the LEGO path is unchanged.
3. A face material with only some flipbook inputs (eyes only, mouth only): only those inputs are keyed, nothing raises.
4. Importing a second emote onto the same character: the first emote's face strips are gone, not stacked.
5. Looping sections (`repeat` 999) and a section longer than the idle: the idle loops; the face strip repeats with the body's.

---

### Task 1: Read Fortnite's Control Rig bytecode

**Files:**
- Create: `patches/CUE4Parse/CUE4Parse/UE4/Objects/RigVM/FRigVMByteCode.cs` (copy of the submodule file, fixed)
- Possibly create: other `patches/CUE4Parse/CUE4Parse/UE4/Objects/RigVM/*.cs` the fix needs (whole-file copies)

**Interfaces:**
- Produces: `fork-dump?path=/Game/Characters/Player/Common/Fortnite_Base_Head/Facials/ControlRigs/Fortnite_Flipbook2_Face_Mapping_CtrlRig&full=1` returns the class with its VM's `ByteCode` instructions, no "Could not read RigVMBlueprintGeneratedClass correctly" in the test log.

- [ ] **Step 1: Reproduce the failure (the test)**

```bash
cd /c/Users/kyooc/Documents/Claude/fpfork-private/devtools && export MSYS_NO_PATHCONV=1
python fpdev.py status
python fpdev.py dump /Game/Characters/Player/Common/Fortnite_Base_Head/Facials/ControlRigs/Fortnite_Flipbook2_Face_Mapping_CtrlRig --out /tmp/cr.json
grep -c '"OpCode"' /tmp/cr.json; grep -iE "RigVM" "$(ls -t "/c/Users/kyooc/AppData/Local/FortnitePorting MP test/Logs/"*.log | head -1)" | grep -viE "aes|key" | tail -3
```
Expected: 0 OpCode entries; log shows `FRigVMExecuteOp..ctor` at `FRigVMByteCode.cs:189`.

- [ ] **Step 2: Find the layout difference**

Read `external/CUE4Parse/CUE4Parse/UE4/Objects/RigVM/FRigVMByteCode.cs` (`ReadRigVMInstruction`, `FRigVMExecuteOp`) and `URigVM.cs` against UE 5.6/5.7's `RigVMByteCode.cpp` (`FRigVMByteCode::Serialize`, `FRigVMExecuteOp` layout; Fortnite builds may write the 5.5+ form where execute ops store `FunctionIndex` and operand counts differently, or the whole byte code as a raw buffer with `ByteCodeVersion`). Log the archive position and the first opcodes (temporary `Console.WriteLine` in the submodule copy, removed before commit) to see where the read goes wrong. Record the finding in the ledger.

- [ ] **Step 3: Fix in the patch copy**

Copy the file(s) to `patches/CUE4Parse/...` and change the read there to match the layout found in Step 2 (version-gated on `FRigVMObjectVersion`/`FUE5ReleaseStreamObjectVersion` as the surrounding code does, so older games still read). Copy the patch over the submodule for the build:
```bash
cd /c/Users/kyooc/Documents/Claude/fpfork && cp -r patches/CUE4Parse/. external/CUE4Parse/
```

- [ ] **Step 4: Build and re-run the test**

```bash
cd /c/Users/kyooc/Documents/Claude/fpfork-private/devtools && bash testapp.sh test
python fpdev.py dump /Game/Characters/Player/Common/Fortnite_Base_Head/Facials/ControlRigs/Fortnite_Flipbook2_Face_Mapping_CtrlRig --out /tmp/cr.json
grep -c '"OpCode"' /tmp/cr.json
```
Expected: build OK; OpCode count > 100; no RigVM error in the new log. Also dump one other Control Rig the old code read (any `*_CtrlRig` from `fpdev.py find CtrlRig --count 5`) to check it still reads.

- [ ] **Step 5: Commit**

```bash
cd /c/Users/kyooc/Documents/Claude/fpfork && git add patches/CUE4Parse/CUE4Parse/UE4/Objects/RigVM/
git commit -m "Read Fortnite's Control Rig bytecode"
```

### Task 2: Decode the flipbook rig and the idle blend

**Files:**
- Create: `fpfork-private/devtools/rigvm_dump.py` (owner-only tool)
- Modify: `docs/superpowers/specs/2026-10-09-flipbook-emote-faces-design.md` (append Appendix A and B)

**Interfaces:**
- Consumes: Task 1's dump.
- Produces: Appendix A — the rule table Task 4 ports and tests; Appendix B — the blend Task 5 ports.

- [ ] **Step 1: Write the dump reader**

`rigvm_dump.py PATH` fetches `fork-dump?full=1` from 24322 and prints the byte code as readable lines: instruction index, opcode, function name (from the VM's function name table), each operand resolved to its register name and, for literals, its default value; jumps with their targets; `Execute` entries per event (`Forwards Solve`). Shape:

```python
"""Readable RigVM byte code of a Control Rig class. usage: python rigvm_dump.py /Game/...CtrlRig"""
import json, sys, urllib.parse, urllib.request

def dump(path):
    url = "http://localhost:24322/fork-dump?" + urllib.parse.urlencode({"path": path, "full": "1"}, safe="/")
    return json.loads(urllib.request.urlopen(url, timeout=600).read())

def main(path):
    exports = dump(path)
    vm = next(e for e in exports if "ByteCode" in json.dumps(e)[:200000])
    # registers: literal and work memory names/defaults; functions: the VM's function names
    ...  # resolve per the JSON shape Task 1's dump shows; print one line per instruction
```
(The `...` is resolved against the real JSON shape seen in Task 1, Step 4 — the shape isn't known before the read works; ledger the shape.)

- [ ] **Step 2: Decode the rig into Appendix A**

Run `python rigvm_dump.py /Game/Characters/Player/Common/Fortnite_Base_Head/Facials/ControlRigs/Fortnite_Flipbook2_Face_Mapping_CtrlRig > out/flipbook2.txt` and read it through. Append to the spec:

```markdown
## Appendix A: Flipbook2 rules (decoded from the rig's byte code)

Per output (flipbook_face_L_brow_index, ..._R_brow_index, ..._L_eye_index, ..._R_eye_index, ..._mouth_index):
| Order | Condition (curve, comparison, threshold) | Index | Notes (wins over / state) |
|---|---|---|---|
| 1 | ... | ... | ... |
Default (no condition): index ...
State carried between frames: (Prev_*_Index, Prev Blink Pose, Blink Pose Threshold 0.025): exact rule.
Enable_Brow/Eye/Mouth: what each turns off.
```
Every row cites the instruction range it comes from. Cross-check against what is already known: threshold 0.25 default, `R_brow_down` 0.4, `jaw_open_pose >= 0.5`, brows down 4 / glabella down 3 / glabella up 2 / brow up 1, eye wide 7, look left/right 5/6, mouth narrow 14 / wide 13 / R raiser+lower 12 / L 11 / both 10 / frown 9.

- [ ] **Step 3: Decode the idle blend into Appendix B**

Dump the base head AnimBP (`/Game/Characters/Player/Common/Fortnite_Base_Head/Fortnite_Base_Head_Export_Skeleton_AnimBP`, `fpdev.py dump ... --grep BlendFacialCurves`) and read `FortAnimNode_BlendFacialCurves`' properties (which curves, blend mode, weight, how `disablefaceoverride` / `flipbook_face_is_idling` act). Append:

```markdown
## Appendix B: idle under the emote
Idle anim: /Game/Animation/Game/MainPlayer/Menu/FACIAL/DefaultFaceIdle_Legacy
Curves taken from the idle: ...
Per curve, value = ... (emote, idle, weight)
disablefaceoverride: ...
```
Where a property is native-only and not in the dump, write the ruling used and its cost if wrong.

- [ ] **Step 4: Commit**

```bash
cd /c/Users/kyooc/Documents/Claude/fpfork && git add docs/superpowers/specs/2026-10-09-flipbook-emote-faces-design.md
git commit -m "Decode the flipbook face rig"
cd ../fpfork-private && git add devtools/rigvm_dump.py && git -c user.name=kyoocreatives -c user.email=280023218+kyoocreatives@users.noreply.github.com commit -m "Add a RigVM byte code reader"
```

### Task 3: Emote payload — curve modes and the face idle

**Files:**
- Modify: `src/FortnitePorting.Exporting/Types/AnimExport.cs:89` (one-line hook)
- Modify: `src/FortnitePorting.Exporting/Types/AnimExport.MaterialPorter.cs`

**Interfaces:**
- Produces: emote payload: each `Sections[i].MPCurveModes` (dict curve → letters) for `Emote` and `LegoEmote`; top-level `MPFaceIdle`: `{Path, Name, Length, Time: 0, MPCurveModes}` (an `ExportAnimSection`) for `Emote` exports, or null when the idle isn't found.

- [ ] **Step 1: The test — export Facepalm, check the payload**

```bash
cd /c/Users/kyooc/Documents/Claude/fpfork-private/devtools && export MSYS_NO_PATHCONV=1
python fpdev.py find EID_Facepalm --count 3
python fpdev.py export facepalm --type Emote --path <the EID_Facepalm path found>
python -c "import json; p=json.load(open('out/fpdev/facepalm/payload.json')); e=p['Exports'][0] if 'Exports' in p else p; print(bool(e['Sections'][0].get('MPCurveModes')), (e.get('MPFaceIdle') or {}).get('Path'))"
```
Expected now: `False None`. (Adjust the key walk to the payload's real nesting on first run; ledger it.)

- [ ] **Step 2: Implement**

`AnimExport.cs`: remove the line-89 hook (`if (exportType is EExportType.LegoEmote) ReadFaceCurveModes();     // MP`) and put one line after the switch, beside `ReadSequenceEffects(asset);     // MP`, so the styles branch (a style's montage) gets curve modes too:
```csharp
        if (exportType is EExportType.LegoEmote or EExportType.Emote) ReadFaceCurveModes();     // MP
```

`AnimExport.MaterialPorter.cs`:
```csharp
    // a flipbook face plays this under an emote's own face curves (the head AnimBP blends it in)
    private const string FaceIdlePath = "FortniteGame/Content/Animation/Game/MainPlayer/Menu/FACIAL/DefaultFaceIdle_Legacy";
    public ExportAnimSection? MPFaceIdle;

    // an emote animates the face material with curves (LEGO directly, flipbook faces through the idle); keep their key interpolation
    private void ReadFaceCurveModes()
    {
        foreach (var section in Sections)
            section.MPCurveModes = CurveModes(section.AssetRef);
        if (Type is not EExportType.Emote) return;
        if (!Context.Meta.Provider.Provider.TryLoadPackageObject<UAnimSequence>(FaceIdlePath, out var idle)) return;
        MPFaceIdle = Context.AnimSequence(idle);
        if (MPFaceIdle is not null) MPFaceIdle.MPCurveModes = CurveModes(idle);
    }
```
(`Type` is `BaseExport`'s field. Update the `ExportAnimSection.MPCurveModes` comment: "an emote section's curve key interpolation".)

- [ ] **Step 3: Build, re-run the test**

```bash
bash testapp.sh test
python fpdev.py export facepalm --type Emote --path <same>
python -c "<same check>"
```
Expected: `True` and a path ending `DefaultFaceIdle_Legacy...`. Also export a LEGO emote used before (`fpdev.py find EID --class` per the LEGO plan's asset) and check its sections still have `MPCurveModes` and no `MPFaceIdle`.

- [ ] **Step 4: Commit**

```bash
cd /c/Users/kyooc/Documents/Claude/fpfork && git add src/FortnitePorting.Exporting/Types/AnimExport.cs src/FortnitePorting.Exporting/Types/AnimExport.MaterialPorter.cs src/FortnitePorting.Exporting/Models/ExportAnimation.cs
git commit -m "Send emote face curves and the face idle"
```

### Task 4: The flipbook rules in Python

**Files:**
- Create: `plugins/Blender/fortnite_porting/material_porter/flipbook_face.py`
- Create: `tests/plugin/flipbook_face_check.py`

**Interfaces:**
- Consumes: Appendix A.
- Produces:
  - `OUTPUTS = ("flipbook_face_l_brow_index", "flipbook_face_r_brow_index", "flipbook_face_l_eye_index", "flipbook_face_r_eye_index", "flipbook_face_mouth_index")` (lower case).
  - `class Rules` with `step(values: dict[str, float]) -> dict[str, int]` (lower-case curve names in; one index per OUTPUT out; carries Appendix A's frame-to-frame state on the instance). A new `Rules()` per section.

- [ ] **Step 1: Write the failing tests**

`tests/plugin/flipbook_face_check.py`, the suite's shape (same as `rig_check.py`):
```python
"""flipbook_face: the Flipbook2 rig's rules (spec Appendix A) and emote face keys. Headless, isolated Blender."""
import sys
import bpy  # noqa: F401
sys.path.insert(0, sys.argv[sys.argv.index("--") + 1])
from fpmp_baseline.material_porter import flipbook_face  # noqa: E402

failed, passed = [], 0
def check(name, got, want):
    global passed
    if got == want:
        passed += 1
    else:
        failed.append(name)
        print("[flipbook_face_check] FAIL %s: got %r, want %r" % (name, got, want))

def first(values):
    return flipbook_face.Rules().step(values)

rest = first({})
check("rest pose", rest, {k: rest[k] for k in flipbook_face.OUTPUTS})
check("left brow down", first({"l_brow_down_pose": 1.0})["flipbook_face_l_brow_index"], 4)
check("below threshold stays at rest", first({"l_brow_down_pose": 0.2})["flipbook_face_l_brow_index"], rest["flipbook_face_l_brow_index"])
check("eye wide", first({"l_eye_wide_pose": 1.0})["flipbook_face_l_eye_index"], 7)
check("mouth narrow", first({"l_lip_corner_narrow_pose": 1.0, "r_lip_corner_narrow_pose": 1.0})["flipbook_face_mouth_index"], 14)
# one check per remaining Appendix A row, each named after the row, plus every tie the appendix records,
# and the carried state (e.g. a blink held across frames by Blink Pose Threshold), using exact curve names from the appendix
print("[flipbook_face_check] %d passed, %d failed" % (passed, len(failed)))
sys.exit(1 if failed else 0)
```
The five concrete checks above use the already-recovered rows; their curve names are corrected to Appendix A's exact names if they differ (ledger). Then add the remaining rows as checks, one `check` per row, written out in full.

- [ ] **Step 2: Run, watch it fail**

Run the plugin test command with `TEST=flipbook_face_check.py`.
Expected: `ModuleNotFoundError` / `ImportError` for `flipbook_face`.

- [ ] **Step 3: Implement the rules**

```python
"""A flipbook face's frames from Fortnite's facial curves, as the head's Control Rig picks them
(Fortnite_Flipbook2_Face_Mapping_CtrlRig; rules decoded in the spec's Appendix A)."""

OUTPUTS = ("flipbook_face_l_brow_index", "flipbook_face_r_brow_index", "flipbook_face_l_eye_index",
           "flipbook_face_r_eye_index", "flipbook_face_mouth_index")
THRESHOLD = 0.25


class Rules:
    """One section's frames in order; keeps what the rig carries from frame to frame."""

    def __init__(self):
        self.previous = {}

    def step(self, values):
        on = lambda name, at=THRESHOLD: values.get(name, 0.0) >= at
        out = {
            "flipbook_face_l_brow_index": self._brow(on, "l"),
            "flipbook_face_r_brow_index": self._brow(on, "r"),
            "flipbook_face_l_eye_index": self._eye(values, on, "l"),
            "flipbook_face_r_eye_index": self._eye(values, on, "r"),
            "flipbook_face_mouth_index": self._mouth(values, on),
        }
        self.previous = out
        return out
```
with `_brow`, `_eye`, `_mouth` written row by row from Appendix A in the appendix's order and tie rule (the order the rig executes; if the rig lets the last match win, test in reverse and return the first hit — say which in a one-line comment).

- [ ] **Step 4: Run, watch it pass**

Same command. Expected: `[flipbook_face_check] N passed, 0 failed`, N = rows + ties + state checks + 2.

- [ ] **Step 5: Commit**

```bash
git add plugins/Blender/fortnite_porting/material_porter/flipbook_face.py tests/plugin/flipbook_face_check.py
git commit -m "Port the flipbook face rules"
```

### Task 5: Key an emote's face, idle underneath

**Files:**
- Modify: `plugins/Blender/fortnite_porting/material_porter/flipbook_face.py`
- Modify: `plugins/Blender/fortnite_porting/material_porter/face_anim.py` (`face_materials` → `face_board.faces`; `_keys`, `_fcurve`, `clear`, `TRACK` reused)
- Modify: `plugins/Blender/fortnite_porting/processing/context/anim_context.py` (MP hook beside the LEGO one)
- Test: `tests/plugin/flipbook_face_check.py`

**Interfaces:**
- Consumes: `Rules`, `OUTPUTS` (Task 4); payload `MPFaceIdle` (Task 3); Appendix B; `face_board.faces(armature) -> [(material, {lower-case input: socket})]` and `face_board.kind(found)` (existing; flipbook kind has `"flipbook_face_mouth_index"` among its slider keys); `face_anim.clear(armature)`, `face_anim._keys(fc, points, interpolations)`, `face_anim._fcurve(action, tree, socket)`, `face_anim._evaluate(keys, modes, name, frame)`, `face_anim.TRACK`.
- Produces: `flipbook_face.apply(armature, sections, idle) -> int` — `sections` as `face_anim.apply`'s (`name`, `dto`, `frame`, `repeat`, `range`, `modes`); `idle` a dict `{"dto": AnimDto, "modes": dict}` or None. Returns face materials animated. `flipbook_face.has_faces(armature) -> bool`.

- [ ] **Step 1: Write the failing tests**

Append to `flipbook_face_check.py` (before the summary). Fake DTOs keep it headless without .ueanim files:
```python
from types import SimpleNamespace as NS
from fpmp_baseline.processing.context import face_board  # noqa: E402

def key(frame, value):
    return NS(frame=frame, value=value)

def curve(name, *keys):
    return NS(name=name, keys=list(keys))

def flip_armature(name, inputs):
    """An armature with a head mesh whose material's group node has the given value inputs."""
    arm = bpy.data.objects.new(name, bpy.data.armatures.new(name))
    bpy.context.scene.collection.objects.link(arm)
    me = bpy.data.meshes.new(name + "_head")
    me.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    head = bpy.data.objects.new(name + "_head", me)
    bpy.context.scene.collection.objects.link(head)
    head.parent = arm
    mat = bpy.data.materials.new(name + "_face")
    mat.use_nodes = True
    group = bpy.data.node_groups.new(name + "_group", "ShaderNodeTree")
    for n, v in inputs.items():
        group.interface.new_socket(n, in_out="INPUT", socket_type="NodeSocketFloat")
    node = mat.node_tree.nodes.new("ShaderNodeGroup")
    node.node_tree = group
    for n, v in inputs.items():
        node.inputs[n].default_value = v
    me.materials.append(mat)
    return arm, mat, node

FB = {"flipbook_face_L_eye_index": 0.0, "flipbook_face_R_eye_index": 0.0, "flipbook_face_mouth_index": 0.0,
      "FB_EyeColumnCount": 3.0, "FB_EyeRowCount": 3.0, "FB_MouthColumnCount": 4.0, "FB_MouthRowCount": 4.0}
arm, mat, node = flip_armature("fb_emote", FB)
check("has flipbook faces", flipbook_face.has_faces(arm), True)
section = {"name": "Emote", "frame": 1, "repeat": 1, "range": (0, 10), "modes": {},
           "dto": NS(curves=[curve("l_eye_wide_pose", key(0, 0.0), key(5, 1.0), key(10, 1.0))])}
idle = {"dto": NS(curves=[curve("l_blink_pose", key(0, 0.0), key(2, 1.0), key(3, 0.0))]), "modes": {}}
check("one face animated", flipbook_face.apply(arm, [section], idle), 1)
ad = mat.node_tree.animation_data
track = ad.nla_tracks.get("MP Face")
check("strip on the MP Face track", [s.frame_start for s in track.strips], [1.0])
fc = track.strips[0].action.fcurves  # Blender 5 layered actions: use the channelbag's fcurves if this attribute is absent
eye = next(f for f in fc if 'flipbook_face_L_eye_index' in f.data_path)
check("eye keys constant", {k.interpolation for k in eye.keyframe_points}, {'CONSTANT'})
check("eye wide once the curve is on", eye.evaluate(6), 7.0)
check("mouth keyed too (rest)", any('flipbook_face_mouth_index' in f.data_path for f in fc), True)
check("no brow input, no brow keys", any('brow' in f.data_path for f in fc), False)
# idle only: an emote section without face curves still blinks (Appendix B decides the blink frame value)
section2 = dict(section, name="Floss", dto=NS(curves=[]))
flipbook_face.apply(arm, [section2], idle)
strips = ad.nla_tracks.get("MP Face").strips
check("second emote replaces the first", [s.name for s in strips], ["Floss"])
blink = next(f for f in strips[0].action.fcurves if 'flipbook_face_L_eye_index' in f.data_path)
check("idle blink shows", blink.evaluate(2) != blink.evaluate(0), True)
# repeat and a section longer than the idle: the idle loops
long = dict(section2, name="Long", range=(0, 30), repeat=999)
flipbook_face.apply(arm, [long], idle)
s = ad.nla_tracks.get("MP Face").strips[0]
check("face repeats with the body", s.repeat, 999)
lb = next(f for f in s.action.fcurves if 'flipbook_face_L_eye_index' in f.data_path)
check("idle loops past its length", lb.evaluate(12) == lb.evaluate(2), True)
# the face board steps aside
check("board off", arm.get("fpmp_face_board", False), False)
# a plain outfit: nothing
plain, _, _ = flip_armature("plain", {"Roughness": 0.5})
check("no flipbook face, nothing", (flipbook_face.has_faces(plain), flipbook_face.apply(plain, [section], idle)), (False, 0))
```
Adapt the `fcurves` access to Blender 5.2's layered actions the way `face_anim` creates them (`fcurve_ensure_for_datablock`): read back through `action.layers[0].strips[0].channelbags[0].fcurves` if `action.fcurves` is absent — put a `fcurves(action)` helper at the top of the test file. "eye wide once the curve is on" and "idle blink shows" take their exact values from Appendix A/B (ledger if the appendix changes them). "board off" checks `fpmp_face_board` is False after apply on an armature whose `fpmp_face_board_materials` is set: set `arm["fpmp_face_board_materials"] = [mat.name]` before the first apply.

- [ ] **Step 2: Run, watch it fail**

Expected: `AttributeError: module ... has no attribute 'has_faces'`.

- [ ] **Step 3: Implement**

In `face_anim.py`, `face_materials` becomes:
```python
def face_materials(armature):
    """The exact face materials of the armature's meshes: (material, {lower-case parameter: input})."""
    from ..processing.context import face_board
    return [(mat, inputs) for mat, inputs in face_board.faces(armature) if face_board.kind([(mat, inputs)])["name"] == "lego"]
```
(Use the kind table's real identifying key — check `face_board.KINDS`; ledger the key used. LEGO emotes must keep finding only LEGO faces.)

In `flipbook_face.py`:
```python
import bpy

from . import face_anim

def _faces(armature):
    from ..processing.context import face_board
    return [(m, i) for m, i in face_board.faces(armature) if any(o in i for o in OUTPUTS)]


def has_faces(armature):
    return bool(_faces(armature))


def _values(curves, modes, frame):
    return {name: face_anim._evaluate(c.keys, modes, name, frame) for name, c in curves.items() if c.keys}


def apply(armature, sections, idle):
    """Key the flipbook index inputs per frame of each section, the idle under the emote (spec Appendix B).
    Returns the number of face materials animated."""
    faces = _faces(armature)
    if not faces:
        return 0
    face_anim.clear(armature)
    if armature.get("fpmp_face_board_materials"):
        armature.fpmp_face_board = False        # the emote's face plays, not the board
    idle_curves = {c.name.lower(): c for c in idle["dto"].curves} if idle else {}
    idle_modes = {k.lower(): v for k, v in ((idle or {}).get("modes") or {}).items()}
    idle_length = max((k.frame for c in idle_curves.values() for k in c.keys), default=0)
    for mat, inputs in faces:
        tree = mat.node_tree
        ad = tree.animation_data or tree.animation_data_create()
        track = None
        for sec in sections:
            curves = {c.name.lower(): c for c in sec["dto"].curves}
            modes = {k.lower(): v for k, v in (sec.get("modes") or {}).items()}
            start, end = int(sec["range"][0]), int(sec["range"][1])
            rules, points = Rules(), {o: [] for o in OUTPUTS if o in inputs}
            for f in range(start, end + 1):
                under = _values(idle_curves, idle_modes, (f - start) % idle_length if idle_length else 0)
                values = blend(under, _values(curves, modes, f))
                for o, index in rules.step(values).items():
                    if o in points:
                        points[o].append((f, float(index)))
            action = bpy.data.actions.new("%s (%s)" % (sec["name"], mat.name))
            ad.action = action
            for o, pts in points.items():
                face_anim._keys(face_anim._fcurve(action, tree, inputs[o]), pts, ['CONSTANT'] * len(pts))
            action.use_frame_range = True
            action.frame_start, action.frame_end = sec["range"]
            ad.action = None
            if track is None:
                track = ad.nla_tracks.new(prev=None)
                track.name = face_anim.TRACK
            strip = track.strips.new(sec["name"], int(sec["frame"]), action)
            strip.repeat = sec["repeat"]
    return len(faces)
```
and `blend(idle_values, emote_values) -> dict` written from Appendix B (e.g. emote overrides per curve, or weighted; `disablefaceoverride` as decoded), with its own checks added to Step 1's tests (one per Appendix B rule) before writing it.

In `anim_context.py`, next to the LEGO hook, after `face_sections` are filled:
```python
        if self.type == EExportType.EMOTE:     # MP: a flipbook face plays the emote's face curves
            from ...material_porter import flipbook_face; flipbook_face.import_emote(self, target_skeleton, face_sections, data.get("MPFaceIdle"))
```
and in `flipbook_face.py`:
```python
def import_emote(anim_context, armature, sections, idle_section):
    """The emote import's hook: never fails the import."""
    from ..logger import Log
    try:
        if not has_faces(armature):
            return
        idle = None
        if idle_section:
            action, dto = anim_context.import_anim(idle_section["Path"], armature)
            idle = {"dto": dto, "modes": idle_section.get("MPCurveModes")}
            if action.users == 0:
                bpy.data.actions.remove(action)
        Log.info("[Material Porter] %d flipbook face(s) animated by the emote" % apply(armature, sections, idle))
    except Exception as e:
        Log.error("[Material Porter] the emote's flipbook face failed: %s: %s" % (type(e).__name__, e))
```
`face_sections` is filled for every import (it already is: `is_main_skeleton`). Check `EExportType.EMOTE` is the plugin enum's name (`processing/enums.py`).

- [ ] **Step 4: Run, watch it pass; run every suite**

`flipbook_face_check.py`, `rig_check.py`, `translator_test.py`, `layout_check.py`, `subsurface_check.py` with the plugin test command.
Expected: all `0 failed`. Add one test for `import_emote` raising inside (`apply` patched to `1/0`): it logs and returns, the call doesn't raise.

- [ ] **Step 5: Commit**

```bash
git add plugins/Blender/fortnite_porting/material_porter/flipbook_face.py plugins/Blender/fortnite_porting/material_porter/face_anim.py plugins/Blender/fortnite_porting/processing/context/anim_context.py tests/plugin/flipbook_face_check.py
git commit -m "Play emote faces on flipbook characters"
```

### Task 6: Real assets

**Files:**
- Create: `fpfork-private/devtools/scripts/emote_onto.py` (an fpdev script: import an emote payload onto the saved character .blend's armature)

**Interfaces:**
- Consumes: everything above; `fpdev.py export/import/script/render`.

- [ ] **Step 1: The script**

```python
"""fpdev script: import EMOTE_PAYLOAD onto the first armature of the open .blend, save it.
usage: fpdev.py script NAME emote_onto.py PAYLOAD"""
import sys
import bpy
from fpmp_baseline.processing.importer import Importer

payload = sys.argv[-1]
arm = next(o for o in bpy.data.objects if o.type == 'ARMATURE' and o.data.get("is_tasty"))
for o in bpy.context.selected_objects:
    o.select_set(False)
arm.select_set(True)
bpy.context.view_layer.objects.active = arm
Importer.Import(open(payload, encoding="utf-8").read())
bpy.ops.wm.save_mainfile()
```
(Check how `fpdev.py script` passes args and imports the plugin copy; adapt and ledger.)

- [ ] **Step 2: VerTet + Facepalm, VerTet + Floss**

```bash
cd /c/Users/kyooc/Documents/Claude/fpfork-private/devtools && export MSYS_NO_PATHCONV=1
python fpdev.py export vertet3 --type Outfit --path <Character_VerTet path> --option RigType=1   # Tasty, as vertet2
python fpdev.py import vertet3
python fpdev.py export facepalm --type Emote --path <EID_Facepalm path>
python fpdev.py script vertet3 scripts/emote_onto.py out/fpdev/facepalm/payload.json
```
Expected: log line "1 flipbook face(s) animated by the emote" (or the face count); repeat with Floss into `vertet4`.

- [ ] **Step 3: Renders**

Pick frames from Appendix A's rules applied to Facepalm's curves: one where a blink or `jaw_open_pose >= 0.5` is on, one at rest. `python fpdev.py render vertet3 --frame F --word face` for each; compose a side-by-side. Check by eye that the rendered eye/mouth frame is the one the rule picks (the atlas cell at that index). Floss: a frame inside an idle blink shows closed eyes.

- [ ] **Step 4: Commit the script**

```bash
cd /c/Users/kyooc/Documents/Claude/fpfork-private && git add devtools/scripts/emote_onto.py && git -c user.name=kyoocreatives -c user.email=280023218+kyoocreatives@users.noreply.github.com commit -m "Add an fpdev emote import script"
```
