"""A face board: controls beside the head that pick a face's expressions and nudge its features.

Some faces are texture atlases driven by their material's inputs: LEGO figures' (MouthPose, EyeLeftU...) and
Fortnite's flipbook faces (flipbook_face_mouth_index, FB_EyeUVOffsetX...: sprites, some outfits). Each slider steps an
expression index around the imported one; each pad moves a feature around its imported place. Toon faces (Peely's)
pick their mouth cell and their eye and brow pieces from the face's curves: their sliders step through the states the
game's material tells apart, setting those curves. Drivers on the material's inputs read the controls; the Face Board
switch mutes them so an emote's face keys play."""
import bpy
from mathutils import Vector

from . import rig_shapes, rig_style

BOARD = "CR_FaceBoard"
GROUP = "Face Board"     # where the board goes on a rig without the kit's groups (Tasty)
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
# Toon faces: per slider its states, each the curves it sets (SubUVTextures' mouth tests, Banana's eye and brow tests;
# exact names: R_Frown_pose and R_frown_pose are two inputs)
TOON = {"name": "toon", "sliders": (), "counts": {}, "pads": (),
        "states": (("Mouth", "C", (("Default", ()), ("Narrow", ("R_lip_corner_narrow_pose",)), ("Frown", ("R_frown_pose",)),
                                   ("Smile", ("R_smile_pose",)), ("Open", ("Jaw_open_Pose",)),
                                   ("Open Smile", ("Jaw_open_Pose", "R_smile_pose")),
                                   ("Open Frown", ("Jaw_open_Pose", "R_Frown_pose")))),
                   ("Eyes", "C", (("Open", ()), ("Blink", ("R_blink_pose",)), ("Squint", ("R_squint_inner_pose",)))),
                   ("Brows", "C", (("None", ()), ("Up", ("R_brow_up_pose",)), ("Raised", ("C_glabella_up_pose",)),
                                   ("Angry", ("C_glabella_down_pose",)))))}
KINDS = (LEGO, FLIPBOOK, TOON)
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
                if any(key in inputs for k in KINDS for key in _keys(k)):
                    found[mat.name] = (mat, inputs)
                    break
    return list(found.values())


def _keys(k):
    """The inputs that tell a face kind: its sliders' (lower case), or the curves its states set."""
    return [key for key, _, _ in k["sliders"]] + [c.lower() for _, _, states in k.get("states", ()) for _, cs in states for c in cs]


def _have(found):
    names = None
    for _, inputs in found:
        names = set(inputs) if names is None else names & set(inputs)
    return names or set()


def kind(found):
    have = _have(found)
    return next((k for k in KINDS if any(key in have for key in _keys(k))), None)


def count(found, key, inputs=None):
    """Expressions a slider steps through: the face's columns x rows when it has them, else COUNT."""
    columns_rows = kind(found)["counts"].get(key)
    inputs = inputs or found[0][1]
    if columns_rows and all(c in inputs for c in columns_rows):
        return max(int(round(inputs[columns_rows[0]].default_value * inputs[columns_rows[1]].default_value)), 1)
    return COUNT


def _range(found, key, inputs):
    """A material's imported expression and its last one (an imported index past the count stays reachable)."""
    rest = round(inputs[key].default_value)
    return rest, max(count(found, key, inputs) - 1, rest)


def _layout(found):
    k, have = kind(found), _have(found)
    sliders = [s for s in k["sliders"] if s[0] in have]
    pads = [p for p in k["pads"] if all(i in have for i in p[1])]
    return sliders, pads


def _states(found):
    """The toon face's state sliders: (name, role, [(label, curves)]) with the states its material has every curve for,
    a slider only where it has more than one."""
    have = _have(found)
    rows = [(name, role, [(label, cs) for label, cs in states if all(c.lower() in have for c in cs)])
            for name, role, states in kind(found).get("states", ())]
    return [r for r in rows if len(r[2]) > 1]


def bones(edit, found, parent, at, left, up, size):
    """Board and controls (edit mode) at `at`, parented to `parent`. False when the face has no slider input."""
    if kind(found) is None:
        return False
    sliders, pads = _layout(found)
    sliders = sliders + [(None, name, role) for name, role, _ in _states(found)]      # states rest at their first
    step = size * STEP_OF_HEAD
    rows = len(sliders) + (1 if pads else 0)
    board = edit.new(BOARD)
    board.head, board.tail = at, at + up * step * ROW
    board.align_roll(left.cross(up))            # board X along the figure's left (expressions count up that way)
    board.parent, board.use_deform = edit[parent], False
    for i, (key, name, _) in enumerate(sliders):
        # rests at its imported expression along the board (its left edge is expression 0)
        b = edit.new(PREFIX + name)
        rest = round(found[0][1][key].default_value) if key else 0
        b.head = at + left * step * rest + up * step * ROW * (rows - 1 - i)
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


def _assign(obj, bone, group):
    """Into the kit's group, or the board's own on a rig that doesn't have the kit's."""
    if group in obj.data.collections:
        rig_style.assign(obj.data, bone, group)
    else:
        (obj.data.collections.get(GROUP) or obj.data.collections.new(GROUP)).assign(obj.data.bones[bone])


def step_of(obj):
    return obj.data[STEP]


def _drive(mat, socket, obj, bone, axis, expression):
    # by name, not position: the importer re-files a material's inputs after the rig is built, and a driver made
    # with socket.driver_add points at an index
    fc = mat.node_tree.driver_add('nodes["%s"].inputs["%s"].default_value' % (socket.node.name, socket.name))
    driver = fc.driver
    # a face material another figure's board drove (the same outfit imported twice) follows this one
    while driver.variables:
        driver.variables.remove(driver.variables[0])
    fc.mute = False
    driver.type = 'SCRIPTED'
    var = driver.variables.new()
    var.name, var.type = "x", 'TRANSFORMS'
    var.targets[0].id = obj
    var.targets[0].bone_target = bone
    var.targets[0].transform_type = 'LOC_' + axis
    var.targets[0].transform_space = 'LOCAL_SPACE'
    driver.expression = expression
    return fc


def _slider(obj, bone, role, step, rest, most):
    """A slider's diamond, sliding along the board from step 0 to `most`, resting at `rest`."""
    bone.custom_shape = rig_shapes.ensure("CR_Diamond")
    bone.use_custom_shape_bone_size = False
    bone.custom_shape_scale_xyz = (step * SIZE,) * 3
    rig_style.style(bone, role)
    _assign(obj, bone.name, "Controls")
    bone.lock_location, bone.lock_rotation, bone.lock_scale = (False, True, True), (True, True, True), (True, True, True)
    limit = bone.constraints.new('LIMIT_LOCATION')
    limit.owner_space = 'LOCAL'
    limit.use_min_x = limit.use_max_x = True
    limit.min_x, limit.max_x = -rest * step, (most - rest) * step
    limit.use_transform_limit = True


def wire(obj, found, size):
    """Shapes, limits and drivers (pose mode), for the bones `bones` made."""
    pose = obj.pose.bones
    step = size * STEP_OF_HEAD
    obj.data[STEP] = step
    sliders, pads = _layout(found)
    states = _states(found)
    widest = max([_range(found, key, found[0][1])[1] + 1 for key, _, _ in sliders] + [len(st) for _, _, st in states]
                 or [COUNT])
    board = pose[BOARD]
    board.custom_shape = rig_shapes.ensure("CR_Square")
    board.use_custom_shape_bone_size = False
    # frame: expressions 0 to the widest count across (a step of margin), every row up
    rows = len(sliders) + len(states) + (1 if pads else 0)
    board.custom_shape_scale_xyz = (step * (widest + 1) * 0.5, step * ROW * rows * 0.5, 1.0)
    board.custom_shape_translation = (step * (widest - 1) * 0.5, step * ROW * (rows - 1) * 0.5, 0.0)
    rig_style.style(board, "C")
    _assign(obj, BOARD, "Controls")
    board.lock_location = board.lock_rotation = board.lock_scale = (True, True, True)
    controls = {}
    for key, name, role in sliders:
        bone = pose[PREFIX + name]
        rest, most = _range(found, key, found[0][1])
        _slider(obj, bone, role, step, rest, most)
        for mat, inputs in found:
            # each face steps from its own imported expression, within its own count
            mat_rest, mat_most = _range(found, key, inputs)
            _drive(mat, inputs[key], obj, bone.name, "X", "max(0,min(%d,%d+round(x/%r)))" % (mat_most, mat_rest, step))
        controls[bone.name] = found[0][1][key].name
    for name, (x_key, y_key), role in pads:
        bone = pose[PREFIX + name + "_UV"]
        bone.custom_shape = rig_shapes.ensure("CR_Square")
        bone.use_custom_shape_bone_size = False
        bone.custom_shape_scale_xyz = (step * SIZE,) * 3
        rig_style.style(bone, role, secondary=True)
        _assign(obj, bone.name, "Secondary")
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
    for name, role, st in states:
        bone = pose[PREFIX + name]
        _slider(obj, bone, role, step, 0, len(st) - 1)
        for curve in sorted({c for _, cs in st for c in cs}):
            # 1 on the states that set it: the game's tests read it past their threshold
            text = "+".join("(1 if abs(x/%r-%d)<0.5 else 0)" % (step, k) for k, (_, cs) in enumerate(st) if curve in cs)
            for mat, inputs in found:
                socket = next(iter(inputs.values())).node.inputs.get(curve)
                if socket is not None:
                    _drive(mat, socket, obj, bone.name, "X", text)
    obj["fpmp_face_board_materials"] = [mat.name for mat, _ in found]
    obj["fpmp_face_board_inputs"] = controls
    obj["fpmp_face_board_states"] = {PREFIX + name: [label for label, _ in st] for name, _, st in states}


def clearance(obj, head, left):
    """How far the head's meshes reach beside the head bone along `left` (what's above the head bone: face, hair, hat),
    so a board placed past it never sits inside the head; FP's head bones are too short to tell."""
    import numpy as np
    base = obj.data.bones[head].head_local
    to_armature = obj.matrix_world.inverted()
    far = 0.0
    for o in _meshes(obj):
        n = len(o.data.vertices)
        if not n:
            continue
        co = np.empty(n * 3, dtype=np.float32)
        o.data.vertices.foreach_get("co", co)
        m = np.array(to_armature @ o.matrix_world)
        points = co.reshape(-1, 3) @ m[:3, :3].T + m[:3, 3]
        above = points[points[:, 2] >= base.z]
        if len(above):
            far = max(far, float(((above - np.array(base)) @ np.array(left)).max()))
    return far


def add(obj, head, left=None, up=Vector((0.0, 0.0, 1.0)), size=None):
    """A board on an existing rig (object mode in and out): beside `head`, or above the model on the root when
    there's no head bone. False when the armature has no face to drive or already has a board."""
    found = faces(obj)
    if not found or kind(found) is None or BOARD in obj.data.bones:
        return False
    left = left if left is not None else Vector((1.0, 0.0, 0.0))      # the figure faces -Y: its left is +X
    clear = clearance(obj, head, left) if head else 0.0
    view_layer = bpy.context.view_layer
    view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')
    edit = obj.data.edit_bones
    points = [p for b in edit for p in (b.head, b.tail)] or [Vector()]
    top = max(p.z for p in points)
    span = max(max(p[i] for p in points) - min(p[i] for p in points) for i in range(3))
    size = size or (edit[head].length if head else span * 0.25)
    if head:
        parent, at = head, edit[head].head + left * max(size * 1.4, clear * 1.15 + size * 0.5) + up * size * 0.2
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
    if BOARD not in obj.pose.bones and not obj.data.get("fpmp_metahuman_board"):
        return
    layout.prop(obj, "fpmp_face_board", text="Face Board", toggle=True)
    mats = [bpy.data.materials.get(n) for n in obj.get("fpmp_face_board_materials", [])]
    group = next((n for m in mats if m and m.node_tree for n in m.node_tree.nodes if n.type == 'GROUP'), None)
    if group is None:
        return
    for control, socket in obj.get("fpmp_face_board_inputs", {}).items():
        if socket in group.inputs:
            layout.label(text="%s: %d" % (control[len(PREFIX):], round(group.inputs[socket].default_value)))
    for control, labels in obj.get("fpmp_face_board_states", {}).items():
        if control in obj.pose.bones:
            k = max(0, min(len(labels) - 1, round(obj.pose.bones[control].location.x / obj.data[STEP])))
            layout.label(text="%s: %s" % (control[len(PREFIX):], labels[k]))


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
    if self.type == 'ARMATURE' and self.data.get("fpmp_metahuman_board"):
        from . import metahuman_board
        metahuman_board.set_on(self, self.fpmp_face_board)


def register():
    bpy.types.Object.fpmp_face_board = bpy.props.BoolProperty(
        name="Face Board", default=True, update=_toggled,
        description="The face board drives the face (off: the face's own animation plays, e.g. an emote's)")


def unregister():
    del bpy.types.Object.fpmp_face_board
