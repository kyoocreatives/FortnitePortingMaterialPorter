# Fork cleanup design

## Goal

Make the fork's code clean, short and readable, and keep it easy to merge upstream FortnitePorting into. The fork stays a fork (no upstream pull requests). Pure cleanup: exports and Blender results must not change.

Agreed with the owner on 2026-10-07:
- The fork becomes the only source of its code. The standalone Material Porter app is retired; fpv4_clouds keeps its own copy of the translator and stops syncing.
- All seven kinds of cleanup are in scope: comments, fork code inside upstream files, dead code, test routes, generated copies, big files, naming and style.
- No behaviour change, checked against recorded outputs after every step.
- Test routes stay, in Debug builds only.

## 1. Reference outputs and checks

`fpfork-private/devtools/baseline.py` with two commands:
- `record`: saves the reference outputs once, before any cleanup.
- `compare`: re-runs and diffs against the reference.

Reference set (covers every fork feature): an outfit with skin subsurface, one with shell fur, one with a style swap, a sidekick, a Rocket Racing car with styles, a LEGO outfit, emote and prop, a weapon with effects, a map cell (meshes, lights, decals, effects, landscape), and a 28.00 static mesh.

What is compared:
1. Export JSON written by the app for each asset (test routes on a test instance).
2. A headless Blender import of each export, dumped as a signature per material and object: node types, settings, values, locations, links, group contents, object transforms and modifiers.
3. Existing suites: translator tests, layout check, `health.py`, a map preview render.

Any difference stops the step: it is either fixed or explained as noise (timestamps, ordering). Small steps run only the parts they touch; each module ends with a full compare. Runs use test instances and isolated headless Blender only.

## 2. One source

- The nine `.g.cs` files (MaterialService, Bridge, IslandProjects, ShaderMapHints, MapReader, Mutable, MutableMeshes, FigureRecipe, UEModelWriter) become plain `.cs` fork files without the generated header. Files the sync script merged are split into sensible files (MapReader holds Maps, MapLandscape and ParamSet).
- The 11 synced plugin modules (`ue_graph`, `layout`, `nodelib`, `env`, `build`, `fallback`, `app_client`, `meshes`, `water`, `world`, `bundles`) lose their headers and are edited in the plugin.
- The private overlay keeps only its own files; the rest comes from the public plugin. Owner builds must still include the private features.
- `materialporter/tools/sync_fork.py` and `sync_translator.py` are retired. The standalone `materialporter/` folder is archived (zip) and removed after the owner confirms.
- fpv4_clouds keeps its own copy and is not referenced by the fork.
- All `fork-*` bridge routes move to `MaterialPorterService.TestRoutes.cs` inside `#if DEBUG`. Release builds keep only the routes the Blender plugin calls. Dev tools use Debug builds and keep working.

## 3. Fork code out of upstream files

Target: each of the 62 upstream files keeps only small hook lines (ideally under 10 changed lines), marked `// MP` or `# MP`.

- C# classes: made `partial` (one word); fork logic moves to `<Name>.MaterialPorter.cs`. Long inline blocks (MeshExport, AnimExport) become one call into a fork helper.
- Enums and models: new values stay in place, grouped at the end under one marker.
- XAML: fork rows and panels become small fork `UserControl`s inserted with one line (BlenderSettingsView, both Installation views, AppWindow, AssetsView, MapView).
- Python plugin: fork logic in `mesh_context`, `material_context`, `anim_context`, `importer` and `utils` moves into `material_porter/` modules; upstream files keep hook calls.
- Exception: where moving code would need a bigger edit to the upstream file than leaving it, it is cleaned in place. Each such case is listed in the final report.

## 4. Cleanup rules

- Comments: say why or state a non-obvious fact, in one or two short lines. No narration, no parenthetical asides, no "Material Porter fork:" prefix. Reverse-engineered game and engine facts stay, shortened.
- Names: follow upstream style (C# PascalCase members, `_camelCase` fields; Python snake_case). No cryptic names outside tiny local loops.
- Dead code: unused members, flags fixed to one value and their dead paths, commented-out code and experiments are removed. Found with compiler warnings and `vulture`, each hit checked by hand.
- File size: files over about 600 lines are split along clear responsibilities (`ue_graph.py`, `MaterialPorterService.cs`, `build.py`, `layout.py`, `effect_replay.py`). Splits move code only.
- Formatting: `dotnet format` and `ruff format` on fork files only; upstream files are never reformatted. Line length about 140 (C#) and 120 (Python).
- Python: no monkey-patching where a direct call works, no bare `except:`, type hints on module-level functions.
- CUE4Parse patch files: keep the difference from CUE4Parse's own code as small as possible.

## 5. Order of work

Work happens on a `cleanup` branch in its own worktree; `materialporter` keeps shipping meanwhile.

1. Baseline tool and recorded reference.
2. One source (section 2).
3. C# out of upstream files.
4. XAML into fork controls.
5. Python out of upstream files.
6. C# cleanup, module by module.
7. Python cleanup, module by module.
8. Docs: `FORK.md` rewritten short (what the fork adds, where the code lives, build and release), dev tools README, memory notes.
9. Final full compare, merge into `materialporter`, archive the standalone app after confirmation, release when the owner asks.

Commits are small, one per module or step. Each step's commit messages are shown to the owner for approval before committing: short and plain.

If a compare shows a difference that can't be explained, that step stops and the owner is told.

## Out of scope

- Behaviour changes and bug fixes (noted for later, not made).
- Reformatting or restyling upstream FortnitePorting code.
- Upstream pull requests.
