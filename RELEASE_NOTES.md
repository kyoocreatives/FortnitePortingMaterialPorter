An unofficial fork of [FortnitePorting](https://github.com/h4lfheart/FortnitePorting) that rebuilds Fortnite's materials in Blender from their Unreal material graphs, and adds LEGO Fortnite and Rocket Racing content. Please don't report its problems on FortnitePorting's repository.

**Install:** download `FortnitePortingMP.exe` below and run it (Windows x64, no installer). Windows may warn about an unsigned app: *More info > Run anyway*. It runs beside an installed FortnitePorting, with its own settings (`%APPDATA%\FortnitePorting MP`) and its own Blender plugin (`fortnite_porting_mp`), installed from the Plugins page as in FortnitePorting. Exact materials need Blender 5.0 or newer; older Blender gets FortnitePorting's shaders.

**This release**
- Chaos cloth on coats, skirts and capes; rigid bodies follow the character's own movement.
- Wind: Blender Wind force fields push hair, cloth and dynamic bones (gusts and turbulence in *Physics > Character Wind*), plus the game's idle wind sway.
- Gravity per movement state, read from each character's anim blueprint.
- More of each character's deform rig is read: aims, distances, parent constraints, branches, and the Control Rig units characters use.
- Fixes: dynamics popping when Live starts, cloth bakes, skirts with several influences.
- Maps: actor name filter, landscape detail and restoring remeshed landscapes, textures capped at 1024 by default, normal maps kept compressed, Smart texture sampling.
- Road from Curve helper: a road segment mesh repeated and bent along a curve.

**What it adds**
- Exact materials, rebuilt from each material's Unreal graph. *Settings > Blender > Prefer FP Shaders for Characters* keeps FortnitePorting's shaders for character materials it has one for.
- Maps through Material Porter's map reader: actor components, overrides, custom primitive data, spline meshes, water bodies, level instances.
- Rocket Racing cars with their styles (tier, body colour, paint, decal, wheels).
- LEGO outfits (cooked and recipe figures, with face styles), LEGO emotes with animated faces, LEGO props and building sets, LEGO wildlife (needs LEGO Fortnite's optional content installed).
- Character rigs and physics: Fortnite's official body rig, face boards, deform correctives, dynamic bones, cloth and rigid bodies, with wind.
- No online account: sign-in, chat and FortnitePorting's updater are off. New releases of this fork are announced in the app.
