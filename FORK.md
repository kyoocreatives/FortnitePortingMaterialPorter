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
- **UEFN islands by map code** and **older builds without an install** (owner builds only, see Flags).
- **Drawn map previews** for maps without a minimap.

Assets
- **Weapons as the game draws them**, with wraps and weapon mods on the asset's page.
- **Rigs**: creature, LEGO figure, sidekick and vehicle rigs (with the Tasty rig setting).
- **Animations tab** (Assets > Gameplay > Animations).
- **Particle effects, played** (Assets > Gameplay > Effects): Niagara CPU emitters are replayed
  over the scene's frames; GPU and stateless emitters are approximated.
- **Rocket Racing** cars and tracks; **LEGO** outfits, emotes, props and wildlife.

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
| `Fork.Islands` | `MP_ISLANDS` | UEFN islands by map code |
| `Fork.OlderBuilds` | `MP_OLDER_BUILDS` | builds downloaded from their manifest |
| `Fork.TimeOfDayExport` | `MP_TIME_OF_DAY` | time of day export (code in the owner's private overlay) |

Public releases build with all three off.

## Where the code lives

- Fork-only code: `src/FortnitePorting/MaterialPorter/`, `src/FortnitePorting.Exporting/MaterialPorter/`,
  `src/FortnitePorting/Views/MaterialPorter/` and `plugins/Blender/fortnite_porting/material_porter/`.
- Upstream classes are extended through partial class files named `<Class>.MaterialPorter.cs`
  next to the original.
- Where an upstream file has to call into the fork, the call is one line marked `// MP` (C#),
  `# MP` (Python) or `<!-- MP -->` (XAML). Larger UI additions are fork UserControls placed there.
- `patches/CUE4Parse/`: whole files laid over the `external/CUE4Parse` submodule (which the fork
  can't push to). Both workflows copy them in after checkout. They read UE 5.6 splines and the mixed
  5.3/5.4 layouts of Fortnite 28.00 builds. After updating the submodule, drop the ones upstream
  now handles.

## Releases

Pushing a tag `v<FP version>-mp.<N>` runs `.github/workflows/build-release.yml`: a single-file
`FortnitePortingMP.exe` released with `RELEASE_NOTES.md` as its text. FP's updater sees `-mp.N`
as a dev build, so `MaterialPorter/ForkUpdates.cs` checks the fork's own releases instead.

## Merging upstream

    git fetch upstream
    git merge upstream/main

Conflicts can only come from the marked lines in upstream files; the fork's own files and
partial classes don't overlap upstream's.
