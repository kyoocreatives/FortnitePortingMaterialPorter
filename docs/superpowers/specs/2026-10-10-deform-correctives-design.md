# Deform correctives: Fortnite's helper bones in Blender

## Goal

Characters with Fortnite's `deform_*` helper bones (elbow, knee, upper arm, groin, glute, wrist, pec) deform in
Blender as in game: each corrective bone follows its blend of limb bones and slides/scales by a weight read from the
pose, with the character's own tuning. It works under any rig (official, Tasty, plain skeleton) and under imported
animation, as in game where it runs after the animation. Secondary motion (`dyn_*`, simulated) is out of scope.

## What the game does (probe 2026-10-10, VibrantShell and AlderOptic)

- The body's `<Char>_AnimBP` runs `CopyPoseFromMesh -> ControlRig -> RigidBody -> AnimDynamics...`. The ControlRig
  node runs the character's own `<Char>_CtrlRig` (Alpha 1, all LODs; pose and curves in and out).
- The CtrlRig's forward solve calls the native `FRigUnit_FortDeformRig` with three inputs (per-part Bool switches,
  Value transforms, Curve flags), then the character's extras (VibrantShell: rotation constraints, spherical pose
  readers feeding additive-local ModifyTransforms; AlderOptic: GetAlphaFromQuatDelta-driven ModifyTransforms).
- `FortDeformRig`'s graph twin (Epic's shared library `FN_DeformRig_ControlRig_FNC`, readable in
  `F_MED_DeformRig_Master_ControlRig_new`) per part:
  - base bone (`deform_X_base_<s>`): ParentConstraint, maintain offset, Average interpolation, from two bones with
    weights (elbow_in: lowerarm .4, upperarm .6 ...);
  - corrective (`deform_X_<s>`): ModifyTransforms AdditiveLocal, `local = lerp(local, V * local, w)`, w clamped 0..1,
    V the part's Value (rotation identity; translation and scale), mirrored on the right (translation negated);
  - weight: elbow/knee/pec from the driver bone's local rotation delta to its reference (Yaw or Pitch, remapped and
    clamped: elbow 15..90, knee 10..100, pec 0..30); upper arm/groin/glute/wrist/lat from a SphericalPoseReader
    (elliptical cone around a driver axis with region, falloff and per-quadrant width/height multipliers);
  - pec: an aim bone (`deform_pec_aim`) and an X-only position constraint before its additive.
  The full per-bone table (30 correctives) is `fpfork-private/devtools/helpers/deform_spec.json`.
- Per-character tuning: the Value transforms differ per character (VibrantShell's rig feeds the library defaults; the
  master rig tunes them); some parts are off (bicep, lat on the probed skeletons).
- Blocker found: the fork's CUE4Parse RigVM literal reader stops at the first struct that isn't a RigElementKey
  (VibrantShell: 4 of 206 literals read), so per-character values and extras' constants don't read today.

## 1. Data

- CUE4Parse patch (`FRigVMMemoryStorageStruct.cs`, `ReadItem`): structs read through CUE4Parse's struct reader
  (native ones as their types: Vector, Rotator, Quat, Transform; others as property fallbacks), enums as their value
  name or byte, objects/soft objects as paths, arrays of any of these. The check: VibrantShell's CtrlRig reads all its
  literals.
- Shipped library data `processing/context/deform_rig.json`, decoded once (devtools, from the master rig and the
  library functions): per part the bones, base blend, weight driver (type and constants), value field, mirror rule,
  the pec aim; the library Value defaults.
- App: when a character part's anim blueprint (the body's) runs a Control Rig node, the export writes that Control
  Rig's full dump (CUE4Parse JSON) to `<part mesh path>.deformrig.json` once and names it in the part's meta
  (`DeformRig`, one `// MP` field). No such rig: nothing.

## 2. Plugin reader (`processing/context/deform_rig_read.py`)

- From the dump: the forward solve's byte code (ported from the probe's decoder); the `FortDeformRig` call's Bool,
  Value and Curve inputs (the character's tuning); the extras as a list of supported operations: SphericalPoseReader,
  ModifyTransforms (additive local), Rotation/PositionConstraintLocalSpaceOffset, GetAlphaFromQuatDelta, and the math
  between them (multiply, remap, clamp, add); unsupported operations stop the extras with one log line (the library
  part still builds).
- No dump, or unreadable: the library defaults.

## 3. Building (`processing/context/deform_rig.py`)

- Base bones: followers of the two source bones at the base's rest; the base copies the first fully and blends the
  second by `w2 / (w1 + w2)` (Epic's weighted average of two transforms).
- Weights: hidden reader bones in the game's frame (FP's `ue_rest`) copying the driver bone, their quaternion channels
  feeding simple-expression drivers: joint-angle weights through `atan2` with Epic's remap and clamp; the spherical
  pose reader as Epic's elliptical cone, staged over helper properties to keep expressions under 255 characters.
- Corrective bones: location = w * V.t and scale = 1 + w * (V.s - 1) in the bone's game frame (the axis change folded
  into the driver coefficients; right side mirrored); pec aim and X-only follow as constraints.
- Extras: the supported operations as the same kinds of drivers and constraints, in their graph order.
- Always on by default; a `Correctives` toggle in the Rig panel (any rig, and plain skeletons) mutes them.
- Hook: after the parts are built (before the body rig), on every skeleton with `deform_*` bones.

## Out of scope

`dyn_*` secondary motion (AnimDynamics, RigidBody/RBAN), the face accessories' physics, curve-driven alpha flags.

## Testing

- CUE4Parse: VibrantShell's CtrlRig literals all read (count and a few known values: the library Value defaults).
- Plugin (headless, synthetic arm/leg skeleton with deform bones, both FP-like and child-pointing tails):
  - each weight type against a pure-Python reference of Epic's formulas (angle remap, spherical pose reader with its
    quadrant multipliers, additive lerp, two-source average), sampled over a pose range;
  - rest stays rest; the right side mirrors the left; the toggle mutes; a skeleton without deform bones gets nothing;
    an unreadable dump falls back to the library defaults with one log line.
- Real asset (devtools): VibrantShell exported with its rig dump; elbow, knee, shoulder, wrist, hip bent through a
  range; every `deform_*` bone against the Python reference; mesh deformation smooth (no jumps between samples). If an
  imported game animation keys `deform_*` bones, compare against its keys too.
- Uncertainty to state: the spherical pose reader's math is ported from Epic's engine semantics without its source at
  hand; the real-asset comparison is against our reference, not the game. If the user installs UE (its ControlRig
  plugin source), verify the reader and the ParentConstraint Average blend against it.
