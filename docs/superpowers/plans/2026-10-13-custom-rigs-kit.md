# Custom rigs on the kit: implementation plan

**Goal:** rebuild the creature and LEGO rigs, and restyle the vehicle rig, on a shared kit that follows the official
(UE) rig's standard.

**Spec:** docs/superpowers/specs/2026-10-13-custom-rigs-kit-design.md

**Architecture:** `rig_kit.py` holds what all three share (Epic gizmos and colours, mesh-fitted sizes, control bones
that game bones copy, IK limbs through a mechanism chain, rig on/off, bake, FK/IK switch, metadata). Each rig file
surveys its skeleton and asks the kit for controls.

## Global constraints

- Game bones keep names, rest pose, hierarchy; building a rig changes no pose at rest.
- Collections: rig_style.COLLECTIONS plus FK. Settings on rig_style.SETTINGS (hidden, in Mechanics).
- Constraints the kit adds on game bones are named with `RK ` (rig off mutes them).
- Gizmos: official_rig.json shapes (`OR_<name>`), native radius 0.05.
- Colours and widths: Epic's, as the official rig uses them.

## Review focus

1. A skeleton whose bones don't meet (sidekick arm gap): IK must still reach (mechanism chain joint to joint).
2. Two-bone legs without a foot (LEGO cow): IK on both bones, target at the last tail.
3. Bones with no skinned vertices (helpers, accessories): sizes fall back to bone length, never zero.
4. An animation imported onto a rigged armature: rig switches off, Bake to Controls brings it back exactly.
5. A flat skeleton (snake, fish): controls still visible and sized.

## Tasks

1. **Kit core** (`rig_kit.py`): gizmo, palette, fit radius, bone helpers, shape placement, follow, metadata.
   Tests: kit section of rig_check (follow keeps rest, sizes outside a mesh, gizmo scale).
2. **Kit runtime**: IK limb (mechanism chain, target, pole, pole line), set_on, bake_to_controls, switch with
   matching, animation-import hook. Tests: IK reaches; switch keeps pose; bake lands keys; rig off plays keys.
3. **Creature rig** rebuilt on the kit (survey kept; two-bone legs). rig_check creature section rewritten.
   Audit renders wolf and cow before/after.
4. **LEGO rig** rebuilt on the kit. rig_check LEGO section updated (face board checks unchanged). Audit renders.
5. **Vehicle rig** restyled with kit gizmos and colours; mechanics unchanged; rig_check vehicle checks kept.
   Audit renders.
6. **Panel**: kit rigs get Rig on/off, Bake, FK/IK rows (per limb, All/Legs/Arms). Real Blender window screenshot.

Commit after each task. Publish only when asked.
