An unofficial fork of [FortnitePorting](https://github.com/h4lfheart/FortnitePorting) that rebuilds Fortnite's materials in Blender from their Unreal material graphs, and adds LEGO Fortnite and Rocket Racing content. Please don't report its problems on FortnitePorting's repository.

**Install:** download `FortnitePortingMP.exe` below and run it (Windows x64, no installer). Windows may warn about an unsigned app: *More info > Run anyway*. It runs beside an installed FortnitePorting, with its own settings (`%APPDATA%\FortnitePorting MP`) and its own Blender plugin (`fortnite_porting_mp`), installed from the Plugins page as in FortnitePorting. Exact materials need Blender 5.0 or newer; older Blender gets FortnitePorting's shaders.

**This release**
- Works with the latest Fortnite update (version 5 mappings).
- Official rig: a clearer side panel with a control picker, one-click IK/FK, stretch and local toggles, and a Shift+Q pie; widgets and colours match Epic's; hand and limb controls sit on their bones.
- Extra hand controls: curl and spread every finger at once, and a held prop the fingers close on.
- The Assets page's rig button applies the official rig when it's chosen in the settings; a Face Board button puts a face board on the selected armature.
- Creature, LEGO and car rigs rebuilt on the official rig's standard.
- Animations: filters by gender, body type and use (locomotion, swimming, skydiving...), an icon per tile, and faster listings.
- Physics: skirts follow and collide with the legs (also when a clip starts mid-pose); fixes for Roxas's coat and Riku's hair.
- Bake to Controls: fingertips stay on the hand on fast clips; baked IK plays the animation.
- Islands: only the newest downloaded version of an island is mounted.
- Fixes: square sprites on effects (curve atlases), a faster rig panel, Shift+Q no longer taken by FortRig on the official rig.

**What it adds**
- Exact materials, rebuilt from each material's Unreal graph. *Settings > Blender > Prefer FP Shaders for Characters* keeps FortnitePorting's shaders for character materials it has one for.
- Maps through Material Porter's map reader: actor components, overrides, custom primitive data, spline meshes, water bodies, level instances.
- Rocket Racing cars with their styles (tier, body colour, paint, decal, wheels).
- LEGO outfits (cooked and recipe figures, with face styles), LEGO emotes with animated faces, LEGO props and building sets, LEGO wildlife (needs LEGO Fortnite's optional content installed).
- Character rigs and physics: Fortnite's official body rig, face boards, deform correctives, dynamic bones, cloth and rigid bodies, with wind.
- No online account: sign-in, chat and FortnitePorting's updater are off. New releases of this fork are announced in the app.
