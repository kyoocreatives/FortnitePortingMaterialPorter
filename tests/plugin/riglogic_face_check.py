"""Checks the RigLogic face: the reader on a synthetic file, the bones it builds.

    blender -b --factory-startup --python-exit-code 1 -P tests/plugin/riglogic_face_check.py -- <plugin parent>

<plugin parent> holds the plugin as package fpmp_baseline (see translator_test.py). Exit code 0 when every check passes."""
import math
import os
import sys
import tempfile

import bpy

sys.path.insert(0, sys.argv[sys.argv.index("--") + 1])
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fpmp_baseline  # noqa: E402,F401
from fpmp_baseline.processing.context import riglogic_read as rr  # noqa: E402
import riglogic_writer  # noqa: E402

FAILS, PASSES = [], [0]


def check(name, got, want, tol=1e-4):
    if isinstance(want, float):
        ok = abs(got - want) <= tol
    elif isinstance(want, (tuple, list)) and want and all(isinstance(w, float) for w in want):
        ok = len(got) == len(want) and all(abs(g - w) <= tol for g, w in zip(got, want))
    else:
        ok = got == want
    if ok:
        PASSES[0] += 1
    else:
        FAILS.append(name)
        print("[riglogic_face_check] FAIL %s: got %r, want %r" % (name, got, want))


SPEC = {
    "gui": ["CTRL_C_jaw.ty", "CTRL_L_eye_blink.ty"],
    "raw": ["CTRL_expressions.jawOpen", "CTRL_expressions.eyeBlinkL", "CTRL_expressions.mouthFunnel"],
    "joints": ["FACIAL_C_FacialRoot", "FACIAL_C_Jaw", "FACIAL_L_Eyelid", "FACIAL_X_Missing"],
    "parents": [0, 0, 0, 0],
    "psds": [([0, 1], 1.0)],                          # input 3 = jawOpen * eyeBlinkL
    "rows": [(1, 3, {0: -20.0}), (1, 1, {0: 0.5}), (1, 2, {3: 2.0}), (2, 3, {1: -40.0, 3: 5.0}), (2, 6, {1: 0.1}),
             (3, 0, {0: 1.0}), (1, 5, {0: 0.01})],
    "gui_to_raw": [(0, 0, 0.0, 1.0, 1.0, 0.0), (1, 1, 0.0, 1.0, 1.0, 0.0)],
}
TMP = tempfile.mkdtemp()
PATH = os.path.join(TMP, "head.rigdna")
riglogic_writer.write(PATH, SPEC)

# --- reader
rig = rr.read(PATH)
check("names", (rig["gui"], rig["raw"], rig["joints"]), (SPEC["gui"], SPEC["raw"], SPEC["joints"]))
check("one corrective: jawOpen * eyeBlinkL", [(p["output"], p["inputs"], p["weight"]) for p in rig["psds"]], [(3, [0, 1], 1.0)])
check("jaw rotation row", rig["channels"][(1, 3)], [(0, -20.0)])
check("eyelid scale row (attr 6 = sx)", [(i, round(c, 6)) for i, c in rig["channels"][(2, 6)]], [(1, 0.1)])
out = rr.evaluate(rig, {"CTRL_expressions.jawOpen": 1.0, "CTRL_expressions.eyeBlinkL": 0.5})
check("evaluate: jaw", out["FACIAL_C_Jaw"][:6], [0.0, 0.5, 2.0 * 0.5, -20.0, 0.0, 0.01])
check("evaluate: eyelid with the corrective", out["FACIAL_L_Eyelid"][3], -40.0 * 0.5 + 5.0 * 0.5)
check("evaluate: rest is zero", all(v == 0.0 for vals in rr.evaluate(rig, {}).values() for v in vals), True)
check("GUI to raw", rr.raw_from_gui(rig, {"CTRL_C_jaw.ty": 0.7})["CTRL_expressions.jawOpen"], 0.7)
# a cut file, or one with bytes left over (another layout), fails loudly
with open(PATH, "rb") as f:
    data = f.read()
for label, damaged in (("a truncated dump raises", data[:-3]), ("trailing bytes raise (another layout)", data + b"\x00\x00")):
    bad = os.path.join(TMP, "bad.rigdna")
    with open(bad, "wb") as f:
        f.write(damaged)
    try:
        rr.read(bad)
        check(label, False, True)
    except rr.RigLogicError:
        check(label, True, True)

# layouts the builder can't follow fail loudly too (they'd move the face wrong)
for label, change in (("rotation signs other than +1", {"signs": (1, 0xFFFFFFFF, 1)}),
                      ("correctives not numbered after the raw controls", {"psd_min": 1}),
                      ("a row for a joint the names don't list", {"rows": SPEC["rows"] + [(9, 0, {0: 1.0})]})):
    odd = os.path.join(TMP, "odd.rigdna")
    riglogic_writer.write(odd, dict(SPEC, **change))
    try:
        rr.read(odd)
        check("%s raise" % label, False, True)
    except rr.RigLogicError:
        check("%s raise" % label, True, True)

# --- builder
from mathutils import Euler, Matrix, Vector  # noqa: E402
from fpmp_baseline.processing.context import riglogic_face as rf  # noqa: E402
from fpmp_baseline.material_porter.effects import ue_rest  # noqa: E402


def face_armature(name):
    """head, the facial root, a jaw and an eyelid under it (rolled, so their frames aren't the armature's), and a facial
    bone the DNA doesn't drive."""
    obj = bpy.data.objects.new(name, bpy.data.armatures.new(name))
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')
    for bone, head, parent in (("head", (0, 0, 1.6), None), ("FACIAL_C_FacialRoot", (0, 0, 1.62), "head"),
                               ("FACIAL_C_Jaw", (0, -0.02, 1.58), "FACIAL_C_FacialRoot"),
                               ("FACIAL_L_Eyelid", (0.03, -0.08, 1.68), "FACIAL_C_FacialRoot"),
                               ("FACIAL_R_NotInDNA", (-0.03, -0.08, 1.68), "FACIAL_C_FacialRoot")):
        b = obj.data.edit_bones.new(bone)
        b.head, b.tail, b.roll = head, Vector(head) + Vector((0, 0.02, 0.01)), 0.3
        if parent:
            b.parent = obj.data.edit_bones[parent]
    bpy.ops.object.mode_set(mode='OBJECT')
    return obj


def hand_formulas(obj, raw_names):
    """Each raw control reads one property set by hand on the armature, so tests drive the raw values directly."""
    for n in raw_names:
        obj["test_" + n] = 0.0

    def var(driver, name):
        v = driver.variables.new()
        v.name, v.type = "h", 'SINGLE_PROP'
        v.targets[0].id, v.targets[0].data_path = obj, '["test_%s"]' % name
        return "h"
    return {n: n for n in raw_names}, var


def ue_local(obj, name):
    """The bone's posed frame, in the game's frame, relative to its parent's posed game frame."""
    b = obj.data.bones[name]
    pose = obj.pose.bones[name].matrix @ (b.matrix_local.inverted() @ ue_rest(b))
    parent = obj.pose.bones[b.parent.name].matrix @ (b.parent.matrix_local.inverted() @ ue_rest(b.parent))
    return parent.inverted() @ pose


def set_raw(obj, values):
    for n, v in values.items():
        obj["test_" + n] = v
    obj.update_tag()
    bpy.context.view_layer.update()


arm = face_armature("rl")
formulas, var = hand_formulas(arm, SPEC["raw"])
built = rf.build(arm, rig, formulas, var)
check("driven joints built (the DNA joint the armature lacks skipped)", built, 2)
check("mechanism bones sit at the game frame", max(abs(x) for r in (arm.data.bones["RL_FACIAL_C_Jaw"].matrix_local -
                                                                   ue_rest(arm.data.bones["FACIAL_C_Jaw"])) for x in r) < 1e-5, True)
check("mechanism and pose bones", all(n in arm.data.bones for n in ("RL_FACIAL_C_Jaw", "RL_pose_FACIAL_C_Jaw", "RL_Expressions")), True)
check("an undriven facial bone left alone", len(arm.pose.bones["FACIAL_R_NotInDNA"].constraints), 0)
bpy.context.view_layer.update()
rest_local = {n: ue_local(arm, n).copy() for n in ("FACIAL_C_Jaw", "FACIAL_L_Eyelid")}
check("rest stays rest", all((m.to_translation() - (ue_rest(arm.data.bones[n].parent).inverted() @
                                                     ue_rest(arm.data.bones[n])).to_translation()).length < 1e-6
                             for n, m in rest_local.items()), True)
raw = {"CTRL_expressions.jawOpen": 1.0, "CTRL_expressions.eyeBlinkL": 0.5}
set_raw(arm, raw)
want = rr.evaluate(rig, raw)
flip = Matrix(((1, 0, 0), (0, -1, 0), (0, 0, 1)))
for name in ("FACIAL_C_Jaw", "FACIAL_L_Eyelid"):
    t, r, s = want[name][0:3], want[name][3:6], want[name][6:9]
    expect_t = rest_local[name].to_translation() + flip @ Vector(t) / 100.0
    expect_r = rest_local[name].to_3x3() @ Euler([math.radians(a) for a in (-r[0], r[1], -r[2])], 'XYZ').to_matrix()
    got = ue_local(arm, name)
    check("%s moves as RigLogic says (translation)" % name, tuple(got.to_translation()), tuple(expect_t), tol=1e-5)
    check("%s moves as RigLogic says (rotation)" % name,
          got.to_quaternion().rotation_difference(expect_r.to_quaternion()).angle < 1e-4, True)
    check("%s scale" % name, tuple(got.to_scale()), tuple(1.0 + v for v in s), tol=1e-5)
drivers = arm.animation_data.drivers
check("every driver simple, no expression over 255 characters",
      all(d.driver.is_simple_expression and len(d.driver.expression) <= 255 for d in drivers), True)
rf.set_on(arm, False)
check("off: the facial bones are free", [c.mute for c in arm.pose.bones["FACIAL_C_Jaw"].constraints if c.name == rf.COPY], [True])
rf.set_on(arm, True)
# Review Focus 2: a channel with more terms than one expression holds is chunked, its sum still exact
long_spec = {"gui": [], "raw": ["CTRL_expressions.k%d" % i for i in range(60)], "joints": ["FACIAL_C_FacialRoot", "FACIAL_C_Jaw"],
             "parents": [0, 0], "psds": [], "rows": [(1, 0, {i: 0.001 * (i + 1) for i in range(60)})], "gui_to_raw": []}
long_path = os.path.join(TMP, "long.rigdna")
riglogic_writer.write(long_path, long_spec)
long_rig = rr.read(long_path)
chunked = face_armature("rl_long")
formulas, var = hand_formulas(chunked, long_spec["raw"])
rf.build(chunked, long_rig, formulas, var)
bpy.context.view_layer.update()
rest_t = ue_local(chunked, "FACIAL_C_Jaw").to_translation()
set_raw(chunked, {n: 1.0 for n in long_spec["raw"]})
check("a 60-term channel sums exactly (cm)", (ue_local(chunked, "FACIAL_C_Jaw").to_translation() - rest_t).x * 100.0,
      sum(0.001 * (i + 1) for i in range(60)), tol=1e-4)
check("and needs several chunks", len([k for k in chunked.pose.bones["RL_FACIAL_C_Jaw"].keys() if k.startswith("c")]) > 1, True)
check("chunk expressions fit", all(len(d.driver.expression) <= 255 and d.driver.is_simple_expression
                                   for d in chunked.animation_data.drivers), True)

# --- the board in bones mode
from fpmp_baseline.processing.context import face_board, metahuman_board as mb  # noqa: E402
face_board.register()
KEYS = sorted(n.replace("CTRL_expressions_", "") for n in mb.load()["curves"])[:73]


def head_3l(name):
    """An armature with head, FACIAL_C_FacialRoot and FACIAL_C_Jaw, and a mesh with 73 of the board's shape keys."""
    obj = bpy.data.objects.new(name, bpy.data.armatures.new(name))
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')
    for bone, z, parent in (("root", 0.0, None), ("head", 1.6, "root"), ("FACIAL_C_FacialRoot", 1.62, "head"),
                            ("FACIAL_C_Jaw", 1.58, "FACIAL_C_FacialRoot")):
        b = obj.data.edit_bones.new(bone)
        b.head, b.tail = (0, 0, z), (0, 0, z + 0.1)
        if parent:
            b.parent = obj.data.edit_bones[parent]
    bpy.ops.object.mode_set(mode='OBJECT')
    me = bpy.data.meshes.new(name + "_head")
    me.from_pydata([(0, 0, 1.6), (0.1, 0, 1.6), (0, 0.1, 1.7)], [], [(0, 1, 2)])
    mesh = bpy.data.objects.new(name + "_head", me)
    bpy.context.scene.collection.objects.link(mesh)
    mesh.parent = obj
    mesh.shape_key_add(name="Basis")
    for k in KEYS:
        mesh.shape_key_add(name=k)
    return obj, mesh


board_spec = dict(SPEC, raw=["CTRL_expressions.jawOpen", "CTRL_expressions.eyeBlinkL", "CTRL_expressions.mouthFunnel"],
                  joints=["FACIAL_C_FacialRoot", "FACIAL_C_Jaw"], parents=[0, 0], rows=[(1, 3, {0: -20.0})])
dna = os.path.join(TMP, "board.rigdna")
riglogic_writer.write(dna, board_spec)
head, mesh = head_3l("bones")
mb.add(head, dna=dna)
check("bones mode: shape keys held at rest, not driven by the board",
      all(d.driver.expression == "0" and not d.driver.variables for d in mesh.data.shape_keys.animation_data.drivers), True)
check("bones mode flag", head.data.get("fpmp_metahuman_board_bones"), True)
check("bones mode: a knob for what the raw controls read", "MB_CTRL_C_jaw" in head.data.bones, True)
head.pose.bones["MB_CTRL_C_jaw"].location.z = mb.unit(head, "CTRL_C_jaw", "y")
head.update_tag()
bpy.context.view_layer.update()
check("the jaw knob drives jawOpen as the board formula says", round(head.pose.bones["RL_Expressions"]["CTRL_expressions.jawOpen"], 3),
      round(mb.evaluate(mb.load()["curves"]["CTRL_expressions_jawOpen"], {("CTRL_C_jaw", "y"): 1.0}), 3))
check("and the jaw bone turns", head.pose.bones["FACIAL_C_Jaw"].matrix.to_quaternion().rotation_difference(
    head.data.bones["FACIAL_C_Jaw"].matrix_local.to_quaternion()).angle > 0.1, True)
# Review Focus 4: an emote import frees the bones
from fpmp_baseline.material_porter import anim_hooks  # noqa: E402
anim_hooks.begin(head)
check("an animation import frees the facial bones", [c.mute for c in head.pose.bones["FACIAL_C_Jaw"].constraints], [True])
# Review Focus 5: a second add builds nothing
check("a second add builds nothing", mb.add(head, dna=dna), 0)
check("one mechanism bone per joint", sum(1 for b in head.data.bones if b.name.startswith("RL_FACIAL_C_Jaw")), 1)
# Review Focus 1: an unreadable file falls back to shape keys
bad = os.path.join(TMP, "unreadable.rigdna")
with open(bad, "wb") as f:
    f.write(b"not a dna")
fallback, fallback_mesh = head_3l("fallback")
check("an unreadable DNA falls back to shape keys", mb.add(fallback, dna=bad), 73)
check("fallback flag off", bool(fallback.data.get("fpmp_metahuman_board_bones")), False)
check("no DNA at all: shape keys, as before", mb.add(head_3l("plain")[0]), 73)

# Important 1: a face already animated on its shape keys (a lobby pose) plays with the board off; board on, the keys rest
lobby, lobby_mesh = head_3l("lobby_bones")
lobby_mesh.data.shape_keys.key_blocks["jawOpen"].value = 0.8
lobby_mesh.data.shape_keys.key_blocks["jawOpen"].keyframe_insert("value", frame=1)
mb.add(lobby, dna=dna)
bpy.context.scene.frame_set(1)
check("lobby: the board starts off and the keys play", (lobby.fpmp_face_board, round(lobby_mesh.data.shape_keys.key_blocks["jawOpen"].value, 3)),
      (False, 0.8))
lobby.fpmp_face_board = True
bpy.context.scene.frame_set(1)
check("board on: the shape keys rest (the bones carry the face)", round(lobby_mesh.data.shape_keys.key_blocks["jawOpen"].value, 3), 0.0)
lobby.fpmp_face_board = False
bpy.context.scene.frame_set(1)
check("board off again: the keys play", round(lobby_mesh.data.shape_keys.key_blocks["jawOpen"].value, 3), 0.8)

# the face costs every update while it has drivers (Blender re-reads each one): board off drops them, board on rebuilds
tiny_spec = dict(board_spec, rows=[(1, 3, {0: -20.0, 1: 0.001})])        # eyeBlinkL turns the jaw a thousandth of a degree
tiny = os.path.join(TMP, "tiny.rigdna")
riglogic_writer.write(tiny, tiny_spec)
lean, _ = head_3l("lean")
mb.add(lean, dna=tiny)
rl = lambda o: [d for d in (o.animation_data.drivers if o.animation_data else []) if d.data_path.startswith('pose.bones["RL_')]  # noqa: E731
reads = lambda o, name: sum(1 for d in rl(o) for v in d.driver.variables if name in v.targets[0].data_path)  # noqa: E731
check("an unseeable term isn't driven", reads(lean, "eyeBlinkL") - sum(1 for d in rl(lean) if d.data_path.endswith('["CTRL_expressions.eyeBlinkL"]')), 0)
lean.fpmp_face_board = False
check("board off: no face drivers left", len(rl(lean)), 0)
lean.fpmp_face_board = True
check("board on: the face drivers are back", len(rl(lean)) > 0, True)
lean.pose.bones["MB_CTRL_C_jaw"].location.z = mb.unit(lean, "CTRL_C_jaw", "y")
lean.update_tag()
bpy.context.view_layer.update()
check("and the jaw follows the knob again", lean.pose.bones["FACIAL_C_Jaw"].matrix.to_quaternion().rotation_difference(
    lean.data.bones["FACIAL_C_Jaw"].matrix_local.to_quaternion()).angle > 0.1, True)

# the import hook: the head part's meta names the DNA; the board may sit on another part's armature (the body's)
from types import SimpleNamespace  # noqa: E402
from fpmp_baseline.material_porter import mesh_hooks  # noqa: E402
body, _ = head_3l("hook_body")
head_part = bpy.data.objects.new("hook_head", None)
mesh_hooks._metahuman(SimpleNamespace(imported_meshes=[{"Skeleton": body, "Meta": None},
                                                       {"Skeleton": head_part, "Meta": {"FaceDNA": dna}}]))
check("the hook finds the head's DNA on another part", body.data.get("fpmp_metahuman_board_bones"), True)

print("[riglogic_face_check] %d passed, %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
