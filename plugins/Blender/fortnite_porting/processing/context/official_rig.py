"""UEFN's own mannequin Control Rig (FN_Mannequin_ControlRig) rebuilt with Blender constraints.

Its controls, spaces, shapes, colours and what drives what were decoded once from the game files into
official_rig.json (spec 2026-10-09-official-body-rig); this builds them on an FP character's skeleton. A control
"on" a bone has that bone's rest (so it can drive it); the others sit at Epic's offset from a reference bone."""
import json
import math
import os
import re

import bpy
from mathutils import Matrix, Quaternion, Vector

from . import rig_shapes, rig_style

DATA = os.path.join(os.path.dirname(__file__), "official_rig.json")
MARK, ON = "fpmp_official_rig", "fpmp_official_rig_on"
PREFIX = "OR "          # every constraint the rig adds
WIDTHS = {"root": 3.5, "limb": 2.5, "digit": 1.5}
CORE = ("pelvis", "spine_01", "thigh_l", "upperarm_l", "head")
MCH = "OR_MCH_"         # mechanism bones: the limbs' IK chains, the spine's IK points
FOLLOW = "OR_follow_"   # a control's child at the rest of what it drives: the control's frame never reaches the bone
MIRROR = Matrix(((-1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)))
_cache = {}


def load():
    if "data" not in _cache:
        with open(DATA, encoding="utf-8") as f:
            _cache["data"] = json.load(f)
    return _cache["data"]


def fits(obj):
    """A humanoid skeleton the rig can sit on."""
    return obj is not None and obj.type == 'ARMATURE' and all(n in obj.data.bones for n in CORE)


def _shape(name, data):
    obj = bpy.data.objects.get("OR_" + name)
    if obj is None or obj.type != 'MESH':
        mesh = bpy.data.meshes.new("OR_" + name)
        mesh.from_pydata([tuple(v) for v in data["shapes"][name]["verts"]], [tuple(e) for e in data["shapes"][name]["edges"]], [])
        obj = bpy.data.objects.new("OR_" + name, mesh)
    return obj


def _buildable(data, bones):
    """Spaces and controls whose bone the skeleton has and whose parent is built, parents first."""
    items = [("space", s) for s in data["spaces"]] + [("control", c) for c in data["controls"]]
    ok, out, changed = set(), [], True
    while changed:
        changed = False
        for kind, item in items:
            if item["name"] in ok or item["bone"] not in bones:
                continue
            if item["parent"] is None or item["parent"] in ok or item["parent"] in bones:
                ok.add(item["name"])
                out.append((kind, item))
                changed = True
    return out


def _shape_transform(obj, pose_bone, item):
    """Epic's gizmo placement in its control's own frame, as the bone's custom shape transform (mirrored on the right)."""
    st = item["shape_transform"]
    local = Matrix.LocRotScale(Vector(st["t"]), Quaternion(st["q"]), Vector(st["s"]))
    if item.get("mirror"):
        local = MIRROR.to_4x4() @ local
    loc, rot, scale = local.decompose()
    pose_bone.use_custom_shape_bone_size = False
    pose_bone.custom_shape_translation = loc
    pose_bone.custom_shape_rotation_euler = rot.to_euler()
    pose_bone.custom_shape_scale_xyz = scale


def _left(name):
    return re.sub(r"(^|_)r(_|$)", lambda m: m.group(1) + "l" + m.group(2), name, count=1)


def _rest(obj, item, by):
    """A control's rest rotation (armature space): Epic's frame, on its bone's game frame unless Epic keeps it
    world-aligned; a right control is its left twin mirrored (Epic mirrors with scale X -1, which a bone can't hold)."""
    from ...material_porter.effects import ue_rest
    twin = by.get(_left(item["name"])) if item.get("mirror") else None
    if twin is not None and twin["bone"] in obj.data.bones:
        return MIRROR @ _rest(obj, twin, by) @ MIRROR
    frame = Quaternion(item["frame"]).to_matrix()
    if item.get("world"):
        return frame
    return ue_rest(obj.data.bones[item["bone"]]).to_3x3().normalized() @ frame


def _relations(data):
    """(driven, driver) pairs a copy constraint joins: each gets a follower under the driver at the driven's rest."""
    s = data["solve"]
    out = [tuple(p[::-1]) for p in s["copy"] + s["spine"]["fk"] + s["neck"]["fk"]]
    out += [(local["space"], local["on"]) for local in s.get("local", [])]
    out += [(space["name"], space["follow"]) for space in data["spaces"] if space.get("follow")]
    # the roll control's buffer keeps its rest turn under the IK foot (the control doesn't spin as the foot rolls)
    out += [(roll["roll"].replace("_ctrl", "_bfr"), "foot_%s_ik_ctrl" % roll["side"]) for roll in s.get("foot_roll", [])]
    for chain in s["ik"]:
        out += [tuple(p[::-1]) for p in chain["fk"]]
        out.append((chain["bones"][2], chain["target"]))
        if chain.get("ball"):
            out.append((chain["ball"][1], chain["ball"][0]))
    out.append((s["neck"]["bones"][-1], s["neck"]["effector"]))
    for space in data["spaces"]:
        pivot = space["name"] + "_ctrl"
        if parent_of(data, pivot) == space["parent"] and any(c["name"] == pivot for c in data["controls"]):
            out.append((space["name"], pivot))
    return out


def _roll_bones(edit, rolls):
    """Per reverse-foot pivot, a bone between it and what hangs from it that takes Epic's foot roll (the pivot itself
    copies its own control's turn) (edit mode)."""
    for roll in rolls:
        for key in ("heel", "ball", "tip"):
            pivot = edit.get(roll[key])
            if pivot is None:
                continue
            children = [c for c in pivot.children]
            bone = edit.new(MCH + "roll_" + pivot.name)
            bone.head, bone.tail, bone.roll = pivot.head.copy(), pivot.tail.copy(), pivot.roll
            bone.parent, bone.use_deform = pivot, False
            for child in children:
                child.parent = bone


def _foot_roll(obj, roll):
    """Epic's foot roll: the roll control's yaw turns the heel (up to 180), then the ball (up to 40 x roll blend),
    then the toe tip, about the roll control's own lateral axis."""
    pb = obj.pose.bones
    if roll["roll"] not in pb or roll["blend"] not in pb:
        return
    axis = obj.data.bones[roll["roll"]].matrix_local.to_3x3().col[2].normalized()
    pb[roll["roll"]].rotation_mode = 'XYZ'
    reach = math.radians(roll["clamp"])
    # Epic's angles in UE's axes; Blender's mirror Y, so each turn flips sign (z = -yaw). The right roll control is
    # the left one mirrored, so its turns are the left's mirrored back: Epic's own signs
    if roll["side"] == "r":
        expressions = {"heel": "clamp(z,0,pi)", "ball": "-clamp(-z,0,%.6g*b)" % reach, "tip": "-clamp(-z-%.6g*b,0,pi)" % reach}
    else:
        expressions = {"heel": "-clamp(-z,0,pi)", "ball": "clamp(z,0,%.6g*b)" % reach, "tip": "clamp(z-%.6g*b,0,pi)" % reach}
    for key, text in expressions.items():
        name = MCH + "roll_" + roll[key]
        if name not in pb:
            continue
        bone = pb[name]
        bone.rotation_mode = 'AXIS_ANGLE'
        local = obj.data.bones[name].matrix_local.to_3x3().inverted() @ axis
        bone.rotation_axis_angle = (0.0, *local.normalized())
        driver = bone.driver_add("rotation_axis_angle", 0).driver
        driver.type = 'SCRIPTED'
        # Epic's yaw is minus Blender's Z turn (the axes mirror Y); read as the channel itself: the control hangs
        # below these pivots, so reading its transform would be a dependency cycle
        var = driver.variables.new()
        var.name, var.type = "z", 'SINGLE_PROP'
        var.targets[0].id, var.targets[0].data_path = obj, 'pose.bones["%s"].rotation_euler[2]' % roll["roll"]
        var = driver.variables.new()
        var.name, var.type = "b", 'SINGLE_PROP'
        var.targets[0].id, var.targets[0].data_path = obj, 'pose.bones["%s"]["%s"]' % (roll["blend"], roll["blend"])
        driver.expression = text


def _followers(edit, data):
    for driven, driver in _relations(data):
        if driven in edit and driver in edit:
            f = edit.new(FOLLOW + driven + "__" + driver)
            f.head, f.tail, f.roll = edit[driven].head.copy(), edit[driven].tail.copy(), edit[driven].roll
            f.parent, f.use_deform = edit[driver], False


def _from_bone(obj, control, bone):
    """The control's pose that puts its follower of `bone` on the bone's current pose (matching, baking)."""
    b = obj.data.bones
    return obj.pose.bones[bone].matrix @ b[bone].matrix_local.inverted() @ b[control].matrix_local


def create(obj, data=None):
    """Build the rig on a fitting armature; returns a summary ("already built" on a second call)."""
    if obj.data.get(MARK):
        return "already built"
    data = data or load()
    items = _buildable(data, set(obj.data.bones.keys()))
    names = {item["name"] for _, item in items}
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')
    edit = obj.data.edit_bones
    ratio = edit["head"].head.z / data["height"] if data["height"] else 1.0
    by = {c["name"]: c for c in data["controls"]}
    for kind, item in items:
        bone = edit.new(item["name"])
        at = edit[item["bone"]]
        if kind == "control":       # at Epic's frame, where the rig places it
            rot = _rest(obj, item, by)
            bone.head = at.head.copy() if item["offset"] is None else at.head + Vector(item["offset"]) * ratio
            length = at.length if item["offset"] is None else max(at.length * 0.5, 0.02)
            bone.tail = bone.head + rot.col[1].normalized() * length
            bone.align_roll(rot.col[2])
        elif item["offset"] is None:
            bone.head, bone.tail, bone.roll = at.head.copy(), at.tail.copy(), at.roll
        else:
            bone.head = at.head + Vector(item["offset"]) * ratio
            bone.tail = bone.head + Vector((0.0, 0.0, max(at.length * 0.5, 0.02)))
        bone.use_deform = False
        if item["parent"]:
            bone.parent = edit[item["parent"]]
    _spine_points(edit, data["solve"]["spine"])
    _poles_in_plane(edit, data["solve"]["ik"])
    _ik_chains(edit, data["solve"]["ik"] + [{"bones": data["solve"]["neck"]["bones"]}])
    _followers(edit, data)
    _roll_bones(edit, data["solve"].get("foot_roll", []))
    bpy.ops.object.mode_set(mode='POSE')
    rig_style.collections(obj.data)
    defaults = {s["name"]: s["default"] for s in data.get("settings", [])}
    for b in obj.data.bones:
        if b.name not in names:
            rig_style.assign(obj.data, b.name, "Game Bones")
    for kind, item in items:
        pb = obj.pose.bones[item["name"]]
        if kind == "space":
            rig_style.assign(obj.data, item["name"], "Mechanics")
            continue
        if item["type"] in ("bool", "float"):        # Epic's settings: values switched from the panel, not posed
            rig_style.assign(obj.data, item["name"], "Mechanics")
            low, high = (item.get("limits") or {}).get("range") or (0.0, 1.0)
            default = defaults.get(item["name"], 0.0)
            pb[item["name"]] = default
            pb.id_properties_ui(item["name"]).update(min=low, max=high, default=default)
            continue
        rig_style.assign(obj.data, item["name"], "Controls")
        pb.custom_shape = _shape(item["shape"], data)
        _shape_transform(obj, pb, item)
        rig_shapes.color(pb, tuple(item["color"]))
        pb.custom_shape_wire_width = WIDTHS[item["role"]]
        limits = item.get("limits") or {}
        for channel in ("lock_location", "lock_rotation", "lock_scale"):
            if channel in limits:
                setattr(pb, channel, limits[channel])
    for name in [b.name for b in obj.data.bones if b.name.startswith((MCH, FOLLOW))]:
        rig_style.assign(obj.data, name, "Mechanics")
    _solve(obj, data, names)
    bpy.ops.object.mode_set(mode='OBJECT')
    for chain in _chains(data, names):      # Epic's defaults: legs in IK, the rest FK (FK where IK couldn't be built)
        ready = _ik_ready(obj, chain["limb"]) if chain.get("limb") else True
        set_switch(obj, chain["switch"], ik=ready and defaults.get(chain["switch"], 0.0) >= 0.5, match=False)
    obj.show_in_front = True
    obj.data[MARK] = True
    obj.data[ON] = True
    ad = obj.animation_data
    if ad is not None and (ad.action is not None or len(ad.nla_tracks)):
        set_on(obj, False)      # the bones are already animated (a lobby pose): they keep playing
    return "official rig: %d controls" % sum(1 for kind, _ in items if kind == "control")


def _poles_in_plane(edit, chains):
    """Each pole out from its knee or elbow in the limb's own bend plane (edit mode), so IK at rest is the rest pose;
    Epic's mannequin offset only gives the side for a limb that's straight."""
    for chain in chains:
        if chain["pole"] not in edit or not all(b in edit for b in chain["bones"]):
            continue
        root, mid, end = (edit[b].head for b in chain["bones"])
        out = _off_axis(mid - root, end - root)
        if out.length < (end - root).length * 0.01:
            out = _off_axis(edit[chain["pole"]].head - mid, end - root)
        if out.length < 1e-6:
            continue
        pole = edit[chain["pole"]]
        length = pole.length
        pole.head = mid + out.normalized() * (mid - root).length
        pole.tail = pole.head + Vector((0.0, 0.0, length))


def _ik_chains(edit, chains):
    """Per limb, its two upper bones copied head to head for the IK (Blender's IK aims a bone's tail and FP's tails
    stop short of the next joint), each carrying a child at the real bone's rest for that bone to copy (edit mode)."""
    for chain in chains:
        if not all(b in edit for b in chain["bones"]):
            continue
        root, mid, end = (edit[b] for b in chain["bones"])
        parent = root.parent
        start = edit.new(MCH + "from_" + root.name)
        start.head, start.tail = root.head.copy(), root.head + (mid.head - root.head) * 0.25
        start.parent, start.use_deform = parent, False
        for bone, tip in ((root, mid.head), (mid, end.head)):
            ik = edit.new(MCH + "ik_" + bone.name)
            ik.head, ik.tail = bone.head.copy(), tip.copy()
            ik.align_roll(bone.z_axis)
            ik.parent, ik.use_deform = parent, False
            ik.use_connect = parent is not None and parent.name.startswith(MCH)
            ik.inherit_scale = 'NONE' if parent is not None and parent.name.startswith(MCH) else ik.inherit_scale
            pose = edit.new(MCH + "pose_" + bone.name)
            pose.head, pose.tail, pose.roll = bone.head.copy(), bone.tail.copy(), bone.roll
            pose.parent, pose.use_deform = ik, False
            parent = ik
        pose = edit.new(MCH + "pose_" + end.name)
        pose.head, pose.tail, pose.roll = end.head.copy(), end.tail.copy(), end.roll
        pose.parent, pose.use_deform, pose.inherit_scale = parent, False, 'NONE'


def _off_axis(v, axis):
    """The part of v square to the axis."""
    a = axis.normalized()
    return v - a * v.dot(a)


def _spine_points(edit, spine):
    """Per spine bone, a bone from its joint to the next for the IK (the top one is the bone's own rest), each carrying
    a child at the real bone's rest for that bone to copy (edit mode)."""
    if not all(b in edit for b in spine["bones"]):
        return
    for name, above in zip(spine["bones"], spine["bones"][1:] + [None]):
        src = edit[name]
        point = edit.new(MCH + "pt_" + name)
        point.head = src.head.copy()
        point.tail = edit[above].head.copy() if above else src.tail.copy()
        point.align_roll(src.z_axis)
        point.use_deform = False
        pose = edit.new(MCH + "pose_" + name)
        pose.head, pose.tail, pose.roll = src.head.copy(), src.tail.copy(), src.roll
        pose.parent, pose.use_deform = point, False


def _by_product(obj, constraint, a, b):
    """Influence = setting a times setting b (IK on and stretch on)."""
    driver = constraint.driver_add("influence").driver
    driver.type = 'SCRIPTED'
    for name, setting in (("a", a), ("b", b)):
        var = driver.variables.new()
        var.name, var.type = name, 'SINGLE_PROP'
        var.targets[0].id, var.targets[0].data_path = obj, 'pose.bones["%s"]["%s"]' % (setting, setting)
    driver.expression = "a*b"


def _neck(obj, neck, s, has):
    """Neck IK: the neck bones reach the head control as a two-bone IK (head-to-head chain, as the limbs), the head
    takes the control's turn; head stretch lengthens the chain as the limbs' stretch does."""
    pb = obj.pose.bones
    root, mid, end = neck["bones"]
    if not has(neck["switch"], neck["effector"], MCH + "ik_" + mid, MCH + "pose_" + end):
        return
    ik = _constraint(pb[MCH + "ik_" + mid], 'IK', "IK")
    ik.target, ik.subtarget, ik.chain_count = obj, neck["effector"], 2
    for bone in (root, mid):
        _copy(obj, bone, MCH + "pose_" + bone, "IK", neck["switch"], ik=True, kind='COPY_LOCATION')
        _copy(obj, bone, MCH + "pose_" + bone, "IK rotation", neck["switch"], ik=True, kind='COPY_ROTATION')
    _copy(obj, end, MCH + "pose_" + end, "IK position", neck["switch"], ik=True, kind='COPY_LOCATION')
    _length(obj, {"bones": neck["bones"], "target": neck["effector"], "stretch": neck["stretch"], "softness": None}, s)


def _constraint(pose_bone, kind, name):
    c = pose_bone.constraints.new(kind)
    c.name = PREFIX + name
    return c


def _by_switch(obj, constraint, switch, ik):
    """Influence follows a switch control's value: IK (1) or FK (0)."""
    driver = constraint.driver_add("influence").driver
    driver.type = 'SCRIPTED'
    var = driver.variables.new()
    var.name, var.type = "ik", 'SINGLE_PROP'
    var.targets[0].id = obj
    var.targets[0].data_path = 'pose.bones["%s"]["%s"]' % (switch, switch)
    driver.expression = "ik" if ik else "1 - ik"


def _copy(obj, bone, control, name, switch=None, ik=False, kind='COPY_TRANSFORMS'):
    c = _constraint(obj.pose.bones[bone], kind, name)
    follower = FOLLOW + bone + "__" + control
    c.target, c.subtarget = obj, follower if follower in obj.pose.bones else control
    if switch:
        _by_switch(obj, c, switch, ik)
    return c


def _length(obj, chain, s):
    """Epic's stretch and softness on an IK chain, as the scale of its head-to-head bones: softness s when on (the
    limb eases to full reach and past it), else stretch up to its maximum ratio, else rest length."""
    pb = obj.pose.bones
    root, mid, _ = chain["bones"]
    start = MCH + "from_" + root
    if not all(n in pb for n in (start, chain.get("stretch", ""))) or (chain.get("softness") and chain["softness"] not in pb):
        return
    length = obj.data.bones[MCH + "ik_" + root].length + obj.data.bones[MCH + "ik_" + mid].length
    q = length * s["softness"]          # soft distance per unit of softness
    big = "%.6g" % length
    soft = "%.6g*v" % q
    # d: the world distance over the armature's scale (rest lengths are in armature units)
    text = ("max(d/k/(%s-%s+%s*(1-exp(-(d/k-%s+%s)/(%s)))),1) if v>1e-6 and d/k>%s-%s else "
            "(clamp(d/k/%s,%.6g,%.6g) if w>0.5 else 1)"
            % (big, soft, soft, big, soft, soft, big, soft, big, s["stretch"]["start"], s["stretch"]["max"]))
    for bone in (root, mid):
        driver = pb[MCH + "ik_" + bone].driver_add("scale", 1).driver
        driver.type = 'SCRIPTED'
        for name, setting in (("v", chain.get("softness")), ("w", chain["stretch"])):
            if setting is None:
                continue
            var = driver.variables.new()
            var.name, var.type = name, 'SINGLE_PROP'
            var.targets[0].id, var.targets[0].data_path = obj, 'pose.bones["%s"]["%s"]' % (setting, setting)
        var = driver.variables.new()
        var.name, var.type = "d", 'LOC_DIFF'
        for k, target in enumerate((start, chain["target"])):
            var.targets[k].id, var.targets[k].bone_target = obj, target
            var.targets[k].transform_space = 'WORLD_SPACE'
        var = driver.variables.new()
        var.name, var.type = "k", 'TRANSFORMS'
        var.targets[0].id, var.targets[0].transform_type, var.targets[0].transform_space = obj, 'SCALE_AVG', 'WORLD_SPACE'
        driver.expression = text if chain.get("softness") else \
            "clamp(d/k/%s,%.6g,%.6g) if w>0.5 else 1" % (big, s["stretch"]["start"], s["stretch"]["max"])


def _ik_ready(obj, chain):
    """A limb's IK side could be built (its bones, target, pole and head-to-head chain exist)."""
    root, mid, end = chain["bones"]
    pb = obj.pose.bones
    return all(n in pb for n in (chain["switch"], chain["target"], chain["pole"], root, mid, end, MCH + "ik_" + mid))


def setting(obj, name):
    """An Epic setting's value (a switch, stretch, softness, local, roll blend)."""
    return obj.pose.bones[name][name]


def set_setting(obj, name, value):
    obj.pose.bones[name][name] = float(value)
    obj.update_tag()        # a property set from Python doesn't re-run the drivers that read it


def _pole_angle(obj, root, pole):
    """IK pole angle keeping the chain at rest: from the root bone's X axis to the pole, around the chain."""
    base = obj.data.bones[root]
    axis = base.tail_local - base.head_local
    normal = axis.cross(obj.data.bones[pole].head_local - base.head_local)
    projected = normal.cross(axis)
    x = base.matrix_local.to_3x3().col[0]
    angle = x.angle(projected)
    return -angle if x.cross(projected).angle(axis) < 1.0 else angle


def _chains(data, built):
    """Every switched part (limbs, spine, neck) whose switch was built: {switch, fk: [(control, bone)], ik: [...]}."""
    s = data["solve"]
    always = {control for control, _ in s["copy"]}       # drive their bone in both modes (the hips)
    parent = {c["name"]: c["parent"] for c in data["controls"] + data["spaces"]}

    def under(root):
        """Controls hanging from `root` (the IK foot's pivots, roll, stretch and softness)."""
        out = []
        for c in data["controls"]:
            p = parent.get(c["name"])
            while p is not None and p != root:
                p = parent.get(p)
            if p == root:
                out.append(c["name"])
        return out

    out = []
    for chain in s["ik"]:
        if chain["switch"] in built:
            ik = [chain["effector"], chain["pole"]] + under(chain["effector"])
            out.append({"switch": chain["switch"], "fk": [p for p in chain["fk"] if p[0] in built],
                        "ik": [n for n in ik if n in built and n not in always], "limb": chain})
    for part in ("spine", "neck"):
        p = s[part]
        if p["switch"] in built:
            ik = p["ik"] if part == "spine" else [p["effector"]]
            fk = p["fk"]
            out.append({"switch": p["switch"], "fk": [q for q in fk if q[0] in built], "part": part,
                        "ik": [n for n in ik if n in built and n not in always]})
    return out


def _solve(obj, data, built):
    pb, s = obj.pose.bones, data["solve"]

    def has(*names):
        return all(n in built or n in pb for n in names)

    for chain in s["ik"]:
        root, mid, end = chain["bones"]
        for control, bone in chain["fk"]:       # FK works whether or not the IK side could be built
            if has(chain["switch"], control, bone):
                _copy(obj, bone, control, "FK", chain["switch"], ik=False)
        if not _ik_ready(obj, chain):
            continue
        ball = chain.get("ball") or [None, None]
        ik = _constraint(pb[MCH + "ik_" + mid], 'IK', "IK")
        ik.target, ik.subtarget = obj, chain["target"]
        ik.pole_target, ik.pole_subtarget = obj, chain["pole"]
        ik.pole_angle = _pole_angle(obj, MCH + "ik_" + root, chain["pole"])
        ik.chain_count = 2
        for bone in (root, mid):
            _copy(obj, bone, MCH + "pose_" + bone, "IK", chain["switch"], ik=True, kind='COPY_LOCATION')
            _copy(obj, bone, MCH + "pose_" + bone, "IK rotation", chain["switch"], ik=True, kind='COPY_ROTATION')
        _copy(obj, end, chain["target"], "IK rotation", chain["switch"], ik=True, kind='COPY_ROTATION')
        if MCH + "pose_" + end in pb:
            _copy(obj, end, MCH + "pose_" + end, "IK position", chain["switch"], ik=True, kind='COPY_LOCATION')
        _length(obj, chain, s)
        if ball[0] and has(*ball):
            _copy(obj, ball[1], ball[0], "IK rotation", chain["switch"], ik=True, kind='COPY_ROTATION')
    for control, bone in s["copy"]:
        if has(control, bone):
            _copy(obj, bone, control, "copy")
    for space in data["spaces"]:
        if space.get("follow") and has(space["name"], space["follow"]):
            _copy(obj, space["name"], space["follow"], "follow")
        # the reverse foot's pivots (heel, tip): Epic's control beside each turns it
        pivot = space["name"] + "_ctrl"
        if has(space["name"], pivot) and parent_of(data, pivot) == space["parent"]:
            _copy(obj, space["name"], pivot, "pivot")
    for roll in s.get("foot_roll", []):
        _foot_roll(obj, roll)
        bfr, foot = roll["roll"].replace("_ctrl", "_bfr"), "foot_%s_ik_ctrl" % roll["side"]
        if has(bfr, foot):
            _copy(obj, bfr, foot, "roll rest", kind='COPY_ROTATION')
    for local in s.get("local", []):
        follower = FOLLOW + local["space"] + "__" + local["on"]
        if has(local["space"], local["setting"]) and follower in pb:
            c = _constraint(pb[local["space"]], 'COPY_ROTATION', "local")
            c.target, c.subtarget = obj, follower
            _by_switch(obj, c, local["setting"], True)
    _spine(obj, s["spine"], has)
    neck = s["neck"]
    for control, bone in neck["fk"]:
        if has(neck["switch"], control, bone):
            _copy(obj, bone, control, "FK", neck["switch"], ik=False)
    if has(neck["switch"], neck["effector"], *neck["bones"]):
        _copy(obj, neck["bones"][-1], neck["effector"], "IK", neck["switch"], ik=True, kind='COPY_ROTATION')
        _neck(obj, neck, s, has)


def parent_of(data, name):
    return next((i["parent"] for i in data["controls"] + data["spaces"] if i["name"] == name), None)


def _spine(obj, spine, has):
    """FK: each spine control drives its bone. IK: each joint blends the hips, tangent and chest controls by how far up
    the spine it sits (a cubic Bezier's weights), each bone aims at the next joint, the spine bones take those turns."""
    pb = obj.pose.bones
    if not has(spine["switch"], *spine["bones"]):
        return
    for control, bone in spine["fk"]:
        if has(control, bone):
            _copy(obj, bone, control, "FK", spine["switch"], ik=False)
    controls = spine["ik"]          # hips, hips tangent, chest tangent, chest
    if len(controls) != 4 or not has(*controls) or not has(*(MCH + "pt_" + b for b in spine["bones"])):
        return
    n = len(spine["bones"])
    for i, bone in enumerate(spine["bones"]):
        t = i / (n - 1)
        blend = _constraint(pb[MCH + "pt_" + bone], 'ARMATURE', "spine blend")
        blend.use_deform_preserve_volume = True
        for control, weight in zip(controls, ((1 - t) ** 3, 3 * t * (1 - t) ** 2, 3 * t * t * (1 - t), t ** 3)):
            if weight > 1e-6:
                target = blend.targets.new()
                target.target, target.subtarget, target.weight = obj, control, weight
        if i < n - 1:
            aim = _constraint(pb[MCH + "pt_" + bone], 'DAMPED_TRACK', "spine aim")
            aim.target, aim.subtarget = obj, MCH + "pt_" + spine["bones"][i + 1]
        _copy(obj, bone, MCH + "pose_" + bone, "IK", spine["switch"], ik=True, kind='COPY_ROTATION')
        if i and has(spine.get("stretch", "")):      # stretch: the bones spread to their points
            _by_product(obj, _copy(obj, bone, MCH + "pose_" + bone, "IK stretch", kind='COPY_LOCATION'),
                        spine["switch"], spine["stretch"])
    _copy(obj, spine["bones"][0], MCH + "pose_" + spine["bones"][0], "IK position", spine["switch"], ik=True, kind='COPY_LOCATION')


def _pole_place(obj, chain):
    """Where a pole goes for the limb's current pose: in its bend plane, out from the knee or elbow."""
    pb = obj.pose.bones
    root, mid, end = (pb[b].head for b in chain["bones"])
    out = mid - (root + end) / 2.0
    if out.length < 1e-6:
        out = pb[chain["bones"][1]].matrix.to_3x3().col[2]
    return Matrix.Translation(mid + out.normalized() * (mid - root).length)


def set_switch(obj, switch, ik, match=True):
    """IK (True) or FK on a switched part. `match` first puts the incoming controls where the bones are now, so the
    part stays put; the outgoing controls hide."""
    chain = next((c for c in _chains(load(), set(obj.pose.bones.keys())) if c["switch"] == switch), None)
    if chain is None:
        return
    pb = obj.pose.bones
    if match:
        bpy.context.view_layer.update()
        if ik and chain.get("limb"):
            limb = chain["limb"]
            eff, target = pb[limb["effector"]], pb[limb["target"]]
            # the IK target may sit under the reverse foot's pivots: move the effector so the target lands on the bone
            eff.matrix = _from_bone(obj, limb["target"], limb["bones"][2]) @ target.matrix.inverted() @ eff.matrix
            bpy.context.view_layer.update()
            pb[limb["pole"]].matrix = _pole_place(obj, limb) @ pb[limb["pole"]].matrix.to_3x3().to_4x4()
            ball = limb.get("ball")
            if ball and ball[0] in pb and ball[1] in pb:
                pb[ball[0]].matrix = _from_bone(obj, ball[0], ball[1])
        elif ik and chain.get("part"):
            s = load()["solve"]
            pairs = [(s["spine"]["ik"][-1], s["spine"]["bones"][-1])] if chain["part"] == "spine" else \
                [(s["neck"]["effector"], s["neck"]["bones"][-1])]
            for control, bone in pairs:
                if control in pb and bone in pb:
                    pb[control].matrix = _from_bone(obj, control, bone)
        elif not ik:
            poses = {control: _from_bone(obj, control, bone) for control, bone in chain["fk"]}
            for control, _ in chain["fk"]:
                pb[control].matrix = poses[control]
                bpy.context.view_layer.update()
    pb[switch][switch] = 1.0 if ik else 0.0
    obj.update_tag()        # a property set from Python doesn't re-run the drivers that read it
    for control, _ in chain["fk"]:      # Blender 5 shows a pose by the pose bone's hide (the bone's is edit mode's)
        pb[control].hide = ik
    for control in chain["ik"]:
        pb[control].hide = not ik
    bpy.context.view_layer.update()


def set_on(obj, on):
    """The rig drives the bones (on), or the bones play their own keys (off)."""
    for pose_bone in obj.pose.bones:
        for c in pose_bone.constraints:
            if c.name.startswith(PREFIX):
                c.mute = not on
    obj.data[ON] = on


def on_animation_import(obj):
    """An imported animation keys the bones: the rig steps aside (Bake to Controls brings it back)."""
    if obj is not None and obj.data.get(MARK):
        set_on(obj, False)


BODY = ("global_ctrl", "root_ctrl", "body_offset_ctrl", "body_ctrl", "hips_ctrl", "neck_01_ctrl", "neck_02_ctrl")


def set_body_controls(obj, show):
    """Epic's Show Body Controls: the fingers, toes, root, body, hips and neck controls hide or show (what the FK/IK
    state hides stays hidden)."""
    data = load()
    pb = obj.pose.bones
    if data["solve"]["visibility"] in pb:
        set_setting(obj, data["solve"]["visibility"], 1.0 if show else 0.0)
    hidden = set()
    for chain in _chains(data, set(pb.keys())):
        ik = setting(obj, chain["switch"]) >= 0.5
        hidden |= {c for c, _ in chain["fk"]} if ik else set(chain["ik"])
    for name in set(BODY) | {c["name"] for c in data["controls"] if c["role"] == "digit"}:
        if name in pb:
            pb[name].hide = not show or name in hidden


def _toggle(row, obj, name, text):
    if name in obj.pose.bones:
        on = setting(obj, name) >= 0.5
        op = row.operator("fpmp.official_rig_setting", text=text, depress=on)
        op.name, op.value = name, 0.0 if on else 1.0


def ui(layout, obj):
    """The official rig's panel lines: Rig on/off; per part FK/IK and Epic's settings (stretch, softness, local);
    foot roll blend; body controls; Bake to Controls."""
    data = load()
    pb = obj.pose.bones
    col = layout.column(align=True)
    on = bool(obj.data.get(ON, True))
    col.operator("fpmp.official_rig_on", text="Rig On" if not on else "Rig Off", icon='CONSTRAINT_BONE').on = not on
    local = {x["space"].split("_")[1] if x["space"].startswith("upperarm") else "neck": x["setting"] for x in data["solve"]["local"]}
    limbs = {c["switch"]: c for c in data["solve"]["ik"]}
    for chain in _chains(data, set(pb.keys())):
        switch = chain["switch"]
        row = col.row(align=True)
        row.label(text=switch.replace("_fk_ik_switch", "").replace("_", " ").title())
        ik = setting(obj, switch) >= 0.5
        fk_op = row.operator("fpmp.official_rig_switch", text="FK", depress=not ik)
        fk_op.switch, fk_op.ik = switch, False
        ik_op = row.operator("fpmp.official_rig_switch", text="IK", depress=ik)
        ik_op.switch, ik_op.ik = switch, True
        part = data["solve"]["spine"] if chain.get("part") == "spine" else data["solve"]["neck"] if chain.get("part") else limbs[switch]
        _toggle(row, obj, part["stretch"], "Stretch")
        if part.get("softness") and part["softness"] in pb:
            row.prop(pb[part["softness"]], '["%s"]' % part["softness"], text="Soft")
        side = switch.split("_")[1] if switch.startswith("arm_") else ("neck" if chain.get("part") == "neck" else None)
        if side in local:
            _toggle(row, obj, local[side], "Local")
    row = col.row(align=True)
    for roll in data["solve"].get("foot_roll", []):
        if roll["blend"] in pb:
            row.prop(pb[roll["blend"]], '["%s"]' % roll["blend"], text="Roll %s" % roll["side"].upper())
    show = data["solve"]["visibility"]
    if show in pb:
        shown = setting(obj, show) >= 0.5
        col.operator("fpmp.official_rig_body_controls", text="Body Controls", depress=shown).show = not shown
    col.operator("fpmp.official_rig_bake", text="Bake to Controls", icon='ACTION')


def _bake_targets(data, built):
    """Control: the bone whose pose it takes (FK, copy) for a bake; IK effectors take their limb's end bone."""
    s = data["solve"]
    pairs = s["copy"] + s["spine"]["fk"] + s["neck"]["fk"] + [p for chain in s["ik"] for p in chain["fk"]]
    targets = {control: bone for control, bone in pairs if control in built and bone in built}
    for chain in s["ik"]:
        if chain["effector"] in built:
            targets[chain["effector"]] = chain["bones"][2]
    return targets


def _settle_order(data, controls, targets):
    """Controls in an order where everything a control hangs from is placed first: its parents, and for a space
    that follows a bone, the control driving that bone."""
    parent = {i["name"]: i["parent"] for i in data["controls"] + data["spaces"]}
    follow = {s["name"]: s.get("follow") for s in data["spaces"]}
    driver = {bone: control for control, bone in targets.items()}
    wanted, out, seen = set(controls), [], set()

    def visit(name, depth=0):
        if name is None or name in seen or depth > 200:
            return
        seen.add(name)
        visit(parent.get(name), depth + 1)
        if follow.get(name):
            visit(driver.get(follow[name]), depth + 1)
        if name in wanted:
            out.append(name)

    for c in controls:
        visit(c)
    return out


def bake_to_controls(obj, start, end):
    """Key every control from the animated bones over start..end, then turn the rig on (FK everywhere). The bones'
    own keys stay underneath in an NLA track, so the bones Epic's controls don't drive keep their animation.
    Returns the frames baked."""
    data, scene, pb = load(), bpy.context.scene, obj.pose.bones
    built = set(pb.keys())
    order = [item["name"] for kind, item in _buildable(data, built) if kind == "control"]
    targets = _bake_targets(data, built)
    limbs = [chain for chain in data["solve"]["ik"] if chain["pole"] in built and all(b in built for b in chain["bones"])]
    set_on(obj, False)
    poses = {}
    for f in range(start, end + 1):
        scene.frame_set(f)
        poses[f] = {control: _from_bone(obj, control, bone) for control, bone in targets.items()}
        poses[f].update({chain["pole"]: _pole_place(obj, chain) for chain in limbs})
    ad = obj.animation_data or obj.animation_data_create()
    if ad.action is not None:
        track = ad.nla_tracks.new()
        track.name = "OR bones"
        track.strips.new(ad.action.name, int(ad.action.frame_range[0]), ad.action)
    ad.action = bpy.data.actions.new("OR controls (%s)" % obj.name)
    for chain in _chains(data, built):
        pb[chain["switch"]][chain["switch"]] = 0.0
    set_on(obj, True)
    keyed = _settle_order(data, [c for c in order if c in poses[start]], targets)
    for f in range(start, end + 1):
        scene.frame_set(f)
        # spaces follow bones other controls drive (the arm's space rides the clavicle), so settle in passes
        for _ in range(4):
            moved = False
            for control in keyed:
                pose_bone, want = pb[control], poses[f][control]
                turn = pose_bone.matrix.to_quaternion().rotation_difference(want.to_quaternion()).angle
                if (pose_bone.matrix.to_translation() - want.to_translation()).length < 1e-5 and \
                        min(turn, 2.0 * math.pi - turn) < 1e-5 and \
                        (pose_bone.matrix.to_scale() - want.to_scale()).length < 1e-5:
                    continue            # already there: no depsgraph update
                pose_bone.matrix = want
                bpy.context.view_layer.update()
                moved = True
            if not moved:
                break
        for control in keyed:
            pose_bone = pb[control]
            pose_bone.keyframe_insert("location", frame=f)
            pose_bone.keyframe_insert("rotation_quaternion" if pose_bone.rotation_mode == 'QUATERNION' else "rotation_euler", frame=f)
            pose_bone.keyframe_insert("scale", frame=f)      # emotes scale bones slightly; Copy Transforms carries it
    for chain in _chains(data, built):
        set_switch(obj, chain["switch"], ik=False, match=False)
    return end - start + 1
