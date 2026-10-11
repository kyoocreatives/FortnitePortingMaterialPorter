"""Stand-in limbs: some characters skin a limb to their own copy of it (TerryBroke's terry_thigh_l, SushiAware's
upperarm_retarget_lf) that the game's own Control Rig builds from the real bone. The official rig drives the real
bones, so each copy is parented to its bone, keeping its offset, as one would by hand in edit mode."""
import bpy

SIDES = {"l": "l", "lf": "l", "left": "l", "r": "r", "rt": "r", "right": "r"}
HELPERS = {"twist", "null", "ik", "fk", "vb", "socket", "target", "end", "tip"}


def stand_ins(obj, driven, rig):
    """Copy -> the driven bone it stands for. A copy is named as its bone plus one word (no number, no helper word),
    isn't under that bone already (correctives are) and is one of a chain of copies sharing that word
    (terry_thigh_l > terry_calf_l). With skinned meshes, the skin must follow the chain. `rig`: the rig's own bones."""
    limbs = {}
    for d in driven:
        words = d.split("_")
        if len(words) == 2 and words[1] in ("l", "r"):
            limbs[(words[0].lower(), words[1])] = d
    found = {}
    for bone in obj.data.bones:
        words = bone.name.split("_")
        sides = [w for w in words if w.lower() in SIDES]
        if bone.name in driven or bone.name in rig or len(words) != 3 or len(sides) != 1:
            continue
        side = SIDES[sides[0].lower()]
        rest = [w for w in words if w is not sides[0]]
        limb = [w for w in rest if (w.lower(), side) in limbs]
        extra = [w for w in rest if w not in limb]
        if len(limb) != 1 or len(extra) != 1 or any(c.isdigit() for c in extra[0]) or extra[0].lower() in HELPERS:
            continue
        target = obj.data.bones[limbs[(limb[0].lower(), side)]]
        if any(p.name == target.name for p in bone.parent_recursive) or any(p.name == bone.name for p in target.parent_recursive):
            continue        # under its bone already (a corrective), or holding it
        found[bone.name] = (target.name, extra[0].lower(), side)
    chained = {n for n, (_, word, side) in found.items()
               if any(o != n and found[o][1:] == (word, side) and (obj.data.bones[o].parent == obj.data.bones[n]
                                                                  or obj.data.bones[n].parent == obj.data.bones[o])
                      for o in found)}
    skinned = {g.name for o in bpy.data.objects if o.type == 'MESH'
               and any(m.type == 'ARMATURE' and m.object == obj for m in o.modifiers) for g in o.vertex_groups}
    if skinned and not any(b in skinned for n in chained for b in [n] + [c.name for c in obj.data.bones[n].children_recursive]):
        return {}
    return {n: found[n][0] for n in chained}


def follow(obj, driven, rig):
    """Parents every stand-in limb bone to the bone it stands for; returns {copy: bone}."""
    found = stand_ins(obj, driven, rig)
    if not found:
        return found
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')
    edit = obj.data.edit_bones
    for copy, bone in found.items():
        edit[copy].use_connect = False
        edit[copy].parent = edit[bone]
    bpy.ops.object.mode_set(mode='OBJECT')
    return found
