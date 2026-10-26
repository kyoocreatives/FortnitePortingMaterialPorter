"""The Rig panel (sidebar, Fortnite Porting tab) for the fork's rigs: show or hide each control group, select or
reset the controls, the rig's switches; the official rig's picker, IK / FK, held props and dynamics in sub-panels
(and a Shift+Q pie: rig_pie). The switches are Object properties (rig_props), their operators on override characters."""
import bpy

from ..processing.context import deform_rig, dynamics_bake, face_board, official_rig, rig_kit, rig_style
from . import rig_props

OFFICIAL = official_rig.MARK
RIG_OPS = ("fpmp.official_rig", "fpmp.creature_rig", "fpmp.vehicle_rig", "fpmp.lego_rig")      # a plain armature's rigs
VEHICLE_SETTINGS = (("auto_wheels", "Wheels Spin"), ("auto_steer", "Wheels Steer"), ("countersteer", "Counter-steer"),
                    ("suspension", "Suspension"), ("lean", "Lean in Turns"))
RIGGED = ("is_vehicle_rig", "is_creature_rig", "is_lego_rig")


def _rig(context):
    obj = context.active_object
    return obj if obj is not None and obj.type == 'ARMATURE' else None


class _RigOp:
    """The shared poll of the operators acting on the rig: an active armature of their kind, with editable data
    (`pose_only`: the pose is enough, which an override character's object has)."""
    pose_only = False

    @staticmethod
    def kind(obj):
        return True

    @classmethod
    def poll(cls, context):
        obj = _rig(context)
        if obj is None or not cls.kind(obj):
            return False
        if cls.pose_only and obj.library is not None:
            cls.poll_message_set("The armature is linked: override it first")
            return False
        if not cls.pose_only and not obj.data.is_editable:
            cls.poll_message_set("The armature's data is linked: make it local or override it first")
            return False
        return True


class _OfficialOp(_RigOp):
    @staticmethod
    def kind(obj):
        return bool(obj.data.get(OFFICIAL))


class _KitOp(_RigOp):
    kind = staticmethod(rig_kit.is_kit)


class _DynamicsOp(_RigOp):
    @staticmethod
    def kind(obj):
        return bool(obj.data.get(dynamics_bake.KEY))


class FPMP_OT_RigSelectControls(_RigOp, bpy.types.Operator):
    """Select every control of the rig (its Controls and Secondary groups)"""
    bl_idname = "fpmp.rig_select_controls"
    bl_label = "Select Controls"
    bl_options = {'REGISTER', 'UNDO'}
    pose_only = True

    def execute(self, context):
        obj = _rig(context)
        # only what the user sees, as Blender's own Select All
        chosen = {b.name for b in rig_style.controls(obj)
                  if not b.hide and any(c.is_visible_effectively for c in b.bone.collections)}
        for bone in obj.pose.bones:
            bone.select = bone.name in chosen
        return {'FINISHED'}


class FPMP_OT_RigResetPose(_RigOp, bpy.types.Operator):
    bl_idname = "fpmp.rig_reset_pose"
    bl_label = "Reset Pose"
    bl_options = {'REGISTER', 'UNDO'}
    pose_only = True
    scope: bpy.props.EnumProperty(items=[('SELECTED', "Selected", "The selected controls"),
                                         ('ALL', "All", "Every control")], default='SELECTED')

    @classmethod
    def description(cls, context, properties):
        return "Put %s back at rest (never the rig's mechanism or the game's bones)" % (
            "every control" if properties.scope == 'ALL' else "the selected controls")

    def execute(self, context):
        for bone in rig_style.controls(_rig(context)):
            if self.scope == 'SELECTED' and not bone.select:
                continue
            bone.location = (0.0, 0.0, 0.0)
            bone.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
            bone.rotation_euler = (0.0, 0.0, 0.0)
            bone.rotation_axis_angle = (0.0, 0.0, 1.0, 0.0)
            bone.scale = (1.0, 1.0, 1.0)
        return {'FINISHED'}


class FPMP_OT_RigPick(_RigOp, bpy.types.Operator):
    bl_idname = "fpmp.rig_pick"
    bl_label = "Pick"
    bl_options = {'REGISTER', 'UNDO'}
    pose_only = True
    names: bpy.props.StringProperty(description="The controls, comma separated")
    extend: bpy.props.BoolProperty(description="Add to the selection")

    @classmethod
    def description(cls, context, properties):
        return "Select %s (Shift: add to the selection)" % ", ".join(_names(properties.names))

    def invoke(self, context, event):
        self.extend = event.shift
        return self.execute(context)

    def execute(self, context):
        obj = _rig(context)
        names = [n for n in _names(self.names) if n in obj.pose.bones]
        if not names:
            return {'CANCELLED'}
        if obj.mode != 'POSE':
            bpy.ops.object.mode_set(mode='POSE')
        for bone in obj.pose.bones:
            if bone.name in names:
                bone.select = True
            elif not self.extend:
                bone.select = False
        obj.data.bones.active = obj.data.bones[names[0]]
        return {'FINISHED'}


class FPMP_OT_KitRigOn(_KitOp, bpy.types.Operator):
    bl_idname = "fpmp.kit_rig_on"
    bl_label = "Rig On/Off"
    bl_description = "The rig drives the bones, or the bones play their own animation"
    bl_options = {'REGISTER', 'UNDO'}
    pose_only = True
    on: bpy.props.BoolProperty(default=True)

    def execute(self, context):
        rig_kit.set_on(_rig(context), self.on)
        return {'FINISHED'}


class FPMP_OT_KitBake(_KitOp, bpy.types.Operator):
    bl_idname = "fpmp.kit_bake"
    bl_label = "Bake to Controls"
    bl_description = "Key the controls from the animated bones over the scene range, then turn the rig on"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        frames = rig_kit.bake_to_controls(_rig(context), scene.frame_start, scene.frame_end)
        self.report({'INFO'}, "%d frames baked to the controls" % frames)
        return {'FINISHED'}


class FPMP_OT_KitSwitch(_KitOp, bpy.types.Operator):
    bl_idname = "fpmp.kit_switch"
    bl_label = "FK/IK"
    bl_options = {'REGISTER', 'UNDO'}
    limbs: bpy.props.StringProperty(description="The limbs, comma separated")
    ik: bpy.props.BoolProperty()

    @classmethod
    def description(cls, context, properties):
        return "Switch %s to %s, keeping the pose" % (", ".join(n.replace("_", " ").title() for n in _names(properties.limbs)),
                                                       "IK" if properties.ik else "FK")

    def execute(self, context):
        for name in _names(self.limbs):
            rig_kit.switch(_rig(context), name, self.ik)
        return {'FINISHED'}


class FPMP_OT_OfficialRigOn(_OfficialOp, bpy.types.Operator):
    bl_idname = "fpmp.official_rig_on"
    bl_label = "Rig On/Off"
    bl_description = "The rig drives the bones, or the bones play their own animation"
    bl_options = {'REGISTER', 'UNDO'}
    pose_only = True
    on: bpy.props.BoolProperty(default=True)

    def execute(self, context):
        official_rig.set_on(_rig(context), self.on)
        return {'FINISHED'}


def _names(text):
    return [n for n in text.split(",") if n]


def _parts_text(names, cut):
    return ", ".join(n.replace(cut, "").replace("_", " ").title() for n in names)


class FPMP_OT_OfficialRigSwitch(_OfficialOp, bpy.types.Operator):
    bl_idname = "fpmp.official_rig_switch"
    bl_label = "FK/IK"
    bl_options = {'REGISTER', 'UNDO'}
    switch: bpy.props.StringProperty(description="The parts' switches, comma separated")
    ik: bpy.props.BoolProperty()

    @classmethod
    def description(cls, context, properties):
        return "Switch %s to %s, keeping the pose" % (_parts_text(_names(properties.switch), "_fk_ik_switch"),
                                                       "IK" if properties.ik else "FK")

    def execute(self, context):
        for switch in _names(self.switch):
            official_rig.set_switch(_rig(context), switch, self.ik)
        return {'FINISHED'}


class FPMP_OT_OfficialRigSetting(_OfficialOp, bpy.types.Operator):
    bl_idname = "fpmp.official_rig_setting"
    bl_label = "Rig Setting"
    bl_options = {'REGISTER', 'UNDO'}
    name: bpy.props.StringProperty(description="Epic's settings, comma separated")
    value: bpy.props.FloatProperty()

    @classmethod
    def description(cls, context, properties):
        names = _names(properties.name)
        what = "Local (the arm or head follows the body, not the shoulder or neck)" if any("local" in n for n in names) \
            else "Stretch (the part lengthens to reach its control)"
        return "%s %s: %s" % ("Turn on" if properties.value >= 0.5 else "Turn off", what, _parts_text(names, ""))

    def execute(self, context):
        for name in _names(self.name):
            official_rig.set_setting(_rig(context), name, self.value)
        return {'FINISHED'}


class FPMP_OT_OfficialRigBodyControls(_OfficialOp, bpy.types.Operator):
    bl_idname = "fpmp.official_rig_body_controls"
    bl_label = "Body Controls"
    bl_description = "Show or hide the fingers, toes, root, body, hips and neck controls"
    bl_options = {'REGISTER', 'UNDO'}
    pose_only = True
    show: bpy.props.BoolProperty(default=True)

    def execute(self, context):
        official_rig.set_body_controls(_rig(context), self.show)
        return {'FINISHED'}


class FPMP_OT_OfficialRigBake(_OfficialOp, bpy.types.Operator):
    bl_idname = "fpmp.official_rig_bake"
    bl_label = "Bake to Controls"
    bl_description = "Key the controls from the animated bones over the scene range, then turn the rig on"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        frames = official_rig.bake_to_controls(_rig(context), scene.frame_start, scene.frame_end)
        self.report({'INFO'}, "%d frames baked to the controls" % frames)
        return {'FINISHED'}


class FPMP_OT_ConvertTastyToOfficial(_RigOp, bpy.types.Operator):
    bl_idname = "fpmp.convert_tasty_to_official"
    bl_label = "Convert to Official Rig"
    bl_description = ("Replace the Tasty rig with Fortnite's official rig, keeping the pose shown now (it is keyed on "
                      "the official controls at this frame). The old animation is kept as a muted NLA track")
    bl_options = {'REGISTER', 'UNDO'}
    hands: bpy.props.BoolProperty(name="Hand Controls", description="Add the fingers and palm controls too")

    @staticmethod
    def kind(obj):
        from ..processing.context import official_convert
        return official_convert.can_convert(obj)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        from ..processing.context import official_convert
        try:
            self.report({'INFO'}, official_convert.convert(_rig(context), self.hands))
        except ValueError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        return {'FINISHED'}


class FPMP_OT_DeformRigOn(_RigOp, bpy.types.Operator):
    bl_idname = "fpmp.deform_rig_on"
    bl_label = "Correctives On/Off"
    bl_description = "Fortnite's deform correctives follow the pose, or stay at rest"
    bl_options = {'REGISTER', 'UNDO'}
    pose_only = True
    on: bpy.props.BoolProperty(default=True)

    @staticmethod
    def kind(obj):
        return bool(obj.data.get(deform_rig.MARK))

    def execute(self, context):
        deform_rig.set_on(_rig(context), self.on)
        return {'FINISHED'}


class FPMP_OT_DynamicsBake(_DynamicsOp, bpy.types.Operator):
    bl_idname = "fpmp.dynamics_bake"
    bl_label = "Simulate Dynamics"
    bl_description = "Run Fortnite's dynamic bones over the current animation and key them in a Dynamics layer"
    bl_options = {'REGISTER', 'UNDO'}
    rate: bpy.props.IntProperty(name="Steps per Second", default=60, min=10, max=240)

    @classmethod
    def poll(cls, context):
        if not super().poll(context):
            return False
        if dynamics_bake.frame_range(_rig(context)) is None:
            cls.poll_message_set("No animation on this armature")
            return False
        return True

    def execute(self, context):
        bones, frames = dynamics_bake.bake(_rig(context), self.rate)
        self.report({'INFO'}, "%d dynamic bones over %d frames" % (bones, frames))
        return {'FINISHED'}


class FPMP_OT_DynamicsOn(_DynamicsOp, bpy.types.Operator):
    bl_idname = "fpmp.dynamics_on"
    bl_label = "Dynamics On/Off"
    bl_description = "Play the simulated dynamic bones, or the animation's own"
    bl_options = {'REGISTER', 'UNDO'}
    pose_only = True
    on: bpy.props.BoolProperty(default=True)

    def execute(self, context):
        dynamics_bake.set_on(_rig(context), self.on)
        return {'FINISHED'}


class FPMP_OT_DynamicsLive(_DynamicsOp, bpy.types.Operator):
    bl_idname = "fpmp.dynamics_live"
    bl_label = "Live Dynamics"
    bl_description = "Simulate the dynamic bones as the animation plays forward (a jump restarts them); Simulate bakes the final result"
    bl_options = {'REGISTER', 'UNDO'}
    on: bpy.props.BoolProperty(default=True)

    def execute(self, context):
        from ..processing.context import dynamics_live
        if not dynamics_live.set_live(_rig(context), self.on):
            self.report({'WARNING'}, "No dynamic bones this armature can simulate")
            return {'CANCELLED'}
        return {'FINISHED'}


def _states(self, context):
    return dynamics_bake.state_items(_rig(context))


class FPMP_OT_DynamicsState(_DynamicsOp, bpy.types.Operator):
    bl_idname = "fpmp.dynamics_state"
    bl_label = "Movement State"
    bl_description = "The character state whose dynamics settings apply (gravity, how much body motion reaches the chains); Simulate again after changing it"
    bl_options = {'REGISTER', 'UNDO'}
    state: bpy.props.EnumProperty(items=_states)

    def execute(self, context):
        dynamics_bake.set_state(_rig(context), self.state)
        return {'FINISHED'}


class FPMP_PT_CreatureRig(bpy.types.Panel):
    bl_label = "Rig"
    bl_idname = "FPMP_PT_creature_rig"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Fortnite Porting"
    bl_order = 0

    @classmethod
    def poll(cls, context):
        return _rig(context) is not None

    def draw_header(self, context):
        obj = _rig(context)
        if obj.data.get(OFFICIAL) or rig_kit.is_kit(obj):
            op = FPMP_OT_OfficialRigOn if obj.data.get(OFFICIAL) else FPMP_OT_KitRigOn
            _switch(self.layout, obj, "fpmp_rig_on", op.bl_idname, "on", text="")

    def draw(self, context):
        obj = _rig(context)
        data, layout = obj.data, self.layout
        if data.get(OFFICIAL):          # first: it can be built on a Tasty-rigged armature
            _controls(layout, obj, rig_style.COLLECTIONS + (face_board.GROUP,))
            layout.operator(FPMP_OT_OfficialRigBake.bl_idname, icon='ACTION')
        elif data.get("is_tasty"):
            from ..processing.context import tasty_style
            _controls(layout, obj, tasty_style.SHOWN + tasty_style.HIDDEN + (face_board.GROUP,))
            layout.operator(FPMP_OT_ConvertTastyToOfficial.bl_idname, icon='ARMATURE_DATA')
        elif not any(data.get(k) for k in RIGGED):
            col = layout.column(align=True)
            # only the rigs that fit this skeleton; all of them (greyed, their tooltips say why) when none does
            fitting = [i for i in RIG_OPS if getattr(bpy.ops.fpmp, i.split(".")[1]).poll()]
            for idname in fitting or RIG_OPS:
                col.operator(idname)
            if data.get("fpmp_metahuman_board"):        # a plain skeleton with a face board
                layout.operator(FPMP_OT_RigSelectControls.bl_idname, text="Select Controls")
        else:
            _controls(layout, obj, rig_style.COLLECTIONS + (rig_style.FK,) + rig_style.LEGACY)
            if rig_kit.is_kit(obj):
                layout.operator(FPMP_OT_KitBake.bl_idname, icon='ACTION')
            if data.get("is_vehicle_rig"):
                _vehicle(layout, obj)
        _switches(layout, obj)


def _overridden(obj):
    """A library-override character or linked armature data: the switch properties can't be written there."""
    return obj.override_library is not None or not obj.data.is_editable


def _switch(layout, obj, prop, idname, arg, text, icon='NONE'):
    """A switch: its property, or on an override character its operator, pressed while on."""
    if not _overridden(obj):
        layout.prop(obj, prop, text=text, icon=icon)
        return
    on = getattr(obj, prop)
    header = not text and icon == 'NONE'        # drawn as the header's checkbox
    if header:
        icon = 'CHECKBOX_HLT' if on else 'CHECKBOX_DEHLT'
    setattr(layout.operator(idname, text=text, icon=icon, emboss=not header, depress=on), arg, not on)


def _eye(layout, owner, prop, text, shown, op=None):
    """A visibility row: flat, the eye says it, a hidden one greyed (`op`: the operator drawn on an override)."""
    row = layout.row()
    row.active, row.alignment = shown, 'LEFT'       # a list, not centred toggles
    icon = 'HIDE_OFF' if shown else 'HIDE_ON'
    if op is not None and _overridden(owner):
        row.operator(op, text=text, icon=icon, emboss=False, depress=shown).show = not shown
    else:
        row.prop(owner, prop, text=text, icon=icon, toggle=True, emboss=False, translate=False)


def _controls(layout, obj, names):
    """The rig's control groups (and the official rig's Body Controls) to show or hide, then select and reset."""
    grid = layout.grid_flow(row_major=True, columns=2, even_columns=True, align=True)
    for group in [c for c in obj.data.collections if c.name in names]:
        _eye(grid, group, "is_visible", group.name, group.is_visible)
    if rig_props.body_switch(obj):
        _eye(grid, obj, "fpmp_body_controls", "Body Controls", obj.fpmp_body_controls,
             FPMP_OT_OfficialRigBodyControls.bl_idname)
    row = layout.row(align=True)
    row.operator(FPMP_OT_RigSelectControls.bl_idname, text="Select All")
    row.operator(FPMP_OT_RigResetPose.bl_idname, text="Reset").scope = 'SELECTED'
    row.operator(FPMP_OT_RigResetPose.bl_idname, text="Reset All").scope = 'ALL'


def _split(layout):
    col = layout.column()
    col.use_property_split, col.use_property_decorate = True, False
    return col


def _switches(layout, obj):
    """The skeleton's own switches, whatever rig sits on it: correctives and the face board."""
    has_correctives, has_board = bool(obj.data.get(deform_rig.MARK)), face_board.has_board(obj)
    if has_correctives or has_board:
        col = _split(layout).column(heading="Drive", align=True)     # Blender's checkbox group: heading, boxes beside it
        if has_correctives:
            _switch(col, obj, "fpmp_correctives", FPMP_OT_DeformRigOn.bl_idname, "on", "Correctives")
        if has_board:
            col.prop(obj, "fpmp_face_board", text="Face")


def _vehicle(layout, obj):
    col = _split(layout)
    col.prop(obj, "fpmp_ground", text="Ground")
    owner, _ = rig_style.settings_owner(obj)
    for key, text in VEHICLE_SETTINGS:
        if key in owner:          # rigs made before a setting existed lack it
            col.prop(owner, '["%s"]' % key, text=text, slider=True)


class _RigSub:
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Fortnite Porting"
    bl_parent_id = FPMP_PT_CreatureRig.bl_idname


class FPMP_PT_RigPicker(_RigSub, bpy.types.Panel):
    bl_label = "Picker"
    bl_idname = "FPMP_PT_rig_picker"
    bl_order = 0

    @classmethod
    def poll(cls, context):
        obj = _rig(context)
        return obj is not None and bool(obj.data.get(OFFICIAL))

    def draw(self, context):
        from ..processing.context import official_picker
        official_picker.ui_picker(self.layout, _rig(context))


def _legacy_ik(obj):
    """An older creature or LEGO rig's IK sliders (ik_*) and Eyes Aim, from its settings."""
    owner, _ = rig_style.settings_owner(obj)
    return owner, sorted(k for k in owner.keys() if k.startswith("ik_")), "eyes_aim" in owner


class FPMP_PT_RigIK(_RigSub, bpy.types.Panel):
    bl_label = "IK / FK"
    bl_idname = "FPMP_PT_rig_ik"
    bl_order = 1

    @classmethod
    def poll(cls, context):
        obj = _rig(context)
        if obj is None or obj.data.get("is_vehicle_rig"):
            return False
        if obj.data.get(OFFICIAL) or rig_kit.is_kit(obj):
            return True
        if not any(obj.data.get(k) for k in RIGGED):
            return False
        _, sliders, eyes = _legacy_ik(obj)
        return bool(sliders) or eyes

    def draw(self, context):
        obj = _rig(context)
        if obj.data.get(OFFICIAL):
            from ..processing.context import official_panel
            official_panel.ui_ik(self.layout, obj)
            return
        owner, sliders, eyes = _legacy_ik(obj)
        if rig_kit.is_kit(obj):
            from ..processing.context import kit_panel
            kit_panel.ui_limbs(self.layout, obj)
        else:
            col = self.layout.column(align=True)
            for key in sliders:
                col.prop(owner, '["%s"]' % key, text=key[3:].replace("_", " ").title(), slider=True, translate=False)
        if eyes:
            self.layout.prop(owner, '["eyes_aim"]', text="Eyes Aim", slider=True)


class FPMP_PT_RigHands(_RigSub, bpy.types.Panel):
    bl_label = "Held Props"
    bl_idname = "FPMP_PT_rig_hands"
    bl_options = {'DEFAULT_CLOSED'}
    bl_order = 2

    @classmethod
    def poll(cls, context):
        from ..processing.context import official_panel
        obj = _rig(context)
        return obj is not None and bool(obj.data.get(OFFICIAL)) and official_panel.has_hands(obj)

    def draw(self, context):
        from ..processing.context import official_panel
        official_panel.ui_hands(self.layout, _rig(context), split=True)


class FPMP_PT_RigSimulation(_RigSub, bpy.types.Panel):
    bl_label = "Dynamics"
    bl_idname = "FPMP_PT_rig_simulation"
    bl_order = 3

    @classmethod
    def poll(cls, context):
        obj = _rig(context)
        return obj is not None and bool(obj.data.get(dynamics_bake.KEY))

    def draw_header(self, context):
        obj = _rig(context)
        self.layout.enabled = dynamics_bake.baked(obj)        # nothing to play before a bake
        _switch(self.layout, obj, "fpmp_dynamics", FPMP_OT_DynamicsOn.bl_idname, "on", "")

    def draw(self, context):
        obj = _rig(context)
        # greyed with the header off, as Blender's panels; not before a bake (Simulate makes one) nor while Live plays
        self.layout.active = obj.fpmp_dynamics or obj.fpmp_dynamics_live or not dynamics_bake.baked(obj)
        col = _split(self.layout)
        col.prop(obj, "fpmp_dynamics_live")
        if dynamics_bake.states(obj):
            col.prop(obj, "fpmp_dynamics_state")
        self.layout.operator(FPMP_OT_DynamicsBake.bl_idname, text="Simulate")


classes = (FPMP_OT_RigSelectControls, FPMP_OT_RigResetPose, FPMP_OT_RigPick, FPMP_OT_KitRigOn, FPMP_OT_KitBake,
           FPMP_OT_KitSwitch, FPMP_OT_OfficialRigOn, FPMP_OT_DeformRigOn,
           FPMP_OT_DynamicsBake, FPMP_OT_DynamicsOn, FPMP_OT_DynamicsLive, FPMP_OT_DynamicsState, FPMP_OT_OfficialRigSwitch,
           FPMP_OT_OfficialRigSetting, FPMP_OT_OfficialRigBodyControls, FPMP_OT_OfficialRigBake,
           FPMP_OT_ConvertTastyToOfficial, FPMP_PT_CreatureRig,
           FPMP_PT_RigPicker, FPMP_PT_RigIK, FPMP_PT_RigHands, FPMP_PT_RigSimulation)


def register():
    rig_props.register()
    for c in classes:
        bpy.utils.register_class(c)
    face_board.register()
    from . import rig_pie
    rig_pie.register()
    from ..processing.context import dynamics_live, official_grip, wind
    wind.register()
    official_grip.register()
    dynamics_live.install()


def unregister():
    from ..processing.context import dynamics_live, official_grip, wind
    dynamics_live.uninstall()
    official_grip.unregister()
    wind.unregister()
    face_board.unregister()
    from . import rig_pie
    rig_pie.unregister()
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
    rig_props.unregister()
