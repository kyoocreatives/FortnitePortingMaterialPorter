# Chaos Cloth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Replay Fortnite's classic Chaos cloth (coats, skirts, capes) in the plugin, baked and live, with the render mesh
following the cloth through the engine's mapping.

**Architecture:** The app writes `<mesh>.cloth.json` (cloth exports, collision physics asset, render mapping per LOD).
`cloth_read` turns it into a JSON-able cloth dict kept on the armature next to the dynamics nodes; `cloth_solver`
(numpy) steps it after the bone nodes on the final pose; `cloth_render` maps the render mesh from a per-frame point cache
with a geometry nodes modifier.

**Tech Stack:** C# (CUE4Parse), Blender 5.2 Python, numpy, geometry nodes.

**Spec:** `docs/superpowers/specs/2026-10-11-chaos-cloth-design.md`; solver reference: owner notes
`fpfork-private/devtools/helpers/chaos_cloth.md` (sections cited as N§x).

## Global Constraints

- Upstream FP files get only one-line `// MP` / `# MP` hooks; fork code in `MaterialPorter/` partials and new modules.
- Comments one or two short lines, only the why; files under ~900 lines.
- An odd dump costs the cloth, never the import (log one line).
- Plugin checks: `tests/plugin/cloth_check.py`, headless, exit 0; the existing dynamics/rigid/rig suites stay green.

## Review Focus

- A cloth asset without a MaxDistance map: every particle kinematic (N§1.6), the cloth just follows skinning.
- An imported LOD whose vertex count matches no exported LOD: no cloth on that mesh, the import goes on.
- A mapping with several influences per vertex: skipped with a log line, mesh stays skinned.
- Live preview scrubbing backwards: the simulation restarts at that frame, the cache slot is rewritten.
- A part with cloth but no dynamics nodes: the cloth still runs (no `AnimBlueprint` meta needed).

---

### Task 1: App writes the cloth file

**Files:** Create `src/FortnitePorting.Exporting/MaterialPorter/ExportContext.Cloth.cs`; modify `Models/ExportMeta.cs`
(`Cloth`), `Context/ExportContext.Fortnite.cs` (hook), `MaterialPorter/ExportContext.AnimBlueprint.cs` (anim script for
cloth parts).

- [x] `Cloth(USkeletalMesh)` writes Assets, Exports (types containing "Cloth"), PhysicsAssets (dumped), Lods
  (NumVertices, Sections: BaseVertexIndex, NumVertices, Asset, AssetLod, Mapping rows of 17 numbers).
- [x] Build (`testapp.sh test`), export Character_Silencer: file has 1 asset, LOD0 section base 13322 with 1175 rows.

### Task 2: Render mapping (`processing/context/cloth_render.py`)

**Interfaces — Produces:** `attach(obj, sections, offsets, unit) -> int`, `cache(obj, points) -> Object`,
`write(cache, start, pos, nrm)`, `write_frame(cache, frame, pos, nrm)`, `set_on(obj, on)`. Positions and normals in the
render mesh's space (Blender units); `sections` rows as exported; `offsets[asset]` = that asset's first point.

- [x] Checks: mapped vertex = barycentric point + normal offset; per frame; others skinned; outside cache skinned;
  `write_frame` grows the cache; off = skinned.
- [x] Engine formula (N§6.2): third barycentric `1 - b0 - b1`; offset `dist * unit`; blend `1 - flag/65535`
  (0xFFFF = skinned, partial flags lerp). Check a partial flag halfway.

### Task 3: Reader (`processing/context/cloth_read.py`)

**Interfaces — Produces:** `read(path, bones) -> list[dict]`, one per asset:
`{"name", "positions" [[x,y,z]], "normals", "triangles" [[a,b,c]], "bones" [names], "influences" [[[bone, w], ..]],
"maps" {target name: [..]}, "reference" bone, "config" {...}, "shared" {...}, "physics" (physics_read.asset),
"tethers" [[[s, e, len], ..] batches], "sections" (Task 2 rows for the LOD matched later), "lods" [{"vertices", "sections"}]}`.
`overrides(capture, state) -> dict` (Task 4).

- [x] Checks on a synthetic file: weight map ids to names (N§1.4), config defaults (N§2.1) with dump values on top,
  (Low, High) pairs, BoneData `BoneIndices[k]` / `BoneWeights[k]` keys to `UsedBoneNames`, reference bone =
  `ReferenceBoneIndex` resolved by the caller's skeleton order, tethers by `bUseGeodesicDistance`, LODs kept.

### Task 4: Per-state settings from the anim blueprint

**Files:** `anim_script.py` (capture `Cloth Controller Function` like the gravity function; `SetTeleport*Threshold`
on the mesh), `dynamics_read.py` (`bClothEnabled` true for parts with cloth), `cloth_read.overrides`.

- [x] Checks: a synthetic graph calling the controller hands over asset, table and core; `overrides` gives the row's
  velocity scales, gravity + joint, drag/lift, core stiffness, damping, friction, thickness, anim drive, iterations;
  a missing state falls back to `OnGround_Standing`; a `bClothEnabled` switch picks its true side when cloth exists.

### Task 5: Solver (`processing/context/cloth_solver.py`)

**Interfaces — Produces:** `ClothSim(cloth, parent_of, state)`: `bones()`, `step(dt, cs, component)` -> particle
positions (N,3) and normals in component space (cm); `reset` on first call.

- [x] Masses (N§2.3), kinematic rule (N§1.6), skinning (N§3.4), dt smoothing and iterations (N§3.2, 4.1), reference
  velocity scaling and pre-sim transform (N§3.6-3.7), gravity (N§3.8), aerodynamics and fictitious force (N§3.9),
  damping (N§4.3), stiffness mapping (N§5.1), edges, bending springs/elements, area, tethers, max distance, backstop,
  anim drive (N§5.2-5.9), colliders with friction (N§5.10), normals (N§4.5), teleport thresholds (N§3.1).
- [x] Checks against hand-computed values (spec Testing list).

### Task 6: Runner, bake, live, import

- [x] `Runner` builds cloth sims from `KEY["cloth"]`, ticks them after the nodes, keeps per-frame results;
  `dynamics_bake.bake` fills each cloth mesh's cache; `dynamics_live` writes the frame's slot; `set_on` toggles modifiers.
- [x] `mesh_hooks._cloth(ctx)`: reads parts' `Cloth` metas, matches LOD, attaches mapping, stores cloth on the skeleton.
- [x] Check: synthetic skirt on a moving pelvis bakes, mapped vertices move, toggle mutes.

### Task 7: Joni the Red (devtools)

- [x] Export, import, bake an emote headless: no NaN, particles within max distance, RigidBody coat off, renders.
