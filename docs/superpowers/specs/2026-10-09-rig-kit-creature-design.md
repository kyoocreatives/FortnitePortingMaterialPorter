# Creature rig on the rig kit

## Goal

Second application of the rig kit (`docs/superpowers/specs/2026-10-09-rig-kit-vehicle-design.md`, built in
`processing/context/rig_style.py`): the creature rig (`processing/context/creature_rig.py`: wolves, raptors, chickens,
LEGO animals, any skeleton it can read) gets the kit's colours, widths, shapes, collections, settings gear and panel,
so its controls read apart and the clutter goes. Use: posing stills and animating in Blender.

## Today

- Colours from Blender's theme palettes by side (`_palette`: THEME01 right, THEME04 left, THEME09 centre, THEME02 face):
  dark on wires.
- Shapes: the add-on's CTRL_ shapes (`sized`: CTRL_Root, CTRL_Spine rings, CTRL_Box, CTRL_Pole_Leg), `CR_Foot`, `CR_Glasses`.
- Collections: Creature Controls, Creature Face (shown), Creature Limb FK, Creature Other (hidden).
- The per-eye targets (`CR_Eye_<bone>`) are drawn: dots far in front of the face.
- Settings `ik_<limb>` (one per IK limb) and `eyes_aim` are custom properties of the armature object.

## The kit, extended

`rig_style.collections(armature, fk=False)`: with `fk=True` an extra **FK** collection, hidden, sits between Secondary and
Mechanics (Controls, Secondary, FK, Mechanics, Game Bones). Rigs without IK (the vehicle) don't pass it and get the four
groups as today. The panel's toggles and `controls()` include FK (selecting controls takes FK bones only when that group
is shown, as for any hidden group). `rig_style.add_settings` and `driven` are used as for the vehicle; settings stay 0-1.

## The creature with the kit

| Control | Shape | Colour | Width | Collection |
|---|---|---|---|---|
| root (game bone, places the creature) | `CR_Circle` on the ground, radius as today, plus a front chevron (the footprint shape's) | main | 3.5 | Controls |
| pelvis | `CR_Square`, flat, about the pelvis | centre | 2.5 | Controls |
| spine, neck, head, centre bones between spine and limbs/tail | `CR_Circle` around the bone (as today's rings) | centre | 2.5 | Controls |
| tail | `CR_Circle`, shrinking to the tip as today | centre | 2.5 | Controls |
| CR_IK_<foot> on a leg | `CR_Foot` on the ground (as today) | side | 2.5 | Controls |
| CR_IK_<hand> on an arm | `CR_Box` | side | 2.5 | Controls |
| CR_Pole_<limb> | `CR_Diamond` | side | 2.5 | Controls |
| CR_Eyes | `CR_Glasses` (as today) | centre | 2.5 | Controls |
| CR_Settings (new) | `CR_Gear`, on the ground beside the creature's left flank, halfway along | settings | 2.5 | Controls |
| face bones (jaw, lids, ears, everything under the head that isn't a limb) | small `CR_Diamond` | side, secondary tint | 1.5 | Secondary |
| a limb's other bones (fingers, toes, scapula), wing bones | small `CR_Circle` | side, secondary tint | 1.5 | Secondary |
| a limb's bones the IK drives (chain and foot) | `CR_Circle` | side | 2.5 | FK |
| CR_Eye_<bone>, CR_MCH_*, CR_Follow_* | none | | | Mechanics |
| everything else from the game | | | | Game Bones |

Side: from the bone's name (`side_of`, `_L`/`_R`), as today; a bone whose name says neither is centre.
Sizes stay creature-relative (today's `h * factor`); unit kit shapes take the size as radius/half-size.

Settings on CR_Settings: `ik_<limb>` for each IK limb (default 1, "IK on this limb (0: FK, as an animation plays it)")
and `eyes_aim` (default 1, "The eyes look at CR_Eyes") when the creature has eyes. Drivers read them there. Panel:
the IK sliders and Eyes Aim from the gear (older creature rigs: from the object, as today).

The armature is drawn in front (`show_in_front`). CR_Settings is parented to the root control and locked (no
transforms), like the vehicle's.

## Out of scope
IK/FK snapping, space switching, the LEGO figure and Tasty rigs, migrating saved rigs.

## Testing
- Headless (`tests/plugin/rig_check.py`): a synthetic quadruped skeleton (pelvis, spine, neck, head with jaw and two
  eyes, four legs of thigh/calf/foot/toe, a tail of three bones, names ending _L/_R) rigged by `creature_rig.create`:
  the five collections with their visibility and members per the table; side colours; widths; CR_Settings with one
  `ik_` per leg and `eyes_aim`; `ik_<leg>` at 0 turns that leg's IK off (the foot no longer follows CR_IK_<foot>);
  Select Controls with FK hidden doesn't select FK bones; a skeleton without eyes has no `eyes_aim`.
- Real creatures through fpdev: the Wolf (Grandma_Mammal) and one LEGO animal import and rig; before/after control renders.
- All plugin suites stay green.
