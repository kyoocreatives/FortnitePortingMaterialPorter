# LEGO figure rig on the rig kit, with a face board

## Goal

Third application of the rig kit (`docs/superpowers/specs/2026-10-09-rig-kit-vehicle-design.md`, `rig_style.py`): the
LEGO figure rig (`processing/context/lego_rig.py`) gets the kit's colours, widths, shapes and collections, plus a new
**face board**: controls beside the head that pick the face's expressions and nudge its features, keyable with the pose.
Use: posing stills and animating in Blender.

## Today

- FK on the figure's own bones (root, pelvis, torso, head, legs, arms, hands, head_accessory); rotation locks on legs
  (one axis) and hands (twist).
- Colours already per side (custom, close to the kit's), CTRL_ shapes from the add-on, collections LEGO Controls / LEGO Other.
- The face is a texture atlas driven by the exact face material's inputs (group node of e.g. `MP MI_Head_<figure>`):
  `MouthPose`, `EyeLeftPose`, `EyeRightPose`, `EyelashLeftPose`, `EyelashRightPose`, `TeethUpperPose`, `TeethLowerPose`,
  `TonguePose`, on some faces `BrowLeftPose` / `BrowRightPose`; plus placement offsets `MouthU/V`, `EyeLeftU/V`,
  `EyeRightU/V`... `material_porter/face_anim.py` finds these materials (`face_materials(armature)`) and keys these
  inputs when an emote with face animation is imported.

## The figure with the kit

| Control | Shape | Colour | Width | Collection |
|---|---|---|---|---|
| root | footprint (today's) | main | 3.5 | Controls |
| pelvis | hip plate (today's) | centre | 2.5 | Controls |
| torso, head | `CR_Circle` around the bone | centre | 2.5 | Controls |
| leg_l/r, arm_l/r | `CR_CircleTick` on the hip/shoulder axis (tick shows the swing) | side | 2.5 | Controls |
| hand_l/r | `CR_Circle` at the wrist | side | 2.5 | Controls |
| head_accessory | `CR_Box` | centre, secondary tint | 1.5 | Secondary |
| face board (below) | | | | Controls / Secondary |
| everything else | | | | Game Bones |

Rotation locks and modes stay as today. No Mechanics bones besides the face board's own (none expected); the
armature is drawn in front.

## The face board

Only when the figure has a face material (`face_anim.face_materials`) with at least one `*Pose` input.

- `CR_FaceBoard`: a frame (`CR_Square`, centre, 2.5) standing beside the head on the figure's left, facing forward
  (-Y), parented to `head`, locked (it only holds the sliders). Size: about the head's width.
- One **pose slider** per `*Pose` input the face has, in a column inside the frame, labelled by order:
  Brow L, Brow R, Eye L, Eye R, Lash L, Lash R, Mouth, Teeth Upper, Teeth Lower, Tongue (those present).
  Each is a bone `CR_Face_<Input>` parented to the board, moving along the board's X only (Limit Location, local,
  0 to 15 steps); the input takes `round(x / step)`, so dragging steps through expressions 0-15. Shape: small
  `CR_Diamond`; side colour for left/right features, centre otherwise; width 2.5; Controls.
- Two **placement pads** for the eyes and one for the mouth: `CR_Face_EyeLeft_UV`, `CR_Face_EyeRight_UV`,
  `CR_Face_Mouth_UV`, small `CR_Square` handles moving in the board's XY plane within ±1 step; X adds to the feature's
  U input, Y to its V input, around the values the figure was imported with (small range, about a tenth of the face).
  Only for features whose U and V inputs exist. Side or centre colour, secondary tint, width 1.5; Secondary.
- The inputs are driven: drivers on the face material's group-node inputs read the board bones' local location.
- **Face Board** on/off: a boolean of the armature object (`fpmp_face_board`, in the Rig panel), default on. Off mutes
  the board's drivers so an emote's face animation (keys on the same inputs) plays; on unmutes them. When
  `face_anim.apply` lays an emote's face animation on a figure that has a board, it turns the board off.
- A face material shared by two rigged figures follows the figure rigged last (one driver per input).

## Panel

The kit panel as for the other rigs. For a LEGO rig with a face board: the Face Board toggle, and the current
expression numbers (read-only) under it.

## Out of scope
IK for LEGO limbs (rigid minifigure joints), brow/eye shapes beyond what the face material does, migrating saved rigs,
knowing each atlas's real number of expressions (the slider allows 0-15; an index past the atlas shows what the
material shows for it).

## Testing
- Headless (`rig_check.py`): a synthetic figure skeleton with every `NEEDED` bone and a mesh whose material has a
  group node with `MouthPose`, `EyeLeftPose`, `EyeRightPose`, `EyeLeftU`, `EyeLeftV`, `MouthU`, `MouthV` inputs:
  kit collections, colours, widths, shapes; the board and its sliders exist only for present inputs; moving
  `CR_Face_MouthPose` 3 steps sets `MouthPose` to 3; the eye pad moves `EyeLeftU` off its imported value; Face Board
  off restores keyed values (drivers muted); a figure without a face material gets no board.
- Real figure through fpdev: LucidAzalea (LegoOutfit) rigs with a board; before/after control renders; a render with
  a different mouth and eye pose set through the board.
- All plugin suites stay green.
