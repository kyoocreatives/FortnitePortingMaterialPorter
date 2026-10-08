# Subsurface Controls Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Skin Subsurface, Base Subsurface, Scatter Distance and Profile Colour controls for exact materials, in the app's settings and on each material's group node.

**Architecture:** The app sends two new options (`BaseSubsurface`, `ProfileColour`) beside the existing ones. The plugin's hook resolves them per import (Base only on cosmetic imports, fur keeps its own) and `build.assemble` wires the group-node inputs: amount = max(Base, game skin amount x Skin), radius = mix(neutral, profile radius, Profile Colour). Values are set on the built material's group node after the build, as today.

**Tech Stack:** C# / Avalonia (app settings), Python in Blender 5.2 (plugin), headless Blender tests (`tests/plugin/*.py`), the fork's baseline compare (`fpfork-private/devtools/baseline.py`).

**Spec:** `docs/superpowers/specs/2026-10-08-subsurface-controls-design.md`

## Global Constraints

- Defaults: Skin Subsurface 1, Base Subsurface 0, Scatter Distance 1 (x the game's), Profile Colour 0.5; fur unchanged (Fur Subsurface Intensity 1, Fur Subsurface Scale 1).
- Ranges: Skin 0-1, Base 0-1, Scatter Distance 0-10, Profile Colour 0-1.
- Base Subsurface only on cosmetic imports: export categories Cosmetic, LEGO (not LEGO prop), Fall Guys, Festival, and Vehicle (Rocket Racing cars come as Vehicle). Never World, Prefab, Mesh, Effect, props.
- Neutral radius = the radius's channel mean on all three channels; no-profile skin = `SKIN_RADIUS` (1, 0.8, 0.65) over 3 mm.
- Existing app setting names stay (`SubsurfaceIntensity`, `SubsurfaceScale`, `FurSubsurfaceIntensity`, `FurSubsurfaceScale`) so saved settings carry over.
- Group-node input names: `Skin Subsurface`, `Base Subsurface`, `Scatter Distance`, `Subsurface Radius`, `Profile Colour`; shell fur: `Subsurface Intensity`, `Scatter Distance`, `Subsurface Radius`.
- Code habits: short why-comments, fork code in fork files, upstream files only `# MP` / `// MP` one-liners; commit messages short and plain, approved by the user before committing.
- Headless Blender runs use isolated `BLENDER_USER_*` folders and `--factory-startup`; never touch the user's Blender or the running app (port 24320).

## Review Focus

- A cosmetic material the game never scatters (Lexa's toon, anime Cloth masters) at Base 0 must render exactly as before (amount 0), not gain visible scattering.
- A profile with equal channels (neutral already) must not change with Profile Colour.
- Scatter Distance 0 must not break the BSDF (Blender accepts scale 0: no scattering) and Skin 0 with Base 0 must give amount 0 on skin.
- A shell fur layer must keep scattering fully with its own fur values and get no Base / Profile Colour inputs.
- A World/Prefab import must get no new inputs on materials the game doesn't scatter (no Eevee subsurface pass added to maps).

Each line above has its check in Task 2's `subsurface_check.py` (cases named after them).

---

## File Structure

- `plugins/Blender/fortnite_porting/material_porter/hook.py`: `subsurface()` resolves the options per import; `is_cosmetic()`; sets the values on the built material's group node.
- `plugins/Blender/fortnite_porting/material_porter/build.py`: constants, `settings()` gains `cosmetic`, `assemble()` subsurface section, `_subsurface_panel()` names, `BUILD_REVISION`.
- `tests/plugin/subsurface_check.py` (new): headless checks of option resolution and of the built graph's amount and radius.
- `src/FortnitePorting/ViewModels/Settings/BlenderSettingsViewModel.cs`: two new options (upstream file: fork lines marked `// MP` as the neighbours are).
- `src/FortnitePorting/Views/MaterialPorter/ExactMaterialSettings.axaml`: labels and two sliders.
- `fpfork-private/devtools/baseline.py`: runs `subsurface_check.py` with the other suites.

---

### Task 1: Resolve the subsurface options per import

**Files:**
- Modify: `plugins/Blender/fortnite_porting/material_porter/hook.py:119-131` (`subsurface()`), `:252-256` (variant digest), `:296-299` (applying values; finished in Task 2)
- Test: `tests/plugin/subsurface_check.py` (create)

**Interfaces:**
- Produces: `hook.Subsurface` (namedtuple `skin, base, scale, colour`), `hook.subsurface(context, material_data) -> Subsurface`, `hook.is_cosmetic(export_type) -> bool`, `entry["cosmetic"]` (bool) set by the hook before the build.

- [ ] **Step 1: Write the failing test**

Create `tests/plugin/subsurface_check.py`:

```python
"""Checks the exact materials' subsurface controls: which values an import resolves (skin, base, distance, profile
colour; fur its own) and what the built graph scatters (amount = max(base, game x skin), radius = mix(neutral,
profile, colour)).

    blender -b --factory-startup --python-exit-code 1 -P tests/plugin/subsurface_check.py -- <plugin parent>

<plugin parent> is a folder holding the plugin as a package named fpmp_baseline (see translator_test.py). Exit code 0
when every check passes."""
import sys
from types import SimpleNamespace

import bpy

sys.path.insert(0, sys.argv[sys.argv.index("--") + 1])
from fpmp_baseline.material_porter import hook  # noqa: E402
from fpmp_baseline.processing.enums import EExportType  # noqa: E402

FAILS = []
PASSES = [0]


def check(name, got, want, tol=1e-4):
    ok = all(abs(g - w) <= tol for g, w in zip(got, want)) if isinstance(want, tuple) else \
        (abs(got - want) <= tol if isinstance(want, float) else got == want)
    if ok:
        PASSES[0] += 1
    else:
        FAILS.append(name)
        print("[subsurface_check] FAIL %s: got %r, want %r" % (name, got, want))


def context(kind, **options):
    return SimpleNamespace(type=kind, options=options)


# options per import
s = hook.subsurface(context(EExportType.OUTFIT), {})
check("defaults on an outfit", tuple(s), (1.0, 0.0, 1.0, 0.5))
s = hook.subsurface(context(EExportType.OUTFIT, SubsurfaceIntensity=0.7, BaseSubsurface=0.3, SubsurfaceScale=4.0,
                            ProfileColour=0.2), {})
check("set on an outfit", tuple(s), (0.7, 0.3, 4.0, 0.2))
s = hook.subsurface(context(EExportType.WORLD, BaseSubsurface=0.3), {})
check("no base on a world", s.base, 0.0)
s = hook.subsurface(context(EExportType.OUTFIT, FurSubsurfaceIntensity=0.6, FurSubsurfaceScale=3.0, BaseSubsurface=0.3,
                            ProfileColour=0.2), {"MPMoves": True})
check("fur keeps its own", tuple(s), (0.6, 0.0, 3.0, 1.0))
s = hook.subsurface(context(EExportType.OUTFIT, SubsurfaceScale=50.0, BaseSubsurface=2.0), {})
check("ranges clamp", (s.scale, s.base), (10.0, 1.0))
for kind, want in ((EExportType.OUTFIT, True), (EExportType.PICKAXE, True), (EExportType.LEGO_OUTFIT, True),
                   (EExportType.LEGO_PROP, False), (EExportType.VEHICLE, True), (EExportType.FALL_GUYS_OUTFIT, True),
                   (EExportType.WORLD, False), (EExportType.PREFAB, False), (EExportType.MESH, False),
                   (EExportType.EFFECT, False)):
    check("cosmetic %s" % kind.name, hook.is_cosmetic(kind), want)

print("[subsurface_check] %d passed, %d failed" % (PASSES[0], len(FAILS)))
if FAILS:
    sys.exit(1)
```

- [ ] **Step 2: Run it to verify it fails**

Copy the plugin first (devtools helper), then run:

```bash
python -c "import sys; sys.path.insert(0, r'C:\Users\kyooc\Documents\Claude\fpfork-private\devtools'); import baseline; print(baseline.copy_plugin(r'C:\Users\kyooc\Documents\Claude\fpfork'))"
"C:/Program Files (x86)/Steam/steamapps/common/Blender/blender.exe" -b --factory-startup --python-exit-code 1 -P tests/plugin/subsurface_check.py -- "C:/Users/kyooc/Documents/Claude/fpfork-private/devtools/out/baseline-plugin"
```

(with `BLENDER_USER_CONFIG`, `BLENDER_USER_SCRIPTS`, `BLENDER_USER_EXTENSIONS` set to a scratch folder). Expected: FAIL, `subsurface()` returns a 2-tuple and `is_cosmetic` doesn't exist.

- [ ] **Step 3: Implement**

In `hook.py`, replace `subsurface()` with:

```python
Subsurface = namedtuple("Subsurface", "skin base scale colour")

# imports whose materials may scatter over their whole surface (Base Subsurface): characters and items, not
# levels or props (hundreds of materials would all compile Eevee's subsurface pass); Rocket Racing cars come as Vehicle
COSMETIC_CATEGORIES = (ExportCategory.COSMETIC, ExportCategory.LEGO, ExportCategory.FALL_GUYS, ExportCategory.FESTIVAL)


def is_cosmetic(export_type):
    if export_type in (EExportType.LEGO_PROP,):
        return False
    return export_type == EExportType.VEHICLE or (int(export_type) & 0xFF00) in COSMETIC_CATEGORIES


def subsurface(context, material_data):
    """The import's subsurface values: Skin Subsurface (x the game's skin amount), Base Subsurface (over the whole
    surface, cosmetics only), Scatter Distance (x the game's) and Profile Colour. Shell fur layers and their base
    (material_porter.shells) use Fur Subsurface Intensity/Scale and neither base nor profile colour."""
    options = getattr(context, "options", None) or {}

    def value(key, default, most):
        v = options.get(key)
        return default if v is None else min(max(0.0, float(v)), most)
    if material_data.get("MPMoves"):
        return Subsurface(value("FurSubsurfaceIntensity", 1.0, 1.0), 0.0, value("FurSubsurfaceScale", 1.0, 20.0), 1.0)
    cosmetic = is_cosmetic(getattr(context, "type", EExportType.NONE))
    return Subsurface(value("SubsurfaceIntensity", 1.0, 1.0), value("BaseSubsurface", 0.0, 1.0) if cosmetic else 0.0,
                      value("SubsurfaceScale", 1.0, 10.0), value("ProfileColour", 0.5, 1.0))
```

Add the imports at the top of `hook.py` (beside its other imports): `from collections import namedtuple` and `from ..processing.enums import EExportType, ExportCategory`.

Where the hook digests the values into the variant (today `if sss != (1.0, 1.0):`), mark cosmetic entries and digest every non-default value:

```python
    # import's subsurface values (fur its own); a cosmetic's materials get the controls even where the game doesn't scatter
    sss = subsurface(context, material_data)
    entry["cosmetic"] = is_cosmetic(getattr(context, "type", EExportType.NONE)) and not material_data.get("MPMoves")
    ...
    if tuple(sss) != (1.0, 0.0, 1.0, 0.5) or entry["cosmetic"]:
        entry["variant"] = _digest("%s sss %g %g %g %g %s" % ((entry.get("variant", ""),) + tuple(sss) + (entry["cosmetic"],)))
```

(the `...` is the unchanged shell lines between them). Leave the value-setting block at `:296-299` for Task 2.

- [ ] **Step 4: Run the test to verify it passes**

Same commands as Step 2 (copy the plugin again first). Expected: `[subsurface_check] 15 passed, 0 failed`.

- [ ] **Step 5: Commit**

Propose the message `Resolve subsurface options per import` to the user; after their OK:

```bash
git add tests/plugin/subsurface_check.py plugins/Blender/fortnite_porting/material_porter/hook.py
git commit -m "Resolve subsurface options per import"
```

---

### Task 2: Build the controls into the material

**Files:**
- Modify: `plugins/Blender/fortnite_porting/material_porter/build.py:21` (`BUILD_REVISION`), `:32-37` (constants), `:56-77` (`settings()`), the subsurface section of `assemble()` (today `:221-284`), `_subsurface_panel()` (today `:1150-1176`)
- Modify: `plugins/Blender/fortnite_porting/material_porter/hook.py:296-299` (setting the values)
- Test: `tests/plugin/subsurface_check.py` (extend)

**Interfaces:**
- Consumes: `hook.Subsurface`, `entry["cosmetic"]` (Task 1).
- Produces: `build.SKIN_SUBSURFACE_INPUT`, `build.BASE_SUBSURFACE`, `build.SCATTER_DISTANCE`, `build.PROFILE_COLOUR`, `build.SUBSURFACE_INTENSITY` (fur), `build.SUBSURFACE_RADIUS`, `build.SKIN_SCALE`; `settings(entry)["cosmetic"]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/plugin/subsurface_check.py`, before the final summary lines:

```python
# the built graph: a small material through the translator and build.assemble, as build_one does
import json, os, runpy, tempfile  # noqa: E401,E402
from fpmp_baseline.material_porter import build  # noqa: E402
from fpmp_baseline.material_porter.ue_graph import Translator  # noqa: E402

T = runpy.run_path(os.path.join(os.path.dirname(os.path.abspath(__file__)), "translator_test.py"))  # its env and evaluator
TMP = tempfile.mkdtemp(prefix="sss_check_")


def built(shading, opacity=None, profile=None, cosmetic=True, shell=False):
    """(material, its group node) for a material scattering by `shading` with Opacity `opacity` (None: no pin)."""
    nodes = []
    props = {}
    if opacity is not None:
        nodes.append({"Type": "MaterialExpressionScalarParameter", "Name": "Op", "Properties": {"ParameterName": "op", "DefaultValue": opacity}})
        props["Opacity"] = {"ExpressionName": "Op", "OutputIndex": 0}
    nodes.append({"Type": "MaterialEditorOnlyData", "Name": "MaterialEditorOnlyData_0", "Properties": props})
    path = os.path.join(TMP, "M_%d.json" % len(os.listdir(TMP)))
    json.dump(nodes, open(path, "w", encoding="utf-8"))
    mat = bpy.data.materials.new("sss")
    mat.node_tree.nodes.clear()
    root = bpy.data.node_groups.new(mat.name, "ShaderNodeTree")
    env = T["TestEnv"]()
    tr = Translator(root, env)
    env.h[0] = tr
    vals = tr.material_attributes(path, build.CARRIED)
    entry = {"asset": {"ShadingModel": "EMaterialShadingModel::" + shading}, "subsurface": profile,
             "cosmetic": cosmetic, "shell": shell, "name": "sss"}
    build.assemble(tr, mat, vals, build.settings(entry))
    node = mat.node_tree.nodes.new("ShaderNodeGroup")
    node.node_tree = root
    return mat, node


def bsdf_input(node, name):
    bsdf = next(n for n in node.node_tree.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled")
    return T["ev_input"](bsdf.inputs[name], (node,))


rose = {"radius": [0.95, 0.332, 0.414], "scale": 0.003}
mat, node = built("MSM_SubsurfaceProfile", opacity=0.8, profile=rose)
check("skin inputs", [i.name for i in node.inputs if "ubsurface" in i.name or i.name in (build.SCATTER_DISTANCE, build.PROFILE_COLOUR)],
      [build.SKIN_SUBSURFACE_INPUT, build.BASE_SUBSURFACE, build.SCATTER_DISTANCE, build.SUBSURFACE_RADIUS, build.PROFILE_COLOUR])
check("skin amount: the game's x skin", bsdf_input(node, "Subsurface Weight"), 0.8)
node.inputs[build.SKIN_SUBSURFACE_INPUT].default_value = 0.5
check("skin amount x 0.5", bsdf_input(node, "Subsurface Weight"), 0.4)
node.inputs[build.BASE_SUBSURFACE].default_value = 0.6
check("base over a lower skin amount", bsdf_input(node, "Subsurface Weight"), 0.6)
node.inputs[build.SKIN_SUBSURFACE_INPUT].default_value = 0.0
node.inputs[build.BASE_SUBSURFACE].default_value = 0.0
check("skin 0 and base 0 scatter nothing", bsdf_input(node, "Subsurface Weight"), 0.0)
mean = sum(rose["radius"]) / 3
node.inputs[build.PROFILE_COLOUR].default_value = 1.0
check("profile colour 1: the profile's", bsdf_input(node, "Subsurface Radius"), tuple(rose["radius"]))
node.inputs[build.PROFILE_COLOUR].default_value = 0.0
check("profile colour 0: neutral", bsdf_input(node, "Subsurface Radius"), (mean, mean, mean))
node.inputs[build.PROFILE_COLOUR].default_value = 0.5
check("profile colour default 0.5", bsdf_input(node, "Subsurface Radius"), tuple(0.5 * (r + mean) for r in rose["radius"]))
check("profile colour default value", node.inputs[build.PROFILE_COLOUR].default_value, 0.5)

flat = {"radius": [0.6, 0.6, 0.6], "scale": 0.003}
mat, node = built("MSM_SubsurfaceProfile", opacity=1.0, profile=flat)
node.inputs[build.PROFILE_COLOUR].default_value = 0.0
check("a neutral profile doesn't change with profile colour", bsdf_input(node, "Subsurface Radius"), (0.6, 0.6, 0.6))

mat, node = built("MSM_DefaultLit", cosmetic=True)
check("a cosmetic the game doesn't scatter has the inputs", build.BASE_SUBSURFACE in node.inputs, True)
check("...and scatters nothing at base 0", bsdf_input(node, "Subsurface Weight"), 0.0)
node.inputs[build.BASE_SUBSURFACE].default_value = 0.5
check("...base alone drives it", bsdf_input(node, "Subsurface Weight"), 0.5)
check("...over skin's neutral distance", node.inputs[build.SCATTER_DISTANCE].default_value, build.SKIN_SCALE)

mat, node = built("MSM_DefaultLit", cosmetic=False)
check("a world material the game doesn't scatter gets nothing", build.BASE_SUBSURFACE in node.inputs, False)

mat, node = built("MSM_DefaultLit", cosmetic=True, shell=True)
check("fur: its own inputs", (build.SUBSURFACE_INTENSITY in node.inputs, build.BASE_SUBSURFACE in node.inputs,
                              build.PROFILE_COLOUR in node.inputs), (True, False, False))
check("fur scatters fully", bsdf_input(node, "Subsurface Weight"), 1.0)
node.inputs[build.SCATTER_DISTANCE].default_value = 0.0
check("distance 0 is accepted", bsdf_input(node, "Subsurface Scale"), 0.0)
```

- [ ] **Step 2: Run them to verify they fail**

Same commands as Task 1 Step 2. Expected: FAIL with `AttributeError: module ... has no attribute 'SKIN_SUBSURFACE_INPUT'`.

- [ ] **Step 3: Implement the constants and settings**

In `build.py`, replace the subsurface constants (today `KEY_SUBSURFACE` through `SKIN_SUBSURFACE`) with:

```python
KEY_SUBSURFACE = "mp_subsurface_scale"  # the game's scattering distance (metres), set on the group node input at build
SKIN_SUBSURFACE_INPUT = "Skin Subsurface"   # x the game's skin amount, where its shading model scatters
BASE_SUBSURFACE = "Base Subsurface"         # over the whole surface (cosmetics); the amount is max(base, skin)
SCATTER_DISTANCE = "Scatter Distance"       # metres
SUBSURFACE_RADIUS = "Subsurface Radius"     # per-colour distance (the profile's, skin's, shell fur's)
PROFILE_COLOUR = "Profile Colour"           # how much of the profile's per-colour spread the radius keeps
SUBSURFACE_INTENSITY = "Subsurface Intensity"   # shell fur's amount (everywhere on a layer)
FUR_RADIUS = (1.0, 0.8, 0.65)                   # default radius for fur
SKIN_SCALE = 0.003                              # metres: Fortnite skin's (SS_HeroSkin_02), for skin without a profile
SKIN_SUBSURFACE = "SkinSubsurfaceIntensity"     # a character's skin scattering parameter
```

In `settings()`, after `"shell": ...`, add:

```python
        # a cosmetic's material (hook.is_cosmetic): subsurface controls even where the game doesn't scatter
        "cosmetic": bool(entry.get("cosmetic")),
```

Bump `BUILD_REVISION` by one (66 -> 67).

- [ ] **Step 4: Implement the subsurface section of `assemble()`**

Replace the block from `# Amount and distance of light scattering under skin or fur ...` through `mat[KEY_SUBSURFACE] = scale` with:

```python
    # Amount and distance of light scattering under skin or fur sit on the material's own group node so an artist
    # can tune them (the import settings set them too). Skin scatters where the game's shading model does (Skin
    # Subsurface scales the game's amount, keeping its soft edges); Base Subsurface scatters a cosmetic's whole
    # surface (amount max(base, skin)); Profile Colour blends the profile's per-colour radius towards its mean.
    # A shell fur layer scatters everywhere by its own amount. These nodes go in the Output frame with the surface.
    with tr.at("Output"):
        weight = bsdf.inputs["Subsurface Weight"]
        scatters = weight.is_linked or weight.default_value > 0.0
        shell, cosmetic = s.get("shell"), s.get("cosmetic")
        if tr.tree != mat.node_tree and (scatters or shell or cosmetic):
            if not scatters and not shell:
                # a cosmetic the game doesn't scatter: Base Subsurface alone, over skin's neutral radius and distance
                bsdf.inputs["Subsurface Radius"].default_value = SKIN_RADIUS
                bsdf.inputs["Subsurface Scale"].default_value = SKIN_SCALE
            scale = bsdf.inputs["Subsurface Scale"].default_value

            def new_input(name, default, description, most=1.0, kind='NodeSocketFloat'):
                sock = tr.tree.interface.new_socket(name, in_out='INPUT', socket_type=kind)
                sock.default_value, sock.min_value, sock.description = default, 0.0, description
                if kind == 'NodeSocketFloat':
                    sock.max_value = most
                gi = tr.node("NodeGroupInput", name.lower())
                return next(o for o in gi.outputs if o.identifier == sock.identifier)

            def node(op, a, b, label):
                n = tr.node("ShaderNodeMath", label, operation=op)
                tr.L.new(a, n.inputs[0])
                tr.L.new(b, n.inputs[1])
                return n.outputs[0]

            game = weight.links[0].from_socket if weight.is_linked else None
            if shell:
                tr.L.new(new_input(SUBSURFACE_INTENSITY, 1.0, "How much light scatters under the fur"), weight)
            else:
                skin = new_input(SKIN_SUBSURFACE_INPUT, 1.0, "How much light scatters where the game's skin scatters (x its amount)")
                amount = None
                if game is not None:
                    # the game's amount relative to its skin's (SkinSubsurfaceIntensity, e.g. Helsie's face 0.4,
                    # lips 0): 1 on skin, the game's ratio elsewhere (keeps soft edges); without that parameter,
                    # the game's amount itself
                    param = next((it for it in tr.tree.interface.items_tree if it.item_type == 'SOCKET'
                                  and it.in_out == 'INPUT' and it.name == SKIN_SUBSURFACE), None)
                    ratio = tr.node("ShaderNodeMath", "game's scattering over its skin's", operation='DIVIDE', use_clamp=True)
                    tr.L.new(game, ratio.inputs[0])
                    if param is not None:
                        gi = tr.node("NodeGroupInput", "skin subsurface intensity")
                        least = tr.node("ShaderNodeMath", "skin's scattering (not 0)", operation='MAXIMUM')
                        tr.L.new(next(o for o in gi.outputs if o.identifier == param.identifier), least.inputs[0])
                        least.inputs[1].default_value = 1e-4
                        tr.L.new(least.outputs[0], ratio.inputs[1])
                    else:
                        ratio.inputs[1].default_value = 1.0
                    amount = node('MULTIPLY', ratio.outputs[0], skin, "skin subsurface")
                elif scatters:
                    amount = skin      # a constant amount: no ratio, the flat Skin Subsurface
                if cosmetic:
                    base = new_input(BASE_SUBSURFACE, 0.0, "How much light scatters over the whole surface, clothes included")
                    amount = node('MAXIMUM', base, amount, "base or skin subsurface") if amount is not None else base
                if amount is not None:
                    tr.L.new(amount, weight)
            tr.L.new(new_input(SCATTER_DISTANCE, scale, "How far light scatters under the surface (metres)", most=1e4),
                     bsdf.inputs["Subsurface Scale"])
            if shell:
                # the game's fur radius (Post FX SubsurfaceColor) is about black: a near-neutral one, red furthest
                tr.L.new(new_input(SUBSURFACE_RADIUS, FUR_RADIUS, "How far each colour scatters, x the distance",
                                   kind='NodeSocketVector'), bsdf.inputs["Subsurface Radius"])
            else:
                # skin radius (the profile's, else SKIN_RADIUS), blended towards its mean by Profile Colour;
                # a radius the graph makes itself (Subsurface's SubsurfaceColor) stays the graph's
                radius = bsdf.inputs["Subsurface Radius"]
                target = radius if not radius.is_linked else None
                if radius.is_linked and radius.links[0].from_node.bl_idname == "ShaderNodeMix" \
                        and radius.links[0].from_node.label == "subsurface radius" \
                        and not radius.links[0].from_node.inputs[5].is_linked:
                    target = radius.links[0].from_node.inputs[5]
                if target is not None:
                    profile = new_input(SUBSURFACE_RADIUS, tuple(target.default_value)[:3],
                                        "How far each colour scatters under skin, x the distance", kind='NodeSocketVector')
                    colour = new_input(PROFILE_COLOUR, 0.5, "How much of the profile's colour the scattering keeps (0: neutral)")
                    parts = tr.node("ShaderNodeSeparateXYZ", "profile radius")
                    tr.L.new(profile, parts.inputs[0])
                    total = tr.node("ShaderNodeMath", "radius sum", operation='ADD')
                    tr.L.new(parts.outputs[0], total.inputs[0])
                    tr.L.new(parts.outputs[1], total.inputs[1])
                    total3 = tr.node("ShaderNodeMath", "radius sum", operation='ADD')
                    tr.L.new(total.outputs[0], total3.inputs[0])
                    tr.L.new(parts.outputs[2], total3.inputs[1])
                    avg = tr.node("ShaderNodeMath", "radius mean", operation='DIVIDE')
                    tr.L.new(total3.outputs[0], avg.inputs[0])
                    avg.inputs[1].default_value = 3.0
                    neutral = tr.node("ShaderNodeCombineXYZ", "neutral radius")
                    for i in range(3):
                        tr.L.new(avg.outputs[0], neutral.inputs[i])
                    blend = tr.node("ShaderNodeMix", "profile colour", data_type='VECTOR')
                    tr.L.new(colour, blend.inputs[0])
                    tr.L.new(neutral.outputs[0], blend.inputs[4])
                    tr.L.new(profile, blend.inputs[5])
                    tr.L.new(blend.outputs[1], target)
            mat[KEY_SUBSURFACE] = scale
```

Update `_subsurface_panel()`'s names tuple to the new inputs, in order:

```python
    names = (SKIN_SUBSURFACE_INPUT, SUBSURFACE_INTENSITY, BASE_SUBSURFACE, SCATTER_DISTANCE, SUBSURFACE_RADIUS, PROFILE_COLOUR)
```

and its docstring's mention of "the subsurface inputs" stays.

Check nothing else uses the old `SUBSURFACE_SCALE` name:

```bash
grep -rn "SUBSURFACE_SCALE" plugins ../fpfork-private/plugin
```

Expected: no matches after this task.

- [ ] **Step 5: Set the values on the built material (hook)**

In `hook.py`, replace the value-setting block (today `if build.KEY_SUBSURFACE in mat:` ...) with:

```python
    if build.KEY_SUBSURFACE in mat:
        # the root group node is the only one in its tree with those inputs
        values = {build.SKIN_SUBSURFACE_INPUT: sss.skin, build.SUBSURFACE_INTENSITY: sss.skin,
                  build.BASE_SUBSURFACE: sss.base, build.PROFILE_COLOUR: sss.colour,
                  build.SCATTER_DISTANCE: mat[build.KEY_SUBSURFACE] * sss.scale}
        for n in mat.node_tree.nodes:
            if n.bl_idname == "ShaderNodeGroup" and build.SCATTER_DISTANCE in n.inputs:
                for name, v in values.items():
                    if name in n.inputs:
                        n.inputs[name].default_value = v
```

- [ ] **Step 6: Run the tests to verify they pass**

Copy the plugin again, then run `subsurface_check.py` and `translator_test.py` (same command shape as Task 1 Step 2). Expected: `[subsurface_check] 33 passed, 0 failed` and `[translator_test] 263 passed, 0 failed`.

- [ ] **Step 7: Commit**

Propose `Build the subsurface controls into the material` to the user; after their OK:

```bash
git add plugins/Blender/fortnite_porting/material_porter/build.py plugins/Blender/fortnite_porting/material_porter/hook.py tests/plugin/subsurface_check.py
git commit -m "Build the subsurface controls into the material"
```

---

### Task 3: App settings

**Files:**
- Modify: `src/FortnitePorting/ViewModels/Settings/BlenderSettingsViewModel.cs:41-45`
- Modify: `src/FortnitePorting/Views/MaterialPorter/ExactMaterialSettings.axaml`

**Interfaces:**
- Produces: export options `BaseSubsurface` (float, 0) and `ProfileColour` (float, 0.5), read by `hook.subsurface()` (Task 1).

- [ ] **Step 1: Add the options**

In `BlenderSettingsViewModel.cs`, replace the subsurface lines with:

```csharp
    // MP: exact materials' subsurface: skin amount (x the game's), base amount over a whole cosmetic, distance (x the
    // game's), how much of the profile's colour the scattering keeps; shell fur has its own amount and distance
    [ObservableProperty] private float _subsurfaceIntensity = 1.0f;
    [ObservableProperty] private float _baseSubsurface = 0.0f;
    [ObservableProperty] private float _subsurfaceScale = 1.0f;
    [ObservableProperty] private float _profileColour = 0.5f;
    [ObservableProperty] private float _furSubsurfaceIntensity = 1.0f;
    [ObservableProperty] private float _furSubsurfaceScale = 1.0f;
```

- [ ] **Step 2: The settings page**

In `ExactMaterialSettings.axaml`, change the "Subsurface Intensity" item's Title to `Skin Subsurface` and its Description to `How much light scatters under skin, where the game's material scatters (x its amount).`; change "Subsurface Scale" to Title `Scatter Distance`, Description `How far light scatters, x the game's distance (1: the game's).` (slider stays 0-10). After the Skin Subsurface item add:

```xml
        <controls:SettingsItem Title="Base Subsurface"
                               Description="Light scattering over the whole of a cosmetic, clothes included, for a softer look. 0: only where the game scatters."
                               IsEnabled="{Binding ExportMaterials}">
            <controls:SettingsItem.Footer>
                <StackPanel Orientation="Horizontal">
                    <Slider Value="{Binding BaseSubsurface}" MinWidth="200"
                            Minimum="0" Maximum="1"
                            TickFrequency="0.05" TickPlacement="BottomRight"
                            IsSnapToTickEnabled="True"/>
                    <TextBlock Text="{Binding BaseSubsurface, StringFormat=N2}"
                               VerticalAlignment="Center" Margin="{ext:Space 1, 0, 0, 0}"/>
                </StackPanel>
            </controls:SettingsItem.Footer>
        </controls:SettingsItem>
```

and after Scatter Distance:

```xml
        <controls:SettingsItem Title="Profile Colour"
                               Description="How much of the skin profile's colour the scattering keeps. 1: the game's; lower softens coloured profiles that darken and saturate skin."
                               IsEnabled="{Binding ExportMaterials}">
            <controls:SettingsItem.Footer>
                <StackPanel Orientation="Horizontal">
                    <Slider Value="{Binding ProfileColour}" MinWidth="200"
                            Minimum="0" Maximum="1"
                            TickFrequency="0.05" TickPlacement="BottomRight"
                            IsSnapToTickEnabled="True"/>
                    <TextBlock Text="{Binding ProfileColour, StringFormat=N2}"
                               VerticalAlignment="Center" Margin="{ext:Space 1, 0, 0, 0}"/>
                </StackPanel>
            </controls:SettingsItem.Footer>
        </controls:SettingsItem>
```

- [ ] **Step 3: Build and check the real page**

```bash
cd C:/Users/kyooc/Documents/Claude/fpfork-private/devtools && bash testapp.sh test
```

Then take the settings page with the Debug route (`fork-settings-shot`, the Blender settings page) to a PNG in the scratchpad and look at it: the six subsurface sliders in order Skin Subsurface, Base Subsurface, Scatter Distance, Profile Colour, Fur Subsurface Intensity, Fur Subsurface Scale, with their defaults (1, 0, 1, 0.5, 1, 1). Check the options reach the plugin: export an outfit through `fork-export-asset` and confirm `MetaData.Settings` has `BaseSubsurface: 0.0` and `ProfileColour: 0.5`.

- [ ] **Step 4: Commit**

Propose `Add base subsurface and profile colour settings`; after the user's OK:

```bash
git add src/FortnitePorting/ViewModels/Settings/BlenderSettingsViewModel.cs src/FortnitePorting/Views/MaterialPorter/ExactMaterialSettings.axaml
git commit -m "Add base subsurface and profile colour settings"
```

---

### Task 4: Baseline and renders

**Files:**
- Modify: `fpfork-private/devtools/baseline.py` (the suites part: `for script in ("translator_test.py", "layout_check.py")`)

- [ ] **Step 1: Run subsurface_check with the suites**

Add `"subsurface_check.py"` to that tuple and parse its line like translator_test's (`\[subsurface_check\] (\d+) passed, (\d+) failed`) into the suites JSON as `subsurface_check: {passed, failed}`.

- [ ] **Step 2: Full compare**

```bash
cd C:/Users/kyooc/Documents/Claude && CUE4PARSE_SKIP_NATIVE=1 python fpfork-private/devtools/baseline.py compare --fork C:/Users/kyooc/Documents/Claude/fpfork --port 24322
```

Expected: differences only in the Blender signatures of cosmetics' materials (new subsurface nodes and inputs) and the new suite numbers; none in map-cell or map exports. Don't touch the test instance while it runs.

- [ ] **Step 3: Renders**

With the scratchpad debug scripts (`fxdbg/run.py` to import, `probe_sun.py`-style lighting), render under one sun: The Night Rose (`Character_RoseDepth_Seed`, LightRose) and Carolina (`Character_ElegantLilyCharm`, LightGold) at Profile Colour 1, 0.5 and 0; Nemia (`Character_PocketScrunchie`) at 0.5 vs 1 (should barely differ); Lexa (`CID_963_Athena_Commando_F_Lexa`) with Base 0.5; one outfit with Base 0.3. Look at each: coloured profiles less dark and saturated at 0.5, Lexa softly scattering, clothes scattering at Base 0.3.

- [ ] **Step 4: Commit (private repo)**

Propose `Run the subsurface check with the suites`; after the user's OK, commit `devtools/baseline.py` in `fpfork-private` with the kyoocreatives noreply identity.
