"""Fortnite's deform correctives (the `deform_*` helper bones) as constraints and drivers: each base bone blends its
two limb bones, each corrective slides/scales (and turns) additively by a weight read from the pose, with the
character's tuning (spec 2026-10-10-deform-correctives). Weights read hidden reader bones in the game's frame:
- angle: Epic's rotator channel (yaw, pitch, roll) of the driver's turn from rest, remapped and clamped;
- spr: Epic's spherical pose reader. Its convention is the one under which Epic's left and right settings mirror
  exactly on the real skeleton: the offset is a change of frame, the reading is the driver's turn from rest, width and
  height are the Z and Y sides of an X axis. The region sits opposite the rest direction (correctives rest at rest);
  that and the elliptical falloff follow Epic's documented behaviour, not its source.
"""
import json
import math

import bpy
from mathutils import Matrix, Vector

READ, FOLLOW, ADD, PREFIX = "DR_read_", "DR_follow_", "DR_add_", "DR "
MARK, ON = "fpmp_deform_rig", "fpmp_deform_rig_on"
GROUP = "Deform Mechanics"
FLIP = Matrix(((1.0, 0.0, 0.0), (0.0, -1.0, 0.0), (0.0, 0.0, 1.0)))     # Epic's axes vs the game frame in Blender
ORDERS = ("XYZ", "XZY", "YXZ", "YZX", "ZXY", "ZYX")                      # Epic's EEulerRotationOrder
SIDES = ((2, 1), (0, 2), (1, 0))                                         # per driver axis: its width and height axes
CHANNELS = {"Yaw": "atan2(-2*(w*z+x*y),1-2*(y*y+z*z))", "Pitch": "asin(clamp(2*(x*z-w*y),-1,1))",
            "Roll": "atan2(2*(w*x+y*z),1-2*(x*x+y*y))"}


def _num(v):
    return "%.6g" % v


def _signed(v):
    return ("-" if v < 0 else "+") + _num(abs(v))


def _switch(bone):
    return bone[len("deform_"):].split("_")[0]


def _game(obj, name):
    from ...material_porter.effects import ue_rest
    return ue_rest(obj.data.bones[name]).to_3x3().normalized()


def _to_bone(obj, name):
    """Epic's local axes of a bone -> its Blender basis axes."""
    from ...material_porter.effects import ue_offset
    return ue_offset(obj.data.bones[name]).to_3x3().normalized() @ FLIP


def _axis_turn(axis, deg):
    return Matrix.Rotation(math.radians(deg), 3, axis)


def _offset(euler):
    """FQuat::MakeFromEuler (X roll, Y pitch, Z yaw) as a matrix on Epic's axes."""
    x, y, z = euler
    return _axis_turn('Z', z) @ _axis_turn('Y', -y) @ _axis_turn('X', -x)


def _epic_quat(euler, order):
    """AnimationCore's QuatFromEuler as a matrix: the X, Y, Z turns applied in the order's sequence."""
    turns = {a: _axis_turn(a, v) for a, v in zip("XYZ", euler)}
    m = Matrix.Identity(3)
    for a in ORDERS[order] if 0 <= order < len(ORDERS) else "XYZ":
        m = turns[a] @ m
    return m


# --- weights in Epic's terms (pure Python: the references the drivers are checked against)

def _rotator(q, channel):
    """Epic's FQuat::Rotator channel (degrees) of an Epic quaternion (x, y, z, w)."""
    x, y, z, w = q
    if channel == "Yaw":
        return math.degrees(math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z)))
    if channel == "Pitch":
        return math.degrees(math.asin(max(-1.0, min(1.0, 2 * (z * x - w * y)))))
    return math.degrees(math.atan2(-2 * (w * x + y * z), 1 - 2 * (x * x + y * y)))


def _scales(weight, key):
    pw, nw, ph, nh = weight[key]
    if weight.get("flip_width"):
        pw, nw = nw, pw
    if weight.get("flip_height"):
        ph, nh = nh, ph
    return pw, nw, ph, nh


def _cone(u, weight):
    """Epic's spherical pose reader on the driver's direction u (the region's axes): 1 inside the active ellipse, a
    smooth falloff to 0 across the falloff band."""
    axis = Vector(weight["axis"])
    k = max(range(3), key=lambda i: abs(axis[i]))
    sign = 1.0 if axis[k] >= 0 else -1.0
    wi, hi = SIDES[k]
    a, b = sign * u[wi], sign * u[hi]
    theta = math.acos(max(-1.0, min(1.0, -sign * u[k]))) / math.pi

    def radius(size, key):
        pw, nw, ph, nh = _scales(weight, key)
        p, q = (pw if a >= 0 else nw), (ph if b >= 0 else nh)
        return size * p * q * math.sqrt(a * a + b * b) / max(1e-9, math.sqrt(q * q * a * a + p * p * b * b))
    r, f = radius(weight["region"], "region_scale"), radius(weight["falloff"], "falloff_scale")
    x = max(0.0, min(1.0, (theta - r) / max(f, 1e-6)))
    return (1 - x) * (1 - x) * (1 + 2 * x)


def _turn(obj, reader):
    """The reader's turn from rest (its game frame), a Blender rotation matrix."""
    pb, bone = obj.pose.bones[reader], obj.data.bones[reader]
    rest = bone.parent.matrix_local.inverted() @ bone.matrix_local
    return (rest.inverted() @ pb.parent.matrix.inverted() @ pb.matrix).to_3x3().normalized()


def reference_weight(obj, spec):
    """A layer's weight computed from the current pose; spec is the layer's stored JSON."""
    spec = json.loads(spec) if isinstance(spec, str) else spec
    weight = spec["weight"]
    if weight["type"] == "const":
        return weight["value"]
    turn = _turn(obj, spec["reader"])
    if weight["type"] == "angle":
        q = turn.to_quaternion()
        lo, hi = weight["from"]
        value = (_rotator((-q.x, q.y, -q.z, q.w), weight["channel"]) - lo) / (hi - lo)
        return max(0.0, min(1.0, value)) if weight.get("clamp", True) else value
    off = _offset(weight["offset"])
    u = off.transposed() @ FLIP @ turn @ FLIP @ off @ Vector(weight["axis"])
    return max(0.0, min(1.0, _cone(u, weight) * weight.get("scale", 1.0)))


# --- drivers

def _prop(obj, pb, key, expression, variables):
    """pb[key] driven by expression over (name, prop path) or (name, reader bone, channel) variables."""
    pb[key] = 0.0
    driver = pb.driver_add('["%s"]' % key).driver
    driver.type = 'SCRIPTED'
    for v in variables:
        var = driver.variables.new()
        var.name = v[0]
        if len(v) == 2:
            var.type = 'SINGLE_PROP'
            var.targets[0].id, var.targets[0].data_path = obj, v[1]
        else:
            var.type = 'TRANSFORMS'
            t = var.targets[0]
            t.id, t.bone_target, t.transform_type = obj, v[1], v[2]
            t.transform_space, t.rotation_mode = 'LOCAL_SPACE', 'QUATERNION'     # local with constraints
    driver.expression = expression


def _path(pb, key):
    return 'pose.bones["%s"]["%s"]' % (pb.name, key)


def _quat_vars(reader):
    return [(n, reader, 'ROT_' + n.upper()) for n in "wxyz"]


def _poly(terms):
    """A sum of coefficient * monomial with each term's sign leading it (Blender misreads "a*-0.5")."""
    out = ""
    for mono, c in terms:
        if abs(c) < 1e-7:
            continue
        body = _num(abs(c)) + ("*" + mono if mono else "")
        out += ("-" if c < 0 else ("+" if out else "")) + body
    return out or "0"


def _rotated(a, c):
    """a @ R(q) @ c as polynomials in the reader's quaternion (w, x, y, z), one per component."""
    r = [[{"": 1, "y*y": -2, "z*z": -2}, {"x*y": 2, "w*z": -2}, {"x*z": 2, "w*y": 2}],
         [{"x*y": 2, "w*z": 2}, {"": 1, "x*x": -2, "z*z": -2}, {"y*z": 2, "w*x": -2}],
         [{"x*z": 2, "w*y": -2}, {"y*z": 2, "w*x": 2}, {"": 1, "x*x": -2, "y*y": -2}]]
    out = []
    for k in range(3):
        coef = {}
        for j in range(3):
            for i in range(3):
                for mono, v in r[j][i].items():
                    coef[mono] = coef.get(mono, 0.0) + a[k][j] * v * c[i]
        out.append(_poly(coef.items()))
    return out


def _weight(obj, pb, k, layer, reader):
    """pb["dr_w<k>"] driven to the layer's weight; returns its path."""
    weight, key = layer["weight"], "dr_w%d" % k
    pb["dr_spec%d" % k] = json.dumps({"weight": weight, "reader": reader})
    if weight["type"] == "const":
        pb[key] = float(weight["value"])
        return _path(pb, key)
    if weight["type"] == "angle":
        lo, hi = weight["from"]
        text = "(%s*57.29578%s)/%s" % (CHANNELS[weight["channel"]], _signed(-lo), _num(hi - lo))
        _prop(obj, pb, key, "clamp(%s,0,1)" % text if weight.get("clamp", True) else text, _quat_vars(reader))
        return _path(pb, key)
    off = _offset(weight["offset"])
    axis = Vector(weight["axis"])
    for n, text in zip("xyz", _rotated(off.transposed() @ FLIP, FLIP @ off @ axis)):
        _prop(obj, pb, "dr_u%d%s" % (k, n), text, _quat_vars(reader))
    i = max(range(3), key=lambda m: abs(axis[m]))
    neg = "" if axis[i] >= 0 else "-"
    along, wide, high = "xyz"[i], "xyz"[SIDES[i][0]], "xyz"[SIDES[i][1]]
    var = {n: (n, _path(pb, "dr_u%d%s" % (k, n))) for n in "xyz"}
    _prop(obj, pb, "dr_t%d" % k, "acos(clamp(%s%s,-1,1))/pi" % ("" if neg else "-", along), [var[along]])
    for tag, size, scales in (("r", weight["region"], "region_scale"), ("f", weight["falloff"], "falloff_scale")):
        pw, nw, ph, nh = _scales(weight, scales)
        _prop(obj, pb, "dr_%s%dp" % (tag, k), "%s if %s%s>=0 else %s" % (_num(pw), neg, wide, _num(nw)), [var[wide]])
        _prop(obj, pb, "dr_%s%dq" % (tag, k), "%s if %s%s>=0 else %s" % (_num(ph), neg, high, _num(nh)), [var[high]])
        _prop(obj, pb, "dr_%s%d" % (tag, k), "%s*p*q*sqrt(a*a+b*b)/max(1e-9,sqrt(q*q*a*a+p*p*b*b))" % _num(size),
              [("a", var[wide][1]), ("b", var[high][1]), ("p", _path(pb, "dr_%s%dp" % (tag, k))),
               ("q", _path(pb, "dr_%s%dq" % (tag, k)))])
    _prop(obj, pb, "dr_x%d" % k, "clamp((t-r)/max(f,1e-6),0,1)",
          [("t", _path(pb, "dr_t%d" % k)), ("r", _path(pb, "dr_r%d" % k)), ("f", _path(pb, "dr_f%d" % k))])
    scale = weight.get("scale", 1.0)
    cone = "(1-x)*(1-x)*(1+2*x)"
    _prop(obj, pb, key, cone if scale == 1.0 else "clamp(%s*%s,0,1)" % (_num(scale), cone), [("x", _path(pb, "dr_x%d" % k))])
    return _path(pb, key)


def _sum(obj, pb, channel, index, terms, base):
    """pb's channel driven to base + sum(coefficient * weight)."""
    terms = [(c, p) for c, p in terms if abs(c) > 1e-9]
    if not terms:
        return
    driver = pb.driver_add(channel, index).driver
    driver.type = 'SCRIPTED'
    text = _num(base) if base else ""
    for n, (c, p) in enumerate(terms):
        var = driver.variables.new()
        var.name, var.type = "w%d" % n, 'SINGLE_PROP'
        var.targets[0].id, var.targets[0].data_path = obj, p
        text += ("-" if c < 0 else ("+" if text else "")) + "w%d*%s" % (n, _num(abs(c)))
    driver.expression = text


# --- building

def _reader_key(obj, weight):
    """(driver, parent) a weight reads, or None when the skeleton lacks them."""
    if weight["type"] == "const":
        return None
    bones = obj.data.bones
    names = set(bones.keys())       # names may be None; a bone collection only takes strings
    driver = weight["bone"] if weight["type"] == "angle" else weight.get("driver")
    if driver not in names:
        return None
    parent = weight.get("parent") if weight["type"] == "spr" else None
    if parent not in names:
        parent = bones[driver].parent.name if bones[driver].parent else None
    return (driver, parent) if parent else None


def _reader_name(obj, key):
    driver, parent = key
    own = obj.data.bones[driver].parent
    return READ + driver + ("" if own and own.name == parent else "__" + parent)


def _layers(obj, lib, tun):
    """Per corrective bone its additive layers, per base bone its weighted sources, and the pec aims."""
    bones = set(obj.data.bones.keys())
    layers, bases, aims = {}, {}, []
    for p in lib["parts"]:
        value = tun["values"].get(p["value"])
        if (not tun["switches"].get(_switch(p["bone"]), True) or value is None or p["bone"] not in bones
                or _reader_key(obj, p["weight"]) is None):
            continue
        t = [-v for v in value["t"]] if p["mirror"] else list(value["t"])
        layers.setdefault(p["bone"], []).append({"weight": p["weight"], "t": t, "s": list(value["s"])})
        sources = [s for s in p["sources"] if s[0] in bones]
        if p["base"] in bones and sources:
            bases.setdefault(p["base"], sources)
        if "aim" in p and {p["aim"]["bone"], p["aim"]["primary_target"]} <= bones:
            aims.append(p["aim"])
    for e in tun.get("extras", []):
        if e["op"] == "modify" and e["bone"] in bones and (e["weight"]["type"] == "const" or _reader_key(obj, e["weight"])):
            layers.setdefault(e["bone"], []).append({"weight": e["weight"], "t": e["t"], "s": e["s"], "euler": e.get("euler"),
                                                     "order": e.get("order") or 0})
    return layers, bases, aims


def _follower(edit, driven, driver):
    name = FOLLOW + driven + "__" + driver
    if name not in edit:
        f = edit.new(name)
        f.head, f.tail, f.roll = edit[driven].head.copy(), edit[driven].tail.copy(), edit[driven].roll
        f.parent, f.use_deform = edit[driver], False
    return name


def _copy(pb, kind, subtarget, influence, tag):
    c = pb.constraints.new(kind)
    c.name, c.target, c.subtarget, c.influence = PREFIX + tag, pb.id_data, subtarget, influence
    return c


def _blend(pb, followers, kind):
    """Epic's weighted average of sources: the first fully, each next by its share of all so far."""
    total = 0.0
    for name, w in followers:
        total += w
        _copy(pb, kind, name, w / total, "blend")


def _nearest(v):
    """The Blender axis name nearest a direction in a bone's basis."""
    i = max(range(3), key=lambda j: abs(v[j]))
    return ("" if v[i] >= 0 else "NEGATIVE_") + "XYZ"[i]


def build(obj, lib, tun, scale=0.01):
    """Fortnite's correctives on an armature with deform_* bones (scale: Blender units per cm). Returns the bones driven
    (0 when already built)."""
    if obj.data.get(MARK):
        return 0
    layers, bases, aims = _layers(obj, lib, tun)
    if not layers:
        return 0
    keys = {_reader_key(obj, layer["weight"]) for ls in layers.values() for layer in ls} - {None}
    readers = {key: _reader_name(obj, key) for key in keys}
    names = set(obj.data.bones.keys())
    turns = [dict(e, parents=[p for p in e["parents"] if p["bone"] in names]) for e in tun.get("extras", [])
             if e["op"] == "rotation_constraint" and e["bone"] in names]
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')
    edit = obj.data.edit_bones
    follow = [(base, [(_follower(edit, base, s), w) for s, w in sources], 'COPY_TRANSFORMS') for base, sources in bases.items()]
    for e in turns:
        follow.append((e["bone"], [(_follower(edit, e["bone"], p["bone"]), p["weight"]) for p in e["parents"]],
                       'COPY_ROTATION'))
    read_from = {}
    for (driver, parent), name in readers.items():
        frame = _game(obj, driver)
        r = edit.new(name)
        r.head = edit[driver].head.copy()
        r.tail = r.head + frame.col[1] * max(edit[driver].length, 0.01)
        r.align_roll(frame.col[2])
        r.parent, r.use_deform = edit[parent], False
        read_from[name] = _follower(edit, name, driver)
    # a constrained bone takes its layers from a twin added after its constraints, as Epic adds them after the blend
    constrained = {bone for bone, followers, _ in follow if followers} | {aim["bone"] for aim in aims}
    channels = {}
    for bone in layers:
        channels[bone] = bone
        if bone in constrained:
            twin = edit.new(ADD + bone)
            twin.head, twin.tail, twin.roll = edit[bone].head.copy(), edit[bone].tail.copy(), edit[bone].roll
            twin.parent, twin.use_deform = edit[bone].parent, False
            channels[bone] = twin.name
    bpy.ops.object.mode_set(mode='POSE')
    pb = obj.pose.bones
    group = obj.data.collections.get(GROUP) or obj.data.collections.new(GROUP)
    group.is_visible = False
    for b in obj.data.bones:
        if b.name.startswith((READ, FOLLOW, ADD)):
            for c in list(b.collections):
                c.unassign(b)
            group.assign(b)
    for bone, followers, kind in follow:
        _blend(pb[bone], followers, kind)
    for name, src in read_from.items():
        pb[name].rotation_mode = 'QUATERNION'
        _copy(pb[name], 'COPY_ROTATION', src, 1.0, "read")
    for aim in aims:
        to_bone = _to_bone(obj, aim["bone"])
        primary = _nearest(to_bone @ Vector(aim["primary"]))
        _copy(pb[aim["bone"]], 'DAMPED_TRACK', aim["primary_target"], 1.0, "aim").track_axis = "TRACK_" + primary
        secondary = _nearest(to_bone @ Vector(aim["secondary"]))
        if aim["secondary_target"] in pb and secondary[-1] != primary[-1]:
            lock = _copy(pb[aim["bone"]], 'LOCKED_TRACK', aim["secondary_target"], 1.0, "aim")
            lock.lock_axis, lock.track_axis = "LOCK_" + primary[-1], "TRACK_" + secondary
    for bone, ls in layers.items():
        to_bone = _to_bone(obj, bone)
        paths = [_weight(obj, pb[bone], k, layer, readers.get(_reader_key(obj, layer["weight"]))) for k, layer in enumerate(ls)]
        target = pb[channels[bone]]
        if target != pb[bone]:
            add = _copy(pb[bone], 'COPY_TRANSFORMS', target.name, 1.0, "add")
            add.mix_mode, add.owner_space, add.target_space = 'AFTER_FULL', 'LOCAL', 'LOCAL'

        for i in range(3):
            _sum(obj, target, "location", i, [((to_bone @ Vector(layer["t"]))[i] * scale, p) for layer, p in zip(ls, paths)], 0.0)
            j = max(range(3), key=lambda m: abs(to_bone[i][m]))
            _sum(obj, target, "scale", i, [(layer["s"][j] - 1.0, p) for layer, p in zip(ls, paths)], 1.0)
        turned = [(layer, p) for layer, p in zip(ls, paths) if layer.get("euler")]
        if turned:
            target.rotation_mode = 'XYZ'
            eulers = [(to_bone @ FLIP @ _epic_quat(layer["euler"], layer["order"]) @ FLIP @ to_bone.transposed()).to_euler('XYZ')
                      for layer, _ in turned]
            for i in range(3):
                _sum(obj, target, "rotation_euler", i, [(e[i], p) for e, (_, p) in zip(eulers, turned)], 0.0)
    bpy.ops.object.mode_set(mode='OBJECT')
    obj.data[MARK] = sorted(layers)
    return len(layers)


def ui(layout, obj):
    """The panel's Correctives toggle, on a skeleton that has them."""
    if obj.data.get(MARK):
        on = bool(obj.data.get(ON, True))
        layout.operator("fpmp.deform_rig_on", text="Correctives", icon='MOD_ARMATURE', depress=on).on = not on


def set_on(obj, on):
    """The correctives drive their bones (on), or leave them at rest (off)."""
    driven = set(obj.data.get(MARK) or [])
    driven |= {ADD + name for name in driven}
    for pb in obj.pose.bones:
        for c in pb.constraints:
            if c.name.startswith(PREFIX) and c.name != PREFIX + "read":
                c.mute = not on
    for fc in obj.animation_data.drivers if obj.animation_data else []:
        if fc.data_path.startswith('pose.bones["') and fc.data_path.split('"')[1] in driven and \
                fc.data_path.endswith(("location", "scale", "rotation_euler")):
            fc.mute = not on
    if not on:
        for name in driven:
            pb = obj.pose.bones.get(name)
            if pb is not None:
                pb.location, pb.scale, pb.rotation_euler = (0.0, 0.0, 0.0), (1.0, 1.0, 1.0), (0.0, 0.0, 0.0)
    obj.data[ON] = bool(on)
    obj.update_tag()
