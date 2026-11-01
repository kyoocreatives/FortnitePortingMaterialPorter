"""The Map tab's input over its view, through the editor's "Fortnite" keymap: click toggles a cell, drag paints cells
(alt: removes), shift-drag boxes, the wheel zooms about the mouse, middle mouse pans, Home fits; the mouse highlights
the cell it's over (the shell's hover hook). Then what the columns' buttons run: open a map, a place's cells, all/none,
export/cancel, data layers, images."""
import bpy

from . import host, tabs
from .maps_tab import MAPS, can_export, export_options, on_map, opened, places
from .state import STATE, client, tag_redraw


def _view(context):
    """The area's map view when the Map tab shows a loaded map, else None."""
    if not tabs.is_tab(context, "map") or not opened(context.window_manager):
        return None
    return MAPS["views"].get(context.area.as_pointer())


def _cells(context):
    return on_map(opened(context.window_manager))


class FP_OT_map_select(bpy.types.Operator):
    bl_idname = "fp.map_select"
    bl_label = "Pick Cells"
    bl_options = {'INTERNAL'}
    remove: bpy.props.BoolProperty(options={'SKIP_SAVE'})      # SKIP_SAVE: a plain click must not reuse alt's

    def invoke(self, context, event):
        view = _view(context)
        if view is None:
            return {'PASS_THROUGH'}
        self.view, self.cells = view, _cells(context)
        self.start = view.cell_at(self.cells, event.mouse_region_x, event.mouse_region_y)
        self.last_xy, self.painted = (event.mouse_region_x, event.mouse_region_y), False
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def _paint(self, cell):
        if cell is None:
            return
        if self.remove:
            MAPS["selected"].discard(cell)
        else:
            MAPS["selected"].add(cell)

    def modal(self, context, event):
        x, y = event.mouse_region_x, event.mouse_region_y
        if event.type == 'MOUSEMOVE':
            # every cell under the stroke, also when the mouse jumps
            (lx, ly), step = self.last_xy, max(2.0, min(c["size"] for c in self.cells) * self.view.scale / 3)
            n = max(1, int(max(abs(x - lx), abs(y - ly)) / step))
            for i in range(1, n + 1):
                cell = self.view.cell_at(self.cells, lx + (x - lx) * i / n, ly + (y - ly) * i / n)
                if cell != self.start or self.painted:
                    if not self.painted:
                        self.painted = True
                        self._paint(self.start)
                    self._paint(cell)
            self.last_xy = (x, y)
            context.region.tag_redraw()
        elif event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            if not self.painted and self.start is not None:
                if self.remove or self.start in MAPS["selected"]:
                    MAPS["selected"].discard(self.start)
                else:
                    MAPS["selected"].add(self.start)
            tag_redraw()
            return {'FINISHED'}
        elif event.type in {'ESC', 'RIGHTMOUSE'}:
            return {'CANCELLED'}
        return {'RUNNING_MODAL'}


class FP_OT_map_box(bpy.types.Operator):
    bl_idname = "fp.map_box"
    bl_label = "Box Pick Cells"
    bl_options = {'INTERNAL'}

    def invoke(self, context, event):
        view = _view(context)
        if view is None:
            return {'PASS_THROUGH'}
        self.view, self.key = view, context.area.as_pointer()
        self.start = (event.mouse_region_x, event.mouse_region_y)
        MAPS["box"][self.key] = (self.start, self.start)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        end = (event.mouse_region_x, event.mouse_region_y)
        if event.type == 'MOUSEMOVE':
            MAPS["box"][self.key] = (self.start, end)
            context.region.tag_redraw()
        elif event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            MAPS["selected"].update(self.view.cells_in(_cells(context), *self.start, *end))
            MAPS["box"].pop(self.key, None)
            tag_redraw()
            return {'FINISHED'}
        elif event.type in {'ESC', 'RIGHTMOUSE'}:
            MAPS["box"].pop(self.key, None)
            context.region.tag_redraw()
            return {'CANCELLED'}
        return {'RUNNING_MODAL'}


class FP_OT_map_pan(bpy.types.Operator):
    bl_idname = "fp.map_pan"
    bl_label = "Pan Map"
    bl_options = {'INTERNAL'}

    def invoke(self, context, event):
        self.view = _view(context)
        if self.view is None:
            return {'PASS_THROUGH'}
        self.last = (event.mouse_region_x, event.mouse_region_y)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type == 'MOUSEMOVE':
            x, y = event.mouse_region_x, event.mouse_region_y
            self.view.pan(x - self.last[0], y - self.last[1])
            self.last = (x, y)
            context.region.tag_redraw()
        elif event.type == 'MIDDLEMOUSE' and event.value == 'RELEASE':
            return {'FINISHED'}
        return {'RUNNING_MODAL'}


class FP_OT_map_zoom(bpy.types.Operator):
    bl_idname = "fp.map_zoom"
    bl_label = "Zoom Map"
    bl_options = {'INTERNAL'}
    steps: bpy.props.IntProperty(default=1)

    def invoke(self, context, event):
        view = _view(context)
        if view is None:
            return {'PASS_THROUGH'}
        view.zoom_at(event.mouse_region_x, event.mouse_region_y, self.steps)
        context.region.tag_redraw()
        return {'FINISHED'}


def hover(context, x, y):
    """The cell under (x, y) (None: off the view) outlined; True when that changed (the shell redraws the view)."""
    view = _view(context)
    cell = view.cell_at(_cells(context), x, y) if view is not None and x is not None else None
    key = context.area.as_pointer()
    if cell == MAPS["hover"].get(key):
        return False
    MAPS["hover"][key] = cell
    return True


class FP_OT_map_fit(bpy.types.Operator):
    """Show the whole map (Home)"""
    bl_idname = "fp.map_fit"
    bl_label = "Fit Map"

    @classmethod
    def poll(cls, context):
        return tabs.is_tab(context, "map")

    def execute(self, context):
        for area in context.screen.areas:
            MAPS["views"].pop(area.as_pointer(), None)
        tag_redraw()
        return {'FINISHED'}


class FP_OT_map_open(bpy.types.Operator):
    """Open this map"""
    bl_idname = "fp.map_open"
    bl_label = "Open Map"
    bl_options = {'INTERNAL'}
    id: bpy.props.StringProperty()

    def execute(self, context):
        if context.window_manager.fp_map_id != self.id:
            context.window_manager.fp_map_id = self.id
            MAPS["selected"].clear()
            MAPS["layers"].clear()
            MAPS["open_at"] = 0.0
        elif (MAPS["open"] or {}).get("error"):        # clicked again after an error: ask again
            MAPS["open"], MAPS["open_at"] = None, 0.0
        tag_redraw()
        return {'FINISHED'}


class FP_OT_map_poi(bpy.types.Operator):
    """Pick the cells around this place (again: drop them)"""
    bl_idname = "fp.map_poi"
    bl_label = "Place"
    bl_options = {'INTERNAL'}
    name: bpy.props.StringProperty()        # as listed: "Override Console 2" is the second of that name

    def execute(self, context):
        got = opened(context.window_manager)
        rows = [row for kind in places(got).values() for row in kind] if got else []
        cells = next((set(c) for label, c in rows if label == self.name), None)
        if cells is None:
            self.report({'WARNING'}, "No place named %s on this map" % self.name)
            return {'CANCELLED'}
        if cells <= MAPS["selected"]:
            MAPS["selected"] -= cells
        else:
            MAPS["selected"] |= cells
        tag_redraw()
        return {'FINISHED'}


class FP_OT_map_all(bpy.types.Operator):
    """Pick every cell"""
    bl_idname = "fp.map_all"
    bl_label = "All Cells"

    def execute(self, context):
        got = opened(context.window_manager)
        MAPS["selected"] = {c["id"] for c in on_map(got)} if got else set()
        tag_redraw()
        return {'FINISHED'}


class FP_OT_map_clear(bpy.types.Operator):
    """Pick no cell"""
    bl_idname = "fp.map_clear"
    bl_label = "No Cells"

    def execute(self, context):
        MAPS["selected"] = set()
        tag_redraw()
        return {'FINISHED'}


class FP_OT_map_export(bpy.types.Operator):
    """Export the terrain (Include Terrain, or Terrain Under Cells) and the picked cells one by one with these flags; each lands in Blender as it finishes"""
    bl_idname = "fp.map_export"
    bl_label = "Export Cells"

    @classmethod
    def poll(cls, context):
        return can_export(context.window_manager)

    def execute(self, context):
        MAPS["queue"].start(context.window_manager.fp_map_id, sorted(MAPS["selected"]), export_options(context.window_manager))
        STATE["error"] = None
        tag_redraw()
        return {'FINISHED'}


class FP_OT_map_cancel(bpy.types.Operator):
    """Stop the cell exporting now and drop the rest (cells already in Blender stay)"""
    bl_idname = "fp.map_cancel"
    bl_label = "Cancel Export"

    @classmethod
    def poll(cls, context):
        return MAPS["queue"].busy

    def execute(self, context):
        MAPS["queue"].cancel()
        client().request("maps-cancel", lambda result: None, run=MAPS["queue"].options.get("run", "0"))
        tag_redraw()
        return {'FINISHED'}


class FP_OT_map_layer(bpy.types.Operator):
    """Export this data layer's actors too (events that start unloaded)"""
    bl_idname = "fp.map_layer"
    bl_label = "Data Layer"
    bl_options = {'INTERNAL'}
    name: bpy.props.StringProperty()

    def execute(self, context):
        MAPS["layers"].symmetric_difference_update({self.name})
        tag_redraw()
        return {'FINISHED'}


class FP_OT_map_images(bpy.types.Operator):
    """Bring the map's images into Blender (minimap, or the landscape's height, normal or weight maps)"""
    bl_idname = "fp.map_images"
    bl_label = "Map Images"
    kind: bpy.props.StringProperty(default="minimap")

    @classmethod
    def poll(cls, context):
        return opened(context.window_manager) is not None and not STATE["busy"]

    def execute(self, context):
        wm, kind = context.window_manager, self.kind
        name = next((m["name"] for m in (MAPS["list"] or {}).get("maps") or [] if m["id"] == wm.fp_map_id), "Map")
        STATE["busy"] = "Writing %s images..." % kind

        def done(result):
            STATE["busy"] = None
            if isinstance(result, Exception):
                STATE["error"] = "Images: %s" % result
            else:
                for f in result["files"]:
                    image = bpy.data.images.load(f["path"], check_existing=True)
                    stem = f["path"].replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0]
                    image.name = ("Minimap " + name) if f["kind"] == "minimap" else stem
                    if f["kind"] != "minimap":
                        image.colorspace_settings.name = 'Non-Color'
                if not result["files"]:
                    STATE["error"] = "No %s image for this map" % kind
            tag_redraw()
        client().request("maps-images", done, timeout=1800, id=wm.fp_map_id, kinds=kind)
        return {'FINISHED'}


classes = (FP_OT_map_select, FP_OT_map_box, FP_OT_map_pan, FP_OT_map_zoom, FP_OT_map_fit,
           FP_OT_map_open, FP_OT_map_poi, FP_OT_map_all, FP_OT_map_clear, FP_OT_map_export, FP_OT_map_cancel,
           FP_OT_map_layer, FP_OT_map_images)
_keymaps = []


def register():
    for c in classes:
        bpy.utils.register_class(c)
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc is None:
        return
    add = host.adder(kc, _keymaps)
    items = [add(FP_OT_map_select.bl_idname, 'LEFTMOUSE', 'PRESS'), add(FP_OT_map_box.bl_idname, 'LEFTMOUSE', 'PRESS', shift=True),
             add(FP_OT_map_pan.bl_idname, 'MIDDLEMOUSE', 'PRESS'), add(FP_OT_map_fit.bl_idname, 'HOME', 'PRESS')]
    k = add(FP_OT_map_select.bl_idname, 'LEFTMOUSE', 'PRESS', alt=True)
    k.properties.remove = True
    items.append(k)
    for key, steps in (('WHEELUPMOUSE', 1), ('WHEELDOWNMOUSE', -1)):
        k = add(FP_OT_map_zoom.bl_idname, key, 'PRESS')
        k.properties.steps = steps
        items.append(k)


def unregister():
    for km, k in _keymaps:
        try:
            km.keymap_items.remove(k)
        except (ReferenceError, RuntimeError):
            pass
    _keymaps.clear()
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
