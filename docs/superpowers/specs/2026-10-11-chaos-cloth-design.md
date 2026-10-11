# Dynamic bones 4: Chaos Cloth (coats, skirts, capes), baked and live

## Goal

Parts whose coats, skirts and capes are cloth in game move in Blender as in game: the mesh's clothing asset is
replayed with the engine's Chaos cloth solver over the animation, the render mesh follows the cloth through the
engine's render mapping, and the anim blueprint takes its cloth path (its rigid-body fallback for those bones off).
Bake and the live preview both drive it.

## What the game does (probe 2026-10-11, Joni the Red F_MED_Silencer; UE 5.8 source)

- The skeletal mesh holds a classic clothing asset (`ClothingAssetCommon` "Silencer_Cloth"): a 340-vertex,
  586-triangle simulation mesh skinned to 56 bones (up to 8 weights, spine, thighs and the coat's `dyn_jacket*`
  bones), weight maps by `EChaosWeightMapTarget` id (max distance, backstop, anim drive, drag, lift...), no cooked
  tethers or self-collision indices, inverse masses zero (computed at runtime from Density), a reference bone (the
  simulation's local space), a collision physics asset (`F_MED_Silencer_CapeCollide`) and two configs:
  `ChaosClothConfig` (density, tether scale, collision thickness and friction, damping, drag, lift, gravity override,
  anim drive, velocity scales) and `ChaosClothSharedSimConfig` (iterations, sub-steps; defaults here).
- The render mesh's cloth sections each carry a mapping per render vertex (`FMeshToMeshVertData`: barycentric
  position, normal and tangent with an offset along the interpolated normal, three simulation vertices, a weight).
  CUE4Parse reads it (`FSkelMeshSection.ClothMappingDataLODs`) but writes none of it to JSON.
- The anim blueprint: `Cloth Off = not bClothEnabled` switches the RigidBody coat off when cloth runs. Its event
  graph calls `Cloth Controller Function DEPRECATED` each tick with a per-state table: velocity scales, gravity in a
  joint's space (the emote state: (-980, -500, 0) in `pelvis`), drag and lift ranges, and core settings (stiffness,
  damping, friction, anim drive, iterations). The library function pushes them through `ChaosClothingInteractor`
  (`SetVelocityScale`, `SetGravity`, `SetWind`, `SetMaterial`, `SetDamping`, `SetCollision`, `SetAnimDrive`,
  `SetLongRangeAttachment`). Wind gusts are random (game time and a random stream). The graph also sets the mesh's
  teleport rotation threshold (15 degrees).
- The solver (owner devtools notes `helpers/chaos_cloth.md`): per tick, the reference bone's motion moves the local
  space with the velocity scales and fictitious forces; kinematic particles (max distance 0) follow the skinned
  simulation mesh; dynamic ones integrate gravity, aerodynamics and damping; then iterations of the XPBD
  constraints (edge, bending, area, long-range attachment, anim drive, max distance, backstop) and the colliders
  (physics asset spheres, capsules, tapered capsules, boxes, convexes) with thickness and friction; teleport and reset
  when the reference bone jumps past the thresholds.

## 1. Data (app)

- New partial `ExportContext.Cloth.cs`: for a skeletal mesh with clothing assets, one file next to it
  (`<mesh>.cloth.json`): each clothing asset's exports (the asset, its two configs) as CUE4Parse serializes them, its
  physics asset dumped through `DumpPackage` (path), and the render mapping per exported LOD: for each section with
  cloth, `BaseVertexIndex`, `NumVertices`, the asset index and asset LOD, and its mapping entries (12 floats, four
  indices, the weight) for the LOD's own mapping (bias 0).
- `BaseMeta.Cloth` (string path) set on every part whose mesh has clothing assets, independent of the anim blueprint.
- The anim blueprint dumps (`AnimBlueprint`, `AnimScript`) are written for a part with cloth even without dynamics
  nodes, so its switches and cloth controller can run.

## 2. Reader (`processing/context/cloth_read.py`, no bpy)

- Simulation mesh as numpy arrays: positions and normals (cm, component space at the reference pose), triangles,
  bone weights mapped to bone names, weight maps by target name, `NumFixedVerts`, reference bone name, collision
  physics asset through `physics_read`.
- Configs with the engine's defaults where the dump is silent; a property that is a (Low, High) pair is weighted per
  particle by its weight map as the engine does.
- Render mapping by LOD index; the import matches it to the imported LOD by index and vertex count, else no cloth for
  that mesh (one log line).
- Per-state overrides: `anim_script` runs the event graph with `bClothEnabled` true; the `Cloth Controller Function`
  call hands over its asset name, per-state table and core settings (as the gravity function hands over its table).
  The reader turns them into what the library pushes for the state (the row, else `OnGround_Standing`):
  `SetVelocityScale(row linear, angular, fictitious)`, `SetGravity(1, override, row gravity in the row joint's frame)`,
  `SetWind(row drag, row lift, air density 1e-6, no wind, outer drag and lift (0.07, 0.5))`,
  `SetMaterial(edge, bending, area)`, `SetLongRangeAttachment(tether stiffness, scale (1, 1))`,
  `SetCollision(thickness, friction, no CCD, self thickness)`, `SetDamping`, `SetAnimDrive`, iterations and sub-steps
  from the core settings. Moving states use their row with the player's movement at rest. A gust's random wind stays
  zero. The graph's `SetTeleportRotationThreshold` / `SetTeleportDistanceThreshold` calls on the mesh are kept too.

## 3. Solver (`processing/context/cloth_solver.py`, numpy, no bpy)

- `ClothSim(cloth, parent_of, state)`: `bones()` (used bones, reference bone, collider bones), `reset(cs)`,
  `step(dt, cs)` returning particle positions in component space (cm).
- Follows the notes' order and constants: masses from density and triangle area, kinematic particles, local space with
  linear / angular velocity scales and fictitious forces (the config's model), gravity (override or world, in the
  state's joint space when the controller says so), drag and lift per triangle against the local-space wind, global and
  local damping, iterations and sub-steps from the shared config, XPBD edge / bending / area with their weight maps,
  long-range attachment (tethers built at load as the engine builds them when the cook has none), anim drive, max
  distance, backstop, collider contacts with thickness and friction, teleport and reset thresholds.
- Out: self-collision, world collision, ChaosClothAsset (panel cloth, Dataflow), cloth LOD transitions, wind gusts.

## 4. Runner, bake and live

- `dynamics_runner.Runner` gains cloth: its sims tick after the bone nodes on the final component pose (the engine
  simulates cloth from the finished pose), with the same pre-roll, sub-steps and component motion. `KEY` keeps the
  cloth next to the nodes (`{"scale", "nodes", "cloth"}`).
- Import: the mapping goes onto each part's vertices before the parts are joined (marked by part), and is relinked
  onto the joined mesh's cache after.
- Render mesh: a hidden point cache object per cloth mesh holds the simulation's positions and normals per frame (mesh
  space, Blender units); a geometry nodes modifier after the Armature modifier maps the cloth section's vertices from
  it with the render mapping (stored as integer and float attributes at import) and leaves every other vertex as
  skinned. A frame without cached data shows the skinned mesh.
- Bake fills the cache for the bake range in the same pass as the bone bake; live writes the current frame's slot.
  The Dynamics toggle mutes the modifier with the twins.

## Out of scope

Self-collision, world collision, wind gusts, ChaosClothAsset panel cloth, cloth LOD blending, GPU skin cache tangent
recompute, float32 rounding, one-sided tapered capsules (collide two-sided), render mappings with several influences
per vertex (left skinned), velocity and acceleration clamps (off by default).

## Testing

- App: Joni the Red's export writes the cloth file with 340 simulation vertices, 586 triangles and a mapping entry
  for every vertex of each cloth section.
- Reader: synthetic cloth dump (all weight-map targets, defaults); the real Joni file (bones resolved, weight maps by
  name, mapping matched to LOD0); per-state overrides from the Joni anim blueprint (emote: velocity scale 0.05,
  gravity in pelvis space).
- Solver, each against hand-computed values: a kinematic particle follows its skinned position; a free particle
  falls under gravity with damping; an edge constraint restores its rest length at stiffness 1; max distance clamps a
  particle to its sphere; backstop keeps it outside; a capsule collider pushes a particle out to radius + thickness;
  friction slows a sliding particle; velocity scale 0 keeps the cloth still in local space under a moving reference
  bone, 1 leaves it behind in world; teleport past the rotation threshold resets velocities.
- Render mapping: a render vertex on a simulation triangle lands on the barycentric point plus the normal offset.
- Bake (headless, synthetic skirt on a moving pelvis): cache filled, the modifier moves the mapped vertices and leaves
  the others, toggle mutes it.
- Real (devtools): Joni the Red with an emote: cloth baked, no NaN, no particle past its max distance, coat outside
  the collision capsules within thickness + 0.5 cm, RigidBody coat switched off; renders in `devtools/out/cloth`.
