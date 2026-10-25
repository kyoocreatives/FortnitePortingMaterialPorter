# Custom landscapes: Fortnite landscape materials on your own meshes

Goal: paint custom landscapes for Fortnite art with the game's landscape materials, looking as they do in game, everything
set from one panel (N panel > Fortnite Porting > Landscape).

## What the person gets
- A landscape material exported onto selected meshes builds every layer and paints into **weightmap images** (three
  layers per image, R/G/B), like UE's weightmaps: crisp, height-broken transitions whatever the mesh density.
- **Landscape panel**, for the active mesh:
  - Layers: each layer with its channel, Paint (Texture Paint on its image and channel, brush in its colour),
    Fill (this layer everywhere, others cleared), Clear; Normalize (all layers sum to 1 per texel, as UE paints).
  - Features: the material's static switches the build consulted (UseSnowForBiomeC, UseBiomeB_Scree,
    UseTempLayersForBiomeMasks...), as checkboxes; Update Material rebuilds with them.
  - Parameters: the material's own inputs (colours, tiling, contrasts...), drawn with their panels.
  - Biomes: the material's map-wide textures (Hera's BiomeTexture, Athena's BiomeColormap...) made paintable:
    Make Paintable, then each biome by name (Chaparral, Red Woods... from the game's own "Isolated" masks function)
    with Paint and Fill, Clear All. Colour maps (Hera's grass colours, Athena's colormap) are painted in colours;
    Hera's grass colours follow the painted biomes (each biome's colour measured from the game's two maps).
- Map imports are unchanged (vertex colour layers at the game's own resolution).

## How
- `landscape_layers`: an image backend beside the attribute one. A mesh's table maps layer -> "WeightmapN.C";
  obj["mp_weightmaps"] maps WeightmapN -> image name. Images are shared by the meshes a material was put on together,
  read through UV map "mp_weights" (top projection over their combined world bounds), ~4 texels/m (256-2048), packed.
- Build: entry["landscape_images"] {layer: [image, channel]}; env.landscape_weight reads an Image Texture (Non-Color,
  Linear, Extend) at UV "mp_weights". Part of the material key and variant.
- Static switches: the env records each switch it reads (name, value) on the built material (mp_switches); the panel
  lists them; overrides ride in the material's overlay (KEY_OVERLAY "switches") and the rebuild applies them.
- Map textures (`ue_graph.maptex`): a texture sample whose coordinates follow the world is probed with the world
  pinned far out and at 0/1 km; clamped coordinates give its rectangle, unclamped map-wide ones a corner (rectangles
  worked out from all corners). Recorded on the material (mp_map_textures). With a biome map (obj["mp_biome_maps"],
  `biome_maps`) the sample reads that image over its rectangle through UV mp_weights; UV maps mp_map_<param>_<n>
  paint each rectangle.
- World-mode builds read every layer the material asks for (a switch may add one, as UseSnowForBiomeC adds BiomeC),
  except those dropped for Eevee's limits.
- Old meshes with packed colour attributes keep working (no image table: attributes).

## Out of scope (later)
Scatter meshes (scree, grass types), swapping the material from a list, LEGO custom-HLSL nodes.

## Checks
- Headless: onto-selected makes images + UV, material reads them, paint/fill/normalize change pixels as specified,
  a switch override rebuilds with the switch flipped (Hera UseSnowForBiomeC), map import unchanged (onto check, rig suites).
- Visual: Hera Sand->Gravel 1 m ramp painted in the weightmap renders crisp (Vulkan).
