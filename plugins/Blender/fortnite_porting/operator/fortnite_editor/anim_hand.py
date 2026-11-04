"""Item in hand: an item from the user's scene (a pickaxe, a weapon) held on the body's hand socket, in the preview
(a copy) and, applied, on the character (a Child Of constraint on the user's item). Guns sit on hand_attach_r where
the skeleton has it, the rest on weapon_r, else the hand (the game's own choice per weapon is native code)."""
import bpy

from . import preview_scene

CONSTRAINT = "MP In Hand"
STATE = {"item": None, "copy": None}       # the user's object's name, its copy in the preview
SOCKETS = ("weapon_r", "hand_r")


def candidates(character):
    """Objects of the user's scene that can be held: top-level meshes and armatures that aren't the character."""
    if bpy.context.scene.get(preview_scene.TAG):
        return []
    out = []
    for ob in bpy.context.scene.objects:
        if ob.type not in {'MESH', 'ARMATURE'} or ob.parent is not None or ob.get(preview_scene.TAG):
            continue
        if character is not None and (ob == character or ob in character.children_recursive):
            continue
        out.append(ob)
    return out


def socket(arm, item):
    gun = any(w in item.name.lower() for w in ("wid_", "rifle", "pistol", "shotgun", "smg", "sniper", "launcher"))
    names = (("hand_attach_r",) if gun else ()) + SOCKETS
    return next((n for n in names if n in arm.data.bones), None)


def _hold(ob, arm, bone):
    con = ob.constraints.get(CONSTRAINT) or ob.constraints.new('CHILD_OF')
    con.name = CONSTRAINT
    con.target, con.subtarget = arm, bone
    con.inverse_matrix.identity()       # the item's origin (its grip) on the socket
    ob.location, ob.rotation_euler = (0, 0, 0), (0, 0, 0)


def show(arm):
    """The picked item's copy in the preview, held by `arm` (none: the copy goes)."""
    old = bpy.data.objects.get(STATE["copy"]) if STATE["copy"] else None
    if old is not None and old.get(preview_scene.TAG) == 2:
        bpy.data.objects.remove(old)
        preview_scene.changed()
    STATE["copy"] = None
    item = bpy.data.objects.get(STATE["item"]) if STATE["item"] else None
    if item is None or arm is None:
        return
    bone = socket(arm, item)
    if bone is None:
        return
    copy = item.copy()
    copy.animation_data_clear()
    for con in list(copy.constraints):
        copy.constraints.remove(con)
    preview_scene.track([copy])
    preview_scene.add([copy])
    _hold(copy, arm, bone)
    STATE["copy"] = copy.name


def apply(target):
    """The picked item held by the character's socket. Returns the socket, None when nothing was held."""
    item = bpy.data.objects.get(STATE["item"]) if STATE["item"] else None
    if item is None:
        return None
    bone = socket(target, item)
    if bone is not None:
        _hold(item, target, bone)
    return bone
