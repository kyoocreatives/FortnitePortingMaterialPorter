"""UEFN's own mannequin Control Rig (FN_Mannequin_ControlRig) rebuilt with Blender constraints.

Its controls, spaces, shapes, colours and what drives what were decoded once from the game files into
official_rig.json (spec 2026-10-09-official-body-rig); this builds them on an FP character's skeleton. A control
"on" a bone has that bone's rest (so it can drive it); the others sit at Epic's offset from a reference bone."""
import json
import math
import os

import bpy
from mathutils import Matrix, Quaternion, Vector

from . import rig_shapes, rig_style

DATA = os.path.join(os.path.dirname(__file__), "official_rig.json")
MARK, ON = "fpmp_official_rig", "fpmp_official_rig_on"
PREFIX = "OR "          # every constraint the rig adds
WIDTHS = {"root": 3.5, "limb": 2.5, "digit": 1.5}
CORE = ("pelvis", "spine_01", "thigh_l", "upperarm_l", "head")
MCH = "OR_MCH_"         # mechanism bones: the spline spine's chain, the limbs' IK chains
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
    """Epic's gizmo placement relative to its control (in the bone's game frame), as the bone's custom shape transform."""
    from ...material_porter.effects import ue_offset
    st = item["shape_transform"]
    local = Matrix.LocRotScale(Vector(st["t"]), Quaternion(st["q"]), Vector(st["s"]))
    if item["offset"] is None:
        local = ue_offset(obj.data.bones[item["bone"]]) @ local
    loc, rot, scale = local.decompose()
    pose_bone.use_custom_shape_bone_size = False
    pose_bone.custom_shape_translation = loc
    pose_bone.custom_shape_rotation_euler = rot.to_euler()
    pose_bone.custom_shape_scale_xyz = scale


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
    for kind, item in items:
        bone = edit.new(item["name"])
        at = edit[item["bone"]]
        if item["offset"] is None:
            bone.head, bone.tail, bone.roll = at.head.copy(), at.tail.copy(), at.roll
        else:
            bone.head = at.head + Vector(item["offset"]) * ratio
            bone.tail = bone.head + Vector((0.0, 0.0, max(at.length * 0.5, 0.02)))
        bone.use_deform = False
        if item["parent"]:
            bone.parent = edit[item["parent"]]
    _spine_chain(edit, data["solve"]["spine"])
    _poles_in_plane(edit, data["solve"]["ik"])
    _ik_chains(edit, data["solve"]["ik"])
    bpy.ops.object.mode_set(mode='POSE')
    rig_style.collections(obj.data)
    for b in obj.data.bones:
        if b.name not in names:
            rig_style.assign(obj.data, b.name, "Game Bones")
    for kind, item in items:
        pb = obj.pose.bones[item["name"]]
        if kind == "space":
            rig_style.assign(obj.data, item["name"], "Mechanics")
            continue
        rig_style.assign(obj.data, item["name"], "Controls")
        pb.custom_shape = _shape(item["shape"], data)
        _shape_transform(obj, pb, item)
        rig_shapes.color(pb, tuple(item["color"]))
        pb.custom_shape_wire_width = WIDTHS[item["role"]]
        limits = item.get("limits") or {}
        if item["type"] in ("bool", "float"):
            low, high = limits.get("range") or (0.0, 1.0)      # switches and settings, keyable
            pb[item["name"]] = 0.0
            pb.id_properties_ui(item["name"]).update(min=low, max=high, default=0.0)
        for channel in ("lock_location", "lock_rotation", "lock_scale"):
            if channel in limits:
                setattr(pb, channel, limits[channel])
    for name in [b.name for b in obj.data.bones if b.name.startswith(MCH)]:
        rig_style.assign(obj.data, name, "Mechanics")
    _solve(obj, data, names)
    bpy.ops.object.mode_set(mode='OBJECT')
    for chain in _chains(data, names):
        set_switch(obj, chain["switch"], ik=False, match=False)
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
        for bone, tip in ((root, mid.head), (mid, end.head)):
            ik = edit.new(MCH + "ik_" + bone.name)
            ik.head, ik.tail = bone.head.copy(), tip.copy()
            ik.align_roll(bone.z_axis)
            ik.parent, ik.use_deform = parent, False
            ik.use_connect = parent is not None and parent.name.startswith(MCH)
            pose = edit.new(MCH + "pose_" + bone.name)
            pose.head, pose.tail, pose.roll = bone.head.copy(), bone.tail.copy(), bone.roll
            pose.parent, pose.use_deform = ik, False
            parent = ik


def _off_axis(v, axis):
    """The part of v square to the axis."""
    a = axis.normalized()
    return v - a * v.dot(a)


def _spine_chain(edit, spine):
    """A copy of the spine bones for the spline spine (edit mode)."""
    if not all(b in edit for b in spine["bones"]):
        return
    parent = edit[spine["bones"][0]].parent
    for name in spine["bones"]:
        src = edit[name]
        bone = edit.new(MCH + name)
        bone.head, bone.tail, bone.roll = src.head.copy(), src.tail.copy(), src.roll
        bone.parent, bone.use_deform = parent, False
        bone.use_connect = parent is not None and parent.name.startswith(MCH)
        parent = bone


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
    c.target, c.subtarget = obj, control
    if switch:
        _by_switch(obj, c, switch, ik)
    return c


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
            fk = p["fk"] if part == "spine" else [q for q in p["fk"] if q[1] == p["bones"][-1]]
            out.append({"switch": p["switch"], "fk": [q for q in fk if q[0] in built], "part": part,
                        "ik": [n for n in ik if n in built and n not in always]})
    return out


def _solve(obj, data, built):
    pb, s = obj.pose.bones, data["solve"]

    def has(*names):
        return all(n in built or n in pb for n in names)

    for chain in s["ik"]:
        root, mid, end = chain["bones"]
        if not has(chain["switch"], chain["target"], chain["pole"], root, mid, end, MCH + "ik_" + mid):
            continue
        ball = chain.get("ball") or [None, None]
        for control, bone in chain["fk"]:
            if has(control, bone):
                _copy(obj, bone, control, "FK", chain["switch"], ik=False)
        ik = _constraint(pb[MCH + "ik_" + mid], 'IK', "IK")
        ik.target, ik.subtarget = obj, chain["target"]
        ik.pole_target, ik.pole_subtarget = obj, chain["pole"]
        ik.pole_angle = _pole_angle(obj, MCH + "ik_" + root, chain["pole"])
        ik.chain_count = 2
        for bone in (root, mid):
            _copy(obj, bone, MCH + "pose_" + bone, "IK", chain["switch"], ik=True)
        _copy(obj, end, chain["target"], "IK rotation", chain["switch"], ik=True, kind='COPY_ROTATION')
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
    _spine(obj, s["spine"], has)
    neck = s["neck"]
    if has(neck["switch"], neck["effector"], *neck["bones"]):
        for control, bone in neck["fk"]:      # the neck keeps its FK; only the head switches
            if has(control, bone):
                head = bone == neck["bones"][-1]
                _copy(obj, bone, control, "FK", neck["switch"] if head else None, ik=False)
        _copy(obj, neck["bones"][-1], neck["effector"], "IK", neck["switch"], ik=True, kind='COPY_ROTATION')


def parent_of(data, name):
    return next((i["parent"] for i in data["controls"] + data["spaces"] if i["name"] == name), None)


def _spine(obj, spine, has):
    """FK: each spine control drives its bone. IK: a curve through the hips, tangent and chest controls, a mechanism
    chain on it (Spline IK), the spine bones taking its rotations."""
    pb = obj.pose.bones
    if not has(spine["switch"], *spine["bones"]):
        return
    for control, bone in spine["fk"]:
        if has(control, bone):
            _copy(obj, bone, control, "FK", spine["switch"], ik=False)
    controls = [c for c in spine["ik"] if has(c)]
    if len(controls) < 2 or not has(*(MCH + b for b in spine["bones"])):
        return
    # the curve runs through the spine's own joints (so IK at rest is the rest pose); each joint follows the hips,
    # the tangents and the chest by how far up the spine it sits
    bones = obj.data.bones
    joints = [bones[b].head_local for b in spine["bones"]] + [bones[spine["bones"][-1]].tail_local]
    curve = bpy.data.curves.new("OR_spine_curve", 'CURVE')
    curve.dimensions = '3D'
    spline = curve.splines.new('POLY')
    spline.points.add(len(joints) - 1)
    for point, joint in zip(spline.points, joints):
        point.co = (*(obj.matrix_world @ joint), 1.0)
    curve_obj = bpy.data.objects.new("OR_spine_curve", curve)
    for collection in obj.users_collection:
        collection.objects.link(curve_obj)
    curve_obj.hide_viewport = curve_obj.hide_render = True
    span = len(controls) - 1
    for i in range(len(joints)):
        at = i * span / (len(joints) - 1)          # 0 at the hips control .. span at the chest control
        for k, control in enumerate(controls):
            weight = max(0.0, 1.0 - abs(at - k))
            if weight <= 0.0:
                continue
            hook = curve_obj.modifiers.new("OR hook %d %s" % (i, control), 'HOOK')
            hook.object, hook.subtarget, hook.strength = obj, control, weight
            hook.vertex_indices_set([i])
            hook.matrix_inverse = (obj.matrix_world @ bones[control].matrix_local).inverted()
    sik = _constraint(pb[MCH + spine["bones"][-1]], 'SPLINE_IK', "spine")
    sik.target, sik.chain_count = curve_obj, len(spine["bones"])
    sik.y_scale_mode, sik.xz_scale_mode = 'FIT_CURVE', 'NONE'
    for bone in spine["bones"]:
        _copy(obj, bone, MCH + bone, "IK", spine["switch"], ik=True, kind='COPY_ROTATION')
    _copy(obj, spine["bones"][0], MCH + spine["bones"][0], "IK position", spine["switch"], ik=True, kind='COPY_LOCATION')


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
            eff.matrix = pb[limb["bones"][2]].matrix @ target.matrix.inverted() @ eff.matrix
            bpy.context.view_layer.update()
            pb[limb["pole"]].matrix = _pole_place(obj, limb) @ pb[limb["pole"]].matrix.to_3x3().to_4x4()
            ball = limb.get("ball")
            if ball and ball[0] in pb and ball[1] in pb:
                pb[ball[0]].matrix = pb[ball[1]].matrix.copy()
        elif ik and chain.get("part"):
            s = load()["solve"]
            pairs = [(s["spine"]["ik"][-1], s["spine"]["bones"][-1])] if chain["part"] == "spine" else \
                [(s["neck"]["effector"], s["neck"]["bones"][-1])]
            for control, bone in pairs:
                if control in pb and bone in pb:
                    pb[control].matrix = pb[bone].matrix.copy()
        elif not ik:
            poses = {control: pb[bone].matrix.copy() for control, bone in chain["fk"]}
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


def ui(layout, obj):
    """The official rig's panel lines: Rig on/off, the FK/IK switches, Bake to Controls."""
    col = layout.column(align=True)
    on = bool(obj.data.get(ON, True))
    col.operator("fpmp.official_rig_on", text="Rig On" if not on else "Rig Off", icon='CONSTRAINT_BONE').on = not on
    for chain in _chains(load(), set(obj.pose.bones.keys())):
        switch = chain["switch"]
        row = col.row(align=True)
        row.label(text=switch.replace("_fk_ik_switch", "").replace("_", " ").title())
        ik = obj.pose.bones[switch].get(switch, 0.0) >= 0.5
        fk_op = row.operator("fpmp.official_rig_switch", text="FK", depress=not ik)
        fk_op.switch, fk_op.ik = switch, False
        ik_op = row.operator("fpmp.official_rig_switch", text="IK", depress=ik)
        ik_op.switch, ik_op.ik = switch, True
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
        poses[f] = {control: pb[bone].matrix.copy() for control, bone in targets.items()}
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
