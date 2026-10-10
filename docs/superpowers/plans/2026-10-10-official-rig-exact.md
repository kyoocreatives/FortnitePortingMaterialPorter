# Official Rig, Exact Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The official body rig turns on Epic's own control axes (left/right mirrored, feet and body world-aligned) and runs Epic's settings (stretch, softness, FK local, head local, foot roll, spine/head stretch, neck IK, body controls).

**Architecture:** The decoder emits each control's Epic frame (relative to its bone, or to the world for offset controls) plus the settings and their constants. The rig builds controls at those frames (right side as the Blender mirror of the left) and drives every bone through a hidden follower (a child of the control at the bone's rest), so frames never leak into bone motion. Settings become hidden properties driven from the panel; their effects are drivers and constraints.

**Tech Stack:** Python 3 decoder (fpfork-private/devtools); Blender 5.2 bpy plugin; headless isolated Blender tests.

**Spec:** `docs/superpowers/specs/2026-10-10-official-rig-exact-design.md`

## Global Constraints

- Upstream FP files: one-line `# MP` hooks only; fork code in fork files; files under ~900 lines (split `official_rig.py` if it passes).
- Comments: one or two short lines, only the why.
- Drivers: simple expressions only (`exp`, `clamp`, `min`, `max`, conditionals are simple; verified); at most 255 characters; a term's sign leads it.
- New edit bones: placed by head, tail and `align_roll` (never `.matrix` on a zero-length bone).
- Pose visibility uses `PoseBone.hide`.
- Rest stays rest: with every setting at its default, building the rig leaves every deform bone where it was (< 1e-3 m, < 1e-2 rad).
- Commits: one at the end with a message the user approves; kyoocreatives noreply identity; no Co-Authored-By; stage by name (never `external/CUE4Parse`, `.superpowers/`, out/).
- Tests: `bash <scratchpad>/rigcheck.sh official_rig_check.py` (and the other suites by name); real asset through `fpfork-private/devtools/fpdev.py`.

## Review Focus

1. A character whose skeleton lacks a bone some control or setting needs (no ball, no neck_02, no clavicle): that part is skipped, the rest builds (Task 2/3 tests with `mannequin(drop=...)`).
2. Switching FK→IK→FK on a posed limb after the frames change: nothing moves (Task 2 test reusing the posed-switch block).
3. Bake to Controls on an animation, then playing it: the bones land where the animation had them (existing bake block must stay green through followers).
4. Softness 0 with stretch off, at full reach: the limb doesn't pop or stretch (Task 3 test at exactly L).
5. A mirrored right control rotated by the user: the right limb moves as the mirror of the left (Task 2 test).

---

## File Structure

- Modify (fpfork-private): `devtools/official_rig_decode.py`: frames, mirror flags, types fix, `settings`, solve additions; regenerate `official_rig.json`.
- Modify: `plugins/Blender/fortnite_porting/processing/context/official_rig.py`: frames, followers, matching/bake through followers, settings. Split into `official_rig.py` (build, solve, switch, bake) and a new `official_rig_settings.py` (stretch/softness, local spaces, foot roll, spine/neck stretch, panel lines) when it grows past ~900 lines.
- Modify: `plugins/Blender/fortnite_porting/operator/rig_ui.py`: settings operators (`fpmp.official_rig_setting`), body controls toggle.
- Modify: `tests/plugin/official_rig_check.py`.
- Create (fpfork-private): `devtools/scripts/official_rig_exact_real.py`.

---

### Task 1: Decoder: frames, settings, solve constants

**Files:** Modify `fpfork-private/devtools/official_rig_decode.py`; regenerated `plugins/.../processing/context/official_rig.json`.

**Interfaces — Produces (JSON):**
- per control: `frame` = `[w, x, y, z]` Blender-axes quaternion: on-bone controls, the rotation from Epic's bone frame to Epic's control frame (`blender_quat(qmul(conj(G[bone].r), G[ctrl].r))`); offset controls, Epic's control world rotation (`blender_quat(G[ctrl].r)`); right-side controls (name has an `_r` token and a left twin): `mirror: true` and the left twin's `frame` and `shape_transform` (built mirrored in Blender).
- per control: `shape_transform` relative to the control's own frame (no `centre_turn`); `visible` (Epic's `bShapeVisible`).
- `TYPES = {0: "bool", 1: "float", 7: "transform", 9: "euler_transform"}`.
- `settings`: list of `{"name", "kind": "bool"|"float", "default", "range": [lo, hi] | null}` for the 22 setting controls, defaults from the probe (legs IK 1, arms/spine/neck 0, fk_local/head_local 1, roll_blend 1, stretch 0, softness 0..2 default 0, ShowBodyControls 1).
- `solve.stretch = {"start": 1.0, "max": 2.0}`, `solve.softness = 0.02`; per IK chain `stretch` and `softness` setting names.
- `solve.local = [{"space", "setting", "on": rotation source, "off": rotation source or null (the space's own parent)}]` for `upperarm_<s>_fk_ctrl_space` (on body_ctrl, off clavicle_<s>_ctrl, setting arm_<s>_fk_local), `head_fk_space` (on body_ctrl, off neck_02_ctrl), `head_ik_space` (on body_ctrl, off null), setting head_local.
- `solve.foot_roll = [{"side", "roll": "foot_roll_<s>_ctrl", "blend": "roll_blend_<s>_ctrl", "heel": "heel_<s>", "ball": "foot_bk1_<s>", "tip": "tip_<s>", "clamp": 40.0}]`.
- `solve.spine.stretch = "spine_stretch_switch"`; `solve.neck` gains `ik_points: ["neck_01", "head_mid", "head_ik_ctrl"]`, `stretch: "head_stretch"`, `local: "head_local"`.

- [ ] **Step 1: Extend `check()` first** with assertions for the new fields: every control has `frame` (4 floats, unit length within 1e-4), `visible`; every `mirror` control's left twin exists and has equal `frame`; `settings` names == the 22 bool/float controls; IK feet `frame` (offset/world) is identity within 1e-3 (`foot_l_ik_ctrl` is on `foot_l`: its bone-relative frame equals `conj(foot bone) * identity`; check instead that composing the FP-agnostic Epic globals gives world identity: `qmul(G["foot_l"].r, frame_ue) ≈ G["foot_l_ik_ctrl"].r`); `solve.foot_roll` has 2 entries naming existing controls/spaces; `solve.local` names exist.
- [ ] **Step 2: Run `python official_rig_decode.py check`**, expect AssertionError on `frame`.
- [ ] **Step 3: Implement** in `build()`: drop `centre_turn` from `shape_transform(v["raw"])`; add `frame`, `visible`, `mirror`; the `settings`, `solve` additions above (names are fixed by the probe; assert they exist in `h`); fix `TYPES`. Mirror: after the loop, for each control whose `side_swap(name, "l") != name` and whose name contains an `_r` token, copy `frame` and `shape_transform` from the left twin and set `mirror: True`.
- [ ] **Step 4: Run `build` then `check`**, expect `ok: ...`.

---

### Task 2: Frames and followers in the rig

**Files:** Modify `official_rig.py`; Modify `tests/plugin/official_rig_check.py`.

**Interfaces — Produces:** `FOLLOW = "OR_follow_"`; `_follower(obj, control, bone) -> str` (edit mode: a child of `control` at `bone`'s rest, named `FOLLOW + bone + "__" + control`); `_from_bone(obj, control, bone) -> Matrix` (the control's pose matrix that puts its follower on the bone's current pose: `pb[bone].matrix @ rest(bone).inverted() @ rest(control)` in armature space).

- [ ] **Step 1: Failing tests** (append to `official_rig_check.py`):

```python
# --- exact frames (spec 2026-10-10-official-rig-exact)
ex = mannequin("exact_fp", fp_tails=True)
official_rig.create(ex)
W = Matrix.Identity(3)


def rest3(name):
    return ex.data.bones[name].matrix_local.to_3x3().normalized()


check("IK foot controls are world-aligned (Epic's frame)", max(abs(x) for r in (rest3("foot_l_ik_ctrl") - W) for x in r) < 1e-3, True)
mirror = Matrix(((-1, 0, 0), (0, 1, 0), (0, 0, 1)))
for left in ("foot_l_ik_ctrl", "hand_l_ik_ctrl", "clavicle_l_ctrl", "upperarm_l_fk_ctrl", "thigh_l_fk_ctrl"):
    right = left.replace("_l_", "_r_")
    check("%s mirrors %s" % (right, left), max(abs(x) for r in (mirror @ rest3(left) @ mirror - rest3(right)) for x in r) < 1e-3, True)
# rest stays rest through the followers, both mannequins, every deform bone
for obj in (mannequin("exact_rest"), ex):
    if not obj.data.get("fpmp_official_rig"):
        official_rig.create(obj)
    bpy.context.view_layer.update()
    moved = [b.name for b in obj.data.bones if b.use_deform and
             (world(obj, b.name).to_translation() - (obj.matrix_world @ b.matrix_local).to_translation()).length > 1e-3]
    check("%s: rest stays rest" % obj.name, moved, [])
# a mirrored right control turned the mirrored way moves the right limb as the mirror of the left
for side, sign in (("l", 1.0), ("r", -1.0)):
    pbn = ex.pose.bones["upperarm_%s_fk_ctrl" % side]
    pbn.rotation_mode = 'XYZ'
    pbn.rotation_euler = (0.3, 0.0, 0.2 * sign)
bpy.context.view_layer.update()
hl, hr = head(ex, "hand_l"), head(ex, "hand_r")
check("mirrored controls move mirrored limbs", (hl - Vector((-hr.x, hr.y, hr.z))).length < 2e-3, True)
```

- [ ] **Step 2: Run, expect FAIL** on the frame checks (controls currently copy bone rests).
- [ ] **Step 3: Implement.**
  - `create()` edit phase: on-bone control rest = `ue_rest(bone)` rotation times `Quaternion(item["frame"])`, head at the bone head, tail along the new Y by the bone length, `align_roll` of the new Z; offset control rest = `Quaternion(item["frame"])` at its offset place. Build left before right; a `mirror` control's rest = the X-mirror of its left twin's rest rotation (`M @ R @ M`, `M = diag(-1,1,1)`), at its own place.
  - Shapes: `_shape_transform(obj, pb, item)`: `local = LocRotScale(st)`; for a mirror control `local = diag(-1,1,1).to_4x4() @ local`; decompose into translation/rotation/scale (negative scale kept).
  - Followers: `_follower` in the edit phase for every pair the solve uses: `solve.copy`, every chain's `fk`, spine/neck `fk`, IK `target` → `bones[2]`, ball `[ctrl, bone]`, neck effector → `bones[-1]`. Constraints (`_copy`) target the follower instead of the control.
  - `set_switch` matching: FK side `pb[control].matrix = _from_bone(obj, control, bone)`; IK side `eff.matrix = _from_bone(obj, target, end) @ target.matrix.inverted() @ eff.matrix` (target is a follower's parent); spine/neck pairs through `_from_bone`.
  - `_bake_targets` unchanged; `bake_to_controls` stores `_from_bone(...)` poses instead of bone matrices.
- [ ] **Step 4: Run the whole `official_rig_check.py`**: new checks pass, every old one stays green (posed FK→IK, bake, rest IK, spine).

---

### Task 3: Settings: hidden values, defaults, stretch and softness, local spaces

**Files:** Modify `official_rig.py` (or new `official_rig_settings.py`); tests.

**Interfaces — Produces:** `setting(obj, name) -> float`; `set_setting(obj, name, value)` (sets the property, `obj.update_tag()`); `SETTINGS_GROUP = "Mechanics"`.

- [ ] **Step 1: Failing tests:**

```python
st = mannequin("settings")
official_rig.create(st)
check("setting controls hidden, no shape", all(st.pose.bones[s["name"]].custom_shape is None and
      "Mechanics" in [c.name for c in st.data.bones[s["name"]].collections] for s in official_rig.load()["settings"]
      if s["name"] in st.pose.bones), True)
check("Epic's defaults", [official_rig.setting(st, n) for n in ("leg_l_fk_ik_switch", "arm_l_fk_ik_switch", "arm_l_fk_local",
      "roll_blend_l_ctrl", "arm_l_stretch_switch")], [1.0, 0.0, 1.0, 1.0, 0.0])
# stretch: a hand target 1.5 lengths out is reached with stretch on, not off; capped at 2
official_rig.set_switch(st, "arm_l_fk_ik_switch", ik=True)
L = (head(st, "lowerarm_l") - head(st, "upperarm_l")).length + (head(st, "hand_l") - head(st, "lowerarm_l")).length
root = head(st, "upperarm_l")
direction = (head(st, "hand_l") - root).normalized()
for far, stretch, reach in ((1.5, 0.0, False), (1.5, 1.0, True), (2.5, 1.0, False)):
    official_rig.set_setting(st, "arm_l_stretch_switch", stretch)
    st.pose.bones["hand_l_ik_ctrl"].matrix = Matrix.Translation(root + direction * L * far) @ \
        st.pose.bones["hand_l_ik_ctrl"].matrix.to_3x3().to_4x4()
    bpy.context.view_layer.update()
    got = (head(st, "hand_l") - root).length
    check("stretch %s at %.1f L: reached %s" % (stretch, far, reach), abs(got - L * far) < 2e-3, reach)
    if far == 2.5:
        check("stretch capped at 2 L", abs(got - 2.0 * L) < 2e-3, True)
# softness: the chain scale equals Epic's formula at a sampled distance
official_rig.set_setting(st, "arm_l_stretch_switch", 0.0)
official_rig.set_setting(st, "arm_l_softness", 1.0)
d = 0.995 * L
st.pose.bones["hand_l_ik_ctrl"].matrix = Matrix.Translation(root + direction * d) @ st.pose.bones["hand_l_ik_ctrl"].matrix.to_3x3().to_4x4()
bpy.context.view_layer.update()
sd = L * 1.0 * 0.02
da = L - sd
s = max(d / (da + sd * (1 - math.exp(-(d - da) / sd))), 1.0)
check("softness scales the chain as Epic's formula", round(st.pose.bones["OR_MCH_ik_upperarm_l"].scale.y, 4), round(s, 4))
# full reach with softness 0 and stretch off: no stretch (Review Focus 4)
official_rig.set_setting(st, "arm_l_softness", 0.0)
bpy.context.view_layer.update()
check("no softness, no stretch: the chain keeps its length", round(st.pose.bones["OR_MCH_ik_upperarm_l"].scale.y, 6), 1.0)
# FK local: the arm space's rotation relative to the clavicle changes when the clavicle turns only when the space
# follows the body (on); off, it rides the clavicle
official_rig.set_switch(st, "arm_l_fk_ik_switch", ik=False)
for on in (1.0, 0.0):
    official_rig.set_setting(st, "arm_l_fk_local", on)
    bpy.context.view_layer.update()


    def relative():
        return (st.pose.bones["clavicle_l_ctrl"].matrix.inverted() @ st.pose.bones["upperarm_l_fk_ctrl_space"].matrix).to_quaternion()
    before = relative()
    st.pose.bones["clavicle_l_ctrl"].rotation_mode = 'XYZ'
    st.pose.bones["clavicle_l_ctrl"].rotation_euler = (0.0, 0.0, 0.4)
    bpy.context.view_layer.update()
    changed = before.rotation_difference(relative()).angle > 0.3
    st.pose.bones["clavicle_l_ctrl"].rotation_euler = (0.0, 0.0, 0.0)
    bpy.context.view_layer.update()
    check("FK local %s: the arm space keeps the body's rotation, not the clavicle's" % on, changed, on == 1.0)
```

- [ ] **Step 2: Run, expect FAIL** (`setting` missing).
- [ ] **Step 3: Implement.**
  - Setting controls: after building, assign to Mechanics, `custom_shape = None`, property default from `settings`; `setting`/`set_setting`; `create()` applies Epic's switch defaults via `set_switch(..., match=False)` (legs IK).
  - IK chain length: on `OR_MCH_ik_<root>` and `OR_MCH_ik_<mid>` (mid with `inherit_scale = 'NONE'`), a driver on `scale[1]` with variables `soft` (setting), `stretch` (setting), `d` (`LOC_DIFF` between `OR_MCH_ik_<root>` head and the IK target, world), constants `L`, `k = 0.02`:
    `max(d/((L-L*soft*k)+L*soft*k*(1-exp(-(d-(L-L*soft*k))/(L*soft*k)))),1) if soft>1e-6 and d>L-L*soft*k else (clamp(d/L,1,2) if stretch>0.5 else 1)`
    (shorten with precomputed `L` text; keep ≤ 255 characters; split into a helper property if longer.)
  - Real limb bones copy scale from the MCH pose followers (Copy Transforms already does).
  - Local spaces: per `solve.local`: a follower of the space under the `on` source (`OR_follow_<space>__<on>`), a Copy Rotation (world) from it on the space, influence driven by the setting; `off: null` keeps the space's own parent.
- [ ] **Step 4: Run, expect PASS; whole suite green.**

---

### Task 4: Foot roll

**Files:** `official_rig.py` / `official_rig_settings.py`; tests.

- [ ] **Step 1: Failing tests:** on a fresh mannequin with the leg in IK: set `foot_roll_l_ctrl` yaw (rotation about its local Z) to +30°, -30°, -60° (and -30° with roll blend 0.5); measure each pivot space's turn (`heel_l`, `foot_bk1_l`, `tip_l` pose rotation vs rest): +30 → heel 30, ball 0, tip 0; -30 → ball 30, heel 0, tip 0; -60 → ball 40, tip 20; blend 0.5 at -30 → ball 20, tip 10. Tolerance 0.5°.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement:** on each pivot space a rotation driver about the space-local axis matching `foot_roll_<s>_ctrl`'s local Z (pick the space axis with the largest |dot|, sign from it), variables `yaw` (`ROT_Z`, local space, of the roll control) and `blend`; expressions in radians: heel `clamp(yaw,0,pi)`, ball `-clamp(-yaw,0,radians(40)*blend)`, tip `-clamp(-yaw-radians(40)*blend,0,pi)` (write the radians constant). The existing pivot copies (`_copy(... "pivot")`) switch to `mix_mode = 'AFTER_FULL'` so the control's own turn adds on top.
- [ ] **Step 4: Run, expect PASS; suite green.**

---

### Task 5: Spine stretch, neck IK, head stretch

**Files:** `official_rig.py`; tests.

- [ ] **Step 1: Failing tests:** spine IK, chest raised 10 cm: stretch off → spine_05 head moves less than 1 cm further from spine_01 than at rest (bones keep length); stretch on → spine_05 head within 1 cm of `OR_MCH_pt_spine_05` head (bones follow the points). Neck IK: moving `head_ik_ctrl` 5 cm forward moves `neck_02` (it no longer stays in FK); at rest neck IK is rest; head stretch on: `head` head follows `head_ik_ctrl` head within 1 cm.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement:** spine bones get a Copy Location from their `OR_MCH_pose_<bone>` follower, influence = `ik * stretch` (a driver: `ik*stretch`); neck: points `OR_MCH_pt_neck_01`, `OR_MCH_pt_neck_02` (and head) blended with Armature constraints from `neck_01`'s FK-side rest, `head_mid` space and `head_ik_ctrl` (linear weights by height), Damped Track to the next point, real neck bones copy rotation (influence = neck switch) and location (influence `ik*head_stretch`); the head copies `head_ik_ctrl`'s follower rotation as today. `_chains` neck part: `fk` = all neck FK pairs again (the neck switches as a whole now).
- [ ] **Step 4: Run, expect PASS; suite green.**

---

### Task 6: Panel and body controls

**Files:** `official_rig.py` (`ui`), `operator/rig_ui.py`; tests.

- [ ] **Step 1: Failing tests:** `bpy.ops.fpmp.official_rig_setting(name="arm_l_stretch_switch", value=1.0)` sets the setting; `bpy.ops.fpmp.official_rig_body_controls(show=False)` hides fingers, toes, `global_ctrl`, `root_ctrl`, `body_offset_ctrl`, `body_ctrl`, `hips_ctrl`, `neck_01_ctrl`, `neck_02_ctrl` (`PoseBone.hide`), and `show=True` brings them back except those the FK/IK state hides.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement:** operators (registered with the existing rig operators); `ui()` rows: per limb `FK | IK | Stretch | Softness slider | Local (arms)`, Spine `FK | IK | Stretch`, Neck `FK | IK | Stretch | Local`, Feet `Roll L | Roll R` sliders, `Body Controls` toggle; property widgets via `layout.prop(pose_bone, '["name"]', text=...)` so they key like Epic's channels.
- [ ] **Step 4: Run, expect PASS; all plugin suites green.**

---

### Task 7: Real asset

**Files (fpfork-private):** `devtools/scripts/official_rig_exact_real.py`.

- [ ] **Step 1:** Script: on the imported `mh_vs`: left/right control frames mirror (as Task 2), IK feet world-aligned; rerun `official_rig_all_ik.py` and `official_rig_switch_posed.py` (natural poses) expecting 0.0 cm / 0.0°; foot roll +30/-30/-60 pivots as Task 4; stretch reach at 1.5 L; print the control-shape positions of `clavicle_l/r_ctrl`, `foot_l/r_ik_ctrl` relative to their bones, left vs mirrored right.
- [ ] **Step 2:** Run `python fpdev.py import mh_vs` then the scripts. Expected: all exact; shapes mirrored within 1 mm.
- [ ] **Step 3:** Commit (after the user approves the message): fpfork plugin + tests + JSON, and the pending face-board master handle; fpfork-private decoder + script.
