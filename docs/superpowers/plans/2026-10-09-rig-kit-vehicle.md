# Rig Kit + Vehicle Rig Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A shared rig kit (colours, widths, shapes, collections, settings bone, panel) and the vehicle rig rebuilt on it.

**Architecture:** `rig_style.py` (new) holds the conventions and small helpers; `rig_shapes.py` gains the new shapes;
`vehicle_rig.py` keeps its construction and calls the kit for settings, styling and collections; the Rig panel moves
to `operator/rig_ui.py` (new) with two operators. A synthetic car skeleton built in a headless test drives TDD;
real cars are checked through fpdev at the end.

**Tech Stack:** Blender 5.2 Python (bpy), the fork's plugin (`plugins/Blender/fortnite_porting`), headless Blender for tests.

**Spec:** `docs/superpowers/specs/2026-10-09-rig-kit-vehicle-design.md`

## Global Constraints

- Colours (normal): left (0.15, 0.45, 1.0); right (1.0, 0.2, 0.15); centre (1.0, 0.82, 0.1); main (1.0, 0.5, 0.05);
  settings (0.7, 0.3, 1.0); secondary = the side's colour lightened halfway to white.
- Wire widths: main 3.5, primary 2.5, secondary 1.5.
- Collections, in order: Controls (shown), Secondary (shown), Mechanics (hidden), Game Bones (hidden).
- Settings bone `CR_Settings`; settings read by drivers at `pose.bones["CR_Settings"]["<name>"]`; vehicle settings
  `auto_wheels`, `auto_steer`, `countersteer`, `suspension` (default 1.0) and `lean` (default 0.0), 0-1.
- Ground stays the armature object's `fpmp_ground`.
- Rigs built before this keep working (panel reads their settings from the object); no migration.
- Fork code habits: short comments (why only), no dead code, files under ~900 lines; upstream files only get `# MP` one-liners
  (all files touched here are fork files).
- Commits: message proposed to the user and approved first; no Co-Authored-By trailer.

## Review Focus

- A vehicle with no steering (tank, rear-steer oddity): no CR_Steer, so the settings and styling code must not assume it.
- A vehicle with no body bone: no CR_Body / CR_Body_Follow; collections and styles must still be complete.
- A wheel bone that is its own chain top (no arch): no CR_Arch for it; styling loops must skip it.
- A rig built before this change (settings on the object, old collection names): the new panel must still show its
  settings and toggles without errors.
- Reset Pose on a rig whose controls use different rotation modes (quaternion, euler, axis-angle): all reset to rest.

Each line's test is in the owning task (Task 3 covers the first three with the synthetic car's variants; Task 4 the last two).

---

### Task 1: New control shapes

**Files:**
- Modify: `plugins/Blender/fortnite_porting/processing/context/rig_shapes.py` (add shape builders, extend `SHAPES`)
- Test: `tests/plugin/rig_check.py` (create)

**Interfaces:**
- Produces: shape names `CR_Circle`, `CR_CircleTick`, `CR_Square`, `CR_Box`, `CR_Diamond`, `CR_Gear` usable with
  `rig_shapes.ensure(name)`; all unit size (radius or half-size 1) in their XY plane (box: a cube), Z up.

- [ ] **Step 1: Write the failing test**

Create `tests/plugin/rig_check.py`:

```python
"""Checks the fork's rig kit and the vehicle rig on a synthetic car skeleton (no game data needed).

    blender -b --factory-startup --python-exit-code 1 -P tests/plugin/rig_check.py -- <plugin parent>

<plugin parent> holds the plugin as package fpmp_baseline (see translator_test.py). Exit code 0 when every check passes."""
import sys

import bpy
from mathutils import Vector

sys.path.insert(0, sys.argv[sys.argv.index("--") + 1])
import fpmp_baseline  # noqa: E402,F401
from fpmp_baseline.processing.context import rig_shapes  # noqa: E402

FAILS = []
PASSES = [0]


def check(name, got, want, tol=1e-4):
    ok = all(abs(g - w) <= tol for g, w in zip(got, want)) if isinstance(want, tuple) else \
        (abs(got - want) <= tol if isinstance(want, float) else got == want)
    if ok:
        PASSES[0] += 1
    else:
        FAILS.append(name)
        print("[rig_check] FAIL %s: got %r, want %r" % (name, got, want))


# shapes
for name in ("CR_Circle", "CR_CircleTick", "CR_Square", "CR_Box", "CR_Diamond", "CR_Gear"):
    shape = rig_shapes.ensure(name)
    reach = max(v.co.length for v in shape.data.vertices)
    check("%s exists with edges" % name, len(shape.data.edges) > 3, True)
    check("%s is unit size" % name, 0.9 <= reach <= 1.8, True)
check("circle is smooth", len(rig_shapes.ensure("CR_Circle").data.vertices), 32)
check("tick reaches past the circle", max(v.co.y for v in rig_shapes.ensure("CR_CircleTick").data.vertices) > 1.1, True)
check("box is 3D", max(abs(v.co.z) for v in rig_shapes.ensure("CR_Box").data.vertices), 1.0)

print("[rig_check] %d passed, %d failed" % (PASSES[0], len(FAILS)))
if FAILS:
    sys.exit(1)
```

- [ ] **Step 2: Run it to verify it fails**

Copy the plugin and run headless (isolated Blender):
```bash
python -c "import sys; sys.path.insert(0, r'C:\Users\kyooc\Documents\Claude\fpfork-private\devtools'); import baseline; baseline.copy_plugin(r'C:\Users\kyooc\Documents\Claude\fpfork')"
BLENDER_USER_CONFIG=<scratch>/config BLENDER_USER_SCRIPTS=<scratch>/scripts BLENDER_USER_EXTENSIONS=<scratch>/ext "C:/Program Files (x86)/Steam/steamapps/common/Blender/blender.exe" -b --factory-startup --python-exit-code 1 -P tests/plugin/rig_check.py -- "C:/Users/kyooc/Documents/Claude/fpfork-private/devtools/out/baseline-plugin"
```
Expected: FAIL, `KeyError: 'CR_Circle'` from `rig_shapes.ensure`.

- [ ] **Step 3: Implement the shapes**

In `rig_shapes.py`, before `SHAPES = ...`, add:

```python
def _circle(steps=32):
    """Circle of radius 1 about Z."""
    verts = [(cos(radians(360.0 * i / steps)), sin(radians(360.0 * i / steps)), 0.0) for i in range(steps)]
    return verts, [(i, (i + 1) % steps) for i in range(steps)]


def _circle_tick():
    """Circle with a tick past its top (+Y), so a turn reads at a glance."""
    verts, edges = _circle()
    verts += [(0.0, 1.0, 0.0), (0.0, 1.35, 0.0)]
    edges.append((len(verts) - 2, len(verts) - 1))
    return verts, edges


def _square():
    """Square of half-size 1."""
    verts = [(-1.0, -1.0, 0.0), (1.0, -1.0, 0.0), (1.0, 1.0, 0.0), (-1.0, 1.0, 0.0)]
    return verts, [(i, (i + 1) % 4) for i in range(4)]


def _box():
    """Wire cube of half-size 1."""
    verts = [(x, y, z) for z in (-1.0, 1.0) for y in (-1.0, 1.0) for x in (-1.0, 1.0)]
    edges = [(0, 1), (2, 3), (4, 5), (6, 7), (0, 2), (1, 3), (4, 6), (5, 7), (0, 4), (1, 5), (2, 6), (3, 7)]
    return verts, edges


def _diamond():
    """Octahedron of radius 1 (a pole: readable from any side)."""
    verts = [(1.0, 0.0, 0.0), (-1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, -1.0, 0.0), (0.0, 0.0, 1.0), (0.0, 0.0, -1.0)]
    edges = [(a, b) for a in range(6) for b in range(a + 1, 6) if a // 2 != b // 2]
    return verts, edges


def _gear(teeth=8):
    """Gear: a ring of radius 0.75 with `teeth` square teeth out to 1, and a hub circle."""
    verts = []
    for i in range(teeth):
        a, step = 2.0 * pi * i / teeth, 2.0 * pi / teeth
        for angle, r in ((a, 0.75), (a + step * 0.15, 1.0), (a + step * 0.45, 1.0), (a + step * 0.6, 0.75)):
            verts.append((r * cos(angle), r * sin(angle), 0.0))
    edges = [(i, (i + 1) % len(verts)) for i in range(len(verts))]
    hub, hub_edges = _circle(16)
    first = len(verts)
    verts += [(x * 0.3, y * 0.3, 0.0) for x, y, _ in hub]
    edges += [(a + first, b + first) for a, b in hub_edges]
    return verts, edges
```

Add `pi` to the math import: `from math import cos, pi, radians, sin`. Extend `SHAPES`:

```python
SHAPES = {"CR_Arrow": _arrow, "CR_Turn": _turn, "CR_Swing": lambda: _turn(18.0), "CR_Foot": _foot, "CR_Glasses": _glasses,
          "CR_UpDown": _updown, "CR_Circle": _circle, "CR_CircleTick": _circle_tick, "CR_Square": _square, "CR_Box": _box,
          "CR_Diamond": _diamond, "CR_Gear": _gear}
```

- [ ] **Step 4: Run the test to verify it passes**

Same commands as Step 2. Expected: `[rig_check] 15 passed, 0 failed`.

- [ ] **Step 5: Commit**

Propose `Add the rig kit's control shapes`; after the user's OK:
```bash
git add plugins/Blender/fortnite_porting/processing/context/rig_shapes.py tests/plugin/rig_check.py
git commit -m "Add the rig kit's control shapes"
```

---

### Task 2: The rig kit (`rig_style.py`)

**Files:**
- Create: `plugins/Blender/fortnite_porting/processing/context/rig_style.py`
- Test: `tests/plugin/rig_check.py` (extend)

**Interfaces:**
- Consumes: `rig_shapes.color(pose_bone, rgb)` (existing), `rig_shapes.ensure(name)` (Task 1).
- Produces:
  - `rig_style.COLLECTIONS = ("Controls", "Secondary", "Mechanics", "Game Bones")`
  - `rig_style.collections(armature) -> dict[str, BoneCollection]` (creates/orders/shows per the table; hides others)
  - `rig_style.assign(armature, bone_name, group)` (moves the bone into exactly one of our collections)
  - `rig_style.side_of(point, centre, left, width) -> "L" | "R" | "C"` (dead zone: |offset| < width * 0.1 is "C")
  - `rig_style.style(pose_bone, role, secondary=False, width=None)`; role in "L", "R", "C", "main", "settings";
    width defaults: main 3.5, secondary 1.5, else 2.5
  - `rig_style.SETTINGS = "CR_Settings"`; `rig_style.add_settings(obj, settings)` with settings a list of
    `(name, default, description)`; `rig_style.setting_path(name) -> 'pose.bones["CR_Settings"]["name"]'`
  - `rig_style.driven(obj, constraint, name)` (influence driven by the setting)
  - `rig_style.settings_owner(obj) -> (owner, path_prefix)`: the settings bone if the rig has one, else the object
  - `rig_style.controls(obj) -> list[PoseBone]` (bones in Controls or Secondary)

- [ ] **Step 1: Write the failing tests**

In `tests/plugin/rig_check.py`, before the summary lines, add:

```python
# the kit, on a bare armature
from fpmp_baseline.processing.context import rig_style  # noqa: E402

arm_data = bpy.data.armatures.new("kit")
kit = bpy.data.objects.new("kit", arm_data)
bpy.context.scene.collection.objects.link(kit)
bpy.context.view_layer.objects.active = kit
bpy.ops.object.mode_set(mode='EDIT')
for name, x in (("CR_Settings", 0.0), ("hand_l", -1.0), ("hand_r", 1.0)):
    b = arm_data.edit_bones.new(name)
    b.head, b.tail = (x, 0.0, 0.0), (x, 0.2, 0.0)
bpy.ops.object.mode_set(mode='POSE')
arm_data.collections.new("FromTheGame")
groups = rig_style.collections(arm_data)
check("kit collections in order", [c.name for c in arm_data.collections if c.name in rig_style.COLLECTIONS], list(rig_style.COLLECTIONS))
check("kit visibility", [groups[n].is_visible for n in rig_style.COLLECTIONS], [True, True, False, False])
check("game's collections hidden", arm_data.collections["FromTheGame"].is_visible, False)
rig_style.assign(arm_data, "hand_l", "Controls")
rig_style.assign(arm_data, "hand_l", "Secondary")
check("assign moves, never doubles", [n for n in rig_style.COLLECTIONS if "hand_l" in groups[n].bones], ["Secondary"])
centre, left = Vector((0.0, 0.0, 0.0)), Vector((-1.0, 0.0, 0.0))
check("side left", rig_style.side_of(Vector((-1.0, 0.0, 0.0)), centre, left, 2.0), "L")
check("side right", rig_style.side_of(Vector((1.0, 0.0, 0.0)), centre, left, 2.0), "R")
check("side centre", rig_style.side_of(Vector((0.1, 0.0, 0.0)), centre, left, 2.0), "C")
pb = kit.pose.bones["hand_l"]
rig_style.style(pb, "L")
check("left colour", tuple(pb.color.custom.normal), (0.15, 0.45, 1.0))
check("primary width", pb.custom_shape_wire_width, 2.5)
rig_style.style(pb, "L", secondary=True)
check("secondary tint", tuple(pb.color.custom.normal), (0.575, 0.725, 1.0))
check("secondary width", pb.custom_shape_wire_width, 1.5)
rig_style.style(kit.pose.bones["hand_r"], "main")
check("main width", kit.pose.bones["hand_r"].custom_shape_wire_width, 3.5)
rig_style.add_settings(kit, [("spin", 1.0, "Wheels spin"), ("lean", 0.0, "Lean")])
check("settings on the bone", (kit.pose.bones["CR_Settings"]["spin"], kit.pose.bones["CR_Settings"]["lean"]), (1.0, 0.0))
check("setting path", rig_style.setting_path("spin"), 'pose.bones["CR_Settings"]["spin"]')
check("settings owner", rig_style.settings_owner(kit)[0] == kit.pose.bones["CR_Settings"], True)
check("settings keyable", kit.keyframe_insert(rig_style.setting_path("spin")), True)
con = kit.pose.bones["hand_r"].constraints.new('COPY_LOCATION')
rig_style.driven(kit, con, "lean")
kit.pose.bones["CR_Settings"]["lean"] = 0.25
bpy.context.view_layer.update()
check("driven influence follows the setting", con.influence, 0.25)
check("controls are the shown groups", sorted(b.name for b in rig_style.controls(kit)), ["hand_l"])
bpy.ops.object.mode_set(mode='OBJECT')
```

- [ ] **Step 2: Run to verify it fails**

Same commands as Task 1 Step 2. Expected: FAIL, `ImportError: cannot import name 'rig_style'`.

- [ ] **Step 3: Implement `rig_style.py`**

```python
"""The fork's rig conventions, shared by its rigs: side colours, wire widths, four bone collections, a keyable
settings bone. Each rig builds its own bones and constraints, then styles them with this."""
import bpy

from . import rig_shapes

COLLECTIONS = ("Controls", "Secondary", "Mechanics", "Game Bones")
SHOWN = {"Controls": True, "Secondary": True, "Mechanics": False, "Game Bones": False}
COLORS = {"L": (0.15, 0.45, 1.0), "R": (1.0, 0.2, 0.15), "C": (1.0, 0.82, 0.1),
          "main": (1.0, 0.5, 0.05), "settings": (0.7, 0.3, 1.0)}
WIDTHS = {"main": 3.5, "primary": 2.5, "secondary": 1.5}
SETTINGS = "CR_Settings"


def collections(armature):
    """The four collections, in order, shown or hidden as the convention says; the game's own are hidden."""
    for collection in armature.collections:
        if collection.name not in COLLECTIONS:
            collection.is_visible = False
    found = {}
    for index, name in enumerate(COLLECTIONS):
        collection = armature.collections.get(name) or armature.collections.new(name)
        collection.is_visible = SHOWN[name]
        armature.collections.move(armature.collections.find(name), index)
        found[name] = collection
    return found


def assign(armature, bone_name, group):
    """Put a bone in exactly one of the kit's collections."""
    bone = armature.bones[bone_name]
    for name in COLLECTIONS:
        if name != group and name in armature.collections:
            armature.collections[name].unassign(bone)
    armature.collections[group].assign(bone)


def side_of(point, centre, left, width):
    """"L", "R" or "C" from where a point sits across the rig (names vary too much between skeletons)."""
    offset = (point - centre).dot(left)
    if abs(offset) < width * 0.1:
        return "C"
    return "L" if offset > 0.0 else "R"


def style(pose_bone, role, secondary=False, width=None):
    """Colour and wire width of a control; role "L", "R", "C", "main" or "settings"."""
    rgb = COLORS[role]
    if secondary:
        rgb = tuple(c + (1.0 - c) * 0.5 for c in rgb)
    rig_shapes.color(pose_bone, rgb)
    pose_bone.custom_shape_wire_width = width or WIDTHS["main" if role == "main" else "secondary" if secondary else "primary"]


def setting_path(name):
    return 'pose.bones["%s"]["%s"]' % (SETTINGS, name)


def add_settings(obj, settings):
    """The rig's settings as 0-1 properties of its settings bone: keyable with the pose."""
    bone = obj.pose.bones[SETTINGS]
    for name, default, description in settings:
        bone[name] = default
        bone.id_properties_ui(name).update(min=0.0, max=1.0, description=description)


def settings_owner(obj):
    """Where a rig keeps its settings: the settings bone, or the object for rigs built before it."""
    bone = obj.pose.bones.get(SETTINGS) if obj.pose else None
    return (bone, setting_path("%s")) if bone is not None else (obj, '["%s"]')


def driven(obj, constraint, name):
    """A constraint's influence follows a setting."""
    driver = constraint.driver_add("influence").driver
    driver.type = 'SCRIPTED'
    var = driver.variables.new()
    var.name, var.type = "on", 'SINGLE_PROP'
    var.targets[0].id = obj
    var.targets[0].data_path = setting_path(name)
    driver.expression = "on"


def controls(obj):
    """The pose bones an animator poses: in Controls or Secondary."""
    shown = [obj.data.collections[n] for n in COLLECTIONS[:2] if n in obj.data.collections]
    return [pb for pb in obj.pose.bones if any(pb.name in c.bones for c in shown)]
```

- [ ] **Step 4: Run to verify it passes**

Expected: `[rig_check] 33 passed, 0 failed` (15 + 18).

- [ ] **Step 5: Commit**

Propose `Add the rig kit`; after the user's OK:
```bash
git add plugins/Blender/fortnite_porting/processing/context/rig_style.py tests/plugin/rig_check.py
git commit -m "Add the rig kit"
```

---

### Task 3: The vehicle rig on the kit

**Files:**
- Modify: `plugins/Blender/fortnite_porting/processing/context/vehicle_rig.py`:
  - `COLORS` (line 45-47): remove (the kit's colours replace it);
  - `_drive_channel` (186-204): a 2-tuple variable reads the settings bone (`rig_style.setting_path`);
  - `_property` (181-183): remove (settings go through `rig_style.add_settings`);
  - `create()`: add the settings bone (edit mode), settings via the kit, drivers via `rig_style.driven`,
    replace the styling/collections block (today 494-592) as below, `obj.show_in_front = True`.
- Test: `tests/plugin/rig_check.py` (extend: synthetic car)

**Interfaces:**
- Consumes: Task 1 shapes, Task 2 `rig_style` API.
- Produces: vehicle rigs with `CR_Settings` (settings `auto_wheels`, `auto_steer`, `countersteer`, `suspension`, `lean`),
  the four kit collections, kit colours and widths, `show_in_front`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/plugin/rig_check.py`, before the summary lines:

```python
# the vehicle rig on a synthetic car: root > frame > body, a differential per axle, axle pivots, front steering
from fpmp_baseline.processing.context import vehicle_rig  # noqa: E402


def car(name, steering=True, body=True, bare_rear=False):
    data = bpy.data.armatures.new(name)
    obj = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')
    edit = data.edit_bones

    def bone(n, head, tail, parent=None):
        b = edit.new(n)
        b.head, b.tail, b.parent = head, tail, parent
        return b
    root = bone("root", (0, 0, 0), (0, 0.3, 0))
    frame = bone("frame", (0, 0, 0.4), (0, 0.3, 0.4), root)
    top = bone("body", (0, 0, 0.7), (0, 0.3, 0.7), frame) if body else frame
    for tag, y in (("fr", 1.3), ("bk", -1.3)):
        diff = bone(("front" if tag == "fr" else "rear") + "_differential", (0, y, 0.35), (0, y + 0.1, 0.35), frame)
        for side, x in (("l", -0.8), ("r", 0.8)):          # forward +Y: left is -X
            if bare_rear and tag == "bk":
                bone("wheel_disc_%s_%s" % (tag, side), (x, y, 0.35), (x * 1.1, y, 0.35), diff)
                continue
            pivot = bone("axle_pivot_%s_%s" % (tag, side), (x * 0.8, y, 0.35), (x * 0.8, y, 0.45), diff)
            hub = bone("wheel_steering_%s_%s" % (tag, side), (x, y, 0.35), (x, y, 0.45), pivot) if steering and tag == "fr" else pivot
            bone("wheel_disc_%s_%s" % (tag, side), (x, y, 0.35), (x * 1.1, y, 0.35), hub)
            bone("shock_up_%s_%s" % (tag, side), (x * 0.7, y, 0.7), (x * 0.7, y, 0.8), top)
    bpy.ops.object.mode_set(mode='OBJECT')
    vehicle_rig.create(obj)
    return obj


v = car("car")
groups = {c.name: c for c in v.data.collections}
member = lambda n: next((g for g in rig_style.COLLECTIONS if g in groups and n in groups[g].bones), None)
check("vehicle collections", [n for n in rig_style.COLLECTIONS if n in groups], list(rig_style.COLLECTIONS))
for n, want in (("CR_Main", "Controls"), ("CR_Drive", "Controls"), ("CR_Steer", "Controls"), ("CR_Drift", "Controls"),
                ("CR_Body", "Controls"), ("CR_Settings", "Controls"), ("CR_Wheel_wheel_disc_fr_l", "Controls"),
                ("CR_Arch_wheel_disc_fr_l", "Secondary"), ("CR_Ground_wheel_disc_fr_l", "Mechanics"),
                ("CR_Lift_wheel_disc_fr_l", "Mechanics"), ("CR_Suspension", "Mechanics"), ("CR_Lean", "Mechanics"),
                ("CR_Body_Follow", "Mechanics"), ("wheel_disc_fr_l", "Game Bones"), ("frame", "Game Bones")):
    check("%s in %s" % (n, want), member(n), want)
pose = v.pose.bones
check("left wheel blue", tuple(pose["CR_Wheel_wheel_disc_fr_l"].color.custom.normal), rig_style.COLORS["L"])
check("right wheel red", tuple(pose["CR_Wheel_wheel_disc_fr_r"].color.custom.normal), rig_style.COLORS["R"])
check("main orange, thick", (tuple(pose["CR_Main"].color.custom.normal), pose["CR_Main"].custom_shape_wire_width),
      (rig_style.COLORS["main"], 3.5))
check("arch thin", pose["CR_Arch_wheel_disc_fr_l"].custom_shape_wire_width, 1.5)
check("wheel ring has a tick", pose["CR_Wheel_wheel_disc_fr_l"].custom_shape.name, "CR_CircleTick")
check("settings gear", pose["CR_Settings"].custom_shape.name, "CR_Gear")
check("settings defaults", tuple(pose["CR_Settings"][k] for k in ("auto_wheels", "auto_steer", "countersteer", "suspension", "lean")),
      (1.0, 1.0, 1.0, 1.0, 0.0))
check("no settings left on the object", any(k in v for k in ("auto_wheels", "lean")), False)
check("drawn in front", v.show_in_front, True)
# a setting turns its behaviour off: driving no longer spins the wheel
spin = lambda: pose["wheel_disc_fr_l"].matrix.to_euler()
bpy.context.view_layer.update()
rest = spin()
pose["CR_Drive"].location.y = 1.0
bpy.context.view_layer.update()
turned = (spin().to_matrix() @ rest.to_matrix().inverted()).to_quaternion().angle
pose["CR_Settings"]["auto_wheels"] = 0.0
bpy.context.view_layer.update()
still = (spin().to_matrix() @ rest.to_matrix().inverted()).to_quaternion().angle
check("driving spins the wheel", turned > 0.5, True)
check("Wheels Spin 0 stops it", still < 1e-3, True)
pose["CR_Drive"].location.y = 0.0
pose["CR_Settings"]["auto_wheels"] = 1.0
# arch still carries the body's corner parts
before = (v.matrix_world @ pose["shock_up_fr_l"].head).z
pose["CR_Arch_wheel_disc_fr_l"].location = (0.0, 0.1, 0.0)
bpy.context.view_layer.update()
check("arch lifts the shock top", abs((v.matrix_world @ pose["shock_up_fr_l"].head).z - before) > 0.05, True)
pose["CR_Arch_wheel_disc_fr_l"].location = (0.0, 0.0, 0.0)
# variants: no steering, no body, wheels without an arch
for label, kwargs in (("no steering", {"steering": False}), ("no body", {"body": False}), ("bare rear", {"bare_rear": True})):
    w = car("car_" + label.replace(" ", "_"), **kwargs)
    check("%s: rig made" % label, bool(w.data.get(vehicle_rig.KEY)), True)
    check("%s: settings" % label, "auto_wheels" in w.pose.bones["CR_Settings"], True)
    unplaced = [b.name for b in w.data.bones if not any(b.name in c.bones for c in w.data.collections if c.name in rig_style.COLLECTIONS)]
    check("%s: every bone in a kit collection" % label, unplaced, [])
```

(The arch's local up axis on the synthetic car is +Y of the CR_Arch bone, as built by `new(...)`: head-to-tail along the
wheel's outward side then aligned; if the check reports the shock didn't rise, set `location` along the axis
`axes[arch]["up"]` reports instead: print it once with `vehicle_rig` internals and adjust the test, not the rig.)

- [ ] **Step 2: Run to verify it fails**

Expected: FAIL on the collection checks (`Vehicle Controls` etc. today) and `KeyError: 'bpy_struct[key]' ... CR_Settings`.

- [ ] **Step 3: Implement**

3a. Imports at the top of `create()`: add `from . import rig_style`.

3b. Settings bone, in edit mode right after `main = new("Main", ...)` (line ~297):
```python
    settings_at = base + forward * (survey.nose - base.dot(forward) + length * 0.12) + left * width * 0.45
    new(rig_style.SETTINGS[len(PREFIX):], settings_at, settings_at + forward * length * 0.08, main)
```
(`new()` prefixes `CR_`, so this makes `CR_Settings`.)

3c. Replace the five `_property(...)` lines (today 364-368) with:
```python
    rig_style.add_settings(obj, [
        ("auto_wheels", 1.0, "The wheels spin as CR_Drive moves forward"),
        ("auto_steer", 1.0, "The front wheels and the steering wheel turn with CR_Steer"),
        ("countersteer", 1.0, "The front wheels turn against CR_Drift, pointing where the vehicle drives"),
        ("suspension", 1.0, "The vehicle rises, pitches and rolls with its wheels (lifted, on the ground)"),
        ("lean", 0.0, "The body rolls out of a turn as CR_Steer turns")])
```
and delete `_property`. Replace every `_driven(obj, con, "<setting>")` in `create()` with
`rig_style.driven(obj, con, "<setting>")`, and drop `_driven` from the `creature_rig` import.

3d. `_drive_channel`: the 2-tuple branch reads the settings bone:
```python
        else:
            var.type = 'SINGLE_PROP'
            var.targets[0].id = obj
            var.targets[0].data_path = rig_style.setting_path(variable[1])
```
with `from . import rig_style` at module top (beside the other imports), and the docstring's "(name, property) for a
custom property of the armature object" becoming "(name, setting) for one of the rig's settings".

3e. Replace the styling block (from `# Shown: controls, wheel rings, parts that move.` through the end of the
`for name in armature.bones.keys():` loop) with:
```python
    # Kit collections: the controls shown, the mechanism and the game's skeleton hidden.
    rig_style.collections(armature)
    centre = survey.centre

    def place(name, group, role, shape=None, scale=None, secondary=False):
        bone = pose[name]
        if shape is not None:
            bone.custom_shape = rig_shapes.ensure(shape)
            bone.use_custom_shape_bone_size = False
        if scale is not None:
            bone.custom_shape_scale_xyz = scale
        rig_style.style(bone, role, secondary=secondary)
        rig_style.assign(armature, name, group)

    main = pose[PREFIX + "Main"]
    main.custom_shape = rig_shapes.footprint(PREFIX + "Footprint_" + obj.name, length * 1.03, width * 1.03)
    main.use_custom_shape_bone_size = False
    main.custom_shape_scale_xyz = (1.0, 1.0, 1.0)
    align_shape(obj, main, x=-left, y=forward, z=up)
    rig_shapes.place(obj, main, centre)
    place(PREFIX + "Main", "Controls", "main")
    place(names["drive"], "Controls", "C", "CR_Arrow", (width * 0.9, length * 0.3, 1.0))
    align_shape(obj, pose[names["drive"]], x=-left, y=forward, z=up)       # arrow on the ground in front of the nose
    rig_shapes.place(obj, pose[names["drive"]], centre + forward * (survey.nose - centre.dot(forward) + length * 0.06))
    radius = reach + length * 0.08
    place(names["drift"], "Controls", "C", "CR_Swing", (radius,) * 3)
    align_shape(obj, pose[names["drift"]], x=left, y=-forward, z=up)       # arc behind the vehicle, about the front axle
    rig_shapes.place(obj, pose[names["drift"]], pivot + up * height * 0.2)
    if names["steer"]:
        # arc around the front axle past the nose at bonnet height, arrows at its ends
        radius = max(survey.nose - pivot.dot(forward) + length * 0.04, width * 0.62)
        place(names["steer"], "Controls", "C", "CR_Turn", (radius,) * 3)
        align_shape(obj, pose[names["steer"]], x=-left, y=forward, z=up)
        rig_shapes.place(obj, pose[names["steer"]], pivot + up * height * 0.45)
    if names["body"]:
        place(names["body"], "Controls", "C", "CR_Square", (length * 0.45, width * 0.55, 1.0), secondary=True)
        align_shape(obj, pose[names["body"]], x=forward, y=left, z=up)
        rig_shapes.place(obj, pose[names["body"]], centre + up * height * 1.15)
    settings = pose[rig_style.SETTINGS]
    place(rig_style.SETTINGS, "Controls", "settings", "CR_Gear", (width * 0.12,) * 3)
    align_shape(obj, settings, x=-left, y=forward, z=up)
    settings.lock_location = settings.lock_rotation = settings.lock_scale = (True, True, True)
    for name, (control, sensor, lift, out, arch) in controls.items():
        role = "L" if out > 0 else "R"
        if arch:
            place(arch, "Secondary", role, "CR_UpDown", (radii[name] * 0.68,) * 3, secondary=True)
            align_shape(obj, pose[arch], x=forward, y=up)
        # ring with a spin tick on the wheel's outer side
        ring_radius = radii[name] * 1.15
        place(control, "Controls", role, "CR_CircleTick", (ring_radius,) * 3)
        align_shape(obj, pose[control], x=forward, y=up, z=left * out)
        at = armature.bones[control].head_local
        side = abs((at - centre).dot(left))
        outside = width * 0.5 - side if side > width * 0.1 else 0.0
        rig_shapes.place(obj, pose[control], at + left * out * max(outside, 0.0))
    for name in armature.bones.keys():
        if any(name in armature.collections[g].bones for g in rig_style.COLLECTIONS):
            continue
        if name.startswith(PREFIX):
            rig_style.assign(armature, name, "Mechanics")
        elif name in parts:
            # box around what the part moves
            points = parts[name]
            along = [p.dot(forward) for p in points]
            across = [p.dot(left) for p in points]
            high = [p.z for p in points]
            middle = (forward * (max(along) + min(along)) + left * (max(across) + min(across))) / 2.0
            middle.z = (max(high) + min(high)) / 2.0
            place(name, "Secondary", rig_style.side_of(middle, centre, left, width), "CR_Box",
                  tuple(max(hi - lo, 0.04) * 0.55 for lo, hi in (
                      (min(along), max(along)), (min(across), max(across)), (min(high), max(high)))), secondary=True)
            align_shape(obj, pose[name], x=forward, y=left, z=up)
            rig_shapes.place(obj, pose[name], middle)
        else:
            rig_style.assign(armature, name, "Game Bones")
    obj.show_in_front = True            # controls inside the body stay clickable
```
Delete `COLORS` (lines 45-47) and the `sized` import if no longer used in `create()` (it stays imported only if a
remaining call uses it; `grep -n "sized(" vehicle_rig.py` must be empty to drop it).

Notes for the implementer:
- `align_shape(obj, pose_bone, x, y, z)` (creature_rig) rotates the shape so its axes point along the given armature-space
  vectors; the ring lies in its XY plane with the tick at +Y, so `x=forward, y=up, z=left*out` stands it on the wheel's
  outer face, tick up.
- The CTRL_ shapes' native scales (`sized`) no longer apply: every shape here is unit size, so `custom_shape_scale_xyz`
  is the size in metres (half-size for squares and boxes, radius for circles).
- `CR_Square`'s half-size `(length * 0.45, width * 0.55)` matches today's slab footprint (`CTRL_Box` at `length*4.5` with
  native 0.1).

- [ ] **Step 4: Run to verify it passes**

Expected: `[rig_check] N passed, 0 failed` with N = 33 + the vehicle checks (count printed). Also run the arch probe on a
real car (Task 5) later.

- [ ] **Step 5: Commit**

Propose `Build the vehicle rig on the rig kit`; after the user's OK:
```bash
git add plugins/Blender/fortnite_porting/processing/context/vehicle_rig.py tests/plugin/rig_check.py
git commit -m "Build the vehicle rig on the rig kit"
```

---

### Task 4: The Rig panel and its operators (`operator/rig_ui.py`)

**Files:**
- Create: `plugins/Blender/fortnite_porting/operator/rig_ui.py` (operators + the panel, moved from convert_op.py)
- Modify: `plugins/Blender/fortnite_porting/operator/convert_op.py`: delete `FPMP_PT_CreatureRig` (lines 241-276) and its
  entry in `classes`; in `register()`/`unregister()` call `rig_ui.register()` / `rig_ui.unregister()`.
- Test: `tests/plugin/rig_check.py` (extend)

**Interfaces:**
- Consumes: `rig_style.controls`, `rig_style.settings_owner`, `rig_style.COLLECTIONS`.
- Produces: operators `fpmp.rig_select_controls`, `fpmp.rig_reset_pose` (property `scope`: 'SELECTED' | 'ALL');
  panel `FPMP_PT_creature_rig` (same idname as before).

- [ ] **Step 1: Write the failing tests**

Append to `tests/plugin/rig_check.py`, before the summary:

```python
# panel operators
from fpmp_baseline.operator import rig_ui  # noqa: E402
rig_ui.register()
bpy.context.view_layer.objects.active = v
bpy.ops.object.mode_set(mode='POSE')
check("select controls", bpy.ops.fpmp.rig_select_controls(), {'FINISHED'})
chosen = sorted(b.name for b in v.data.bones if b.select)
check("selected exactly the controls", chosen, sorted(b.name for b in rig_style.controls(v)))
for b in v.pose.bones:
    b.location = (0.1, 0.2, 0.3)
    b.scale = (2.0, 2.0, 2.0)
pose["CR_Main"].rotation_mode = 'QUATERNION'
pose["CR_Main"].rotation_quaternion = (0.7, 0.7, 0.0, 0.0)
pose["CR_Drive"].rotation_mode = 'AXIS_ANGLE'
pose["CR_Drive"].rotation_axis_angle = (0.5, 0.0, 0.0, 1.0)
pose["CR_Steer"].rotation_euler = (0.0, 0.4, 0.0)
check("reset all", bpy.ops.fpmp.rig_reset_pose(scope='ALL'), {'FINISHED'})
check("controls back at rest", all(tuple(b.location) == (0, 0, 0) and tuple(b.scale) == (1, 1, 1) for b in rig_style.controls(v)), True)
check("quaternion reset", tuple(pose["CR_Main"].rotation_quaternion), (1.0, 0.0, 0.0, 0.0))
check("axis-angle reset", tuple(pose["CR_Drive"].rotation_axis_angle), (0.0, 0.0, 1.0, 0.0))
check("euler reset", tuple(pose["CR_Steer"].rotation_euler), (0.0, 0.0, 0.0))
check("game bones untouched", tuple(pose["frame"].location), (0.1, 0.2, 0.3))
for b in rig_style.controls(v):
    b.location = (0.0, 0.5, 0.0)
for b in v.data.bones:
    b.select = b.name == "CR_Drive"
bpy.ops.fpmp.rig_reset_pose(scope='SELECTED')
check("reset selected only", (tuple(pose["CR_Drive"].location), tuple(pose["CR_Main"].location)), ((0, 0, 0), (0.0, 0.5, 0.0)))
bpy.ops.object.mode_set(mode='OBJECT')
# a rig built before the kit: settings on the object, no settings bone
old = bpy.data.objects.new("old_rig", bpy.data.armatures.new("old_rig"))
old.data["is_vehicle_rig"] = True
old["auto_wheels"] = 1.0
owner, path = rig_style.settings_owner(old)
check("old rig's settings on the object", (owner == old, path % "auto_wheels"), (True, '["auto_wheels"]'))
```

- [ ] **Step 2: Run to verify it fails**

Expected: FAIL, `ImportError: cannot import name 'rig_ui'`.

- [ ] **Step 3: Implement `operator/rig_ui.py`**

```python
"""The Rig panel (sidebar, Fortnite Porting tab) for the fork's rigs: show or hide each control group, select or
reset the controls, the rig's settings."""
import bpy

from ..processing.context import rig_style

VEHICLE_SETTINGS = (("auto_wheels", "Wheels Spin"), ("auto_steer", "Wheels Steer"), ("countersteer", "Counter-steer"),
                    ("suspension", "Body Follows Wheels"), ("lean", "Lean in Turns"))
OLD_GROUPS = ("Vehicle Controls", "Vehicle Wheel Controls", "Vehicle Parts", "Vehicle Wheels", "Vehicle Other")


def _rig(context):
    obj = context.active_object
    return obj if obj is not None and obj.type == 'ARMATURE' else None


class FPMP_OT_RigSelectControls(bpy.types.Operator):
    """Select every control of the rig (its Controls and Secondary groups)"""
    bl_idname = "fpmp.rig_select_controls"
    bl_label = "Select Controls"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _rig(context) is not None

    def execute(self, context):
        obj = _rig(context)
        chosen = {b.name for b in rig_style.controls(obj)}
        for bone in obj.data.bones:
            bone.select = bone.name in chosen
        return {'FINISHED'}


class FPMP_OT_RigResetPose(bpy.types.Operator):
    """Put the rig's controls back at rest (never its mechanism or the game's bones)"""
    bl_idname = "fpmp.rig_reset_pose"
    bl_label = "Reset Pose"
    bl_options = {'REGISTER', 'UNDO'}
    scope: bpy.props.EnumProperty(items=[('SELECTED', "Selected", "The selected controls"),
                                         ('ALL', "All", "Every control")], default='SELECTED')

    @classmethod
    def poll(cls, context):
        return _rig(context) is not None

    def execute(self, context):
        for bone in rig_style.controls(_rig(context)):
            if self.scope == 'SELECTED' and not bone.bone.select:
                continue
            bone.location = (0.0, 0.0, 0.0)
            bone.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
            bone.rotation_euler = (0.0, 0.0, 0.0)
            bone.rotation_axis_angle = (0.0, 0.0, 1.0, 0.0)
            bone.scale = (1.0, 1.0, 1.0)
        return {'FINISHED'}


class FPMP_PT_CreatureRig(bpy.types.Panel):
    bl_label = "Rig"
    bl_idname = "FPMP_PT_creature_rig"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Fortnite Porting"

    @classmethod
    def poll(cls, context):
        return _rig(context) is not None

    def draw(self, context):
        obj = _rig(context)
        data = obj.data
        col = self.layout.column(align=True)
        if not any(data.get(k) for k in ("is_vehicle_rig", "is_creature_rig", "is_lego_rig")):
            col.operator("fpmp.creature_rig")
            col.operator("fpmp.vehicle_rig")
            col.operator("fpmp.lego_rig")
            return
        groups = [c for c in data.collections if c.name in rig_style.COLLECTIONS + OLD_GROUPS]
        grid = col.grid_flow(columns=2, align=True)
        for group in groups:
            grid.prop(group, "is_visible", text=group.name, toggle=True, icon='HIDE_OFF' if group.is_visible else 'HIDE_ON')
        row = col.row(align=True)
        row.operator(FPMP_OT_RigSelectControls.bl_idname, text="Select Controls")
        row = col.row(align=True)
        row.operator(FPMP_OT_RigResetPose.bl_idname, text="Reset Selected").scope = 'SELECTED'
        row.operator(FPMP_OT_RigResetPose.bl_idname, text="Reset All").scope = 'ALL'
        owner, path = rig_style.settings_owner(obj)
        col.separator()
        if data.get("is_vehicle_rig"):
            col.prop(obj, "fpmp_ground", text="Ground")
            for key, text in VEHICLE_SETTINGS:
                if key in owner:          # rigs made before a setting existed lack it
                    col.prop(owner, '["%s"]' % key, text=text, slider=True)
            return
        if data.get("is_lego_rig"):
            return
        for key in sorted(k for k in owner.keys() if k.startswith("ik_")):
            col.prop(owner, '["%s"]' % key, text=key[3:], slider=True)
        if "eyes_aim" in owner:
            col.prop(owner, '["eyes_aim"]', text="Eyes Aim", slider=True)


classes = (FPMP_OT_RigSelectControls, FPMP_OT_RigResetPose, FPMP_PT_CreatureRig)


def register():
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
```

In `convert_op.py`: delete the `FPMP_PT_CreatureRig` class and its name in `classes`; in `register()` after the
`for c in classes` loop add `rig_ui.register()`, in `unregister()` before unregistering `classes` add
`rig_ui.unregister()`, importing it as `from . import rig_ui` inside both functions (as `vehicle_rig` is).

- [ ] **Step 4: Run to verify it passes**

Expected: `[rig_check] N passed, 0 failed` (all previous + 10).

- [ ] **Step 5: Commit**

Propose `Add show/hide, select and reset to the Rig panel`; after the user's OK:
```bash
git add plugins/Blender/fortnite_porting/operator/rig_ui.py plugins/Blender/fortnite_porting/operator/convert_op.py tests/plugin/rig_check.py
git commit -m "Add show/hide, select and reset to the Rig panel"
```

---

### Task 5: Real cars, renders, suites

**Files:**
- Modify: `fpfork-private/devtools/baseline.py` (run `rig_check.py` with the suites: parse `[rig_check] N passed, M failed`
  like subsurface_check)
- No product code (a fix found here goes back to the owning task's file with a test first).

- [ ] **Step 1: Real cars**

```bash
cd C:/Users/kyooc/Documents/Claude/fpfork-private/devtools
python fpdev.py status          # start with: bash testapp.sh test --no-build
python fpdev.py import car && python fpdev.py import car2
python fpdev.py script car <scratchpad>/arch_check.py   # Expected: ARCHCHECK PASS
python fpdev.py script car2 <scratchpad>/arch_check.py  # Expected: ARCHCHECK PASS
python fpdev.py render car --controls front,top,side
python fpdev.py render car2 --controls front,top,side
```
Look at both sheets: four colours read apart (orange main, yellow centre controls, blue left / red right wheels, purple
gear), ticks on the wheel rings, thinner arches, the body square over the roof, nothing hidden inside the body.
Send the user the before (out/fpdev/car/sheet.png from before Task 3, kept as `sheet_before.png`) and after sheets.

- [ ] **Step 2: Suites**

Add `rig_check.py` to `baseline.py`'s suites (tuple in `capture_tests`, a `parse_rig` like `parse_subsurface`, the
result in `tests.json` and the summary line). Run translator_test, layout_check, subsurface_check, rig_check headless.
Expected: all pass.

- [ ] **Step 3: Commit (private repo)**

Propose `Run the rig check with the suites`; after the user's OK, commit `devtools/baseline.py` in fpfork-private with
`-c user.name=kyoocreatives -c user.email=280023218+kyoocreatives@users.noreply.github.com`.
