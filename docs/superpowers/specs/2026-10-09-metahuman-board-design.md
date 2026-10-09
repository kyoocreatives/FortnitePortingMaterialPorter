# MetaHuman face board for Fortnite's MetaHuman-style heads

## Goal

Fortnite characters whose heads are MetaHuman-style ("3L": `FACIAL_` face bones, MetaHuman expression shape keys) get
Epic's MetaHuman face board in Blender: its controls (`CTRL_L_brow_down`, `CTRL_C_jaw`, the eye and mouth boxes) laid
out as Epic's board, driving FP's face shape keys through Epic's own control-to-expression mapping. Bone motion
(MetaHuman's RigLogic) is not part of it.

Use: posing and animating faces in Blender; emotes still drive the face as today.

## What the game files hold (found by probe)

- Epic's board rig: `FortniteGame/VKTemplates/Basic/VKT_Talisman_Bridge/Plugins/VKT_Talisman_Bridge/Content/MetaHumans/
  Common/Face/Face_ControlBoard_CtrlRig`, an editor Control Rig Blueprint, readable with the fork's CUE4Parse RigVM and
  ControlRig patches (a full `fork-dump` works; fpdev's dump falls back to its light form on size):
  - hierarchy: 175 controls (155 float sliders, 14 2D boxes, 6 transforms; `MH_FACIAL_BOARD` frame, knobs in Epic's
    `Sphere_Solid`), 183 nulls, 898 bones, 1398 curves (276 `CTRL_expressions_*`, the rest MetaHuman blend-shape and
    joint curves);
  - byte code (1436 instructions): forward, slider/box values → `FRigVMFunction_MathFloatRemap`,
    `FRigVMFunction_AlphaInterp`, `FRigVMFunction_MathFloatAdd`, `FRigVMFunction_AnimEvalRichCurve` →
    `FRigUnit_SetCurveValue` (260 writes); backward, curves → controls (not used here);
  - its literal constants are names (430), structs (36: Vector2D, RigElementKey), floats (17), enums, a string; the
    fork's literal reader stops on Vector2D, enums and strings today.
- FP's 3L heads (VibrantShell, VowelSleep): 73 face shape keys named as MetaHuman's expression curves without the
  `CTRL_expressions_` prefix (`browDownL`, `eyeBlinkL`, `jawOpen`...), 86 `FACIAL_` bones.
- Each head's DNA (`<Head>_DNA`, class `DNA`): definition readable (115 GUI controls, 167 raw controls, 95 joints) once
  the class is registered and Fortnite's 4-byte prefix skipped; its behaviour is in a RigLogic runtime dump not decoded
  (out of scope).

## 1. Data, decoded once

- CUE4Parse patch (`FRigVMMemoryStorageStruct.cs`): the literal reader also reads Vector2D (unversioned struct, two
  doubles), enums (as the value's name) and strings.
- A devtools script (fpfork-private) reads the board rig and writes
  `plugins/Blender/fortnite_porting/processing/context/metahuman_board.json`:
  - `controls`: per control: `name`, `kind` (`slider` | `box` | `frame`), `position` (board plane x, y, from the board's
    hierarchy, composed through its nulls), `limits` (per axis min, max), `shape`;
  - `curves`: per `CTRL_expressions_*` curve the forward formula as a small expression tree over control channels
    (`<control>.x`/`.y`), constants and the operations the byte code uses (remap with its in/out ranges and clamp,
    alpha interp with its scale/bias/clamp settings, add, rich-curve lookup as key pairs);
  - `frame`: the board's outline (MH_FACIAL_BOARD) for its custom shape.
- The decoder's check: every forward curve write is decoded, every channel names a control, and every formula is 0
  with every control at rest.

## 2. The board in Blender (`processing/context/metahuman_board.py`)

- Qualifies: an armature with `FACIAL_C_FacialRoot` and a mesh under it whose shape keys include at least 20 of the
  decoded expression names (without the prefix).
- Placement: beside the head bone (`head`), sized to the head as the flipbook board; a board bone (frame) parented to the
  head, knob bones on the board plane at Epic's positions, limited to their frames (Limit Location, local), labelled by
  Epic's names; the frame and the slider/box outlines as custom shapes; bones in the kit's `Controls` collection (or the
  board's own group on a rig without the kit's).
- Shape keys: each FP shape key `X` with a decoded curve `CTRL_expressions_X` gets one driver: the curve's formula over
  the knobs' local positions, written as a scripted expression Blender evaluates without Python (arithmetic, `min`,
  `max`); a rich-curve lookup becomes piecewise-linear `min`/`max` terms. Curves without a shape key are skipped.
- Switch: the existing Face Board switch (`fpmp_face_board`) and panel lines; off mutes the board's drivers. An emote
  import that keys the shape keys (FP's MetaHuman curve path) turns the board off.
- Works on the plain skeleton, Tasty and the official rig.

## 3. Hook

The fork's after-import hook adds the board after the body rig on a qualifying skeleton, any rig type; a failure is
logged and never fails the import.

## Out of scope

Bone motion (RigLogic dump); baking an animation back onto the board (the rig's backward graph); 3L heads without these
shape keys; DNA reading in the app.

## Testing

- Headless (`tests/plugin/metahuman_board_check.py`):
  - formulas: hand-checked cases (one slider, one 2D box, one curve built from two controls) evaluate as decoded;
  - a synthetic 3L armature (FACIAL_C_FacialRoot, head) with a mesh carrying the 73 keys: board built, one driver per
    key with a decoded curve, knobs limited to their frames, moving `CTRL_L_brow_down` raises `browDownL` as its formula
    says, rest leaves every key at 0;
  - the switch mutes and unmutes; an emote import turns it off;
  - an armature without FACIAL bones, or without the shape keys, gets nothing; a failure is logged, not raised.
- Decoder check as above.
- Real asset through fpdev: VibrantShell (or VowelSleep) imported; renders of the face at rest and with a few board
  poses (brow down, jaw open, smile).
- All plugin suites stay green; the app builds.
