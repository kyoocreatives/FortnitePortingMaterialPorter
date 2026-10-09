"""Checks the MetaHuman face board (Epic's Face_ControlBoard_CtrlRig on FP's MetaHuman-style heads).

    blender -b --factory-startup --python-exit-code 1 -P tests/plugin/metahuman_board_check.py -- <plugin parent>

<plugin parent> holds the plugin as package fpmp_baseline (see translator_test.py). Exit code 0 when every check passes."""
import re
import sys

import bpy  # noqa: F401

sys.path.insert(0, sys.argv[sys.argv.index("--") + 1])
import fpmp_baseline  # noqa: E402,F401
from fpmp_baseline.processing.context import metahuman_board as mb  # noqa: E402

FAILS, PASSES = [], [0]


def check(name, got, want, tol=1e-4):
    ok = all(abs(g - w) <= tol for g, w in zip(got, want)) if isinstance(want, tuple) else \
        (abs(got - want) <= tol if isinstance(want, float) else got == want)
    if ok:
        PASSES[0] += 1
    else:
        FAILS.append(name)
        print("[metahuman_board_check] FAIL %s: got %r, want %r" % (name, got, want))


def ctl(name, axis="y"):
    return {"op": "control", "name": name, "axis": axis}


def var(name, axis):
    return "%s_%s" % (name, axis)


remap = {"op": "remap", "in": ctl("A"), "from": [0.0, 1.0], "to": [0.0, 2.0], "clamp": True}
check("remap", mb.evaluate(remap, {("A", "y"): 0.75}), 1.5)
check("remap clamps", mb.evaluate(remap, {("A", "y"): 3.0}), 2.0)
interp = {"op": "interp", "in": ctl("B", "x"), "scale": 1.0, "bias": 0.0, "clamp": [0.0, 1.0],
          "range_in": [0.0, -1.0], "range_out": [0.0, 1.0]}
check("interp maps a negative range, then clamps", (mb.evaluate(interp, {("B", "x"): -0.4}), mb.evaluate(interp, {("B", "x"): 0.4})), (0.4, 0.0))
loose = dict(interp, clamp=None)
check("interp's range map is unclamped (UE's scale-bias-clamp)", mb.evaluate(loose, {("B", "x"): -2.0}), 2.0)
both = {"op": "add", "a": remap, "b": interp}
check("add", mb.evaluate(both, {("A", "y"): 0.5, ("B", "x"): -0.25}), 1.25)
lerp = {"op": "lerp", "a": ctl("A"), "b": {"op": "const", "value": 1.0}, "t": ctl("T", "x")}
check("lerp", mb.evaluate(lerp, {("A", "y"): 0.2, ("T", "x"): 0.5}), 0.6)
curve = {"op": "curve", "in": ctl("C"), "keys": [[0.0, 0.0], [0.5, 1.0], [1.0, 0.0]]}
check("curve", (mb.evaluate(curve, {("C", "y"): 0.5}), mb.evaluate(curve, {("C", "y"): 0.25}), mb.evaluate(curve, {("C", "y"): 2.0})), (1.0, 0.5, 0.0))
for name, node, values in (("remap", remap, {("A", "y"): 0.6}), ("interp", interp, {("B", "x"): -0.3}),
                           ("loose interp", loose, {("B", "x"): -2.0}), ("add", both, {("A", "y"): 0.2, ("B", "x"): -0.9}),
                           ("lerp", lerp, {("A", "y"): 0.3, ("T", "x"): 0.25}), ("curve", curve, {("C", "y"): 0.7}),
                           ("curve below", curve, {("C", "y"): -0.5})):
    text = mb.expression(node, var)
    scope = {var(c, a): v for (c, a), v in values.items()}
    scope.update({var(c, a): 0.0 for c, a in (("A", "y"), ("B", "x"), ("C", "y"), ("T", "x")) if var(c, a) not in scope})
    scope.update(min=min, max=max)
    check("%s expression matches" % name, round(eval(text, scope), 6), round(mb.evaluate(node, values), 6))
    check("%s expression is plain" % name, all(w in ("min", "max") or re.fullmatch(r"[ABCT]_[xy]", w) for w in
                                              re.findall(r"[A-Za-z_]\w*", text)), True)
data = mb.load()
check("every decoded curve is 0 at rest", all(abs(mb.evaluate(n, {})) < 1e-6 for n in data["curves"].values()), True)
check("every decoded curve writes as an expression", all(mb.expression(n, var) for n in data["curves"].values()), True)

# --- the board on a head
from types import SimpleNamespace  # noqa: E402
from fpmp_baseline.processing.context import face_board  # noqa: E402
face_board.register()
KEYS = sorted(n.replace("CTRL_expressions_", "") for n in data["curves"])[:73]


def head_3l(name, keys=KEYS, facial=True):
    """An armature with head (and FACIAL_C_FacialRoot) bones and a mesh with the given shape keys."""
    arm = bpy.data.objects.new(name, bpy.data.armatures.new(name))
    bpy.context.scene.collection.objects.link(arm)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode='EDIT')
    for n, z in (("root", 0.0), ("head", 1.6)) + ((("FACIAL_C_FacialRoot", 1.62),) if facial else ()):
        b = arm.data.edit_bones.new(n)
        b.head, b.tail = (0, 0, z), (0, 0, z + 0.1)
    bpy.ops.object.mode_set(mode='OBJECT')
    me = bpy.data.meshes.new(name + "_head")
    me.from_pydata([(0, 0, 1.6), (0.1, 0, 1.6), (0, 0.1, 1.7)], [], [(0, 1, 2)])
    mesh = bpy.data.objects.new(name + "_head", me)
    bpy.context.scene.collection.objects.link(mesh)
    mesh.parent = arm
    mesh.shape_key_add(name="Basis")
    for k in keys + ["shoeMorphA"]:
        mesh.shape_key_add(name=k)
    return arm, mesh


def controls_of(node, out):
    if node.get("op") == "control":
        out.append((node["name"], node["axis"]))
    for k in ("a", "b", "t", "in"):
        if isinstance(node.get(k), dict):
            controls_of(node[k], out)
    return out


arm, mesh = head_3l("mh")
check("fits", mb.fits(arm), True)
made = mb.add(arm)
check("one driver per decoded key", made, len(KEYS))
drivers = mesh.data.shape_keys.animation_data.drivers
check("non-face keys left alone", drivers.find('key_blocks["shoeMorphA"].value') is None, True)
check("board bone", "MB_Board" in arm.data.bones, True)
outline = arm.pose.bones["MB_Board"].custom_shape
n_sliders = sum(c["kind"] == "slider" for c in data["controls"])
n_boxes = sum(c["kind"] == "box" for c in data["controls"])
check("board outline: frame, a track per slider, a box per box", outline is not None and len(outline.data.edges) == 4 + n_sliders + 4 * n_boxes, True)
check("knobs in Controls or the board group", any("MB_CTRL_L_brow_down" in c.bones for c in arm.data.collections), True)
check("board bone in the knobs' group", [c.name for c in arm.data.collections if "MB_Board" in c.bones] ==
      [c.name for c in arm.data.collections if "MB_CTRL_L_brow_down" in c.bones], True)
bpy.context.view_layer.update()
check("rest leaves every key at 0", max(abs(k.value) for k in mesh.data.shape_keys.key_blocks if k.name in KEYS), 0.0)
# a knob moves its key as the formula says (browDownL: one slider)
pick = "CTRL_expressions_browDownL" if "CTRL_expressions_browDownL" in data["curves"] and "browDownL" in KEYS else \
    next(n for n in data["curves"] if n.replace("CTRL_expressions_", "") in KEYS and controls_of(data["curves"][n], []))
control, axis = controls_of(data["curves"][pick], [])[0]
kb = arm.pose.bones["MB_" + control]
setattr(kb.location, "x" if axis == "x" else "z", 0.6 * mb.unit(arm, control, axis))
arm.update_tag()
bpy.context.view_layer.update()
key = mesh.data.shape_keys.key_blocks[pick.replace("CTRL_expressions_", "")]
check("a knob drives its key as the formula says", round(key.value, 3), round(mb.evaluate(data["curves"][pick], {(control, axis): 0.6}), 3))
lim = next(c for c in data["controls"] if c["name"] == control)["limits"][axis]
setattr(kb.location, "x" if axis == "x" else "z", (lim[1] + 5.0) * mb.unit(arm, control, axis))
bpy.context.view_layer.update()
arm.update_tag()
bpy.context.view_layer.update()
check("knobs stop at Epic's limits", round(key.value, 3), round(mb.evaluate(data["curves"][pick], {(control, axis): lim[1]}), 3))
setattr(kb.location, "x" if axis == "x" else "z", 0.0)
arm.update_tag()
bpy.context.view_layer.update()
check("a second add makes nothing", mb.add(arm), 0)
arm.fpmp_face_board = False
check("switch off mutes the drivers", all(d.mute for d in drivers), True)
arm.fpmp_face_board = True
check("switch on unmutes", any(d.mute for d in drivers), False)
mb.on_animation_import(arm)
check("an emote turns the board off", arm.fpmp_face_board, False)
legacy, _ = head_3l("legacy", keys=["jaw_open_pose", "L_blink_pose"], facial=False)
check("a legacy face gets no MetaHuman board", [mb.fits(legacy), mb.add(legacy)], [False, 0])
few, few_mesh = head_3l("few", keys=KEYS[:25])
check("a head with only some keys gets drivers for those", mb.add(few), 25)
toofew, _ = head_3l("toofew", keys=KEYS[:5])
check("too few expression keys: no board", mb.fits(toofew), False)
# the hook
from fpmp_baseline.material_porter import mesh_hooks  # noqa: E402
hooked, _ = head_3l("hooked")
mesh_hooks._metahuman(SimpleNamespace(imported_meshes=[{"Skeleton": hooked, "Mesh": None}]))
check("the hook adds the board", bool(hooked.data.get("fpmp_metahuman_board")), True)
broken, _ = head_3l("broken")
real = mb.add
mb.add = lambda obj, size=None: 1 / 0
try:
    mesh_hooks._metahuman(SimpleNamespace(imported_meshes=[{"Skeleton": broken, "Mesh": None}]))
    survived = True
except Exception:
    survived = False
mb.add = real
check("a board failure doesn't break the import", [survived, bpy.context.mode], [True, "OBJECT"])
from fpmp_baseline.material_porter import anim_hooks  # noqa: E402
again, _ = head_3l("again")
mb.add(again)
anim_hooks.begin(again)
check("the animation hook turns the board off", again.fpmp_face_board, False)

# --- review fixes
from mathutils import Vector  # noqa: E402
ctl_by = {c["name"]: c for c in data["controls"]}
board_arm, board_mesh = head_3l("orient")
mb.add(board_arm)


def board_dir(v):
    """A board-plane vector (right, up) in world space."""
    return Vector((v[0], 0.0, v[1])).normalized()


def knob_axis(name, col):
    return (board_arm.matrix_world.to_3x3() @ board_arm.data.bones["MB_" + name].matrix_local.to_3x3()).col[col].normalized()


# 1. knobs move the way Epic's do: brow down and blink pull down, the jaw's x and the right mouth corner as Epic orients them
for name in ("CTRL_L_brow_down", "CTRL_L_eye_blink", "CTRL_L_mouth_cornerPull"):
    check("%s slides along Epic's direction" % name, (knob_axis(name, 2) - board_dir(ctl_by[name]["axes"]["y"])).length < 1e-3, True)
check("brow down pulls down", knob_axis("CTRL_L_brow_down", 2).z < -0.5, True)
for name in ("CTRL_C_jaw", "CTRL_R_mouth_corner", "CTRL_L_eye"):
    check("%s box x as Epic's" % name, (knob_axis(name, 0) - board_dir(ctl_by[name]["axes"]["x"])).length < 1e-3, True)
    check("%s box y as Epic's" % name, (knob_axis(name, 2) - board_dir(ctl_by[name]["axes"]["y"])).length < 1e-3, True)
# a knob one Epic unit along its slide drives its key to 1 (the control's own scale counted)
kb = board_arm.pose.bones["MB_CTRL_L_brow_down"]
kb.location.z = mb.unit(board_arm, "CTRL_L_brow_down", "y")
board_arm.update_tag()
bpy.context.view_layer.update()
check("one Epic unit along the slide is 1.0", round(board_mesh.data.shape_keys.key_blocks["browDownL"].value, 3), 1.0)
kb.location.z = 0.0
board_arm.update_tag()
# 6. every driver runs without Python scripts
board_drivers = board_mesh.data.shape_keys.animation_data.drivers
check("every driver is a simple expression", all(fc.driver.is_simple_expression and fc.driver.is_valid for fc in board_drivers), True)
# 3. a face already animated (a lobby pose) keeps playing: the board starts off
lobby, lobby_mesh = head_3l("lobby")
lobby_mesh.data.shape_keys.key_blocks["browDownL"].value = 0.5
lobby_mesh.data.shape_keys.key_blocks["browDownL"].keyframe_insert("value", frame=1)
mb.add(lobby)
check("an animated face starts with the board off", [lobby.fpmp_face_board, all(fc.mute for fc in lobby_mesh.data.shape_keys.animation_data.drivers)], [False, True])
# 2. on a plain skeleton (no kit, no Tasty) Select Controls finds the knobs
from fpmp_baseline.processing.context import rig_style  # noqa: E402
check("plain skeleton: knobs are controls", "MB_CTRL_L_brow_down" in {p.name for p in rig_style.controls(arm)}, True)

print("[metahuman_board_check] %d passed, %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
