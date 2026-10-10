# Dynamic bones 2: RigidBody (hair locks, necklaces, earrings), baked

## Goal

Characters' physics-asset chains (hair locks, hair tails, necklaces, earrings, hanging props) move in Blender as in
game: the anim blueprint's RigidBody node is replayed with the engine's Chaos immediate solver over the animation and
baked into the same `Dynamics` layer as the AnimDynamics bones (sub-project 1), in graph order with them and with the
small skeletal controls that follow it (Constraint, ModifyBone).

## What the game does (probe 2026-10-10, VibrantShell; UE 5.8 source)

- Body ABP: `... ControlRig -> RigidBody -> AnimDynamics x14 -> Root`. FaceAcc ABP: `CopyPoseFromMesh -> RigidBody ->
  Constraint x3 -> ModifyBone x3 -> Root` (the hair tail pulled toward neck_01/spine_05 and nudged).
- RigidBody node: `OverridePhysicsAsset` (else the mesh's), BaseBoneSpace (pelvis / neck_02), gravity override
  (0,0,-980) in world space, SimSpaceSettings with `WorldAlpha` 1 (the base bone's motion pushes the chains), velocity
  and acceleration clamps per character.
- Physics assets: capsules only; `Simulated` bodies move, the others are kinematic and follow the pose (spine, neck,
  head, clavicles, chain bases). Joints: locked linear, limited twist/swing, soft by default (stiffness 50, damping 5,
  most tuned), SLERP drives on most; the collision table leaves hair-to-head/neck/shoulder pairs colliding.
- The solver (owner devtools notes `helpers/rigidbody_algorithm.md`): mass from capsule volume to the 0.75 power,
  inertia conditioning, per-joint mass conditioning; one step per game tick (rolling step time, at most 1/30 s, up to 4
  sub-steps); integrate with gravity and lerp damping and the sim-space terms; kinematic targets with the engine's
  sub-step fractions; 6 position iterations of the linearised ("cached") joint solver root to tip (locked linear rows
  together, twist and pyramid swing rows, soft rows with the engine's unit scales, drives) then contacts; implicit
  velocity and 1 velocity pass on hard rows; 1 projection pass (teleport over 5 cm, alpha 1, 10% to velocity);
  write-back of simulated bodies only.

## 1. Data

- App (done): each part's meta names its physics asset dump (`PhysicsAsset`) next to `AnimBlueprint`.
- `physics_read.py`: bodies (bone, type, shapes, mass settings, damping, gravity), constraints (bones, frames, linear/
  cone/twist limits with soft settings, drives, projection, parent dominates, mass conditioning, disable collision),
  the disabled pairs, the asset's solver settings; engine defaults where the dump is silent.
- `dynamics_read.py` returns every replayed node in graph order with a `kind`: `anim_dynamics` (as now),
  `rigid_body` (node settings plus its physics asset), `constraint` (FAnimNode_Constraint setups and weights),
  `modify_bone` (FAnimNode_ModifyBone). Other skeletal controls are named in one log line and not replayed.
- The armature keeps them under `fpmp_dynamics` as now (`{"scale", "nodes"}`), the physics asset inlined in its node.

## 2. Solver (`processing/context/rigid_solver.py`, `rigid_joints.py`, `rigid_contacts.py`; no bpy)

- Bodies: capsule/sphere/box mass, CoM and principal inertia; mass scale/override, COM nudge, inertia scale; inertia
  conditioning.
- Joints: the cached linear solver as the engine runs it by default (rows linearised once per step; locked linear rows
  solved together; twist and pyramid swing rows; soft limits in acceleration mode with the angular x100000 / x1000 and
  linear x1.5 / x1.2 scales; SLERP and twist/swing angular drives; mass conditioning; sort root to tip; velocity rows
  on hard rows; projection). The non-linear solver (elliptical cone) is not ported: an asset asking for it logs one
  line and uses the linear one.
- Contacts: capsule and sphere pairs (closest points of segments, kept within the 3 cm cull distance), position rows
  without friction, velocity rows with restitution 0.3 and friction 0.7; box shapes collide as their bounding capsule.
  One contact per pair (the engine builds a manifold of up to two for near-parallel capsules): the one deviation kept.
- Node wrapper `RigidSim(node, parent_of)` with the same interface as the AnimDynamics `Sim`: `bones()`,
  `evaluate(dt, cs, root)`. Spaces (Component, World as Component with the component fixed, BaseBone), gravity into
  the space each frame, sim-space motion from the base bone (velocity, acceleration, angular velocity and acceleration
  with the history and clamps, WorldAlpha, damping targets, drag), step time and sub-steps, kinematic targets, reset on
  the first evaluation (output = input), write-back of simulated bodies only.
- Post nodes (`processing/context/bone_controls.py`): Constraint (translation/rotation/scale/parent per axis, weights,
  ref-pose or no offset) and ModifyBone (ignore/replace/additive in component, parent, bone or world space) as the
  engine evaluates them.

## 3. Bake

- `dynamics_bake` runs the node list in order: each kind's sim takes the pose reaching it and returns component
  transforms; RigidBody bodies and post-node bones join the simulated (twinned, keyed) set; children follow as now.
- Import (`mesh_hooks._dynamics`) reads both metas; emote import, Simulate and the toggle are unchanged.

## Out of scope

The non-linear joint solver, world geometry, cloth colliders, CCD, physics fields, deferred simulation, bone velocity
transfer, freeze-pose, LOD culling, float32 rounding.

## Testing

- Reader: synthetic physics dump and ABP with every kind; defaults; disabled pairs by name; real FaceAcc dump (39
  bodies, 26 joints, 664 disabled pairs) in the devtools check.
- Bodies: capsule mass and inertia against the notes' worked example (r 1.5, L 6: 0.116 kg, 0.69 / 0.124), override and
  nudge; conditioning shrinks a chain capsule's inverse inertia into 0.3-0.45x.
- Joints: a locked pin under gravity holds its connector within the position tolerance; a soft swing limit removes
  ~94% of an overshoot per pass at 60 Hz with (50, 5); the pyramid limits swing about Z and Y independently; twist sign
  convention; projection teleports a child over 5 cm and adds 10% velocity; a SLERP drive pulls toward the target; mass
  conditioning raises a light parent to 0.2x.
- Contacts: two capsules overlapping get pushed apart along the segment normal; a disabled pair doesn't.
- Node: reset frame output = input; a still pose under gravity settles; base-bone motion with WorldAlpha 1 swings a
  chain, with 0 it doesn't; step time and sub-step fractions (1/3, 7/9).
- Post nodes: each mode/space against hand-computed transforms.
- Bake (headless, synthetic head + hair chain): keys on twins, collision keeps the chain out of a head capsule.
- Real (devtools): VibrantShell FaceAcc + body with an emote: every simulated bone keyed, joints within 0.5 cm, swings
  within limits + 2 deg, no NaN, bake time logged.
