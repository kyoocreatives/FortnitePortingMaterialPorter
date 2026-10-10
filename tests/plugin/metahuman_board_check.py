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


def head_3l(name, keys=KEYS, facial=True, legacy=False):
    """An armature with head (and FACIAL_C_FacialRoot) bones and a mesh with the given shape keys."""
    arm = bpy.data.objects.new(name, bpy.data.armatures.new(name))
    bpy.context.scene.collection.objects.link(arm)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode='EDIT')
    for n, z in (("root", 0.0), ("head", 1.6)) + ((("FACIAL_C_FacialRoot", 1.62),) if facial else ()) +             ((("faceAttach", 1.63),) if legacy else ()):
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
drawing = arm.pose.bones["MB_Board"].custom_shape
on_board = {c["name"] for c in data["controls"] if c["kind"] in ("slider", "box") and c["group"] not in (None, "faceAndEyesAimFollowHead")}
read = {c for k in KEYS for c, _ in controls_of(data["curves"]["CTRL_expressions_" + k], [])}
knobs = {b.name[3:] for b in arm.data.bones if b.name.startswith("MB_CTRL")}
check("only board knobs the head's keys read (FP ships a third of Epic's curves)", knobs, read & on_board)
shape = mb.load_drawing()
check("the board is Epic's own drawing (faceboard_2x)", drawing is not None and len(drawing.data.vertices) == len(shape["verts"]) // 2
      and len(drawing.data.polygons) == len(shape["faces"]), True)
xs = [v.co.x for v in drawing.data.vertices]
check("board at least 4 head lengths wide", max(xs) - min(xs) >= 4 * arm.data.bones["head"].length, True)
# every knob sits on Epic's drawing (its track or box), in the board bone's frame
board_bone = arm.data.bones["MB_Board"]
step = mb.step(arm)
pts = [(shape["verts"][i], shape["verts"][i + 1]) for i in range(0, len(shape["verts"]), 2)]
by_name = {c["name"]: c for c in data["controls"]}
off = []
for name in knobs:
    at = board_bone.matrix_local.inverted() @ arm.data.bones["MB_" + name].head_local
    x, up = at.x / step, at.z / step
    if min((x - px) ** 2 + (up - py) ** 2 for px, py in pts) > 0.7 ** 2:
        off.append(name)
check("every knob sits on Epic's drawing %s" % off, off, [])
knob = arm.pose.bones["MB_CTRL_L_brow_down"]
check("knobs are Epic's spheres", knob.custom_shape is not None and len(knob.custom_shape.data.polygons) > 0, True)
check("knobs in Epic's colour", tuple(knob.color.custom.normal), (1.0, 1.0, 0.0))
check("knob at Epic's size", round(knob.custom_shape_scale_xyz[0] * (knob.custom_shape.dimensions.x / 2), 6),
      round(by_name["CTRL_L_brow_down"]["radius"] * step, 6))
# Epic's groups: a sub-collection per face area under the board's group
groups = [c for c in arm.data.collections_all if "MB_CTRL_L_brow_down" in c.bones]
check("knob in its Epic group", [c.name for c in groups], ["Face Brow"])
check("Epic groups sit under the board's group", groups[0].parent is not None and "MB_Board" in groups[0].parent.bones, True)
mouth = sorted(n for n in knobs if by_name[n]["group"] == "mouth")[0]
check("every area grouped as Epic's (%s)" % mouth, [c.name for c in arm.data.collections_all if "MB_" + mouth in c.bones], ["Face Mouth"])
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
plain, _ = head_3l("plain", keys=["jaw_open_pose", "L_blink_pose"], facial=False)
check("a face without FACIAL bones or faceAttach gets no board", [mb.fits(plain), mb.add(plain)], [False, 0])
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
board_arm, board_mesh = head_3l("orient", keys=sorted(n.replace("CTRL_expressions_", "") for n in data["curves"]))     # every knob
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
# 4. the drawing can't be picked (drag-selecting knobs never grabs it); a master handle moves, turns and scales the board
bpy.context.view_layer.update()
check("the drawing is unselectable", board_arm.data.bones["MB_Board"].hide_select, True)
check("a master handle carries the board", board_arm.data.bones["MB_Board"].parent.name, "MB_Master")
master = board_arm.pose.bones["MB_Master"]
check("the master is selectable and free", (master.bone.hide_select, any(master.lock_location), any(master.lock_rotation),
                                            any(master.lock_scale), master.custom_shape is not None), (False, False, False, False, True))
check("the master sits outside the drawing (top left)", master.bone.head_local.z > max(
    (board_arm.data.bones["MB_Board"].matrix_local @ v.co).z for v in board_arm.pose.bones["MB_Board"].custom_shape.data.vertices), True)
knob_before = (board_arm.matrix_world @ board_arm.pose.bones["MB_CTRL_L_brow_down"].matrix).to_translation()
master.location, master.scale = (0.3, 0.1, -0.2), (2.0, 2.0, 2.0)
master.rotation_mode = 'XYZ'
master.rotation_euler = (0.0, 0.0, 0.7)
board_arm.update_tag()
bpy.context.view_layer.update()
check("the knobs follow the master", ((board_arm.matrix_world @ board_arm.pose.bones["MB_CTRL_L_brow_down"].matrix).to_translation()
                                      - knob_before).length > 0.1, True)
check("moving the board leaves the face at rest", max(abs(k.value) for k in board_mesh.data.shape_keys.key_blocks
                                                     if "CTRL_expressions_" + k.name in data["curves"]), 0.0)
master.location, master.scale, master.rotation_euler = (0.0, 0.0, 0.0), (1.0, 1.0, 1.0), (0.0, 0.0, 0.0)
board_arm.update_tag()
# 2. on a plain skeleton (no kit, no Tasty) Select Controls finds the knobs
from fpmp_baseline.processing.context import rig_style  # noqa: E402
check("plain skeleton: knobs are controls", "MB_CTRL_L_brow_down" in {p.name for p in rig_style.controls(arm)}, True)

# --- legacy (pre-3L) heads: the board's 3L curves reach their *_pose keys through Fortnite's 3L-to-legacy mapping
from fpmp_baseline.material_porter import curve_expressions  # noqa: E402
LEGACY = mb.load_legacy()["curves"]
old_head, old_mesh = head_3l("old", keys=sorted(LEGACY) + ["phoneme_oo_pose"], facial=False, legacy=True)
check("a legacy head fits", mb.fits(old_head), True)
check("one driver per mapped legacy key", mb.add(old_head), len(LEGACY))
old_keys = old_mesh.data.shape_keys.key_blocks
old_drivers = old_mesh.data.shape_keys.animation_data.drivers
check("an unmapped legacy key isn't driven", any(d.data_path == 'key_blocks["phoneme_oo_pose"].value' for d in old_drivers), False)
bpy.context.view_layer.update()
check("legacy rest leaves every key at 0", max(abs(k.value) for k in old_keys if k.name in LEGACY), 0.0)
target = "L_brow_down_pose"
source = next(e["Value"] for e in LEGACY[target] if e["ElementType"] == 1 and e["Value"] in data["curves"])
control, axis = controls_of(data["curves"][source], [])[0]
setattr(old_head.pose.bones["MB_" + control].location, "x" if axis == "x" else "z", 0.6 * mb.unit(old_head, control, axis))
old_head.update_tag()
bpy.context.view_layer.update()


def through(name):
    node = data["curves"].get(name)
    return mb.evaluate(node, {(control, axis): 0.6}) if node else 0.0


want = curve_expressions.evaluate(LEGACY[target], lambda n: through(next(k for k in {e['Value'] for st in LEGACY.values() for e in st if e['ElementType'] == 1} if k.lower() == n)))
check("a knob drives a legacy key through the mapping", round(old_keys[target].value, 3), round(want, 3))
check("legacy drivers simple and short", all(d.driver.is_simple_expression and len(d.driver.expression) <= 255
                                             for d in list(old_drivers) + list(old_head.animation_data.drivers)), True)
old_head.fpmp_face_board = False
check("switch off mutes the legacy drivers", all(d.mute for d in old_drivers), True)
old_head.fpmp_face_board = True

# a legacy head whose material also reads the legacy curves: the board drives those inputs too
peel, _ = head_3l("peel", keys=sorted(LEGACY), facial=False, legacy=True)
group = bpy.data.node_groups.new("peel face", "ShaderNodeTree")
POSES = ("L_brow_down_pose", "R_smile_pose", "R_Frown_pose", "R_frown_pose")
for name in POSES:
    group.interface.new_socket(name, in_out='INPUT', socket_type='NodeSocketFloat')
peel_mat = bpy.data.materials.new("MP peel body")
peel_node = peel_mat.node_tree.nodes.new("ShaderNodeGroup")
peel_node.node_tree = group
bpy.data.objects["peel_head"].data.materials.append(peel_mat)
check("a legacy head with a curve-reading material fits", mb.fits(peel), True)
check("one driver per mapped key and material input", mb.add(peel), len(LEGACY) + len(POSES))
# the material builder may add inputs before them later: each driver still finds its own
first = group.interface.new_socket("Added later", in_out='INPUT', socket_type='NodeSocketFloat')
group.interface.move(first, 0)
setattr(peel.pose.bones["MB_" + control].location, "x" if axis == "x" else "z", 0.6 * mb.unit(peel, control, axis))
peel.update_tag()
bpy.context.view_layer.update()
check("a knob drives the material's legacy input", round(peel_node.inputs["L_brow_down_pose"].default_value, 3), round(want, 3))
check("an input added before them isn't driven", peel_node.inputs["Added later"].default_value, 0.0)
peel.fpmp_face_board = False
check("switch off mutes the material drivers", all(d.mute for d in peel_mat.node_tree.animation_data.drivers), True)
peel.fpmp_face_board = True
twin_head, _ = head_3l("peel2", keys=sorted(LEGACY), facial=False, legacy=True)
bpy.data.objects["peel2_head"].data.materials.append(peel_mat)        # the same outfit imported again
mb.add(twin_head)
check("a shared material keeps one driver per input", len(peel_mat.node_tree.animation_data.drivers), len(POSES))
# a toon face that only reads the curves in its material (Peely's) gets the face board's states, not this board
toon, _ = head_3l("toon", keys=[], facial=False, legacy=True)
bpy.data.objects["toon_head"].data.materials.append(peel_mat)
check("a material-only toon face isn't a MetaHuman legacy head", mb.fits(toon), False)
# the board clears the head: past the head mesh's widest point above the neck, whatever the head bone's length
wide, wide_mesh = head_3l("wide")
wide_mesh.data.vertices[1].co = (0.35, 0.0, 1.65)
mb.add(wide)
check("the board starts past the head mesh", wide.data.bones["MB_Master"].head_local.x > 0.35, True)

print("[metahuman_board_check] %d passed, %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
