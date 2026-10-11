# Official rig: extra hand controls

## Intent
The user asked for FortRig-style hand controls on the fork's official (Epic) rig, behind a setting in the app. Epic's
rig only has one control per finger bone (plus metacarpals), so posing a fist means turning 15 controls. The extras
are a convenience layer and are not part of Epic's rig.

## Setting
- App: Settings > Blender > Armature, a toggle **Extra Hand Controls**, off by default. It shows only when Rig is
  "Fortnite Official Rig". It is stored as `OfficialRigHandControls` on `BlenderSettingsViewModel` and reaches the
  plugin in the payload's settings like every Blender setting.
- Plugin: `mesh_hooks._official` builds the layer after the rig when the option is on.

## Controls (per side, when the hand and at least two long fingers exist)
- `fingers_<s>_ctrl` over the back of the hand: rotate X to curl every finger at once (the thumb partly); rotate Z to
  spread the fingers apart from the middle finger.
- `palm_<s>_ctrl` at the little-finger edge: rotate X to cup the palm. The metacarpals roll more toward the little
  finger (index 0, middle 0.3, ring 0.65, pinky 1). Needs the metacarpal controls.
- `<finger>_<s>_curl_ctrl` along each finger: rotate X to curl that finger.
- They live in the rig's Controls collection and draw with Epic's shapes and the side's colour.

## Mechanism
- Each of Epic's finger and metacarpal controls gets a hidden parent `OR_MCH_curl_<control>` at the joint. Its X is
  the finger's curl axis, its Z points out of the palm. The control is re-parented under it. At rest the pivot adds
  nothing, so Epic's controls behave exactly as before and stay free on top.
- Hand controls drive the pivots through Transformation constraints (local space, after the pivot's own):
  - curl: 0..90 degrees on the control maps to 0..room on each joint, -30..0 to -15..0 (two clamped maps, so both are
    exactly 0 at rest);
  - spread: Z ±45 degrees times each finger's weight on its first joint (thumb 0.9, index 1, middle 0, ring 0.8,
    pinky 1.6, signed away from the middle finger);
  - cup: X ±60 degrees times the metacarpal weight;
  - finger curl: as the fingers control, for one finger.
- Room per joint = the anatomical maximum (fingers 90/105/80, thumb 45/60/80 degrees) minus the joint's rest bend
  (at least 10). A Limit Rotation on each pivot keeps the sum of the mechanisms between -15 and room.
- The thumb's curl axis is its own bend plane; the long fingers use their own plane when it agrees with the hand's,
  else the hand's mean plane.

## Bake to Controls
The hand controls are keyed at rest on every baked frame. Epic's finger controls keep carrying the animation, so the
bake lands as before.

## Out of scope
FortRig's held-prop grip (Shrinkwrap), per-phalanx limits on Epic's controls, a rig-panel toggle.

## Tests (tests/plugin/official_rig_check.py)
- With the layer, a mannequin at rest poses exactly as without it.
- Fingers curl 90: each long finger's tip moves toward the palm and every joint stays within room + 1 degree.
- Spread moves the index and pinky tips apart, the middle finger stays put.
- Palm cup turns the pinky metacarpal more than the index one.
- Epic's finger control still turns its bone with the layer on.
- Bake to Controls with the layer: baked bones land as before and the hand controls are keyed at rest.
- The option off: no hand controls are built.
