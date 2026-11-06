An unofficial fork of [FortnitePorting](https://github.com/h4lfheart/FortnitePorting) that rebuilds Fortnite's materials in Blender from their Unreal material graphs, and adds LEGO Fortnite and Rocket Racing content. Please don't report its problems on FortnitePorting's repository.

**Install:** download `FortnitePortingMP.exe` below and run it (Windows x64, no installer). Windows may warn about an unsigned app: *More info > Run anyway*. It runs beside an installed FortnitePorting, with its own settings (`%APPDATA%\FortnitePorting MP`) and its own Blender plugin (`fortnite_porting_mp`), installed from the Plugins page as in FortnitePorting. Exact materials need Blender 5.0 or newer; older Blender gets FortnitePorting's shaders.

**Version numbers:** versions now read like `fpv-4.3.5/mpv-1.13.0`: the FortnitePorting version this fork is merged with, then the fork's own version. They replace `4.0.0-mp.N`.

**This release**
Faster imports and fixes:
- Maps and characters import much faster: material builds reuse what earlier materials built, landscape layers settle in a few builds, and effects replay faster.
- Face poses bake to shape keys in a fraction of a second instead of many seconds.
- Map exports no longer save their textures again every time.
- Material copies (same material, other values) are reused again instead of rebuilt.
- Tasty rig: fixed a crash on import.

**What it adds**
- Exact materials, rebuilt from each material's Unreal graph. *Settings > Blender > Prefer FP Shaders for Characters* keeps FortnitePorting's shaders for character materials it has one for.
- Maps through Material Porter's map reader: actor components, overrides, custom primitive data, spline meshes, water bodies, level instances.
- Rocket Racing cars with their styles (tier, body colour, paint, decal, wheels).
- LEGO outfits (cooked and recipe figures, with face styles), LEGO emotes with animated faces, LEGO props and building sets, LEGO wildlife (needs LEGO Fortnite's optional content installed).
- Character rigs and physics: Fortnite's official body rig, face boards, deform correctives, dynamic bones, cloth and rigid bodies, with wind.
- No FortnitePorting account: its sign-in, chat and updater are off. New releases of this fork are announced in the app.
