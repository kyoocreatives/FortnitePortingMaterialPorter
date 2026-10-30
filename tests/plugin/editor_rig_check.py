"""The Rig tab's reading of real rigs (operator/fortnite_editor/rig_source.py): an Official rig built by the plugin on
a synthetic mannequin, and a MetaHuman face board built on a head with the face's shape keys. Headless.

    blender -b --factory-startup --python-exit-code 1 -P tests/plugin/editor_rig_check.py -- <plugin parent>
"""
import sys

import bpy
from mathutils import Vector

sys.path.insert(0, sys.argv[sys.argv.index("--") + 1])
import fpmp_baseline  # noqa: E402,F401
from fpmp_baseline.operator.fortnite_editor import rig_source  # noqa: E402
from fpmp_baseline.processing.context import metahuman_board as mb  # noqa: E402
from fpmp_baseline.processing.context import official_rig  # noqa: E402

FAILS, PASSES = [], [0]


def check(name, ok, detail=""):
    if ok:
        PASSES[0] += 1
    else:
        FAILS.append(name)
        print("[editor_rig_check] FAIL %s %s" % (name, detail))


# a mannequin facing -Y, as official_rig_check builds it
PLACES = {"root": (0, 0, 0), "pelvis": (0, 0, 1.0), "spine_01": (0, 0, 1.08), "spine_02": (0, 0, 1.16), "spine_03": (0, 0, 1.24),
          "spine_04": (0, 0, 1.32), "spine_05": (0, 0, 1.40), "neck_01": (0, 0, 1.50), "neck_02": (0, 0, 1.55), "head": (0, 0, 1.62),
          "thigh_l": (0.1, 0, 0.95), "calf_l": (0.1, -0.03, 0.52), "foot_l": (0.1, 0, 0.1), "ball_l": (0.1, -0.12, 0.02),
          "clavicle_l": (0.04, 0, 1.42), "upperarm_l": (0.18, 0, 1.42), "lowerarm_l": (0.46, 0.03, 1.42), "hand_l": (0.72, 0, 1.42)}


def place(name):
    if name in PLACES:
        return Vector(PLACES[name])
    if name.endswith("_r") and name[:-2] + "_l" in PLACES:
        x, y, z = PLACES[name[:-2] + "_l"]
        return Vector((-x, y, z))
    return None


def mannequin(name):
    data = official_rig.load()
    obj = bpy.data.objects.new(name, bpy.data.armatures.new(name))
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')
    edit = obj.data.edit_bones
    for b in data["skeleton"]:
        if b["name"] in edit or (b["parent"] and b["parent"] not in edit):
            continue
        bone = edit.new(b["name"])
        parent = edit.get(b["parent"]) if b["parent"] else None
        bone.head = place(b["name"]) or ((parent.head if parent else Vector((0, 0, 0))) + Vector((0, 0, 0.03)))
        bone.tail = bone.head + Vector((0, 0, 0.05))
        bone.parent = parent
    for b in edit:
        kids = [k for k in b.children if place(k.name) is not None]
        if kids and (kids[0].head - b.head).length > 1e-4:
            b.tail = kids[0].head
    bpy.ops.object.mode_set(mode='OBJECT')
    return obj


man = mannequin("man")
official_rig.create(man)
rig = rig_source.read(man)
names = {c.name: c for c in rig.controls}
check("official: kind", rig.kind == "official", rig.kind)
check("official: its controls", len(rig.controls) >= 80, len(rig.controls))
check("official: left hand on the screen's right", names["hand_l_ik_ctrl"].side == "L" and names["hand_l_ik_ctrl"].x > 0.5,
      names.get("hand_l_ik_ctrl"))
check("official: roles", names["global_ctrl"].role == "root" and names["index_01_l_ctrl"].role == "finger"
      and names["foot_l_ik_ctrl"].role == "limb", [names[n].role for n in ("global_ctrl", "index_01_l_ctrl", "foot_l_ik_ctrl")])
switches = {s.name: s for s in rig.switches}
check("official: limb switches", {"arm_l_fk_ik_switch", "arm_r_fk_ik_switch", "leg_l_fk_ik_switch", "leg_r_fk_ik_switch"} <= set(switches),
      sorted(switches))
check("official: legs start in IK", switches["leg_l_fk_ik_switch"].is_ik and not switches["arm_l_fk_ik_switch"].is_ik)
check("official: no board", rig.board is None)
check("official: a skeleton to draw the body with", len(rig.skeleton) >= 20 and all(len(seg) == 4 for seg in rig.skeleton),
      len(rig.skeleton))
hidden = rig_source.hidden(man)
check("official: the inactive chain is hidden", "hand_l_ik_ctrl" in hidden and "upperarm_l_fk_ctrl" not in hidden,
      sorted(n for n in hidden if "hand_l" in n or "upperarm_l" in n))
rig_source.set_switch(man, switches["arm_l_fk_ik_switch"], True)
check("official: switching arm_l to IK shows its IK hand", "hand_l_ik_ctrl" not in rig_source.hidden(man))
check("armature of a child mesh", rig_source.armature_of(man) is man)

# Key keys the IK/FK switches too, stepped: flipping a switch later leaves the frames keyed before it as they were
from fpmp_baseline.operator.fortnite_editor import rig_ops, rig_tab  # noqa: E402
bpy.utils.register_class(rig_ops.FP_OT_rig_key)
scene, SWITCH = bpy.context.scene, 'pose.bones["arm_l_fk_ik_switch"]["arm_l_fk_ik_switch"]'
bpy.context.view_layer.objects.active = man
man.pose.bones["hand_l_ik_ctrl"].select = True
scene.frame_set(1)
bpy.ops.fp.rig_key(what='SELECTED')
scene.frame_set(20)
rig_source.set_switch(man, switches["arm_l_fk_ik_switch"], False)
bpy.ops.fp.rig_key(what='SELECTED')
curve = next((c for c in rig_tab.curves(man) if c.data_path == SWITCH), None)
check("key: the switch keyed at both frames", curve is not None and len(curve.keyframe_points) == 2,
      curve and len(curve.keyframe_points))
check("key: switch keys step", curve is not None and all(p.interpolation == 'CONSTANT' for p in curve.keyframe_points))
check("key: every switch keyed", all(rig_tab.keyed(man, rig_source.switch_path(man, s))
                                     for s in rig_source.switches(man, "official")))
check("key: the picked control keyed", rig_tab.keyed(man, 'pose.bones["hand_l_ik_ctrl"].location'))
scene.frame_set(1)
at_1 = man.pose.bones["arm_l_fk_ik_switch"]["arm_l_fk_ik_switch"]
scene.frame_set(20)
at_20 = man.pose.bones["arm_l_fk_ik_switch"]["arm_l_fk_ik_switch"]
check("key: a later flip to FK leaves frame 1 in IK", at_1 == 1.0 and at_20 == 0.0, (at_1, at_20))
bpy.ops.fp.rig_key(what='FACE')
check("key: Key Face leaves the switches", len(curve.keyframe_points) == 2)
bpy.utils.unregister_class(rig_ops.FP_OT_rig_key)
man.animation_data_clear()
scene.frame_set(1)

# a MetaHuman board on a head whose mesh has every key the board decodes
data = mb.load()
keys = sorted(n.replace("CTRL_expressions_", "") for n in data["curves"])
head = bpy.data.objects.new("mh", bpy.data.armatures.new("mh"))
bpy.context.scene.collection.objects.link(head)
bpy.context.view_layer.objects.active = head
bpy.ops.object.mode_set(mode='EDIT')
for n, z in (("root", 0.0), ("head", 1.6), ("FACIAL_C_FacialRoot", 1.62)):
    b = head.data.edit_bones.new(n)
    b.head, b.tail = (0, 0, z), (0, 0, z + 0.1)
bpy.ops.object.mode_set(mode='OBJECT')
me = bpy.data.meshes.new("mh_head")
me.from_pydata([(0, 0, 1.6), (0.1, 0, 1.6), (0, 0.1, 1.7)], [], [(0, 1, 2)])
mesh = bpy.data.objects.new("mh_head", me)
bpy.context.scene.collection.objects.link(mesh)
mesh.parent = head
mesh.modifiers.new("Armature", 'ARMATURE').object = head
mesh.shape_key_add(name="Basis")
for k in keys:
    mesh.shape_key_add(name=k)
mb.add(head)
face = rig_source.read(head)
check("board: found", face.board is not None and face.board.kind == "metahuman", face.board and face.board.kind)
knobs = {k.bone: k for k in face.board.knobs}
check("board: knobs", len(knobs) > 100, len(knobs))
jaw = knobs.get("MB_CTRL_C_jaw")
check("board: the jaw is a box", jaw is not None and len(jaw.axes) == 2, jaw)
check("board: knob sizes from their shapes", jaw.size > 0 and all(k.size > 0 for k in knobs.values()), jaw.size)
brow = knobs.get("MB_CTRL_L_brow_down")
check("board: a brow slider moves up the board", brow is not None and len(brow.axes) == 1 and abs(brow.axes[0].dz) > abs(brow.axes[0].dx),
      brow)
check("board: ranges from the limit", all(a.hi > a.lo for k in knobs.values() for a in k.axes))
check("board: the left brow right of the right brow", brow.x > knobs["MB_CTRL_R_brow_down"].x)
check("board: the drawing as outline", face.board.outline and len(face.board.outline) % 3 == 0 and len(face.board.outline) > 1000,
      len(face.board.outline or []))
check("board: armature of the head mesh", rig_source.armature_of(mesh) is head)
values = rig_source.knob_values(head, "MB_CTRL_C_jaw")
check("board: knob values read", values == {0: 0.0, 2: 0.0}, values)
rig_source.set_knob(head, "MB_CTRL_C_jaw", {2: -0.5})
check("board: knob values written", abs(head.pose.bones["MB_CTRL_C_jaw"].location[2] + 0.5) < 1e-6)

# the tab's own state: handlers survive opening a file, a new file forgets old rigs, renamed bones read again,
# armatures the tab can't make active are left out
check("tab: redraw handlers survive File > Open", all(hasattr(f, "_bpy_persistent")
                                                      for f in (rig_tab._depsgraph, rig_tab._frame, rig_tab._loaded)))
first = rig_tab.rig_of(man)["rig"]
bpy.context.view_layer.objects.active = man
bpy.ops.object.mode_set(mode='EDIT')
man.data.edit_bones["hips_ctrl"].name = "hips_ctrl_renamed"
bpy.ops.object.mode_set(mode='OBJECT')
again = rig_tab.rig_of(man)["rig"]
check("tab: a renamed control is read again", any(c.name == "hips_ctrl_renamed" for c in again.controls)
      and not any(c.name == "hips_ctrl" for c in again.controls))
rig_tab.RIG.update(arm="man", views={1: {"arm": "man"}}, hover={1: None}, box={})
rig_tab._loaded(None)
check("tab: opening a file forgets the shown rigs", rig_tab.RIG["cache"] == {} and rig_tab.RIG["views"] == {} and rig_tab.RIG["arm"] is None)
# the body's silhouette: its meshes' triangles at rest, seen from the front in the controls' plane
me = bpy.data.meshes.new("torso")
me.from_pydata([(-0.2, -0.1, 1.0), (0.3, -0.1, 1.0), (0.3, -0.1, 1.5), (-0.2, -0.1, 1.5)], [], [(0, 1, 2, 3)])
torso = bpy.data.objects.new("torso", me)
bpy.context.scene.collection.objects.link(torso)
torso.parent = man
torso.modifiers.new("Armature", 'ARMATURE').object = man
loose = bpy.data.objects.new("loose", bpy.data.meshes.new("loose"))
bpy.context.scene.collection.objects.link(loose)
meshes = rig_source.body_meshes(man)
sil = rig_source.silhouette(man, meshes)
check("silhouette: the character's meshes only", meshes == [torso], meshes)
check("silhouette: two triangles in the front plane", sil is not None and sil.shape == (6, 2)
      and abs(sil[:, 0].min() + 0.2) < 1e-5 and abs(sil[:, 0].max() - 0.3) < 1e-5 and abs(sil[:, 1].max() - 1.5) < 1e-5,
      sil)
check("silhouette: none for a bare armature", rig_source.silhouette(man, []) is None)
hidden_coll = bpy.data.collections.new("excluded")
bpy.context.scene.collection.children.link(hidden_coll)
away = bpy.data.objects.new("away", bpy.data.armatures.new("away"))
hidden_coll.objects.link(away)
bpy.context.view_layer.layer_collection.children["excluded"].exclude = True
bpy.context.view_layer.update()
check("tab: an armature outside the view layer isn't offered", not rig_tab.usable(away, bpy.context.view_layer)
      and rig_tab.usable(man, bpy.context.view_layer))

print("[editor_rig_check] %d passed, %d failed" % (PASSES[0], len(FAILS)))
if FAILS:
    sys.exit(1)
