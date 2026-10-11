"""Extra hand controls on the official rig (an app option, not part of Epic's rig): curl and spread every finger,
cup the palm, curl one finger.

Each of Epic's finger and metacarpal controls gets a pivot parent at its joint, turned by the hand controls through
Transformation constraints; Epic's controls stay free on top, and at rest the pivots add nothing."""
import math

import bpy
from mathutils import Vector

from . import rig_shapes, rig_style
from .official_rig import HAND, MCH, PREFIX, WIDTHS, _shape, load

LONG = ("index", "middle", "ring", "pinky")
FINGERS = ("thumb",) + LONG
# total flexion a joint reaches in a real fist (degrees): base, middle and last knuckle; a joint gets what its rest
# bend leaves of it, so a full curl is a fist and never a finger folded through the palm
JOINT_MAX = {"thumb": (45.0, 60.0, 80.0), "finger": (90.0, 105.0, 80.0)}
MIN_ROOM = 10.0
BACK = 15.0                 # how far a joint may bend back from rest
CURL_SHARE = {"thumb": (0.3, 0.5, 0.6), "finger": (1.0, 1.0, 1.0)}      # of each joint's room at a full hand curl
SPREAD = {"thumb": 0.9, "index": 1.0, "middle": 0.0, "ring": 0.8, "pinky": 1.6}
CUP = {"index": 0.0, "middle": 0.3, "ring": 0.65, "pinky": 1.0}


def build(obj, data=None):
    """Adds the hand controls to a built official rig; returns how many controls it added."""
    data = data or load()
    color = {c["name"]: c["color"] for c in data["controls"]}
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')
    hands = [h for h in (_hand_edit(obj.data.edit_bones, side) for side in ("l", "r")) if h]
    bpy.ops.object.mode_set(mode='POSE')
    for h in hands:
        _hand_pose(obj, h, data, color.get("hand_%s_ik_ctrl" % h["side"]) or color.get("hand_%s_fk_ctrl" % h["side"]))
    bpy.ops.object.mode_set(mode='OBJECT')
    return sum(len(h["controls"]) for h in hands)


def _ortho(axis, along):
    """`axis` made square to `along`."""
    a = along.normalized()
    v = axis - a * axis.dot(a)
    return v.normalized() if v.length > 1e-9 else None


def _bone(edit, name, head, tail, x, parent):
    b = edit.new(name)
    b.head, b.tail = head, tail
    b.align_roll(x.cross(tail - head))          # roll by Z, so X lands on `x`
    b.parent, b.use_deform = parent, False
    return b


def _hand_edit(edit, side):
    """Edit mode: pivots under Epic's finger controls, and the hand controls. None when the hand can't take them."""
    s = "_" + side
    if "hand" + s not in edit or "fingers%s_space" % s not in edit:
        return None
    chains = {}
    for f in FINGERS:
        segs = [f + "_0%d" % i + s for i in (1, 2, 3)]
        if not all(n in edit and n + "_ctrl" in edit for n in segs):
            continue
        pts = [edit[n].head.copy() for n in segs]
        pts.append(pts[-1] + (edit[segs[-1]].tail - edit[segs[-1]].head).normalized() * (pts[-1] - pts[-2]).length)
        normal = sum(((pts[i + 1] - pts[i]).cross(pts[i + 2] - pts[i + 1]) for i in range(len(pts) - 2)), Vector())
        chains[f] = {"segs": segs, "pts": pts, "normal": normal}
    long = [f for f in LONG if f in chains]
    if len(long) < 2:
        return None
    ref = sum((chains[f]["normal"].normalized() for f in long if chains[f]["normal"].length > 1e-9), Vector())
    wrist = edit["hand" + s].head.copy()
    knuckles = sum((chains[f]["pts"][0] for f in long), Vector()) / len(long)
    y = (knuckles - wrist).normalized()
    x = _ortho(ref, y) if ref.length > 1e-6 else None
    x = x or _ortho(edit["hand" + s].x_axis, y)
    palm = x.cross(y)               # where a curl takes the fingertips
    size = (knuckles - wrist).length
    out = {"side": side, "chains": chains, "pivots": {}, "cups": {}, "controls": [], "spread": {}}
    for f, chain in chains.items():
        n = chain["normal"]
        if n.length < 1e-9 or (f != "thumb" and n.normalized().dot(x) < 0.2):
            n = x.copy()
        n.normalize()
        meta = f + "_metacarpal" + s
        prev = (edit[meta].tail - edit[meta].head) if meta in edit else chain["pts"][0] - wrist
        limits = JOINT_MAX["thumb" if f == "thumb" else "finger"]
        chain["rooms"] = []
        pivots = []
        for i, seg in enumerate(chain["segs"]):
            head, tail = chain["pts"][i], chain["pts"][i + 1]
            d = tail - head
            rest = math.degrees(prev.angle(d, 0.0)) if prev.length > 1e-9 and d.length > 1e-9 else 0.0
            chain["rooms"].append(max(MIN_ROOM, limits[i] - rest))
            prev = d
            pivots.append(_pivot(edit, seg + "_ctrl", head, tail, _ortho(n, d) or n))
        out["pivots"][f] = pivots
        if f in CUP and meta + "_ctrl" in edit:
            out["cups"][f] = _pivot(edit, meta + "_ctrl", edit[meta].head.copy(), chain["pts"][0],
                                    _ortho(n, chain["pts"][0] - edit[meta].head) or n)
        first = pivots[0]
        away = chain["pts"][0] - (chains["index"]["pts"][0] if f == "thumb" and "index" in chains else
                                  chains.get("middle", chain)["pts"][0])
        w = SPREAD.get(f, 0.0)
        out["spread"][f] = w if edit[first].z_axis.cross(edit[first].y_axis).dot(away) > 0 else -w
        curl = "%s%s_curl_ctrl" % (f, s)
        _bone(edit, curl, chain["pts"][0] - palm * size * 0.25, chain["pts"][-1] - palm * size * 0.25, n,
              edit["fingers%s_space" % s])
        out["controls"].append((curl, "Circle_Thick", size * 0.08))
        chain["curl"] = curl
    hand = edit["hand" + s]
    fingers = "fingers%s_ctrl" % s
    back = knuckles - palm * size * 0.6
    _bone(edit, fingers, back, back + y * size * 0.6, x, hand)
    out["controls"].append((fingers, "RoundedSquare_Thick", size * 0.45))
    if "pinky" in out["cups"]:
        edge = edit["pinky_metacarpal" + s].head
        side_out = _ortho(edge - knuckles, y) or x
        at = (edge + chains["pinky"]["pts"][0]) * 0.5 + side_out * size * 0.4
        _bone(edit, "palm%s_ctrl" % s, at, at + y * size * 0.45, x, hand)
        out["controls"].append(("palm%s_ctrl" % s, "Arrow_Thick", size * 0.3))
    return out


def _pivot(edit, control, head, tail, x):
    """A parent for Epic's control at its joint, turned by the hand controls."""
    ctrl = edit[control]
    pivot = _bone(edit, MCH + "curl_" + control, head, tail, x, ctrl.parent)
    ctrl.use_connect = False
    ctrl.parent = pivot
    return pivot.name


def _map(c, axis, lo, hi, to_lo, to_hi):
    """Transformation: the owner's rotation about `axis` follows the target's over lo..hi, remapped to to_lo..to_hi."""
    c.map_from = c.map_to = 'ROTATION'
    for ax in "xyz":
        setattr(c, "from_min_%s_rot" % ax, lo)
        setattr(c, "from_max_%s_rot" % ax, hi)
        setattr(c, "to_min_%s_rot" % ax, 0.0)
        setattr(c, "to_max_%s_rot" % ax, 0.0)
        setattr(c, "map_to_%s_from" % ax, ax.upper())
    setattr(c, "to_min_%s_rot" % axis, to_lo)
    setattr(c, "to_max_%s_rot" % axis, to_hi)


def _drive(obj, pivot, control, name, axis, lo, hi, to_lo, to_hi):
    c = obj.pose.bones[pivot].constraints.new('TRANSFORM')
    c.name = PREFIX + name
    c.target, c.subtarget = obj, control
    c.owner_space = c.target_space = 'LOCAL'
    c.mix_mode_rot = 'AFTER'
    _map(c, axis, lo, hi, to_lo, to_hi)


def _hand_pose(obj, h, data, color):
    pb, s = obj.pose.bones, "_" + h["side"]
    full, back = math.radians(90.0), math.radians(30.0)
    for name, shape, size in h["controls"]:
        bone = pb[name]
        obj.data.bones[name][HAND] = True
        rig_style.assign(obj.data, name, "Controls")
        bone.custom_shape = _shape(shape, data)
        verts = data["shapes"][shape]["verts"]
        extent = max(max(abs(c) for c in v) for v in verts) or 1.0
        bone.use_custom_shape_bone_size = False
        bone.custom_shape_scale_xyz = (size / extent,) * 3
        rig_shapes.color(bone, tuple(color))
        bone.custom_shape_wire_width = WIDTHS["limb"]
        bone.rotation_mode = 'XYZ'
        bone.lock_location = bone.lock_scale = (True, True, True)
        bone.lock_rotation = (False, True, not name.startswith("fingers"))     # Fingers also spreads (Z)
    for pivots in list(h["pivots"].values()) + [[p] for p in h["cups"].values()]:
        for p in pivots:
            rig_style.assign(obj.data, p, "Mechanics")
    for f, pivots in h["pivots"].items():
        chain = h["chains"][f]
        shares = CURL_SHARE["thumb" if f == "thumb" else "finger"]
        for i, p in enumerate(pivots):
            room = math.radians(chain["rooms"][i])
            # two clamped maps so a control at rest gives exactly 0: forward to the joint's room, back a little
            _drive(obj, p, "fingers%s_ctrl" % s, "hand curl", "x", 0.0, full, 0.0, room * shares[i])
            _drive(obj, p, "fingers%s_ctrl" % s, "hand curl back", "x", -back, 0.0, -math.radians(BACK) * shares[i], 0.0)
            _drive(obj, p, chain["curl"], "finger curl", "x", 0.0, full, 0.0, room)
            _drive(obj, p, chain["curl"], "finger curl back", "x", -back, 0.0, -math.radians(BACK), 0.0)
        if h["spread"].get(f):
            limit = math.radians(45.0)
            w = h["spread"][f]
            _drive(obj, pivots[0], "fingers%s_ctrl" % s, "hand spread", "z", -limit, limit, -limit * w, limit * w)
        for i, p in enumerate(pivots):          # the mechanisms together stay within the knuckle's reach
            c = pb[p].constraints.new('LIMIT_ROTATION')
            c.name, c.owner_space = PREFIX + "knuckle", 'LOCAL'
            c.use_limit_x, c.min_x, c.max_x = True, -math.radians(BACK), math.radians(chain["rooms"][i])
    if "palm%s_ctrl" % s in pb:
        limit = math.radians(60.0)
        for f, p in h["cups"].items():
            if CUP.get(f):
                _drive(obj, p, "palm%s_ctrl" % s, "palm cup", "x", -limit, limit, -limit * CUP[f], limit * CUP[f])
