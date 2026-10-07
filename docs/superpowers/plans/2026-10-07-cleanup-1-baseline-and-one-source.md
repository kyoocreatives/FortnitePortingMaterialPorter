# Cleanup Plan 1: reference outputs, one source, debug-only test routes

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the before/after check every later cleanup step relies on, then make the fork the only source of its code and keep test routes out of release builds, with exports and Blender results unchanged.

**Architecture:** Work happens on a `cleanup` branch in a sibling worktree (`Documents/Claude/fpfork-cleanup`). A `baseline.py` dev tool records reference outputs from the current `materialporter` code (export JSON and exported files from a Debug test instance, plus a signature of what the Blender plugin builds from them, plus the existing suites) and compares any later build against them. Then the generated `.g.cs` and synced `.py` copies become plain fork files, and the `fork-*` test routes move into a `#if DEBUG` partial.

**Tech Stack:** C# / .NET 10 (Avalonia app, CUE4Parse), Python 3 for dev tools, Blender 5.2 headless (Steam install) for plugin checks, Git Bash.

**Spec:** `docs/superpowers/specs/2026-10-07-fork-cleanup-design.md` (sections 1 and 2; later sections get Plans 2-4).

## Global Constraints

- Pure cleanup: exports and Blender results must not change. Any unexplained difference in a compare stops the task.
- Only test instances (`FORTNITEPORTING_MP_PROFILE`, port 24322 or another) and isolated headless Blender (`BLENDER_USER_CONFIG/EXTENSIONS/SCRIPTS` in a scratch folder, `--factory-startup`). Never the owner's running app (port 24320) or Blender.
- Commits: short plain messages, shown to the owner and approved before committing. Author `kyoocreatives <280023218+kyoocreatives@users.noreply.github.com>`, no co-author trailer.
- Never commit `Fork.local.props`; never stage `external/CUE4Parse`.
- Upstream (FortnitePorting) files are never reformatted.
- Private overlay (`fpfork-private`) code and names never go into the fork repo.
- Blender: `C:/Program Files (x86)/Steam/steamapps/common/Blender/blender.exe`.

## Review Focus

- A test instance left running from an earlier task: the next build fails to copy the exe or compares the wrong build. `testapp.sh` must stop instances started from the worktree's own Debug output too.
- Non-deterministic output (timestamps, GUIDs, dictionary order, parallel map reading) showing up as false differences: the noise check in Task 5 must classify these before any cleanup starts.
- The worktree missing what the main checkout has implicitly: the CUE4Parse overlay files, `Fork.local.props`, the private overlay. Then builds differ and compares lie. Task 1 checks the owner flags with `constcheck`.
- A release build still answering a test route, or a debug build missing one the dev tools need. Task 8 checks both directions.
- The plugin copy used for Blender checks coming from the wrong folder (main checkout instead of worktree). `baseline.py` takes the fork folder explicitly and prints it.

---

### Task 1: Worktree and dev tools that can target it

**Files:**
- Modify: `fpfork-private/devtools/testapp.sh` (fork folder from `FPFORK`, stop rule)
- Modify: `fpfork-private/devtools/README.md`

**Interfaces:**
- Produces: `FPFORK=<dir> bash testapp.sh <name> [...]` builds and runs that checkout's Debug app. Default stays `../../fpfork`.

- [ ] **Step 1: Create the worktree**

```bash
cd /c/Users/kyooc/Documents/Claude/fpfork
git worktree add ../fpfork-cleanup -b cleanup materialporter
cd ../fpfork-cleanup
git submodule update --init --recursive
cp -r patches/CUE4Parse/* external/CUE4Parse/
cp ../fpfork/src/FortnitePorting/Fork.local.props src/FortnitePorting/
```

- [ ] **Step 2: Make testapp.sh take the fork folder**

In `testapp.sh`, replace `FORK="$HERE/../../fpfork"` with:

```bash
FORK="$(cd "${FPFORK:-$HERE/../../fpfork}" && pwd)"
```

and the stop line's path pattern `*fpfork\src\FortnitePorting\bin\*` with one built from `$FORK`:

```bash
WINFORK="$(cd "$FORK" && pwd -W | sed 's#/#\\#g')"
powershell -NoProfile -Command "Get-Process -Name FortnitePorting -ErrorAction SilentlyContinue | Where-Object { \$_.Path -like '$WINFORK\src\FortnitePorting\bin\*' } | Stop-Process -Force" 2>/dev/null
```

- [ ] **Step 3: Build and run the worktree, check owner flags**

```bash
cd /c/Users/kyooc/Documents/Claude/fpfork-private/devtools
FPFORK=../../fpfork-cleanup bash testapp.sh test
dotnet constcheck/bin/Release/net10.0/constcheck.dll ../../fpfork-cleanup/src/FortnitePorting/bin/Debug/net10.0/win-x64/FortnitePorting.dll
```

Expected: `loaded in ...s on port 24322`, and `Islands = True`, `OlderBuilds = True`, `TimeOfDayExport = True`.

- [ ] **Step 4: README line**

Add to the README table: ``FPFORK=<dir>`` before `testapp.sh` runs another checkout (a worktree).

- [ ] **Step 5: Commit (fpfork-private)** after the owner approves the message, proposed: `testapp: run another checkout`.

---

### Task 2: baseline.py, export part

**Files:**
- Create: `fpfork-private/devtools/baseline.py`
- Create: `fpfork-private/devtools/baseline_assets.json`

**Interfaces:**
- Produces: `python baseline.py record|compare --fork <dir> --port <n> [--only exports|blender|suites]`. Reference stored in `fpfork-private/devtools/out/baseline/` (git-ignored by the existing `out/` rule). Compare prints one line per difference and exits 1 if any.
- `baseline_assets.json`: list of `{ "name": str, "route": "fork-export-asset" | "fork-export-world", "query": {..} }`.

- [ ] **Step 1: Pick the reference assets**

With the worktree test instance running (Task 1), find exact paths with the existing route, for example:

```bash
python - <<'EOF'
import json, urllib.request, urllib.parse
def get(r, **q): return json.loads(urllib.request.urlopen(f"http://localhost:24322/{r}?{urllib.parse.urlencode(q)}", timeout=600).read())
for words in ["Character Jules Elite", "Character Bonerattler", "Sidekick", "Vehicle Body", "Figure Juno", "Wildlife Juno"]:
    print(words, get("fork-find-assets", q=words)[:5])
EOF
```

Write `baseline_assets.json` with 10 entries covering: an outfit with skin subsurface, an outfit with shell fur, an outfit with a named style (`styles=`), a sidekick, a Rocket Racing car with `picks=`, a LEGO outfit with `face=`, a LEGO emote, a LEGO prop, a weapon with `effects=1`, and one map cell through `fork-export-world` (`path=<a Hera_V2 _Generated_ cell>&landscape=1`). Each entry uses the query parameters the route already accepts (see `MaterialPorterService.cs`, `fork-export-asset` and `fork-export-world`).

- [ ] **Step 2: Write the export capture**

`baseline.py` export part:
- For each asset: call the route, save the JSON as `exports/<name>.json` with keys sorted and indented.
- Walk the JSON for strings that are paths of exported files under `MetaData.AssetsRoot` (any string starting with that root, or relative paths the plugin joins to it) and save `exports/<name>.files.json` = `{relative path: sha256}`.
- `compare` re-runs into a temp folder and diffs: JSON with a recursive diff that prints the JSON path of each differing value, and file hashes by path.

```python
def diff(a, b, path="$", out=None):
    out = [] if out is None else out
    if type(a) is not type(b):
        out.append(f"{path}: {a!r} != {b!r}")
    elif isinstance(a, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                out.append(f"{path}.{k}: only in {'reference' if k in a else 'new'}")
            else:
                diff(a[k], b[k], f"{path}.{k}", out)
    elif isinstance(a, list):
        if len(a) != len(b):
            out.append(f"{path}: length {len(a)} != {len(b)}")
        for i, (x, y) in enumerate(zip(a, b)):
            diff(x, y, f"{path}[{i}]", out)
    elif a != b:
        out.append(f"{path}: {a!r} != {b!r}")
    return out
```

- [ ] **Step 3: Self-test**

Run `record`, then `compare` against the same build. Expected: the compare lists only noise (see Task 5); fix the capture if it fails to run.

- [ ] **Step 4: Commit (fpfork-private)** after approval, proposed: `Add baseline tool (exports)`.

---

### Task 3: baseline.py, Blender part

**Files:**
- Modify: `fpfork-private/devtools/baseline.py`
- Create: `fpfork-private/devtools/baseline_blender.py` (runs inside Blender)

**Interfaces:**
- Consumes: `exports/<name>.json` from Task 2.
- Produces: `blender/<name>.json`, the signature of the imported scene.

- [ ] **Step 1: Plugin copy**

`baseline.py` copies `<fork>/plugins/Blender/fortnite_porting` and then `fpfork-private/plugin/*` into a temp folder as package `fpmp_baseline` (the overlay files over it, as the app packs them), and prints the fork folder used.

- [ ] **Step 2: Import and dump inside Blender**

`baseline_blender.py <plugin parent> <payload.json> <out.json>`:

```python
import importlib, json, sys
import bpy
argv = sys.argv[sys.argv.index("--") + 1:]
sys.path.insert(0, argv[0])
bpy.ops.wm.read_factory_settings(use_empty=True)
fp = importlib.import_module("fpmp_baseline"); fp.ueformat_register()
from fpmp_baseline.processing.importer import Importer
Importer.Import(open(argv[1], encoding="utf-8").read())
# (deferred layouts run on a timer in the UI; run them now)
from fpmp_baseline.material_porter import build
for tree in list(bpy.data.node_groups) + [m.node_tree for m in bpy.data.materials if m.node_tree]:
    build.arrange_pending(tree)

def value(v):
    try:
        return [round(x, 5) for x in v]
    except TypeError:
        return round(v, 5) if isinstance(v, float) else str(v)

def tree_sig(t):
    nodes = {}
    for n in t.nodes:
        nodes[n.name] = {
            "type": n.bl_idname, "label": n.label, "loc": value(n.location),
            "parent": n.parent.name if n.parent else None,
            "tree": n.node_tree.name if getattr(n, "node_tree", None) else None,
            "inputs": {s.identifier: value(s.default_value) for s in n.inputs if hasattr(s, "default_value") and not s.is_linked},
            "props": {p.identifier: value(getattr(n, p.identifier)) for p in n.bl_rna.properties
                      if not p.is_readonly and p.type in {"ENUM", "BOOLEAN", "INT", "FLOAT", "STRING"} and p.identifier not in {"name", "select", "location", "width", "height", "show_options", "show_preview", "hide", "mute"}},
        }
    links = sorted(f"{l.from_node.name}:{l.from_socket.identifier}>{l.to_node.name}:{l.to_socket.identifier}" for l in t.links)
    return {"nodes": nodes, "links": links}

sig = {
    "groups": {g.name: tree_sig(g) for g in bpy.data.node_groups},
    "materials": {m.name: tree_sig(m.node_tree) for m in bpy.data.materials if m.node_tree},
    "objects": {o.name: {"type": o.type, "matrix": [value(r) for r in o.matrix_world],
                          "modifiers": [m.type for m in o.modifiers], "data": o.data.name if o.data else None,
                          "materials": [s.material.name if s.material else None for s in o.material_slots]}
                for o in bpy.data.objects},
    "images": sorted(i.name for i in bpy.data.images),
}
json.dump(sig, open(argv[2], "w"), indent=1, sort_keys=True)
```

If `build.arrange_pending` has another name when this task runs, use the function the timer in `build.py` calls (search `ensure_layout_timer`).

- [ ] **Step 3: Run it from baseline.py**

Per asset: `blender -b --factory-startup -P baseline_blender.py -- <plugin parent> exports/<name>.json blender/<name>.json` with `MATERIAL_PORTER_BRIDGE=http://localhost:<port>` and the scratch `BLENDER_USER_*` variables set; compare uses the same `diff`.

- [ ] **Step 4: Self-test** as Task 2 Step 3.

- [ ] **Step 5: Commit (fpfork-private)** after approval, proposed: `baseline: Blender signatures`.

---

### Task 4: Plugin tests into the fork, suites in baseline.py

**Files:**
- Create: `fpfork/tests/plugin/translator_test.py` (from `fpv4_clouds/tools/translator_test.py`)
- Create: `fpfork/tests/plugin/layout_check.py` (from `fpv4_clouds/tools/layout_check.py`)
- Modify: `fpfork-private/devtools/baseline.py` (`--only suites`)

**Interfaces:**
- Produces: `blender -b --factory-startup --python-exit-code 1 -P tests/plugin/translator_test.py -- <plugin parent>` exits 0 when all checks pass; same for `layout_check.py` with a pre-layout blend made by the test itself.

- [ ] **Step 1: Copy and repoint the tests**

Copy both files into the worktree. Replace their `sys.path.insert(0, HERE)` / `import ue_graph` / `import layout` lines so they import from the plugin package copy:

```python
sys.path.insert(0, sys.argv[sys.argv.index("--") + 1])
from fpmp_baseline.material_porter import ue_graph, layout, nodelib
```

`layout_check.py` builds its groups with fpv4_clouds' `build_groups.py`, which won't come along. Instead it lays out the translator tests' own built trees: build every tree `translator_test.py` builds, copy each, lay one out with `layout.arrange`, and run the existing comparison (`every input fed by the same source`, `unlinked values unchanged`, `interface unchanged`).

- [ ] **Step 2: Run both against the worktree plugin**

Expected: `translator_test` reports the same count as in fpv4_clouds (253 checks, 0 failed); `layout_check` reports `0 check(s) failed`.

- [ ] **Step 3: baseline.py suites**

`--only suites` runs both tests, `health.py test --meshes 300 --materials 150` against the instance, and the Wild Estate map preview through `fork-map-preview` (image hash). It records pass counts and health numbers; compare fails on any change.

- [ ] **Step 4: Commit** after approval, proposed: fork `Add plugin tests`, fpfork-private `baseline: test suites`.

---

### Task 5: Record the reference and find the noise

**Files:** none in git (output in `devtools/out/baseline/`).

- [ ] **Step 1: Record** from the worktree at the branch point (no cleanup yet): `python baseline.py record --fork ../../fpfork-cleanup --port 24322`.

- [ ] **Step 2: Compare twice** against fresh runs of the same build: `python baseline.py compare ...` two times.

- [ ] **Step 3: Classify every difference** as noise or a real nondeterminism. Write the noise rules into `baseline.py` as an explicit ignore list (JSON paths or key names, each with a one-line reason), for example generated GUIDs or timestamps. Real nondeterminism (for example placement order from parallel reading) is sorted in the signature instead of ignored.

- [ ] **Step 4: Compare once more.** Expected: `0 differences`. Show the owner the ignore list.

- [ ] **Step 5: Commit (fpfork-private)** after approval, proposed: `baseline: ignore list`.

---

### Task 6: C# copies become fork files

**Files:**
- Rename (git mv, in the worktree): `src/FortnitePorting/MaterialPorter/{MaterialService,Bridge,IslandProjects,ShaderMapHints}.g.cs` to `.cs`; `src/FortnitePorting.Exporting/MaterialPorter/{MapReader,Mutable,MutableMeshes,FigureRecipe,UEModelWriter}.g.cs` to `.cs`
- Split: `MapReader.cs` into `MapReader.cs` (the reader), `MapTypes.cs` (MapMesh, MapLight, MapDecal, MapEffect, MapOptions, MapCell, MapScan, MapLandscape) and `ParamSet.cs`

**Interfaces:**
- Produces: same types, namespaces and members as before; only file names and headers change.

- [ ] **Step 1: Rename and drop headers**

```bash
cd /c/Users/kyooc/Documents/Claude/fpfork-cleanup
for f in $(git ls-files 'src/**/*.g.cs'); do git mv "$f" "${f%.g.cs}.cs"; done
for f in $(git ls-files 'src/**/MaterialPorter/*.cs'); do sed -i '1{/^\/\/ Generated by/d}' "$f"; done
```

- [ ] **Step 2: Split MapReader.cs** by moving each type block as is into the two new files, with the same `using` lines and namespace.

- [ ] **Step 3: Build** Debug and Release (`-p:MPIslands=false -p:MPOlderBuilds=false "-p:MPPrivateRoot=none\\"`). Expected: 0 errors.

- [ ] **Step 4: Compare exports** `python baseline.py compare --only exports ...`. Expected: 0 differences.

- [ ] **Step 5: Commit** after approval, proposed: `Make Material Porter copies normal fork files`.

---

### Task 7: Python copies become plugin files; retire sync scripts

**Files:**
- Modify: the 11 plugin modules (`ue_graph`, `layout`, `nodelib`, `env`, `build`, `fallback`, `app_client`, `meshes`, `water`, `world`, `bundles`): drop the first `# Generated by ...` line
- Check: `fpfork-private/plugin/material_porter/` holds only the overlay's own files
- Retire: `materialporter/tools/sync_fork.py`, `sync_translator.py` (not in git; they leave with the archive in Plan 4)

- [ ] **Step 1: Drop headers**

```bash
cd /c/Users/kyooc/Documents/Claude/fpfork-cleanup/plugins/Blender/fortnite_porting/material_porter
for f in ue_graph layout nodelib env build fallback app_client meshes water world bundles; do sed -i '1{/^# Generated by/d}' $f.py; done
```

- [ ] **Step 2: Overlay check**

`ls ../../../../../fpfork-private/plugin/material_porter` lists only the overlay's own modules (no copy of the 11). If a copy is there, delete it from the overlay and make sure the overlay imports the public module instead.

- [ ] **Step 3: Compare Blender** `python baseline.py compare --only blender ...` and `--only suites`. Expected: 0 differences.

- [ ] **Step 4: Commit** after approval, proposed: `Edit the plugin's Material Porter modules in place`.

---

### Task 8: Test routes in Debug builds only

**Files:**
- Create: `src/FortnitePorting/MaterialPorter/MaterialPorterService.TestRoutes.cs`
- Modify: `src/FortnitePorting/MaterialPorter/MaterialPorterService.cs` (make the class `partial` if it isn't; move routes; one call)

**Interfaces:**
- Produces: `partial class MaterialPorterService { private async Task<object?> TestRouteAsync(string route, NameValueCollection query) }` defined only under `#if DEBUG`; under release, a stub returning `null`.
- Release keeps: `fork-caps`, `fork-island-find`, `fork-island-mesh`, `fork-island-material` (the plugin calls them) and the Material Porter routes in `Bridge.cs`.
- Debug adds: `fork-asset-page`, `fork-assets-page`, `fork-car`, `fork-dump`, `fork-effect-census`, `fork-effect-program`, `fork-emote-census`, `fork-export-asset`, `fork-export-world`, `fork-find-assets`, `fork-find-builds`, `fork-find-files`, `fork-loader`, `fork-map-preview`, `fork-map-shot`, `fork-projects`, `fork-reload`, `fork-screenshot`, `fork-settings-shot`, `fork-status`, `fork-weapon-mods`.

- [ ] **Step 1: Move the route blocks**

Cut each Debug route's `if (route == "...") { ... }` block, and helpers only they use (`SaveShot`, `PickedStyles`...), into the new file:

```csharp
#if DEBUG
namespace FortnitePorting.MaterialPorter;

public partial class MaterialPorterService
{
    // test-only bridge routes (dev tools: testapp.sh, health.py, baseline.py)
    private async Task<object?> TestRouteAsync(string route, System.Collections.Specialized.NameValueCollection query)
    {
        if (route == "fork-status") { ... }
        ...
        return null;
    }
}
#endif
```

and where the blocks were, one line:

```csharp
#if DEBUG
        if (await TestRouteAsync(route, query) is { } tested) return tested;
#endif
```

Use the real type of `query` in the existing method (check its signature first).

- [ ] **Step 2: Build both ways**

Debug: 0 errors. Release (`-c Release`): 0 errors.

- [ ] **Step 3: Check routes both ways**

Debug test instance: `fork-status` answers. Release exe started as a test instance (`FORTNITEPORTING_MP_PROFILE=test`, as done for the public-build check): `fork-status` answers 404 or an unknown-route error, `fork-caps` still answers. Stop it after.

- [ ] **Step 4: Full compare** `python baseline.py compare ...`. Expected: 0 differences.

- [ ] **Step 5: Commit** after approval, proposed: `Keep test routes in debug builds`.

---

### Task 9: Notes point at the fork

**Files:**
- Modify: memory notes `project-material-porter.md`, `project-fp-fork.md`, `project-exact-material-converter.md`, `reference-fpfork-devtools.md` (the "edit materialporter or fpv4_clouds, then sync" rule becomes "edit the fork; fpv4_clouds has its own copy")
- Modify: `fpfork/FORK.md` lines that tell people to sync from Material Porter (full rewrite is Plan 4)
- Modify: `Documents/Claude/resume.md`

- [ ] **Step 1: Update the notes** with the new rule and the `baseline.py` workflow (record once, compare after each step).

- [ ] **Step 2: Commit FORK.md** after approval, proposed: `FORK.md: code is edited in the fork`.

---

## Later plans (written when reached)

- Plan 2: C# and XAML out of upstream files (spec section 3, steps 3-4).
- Plan 3: Python out of upstream files, then module-by-module cleanup (spec sections 3-4, steps 5-7).
- Plan 4: docs rewrite, final compare, merge, archive the standalone app (spec step 8-9).
