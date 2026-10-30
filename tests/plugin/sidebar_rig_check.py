"""The Rig sidebar on a real import (run once per kind: qt_or, vt_or, tasty_rr, rg_wolf, car): its toggles are
Object properties whose get/set round-trip through the rig's own state functions, the rig operators' shared poll, the
Dynamics state names, the panel's draw (no blue toggles, no prose) and its caches. Headless.

    python plugin_test.py tests/plugin/sidebar_rig_check.py --blend out/fpdev/qt_or/qt_or.blend
"""
import sys
from types import SimpleNamespace

import bpy

sys.path.insert(0, sys.argv[sys.argv.index("--") + 1])
import fpmp_baseline  # noqa: E402

fpmp_baseline.register()
from fpmp_baseline.operator import rig_ui  # noqa: E402
from fpmp_baseline.processing.context import (deform_rig, dynamics_bake, dynamics_live, face_board,  # noqa: E402
                                              official_panel, official_rig, rig_kit)

FAILS, PASSES = [], [0]


def check(name, got, want):
    if got == want:
        PASSES[0] += 1
    else:
        FAILS.append(name)
        print("[sidebar_rig_check] FAIL %s: got %r want %r" % (name, got, want))


class Layout:
    """Records what a draw puts in a panel; children inherit `active`, the property split and the
    collapsible panel they sit in (`where`), as in Blender."""
    def __init__(self, log, active=True, where=None):
        self.log, self.active, self.enabled, self.where = log, active, True, where
        self.use_property_split = self.use_property_decorate = False
        self.alignment, self.scale_y, self.ui_units_x = 'EXPAND', 1.0, 0

    def _child(self, *_, where=None, **kw):
        if "factor" in kw:
            self.log.append(("split", kw["factor"], self.where))
        child = Layout(self.log, self.active, where or self.where)
        child.use_property_split = self.use_property_split
        return child

    column = row = split = box = grid_flow = column_flow = _child

    def panel(self, idname, default_closed=False):
        self.log.append(("panel", idname, default_closed))
        return self._child(where=idname + " header"), self._child(where=idname)

    def separator(self, **_):
        pass

    def label(self, text="", **_):
        self.log.append(("label", text, self.where))

    def operator(self, idname, text="", **kw):
        args = SimpleNamespace()
        self.log.append(("op", idname, text, kw.get("depress", False), self.where, args))
        return args

    def operator_menu_enum(self, idname, prop, text="", **_):
        self.log.append(("menu_enum", idname, text))

    def menu(self, idname, text="", **_):
        self.log.append(("menu", idname, text))

    def prop(self, owner, path, text=None, **kw):
        self.log.append(("prop", path, text, self.active, kw.get("icon"), kw.get("emboss", True), self.use_property_split))


def draw(panel, header=False):
    log = []
    getattr(panel, "draw_header" if header else "draw")(SimpleNamespace(layout=Layout(log)), bpy.context)
    return log


FLAGS = ("fpmp_official_rig", "is_tasty", "is_creature_rig", "is_vehicle_rig", "is_lego_rig")
arm = next(o for o in bpy.data.objects if o.type == 'ARMATURE' and any(o.data.get(k) for k in FLAGS))
kind = next(k for k in FLAGS if arm.data.get(k))
print("[sidebar_rig_check] %s: %s" % (arm.name, kind))
layer = bpy.context.view_layer
arm.hide_set(False)
layer.objects.active = arm
official, kit = kind == "fpmp_official_rig", rig_kit.is_kit(arm)

# registration: the toggles are properties, the Face sub-panel is gone, every panel has its order
for name in ("fpmp_rig_on", "fpmp_body_controls", "fpmp_correctives", "fpmp_dynamics", "fpmp_dynamics_live",
             "fpmp_dynamics_state"):
    check("Object.%s registered" % name, name in bpy.types.Object.bl_rna.properties, True)
# not overridable: on a library-override character the panel draws the switches' operators instead
SWITCH_PROPS = ("fpmp_rig_on", "fpmp_body_controls", "fpmp_correctives", "fpmp_dynamics", "fpmp_dynamics_live",
                "fpmp_dynamics_state", "fpmp_face_board")
for name in SWITCH_PROPS:
    p = bpy.types.Object.bl_rna.properties[name]
    check("%s not overridable" % name, (p.is_library_editable, p.is_overridable), (False, False))
check("the Face sub-panel is gone", hasattr(bpy.types, "FPMP_PT_rig_face"), False)
panels = (rig_ui.FPMP_PT_CreatureRig, rig_ui.FPMP_PT_RigPicker, rig_ui.FPMP_PT_RigIK, rig_ui.FPMP_PT_RigHands,
          rig_ui.FPMP_PT_RigSimulation)
check("every rig panel sets bl_order", [hasattr(p, "bl_order") for p in panels], [True] * len(panels))
check("the Rig panel comes first", rig_ui.FPMP_PT_CreatureRig.bl_order, 0)
check("Dynamics sub-panel label", rig_ui.FPMP_PT_RigSimulation.bl_label, "Dynamics")
check("Held Props starts closed", 'DEFAULT_CLOSED' in rig_ui.FPMP_PT_RigHands.bl_options, True)

# the shared poll: the right kind of rig, nothing else
OFFICIAL_OPS = ("official_rig_on", "official_rig_switch", "official_rig_setting", "official_rig_body_controls",
                "official_rig_bake")
KIT_OPS = ("kit_rig_on", "kit_bake", "kit_switch")
check("official operators poll on this rig", [getattr(bpy.ops.fpmp, n).poll() for n in OFFICIAL_OPS], [official] * 5)
check("kit operators poll on this rig", [getattr(bpy.ops.fpmp, n).poll() for n in KIT_OPS], [kit] * 3)
check("correctives operator polls when the skeleton has them", bpy.ops.fpmp.deform_rig_on.poll(),
      bool(arm.data.get(deform_rig.MARK)))
check("dynamics operators poll when the armature has dynamics", [bpy.ops.fpmp.dynamics_on.poll(),
      bpy.ops.fpmp.dynamics_live.poll(), bpy.ops.fpmp.dynamics_state.poll()], [bool(arm.data.get(dynamics_bake.KEY))] * 3)
check("select and reset poll on any rig", (bpy.ops.fpmp.rig_select_controls.poll(), bpy.ops.fpmp.rig_reset_pose.poll()),
      (True, True))
mesh = next((o for o in layer.objects if o.type == 'MESH'), None)
if mesh is not None:
    layer.objects.active = mesh
    check("no rig operator polls on a mesh", any(getattr(bpy.ops.fpmp, n).poll() for n in OFFICIAL_OPS + KIT_OPS +
          ("rig_select_controls", "rig_reset_pose", "rig_pick", "deform_rig_on", "dynamics_on", "dynamics_live")), False)
    check("the property getters are safe on a mesh", (mesh.fpmp_rig_on, mesh.fpmp_correctives, mesh.fpmp_dynamics), (False,) * 3)
    layer.objects.active = arm

# rig on/off round-trips through the rig's own state
if official or kit:
    key = official_rig.ON if official else rig_kit.ON
    arm.fpmp_rig_on = False
    check("Rig off: stored and the constraints muted", (bool(arm.data[key]), arm.fpmp_rig_on), (False, False))
    arm.fpmp_rig_on = True
    check("Rig on again", (bool(arm.data[key]), arm.fpmp_rig_on), (True, True))
    header = draw(rig_ui.FPMP_PT_CreatureRig, header=True)
    check("the Rig header is a checkbox", [(d[0], d[1], d[2]) for d in header], [("prop", "fpmp_rig_on", "")])

if official:
    show = official_rig.load()["solve"]["visibility"]
    arm.fpmp_body_controls = False
    check("Body Controls off: Epic's setting and the controls hide",
          (official_rig.setting(arm, show), arm.pose.bones["hips_ctrl"].hide, arm.fpmp_body_controls), (0.0, True, False))
    arm.fpmp_body_controls = True
    check("Body Controls on again", (official_rig.setting(arm, show), arm.fpmp_body_controls), (1.0, True))
    # a bone that lost its property reads 0, never a KeyError
    bone = arm.pose.bones[show]
    keep = bone[show]
    del bone[show]
    check("setting() of a lost property", official_rig.setting(arm, show), 0.0)
    bone[show] = keep
    check("parts() is computed once per rig, not per call", official_panel.parts(arm) is official_panel.parts(arm), True)
    # same armature name and bone count, another rig (another file): the cache must not hand back the old parts
    switch = official_panel.parts(arm)[-1]["switch"]
    before = len(official_panel.parts(arm))
    arm.data.bones[switch].name = "renamed_switch"
    check("parts() follows the switch bones, not the bone count", len(official_panel.parts(arm)), before - 1)
    arm.data.bones["renamed_switch"].name = switch
    check("parts() back", len(official_panel.parts(arm)), before)

if arm.data.get(deform_rig.MARK):
    arm.fpmp_correctives = False
    check("Correctives off round-trips", (bool(arm.data[deform_rig.ON]), arm.fpmp_correctives), (False, False))
    arm.fpmp_correctives = True
    check("Correctives on again", (bool(arm.data[deform_rig.ON]), arm.fpmp_correctives), (True, True))

if arm.data.get(dynamics_bake.KEY):
    check("Dynamics reads the bake's state", arm.fpmp_dynamics, dynamics_bake.is_on(arm))
    check("state names read as words", dynamics_bake.state_label("OnGround_Standing"), "On Ground, Standing")
    check("state names: small words", dynamics_bake.state_label("EmoteOrMelee"), "Emote or Melee")
    states = dynamics_bake.states(arm)
    if states:
        a = dynamics_bake.state_items(arm)
        check("the state items are cached (Blender keeps no copy of their strings)", a is dynamics_bake.state_items(arm), True)
        check("the state items are labelled", a[0][1], dynamics_bake.state_label(states[0]))
        arm.fpmp_dynamics_state = states[-1]
        check("State round-trips", (arm.data.get("fpmp_dynamics_state"), arm.fpmp_dynamics_state), (states[-1], states[-1]))
    arm.fpmp_dynamics_live = True
    check("Live on round-trips", (dynamics_live.is_live(arm), arm.fpmp_dynamics_live), (True, True))
    arm.fpmp_dynamics_live = False
    check("Live off round-trips", (dynamics_live.is_live(arm), arm.fpmp_dynamics_live), (False, False))
    # the panel reads the cloth meshes from a cache: no object scan per redraw
    from fpmp_baseline.processing.context import cloth_render
    calls, find = [0], cloth_render.find

    def counted(i):
        calls[0] += 1
        return find(i)
    cloth_render.find = counted
    dynamics_bake._cloth(arm)
    calls[0] = 0
    for _ in range(3):
        draw(rig_ui.FPMP_PT_RigSimulation)
        draw(rig_ui.FPMP_PT_RigSimulation, header=True)
    cloth_render.find = find
    check("redraws scan no objects", calls[0], 0)
    # the body greys with its header off, once there is a bake to turn on (before one, Simulate is what's wanted)
    baked, on = dynamics_bake.baked, dynamics_bake.is_on
    dynamics_bake.baked, dynamics_bake.is_on = (lambda o: True), (lambda o: False)
    check("Dynamics off: its body greyed", {d[3] for d in draw(rig_ui.FPMP_PT_RigSimulation) if d[0] == "prop"}, {False})
    dynamics_bake.is_on = lambda o: True
    check("Dynamics on: its body active", {d[3] for d in draw(rig_ui.FPMP_PT_RigSimulation) if d[0] == "prop"}, {True})
    dynamics_bake.baked, dynamics_bake.is_on = baked, on
    if not baked(arm):
        check("no bake yet: the body active", {d[3] for d in draw(rig_ui.FPMP_PT_RigSimulation) if d[0] == "prop"}, {True})

# the panel: rows of props, no blue operator toggles, no prose
log = draw(rig_ui.FPMP_PT_CreatureRig)
for sub in panels[1:]:
    if sub.poll(bpy.context):
        log += draw(sub)
        if hasattr(sub, "draw_header"):
            log += draw(sub, header=True)
pressed = [d for d in log if d[0] == "op" and d[3]]
main = draw(rig_ui.FPMP_PT_CreatureRig)
check("the Rig panel has no pressed operator", [d for d in main if d[0] == "op" and d[3]], [])
check("no label is prose or a readout", [d[1] for d in log if d[0] == "label" and (":" in d[1] or len(d[1].split()) > 3)], [])
groups = [d for d in main if d[0] == "prop" and d[1] == "is_visible"]
check("visibility rows are flat and never greyed (the eye says hidden)",
      all(not d[5] and d[3] and d[4] in ('HIDE_OFF', 'HIDE_ON') for d in groups) and len(groups) > 0, True)
check("each row's eye follows its group", [d[4] for d in groups],
      ['HIDE_OFF' if c.is_visible else 'HIDE_ON' for c in arm.data.collections if c.name in [d[2] for d in groups]])


class Elsewhere:
    """bpy.context as another editor's sidebar sees it (the panel copied into a non-3D-viewport editor)."""
    space_data = SimpleNamespace(type='NODE_EDITOR')

    def __getattr__(self, name):
        return getattr(bpy.context, name)


elsewhere = []
rig_ui.FPMP_PT_CreatureRig.draw(SimpleNamespace(layout=Layout(elsewhere)), Elsewhere())
check("outside the 3D viewport no Select All / Reset row (that editor's own view does it)",
      [d[2] for d in elsewhere if d[0] == "op" and d[2] in ("Select All", "Reset", "Reset All")], [])
texts = [d[2] for d in main if d[0] == "op"]
ops = [d[1] for d in main if d[0] == "op"]
if official or kit or kind == "is_tasty":
    check("Select All, Reset, Reset All", [t for t in texts if t in ("Select All", "Reset", "Reset All")], ["Select All", "Reset", "Reset All"])
if official or kit:
    check("Bake to Controls", any(o in ops for o in ("fpmp.official_rig_bake", "fpmp.kit_bake")), True)
if official:
    check("Body Controls is a row of the list", [d[1] for d in main if d[1] == "fpmp_body_controls" and not d[5]], ["fpmp_body_controls"])
    check("the Picker's neck cells", [d[2] for d in draw(rig_ui.FPMP_PT_RigPicker) if d[0] == "op" and d[2].startswith("Neck")],
          ["Neck 2", "Neck 1"])
    ik = draw(rig_ui.FPMP_PT_RigIK)
    check("IK / FK: FK | IK pressed as a one-of, Stretch and Local apart", all(t in [d[2] for d in ik if d[0] == "op"]
          for t in ("FK", "IK", "Stretch", "Local")), True)
if kind == "is_tasty":
    check("Tasty: Convert to Official Rig", "fpmp.convert_tasty_to_official" in ops, True)
if arm.data.get(deform_rig.MARK):
    check("Correctives is a checkbox", [d[6] for d in main if d[1] == "fpmp_correctives"], [True])
if face_board.has_board(arm):
    check("the face board's switch reads Face (beside Tasty's Face Board group)",
          [d[2] for d in main if d[1] == "fpmp_face_board"], ["Face"])
if kit or kind == "is_creature_rig":
    check("the IK / FK sub-panel shows for creatures", rig_ui.FPMP_PT_RigIK.poll(bpy.context), True)
    ik = draw(rig_ui.FPMP_PT_RigIK)
    check("creature limbs drawn there", "FK" in [d[2] for d in ik if d[0] == "op"], True)
if kit:
    from fpmp_baseline.processing.context import kit_panel
    rows = kit_panel.groups(arm)
    singles = [label for label, held in rows if len(held) == 1 and label != "All"]
    grouped = len(rows) > len(singles)
    check("kit table: the official grid (FK | IK cell, then the flags' column)",
          official_panel.BUTTONS in [d[1] for d in ik if d[0] == "split"], True)
    if grouped:
        check("kit table: each limb under a closed Each Side", ([d for d in ik if d[0] == "panel"],
              [d[1] for d in ik if d[0] == "label" and d[2] == "fpmp_kit_rig_sides"]),
              ([("panel", "fpmp_kit_rig_sides", True)], singles))
        check("kit table: group rows on top", [d[1] for d in ik if d[0] == "label" and d[2] is None],
              [label for label, _ in rows if label not in singles])
if kind == "is_vehicle_rig":
    check("vehicle settings split", all(d[6] for d in main if d[0] == "prop" and d[1] != "is_visible"), True)
    check("vehicle labels fit the 280 px sidebar", max(len(d[2]) for d in main if d[0] == "prop" and d[6]) <= 13, True)
    check("no IK / FK for a vehicle", rig_ui.FPMP_PT_RigIK.poll(bpy.context), False)
if arm.data.get(dynamics_bake.KEY):
    sim = draw(rig_ui.FPMP_PT_RigSimulation) + draw(rig_ui.FPMP_PT_RigSimulation, header=True)
    reason = dynamics_bake.cannot_bake(arm)
    check("Simulate greyed says why (its poll message)", (bool(reason), bpy.ops.fpmp.dynamics_bake.poll()),
          (dynamics_bake.frame_range(arm) is None, dynamics_bake.frame_range(arm) is not None))
    if dynamics_bake.frame_range(arm) is None:
        bone = arm.pose.bones[0]
        bone.keyframe_insert("location", frame=1)
        ad = arm.animation_data
        check("an action: Simulate polls", (dynamics_bake.cannot_bake(arm), bpy.ops.fpmp.dynamics_bake.poll()), ("", True))
        act, ad.action = ad.action, None
        track = ad.nla_tracks.new()
        track.strips.new(act.name, 1, act)
        track.mute = True
        check("only muted tracks: the message says so", "muted" in dynamics_bake.cannot_bake(arm), True)
        ad.nla_tracks.remove(track)
        bpy.data.actions.remove(act)
    check("Dynamics: header checkbox, Live checkbox, Simulate, State",
          ([d[1] for d in sim if d[0] == "prop"], [d[2] for d in sim if d[0] == "op"]),
          (["fpmp_dynamics_live"] + (["fpmp_dynamics_state"] if dynamics_bake.states(arm) else []) + ["fpmp_dynamics"],
           ["Simulate"]))

# an older creature rig: its ik_* sliders, labelled as words
old = bpy.data.objects.new("legacy_rig", bpy.data.armatures.new("legacy_rig"))
bpy.context.scene.collection.objects.link(old)
old.data["is_creature_rig"] = True
old["ik_front_leg_l"] = old["eyes_aim"] = 1.0
layer.objects.active = old
check("legacy ik_ sliders title-cased", [d[2] for d in draw(rig_ui.FPMP_PT_RigIK) if d[0] == "prop"], ["Front Leg L", "Eyes Aim"])
bpy.data.objects.remove(old)
layer.objects.active = arm

# a library-override character (its armature data stays linked): the switches are their operators, pressed while on
import os  # noqa: E402
import tempfile  # noqa: E402
lib = os.path.join(tempfile.mkdtemp(), "lib.blend")
coll = arm.users_collection[0].name
bpy.ops.wm.save_as_mainfile(filepath=lib, copy=True)
bpy.ops.wm.read_homefile(use_empty=True)
with bpy.data.libraries.load(lib, link=True) as (_, dst):
    dst.collections = [coll]
bpy.context.scene.collection.children.link(dst.collections[0])
ov = dst.collections[0].override_hierarchy_create(bpy.context.scene, bpy.context.view_layer)
arm = next(o for o in ov.all_objects if o.type == 'ARMATURE' and any(o.data.get(k) for k in FLAGS))
bpy.context.view_layer.objects.active = arm
check("override: the armature data is linked", arm.data.library is not None, True)
SWITCH_OPS = {"fpmp_rig_on": ("official_rig_on" if official else "kit_rig_on", "on"),
              "fpmp_body_controls": ("official_rig_body_controls", "show"),
              "fpmp_correctives": ("deform_rig_on", "on"), "fpmp_dynamics": ("dynamics_on", "on")}
switches = [n for n in SWITCH_OPS if getattr(arm, n) or n == "fpmp_rig_on" and (official or kit)]
check("override: the switch properties are read-only", [arm.is_property_readonly(n) for n in switches], [True] * len(switches))


def drawn():
    log = draw(rig_ui.FPMP_PT_CreatureRig, header=True) + draw(rig_ui.FPMP_PT_CreatureRig)
    if rig_ui.FPMP_PT_RigSimulation.poll(bpy.context):
        log += draw(rig_ui.FPMP_PT_RigSimulation, header=True)
    return log


check("override: no switch drawn as a property", [d[1] for d in drawn() if d[0] == "prop" and d[1] in SWITCH_OPS], [])
for n in switches:
    op, arg = SWITCH_OPS[n]
    run = getattr(bpy.ops.fpmp, op)
    check("override: %s is its operator, pressed, turning it off" % n,
          [(d[3], getattr(d[5], arg, None)) for d in drawn() if d[0] == "op" and d[1] == "fpmp." + op], [(True, False)])
    check("override: %s's operator polls" % n, run.poll(), True)
    run(**{arg: False})
    check("override: %s turns off" % n, (getattr(arm, n), [(d[3], getattr(d[5], arg, None)) for d in drawn()
          if d[0] == "op" and d[1] == "fpmp." + op]), (False, [(False, True)]))
    run(**{arg: True})
    check("override: %s on again" % n, getattr(arm, n), True)
check("override: select, reset and pick poll", (bpy.ops.fpmp.rig_select_controls.poll(), bpy.ops.fpmp.rig_reset_pose.poll(),
      bpy.ops.fpmp.rig_pick.poll()), (True, True, True))
if dynamics_bake.KEY in arm.data:
    check("override: Live and State greyed (Live adds bones to the linked armature)",
          (arm.is_property_readonly("fpmp_dynamics_live"), arm.is_property_readonly("fpmp_dynamics_state")), (True, True))

print("[sidebar_rig_check] %d passed, %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
