"""Replace Selected's scene work (operator/fortnite_editor/replace.py) on built scenes: a character swapped keeps the
old one's parent, transform, collection place, action (slot) and pose; a user-attached sword moves over; only the
old import's objects and the data nothing else uses go. A prop swapped in a shared collection; the selection rules.
Headless.

    python plugin_test.py tests/plugin/editor_replace_check.py --name replace
"""
import sys
from types import SimpleNamespace

import bpy
from mathutils import Euler, Matrix, Vector

sys.path.insert(0, sys.argv[sys.argv.index("--") + 1])
import fpmp_baseline  # noqa: E402,F401
from fpmp_baseline.operator.fortnite_editor import replace  # noqa: E402

FAILS, PASSES = [], [0]
SCENE = bpy.context.scene
LAYER = bpy.context.view_layer


def check(name, ok, detail=""):
    if ok:
        PASSES[0] += 1
    else:
        FAILS.append(name)
        print("[editor_replace_check] FAIL %s %s" % (name, detail))


def close(a, b, eps=1e-4):
    return all(abs(x - y) < eps for x, y in zip(a, b))


def mat_close(a, b, eps=1e-4):
    return all(close(ra, rb, eps) for ra, rb in zip(a, b))


def reset():
    for coll in (bpy.data.objects, bpy.data.meshes, bpy.data.armatures, bpy.data.materials, bpy.data.images,
                 bpy.data.node_groups, bpy.data.actions, bpy.data.collections):
        for idb in list(coll):
            coll.remove(idb)


def collection(name, parent=None):
    c = bpy.data.collections.new(name)
    (parent or SCENE.collection).children.link(c)
    return c


def material(name, image=None, group=None):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    if image is not None:
        tex = m.node_tree.nodes.new('ShaderNodeTexImage')
        tex.image = bpy.data.images.new(image, 4, 4)
    if group is not None:
        ng = bpy.data.node_groups.get(group) or bpy.data.node_groups.new(group, 'ShaderNodeTree')
        m.node_tree.nodes.new('ShaderNodeGroup').node_tree = ng
    return m


def mesh_object(name, col, mats=(), data=None):
    if data is None:
        data = bpy.data.meshes.new(name + "_Mesh")
        data.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
        for m in mats:
            data.materials.append(m)
    o = bpy.data.objects.new(name, data)
    col.objects.link(o)
    return o


BONES = {"root": (0, 0, 0), "pelvis": (0, 0, 1), "spine": (0, 0, 1.2), "hand_r": (-0.6, 0, 1.4)}


def armature(name, col, extra=(), drop=()):
    o = bpy.data.objects.new(name, bpy.data.armatures.new(name))
    col.objects.link(o)
    LAYER.objects.active = o
    bpy.ops.object.mode_set(mode='EDIT')
    for b, head in BONES.items():
        if b in drop:
            continue
        e = o.data.edit_bones.new(b)
        e.head, e.tail = head, Vector(head) + Vector((0, 0, 0.1))
    for b in extra:
        e = o.data.edit_bones.new(b)
        e.head, e.tail = (0, 0, 2), (0, 0, 2.1)
    bpy.ops.object.mode_set(mode='OBJECT')
    return o


def body(name, arm, col, mats):
    m = mesh_object(name, col, mats)
    m.parent = arm
    m.modifiers.new("Armature", 'ARMATURE').object = arm
    return m


def dance(name, owner):
    a = bpy.data.actions.new(name)
    slot = a.slots.new('OBJECT', owner.name)
    bag = a.layers.new("Layer").strips.new(type='KEYFRAME').channelbag(slot, ensure=True)
    for path, index, values in (('pose.bones["pelvis"].location', 2, (0.0, 0.5)), ('pose.bones["spine"].rotation_quaternion', 0, (1.0, 0.7))):
        fc = bag.fcurves.new(path, index=index)
        fc.keyframe_points.insert(1, values[0])
        fc.keyframe_points.insert(11, values[1])
    ad = owner.animation_data_create()
    ad.action = a
    ad.action_slot = slot
    return a, slot


def character_case():
    reset()
    SCENE.frame_set(1)
    chars = collection("Characters")
    old_col = collection("CID_Old", chars)
    props = collection("Props")
    stage = bpy.data.objects.new("Stage", None)
    SCENE.collection.objects.link(stage)
    stage.location, stage.rotation_euler = (5, 0, 0), (0, 0, 0.5)
    shared = material("M_Shared")
    keeper = mesh_object("Keeper", SCENE.collection, [shared])
    orphan = bpy.data.materials.new("M_Orphan")         # the user's, unused: no purge of what we didn't touch
    old = armature("Old_Arm", old_col)
    old_body = body("Old_Body", old, old_col, [material("M_Old", "T_Old", "NG_Old"), shared])
    shape = mesh_object("OR_Shape", SCENE.collection)
    SCENE.collection.objects.unlink(shape)      # bone shapes live outside collections (custom_shape users)
    only = mesh_object("OR_OldOnly", SCENE.collection)
    SCENE.collection.objects.unlink(only)
    old.pose.bones["root"].custom_shape = shape
    old.pose.bones["pelvis"].custom_shape = only
    old.parent = stage
    old.matrix_parent_inverse = Matrix.Translation((-1, 0, 0))
    old.location, old.rotation_euler, old.scale = (1, 2, 3), (0.1, 0.2, 0.3), (0.5, 0.5, 0.5)
    old.pose.bones["hand_r"].rotation_mode = 'XYZ'
    old.pose.bones["hand_r"].rotation_euler = (0.4, 0, 0)
    old.pose.bones["hand_r"]["ik_fk"] = 1.0
    action, slot = dance("Dance", old)
    sword = mesh_object("Sword", props)     # the user's, on the hand
    sword.parent, sword.parent_type, sword.parent_bone = old, 'BONE', "hand_r"
    hat = mesh_object("Hat", props)     # the user's, on the old body mesh
    hat.parent = old_body
    LAYER.update()
    old_world = old.matrix_world.copy()
    sword_world, hat_world = sword.matrix_world.copy(), hat.matrix_world.copy()

    # the editor's import: its own collection (in the scene's), an armature with a body, a bone shape in the scene
    new_col = collection("CID_New")
    new = armature("New_Arm", new_col, extra=("dyn_hair",))
    new_body = body("New_Body", new, new_col, [material("M_New", "T_New"), shared])
    ctrl = mesh_object("CTRL_Root", SCENE.collection)
    new.pose.bones["root"].custom_shape = shape
    new.pose.bones["pelvis"].custom_shape = ctrl
    new.pose.bones["hand_r"]["ik_fk"] = 0.0
    new.location = (0, 0, 0)
    new_objects = [new, new_body, ctrl]

    target, why = replace.find_target(SimpleNamespace(selected_objects=[old_body]))
    check("char: body selected -> its armature", target == old, why)
    main, notes = replace.replace(old, new_objects, LAYER)
    print("[editor_replace_check] notes:", "; ".join(notes))
    check("char: new root is the armature", main == new, main)
    LAYER.update()
    check("char: parent", new.parent == stage and new.parent_type == 'OBJECT')
    check("char: world matrix", mat_close(new.matrix_world, old_world), (new.matrix_world, old_world))
    check("char: local transform", close(new.location, (1, 2, 3)) and close(new.scale, (0.5, 0.5, 0.5)))
    ad = new.animation_data
    check("char: action", ad is not None and ad.action == action and ad.action_slot == slot)
    SCENE.frame_set(11)
    check("char: action plays", abs(new.pose.bones["pelvis"].location[2] - 0.5) < 1e-4, new.pose.bones["pelvis"].location[:])
    check("char: pose", close(new.pose.bones["hand_r"].rotation_euler, (0.4, 0, 0)) and new.pose.bones["hand_r"].rotation_mode == 'XYZ')
    check("char: bone switch", new.pose.bones["hand_r"]["ik_fk"] == 1.0)
    SCENE.frame_set(1)
    LAYER.update()
    gone = [n for n in ("Old_Arm", "Old_Body", "OR_OldOnly") if n in bpy.data.objects]
    check("char: old objects removed", not gone, gone)
    check("char: old armature data removed", "Old_Arm" not in bpy.data.armatures)
    gone = [n for n, c in (("M_Old", bpy.data.materials), ("T_Old", bpy.data.images), ("NG_Old", bpy.data.node_groups),
                           ("Old_Body_Mesh", bpy.data.meshes)) if n in c]
    check("char: old data removed", not gone, gone)
    kept = [n for n, c in (("M_Shared", bpy.data.materials), ("M_Orphan", bpy.data.materials), ("Keeper", bpy.data.objects),
                           ("Stage", bpy.data.objects), ("OR_Shape", bpy.data.objects), ("CTRL_Root", bpy.data.objects),
                           ("Dance", bpy.data.actions), ("M_New", bpy.data.materials)) if n not in c]
    check("char: shared / unrelated data kept", not kept, kept)
    check("char: sword moved to the new hand", sword.parent == new and sword.parent_type == 'BONE' and sword.parent_bone == "hand_r")
    check("char: sword where it was", mat_close(sword.matrix_world, sword_world), (sword.matrix_world, sword_world))
    check("char: hat kept, on the new root, where it was", hat.parent == new and mat_close(hat.matrix_world, hat_world),
          (hat.parent, hat.matrix_world, hat_world))
    check("char: sword's collection kept", [c.name for c in sword.users_collection] == ["Props"])
    check("char: old collection removed", "CID_Old" not in bpy.data.collections)
    check("char: new collection in its place", "CID_New" in chars.children and "CID_New" not in SCENE.collection.children,
          [c.name for c in SCENE.collection.children])
    check("char: new body still in its collection", [c.name for c in new_body.users_collection] == ["CID_New"])
    check("char: new root selected and active", LAYER.objects.active == new and new.select_get())
    check("char: user orphan untouched", orphan.name in bpy.data.materials)


def no_action_case():
    """No action: the pose goes over; an action whose bones aren't there stays off."""
    reset()
    col = collection("CID_A")
    old = armature("A_Arm", col)
    body("A_Body", old, col, [])
    old.pose.bones["spine"].rotation_quaternion = (0.9, 0.1, 0, 0)
    new_col = collection("CID_B")
    new = armature("B_Arm", new_col)
    new_body = body("B_Body", new, new_col, [])
    main, notes = replace.replace(old, [new, new_body], LAYER)
    check("pose: spine", close(new.pose.bones["spine"].rotation_quaternion, (0.9, 0.1, 0, 0)), notes)
    check("pose: noted", any(n.startswith("pose (") for n in notes), notes)

    reset()
    col = collection("CID_A")
    old = armature("A_Arm", col)
    dance("Dance", old)
    new_col = collection("CID_B")
    new = armature("B_Arm", new_col, drop=("pelvis", "spine"))      # a skeleton without the animated bones
    main, notes = replace.replace(old, [new], LAYER)
    check("mismatch: action kept off", new.animation_data is None or new.animation_data.action is None, notes)
    check("mismatch: said why", any("kept off" in n for n in notes), notes)
    check("mismatch: action not removed", "Dance" in bpy.data.actions)


def prop_case():
    """A prop in the user's own collection: the new prop joins that collection, its import collection goes."""
    reset()
    set_col = collection("Set")
    wall = mesh_object("Wall", set_col)
    data = bpy.data.meshes.new("CrateMesh")
    data.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    crate = mesh_object("Crate_Old", set_col, data=data)
    twin = mesh_object("Crate_Twin", set_col, data=data)        # a linked duplicate: the mesh stays
    crate.location, crate.rotation_euler = (3, 4, 0), (0, 0, 1.0)
    new_col = collection("Crate_New")
    new = mesh_object("Crate_New", new_col)
    target, why = replace.find_target(SimpleNamespace(selected_objects=[crate]))
    check("prop: target", target == crate, why)
    main, notes = replace.replace(crate, [new], LAYER)
    check("prop: root", main == new, notes)
    check("prop: transform", close(new.location, (3, 4, 0)) and close(new.rotation_euler, (0, 0, 1.0)))
    check("prop: in the user's collection", [c.name for c in new.users_collection] == ["Set"], [c.name for c in new.users_collection])
    check("prop: import collection removed", "Crate_New" not in bpy.data.collections)
    check("prop: old removed, shared mesh kept", "Crate_Old" not in bpy.data.objects and "CrateMesh" in bpy.data.meshes
          and twin.data == data)
    check("prop: neighbours kept", "Wall" in bpy.data.objects and wall.users_collection[0] == set_col)


def tag_case():
    """Tags: a tagged child of another import under the root is foreign even in the same collection."""
    reset()
    col = collection("Scene Stuff")
    old = armature("T_Arm", col)
    b = body("T_Body", old, col, [])
    gun = mesh_object("T_Gun", col)
    gun.parent = old
    replace.tag_objects([old, b], "1.1")
    replace.tag_objects([gun], "1.2")
    mine, foreign = replace.asset_objects(old)
    check("tag: members", set(mine) == {old, b}, mine)
    check("tag: other import is foreign", foreign == [gun], foreign)
    check("tag: gun's own root", replace.import_root(gun) == gun)


def selection_case():
    reset()
    col = collection("C")
    arm = armature("S_Arm", col)
    b = body("S_Body", arm, col, [])
    cam = bpy.data.objects.new("Cam", bpy.data.cameras.new("Cam"))
    col.objects.link(cam)
    other = mesh_object("Other", collection("D"))
    _, why = replace.find_target(SimpleNamespace(selected_objects=[]))
    check("select: nothing", why and why.startswith("Select an imported"), why)
    _, why = replace.find_target(SimpleNamespace(selected_objects=[b, other]))
    check("select: two", why and "2 are selected" in why, why)
    _, why = replace.find_target(SimpleNamespace(selected_objects=[cam]))
    check("select: camera", why and "not a character or prop" in why, why)
    root, why = replace.find_target(SimpleNamespace(selected_objects=[arm, b]))
    check("select: armature and body is one", root == arm, why)
    main, notes = replace.replace(arm, [cam.copy()], LAYER)       # an object outside any collection: nothing to place
    check("select: nothing placeable keeps the old", main is None and "S_Arm" in bpy.data.objects, notes)


for case in (character_case, no_action_case, prop_case, tag_case, selection_case):
    try:
        case()
    except Exception:
        import traceback
        traceback.print_exc()
        FAILS.append(case.__name__ + " raised")

print("[editor_replace_check] %d passed, %d failed%s" % (PASSES[0], len(FAILS), (": " + ", ".join(FAILS)) if FAILS else ""))
if FAILS:
    sys.exit(1)
