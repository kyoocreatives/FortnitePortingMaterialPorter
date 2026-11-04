"""Limb twist on the official rig: the game fills the twist bones at runtime (animations leave them at rest), so a
turned wrist or foot pinches the skin. Each twist bone takes a share of its limb's twist by where it sits along the
limb: the forearm and calf follow the hand and foot, the upper arm and thigh unwind their own bone's turn. Twist is read
as swing and twist (stable up to a half turn, bending ignored). Preserve Volume: the body's skinning keeps its volume
at bent joints (dual quaternion)."""
import bpy

PREFIX = "TW "          # its own prefix: it stays on when the rig steps aside for an imported animation
MARK, ON = "fpmp_limb_twist", "fpmp_limb_twist_on"
# twist bones' limb, the joint at its far end, the bone whose turn they share, and whether they unwind it
LIMBS = (("lowerarm", "hand", "hand", False), ("calf", "foot", "foot", False),
         ("upperarm", "lowerarm", "upperarm", True), ("thigh", "calf", "thigh", True))


def _axis(bone, direction):
    """The bone's local axis nearest a rest direction (armature space), and its sign."""
    m = bone.matrix_local.to_3x3()
    dots = [m.col[i].normalized().dot(direction) for i in range(3)]
    i = max(range(3), key=lambda k: abs(dots[k]))
    return "XYZ"[i], 1.0 if dots[i] > 0 else -1.0


def _twist_bones(bones, limb, side):
    return [b for b in bones if b.startswith(limb + "_twist_") and b.endswith("_" + side)]


def build(obj):
    """The twist constraints, once; returns how many twist bones follow."""
    if obj.data.get(MARK):
        return 0
    bones, pb = obj.data.bones, obj.pose.bones
    count = 0
    for side in ("l", "r"):
        for limb, end, source, unwind in LIMBS:
            names = [limb + "_" + side, end + "_" + side, source + "_" + side]
            if not all(n in bones for n in names):
                continue
            start, stop = bones[names[0]].head_local, bones[names[1]].head_local
            direction = (stop - start).normalized()
            length = (stop - start).length
            src_axis, src_sign = _axis(bones[names[2]], direction)
            for name in _twist_bones(bones.keys(), limb, side):
                t = max(0.0, min(1.0, (bones[name].head_local - start).dot(direction) / length))
                share = -(1.0 - t) if unwind else t
                own_axis, own_sign = _axis(bones[name], direction)
                c = pb[name].constraints.new('TRANSFORM')
                c.name = PREFIX + "twist"
                c.target, c.subtarget = obj, names[2]
                c.target_space = c.owner_space = 'LOCAL'
                c.map_from, c.from_rotation_mode = 'ROTATION', 'SWING_TWIST_' + src_axis
                a = src_axis.lower()
                setattr(c, "from_min_%s_rot" % a, -3.14159265)
                setattr(c, "from_max_%s_rot" % a, 3.14159265)
                c.map_to, c.mix_mode_rot = 'ROTATION', 'REPLACE'     # the other channels map to 0: rest
                o, k = own_axis.lower(), share * src_sign * own_sign
                setattr(c, "map_to_%s_from" % o, src_axis)
                setattr(c, "to_min_%s_rot" % o, -3.14159265 * k)
                setattr(c, "to_max_%s_rot" % o, 3.14159265 * k)
                count += 1
    obj.data[MARK] = True
    obj.data[ON] = True
    return count


def is_on(obj):
    return bool(obj.data.get(MARK)) and bool(obj.data.get(ON, True))


def set_on(obj, on):
    """Twist bones follow their limb (built on first use), or stay at rest."""
    if on:
        build(obj)
    for pose_bone in obj.pose.bones:
        for c in pose_bone.constraints:
            if c.name.startswith(PREFIX):
                c.mute = not on
    obj.data[ON] = on


def _skins(obj):
    return [m for o in bpy.data.objects if o.type == 'MESH' for m in o.modifiers
            if m.type == 'ARMATURE' and m.object == obj]


def volume(obj):
    skins = _skins(obj)
    return bool(skins) and all(m.use_deform_preserve_volume for m in skins)


def set_volume(obj, on):
    for m in _skins(obj):
        m.use_deform_preserve_volume = on
