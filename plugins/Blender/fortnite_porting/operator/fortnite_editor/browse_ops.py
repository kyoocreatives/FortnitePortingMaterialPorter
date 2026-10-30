"""The Assets tab's FP tools: its Filters panel, Favorites, Random, picking several assets (ctrl / shift-click) and
importing them one after another, and the keyboard (arrows move the pick, Enter imports)."""
import random

import bpy

from . import host, tabs
from .state import STATE, client, filter_groups, active_filters, items, picked, tag_redraw


def _grid(context):
    if not tabs.is_tab(context, "cosmetics"):
        return None
    return STATE["grids"].get(context.area.as_pointer(), (None, None))[0]


def show(context, path):
    """Pick `path` and scroll the grid to it."""
    wm = context.window_manager
    wm.fp_browse_selected = path
    shown = items(wm)
    grid = STATE["grids"].get(context.area.as_pointer(), (None, None))[0] if context.area else None
    i = next((n for n, it in enumerate(shown) if it["path"] == path), None)
    if grid is not None and i is not None:
        row = i // grid.columns
        top = row * (grid.tile_h + grid.gap)
        if top < grid.scroll:
            wm.fp_browse_scroll = top
        elif top + grid.tile_h + 2 * grid.gap > grid.scroll + grid.height:
            wm.fp_browse_scroll = top + grid.tile_h + 2 * grid.gap - grid.height
    tag_redraw()


def pick(wm, path, extend=False, range_=False):
    """A click's pick: alone, added / removed (ctrl), or the run from the active one (shift)."""
    shown = [i["path"] for i in items(wm)]
    if range_ and wm.fp_browse_selected in shown and path in shown:
        a, b = sorted((shown.index(wm.fp_browse_selected), shown.index(path)))
        STATE["picked"] = list(dict.fromkeys(picked(wm) + shown[a:b + 1]))
    elif extend:
        now = picked(wm)
        if path in now and len(now) > 1:
            now.remove(path)
            STATE["picked"] = now
            wm.fp_browse_selected = now[-1]
            return
        STATE["picked"] = now + [path]
    else:
        STATE["picked"] = []
    wm.fp_browse_selected = path


def toggle_filter(wm, title):
    on = active_filters(wm)
    on = [f for f in on if f != title] if title in on else on + [title]
    wm.fp_browse_filters, wm.fp_browse_scroll = "|".join(on), 0.0
    tag_redraw()


def clear_filters(wm):
    wm.fp_browse_filters = ""
    tag_redraw()


class FP_OT_browse_filter(bpy.types.Operator):
    """Show only the assets this filter keeps (FP's Filters)"""
    bl_idname = "fp.browse_filter"
    bl_label = "Filter"
    bl_options = {'INTERNAL'}
    title: bpy.props.StringProperty()

    def execute(self, context):
        toggle_filter(context.window_manager, self.title)
        return {'FINISHED'}


class FP_OT_browse_filters_clear(bpy.types.Operator):
    """Show every asset again"""
    bl_idname = "fp.browse_filters_clear"
    bl_label = "Clear Filters"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        clear_filters(context.window_manager)
        return {'FINISHED'}


def filter_items(wm):
    """The Filters panel's rows: a header per group, a checked row per filter, Clear filters while some are on."""
    on = set(active_filters(wm))
    rows = []
    for category, filters in filter_groups(wm):
        rows.append({"header": category.title()})
        for title, _group in filters:
            rows.append({"label": title, "check": title in on, "id": title,
                         "action": lambda context, title=title: toggle_filter(context.window_manager, title)})
    if on:
        rows += [{"sep": True}, {"label": "Clear filters", "icon": "mdi_Close", "id": "clear",
                                 "action": lambda context: clear_filters(context.window_manager)}]
    return rows


def _filters_panel(ui, col):
    from .ui import fields
    t = ui.t
    for row in filter_items(bpy.context.window_manager):
        if "header" in row:
            if col.used:
                col.space(t.gap)
            col.label(row["header"], t.text3, t.caption)
        elif "sep" in row:
            col.separator()
        elif "check" in row:
            _, r = col.split("")
            fields.checkbox(ui, "filters:" + row["id"], r, row["check"], row["label"], row["action"])
        else:
            col.button("filters:" + row["id"], row["label"], row["icon"], row["action"])


def open_filters(context, anchor, side="below"):
    """FP's Filters: what the grid shows"""
    from .ui import popups
    return popups.open_panel(context, anchor, _filters_panel, "filters", width=240, side=side)


class FP_OT_browse_favorite(bpy.types.Operator):
    """Add to or remove from FP's Favorites (its Favorites filter shows them)"""
    bl_idname = "fp.browse_favorite"
    bl_label = "Favorite"
    bl_options = {'INTERNAL'}
    path: bpy.props.StringProperty()

    def execute(self, context):
        kind, path = context.window_manager.fp_browse_type, self.path       # the answer comes after the operator ends

        def done(result):
            if isinstance(result, Exception):
                STATE["error"] = "favorite: %s" % result
            else:
                for item in (STATE["tabs"].get(kind) or {}).get("items") or []:
                    if item["path"] == path:
                        item["favorite"] = bool(result.get("favorite"))
                STATE["items_rev"] += 1
            tag_redraw()
        client().request("browse-favorite", done, type=kind, path=path)
        return {'FINISHED'}


class FP_OT_browse_random(bpy.types.Operator):
    """Pick a random asset of the ones shown"""
    bl_idname = "fp.browse_random"
    bl_label = "Random"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        shown = items(context.window_manager)
        if not shown:
            return {'CANCELLED'}
        STATE["picked"] = []
        show(context, random.choice(shown)["path"])
        return {'FINISHED'}


class FP_OT_browse_step(bpy.types.Operator):
    """Move the pick through the grid (arrow keys)"""
    bl_idname = "fp.browse_step"
    bl_label = "Move Pick"
    bl_options = {'INTERNAL'}
    dx: bpy.props.IntProperty()
    dy: bpy.props.IntProperty()

    def invoke(self, context, event):
        grid = _grid(context)
        if grid is None:
            return {'PASS_THROUGH'}
        wm = context.window_manager
        shown = [i["path"] for i in items(wm)]
        if not shown:
            return {'FINISHED'}
        i = shown.index(wm.fp_browse_selected) if wm.fp_browse_selected in shown else -1
        i = 0 if i < 0 else min(max(i + self.dx + self.dy * grid.columns, 0), len(shown) - 1)
        STATE["picked"] = []
        show(context, shown[i])
        return {'FINISHED'}


class FP_OT_browse_enter(bpy.types.Operator):
    """Import the picked assets (Enter)"""
    bl_idname = "fp.browse_enter"
    bl_label = "Import Picked"
    bl_options = {'INTERNAL'}

    def invoke(self, context, event):
        if _grid(context) is None or not picked(context.window_manager):
            return {'PASS_THROUGH'}
        bpy.ops.fp.browse_import()
        return {'FINISHED'}


classes = (FP_OT_browse_filter, FP_OT_browse_filters_clear, FP_OT_browse_favorite, FP_OT_browse_random,
           FP_OT_browse_step, FP_OT_browse_enter)
_keymaps = []


def register():
    from .ui import blender as ub
    bpy.types.WindowManager.fp_browse_filters = bpy.props.StringProperty(options={'SKIP_SAVE'})
    for c in classes:
        bpy.utils.register_class(c)
    ub.OPENERS["filters"] = open_filters
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc is None:
        return
    add = host.adder(kc, _keymaps)
    for key, dx, dy in (('LEFT_ARROW', -1, 0), ('RIGHT_ARROW', 1, 0), ('UP_ARROW', 0, -1), ('DOWN_ARROW', 0, 1)):
        kmi = add(FP_OT_browse_step.bl_idname, key, 'PRESS', repeat=True)
        kmi.properties.dx, kmi.properties.dy = dx, dy
    add(FP_OT_browse_enter.bl_idname, 'RET', 'PRESS')
    add(FP_OT_browse_enter.bl_idname, 'NUMPAD_ENTER', 'PRESS')


def unregister():
    from .ui import blender as ub
    ub.OPENERS.pop("filters", None)
    for km, kmi in _keymaps:
        try:
            km.keymap_items.remove(kmi)
        except (ReferenceError, RuntimeError):
            pass
    _keymaps.clear()
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
    del bpy.types.WindowManager.fp_browse_filters
