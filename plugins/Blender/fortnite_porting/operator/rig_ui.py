"""The Rig panel (sidebar, Fortnite Porting tab) for the fork's rigs: show or hide each control group, select or
reset the controls, the rig's settings; the official rig's picker, IK / FK, held props, simulation and face in
sub-panels (and a Shift+Q pie: rig_pie)."""
import bpy

from ..processing.context import deform_rig, dynamics_bake, face_board, rig_style

VEHICLE_SETTINGS = (("auto_wheels", "Wheels Spin"), ("auto_steer", "Wheels Steer"), ("countersteer", "Counter-steer"),
                    ("suspension", "Body Follows Wheels"), ("lean", "Lean in Turns"))


def _rig(context):
    obj = context.active_object
    return obj if obj is not None and obj.type == 'ARMATURE' else None


class FPMP_OT_RigSelectControls(bpy.types.Operator):
    """Select every control of the rig (its Controls and Secondary groups)"""
    bl_idname = "fpmp.rig_select_controls"
    bl_label = "Select Controls"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _rig(context) is not None

    def execute(self, context):
        obj = _rig(context)
        # only what the user sees, as Blender's own Select All
        chosen = {b.name for b in rig_style.controls(obj)
                  if not b.hide and any(c.is_visible_effectively for c in b.bone.collections)}
        for bone in obj.pose.bones:
            bone.select = bone.name in chosen
        return {'FINISHED'}


class FPMP_OT_RigResetPose(bpy.types.Operator):
    """Put the rig's controls back at rest (never its mechanism or the game's bones)"""
    bl_idname = "fpmp.rig_reset_pose"
    bl_label = "Reset Pose"
    bl_options = {'REGISTER', 'UNDO'}
    scope: bpy.props.EnumProperty(items=[('SELECTED', "Selected", "The selected controls"),
                                         ('ALL', "All", "Every control")], default='SELECTED')

    @classmethod
    def poll(cls, context):
        return _rig(context) is not None

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


class FPMP_OT_RigPick(bpy.types.Operator):
    bl_idname = "fpmp.rig_pick"
    bl_label = "Pick"
    bl_options = {'REGISTER', 'UNDO'}
    names: bpy.props.StringProperty(description="The controls, comma separated")
    extend: bpy.props.BoolProperty(description="Add to the selection")

    @classmethod
    def description(cls, context, properties):
        return "Select %s (Shift: add to the selection)" % ", ".join(_names(properties.names))

    @classmethod
    def poll(cls, context):
        return _rig(context) is not None

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


class FPMP_OT_KitRigOn(bpy.types.Operator):
    bl_idname = "fpmp.kit_rig_on"
    bl_label = "Rig On/Off"
    bl_description = "The rig drives the bones, or the bones play their own animation"
    bl_options = {'REGISTER', 'UNDO'}
    on: bpy.props.BoolProperty(default=True)

    def execute(self, context):
        from ..processing.context import rig_kit
        rig_kit.set_on(_rig(context), self.on)
        return {'FINISHED'}


class FPMP_OT_KitBake(bpy.types.Operator):
    bl_idname = "fpmp.kit_bake"
    bl_label = "Bake to Controls"
    bl_description = "Key the controls from the animated bones over the scene range, then turn the rig on"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        from ..processing.context import rig_kit
        scene = context.scene
        frames = rig_kit.bake_to_controls(_rig(context), scene.frame_start, scene.frame_end)
        self.report({'INFO'}, "%d frames baked to the controls" % frames)
        return {'FINISHED'}


class FPMP_OT_KitSwitch(bpy.types.Operator):
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
        from ..processing.context import rig_kit
        for name in _names(self.limbs):
            rig_kit.switch(_rig(context), name, self.ik)
        return {'FINISHED'}


class FPMP_OT_OfficialRigOn(bpy.types.Operator):
    bl_idname = "fpmp.official_rig_on"
    bl_label = "Rig On/Off"
    bl_description = "The rig drives the bones, or the bones play their own animation"
    bl_options = {'REGISTER', 'UNDO'}
    on: bpy.props.BoolProperty(default=True)

    def execute(self, context):
        from ..processing.context import official_rig
        official_rig.set_on(_rig(context), self.on)
        return {'FINISHED'}


def _names(text):
    return [n for n in text.split(",") if n]


def _parts_text(names, cut):
    return ", ".join(n.replace(cut, "").replace("_", " ").title() for n in names)


class FPMP_OT_OfficialRigSwitch(bpy.types.Operator):
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
        from ..processing.context import official_rig
        for switch in _names(self.switch):
            official_rig.set_switch(_rig(context), switch, self.ik)
        return {'FINISHED'}


class FPMP_OT_OfficialRigSetting(bpy.types.Operator):
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
        from ..processing.context import official_rig
        for name in _names(self.name):
            official_rig.set_setting(_rig(context), name, self.value)
        return {'FINISHED'}


class FPMP_OT_OfficialRigBodyControls(bpy.types.Operator):
    bl_idname = "fpmp.official_rig_body_controls"
    bl_label = "Body Controls"
    bl_description = "Show or hide the fingers, toes, root, body, hips and neck controls"
    bl_options = {'REGISTER', 'UNDO'}
    show: bpy.props.BoolProperty(default=True)

    def execute(self, context):
        from ..processing.context import official_rig
        official_rig.set_body_controls(_rig(context), self.show)
        return {'FINISHED'}


class FPMP_OT_OfficialRigBake(bpy.types.Operator):
    bl_idname = "fpmp.official_rig_bake"
    bl_label = "Bake to Controls"
    bl_description = "Key the controls from the animated bones over the scene range, then turn the rig on"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        from ..processing.context import official_rig
        scene = context.scene
        frames = official_rig.bake_to_controls(_rig(context), scene.frame_start, scene.frame_end)
        self.report({'INFO'}, "%d frames baked to the controls" % frames)
        return {'FINISHED'}


class FPMP_OT_DeformRigOn(bpy.types.Operator):
    bl_idname = "fpmp.deform_rig_on"
    bl_label = "Correctives On/Off"
    bl_description = "Fortnite's deform correctives follow the pose, or stay at rest"
    bl_options = {'REGISTER', 'UNDO'}
    on: bpy.props.BoolProperty(default=True)

    def execute(self, context):
        from ..processing.context import deform_rig
        deform_rig.set_on(_rig(context), self.on)
        return {'FINISHED'}


class FPMP_OT_DynamicsBake(bpy.types.Operator):
    bl_idname = "fpmp.dynamics_bake"
    bl_label = "Simulate Dynamics"
    bl_description = "Run Fortnite's dynamic bones over the current animation and key them in a Dynamics layer"
    bl_options = {'REGISTER', 'UNDO'}
    rate: bpy.props.IntProperty(name="Steps per Second", default=60, min=10, max=240)

    @classmethod
    def poll(cls, context):
        obj = _rig(context)
        if obj is None or not obj.data.get(dynamics_bake.KEY):
            return False
        if dynamics_bake.frame_range(obj) is None:
            cls.poll_message_set("No animation on this armature")
            return False
        return True

    def execute(self, context):
        bones, frames = dynamics_bake.bake(_rig(context), self.rate)
        self.report({'INFO'}, "%d dynamic bones over %d frames" % (bones, frames))
        return {'FINISHED'}


class FPMP_OT_DynamicsOn(bpy.types.Operator):
    bl_idname = "fpmp.dynamics_on"
    bl_label = "Dynamics On/Off"
    bl_description = "Play the simulated dynamic bones, or the animation's own"
    bl_options = {'REGISTER', 'UNDO'}
    on: bpy.props.BoolProperty(default=True)

    def execute(self, context):
        dynamics_bake.set_on(_rig(context), self.on)
        return {'FINISHED'}


class FPMP_OT_DynamicsLive(bpy.types.Operator):
    bl_idname = "fpmp.dynamics_live"
    bl_label = "Live Dynamics"
    bl_description = "Simulate the dynamic bones as the animation plays forward (a jump restarts them); Simulate bakes the final result"
    bl_options = {'REGISTER', 'UNDO'}
    on: bpy.props.BoolProperty(default=True)

    @classmethod
    def poll(cls, context):
        obj = _rig(context)
        return obj is not None and bool(obj.data.get(dynamics_bake.KEY))

    def execute(self, context):
        from ..processing.context import dynamics_live
        if not dynamics_live.set_live(_rig(context), self.on):
            self.report({'WARNING'}, "No dynamic bones this armature can simulate")
            return {'CANCELLED'}
        return {'FINISHED'}


def _states(self, context):
    obj = _rig(context)
    return [(s, s.replace("_", " "), "") for s in (dynamics_bake.states(obj) if obj else [])] or [("OnGround_Standing", "On Ground Standing", "")]


class FPMP_OT_DynamicsState(bpy.types.Operator):
    bl_idname = "fpmp.dynamics_state"
    bl_label = "Movement State"
    bl_description = "The character state whose dynamics settings apply (gravity, how much body motion reaches the chains); Simulate again after changing it"
    bl_options = {'REGISTER', 'UNDO'}
    state: bpy.props.EnumProperty(items=_states)

    def execute(self, context):
        from ..processing.context import dynamics_live
        from ..processing.context.dynamics_runner import STATE
        obj = _rig(context)
        obj.data[STATE] = self.state
        if dynamics_live.is_live(obj):
            dynamics_live.set_live(obj, True)       # restart with the new state
        return {'FINISHED'}


class FPMP_PT_CreatureRig(bpy.types.Panel):
    bl_label = "Rig"
    bl_idname = "FPMP_PT_creature_rig"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Fortnite Porting"

    @classmethod
    def poll(cls, context):
        return _rig(context) is not None

    def draw(self, context):
        obj = _rig(context)
        data = obj.data
        col = self.layout.column(align=True)
        if data.get(OFFICIAL):          # first: it can be built on a Tasty-rigged armature
            from ..processing.context import official_panel
            official_panel.ui_top(col, obj)
            col.separator()
            _controls(col, data, rig_style.COLLECTIONS + (face_board.GROUP,))
            official_panel.ui_body(col, obj)
            return
        if data.get("is_tasty"):
            from ..processing.context import tasty_style
            _controls(col, data, tasty_style.SHOWN + tasty_style.HIDDEN + (face_board.GROUP,))
            return
        if not any(data.get(k) for k in ("is_vehicle_rig", "is_creature_rig", "is_lego_rig")):
            col.operator("fpmp.creature_rig")
            col.operator("fpmp.vehicle_rig")
            col.operator("fpmp.lego_rig")
            if data.get("fpmp_metahuman_board"):        # a plain skeleton with a face board
                col.separator()
                col.operator(FPMP_OT_RigSelectControls.bl_idname, text="Select Controls", icon='RESTRICT_SELECT_OFF')
            return
        from ..processing.context import kit_panel, rig_kit
        if rig_kit.is_kit(obj):
            kit_panel.ui_top(col, obj)
            col.separator()
        _controls(col, data, rig_style.COLLECTIONS + (rig_style.FK,) + rig_style.LEGACY)
        owner, path = rig_style.settings_owner(obj)
        col.separator()
        if data.get("is_vehicle_rig"):
            col.prop(obj, "fpmp_ground", text="Ground")
            for key, text in VEHICLE_SETTINGS:
                if key in owner:          # rigs made before a setting existed lack it
                    col.prop(owner, '["%s"]' % key, text=text, slider=True)
            return
        if data.get("is_lego_rig"):
            return
        if rig_kit.is_kit(obj):
            kit_panel.ui_limbs(col, obj)
        else:
            for key in sorted(k for k in owner.keys() if k.startswith("ik_")):
                col.prop(owner, '["%s"]' % key, text=key[3:], slider=True)
        if "eyes_aim" in owner:
            col.prop(owner, '["eyes_aim"]', text="Eyes Aim", slider=True)


OFFICIAL = "fpmp_official_rig"


def _controls(col, data, names):
    """The rig's control groups (show/hide), then select and reset."""
    grid = col.grid_flow(columns=2, align=True)
    for group in [c for c in data.collections if c.name in names]:
        grid.prop(group, "is_visible", text=group.name, toggle=True, icon='HIDE_OFF' if group.is_visible else 'HIDE_ON')
    row = col.row(align=True)
    row.operator(FPMP_OT_RigSelectControls.bl_idname, text="Select All", icon='RESTRICT_SELECT_OFF')
    row.operator(FPMP_OT_RigResetPose.bl_idname, text="Reset", icon='LOOP_BACK').scope = 'SELECTED'
    row.operator(FPMP_OT_RigResetPose.bl_idname, text="Reset All").scope = 'ALL'


class _RigSub:
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Fortnite Porting"
    bl_parent_id = FPMP_PT_CreatureRig.bl_idname


class FPMP_PT_RigPicker(_RigSub, bpy.types.Panel):
    bl_label = "Picker"
    bl_idname = "FPMP_PT_rig_picker"

    @classmethod
    def poll(cls, context):
        obj = _rig(context)
        return obj is not None and bool(obj.data.get(OFFICIAL))

    def draw(self, context):
        from ..processing.context import official_picker
        official_picker.ui_picker(self.layout, _rig(context))


class FPMP_PT_RigIK(_RigSub, bpy.types.Panel):
    bl_label = "IK / FK"
    bl_idname = "FPMP_PT_rig_ik"

    @classmethod
    def poll(cls, context):
        obj = _rig(context)
        return obj is not None and bool(obj.data.get(OFFICIAL))

    def draw(self, context):
        from ..processing.context import official_panel
        official_panel.ui_ik(self.layout, _rig(context))


class FPMP_PT_RigHands(_RigSub, bpy.types.Panel):
    bl_label = "Held Props"
    bl_idname = "FPMP_PT_rig_hands"

    @classmethod
    def poll(cls, context):
        from ..processing.context import official_panel
        obj = _rig(context)
        return obj is not None and bool(obj.data.get(OFFICIAL)) and official_panel.has_hands(obj)

    def draw(self, context):
        from ..processing.context import official_panel
        official_panel.ui_hands(self.layout, _rig(context))


class FPMP_PT_RigSimulation(_RigSub, bpy.types.Panel):
    bl_label = "Simulation"
    bl_idname = "FPMP_PT_rig_simulation"

    @classmethod
    def poll(cls, context):
        obj = _rig(context)
        return obj is not None and bool(obj.data.get(deform_rig.MARK) or obj.data.get(dynamics_bake.KEY))

    def draw(self, context):
        col = self.layout.column(align=True)
        deform_rig.ui(col, _rig(context))
        dynamics_bake.ui(col, _rig(context))


class FPMP_PT_RigFace(_RigSub, bpy.types.Panel):
    bl_label = "Face"
    bl_idname = "FPMP_PT_rig_face"

    @classmethod
    def poll(cls, context):
        obj = _rig(context)
        return obj is not None and (face_board.BOARD in obj.pose.bones or bool(obj.data.get("fpmp_metahuman_board")))

    def draw(self, context):
        face_board.ui(self.layout.column(align=True), _rig(context))


classes = (FPMP_OT_RigSelectControls, FPMP_OT_RigResetPose, FPMP_OT_RigPick, FPMP_OT_KitRigOn, FPMP_OT_KitBake,
           FPMP_OT_KitSwitch, FPMP_OT_OfficialRigOn, FPMP_OT_DeformRigOn,
           FPMP_OT_DynamicsBake, FPMP_OT_DynamicsOn, FPMP_OT_DynamicsLive, FPMP_OT_DynamicsState, FPMP_OT_OfficialRigSwitch,
           FPMP_OT_OfficialRigSetting, FPMP_OT_OfficialRigBodyControls, FPMP_OT_OfficialRigBake, FPMP_PT_CreatureRig,
           FPMP_PT_RigPicker, FPMP_PT_RigIK, FPMP_PT_RigHands, FPMP_PT_RigSimulation, FPMP_PT_RigFace)


def register():
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
