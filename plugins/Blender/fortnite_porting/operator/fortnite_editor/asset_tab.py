"""The picked asset's operators: Import (double-click in the grid too), its style picks and their menu (the info
column, cosmetics_page.py, draws the card, the style rows open a tile picker); its styles asked from the engine once."""
import bpy

from . import importing
from .state import STATE, client, picked, selected, tag_redraw


def ask_styles(kind, path):
    """The picked asset's style channels, asked once (the timer calls this)."""
    if not path or path in STATE["styles"]:
        return
    STATE["styles"][path] = "asked"

    def done(result):
        STATE["styles"][path] = [] if isinstance(result, Exception) else result
        tag_redraw()
    client().request("browse-styles", done, type=kind, path=path)


class FP_OT_browse_import(bpy.types.Operator):
    """Import the picked assets with their picked styles (FP exports each, Blender imports it here)"""
    bl_idname = "fp.browse_import"
    bl_label = "Import"

    @classmethod
    def poll(cls, context):
        return not STATE["busy"] and selected(context.window_manager) is not None

    def execute(self, context):
        wm = context.window_manager
        by_path = {i["path"]: i for i in (STATE["tabs"].get(wm.fp_browse_type) or {}).get("items") or []}
        found = [by_path[p] for p in picked(wm) if p in by_path]
        importing.start_all([(wm.fp_browse_type, i, STATE["picks"].get(i["path"], {})) for i in found])
        return {'FINISHED'}


class FP_OT_style_pick(bpy.types.Operator):
    """Use this style option when importing"""
    bl_idname = "fp.style_pick"
    bl_label = "Pick Style"
    bl_options = {'INTERNAL'}
    path: bpy.props.StringProperty()
    channel: bpy.props.StringProperty()
    option: bpy.props.StringProperty()

    def execute(self, context):
        STATE["picks"].setdefault(self.path, {})[self.channel] = self.option
        tag_redraw()
        return {'FINISHED'}


def style_items(path, channel):
    """The tile picker's items for a style channel: each option name once (picks are by name), its icon the tile, the
    current pick (or the asset's own style) marked; picking sets the import pick."""
    current = STATE["picks"].get(path, {}).get(channel["channel"], channel.get("selected"))
    items, seen = [], set()
    for option in channel["options"]:
        name = option["name"]
        if name in seen:
            continue
        seen.add(name)

        def pick(context, name=name):
            STATE["picks"].setdefault(path, {})[channel["channel"]] = name
            tag_redraw()
        items.append({"label": name, "image": option.get("icon"), "check": name == current, "action": pick,
                      "id": len(items)})
    return items


def open_styles(context, anchor, path, channel, parent=None):
    """The style channel's tile picker beside `anchor` (the info column's style row: over the well)."""
    from .ui import popups
    return popups.open_tiles(context, anchor, lambda: style_items(path, channel), "styles",
                             "Search %s" % channel["channel"].lower(), side="left", key="styles", parent=parent)


classes = (FP_OT_browse_import, FP_OT_style_pick)


def register():
    STATE.setdefault("styles", {})
    STATE.setdefault("picks", {})
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
