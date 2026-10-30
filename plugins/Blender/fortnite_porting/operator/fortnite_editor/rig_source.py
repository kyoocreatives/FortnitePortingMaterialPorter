"""The Rig tab's view of an armature, read from the rig itself: its controls (seen from the front), its limbs' IK/FK
switches, and its face board's knobs (each with the axes and range its Limit Location gives). The tab never changes
a rig's data: it selects bones, sets knob locations and flips switches through the rig's own setters."""
import re
from collections import namedtuple

import numpy as np
from mathutils import Euler, Matrix

from .rig_layout import FINGERS, Axis, Board, Control, Knob, Switch, finger_part

# a Control's chain: its nearest control ancestor; skeleton: the deform bones as (x0, z0, x1, z1), seen from the front
Rig = namedtuple("Rig", "kind controls switches board skeleton")

BOARD_ROOTS = (("MB_Board", "metahuman"), ("CR_FaceBoard", "bones"))
NOT_CONTROLS = {"Mechanics", "Game Bones", "Deform Mechanics", "Face Mechanics", "Base", "Twist", "Dynamic", "Deform", "Extra"}
_ROLES = (("root", ("global", "root")),
          ("finger", ("thumb", "index", "middle", "ring", "pinky", "metacarpal", "finger", "palm")),
          ("body", ("body", "hips", "pelvis", "spine", "chest", "neck", "head", "clavicle", "torso")),
          ("limb", ("hand", "foot", "arm", "leg", "thigh", "calf", "ball", "knee", "elbow", "_pv", "pole", "_ik", "roll")))


def armature_of(obj):
    """The armature an object belongs to: itself, a mesh's Armature modifier target, or its armature parent."""
    if obj is None:
        return None
    if obj.type == 'ARMATURE':
        return obj
    for m in getattr(obj, "modifiers", ()):
        if m.type == 'ARMATURE' and m.object is not None:
            return m.object
    parent = obj.parent
    return parent if parent is not None and parent.type == 'ARMATURE' else None


def kind(arm):
    data = arm.data
    if data.get("fpmp_official_rig"):
        return "official"
    if data.get("is_tasty"):
        return "tasty"
    if data.get("fpmp_rig_kit"):
        return "kit"
    return "generic"


def side_of(name):
    if re.search(r"(^|_)l(_|$)|_L_|^L_|(?<=[a-z])L$", name):
        return "L"
    if re.search(r"(^|_)r(_|$)|_R_|^R_|(?<=[a-z])R$", name):
        return "R"
    return "C"


def role_of(name):
    low = name.lower()
    for role, words in _ROLES:
        if any(w in low for w in words):
            return role
    return "other"


def _facing_sign(arm):
    """+1 when the character faces -Y (seen from the front, its left is +X), -1 when it faces +Y."""
    bones = arm.data.bones
    for foot, ball in (("foot_l", "ball_l"), ("foot_r", "ball_r")):
        if foot in bones and ball in bones:
            return 1.0 if bones[ball].head_local.y <= bones[foot].head_local.y else -1.0
    return 1.0


def _board_bones(arm):
    for root, board_kind in BOARD_ROOTS:
        if root in arm.data.bones:
            return root, board_kind
    return None, None


def _under(bone, root):
    while bone is not None:
        if bone.name == root:
            return True
        bone = bone.parent
    return False


def _is_control(arm, pb, rig_kind, board_root):
    bone = pb.bone
    if pb.custom_shape is None or (board_root and _under(bone, board_root)) or bone.name.startswith(("MB_", "CR_Face")):
        return False
    groups = {c.name for c in bone.collections}
    if rig_kind == "tasty":
        return "Rig" in groups
    return bool(groups) and not groups <= NOT_CONTROLS and not any(g.startswith("Face") for g in groups)


def switches(arm, rig_kind):
    pb = arm.pose.bones
    out = []
    if rig_kind == "official":
        from ...processing.context import official_rig
        for chain in official_rig._chains(official_rig.load(), set(pb.keys())):
            name = chain["switch"]
            if name in pb and name in pb[name]:
                label = name.replace("_fk_ik_switch", "").replace("_", " ").title()
                out.append(Switch(name, label, side_of(name), pb[name][name] >= 0.5, name))
    elif rig_kind == "kit":
        from ...processing.context import rig_kit
        settings = pb.get("CR_Settings")
        for limb in rig_kit.meta(arm).get("limbs") or []:
            setting = limb.get("setting")
            if settings is not None and setting in settings:
                out.append(Switch(limb["name"], limb.get("label") or limb["name"], (limb.get("side") or "C")[:1].upper(),
                                  settings[setting] >= 0.5, limb["name"]))
    elif rig_kind == "tasty":
        for prop, label in (("use_ik_hand_l", "Arm L"), ("use_ik_hand_r", "Arm R"), ("use_ik_leg_l", "Leg L"),
                            ("use_ik_leg_r", "Leg R")):
            if prop in arm:
                out.append(Switch(prop, label, label[-1], bool(arm[prop]), prop))
    return out


def set_switch(arm, switch, ik):
    """IK (True) or FK for one switch, through the rig's own setter (the incoming controls match the pose)."""
    rig_kind = kind(arm)
    if rig_kind == "official":
        from ...processing.context import official_rig
        official_rig.set_switch(arm, switch.name, ik, match=True)
    elif rig_kind == "kit":
        from ...processing.context import rig_kit
        rig_kit.switch(arm, switch.name, ik)
    else:
        arm[switch.name] = bool(ik)
        arm.update_tag()


def switch_path(arm, switch):
    """The armature's data path of the property a switch flips (what keying it keys), None when the rig has none."""
    from bpy.utils import escape_identifier as esc
    rig_kind = kind(arm)
    if rig_kind == "official":
        return 'pose.bones["%s"]["%s"]' % (esc(switch.name), esc(switch.name))
    if rig_kind == "kit":
        from ...processing.context import rig_kit
        limb = next((l for l in rig_kit.meta(arm).get("limbs") or [] if l["name"] == switch.name), None)
        return 'pose.bones["CR_Settings"]["%s"]' % esc(limb["setting"]) if limb and limb.get("setting") else None
    return '["%s"]' % esc(switch.name)


def hidden(arm):
    """Controls the rig hides now (the inactive IK or FK chain)."""
    return {pb.name for pb in arm.pose.bones if pb.hide or pb.bone.hide}


def _limit(pb):
    return next((c for c in pb.constraints if c.type == 'LIMIT_LOCATION'), None)


def _knob(arm, pb, root):
    limit = _limit(pb)
    if limit is None:
        return None
    rest = pb.bone.matrix_local
    axes = []
    for i, attr in enumerate("xyz"):
        if pb.lock_location[i] or not (getattr(limit, "use_min_" + attr) and getattr(limit, "use_max_" + attr)):
            continue
        lo, hi = getattr(limit, "min_" + attr), getattr(limit, "max_" + attr)
        if hi - lo > 1e-9:
            col = rest.col[i]
            axes.append(Axis(i, col.x, col.z, lo, hi))
    if not axes:
        return None
    group = next((c.name[5:] for c in pb.bone.collections if c.name.startswith("Face ")), "")
    head = pb.bone.head_local
    size = pb.custom_shape_scale_xyz[0] * (pb.bone.length if pb.use_custom_shape_bone_size else 1.0) if pb.custom_shape else 0.0
    return Knob(pb.name, group, head.x, head.z, tuple(axes), size)


def _outline(arm, root):
    """The board bone's custom shape as triangles in the board plane (x, z), or None."""
    pb = arm.pose.bones[root]
    shape = pb.custom_shape
    if shape is None or shape.type != 'MESH' or not shape.data.polygons:
        return None
    size = pb.bone.length if pb.use_custom_shape_bone_size else 1.0
    m = (pb.bone.matrix_local @ Matrix.Translation(pb.custom_shape_translation)
         @ Euler(pb.custom_shape_rotation_euler).to_matrix().to_4x4()
         @ Matrix.Diagonal([s * size for s in pb.custom_shape_scale_xyz] + [1.0]))
    mesh = shape.data
    mesh.calc_loop_triangles()
    points = [(m @ v.co) for v in mesh.vertices]
    return [(points[i].x, points[i].z) for tri in mesh.loop_triangles for i in tri.vertices]


def _hand_grid(controls, side):
    """A hand's finger controls as a schematic: a column per finger (the thumb toward the body's middle on screen), a
    row per joint (metacarpal 0 to 03); the hand-wide controls (fingers, palm, curls) in a row before the metacarpals."""
    out, extra = {}, []
    for c in controls:
        if c.role != "finger" or c.side != side:
            continue
        finger, joint = finger_part(c.name)
        if joint is None:
            extra.append(c.name)
            continue
        finger = FINGERS.index(finger)
        column = len(FINGERS) - 1 - finger if side == "R" else finger      # the thumb toward the body
        out[c.name] = (float(column), float(joint))
    for i, name in enumerate(sorted(extra)):
        out[name] = (i * (len(FINGERS) - 1) / max(1, len(extra) - 1) if len(extra) > 1 else 2.0, -1.2)
    return out


def _skeleton(arm, flip, root):
    """Joint to parent joint for the deform bones (FP's bone tails point anywhere); no face, IK targets or hair, nothing
    hung on the head (hats, hair: a fan of spikes over it)."""
    def keep(b):
        return (b is not None and b.use_deform and not b.name.startswith(("FACIAL", "MB_", "CR_", "ik_", "dyn_"))
                and not (root and _under(b, root)) and not (b.parent and _under(b.parent, "head")))
    out = []
    for bone in arm.data.bones:
        if keep(bone) and keep(bone.parent):
            h, p = bone.head_local, bone.parent.head_local
            out.append((p.x * flip, p.z, h.x * flip, h.z))
    return out


def body_meshes(arm):
    """The character's shown meshes: those under the armature that it deforms or carries."""
    return [o for o in arm.children_recursive if o.type == 'MESH' and armature_of(o) == arm and o.visible_get()]


def silhouette(arm, meshes):
    """The meshes' triangles at rest seen from the front, as an (n, 2) float32 array of (x, z) in the armature's
    space (the controls' plane), or None."""
    to_arm = arm.matrix_world.inverted()
    flip = _facing_sign(arm)
    parts = []
    for ob in meshes:
        me = ob.data
        me.calc_loop_triangles()
        if not me.loop_triangles:
            continue
        co = np.empty(len(me.vertices) * 3, np.float32)
        me.vertices.foreach_get("co", co)
        tris = np.empty(len(me.loop_triangles) * 3, np.int32)
        me.loop_triangles.foreach_get("vertices", tris)
        m = np.array(to_arm @ ob.matrix_world, np.float32)
        co = co.reshape(-1, 3) @ m[:3, :3].T + m[:3, 3]
        parts.append(np.stack((co[tris, 0] * flip, co[tris, 2]), axis=1))
    return np.concatenate(parts) if parts else None


def read(arm):
    rig_kind = kind(arm)
    root, board_kind = _board_bones(arm)
    flip = _facing_sign(arm)
    picked = [pb for pb in arm.pose.bones if _is_control(arm, pb, rig_kind, root)]
    names = {pb.name for pb in picked}
    controls = []
    for pb in picked:
        head, up = pb.bone.head_local, pb.bone.parent
        while up is not None and up.name not in names:
            up = up.parent
        controls.append(Control(pb.name, side_of(pb.name), role_of(pb.name), up.name if up else "", head.x * flip, head.z))
    for side in "LR":
        palm = _hand_grid(controls, side)
        controls = [c._replace(x=palm[c.name][0], z=palm[c.name][1]) if c.name in palm else c for c in controls]
    board = None
    if root:
        knobs = [k for k in (_knob(arm, pb, root) for pb in arm.pose.bones if pb.name != root and _under(pb.bone, root)) if k]
        if knobs:
            step = arm.data.get("fpmp_face_step", 0.0) if board_kind == "bones" else 0.0    # face_board.STEP
            board = Board(board_kind, knobs, _outline(arm, root), step)
    if not controls and board is None:
        rig_kind = "none"
    return Rig(rig_kind, controls, switches(arm, rig_kind), board, _skeleton(arm, flip, root))


def knob_values(arm, bone):
    pb = arm.pose.bones[bone]
    limit = _limit(pb)
    return {i: pb.location[i] for i, attr in enumerate("xyz")
            if limit and not pb.lock_location[i] and getattr(limit, "use_min_" + attr) and getattr(limit, "use_max_" + attr)
            and getattr(limit, "max_" + attr) - getattr(limit, "min_" + attr) > 1e-9}


def set_knob(arm, bone, values):
    pb = arm.pose.bones[bone]
    for i, v in values.items():
        pb.location[i] = v
