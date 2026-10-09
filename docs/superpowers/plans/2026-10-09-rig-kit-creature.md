# Creature Rig on the Rig Kit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The creature rig styled, grouped and set up with the rig kit, plus the kit's optional FK group.

**Architecture:** `rig_style.collections` gains `fk=True` (a hidden FK group); `rig_shapes` gains a circle-with-chevron;
`creature_rig.create()` keeps its survey and constraints but adds a CR_Settings bone and replaces its styling block with
kit calls. A synthetic quadruped in `tests/plugin/rig_check.py` drives TDD; the Wolf and a LEGO animal are checked through fpdev.

**Tech Stack:** Blender 5.2 Python (bpy), the fork's Blender plugin, headless Blender tests.

**Spec:** `docs/superpowers/specs/2026-10-09-rig-kit-creature-design.md` (kit: `docs/superpowers/specs/2026-10-09-rig-kit-vehicle-design.md`)

## Global Constraints

- Kit colours/widths as in `rig_style.py` (left blue, right red, centre yellow, main orange, settings purple; widths 3.5 / 2.5 / 1.5).
- Collections with FK: Controls (shown), Secondary (shown), FK (hidden), Mechanics (hidden), Game Bones (hidden), in that order.
- Settings on `CR_Settings`: `ik_<limb>` per IK limb (default 1, "IK on this limb (0: FK, as an animation plays it)"),
  `eyes_aim` (default 1, "The eyes look at CR_Eyes") only when the creature has eyes.
- Side from the bone name (`creature_rig.side_of`); sizes creature-relative (`h * factor`); kit shapes are unit size
  (scale = radius or half-size in metres).
- Older creature rigs keep working (panel reads their settings from the object).
- Fork code habits; commit messages approved by the user first; no Co-Authored-By trailer.

## Review Focus

- A creature with no IK limbs at all (a fish, a snake): no FK group, no `ik_` settings, CR_Settings still made.
- A creature with no eyes: no `eyes_aim`, no CR_Eyes; panel draws without it.
- A limb whose bones don't meet (helper chain CR_MCH_ / CR_Follow_): those go to Mechanics, IK still switches off at 0.
- An arm with a hand (IK box) on a biped creature (raptor): hand IK control is a box, side-coloured.
- LEGO animals (tiny scale, centimetre bones): shapes still visible (sizes stay height-relative).

Task 2's synthetic quadruped covers eyes/no-eyes and leg IK; the no-IK and LEGO cases are checked on real assets in Task 3.

---

### Task 1: Kit FK group and the root shape

**Files:**
- Modify: `plugins/Blender/fortnite_porting/processing/context/rig_style.py` (`collections`, `controls`, add `FK`)
- Modify: `plugins/Blender/fortnite_porting/processing/context/rig_shapes.py` (add `CR_CircleArrow`)
- Modify: `plugins/Blender/fortnite_porting/operator/rig_ui.py` (toggle list includes FK)
- Test: `tests/plugin/rig_check.py` (extend)

**Interfaces:**
- Produces: `rig_style.FK = "FK"`; `rig_style.collections(armature, fk=False)`; `controls()` also returns bones in FK;
  shape `CR_CircleArrow` (unit circle plus a chevron at +Y beyond it, XY plane).

- [ ] **Step 1: Write the failing tests** (append before the summary lines of `rig_check.py`)

```python
# kit: optional FK group
fk_rig = bpy.data.objects.new("fk_rig", bpy.data.armatures.new("fk_rig"))
bpy.context.scene.collection.objects.link(fk_rig)
fk_groups = rig_style.collections(fk_rig.data, fk=True)
check("fk order", [c.name for c in fk_rig.data.collections], ["Controls", "Secondary", "FK", "Mechanics", "Game Bones"])
check("fk hidden", fk_groups["FK"].is_visible, False)
check("vehicle has no fk", "FK" in v.data.collections, False)
arrow = rig_shapes.ensure("CR_CircleArrow")
check("circle arrow points ahead", max(p.co.y for p in arrow.data.vertices) > 1.1, True)
```

- [ ] **Step 2: Run, expect FAIL** — `TypeError: collections() got an unexpected keyword argument 'fk'`.

Run (as before): copy the plugin with `baseline.copy_plugin`, then
`blender -b --factory-startup --python-exit-code 1 -P tests/plugin/rig_check.py -- <devtools>/out/baseline-plugin`
with isolated BLENDER_USER_* folders.

- [ ] **Step 3: Implement**

`rig_style.py`:
```python
FK = "FK"         # an IK rig's FK bones: hidden until wanted
```
(below `COLLECTIONS`), and `collections` becomes:
```python
def collections(armature, fk=False):
    """The kit's collections, in order, shown or hidden as the convention says; the game's own are hidden.
    `fk`: also the FK group (rigs with IK), between Secondary and Mechanics."""
    names = COLLECTIONS[:2] + ((FK,) if fk else ()) + COLLECTIONS[2:]
    for collection in armature.collections:
        if collection.name not in names:
            collection.is_visible = False
    found = {}
    for index, name in enumerate(names):
        collection = armature.collections.get(name) or armature.collections.new(name)
        collection.is_visible = SHOWN.get(name, False)
        armature.collections.move(armature.collections.find(name), index)
        found[name] = collection
    return found
```
`assign` must also clear FK: replace `for name in COLLECTIONS:` with `for name in COLLECTIONS + (FK,):`.
`controls`: `COLLECTIONS[:2] + (FK,) + LEGACY_CONTROLS`, docstring "(Controls, Secondary, FK, or an older rig's control groups)".

`rig_shapes.py`, before `SHAPES`:
```python
def _circle_arrow():
    """Circle with a chevron ahead of it (+Y): a ground control that shows which way the rig faces."""
    verts, edges = _circle()
    verts += [(-0.18, 1.12, 0.0), (0.0, 1.3, 0.0), (0.18, 1.12, 0.0)]
    n = len(verts)
    edges += [(n - 3, n - 2), (n - 2, n - 1)]
    return verts, edges
```
and add `"CR_CircleArrow": _circle_arrow` to `SHAPES`.

`rig_ui.py`: the toggle filter becomes `rig_style.COLLECTIONS + (rig_style.FK,) + rig_style.LEGACY`.

- [ ] **Step 4: Run, expect PASS** — all previous checks plus 4.

- [ ] **Step 5: Commit** — propose `Add the kit's FK group`; after the user's OK commit the three files and the test.

---

### Task 2: The creature rig on the kit

**Files:**
- Modify: `plugins/Blender/fortnite_porting/processing/context/creature_rig.py`:
  - module top: `from . import rig_shapes, rig_style`;
  - `create()`: drop `ensure_blend_data` (no CTRL_ shapes used any more), add the CR_Settings bone (edit mode, before
    `bpy.ops.object.mode_set(mode='POSE')`), replace the styling block (from `ours = ("Creature Controls", ...` to the end
    of the `if eyes:` block) with the code below;
  - delete `_palette` and `_driven` (no other users; `sized`, `align_shape`, `NATIVE` stay: the LEGO rig uses them).
- Test: `tests/plugin/rig_check.py` (extend: synthetic quadruped)

**Interfaces:**
- Consumes: Task 1 (`collections(fk=)`, `FK`, `CR_CircleArrow`), the kit (`style`, `assign`, `add_settings`, `driven`, `SETTINGS`).
- Produces: creature rigs with CR_Settings (`ik_<limb>`, `eyes_aim`), kit collections (+FK when there is IK), kit styling, `show_in_front`.

- [ ] **Step 1: Write the failing tests** (append before the summary)

```python
# the creature rig on a synthetic quadruped (facing -Y, as UE)
from fpmp_baseline.processing.context import creature_rig  # noqa: E402


def quadruped(name, eyes=True):
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
    root = bone("root", (0, 0, 0), (0, 0.1, 0))
    pelvis = bone("pelvis", (0, 0.4, 0.6), (0, 0.2, 0.6), root)
    spine1 = bone("spine_01", (0, 0.2, 0.6), (0, 0.0, 0.62), pelvis)
    spine2 = bone("spine_02", (0, 0.0, 0.62), (0, -0.3, 0.65), spine1)
    neck = bone("neck", (0, -0.3, 0.65), (0, -0.45, 0.8), spine2)
    head = bone("head", (0, -0.45, 0.8), (0, -0.6, 0.85), neck)
    bone("jaw", (0, -0.5, 0.75), (0, -0.65, 0.72), head)
    if eyes:
        for side, x in (("l", -0.05), ("r", 0.05)):
            bone("eye_" + side, (x, -0.58, 0.85), (x, -0.62, 0.85), head)
    for side, x in (("l", -0.12), ("r", 0.12)):
        for prefix, y, parent in (("", 0.35, pelvis), ("f", -0.2, spine2)):
            thigh = bone(prefix + "thigh_" + side, (x, y, 0.55), (x, y - 0.05, 0.3), parent)
            calf = bone(prefix + "calf_" + side, (x, y - 0.05, 0.3), (x, y, 0.08), thigh)
            foot = bone(("paw_" if prefix else "foot_") + side, (x, y, 0.08), (x, y - 0.05, 0.02), calf)
            bone(prefix + "toe_" + side, (x, y - 0.05, 0.02), (x, y - 0.1, 0.0), foot)
    t1 = bone("tail_01", (0, 0.4, 0.6), (0, 0.6, 0.55), pelvis)
    t2 = bone("tail_02", (0, 0.6, 0.55), (0, 0.8, 0.5), t1)
    bone("tail_03", (0, 0.8, 0.5), (0, 1.0, 0.45), t2)
    bpy.ops.object.mode_set(mode='OBJECT')
    creature_rig.create(obj)
    return obj


q = quadruped("quad")
qp = q.pose.bones
qg = {c.name: c for c in q.data.collections}
in_group = lambda n: next((g for g in rig_style.COLLECTIONS[:2] + (rig_style.FK,) + rig_style.COLLECTIONS[2:] if g in qg and n in qg[g].bones), None)
check("creature collections", [c.name for c in q.data.collections if c.name in qg and c.name in
      rig_style.COLLECTIONS + (rig_style.FK,)], ["Controls", "Secondary", "FK", "Mechanics", "Game Bones"])
check("creature visibility", [qg[n].is_visible for n in ("Controls", "Secondary", "FK", "Mechanics", "Game Bones")],
      [True, True, False, False, False])
for n, want in (("root", "Controls"), ("pelvis", "Controls"), ("spine_01", "Controls"), ("head", "Controls"),
                ("tail_02", "Controls"), ("CR_IK_foot_l", "Controls"), ("CR_Pole_thigh_l", "Controls"),
                ("CR_Eyes", "Controls"), ("CR_Settings", "Controls"), ("jaw", "Secondary"), ("toe_l", "Secondary"),
                ("thigh_l", "FK"), ("calf_l", "FK"), ("foot_l", "FK"), ("CR_Eye_eye_l", "Mechanics")):
    check("%s in %s" % (n, want), in_group(n), want)
check("root is main", tuple(qp["root"].color.custom.normal), rig_style.COLORS["main"], tol=0.01)
check("root shape", qp["root"].custom_shape.name, "CR_CircleArrow")
check("left foot IK blue", tuple(qp["CR_IK_foot_l"].color.custom.normal), rig_style.COLORS["L"], tol=0.01)
check("right pole red", tuple(qp["CR_Pole_thigh_r"].color.custom.normal), rig_style.COLORS["R"], tol=0.01)
check("pole is a diamond", qp["CR_Pole_thigh_r"].custom_shape.name, "CR_Diamond")
check("spine centre yellow", tuple(qp["spine_01"].color.custom.normal), rig_style.COLORS["C"], tol=0.01)
check("face thin", qp["jaw"].custom_shape_wire_width, 1.5)
check("creature settings", sorted(k for k in qp["CR_Settings"].keys() if not k.startswith("_")),
      ["eyes_aim", "ik_fthigh_l", "ik_fthigh_r", "ik_thigh_l", "ik_thigh_r"])
check("no settings left on the object", any(k.startswith("ik_") or k == "eyes_aim" for k in q.keys()), False)
check("creature drawn in front", q.show_in_front, True)
# IK at 0 lets the foot go: moving the IK target no longer moves the foot
bpy.context.view_layer.update()
rest = (q.matrix_world @ qp["foot_l"].head).copy()
qp["CR_IK_foot_l"].location = (0.0, 0.0, 0.1)
bpy.context.view_layer.update()
followed = ((q.matrix_world @ qp["foot_l"].head) - rest).length
qp["CR_Settings"]["ik_thigh_l"] = 0.0
q.update_tag()
bpy.context.view_layer.update()
left_alone = ((q.matrix_world @ qp["foot_l"].head) - rest).length
check("IK moves the foot", followed > 0.02, True)
check("IK 0 lets it go", left_alone < 1e-3, True)
qp["CR_IK_foot_l"].location = (0.0, 0.0, 0.0)
qp["CR_Settings"]["ik_thigh_l"] = 1.0
q2 = quadruped("quad_no_eyes", eyes=False)
check("no eyes: no eyes_aim", "eyes_aim" in q2.pose.bones["CR_Settings"], False)
```

(The leg IK limbs are named after their first chain bone: `thigh_l`, `fthigh_l`; feet are `foot_l` / `paw_l`. If the
survey names them otherwise, print `[l.name for l in creature_rig.Survey(...).limbs]` once and fix the test's names,
not the rig.)

- [ ] **Step 2: Run, expect FAIL** on the collection checks (`Creature Controls` today) and missing CR_Settings.

- [ ] **Step 3: Implement**

3a. Module top: add `from . import rig_shapes, rig_style` after the `mathutils` import; in `create()` delete
`from ...utils import ensure_blend_data` and the `ensure_blend_data()` call, and the local `from . import rig_shapes`.

3b. CR_Settings bone: right before `bpy.ops.object.mode_set(mode='POSE')` (after the eyes' edit bones):
```python
    # settings gear on the ground beside the left flank, halfway along
    left = Vector((0.0, 0.0, 1.0)).cross(ahead)
    usable = [b for b in edit if not HELPER.search(b.name) and not b.name.startswith(PREFIX)]
    across = max((abs(b.head.dot(left)) for b in usable), default=survey.height * 0.2)
    along = [b.head.dot(ahead) for b in usable] or [0.0]
    spot = ahead * (max(along) + min(along)) / 2.0 + left * (across + survey.height * 0.25)
    spot.z = survey.ground
    gear = edit.new(rig_style.SETTINGS)
    gear.head, gear.tail = spot, spot + ahead * survey.height * 0.1
    gear.parent, gear.use_deform = edit.get(survey.root), False
```

3c. Replace the styling block (from `ours = ("Creature Controls", ...` through the end of the `if eyes:` block, just
before `armature[KEY] = True`) with:
```python
    rig_style.collections(armature, fk=bool(legs))
    pose = obj.pose.bones
    h = survey.height
    up = Vector((0.0, 0.0, 1.0))
    forward = ahead
    RING = (pi / 2.0, 0.0, 0.0)            # a kit circle lies in XY; this stands it around the bone's Y axis

    def show(name, group, shape, size, role=None, secondary=False, ring=False):
        """A control: unit kit shape at `size` metres (radius or half-size), the kit's colour and width, its group."""
        if name not in pose:
            return
        bone = pose[name]
        bone.custom_shape = rig_shapes.ensure(shape)
        bone.use_custom_shape_bone_size = False
        bone.custom_shape_scale_xyz = (size, size, size)
        if ring:
            bone.custom_shape_rotation_euler = RING
        rig_style.style(bone, role or side_of(name), secondary=secondary)
        rig_style.assign(armature, name, group)

    if survey.root:
        show(survey.root, "Controls", "CR_CircleArrow", h * 0.65, role="main")
        align_shape(obj, pose[survey.root], y=forward, z=up)        # flat on the ground, chevron ahead
    for name in survey.spine:
        show(name, "Controls", "CR_Circle", h * (0.15 if name != survey.head else 0.11), ring=True)
    if survey.pelvis in pose:
        show(survey.pelvis, "Controls", "CR_Square", h * 0.22)
        align_shape(obj, pose[survey.pelvis], y=forward, z=up)
    # Bones between the spine and a limb or the tail (e.g. a hips bone the rear legs hang from).
    spine = set(survey.spine)
    for root in [l.bones[0] for l in survey.limbs if l.kind != "face"] + survey.tail[:1]:
        at = armature.bones[root].parent
        while at is not None and at.name not in spine and side_of(at.name) == "C":
            show(at.name, "Controls", "CR_Circle", h * 0.15, ring=True)
            at = at.parent
    for i, name in enumerate(survey.tail):
        show(name, "Controls", "CR_Circle", h * max(0.07 - 0.006 * i, 0.03), ring=True)
    ik_driven = {n for leg in legs for n in leg.chain + [leg.foot]}
    for limb in survey.limbs:
        for name in limb.bones:
            if name in ik_driven:
                show(name, rig_style.FK, "CR_Circle", h * (0.035 if name == limb.foot else 0.055), ring=True)
            elif limb.kind == "face":
                show(name, "Secondary", "CR_Diamond", h * 0.018, secondary=True)
            elif limb.kind == "wing":
                show(name, "Secondary", "CR_Circle", h * 0.055, secondary=True, ring=True)
            elif limb.foot and limb.bones.index(name) > limb.bones.index(limb.foot):
                # past the foot or hand: toes, claws, fingers
                show(name, "Secondary", "CR_Circle", h * 0.02, secondary=True, ring=True)
            else:
                show(name, "Controls", "CR_Circle", h * (0.035 if name == limb.foot or name in limb.bones[-2:] else 0.055), ring=True)
        if limb.kind != "face":
            # rest of the limb: fingers, toes, scapula
            for child in armature.bones[limb.bones[0]].children_recursive:
                if child.name not in limb.bones and not HELPER.search(child.name):
                    show(child.name, "Secondary", "CR_Circle", h * 0.02, secondary=True, ring=True)
    # Centre bones off the spine not shown yet (a fish's body behind its head).
    placed = lambda n: any(n in armature.collections[g].bones for g in armature.collections.keys()
                           if g in rig_style.COLLECTIONS + (rig_style.FK,))
    if survey.pelvis:
        head_bones = ({c.name for c in armature.bones[survey.head].children_recursive} | {survey.head}) if survey.head else set()
        for bone in armature.bones[survey.pelvis].children_recursive:
            if not placed(bone.name) and bone.name not in head_bones and side_of(bone.name) == "C" \
                    and bone.use_deform and not HELPER.search(bone.name):
                show(bone.name, "Controls", "CR_Circle", h * 0.1, ring=True)
    if survey.head:
        for child in armature.bones[survey.head].children_recursive:
            if placed(child.name) or HELPER.search(child.name) or child.name.startswith(PREFIX):
                continue
            show(child.name, "Secondary", "CR_Diamond", h * 0.018, secondary=True)

    settings = [("ik_" + leg.name, 1.0, "IK on this limb (0: FK, as an animation plays it)") for leg in legs]
    if eyes:
        settings.append(("eyes_aim", 1.0, "The eyes look at CR_Eyes"))
    rig_style.add_settings(obj, settings)
    show(rig_style.SETTINGS, "Controls", "CR_Gear", h * 0.08, role="settings")
    align_shape(obj, pose[rig_style.SETTINGS], y=forward, z=up)
    pose[rig_style.SETTINGS].lock_location = pose[rig_style.SETTINGS].lock_rotation = (True, True, True)
    pose[rig_style.SETTINGS].lock_scale = (True, True, True)
    for leg in legs:
        prop = "ik_" + leg.name
        target_name, pole_name = PREFIX + "IK_" + leg.foot, PREFIX + "Pole_" + leg.name
        if leg.kind == "arm":
            show(target_name, "Controls", "CR_Box", h * 0.03, role=side_of(leg.foot))
        else:
            # footprint on the ground under the foot, facing the creature's direction
            show(target_name, "Controls", "CR_Foot", h * 0.3, role=side_of(leg.foot))
            align_shape(obj, pose[target_name], y=forward, z=up)
            foot = armature.bones[leg.foot].head_local
            rig_shapes.place(obj, pose[target_name], Vector((foot.x, foot.y, survey.ground)))
        show(pole_name, "Controls", "CR_Diamond", h * 0.03, role=side_of(leg.foot))
        if leg.helpers:
            # IK on the helper chain (its tail is the foot's head); the real bones follow it
            ik = pose[leg.helpers[-1]].constraints.new('IK')
            ik.name = "CR IK"
            ik.target, ik.subtarget = obj, target_name
            ik.pole_target, ik.pole_subtarget = obj, pole_name
            ik.pole_angle = poles[leg.name]
            ik.chain_count = len(leg.helpers)
            for name in leg.chain:
                copy = pose[name].constraints.new('COPY_TRANSFORMS')
                copy.name = "CR IK"
                copy.target, copy.subtarget = obj, PREFIX + "Follow_" + name
                rig_style.driven(obj, copy, prop)
        else:
            # IK on the foot with use_tail off, so its head is the chain tip
            # (a reoriented bone's tail needn't meet its child's head)
            ik = pose[leg.foot].constraints.new('IK')
            ik.name = "CR IK"
            ik.use_tail = False
            ik.target, ik.subtarget = obj, target_name
            ik.pole_target, ik.pole_subtarget = obj, pole_name
            ik.pole_angle = poles[leg.name]
            ik.chain_count = len(leg.chain)     # the foot isn't a segment; its head is the tip
            rig_style.driven(obj, ik, prop)
        turn = pose[leg.foot].constraints.new('COPY_ROTATION')
        turn.name = "CR IK Foot"
        turn.target, turn.subtarget = obj, target_name
        rig_style.driven(obj, turn, prop)
    if eyes:
        aim = pose[PREFIX + "Eyes"]
        lefts = [n for n in eyes if side_of(n) == "L"]
        rights = [n for n in eyes if side_of(n) == "R"]
        across = (armature.bones[lefts[0]].head_local - armature.bones[rights[0]].head_local) if lefts and rights else forward.cross(up)
        half = max(across.length / 2.0, h * 0.04)
        show(aim.name, "Controls", "CR_Glasses", half, role="C")
        align_shape(obj, aim, x=across.normalized(), y=forward)          # rings face forward
        for name, track in eyes.items():
            look = pose[name].constraints.new('DAMPED_TRACK')
            look.name = "CR Look"
            look.target, look.subtarget = obj, PREFIX + "Eye_" + name
            look.track_axis = track
            rig_style.driven(obj, look, "eyes_aim")
    for name in armature.bones.keys():
        if not placed(name):
            rig_style.assign(armature, name, "Mechanics" if name.startswith(PREFIX) else "Game Bones")
    obj.show_in_front = True            # controls inside the body stay clickable
```
Add `pi` to the imports: `from math import pi` (the module imports `re`, `bpy`, `mathutils` today). Delete `_palette` and
`_driven`.

Size notes for the implementer: the old `sized(name, shape, palette, size)` took a full width; the kit takes radius /
half-size, so every old factor is halved (CTRL_Spine `h*0.3` → circle `h*0.15`; CTRL_Box `h*0.035` → diamond `h*0.018`;
pole `h*0.08` → `h*0.03` diamond, a touch smaller since a diamond reads larger). The root's old `CTRL_Root` at `h*1.3`
becomes a circle of radius `h*0.65`. `CR_Foot` keeps its old scale (`h*0.3`).

- [ ] **Step 4: Run, expect PASS** — all checks.

- [ ] **Step 5: Commit** — propose `Build the creature rig on the kit`; after the user's OK commit `creature_rig.py` and the test.

---

### Task 3: Real creatures, renders, suites

- [ ] **Step 1:** `fpdev.py status` (start the test instance if needed); `fpdev.py import wolf` (payload exists), then a
LEGO animal: `python fpdev.py loader LegoWildlife --filter cow` to find one, `fpdev export cow --type LegoWildlife --path <it>`,
`fpdev import cow`. If an import doesn't rig on its own, run `<scratchpad>/rig_any.py` through `fpdev script`.
Expected: both rig; `rig_any.py` lists the five kit collections (FK hidden) and CR_Settings keys.
- [ ] **Step 2:** `fpdev render wolf --controls front,top,side` and the same for the LEGO animal; keep the wolf's before
sheet (`sheet_before.png`, rendered before Task 2). Look critically: colours read apart, rings smooth, face diamonds
small, no eye-target dots, gear clear of the body, LEGO shapes visible at its scale.
- [ ] **Step 3:** All suites: `baseline.py compare --only suites --no-build`. Expected: translator 263, layout 951 ok,
subsurface 45, rig_check all passed.
