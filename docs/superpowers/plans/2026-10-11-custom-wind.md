# Custom Wind Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Blender Wind force fields push characters' replayed dynamics (AnimDynamics, rigid bodies, Chaos cloth) in Live and bakes.

**Architecture:** `wind.py` reads the scene's wind fields and turns them into one velocity per frame (gusts and turbulence
from UE's Perlin noise on scene time). The dynamics runner converts it to the game's numbers and hands it to each sim
before stepping; sims without wind run today's code path unchanged.

**Tech Stack:** Blender 5.2 Python (bpy, mathutils, numpy), the fork's plugin (`plugins/Blender/fortnite_porting`).

**Spec:** `docs/superpowers/specs/2026-10-11-custom-wind-design.md`

## Global Constraints

- No wind field in the scene: every result bit-identical to today's.
- Wind field: `field.type == 'WIND'`, local +Z = direction, `field.strength` = speed in m/s; shape/falloff/noise/flow ignored; hidden fields don't blow.
- Object properties `fpmp_wind_gust` (0..1, default 0.4), `fpmp_wind_gust_speed` (Hz, default 0.3), `fpmp_wind_turbulence` (degrees, default 15), drawn in a Physics-tab panel only for wind fields.
- Bodies: `a = WIND_ACCEL * W` (sim space), `WIND_ACCEL = 0.5` per second (tuned; spec); cloth: aerodynamic air velocity `W - V_triangle`.
- No Rig panel additions. Plugin tests run headless through the suite runner (isolated Blender user dirs).

## Review Focus

- A field keyframed off and on: the next frame reads the new strength (Live and bake read the scene each tick).
- A scaled or rotated armature: wind is converted through the armature's world matrix, so downwind stays downwind.
- Two fields blowing opposite ways at equal strength (gust 0): no push.
- A field with strength 0 or a non-finite value: no wind, no NaN.
- A character without dynamics or cloth: nothing reads fields (no cost).

---

### Task 1: wind.py — fields, velocity, gust properties and panel

**Files:**
- Create: `plugins/Blender/fortnite_porting/processing/context/wind.py`
- Modify: `plugins/Blender/fortnite_porting/operator/rig_ui.py` (register/unregister call)
- Test: `tests/plugin/wind_check.py` (new)

**Interfaces:**
- Produces: `wind.fields(scene) -> list[Object]`; `wind.velocity(fields, time) -> mathutils.Vector | None` (world, m/s;
  None when no field blows); `wind.scene_time(scene) -> float`; `wind.WIND_ACCEL` (re-exported from dynamics_solver, 0.5); `wind.register()/unregister()`.

- [ ] Write `wind_check.py`: still field (gust 0, turbulence 0) blowing along its +Z at its strength; rotated field
  blows along its rotated Z; two opposite equal fields give a zero vector; gust range stays within strength*(1±gust)
  and is deterministic for a time; turbulence keeps the angle to the axis within the turbulence; hidden and non-wind
  fields skipped; zero strength and NaN strength give None; property defaults; panel poll true only for wind fields.
- [ ] Run: suite.sh wind_check.py — fails (module missing).
- [ ] Implement `wind.py` (perlin from `.perlin`; seeds 31.7, 77.1, 151.3 per the spec).
- [ ] Run: suite.sh wind_check.py — passes.

### Task 2: the solvers take a wind

**Files:**
- Modify: `processing/context/dynamics_solver.py` (Sim: `self.wind = None`; body acceleration), `rigid_solver.py`
  (RigidSim: `self.wind = None`; added to `_gravity`), `cloth_solver.py` (ClothSim: `self.wind = None`; `_aero` air velocity)
- Test: `tests/plugin/dynamics_check.py`, `rigid_check.py`, `cloth_check.py`

**Interfaces:**
- Consumes: `wind.WIND_ACCEL`.
- Produces: attribute `wind` on Sim and RigidSim (Vector, component space, cm/s, or None) and on ClothSim (Vector,
  the cloth's world, cm/s, or None).

- [ ] Tests: a free AnimDynamics body with sideways wind moves downwind by WIND_ACCEL*W over a frame (no gravity);
  wind None leaves the hanging-body result identical; a rigid body's gravity gains WIND_ACCEL*W; a loose cloth with
  Drag moves downwind, without Drag it doesn't.
- [ ] Run: fail. Implement. Run: dynamics/rigid/cloth suites pass.

### Task 3: the runner hands the wind over

**Files:**
- Modify: `processing/context/dynamics_runner.py` (`Runner.tick`: read fields once per tick, convert, set on sims)
- Test: `tests/plugin/wind_check.py` (a bake-level check on a synthetic dynamics armature)

**Interfaces:**
- Consumes: `wind.fields`, `wind.velocity`, `wind.scene_time`; sims' `wind`.

- [ ] Test: a Runner on a minimal armature (one AnimDynamics body) with a wind field: the body's tip moves downwind
  (in Blender world) over a few ticks; with the field hidden, identical to no field.
- [ ] Run: fail. Implement: `W_world_game = FLIP @ v / unit`; component `q.conjugated() @ W`; sims get the component
  vector, cloth sims the world one; None when `velocity` is None.
- [ ] Run all plugin suites; compare.sh on reference characters (no field: no differences); a real import with a field
  (Chun-Li, Joni): hair and cloth lean downwind, finite. Tune WIND_ACCEL if the default field looks too weak/strong;
  record the value in the spec.
- [ ] Commit (wording approved by the user), publish when asked.
