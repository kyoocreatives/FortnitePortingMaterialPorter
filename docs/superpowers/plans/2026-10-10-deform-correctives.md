# Deform Correctives Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fortnite's `deform_*` helper bones follow the game's corrective logic in Blender, per character, under any rig and animation.

**Architecture:** CUE4Parse reads every RigVM literal; the app writes each character's deform Control Rig dump next to the body; the plugin reads it (or falls back to Epic's library defaults shipped as `deform_rig.json`) and builds constraints and simple-expression drivers: base bones blend two sources through followers, weights come from reader bones in the game's frame (joint-angle remap or spherical pose reader), correctives slide/scale additively.

**Tech Stack:** C# (.NET 10, CUE4Parse patches, FortnitePorting.Exporting); Python 3 devtools decoder; Blender 5.2 plugin (bpy, mathutils, stdlib); headless isolated tests.

**Spec:** `docs/superpowers/specs/2026-10-10-deform-correctives-design.md`

## Global Constraints

- Upstream files: one-line `// MP` / `# MP` hooks only; fork code in fork files; CUE4Parse changes as whole-file patches under `patches/CUE4Parse/` copied over the submodule (`cp -r patches/CUE4Parse/. external/CUE4Parse/`), never staging `external/CUE4Parse`.
- Drivers: simple expressions (`exp`, `atan2`, `acos`, `sqrt`, `clamp`, `min`, `max`, conditionals are simple), at most 255 characters, a term's sign leads it, staged through helper properties when longer.
- New edit bones placed by head/tail/`align_roll`; pose visibility via `PoseBone.hide`; Python property sets followed by `obj.update_tag()`.
- Rest stays rest: building the correctives leaves every bone where it was (< 1e-4 m, < 1e-3 rad).
- The spherical pose reader's math lives in one function pair (Python reference + driver builder), marked as Epic's documented semantics (exact source unverified).
- Commits: one per repo at the end, message approved by the user; kyoocreatives noreply identity; no Co-Authored-By.
- Tests: `bash <scratchpad>/rigcheck.sh deform_rig_check.py` (new) and the existing suites; app build `bash fpfork-private/devtools/testapp.sh test`; real asset via `fpdev.py`.

## Review Focus

1. A character without a CtrlRig, or whose rig doesn't call FortDeformRig: library defaults, one log line (Task 4/6 tests).
2. A skeleton with only some deform bones (no bicep/lat, one side missing): built parts work, missing ones skipped (Task 5 test).
3. Correctives under an imported animation and under the official rig: the base bones follow the animated limb bones (Task 6 test with keyed limb bones).
4. Extreme poses (elbow fully bent past 90, arm straight up past the cone): weights clamp to [0, 1], no flips (Task 5 test sampling a wide range).
5. Re-running the hook on the same armature: nothing duplicated (Task 6 test).

---

### Task 1: CUE4Parse reads every RigVM literal

**Files:** Modify `patches/CUE4Parse/CUE4Parse/UE4/Objects/RigVM/FRigVMMemoryStorageStruct.cs` (copy to the submodule after editing).

- [ ] **Step 1: Failing check.** `fpfork-private/devtools/helpers/literals_check.py`: GET `http://localhost:24322/fork-dump?path=<VibrantShell CtrlRig>&full=1`, count the literal values present in the RigVM's literal memory (`LiteralMemoryStorage.Values` non-null entries) against the property descriptions; print `LITERALS read N of M`; assert `N == M` and that a Value struct carries `elbow_in` translation `(0, 9, 0)` (the library default the probe matched).
- [ ] **Step 2: Run it on the current test build:** expect `read 4 of 206`, assertion fails.
- [ ] **Step 3: Implement** in `ReadItem`: `EPropertyBagPropertyType.Struct` → `new FStructFallback(Ar, desc.ValueTypeObject?.Name)` for non-native structs, and the native ones read as their CUE4Parse types (`FVector`, `FRotator`, `FQuat`, `FTransform` via `new FTransform(Ar)`, `FVector2D`, `FLinearColor`) keyed by `desc.ValueTypeObject.Name`; `Enum` → the enum value name from `desc.ValueTypeObject` (fallback: the byte); `Object`/`SoftObject`/`Class` → the package index / soft path text; `String`/`Text` → string. Keep `RigElementKey` as today.
- [ ] **Step 4:** copy patches over the submodule, `bash testapp.sh test`, rerun the check: `read 206 of 206`, assertion passes. Also rerun `fpfork-private/devtools/official_rig_decode.py check` and `metahuman_board_decode.py check` (they read RigVM literals too): still `ok`.

### Task 2: Library data `deform_rig.json`

**Files (fpfork-private):** `devtools/deform_rig_decode.py` (from `helpers/spec.py` + `semantic.py`); output `plugins/Blender/fortnite_porting/processing/context/deform_rig.json`.

- [ ] **Step 1: `check()` first:** the JSON has `parts` (per corrective: `bone`, `base`, `sources` [[bone, weight]×2], `value` field name, `mirror`, `weight` {`type`: `angle` (bone, channel Yaw|Pitch, from, to) | `spr` (driver, parent, axis, offset_pos, offset_neg, region, falloff, region_scale [pw, nw, ph, nh], falloff_scale [...], flip_width, flip_height)}; pec extras `aim`), `values` (library defaults per field: `t` cm, `s`), `switches` (part on/off defaults); every `bone`/`base`/`source` is in Epic's mannequin skeleton names; 30 correctives.
- [ ] **Step 2:** run, expect missing file. **Step 3:** implement `build()` from the master rig's dump (already decoded by the probe: reuse `helpers/semantic.py` operations and `deform_spec.json`) adding the SPR falloff scale factors and flip flags per bone. **Step 4:** `build` then `check` → `ok: 30 parts`.

### Task 3: App writes the character's deform rig

**Files:** Create `src/FortnitePorting.Exporting/MaterialPorter/ExportContext.DeformRig.cs`; Modify `src/FortnitePorting.Exporting/Models/ExportMeta.cs` (`BaseMeta`: `public string? DeformRig;     // MP: the character's deform Control Rig dump (deform_rig)`); Modify `ExportContext.Fortnite.cs` (inside the `AdditionalData` block, after the switch: `exportPart.Meta.DeformRig = DeformRig(additionalData);     // MP`).

- [ ] **Step 1:** `DeformRig(UObject additionalData) -> string?`: `AnimClass` → its class default object → the `AnimGraphNode_ControlRig` struct (any property named `AnimGraphNode_ControlRig*`) → `ControlRigClass` → its package; serialize the package's exports exactly as `fork-dump` with `full=1` does (same `JsonSerializerSettings`) to `GetExportPath(rigClass, "deformrig.json")` once; return the path. No AnimClass / no Control Rig node: null.
- [ ] **Step 2:** build the test app; export VibrantShell (`fpdev.py export mh_vs --type Outfit --path .../Character_VibrantShell --option RigType=2`); expect a body part meta with `DeformRig` ending `F_MED_VibrantShell_CtrlRig.deformrig.json`, the file present and parseable, `literals_check.py` logic passing on it.

### Task 4: Plugin reader `deform_rig_read.py`

**Files:** Create `plugins/.../processing/context/deform_rig_read.py`; Create `tests/plugin/deform_rig_check.py` (+ a tiny synthetic dump fixture built in the test).

**Interfaces — Produces:** `read(path) -> {"values": {field: {"t": [3], "s": [3]}}, "switches": {part: bool}, "extras": [op...]}`; `library() -> dict` (deform_rig.json); `tuning(path_or_None) -> dict` (read, else library values/switches; logs once on failure).

- [ ] **Step 1: Failing tests:** a synthetic dump with a byte code calling `FortDeformRig` whose Bool/Value literals set `elbow` off and `elbow_in.t = (0, 7, 0)`: `read()` returns them; a dump without the call → `tuning()` returns library values with one printed line; a missing path → library values.
- [ ] **Step 2:** run, expect ImportError. **Step 3:** port the probe's decoder (`helpers/disasm.py` operand reading, `semantic.py` call recognition) to read the call's operands and resolve their literals; extras: SPR / ModifyTransforms / constraint / GetAlphaFromQuatDelta / math ops as dicts. **Step 4:** run, pass. Real file check (devtools): VibrantShell's dump → values equal the library defaults (the probe's finding), switches all on, extras 2 constraints + 7 SPR + 9 ModifyTransforms.

### Task 5: Builder `deform_rig.py`

**Files:** Create `plugins/.../processing/context/deform_rig.py`; tests in `deform_rig_check.py`.

**Interfaces — Produces:** `build(obj, lib, tuning) -> int` (correctives built); `set_on(obj, on)`; `spr_weight(dir_local, params) -> float` and `angle_weight(...)` pure-Python references (also used by tests); constants `READ = "DR_read_"`, `FOLLOW = "DR_follow_"`, `COPY = "DR copy"`.

- [ ] **Step 1: Failing tests** on a synthetic skeleton (clavicle, upperarm, lowerarm, hand, lowerarm_twist_01, thigh, calf, pelvis, spine_03/04 with every deform base/child pair; FP-like and child-pointing tails):
  - rest stays rest;
  - elbow bent 0, 15, 50, 90, 150 deg about the elbow hinge: `deform_elbow_in_l` translation in its game frame equals `w * V.t` with `w = clamp((yaw-15)/75, 0, 1)` (reference), scale `1 + w(V.s-1)`;
  - upper arm raised through a range: `deform_upperarm_up_l` matches `spr_weight` (reference) within 1e-3;
  - base bone = weighted average of its sources' motions (elbow_in base at 0.6 upperarm / 0.4 lowerarm);
  - right side mirrors the left (mirrored poses give mirrored corrective translations);
  - a skeleton missing `deform_glute_*` builds the rest;
  - `set_on(False)` returns the bones to their own keys;
  - every driver simple and ≤ 255 characters.
- [ ] **Step 2:** run, expect ImportError. **Step 3:** implement: followers per base source; reader bones per driver (game frame via `ue_rest`, parent = the driver's parent) copying the driver through a follower; weight drivers (angle: quaternion → yaw/pitch with `atan2`, remap, clamp; SPR: staged expressions computing the driver axis in the region frame, polar angle, azimuth quadrant factors, elliptical radii, smooth falloff); corrective drivers on location/scale with the axis change folded in; pec aim (Damped Track to upperarm, Locked Track to spine_04) and X-only follow. **Step 4:** run, pass; all suites green.

### Task 6: Extras, hook, panel

**Files:** `deform_rig.py`, `material_porter/mesh_hooks.py` (`after_parts`: `_correctives(ctx)` before the rigs), `processing/context/official_rig.py`/`operator/rig_ui.py` (panel toggle), tests.

- [ ] **Step 1: Failing tests:** extras: an SPR + ModifyTransforms extra from a synthetic tuning moves its bone by the reference; hook: an imported-animation armature (limb bones keyed) gets correctives following the keyed pose; a second hook call adds nothing; no deform bones → nothing; panel stand-in layout shows `Correctives`.
- [ ] **Step 2:** run, fail. **Step 3:** implement extras (supported ops → constraints/drivers in graph order), the hook (FaceDNA-style meta lookup of `DeformRig` across parts), `fpmp.deform_rig_on` operator and the panel row (official rig ui and the generic Rig panel). **Step 4:** run, pass; all suites green.

### Task 7: Real asset

- [ ] **Step 1:** devtools script `deform_rig_real.py`: on imported VibrantShell (with its rig dump), bend elbow/knee/shoulder/wrist/hip through ranges on both sides; compare every `deform_*` bone to the Python reference (worst error); check mesh motion between samples has no jumps (max vertex step bounded); if the imported FP emote animation keys `deform_*` bones, compare against them.
- [ ] **Step 2:** run; expected: reference match < 1e-3 m, smooth; report the SPR caveat.
- [ ] **Step 3:** commit (user-approved messages): fpfork (patch, C#, plugin, data, tests) and fpfork-private (decoder, scripts).
