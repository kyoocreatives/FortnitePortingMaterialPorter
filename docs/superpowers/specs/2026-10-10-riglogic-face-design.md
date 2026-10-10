# RigLogic face bones for Fortnite's MetaHuman-style heads

## Goal

On Fortnite's MetaHuman-style ("3L") heads, Epic's face board in Blender moves the face's `FACIAL_` bones the way the
game does: through the head's own RigLogic data (its DNA), as Epic's RigLogic solver runs it. Every board control the
DNA knows then does something (tongue, teeth, lip roll, eyelashes...), not only the 58 whose shape keys FP exports.

Use: posing and animating faces in Blender with the board. The face's shape keys stay at rest while the board drives the
bones (the user's choice: bones, as the game does).

## What the game files hold (found by probe, 2026-10-10)

- Each 3L head mesh (`F_MED_VibrantShell_Head`) carries `DNAAssetUserData` pointing at `<Head>_DNA` (class `DNA`). The
  cooked export holds, after its properties: a 4-byte little-endian 1, a MetaHuman DNA v2.8 (definition only: 115 GUI
  controls, 167 raw controls `CTRL_expressions.*`, 95 joints, 16 animated maps, 0 blend shapes; empty behaviour), then
  RigLogic's runtime dump to the end of the file (VibrantShell: 403,644 of 418,685 bytes).
- The dump is RigLogic's own serialization (Epic's OpenRigLogic, MIT, is the reference), written by a RigLogic older than
  the v13.5 snapshot: no `RLDS` header, a 22-byte configuration (no floating-point fields), evaluator kinds as a counted
  vector, no ML-joint metadata, conditional tables without row count, joint groups of 9 fields with block height 8 and
  no per-LOD output rows. All big-endian, u32 counts. VibrantShell: configuration AnyVector, quaternion rotations;
  328 PSDs (correctives: products of 2-5 clamped raw inputs, weight 1, capped at 1); 72 joint groups, 85 driven joints,
  531 output channels, 57,965 non-zero coefficients at LOD 0 (LOD 1 equal; LODs 2-3 empty); 50 animated-map rows.
- Units cm and degrees; joint rows give Euler deltas (rotation order xyz, signs +1) that RigLogic turns into a delta
  quaternion. The dump's frame mirrors Y against UE's (and FP's ue_rest frames): translation y and Euler x, z flip sign.
- The game runs it: `Fortnite_Base_3L_Head_AnimBP` holds an `AnimNode_RigLogic` (after a `PreRigLogic` cached pose).
- Checked on VibrantShell in Blender: jaw open, smile, blink applied as bone deltas deform the head like FP's matching
  shape keys (vertex-motion similarity 0.96, 0.98, 0.92; the bones move slightly less). Bones and shape keys together
  double the motion.

## 1. App: the head's RigLogic data to the plugin

- CUE4Parse patch: the export class `DNA` reads as `UDNAAsset`; `UDNAAsset` skips the 4-byte prefix and keeps the bytes
  after its properties as they are (`RawData`) instead of parsing them.
- Head export (`ExportContext.Fortnite.cs`, head branch, one `// MP` line): when the head's skeletal mesh has
  `DNAAssetUserData`, the bytes are written once to the assets folder (`<mesh path>.rigdna`) and the path goes into
  `ExportHeadMeta` (one `// MP` field, `FaceDNA`). Fork code in an `ExportContext` MaterialPorter partial.
- No DNA (most heads): nothing changes.

## 2. Plugin: reading it (`processing/context/riglogic_read.py`)

- Stdlib only, no per-frame work. `read(path) -> Rig` returns, for LOD 0: GUI and raw control names, joint names, the
  PSD definitions (inputs, clamps, weights), per driven joint channel its (input index, coefficient) pairs, and the
  animated-map names (kept, unused). Inputs index the raw controls first, then the PSDs, as RigLogic.
- It locates the DNA by its index table and reads the dump to the end; every byte must be consumed, or it raises (a
  format change fails loudly; the board then keeps today's shape-key behaviour).
- Evaluation in pure Python (`evaluate(rig, raw values) -> joint deltas`) for tests and checks.

## 3. Plugin: building it (`processing/context/riglogic_face.py`, from the board)

- When the head's meta carries `FaceDNA` and it reads, the MetaHuman board (`metahuman_board.add`) builds bones mode:
  - Raw controls: 167 properties on the armature (`CTRL_expressions.<name>`), each driven by the knobs through Epic's
    board formula already decoded (`metahuman_board.json` curves; the same expressions today's shape-key drivers use).
    A raw control without a decoded formula stays 0.
  - PSDs: one property each, driven by `min(1, weight * a * b * ...)` of its inputs clamped to their ranges.
  - Joints: per driven joint, a mechanism bone at the joint in the game's frame (FP's `ue_rest`), parented as the
    joint's parent; its location (cm / 100) and Euler XYZ rotation (degrees to radians) driven by the coefficient sums,
    with the Y mirror applied (y, rx, rz negated). A child of it sits at the real bone's rest; the real facial bone
    copies it (Copy Transforms), as the official rig's IK chains do.
  - Motion is added to FP's rest pose, not to the DNA's neutral (they differ by about 4 degrees on average): the face at
    rest is unchanged.
  - Every driver is a simple expression (sums, products, `min`, `max`): no Python at evaluation.
- Knobs: every board control the DNA's GUI list names gets a knob (with Epic's drawing, sphere and group as today).
- Shape keys: not driven in bones mode (they stay at rest).
- Board off (the existing switch, and an imported animation turning it off): the facial bones' copies and the property
  drivers mute, so the face plays its own animation.
- Without `FaceDNA`, or when it fails to read: today's shape-key board, logged once.

## Out of scope

Imported emote animation driving the bones (next step: emote curves key the raw controls); wrinkle maps (animated
maps); Epic's eye-aim frame; LODs other than 0; ML, RBF and twist/swing RigLogic data (absent on the probed head).

## Testing

- Headless (`tests/plugin/riglogic_face_check.py`, synthetic, no game data in the repo):
  - the reader on a small dump written by a test writer in the same format (round trip, every byte consumed, a
    truncated dump raises);
  - bones mode on a synthetic 3L armature: properties, PSDs, mechanism bones and copies built; moving a knob moves its
    joint as `evaluate` says (location and rotation); rest stays rest; board off frees the bones; every driver simple;
    shape keys untouched.
- Board suite stays green (shape-key mode unchanged without `FaceDNA`).
- Real asset through fpdev (devtools scripts): VibrantShell exported and imported with its DNA; the dump fully consumed;
  jaw open, smile, blink and a tongue control move the bones as the reference evaluation says; deformation similarity
  to the shape keys at least 0.9 for jaw, smile, blink; import time with the board.
- All plugin suites green; the app builds; the real asset page opens.
