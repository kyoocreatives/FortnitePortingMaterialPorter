An unofficial fork of [FortnitePorting](https://github.com/h4lfheart/FortnitePorting) that rebuilds Fortnite's materials in Blender from their Unreal material graphs, and adds LEGO Fortnite and Rocket Racing content. Please don't report its problems on FortnitePorting's repository.

**Install:** download `FortnitePortingMP.exe` below and run it (Windows x64, no installer). Windows may warn about an unsigned app: *More info > Run anyway*. It runs beside an installed FortnitePorting, with its own settings (`%APPDATA%\FortnitePorting MP`) and its own Blender plugin (`fortnite_porting_mp`), installed from the Plugins page as in FortnitePorting. Exact materials need Blender 5.0 or newer; older Blender gets FortnitePorting's shaders.

**Version numbers:** versions now read like `fpv-4.3.5/mpv-1.13.0`: the FortnitePorting version this fork is merged with, then the fork's own version. They replace `4.0.0-mp.N`.

**This release**
Blender 5.3 and Fortnite 43.00 fixes:
- Blender 5.3: exact materials (cel-shaded skins included) build again instead of falling back to FortnitePorting's shaders.
- Fortnite 43.00: Mutable characters and cars read again.
- Delilah map: grass masks and biome maps work on custom landscapes.
- UEFN project meshes: normal maps no longer come out flat.
- Blender editor: FP starts only when you press Start, and a Stop button shuts it down.
- Terrain API for add-ons that generate terrain.

**What it adds**
- Exact materials, rebuilt from each material's Unreal graph. *Settings > Blender > Prefer FP Shaders for Characters* keeps FortnitePorting's shaders for character materials it has one for.
- Maps through Material Porter's map reader: actor components, overrides, custom primitive data, spline meshes, water bodies, level instances.
- Rocket Racing cars with their styles (tier, body colour, paint, decal, wheels).
- LEGO outfits (cooked and recipe figures, with face styles), LEGO emotes with animated faces, LEGO props and building sets, LEGO wildlife (needs LEGO Fortnite's optional content installed).
- Character rigs and physics: Fortnite's official body rig, face boards, deform correctives, dynamic bones, cloth and rigid bodies, with wind.
- No FortnitePorting account: its sign-in, chat and updater are off. New releases of this fork are announced in the app.
