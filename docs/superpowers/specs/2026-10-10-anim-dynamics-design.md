# Dynamic bones 1: AnimDynamics, baked

## Goal

Characters' `dyn_*` bones driven by the game's AnimDynamics nodes (bracelets, pendants, wings, tassels) swing in
Blender as in game: after an emote import, and from a Rig panel button on any animation, a bake runs the engine's
solver over the animation with the character's own settings and writes the result as an NLA layer. Sub-project 2
(RigidBody / RBAN chains from physics assets) is out of scope here.

## What the game does (probe 2026-10-10, VibrantShell, UE 5.8 source)

- The body's `<Char>_AnimBP` runs `CopyPoseFromMesh -> ControlRig -> RigidBody -> AnimDynamics x14 -> Root`. Each
  AnimDynamics node simulates one body (`bChain` false) on one bone, in RootRelative space, and sees the previous
  node's output, so a pendant's lower link moves with the upper one. Node order comes from the `ComponentPose.LinkID`
  chain (LinkID indexes the class's anim node properties in field order).
- The node settings are cooked on the class default object as `AnimGraphNode_AnimDynamics*` structs (absent values
  are the struct defaults). VibrantShell: box 15 cm, joint offset (-0.1, 0, 0), linear axes locked, angular limits with
  twist about X, angular damping override 0.999, gravity override (0, 0, -980) in sim space, angular spring 25 with
  target (0, 0, 0), SimSpaceAngularAlpha 1 with ExternalAngularVelocity (1, 1, 1), 4 + 1 solver passes.
- The solver (notes in owner devtools: `helpers/animdynamics_algorithm.md`): mass 1 bodies, box inertia, damping as
  `(1 - d)^dt`, gravity, sim-space fictitious forces (Coriolis 0.5, centrifugal, Euler), springs, sequential-impulse
  linear and angular limits (4 biased passes, Euler position / RK4 orientation, bias removed, 1 pass), written back as
  the body's joint transform. One step per game tick, `dt = min(tick, 1/30)`. Quirks kept: the inverse inertia rotated
  as `R^T I^-1 R`, the unrotated tensor on the first step, the zero-target spring doing nothing, twist locked with a
  0.3 bias, the locked-Z case using the raw angle.

## 1. Data (app)

- When a part's anim blueprint has AnimDynamics (or RigidBody) nodes, the export writes the anim blueprint package's
  dump (CUE4Parse JSON) to `<anim class path>.animbp.json` once and names it in the part's meta (`AnimBlueprint`, one
  `// MP` field next to `DeformRig`). No such node: nothing. A dump failure logs one warning and costs only the
  dynamics.

## 2. Plugin reader (`processing/context/dynamics_read.py`)

- `nodes(paths)`: per dump, the AnimDynamics nodes in evaluation order (from the Root's LinkID chain, nearest the
  source first), each as plain data with the engine defaults filled in: bound bone, chain end, simulation space and
  relative bone, body definitions (box, joint offset, constraint setup, collision), damping/bias overrides, gravity,
  springs, sim-space settings, external force, planar and spherical limits, solver passes, alpha. Parts in import
  order (the body first: other parts copy its finished pose).
- Unsupported features (wind, retargeting, curve/bool alpha) are ignored with one log line per node; a node on a bone
  the armature lacks is skipped.
- Import: the character import stores the node list as JSON on the armature data (`fpmp_dynamics`), so a later emote
  import onto that armature finds it.

## 3. Solver (`processing/context/dynamics_solver.py`, no bpy)

- The engine's AnimDynamics step in its own numbers (cm, the game's left-handed axes): bodies, linear limits (locked,
  limited, free; planar; spherical), angular limits (angular range with twist axis X/Y/Z, cone), linear and angular
  springs, damping, gravity (override or default, scale), external force, component linear terms (zero in a bake),
  sim-space angular terms, single bodies and chains, all simulation spaces (the component is fixed at the origin in a
  bake, so World and Actor equal Component).
- Interface: `Sim(node)`; `sim.step(dt, inputs)` where inputs are the bound bones' and root's component transforms in
  game axes; returns the bones' new component transforms. Written in our own words from the notes; no engine code.

## 4. Bake (`processing/context/dynamics_bake.py`, operator in the Rig panel)

- Frames: the armature's animated range (its NLA strips, else its action). Sampling: each scene frame once
  (`frame_set`), reading the local (parent-relative) pose of the bones the nodes need (bound bones, chain bones, their
  ancestors, the root) in game axes (`ue_rest`, the deform rig's `FLIP`, cm). Between frames the sim ticks at the bake
  rate (default 60 Hz, an operator option) on locals interpolated as the engine interpolates keys (linear
  translation, normalized-lerp rotation), composed to component space with earlier nodes' results applied.
- Pre-roll: one second on the first frame's pose before frame one, so bodies start settled as they are in game when an
  emote begins (the engine itself starts from rest at the first pose; the game's character was already idling).
- Output: per frame, each simulated bone's component result made local to its parent's final transform, converted
  to the pose bone's basis, keyed (location and rotation) into a `Dynamics` action on an NLA track above the
  animation, replacing only those bones. Re-baking replaces it; a `Dynamics` toggle in the Rig panel mutes the track.
- Emote import runs the bake at the end when the armature has `fpmp_dynamics` (the official rig's controls stay
  aside as they already do for the animation). The button bakes the current animation on demand.
- No dynamics data, or no animation: the button is disabled with the reason; nothing else changes.

## Out of scope

RigidBody/RBAN (sub-project 2), wind, retargeting settings, LOD thresholds, live (non-baked) simulation.

## Testing

- Solver (plain Python, no Blender): hand-computed single steps against the notes' formulas (damping factors, gravity,
  nailed-joint correction, angular limit impulses, RK4 orientation); a body under gravity settles inside its angular
  range at the limit it leans on; twist converges by the 0.3 bias per step; the zero-target spring adds nothing and a
  non-zero one aligns the axis; a non-cubic box uses `R^T I^-1 R` (differs from the textbook form); `dt` clamps to 1/30;
  chains keep their joints together.
- Reader: VibrantShell's dump gives 14 nodes in the LinkID order (pendant 1 before 2 before 3), its values and
  defaults filled.
- Bake (headless Blender, synthetic arm with a `dyn_` bone): rest pose with zero gravity stays at rest; a swung arm
  makes the bone lag and return; keys land only on simulated bones in a `Dynamics` track; the toggle mutes it;
  an armature without data bakes nothing; game axes round-trip (bake of an identity sim reproduces the animation).
- Real asset (devtools): VibrantShell with an emote: 14 bones keyed, every bone's swing within its angular limits,
  joints on their animated positions, no NaN, bake time logged (target under 10 s for a 20 s emote).
