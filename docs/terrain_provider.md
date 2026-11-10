# Terrain provider API

For add-ons that generate terrain meshes: they set a game landscape material's layer weights and keep the plugin's
scatter layers, biome maps and Landscape tab in step with the mesh. The code is in
`plugins/Blender/fortnite_porting/material_porter/terrain_provider.py`. It does nothing until another add-on calls it.

## Finding it

```python
api = bpy.app.driver_namespace.get("fp_terrain_api")
if api is not None and api.VERSION == 1:
    ...
```

The plugin publishes it when it registers and removes it when it unregisters (only if the entry is still its own).
Calls marked optional are new in version 1. Check them with `hasattr`.

## Calls

| Call | What it does |
|---|---|
| `VERSION` | `1` |
| `game_layers(obj) -> list[str]` | The layers the mesh's game landscape material blends, sorted. On a mesh the material was put on, this also includes every layer that has a channel. Returns `[]` when no game landscape material is assigned. The result is cached per material, so it's cheap enough to call on redraw. |
| `write_layers(obj, weights)` | `weights`: `{layer: per-vertex floats}`, one value per vertex, clamped to 0..1. See [Writing layers](#writing-layers). |
| `terrain_changed(obj)` | The mesh's geometry or weights changed. Refreshes run after 0.25 s of quiet, and at least once a second while changes keep coming. Safe to call with a deleted object. Never raises. |
| `adopt(obj)` | Lists the mesh as a custom landscape in the Landscape tab (`obj["mp_landscape"]`). Safe to call more than once. |
| `unadopt(obj)` (optional) | Takes back what `adopt` did. A mesh that a landscape material was put on stays listed. Weights and channels stay. |
| `on_resize(obj, old_n, new_n) -> bool` (optional) | Call it after the mesh was rebuilt with a different vertex count. It schedules a refresh. It always returns `False`, which means the caller resamples the attributes. The plugin can't rebuild painted masks or packed layers itself. |
| `flush()` | Runs pending refreshes now. Headless scripts need it because timers don't fire while a script runs. |

### Writing layers

Weights go where the mesh already keeps its layers (`obj["mp_layers"]` maps each layer to a channel):

- **Weightmap images.** This applies to a mesh a landscape material was put on (`obj["mp_weightmaps"]`).
  - The per-vertex weights are sampled at each texel the mesh covers. A full grid is sampled bilinearly; other meshes are averaged per texel and interpolated between.
  - A new layer gets a channel through `weightmaps.ensure`, the same call the Landscape panel uses.
- **Packed colour channels.** Every other mesh stores layers in `Attribute1.R/G/B`, `Attribute2.R`... A new layer takes the next free channel.
  - A map's landscape proxy (`obj["mp_layers_map"]`) follows the scene-wide order `scene["mp_landscape_layers"]`.

After writing, every texel or vertex is normalised the way the Normalize tool does it: all layers together sum to 1. Where nothing is painted, the values are left at 0. Layers you don't pass keep their weights and are normalised along with the rest, so pass zeros to clear a layer.

`write_layers` raises `ValueError` when an array doesn't have one value per vertex. It schedules `terrain_changed` itself.

### What a refresh does

- Lays UV map `mp_weights` again when the vertex count changed or the map is missing (only on meshes with weightmaps). The biome maps' UV maps (`mp_map_*`) are laid again with it.
- Scatter layers weighted by a landscape layer look up its channel again, so they follow a layer that moved, for example into weightmaps after a material was put on. Their instances update.
- Grass masks (`mp_grass_*`) are evaluated again. This needs the FP app. If it fails, the failure is logged and the refresh carries on.
- Weightmap images are packed again, so undo and saving keep them.
- The scatter biome groups refresh, and the editor's Landscape tab reads the terrain again.

## Mesh ownership

The generator owns `height`, its own mask attributes, `tectonic_*` and `.`-prefixed attributes. Everything else on the mesh belongs to the plugin or the user, and the generator keeps it when it rewrites the mesh. That includes:

- the layer colour attributes `Attribute1`... and every `mp_*` attribute (`mp_scatter_<id>` paint masks, `mp_grass_*`)
- UV maps `mp_weights` and `mp_map_*`
- custom properties (`mp_layers`, `mp_weightmaps`, `mp_weight_set`, `mp_scatter`, `mp_weights_frame`...)
- material slots and the scatter modifiers

When the vertex count changes, the generator resamples point and corner attributes bilinearly and face attributes by nearest face.

## UV map `mp_weights`

The weightmaps cover a square in the XY plane: `u = (x - x0) / S`, `v = (y - y0) / S`, where `S` is the larger of the terrain's width and depth.
- For a square grid terrain (one mesh), this is the generator's convention: `u = col/(n-1)`, `v = row/(n-1)`, with `v` increasing toward +Y.
- A rectangular terrain must keep the square form. Stretching `v` to 0..1 would stretch the weightmaps.

The plugin records the mapping from local XY to UV in `obj["mp_weights_frame"]` the first time it needs it. Weights are always written through the map the material reads them through: when the generator moved the vertices or laid `mp_weights` again as a top projection, the record is worked out again from the UV. When the UV map is missing (or isn't a top projection) after a vertex count change, the plugin lays `mp_weights` from the record. Meshes sharing one weightmap set (several pieces) share one square over all of them, so only the plugin should lay their UVs.
