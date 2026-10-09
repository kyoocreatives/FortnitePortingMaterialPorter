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
