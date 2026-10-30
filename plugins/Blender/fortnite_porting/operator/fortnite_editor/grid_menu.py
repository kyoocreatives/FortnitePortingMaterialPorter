"""The Assets grid's right-click menu: the tile under the mouse becomes the pick, then Import, Import with Styles,
Replace Selected, Show in Files, Copy Path, Favorites, Open in FP App.

Replace Selected asks for the import now and does the swap when it lands (importing.AFTER, from the editor's timer),
as one undo step. Every editor import's new objects get an import tag there (replace.TAG) so a later replace knows
exactly what that import brought."""
import itertools
import time

import bpy

from . import host, importing, replace, tabs
from .state import STATE, client, tag_redraw

MENU = {"path": None}       # the tile the menu was opened on
REPLACES = []       # {"label", "root", "uid", "name"}: replaces waiting for their import to land
_ids = itertools.count(1)
_keymaps = []


def _items(wm):
    return (STATE["tabs"].get(wm.fp_browse_type) or {}).get("items") or []


def _item(wm, path):
    return next((i for i in _items(wm) if i["path"] == path), None)


def label_of(item):
    """The label importing.start gives the import (its AFTER hook hears it back)."""
    return item.get("name") or item["path"].rsplit(".", 1)[-1]


def _window_of(obj):
    wm = bpy.context.window_manager
    return next((w for w in wm.windows if obj is not None and obj.name in w.view_layer.objects), wm.windows[0])


# ------------------------------------------------------------------ the import hook

def landed(label, new, message):
    """importing.AFTER: tag what the import brought; when a replace waits for this import, do it."""
    if new:
        replace.tag_objects(new, "%d.%d" % (int(time.time()), next(_ids)))
    job = next((j for j in REPLACES if j["label"] == label), None)
    if job is None:
        return
    REPLACES.remove(job)
    from . import logbook
    old = bpy.data.objects.get(job["root"])
    if old is None or old.session_uid != job["uid"]:
        logbook.write("Replace: %s isn't in the scene any more, %s stays imported beside" % (job["root"], label), "warning")
        return
    if not new:
        logbook.write("Replace of %s cancelled: the import brought nothing" % old.name, "warning")
        return
    win = _window_of(old)
    try:
        with bpy.context.temp_override(window=win):
            main, notes = replace.replace(old, new, win.view_layer)
            step = "Replace %s with %s" % (job["root"], label)
            if main is not None and getattr(importing, "_step", None):
                importing._step[:] = [step]     # importing pushes the import's own step after AFTER: one step, ours
            elif main is not None:
                bpy.ops.ed.undo_push(message=step)
    except Exception as e:      # never raise in the editor's timer: say it
        STATE["error"] = "Replace failed: %s" % e
        logbook.write(STATE["error"], "error")
        return
    if main is None:
        logbook.write("Replace: " + "; ".join(notes), "warning")
    else:
        logbook.write("Replaced %s with %s: %s" % (job["root"], main.name, "; ".join(notes)))
    tag_redraw()


# ------------------------------------------------------------------ operators

class FP_OT_grid_menu(bpy.types.Operator):
    """The asset under the mouse: picked, then its menu"""
    bl_idname = "fp.grid_menu"
    bl_label = "Asset Menu"
    bl_options = {'INTERNAL'}

    def invoke(self, context, event):
        from . import browse_ops, grid_ops
        if not tabs.is_tab(context, "cosmetics"):
            return {'PASS_THROUGH'}
        item = grid_ops.hit_item(context.area.as_pointer(), event.mouse_region_x, event.mouse_region_y)
        if item is None:
            return {'PASS_THROUGH'}
        browse_ops.pick(context.window_manager, item["path"])
        MENU["path"] = item["path"]
        tag_redraw()
        from .ui import popups
        from .ui.rect import Rect
        at = Rect(event.mouse_region_x, event.mouse_region_y, 0, 0)
        popups.open_menu(context, at, lambda: tile_items(item["path"], at), "tile", title=label_of(item), side="at")
        return {'FINISHED'}


class FP_OT_grid_replace(bpy.types.Operator):
    """Import this asset and put it in place of the selected character or prop: its transform, parent and
    collection, and a character's action and pose. The old one is removed once the new one is in"""
    bl_idname = "fp.grid_replace"
    bl_label = "Replace Selected"
    bl_options = {'INTERNAL'}
    path: bpy.props.StringProperty(options={'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        root, why = replace.find_target(context)
        if root is None:
            cls.poll_message_set(why)
            return False
        if any(j["root"] == root.name for j in REPLACES):
            cls.poll_message_set("%s is already being replaced" % root.name)
            return False
        return True

    def execute(self, context):
        wm = context.window_manager
        item = _item(wm, self.path or MENU["path"])
        root, why = replace.find_target(context)
        if item is None or root is None:
            self.report({'WARNING'}, why or "That asset isn't listed any more")
            return {'CANCELLED'}
        REPLACES.append({"label": label_of(item), "root": root.name, "uid": root.session_uid})
        importing.start_all([(wm.fp_browse_type, item, STATE["picks"].get(item["path"], {}))])
        self.report({'INFO'}, "%s replaces %s once imported" % (label_of(item), root.name))
        tag_redraw()
        return {'FINISHED'}


def open_styles(context, anchor, path, parent=None):
    """Import with Styles: the info column shows the style rows: the first channel's tile picker; folded away: a panel
    of the channels (each opens its picker) and Import."""
    from .asset_tab import open_styles as open_channel
    from .ui import blender as ub, popups
    channels = STATE["styles"].get(path)
    shown = [c for c in channels if len(c["options"]) >= 2] if isinstance(channels, list) else None
    if shown == []:
        from . import logbook
        logbook.write("No styles to pick: Import imports it as it is")
        return None
    if shown and "info" not in ub.folded(context, tabs.current(context.space_data)):
        return open_channel(context, anchor, path, shown[0])

    def content(ui, col):
        from .ui import fields
        item = _item(bpy.context.window_manager, path)
        if item is None:
            col.label("That asset isn't listed any more")
            return
        col.label(label_of(item), ui.t.text1)
        chans = STATE["styles"].get(path)
        if not isinstance(chans, list):
            col.label("Reading styles...", ui.t.text3)
        else:
            picks = STATE["picks"].get(path, {})
            for c in chans:
                if len(c["options"]) < 2:
                    continue
                current = picks.get(c["channel"], c["selected"])
                _, r = col.split(c["channel"])

                def pick(context, r=r, c=c):
                    open_channel(context, r, path, c, parent="grid_styles")
                fields.dropdown(ui, "grid_styles:" + c["channel"], r, current, pick)
        col.space()

        def run(context):
            ub.STACKS[context.area.as_pointer()].close_all()
            bpy.ops.fp.browse_import()
        col.button("grid_styles:import", "Import", "mdi_Download", run, style="accent")
    return popups.open_panel(context, anchor, content, "grid_styles", width=300, side="right")


class FP_OT_grid_show_in_files(bpy.types.Operator):
    """Show this asset's package in the Files tab (its folder open, the file picked)"""
    bl_idname = "fp.grid_show_in_files"
    bl_label = "Show in Files"
    bl_options = {'INTERNAL'}
    path: bpy.props.StringProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        path = self.path or MENU["path"]
        if not path:
            return {'CANCELLED'}
        area = context.area
        host.set_tab(area.spaces.active, "files")
        reveal(area.as_pointer(), path)
        tag_redraw()
        return {'FINISHED'}


class FP_OT_grid_copy_path(bpy.types.Operator):
    """Copy the asset's object path"""
    bl_idname = "fp.grid_copy_path"
    bl_label = "Copy Path"
    bl_options = {'INTERNAL'}
    path: bpy.props.StringProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        context.window_manager.clipboard = self.path or MENU["path"] or ""
        self.report({'INFO'}, "Copied " + context.window_manager.clipboard)
        return {'FINISHED'}


class FP_OT_grid_open_app(bpy.types.Operator):
    """Bring the FP app to the front"""
    bl_idname = "fp.grid_open_app"
    bl_label = "Open in FP App"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        def done(result):       # the app has no route to open one asset's page: showing it is all it can do
            if isinstance(result, Exception) and not client().show():
                STATE["error"] = "Open in FP App: %s" % result
        client().request("engine-show", done, timeout=15)
        return {'FINISHED'}


def tile_items(path, anchor):
    """The asset tile's menu: Import, Import with Styles, Replace Selected, Show in Files, Copy Path, Favorites, FP."""
    wm = bpy.context.window_manager
    item = _item(wm, path) if path else None
    if item is None:
        return [{"header": "That asset isn't listed any more"}]
    root, why = replace.find_target(bpy.context)
    busy = root is not None and any(j["root"] == root.name for j in REPLACES)
    fav = bool(item.get("favorite"))

    def op(fn):
        return lambda context: fn()
    return [
        {"label": "Import", "icon": "mdi_Download", "action": op(lambda: bpy.ops.fp.browse_import())},
        {"label": "Import with Styles...", "icon": "mdi_Brush",
         "action": lambda context: open_styles(context, anchor, path)},
        {"label": "Replace Selected", "icon": "mdi_Refresh", "action": op(lambda: bpy.ops.fp.grid_replace(path=path)),
         "enabled": root is not None and not busy,
         "tip": FP_OT_grid_replace.__doc__.split(":")[0] if root is not None and not busy else
         ("%s is already being replaced" % root.name if busy else why)},
        {"sep": True},
        {"label": "Show in Files", "icon": "mdi_FolderOpen", "action": op(lambda: bpy.ops.fp.grid_show_in_files(path=path))},
        {"label": "Copy Path", "icon": "mdi_ContentCopy", "action": op(lambda: bpy.ops.fp.grid_copy_path(path=path))},
        {"sep": True},
        {"label": "Remove from Favorites" if fav else "Add to Favorites", "icon": "mdi_Heart", "check": fav or None,
         "action": op(lambda: bpy.ops.fp.browse_favorite(path=path))},
        {"label": "Open in FP App", "icon": "Fortnite", "action": op(lambda: bpy.ops.fp.grid_open_app())},
    ]


# ------------------------------------------------------------------ Show in Files

def file_of(object_path, found):
    """The game file (files-search item) holding `object_path` ("/Game/A/B.B" in "FortniteGame/Content/A/B.uasset")."""
    from .files_tab import _object_path
    want = object_path.lower()
    return next((i for i in found if _object_path(i["path"]).lower() == want), None)


REVEAL = {}         # area pointer -> {"path", "file", "until"}: a file to scroll to once its folder is listed


def reveal(area_key, object_path, tries=30):
    """Find the asset's file (files-search by its package name), open its folder in the Files tab, pick it."""
    name = object_path.rsplit("/", 1)[-1].split(".")[0]

    def done(result):
        from . import files_tab
        if isinstance(result, Exception):
            STATE["error"] = "Show in Files: %s" % result
            return
        if result.get("loading"):        # the app still lists its files
            if tries > 0:
                bpy.app.timers.register(lambda: reveal(area_key, object_path, tries - 1), first_interval=1.0)
            return False
        found = file_of(object_path, result.get("items") or [])
        if found is None:
            STATE["error"] = "Show in Files: no game file holds %s" % object_path
            return
        wm = bpy.context.window_manager
        files_tab._go(found["folder"])(bpy.context)
        files_tab.FILES["selected"], files_tab.FILES["anchor"] = [found["path"]], found["path"]
        REVEAL[area_key] = {"file": found["path"], "until": time.time() + 30}
        if not bpy.app.timers.is_registered(_scroll_to):
            bpy.app.timers.register(_scroll_to, first_interval=0.2)
        tag_redraw()
    client().request("files-search", done, q=name, limit=200)


def _scroll_to():
    """Scroll each waiting Files list to its file once the folder's rows are drawn."""
    from . import files_tab
    for key, want in list(REVEAL.items()):
        state = files_tab.FILES["rows"].get(key)
        rows = state.get("items") if state else None
        i = next((n for n, it in enumerate(rows or []) if it["path"] == want["file"]), None)
        if i is not None and state.get("layout") is not None:
            layout = state["layout"]
            state["scroll"] = max(0.0, min(i * layout.row_h - (layout.height - layout.row_h) / 2, layout.max_scroll))
            del REVEAL[key]
            tag_redraw()
        elif time.time() > want["until"]:
            del REVEAL[key]
    return 0.2 if REVEAL else None


# ------------------------------------------------------------------ registration

classes = (FP_OT_grid_menu, FP_OT_grid_replace, FP_OT_grid_show_in_files, FP_OT_grid_copy_path, FP_OT_grid_open_app)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    if landed not in importing.AFTER:
        importing.AFTER.append(landed)
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc is not None:      # after host.swallow_presses (it takes the right presses nothing else did)
        host.adder(kc, _keymaps)(FP_OT_grid_menu.bl_idname, 'RIGHTMOUSE', 'PRESS')


def unregister():
    for km, kmi in _keymaps:
        try:
            km.keymap_items.remove(kmi)
        except (ReferenceError, RuntimeError):
            pass
    _keymaps.clear()
    if landed in importing.AFTER:
        importing.AFTER.remove(landed)
    if bpy.app.timers.is_registered(_scroll_to):
        bpy.app.timers.unregister(_scroll_to)
    REVEAL.clear()
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
