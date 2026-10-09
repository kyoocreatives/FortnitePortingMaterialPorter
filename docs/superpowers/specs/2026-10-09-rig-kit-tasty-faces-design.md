# Tasty on the rig kit, and the face board for flipbook faces

## Goal

Last application of the rig kit (`2026-10-09-rig-kit-vehicle-design.md`, `rig_style.py`) and a wider face board
(`2026-10-09-rig-kit-lego-design.md`, `face_board.py`):

A. FP's character rig (Tasty) gets the kit's colours, widths and visibility, through the fork's after-import hook,
   without changing FP's files.
B. The face board works for any character whose face is a material flipbook (Fortnite's flipbook-face master,
   sprites, flipbook BR outfits), on Tasty, creature and LEGO rigs; sprites start getting the creature rig.

Use: posing stills and animating in Blender.

## A. Tasty

Tasty (`processing/context/tasty_context.py`, upstream FP) is built during a character import with RigType Tasty
(`mesh_context.create_tasty_rig`), then the fork's `material_porter/mesh_hooks.after_parts` runs. The restyle happens
there, on the master skeleton marked `is_tasty`.

Today its groups are: Rig (71: root, IK hands/feet, poles, spine, fingers), Face (29), Base (40, plain skeleton, no
shapes), Dynamic (56, hair/tail/cloth), Twist (14), Deform (25), Sockets (10), all shown; Extra (82) hidden. Shapes are
the add-on's CTRL_ shapes in Blender theme palettes. Settings (`use_ik_*`, `use_pole_targets`) are object properties
drawn by FP's `TASTY_PT_RigSettings` panel.

The restyle:
- Visibility: Rig and Face shown; Dynamic, Base, Twist, Deform, Sockets, Extra hidden. Group names stay Tasty's.
- Colours (every bone with a custom shape): kit side colours from the bone name (`_l`/`_r` suffix, `l_`/`r_`/`L_`/`R_`
  prefix; else centre); `root` main. Face and Dynamic bones take the secondary tint.
- Widths: root 3.5; Rig 2.5; Face and Dynamic 1.5. Other groups' bones keep their widths (hidden anyway).
- Shapes: Tasty's own, unchanged.
- `show_in_front` on the armature object.
- Marked `fpmp_tasty_styled` on the armature data so a re-run (re-import into the same armature) doesn't style twice.

Panel (`operator/rig_ui.py`): for a Tasty armature, the group toggles (Tasty's eight groups), Select Controls and
Reset Selected / All, where controls are the Rig and Face groups (`rig_style.controls` counts them for `is_tasty`
armatures). The IK settings stay in FP's Tasty panel; the fork panel doesn't draw them.

## B. Face board for flipbook faces

`face_board` learns a second face type. A face material is found as today (`face_anim.face_materials`, extended to also
accept the flipbook inputs below), and the type is read from its inputs:

| Type | Sliders (input, label, side) | Count | Pads (X, Y inputs) |
|---|---|---|---|
| LEGO (as built) | the `*Pose` inputs | 0-15 | eyes (each), mouth: `<feature>U/V` |
| Flipbook | `Flipbook_face_L_brow_index` Brow L (L), `Flipbook_face_R_brow_index` Brow R (R), `flipbook_face_L_eye_index` Eye L (L), `flipbook_face_R_eye_index` Eye R (R), `flipbook_face_mouth_index` Mouth (C) | `FB_<Brow/Eye/Mouth>ColumnCount × FB_<...>RowCount` when both exist, else 16 | brows `FB_BrowUVOffsetX/Y` (C), eyes `FB_EyeUVOffsetX/Y` (C), mouth `FB_MouthUVOffsetX/Y` (C) |

Input names compare case-insensitively (the brow ones start with a capital F). A slider's range is 0 to count-1 around
the imported index, as today (the LEGO 0-15 range is count 16). Pads move ±0.1 UV around the imported offset, as
today.

Where the board attaches:
- the rig's head bone: Tasty `head`; creature rig `Survey.head`; LEGO `head`;
- no head bone (sprites): the rig's root, the board placed above the model's top, centred, facing forward.

Which imports get a board, when their meshes carry a flipbook or LEGO face material:
- Tasty outfits (in the hook, after the restyle);
- creature-rigged imports (`creature_rig.create`), sidekicks included;
- LEGO figures (as today);
- sprites: the Sprite import type is added to the hook's creature-rig list, so a sprite gets the creature rig and, with
  a flipbook face, a board.

The Face Board switch (`fpmp_face_board`) and its panel lines work for every board. Emotes on flipbook outfits don't
key these inputs today, so nothing turns those boards off on import.

## Out of scope
Importing emotes' flipbook face animation; IK/FK snapping; replacing Tasty's shapes or moving its settings; how well
the creature rig reads every sprite skeleton (checked on one, the Air Sprite).

## Testing
- Headless (`rig_check.py`):
  - Tasty: a synthetic armature marked `is_tasty` with Tasty's eight groups and shaped bones (`root`, `ik_hand_l`,
    `hand_r`-like names, a face bone, a dynamic bone) run through the restyle: visibility, colours by side, widths, idempotent
    second run, `show_in_front`; `rig_style.controls` returns Rig + Face bones.
  - Flipbook board: the quadruped (creature rig) and a Tasty-like armature with a head mesh whose material has the
    flipbook inputs (eye 3×3, mouth 4×4, offsets): sliders for present inputs, slider ranges from the counts, mouth
    slider moves the index, pads move the offsets; a board on a rig with no head bone sits above the model.
- Real assets through fpdev: Nemia with Tasty (restyle; before/after control renders); the Air Sprite (creature rig +
  flipbook board; a face render with eyes and mouth changed through the board); one flipbook BR outfit if one is found
  among the materials listed (WartyBrine, BlockStack, InfoInvader, VerTet, SaltyBrim, SpeedyPeas).
- All plugin suites stay green.
