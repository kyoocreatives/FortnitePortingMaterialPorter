# LEGO Figure Rig on the Kit + Face Board Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The LEGO figure rig styled and grouped with the rig kit, plus a face board driving the face material's expressions.

**Architecture:** `lego_rig.create()` keeps its bones and locks and styles them through `rig_style`. A new
`processing/context/face_board.py` builds the board's bones (edit mode), wires shapes, limits and drivers (pose mode),
and switches its drivers with an object property `fpmp_face_board`. `face_anim.apply` turns the board off when an
emote brings face animation. Synthetic skeleton + synthetic face material in `tests/plugin/rig_check.py`.

**Tech Stack:** Blender 5.2 Python (bpy), the fork's Blender plugin, headless tests.

**Spec:** `docs/superpowers/specs/2026-10-09-rig-kit-lego-design.md`

## Global Constraints

- Kit colours/widths/collections as `rig_style` (Controls, Secondary, Mechanics, Game Bones; no FK group for LEGO).
- Pose inputs, in board order: `browleftpose` Brow L (L), `browrightpose` Brow R (R), `eyeleftpose` Eye L (L),
  `eyerightpose` Eye R (R), `eyelashleftpose` Lash L (L), `eyelashrightpose` Lash R (R), `mouthpose` Mouth (C),
  `teethupperpose` Teeth Upper (C), `teethlowerpose` Teeth Lower (C), `tonguepose` Tongue (C).
- Pads: `eyeleft` (L), `eyeright` (R), `mouth` (C), each needing `<pad>u` and `<pad>v` inputs.
- Slider range: the imported expression ± up to 15 (clamped 0-15); pad range ±1 step = ±0.1 UV around the imported value.
- Face Board property `Object.fpmp_face_board` (default True); off mutes the board's drivers.
- Fork code habits; commit messages approved by the user; no Co-Authored-By trailer.

## Review Focus

- A figure whose face material lacks some inputs (no brows, no tongue): only present sliders/pads exist; no KeyError.
- A figure with no face material at all (bald head prop, missing texture): no board, rig still made.
- An imported expression that isn't 0 (face picks `Mouth:12`): the board keeps it at rest; Reset Pose returns to it.
- Face Board off then on: drivers mute/unmute; with the emote's keys present, off plays the keys.
- Two figures sharing one face material: the last rigged one drives it; the first's toggle must not error.

Task 2 covers the first, second (via the plain synthetic figure of Task 1), third and fourth; the fifth is checked by reading.

---

### Task 1: The LEGO figure on the kit

**Files:**
- Modify: `plugins/Blender/fortnite_porting/processing/context/lego_rig.py` (styling, collections; drop `COLORS`,
  `ensure_blend_data`, `sized`)
- Modify: `plugins/Blender/fortnite_porting/processing/context/creature_rig.py` (delete `sized` and `NATIVE`: their last
  user goes)
- Test: `tests/plugin/rig_check.py`

**Interfaces:**
- Produces: `lego_rig.create(obj)` styled with the kit; `lego_rig.figure(...)`-free (no new API).

- [ ] **Step 1: Write the failing test** (append before the summary)

```python
# the LEGO figure on the kit: the shared minifigure skeleton, facing -Y (its right is -X)
from fpmp_baseline.processing.context import lego_rig  # noqa: E402


def figure(name, face=None):
    """A LEGO figure skeleton; `face`: {input name: value} for a head mesh whose material's group node has them."""
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
    root = bone("root", (0, 0, 0), (0, 0, 0.1))
    pelvis = bone("pelvis", (0, 0, 0.45), (0, 0, 0.5), root)
    for side, x in (("l", 0.1), ("r", -0.1)):
        bone("leg_" + side, (x, 0, 0.45), (x, 0, 0.05), pelvis)
    torso = bone("torso", (0, 0, 0.5), (0, 0, 0.85), pelvis)
    for side, x in (("l", 0.2), ("r", -0.2)):
        arm = bone("arm_" + side, (x, 0, 0.8), (x * 1.1, 0, 0.6), torso)
        bone("hand_" + side, (x * 1.1, 0, 0.6), (x * 1.1, -0.05, 0.5), arm)
    neck = bone("neck_accessory", (0, 0, 0.85), (0, 0, 0.9), torso)
    head = bone("head", (0, 0, 0.9), (0, 0, 1.15), neck)
    bone("head_accessory", (0, 0, 1.15), (0, 0, 1.25), head)
    bpy.ops.object.mode_set(mode='OBJECT')
    if face is not None:
        group = bpy.data.node_groups.new(name + " face", 'ShaderNodeTree')
        for key in face:
            group.interface.new_socket(key, in_out='INPUT', socket_type='NodeSocketFloat')
        group.interface.new_socket("Surface", in_out='OUTPUT', socket_type='NodeSocketShader')
        mat = bpy.data.materials.new("MP MI_Head_" + name)
        node = mat.node_tree.nodes.new("ShaderNodeGroup")
        node.node_tree = group
        for key, value in face.items():
            node.inputs[key].default_value = value
        mesh = bpy.data.meshes.new(name + " head")
        mesh.from_pydata([(0, 0, 0.9), (0.1, 0, 0.9), (0, 0, 1.1)], [], [(0, 1, 2)])
        mesh.materials.append(mat)
        head_obj = bpy.data.objects.new(name + " head", mesh)
        bpy.context.scene.collection.objects.link(head_obj)
        head_obj.modifiers.new("Armature", 'ARMATURE').object = obj
    lego_rig.create(obj)
    return obj


fig = figure("fig")
fp = fig.pose.bones
fgroups = {c.name: c for c in fig.data.collections}
member_of = lambda n: next((g for g in rig_style.COLLECTIONS if g in fgroups and n in fgroups[g].bones), None)
check("lego collections", [c.name for c in fig.data.collections if c.name in rig_style.COLLECTIONS], list(rig_style.COLLECTIONS))
for n, want in (("root", "Controls"), ("pelvis", "Controls"), ("torso", "Controls"), ("head", "Controls"),
                ("leg_l", "Controls"), ("arm_r", "Controls"), ("hand_l", "Controls"), ("head_accessory", "Secondary"),
                ("neck_accessory", "Game Bones")):
    check("lego %s in %s" % (n, want), member_of(n), want)
check("lego root main", tuple(fp["root"].color.custom.normal), rig_style.COLORS["main"], tol=0.01)
check("lego left leg blue", tuple(fp["leg_l"].color.custom.normal), rig_style.COLORS["L"], tol=0.01)
check("lego right arm red", tuple(fp["arm_r"].color.custom.normal), rig_style.COLORS["R"], tol=0.01)
check("lego leg dial ticks", fp["leg_l"].custom_shape.name, "CR_CircleTick")
check("lego torso ring", fp["torso"].custom_shape.name, "CR_Circle")
check("lego accessory box", fp["head_accessory"].custom_shape.name, "CR_Box")
check("lego accessory thin", fp["head_accessory"].custom_shape_wire_width, 1.5)
check("lego leg swings on one axis", tuple(fp["leg_l"].lock_rotation), (False, True, True))
check("lego drawn in front", fig.show_in_front, True)
check("no face material, no board", "CR_FaceBoard" in fp, False)
```

- [ ] **Step 2: Run, expect FAIL** on the collection checks (`LEGO Controls` today).

Same command as before (copy the plugin with `baseline.copy_plugin`, run `rig_check.py` headless, isolated).

- [ ] **Step 3: Implement**

`lego_rig.py`: module imports become
```python
import bpy
from mathutils import Vector

from . import rig_shapes, rig_style
```
Delete `COLORS`. In `create()`: delete `from ...utils import ensure_blend_data`, `from . import rig_shapes`,
`ensure_blend_data()`, and `from .creature_rig import align_shape, sized` becomes `from .creature_rig import align_shape`.
Replace everything from `collections = {}` to the end of the `if "head_accessory" in bones:` block with:
```python
    from math import pi
    rig_style.collections(armature)
    RING = (pi / 2.0, 0.0, 0.0)            # a kit circle lies in XY; this stands it around the bone's Y axis

    def control(name, role, shape=None, size=None, secondary=False, ring=False):
        bone = pose[name]
        if shape is not None:
            bone.custom_shape = rig_shapes.ensure(shape)
        bone.use_custom_shape_bone_size = False
        if size is not None:
            bone.custom_shape_scale_xyz = (size, size, size)
        if ring:
            bone.custom_shape_rotation_euler = RING
        rig_style.style(bone, role, secondary=secondary)
        rig_style.assign(armature, name, "Secondary" if secondary else "Controls")

    root = pose["root"]
    # Proportions come from the joints, since mesh bounds would include hands and hair.
    hip_x, shoulder_x = abs(bones["leg_l"].head_local.x), abs(bones["arm_l"].head_local.x)
    root.custom_shape = rig_shapes.footprint("CR_Footprint_" + obj.name, hip_x * 5.0, hip_x * 5.5)
    control("root", "main", size=1.0)
    align_shape(obj, root, x=-right, y=forward, z=up)
    rig_shapes.place(obj, root, Vector(((low.x + high.x) / 2.0, (low.y + high.y) / 2.0, low.z)))
    pelvis = pose["pelvis"]
    pelvis.custom_shape = rig_shapes.footprint("CR_Plate_" + obj.name, hip_x * 2.6, hip_x * 4.2)
    control("pelvis", "C", size=1.0)
    align_shape(obj, pelvis, x=-right, y=forward, z=up)
    # Rings around torso and head; their bones point up and a ring's axis is its bone's.
    torso, head = bones["torso"], bones["head"]
    control("torso", "C", "CR_Circle", shoulder_x * 1.3, ring=True)
    rig_shapes.place(obj, pose["torso"], (torso.head_local + torso.tail_local) / 2.0)
    head_width = shoulder_x * 1.9
    control("head", "C", "CR_Circle", head_width * 0.5, ring=True)
    rig_shapes.place(obj, pose["head"], head.head_local + up * head_width * 0.45)
    for side, out in (("l", -right), ("r", right)):
        role = side.upper()
        leg = bones["leg_" + side]
        hip = leg.head_local
        # dial on the hip axis (bone X), its tick down the leg
        control(leg.name, role, "CR_CircleTick", (hip.z - low.z) * 0.35)
        align_shape(obj, pose[leg.name], y=-up, z=leg.matrix_local.col[0].to_3d())
        rig_shapes.place(obj, pose[leg.name], hip + out * (abs(hip.x) * 0.9) - up * (hip.z - low.z) * 0.15)
        pose[leg.name].rotation_mode = 'XYZ'
        pose[leg.name].lock_rotation = (False, True, True)
        arm = bones["arm_" + side]
        control(arm.name, role, "CR_CircleTick", shoulder_x * 0.55)
        align_shape(obj, pose[arm.name], y=-up, z=arm.matrix_local.col[0].to_3d())
        rig_shapes.place(obj, pose[arm.name], arm.head_local + out * shoulder_x * 0.25)
        hand = bones["hand_" + side]
        control(hand.name, role, "CR_Circle", shoulder_x * 0.35, ring=True)
        pose[hand.name].rotation_mode = 'YXZ'
        pose[hand.name].lock_rotation = (True, False, True)
    if "head_accessory" in bones:
        control("head_accessory", "C", "CR_Box", shoulder_x * 0.4, secondary=True)
    for name in bones.keys():
        if not any(name in armature.collections[g].bones for g in rig_style.COLLECTIONS):
            rig_style.assign(armature, name, "Mechanics" if name.startswith("CR_") else "Game Bones")
    obj.show_in_front = True            # controls inside the body stay clickable
```
(Sizes: the old `sized` took a full width; the kit takes radius/half-size, so each factor is halved.)

`creature_rig.py`: delete `NATIVE` and `sized` (no users left: `grep -rn "sized(\|NATIVE" plugins` must only show
unrelated text).

- [ ] **Step 4: Run, expect PASS** — all checks.

- [ ] **Step 5: Commit** — propose `Build the LEGO figure rig on the kit`.

---

### Task 2: The face board

**Files:**
- Create: `plugins/Blender/fortnite_porting/processing/context/face_board.py`
- Modify: `plugins/Blender/fortnite_porting/processing/context/lego_rig.py` (build the board: edit-mode bones before
  the pose-mode styling; wiring after it)
- Modify: `plugins/Blender/fortnite_porting/operator/rig_ui.py` (register `face_board`; LEGO panel: Face Board toggle,
  current expressions)
- Modify: `plugins/Blender/fortnite_porting/material_porter/face_anim.py` (`apply`: turn the board off)
- Test: `tests/plugin/rig_check.py`

**Interfaces:**
- Consumes: Task 1's styled figure; `face_anim.face_materials(armature) -> list[(material, {lower name: socket})]`.
- Produces: `face_board.POSES`, `face_board.PADS`, `face_board.BOARD = "CR_FaceBoard"`,
  `face_board.bones(edit, faces, head, left, up, size) -> bool` (edit mode; False when no face inputs),
  `face_board.wire(obj, faces, size)` (pose mode), `face_board.set_on(obj, on)`, `face_board.register()/unregister()`
  (the `Object.fpmp_face_board` property).

- [ ] **Step 1: Write the failing tests** (append before the summary)

```python
# the face board
from fpmp_baseline.processing.context import face_board  # noqa: E402
face_board.register()
lf = figure("faced", face={"MouthPose": 12.0, "EyeLeftPose": 0.0, "EyeRightPose": 0.0, "EyeLeftU": 0.1, "EyeLeftV": 0.02,
                           "MouthU": 0.0, "MouthV": -0.2})
lp = lf.pose.bones
mat = bpy.data.materials["MP MI_Head_faced"]
grp = next(n for n in mat.node_tree.nodes if n.type == 'GROUP')
check("board made", "CR_FaceBoard" in lp, True)
check("present sliders only", sorted(n for n in lp.keys() if n.startswith("CR_Face_") and not n.endswith("_UV")),
      ["CR_Face_EyeLeftPose", "CR_Face_EyeRightPose", "CR_Face_MouthPose"])
check("pads for present pairs", sorted(n for n in lp.keys() if n.endswith("_UV")), ["CR_Face_EyeLeft_UV", "CR_Face_Mouth_UV"])
lf.update_tag()
bpy.context.view_layer.update()
check("imported expression kept at rest", grp.inputs["MouthPose"].default_value, 12.0)
step = face_board.step_of(lf)
lp["CR_Face_MouthPose"].location.x = 3 * step
lf.update_tag()
bpy.context.view_layer.update()
check("slider: 3 steps = 15", grp.inputs["MouthPose"].default_value, 15.0)
lp["CR_Face_MouthPose"].location.x = 9 * step
lf.update_tag()
bpy.context.view_layer.update()
check("slider clamps at 15", grp.inputs["MouthPose"].default_value, 15.0)
lp["CR_Face_EyeLeft_UV"].location.x = step
lf.update_tag()
bpy.context.view_layer.update()
check("pad moves the eye", abs(grp.inputs["EyeLeftU"].default_value - 0.1) > 0.05, True)
# off: the emote's keys play
grp.inputs["MouthPose"].default_value = 2.0
grp.inputs["MouthPose"].keyframe_insert("default_value", frame=1)
face_board.set_on(lf, False)
mat.node_tree.update_tag()
bpy.context.scene.frame_set(1)
check("board off: keys play", grp.inputs["MouthPose"].default_value, 2.0)
face_board.set_on(lf, True)
check("board on: drivers live", all(not fc.mute for fc in mat.node_tree.animation_data.drivers), True)
lf.fpmp_face_board = False
check("toggle off mutes", all(fc.mute for fc in mat.node_tree.animation_data.drivers), True)
lf.fpmp_face_board = True
check("toggle on unmutes", all(not fc.mute for fc in mat.node_tree.animation_data.drivers), True)
```

- [ ] **Step 2: Run, expect FAIL** — `ImportError: cannot import name 'face_board'`.

- [ ] **Step 3: Implement `face_board.py`**

```python
"""A LEGO figure's face board: controls beside the head that pick the face's expressions and nudge its features.

The face is a texture atlas driven by the exact face material's inputs (MouthPose, EyeLeftU...). Each pose slider
steps an expression index around the imported one; each pad moves a feature's U/V around its imported place.
Drivers on the material's inputs read the controls; the Face Board switch mutes them so an emote's face keys play."""
import bpy
from mathutils import Vector

from . import rig_shapes, rig_style

BOARD = "CR_FaceBoard"
PREFIX = "CR_Face_"
POSES = (("browleftpose", "BrowLeftPose", "L"), ("browrightpose", "BrowRightPose", "R"),
         ("eyeleftpose", "EyeLeftPose", "L"), ("eyerightpose", "EyeRightPose", "R"),
         ("eyelashleftpose", "EyelashLeftPose", "L"), ("eyelashrightpose", "EyelashRightPose", "R"),
         ("mouthpose", "MouthPose", "C"), ("teethupperpose", "TeethUpperPose", "C"),
         ("teethlowerpose", "TeethLowerPose", "C"), ("tonguepose", "TonguePose", "C"))
PADS = (("eyeleft", "EyeLeft", "L"), ("eyeright", "EyeRight", "R"), ("mouth", "Mouth", "C"))
MOST = 15           # expressions a slider reaches
PAD_UV = 0.1        # UV a pad moves its feature at full reach
STEP = "fpmp_face_step"


def _inputs(faces):
    """Lower-case input names every face material has."""
    names = None
    for _, inputs in faces:
        names = set(inputs) if names is None else names & set(inputs)
    return names or set()


def bones(edit, faces, head, left, up, size):
    """Board and controls (edit mode), beside the head on the figure's left. False when the face has no pose input."""
    have = _inputs(faces)
    poses = [p for p in POSES if p[0] in have]
    pads = [p for p in PADS if p[0] + "u" in have and p[0] + "v" in have]
    if not poses:
        return False
    step = size * 0.05
    rows = len(poses) + (1 if pads else 0)
    at = edit[head].head + left * size * 1.4 + up * size * 0.2
    board = edit.new(BOARD)
    board.head, board.tail = at, at + up * step * rows
    board.align_roll(-left.cross(up))           # board X along the figure's left, Z towards the viewer
    board.parent, board.use_deform = edit[head], False
    for i, (_, name, _) in enumerate(poses):
        b = edit.new(PREFIX + name)
        b.head = at + up * step * (rows - 1 - i)
        b.tail = b.head + up * step * 0.5
        b.align_roll(-left.cross(up))
        b.parent, b.use_deform = board, False
    for j, (_, name, _) in enumerate(pads):
        b = edit.new(PREFIX + name + "_UV")
        b.head = at + left * step * 4.0 * j
        b.tail = b.head + up * step * 0.5
        b.align_roll(-left.cross(up))
        b.parent, b.use_deform = board, False
    return True


def step_of(obj):
    return obj.data[STEP]


def _drive(mat, socket, obj, bone, axis, expression):
    fc = socket.driver_add("default_value")
    driver = fc.driver
    driver.type = 'SCRIPTED'
    var = driver.variables.new()
    var.name, var.type = "x", 'TRANSFORMS'
    var.targets[0].id = obj
    var.targets[0].bone_target = bone
    var.targets[0].transform_type = 'LOC_' + axis
    var.targets[0].transform_space = 'LOCAL_SPACE'
    driver.expression = expression
    return fc


def wire(obj, faces, size):
    """Shapes, limits and drivers (pose mode), for the bones `bones` made."""
    pose = obj.pose.bones
    have = _inputs(faces)
    step = size * 0.05
    obj.data[STEP] = step
    board = pose[BOARD]
    poses = [p for p in POSES if PREFIX + p[1] in pose]
    pads = [p for p in PADS if PREFIX + p[1] + "_UV" in pose]
    board.custom_shape = rig_shapes.ensure("CR_Square")
    board.use_custom_shape_bone_size = False
    width = step * (MOST + 2)
    board.custom_shape_scale_xyz = (width * 0.5, step * (len(poses) + 1) * 0.5, 1.0)
    board.custom_shape_translation = (width * 0.5 - step, step * (len(poses) + 1) * 0.5 - step * 0.5, 0.0)
    rig_style.style(board, "C")
    rig_style.assign(obj.data, BOARD, "Controls")
    board.lock_location = board.lock_rotation = board.lock_scale = (True, True, True)
    for key, name, role in poses:
        bone = pose[PREFIX + name]
        rest = round(faces[0][1][key].default_value)
        bone.custom_shape = rig_shapes.ensure("CR_Diamond")
        bone.use_custom_shape_bone_size = False
        bone.custom_shape_scale_xyz = (step * 0.4,) * 3
        rig_style.style(bone, role)
        rig_style.assign(obj.data, bone.name, "Controls")
        bone.lock_location, bone.lock_rotation, bone.lock_scale = (False, True, True), (True, True, True), (True, True, True)
        limit = bone.constraints.new('LIMIT_LOCATION')
        limit.owner_space = 'LOCAL'
        limit.use_min_x = limit.use_max_x = True
        limit.min_x, limit.max_x = -rest * step, (MOST - rest) * step
        limit.use_transform_limit = True
        for mat, inputs in faces:
            _drive(mat, inputs[key], obj, bone.name, "X", "max(0,min(%d,%d+round(x/%r)))" % (MOST, rest, step))
    for key, name, role in pads:
        bone = pose[PREFIX + name + "_UV"]
        bone.custom_shape = rig_shapes.ensure("CR_Square")
        bone.use_custom_shape_bone_size = False
        bone.custom_shape_scale_xyz = (step * 0.5,) * 3
        rig_style.style(bone, role, secondary=True)
        rig_style.assign(obj.data, bone.name, "Secondary")
        bone.lock_location, bone.lock_rotation, bone.lock_scale = (False, False, True), (True, True, True), (True, True, True)
        limit = bone.constraints.new('LIMIT_LOCATION')
        limit.owner_space = 'LOCAL'
        limit.use_min_x = limit.use_max_x = limit.use_min_y = limit.use_max_y = True
        limit.min_x, limit.max_x, limit.min_y, limit.max_y = -step, step, -step, step
        limit.use_transform_limit = True
        for mat, inputs in faces:
            for axis, suffix in (("X", "u"), ("Y", "v")):
                rest = inputs[key + suffix].default_value
                _drive(mat, inputs[key + suffix], obj, bone.name, axis, "%r+x/%r*%r" % (rest, step, PAD_UV))
    obj["fpmp_face_board_materials"] = [mat.name for mat, _ in faces]


def set_on(obj, on):
    """Mute or unmute the board's drivers (the face's own keys play while they're muted)."""
    for name in obj.get("fpmp_face_board_materials", []):
        mat = bpy.data.materials.get(name)
        ad = mat.node_tree.animation_data if mat is not None and mat.node_tree else None
        for fc in (ad.drivers if ad else ()):
            if any(t.id == obj for v in fc.driver.variables for t in v.targets):
                fc.mute = not on


def _toggled(self, context):
    set_on(self, self.fpmp_face_board)


def register():
    bpy.types.Object.fpmp_face_board = bpy.props.BoolProperty(
        name="Face Board", default=True, update=_toggled,
        description="The face board drives the face (off: the face's own animation plays, e.g. an emote's)")


def unregister():
    del bpy.types.Object.fpmp_face_board
```

In `lego_rig.create()`:
- after the figure size block (`size = high - low` ...), before the styling: build the board in edit mode
```python
    from . import face_board
    from ...material_porter import face_anim
    faces = face_anim.face_materials(obj)
    head_size = abs(bones["arm_l"].head_local.x) * 1.9
    has_board = False
    if faces:
        bpy.ops.object.mode_set(mode='EDIT')
        has_board = face_board.bones(armature.edit_bones, faces, "head", -right, up, head_size)
        bpy.ops.object.mode_set(mode='POSE')
        bones, pose = armature.bones, obj.pose.bones
```
- after `obj.show_in_front = True`, before the final "Game Bones" loop runs (move that loop to the very end):
```python
    if has_board:
        face_board.wire(obj, faces, head_size)
```
and keep the final unassigned-bones loop after the wiring.

`rig_ui.py`: `register()` also calls `face_board.register()` (and `unregister()` its `unregister()`), imported as
`from ..processing.context import face_board`. In the panel's LEGO branch (today `if data.get("is_lego_rig"): return`):
```python
        if data.get("is_lego_rig"):
            if face_board.BOARD in obj.pose.bones:
                col.prop(obj, "fpmp_face_board", text="Face Board", toggle=True)
                faces = [bpy.data.materials.get(n) for n in obj.get("fpmp_face_board_materials", [])]
                group = next((n for m in faces if m and m.node_tree for n in m.node_tree.nodes if n.type == 'GROUP'), None)
                if group is not None:
                    for _, name, _ in face_board.POSES:
                        if name in group.inputs:
                            col.label(text="%s: %d" % (name.replace("Pose", ""), round(group.inputs[name].default_value)))
            return
```

`face_anim.apply`: at its start, after `clear(armature)`:
```python
    if armature.get("fpmp_face_board_materials"):
        armature.fpmp_face_board = False        # the emote's face keys play, not the board
```

- [ ] **Step 4: Run, expect PASS.** If a driver-mute/keys check misbehaves, debug with superpowers:systematic-debugging
  (drivers on node sockets live in `mat.node_tree.animation_data.drivers`).

- [ ] **Step 5: Commit** — propose `Add a face board to the LEGO rig`.

---

### Task 3: Real figure, renders, suites

- [ ] **Step 1:** `fpdev import lego` (payload exists: LucidAzalea); `fpdev script lego <scratchpad>/face_probe2.py`
  to read the face inputs after rigging; `fpdev render lego --controls front,top,side`; before sheet = the render made
  before Task 1 (`sheet_before.png`).
- [ ] **Step 2:** A face test render: through `fpdev script` set `CR_Face_MouthPose` and `CR_Face_EyeLeftPose`
  2 steps over and save; `fpdev render lego --frame Head` before/after: the mouth and eye change.
- [ ] **Step 3:** All suites (`baseline.py compare --only suites --no-build`): all pass.
