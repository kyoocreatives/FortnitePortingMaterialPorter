"""Checks the flipbook face rules (the Flipbook2 Control Rig, spec Appendix A) and emote face keys (no game data needed).

    blender -b --factory-startup --python-exit-code 1 -P tests/plugin/flipbook_face_check.py -- <plugin parent>

<plugin parent> holds the plugin as package fpmp_baseline (see translator_test.py). Exit code 0 when every check passes."""
import sys

import bpy  # noqa: F401

sys.path.insert(0, sys.argv[sys.argv.index("--") + 1])
import fpmp_baseline  # noqa: E402,F401
from fpmp_baseline.material_porter import flipbook_face  # noqa: E402

FAILS = []
PASSES = [0]


def check(name, got, want):
    if got == want:
        PASSES[0] += 1
    else:
        FAILS.append(name)
        print("[flipbook_face_check] FAIL %s: got %r, want %r" % (name, got, want))


def pick(values, output):
    return flipbook_face.indices(values)[output]


LB, RB = "flipbook_face_l_brow_index", "flipbook_face_r_brow_index"
LE, RE = "flipbook_face_l_eye_index", "flipbook_face_r_eye_index"
MO = "flipbook_face_mouth_index"

check("rest", flipbook_face.indices({}), {LB: 0, RB: 0, LE: 0, RE: 0, MO: 0})

# brows
check("glabella down sets both brows 8", (pick({"c_glabella_down_pose": 0.3}, LB), pick({"c_glabella_down_pose": 0.3}, RB)), (8, 8))
check("glabella up needs 0.9", (pick({"c_glabella_up_pose": 0.9}, LB), pick({"c_glabella_up_pose": 0.89}, LB)), (7, 0))
check("glabella up wins over down", pick({"c_glabella_down_pose": 0.3, "c_glabella_up_pose": 0.95}, LB), 7)
check("left brow down 6, right untouched", (pick({"l_brow_down_pose": 0.3}, LB), pick({"l_brow_down_pose": 0.3}, RB)), (6, 0))
check("below the threshold stays at rest", pick({"l_brow_down_pose": 0.24}, LB), 0)
check("right brow up 5", pick({"r_brow_up_pose": 0.3}, RB), 5)
check("brow up wins over down", pick({"l_brow_down_pose": 0.3, "l_brow_up_pose": 0.3}, LB), 5)
check("glabella down + strong brow down 4", (pick({"c_glabella_down_pose": 0.6, "l_brow_down_pose": 0.8}, LB),
                                             pick({"c_glabella_down_pose": 0.6, "l_brow_down_pose": 0.8}, RB)), (4, 8))
check("glabella down + strong brow up 2", pick({"c_glabella_down_pose": 0.6, "l_brow_up_pose": 0.8}, LB), 2)
check("glabella up + strong brow down 3", (pick({"c_glabella_up_pose": 0.95, "l_brow_down_pose": 0.8}, LB),
                                           pick({"c_glabella_up_pose": 0.95, "l_brow_down_pose": 0.8}, RB)), (3, 7))
check("glabella up + strong brow up 1", pick({"c_glabella_up_pose": 0.95, "r_brow_up_pose": 0.8}, RB), 1)

# eyes
check("blink at 0.5", (pick({"l_blink_pose": 0.5}, LE), pick({"l_blink_pose": 0.49}, LE)), (3, 0))
check("blink + squint 2", pick({"l_blink_pose": 0.6, "l_squint_inner_pose": 0.7}, LE), 2)
check("blink + squint + squeeze 1", pick({"l_blink_pose": 0.6, "l_squint_inner_pose": 0.7, "l_squeeze_pose": 0.7}, LE), 1)
check("squeeze needs the squint", pick({"l_blink_pose": 0.6, "l_squeeze_pose": 0.9}, LE), 3)
check("wide 7", pick({"l_wide_pose": 0.75}, LE), 7)
check("brow down (6) makes eye 6, over wide", (pick({"l_brow_down_pose": 0.3}, LE), pick({"l_brow_down_pose": 0.3, "l_wide_pose": 0.8}, LE)), (6, 6))
check("brow up (5) leaves the eye", pick({"l_brow_up_pose": 0.3}, LE), 0)
check("brow 4 makes eye 5", pick({"c_glabella_down_pose": 0.6, "l_brow_down_pose": 0.8}, LE), 5)
check("brow 3 makes eye 4", pick({"c_glabella_up_pose": 0.95, "l_brow_down_pose": 0.8}, LE), 4)
check("a blink ignores the brow", pick({"l_blink_pose": 0.6, "l_brow_down_pose": 0.3}, LE), 3)
check("right eye blinks on its own", (pick({"r_blink_pose": 0.5}, RE), pick({"r_blink_pose": 0.5}, LE)), (3, 0))

# mouth, jaw open
check("jaw open 7", pick({"jaw_open_pose": 0.3}, MO), 7)
check("right snear needs 0.5", (pick({"jaw_open_pose": 0.3, "r_upper_lip_raiser_pose": 0.5}, MO),
                                pick({"jaw_open_pose": 0.3, "r_upper_lip_raiser_pose": 0.3}, MO)), (6, 7))
check("left snear 5", pick({"jaw_open_pose": 0.3, "l_upper_lip_raiser_pose": 0.3}, MO), 5)
check("open frown 4", pick({"jaw_open_pose": 0.3, "l_frown_pose": 0.3}, MO), 4)
check("open right smile 3", pick({"jaw_open_pose": 0.3, "r_smile_pose": 0.3}, MO), 3)
check("open left smile 2, over right", pick({"jaw_open_pose": 0.3, "r_smile_pose": 0.3, "l_smile_pose": 0.3}, MO), 2)
# mouth, jaw closed
check("jaw right 13, left 14", (pick({"jaw_right_pose": 0.3}, MO), pick({"jaw_left_pose": 0.3}, MO)), (13, 14))
check("jaw sides: the larger, ties to left", (pick({"jaw_right_pose": 0.5, "jaw_left_pose": 0.3}, MO),
                                              pick({"jaw_right_pose": 0.3, "jaw_left_pose": 0.3}, MO)), (13, 14))
check("closed snears 14 / 13", (pick({"r_lower_lip_down_pose": 0.5}, MO), pick({"l_lower_lip_down_pose": 0.3}, MO)), (14, 13))
check("phonemes oo / ch 1", (pick({"phoneme_oo_pose": 0.3}, MO), pick({"phoneme_ch_pose": 0.3}, MO)), (1, 1))
check("closed frown 12", pick({"l_frown_pose": 0.3}, MO), 12)
check("closed smile 11", pick({"r_smile_pose": 0.3}, MO), 11)
check("both upper lips raised 10", pick({"l_upper_lip_raiser_pose": 0.5, "r_upper_lip_raiser_pose": 0.5}, MO), 10)
check("both frowns with a snear 10", pick({"l_frown_pose": 0.3, "r_frown_pose": 0.3, "l_lower_lip_down_pose": 0.3}, MO), 10)
check("right smile + right snear 9", pick({"r_smile_pose": 0.3, "r_upper_lip_raiser_pose": 0.5}, MO), 9)
check("left smile + left snear 8", pick({"l_smile_pose": 0.3, "l_upper_lip_raiser_pose": 0.3}, MO), 8)
check("jaw below the threshold stays closed", pick({"jaw_open_pose": 0.2}, MO), 0)

# bypass: the incoming flipbook curves pass through, absent ones are left alone
by = flipbook_face.indices({"flipbook_face_bypass_mapping": 0.5, "flipbook_face_mouth_index": 3.0, "jaw_open_pose": 1.0})
check("bypass passes the mouth through", by[MO], 3)
check("bypass leaves absent outputs", by[LE], None)

# an emote's face, keyed on a flipbook material (fake .ueanim data: curves of keys)
from types import SimpleNamespace as NS  # noqa: E402
from fpmp_baseline.processing.context import face_board  # noqa: E402
face_board.register()


def key(frame, value):
    return NS(frame=frame, value=value)


def curve(name, *keys):
    return NS(name=name, keys=list(keys))


def fcurves(action):
    """Blender 5 layered actions keep their curves in the slot's channelbag."""
    return [fc for layer in action.layers for strip in layer.strips for bag in strip.channelbags for fc in bag.fcurves]


def fc_for(action, name):
    path = node.inputs[name].path_from_id("default_value")      # the face material's group node (set below)
    return next(fc for fc in fcurves(action) if fc.data_path == path)


def flip_armature(name, inputs):
    """An armature with a mesh whose material's group node has the given value inputs."""
    arm = bpy.data.objects.new(name, bpy.data.armatures.new(name))
    bpy.context.scene.collection.objects.link(arm)
    me = bpy.data.meshes.new(name + "_head")
    me.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    head = bpy.data.objects.new(name + "_head", me)
    bpy.context.scene.collection.objects.link(head)
    head.parent = arm
    mat = bpy.data.materials.new(name + "_face")
    mat.use_nodes = True
    group = bpy.data.node_groups.new(name + "_group", "ShaderNodeTree")
    for n in inputs:
        group.interface.new_socket(n, in_out="INPUT", socket_type="NodeSocketFloat")
    node = mat.node_tree.nodes.new("ShaderNodeGroup")
    node.node_tree = group
    for n, value in inputs.items():
        node.inputs[n].default_value = value
    me.materials.append(mat)
    return arm, mat, node


FB = {"flipbook_face_L_eye_index": 2.0, "flipbook_face_R_eye_index": 0.0, "flipbook_face_mouth_index": 0.0,
      "FB_EyeColumnCount": 3.0, "FB_EyeRowCount": 3.0, "FB_MouthColumnCount": 4.0, "FB_MouthRowCount": 4.0}
arm, mat, node = flip_armature("fb_emote", FB)
arm["fpmp_face_board_materials"] = [mat.name]
check("has flipbook faces", flipbook_face.has_faces(arm), True)
idle = {"dto": NS(curves=[curve("L_blink_pose", key(0, 0.0), key(2, 1.0), key(3, 0.0))]), "modes": {}}
section = {"name": "Emote", "frame": 1, "repeat": 1, "range": (0, 10), "modes": {},
           "dto": NS(curves=[curve("L_wide_pose", key(0, 0.0), key(5, 1.0), key(10, 1.0))])}
check("one face animated", flipbook_face.apply(arm, [section], idle), 1)
ad = mat.node_tree.animation_data
strips = ad.nla_tracks["MP Face"].strips
check("strip at the body's frame", [s.frame_start for s in strips], [1.0])
eye = fc_for(strips[0].action, "flipbook_face_L_eye_index")
check("eye keys constant", {k.interpolation for k in eye.keyframe_points}, {'CONSTANT'})
check("eye wide once the curve is on", eye.evaluate(6), 7.0)
check("the idle's blink over the emote", eye.evaluate(2), 3.0)
check("mouth keyed too", fc_for(strips[0].action, "flipbook_face_mouth_index").evaluate(4), 0.0)
check("keys only the flipbook inputs the face has", len(fcurves(strips[0].action)), 3)
check("board off while the emote plays", arm.fpmp_face_board, False)

# a common emote (no face curves): the idle drives the face
floss = dict(section, name="Floss", dto=NS(curves=[]))
flipbook_face.apply(arm, [floss], idle)
strips = ad.nla_tracks["MP Face"].strips
check("a second emote replaces the first", [s.name for s in strips], ["Floss"])
blink = fc_for(strips[0].action, "flipbook_face_L_eye_index")
check("idle blink shows", (blink.evaluate(2), blink.evaluate(0)), (3.0, 0.0))

# a looping face emote longer than the idle: it repeats with the body, the idle's blinks loop under it
long = dict(section, name="Long", range=(0, 30), repeat=999)
flipbook_face.apply(arm, [long], idle)
s = ad.nla_tracks["MP Face"].strips[0]
check("face repeats with the body", s.repeat, 999.0)
check("idle loops past its length", fc_for(s.action, "flipbook_face_L_eye_index").evaluate(11), 3.0)

# a looping emote shorter than the idle, without a face: the idle keeps its own loop (its blink still comes)
late = {"dto": NS(curves=[curve("L_blink_pose", key(0, 0.0), key(6, 1.0), key(7, 0.0))]), "modes": {}, "length": 8}
short = dict(floss, name="Short", range=(0, 4), repeat=999)
flipbook_face.apply(arm, [short], late)
s = ad.nla_tracks["MP Face"].strips[0]
check("idle loop spans the idle", tuple(s.action.frame_range), (0.0, 8.0))
check("its blink is in the loop", fc_for(s.action, "flipbook_face_L_eye_index").evaluate(6), 3.0)
check("repeats cover the emote's loops", s.repeat, 4 * 999 / 8)

# a MetaHuman (3L) emote: its curves reach the rules through the payload's 3L-to-legacy expressions
from fpmp_baseline.material_porter import curve_expressions  # noqa: E402
NAME, OP, FN, NUM = 1, 0, 2, 3
clamp_mix = [{"ElementType": NAME, "Value": "CTRL_a"}, {"ElementType": NAME, "Value": "CTRL_b"}, {"ElementType": NUM, "Value": 0.5},
             {"ElementType": OP, "Value": 3}, {"ElementType": OP, "Value": 1}, {"ElementType": NUM, "Value": 0.0},
             {"ElementType": NUM, "Value": 1.0}, {"ElementType": FN, "Value": 0}]
check("expression: clamp(a + b * 0.5, 0, 1)", curve_expressions.evaluate(clamp_mix, {"ctrl_a": 0.4, "ctrl_b": 0.4}.get), 0.6000000000000001)
check("expression: clamped", curve_expressions.evaluate(clamp_mix, {"ctrl_a": 0.9, "ctrl_b": 0.9}.get), 1.0)
check("expression: a missing curve reads 0", curve_expressions.evaluate(clamp_mix, {}.get), 0.0)
mapping = [{"Name": "L_blink_pose", "ExpressionStack": [{"ElementType": NAME, "Value": "CTRL_expressions_eyeBlinkL"}]},
           {"Name": "jaw_open_pose", "ExpressionStack": [{"ElementType": NAME, "Value": "CTRL_expressions_jawOpen"}]}]
mh = dict(section, name="MetaHuman", dto=NS(curves=[curve("is_3L", key(0, 1.0)),
                                                     curve("CTRL_expressions_eyeBlinkL", key(0, 0.0), key(4, 1.0), key(10, 1.0)),
                                                     curve("CTRL_expressions_jawOpen", key(0, 0.0), key(6, 1.0), key(10, 1.0))]))
flipbook_face.apply(arm, [mh], None, mapping)
s = ad.nla_tracks["MP Face"].strips[0]
check("3L blink closes the eye", (fc_for(s.action, "flipbook_face_L_eye_index").evaluate(1), fc_for(s.action, "flipbook_face_L_eye_index").evaluate(5)), (0.0, 3.0))
check("3L jaw opens the mouth", fc_for(s.action, "flipbook_face_mouth_index").evaluate(8), 7.0)

# bypass without flipbook curves: the input keeps its imported value
bypass = dict(section, name="Bypass", dto=NS(curves=[curve("flipbook_face_bypass_mapping", key(0, 1.0))]))
flipbook_face.apply(arm, [bypass], None)
s = ad.nla_tracks["MP Face"].strips[0]
check("bypassed input keeps its imported value", fc_for(s.action, "flipbook_face_L_eye_index").evaluate(3), 2.0)

# a plain outfit: nothing
plain, _, _ = flip_armature("plain", {"Roughness": 0.5})
check("no flipbook face, nothing", (flipbook_face.has_faces(plain), flipbook_face.apply(plain, [section], idle)), (False, 0))

# a LEGO face: the LEGO path keeps finding it, the flipbook path leaves it
from fpmp_baseline.material_porter import face_anim  # noqa: E402
lego, lego_mat, _ = flip_armature("lego", {"MouthPose": 0.0, "MouthU": 0.0})
check("LEGO face still found by the LEGO path", [m.name for m, _ in face_anim.face_materials(lego)], [lego_mat.name])
check("flipbook path leaves a LEGO face", flipbook_face.has_faces(lego), False)
check("LEGO path leaves a flipbook face", face_anim.face_materials(arm), [])

# the import hook never breaks an import
real_apply = flipbook_face.apply
flipbook_face.apply = lambda *a: 1 / 0
try:
    flipbook_face.import_emote(None, arm, [section], None, None)
    survived = True
except Exception:
    survived = False
flipbook_face.apply = real_apply
check("a face failure doesn't break the emote import", survived, True)

print("[flipbook_face_check] %d passed, %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
