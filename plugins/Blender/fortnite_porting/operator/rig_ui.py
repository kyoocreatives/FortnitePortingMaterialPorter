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
                  if not b.bone.hide and any(c.is_visible for c in b.bone.collections)}
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
        if not any(data.get(k) for k in ("is_vehicle_rig", "is_creature_rig", "is_lego_rig")):
            col.operator("fpmp.creature_rig")
            col.operator("fpmp.vehicle_rig")
            col.operator("fpmp.lego_rig")
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
            if face_board.BOARD in obj.pose.bones:
                col.prop(obj, "fpmp_face_board", text="Face Board", toggle=True)
                faces = [bpy.data.materials.get(n) for n in obj.get("fpmp_face_board_materials", [])]
                group = next((n for m in faces if m and m.node_tree for n in m.node_tree.nodes if n.type == 'GROUP'), None)
                if group is not None:
                    for _, name, _ in face_board.POSES:
                        if name in group.inputs:
                            col.label(text="%s: %d" % (name.replace("Pose", ""), round(group.inputs[name].default_value)))
            return
        for key in sorted(k for k in owner.keys() if k.startswith("ik_")):
            col.prop(owner, '["%s"]' % key, text=key[3:], slider=True)
        if "eyes_aim" in owner:
            col.prop(owner, '["eyes_aim"]', text="Eyes Aim", slider=True)


classes = (FPMP_OT_RigSelectControls, FPMP_OT_RigResetPose, FPMP_PT_CreatureRig)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    face_board.register()


def unregister():
    face_board.unregister()
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
