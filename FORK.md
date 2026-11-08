# FortnitePorting, Material Porter fork

Branch `materialporter` on top of upstream `h4lfheart/FortnitePorting` (remote `upstream`,
base `cabc462d`, 2026-09-18). GPL-3.0 like upstream.

## What it adds

Materials
- **Exact materials.** Materials are rebuilt in Blender from their UE graph instead of FP's preset
  shaders. The app serves the graphs, textures and parameters on a local bridge (port 24320); the
  plugin's `material_porter` package translates them. FP's own material is the fallback.
- **Convert to Exact Materials** (3D View sidebar > Fortnite Porting > Exact Materials): turns
  materials from earlier FP imports into exact ones.
- **Material Fixer** (same tab, also in the Shader Editor's sidebar): repairs FP materials in place
  and writes a report to the "FP Fixer Report" text (`operator/fixer/`).
- **Prefer FP Shaders for Characters** and **Rim Light** (Blender settings > Materials).
- **Max Texture Size** (Export Options > Blender > Texture) and textures kept compressed in GPU memory.

Maps
- **Maps through the fork's map reader**: actors, instances, landscapes, splines, water, decals,
  lights (with shadow linking), placed effects, Neon City billboards, shadow proxies.
- **Terrain Under Cells** (Map page and the editor's Map tab): picked cells bring only the landscape tiles under them.
  Tiles line up across exports; one already in the scene isn't added again.
- **UEFN islands by map code** and **local UEFN projects** (with UEFN access, see below).
- **Older builds without an install** (owner builds only, see Flags).
- **Drawn map previews** for maps without a minimap.

Assets
- **Weapons as the game draws them**, with wraps and weapon mods on the asset's page.
- **Rigs**: creature, LEGO figure, sidekick and vehicle rigs (with the Tasty rig setting). Fortnite's official rig,
  with optional extra hand controls and a held prop the fingers grip (Blender > Armature > Extra Hand Controls;
  not part of Epic's rig).
- **Animations tab** (Assets > Gameplay > Animations), filtered by what they're for, gender, body type and use
  (Locomotion, Combat, Skydiving...).
- **Particle effects, played** (Assets > Gameplay > Effects): Niagara CPU emitters are replayed
  over the scene's frames; GPU and stateless emitters are approximated.
- **Rocket Racing** cars and tracks; **LEGO** outfits, emotes, props and wildlife.

Editor
- **Fortnite editor** in stock Blender 5.2 and up (`operator/fortnite_editor/`, routes in
  `MaterialPorter/Editor/`): a node editor of its own, GPU-drawn, with Assets, Files, Maps, Rig and Landscape tabs. The
  app runs as its hidden engine (`Engine`, `--background`), started on the editor's first draw.

App
- **Status line and log** under the window, as in Material Porter's app.
- **No online account**: `SupabaseService` is inert; no sign-in, chat or leaderboard.

## Runs beside upstream FP

`MaterialPorter/Fork.cs` names what differs: settings in `%APPDATA%\FortnitePorting MP`, data in
`%LOCALAPPDATA%\FortnitePorting MP`, instance pipe `FortnitePortingMP`, plugin installed as
`scripts/startup/fortnite_porting_mp`, Blender port 40010 (upstream 40000), bridge 24320.

`FORTNITEPORTING_MP_PROFILE=test` runs a separate instance (own folders, bridge 24322;
`FORTNITEPORTING_MP_BRIDGE_PORT` picks another). Debug builds add test routes to the bridge
(`MaterialPorterService.TestRoutes.cs`); Release builds don't have them.

## Flags

Set in `Fork.local.props` (not committed) and read through `Fork`:

| Flag | Build constant | What it turns on |
|---|---|---|
| `Fork.Islands` | `MP_ISLANDS` | UEFN access always on |
| `Fork.OlderBuilds` | `MP_OLDER_BUILDS` | builds downloaded from their manifest |
| `Fork.TimeOfDayExport` | `MP_TIME_OF_DAY` | time of day export (code in the owner's private overlay) |

Public releases build with all three off.

## UEFN access

Islands by map code and local UEFN projects are in every build, off unless `Fork.Islands` is true. A public build turns it
on for people with a role on the owner's Discord server: they sign in with Discord (Settings > Application > UEFN Access,
PKCE, no client secret; `UefnAccess`) and the app reads the role from Discord at each start. The sign-in is kept in
`%APPDATA%\FortnitePorting MP`, which updates don't replace, so access stays. Removing the role ends it at their next start.
Access needs internet at each start: if Discord can't be asked, it's off for that run and the sign-in is kept. Without
access, the only thing that shows is a plain Discord sign-in row that doesn't say what it's for. This keeps access out of ordinary use; it can't stop someone building the source
with the check changed.

## Where the code lives

- Fork-only code: `src/FortnitePorting/MaterialPorter/`, `src/FortnitePorting.Exporting/MaterialPorter/`,
  `src/FortnitePorting/Views/MaterialPorter/`, `plugins/Blender/fortnite_porting/material_porter/` and
  `plugins/Blender/fortnite_porting/operator/fortnite_editor/`.
- Plugin checks: `tests/plugin/`, each run headless with `-- <plugin parent>`.
- Upstream classes are extended through partial class files named `<Class>.MaterialPorter.cs`
  next to the original.
- Where an upstream file has to call into the fork, the call is one line marked `// MP` (C#),
  `# MP` (Python) or `<!-- MP -->` (XAML). Larger UI additions are fork UserControls placed there.
- `patches/CUE4Parse/`: whole files laid over the `external/CUE4Parse` submodule (which the fork
  can't push to). Both workflows copy them in after checkout. They read UE 5.6 splines and the mixed
  5.3/5.4 layouts of Fortnite 28.00 builds, and 43.00's (UE 6.0) Mutable programs and meshes. After
  updating the submodule, drop the ones upstream now handles.

## Releases

A version has two parts, shown as `fpv-4.3.5/mpv-1.12.3`: the upstream FP version the fork is merged
with, and Material Porter's own semantic version (a fix bumps the last number; a bigger update bumps
the middle one and resets the last).

Pushing a tag `v<FP version>-mp.<MP version>` (`v4.3.5-mp.1.14.0`) runs
`.github/workflows/build-release.yml`: a single-file `FortnitePortingMP.exe` released with
`RELEASE_NOTES.md` as its text. FP's updater sees `-mp.` as a dev build, so
`MaterialPorter/ForkUpdates.cs` checks the fork's own releases instead.

Apps from before this numbering only read GitHub's latest release, and only a whole number after
`mp.`. A bridge tag has one (`v4.3.5-mp.154-1.13.0`) and the workflow marks it latest; plain tags are
not marked. So old apps keep being sent to the bridge, and the bridge, which reads the whole release
list, prompts on to the newest. A new bridge tag moves "latest" forward.

## Merging upstream

    git fetch upstream
    git merge upstream/main

Conflicts can only come from the marked lines in upstream files; the fork's own files and
partial classes don't overlap upstream's.

Then set the new upstream version in the csproj's `<Version>`, `blender_manifest.toml`, `bl_info` in
the plugin's `__init__.py` and `FortnitePorting.uplugin`.
