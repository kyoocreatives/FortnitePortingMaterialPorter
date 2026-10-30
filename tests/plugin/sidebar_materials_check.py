"""Sidebar rework, Materials / Effect / Shell Fur: panel and operator polls per object kind, the Animated Materials
checkbox over material_time, the specials menu and the Object > Fortnite Porting submenu, and a fixer panel that
never asks the app from draw or poll (the islands probe runs on its own thread and retries).

    python plugin_test.py tests/plugin/sidebar_materials_check.py [--blend out/fpdev/rg_wolf/rg_wolf.blend]
With a blend: its armature must fit its own rig (rg_wolf: creature, car: vehicle) and no other."""
import os
import sys
import threading
import time

import bpy

sys.path.insert(0, sys.argv[sys.argv.index("--") + 1])
import fpmp_baseline  # noqa: E402

fpmp_baseline.register()
from fpmp_baseline.material_porter import custom_fur, effects, material_time  # noqa: E402
from fpmp_baseline.operator import convert_op  # noqa: E402
from fpmp_baseline.operator import fixer  # noqa: E402

FAILS, PASSES = [], [0]


def check(name, got, want=True):
    ok = got == want
    if ok:
        PASSES[0] += 1
    else:
        FAILS.append(name)
    print("[sidebar_materials_check] %s %s %s" % ("PASS" if ok else "FAIL", name, "" if ok else (got, want)))


class Stub:
    """A layout that takes any call; records the operators and menus drawn."""
    def __init__(self, log=None):
        self.__dict__["log"] = log if log is not None else []

    def __getattr__(self, name):
        def call(*args, **kwargs):
            if name in ("operator", "menu") and args:
                self.log.append(args[0])
                if "icon" in kwargs:
                    self.log.append("icon:%s:%s" % (args[0], kwargs["icon"]))
            if name == "prop" and "text" in kwargs:
                self.log.append("text:" + kwargs["text"])
            if name == "panel":
                return Stub(self.log), Stub(self.log)
            return Stub(self.log)
        return call

    def __setattr__(self, name, value):
        pass


def draw(cls, log=None):
    fake = type("Fake", (), {})()
    fake.layout = Stub(log)
    cls.draw(fake, bpy.context)
    return fake.layout.log


def poll(cls):
    return not hasattr(cls, "poll") or bool(cls.poll(bpy.context))


scene = bpy.context.scene
view_layer = bpy.context.view_layer
for o in list(scene.objects):
    o.select_set(False)
blend = os.path.splitext(os.path.basename(bpy.data.filepath))[0]
real_arm = next((o for o in scene.objects if o.type == 'ARMATURE'), None)


def activate(obj, *others):
    for o in scene.objects:
        o.select_set(False)
    view_layer.objects.active = obj
    for o in (obj,) + others:
        if o is not None:
            o.select_set(True)


def armature(name, bones):
    data = bpy.data.armatures.new(name)
    obj = bpy.data.objects.new(name, data)
    scene.collection.objects.link(obj)
    activate(obj)
    bpy.ops.object.mode_set(mode='EDIT')
    for i, b in enumerate(bones):
        eb = data.edit_bones.new(b)
        eb.head, eb.tail = (0, 0, i * 0.1), (0, 0, i * 0.1 + 0.1)
    bpy.ops.object.mode_set(mode='OBJECT')
    return obj


Exact, Effect, Fur = convert_op.FPMP_PT_Exact, convert_op.FPMP_PT_Effect, convert_op.FPMP_PT_ShellFur
ops = bpy.ops.fpmp

# no active object
view_layer.objects.active = None
for o in scene.objects:
    o.select_set(False)
check("Materials always shows", poll(Exact))
check("Effect hidden without an active object", poll(Effect), False)
check("Shell Fur hidden without an active object", poll(Fur), False)
check("Replay Effect greyed without a selection", ops.replay_effect.poll(), False)
check("Remove Wrap greyed without a selection", ops.remove_wrap.poll(), False)
check("Rig Creature greyed without an active object", ops.creature_rig.poll(), False)
check("Convert Exact available in Object Mode", ops.convert_exact.poll())

# a plain mesh, then in Edit Mode, then with fur
bpy.ops.mesh.primitive_plane_add()
plane = bpy.context.active_object
check("Shell Fur hidden on a plain mesh in Object Mode", poll(Fur), False)
check("Effect hidden on a plain mesh", poll(Effect), False)
check("Remove Wrap available with a mesh selected", ops.remove_wrap.poll())
bpy.ops.object.mode_set(mode='EDIT')
check("Shell Fur shows in Edit Mode", poll(Fur))
check("and draws Add", "fpmp.add_shell_fur" in draw(Fur))
check("Convert Exact greyed in Edit Mode", ops.convert_exact.poll(), False)
bpy.ops.object.mode_set(mode='OBJECT')
plane.modifiers.new(custom_fur.MODIFIER, 'NODES')
check("Shell Fur shows on a mesh with fur", poll(Fur))
check("and draws Remove, not Add", ("fpmp.remove_shell_fur" in draw(Fur), "fpmp.add_shell_fur" in draw(Fur)), (True, False))
bpy.ops.mesh.primitive_plane_add()
furred = bpy.context.active_object
custom_fur.add_fur(furred)
labels = [s[5:] for s in draw(Fur) if s.startswith("text:")]
check("fur inputs drawn", len(labels) > 3)
check("fur: 'Variation', not 'Length Variation'", ("Variation" in labels, "Length Variation" in labels), (True, False))
bpy.ops.object.mode_set(mode='EDIT')
check("fur in Edit Mode: Add, no Remove", ("fpmp.add_shell_fur" in draw(Fur), "fpmp.remove_shell_fur" in draw(Fur)), (True, False))
bpy.ops.object.mode_set(mode='OBJECT')

# an effect: its empty, a piece under it; a plain empty
root = bpy.data.objects.new("FX_Test", None)
root[effects.KEY] = "System"
root["mp_effect_program"] = "FX_Test replay"
piece = bpy.data.objects.new("FX_Piece", None)
piece[effects.KEY] = "Sprite"
piece.parent = root
loose = bpy.data.objects.new("Loose", None)
for o in (root, piece, loose):
    scene.collection.objects.link(o)
activate(piece)
check("Effect shows on a piece of a replayed effect", poll(Effect))
check("Replay Effect available", ops.replay_effect.poll())
check("Effect draws Replay", "fpmp.replay_effect" in draw(Effect))
activate(loose)
check("Effect hidden on a plain empty", poll(Effect), False)
check("Replay greyed on a plain empty", ops.replay_effect.poll(), False)
walked = []
from fpmp_baseline.material_porter import effect_replay  # noqa: E402
orig_roots = effect_replay.roots
effect_replay.roots = lambda objs: walked.append(1) or orig_roots(objs)
activate(plane)
poll(Effect)
check("Effect poll does no root walk on an object without effect keys", walked, [])
effect_replay.roots = orig_roots


def system(name):
    obj = bpy.data.objects.new(name, None)
    obj[effects.KEY] = "System"
    scene.collection.objects.link(obj)
    return obj


# effects held under another object: a pickaxe's trail (the pickaxe on a character), a character's contrail
character = bpy.data.objects.new("Character", bpy.data.armatures.new("Character"))
pickaxe = bpy.data.objects.new("Pickaxe", bpy.data.meshes.new("Pickaxe"))
for o in (character, pickaxe):
    scene.collection.objects.link(o)
pickaxe.parent = character
trail = system("FX_Trail")
trail.parent = pickaxe
effect_replay.store(trail, [], {}, 0.01)
activate(pickaxe)
check("Effect shows on the pickaxe holding a trail", poll(Effect))
check("Replay available from the holder", ops.replay_effect.poll())
check("holder draws Replay and the Offset", ("fpmp.replay_effect" in draw(Effect), "text:Offset" in draw(Effect)), (True, True))
activate(character)
check("Effect shows on the character holding the pickaxe", poll(Effect))
runner = bpy.data.objects.new("Runner", bpy.data.armatures.new("Runner"))
scene.collection.objects.link(runner)
contrail = system("FX_Contrail")
effect_replay.store(contrail, [], {}, 0.01)
effect_replay.attach(contrail, runner)
activate(runner)
check("Effect shows on a character with a contrail", poll(Effect))
check("the holder carries no note of its own (a linked one can hold too)", "mp_effects_held" in runner, False)
contrail.name = "FX_Contrail_renamed"
check("a renamed effect still found from its holder", effect_replay.held(runner), [contrail])
uid = contrail.session_uid
bpy.data.objects.remove(contrail)
check("a deleted effect no longer found, its note pruned", (effect_replay.held(runner),
      uid in effect_replay.stand._HELD.get(runner.session_uid, {})), ([], False))
trail.parent = None
activate(character)
check("an effect taken off no longer counts", poll(Effect), False)
legacy_holder = bpy.data.objects.new("OldPickaxe", bpy.data.meshes.new("OldPickaxe"))
scene.collection.objects.link(legacy_holder)
legacy = system("FX_Old")
legacy[effect_replay.KEY_PROGRAM] = "FX_Old replay"
legacy.parent = legacy_holder
activate(legacy_holder)
before = poll(Effect)
effect_replay.note_holders()
check("an older import's holder found once its file is loaded", (before, poll(Effect)), (False, True))
walks = []
real_children = {k: getattr(bpy.types.Object, k) for k in ("children", "children_recursive")}
try:
    for k, prop in real_children.items():
        setattr(bpy.types.Object, k, property(lambda self, p=prop: walks.append(1) or p.fget(self)))
    for obj in (plane, character, pickaxe, runner, legacy_holder, loose):
        activate(obj)
        poll(Effect)
        ops.replay_effect.poll()
        if poll(Effect):
            draw(Effect)
    check("Effect poll and draw walk no children", walks, [])
    loose.children
    check("(the walk counter counts)", walks, [1])
finally:
    for k, prop in real_children.items():
        setattr(bpy.types.Object, k, prop)
holder_uid = legacy_holder.session_uid
bpy.data.objects.remove(legacy)
effect_replay.note_holders()
check("noting again (a file opening) forgets effects gone", holder_uid in effect_replay.stand._HELD, False)

# armatures of each kind
human = armature("Human", ("root", "pelvis", "spine_01", "thigh_l", "upperarm_l", "head"))
check("Rig Creature greyed on a humanoid", ops.creature_rig.poll(), False)
check("Rig Vehicle greyed on a humanoid", ops.vehicle_rig.poll(), False)
from fpmp_baseline.processing.context import official_rig as _official  # noqa: E402
fit = armature("Fit", tuple(_official.CORE))
check("Official Rig on a humanoid", ops.official_rig.poll())
car = armature("Car", ("root", "frame", "body", "axle_pivot_fl", "wheel_steering_fl", "tire_fl", "tire_bl"))
check("Rig Vehicle on wheels", ops.vehicle_rig.poll())
check("Rig Creature greyed on wheels", ops.creature_rig.poll(), False)
check("Rig LEGO greyed on wheels", ops.lego_rig.poll(), False)
wolf = armature("Wolf", ("root", "QuadSpine_A_Pelvis_C", "QuadSpine_A_Spine1_C", "PawedLeg_A_Thigh_L", "Head_C"))
check("Rig Creature on a quadruped", ops.creature_rig.poll())
check("Official Rig greyed on a quadruped", ops.official_rig.poll(), False)
check("Rig Vehicle greyed on a quadruped", ops.vehicle_rig.poll(), False)
lego = armature("Lego", ("root", "pelvis", "torso", "head", "leg_l", "leg_r", "arm_l", "arm_r", "hand_l", "hand_r"))
check("Rig LEGO on a figure", ops.lego_rig.poll())
check("Rig Creature greyed on a LEGO figure", ops.creature_rig.poll(), False)
wolf.data["is_creature_rig"] = True
activate(wolf)
check("Rig Creature greyed once rigged", ops.creature_rig.poll(), False)
if real_arm is not None and blend in ("rg_wolf", "car"):
    activate(real_arm)
    for key in convert_op.RIGGED:      # the test blends come rigged: judge the skeleton
        if key in real_arm.data:
            del real_arm.data[key]
    check("%s: Rig Creature" % blend, ops.creature_rig.poll(), blend == "rg_wolf")
    check("%s: Rig Vehicle" % blend, ops.vehicle_rig.poll(), blend == "car")
    check("%s: Rig LEGO" % blend, ops.lego_rig.poll(), False)

# Animated Materials: the checkbox reads and writes material_time's switch
mat = bpy.data.materials.new("timed")
mat.use_nodes = True
node = mat.node_tree.nodes.new("ShaderNodeValue")
node.label = "Time (s)"
fc = node.outputs[0].driver_add("default_value")
fc.driver.expression = "frame / 30"
check("checkbox on by default", scene.fpmp_animated_materials)
scene.fpmp_animated_materials = False
check("off: the switch is off", material_time.is_animated(scene), False)
check("off: the time driver muted", fc.mute)
scene.fpmp_animated_materials = True
check("on: the switch is on", material_time.is_animated(scene))
check("on: the time driver plays", fc.mute, False)
material_time.set_animated(False, scene)
check("the switch set elsewhere shows in the checkbox", scene.fpmp_animated_materials, False)
ops.material_animation(on=True)
check("the old operator still sets it", scene.fpmp_animated_materials)

# menus
check("specials menu registered", hasattr(bpy.types, "FPMP_MT_exact_specials"))
check("Object submenu registered", hasattr(bpy.types, "FPMP_MT_object"))
hooks = getattr(bpy.types.VIEW3D_MT_object.draw, "_draw_funcs", [])
check("Object menu holds the submenu", any(getattr(f, "__module__", "").startswith("fpmp_baseline") for f in hooks))
listed = draw(bpy.types.FPMP_MT_object)
want = ["fpmp.convert_exact", "fpmp.remove_wrap", "fpmp.replay_effect", "fpmp.road_from_curve", "fpmp.restore_landscape",
        "fpmp.fix_materials", "fpmp.official_rig", "fpmp.creature_rig", "fpmp.vehicle_rig", "fpmp.lego_rig", "fpmp.convert_tasty_to_official"]
check("Object submenu lists the one-time operators", [w for w in want if w not in listed], [])
for idname in want:
    check("%s is registered" % idname, hasattr(getattr(bpy.ops.fpmp, idname.split(".")[1]), "poll"))
check("Materials panel draws the specials menu", "FPMP_MT_exact_specials" in draw(Exact))
fixer._caps["islands"] = False
check("specials without islands", [i for i in draw(bpy.types.FPMP_MT_exact_specials) if "recover" in i], [])
fixer._caps["islands"] = True
check("specials with islands", "fpmp.recover_island" in draw(bpy.types.FPMP_MT_exact_specials))
check("specials list", [w for w in ("fpmp.remove_wrap", "fpmp.road_from_curve", "fpmp.restore_landscape", "fpmp.fix_materials")
                        if w not in draw(bpy.types.FPMP_MT_exact_specials)], [])
check("bl_order", (Exact.bl_order, convert_op.FPMP_PT_Effect.bl_order, Fur.bl_order), (10, 2, 3))

# the fixer: a sub-panel of Materials; draw and poll never ask the app
view3d = fixer.FPMP_PT_fixer_view3d
check("fixer under Materials, closed", (view3d.bl_parent_id, 'DEFAULT_CLOSED' in view3d.bl_options), ("FPMP_PT_exact", True))
check("node editor copy stays top-level", getattr(fixer.FPMP_PT_fixer_nodes, "bl_parent_id", ""), "")
fixer.stop_probe()
calls = []


class FakeClient:
    fail = True

    def __init__(self, url):
        pass

    def get(self, route, path):
        calls.append(threading.current_thread() is threading.main_thread())
        if FakeClient.fail:
            raise fixer.AppError("not up")
        return {"islands": True}


real_client = fixer.AppClient
fixer.AppClient = FakeClient
fixer._caps["islands"] = None
for c in (view3d, fixer.FPMP_PT_fixer_nodes):
    draw(c)
draw(bpy.types.FPMP_MT_exact_specials)
draw(Exact)
check("no app call from draw or poll", calls, [])
fixer.RETRY = 0.2
fixer.start_probe()
deadline = time.time() + 3
while not calls and time.time() < deadline:
    time.sleep(0.05)
check("the probe asks on its own thread", calls[:1], [False])
check("app down: islands still unknown", fixer._caps["islands"], None)
FakeClient.fail = False
deadline = time.time() + 3
while fixer._caps["islands"] is None and time.time() < deadline:
    time.sleep(0.05)
check("and retries until the app answers", fixer._caps["islands"], True)
check("never on the main thread", any(calls), False)
fixer.stop_probe()
fixer.AppClient = real_client
logged = draw(view3d)
check("fixer draws Recover when islands are available", "fpmp.recover_island" in logged)
check("Fix button without an icon", [s for s in logged if s.startswith("icon:fpmp.fix_materials")], [])
check("Animated Materials not overridable", scene.bl_rna.properties["fpmp_animated_materials"].is_library_editable, False)

# the probe survives odd answers, and stopping it never waits on a request
answers = iter([ValueError("not JSON"), ["a list"], "text"])


class OddClient:
    def __init__(self, url):
        pass

    def get(self, route, path):
        a = next(answers, {"islands": True})
        if isinstance(a, Exception):
            raise a
        return a


fixer.AppClient = OddClient
fixer._caps["islands"] = None
fixer.start_probe()
deadline = time.time() + 5
while fixer._caps["islands"] is None and time.time() < deadline:
    time.sleep(0.05)
check("the probe retries after odd answers", fixer._caps["islands"], True)
fixer.stop_probe()
gate = threading.Event()


class StuckClient:
    def __init__(self, url):
        pass

    def get(self, route, path):
        gate.wait(10)
        return {"islands": False}


fixer.AppClient = StuckClient
fixer._caps["islands"] = None
fixer.start_probe()
time.sleep(0.2)
t0 = time.time()
fixer.stop_probe()
check("stop_probe doesn't wait on a request in flight", time.time() - t0 < 0.5)
gate.set()
time.sleep(0.2)
check("a stopped probe leaves the caps alone", fixer._caps["islands"], None)
check("and its redraw timer is gone", bpy.app.timers.is_registered(fixer._redraw_when_known), False)

# fixer status: short, capitalised
check("status: fixed", fixer.fix_status(12, 3, False), "Fixed 12 materials, 3 colour spaces")
check("status: preview", fixer.fix_status(1, 0, True), "Would fix 1 material")
check("status: nothing", fixer.fix_status(0, 0, False), "Nothing to fix")
props = scene.fpmp_fixer
props.preview = True
ops.fix_materials()
check("Fix sets a capitalised status", props.status[:1].isupper())
props.preview = False

# Recover Island: EXEC runs it through (headless, redo); the dialog only asks
isl = fixer.island
saved = {k: getattr(isl, k) for k in ("build_targets", "resolve", "resolved_records", "apply_textures", "apply_colors")}


def fake_resolve(app, targets):
    yield 1, 2, "M_A"
    yield 2, 2, "M_B"
    return [{"mat": "M_A", "status": "found", "textures": [{"path": "/T_A"}]}, {"mat": "M_B", "status": "not found"}]


isl.build_targets = lambda mats: [{"mat": "M_A"}, {"mat": "M_B"}]
isl.resolve = fake_resolve
isl.resolved_records = lambda records: records[:1]
isl.apply_textures = lambda app, records, problems: (3, type("Run", (), {"report": {}, "cs_changes": []})())
isl.apply_colors = lambda records: []
fixer.AppClient = type("IslandClient", (), {"__init__": lambda self, url: None, "get": lambda self, r, p: {"islands": True}})
try:
    got = ops.recover_island()
    check("Recover runs through on EXEC (headless)", (got, fixer._running), ({'FINISHED'}, False))
    check("Recover status short", props.status, "Recovered 1/2 materials")
    check("Recover not redoable (its run is modal in the UI)", 'REGISTER' in fixer.FPMP_OT_RecoverIsland.bl_options, False)
finally:
    for k, v in saved.items():
        setattr(isl, k, v)
    fixer.AppClient = real_client

print("[sidebar_materials_check] %d passed, %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
