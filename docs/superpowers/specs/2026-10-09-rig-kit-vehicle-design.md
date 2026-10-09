# Rig kit, applied to the vehicle rig

## Goal

Make the fork's control rigs easy to read and easy to find controls in, the way production rigs are (Rigify,
Advanced Skeleton, Car-Rig Pro, UE Control Rig): fewer bones on screen, controls told apart by colour and shape,
settings keyable. Use: posing for stills and animating in Blender (no export to other software).

This spec covers a shared rig kit and its first application, the vehicle rig. The creature, LEGO and character
(Tasty) rigs get the kit afterwards, each in its own spec.

## Today (vehicle rig, `processing/context/vehicle_rig.py`)

- Collections: Vehicle Controls, Vehicle Wheel Controls, Vehicle Parts (shown); Vehicle Wheels, Vehicle Other (hidden).
- Colours per function (`COLORS`): main yellow, drive blue, steer red, drift magenta, body green, wheel orange,
  arch light green, part cyan. Main and wheel rings read as the same colour; nothing says which side a control is on.
- Shapes from `rig_shapes.py` (arrow, arcs, up-down, footprint) and the add-on's CTRL_ shapes; wheel rings are
  octagons with no mark showing how far a wheel has turned.
- Settings (`auto_wheels`, `auto_steer`, `countersteer`, `suspension`, `lean`) are custom properties on the armature
  object, read by drivers (`creature_rig._driven`): not keyable with the pose, only visible in the Rig panel.
- Panel `FPMP_PT_CreatureRig` (operator/convert_op.py): a text hint, the Ground field, the setting sliders.

## The kit: `processing/context/rig_style.py` (new)

Used by every fork rig; the rigs keep their own construction code and call the kit to style what they made.

### Colours
| Role | Colour (normal) |
|---|---|
| Left | blue (0.15, 0.45, 1.0) |
| Right | red (1.0, 0.2, 0.15) |
| Centre | yellow (1.0, 0.82, 0.1) |
| Secondary | the side's colour lightened halfway to white |
| Main (places the whole rig) | orange (1.0, 0.5, 0.05) |
| Settings | purple (0.7, 0.3, 1.0) |

Selected: the colour lightened (as `rig_shapes.color` does today); active: white. Side comes from the bone's
position relative to the rig's centre line (not its name, which varies between skeletons), with a small dead zone
in the middle counting as centre.

### Line widths
Main 3.5, primary 2.5, secondary 1.5 (`custom_shape_wire_width`).

### Shapes (`rig_shapes.py`, built once per file as today)
Kept: arrow, arc (turn), swing, up-down, foot, glasses, footprint. Added:
- `circle`: 32 segments (replaces octagon use for rings);
- `circle_tick`: circle plus a short radial tick at the top, so rotation reads at a glance;
- `square`, `box` (wire cube), `diamond` (poles), `gear` (settings: 8 teeth around a circle).
All in their XY plane, Z up, unit size, as the existing shapes.

### Collections
Every fork rig ends with exactly these on its armature (created if missing, in this order):
| Collection | Shown | What |
|---|---|---|
| Controls | yes | what an animator poses most |
| Secondary | yes | fine or occasional controls |
| Mechanics | no | helper bones the rig drives itself |
| Game Bones | no | the original skeleton |
A game bone the rig poses directly (a door, the hood) is a control and goes in Controls or Secondary, not Game Bones.
Collections the game skeleton brought with it are hidden, as today.

### Display
The armature object is drawn in front of meshes (`show_in_front`), so controls inside the body stay clickable.

### Settings control
A `CR_Settings` bone holds the rig's settings as custom properties (0-1 sliders, with descriptions): keyable in
the pose's action, shown with a gear, in Controls, beside the model (vehicle: in front of the nose, on the ground,
to the left). Drivers read `pose.bones["CR_Settings"]["<setting>"]` on the armature object. Not deformed, parented to
the rig's main control so it travels with it.

### Panel (operator/convert_op.py, the existing Rig panel)
For a fork rig:
1. One toggle per collection above (eye icon, name), in a row or two.
2. Select All Controls (Controls + Secondary); Reset Pose: Selected / All (location, rotation, scale of controls only,
   never Mechanics or Game Bones).
3. The rig's settings (from CR_Settings; for rigs built before it, from the object, as today), then the rig's own
   fields (vehicle: Ground).
The one-line text hints go: the shapes and colours explain themselves.

## The vehicle with the kit

| Control | Shape | Colour | Width | Collection |
|---|---|---|---|---|
| CR_Main | footprint (today's) | main | 3.5 | Controls |
| CR_Drive | arrow at the nose | centre | 2.5 | Controls |
| CR_Steer | arc at the front axle | centre | 2.5 | Controls |
| CR_Drift | arc at the back | centre | 2.5 | Controls |
| CR_Body | slab over the roof | centre, secondary tint | 2.5 | Controls |
| CR_Wheel_<wheel> | circle_tick on the wheel's outer face, radius 1.15 x the wheel's | side | 2.5 | Controls |
| CR_Arch_<wheel> | up-down, 1.5 x today's size, over the wheel | side, secondary tint | 1.5 | Secondary |
| moving parts (door, hood...) | today's part shape | side or centre, secondary tint | 1.5 | Secondary |
| CR_Settings | gear | settings | 2.5 | Controls |
| CR_Ground_*, CR_Lift_*, CR_Suspension, CR_Lean, CR_Body_Follow | (none) | | | Mechanics |
| wheels, frame, sockets, everything else from the game | | | | Game Bones |

Settings moved to CR_Settings: Wheels Spin (`auto_wheels`), Wheels Steer (`auto_steer`), Counter-steer
(`countersteer`), Body Follows Wheels (`suspension`), Lean in Turns (`lean`), same defaults as today. Ground stays a
property of the armature object (it is an object pointer, not a number to animate).

Rigs built before this change keep working: the panel finds their settings on the object; they are not migrated.
Rebuilding the rig (import again) gives the new one.

## Out of scope
- Following a path, IK/FK snapping, space switching, keying helpers (later passes).
- The creature, LEGO and Tasty rigs (next specs; Tasty through the existing after-build hook, FP's files untouched).
- Migrating existing rigs in saved files.

## Testing
- A headless check on two cars (Alokin, Backfire) after import: the four collections exist with the visibility
  above and every CR_ bone in the one the table gives; side colours match each wheel's side; CR_Settings exists with
  the five properties; each setting at 0 turns its behaviour off (e.g. `auto_wheels` 0: moving CR_Drive no longer
  spins the wheels); the arch check (body corner parts rise, wheel stays) still passes.
- Before/after control renders (`fpdev render --controls front,top,side`) shown to the user.
- The plugin suites stay green; the baseline compare's car signature changes only in the rig's bones, constraints,
  drivers and collections.
