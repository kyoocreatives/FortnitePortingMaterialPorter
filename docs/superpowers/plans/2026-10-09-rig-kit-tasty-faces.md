# Tasty on the Kit + Flipbook Face Board Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tasty restyled with the kit through the fork's hook, and the face board generalised to flipbook faces on
Tasty, creature (incl. sidekicks, sprites) and LEGO rigs.

**Architecture:** `face_board.py` gets face types (LEGO, Flipbook) as data tables, its own face finder, per-slider
counts, and an attach point that can be a head bone or the root. `tasty_style.py` (new) restyles a Tasty armature and
adds its board. `creature_rig.create` adds a board; `mesh_hooks.after_parts` calls the Tasty restyle and rigs sprites.
Synthetic armatures and face materials in `tests/plugin/rig_check.py`.

**Tech Stack:** Blender 5.2 Python (bpy), the fork's Blender plugin, headless tests.

**Spec:** `docs/superpowers/specs/2026-10-09-rig-kit-tasty-faces-design.md`

## Global Constraints

- FP's own files (tasty_context.py, tasty_op.py, mesh_context.py) are not edited; mesh_context already calls `mp.after_parts` (an `# MP` line).
- Flipbook inputs (compare lower-case): sliders `flipbook_face_l_brow_index` BrowL (L), `flipbook_face_r_brow_index` BrowR (R),
  `flipbook_face_l_eye_index` EyeL (L), `flipbook_face_r_eye_index` EyeR (R), `flipbook_face_mouth_index` Mouth (C);
  counts `fb_<brow|eye|mouth>columncount × fb_<...>rowcount` else 16; pads Brows / Eyes / Mouth on
  `fb_<brow|eye|mouth>uvoffsetx/y` (C).
- LEGO type keeps its control names (`CR_Face_MouthPose`...), counts 16, pads `CR_Face_EyeLeft_UV`... (existing tests stay valid).
- Tasty: Rig and Face shown; Dynamic, Base, Twist, Deform, Sockets, Extra hidden; root main 3.5; Rig 2.5; Face and Dynamic
  secondary tint 1.5; other shaped bones recoloured, width kept; `show_in_front`; marker `fpmp_tasty_styled`.
- Commit messages approved by the user; no Co-Authored-By trailer.

## Review Focus

- A face material with only some flipbook inputs (eyes, no mouth): only those sliders/pads.
- Counts present for the eyes but not the mouth: mouth slider 0-15.
- A Tasty import whose armature was already styled (re-import into the scene): no double styling, no second board.
- A creature (sidekick) whose face isn't a flipbook (Beebo: 3D eyes): no board, rig unchanged.
- A sprite whose skeleton the creature rig reads poorly: the rig is still made (or fails into the hook's log), the import
  never breaks.

---

### Task 1: Face types in the face board

**Files:**
- Modify: `plugins/Blender/fortnite_porting/processing/context/face_board.py` (rewrite the module as below)
- Modify: `plugins/Blender/fortnite_porting/processing/context/lego_rig.py` (use `face_board.faces`)
- Modify: `plugins/Blender/fortnite_porting/operator/rig_ui.py` (face board lines for any rig; from the stored mapping)
- Test: `tests/plugin/rig_check.py`

**Interfaces:**
- Produces: `face_board.faces(armature) -> list[(material, {lower: socket})]` (LEGO or flipbook faces);
  `face_board.kind(faces) -> dict | None`; `face_board.bones(edit, faces, parent, at, left, up, size) -> bool`;
  `face_board.wire(obj, faces, size)`; `face_board.ui(layout, obj)` (panel lines); `face_board.attach_point(...)` not
  needed (callers pass `parent` and `at`). Data stored on the armature object: `fpmp_face_board_materials`,
  `fpmp_face_board_inputs` ({control: socket name}).

- [ ] **Step 1: Write the failing tests** (append before the summary)

```python
# flipbook faces: a creature with a flipbook head
def attach_face(obj, name, face):
    """A head mesh on `obj` whose material's group node has the `face` inputs (name: value)."""
    group = bpy.data.node_groups.new(name + " face", 'ShaderNodeTree')
    for key in face:
        group.interface.new_socket(key, in_out='INPUT', socket_type='NodeSocketFloat')
    group.interface.new_socket("Surface", in_out='OUTPUT', socket_type='NodeSocketShader')
    mat = bpy.data.materials.new("MP " + name + " face")
    node = mat.node_tree.nodes.new("ShaderNodeGroup")
    node.node_tree = group
    for key, value in face.items():
        node.inputs[key].default_value = value
    mesh = bpy.data.meshes.new(name + " head")
    mesh.from_pydata([(0, 0, 0.9), (0.1, 0, 0.9), (0, 0, 1.1)], [], [(0, 1, 2)])
    mesh.materials.append(mat)
    head = bpy.data.objects.new(name + " head", mesh)
    bpy.context.scene.collection.objects.link(head)
    head.modifiers.new("Armature", 'ARMATURE').object = obj
    return mat, node


FLIP = {"flipbook_face_L_eye_index": 0.0, "flipbook_face_R_eye_index": 0.0, "flipbook_face_mouth_index": 2.0,
        "FB_EyeColumnCount": 3.0, "FB_EyeRowCount": 3.0, "FB_EyeUVOffsetX": 0.24, "FB_EyeUVOffsetY": 0.03,
        "FB_MouthUVOffsetX": 0.0, "FB_MouthUVOffsetY": 0.0}
fl = bpy.data.objects.new("flipfig", bpy.data.armatures.new("flipfig"))
bpy.context.scene.collection.objects.link(fl)
fmat, fnode = attach_face(fl, "flipfig", FLIP)
fl_faces = face_board.faces(fl)
check("flipbook face found", [m.name for m, _ in fl_faces], ["MP flipfig face"])
check("flipbook kind", face_board.kind(fl_faces)["name"], "flipbook")
check("lego face still LEGO", face_board.kind(face_board.faces(lf))["name"], "lego")
check("flipbook counts", (face_board.count(fl_faces, "flipbook_face_l_eye_index"), face_board.count(fl_faces, "flipbook_face_mouth_index")), (9, 16))
# board on the quadruped's head, driven
qf = quadruped("quad_flip")
qmat, qnode = attach_face(qf, "quad_flip", FLIP)
creature_rig_board = face_board.add(qf, head="head")
check("board added to a rigged creature", creature_rig_board, True)
qfp = qf.pose.bones
check("flipbook sliders", sorted(n for n in qfp.keys() if n.startswith("CR_Face_") and not n.endswith("_UV")),
      ["CR_Face_EyeL", "CR_Face_EyeR", "CR_Face_Mouth"])
check("flipbook pads", sorted(n for n in qfp.keys() if n.endswith("_UV")), ["CR_Face_Eyes_UV", "CR_Face_Mouth_UV"])
fstep = face_board.step_of(qf)
qfp["CR_Face_EyeL"].location.x = 20 * fstep
qfp["CR_Face_Mouth"].location.x = 3 * fstep
qf.update_tag()
bpy.context.scene.frame_set(5)
check("eye slider stops at its count", qnode.inputs["flipbook_face_L_eye_index"].default_value, 8.0)
check("mouth slider: 2 + 3", qnode.inputs["flipbook_face_mouth_index"].default_value, 5.0)
qfp["CR_Face_Eyes_UV"].location.y = fstep
qf.update_tag()
bpy.context.scene.frame_set(6)
check("eyes pad moves the eye offset", abs(qnode.inputs["FB_EyeUVOffsetY"].default_value - 0.03) > 0.05, True)
# no head bone: the board sits above the model
nh = bpy.data.objects.new("nohead", bpy.data.armatures.new("nohead"))
bpy.context.scene.collection.objects.link(nh)
bpy.context.view_layer.objects.active = nh
bpy.ops.object.mode_set(mode='EDIT')
rb = nh.data.edit_bones.new("root")
rb.head, rb.tail = (0, 0, 0), (0, 0, 0.5)
bpy.ops.object.mode_set(mode='OBJECT')
attach_face(nh, "nohead", FLIP)
check("board without a head bone", face_board.add(nh, head=None), True)
check("board above the model", nh.data.bones["CR_FaceBoard"].head_local.z > 0.5, True)
```

- [ ] **Step 2: Run, expect FAIL** — `AttributeError: module ... has no attribute 'faces'`.

- [ ] **Step 3: Implement** — `face_board.py` becomes:

```python
"""A face board: controls beside the head that pick a face's expressions and nudge its features.

Some faces are texture atlases driven by their material's inputs: LEGO figures' (MouthPose, EyeLeftU...) and
Fortnite's flipbook faces (flipbook_face_mouth_index, FB_EyeUVOffsetX...: sprites, some outfits). Each slider steps an
expression index around the imported one; each pad moves a feature around its imported place. Drivers on the
material's inputs read the controls; the Face Board switch mutes them so an emote's face keys play."""
import bpy
from mathutils import Vector

from . import rig_shapes, rig_style

BOARD = "CR_FaceBoard"
PREFIX = "CR_Face_"
# Face types: sliders (input, control, side), counts (input: (columns, rows) inputs), pads (control, (x, y) inputs, side).
LEGO = {"name": "lego",
        "sliders": (("browleftpose", "BrowLeftPose", "L"), ("browrightpose", "BrowRightPose", "R"),
                    ("eyeleftpose", "EyeLeftPose", "L"), ("eyerightpose", "EyeRightPose", "R"),
                    ("eyelashleftpose", "EyelashLeftPose", "L"), ("eyelashrightpose", "EyelashRightPose", "R"),
                    ("mouthpose", "MouthPose", "C"), ("teethupperpose", "TeethUpperPose", "C"),
                    ("teethlowerpose", "TeethLowerPose", "C"), ("tonguepose", "TonguePose", "C")),
        "counts": {},
        "pads": (("EyeLeft", ("eyeleftu", "eyeleftv"), "L"), ("EyeRight", ("eyerightu", "eyerightv"), "R"),
                 ("Mouth", ("mouthu", "mouthv"), "C"))}
FLIPBOOK = {"name": "flipbook",
            "sliders": (("flipbook_face_l_brow_index", "BrowL", "L"), ("flipbook_face_r_brow_index", "BrowR", "R"),
                        ("flipbook_face_l_eye_index", "EyeL", "L"), ("flipbook_face_r_eye_index", "EyeR", "R"),
                        ("flipbook_face_mouth_index", "Mouth", "C")),
            "counts": {"flipbook_face_l_brow_index": ("fb_browcolumncount", "fb_browrowcount"),
                       "flipbook_face_r_brow_index": ("fb_browcolumncount", "fb_browrowcount"),
                       "flipbook_face_l_eye_index": ("fb_eyecolumncount", "fb_eyerowcount"),
                       "flipbook_face_r_eye_index": ("fb_eyecolumncount", "fb_eyerowcount"),
                       "flipbook_face_mouth_index": ("fb_mouthcolumncount", "fb_mouthrowcount")},
            "pads": (("Brows", ("fb_browuvoffsetx", "fb_browuvoffsety"), "C"),
                     ("Eyes", ("fb_eyeuvoffsetx", "fb_eyeuvoffsety"), "C"),
                     ("Mouth", ("fb_mouthuvoffsetx", "fb_mouthuvoffsety"), "C"))}
KINDS = (LEGO, FLIPBOOK)
COUNT = 16          # expressions a slider reaches when the face doesn't say
PAD_UV = 0.1        # UV a pad moves its feature at full reach
STEP = "fpmp_face_step"
STEP_OF_HEAD = 0.06     # a slider step, in head widths
ROW = 2.5               # rows apart, in steps
SIZE = 0.6              # a slider's diamond and a pad's square, in steps


def _meshes(armature):
    return [o for o in bpy.data.objects if o.type == 'MESH' and
            (o.parent == armature or any(m.type == 'ARMATURE' and m.object == armature for m in o.modifiers))]


def faces(armature):
    """The armature's face materials, LEGO or flipbook: [(material, {lower-case input: socket})]."""
    found = {}
    for o in _meshes(armature):
        for slot in o.material_slots:
            mat = slot.material
            if mat is None or mat.node_tree is None or mat.name in found:
                continue
            for n in mat.node_tree.nodes:
                if n.type != 'GROUP':
                    continue
                inputs = {i.name.lower(): i for i in n.inputs if i.type == 'VALUE'}
                if any(key in inputs for k in KINDS for key, _, _ in k["sliders"]):
                    found[mat.name] = (mat, inputs)
                    break
    return list(found.values())


def _have(found):
    names = None
    for _, inputs in found:
        names = set(inputs) if names is None else names & set(inputs)
    return names or set()


def kind(found):
    have = _have(found)
    return next((k for k in KINDS if any(key in have for key, _, _ in k["sliders"])), None)


def count(found, key):
    """Expressions a slider steps through: the face's columns x rows when it has them, else COUNT."""
    columns_rows = kind(found)["counts"].get(key)
    inputs = found[0][1]
    if columns_rows and all(c in inputs for c in columns_rows):
        return max(int(round(inputs[columns_rows[0]].default_value * inputs[columns_rows[1]].default_value)), 1)
    return COUNT


def _layout(found):
    k, have = kind(found), _have(found)
    sliders = [s for s in k["sliders"] if s[0] in have]
    pads = [p for p in k["pads"] if all(i in have for i in p[1])]
    return sliders, pads


def bones(edit, found, parent, at, left, up, size):
    """Board and controls (edit mode) at `at`, parented to `parent`. False when the face has no slider input."""
    if kind(found) is None:
        return False
    sliders, pads = _layout(found)
    step = size * STEP_OF_HEAD
    rows = len(sliders) + (1 if pads else 0)
    board = edit.new(BOARD)
    board.head, board.tail = at, at + up * step * ROW
    board.align_roll(left.cross(up))            # board X along the figure's left (expressions count up that way)
    board.parent, board.use_deform = edit[parent], False
    for i, (key, name, _) in enumerate(sliders):
        # rests at its imported expression along the board (its left edge is expression 0)
        b = edit.new(PREFIX + name)
        b.head = at + left * step * round(found[0][1][key].default_value) + up * step * ROW * (rows - 1 - i)
        b.tail = b.head + up * step * 0.5
        b.align_roll(left.cross(up))
        b.parent, b.use_deform = board, False
    for j, (name, _, _) in enumerate(pads):
        b = edit.new(PREFIX + name + "_UV")
        b.head = at + left * step * (2.0 + 5.0 * j)
        b.tail = b.head + up * step * 0.5
        b.align_roll(left.cross(up))
        b.parent, b.use_deform = board, False
    return True


def step_of(obj):
    return obj.data[STEP]
```
then the existing `_drive` unchanged, and `wire` becomes:
```python
def wire(obj, found, size):
    """Shapes, limits and drivers (pose mode), for the bones `bones` made."""
    pose = obj.pose.bones
    step = size * STEP_OF_HEAD
    obj.data[STEP] = step
    sliders, pads = _layout(found)
    widest = max((count(found, key) for key, _, _ in sliders), default=COUNT)
    board = pose[BOARD]
    board.custom_shape = rig_shapes.ensure("CR_Square")
    board.use_custom_shape_bone_size = False
    # frame: expressions 0 to the widest count across (a step of margin), every row up
    rows = len(sliders) + (1 if pads else 0)
    board.custom_shape_scale_xyz = (step * (widest + 1) * 0.5, step * ROW * rows * 0.5, 1.0)
    board.custom_shape_translation = (step * (widest - 1) * 0.5, step * ROW * (rows - 1) * 0.5, 0.0)
    rig_style.style(board, "C")
    rig_style.assign(obj.data, BOARD, "Controls")
    board.lock_location = board.lock_rotation = board.lock_scale = (True, True, True)
    controls = {}
    for key, name, role in sliders:
        bone = pose[PREFIX + name]
        most = count(found, key) - 1
        rest = round(found[0][1][key].default_value)
        bone.custom_shape = rig_shapes.ensure("CR_Diamond")
        bone.use_custom_shape_bone_size = False
        bone.custom_shape_scale_xyz = (step * SIZE,) * 3
        rig_style.style(bone, role)
        rig_style.assign(obj.data, bone.name, "Controls")
        bone.lock_location, bone.lock_rotation, bone.lock_scale = (False, True, True), (True, True, True), (True, True, True)
        limit = bone.constraints.new('LIMIT_LOCATION')
        limit.owner_space = 'LOCAL'
        limit.use_min_x = limit.use_max_x = True
        limit.min_x, limit.max_x = -rest * step, (most - rest) * step
        limit.use_transform_limit = True
        for mat, inputs in found:
            _drive(mat, inputs[key], obj, bone.name, "X", "max(0,min(%d,%d+round(x/%r)))" % (most, rest, step))
        controls[bone.name] = found[0][1][key].name
    for name, (x_key, y_key), role in pads:
        bone = pose[PREFIX + name + "_UV"]
        bone.custom_shape = rig_shapes.ensure("CR_Square")
        bone.use_custom_shape_bone_size = False
        bone.custom_shape_scale_xyz = (step * SIZE,) * 3
        rig_style.style(bone, role, secondary=True)
        rig_style.assign(obj.data, bone.name, "Secondary")
        bone.lock_location, bone.lock_rotation, bone.lock_scale = (False, False, True), (True, True, True), (True, True, True)
        limit = bone.constraints.new('LIMIT_LOCATION')
        limit.owner_space = 'LOCAL'
        limit.use_min_x = limit.use_max_x = limit.use_min_y = limit.use_max_y = True
        limit.min_x, limit.max_x, limit.min_y, limit.max_y = -step, step, -step, step
        limit.use_transform_limit = True
        for mat, inputs in found:
            for axis, key in (("X", x_key), ("Y", y_key)):
                rest = inputs[key].default_value
                _drive(mat, inputs[key], obj, bone.name, axis, "%r+x/%r*%r" % (rest, step, PAD_UV))
    obj["fpmp_face_board_materials"] = [mat.name for mat, _ in found]
    obj["fpmp_face_board_inputs"] = controls


def add(obj, head, left=None, up=Vector((0.0, 0.0, 1.0)), size=None):
    """A board on an existing rig (object mode in and out): beside `head`, or above the model on the root when
    there's no head bone. False when the armature has no face to drive or already has a board."""
    found = faces(obj)
    if not found or kind(found) is None or BOARD in obj.data.bones:
        return False
    left = left if left is not None else Vector((1.0, 0.0, 0.0))      # the figure faces -Y: its left is +X
    view_layer = bpy.context.view_layer
    view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')
    edit = obj.data.edit_bones
    points = [p for b in edit for p in (b.head, b.tail)] or [Vector()]
    top = max(p.z for p in points)
    span = max(max(p[i] for p in points) - min(p[i] for p in points) for i in range(3))
    size = size or (edit[head].length if head else span * 0.25)
    if head:
        parent, at = head, edit[head].head + left * size * 1.4 + up * size * 0.2
    else:
        roots = [b.name for b in edit if b.parent is None]
        parent = roots[0]
        middle = sum(points, Vector()) / len(points)
        at = Vector((middle.x, middle.y, top)) + up * size * 0.3 - left * size * STEP_OF_HEAD * 8.0
    made = bones(edit, found, parent, at, left, up, size)
    bpy.ops.object.mode_set(mode='POSE')
    if made:
        wire(obj, found, size)
    bpy.ops.object.mode_set(mode='OBJECT')
    return made


def ui(layout, obj):
    """The Face Board switch and the current expressions, for a rig that has a board."""
    if BOARD not in obj.pose.bones:
        return
    layout.prop(obj, "fpmp_face_board", text="Face Board", toggle=True)
    mats = [bpy.data.materials.get(n) for n in obj.get("fpmp_face_board_materials", [])]
    group = next((n for m in mats if m and m.node_tree for n in m.node_tree.nodes if n.type == 'GROUP'), None)
    if group is None:
        return
    for control, socket in obj.get("fpmp_face_board_inputs", {}).items():
        if socket in group.inputs:
            layout.label(text="%s: %d" % (control[len(PREFIX):], round(group.inputs[socket].default_value)))
```
`set_on`, `_toggled`, `register`, `unregister` stay as they are. Delete `POSES`, `PADS`, `MOST`, `_inputs`.

`lego_rig.py`: `faces = face_anim.face_materials(obj)` → `faces = face_board.faces(obj)` (drop the face_anim import);
`face_board.bones(armature.edit_bones, faces, "head", -right, up, head_size)` →
```python
        at = armature.edit_bones["head"].head + (-right) * head_size * 1.4 + up * head_size * 0.2
        has_board = face_board.bones(armature.edit_bones, faces, "head", at, -right, up, head_size)
```

`rig_ui.py`: in the LEGO branch replace the face-board block with `face_board.ui(col, obj)`; also call
`face_board.ui(col, obj)` at the end of the vehicle-less creature branch (after Eyes Aim) and in the Tasty branch (Task 2).

- [ ] **Step 4: Run, expect PASS** — all checks (the LEGO face board checks keep passing with the LEGO type).

- [ ] **Step 5: Commit** — propose `Teach the face board flipbook faces`.

---

### Task 2: Tasty on the kit

**Files:**
- Create: `plugins/Blender/fortnite_porting/processing/context/tasty_style.py`
- Modify: `plugins/Blender/fortnite_porting/processing/context/rig_style.py` (`controls` for Tasty)
- Modify: `plugins/Blender/fortnite_porting/operator/rig_ui.py` (Tasty branch)
- Test: `tests/plugin/rig_check.py`

**Interfaces:**
- Produces: `tasty_style.style(obj) -> bool` (False when already styled), `tasty_style.SHOWN`, `tasty_style.HIDDEN`,
  `tasty_style.MARK = "fpmp_tasty_styled"`; `rig_style.TASTY_CONTROLS = ("Rig", "Face")`.

- [ ] **Step 1: Write the failing tests**

```python
# Tasty on the kit: a Tasty-like armature (FP's groups, shaped bones)
from fpmp_baseline.processing.context import tasty_style  # noqa: E402
ta = bpy.data.objects.new("tasty", bpy.data.armatures.new("tasty"))
bpy.context.scene.collection.objects.link(ta)
ta.data["is_tasty"] = True
bpy.context.view_layer.objects.active = ta
bpy.ops.object.mode_set(mode='EDIT')
for n, x in (("root", 0), ("ik_hand_l", 0.3), ("ik_hand_r", -0.3), ("spine_01", 0), ("R_eye", -0.03), ("dyn_hair_l", 0.05),
             ("upperarm_twist_01_l", 0.2), ("thigh_l", 0.1)):
    b = ta.data.edit_bones.new(n)
    b.head, b.tail = (x, 0, 1), (x, 0, 1.1)
bpy.ops.object.mode_set(mode='POSE')
cube = rig_shapes.ensure("CR_Box")
for g, members in (("Rig", ("root", "ik_hand_l", "ik_hand_r", "spine_01")), ("Face", ("R_eye",)), ("Dynamic", ("dyn_hair_l",)),
                   ("Twist", ("upperarm_twist_01_l",)), ("Base", ("thigh_l",)), ("Deform", ()), ("Sockets", ()), ("Extra", ())):
    c = ta.data.collections.new(g)
    for m in members:
        c.assign(ta.data.bones[m])
for n in ("root", "ik_hand_l", "ik_hand_r", "spine_01", "R_eye", "dyn_hair_l", "upperarm_twist_01_l"):
    ta.pose.bones[n].custom_shape = cube
    ta.pose.bones[n].custom_shape_wire_width = 4.0
bpy.ops.object.mode_set(mode='OBJECT')
check("tasty styled", tasty_style.style(ta), True)
check("tasty styled once", tasty_style.style(ta), False)
tp = ta.pose.bones
check("tasty visibility", {c.name: c.is_visible for c in ta.data.collections},
      {"Rig": True, "Face": True, "Dynamic": False, "Twist": False, "Base": False, "Deform": False, "Sockets": False, "Extra": False})
check("tasty root main", tuple(tp["root"].color.custom.normal), rig_style.COLORS["main"], tol=0.01)
check("tasty root width", tp["root"].custom_shape_wire_width, 3.5)
check("tasty left IK blue", tuple(tp["ik_hand_l"].color.custom.normal), rig_style.COLORS["L"], tol=0.01)
check("tasty right IK red", tuple(tp["ik_hand_r"].color.custom.normal), rig_style.COLORS["R"], tol=0.01)
check("tasty spine centre", tuple(tp["spine_01"].color.custom.normal), rig_style.COLORS["C"], tol=0.01)
check("tasty R_ prefix is right", tuple(tp["R_eye"].color.custom.normal), tuple(c + (1 - c) * 0.5 for c in rig_style.COLORS["R"]), tol=0.01)
check("tasty face thin", tp["R_eye"].custom_shape_wire_width, 1.5)
check("tasty twist keeps width", tp["upperarm_twist_01_l"].custom_shape_wire_width, 4.0)
check("tasty drawn in front", ta.show_in_front, True)
check("tasty controls", sorted(b.name for b in rig_style.controls(ta)), ["R_eye", "ik_hand_l", "ik_hand_r", "root", "spine_01"])
```

- [ ] **Step 2: Run, expect FAIL** — `ImportError: cannot import name 'tasty_style'`.

- [ ] **Step 3: Implement**

`tasty_style.py`:
```python
"""FP's character rig (Tasty) in the rig kit's colours and visibility, applied after FP builds it (FP's files untouched).

Tasty's groups: Rig (root, IK, poles, spine, fingers) and Face stay shown; Dynamic (hair, cloth), Base (the plain
skeleton), Twist, Deform, Sockets and Extra are hidden, one click away in the Rig panel. Tasty's shapes are kept."""
import re

from . import face_board, rig_style

SHOWN = ("Rig", "Face")
HIDDEN = ("Dynamic", "Base", "Twist", "Deform", "Sockets", "Extra")
SECONDARY = ("Face", "Dynamic")
MARK = "fpmp_tasty_styled"
SIDE = re.compile(r"(^|_)(?P<s>[lr])$|^(?P<p>[lr])_", re.IGNORECASE)


def side(name):
    m = SIDE.search(name)
    if m is None:
        return "C"
    return (m.group("s") or m.group("p")).upper()


def style(obj):
    """Restyle a Tasty armature once; False when it already was."""
    data = obj.data
    if data.get(MARK):
        return False
    for collection in data.collections_all:
        collection.is_visible = collection.name in SHOWN
    for bone in obj.pose.bones:
        if bone.custom_shape is None:
            continue
        groups = {c.name for c in bone.bone.collections}
        if bone.name == "root":
            rig_style.style(bone, "main")
        elif groups & set(SECONDARY):
            rig_style.style(bone, side(bone.name), secondary=True)
        elif "Rig" in groups:
            rig_style.style(bone, side(bone.name))
        else:
            rig_style.style(bone, side(bone.name), width=bone.custom_shape_wire_width)
    obj.show_in_front = True            # controls inside the body stay clickable
    data[MARK] = True
    return True


def add_face_board(obj):
    """A face board on the head when the character's face is a flipbook (or LEGO) material."""
    if "head" not in obj.data.bones:
        return False
    span = [obj.data.bones[n].head_local for n in ("upperarm_l", "upperarm_r") if n in obj.data.bones]
    size = (span[0] - span[1]).length * 0.5 if len(span) == 2 else None
    return face_board.add(obj, head="head", size=size)
```

`rig_style.py`: below `LEGACY`:
```python
TASTY_CONTROLS = ("Rig", "Face")      # FP's character rig's control groups
```
and `controls`:
```python
def controls(obj):
    """The pose bones an animator poses: in Controls, Secondary, FK (or an older rig's control groups, or Tasty's)."""
    names = TASTY_CONTROLS if obj.data.get("is_tasty") else COLLECTIONS[:2] + (FK,) + LEGACY_CONTROLS
    shown = [obj.data.collections[n] for n in names if n in obj.data.collections]
    return [pb for pb in obj.pose.bones if any(pb.name in c.bones for c in shown)]
```

`rig_ui.py` draw: before the `if not any(data.get(k) for k in ("is_vehicle_rig", "is_creature_rig", "is_lego_rig")):`
check, handle Tasty:
```python
        if data.get("is_tasty"):
            from ..processing.context import tasty_style
            grid = col.grid_flow(columns=2, align=True)
            for group in [c for c in data.collections if c.name in tasty_style.SHOWN + tasty_style.HIDDEN]:
                grid.prop(group, "is_visible", text=group.name, toggle=True, icon='HIDE_OFF' if group.is_visible else 'HIDE_ON')
            col.operator(FPMP_OT_RigSelectControls.bl_idname, text="Select Controls")
            row = col.row(align=True)
            row.operator(FPMP_OT_RigResetPose.bl_idname, text="Reset Selected").scope = 'SELECTED'
            row.operator(FPMP_OT_RigResetPose.bl_idname, text="Reset All").scope = 'ALL'
            face_board.ui(col, obj)
            return
```

- [ ] **Step 4: Run, expect PASS.**

- [ ] **Step 5: Commit** — propose `Restyle Tasty with the rig kit`.

---

### Task 3: Hooks: Tasty imports, creatures, sprites

**Files:**
- Modify: `plugins/Blender/fortnite_porting/material_porter/mesh_hooks.py` (`after_parts`)
- Modify: `plugins/Blender/fortnite_porting/processing/context/creature_rig.py` (a board at the end of `create`)
- Test: `tests/plugin/rig_check.py`

**Interfaces:**
- Consumes: `tasty_style.style/add_face_board`, `face_board.add`.

- [ ] **Step 1: Write the failing tests**

```python
# creature_rig.create adds a board for a flipbook face
qb = quadruped("quad_board_on_create", eyes=False)
check("creature without a face: no board", "CR_FaceBoard" in qb.data.bones, False)
# a quadruped skeleton built, then a flipbook face attached BEFORE the rig is made
qc = quadruped_bones("quad_face_first")
attach_face(qc, "quad_face_first", FLIP)
creature_rig.create(qc)
check("creature rig adds the board", "CR_FaceBoard" in qc.data.bones, True)
# Tasty: the hook's restyle + board, via tasty_style
tf = ta.copy(); tf.data = ta.data.copy(); bpy.context.scene.collection.objects.link(tf)
tf.data[tasty_style.MARK] = False
bpy.context.view_layer.objects.active = tf
bpy.ops.object.mode_set(mode='EDIT')
hb = tf.data.edit_bones.new("head")
hb.head, hb.tail = (0, 0, 1.5), (0, 0, 1.7)
bpy.ops.object.mode_set(mode='OBJECT')
attach_face(tf, "tasty_face", FLIP)
check("tasty board", tasty_style.add_face_board(tf), True)
check("tasty board once", tasty_style.add_face_board(tf), False)
```

For this test, split the quadruped builder: `quadruped_bones(name, eyes=True, tail_on_root=False, scapula=False)` builds the
skeleton and returns it without rigging; `quadruped(...)` calls it then `creature_rig.create(obj)` (refactor the existing
builder accordingly; existing calls keep working).

- [ ] **Step 2: Run, expect FAIL** — the creature rig doesn't add a board.

- [ ] **Step 3: Implement**

`creature_rig.create`, at the very end before `return`, after `bpy.ops.object.mode_set(mode='OBJECT')`:
```python
    from . import face_board
    face = face_board.add(obj, head=survey.head, left=Vector((0.0, 0.0, 1.0)).cross(ahead))
```
and add `", a face board" if face else ""` to the returned summary line (append to `parts` before the join:
`parts.append("face board")` when `face`).

`mesh_hooks.after_parts`, right after `settle_effects(ctx)`:
```python
    # FP's character rig in the kit's colours, and a face board when the face is a flipbook
    if rig_type == tasty:
        from ..processing.context import tasty_style
        for skeleton in {m.get("Skeleton") for m in ctx.imported_meshes if _alive(m.get("Skeleton"))}:
            if skeleton.data.get("is_tasty") and tasty_style.style(skeleton):
                tasty_style.add_face_board(skeleton)
```
move the local `armature(o)` helper to module level as `_alive(o)` (same body) and use it in both places; add
`EExportType.SPRITE` to the creature-rig list (the `ctx.type not in [...]` list).

- [ ] **Step 4: Run, expect PASS.**

- [ ] **Step 5: Commit** — propose `Add face boards to Tasty, creatures and sprites`.

---

### Task 4: Real assets, renders, suites

- [ ] **Step 1:** Nemia with Tasty: `fpdev export nemia --type Outfit --path /BRCosmetics/Athena/Items/Cosmetics/Characters/Character_PocketScrunchie --option RigType=1`
  (exists), `fpdev import nemia`, `fpdev render nemia --controls front,top,side`; before = the sheet rendered before Task 2
  (`sheet_before.png`).
- [ ] **Step 2:** Air Sprite: `fpdev import sprite` (payload exists; re-export with `--type Sprite --path /SpriteLibrary_CH7S3/SpriteDefinitions/AirSprite/ESD_AirSprite`
  if needed); probe the rig and board (`arm_probe.py`, `board_probe.py`-like); a face render with eyes and mouth moved
  through the board (`fpdev render sprite --frame <face material word>` before/after).
- [ ] **Step 3:** One flipbook BR outfit: find one whose body material lists `flipbook_face_mouth_index`
  (`fpdev find` the outfits named in the spec: WartyBrine, BlockStack, InfoInvader, VerTet, SaltyBrim, SpeedyPeas → their
  Character_ item via the Outfit loader), import with Tasty, check the board.
- [ ] **Step 4:** Beebo (sidekick, 3D eyes): import, no board, creature rig unchanged.
- [ ] **Step 5:** All suites green.
