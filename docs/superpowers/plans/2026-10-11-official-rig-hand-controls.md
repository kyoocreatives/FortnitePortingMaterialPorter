# Official rig hand controls: implementation plan

**Goal:** optional FortRig-style hand controls on the official rig, behind an app setting.
**Spec:** `docs/superpowers/specs/2026-10-11-official-rig-hand-controls-design.md`
**Architecture:** a new plugin module `official_hands.py` builds pivots, controls and constraints on a rig `official_rig`
already built; `mesh_hooks._official` calls it when the payload option is on; Bake to Controls keys its controls at rest.

## Task 1: plugin layer (TDD)
- Test first in `tests/plugin/official_rig_check.py` (spec's test list); the test mannequin gets a real left hand
  (metacarpals, fingers bent slightly, thumb) so planes and rooms exist.
- `processing/context/official_hands.py`:
  - `build(obj, data=None) -> int` (controls added; 0 when the hand is missing),
  - `CONTROL = "fpmp_hand_control"` bone property marking its controls,
  - edit mode: pivots, re-parenting, controls (Epic shapes `Circle_Thick` / `RoundedSquare_Thick`, side colours from
    the hand FK control), pose mode: Transformation and Limit Rotation constraints.
- `official_rig._bake`: hand controls (bones with `CONTROL`) join `still`.
- Run: official_rig_check, rig_check.

## Task 2: app setting
- `BlenderSettingsViewModel`: `IsOfficialRig`, `_officialRigHandControls` (MP lines).
- `Views/MaterialPorter/OfficialRigSettings.axaml(.cs)`: the toggle, visible when `IsOfficialRig`; one MP hook line in
  `BlenderSettingsView.axaml` after Reorient Bones.
- `mesh_hooks._official`: `official_hands.build(skeleton)` when `ctx.options.get("OfficialRigHandControls")`.
- `FORK.md`: one line under the official rig.
- Run: `bash testapp.sh test` build, open the real settings page (`fork-settings-shot`), import a character with the
  option through the test instance and check the controls exist.
