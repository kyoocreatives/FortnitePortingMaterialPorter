"""The kit the fork's custom rigs (creatures, LEGO figures) are built on, to the official rig's standard: Epic's gizmos
and colours, controls sized to the mesh around them, control bones the game bones copy (the rig can be switched off and
an animation baked onto it), IK limbs through a mechanism chain with an FK/IK switch that keeps the pose.

An FK control is a copy of its game bone (same rest frame), so the bone copies it exactly and nothing moves at rest.
What each rig built is kept on the armature (META): the controls the game bones copy and its limbs."""
import json
from math import pi

import bpy
from mathutils import Matrix, Vector

from . import rig_shapes, rig_style

PREFIX = "RK "                      # constraints the kit puts on game bones: the rig off mutes them
MCH = "MCH_"
META, ON = "fpmp_rig_kit", "fpmp_rig_kit_on"
UNIT = 0.05                         # a gizmo's native radius
RING = (pi / 2.0, 0.0, 0.0)         # a flat gizmo stood up around its bone's Y axis
# Epic's colours, as the official rig shows them
COLORS = {"root": (1.0, 0.96, 0.0), "body": (1.0, 0.07, 0.0), "hips": (1.0, 0.5, 0.09), "neck": (1.0, 0.96, 0.0),
          "spine_low": (1.0, 0.21, 0.0), "spine_high": (1.0, 0.78, 0.0),
          "L": (0.0, 0.02, 0.41), "R": (0.51, 0.0, 0.0), "C": (1.0, 0.78, 0.0),
          "L_digit": (0.0, 0.02, 0.41), "R_digit": (1.0, 0.0, 0.0), "C_digit": (1.0, 0.5, 0.09),
          "L_pivot": (0.08, 0.1, 0.6), "R_pivot": (0.56, 0.14, 0.14), "line": (0.55, 0.55, 0.55)}
WIDTHS = {"root": 3.5, "limb": 2.5, "digit": 1.5, "line": 1.0}


def gizmo(name):
    from . import official_rig
    return official_rig.gizmo(name)


def tone(side, kind=""):
    """Epic's colour of a control on a side ("L", "R", "C"), plain or of a kind ("digit", "pivot")."""
    return COLORS.get("%s_%s" % (side, kind), COLORS[side]) if kind else COLORS[side]


def spine_tone(t):
    """Spine colours run from red-orange at the hips to yellow-orange at the chest (t 0 to 1)."""
    a, b = Vector(COLORS["spine_low"]), Vector(COLORS["spine_high"])
    return tuple(a.lerp(b, max(0.0, min(1.0, t))))


class Fit:
    """The skinned mesh around each bone at rest (armature space): each vertex counts for the bone weighing most on it."""

    def __init__(self, obj):
        self.points = {}
        inverse = obj.matrix_world.inverted()
        meshes = [o for o in bpy.data.objects if o.type == 'MESH'
                  and any(m.type == 'ARMATURE' and m.object == obj for m in o.modifiers)]
        for mesh in meshes:
            names = {g.index: g.name for g in mesh.vertex_groups}
            to_arm = inverse @ mesh.matrix_world
            for v in mesh.data.vertices:
                best = max(v.groups, key=lambda g: g.weight, default=None)
                if best is not None and best.weight > 0.3 and best.group in names:
                    self.points.setdefault(names[best.group], []).append(to_arm @ v.co)
        self.all = [p for ps in self.points.values() for p in ps]

    def radius(self, bones, head, tail, fallback):
        """How far the mesh of `bones` reaches around the line head-tail (65th percentile: fur and spikes don't inflate
        it), else `fallback`."""
        pts = [p for b in bones for p in self.points.get(b, ())]
        if len(pts) < 8:
            return fallback
        axis = tail - head
        if axis.length < 1e-6:
            d = sorted((p - head).length for p in pts)
        else:
            u = axis.normalized()
            d = sorted(((p - head) - u * (p - head).dot(u)).length for p in pts)
        return max(d[int(len(d) * 0.65)], fallback * 0.25)

    def bounds(self, bones=None):
        """(low, high) of the mesh of `bones` (all when None), or None."""
        pts = self.all if bones is None else [p for b in bones for p in self.points.get(b, ())]
        if not pts:
            return None
        return Vector([min(p[i] for p in pts) for i in range(3)]), Vector([max(p[i] for p in pts) for i in range(3)])


# edit mode

def copy_bone(edit, source, name, parent):
    """A bone with `source`'s rest frame (an FK control or a follower), not deforming."""
    b = edit.new(name)
    b.head, b.tail, b.roll = source.head.copy(), source.tail.copy(), source.roll
    b.parent, b.use_deform = parent, False
    return b


def new_bone(edit, name, head, tail, parent, z=None):
    """A bone head to tail, its Z axis toward `z` when given."""
    b = edit.new(name)
    b.head, b.tail = head, tail
    if z is not None:
        b.align_roll(z)
    b.parent, b.use_deform = parent, False
    return b


def pole_angle(base, tip, pole):
    """IK pole angle that keeps a chain's rest pose (base: its first edit bone, tip: where it ends)."""
    def signed(a, b, normal):
        angle = a.angle(b)
        return -angle if a.cross(b).angle(normal) < 1.0 else angle
    normal = (tip - base.head).cross(pole - base.head)
    axis = normal.cross(base.tail - base.head)
    return signed(base.x_axis, axis, base.tail - base.head)


def ik_limb(edit, name, chain, foot, tip, parent, target_parent, ahead, size, front=False):
    """The IK half of a limb, in edit mode. `chain`: the game bones IK bends, top down; `foot`: the bone after them (its
    head is where they end) or None (they end at `tip`). A mechanism chain joint to joint (MCH_<bone>_ik) solves, each
    game bone follows it through a copy at its own rest frame (MCH_<bone>_ik_pose); `parent` holds the chain. The
    target (<name>_ik_ctrl) is world-aligned, flat, under `target_parent`; the pole (<name>_pv_ctrl) sits out from the bend. Returns the limb's
    metadata (bone names) and the pole angle."""
    joints = [edit[n].head.copy() for n in chain] + [tip.copy()]
    length = sum((b - a).length for a, b in zip(joints, joints[1:]))
    line = joints[-1] - joints[0]
    off = lambda p: p - (joints[0] + line * ((p - joints[0]).dot(line) / max(line.length_squared, 1e-12)))
    bend_at = max(range(1, len(joints) - 1), key=lambda i: off(joints[i]).length) if len(joints) > 2 else 1
    bend = off(joints[bend_at]) if len(joints) > 2 else Vector()
    if bend.length < 2e-3 * length and len(joints) > 2:
        # a straight limb: IK can't pick a bend, so the mechanism's joint (not the game bone) leans 1% of the limb
        # (hind knees forward, front elbows back)
        lean = (-ahead if front else ahead) - line.normalized() * (-ahead if front else ahead).dot(line.normalized())
        if lean.length < 1e-6:
            lean = edit[chain[0]].x_axis.copy()
        joints[bend_at] = joints[bend_at] + lean.normalized() * length * 0.01
        bend = off(joints[bend_at])
    if bend.length < 1e-6:
        bend = edit[chain[0]].z_axis.copy()
    mch = []
    for i, bone in enumerate(chain):
        m = new_bone(edit, MCH + bone + "_ik", joints[i], joints[i + 1], edit.get(parent) if i == 0 else edit[mch[-1]],
                     edit[bone].z_axis)
        m.use_connect = i > 0
        mch.append(m.name)
        copy_bone(edit, edit[bone], MCH + bone + "_ik_pose", m)
    down = Vector((0.0, 0.0, -1.0))
    target = new_bone(edit, name + "_ik_ctrl", joints[-1], joints[-1] + ahead * size, edit.get(target_parent), -down)
    if foot is not None:
        copy_bone(edit, edit[foot], MCH + foot + "_ik_pose", target)
    at = joints[bend_at] + bend.normalized() * length * 0.5
    pole = new_bone(edit, name + "_pv_ctrl", at, at + Vector((0.0, 0.0, size)), edit.get(target_parent), ahead)
    line_bone = new_bone(edit, MCH + name + "_pole_line", joints[bend_at], at, edit[mch[bend_at - 1]] if bend_at > 0 else None)
    line_bone.tail = at
    angle = pole_angle(edit[mch[0]], joints[-1], at)
    return {"name": name, "bones": list(chain) + ([foot] if foot else []), "mch": mch, "target": target.name,
            "pole": pole.name, "line": line_bone.name, "bend": bend_at, "foot": foot}, angle


# pose mode

def align(obj, pose_bone, x=None, y=None, z=None):
    """Rotate a control's gizmo so its axes point along x/y/z (armature space; a flat gizmo lies in its XY plane, so
    z=up lays it on the ground). Missing axes complete a right-handed frame."""
    axes = [x, y, z]
    if axes.count(None) == 2:
        i = next(i for i in range(3) if axes[i] is not None)
        a = axes[i].normalized()
        other = Vector((1, 0, 0)) if abs(a.x) < 0.9 else Vector((0, 1, 0))
        b = a.cross(other).normalized()
        axes = [None] * 3
        axes[i], axes[(i + 1) % 3], axes[(i + 2) % 3] = a, b, a.cross(b)
    elif None in axes:
        i = axes.index(None)
        axes[i] = axes[(i + 1) % 3].cross(axes[(i + 2) % 3])
    axes = [a.normalized() for a in axes]
    want = Matrix([axes[0], axes[1], axes[2]]).transposed()
    rest = obj.data.bones[pose_bone.name].matrix_local.to_3x3()
    pose_bone.custom_shape_rotation_euler = (rest.inverted() @ want).to_euler()


def look(obj, name, shape, radius, color, width, group, ring=False, at=None, stretch=None):
    """A control's look: Epic gizmo `shape` at `radius` metres (`stretch` scales its axes further), colour, wire width,
    collection; `ring` stands it around the bone, `at` (armature space) moves it there."""
    pb = obj.pose.bones[name]
    pb.custom_shape = gizmo(shape)
    pb.use_custom_shape_bone_size = False
    s = max(radius, 1e-4) / UNIT
    pb.custom_shape_scale_xyz = tuple(s * k for k in (stretch or (1.0, 1.0, 1.0)))
    if ring:
        pb.custom_shape_rotation_euler = RING
    if at is not None:
        rig_shapes.place(obj, pb, at)
    rig_shapes.color(pb, color)
    pb.custom_shape_wire_width = width
    rig_style.assign(obj.data, name, group)
    return pb


def follow(obj, bone, control, name="follow"):
    """The game bone copies its control (same rest frame: exact)."""
    c = obj.pose.bones[bone].constraints.new('COPY_TRANSFORMS')
    c.name, c.target, c.subtarget = PREFIX + name, obj, control
    return c


def limb_constraints(obj, limb, angle, setting):
    """The IK half's constraints: IK on the mechanism chain, each game bone (and the foot) copies its follower with
    the limb's IK setting as influence; the pole line stretches to the pole."""
    pose = obj.pose.bones
    ik = pose[limb["mch"][-1]].constraints.new('IK')
    ik.name = PREFIX + "ik"
    ik.target, ik.subtarget = obj, limb["target"]
    ik.pole_target, ik.pole_subtarget, ik.pole_angle = obj, limb["pole"], angle
    ik.chain_count = len(limb["mch"])
    for bone in limb["bones"]:
        if bone == limb["foot"]:
            c = pose[bone].constraints.new('COPY_ROTATION')
            c.name, c.target, c.subtarget = PREFIX + "ik", obj, MCH + bone + "_ik_pose"
            rig_style.driven(obj, c, setting)
            c = pose[bone].constraints.new('COPY_LOCATION')
            c.name, c.target, c.subtarget, c.head_tail = PREFIX + "ik tip", obj, limb["mch"][-1], 1.0
        else:
            c = pose[bone].constraints.new('COPY_TRANSFORMS')
            c.name, c.target, c.subtarget = PREFIX + "ik", obj, MCH + bone + "_ik_pose"
        rig_style.driven(obj, c, setting)
    stretch = pose[limb["line"]].constraints.new('STRETCH_TO')
    stretch.target, stretch.subtarget = obj, limb["pole"]
    line = pose[limb["line"]]
    line.custom_shape = rig_shapes.ensure("CR_Line")
    line.use_custom_shape_bone_size = True
    rig_shapes.color(line, COLORS["line"])
    line.custom_shape_wire_width = WIDTHS["line"]
    obj.data.bones[limb["line"]].hide_select = True


# what was built

def meta(obj):
    try:
        return json.loads(obj.data.get(META) or "{}")
    except ValueError:
        return {}


def save(obj, data):
    obj.data[META] = json.dumps(data)


def is_kit(obj):
    return obj is not None and obj.type == 'ARMATURE' and bool(obj.data.get(META))


# running it

def set_on(obj, on):
    """The rig drives the game bones (on), or they play their own keys (off)."""
    for pose_bone in obj.pose.bones:
        for c in pose_bone.constraints:
            if c.name.startswith(PREFIX):
                c.mute = not on
    obj.data[ON] = on


def on_animation_import(obj):
    """An imported animation keys the game bones: the rig steps aside (Bake to Controls brings it back)."""
    if is_kit(obj):
        set_on(obj, False)


def _place_target(obj, limb):
    """The IK target's pose matrix that puts the foot (or the chain's tip) where it is now."""
    pose, bones = obj.pose.bones, obj.data.bones
    target = bones[limb["target"]]
    if limb["foot"]:
        foot = bones[limb["foot"]]
        return pose[limb["foot"]].matrix @ foot.matrix_local.inverted() @ target.matrix_local
    last = pose[limb["bones"][-1]]
    m = pose[limb["target"]].matrix.copy()
    m.translation = last.tail
    return m


def _place_pole(obj, limb):
    """The pole's pose matrix out from the limb's bend as it is now."""
    pose = obj.pose.bones
    joints = [pose[b].head.copy() for b in limb["bones"][:len(limb["mch"])]]
    joints.append(pose[limb["foot"]].head.copy() if limb["foot"] else pose[limb["bones"][-1]].tail.copy())
    length = sum((b - a).length for a, b in zip(joints, joints[1:]))
    line = joints[-1] - joints[0]
    p = joints[min(limb["bend"], len(joints) - 2)]
    bend = p - (joints[0] + line * ((p - joints[0]).dot(line) / max(line.length_squared, 1e-12)))
    m = pose[limb["pole"]].matrix.copy()
    if bend.length > 1e-5 * length:
        m.translation = p + bend.normalized() * length * 0.5
    return m


def switch(obj, name, ik, match=True):
    """A limb to IK or FK. `match` first puts the incoming controls where the bones are now, so the limb stays put;
    the outgoing controls hide."""
    limb = next((l for l in meta(obj).get("limbs", []) if l["name"] == name), None)
    if limb is None:
        return
    pose = obj.pose.bones
    if match:
        bpy.context.view_layer.update()
        if ik:
            want = {limb["target"]: _place_target(obj, limb), limb["pole"]: _place_pole(obj, limb)}
            for control, m in want.items():
                pose[control].matrix = m
                bpy.context.view_layer.update()
        else:
            poses = {c: pose[b].matrix.copy() for c, b in zip(limb["fk"], limb["bones"])}
            for c in limb["fk"]:
                pose[c].matrix = poses[c]
                bpy.context.view_layer.update()
    pose[rig_style.SETTINGS][limb["setting"]] = 1.0 if ik else 0.0
    obj.update_tag()
    show(obj, limb, ik)
    bpy.context.view_layer.update()


def show(obj, limb, ik):
    """Show a limb's IK or FK controls, hide the others (Blender 5 shows a pose by the pose bone's hide)."""
    pose = obj.pose.bones
    for c in limb["fk"]:
        pose[c].hide = ik
    for c in limb["ik"]:
        if c in pose:
            pose[c].hide = not ik


def _order(obj, names):
    """Controls parents first."""
    bones = obj.data.bones
    return sorted(names, key=lambda n: len(bones[n].parent_recursive))


def _lands(have, want):
    turn = have.to_quaternion().rotation_difference(want.to_quaternion()).angle
    return (have.to_translation() - want.to_translation()).length < 1e-5 and min(turn, 2.0 * pi - turn) < 1e-5


def bake_to_controls(obj, start, end):
    """Key the controls from the animated game bones over start..end, then turn the rig on (limbs in FK). The bones'
    own keys stay underneath in an NLA track. Returns the frames baked."""
    data, scene, pose = meta(obj), bpy.context.scene, obj.pose.bones
    follows = data.get("follow", {})
    limbs = data.get("limbs", [])
    set_on(obj, False)
    poses = {}
    for f in range(start, end + 1):
        scene.frame_set(f)
        want = {c: pose[b].matrix.copy() for c, b in follows.items()}
        for limb in limbs:
            want[limb["target"]] = _place_target(obj, limb)
            want[limb["pole"]] = _place_pole(obj, limb)
        poses[f] = want
    ad = obj.animation_data or obj.animation_data_create()
    if ad.action is not None:
        track = ad.nla_tracks.new()
        track.name = "RK bones"
        track.strips.new(ad.action.name, int(ad.action.frame_range[0]), ad.action)
    ad.action = bpy.data.actions.new("RK controls (%s)" % obj.name)
    for limb in limbs:
        pose[rig_style.SETTINGS][limb["setting"]] = 0.0
        show(obj, limb, False)
    set_on(obj, True)
    obj.update_tag()
    keyed = _order(obj, list(poses[start]))
    still = [c for c in data.get("controls", []) if c not in poses[start] and c in pose]
    rest = {c: (pose[c].parent.bone.matrix_local.inverted() @ pose[c].bone.matrix_local) if pose[c].parent
            else pose[c].bone.matrix_local.copy() for c in keyed}
    for f in range(start, end + 1):
        scene.frame_set(f)
        want = poses[f]
        for c in still:
            pose[c].matrix_basis = Matrix()
        for _ in range(4):          # a control under a constrained or unkeyed bone settles over passes
            for c in keyed:
                parent = pose[c].parent
                above = want[parent.name] if parent is not None and parent.name in want else \
                    (parent.matrix if parent is not None else Matrix())
                pose[c].matrix_basis = (above @ rest[c]).inverted() @ want[c]
            bpy.context.view_layer.update()
            if all(_lands(pose[c].matrix, want[c]) for c in keyed):
                break
        for c in keyed + still:
            pb = pose[c]
            pb.keyframe_insert("location", frame=f)
            pb.keyframe_insert("rotation_quaternion" if pb.rotation_mode == 'QUATERNION' else "rotation_euler", frame=f)
            pb.keyframe_insert("scale", frame=f)
    return end - start + 1
