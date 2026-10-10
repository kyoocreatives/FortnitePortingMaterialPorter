# Official rig, exact: Epic's control frames and settings

## Goal

The fork's official body rig (spec 2026-10-09-official-body-rig) behaves like UEFN's FN_Mannequin_ControlRig: every
control turns on Epic's own axes (left and right mirrored, IK feet and the body world-aligned), its shape sits where
Epic draws it, and Epic's settings work: stretch, softness, FK local, head local, foot roll with roll blend, spine and
head stretch, show body controls. The setting controls themselves (the red diamonds) are hidden; their values live in
the panel.

User reports this fixes: IK feet that differ between left and right, misplaced and misrotated clavicle arrows, turned
spine IK controls, and the setting diamonds that do nothing when moved.

## What Epic's rig does (decoded 2026-10-10 from the rig's byte code)

- Control frames: each control has its own frame (`Offset` composed with its parents), not its bone's. Measured against
  the bones: IK feet, hips, chest and their tangents are world-aligned (90 degrees from their bones); IK hands 102
  (left); FK thighs 9, upper arms 14; every right-side control is its left one mirrored (scale X = -1).
- FK/IK switches are hard branches (> 0.5). In IK the FK controls are snapped onto the solved bones each frame; in FK
  the IK controls are. Defaults: legs IK (1), arms FK (0), spine and neck FK (0).
- Stretch (`<limb>_stretch_switch`): two-bone IK `bEnableStretch` with StretchStartRatio 1.0, StretchMaximumRatio 2.0:
  the limb lengthens to reach its target, up to twice its length. Default off.
- Softness (`<limb>_softness`, 0..2, default 0): the IK bone lengths are scaled by `s` before the solve:
  `L = LA + LB; d = |limb root - IK target|; sd = L * softness * 0.02; da = L - sd;`
  `s = max(d / (da + sd * (1 - exp(-(d - da) / sd))), 1)` when `softness > 1e-6` and `d > da`, else 1.
- FK local (`arm_<side>_fk_local`, default 1): `upperarm_<side>_fk_ctrl_space` keeps its translation and scale under
  `clavicle_<side>_ctrl`; its rotation follows `body_ctrl` when on, the clavicle when off.
- Head local (`head_local`, default 1): `head_fk_space` rotation follows `body_ctrl` (on) or `neck_02_ctrl` (off),
  translation follows `neck_02_ctrl`; `head_ik_space` rotation follows `body_ctrl` (on) or its parent `chest_space` (off),
  translation follows `chest_space`.
- Foot roll: pivot chain `foot_<s>_ik_ctrl > heel > tip > ball (foot_bk1) > foot_bk1 ctrl > foot_ik (the leg IK target)`;
  `foot_roll_<s>_ctrl` yaw (its local Z, the foot's lateral axis) with `B = 40 * roll_blend` (default 1): heel turns by
  `clamp(yaw, 0, 180)`, ball by `-clamp(-yaw, 0, B)`, tip by `-clamp(-yaw - B, 0, 180)`; the heel and tip controls add
  their own turn about their own position. `foot_roll` keeps its rest rotation relative to the IK foot.
- Spine stretch (`spine_stretch_switch`, default 0): off, the spine bones keep rest length (they can bunch, never
  spread); on, they spread along the hips-to-chest curve. Head stretch (`head_stretch`): the same for the neck in neck IK.
- Neck IK: a curve from `neck_01` through the `head_mid` null to `head_ik_ctrl`; the neck bones follow it, the head takes
  `head_ik_ctrl`'s rotation.
- Show body controls (default 1): visibility only (fingers, toes, `global_ctrl`, `root_ctrl`, `body_offset_ctrl`,
  `body_ctrl`, `hips_ctrl`, `neck_01/02_ctrl`).
- Setting controls are bool/float controls; Epic draws them, but moving them does nothing (they're switched as values).
  `roll_blend` is hidden in Epic's rig too.

## 1. Data (decoder, `fpfork-private/devtools/official_rig_decode.py` -> `official_rig.json`)

- Per control: `frame` = its rest rotation relative to its bone's game frame (on-bone controls) or to the world (offset
  controls), from the left side; right-side controls carry `mirror: true` (built as the Blender mirror of the left).
- `shape_transform` relative to the control's own frame (no bone compensation).
- Control types fixed (7 = Transform, 9 = EulerTransform).
- `settings`: per setting its name, kind (bool/float), default, range; `solve` gains `stretch` (ratios), `softness`
  (constant 0.02), `fk_local` and `head_local` spaces (which space, which rotation source on/off, which translation
  source), `foot_roll` per side (pivots, roll control, clamp 40, axis), `neck` IK points.
- The decoder's check: left and right frames mirror; every setting named in the byte code is in `settings`.

## 2. The rig (`processing/context/official_rig.py`, split if it passes ~900 lines)

- Controls are built at Epic's frames (right side mirrored from the left in Blender's way: head/tail mirrored across X,
  roll negated), at the same places as today.
- Every control-to-bone relation goes through a hidden follower: a child of the control at the driven bone's rest
  (`OR_follow_<bone>`), the bone copying the follower. Covers FK copies, the always-copied hips, the IK end and ball
  rotation, the head (FK and IK), the spine points, the neck.
- FK/IK matching and Bake to Controls place controls from bones through the same followers (control = bone pose times
  the follower's inverse rest offset).
- Setting controls: hidden (Mechanics), no shape; their values stay keyable properties on them, defaults from Epic.
- Stretch and softness: a driver on the IK chain's length (the head-to-head IK bones' Y scale): softness's `s` when
  softness > 0, else stretch's `clamp(d / L, 1, 2)` when stretch is on, else 1; `d` from a distance variable between
  the limb root and the IK target. The real bones copy the scale (the limb stretches as in UE).
- FK local / head local: each switched space gets a follower under its rotation source and a Copy Rotation from it,
  influence = the setting; its own parent keeps translation.
- Foot roll: drivers on the heel, ball and tip pivots (Epic's clamps), added after their controls' own turn.
- Spine stretch: the spine bones also copy their point's location, influence = the setting. Neck IK: the neck bones
  follow blended points from `neck_01` to `head_ik_ctrl` (as the spine), head stretch the same way.
- Show body controls: a panel toggle hiding those controls.
- Defaults applied at build: legs IK, arms FK, FK local and head local on, roll blend 1; IK at rest is the rest pose.

## 3. Panel (`operator/rig_ui.py` and the rig's `ui`)

Per limb one row: FK | IK, Stretch, Softness (slider 0..2), and Local for the arms. Spine row: FK | IK, Stretch.
Neck row: FK | IK, Stretch, Local (head local). Per foot: roll blend slider. Body Controls toggle. Dense rows, no
decoration.

## Out of scope

Helper and corrective bones (`deform_*`, the game's post-process drivers): next spec. Legacy (pre-3L) heads' face board:
queued after this. Epic's continuous FK-follows-IK snapping (the FK controls stay hidden in IK; matching happens on
switch).

## Testing

- Headless (`tests/plugin/official_rig_check.py`, synthetic mannequin, both child-pointing and FP-like tails):
  - left and right control frames mirror; IK feet, hips and chest controls world-aligned; a rotation of the left foot
    control about its local axis turns the foot as the mirrored rotation of the right turns the right foot;
  - rest stays rest through followers (all chains, FK and IK, both mannequins);
  - stretch: a target 1.5 limb lengths away is reached with stretch on, not with it off; capped at 2;
  - softness: the chain length scale equals Epic's formula for a sampled distance;
  - FK local on: turning `body_ctrl` turns the FK arm space; off: it doesn't;
  - foot roll: yaw +30 turns the heel pivot, -30 the ball, -60 the ball 40 and the tip 20; roll blend 0.5 caps the ball
    at 20;
  - spine stretch on spreads the spine toward a raised chest, off keeps bone lengths;
  - setting controls hidden; panel switches set the properties; matching and bake still exact.
- All plugin suites green.
- Real asset (devtools scripts): all-IK at rest exact, posed FK-to-IK exact, foot roll and stretch on VibrantShell;
  control shapes compared left against right.
