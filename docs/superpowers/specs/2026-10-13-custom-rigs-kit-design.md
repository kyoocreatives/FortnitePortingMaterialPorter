# Creature, LEGO and vehicle rigs on the official rig's standard

## Why

An audit of the three custom rigs (wolf, LEGO cow, LEGO minifig, Alokin car; renders from four angles plus
close-ups) found them far from the official (UE) rig's quality:

- Creatures put one identical yellow ring on every game bone: spine, neck and tail rings stack inside the body and
  can't be told apart. There is no body (COG) control, the pelvis is a flat square cutting the torso, face bones are
  all the same tiny diamond, poles float with no line to their joint.
- The LEGO cow's legs are not found as legs (two bones, no foot): ten FK "arms", no foot IK, rings buried in the legs.
- LEGO figures: leg and arm dials overlap each other and the hips, the head ring sits inside the head at mouth
  height, the pelvis plate and root footprint stack, the root's chevron reads sideways.
- Vehicles: the body control is a slab floating over the roof; drive, steer and drift arrows are all yellow and
  pile up at the nose; suspension arrows stab through the wheels.

The user allowed starting them over to match the new standard.

## The standard (from the official rig)

- **Separate controls.** Each animator control is its own bone (`<part>_ctrl`); game bones follow them through
  constraints named with a prefix, so the rig can be switched off (game bones play their own keys) and an imported
  animation can be baked onto the controls. Game bones keep names, rest pose and hierarchy.
- **Epic's gizmos and colours.** Shapes come from the official rig's decoded gizmo library (Circle_Thick, Box_Thick,
  Sphere_Solid, Hexagon_Thin, Octagon_Thin, RoundedSquare_Thick, Arrow_Thick, Arrow4_Thick...). Colours follow Epic:
  left blue, right red, centre yellow to orange (root yellow, body orange). Wire widths: root 3.5, limb 2.5,
  digit 1.5.
- **Sized to the body.** A ring around a bone is sized from the skinned mesh around it (the cross-section the bone
  moves), so it sits just outside the surface; clamped so neighbours don't swallow each other.
- **Oriented for the animator.** FK controls take their bone's frame (rings perpendicular to the bone). IK and world
  controls (feet, body, root) are world-aligned, flat to the ground, facing the creature's forward.
- **Hierarchy like UE.** global_ctrl > root_ctrl > body_ctrl (COG) > hips/spine; feet IK under root; poles under
  their foot's parent space; tail and face FK under their bones' controls.
- **FK/IK per limb** with matching (switching keeps the pose), the inactive side's controls hidden. Poles show a
  thin line to their joint.
- Collections: Controls, Secondary, FK, Mechanics, Game Bones (rig_style). Settings live on a hidden bone; the panel
  shows them.

## Shared kit: `rig_kit.py`

- `gizmo(name)`: the official rig's shape object (`OR_<name>`), created on demand from official_rig.json.
- `COLORS`: Epic's palette by role and side; `WIDTHS` by role.
- `fit_radius(obj, bone, along)`: the skinned mesh's radius around a bone (vertices weighted to it, distance to the
  bone's line, 85th percentile), else a fraction of the bone length.
- `control(...)`: makes a control bone in edit mode at a frame, parent given; `style(...)` in pose mode sets gizmo,
  colour, width, shape transform, collection.
- `follow(obj, bone, control)`: the game bone copies its control (Copy Transforms, prefix `RK `), with a rest offset
  when frames differ.
- `pole_line(obj, joint, pole)`: an unselectable line bone stretched from the joint to the pole.
- `set_on(obj, on)`, `bake_to_controls(obj, start, end)`: generic: each control keyed so its game bone lands where the
  animation puts it (FK: the bone's pose through the rest offset; IK feet: the foot; poles: placed off the knee).
- `switch(obj, limb, ik)`: FK/IK with matching for a limb described in the rig's metadata.
- Metadata on the armature (`fpmp_rig_kit` JSON): limbs (name, side, fk controls, ik controls, switch setting,
  bones), controls -> game bone, for the panel and bake.

## Creature rig (rebuilt)

Survey kept and improved:
- Legs of two bones (LEGO cow) are legs: IK on both, the target at the last bone's tail.
- Forward from pelvis to head, else -Y.

Controls:
- global_ctrl: Hexagon_Thin on the ground, sized to the footprint. root_ctrl: Arrow_Thin pointing forward.
- body_ctrl: Octagon_Thin, horizontal, at the pelvis, around the body. hips_ctrl: Circle_Thick around the pelvis.
- Spine FK: RoundedSquare_Thick perpendicular to each spine bone, fitted. chest_ctrl: Circle_Thick at the last
  spine bone before the neck.
- Neck FK: Circle_Thick fitted. head_ctrl: Circle_Thick around the head.
- Tail FK: Circle_Thick fitted, tapering.
- Legs/arms IK: foot Box_Thick at the ankle, flat; ball/toe Circle_Thick; pole Sphere_Solid with a line. FK:
  Circle_Thick fitted per bone (FK collection, shown when the limb is FK).
- Toes, claws, fingers: Arrow_Thick digits. Ears, jaw, tongue, eyelids: Arrow_Thick secondary, pointing along the
  bone. Eyes: a look-at RoundedSquare in front of the face with a Circle per eye.
- Face board as today.

## LEGO rig (rebuilt)

Same skeleton for every figure (FigureBody_r2). Controls, all FK, each limb locked to its real axis:
- global_ctrl (Hexagon on the ground), root_ctrl (Arrow), body_ctrl (Octagon at the hips, around them).
- torso_ctrl: RoundedSquare around the torso; head_ctrl: Circle around the neck, above the shoulders;
  head_accessory: Box around the hair/hat (secondary).
- legs: Circle on the hip axis, outside the leg, each on its own side (no overlap). arms: Circle on the shoulder
  axis outside the shoulder. hands: Circle at the wrist around the wrist axis.
- Face board as today.

## Vehicle rig (restyled; mechanics kept)

Mechanics, bones and settings stay. Controls restyled:
- CR_Main: a RoundedSquare footprint around the car, yellow (root).
- CR_Drive: Arrow_Thick off the nose, orange. CR_Steer: curved double arrow around the front axle, green-yellow
  (its own colour). CR_Drift: curved arrow behind the rear axle, purple.
- CR_Body: Box_Thick around the body (cabin height), orange, instead of the slab.
- Wheels: Circle_Thick on the wheel's outer face, side colours; suspension (lift) arrows beside the wheel, outside
  it, short.
- Parts (doors, turret): Box_Thick around what they move (as today), secondary.

## Panel

The Rig panel treats kit rigs like the official rig:
- Rig on/off and Bake to Controls.
- IK/FK rows per limb plus All/Legs/Arms groups, built from the kit metadata.
- The picker and pie stay official-only for now.

## Testing

- rig_check keeps its behaviour checks (IK reaches, eyes aim, wheels spin...) updated to the new names.
- New kit checks: every game bone follows its control at rest (no pose change when the rig is built); rig off
  restores keys; bake lands an animation; FK/IK switch keeps the pose; ring sizes sit outside the mesh.
- Audit renders (four angles plus close-ups) before/after on wolf, cow, LEGO, car, judged critically.

## Out of scope

Pickers for creatures; foot roll pivots for creatures (later); vehicle mechanics changes.
