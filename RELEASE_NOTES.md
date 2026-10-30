An unofficial fork of [FortnitePorting](https://github.com/h4lfheart/FortnitePorting) that rebuilds Fortnite's materials in Blender from their Unreal material graphs, and adds LEGO Fortnite and Rocket Racing content. Please don't report its problems on FortnitePorting's repository.

**Install:** download `FortnitePortingMP.exe` below and run it (Windows x64, no installer). Windows may warn about an unsigned app: *More info > Run anyway*. It runs beside an installed FortnitePorting, with its own settings (`%APPDATA%\FortnitePorting MP`) and its own Blender plugin (`fortnite_porting_mp`), installed from the Plugins page as in FortnitePorting. Exact materials need Blender 5.0 or newer; older Blender gets FortnitePorting's shaders.

**Version numbers:** versions now read like `fpv-4.3.5/mpv-1.13.0`: the FortnitePorting version this fork is merged with, then the fork's own version. They replace `4.0.0-mp.N`.

**This release**
- **Fortnite editor in Blender 5.2 and up:** a new editor type with Assets, Files, Maps, Rig and Landscape tabs. Browse and import without leaving Blender; the app runs hidden behind it. Quick import with Ctrl+Shift+F from any editor.
- Landscape: paint layers and biomes, hide them in the material, update the material from the side panel, terrain imports faster.
- Scatter: the game's own grass on landscapes and custom terrain, placed map foliage as instances, grass that follows the painted biomes and an optional blend with the ground colour.
- Rework of the Fortnite Porting side panel: Rig, Landscape, Effect, Shell Fur and Materials panels.
- Anime skins: the game's ink outlines, sharp pupils, cel bands that follow the sun; mesh pieces the game doesn't draw stay hidden.
- Foliage: Base Darkening, and black grass on some maps fixed.
- Maps: Hidden collection for hidden actors, Data Layers menu.
- Effects: offset slider to shift an effect's timing.
- Rigs: creature face controls, legs that end where the game's IK does, FK arms that follow the spine by default, Tasty rig to official rig.
- Materials go onto selected meshes with the attributes they read; LEGO heads no longer import white; texture-capped materials no longer share full-size images.
- Merged with FortnitePorting 4.3.5.

**What it adds**
- Exact materials, rebuilt from each material's Unreal graph. *Settings > Blender > Prefer FP Shaders for Characters* keeps FortnitePorting's shaders for character materials it has one for.
- Maps through Material Porter's map reader: actor components, overrides, custom primitive data, spline meshes, water bodies, level instances.
- Rocket Racing cars with their styles (tier, body colour, paint, decal, wheels).
- LEGO outfits (cooked and recipe figures, with face styles), LEGO emotes with animated faces, LEGO props and building sets, LEGO wildlife (needs LEGO Fortnite's optional content installed).
- Character rigs and physics: Fortnite's official body rig, face boards, deform correctives, dynamic bones, cloth and rigid bodies, with wind.
- No FortnitePorting account: its sign-in, chat and updater are off. New releases of this fork are announced in the app.
