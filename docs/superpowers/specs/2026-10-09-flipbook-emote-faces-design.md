# Emote face animation for flipbook faces

## Goal

An emote imported onto a character whose face is a material flipbook (VerTet, InfoInvader, WartyBrine, SaltyBrim...)
plays its face as in game: the brow, eye and mouth frames change with the emote, and the default idle's blinks and eye
looks run where the emote doesn't drive the face. Builds on the face board (`2026-10-09-rig-kit-tasty-faces-design.md`)
and the LEGO emote face path (`material_porter/face_anim.py`).

Use: posing stills and animating in Blender.

## How the game does it (found on real assets)

- Emotes never carry flipbook curves. Built-in emotes, outfit idles and older emotes carry Fortnite's legacy facial
  curves (`jaw_open_pose`, `l_blink_pose`, `look_*_pose`, `l_smile_pose`, `disablefaceoverride`...); most current
  common emotes (Floss, Dance Moves) carry none.
- Flipbook heads subclass `Fortnite_Base_Head_Export_Skeleton_AnimBP` with `bIsFlipbook_face`. It copies the body's
  curves, maps other rigs' curves to the legacy names (RigMapper, `FN_3LToLegacy_Main_Mapping`), blends in the default
  idle's blinks and eye looks (`FortAnimNode_BlendFacialCurves`, `/Game/Animation/Game/MainPlayer/Menu/FACIAL/
  DefaultFaceIdle_Legacy`), then runs the Control Rig
  `/Game/Characters/Player/Common/Fortnite_Base_Head/Facials/ControlRigs/Fortnite_Flipbook2_Face_Mapping_CtrlRig`
  (inputs `Enable_Brow/Eye/Mouth`).
- The Control Rig thresholds the `*_pose` curves (0.25 by default, some 0.4 or 0.5) and writes integer
  `flipbook_face_{L,R}_brow_index`, `flipbook_face_{L,R}_eye_index`, `flipbook_face_mouth_index`; the head's skeleton
  marks these as material curves, so UE sets the face material's parameters of the same name (case-insensitive).
- `FB_*UVOffset/Scale` are fixed per material instance; nothing animates them.
- The fork's emote export already sends every float curve of each section; nothing maps them to frames today.

## 1. Decode the rig once

- CUE4Parse (patched via `patches/CUE4Parse/`) reads current Fortnite RigVM bytecode: today
  `FRigVMExecuteOp` throws (`FRigVMByteCode.cs:189`, "Could not read RigVMBlueprintGeneratedClass correctly").
- A devtools script (fpfork-private) dumps `Fortnite_Flipbook2_Face_Mapping_CtrlRig`'s instructions, literals and
  variables in readable form. From it, the exact rules: per feature (each brow, each eye, mouth), the curves read,
  thresholds, the index written, the order and which rule wins when several match, and what `Prev_*_Index`,
  `Prev Blink Pose` and `Blink Pose Threshold` (0.025) do.
- The same pass reads the base head AnimBP's `FortAnimNode_BlendFacialCurves` settings: which idle curves blend in,
  how (override, add, max, weight), and what `disablefaceoverride` and `flipbook_face_is_idling` change.
- The decoded rules are written by hand into the plugin (section 3); the dump is evidence, not shipped.

## 2. App

- Regular emotes (`EExportType.Emote`) get `MPCurveModes` per section as LEGO emotes do (`AnimExport.cs`,
  `ReadFaceCurveModes`), so stepped `*_pose` keys stay stepped.
- The emote payload carries the idle (`DefaultFaceIdle_Legacy`) as one extra curve-only section, marked as the face
  idle, not laid on the body's NLA.

## 3. Blender

`material_porter/flipbook_face.py`:
- `indices(values) -> {input name: index}`: the decoded rules, from a frame's curve values (lower-case names) to the
  flipbook index inputs, with any state the rules carry (previous index, blink hold) passed frame to frame.
- `apply(armature, sections, idle) -> int`: for each section (same dicts as `face_anim.apply`), every frame of its
  range: the emote's curve values, the idle's (looped from frame 0 of the section) blended under them as the AnimBP
  does, mapped by `indices`, keyed CONSTANT on the face materials' flipbook index inputs that exist. One action per
  section, strips on the material's `MP Face` NLA track at the body's strip frames, with the body's repeat (as
  `face_anim`). Returns the number of face materials animated.
- The face board switches off (`fpmp_face_board = False`) when an emote animates the face; its toggle brings it back.
- An earlier emote's face animation is cleared first (`face_anim.clear`).

Import (`processing/context/anim_context.py`, MP hook beside the LEGO one): an `EMOTE` import whose target armature
has flipbook face materials runs `flipbook_face.apply`; a failure is logged, never fails the import.

`face_anim.face_materials` uses `face_board.faces` (one face finder for board and animation).

## Out of scope

Per-outfit `Enable_Brow/Eye/Mouth` beyond "the material has that input"; sprites' own emotes unless they carry the same
curves (checked once); a general RigVM interpreter; animating the UV offsets.

## Testing

- Headless (`rig_check.py` or a new `tests/plugin/flipbook_face_check.py`):
  - `indices` on hand-built curve values: one case per decoded rule, the tie cases, the blink hold, all-zero (rest).
  - `apply` on the synthetic flipbook armature: keys on the index inputs, CONSTANT interpolation, idle fills a section
    with no face curves, emote curves win where the decoded blend says so, strip frames and repeat, board turned off,
    a second emote replaces the first.
- CUE4Parse: the Flipbook2 rig's class reads without the RigVM error (devtools dump runs).
- Real assets through fpdev: VerTet with a face-curve emote (Emote_Facepalm or a built-in one) and with Floss (idle
  only); renders at frames with a known expression (a blink, an open jaw) against the atlas frame the rules pick.
- All plugin suites stay green; the app builds.

## Appendix A: Flipbook2 rules (decoded from the rig's byte code)

Read with the fork's CUE4Parse RigVM patch and `fpfork-private/devtools/rigvm_dump.py`; instruction numbers are the
rig's (618 instructions; entry "Forwards Solve" at 573). Curve names compare case-insensitively; a curve the pose lacks
reads 0. `t` = the rig variable `threshold` (default 0.25); `Enable_Brow/Eye/Mouth` default true. Within a block, rules
run in order and a later match overwrites an earlier one.

Main (573-617):
- `flipbook_face_bypass_mapping >= 0.5`: nothing is written (the five curves pass through as they come).
- Else: all five indices = 0; Brow (always, given Enable_Brow, Enable_Eye); Eye if Enable_Eye; Mouth if Enable_Mouth;
  then the indices are written as `flipbook_face_L_brow_index`, `_R_brow_index`, `_L_eye_index`, `_R_eye_index`,
  `flipbook_face_mouth_index`.

Brow (128-319), only when Enable_Brow or Enable_Eye; `up` curves read only when Enable_Brow (else 0):

| Order | Condition | Sets |
|---|---|---|
| 1 | Enable_Brow and `C_glabella_down_pose >= t` | L = R = 8 |
| 2 | Enable_Brow and `C_glabella_up_pose >= 0.9` | L = R = 7 |
| 3 | `L_brow_down_pose >= t` / `R_brow_down_pose >= t` | L = 6 / R = 6 |
| 4 | Enable_Brow and `L_brow_up_pose >= t` / `R_brow_up_pose >= t` | L = 5 / R = 5 |
| 5 | `C_glabella_down_pose >= 0.5`, then per side: `*_brow_down_pose >= 0.75` → 4; then Enable_Brow and `*_brow_up_pose >= 0.75` → 2 | side |
| 6 | `C_glabella_up_pose >= 0.9`, then per side: `*_brow_down_pose >= 0.75` → 3; then Enable_Brow and `*_brow_up_pose >= 0.75` → 1 | side |

(Per side in 5 and 6: L then R, L's pair before R's.)

Eye (0-127), per side X in L, R:
- `X_blink_pose >= 0.5`: eye = 3; and if `X_squint_inner_pose >= 0.7`: eye = 2; and within that, if `X_squeeze_pose >= 0.7`: eye = 1.
- Else: `X_wide_pose >= 0.75` → 7; then by that side's brow index (set above): 6 → eye 6; 4 → eye 5; 3 → eye 4.

Mouth (383-572):
- `l_snear` = `L_upper_lip_raiser_pose >= t` or `L_lower_lip_down_pose >= t`; `r_snear` = the R pair `>= 0.5` (the rig's
  own asymmetry).
- `jaw_open_pose >= t`: 7; `r_snear` → 6; `l_snear` → 5; `L_frown_pose >= t` or `R_frown_pose >= t` → 4;
  `R_smile_pose >= t` → 3; `L_smile_pose >= t` → 2.
- Else: the larger of `jaw_right_pose`, `jaw_left_pose` that is `>= t` → 13 / 14 (equal values: the later, left);
  `r_snear` → 14; `l_snear` → 13; `phoneme_oo_pose >= t` or `phoneme_ch_pose >= t` → 1; either frown `>= t` → 12;
  either smile `>= t` → 11; both upper lip raisers `>= t` → 10; both frowns `>= t` and (`l_snear` or `r_snear`) → 10;
  `R_smile_pose >= t` and `r_snear` → 9; `L_smile_pose >= t` and `l_snear` → 8.

No state is carried between frames: the rig resets the indices each evaluation. (The `Prev_*` variables belong to the
head AnimBP, not the rig.)

## Appendix B: idle under the emote

The head AnimBP (`Fortnite_Base_Head_Export_Skeleton_AnimBP`, parent `HeadPartAnimInstance`) plays
`DefaultFaceIdle_Legacy` (60 curves: every `*_pose` the rig reads, plus `flipbook_face_is_idling`) and blends it with
the incoming pose in `FortAnimNode_BlendFacialCurves` (preset `BlinksAndEyeDarts`, Alpha bound to the native
`FaceIdleBlinksAlpha`); a native `bSkipDefaultFaceIdle` and `FN_ZeroOutLegacyDefaultFaceIdle` also switch parts of it.
Those three are computed in game code, not in the assets.

Ruling (native behaviour not in the data): per section,
- the section carries none of the curves Appendix A reads (common emotes): the idle's curves drive the face;
- it carries some: the emote's curves drive the face, and `L_blink_pose` / `R_blink_pose` are the larger of the emote's
  and the idle's (the preset's blinks; its eye darts are look curves the rig never reads).
The idle loops from the section's first frame. Cost if wrong: on common emotes the face may show the idle's expressions
where the game shows a neutral face with blinks only; on face emotes an extra blink may land where the game holds the
emote's eyes.

Not reproduced (the head AnimBP's event graph and its native parent aren't in the cooked data):
- `Prev_*_Index`, `Prev Blink Pose`, `Blink Pose Threshold` (0.025): likely hold the previous frames through a blink's
  tail; ignored. Cost if wrong: an eye reopens a frame or two early at the end of a blink.
- `disablefaceoverride`: carried by face emotes alongside their face curves (seen with 44 others on SaltyBrim's
  built-in emote), so it doesn't change which source drives; ignored. Cost if wrong: an emote that carries only it
  would show the idle's expressions instead of a neutral face.

MetaHuman (3L) emotes (a section with `is_3l`): their curves become legacy ones through the payload's
`MetahumanToLegacyMappings` (`FN_3LToLegacy_Main_Mapping`) before the rules, as the head's mapping does, and the section
counts as carrying face curves.
