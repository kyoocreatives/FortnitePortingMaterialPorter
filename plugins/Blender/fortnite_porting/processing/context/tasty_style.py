"""FP's character rig (Tasty) in the rig kit's colours and visibility, applied after FP builds it (FP's files untouched).

Tasty's groups: Rig (root, IK, poles, spine, fingers) and Face stay shown; Dynamic (hair, cloth), Base (the plain
skeleton), Twist, Deform, Sockets and Extra are hidden, one click away in the Rig panel. Tasty's shapes are kept."""
import re

from . import face_board, rig_style

SHOWN = ("Rig", "Face")
HIDDEN = ("Dynamic", "Base", "Twist", "Deform", "Sockets", "Extra")
SECONDARY = ("Face", "Dynamic")
MARK = "fpmp_tasty_styled"
SIDE = re.compile(r"(^|_)(?P<s>[lr])($|_)", re.IGNORECASE)     # hand_l, l_eye, FACIAL_L_Eye


def side(name):
    m = SIDE.search(name)
    if m is None:
        return "C"
    return m.group("s").upper()


def style(obj):
    """Restyle a Tasty armature once; False when it already was."""
    data = obj.data
    if data.get(MARK):
        return False
    for collection in data.collections_all:
        if collection.name in SHOWN + HIDDEN:       # a group FP adds later stays as FP made it
            collection.is_visible = collection.name in SHOWN
    for bone in obj.pose.bones:
        if bone.custom_shape is None:
            continue
        groups = {c.name for c in bone.bone.collections}
        if bone.name == "root":
            rig_style.style(bone, "main")
        elif groups & set(SECONDARY):
            rig_style.style(bone, side(bone.name), secondary=True)
        elif "Rig" in groups:
            rig_style.style(bone, side(bone.name))
        else:
            rig_style.style(bone, side(bone.name), width=bone.custom_shape_wire_width)
    obj.show_in_front = True            # controls inside the body stay clickable
    data[MARK] = True
    return True


def add_face_board(obj):
    """A face board on the head when the character's face is a flipbook (or LEGO) material."""
    if "head" not in obj.data.bones:
        return False
    span = [obj.data.bones[n].head_local for n in ("upperarm_l", "upperarm_r") if n in obj.data.bones]
    size = (span[0] - span[1]).length * 0.5 if len(span) == 2 else None
    return face_board.add(obj, head="head", size=size)
