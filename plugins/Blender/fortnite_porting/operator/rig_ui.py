"""The Rig panel (sidebar, Fortnite Porting tab) for the fork's rigs: show or hide each control group, select or
reset the controls, the rig's settings."""
import bpy

from ..processing.context import face_board, rig_style

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
                  if not b.hide and any(c.is_visible for c in b.bone.collections)}
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


class FPMP_OT_OfficialRigSwitch(bpy.types.Operator):
    bl_idname = "fpmp.official_rig_switch"
    bl_label = "FK/IK"
    bl_description = "Switch this part between FK and IK, keeping its pose"
    bl_options = {'REGISTER', 'UNDO'}
    switch: bpy.props.StringProperty()
    ik: bpy.props.BoolProperty()

    def execute(self, context):
        from ..processing.context import official_rig
        official_rig.set_switch(_rig(context), self.switch, self.ik)
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
        if data.get("is_tasty"):
            from ..processing.context import tasty_style
            grid = col.grid_flow(columns=2, align=True)
            for group in [c for c in data.collections if c.name in tasty_style.SHOWN + tasty_style.HIDDEN + (face_board.GROUP,)]:
                grid.prop(group, "is_visible", text=group.name, toggle=True, icon='HIDE_OFF' if group.is_visible else 'HIDE_ON')
            col.operator(FPMP_OT_RigSelectControls.bl_idname, text="Select Controls")
            row = col.row(align=True)
            row.operator(FPMP_OT_RigResetPose.bl_idname, text="Reset Selected").scope = 'SELECTED'
            row.operator(FPMP_OT_RigResetPose.bl_idname, text="Reset All").scope = 'ALL'
            face_board.ui(col, obj)
            return
        if data.get("fpmp_official_rig"):
            from ..processing.context import official_rig
            grid = col.grid_flow(columns=2, align=True)
            for group in [c for c in data.collections if c.name in rig_style.COLLECTIONS + (face_board.GROUP,)]:
                grid.prop(group, "is_visible", text=group.name, toggle=True, icon='HIDE_OFF' if group.is_visible else 'HIDE_ON')
            col.operator(FPMP_OT_RigSelectControls.bl_idname, text="Select Controls")
            row = col.row(align=True)
            row.operator(FPMP_OT_RigResetPose.bl_idname, text="Reset Selected").scope = 'SELECTED'
            row.operator(FPMP_OT_RigResetPose.bl_idname, text="Reset All").scope = 'ALL'
            official_rig.ui(col, obj)
            face_board.ui(col, obj)
            return
        if not any(data.get(k) for k in ("is_vehicle_rig", "is_creature_rig", "is_lego_rig")):
            col.operator("fpmp.creature_rig")
            col.operator("fpmp.vehicle_rig")
            col.operator("fpmp.lego_rig")
            if data.get("fpmp_metahuman_board"):        # a plain skeleton with a face board
                col.separator()
                col.operator(FPMP_OT_RigSelectControls.bl_idname, text="Select Controls")
                face_board.ui(col, obj)
            return
        groups = [c for c in data.collections if c.name in rig_style.COLLECTIONS + (rig_style.FK,) + rig_style.LEGACY]
        grid = col.grid_flow(columns=2, align=True)
        for group in groups:
            grid.prop(group, "is_visible", text=group.name, toggle=True, icon='HIDE_OFF' if group.is_visible else 'HIDE_ON')
        row = col.row(align=True)
        row.operator(FPMP_OT_RigSelectControls.bl_idname, text="Select Controls")
        row = col.row(align=True)
        row.operator(FPMP_OT_RigResetPose.bl_idname, text="Reset Selected").scope = 'SELECTED'
        row.operator(FPMP_OT_RigResetPose.bl_idname, text="Reset All").scope = 'ALL'
        owner, path = rig_style.settings_owner(obj)
        col.separator()
        if data.get("is_vehicle_rig"):
            col.prop(obj, "fpmp_ground", text="Ground")
            for key, text in VEHICLE_SETTINGS:
                if key in owner:          # rigs made before a setting existed lack it
                    col.prop(owner, '["%s"]' % key, text=text, slider=True)
            return
        if data.get("is_lego_rig"):
            face_board.ui(col, obj)
            return
        for key in sorted(k for k in owner.keys() if k.startswith("ik_")):
            col.prop(owner, '["%s"]' % key, text=key[3:], slider=True)
        if "eyes_aim" in owner:
            col.prop(owner, '["eyes_aim"]', text="Eyes Aim", slider=True)
        face_board.ui(col, obj)


classes = (FPMP_OT_RigSelectControls, FPMP_OT_RigResetPose, FPMP_OT_OfficialRigOn, FPMP_OT_OfficialRigSwitch,
           FPMP_OT_OfficialRigBake, FPMP_PT_CreatureRig)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    face_board.register()


def unregister():
    face_board.unregister()
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
