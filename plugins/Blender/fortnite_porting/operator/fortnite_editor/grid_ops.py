"""The grid's input, through the editor's "Fortnite" keymap: click picks, double-click imports, the wheel scrolls,
ctrl+wheel resizes the tiles, page keys jump, the mouse highlights what it's over."""
import bpy

from . import browse_ops, grid_menu, host, tabs
from .state import STATE, tag_redraw

TILE_MIN, TILE_MAX, TILE_STEP = 64, 256, 16


def hit_item(area_key, x, y):
    """The item under (x, y) in the grid that area drew last, else None."""
    grid, items = STATE["grids"].get(area_key, (None, None))
    i = grid.hit(x, y) if grid is not None else None
    return items[i] if i is not None else None


def _grid(context):
    """The grid this area drew, None on other tabs (their input passes through to their own panels)."""
    if not tabs.is_tab(context, "cosmetics"):
        return None
    return STATE["grids"].get(context.area.as_pointer(), (None, None))[0]


class FP_OT_grid_pick(bpy.types.Operator):
    """Pick the asset under the mouse (ctrl: add or remove it, shift: the run from the last pick); Import takes them all"""
    bl_idname = "fp.grid_pick"
    bl_label = "Pick Asset"
    bl_options = {'INTERNAL'}
    extend: bpy.props.BoolProperty(options={'SKIP_SAVE'})
    range: bpy.props.BoolProperty(options={'SKIP_SAVE'})

    def invoke(self, context, event):
        item = hit_item(context.area.as_pointer(), event.mouse_region_x, event.mouse_region_y) if _grid(context) else None
        if item is None:
            return {'PASS_THROUGH'}
        browse_ops.pick(context.window_manager, item["path"], self.extend, self.range)
        tag_redraw()
        return {'FINISHED'}


class FP_OT_grid_open(bpy.types.Operator):
    """Import the asset under the mouse"""
    bl_idname = "fp.grid_open"
    bl_label = "Import Asset"
    bl_options = {'INTERNAL'}

    def invoke(self, context, event):
        item = hit_item(context.area.as_pointer(), event.mouse_region_x, event.mouse_region_y) if _grid(context) else None
        if item is None:
            return {'PASS_THROUGH'}
        context.window_manager.fp_browse_selected = item["path"]
        STATE["picked"] = []
        if hasattr(bpy.ops.fp, "browse_import"):
            bpy.ops.fp.browse_import()
        return {'FINISHED'}


class FP_OT_grid_scroll(bpy.types.Operator):
    """Scroll the grid"""
    bl_idname = "fp.grid_scroll"
    bl_label = "Scroll"
    bl_options = {'INTERNAL'}
    rows: bpy.props.FloatProperty(default=1)

    def invoke(self, context, event):
        grid = _grid(context)
        if grid is None:
            return {'PASS_THROUGH'}
        wm = context.window_manager
        step = (grid.tile_h + grid.gap) * self.rows
        if abs(self.rows) >= 4:     # page keys: a screenful
            step = grid.height * (1 if self.rows > 0 else -1)
        wm.fp_browse_scroll = min(max(0.0, grid.scroll + step), grid.max_scroll)
        tag_redraw()
        return {'FINISHED'}


class FP_OT_grid_zoom(bpy.types.Operator):
    """Bigger or smaller tiles"""
    bl_idname = "fp.grid_zoom"
    bl_label = "Tile Size"
    bl_options = {'INTERNAL'}
    step: bpy.props.IntProperty(default=1)

    def invoke(self, context, event):
        if _grid(context) is None:
            return {'PASS_THROUGH'}     # ctrl+wheel on a form tab's field is the field's
        wm = context.window_manager
        wm.fp_browse_tile = min(max(TILE_MIN, wm.fp_browse_tile + self.step * TILE_STEP), TILE_MAX)
        tag_redraw()
        return {'FINISHED'}


def hover(context, x, y):
    """The tile under (x, y) (None: off the grid) highlighted; True when that changed (the shell redraws)."""
    grid = _grid(context)
    i = grid.hit(x, y) if grid is not None and x is not None else None
    key = context.area.as_pointer()
    if i == STATE["hover"].get(key):
        return False
    STATE["hover"][key] = i
    return True


classes = (FP_OT_grid_pick, FP_OT_grid_open, FP_OT_grid_scroll, FP_OT_grid_zoom)
_keymaps = []


def register():
    for c in classes:
        bpy.utils.register_class(c)
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc is None:
        return
    add = host.adder(kc, _keymaps)
    items = [add(FP_OT_grid_pick.bl_idname, 'LEFTMOUSE', 'PRESS'),
             add(FP_OT_grid_open.bl_idname, 'LEFTMOUSE', 'DOUBLE_CLICK')]
    add(FP_OT_grid_pick.bl_idname, 'LEFTMOUSE', 'PRESS', ctrl=True).properties.extend = True
    add(FP_OT_grid_pick.bl_idname, 'LEFTMOUSE', 'PRESS', shift=True).properties.range = True
    for key, rows, ctrl in (('WHEELDOWNMOUSE', 1, False), ('WHEELUPMOUSE', -1, False), ('PAGE_DOWN', 4, False),
                            ('PAGE_UP', -4, False)):
        kmi = add(FP_OT_grid_scroll.bl_idname, key, 'PRESS', ctrl=ctrl)
        kmi.properties.rows = rows
        items.append(kmi)
    for key, step in (('WHEELUPMOUSE', 1), ('WHEELDOWNMOUSE', -1)):
        kmi = add(FP_OT_grid_zoom.bl_idname, key, 'PRESS', ctrl=True)
        kmi.properties.step = step
        items.append(kmi)
    grid_menu.register()        # the tiles' right-click menu


def unregister():
    grid_menu.unregister()
    for km, kmi in _keymaps:
        try:
            km.keymap_items.remove(kmi)
        except (ReferenceError, RuntimeError):
            pass
    _keymaps.clear()
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
