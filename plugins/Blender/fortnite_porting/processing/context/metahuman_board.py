"""Epic's MetaHuman face board (Face_ControlBoard_CtrlRig) on FP's MetaHuman-style heads: its controls drive the head's
expression shape keys through Epic's own control-to-curve mapping, decoded once into metahuman_board.json (spec
2026-10-09-metahuman-board), or its bones through RigLogic. On a legacy (pre-3L) head the board's 3L curves reach its
*_pose shape keys through Fortnite's 3L-to-legacy mapping (metahuman_legacy.json), as the game plays 3L faces there."""
import gzip
import json
import math
import os

import bpy
from mathutils import Vector

from . import face_board, rig_shapes, rig_style

DATA = os.path.join(os.path.dirname(__file__), "metahuman_board.json")
LEGACY = os.path.join(os.path.dirname(__file__), "metahuman_legacy.json")     # FN_3LToLegacy_Main_Mapping
DRAWING = os.path.join(os.path.dirname(__file__), "metahuman_board_shape.json.gz")      # Epic's faceboard_2x gizmo
_cache = {}


def load():
    if "data" not in _cache:
        with open(DATA, encoding="utf-8") as f:
            _cache["data"] = json.load(f)
    return _cache["data"]


def load_legacy():
    if "legacy" not in _cache:
        with open(LEGACY, encoding="utf-8") as f:
            _cache["legacy"] = json.load(f)
    return _cache["legacy"]


def load_drawing():
    """Epic's board drawing: flat [right, up] pairs in board units (cm from the board control), and its faces."""
    if "drawing" not in _cache:
        with gzip.open(DRAWING, "rt", encoding="utf-8") as f:
            _cache["drawing"] = json.load(f)
    return _cache["drawing"]


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def evaluate(node, values):
    """A formula's value; values[(control, axis)], missing ones 0."""
    op = node["op"]
    if op == "control":
        return values.get((node["name"], node["axis"]), 0.0)
    if op == "const":
        return node["value"]
    if op == "add":
        return evaluate(node["a"], values) + evaluate(node["b"], values)
    if op == "lerp":
        a, b, t = (evaluate(node[k], values) for k in ("a", "b", "t"))
        return a + (b - a) * t
    x = evaluate(node["in"], values)
    if op == "remap":
        (a, b), (c, d) = node["from"], node["to"]
        t = (x - a) / (b - a) if b != a else 0.0
        if node["clamp"]:
            t = _clamp(t, 0.0, 1.0)
        return c + (d - c) * t
    if op == "interp":
        # UE's scale-bias-clamp: an unclamped range map, then scale and bias, then the clamp
        if node.get("range_in"):
            (a, b), (c, d) = node["range_in"], node["range_out"]
            x = c + (d - c) * ((x - a) / (b - a) if b != a else 0.0)
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
    if op == "add":
        return "(%s+%s)" % (expression(node["a"], var), expression(node["b"], var))
    if op == "lerp":
        a, b, t = (expression(node[k], var) for k in ("a", "b", "t"))
        return "(%s+(%s-%s)*%s)" % (a, b, a, t)
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
            x = "(%s+%s*(%s-%s)/%s)" % (_num(c), _num(d - c), x, _num(a), _num(b - a)) if b != a else _num(c)
        x = "(%s*%s+%s)" % (x, _num(node["scale"]), _num(node["bias"]))
        if node.get("clamp"):
            x = "max(%s,min(%s,%s))" % (_num(node["clamp"][0]), _num(node["clamp"][1]), x)
        return x
    if op == "curve":
        # piecewise linear: the first key's value plus each segment's rise, its share clamped to the segment
        keys = node["keys"]
        terms = [_num(keys[0][1])]
        for (t0, v0), (t1, v1) in zip(keys, keys[1:]):
            if t1 != t0:
                terms.append("%s*max(0.0,min(1.0,(%s-%s)/%s))" % (_num(v1 - v0), x, _num(t0), _num(t1 - t0)))
        return "(%s)" % "+".join(terms)
    raise ValueError(op)


# --- the board in Blender
MARK, MESHES, STEP = "fpmp_metahuman_board", "fpmp_metahuman_board_meshes", "fpmp_metahuman_board_step"
MATERIALS = "fpmp_metahuman_board_materials"
OLD = "fpmp_metahuman_board_legacy"           # a legacy head: the board drives its *_pose keys
BONES = "fpmp_metahuman_board_bones"         # the board drives the facial bones (RigLogic), not the shape keys
PREFIX = "MB_"
MASTER = PREFIX + "Master"      # the handle that moves the board: the drawing itself can't be picked
OFF_BOARD = (None, "faceAndEyesAimFollowHead")       # Epic's eye-aim frame beside the board: needs its aim solve
BOARD = PREFIX + "Board"
ENOUGH = 20         # expression shape keys a head needs to count as MetaHuman-style


def _meshes(obj):
    return [o for o in bpy.data.objects if o.type == 'MESH' and o.data.shape_keys is not None and
            (o.parent == obj or any(m.type == 'ARMATURE' and m.object == obj for m in o.modifiers))]


def _keys(obj, data):
    names = {n.replace("CTRL_expressions_", "") for n in data["curves"]}
    return [(m, k) for m in _meshes(obj) for k in m.data.shape_keys.key_blocks if k.name in names]


def _legacy_keys(obj):
    names = load_legacy()["curves"]
    return [(m, k) for m in _meshes(obj) for k in m.data.shape_keys.key_blocks if k.name in names]


def _legacy_inputs(obj):
    """[(material, group input, legacy curve)]: material inputs named after mapped legacy curves. A flipbook face
    (Peely's) picks its cell from them, as the game drives them from the face's curves."""
    names = {n.lower(): n for n in load_legacy()["curves"]}
    out, seen = [], set()
    for m in face_board._meshes(obj):
        for slot in m.material_slots:
            mat = slot.material
            if mat is None or mat.node_tree is None or mat.name in seen:
                continue
            seen.add(mat.name)
            out += [(mat, i, names[i.name.lower()]) for n in mat.node_tree.nodes if n.type == 'GROUP'
                    for i in n.inputs if i.type == 'VALUE' and not i.is_linked and i.name.lower() in names]
    return out


def _socket_path(socket):
    """A group input's value path by its identifier: the material builder may add inputs before it later, which
    shifts an index path onto another input."""
    return '%s.inputs["%s"].default_value' % (socket.node.path_from_id(), socket.identifier)


def _legacy(obj):
    """A legacy head (faceAttach, no FACIAL bones) with enough of the mapped *_pose keys, or a material reading them."""
    return "faceAttach" in obj.data.bones and "FACIAL_C_FacialRoot" not in obj.data.bones and \
        (len(_legacy_keys(obj)) >= ENOUGH or bool(_legacy_inputs(obj)))


def fits(obj):
    """A MetaHuman-style head (FACIAL bones and enough of the expression shape keys), or a legacy head."""
    return obj is not None and obj.type == 'ARMATURE' and "head" in obj.data.bones and (
        "FACIAL_C_FacialRoot" in obj.data.bones and len(_keys(obj, load())) >= ENOUGH or _legacy(obj))


def step(obj):
    """Metres per board unit on this armature's board."""
    return obj.data[STEP]


def _read(node, out):
    """The controls an expression reads."""
    if node.get("op") == "control":
        out.add(node["name"])
    for k in ("a", "b", "t", "in"):
        if isinstance(node.get(k), dict):
            _read(node[k], out)
    return out


def _layout(data, size, live):
    """(metres per board unit, each knob's place: right and up from the board control, in metres). `size`: the
    drawing's width in metres."""
    xs = load_drawing()["verts"][0::2]
    s = size / (max(xs) - min(xs))
    return s, {c["name"]: (c["position"][0] * s, c["position"][1] * s) for c in data["controls"]
               if c["kind"] in ("slider", "box") and c["name"] in live and c["group"] not in OFF_BOARD}


def _bones(obj, data, size, live, clear):
    """Board and knob bones (edit mode): the board beside the head, knobs at Epic's places, pointing into the board
    so their local X runs right and Z up."""
    edit = obj.data.edit_bones
    head = edit["head"]
    s, places = _layout(data, size, live)
    v = load_drawing()["verts"]
    ups = v[1::2]
    # the drawing's left edge past the head mesh, its middle a head length above the head bone
    origin = head.head + Vector((clear - min(v[0::2]) * s, 0.0, head.length - (min(ups) + max(ups)) / 2 * s))
    master = edit.new(MASTER)        # above the drawing's top left corner
    master.head = origin + Vector((min(v[0::2]) * s, 0.0, max(ups) * s + size * 0.04))
    master.tail = master.head + Vector((0.0, size * 0.1, 0.0))
    master.align_roll(Vector((0.0, 0.0, 1.0)))
    master.parent, master.use_deform = head, False
    board = edit.new(BOARD)
    board.head, board.tail = origin, origin + Vector((0.0, size * 0.1, 0.0))
    board.align_roll(Vector((0.0, 0.0, 1.0)))
    board.parent, board.use_deform = master, False
    board.hide_select = True         # drag-selecting knobs never grabs the drawing
    axes = {c["name"]: c["axes"] for c in data["controls"]}
    for name, (right, up) in places.items():
        # local Z along Epic's y, local X along its x; a mirrored control's bone points out of the board instead
        slide = _plane(axes[name]["y"]) if _length(axes[name]["y"]) > 1e-6 else Vector((0.0, 0.0, 1.0))
        across = _plane(axes[name]["x"])
        into = Vector((0.0, 1.0, 0.0))
        if _length(axes[name]["x"]) > 1e-6 and into.cross(slide).dot(across) < 0.0:
            into = -into
        b = edit.new(PREFIX + name)
        b.head = origin + Vector((right, 0.0, up))
        b.tail = b.head + into * size * 0.03
        b.align_roll(slide)
        b.parent, b.use_deform = board, False
    return s


def _length(v):
    return (v[0] ** 2 + v[1] ** 2) ** 0.5


def _plane(v):
    """A board vector (right, up) in the armature's space (the board stands in its X/Z plane)."""
    return Vector((v[0], 0.0, v[1])).normalized() if _length(v) > 1e-9 else Vector()


def unit(obj, control, axis):
    """Metres the knob moves for one unit of the control's value along `axis` ("x" or "y")."""
    c = next(c for c in load()["controls"] if c["name"] == control)
    return _length(c["axes"][axis]) * step(obj)


def _drawing(s):
    """Epic's board drawing as the board bone's custom shape, at `s` metres per board unit."""
    d = load_drawing()
    v = d["verts"]
    mesh = bpy.data.meshes.new("MB_BoardShape")
    mesh.from_pydata([(v[i] * s, 0.0, v[i + 1] * s) for i in range(0, len(v), 2)], d["edges"], d["faces"])
    return bpy.data.objects.new("MB_BoardShape", mesh)


def _sphere():
    """Epic's knob gizmo (a solid sphere), radius 1."""
    obj = bpy.data.objects.get("MB_Knob")
    if obj is not None and obj.type == 'MESH':
        return obj
    rings, segments = 6, 12
    verts = [(0.0, 0.0, 1.0)] + [(math.sin(math.pi * r / rings) * math.cos(2 * math.pi * k / segments),
                                  math.sin(math.pi * r / rings) * math.sin(2 * math.pi * k / segments),
                                  math.cos(math.pi * r / rings)) for r in range(1, rings) for k in range(segments)] + [(0.0, 0.0, -1.0)]
    ring = lambda r, k: 1 + (r - 1) * segments + k % segments
    faces = [(0, ring(1, k), ring(1, k + 1)) for k in range(segments)]
    faces += [(ring(r, k), ring(r + 1, k), ring(r + 1, k + 1), ring(r, k + 1)) for r in range(1, rings - 1) for k in range(segments)]
    faces += [(len(verts) - 1, ring(rings - 1, k + 1), ring(rings - 1, k)) for k in range(segments)]
    mesh = bpy.data.meshes.new("MB_Knob")
    mesh.from_pydata(verts, [], faces)
    return bpy.data.objects.new("MB_Knob", mesh)


def _knob(obj, c, s):
    pb = obj.pose.bones[PREFIX + c["name"]]
    pb.custom_shape = _sphere()
    pb.use_custom_shape_bone_size = False
    pb.custom_shape_scale_xyz = (c["radius"] * s,) * 3
    rig_shapes.color(pb, tuple(c["color"]))
    limits = c["limits"]
    pb.lock_location = (not limits.get("x"), True, not limits.get("y"))
    pb.lock_rotation = pb.lock_scale = (True, True, True)
    limit = pb.constraints.new('LIMIT_LOCATION')
    limit.name, limit.owner_space, limit.use_transform_limit = "MB limit", 'LOCAL', True
    for axis, attr in (("x", "x"), ("y", "z")):
        lo, hi = limits.get(axis) or (0.0, 0.0)
        reach = _length(c["axes"][axis]) * s        # metres per unit of the control's value
        setattr(limit, "use_min_" + attr, True)
        setattr(limit, "use_max_" + attr, True)
        setattr(limit, "min_" + attr, lo * reach)
        setattr(limit, "max_" + attr, hi * reach)
    area = "Face " + c["group"][0].upper() + c["group"][1:]      # Epic's GRP_<area>GUI
    group = obj.data.collections_all.get(area) or obj.data.collections.new(area, parent=_group(obj))
    group.assign(obj.data.bones[pb.name])


def _group(obj, name=None):
    """The kit's Controls, or the face board's own group on a rig without the kit's (`name` goes in it)."""
    if "Controls" in obj.data.collections:
        if name:
            rig_style.assign(obj.data, name, "Controls")
        return obj.data.collections["Controls"]
    group = obj.data.collections.get(face_board.GROUP) or obj.data.collections.new(face_board.GROUP)
    if name:
        group.assign(obj.data.bones[name])
    return group


def _drive(obj, key, node, s, axes, built):
    driver = key.driver_add("value").driver
    driver.type = 'SCRIPTED'
    driver.expression = _expression(obj, driver, node, s, axes, built)


def _expression(obj, driver, node, s, axes, built):
    """A board formula as the driver's expression, its knobs added as the driver's variables."""
    used = {}

    def var(control, axis):
        if control not in built:
            return "0.0"        # a control off the board (Epic's eye-aim frame)
        if (control, axis) not in used:
            used[(control, axis)] = "v%d" % len(used)
            v = driver.variables.new()
            v.name, v.type = used[(control, axis)], 'TRANSFORMS'
            v.targets[0].id, v.targets[0].bone_target = obj, PREFIX + control
            v.targets[0].transform_type = 'LOC_X' if axis == "x" else 'LOC_Z'
            v.targets[0].transform_space = 'LOCAL_SPACE'
        reach = _length(axes[control][axis]) * s
        return "(%s/%s)" % (used[(control, axis)], _num(reach)) if reach > 1e-9 else "0.0"
    return expression(node, var)


def _rig(obj, dna):
    """The head's RigLogic data, or None (no file, or unreadable: the board drives the shape keys instead)."""
    if not dna:
        return None
    from . import riglogic_read
    try:
        return riglogic_read.read(dna)
    except (OSError, riglogic_read.RigLogicError) as e:
        print("[MetaHuman board] %s: RigLogic data unreadable (%s), shape keys instead" % (obj.name, e))
        return None


def add(obj, size=None, dna=None):
    """The board beside the head, its knobs driving the face: the facial bones through the head's RigLogic data (`dna`,
    a .rigdna file), else the expression shape keys. Returns the drivers made on raw controls or shape keys (0 when
    the armature doesn't fit or already has its board)."""
    data = load()
    if obj.data.get(MARK) or not fits(obj):
        return 0
    size = size or max(obj.data.bones["head"].length * 5.0, 0.3)
    legacy = _legacy(obj)
    keys = _legacy_keys(obj) if legacy else _keys(obj, data)
    rig = None if legacy else _rig(obj, dna)
    if legacy:
        # what the board drives: shape keys and material inputs, each with its legacy curve
        targets = [(k, "value", k.name) for _, k in keys] + [(i.id_data, _socket_path(i), c) for _, i, c in _legacy_inputs(obj)]
        # the 3L curves those curves read, as the board's formulas give them
        stacks = load_legacy()["curves"]
        formulas = {e["Value"]: data["curves"][e["Value"]] for _, _, c in targets for e in stacks[c]
                    if e["ElementType"] == 1 and e["Value"] in data["curves"]}
        live = set().union(*(_read(node, set()) for node in formulas.values()))
    elif rig is not None:
        # the raw controls the board has formulas for; the knobs are what those formulas read
        formulas = {n: data["curves"][n.replace(".", "_")] for n in rig["raw"] if n.replace(".", "_") in data["curves"]}
        live = set().union(*(_read(node, set()) for node in formulas.values()))
    else:
        # FP heads carry about a third of Epic's expression curves: knobs nothing reads aren't built
        live = set().union(*(_read(data["curves"]["CTRL_expressions_" + k.name], set()) for _, k in keys))
    clear = max(obj.data.bones["head"].length * 1.5, face_board.clearance(obj, "head", Vector((1.0, 0.0, 0.0))) + size * 0.1)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')
    s = _bones(obj, data, size, live, clear)
    bpy.ops.object.mode_set(mode='POSE')
    obj.data[STEP] = s
    board = obj.pose.bones[BOARD]
    board.custom_shape, board.use_custom_shape_bone_size = _drawing(s), False
    board.lock_location = board.lock_rotation = board.lock_scale = (True, True, True)
    rig_shapes.color(board, (0.55, 0.55, 0.6))
    _group(obj, BOARD)
    handle = obj.pose.bones[MASTER]
    handle.custom_shape, handle.use_custom_shape_bone_size = rig_shapes.ensure("CR_Square"), False
    handle.custom_shape_scale_xyz = (size * 0.03,) * 3
    handle.custom_shape_rotation_euler = (1.5708, 0.0, 0.0)      # the square faces the viewer, as the board does
    rig_shapes.color(handle, (0.55, 0.55, 0.6))
    _group(obj, MASTER)
    for c in data["controls"]:
        if PREFIX + c["name"] in obj.pose.bones:
            _knob(obj, c, s)
    bpy.ops.object.mode_set(mode='OBJECT')
    axes = {c["name"]: c["axes"] for c in data["controls"]}
    built = {b.name[len(PREFIX):] for b in obj.data.bones if b.name.startswith(PREFIX)}
    if legacy:
        _legacy_drive(obj, targets, formulas, lambda driver, node: _expression(obj, driver, node, s, axes, built))
        obj.data[OLD] = True
        obj.data[MATERIALS] = sorted({m.name for m, _, _ in _legacy_inputs(obj)})
        made = len(targets)
    elif rig is not None:
        from . import riglogic_face
        riglogic_face.build(obj, rig, formulas, lambda driver, node: _expression(obj, driver, node, s, axes, built))
        for _, key in keys:         # the bones carry the face: its shape keys rest while the board is on
            key.driver_add("value").driver.expression = "0"
        obj.data[BONES] = True
        made = len(formulas)
    else:
        for _, key in keys:
            _drive(obj, key, data["curves"]["CTRL_expressions_" + key.name], s, axes, built)
        made = len(keys)
    obj.data[MARK] = True
    obj.data[MESHES] = sorted({m.name for m, _ in keys})
    if any(m.data.shape_keys.animation_data and (m.data.shape_keys.animation_data.action or
                                                 len(m.data.shape_keys.animation_data.nla_tracks)) for m, _ in keys):
        obj.fpmp_face_board = False         # the face is already animated (a lobby pose): it keeps playing
    return made


def _legacy_drive(obj, targets, formulas, write):
    """The board's 3L curves as props on its board bone, and each target (owner, path, legacy curve) their mapped sum."""
    from ...material_porter import curve_expressions
    from .driver_batch import Batch
    board = obj.pose.bones[BOARD]
    with Batch() as batch:
        for name, node in formulas.items():
            board[name] = 0.0
            driver = batch.new(board, '["%s"]' % name)
            driver.expression = write(driver, node)
        for owner, path, curve in targets:
            driver = batch.new(owner, path)
            driver.expression = curve_expressions.text(load_legacy()["curves"][curve], _reader(obj, driver, formulas))


def _reader(obj, driver, formulas):
    """name(3L curve) for curve_expressions.text: the driver's variable reading it on the board bone."""
    used = {}

    def name(curve):
        if curve not in formulas:
            return "0.0"        # a 3L curve the board doesn't make (Fortnite's own brow curves): at rest
        if curve not in used:
            used[curve] = "c%d" % len(used)
            v = driver.variables.new()
            v.name, v.type = used[curve], 'SINGLE_PROP'
            v.targets[0].id, v.targets[0].data_path = obj, 'pose.bones["%s"]["%s"]' % (BOARD, curve)
        return used[curve]
    return name


def set_on(obj, on):
    """Mute or unmute the board's shape-key drivers (the face's own keys play while they're muted)."""
    owners = [bpy.data.objects.get(n) for n in obj.data.get(MESHES, [])]
    owners = [o.data.shape_keys for o in owners if o is not None and o.data.shape_keys]
    owners += [m.node_tree for m in (bpy.data.materials.get(n) for n in obj.data.get(MATERIALS, [])) if m and m.node_tree]
    for owner in owners:
        ad = owner.animation_data
        for fc in (ad.drivers if ad else ()):
            if any(t.id == obj for v in fc.driver.variables for t in v.targets) or \
                    ((obj.data.get(BONES) or obj.data.get(OLD)) and not fc.driver.variables):
                fc.mute = not on
    if obj.data.get(BONES):
        from . import riglogic_face
        riglogic_face.set_on(obj, on)


def on_animation_import(obj):
    """An imported animation keys the face's shape keys: the board steps aside."""
    if obj is not None and obj.data.get(MARK):
        obj.fpmp_face_board = False
