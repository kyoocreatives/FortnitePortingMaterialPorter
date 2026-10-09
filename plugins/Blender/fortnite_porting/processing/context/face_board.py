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
STEP_OF_HEAD = 0.06     # a slider step, in head widths
ROW = 2.5               # rows apart, in steps
SIZE = 0.6              # a slider's diamond and a pad's square, in steps


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
    step = size * STEP_OF_HEAD
    rows = len(poses) + (1 if pads else 0)
    at = edit[head].head + left * size * 1.4 + up * size * 0.2
    board = edit.new(BOARD)
    board.head, board.tail = at, at + up * step * ROW
    board.align_roll(left.cross(up))            # board X along the figure's left (expressions count up that way)
    board.parent, board.use_deform = edit[head], False
    for i, (key, name, _) in enumerate(poses):
        # rests at its imported expression along the board (its left edge is expression 0)
        b = edit.new(PREFIX + name)
        b.head = at + left * step * round(faces[0][1][key].default_value) + up * step * ROW * (rows - 1 - i)
        b.tail = b.head + up * step * 0.5
        b.align_roll(left.cross(up))
        b.parent, b.use_deform = board, False
    for j, (_, name, _) in enumerate(pads):
        b = edit.new(PREFIX + name + "_UV")
        b.head = at + left * step * (2.0 + 5.0 * j)
        b.tail = b.head + up * step * 0.5
        b.align_roll(left.cross(up))
        b.parent, b.use_deform = board, False
    return True


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


def wire(obj, faces, size):
    """Shapes, limits and drivers (pose mode), for the bones `bones` made."""
    pose = obj.pose.bones
    have = _inputs(faces)
    step = size * STEP_OF_HEAD
    obj.data[STEP] = step
    board = pose[BOARD]
    poses = [p for p in POSES if PREFIX + p[1] in pose]
    pads = [p for p in PADS if PREFIX + p[1] + "_UV" in pose]
    board.custom_shape = rig_shapes.ensure("CR_Square")
    board.use_custom_shape_bone_size = False
    # frame: expressions 0-15 across (a step of margin), every row up
    rows = len(poses) + (1 if pads else 0)
    board.custom_shape_scale_xyz = (step * (MOST + 2) * 0.5, step * ROW * rows * 0.5, 1.0)
    board.custom_shape_translation = (step * MOST * 0.5, step * ROW * (rows - 1) * 0.5, 0.0)
    rig_style.style(board, "C")
    rig_style.assign(obj.data, BOARD, "Controls")
    board.lock_location = board.lock_rotation = board.lock_scale = (True, True, True)
    for key, name, role in poses:
        bone = pose[PREFIX + name]
        rest = round(faces[0][1][key].default_value)
        bone.custom_shape = rig_shapes.ensure("CR_Diamond")
        bone.use_custom_shape_bone_size = False
        bone.custom_shape_scale_xyz = (step * SIZE,) * 3
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
        bone.custom_shape_scale_xyz = (step * SIZE,) * 3
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
