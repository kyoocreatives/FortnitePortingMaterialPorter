"""The one-time operators in menus instead of panel rows: the Materials panel's specials, and Object > Fortnite
Porting (where F3 finds them)."""
import bpy


class FPMP_MT_ExactSpecials(bpy.types.Menu):
    bl_label = "Material Specials"
    bl_idname = "FPMP_MT_exact_specials"

    def draw(self, context):
        from . import fixer
        layout = self.layout
        layout.operator("fpmp.remove_wrap", text="Remove Wrap from Selected")
        layout.operator("fpmp.road_from_curve")
        layout.operator("fpmp.restore_landscape")
        layout.separator()
        layout.operator("fpmp.fix_materials", text="Fix FP Materials...").ask = True
        if fixer.islands_available():
            layout.operator("fpmp.recover_island", text="Recover Island Textures...").ask = True


class FPMP_MT_Object(bpy.types.Menu):
    bl_label = "Fortnite Porting"
    bl_idname = "FPMP_MT_object"

    def draw(self, context):
        layout = self.layout
        layout.operator("fpmp.convert_exact").scope = 'SELECTED'
        layout.operator("fpmp.remove_wrap")
        layout.operator("fpmp.replay_effect")
        layout.separator()
        layout.operator("fpmp.road_from_curve")
        layout.operator("fpmp.restore_landscape")
        layout.operator("fpmp.fix_materials", text="Fix FP Materials...").ask = True
        layout.separator()
        layout.operator("fpmp.official_rig")
        layout.operator("fpmp.creature_rig")
        layout.operator("fpmp.vehicle_rig")
        layout.operator("fpmp.lego_rig")
        layout.operator("fpmp.convert_tasty_to_official")


def _object_menu(self, context):
    self.layout.separator()
    self.layout.menu(FPMP_MT_Object.bl_idname)


classes = (FPMP_MT_ExactSpecials, FPMP_MT_Object)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.VIEW3D_MT_object.append(_object_menu)


def unregister():
    bpy.types.VIEW3D_MT_object.remove(_object_menu)
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
