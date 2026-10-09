# Official Body Rig Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A third Rig Type, "Fortnite Official Rig (IK/FK)", that builds UEFN's `FN_Mannequin_ControlRig` on an imported character with Blender constraints.

**Architecture:** CUE4Parse (fork patch) exposes the rig's control settings; a devtool decodes the rig once into `processing/context/official_rig.json` (controls, spaces, solving, gizmo shapes); `processing/context/official_rig.py` builds controls, constraints, switches, spaces, a spline spine, Rig on/off and Bake to Controls from that file; the fork's after-import hook calls it when the new Rig Type is chosen.

**Tech Stack:** C# (.NET 10, CUE4Parse + `patches/CUE4Parse/` overlay), Blender 5.2 Python plugin, headless isolated Blender tests, fpdev devtools.

**Spec:** `docs/superpowers/specs/2026-10-09-official-body-rig-design.md`

## Global Constraints

- Upstream FP files get only one-line hooks marked `// MP` / `# MP`; fork code in `processing/context/official_rig.py`, `material_porter/`, `*.MaterialPorter.cs`.
- CUE4Parse changes are whole-file copies under `patches/CUE4Parse/` (copied over `external/CUE4Parse` for the build); never stage `external/CUE4Parse`.
- Every constraint the rig adds is named with an `OR ` prefix; original bones keep their rest data.
- Epic's names, shapes and colours (not the kit's palette); the kit's collections `Controls`, `Mechanics`, `Game Bones`.
- Widths: root controls 3.5, limbs 2.5, fingers and toes 1.5.
- Humanoid master skeleton only for this rig; creatures, sidekicks, sprites, vehicles, LEGO keep their own rigs.
- A rig failure is logged and leaves the plain skeleton; it never fails the import.
- Headless Blender only, isolated `BLENDER_USER_CONFIG/SCRIPTS/EXTENSIONS`; never the user's Blender or the app on 24320; test instance 24322.
- Plugin test command (from `fpfork`, `$SB` a scratch folder, `TEST` the file):
  `python -c "import sys; sys.path.insert(0, r'C:\Users\kyooc\Documents\Claude\fpfork-private\devtools'); import baseline; baseline.copy_plugin(r'C:\Users\kyooc\Documents\Claude\fpfork')"` then
  `BLENDER_USER_CONFIG=$SB/config BLENDER_USER_SCRIPTS=$SB/scripts BLENDER_USER_EXTENSIONS=$SB/ext "C:/Program Files (x86)/Steam/steamapps/common/Blender/blender.exe" -b --factory-startup --python-exit-code 1 -P tests/plugin/TEST -- "C:/Users/kyooc/Documents/Claude/fpfork-private/devtools/out/baseline-plugin"`.
- App build: `cd fpfork-private/devtools && bash testapp.sh test` (without `MSYS_NO_PATHCONV`); fpdev with `MSYS_NO_PATHCONV=1`.
- Never print secrets; filter logs with `grep -viE "aes|key"`. Commits: short plain messages approved by the user; kyoocreatives noreply identity; no Co-Authored-By.

## Review Focus

1. A character whose skeleton lacks some of the rig's bones (big or small body types, extra or missing finger/twist bones): controls for missing bones are skipped, the rest builds, nothing raises.
2. Re-importing onto an armature that already has the official rig (or importing a second outfit part into it): no duplicate controls or doubled constraints.
3. An emote imported, then Bake to Controls, then a second emote imported: the rig switches off again and the first bake's control keys don't fight the bones.
4. FK/IK switch while posed: switching keeps the limb where it is (the inactive chain is matched first), as an animator expects.
5. Rig Type Official on a creature, sidekick, vehicle or LEGO import: those get their own rigs exactly as with Tasty.

---

### Task 1: Control settings readable

**Files:**
- Create: `patches/CUE4Parse/CUE4Parse/UE4/Objects/ControlRig/RigHierarchyElements.cs` (copy of the submodule file, changed)

**Interfaces:**
- Produces: `fork-dump?full=1` of `FN_Mannequin_ControlRig` shows, per control element (`LoadedKey.Type == 4`), `Settings` (`ControlType`, `ShapeName`, `ShapeColor`, `LimitEnabled`, `MinimumValue`, `MaximumValue`, `DisplayName`), `Offset`, `Shape`.

- [ ] **Step 1: The test (fails today)**

```bash
cd /c/Users/kyooc/Documents/Claude/fpfork-private/devtools && export MSYS_NO_PATHCONV=1
python fpdev.py dump FortniteGame/VKTemplates/Basic/VKT_Animation101/Plugins/VKT_Animation101/Content/Mannequin/Meshes/FN_Mannequin_ControlRig --out C:/Users/kyooc/AppData/Local/Temp/fnrig.json
python -c "import json; d=json.load(open(r'C:/Users/kyooc/AppData/Local/Temp/fnrig.json')); els=[e for e in d if e.get('Type')=='RigHierarchy'][0]['Elements']; c=[e for e in els if e['LoadedKey']['Type']==4]; print(len(c), sum('Settings' in e for e in c))"
```
Expected: `129 0`.

- [ ] **Step 2: Make the fields public in the patch copy**

Copy `external/CUE4Parse/CUE4Parse/UE4/Objects/ControlRig/RigHierarchyElements.cs` to the patch path and change, in `FRigControlElement`:
```csharp
    public FRigControlSettings Settings;     // MP: dumps (the fork reads Epic's rigs' controls)
    public FRigCurrentAndInitialTransform Offset;     // MP
    public FRigCurrentAndInitialTransform Shape;     // MP
```
Copy the patch over the submodule: `cp -r patches/CUE4Parse/. external/CUE4Parse/`.

- [ ] **Step 3: Element transforms**

Read `FRigCurrentAndInitialTransform` and the hierarchy's `Load` (`URigHierarchy` / `RigHierarchyElements.cs`). UE 5.4+ stores element transforms in hierarchy-level pools (`FRigReusableElementStorage<FTransform>`) that elements index into. If the pools are read but not resolved into elements, resolve them in the patch so `PoseStorage.Initial.Global` holds the transform; if they are not read at all and the read is more than one class of change, stop here and record the ruling "placement from the construction graph and the skeleton" (Task 2 then places by bone; cost: poles' exact offsets come from the graph instead).

- [ ] **Step 4: Build, re-run the test**

```bash
bash testapp.sh test
python fpdev.py dump <same> --out <same>
python -c "<same check>"
```
Expected: `129 129`. Also: `python fpdev.py dump /Game/Characters/Player/Common/Fortnite_Base_Head/Facials/ControlRigs/Fortnite_Flipbook2_Face_Mapping_CtrlRig --out ...` still reads (no new "Could not read" lines in the log).

- [ ] **Step 5: Commit**

```bash
cd /c/Users/kyooc/Documents/Claude/fpfork && git add patches/CUE4Parse/CUE4Parse/UE4/Objects/ControlRig/RigHierarchyElements.cs
git commit -m "Read Control Rig control settings"
```

### Task 2: Decode the rig into official_rig.json

**Files:**
- Create: `fpfork-private/devtools/official_rig_decode.py`
- Create: `fpfork-private/devtools/scripts/gizmo_shapes.py` (fpdev script: the gizmo meshes' wire as vertices and edges)
- Create: `plugins/Blender/fortnite_porting/processing/context/official_rig.json`

**Interfaces:**
- Consumes: Task 1's dump.
- Produces `official_rig.json` (lengths in metres, Blender axes: UE (x, y, z) cm → Blender (x, -y, z) × 0.01, as FP's importer does):

```json
{
  "source": "FortniteGame/VKTemplates/Basic/VKT_Animation101/Plugins/VKT_Animation101/Content/Mannequin/Meshes/FN_Mannequin_ControlRig",
  "skeleton": [{"name": "root", "parent": null}, {"name": "pelvis", "parent": "root"}],
  "spaces": [{"name": "leg_l_controls_space", "parent": "body_ctrl", "bone": "thigh_l"}],
  "controls": [{"name": "foot_l_ik_ctrl", "type": "transform", "parent": "leg_l_controls_space", "spaces": [],
                "shape": "Box_Thick", "color": [0.0, 0.02, 0.41], "size": 1.0, "limits": null,
                "bone": "foot_l", "offset": null, "default": null, "role": "limb"}],
  "solve": {
    "fk": [["upperarm_l_fk_ctrl", "upperarm_l"]],
    "ik": [{"bones": ["upperarm_l", "lowerarm_l", "hand_l"], "effector": "hand_l_ik_ctrl", "pole": "arm_l_pv_ik_ctrl",
            "switch": "arm_l_fk_ik_switch", "fk": ["upperarm_l_fk_ctrl", "lowerarm_l_fk_ctrl", "hand_l_fk_ctrl"]}],
    "spine": {"controls": ["spine_01_ctrl", "spine_02_ctrl", "spine_03_ctrl"], "bones": ["spine_01", "spine_02", "spine_03", "spine_04", "spine_05"]},
    "copy": [["hips_ctrl", "pelvis"]]
  },
  "shapes": {"Box_Thick": {"verts": [[0.5, 0.5, 0.5]], "edges": [[0, 1]]}}
}
```
`type`: `transform` | `transform_no_scale` | `bool` | `float`. `role`: `root` | `limb` | `digit` (width 3.5 / 2.5 / 1.5). `offset`: `[x, y, z]` in the `bone`'s space (poles) or null. `limits`: `{"location": [[min, max] | null ×3], "rotation": [...]}` or null. `copy`: other control → bone pairs the graph sets (hips, head, neck, clavicles, fingers, toes when not in `fk`).

- [ ] **Step 1: The data check (fails: no file yet)**

`official_rig_decode.py check` loads the JSON and the dump and asserts: controls' names/count/types equal the dump's control elements; every `bone` in `controls`/`spaces`/`solve` is in `skeleton`; every `parent` is a control, a space or null; every `shape` is in `shapes`; every `fk`/`ik`/`copy` control exists; 4 IK chains (arms, legs) each with a `switch`. Run: `python official_rig_decode.py check`. Expected: FileNotFoundError on the JSON.

- [ ] **Step 2: Decode**

`official_rig_decode.py build` from the Task 1 dump:
- `skeleton`: bone elements (`LoadedKey.Type == 1`) with `ParentKey.Name`.
- `spaces`, `controls`: null (2) and control (4) elements; `parent` from `ParentConstraints[0]` (resolve `ParentElement` by index into the element list, or the element's `Parent` key), `spaces` from the remaining parent constraints / `Settings` customization.
- `bone`, `offset`: from the construction graph: `RigVMUnitNode`s in the "Construction" graph (`ControlRigGraph` named so) whose struct is `SetTransform` with a control/space `Item` pin and a `Value` pin linked from a `GetTransform` of a bone (follow `RigVMLink`s by pin path); a computed offset (poles) is the chain of maths nodes on that link evaluated with the pins' default values. Where no construction node places a control, fall back to Epic's naming (`<bone>_fk_ctrl`, `<bone>_ctrl`, `foot_l_ik_ctrl` → `foot_l`); list such controls in the decode's printed report.
- `solve`: from the Forward graph's units: `TwoBoneIKSimplePerItem` (its `ItemA/B/EffectorItem` pins → `bones`, its effector and pole controls by link), the switch bools read by `GetControlBool` feeding the branch that chooses FK or IK, `ControlRigSplineFromPoints` inputs (spine controls) and `PositionFromControlRigSpline`/`SetTransform` targets (spine bones), and `SetTransform` of bones from controls (`fk`, `copy`).
- `shapes`: run `scripts/gizmo_shapes.py` (below) and merge.
- `color`: `Settings.ShapeColor` RGB; `role` by name (`global|root|body|hips` → root, fingers/toes → digit, else limb); `size` from `Shape.Initial.Local` scale (1.0 when absent).

`scripts/gizmo_shapes.py`: `fpdev.py export gizmos --type Mesh --path Engine/Plugins/Animation/ControlRig/Content/Controls/<mesh>` for each mesh the `DefaultGizmoLibrary` asset lists (its `Shapes[].StaticMesh`), `fpdev.py import gizmos`, then the script prints each mesh's boundary/feature edges (edges with one face, or all edges for line meshes) as JSON in Blender units, normalized to unit size like the library.

- [ ] **Step 3: Run the check**

`python official_rig_decode.py build && python official_rig_decode.py check`. Expected: `ok: 129 controls, 26 spaces, 346 bones, 4 IK chains, N shapes` and the fallback list (ledger it).

- [ ] **Step 4: Commit**

```bash
cd /c/Users/kyooc/Documents/Claude/fpfork && git add plugins/Blender/fortnite_porting/processing/context/official_rig.json && git commit -m "Add the official rig's decoded layout"
cd ../fpfork-private && git add devtools/official_rig_decode.py devtools/scripts/gizmo_shapes.py && git -c user.name=kyoocreatives -c user.email=280023218+kyoocreatives@users.noreply.github.com commit -m "Add the official rig decoder"
```

### Task 3: Rig Type and the controls

**Files:**
- Modify: `src/FortnitePorting/ViewModels/Settings/BlenderSettingsViewModel.cs` (ERigType, one MP line)
- Modify: `plugins/Blender/fortnite_porting/processing/enums.py` (ERigType, one MP line)
- Modify: `plugins/Blender/fortnite_porting/processing/context/mesh_context.py:23` (one MP line: merge armatures)
- Create: `plugins/Blender/fortnite_porting/processing/context/official_rig.py`
- Modify: `plugins/Blender/fortnite_porting/material_porter/mesh_hooks.py` (`after_parts`)
- Create: `tests/plugin/official_rig_check.py`

**Interfaces:**
- Consumes: `official_rig.json` (Task 2); `rig_style.collections(armature)`, `rig_style.assign(armature, bone, group)`, `rig_shapes.color(pose_bone, rgb)`, `face_board.add(obj, head=..., size=...)`.
- Produces: `official_rig.load() -> dict`; `official_rig.fits(obj) -> bool`; `official_rig.create(obj, data=None) -> str` (summary); armature data marks `fpmp_official_rig` (True) and `fpmp_official_rig_on` (True); pose bones named as `controls[].name` and `spaces[].name`; shape objects `OR_<shape>`.

- [ ] **Step 1: The failing test**

`tests/plugin/official_rig_check.py` (shape as `rig_check.py`):
```python
"""Checks the official Fortnite rig (FN_Mannequin_ControlRig rebuilt with constraints) on a synthetic mannequin.

    blender -b --factory-startup --python-exit-code 1 -P tests/plugin/official_rig_check.py -- <plugin parent>"""
import sys

import bpy
from mathutils import Vector

sys.path.insert(0, sys.argv[sys.argv.index("--") + 1])
import fpmp_baseline  # noqa: E402,F401
from fpmp_baseline.processing.context import official_rig  # noqa: E402

FAILS, PASSES = [], [0]


def check(name, got, want, tol=1e-3):
    ok = all(abs(g - w) <= tol for g, w in zip(got, want)) if isinstance(want, tuple) else \
        (abs(got - want) <= tol if isinstance(want, float) else got == want)
    if ok:
        PASSES[0] += 1
    else:
        FAILS.append(name)
        print("[official_rig_check] FAIL %s: got %r, want %r" % (name, got, want))


# where the main bones sit (metres, facing -Y, knees and elbows slightly bent so IK has a plane)
PLACES = {"root": (0, 0, 0), "pelvis": (0, 0, 1.0), "spine_01": (0, 0, 1.08), "spine_02": (0, 0, 1.16), "spine_03": (0, 0, 1.24),
          "spine_04": (0, 0, 1.32), "spine_05": (0, 0, 1.40), "neck_01": (0, 0, 1.50), "neck_02": (0, 0, 1.55), "head": (0, 0, 1.62),
          "thigh_l": (0.1, 0, 0.95), "calf_l": (0.1, -0.03, 0.52), "foot_l": (0.1, 0, 0.1), "ball_l": (0.1, -0.12, 0.02),
          "clavicle_l": (0.04, 0, 1.42), "upperarm_l": (0.18, 0, 1.42), "lowerarm_l": (0.46, 0.03, 1.42), "hand_l": (0.72, 0, 1.42)}


def place(name):
    if name in PLACES:
        return Vector(PLACES[name])
    mirror = name[:-2] + "_l" if name.endswith("_r") else None
    if mirror in PLACES:
        x, y, z = PLACES[mirror]
        return Vector((-x, y, z))
    return None


def mannequin(name="mannequin", drop=()):
    """An armature with the data's skeleton; bones without a place sit 5 cm past their parent. `drop`: bones left out."""
    data = official_rig.load()
    obj = bpy.data.objects.new(name, bpy.data.armatures.new(name))
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')
    edit = obj.data.edit_bones
    for b in data["skeleton"]:
        if b["name"] in drop or (b["parent"] and b["parent"] not in edit):
            continue
        bone = edit.new(b["name"])
        parent = edit.get(b["parent"]) if b["parent"] else None
        bone.head = place(b["name"]) or ((parent.tail if parent else Vector((0, 0, 0))) + Vector((0, 0, 0.05)))
        bone.tail = bone.head + Vector((0, 0, 0.05))
        bone.parent = parent
    for b in edit:      # chains point at their child (IK needs it)
        kids = [k for k in b.children if place(k.name) is not None]
        if kids and (kids[0].head - b.head).length > 1e-4:
            b.tail = kids[0].head
    bpy.ops.object.mode_set(mode='OBJECT')
    return obj


data = official_rig.load()
man = mannequin()
check("fits a mannequin", official_rig.fits(man), True)
summary = official_rig.create(man)
names = {c["name"] for c in data["controls"]}
check("every control built", names <= set(man.pose.bones.keys()), True)
check("spaces built", {s["name"] for s in data["spaces"]} <= set(man.pose.bones.keys()), True)
fc = next(c for c in data["controls"] if c["name"] == "foot_l_ik_ctrl")
pb = man.pose.bones["foot_l_ik_ctrl"]
check("Epic's shape", pb.custom_shape.name, "OR_" + fc["shape"])
check("Epic's colour", tuple(pb.color.custom.normal), tuple(fc["color"]), tol=0.01)
check("on its bone", tuple(man.data.bones["foot_l_ik_ctrl"].head_local), tuple(man.data.bones["foot_l"].head_local))
check("parent as the data", man.data.bones["foot_l_ik_ctrl"].parent.name, fc["parent"])
check("root width", man.pose.bones["global_ctrl"].custom_shape_wire_width, 3.5)
check("controls collection", "foot_l_ik_ctrl" in man.data.collections["Controls"].bones, True)
check("game bones hidden", man.data.collections["Game Bones"].is_visible, False)
check("marked", (man.data.get("fpmp_official_rig"), man.data.get("fpmp_official_rig_on")), (True, True))
check("a second build adds nothing", (official_rig.create(man), len(man.pose.bones)), ("already built", len(man.pose.bones)))
# a skeleton missing some bones: their controls are skipped, the rest builds
small = mannequin("small", drop=("ball_l", "ball_r"))
official_rig.create(small)
check("missing bones skip their controls", "foot_l_ik_ctrl" in small.pose.bones, True)
# not a humanoid
blob = bpy.data.objects.new("blob", bpy.data.armatures.new("blob"))
check("a non-humanoid doesn't fit", official_rig.fits(blob), False)

print("[official_rig_check] %d passed, %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
```
(A control whose `bone` is dropped is skipped with its children; "missing bones skip their controls" asserts the rest still builds — adjust the dropped bones to ones the data's toe controls use, per the JSON; ledger.)

- [ ] **Step 2: Run, watch it fail**

Expected: `ImportError: cannot import name 'official_rig'`.

- [ ] **Step 3: Implement the enum lines, the hook and the controls**

`BlenderSettingsViewModel.cs`, after `Tasty`:
```csharp
    Tasty,
    [Description("Fortnite Official Rig (IK/FK)")] Official     // MP
```
(add the comma after `Tasty`). `enums.py`:
```python
    TASTY = auto()
    OFFICIAL = auto()     # MP: UEFN's own mannequin rig (processing/context/official_rig.py)
```
`mesh_context.py`, after the Tasty options block:
```python
        if rig_type == ERigType.OFFICIAL: self.options["MergeArmatures"] = True     # MP: one skeleton for the official rig
```
`official_rig.py`:
```python
"""UEFN's own mannequin Control Rig (FN_Mannequin_ControlRig) rebuilt with Blender constraints.

Its controls, spaces, shapes, colours and what drives what were decoded once from the game files into
official_rig.json (spec 2026-10-09-official-body-rig); this builds them on an FP character's skeleton."""
import json
import os

import bpy
from mathutils import Matrix, Vector

from . import rig_shapes, rig_style

DATA = os.path.join(os.path.dirname(__file__), "official_rig.json")
MARK, ON = "fpmp_official_rig", "fpmp_official_rig_on"
PREFIX = "OR "
WIDTHS = {"root": 3.5, "limb": 2.5, "digit": 1.5}
CORE = ("pelvis", "spine_01", "thigh_l", "upperarm_l", "head")
_cache = {}


def load():
    if "data" not in _cache:
        with open(DATA, encoding="utf-8") as f:
            _cache["data"] = json.load(f)
    return _cache["data"]


def fits(obj):
    return obj is not None and obj.type == 'ARMATURE' and all(n in obj.data.bones for n in CORE)


def _shape(name, data):
    obj = bpy.data.objects.get("OR_" + name)
    if obj is None:
        mesh = bpy.data.meshes.new("OR_" + name)
        mesh.from_pydata(data["shapes"][name]["verts"], data["shapes"][name]["edges"], [])
        obj = bpy.data.objects.new("OR_" + name, mesh)
    return obj


def _buildable(data, bones):
    """Spaces and controls whose bone the skeleton has and whose parent is buildable, in hierarchy order."""
    ok, items = set(), [("space", s) for s in data["spaces"]] + [("control", c) for c in data["controls"]]
    out = []
    changed = True
    while changed:
        changed = False
        for kind, item in items:
            if item["name"] in ok or item["bone"] not in bones:
                continue
            if item["parent"] is None or item["parent"] in ok or item["parent"] in bones:
                ok.add(item["name"])
                out.append((kind, item))
                changed = True
    return out


def create(obj, data=None):
    """Build the rig on a fitting armature; returns a summary ("already built" on a second call)."""
    if obj.data.get(MARK):
        return "already built"
    data = data or load()
    items = _buildable(data, set(obj.data.bones.keys()))
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')
    edit = obj.data.edit_bones
    for kind, item in items:
        bone = edit.new(item["name"])
        at = edit[item["bone"]]
        bone.head, bone.tail, bone.roll = at.head.copy(), at.tail.copy(), at.roll
        if item.get("offset"):
            shift = at.matrix.to_3x3() @ Vector(item["offset"])
            bone.head, bone.tail = bone.head + shift, bone.tail + shift
        bone.use_deform = False
        if item["parent"]:
            bone.parent = edit[item["parent"]]
    bpy.ops.object.mode_set(mode='POSE')
    groups = rig_style.collections(obj.data)
    for b in obj.data.bones:
        if b.name not in {i["name"] for _, i in items}:
            rig_style.assign(obj.data, b.name, "Game Bones")
    for kind, item in items:
        pb = obj.pose.bones[item["name"]]
        if kind == "space":
            rig_style.assign(obj.data, item["name"], "Mechanics")
            continue
        rig_style.assign(obj.data, item["name"], "Controls")
        pb.custom_shape = _shape(item["shape"], data)
        pb.use_custom_shape_bone_size = False
        pb.custom_shape_scale_xyz = (item.get("size") or 1.0,) * 3
        rig_shapes.color(pb, tuple(item["color"]))
        pb.custom_shape_wire_width = WIDTHS[item["role"]]
        if item["type"] in ("bool", "float"):
            pb[item["name"]] = item.get("default") or (False if item["type"] == "bool" else 0.0)
    bpy.ops.object.mode_set(mode='OBJECT')
    obj.show_in_front = True
    obj.data[MARK] = True
    obj.data[ON] = True
    return "official rig: %d controls" % sum(1 for k, _ in items if k == "control")
```
(Task 4 adds the solving inside `create` before the marks.)

`mesh_hooks.after_parts`: add after the Tasty block:
```python
    if rig_type == ERigType.OFFICIAL:
        _official(ctx)
```
with (imports `ERigType` from `..processing.enums` beside `EExportType`):
```python
def _official(ctx):
    """UEFN's own mannequin rig on the humanoid master skeleton, and the face board when the face is a flipbook."""
    from ..processing.context import face_board, official_rig
    for skeleton in {m.get("Skeleton") for m in ctx.imported_meshes if _alive(m.get("Skeleton"))}:
        if not official_rig.fits(skeleton):
            continue
        try:
            Log.info(official_rig.create(skeleton))
            if "head" in skeleton.data.bones:
                face_board.add(skeleton, head="head")
        except Exception as e:
            Log.error("%s: official rig (%s: %s)" % (skeleton.name, type(e).__name__, e))
            if bpy.context.mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')
```
and the creature/vehicle/LEGO branch's condition reads `if rig_type not in (tasty, ERigType.OFFICIAL) or ctx.type not in [...]` (Review Focus 5).

- [ ] **Step 4: Run, watch it pass**

Expected: `[official_rig_check] N passed, 0 failed`. Then a hook check appended to the test: a fake ctx (`SimpleNamespace(imported_meshes=[{"Skeleton": man2, "Mesh": None}], type=EExportType.OUTFIT, mp_deferred_effects=None)`) through `mesh_hooks._official(ctx)` builds the rig; with `official_rig.create` patched to raise, `_official` logs and returns (no exception). Review Focus 5: copy `rig_check.py`'s `quadruped()` helper into the test and pass a WILDLIFE fake ctx with it through `mesh_hooks.after_parts(ctx, ERigType.OFFICIAL, ERigType.TASTY)` (with `shells.apply` and `settle_effects` tolerant of the fake: give it `imported_meshes` entries with `"Mesh": None` and `mp_deferred_effects = None`); check the quadruped gets `is_creature_rig`.

- [ ] **Step 5: Commit**

```bash
git add src/FortnitePorting/ViewModels/Settings/BlenderSettingsViewModel.cs plugins/Blender/fortnite_porting/processing/enums.py plugins/Blender/fortnite_porting/processing/context/mesh_context.py plugins/Blender/fortnite_porting/processing/context/official_rig.py plugins/Blender/fortnite_porting/material_porter/mesh_hooks.py tests/plugin/official_rig_check.py
git commit -m "Add the official rig's controls"
```

### Task 4: Solving — FK, IK, switches, spaces, spine, limits

**Files:**
- Modify: `plugins/Blender/fortnite_porting/processing/context/official_rig.py`
- Test: `tests/plugin/official_rig_check.py`

**Interfaces:**
- Consumes: Task 3's `create`, the JSON's `solve`.
- Produces: `official_rig.set_switch(obj, switch_name, ik: bool, match=True)`; `official_rig.set_space(obj, control_name, space_index)`; constraints named `OR FK`, `OR IK`, `OR IK rotation`, `OR copy`, `OR space <n>`, `OR spine`, `OR limit`; a mechanism chain `OR_MCH_spine_*` and curve object `OR_spine_curve`.

- [ ] **Step 1: Failing tests** (append to `official_rig_check.py` before the summary)

```python
def world(obj, bone):
    bpy.context.view_layer.update()
    return obj.matrix_world @ obj.pose.bones[bone].matrix


def head(obj, bone):
    return world(obj, bone).to_translation()


rig = mannequin("solve")
official_rig.create(rig)
pb = rig.pose.bones
# FK
official_rig.set_switch(rig, "arm_l_fk_ik_switch", ik=False)
pb["upperarm_l_fk_ctrl"].rotation_mode = 'XYZ'
pb["upperarm_l_fk_ctrl"].rotation_euler = (0.0, 0.0, 0.6)
check("FK control turns its bone", (world(rig, "upperarm_l").to_quaternion().rotation_difference(world(rig, "upperarm_l_fk_ctrl").to_quaternion()).angle < 1e-3), True)
pb["upperarm_l_fk_ctrl"].rotation_euler = (0.0, 0.0, 0.0)
# IK
official_rig.set_switch(rig, "leg_l_fk_ik_switch", ik=True)
start = head(rig, "foot_l").copy()
pb["foot_l_ik_ctrl"].location = (0.0, 0.0, 0.0)
eff = world(rig, "foot_l_ik_ctrl").to_translation()
pb["foot_l_ik_ctrl"].matrix = rig.matrix_world.inverted() @ Matrix.Translation(eff + Vector((0, -0.1, 0.15))) @ world(rig, "foot_l_ik_ctrl").to_quaternion().to_matrix().to_4x4()
check("IK foot reaches its control", tuple(head(rig, "foot_l")), tuple(eff + Vector((0, -0.1, 0.15))), tol=2e-3)
knee = head(rig, "calf_l")
check("knee bends toward the pole", (knee - head(rig, "thigh_l")).dot(head(rig, "leg_l_pv_ik_ctrl") - head(rig, "thigh_l")) > 0, True)
# switch: FK drives the thigh again, the IK controls hide, the limb stays put (matched)
thigh_before = world(rig, "thigh_l").copy()
official_rig.set_switch(rig, "leg_l_fk_ik_switch", ik=False)
check("switching keeps the limb", (world(rig, "thigh_l").to_translation() - thigh_before.to_translation()).length < 1e-3 and
      world(rig, "thigh_l").to_quaternion().rotation_difference(thigh_before.to_quaternion()).angle < 1e-2, True)
check("inactive IK controls hide", rig.data.bones["foot_l_ik_ctrl"].hide, True)
check("active FK controls show", rig.data.bones["thigh_l_fk_ctrl"].hide, False)
# spaces: a control with spaces keeps its world place when its space changes
spaced = next((c for c in official_rig.load()["controls"] if len(c["spaces"]) > 1 and c["name"] in pb), None)
if spaced:
    before = world(rig, spaced["name"]).copy()
    official_rig.set_space(rig, spaced["name"], 1)
    check("space change keeps the place", (world(rig, spaced["name"]).to_translation() - before.to_translation()).length < 1e-3, True)
# spine: moving the top spine control moves the top spine bone toward it
top = official_rig.load()["solve"]["spine"]["controls"][-1]
top_bone = official_rig.load()["solve"]["spine"]["bones"][-1]
before = head(rig, top_bone).copy()
pb[top].location += Vector((0.05, 0.0, 0.0))
check("spine follows its controls", (head(rig, top_bone) - before).length > 0.01, True)
# every constraint the rig adds is an OR one
check("constraints named OR", all(c.name.startswith(official_rig.PREFIX) for b in rig.pose.bones for c in b.constraints), True)
```

- [ ] **Step 2: Run, watch them fail**

Expected: `AttributeError: ... has no attribute 'set_switch'`.

- [ ] **Step 3: Implement**

In `official_rig.py`, a `_solve(obj, data, built)` called from `create` (in pose mode, before the marks), with `built` the set of built names:

```python
def _constraint(pose_bone, kind, name):
    c = pose_bone.constraints.new(kind)
    c.name = PREFIX + name
    return c


def _switch_driver(obj, constraint, switch, ik):
    """Influence = the switch (IK) or 1 - switch (FK)."""
    driver = constraint.driver_add("influence").driver
    driver.type = 'SCRIPTED'
    var = driver.variables.new()
    var.name, var.type = "ik", 'SINGLE_PROP'
    var.targets[0].id = obj
    var.targets[0].data_path = 'pose.bones["%s"]["%s"]' % (switch, switch)
    driver.expression = "ik" if ik else "1 - ik"


def _pole_angle(obj, base, pole):
    """IK pole angle that keeps the chain's rest pose (the usual formula: angle between the base bone's X axis and the
    pole direction, around the chain's axis)."""
    bones = obj.data.bones
    b = bones[base]
    chain_axis = (bones[b.children[0].name].tail_local - b.head_local).normalized() if b.children else b.y_axis
    to_pole = (bones[pole].head_local - b.head_local)
    to_pole = (to_pole - chain_axis * to_pole.dot(chain_axis)).normalized()
    x = b.matrix_local.to_3x3().col[0]
    angle = x.angle(to_pole)
    return angle if x.cross(to_pole).dot(chain_axis) > 0 else -angle


def _solve(obj, data, built):
    pb, solve = obj.pose.bones, data["solve"]
    for control, bone in solve["fk"] + solve["copy"]:
        if control in built and bone in pb:
            c = _constraint(pb[bone], 'COPY_TRANSFORMS', "FK" if [control, bone] in solve["fk"] else "copy")
            c.target, c.subtarget = obj, control
    for chain in solve["ik"]:
        root, mid, end = chain["bones"]
        if not all(n in built for n in (chain["effector"], chain["pole"], chain["switch"])):
            continue
        ik = _constraint(pb[mid], 'IK', "IK")
        ik.target, ik.subtarget = obj, chain["effector"]
        ik.pole_target, ik.pole_subtarget = obj, chain["pole"]
        ik.pole_angle = _pole_angle(obj, root, chain["pole"])
        ik.chain_count = 2
        rot = _constraint(pb[end], 'COPY_ROTATION', "IK rotation")
        rot.target, rot.subtarget = obj, chain["effector"]
        _switch_driver(obj, ik, chain["switch"], True)
        _switch_driver(obj, rot, chain["switch"], True)
        for bone in chain["bones"]:
            for c in pb[bone].constraints:
                if c.name == PREFIX + "FK":
                    _switch_driver(obj, c, chain["switch"], False)
    # spaces: one Child Of per space, the active one by the control's "space" property
    for item in data["controls"]:
        if item["name"] not in built or len(item["spaces"]) < 2:
            continue
        owner = pb[item["name"]]
        owner["space"] = 0
        for n, space in enumerate(item["spaces"]):
            if space not in built and space not in pb:
                continue
            c = _constraint(owner, 'CHILD_OF', "space %d" % n)
            c.target, c.subtarget = obj, space
            c.set_inverse_pending = True
            driver = c.driver_add("influence").driver
            driver.type = 'SCRIPTED'
            var = driver.variables.new()
            var.name, var.type = "s", 'SINGLE_PROP'
            var.targets[0].id = obj
            var.targets[0].data_path = 'pose.bones["%s"]["space"]' % item["name"]
            driver.expression = "1 if s == %d else 0" % n
    _spine(obj, solve["spine"], built)
    for item in data["controls"]:
        if item["name"] in built and item.get("limits"):
            _limits(pb[item["name"]], item["limits"])
```
`_spine(obj, spine, built)`: a poly curve `OR_spine_curve` with one point per spine control, each point hooked (Hook modifier, `object=obj`, `subtarget=control`) to its control; a mechanism chain `OR_MCH_<bone>` duplicating the spine bones (Mechanics collection) with Spline IK on the last (`chain_count = len(bones)`, `target = curve`, `y_scale_mode='FIT_CURVE'`, `xz_scale_mode='NONE'`); each spine bone gets `OR spine` Copy Rotation from its `OR_MCH_` twin, and the first gets Copy Location too. `_limits(pose_bone, limits)`: `OR limit` Limit Location / Limit Rotation (`owner_space='LOCAL'`, `use_min/max_*` per axis where the pair is not null, radians for rotation).

`set_switch(obj, switch, ik, match=True)`: when `match`, before flipping put the target chain's controls where the bones are now (FK controls ← their bones' pose matrices; effector ← end bone; pole ← `_pole_place(obj, chain)` = mid joint + (mid − midpoint(root, end)).normalized() × upper bone length); set `pose.bones[switch][switch] = 1.0 if ik else 0.0`; hide the other mode's controls (`bones[name].hide = True`; the FK controls of the chain when IK, the effector and pole when FK); `bpy.context.view_layer.update()`.

`set_space(obj, control, index)`: remember `pose_bone.matrix` (pose space), set `pose_bone["space"] = index`, update, assign the remembered matrix back, update.

The switch controls' property is the bool decoded as a float 0/1 (`pb[name] = 1.0`) with `id_properties_ui(name).update(min=0.0, max=1.0)`, so it can be keyed and driven.

- [ ] **Step 4: Run, watch them pass; run every plugin suite**

Expected: `official_rig_check` 0 failed; `rig_check`, `flipbook_face_check`, `translator_test`, `layout_check`, `subsurface_check` unchanged.

- [ ] **Step 5: Commit**

```bash
git add plugins/Blender/fortnite_porting/processing/context/official_rig.py tests/plugin/official_rig_check.py
git commit -m "Solve the official rig with constraints"
```

### Task 5: Rig on/off, the panel, emotes switch it off

**Files:**
- Modify: `plugins/Blender/fortnite_porting/processing/context/official_rig.py`
- Modify: `plugins/Blender/fortnite_porting/operator/rig_ui.py`
- Modify: `plugins/Blender/fortnite_porting/processing/context/anim_context.py` (one MP line)
- Modify: `plugins/Blender/fortnite_porting/processing/context/rig_style.py` (`controls()` knows the official rig)
- Test: `tests/plugin/official_rig_check.py`

**Interfaces:**
- Produces: `official_rig.set_on(obj, on: bool)`; `official_rig.ui(layout, obj)`; operators `fpmp.official_rig_on` (prop `on`), `fpmp.official_rig_switch` (props `switch`, `ik`), `fpmp.official_rig_space` (props `control`, `index`), `fpmp.official_rig_bake` (Task 6 fills it).

- [ ] **Step 1: Failing tests**

```python
off = mannequin("onoff")
official_rig.create(off)
official_rig.set_on(off, False)
check("rig off mutes every OR constraint", all(c.mute for b in off.pose.bones for c in b.constraints if c.name.startswith(official_rig.PREFIX)), True)
check("rig off is marked", off.data["fpmp_official_rig_on"], False)
official_rig.set_on(off, True)
check("rig on unmutes", any(c.mute for b in off.pose.bones for c in b.constraints if c.name.startswith(official_rig.PREFIX)), False)
from fpmp_baseline.processing.context import rig_style  # noqa: E402
check("Select Controls finds the official controls", "foot_l_ik_ctrl" in {p.name for p in rig_style.controls(off)}, True)
# an emote import onto it switches it off (the hook the importer calls)
official_rig.on_animation_import(off)
check("an emote switches the rig off", off.data["fpmp_official_rig_on"], False)
```

- [ ] **Step 2: Run, watch them fail** — Expected: `AttributeError: ... 'set_on'`.

- [ ] **Step 3: Implement**

```python
def set_on(obj, on):
    """The rig drives the bones (on) or the bones play their own keys (off)."""
    for pb in obj.pose.bones:
        for c in pb.constraints:
            if c.name.startswith(PREFIX):
                c.mute = not on
    obj.data[ON] = on


def on_animation_import(obj):
    """An imported animation keys the bones: the rig steps aside (Bake to Controls brings it back)."""
    if obj.data.get(MARK):
        set_on(obj, False)
```
`rig_style.controls`: when `obj.data.get("fpmp_official_rig")`, the shown `Controls` collection's bones (it already is a kit collection; make sure the official branch isn't shadowed by the Tasty check).
`anim_context.py`, at the start of `import_anim_data` after the Tasty settings lines:
```python
        from .official_rig import on_animation_import; on_animation_import(target_skeleton)     # MP: the official rig steps aside
```
`rig_ui.py`: a branch for `data.get("fpmp_official_rig")` drawing: group toggles (`Controls`, `Mechanics`, `Game Bones`, `Face Board`), Select Controls, Reset Selected / All (existing operators), Rig on/off (`fpmp.official_rig_on`), the four switches (a row each: label, FK / IK buttons calling `fpmp.official_rig_switch`), the active control's spaces (when it has `space`: one button per space calling `fpmp.official_rig_space`), Bake to Controls (`fpmp.official_rig_bake`), and `face_board.ui(layout, obj)`. Register the operators in `rig_ui.register`.

- [ ] **Step 4: Run, watch them pass; run every plugin suite.** Expected: 0 failed everywhere.

- [ ] **Step 5: Commit**

```bash
git add plugins/Blender/fortnite_porting/processing/context/official_rig.py plugins/Blender/fortnite_porting/operator/rig_ui.py plugins/Blender/fortnite_porting/processing/context/anim_context.py plugins/Blender/fortnite_porting/processing/context/rig_style.py tests/plugin/official_rig_check.py
git commit -m "Add the official rig's panel and switch"
```

### Task 6: Bake to Controls

**Files:**
- Modify: `plugins/Blender/fortnite_porting/processing/context/official_rig.py`
- Modify: `plugins/Blender/fortnite_porting/operator/rig_ui.py` (the operator's execute)
- Test: `tests/plugin/official_rig_check.py`

**Interfaces:**
- Produces: `official_rig.bake_to_controls(obj, start: int, end: int) -> int` (frames baked); after it the rig is on, every switch is FK (0.0), the bones' own action is kept in the armature's NLA, muted, in a track named `OR bones`.

- [ ] **Step 1: Failing tests**

```python
bk = mannequin("bake")
official_rig.create(bk)
official_rig.on_animation_import(bk)        # as an emote import does
pb = bk.pose.bones
for f, angle in ((1, 0.0), (5, 0.5)):
    for name in ("upperarm_l", "thigh_l", "spine_02"):
        pb[name].rotation_mode = 'XYZ'
        pb[name].rotation_euler = (angle, 0.0, angle * 0.5)
        pb[name].keyframe_insert("rotation_euler", frame=f)
bpy.context.scene.frame_set(5)
keyed = {n: world(bk, n).copy() for n in ("upperarm_l", "thigh_l", "spine_02", "hand_l", "foot_l")}
check("bake covers the range", official_rig.bake_to_controls(bk, 1, 5), 5)
check("rig on after the bake", bk.data["fpmp_official_rig_on"], True)
bpy.context.scene.frame_set(5)
for n, m in keyed.items():
    check("baked %s lands" % n, (world(bk, n).to_translation() - m.to_translation()).length < 2e-3 and
          world(bk, n).to_quaternion().rotation_difference(m.to_quaternion()).angle < 2e-2, True)
check("bones' own keys kept, muted", [t.name for t in bk.animation_data.nla_tracks if t.mute], ["OR bones"])
# a second emote after a bake: the rig steps aside again
official_rig.on_animation_import(bk)
check("a second emote switches it off again", bk.data["fpmp_official_rig_on"], False)
```

- [ ] **Step 2: Run, watch them fail** — Expected: `AttributeError: ... 'bake_to_controls'`.

- [ ] **Step 3: Implement**

```python
def bake_to_controls(obj, start, end):
    """Key every control from the animated bones over start..end (rig off while reading), then rig on with the
    bones' action muted in the NLA. Returns the frames baked."""
    data, scene = load(), bpy.context.scene
    built = {c["name"] for c in data["controls"] if c["name"] in obj.pose.bones}
    pb = obj.pose.bones
    targets = {}            # control: the bone whose pose it takes
    for control, bone in data["solve"]["fk"] + data["solve"]["copy"]:
        if control in built:
            targets[control] = bone
    for chain in data["solve"]["ik"]:
        if chain["effector"] in built:
            targets[chain["effector"]] = chain["bones"][2]
    placed = {c["name"]: c["bone"] for c in data["controls"]}
    for control in data["solve"]["spine"]["controls"]:
        if control in built:
            targets[control] = placed[control]        # the bone it was placed on
    order = [c["name"] for c in data["controls"] if c["name"] in targets]      # parents before children
    poses = {}
    set_on(obj, False)
    for f in range(start, end + 1):
        scene.frame_set(f)
        poses[f] = {c: pb[targets[c]].matrix.copy() for c in order}
        poses[f].update({chain["pole"]: _pole_place(obj, chain) for chain in data["solve"]["ik"] if chain["pole"] in built})
    ad = obj.animation_data
    bones_action = ad.action
    if bones_action is not None:
        track = ad.nla_tracks.new()
        track.name = "OR bones"
        track.strips.new(bones_action.name, int(bones_action.frame_range[0]), bones_action)
        track.mute = True
        ad.action = None
    for chain in data["solve"]["ik"]:
        if chain["switch"] in built:
            pb[chain["switch"]][chain["switch"]] = 0.0
    set_on(obj, True)
    for f in range(start, end + 1):
        scene.frame_set(f)
        for control in order + [p for p in poses[f] if p not in order]:
            pb[control].matrix = poses[f][control]
            bpy.context.view_layer.update()
            pb[control].keyframe_insert("location", frame=f)
            pb[control].keyframe_insert("rotation_quaternion" if pb[control].rotation_mode == 'QUATERNION' else "rotation_euler", frame=f)
    return end - start + 1
```
The operator `fpmp.official_rig_bake` runs it over the scene's frame range on the active armature and reports the frames.

- [ ] **Step 4: Run, watch them pass; run every plugin suite.** Expected: 0 failed everywhere.

- [ ] **Step 5: Commit**

```bash
git add plugins/Blender/fortnite_porting/processing/context/official_rig.py plugins/Blender/fortnite_porting/operator/rig_ui.py tests/plugin/official_rig_check.py
git commit -m "Bake animations onto the official rig"
```

### Task 7: Real assets

**Files:**
- Create: `fpfork-private/devtools/scripts/official_rig_bake.py` (fpdev script: bake the scene range, print error at a few frames)

- [ ] **Step 1: Nemia with the official rig**

```bash
cd /c/Users/kyooc/Documents/Claude/fpfork-private/devtools && export MSYS_NO_PATHCONV=1
python fpdev.py export nemia_or --type Outfit --path <Nemia's character path, as nemia earlier> --option RigType=2
python fpdev.py import nemia_or
python fpdev.py render nemia_or --controls front,side,top
```
Expected: the import log has "official rig: N controls"; the control renders show Epic's shapes and colours on the body; no "official rig (" error line.

- [ ] **Step 2: An emote, baked**

```bash
cp out/fpdev/nemia_or/nemia_or.blend out/fpdev/nemia_bake/nemia_bake.blend
python fpdev.py script nemia_bake scripts/emote_onto.py "<facepalm payload, Windows path>"
python fpdev.py script nemia_bake scripts/official_rig_bake.py
```
`official_rig_bake.py`: records the deform bones' world matrices at frames 1, 40, 80 with the rig off, bakes (`official_rig.bake_to_controls` over the scene range), then prints the largest bone position error (cm) and rotation error (degrees) at those frames. Expected: under 0.5 cm and 1 degree (ledger the numbers).

- [ ] **Step 3: Commit the script**

```bash
cd /c/Users/kyooc/Documents/Claude/fpfork-private && git add devtools/scripts/official_rig_bake.py && git -c user.name=kyoocreatives -c user.email=280023218+kyoocreatives@users.noreply.github.com commit -m "Add an official rig bake check"
```
