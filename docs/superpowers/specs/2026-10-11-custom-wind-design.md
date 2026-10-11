# Custom wind for character dynamics (design)

2026-10-11. Status: approved (Blender Wind force fields; no Rig panel additions).

## Goal

Let the user add wind to a scene so imported characters' simulated parts (AnimDynamics chains, rigid-body
chains, Chaos cloth) get idle movement, in Live and in bakes. It is an artistic layer on top of the replay of the
game's dynamics: with no wind field in the scene every result stays exactly what it is today.

## User-facing behaviour

- **Wind fields are Blender's own.** An empty with a Wind force field (`field.type == 'WIND'`). Its local +Z is the
  direction (the viewport arrow), `field.strength` is the speed in m/s. Both can be keyframed. Several fields add up.
  The field's shape, falloff, noise and flow settings are ignored: the wind is uniform in space. Fields that are hidden
  (viewport) or whose collection is excluded don't blow.
- **Gusts and turbulence** are three properties every object gets (`fpmp_wind_gust`, `fpmp_wind_gust_speed`,
  `fpmp_wind_turbulence`), drawn in a "Character Wind" panel in the Physics tab when the active object is a wind field:
  - Gust (0..1, default 0.4): how far the speed swings about `strength` (0.4 = between 60% and 140%).
  - Gust Speed (Hz, default 0.3): how fast it swings.
  - Turbulence (degrees, default 15): how far the direction wanders from the field's axis.
- Every character responds the same way (no per-character control, no Rig panel additions): the user adds fields with
  Blender's Add > Force Field > Wind.

## How the wind is computed

`wind.py` (new, `processing/context/`, no bpy apart from reading fields):

- `fields(scene)`: the scene's visible wind field objects.
- `velocity(fields, time)`: the summed wind velocity (world, m/s) at scene time `time` (seconds,
  `frame / fps`): per field
  - speed = `strength * (1 + gust * perlin(time * gust_speed + 31.7))`, never below 0;
  - direction = the field's +Z, turned by `turbulence * perlin(time * gust_speed * 0.5 + 77.1)` about the field's X
    and by `turbulence * perlin(time * gust_speed * 0.5 + 151.3)` about its Y;
  - `perlin` is UE's PerlinNoise1D (`perlin.py`, already used by the rig harmonics and the state wind).
  The seeds decorrelate the three signals; the same scene time always gives the same wind (Live and bakes match).
- The runner converts it to the game's numbers once per frame: cm/s in the character's component space (through the
  armature's world matrix, the same conversion `to_game` does for bones), and in the game's world space for cloth.

## How each simulation feels it

- **Chaos cloth** (`cloth_solver`): the aerodynamic force already models drag and lift from the air's velocity
  relative to each triangle; today the air is still (`v = -V_triangle`). With wind it becomes `v = W - V_triangle`
  (W in the cloth's world space, cm/s). Cloth whose asset has no drag/lift coefficients ignores wind, as in Chaos.
- **AnimDynamics** (`dynamics_solver.Sim`) and **rigid bodies** (`rigid_solver.RigidSim`): the game has no wind model
  for these. Wind adds an acceleration `a = WIND_ACCEL * W` (W in the node's simulation space) to the
  gravity they already integrate, with `WIND_ACCEL = 0.5` per second (a 2 m/s wind pushes like 0.1 g), tuned on Chun-Li and Joni: at 1.5 a
  2 m/s wind pinned Joni's jacket against its limits; at 0.5 it leans about 25 degrees and Chun-Li's light ribbons
  40-60 degrees, gusting.
- The runner sets each sim's wind before stepping it (`sim.wind = W_component`); a sim with no wind (None) runs the
  code path it runs today, unchanged.

## Data flow

Live and bakes both step `Runner.tick` per frame. Before stepping, the runner reads the wind fields at the frame's
time (`dynamics_live` and `dynamics_bake` already set the scene frame), converts the velocity, and hands it to its
sims and cloth sims. Nothing is stored in the import: wind is read from the scene on every evaluation, so adding,
moving or keyframing a field changes the next Live frame or bake.

## Error handling

- A field object without the custom properties uses the defaults.
- Non-finite values (a field scaled to zero, NaN strength) count as no wind from that field.
- No wind fields: the sims get None and behave exactly as today (checked by the before/after
  compare: no differences on the reference characters without a field).

## Testing

- `tests/plugin/wind_check.py` (new, headless): field discovery (hidden and non-wind fields skipped); velocity
  direction and speed for a still field (gust 0, turbulence 0); gust range and determinism over time; turbulence
  bounds; the gust properties' defaults and the panel's poll (wind fields only).
- `rigid_check.py` / `dynamics_check.py`: a body under sideways wind accelerates downwind; no wind leaves the results bit-identical to today's.
- `cloth_check.py`: a hanging cloth with drag coefficients moves downwind; without coefficients it doesn't.
- Real import (compare.sh on the reference characters): unchanged without a field; with a field, hair and cloth lean
  downwind and stay finite (no NaN, no far bones beyond the field's push).

## Out of scope

Field shapes and falloff, Blender's own field noise/flow, per-bone sampling (the wind is read at the character's
root), wind on the corrective rigs and Control Rig nodes.
