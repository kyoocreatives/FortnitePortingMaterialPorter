"""Landscape Layers panel: a landscape mesh's paint layers (three to a colour attribute, material_porter.landscape_layers),
each with the channel it lives in and a Paint button that picks that attribute and the brush colour painting it."""
import bpy

CHANNEL_COLORS = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))


def _brush_color(context, rgb):
    """The vertex paint brush colour (the unified colour too, whichever this Blender paints with)."""
    ts = context.tool_settings
    for settings in (getattr(ts.vertex_paint, "unified_paint_settings", None), getattr(ts, "unified_paint_settings", None)):
        if settings is not None and hasattr(settings, "color"):
            settings.color = rgb
    brush = getattr(ts.vertex_paint, "brush", None)
    if brush is not None:
        try:
            brush.color = rgb
        except AttributeError:
            pass        # an asset brush not made local: the unified colour paints


class FPMP_OT_PaintLandscapeLayer(bpy.types.Operator):
    """Paint this layer: its colour attribute made active, Vertex Paint on, the brush set to its channel (red, green or
    blue; painting it lowers the attribute's other two layers, as landscape layers blend)"""
    bl_idname = "fpmp.paint_landscape_layer"
    bl_label = "Paint Layer"
    bl_options = {'REGISTER', 'UNDO'}

    layer: bpy.props.StringProperty()

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'MESH' and bool(obj.get("mp_landscape"))

    def execute(self, context):
        from ..material_porter import landscape_layers
        obj = context.active_object
        where = landscape_layers.table(obj).get(self.layer)
        if where is None:
            self.report({'WARNING'}, "%s isn't a packed layer of %s" % (self.layer, obj.name))
            return {'CANCELLED'}
        attribute, channel = where
        mesh = obj.data
        mesh.color_attributes.active_color = mesh.color_attributes[attribute]
        if obj.mode != 'VERTEX_PAINT':
            bpy.ops.object.mode_set(mode='VERTEX_PAINT')
        _brush_color(context, CHANNEL_COLORS[channel])
        self.report({'INFO'}, "Painting %s (%s %s)" % (self.layer, attribute, landscape_layers.CHANNELS[channel]))
        return {'FINISHED'}


class FPMP_OT_UpdateLandscapeMaterial(bpy.types.Operator):
    """Rebuild the selected landscapes' exact materials for the layers painted now (unpainted layers stay out of the
    material; the FP app must be running)"""
    bl_idname = "fpmp.update_landscape_material"
    bl_label = "Update Material"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return any(o.type == 'MESH' and o.get("mp_landscape") for o in context.selected_objects)

    def execute(self, context):
        import json
        import types
        from ..material_porter import build, hook
        if context.active_object is not None and context.active_object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        changed, skipped = [], []
        for o in [o for o in context.selected_objects if o.type == 'MESH' and o.get("mp_landscape")]:
            for slot in o.material_slots:
                mat = slot.material
                path = mat.get(build.KEY_PATH) if mat is not None else None
                if not path:
                    continue
                job = types.SimpleNamespace(options={"RimLight": bool(mat.get("MPRimLight"))},
                                            type=types.SimpleNamespace(name="MESH"))
                data = {"Path": path, "Name": mat.get("OriginalName") or mat.name}
                if overlay := mat.get(hook.KEY_OVERLAY):
                    data["MPOverlay"] = json.loads(overlay)
                new = hook.build_exact(job, data, obj=o)
                if new is None:
                    skipped.append(mat.name)
                    continue
                for key in ("Hash", "OriginalName", "MPRimLight", "MPSubsurface"):
                    if key in mat and key not in new:
                        new[key] = mat[key]
                if new is not mat:
                    slot.material = new
                    changed.append(new.name)
        if skipped:
            self.report({'WARNING'}, "Not rebuilt (is the FP app running?): %s" % ", ".join(sorted(set(skipped))))
        else:
            self.report({'INFO'}, ", ".join(sorted(set(changed))) or "Already up to date")
        return {'FINISHED'}


class FPMP_PT_LandscapeLayers(bpy.types.Panel):
    bl_label = "Landscape Layers"
    bl_idname = "FPMP_PT_landscape_layers"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Fortnite Porting"

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'MESH' and bool(obj.get("mp_landscape")) and bool(obj.get("mp_layers"))

    def draw(self, context):
        from ..material_porter import landscape_layers
        obj = context.active_object
        packed = landscape_layers.table(obj)
        active = obj.data.color_attributes.active_color
        col = self.layout.column(align=True)
        for layer in sorted(packed, key=lambda l: (packed[l][0], packed[l][1])):
            attribute, channel = packed[layer]
            row = col.row(align=True)
            row.label(text=layer)
            row.label(text="%s %s" % (attribute, landscape_layers.CHANNELS[channel]))
            painting = obj.mode == 'VERTEX_PAINT' and active is not None and active.name == attribute
            row.operator(FPMP_OT_PaintLandscapeLayer.bl_idname, text="", icon='BRUSH_DATA', depress=painting).layer = layer
        self.layout.operator(FPMP_OT_UpdateLandscapeMaterial.bl_idname, icon='FILE_REFRESH')


classes = (FPMP_OT_PaintLandscapeLayer, FPMP_OT_UpdateLandscapeMaterial, FPMP_PT_LandscapeLayers)


def register():
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
