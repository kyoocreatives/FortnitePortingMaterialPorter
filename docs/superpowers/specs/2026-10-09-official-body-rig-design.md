# Fortnite's official body rig in Blender

## Goal

A third Rig Type, "Fortnite Official Rig (IK/FK)", that builds UEFN's own mannequin Control Rig
(`FN_Mannequin_ControlRig`) on an imported character: Epic's controls, names, shapes, colours, spaces and FK/IK
switches, with its solving rebuilt as Blender constraints. Default (FK) and Tasty (IK, restyled by the kit) stay.
FortRig (the separate add-on) stays as it is.

Use: posing stills and animating in Blender.

## What the rig is (found on the game files)

`FortniteGame/VKTemplates/Basic/VKT_Animation101/Plugins/VKT_Animation101/Content/Mannequin/Meshes/FN_Mannequin_ControlRig`
is a full editor Control Rig Blueprint (graphs, pins, links, hierarchy, compiled byte code), readable with the fork's
CUE4Parse RigVM patch:
- Hierarchy: 346 bones (the Fortnite mannequin skeleton FP characters share), 26 nulls (spaces such as
  `leg_l_controls_space`, `upperarm_l_fk_ctrl_space`, `head_fk_space`), 129 controls: `global_ctrl`, `root_ctrl`,
  `body_offset_ctrl`, `body_ctrl`, `hips_ctrl`, `spine_01-03_ctrl`, `clavicle_*_ctrl`, `*_fk_ctrl` chains,
  `foot_*_ik_ctrl`, `hand_*_ik_ctrl`, `*_pv_ik_ctrl` poles, `arm/leg_*_fk_ik_switch`, fingers, toes, head and neck.
- Control types: 87 Euler transform, 20 transform without scale, 16 bool, 6 float. Settings carry shape name
  (Epic's gizmo library: Arrow_Thick, Circle_Thick, Box_Thick, Sphere_Thick, RoundedSquare_Thin, Hexagon_Thin...),
  colour and limits. The gizmo meshes are in the files (`Engine/Plugins/Animation/ControlRig/Content/Controls/
  DefaultGizmoLibrary`).
- Solving (byte code units): TwoBoneIKSimplePerItem, AimBoneMath, ControlRigSplineFromPoints /
  PositionFromControlRigSpline / TransformFromControlRigSpline, ProjectTransformToNewParent, Get/SetTransform,
  Get/SetControl values and visibility, maths. "Backward *" graphs (Arm, Clavicle, Fingers, Foot, Leg, Neck, Spine) are
  the inverse solve that puts an animation onto the controls.
- Read gaps today: the controls' settings are private fields in CUE4Parse (not in dumps); element transforms (UE 5.4+
  pooled storage) read as zero; a few class-level properties fail to read (not needed).

## 1. Data, decoded once

A devtools script (fpfork-private) writes `plugins/Blender/fortnite_porting/processing/context/official_rig.json`:
- `controls`: per control, in hierarchy order: `name`, `type` (transform / transform_no_scale / bool / float),
  `parent` (the element it hangs from, a control or a space), `spaces` (its available spaces, when it has any),
  `shape`, `color` (RGB, as Epic's), `limits` (per axis when set), `bone` (the skeleton bone it is placed on),
  `default` (bool/float defaults).
- `spaces`: per null: `name`, `parent`, `bone`.
- `solve`: what each solving block drives: IK chains (`root`, `mid`, `end` bones, `effector` control, `pole` control,
  `switch` control), aims, the spline spine (its controls and the bones placed along it), FK pairs (control, bone).
- `shapes`: each gizmo used, as vertices and edges (from `DefaultGizmoLibrary`'s meshes, normalized as Epic's).

`bone` comes from the rig's construction graph (Set Transform nodes placing a control or space at a bone's transform),
cross-checked with Epic's naming (`foot_l_ik_ctrl` on `foot_l`); a control whose placement isn't a bone (poles) gets
`bone` plus `offset` as the construction graph computes it. Making the settings and transforms readable may need fork
CUE4Parse patch changes (public settings, the transform storage); any such change is a patch file like the RigVM ones.

The app is unchanged except the new Rig Type value.

## 2. The rig (`processing/context/official_rig.py`)

Built from the JSON on the master skeleton of a humanoid outfit (not creatures, sidekicks, sprites, vehicles, LEGO):
- Controls: one bone per control (Epic's name), placed per `bone`/`offset`, parented as `parent` says (spaces become
  bones too). Custom shapes from `shapes` (created once per file, like the kit's `rig_shapes`), coloured with `color`;
  wire width by role (root controls 3.5, limbs 2.5, fingers and toes 1.5, as the kit). Epic's look, not the kit's
  palette.
- Bone collections: the kit's `Controls`, `Mechanics`, `Game Bones` (and `Face Board` when a board is added); the
  game bones hidden.
- Solving, in Blender terms:
  - FK: Copy Transforms (control → bone) on each FK pair.
  - Arms and legs: IK constraint (chain 2) to the effector control with the pole control; feet and hands follow the
    effector's rotation (Copy Rotation), as AimBoneMath/SetTransform do.
  - FK/IK switch: a bool property on the `*_fk_ik_switch` control (default from the data), driving the FK and IK
    constraints' influence; the inactive chain's controls hide.
  - Spine: `spine_01-03_ctrl` drive the spine bones along a curve through them (Spline IK on a mechanism chain), the
    nearest Blender equivalent of Epic's spline units; neck and head FK.
  - Spaces: each control with `spaces` gets a Child Of per space, one active at a time, chosen by an enum property on
    the control.
  - Limits: Limit Location / Rotation where `limits` say.
- Original bones keep their rest data (FP's animation import needs it); every constraint the rig adds is named with an
  `OR ` prefix (official rig) so they can be found and muted together.
- Marked `fpmp_official_rig` on the armature data; the Rig switch (`fpmp_official_rig_on`) mutes every `OR` constraint.

Panel (`operator/rig_ui.py`): group toggles, Select Controls, Reset Selected / All, the four FK/IK switches, the
spaces of the selected control, Rig on/off, Bake to Controls, and the face board lines when there is one.

## 3. Plumbing

- `ERigType` gains `Official` in the app (`BlenderSettingsViewModel.cs`, description "Fortnite Official Rig (IK/FK)")
  and `OFFICIAL` in the plugin (`processing/enums.py`): one `// MP` / `# MP` line each.
- With Official chosen, FP builds no Tasty rig; the fork's `mesh_hooks.after_parts` builds the official rig on the
  master skeleton, then the face board when the face is a flipbook (as for Tasty).
- A failure is logged and leaves the plain skeleton; it never fails the import.

## 4. Animation

- An emote or animation imported onto an official-rig character keys the bones as today and switches the rig off, so
  it plays.
- Bake to Controls (operator, panel button): over the scene range (or the action's), keys every control from the
  animated bones: FK controls from their bones, IK effectors from the end bones, poles from the chain's plane, spine
  controls from their bones, switches set to FK; then switches the rig on. Done with Blender's visual transform
  evaluation, frame by frame; the bone action is kept, muted.

## Out of scope

The MetaHuman face board; Epic's modular rig; stretch and squash (not in Epic's rig); retargeting other skeletons;
reading the rig at every export.

## Testing

- Headless (`tests/plugin/official_rig_check.py`): a synthetic mannequin skeleton (the bones the data names) built
  with the official rig:
  - control count and names equal the data's; shapes and colours as the data; spaces parented right;
  - FK: rotating `upperarm_l_fk_ctrl` rotates `upperarm_l`;
  - IK: moving `foot_l_ik_ctrl` moves `foot_l` there (within tolerance) with the knee toward `leg_l_pv_ik_ctrl`;
  - switch: flipping `leg_l_fk_ik_switch` changes which drives `thigh_l`, and hides the other chain;
  - spaces: changing a control's space keeps its world place;
  - Rig off mutes every `OR` constraint;
  - Bake to Controls round trip: a keyed bone pose baked, rig on, bones land within tolerance.
- Data: the JSON's controls equal the dump's (count, names, types); every `bone` exists in the mannequin skeleton.
- Real asset through fpdev: Nemia with the official rig (control renders front/side/top); one emote imported, baked to
  controls, compared at a few frames with the bone-keyed playback.
- All plugin suites stay green; the app builds.
