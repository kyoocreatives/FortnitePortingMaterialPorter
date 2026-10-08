# Subsurface controls: skin, base, distance, profile colour

## Goal

Keep subsurface scattering faithful to the game by default, with fewer limits and more control:
skin scattering stays where the game puts it, an optional base amount scatters over a whole cosmetic,
and coloured skin profiles are softened by default.

## How it works in UE and Fortnite (what this builds on)

- UE's skin model is **Subsurface Profile**: the material's **Opacity** pin is the per-pixel scattering amount
  (0-1); distance and colour come from the profile asset (MeanFreePath colour x distance, Surface Albedo, Tint,
  World Unit Scale). **Subsurface**, **Preintegrated Skin** and **Two-Sided Foliage** read Opacity and
  SubsurfaceColor instead.
- Fortnite's character masters (`M_CharacterParent_2023`, `M_FN_Character_MASTER`) pick the shading model per
  pixel (`MSM_FromMaterialExpression`): skin pixels are Subsurface Profile, scaled by `SkinSubsurfaceIntensity`
  and a mask; cloth, hair and armour are DefaultLit, ClearCoat or Cloth. Anime masters are Cloth; custom masters
  (Lexa's toon, Celestial, SypherPK's Fantastical) are DefaultLit. Those never scatter in the game.
- Nearly every skin uses `SS_HeroSkin_02` (radius 0.88, 0.79, 0.78 over 3 mm). A few use coloured skin-tone
  profiles: `SSP_SkinTone_LightRose` (0.95, 0.33, 0.41), `SSP_SkinTone_LightGold` (0.86, 0.50, 0.24). In Blender
  their per-channel spread makes skin darker and more saturated than in the game.

## Controls

Same four controls in the app (Blender settings > Exact Materials: what an import starts with) and on each
material's group node (Pre FX panel, beside the game's own subsurface parameter: tweaked after import).

| Control | Range | Default | Meaning |
|---|---|---|---|
| Skin Subsurface | 0-1 | 1 | The game's per-pixel skin amount times this (the soft-edge ratio to `SkinSubsurfaceIntensity` is kept, so lips stay unscattered). |
| Base Subsurface | 0-1 | 0 | Scattering over the whole material, clothes included. Final amount = max(Base, Skin). Cosmetic imports only. |
| Scatter Distance | 0-10 | 1 | Times the game's distance. On the group node it is the distance itself in metres (as today's Subsurface Scale). |
| Profile Colour | 0-1 | 0.5 | How much of the profile's per-channel colour the radius keeps: 1 the game's, 0 neutral (every channel at the profile's mean, so the overall distance doesn't change). |

Fur keeps its own Fur Subsurface Intensity and Fur Subsurface Scale, unchanged; Base and Profile Colour don't apply to fur.

## Where each applies

- **Skin Subsurface**: only where the game scatters, as today (per-pixel Subsurface Profile / Preintegrated Skin
  pixels, Subsurface and Two-Sided Foliage materials).
- **Base Subsurface**: every exact material of a cosmetic import, including materials the game never scatters.
  Not on World, Prefab, plain Mesh, Effect or LEGO prop imports (levels and props: hundreds of materials that would
  all compile Eevee's subsurface pass). Where a material has no profile, it uses the neutral radius
  (`SKIN_RADIUS`) and 3 mm.

## Changes

### App

- `BlenderSettingsViewModel`: keep `SubsurfaceIntensity` and `SubsurfaceScale` (saved settings carry over),
  add `BaseSubsurface` (0) and `ProfileColour` (0.5).
- `Views/MaterialPorter/ExactMaterialSettings.axaml`: labels Skin Subsurface and Scatter Distance, Scatter
  Distance slider 0-10, new sliders Base Subsurface (0-1) and Profile Colour (0-1). Descriptions short and plain.
- The values reach the plugin through the Blender export options, like the existing ones.

### Plugin

- `hook.subsurface()`: returns skin, base, scale and colour. Base is 0 unless the import is a cosmetic (the
  context's export type); fur returns its own intensity and scale, base 0, colour 1.
- `hook` (where the group node values are set today): sets Skin Subsurface, Base Subsurface, Scatter Distance
  (`mp_subsurface_scale` x scale) and Profile Colour on each built material's group node.
- `build.assemble` (subsurface section):
  - Amount: `max(Base Subsurface, skin)`, skin being today's weight (game ratio x Skin Subsurface, or Skin
    Subsurface where the game's weight is constant).
  - Radius: `mix(neutral, Subsurface Radius, Profile Colour)`, neutral = the radius's channel mean on all three
    channels, built from group inputs so it stays live. A radius the graph makes itself (SubsurfaceColor of the
    Subsurface model) is left as it is.
  - A cosmetic material without any game scattering gets the inputs too (Base drives its amount; Skin has
    nothing to scale), with `SKIN_RADIUS` and 3 mm.
  - Group inputs, in this order: Skin Subsurface, Base Subsurface, Scatter Distance, Subsurface Radius,
    Profile Colour; `_subsurface_panel` places them.
- `build.BUILD_REVISION` bumped: materials built before are rebuilt on the next import.

## Testing

- Headless Eevee renders under one sun: The Night Rose (LightRose) and Carolina (LightGold) at Profile Colour
  1, 0.5, 0; Nemia (HeroSkin_02) barely changes; Lexa with Base 0.5 scatters where the game doesn't; one outfit
  with Base 0.3 scatters its clothes.
- Screenshot of the app's settings page with the new sliders (the real page, not only the route).
- Baseline compare: differences only in cosmetics' materials (new subsurface nodes); none in map-cell;
  translator_test unchanged.

## Out of scope

- A per-character control object driving all its materials.
- The profile's Surface Albedo, Tint, dual specular and transmission.
- Subsurface on map and prop imports.
