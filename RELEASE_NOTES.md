An unofficial fork of [FortnitePorting](https://github.com/h4lfheart/FortnitePorting) that rebuilds Fortnite's materials in Blender from their Unreal material graphs, and adds LEGO Fortnite and Rocket Racing content. Please don't report its problems on FortnitePorting's repository.

**Install:** download `FortnitePortingMP.exe` below and run it (Windows x64, no installer). Windows may warn about an unsigned app: *More info > Run anyway*. It runs beside an installed FortnitePorting, with its own settings (`%APPDATA%\FortnitePorting MP`) and its own Blender plugin (`fortnite_porting_mp`), installed from the Plugins page as in FortnitePorting. Exact materials need Blender 5.0 or newer; older Blender gets FortnitePorting's shaders.

**Version numbers:** versions now read like `fpv-4.3.5/mpv-1.13.0`: the FortnitePorting version this fork is merged with, then the fork's own version. They replace `4.0.0-mp.N`.

**This release**
- **Animation tab** in the Fortnite editor: emotes, lobby idles and locomotion play on a mannequin (or your character) in a live 3D preview, with props, sound, mirror and item in hand, sorting and tags. Apply puts what plays onto your character as NLA strips.
- **Effects tab:** browse the game's effects with search, sorting and tags, preview them looping live, move, rotate and scale them while they play, and import them. Effects in the scene can be frozen at a frame.
- **Materials tab:** built materials grouped and edited across the ticked ones, relative edits, copy and paste, and edits kept through re-imports. Rebuild On puts a material on any game material's shader, with Two-Tone Rim (the anime two-tone rim light) as a preset.
- Official rig: the forearm, upper arm, thigh and calf twist bones follow their limb's twist, and Preserve Volume is on by default (both switchable in the Rig panel).
- Anime outlines on parts without overlay data and across style swaps; anime skins take the night tint from the sky's hour.
- Maps: terrain under picked map cells, cell lists name their levels, comma-separated map filters, new season islands listed, the day sequence taken from the current island.
- Faster start and material lookups, less memory, and fixes for a project map freeze and a landscape crash on game version 43.00.
- Merged with FortnitePorting 4.3.6 (rig fix for `rt_` deform bones).

**What it adds**
- Exact materials, rebuilt from each material's Unreal graph. *Settings > Blender > Prefer FP Shaders for Characters* keeps FortnitePorting's shaders for character materials it has one for.
- Maps through Material Porter's map reader: actor components, overrides, custom primitive data, spline meshes, water bodies, level instances.
- Rocket Racing cars with their styles (tier, body colour, paint, decal, wheels).
- LEGO outfits (cooked and recipe figures, with face styles), LEGO emotes with animated faces, LEGO props and building sets, LEGO wildlife (needs LEGO Fortnite's optional content installed).
- Character rigs and physics: Fortnite's official body rig, face boards, deform correctives, dynamic bones, cloth and rigid bodies, with wind.
- No FortnitePorting account: its sign-in, chat and updater are off. New releases of this fork are announced in the app.
