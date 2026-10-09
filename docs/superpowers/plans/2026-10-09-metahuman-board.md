# MetaHuman Face Board Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fortnite's MetaHuman-style heads get Epic's MetaHuman face board in Blender, its controls driving FP's face shape keys through Epic's decoded control-to-expression mapping.

**Architecture:** The fork's CUE4Parse literal reader learns the board rig's remaining constant types; a devtool symbolically runs the board rig's forward byte code once and writes each `CTRL_expressions_*` curve's formula, the controls' board layout and limits into `metahuman_board.json`; `processing/context/metahuman_board.py` turns formulas into driver expressions and builds the board beside the head on qualifying skeletons, from the fork's after-import hook.

**Tech Stack:** C# (CUE4Parse `patches/CUE4Parse/` overlay), Blender 5.2 Python plugin, headless isolated Blender tests, fpdev devtools.

**Spec:** `docs/superpowers/specs/2026-10-09-metahuman-board-design.md`

## Global Constraints

- Upstream FP files get only one-line hooks marked `// MP` / `# MP`; CUE4Parse changes are whole-file copies under `patches/CUE4Parse/`; never stage `external/CUE4Parse`.
- Driver expressions only use arithmetic, `min`, `max` (Blender's simple-expression evaluator; no Python auto-run needed).
- The board uses the existing Face Board switch (`fpmp_face_board`) and panel lines; bones in the kit's `Controls` collection or the board's own group (`face_board.GROUP`) on rigs without the kit's.
- Qualifying skeleton: `FACIAL_C_FacialRoot` bone and a mesh under it with at least 20 of the decoded expression names as shape keys.
- A failure is logged and never fails the import. Headless Blender only, isolated; test instance 24322.
- Plugin test command: as in the official rig plan (`baseline.copy_plugin` then isolated `blender -b --factory-startup --python-exit-code 1 -P tests/plugin/TEST -- <plugin parent>`).
- Never print secrets; filter logs with `grep -viE "aes|key"`. Commits: short plain messages approved by the user; kyoocreatives noreply identity; no Co-Authored-By.

## Review Focus

1. A 3L head whose mesh lacks some of the 73 keys (or has extra non-face keys like `shoeMorphA`): only matching keys get drivers; nothing raises.
2. Re-importing onto a skeleton that already has the board: no second board, no doubled drivers.
3. An emote imported (FP keys the shape keys from MetaHuman curves) then the Face Board switch turned back on: the board drives again, the emote's keys are not destroyed.
4. A legacy-face character (`*_pose` shape keys, no FACIAL bones) or a flipbook face: no MetaHuman board (the flipbook board path is unchanged).
5. Both a MetaHuman board and the official/Tasty rig on one armature: board bones don't collide with rig bones; Select Controls includes the knobs.

---

### Task 1: The literal reader's remaining types

**Files:**
- Modify: `patches/CUE4Parse/CUE4Parse/UE4/Objects/RigVM/FRigVMMemoryStorageStruct.cs`

**Interfaces:**
- Produces: in the board rig's full dump, `RigVM.LiteralMemoryStorage.Values` holds every literal (Vector2D as `{"X":..,"Y":..}`, enums as their value name, strings as text).

- [ ] **Step 1: The test (fails today)**

```bash
cd /c/Users/kyooc/Documents/Claude/fpfork-private/devtools
python - <<'PYEOF'
import json, urllib.parse, urllib.request
p = "FortniteGame/VKTemplates/Basic/VKT_Talisman_Bridge/Plugins/VKT_Talisman_Bridge/Content/MetaHumans/Common/Face/Face_ControlBoard_CtrlRig"
raw = urllib.request.urlopen("http://localhost:24322/fork-dump?" + urllib.parse.urlencode({"path": p, "full": "1"}, safe="/"), timeout=900).read()
open("out/face_board_dump.json", "wb").write(raw)
vm = [e for e in json.loads(raw) if e.get("Type") == "RigVM"][0]
lit = vm["LiteralMemoryStorage"]
print(len(lit["PropertyDescs"]), len(lit.get("Values") or {}))
PYEOF
```
Expected: `490 0` (or a Values count below 490).

- [ ] **Step 2: Implement** — in `ReadItem` add:

```csharp
        EPropertyBagPropertyType.String => Ar.ReadFString(),
        EPropertyBagPropertyType.Enum => Ar.ReadFName().Text,
        EPropertyBagPropertyType.Struct when desc.ValueTypeObject?.Name is "Vector2D" => ReadVector2D(Ar),
```
with
```csharp
    // FVector2D, an unversioned struct of two doubles (X, Y), either left out when zero
    private static Dictionary<string, double> ReadVector2D(FAssetArchive Ar)
    {
        var header = new FUnversionedHeader(Ar);
        var value = new Dictionary<string, double> { ["X"] = 0.0, ["Y"] = 0.0 };
        var index = 0;
        var zeroBit = 0;
        foreach (var fragment in header.Fragments)
        {
            index += fragment.SkipNum;
            for (var i = 0; i < fragment.ValueNum; i++, index++)
            {
                if (fragment.HasAnyZeroes && header.ZeroMask[zeroBit++]) continue;
                value[index == 0 ? "X" : "Y"] = Ar.Read<double>();
            }
        }
        return value;
    }
```
(If an enum is stored as a byte rather than a name in this bag, read it as `Ar.ReadByte()`; ledger which.) Copy the patch over the submodule: `cp -r patches/CUE4Parse/. external/CUE4Parse/`.

- [ ] **Step 3: Build, re-run** — `bash testapp.sh test`, then Step 1's script. Expected: `490 490`. Re-dump `Fortnite_Flipbook2_Face_Mapping_CtrlRig` and check its 56 literal values still read.

- [ ] **Step 4: Commit**

```bash
cd /c/Users/kyooc/Documents/Claude/fpfork && git add patches/CUE4Parse/CUE4Parse/UE4/Objects/RigVM/FRigVMMemoryStorageStruct.cs && git commit -m "Read the rest of Control Rig constants"
```

### Task 2: Decode the board into metahuman_board.json

**Files:**
- Create: `fpfork-private/devtools/metahuman_board_decode.py`
- Create: `plugins/Blender/fortnite_porting/processing/context/metahuman_board.json`

**Interfaces:**
- Consumes: Task 1's dump (`out/face_board_dump.json`).
- Produces `metahuman_board.json`:
```json
{
  "source": "FortniteGame/VKTemplates/.../Face_ControlBoard_CtrlRig",
  "unit": 1.0,
  "controls": [{"name": "CTRL_L_brow_down", "kind": "slider", "position": [x, y], "limits": {"x": null, "y": [0.0, 1.0]}, "shape": "Sphere_Solid"}],
  "frames": [{"name": "MH_FACIAL_BOARD", "outline": [[x, y], ...]}],
  "curves": {"CTRL_expressions_browDownL": {"op": "control", "name": "CTRL_L_brow_down", "axis": "y"}}
}
```
Formula nodes: `{"op": "control", "name", "axis": "x"|"y"}`, `{"op": "const", "value"}`, `{"op": "add", "a", "b"}`, `{"op": "mul", "a", "b"}`, `{"op": "remap", "in", "from": [a, b], "to": [c, d], "clamp": bool}`, `{"op": "interp", "in", "scale", "bias", "clamp": [min, max] | null, "range_in": [a, b] | null, "range_out": [c, d] | null}`, `{"op": "curve", "in", "keys": [[t, v], ...]}` (piecewise linear). `position`/`limits` in Epic's board units (`unit`: board units per Epic unit; positions as the hierarchy composes them).

- [ ] **Step 1: The check (fails: no file)**

`metahuman_board_decode.py check`: every `FRigUnit_SetCurveValue` in the forward entry writing a `CTRL_expressions_*` curve has a formula; every `control` node names a control in `controls`; every formula evaluates to 0.0 with every control at 0 (sliders and boxes at rest); prints counts. Run it: expected FileNotFoundError.

- [ ] **Step 2: Decode**

`metahuman_board_decode.py build`:
- The forward instructions are the entry `Forwards Solve`'s range (`ByteCodeStorage.Entries`, `BranchInfos`) up to the next entry (`Backwards Solve`); walk them in order with a symbolic register file: `Copy` copies a register's expression (respecting `.X`/`.Y` segment paths of Vector2D registers); `GetControlFloat(Control=name)` sets its value register to `control(name, "y")` when the control's axis is Y-only per its limits, else `x` (read the control's primary axis from the hierarchy settings: `PrimaryAxis`); `GetControlVector2D(Control=name)` sets `.X`→`control(name,"x")`, `.Y`→`control(name,"y")`; `MathFloatRemap(Value, SourceMinimum, SourceMaximum, TargetMinimum, TargetMaximum, bClamp)` → `remap`; `AlphaInterp(Value, Scale, Bias, bMapRange, InRange, OutRange, bClampResult, ClampMin, ClampMax, ...)` → `interp`; `MathFloatAdd(A, B)` → `add`; `AnimEvalRichCurve(Value, Curve, SourceMinimum, SourceMaximum, TargetMinimum, TargetMaximum)` → `curve` with the literal curve's keys mapped through the ranges; `SetCurveValue(Curve=name, Value)` records `curves[name] = expr(Value)` when `name` starts with `CTRL_expressions`. Operand names come from the literal/work register descs (as `rigvm_dump.py`); literal values from Task 1. A branch/jump inside the forward range is followed only when its condition is a literal; anything else stops the decode with the instruction index printed (a ruling then decides).
- `controls`: control elements of the board hierarchy (`Settings.ControlType` 1 float → `slider`, 3 Vector2D → `box`, others → `frame`), `position` = the element's composed global translation (hierarchy locals as the official rig decoder composes them) projected onto the board plane (UE Y, Z → board x, y), `limits` from `Settings.LimitEnabled`/`MinimumValue`/`MaximumValue` per axis, `shape` from `Settings.ShapeName`.
- `frames`: the board frame control(s) (`MH_FACIAL_BOARD` and any `kind: frame` with a `Thick`/`Square` shape) as rectangles from their shape transform scale.

- [ ] **Step 3: Run build then check** — Expected: `ok: N curves (N >= 73 of the shape-key names), M controls, every formula 0 at rest`.

- [ ] **Step 4: Commit**

```bash
cd /c/Users/kyooc/Documents/Claude/fpfork && git add plugins/Blender/fortnite_porting/processing/context/metahuman_board.json && git commit -m "Add the MetaHuman board's decoded mapping"
cd ../fpfork-private && git add devtools/metahuman_board_decode.py && git -c user.name=kyoocreatives -c user.email=280023218+kyoocreatives@users.noreply.github.com commit -m "Add the MetaHuman board decoder"
```

### Task 3: Formulas as driver expressions

**Files:**
- Create: `plugins/Blender/fortnite_porting/processing/context/metahuman_board.py` (the expression part)
- Create: `tests/plugin/metahuman_board_check.py`

**Interfaces:**
- Produces: `metahuman_board.load() -> dict`; `metahuman_board.expression(node, var) -> str` where `var(control_name, axis) -> str` is a driver variable name; `metahuman_board.evaluate(node, values) -> float` with `values[(control, axis)]` (used by tests and the decoder check).

- [ ] **Step 1: Failing tests**

```python
"""Checks the MetaHuman face board (Epic's Face_ControlBoard_CtrlRig on FP's MetaHuman-style heads).

    blender -b --factory-startup --python-exit-code 1 -P tests/plugin/metahuman_board_check.py -- <plugin parent>"""
import sys

import bpy  # noqa: F401

sys.path.insert(0, sys.argv[sys.argv.index("--") + 1])
import fpmp_baseline  # noqa: E402,F401
from fpmp_baseline.processing.context import metahuman_board as mb  # noqa: E402

FAILS, PASSES = [], [0]


def check(name, got, want, tol=1e-4):
    ok = abs(got - want) <= tol if isinstance(want, float) else got == want
    if ok:
        PASSES[0] += 1
    else:
        FAILS.append(name)
        print("[metahuman_board_check] FAIL %s: got %r, want %r" % (name, got, want))


ctl = lambda name, axis="y": {"op": "control", "name": name, "axis": axis}
var = lambda name, axis: "%s_%s" % (name, axis)
remap = {"op": "remap", "in": ctl("A"), "from": [0.0, 1.0], "to": [0.0, 2.0], "clamp": True}
check("remap clamps", mb.evaluate(remap, {("A", "y"): 0.75}), 1.5)
check("remap clamps above", mb.evaluate(remap, {("A", "y"): 3.0}), 2.0)
interp = {"op": "interp", "in": ctl("B", "x"), "scale": -1.0, "bias": 0.0, "clamp": [0.0, 1.0], "range_in": None, "range_out": None}
check("interp scales then clamps", (mb.evaluate(interp, {("B", "x"): -0.4}), mb.evaluate(interp, {("B", "x"): 0.4})), (0.4, 0.0))
both = {"op": "add", "a": remap, "b": interp}
check("add", mb.evaluate(both, {("A", "y"): 0.5, ("B", "x"): -0.25}), 1.25)
curve = {"op": "curve", "in": ctl("C"), "keys": [[0.0, 0.0], [0.5, 1.0], [1.0, 0.0]]}
check("curve peaks", (mb.evaluate(curve, {("C", "y"): 0.5}), mb.evaluate(curve, {("C", "y"): 0.25}), mb.evaluate(curve, {("C", "y"): 2.0})), (1.0, 0.5, 0.0))
for name, node, values in (("remap", remap, {("A", "y"): 0.6}), ("interp", interp, {("B", "x"): -0.3}),
                           ("add", both, {("A", "y"): 0.2, ("B", "x"): -0.9}), ("curve", curve, {("C", "y"): 0.7})):
    text = mb.expression(node, var)
    scope = {var(c, a): v for (c, a), v in values.items()}
    scope.update(min=min, max=max)
    check("%s expression matches" % name, round(eval(text, scope), 6), round(mb.evaluate(node, values), 6))
    check("%s expression is plain" % name, all(w in ("min", "max") or w.startswith(("A_", "B_", "C_")) for w in
          __import__("re").findall(r"[A-Za-z_]\w*", text)), True)
data = mb.load()
check("every decoded curve is 0 at rest", all(abs(mb.evaluate(n, {})) < 1e-6 for n in data["curves"].values()), True)

print("[metahuman_board_check] %d passed, %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
```

- [ ] **Step 2: Run, watch it fail** — Expected: `ImportError: cannot import name 'metahuman_board'`.

- [ ] **Step 3: Implement**

```python
"""Epic's MetaHuman face board (Face_ControlBoard_CtrlRig) on FP's MetaHuman-style heads: its controls drive the
head's expression shape keys through Epic's own control-to-curve mapping, decoded once into metahuman_board.json
(spec 2026-10-09-metahuman-board). The face's bones (MetaHuman's RigLogic) aren't driven."""
import json
import os

DATA = os.path.join(os.path.dirname(__file__), "metahuman_board.json")
_cache = {}


def load():
    if "data" not in _cache:
        with open(DATA, encoding="utf-8") as f:
            _cache["data"] = json.load(f)
    return _cache["data"]


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def evaluate(node, values):
    """A formula's value; values[(control, axis)] (missing: 0)."""
    op = node["op"]
    if op == "control":
        return values.get((node["name"], node["axis"]), 0.0)
    if op == "const":
        return node["value"]
    if op in ("add", "mul"):
        a, b = evaluate(node["a"], values), evaluate(node["b"], values)
        return a + b if op == "add" else a * b
    x = evaluate(node["in"], values)
    if op == "remap":
        (a, b), (c, d) = node["from"], node["to"]
        t = (x - a) / (b - a) if b != a else 0.0
        if node["clamp"]:
            t = _clamp(t, 0.0, 1.0)
        return c + (d - c) * t
    if op == "interp":
        if node.get("range_in"):
            (a, b), (c, d) = node["range_in"], node["range_out"]
            x = c + (d - c) * _clamp((x - a) / (b - a) if b != a else 0.0, 0.0, 1.0)
        x = x * node["scale"] + node["bias"]
        return _clamp(x, *node["clamp"]) if node.get("clamp") else x
    if op == "curve":
        keys = node["keys"]
        if x <= keys[0][0]:
            return keys[0][1]
        for (t0, v0), (t1, v1) in zip(keys, keys[1:]):
            if x <= t1:
                return v0 + (v1 - v0) * ((x - t0) / (t1 - t0) if t1 != t0 else 0.0)
        return keys[-1][1]
    raise ValueError(op)


def _num(v):
    return repr(round(float(v), 6))


def expression(node, var):
    """The formula as a driver expression Blender runs without Python: arithmetic, min, max."""
    op = node["op"]
    if op == "control":
        return var(node["name"], node["axis"])
    if op == "const":
        return _num(node["value"])
    if op in ("add", "mul"):
        return "(%s%s%s)" % (expression(node["a"], var), "+" if op == "add" else "*", expression(node["b"], var))
    x = expression(node["in"], var)
    if op == "remap":
        (a, b), (c, d) = node["from"], node["to"]
        t = "((%s-%s)/%s)" % (x, _num(a), _num(b - a)) if b != a else "0.0"
        if node["clamp"]:
            t = "max(0.0,min(1.0,%s))" % t
        return "(%s+%s*%s)" % (_num(c), _num(d - c), t)
    if op == "interp":
        if node.get("range_in"):
            (a, b), (c, d) = node["range_in"], node["range_out"]
            x = "(%s+%s*max(0.0,min(1.0,(%s-%s)/%s)))" % (_num(c), _num(d - c), x, _num(a), _num(b - a))
        x = "(%s*%s+%s)" % (x, _num(node["scale"]), _num(node["bias"]))
        if node.get("clamp"):
            x = "max(%s,min(%s,%s))" % (_num(node["clamp"][0]), _num(node["clamp"][1]), x)
        return x
    if op == "curve":
        # piecewise linear: the first key's value, plus each segment's rise over its span, clamped to the segment
        keys = node["keys"]
        terms = [_num(keys[0][1])]
        for (t0, v0), (t1, v1) in zip(keys, keys[1:]):
            if t1 != t0:
                terms.append("%s*max(0.0,min(1.0,(%s-%s)/%s))" % (_num(v1 - v0), x, _num(t0), _num(t1 - t0)))
        return "(%s)" % "+".join(terms)
    raise ValueError(op)
```

- [ ] **Step 4: Run, watch it pass** — Expected: `[metahuman_board_check] N passed, 0 failed`.

- [ ] **Step 5: Commit**

```bash
git add plugins/Blender/fortnite_porting/processing/context/metahuman_board.py tests/plugin/metahuman_board_check.py && git commit -m "Turn the MetaHuman board's formulas into drivers"
```

### Task 4: The board on a head, its switch, the hook

**Files:**
- Modify: `plugins/Blender/fortnite_porting/processing/context/metahuman_board.py`
- Modify: `plugins/Blender/fortnite_porting/material_porter/mesh_hooks.py` (`after_parts`)
- Modify: `plugins/Blender/fortnite_porting/processing/context/anim_context.py` (the official rig's MP line gains the board)
- Modify: `plugins/Blender/fortnite_porting/operator/rig_ui.py` (board lines where `face_board.ui` is drawn)
- Test: `tests/plugin/metahuman_board_check.py`

**Interfaces:**
- Consumes: Task 3's `expression`, `load`; `face_board.GROUP`, `face_board.set_on` pattern; `rig_shapes.ensure`, `rig_style.assign`.
- Produces: `metahuman_board.fits(obj) -> bool`; `metahuman_board.add(obj, size=None) -> int` (drivers made; 0 when it doesn't fit or is already built); `metahuman_board.on_animation_import(obj)`; bones `MB_<control>` and `MB_Board`; armature data mark `fpmp_metahuman_board`; armature data `fpmp_face_board_materials` unchanged (shape-key drivers live on the mesh's shape keys; the switch mutes them via `fpmp_metahuman_board_meshes`).

- [ ] **Step 1: Failing tests** (append before the summary)

```python
from mathutils import Vector  # noqa: E402
from fpmp_baseline.processing.context import face_board  # noqa: E402
face_board.register()
KEYS = [n.replace("CTRL_expressions_", "") for n in data["curves"]][:73]


def head_3l(name, keys=KEYS, facial=True):
    """An armature with head (and FACIAL_C_FacialRoot) bones and a mesh with the given shape keys."""
    arm = bpy.data.objects.new(name, bpy.data.armatures.new(name))
    bpy.context.scene.collection.objects.link(arm)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode='EDIT')
    for n, z in (("root", 0.0), ("head", 1.6)) + ((("FACIAL_C_FacialRoot", 1.62),) if facial else ()):
        b = arm.data.edit_bones.new(n)
        b.head, b.tail = (0, 0, z), (0, 0, z + 0.1)
    bpy.ops.object.mode_set(mode='OBJECT')
    me = bpy.data.meshes.new(name + "_head")
    me.from_pydata([(0, 0, 1.6), (0.1, 0, 1.6), (0, 0.1, 1.7)], [], [(0, 1, 2)])
    mesh = bpy.data.objects.new(name + "_head", me)
    bpy.context.scene.collection.objects.link(mesh)
    mesh.parent = arm
    mesh.shape_key_add(name="Basis")
    for k in keys + ["shoeMorphA"]:
        mesh.shape_key_add(name=k)
    return arm, mesh


arm, mesh = head_3l("mh")
check("fits", mb.fits(arm), True)
made = mb.add(arm)
keyed = [k for k in mesh.data.shape_keys.key_blocks if k.name in KEYS]
check("one driver per decoded key", made, len(keyed))
check("non-face keys left alone", mesh.data.shape_keys.animation_data.drivers.find('key_blocks["shoeMorphA"].value') is None, True)
check("board bone", "MB_Board" in arm.data.bones, True)
pick = next(n for n, node in data["curves"].items() if node["op"] != "const" and n.replace("CTRL_expressions_", "") in KEYS)
key = mesh.data.shape_keys.key_blocks[pick.replace("CTRL_expressions_", "")]
bpy.context.view_layer.update()
check("rest leaves the key at 0", round(key.value, 4), 0.0)
first = next(c for c in __import__("json").dumps(data["curves"][pick]).split('"name": "')[1:]).split('"')[0]
kb = arm.pose.bones["MB_" + first]
lim = next(c for c in data["controls"] if c["name"] == first)["limits"]
axis = "y" if lim.get("y") else "x"
target = lim[axis][1]
setattr(kb.location, "z" if axis == "y" else "x", target * mb.step(arm))
arm.update_tag()
bpy.context.view_layer.update()
check("moving a knob drives its key as the formula says", round(key.value, 3),
      round(mb.evaluate(data["curves"][pick], {(first, axis): target}), 3))
check("a second add makes nothing", mb.add(arm), 0)
arm.fpmp_face_board = False
check("switch off mutes the drivers", all(d.mute for d in mesh.data.shape_keys.animation_data.drivers), True)
arm.fpmp_face_board = True
check("switch on unmutes", any(d.mute for d in mesh.data.shape_keys.animation_data.drivers), False)
mb.on_animation_import(arm)
check("an emote turns the board off", arm.fpmp_face_board, False)
legacy, _ = head_3l("legacy", keys=["jaw_open_pose", "L_blink_pose"], facial=False)
check("a legacy face gets no MetaHuman board", (mb.fits(legacy), mb.add(legacy)), (False, 0))
few, few_mesh = head_3l("few", keys=KEYS[:25])
check("a head with only some keys gets drivers for those", mb.add(few), 25)
# the hook
from types import SimpleNamespace  # noqa: E402
from fpmp_baseline.material_porter import mesh_hooks  # noqa: E402
hooked, _ = head_3l("hooked")
mesh_hooks._metahuman(SimpleNamespace(imported_meshes=[{"Skeleton": hooked, "Mesh": None}]))
check("the hook adds the board", bool(hooked.data.get("fpmp_metahuman_board")), True)
broken, _ = head_3l("broken")
real = mb.add
mb.add = lambda obj, size=None: 1 / 0
try:
    mesh_hooks._metahuman(SimpleNamespace(imported_meshes=[{"Skeleton": broken, "Mesh": None}]))
    survived = True
except Exception:
    survived = False
mb.add = real
check("a board failure doesn't break the import", survived, True)
```

- [ ] **Step 2: Run, watch it fail** — Expected: `AttributeError: ... 'fits'`.

- [ ] **Step 3: Implement**

```python
import bpy
from mathutils import Vector

from . import face_board, rig_shapes, rig_style

MARK, MESHES, STEP = "fpmp_metahuman_board", "fpmp_metahuman_board_meshes", "fpmp_metahuman_board_step"
PREFIX = "MB_"
BOARD = PREFIX + "Board"


def _meshes(obj):
    return [o for o in bpy.data.objects if o.type == 'MESH' and o.data.shape_keys is not None and
            (o.parent == obj or any(m.type == 'ARMATURE' and m.object == obj for m in o.modifiers))]


def _keys(obj, data):
    names = {n.replace("CTRL_expressions_", "") for n in data["curves"]}
    return [(m, k) for m in _meshes(obj) for k in m.data.shape_keys.key_blocks if k.name in names]


def fits(obj):
    return obj is not None and obj.type == 'ARMATURE' and "FACIAL_C_FacialRoot" in obj.data.bones and \
        len(_keys(obj, load())) >= 20


def step(obj):
    return obj.data[STEP]


def add(obj, size=None):
    """The board beside the head, its knobs driving the head's expression shape keys. Returns the drivers made."""
    data = load()
    if obj.data.get(MARK) or not fits(obj) or "head" not in obj.data.bones:
        return 0
    head = obj.data.bones["head"]
    size = size or max(head.length * 2.0, 0.1)
    xs = [c["position"][0] for c in data["controls"]]
    ys = [c["position"][1] for c in data["controls"]]
    span = max(max(xs) - min(xs), max(ys) - min(ys), 1e-6)
    s = size * 1.2 / span                       # board units -> metres
    origin = head.head_local + Vector((size * 1.2, 0.0, 0.0))
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')
    edit = obj.data.edit_bones
    board = edit.new(BOARD)
    board.head, board.tail = origin, origin + Vector((0.0, -size * 0.1, 0.0))
    board.parent, board.use_deform = edit["head"], False
    for c in data["controls"]:
        if c["kind"] not in ("slider", "box"):
            continue
        b = edit.new(PREFIX + c["name"])
        b.head = origin + Vector(((c["position"][0] - min(xs)) * s, 0.0, (c["position"][1] - min(ys)) * s))
        b.tail = b.head + Vector((0.0, -size * 0.03, 0.0))      # facing out of the board: local X right, Z up
        b.parent, b.use_deform = board, False
    bpy.ops.object.mode_set(mode='POSE')
    obj.data[STEP] = s
    group = "Controls" if "Controls" in obj.data.collections else None
    for c in data["controls"]:
        name = PREFIX + c["name"]
        if name not in obj.pose.bones:
            continue
        pb = obj.pose.bones[name]
        pb.custom_shape = rig_shapes.ensure("CR_Circle")
        pb.use_custom_shape_bone_size = False
        pb.custom_shape_scale_xyz = (size * 0.012,) * 3
        rig_shapes.color(pb, (1.0, 0.85, 0.1))
        pb.lock_location = (not c["limits"].get("x"), True, not c["limits"].get("y"))
        pb.lock_rotation = pb.lock_scale = (True, True, True)
        limit = pb.constraints.new('LIMIT_LOCATION')
        limit.owner_space, limit.use_transform_limit = 'LOCAL', True
        for axis, attr in (("x", "x"), ("y", "z")):
            rng = c["limits"].get(axis) or (0.0, 0.0)
            setattr(limit, "use_min_" + attr, True)
            setattr(limit, "use_max_" + attr, True)
            setattr(limit, "min_" + attr, rng[0] * s)
            setattr(limit, "max_" + attr, rng[1] * s)
        if group:
            rig_style.assign(obj.data, name, group)
        else:
            (obj.data.collections.get(face_board.GROUP) or obj.data.collections.new(face_board.GROUP)).assign(obj.data.bones[name])
    bpy.ops.object.mode_set(mode='OBJECT')
    made = 0
    for mesh, key in _keys(obj, data):
        node = data["curves"]["CTRL_expressions_" + key.name]
        fc = key.driver_add("value")
        driver = fc.driver
        driver.type = 'SCRIPTED'
        used = {}

        def var(control, axis, driver=driver, used=used):
            name = "v%d" % len(used) if (control, axis) not in used else used[(control, axis)]
            if (control, axis) not in used:
                used[(control, axis)] = name
                v = driver.variables.new()
                v.name, v.type = name, 'TRANSFORMS'
                v.targets[0].id, v.targets[0].bone_target = obj, PREFIX + control
                v.targets[0].transform_type = 'LOC_X' if axis == "x" else 'LOC_Z'
                v.targets[0].transform_space = 'LOCAL_SPACE'
            return "(%s/%r)" % (name, s)
        driver.expression = expression(node, var)
        made += 1
    obj.data[MARK] = True
    obj.data[MESHES] = sorted({m.name for m, _ in _keys(obj, data)})
    return made


def set_on(obj, on):
    for name in obj.data.get(MESHES, []):
        mesh = bpy.data.objects.get(name)
        ad = mesh.data.shape_keys.animation_data if mesh and mesh.data.shape_keys else None
        for fc in (ad.drivers if ad else []):
            fc.mute = not on


def on_animation_import(obj):
    if obj is not None and obj.data.get(MARK):
        obj.fpmp_face_board = False
```
and in `face_board._toggled` (the switch's update) also call `metahuman_board.set_on(obj, obj.fpmp_face_board)` when the armature has the mark (lazy import). `mesh_hooks.after_parts`, after the official/Tasty blocks:
```python
    _metahuman(ctx)
```
with
```python
def _metahuman(ctx):
    """Epic's MetaHuman face board on a MetaHuman-style head (FACIAL bones, expression shape keys)."""
    from ..processing.context import metahuman_board
    for skeleton in {m.get("Skeleton") for m in ctx.imported_meshes if _alive(m.get("Skeleton"))}:
        try:
            if metahuman_board.fits(skeleton):
                Log.info("%s: MetaHuman board, %d shape keys" % (skeleton.name, metahuman_board.add(skeleton)))
        except Exception as e:
            Log.error("%s: MetaHuman board (%s: %s)" % (skeleton.name, type(e).__name__, e))
            if bpy.context.mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')
```
`anim_context.py`'s MP line becomes:
```python
        from ...material_porter.anim_hooks import begin; begin(target_skeleton)     # MP: rigs and boards step aside for the animation
```
with a new `material_porter/anim_hooks.py`:
```python
"""The fork's steps when an animation is imported onto an armature: rigs and face boards step aside so it plays."""


def begin(armature):
    from ..processing.context import metahuman_board, official_rig
    official_rig.on_animation_import(armature)
    metahuman_board.on_animation_import(armature)
```
`rig_ui.py`: where `face_board.ui(col, obj)` is drawn, the switch already shows when `fpmp_face_board_materials` exists; also show it when `fpmp_metahuman_board` is set (one condition in `face_board.ui`).

- [ ] **Step 4: Run, watch them pass; run every plugin suite** — Expected: 0 failed everywhere (official_rig_check, rig_check, flipbook_face_check, translator, layout, subsurface).

- [ ] **Step 5: Commit**

```bash
git add plugins/Blender/fortnite_porting/processing/context/metahuman_board.py plugins/Blender/fortnite_porting/processing/context/face_board.py plugins/Blender/fortnite_porting/material_porter/mesh_hooks.py plugins/Blender/fortnite_porting/material_porter/anim_hooks.py plugins/Blender/fortnite_porting/processing/context/anim_context.py plugins/Blender/fortnite_porting/operator/rig_ui.py tests/plugin/metahuman_board_check.py
git commit -m "Add the MetaHuman face board"
```

### Task 5: Real asset

**Files:**
- Create: `fpfork-private/devtools/scripts/metahuman_board_pose.py` (fpdev script: set board knobs by name=value, save)

- [ ] **Step 1: Import a MetaHuman-style outfit**

```bash
cd /c/Users/kyooc/Documents/Claude/fpfork-private/devtools && export MSYS_NO_PATHCONV=1
python fpdev.py find VibrantShell --count 20 | grep "Cosmetics/Characters"
python fpdev.py export mh_vs --type Outfit --path <Character_VibrantShell path> --option RigType=2
python fpdev.py import mh_vs
```
Expected: the import log has "MetaHuman board, N shape keys" (N about 73) and no "MetaHuman board (" error.

- [ ] **Step 2: Poses**

`metahuman_board_pose.py`: `python fpdev.py script mh_vs scripts/metahuman_board_pose.py CTRL_L_brow_down=1 CTRL_R_brow_down=1` sets each knob to the value × `metahuman_board.step(arm)` on its limited axis and saves to a copy name passed as the last argument. Make three copies (brow down, jaw open via `CTRL_C_jaw` y, smile via `CTRL_L_mouth_cornerPull`/`CTRL_R_mouth_cornerPull`) and render each with `python fpdev.py render <copy> --frame Head --cycles`. Expected: the face changes as named; rest render unchanged from the plain import.

- [ ] **Step 3: Commit the script**

```bash
cd /c/Users/kyooc/Documents/Claude/fpfork-private && git add devtools/scripts/metahuman_board_pose.py && git -c user.name=kyoocreatives -c user.email=280023218+kyoocreatives@users.noreply.github.com commit -m "Add a MetaHuman board pose script"
```
