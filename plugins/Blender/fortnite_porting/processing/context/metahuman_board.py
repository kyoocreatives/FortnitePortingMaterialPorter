"""Epic's MetaHuman face board (Face_ControlBoard_CtrlRig) on FP's MetaHuman-style heads: its controls drive the head's
expression shape keys through Epic's own control-to-curve mapping, decoded once into metahuman_board.json (spec
2026-10-09-metahuman-board). The face's bones (MetaHuman's RigLogic) aren't driven."""
import json
import os

import bpy
from mathutils import Vector

from . import face_board, rig_shapes, rig_style

DATA =os.path.join(os.path.dirname(__file__), "metahuman_board.json")
_cache = {}


def load():
    if "data" not in _cache:
        with open(DATA, encoding="utf-8") as f:
            _cache["data"] = json.load(f)
    return _cache["data"]


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
PREFIX = "MB_"
BOARD = PREFIX + "Board"
ENOUGH = 20         # expression shape keys a head needs to count as MetaHuman-style


def _meshes(obj):
    return [o for o in bpy.data.objects if o.type == 'MESH' and o.data.shape_keys is not None and
            (o.parent == obj or any(m.type == 'ARMATURE' and m.object == obj for m in o.modifiers))]


def _keys(obj, data):
    names = {n.replace("CTRL_expressions_", "") for n in data["curves"]}
    return [(m, k) for m in _meshes(obj) for k in m.data.shape_keys.key_blocks if k.name in names]


def fits(obj):
    """A MetaHuman-style head: FACIAL bones and enough of the expression shape keys."""
    return obj is not None and obj.type == 'ARMATURE' and "FACIAL_C_FacialRoot" in obj.data.bones and \
        "head" in obj.data.bones and len(_keys(obj, load())) >= ENOUGH


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
    """(metres per board unit, each knob's place on the board: right and up from the board bone, in metres)."""
    knobs = [c for c in data["controls"] if c["kind"] in ("slider", "box") and c["name"] in live]
    xs, ys = [c["position"][0] for c in knobs], [c["position"][1] for c in knobs]
    s = size / max(max(xs) - min(xs), max(ys) - min(ys), 1e-6)
    middle = (min(xs) + max(xs)) / 2
    return s, {c["name"]: ((c["position"][0] - middle) * s, (c["position"][1] - min(ys)) * s) for c in knobs}


def _bones(obj, data, size, live):
    """Board and knob bones (edit mode): the board beside the head, knobs at Epic's places, pointing into the board
    so their local X runs right and Z up."""
    edit = obj.data.edit_bones
    head = edit["head"]
    s, places = _layout(data, size, live)
    origin = head.head + Vector((size * 0.8, 0.0, -size * 0.5))
    board = edit.new(BOARD)
    board.head, board.tail = origin, origin + Vector((0.0, size * 0.1, 0.0))
    board.align_roll(Vector((0.0, 0.0, 1.0)))
    board.parent, board.use_deform = head, False
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


def _outline(data, size, live):
    """The board's drawing (its bone's custom shape): a frame around the knobs, a track per slider over its range,
    a rectangle per 2D box over its ranges."""
    s, places = _layout(data, size, live)
    verts, edges = [], []

    def loop(points, closed):
        start = len(verts)
        verts.extend(points)
        edges.extend([(start + i, start + (i + 1) % len(points)) for i in range(len(points) if closed else len(points) - 1)])

    rights = [p[0] for p in places.values()]
    ups = [p[1] for p in places.values()]
    pad = size * 0.06
    loop([(min(rights) - pad, 0.0, min(ups) - pad), (max(rights) + pad, 0.0, min(ups) - pad),
          (max(rights) + pad, 0.0, max(ups) + pad), (min(rights) - pad, 0.0, max(ups) + pad)], True)
    def at(right, up, ax, ay, x, y):
        return (right + (ax[0] * x + ay[0] * y) * s, 0.0, up + (ax[1] * x + ay[1] * y) * s)

    for c in data["controls"]:
        if c["name"] not in places:
            continue
        right, up = places[c["name"]]
        ax, ay = c["axes"]["x"], c["axes"]["y"]
        if c["kind"] == "slider":
            lo, hi = c["limits"]["y"]
            loop([at(right, up, ax, ay, 0.0, lo), at(right, up, ax, ay, 0.0, hi)], False)
        else:
            (x0, x1), (y0, y1) = c["limits"]["x"], c["limits"]["y"]
            loop([at(right, up, ax, ay, x0, y0), at(right, up, ax, ay, x1, y0),
                  at(right, up, ax, ay, x1, y1), at(right, up, ax, ay, x0, y1)], True)
    mesh = bpy.data.meshes.new("MB_BoardShape")
    mesh.from_pydata(verts, edges, [])
    return bpy.data.objects.new("MB_BoardShape", mesh)


def _knob(obj, c, s):
    pb = obj.pose.bones[PREFIX + c["name"]]
    pb.custom_shape = rig_shapes.ensure("CR_Circle")
    pb.use_custom_shape_bone_size = False
    pb.custom_shape_scale_xyz = (s * 0.35,) * 3
    pb.custom_shape_rotation_euler = (1.5708, 0.0, 0.0)        # the circle faces the viewer
    rig_shapes.color(pb, (1.0, 0.85, 0.1))
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
    _group(obj, pb.name)


def _group(obj, name):
    """Into the kit's Controls, or the face board's own group on a rig without the kit's."""
    if "Controls" in obj.data.collections:
        rig_style.assign(obj.data, name, "Controls")
    else:
        (obj.data.collections.get(face_board.GROUP) or obj.data.collections.new(face_board.GROUP)).assign(obj.data.bones[name])


def _drive(obj, key, node, s, axes):
    driver = key.driver_add("value").driver
    driver.type = 'SCRIPTED'
    used = {}

    def var(control, axis):
        if (control, axis) not in used:
            used[(control, axis)] = "v%d" % len(used)
            v = driver.variables.new()
            v.name, v.type = used[(control, axis)], 'TRANSFORMS'
            v.targets[0].id, v.targets[0].bone_target = obj, PREFIX + control
            v.targets[0].transform_type = 'LOC_X' if axis == "x" else 'LOC_Z'
            v.targets[0].transform_space = 'LOCAL_SPACE'
        reach = _length(axes[control][axis]) * s
        return "(%s/%s)" % (used[(control, axis)], _num(reach)) if reach > 1e-9 else "0.0"
    driver.expression = expression(node, var)


def add(obj, size=None):
    """The board beside the head, its knobs driving the head's expression shape keys. Returns the drivers made
    (0 when the armature doesn't fit or already has its board)."""
    data = load()
    if obj.data.get(MARK) or not fits(obj):
        return 0
    size = size or max(obj.data.bones["head"].length * 5.0, 0.3)
    keys = _keys(obj, data)
    # FP heads carry about a third of Epic's expression curves: knobs nothing reads aren't built
    live = set().union(*(_read(data["curves"]["CTRL_expressions_" + k.name], set()) for _, k in keys))
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')
    s = _bones(obj, data, size, live)
    bpy.ops.object.mode_set(mode='POSE')
    obj.data[STEP] = s
    board = obj.pose.bones[BOARD]
    board.lock_location = board.lock_rotation = (True, True, True)
    board.custom_shape, board.use_custom_shape_bone_size = _outline(data, size, live), False
    rig_shapes.color(board, (0.55, 0.55, 0.6))
    _group(obj, BOARD)
    for c in data["controls"]:
        if PREFIX + c["name"] in obj.pose.bones:
            _knob(obj, c, s)
    bpy.ops.object.mode_set(mode='OBJECT')
    for _, key in keys:
        _drive(obj, key, data["curves"]["CTRL_expressions_" + key.name], s, {c["name"]: c["axes"] for c in data["controls"]})
    obj.data[MARK] = True
    obj.data[MESHES] = sorted({m.name for m, _ in keys})
    if any(m.data.shape_keys.animation_data and (m.data.shape_keys.animation_data.action or
                                                 len(m.data.shape_keys.animation_data.nla_tracks)) for m, _ in keys):
        obj.fpmp_face_board = False         # the face is already animated (a lobby pose): it keeps playing
    return len(keys)


def set_on(obj, on):
    """Mute or unmute the board's shape-key drivers (the face's own keys play while they're muted)."""
    for name in obj.data.get(MESHES, []):
        mesh = bpy.data.objects.get(name)
        ad = mesh.data.shape_keys.animation_data if mesh is not None and mesh.data.shape_keys else None
        for fc in (ad.drivers if ad else ()):
            if any(t.id == obj for v in fc.driver.variables for t in v.targets):
                fc.mute = not on


def on_animation_import(obj):
    """An imported animation keys the face's shape keys: the board steps aside."""
    if obj is not None and obj.data.get(MARK):
        obj.fpmp_face_board = False
