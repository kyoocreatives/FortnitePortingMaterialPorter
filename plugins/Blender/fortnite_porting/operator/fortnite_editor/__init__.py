"""The Fortnite editor: FP's pages in a node editor of their own (host.py), with the app running as a
hidden engine Blender starts itself.

Regions: the tab strip in the toolbar, the header (the tab's own tools, the engine's state), the tab's main view drawn
in the main region (grid_draw / grid_ops for Cosmetics), its list and tools in the sidebar. A timer pumps the engine's answers and asks for
what the last draw needed (the tab's items, its visible icons)."""
import time

import bpy

from . import (anim_play, anim_tab, asset_tab, browse_ops, logbook, remember, status_bar, cosmetics_page, files_tab, grid_draw, grid_ops, host, land_ops, land_tab, maps_ops, maps_tab, scatter_ops,
               mat_tab, preview, rig_ops, rig_tab, settings_tab, setup_page, tabs, textures, tool_tabs, workspaces)
from .ui import blender as ui_blender
from .state import STATE, active_filters, client, icon_failed, icon_wanted, reset_client, save_prefs, selected, tag_redraw

TAB_ICONS = {
    "Outfit": 'USER', "Backpack": 'MOD_CLOTH', "Pickaxe": 'TOOL_SETTINGS', "Glider": 'MOD_WAVE', "Pet": 'EVENT_P',
    "Toy": 'MESH_UVSPHERE', "Emote": 'ARMATURE_DATA', "Emoticon": 'FUND', "Spray": 'BRUSH_DATA', "Banner": 'IMAGE_DATA',
    "LoadingScreen": 'IMAGE_BACKGROUND', "Wrap": 'TEXTURE', "Prop": 'MESH_CUBE', "Prefab": 'PACKAGE',
    "Item": 'MOD_EQUALIZER', "Weapon": 'MOD_EQUALIZER', "Vehicle": 'AUTO', "Resource": 'MATERIAL', "Trap": 'GHOST_ENABLED',
    "Sidekick": 'COMMUNITY', "Lego": 'MESH_ICOSPHERE', "Car": 'AUTO', "Instrument": 'SPEAKER', "Kicks": 'MOD_DYNAMICPAINT',
}
MAX_ICON_REQUESTS = 12
_valid_icons = set()


def tab_icon(kind):
    """The tab's icon, ASSET_MANAGER for one this Blender lacks (icon names change between versions)."""
    if not _valid_icons:
        _valid_icons.update(bpy.types.UILayout.bl_rna.functions["label"].parameters["icon"].enum_items.keys())
    icon = TAB_ICONS.get(kind, 'ASSET_MANAGER')
    return icon if icon in _valid_icons else 'ASSET_MANAGER'


# ------------------------------------------------------------------ the engine's answers

def _answer(kind, arg):
    def done(result):
        if isinstance(result, Exception):
            if kind == "icon":
                icon_failed(arg, time.time())
            else:
                STATE["error"] = "%s: %s" % (kind, result)
            return
        if kind == "categories":
            STATE["categories"] = result
        elif kind == "tab":
            STATE["tabs"][arg] = result
            STATE["items_rev"] += 1
        if kind != "icon":
            STATE["header_dirty"] = True        # the header counts a tab's items; an icon only shows in the grid
        elif kind == "icon":
            STATE["icons_asked"].discard(arg)
            if not textures.add(arg, result.get("file") or result.get("File")):
                icon_failed(arg, time.time())
    return done


def _tick():
    remember.tick()
    e = client()
    was = (e.state, STATE["error"])
    changed = e.pump()
    for tab in tabs.all_tabs():
        if tab.tick is not None:
            tab.tick()
    if e.last_error:
        STATE["error"], e.last_error = e.last_error, None
    if STATE["error"] and STATE["error"] != STATE.get("error_logged"):      # every error to the status bar's log
        STATE["error_logged"] = STATE["error"]
        logbook.write(STATE["error"], "error")
    if e.state == "ready":
        logbook.tick()
    if e.state == "ready":
        wm = bpy.context.window_manager
        if STATE["categories"] is None and "categories" not in STATE["tab_asked"]:
            STATE["tab_asked"]["categories"] = time.time()
            e.request("browse-categories", _answer("categories", None))
        kind = wm.fp_browse_type
        tab = STATE["tabs"].get(kind) or {}
        if not tab.get("finished") and time.time() - STATE["tab_asked"].get(kind, 0) > 0.5:
            STATE["tab_asked"][kind] = time.time()
            e.request("browse", _answer("tab", kind), type=kind)
        asset_tab.ask_styles(kind, wm.fp_browse_selected)
        settings_tab.ask()
        in_flight = len(STATE["icons_asked"])
        picked = selected(wm)       # the Asset tab's card needs the picked asset's icon, scrolled into view or not
        wanted = ([picked["icon"]] if picked and picked.get("icon") else []) + STATE["want_icons"]
        for path in wanted:
            if in_flight >= MAX_ICON_REQUESTS:
                break
            if icon_wanted(path, time.time()) and textures.get(path) is None:
                STATE["icons_asked"].add(path)
                e.request("texture", _answer("icon", path), path=path, cap=256)
                in_flight += 1
    if changed:
        header = STATE.pop("header_dirty", False) or (e.state, STATE["error"]) != was
        tag_redraw(main_only=not header)
    return 0.1


# ------------------------------------------------------------------ operators

class FP_OT_browse_tab(bpy.types.Operator):
    """Show this tab's assets"""
    bl_idname = "fp.browse_tab"
    bl_label = "Show Tab"
    type: bpy.props.StringProperty()

    def execute(self, context):
        wm = context.window_manager
        wm.fp_browse_type, wm.fp_browse_scroll = self.type, 0.0
        STATE["hover"].clear()
        STATE["picked"] = []
        tag_redraw()
        return {'FINISHED'}


class FP_OT_engine_show(bpy.types.Operator):
    """Show the FP app's window (first-time setup, or to use the app itself)"""
    bl_idname = "fp.engine_show"
    bl_label = "Show FP"

    def execute(self, context):
        if not client().show():
            self.report({'WARNING'}, "FP's exe is unknown: use Locate FP")
        return {'FINISHED'}


class FP_OT_engine_locate(bpy.types.Operator):
    """Pick FortnitePortingMP.exe (the editor starts it hidden when it isn't running)"""
    bl_idname = "fp.engine_locate"
    bl_label = "Locate FP"
    filepath: bpy.props.StringProperty(subtype='FILE_PATH')
    filter_glob: bpy.props.StringProperty(default="*.exe", options={'HIDDEN'})

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        save_prefs(exe=self.filepath)
        reset_client()
        client().ensure()
        return {'FINISHED'}


class FP_OT_browse_refresh(bpy.types.Operator):
    """Ask FP again for the categories and this tab (after it loaded another game version)"""
    bl_idname = "fp.browse_refresh"
    bl_label = "Refresh"

    def execute(self, context):
        STATE.update(categories=None, tabs={}, tab_asked={}, error=None)
        files_tab.reset()
        maps_tab.reset()
        client().ensure()
        return {'FINISHED'}


# ------------------------------------------------------------------ regions

STATE_ICONS = {"ready": 'CHECKMARK', "loading": 'SORTTIME', "starting": 'SORTTIME', "setup": 'ERROR',
               "no_exe": 'ERROR', "too_old": 'ERROR', "error": 'ERROR', "unknown": 'SORTTIME'}


class FP_OT_editor_tab(bpy.types.Operator):
    bl_idname = "fp.editor_tab"
    bl_label = "Editor Tab"
    bl_options = {'INTERNAL'}
    tab: bpy.props.StringProperty()

    @classmethod
    def description(cls, context, properties):
        tab = next((t for t in tabs.all_tabs() if t.id == properties.tab), None)
        return tab.label if tab is not None else ""

    def execute(self, context):
        host.set_tab(context.space_data, self.tab)
        return {'FINISHED'}


def _cosmetics_header(layout, context):
    wm = context.window_manager
    tab = tabs.current(context.space_data)
    folded = ui_blender.folded(context, tab)
    if "side" in folded:        # the category list folded away: its picker here
        row = layout.row()
        row.ui_units_x = 6
        row.operator("fp.popup_open", text=cosmetics_page.plural(wm.fp_browse_type),
                     icon=tab_icon(wm.fp_browse_type)).name = "categories"
    if "info" in folded and selected(wm) is not None:      # and Import, the info column's action
        layout.operator("fp.browse_import", text="Import", icon='IMPORT')
    narrow = bool(folded)
    row = layout.row(align=True)
    row.ui_units_x = 8 if narrow else 12
    row.prop(wm, "fp_browse_search", text="", icon='VIEWZOOM')
    on = len(active_filters(wm))
    layout.operator("fp.popup_open", text="" if narrow else ("Filters (%d)" % on if on else "Filters"),
                    icon='FILTER').name = "filters"
    layout.prop(wm, "fp_browse_sort", text="", icon='SORTSIZE' if narrow else 'NONE', icon_only=narrow)
    layout.operator("fp.browse_random", text="", icon='MOD_NOISE')
    sub = layout.row()
    sub.ui_units_x = 4 if narrow else 6
    sub.prop(wm, "fp_browse_tile", text="" if narrow else "Size", slider=True)
    tab = STATE["tabs"].get(wm.fp_browse_type) or {}
    if tab.get("finished") and not narrow:
        drawn = STATE["grids"].get(context.area.as_pointer())
        shown = len(drawn[1]) if drawn else len(tab.get("items") or [])
        layout.label(text="%d / %d" % (shown, len(tab.get("items") or [])))


def _draw_main():
    """The area's tab draws its view, then the shell (nav, list column, info column) goes over it."""
    context = bpy.context
    tab = tabs.current(context.space_data)
    if tab is None or tab.id != "cosmetics":
        STATE["grids"].pop(context.area.as_pointer(), None)     # only the Cosmetics tab's grid takes grid clicks
    ui_blender.ground(context, tab)
    if tab is not None and tab.draw is not None:
        tab.draw()
    actions = dict(_ACTIONS, drawer_open=context.space_data.show_region_ui)
    if setup_page.needed(tab):
        actions["well"] = setup_page.draw
    ui_blender.draw(context, tabs.all_tabs(), tab, actions)


def _tab_action(tab_id):
    def run(context):
        bpy.ops.fp.editor_tab(tab=tab_id)
    return run


def _settings(context):
    """The nav's Export Options: the panel beside its button."""
    from .ui.rect import Rect
    frame = ui_blender.FRAMES.get(context.area.as_pointer())
    hit = frame.find("nav:settings") if frame is not None else None
    anchor = hit.rect if hit is not None else Rect(0, 0, 0, 0)
    settings_tab.open_settings(context, anchor, "right")


def _drawer(context):
    space = context.space_data
    space.show_region_ui = not space.show_region_ui


_ACTIONS = {"tab": _tab_action, "settings": _settings, "drawer": _drawer}


def _header(layout, context):
    e = client()
    if e.state == "unknown":
        e.ensure()      # the first draw of a Fortnite area starts or finds the engine
    layout.template_header()
    layout.operator("fp.popup_open", text="Fortnite", emboss=False).name = "fortnite"
    current = tabs.current(context.space_data)
    if current is not None:
        if current.header is not None:
            current.header(layout, context)
    layout.separator_spacer()       # what imports are doing: the status bar under the panel
    if e.state != "ready":
        layout.label(text=e.status_text[:90] or "Connecting to FP...", icon=STATE_ICONS.get(e.state, 'INFO'))
    if e.state in ("no_exe", "error"):
        layout.operator(FP_OT_engine_locate.bl_idname, icon='FILEBROWSER')
    if e.state in ("setup", "ready", "loading", "starting", "too_old"):
        layout.operator(FP_OT_engine_show.bl_idname, text="", icon='WINDOW')
    layout.operator("fp.popup_open", text="", icon='PREFERENCES').name = "settings"
    layout.operator(FP_OT_browse_refresh.bl_idname, text="", icon='FILE_REFRESH')


classes = (FP_OT_browse_tab, FP_OT_engine_show, FP_OT_engine_locate, FP_OT_browse_refresh, FP_OT_editor_tab)
from . import quick_search  # noqa: E402  (Ctrl+Shift+F over any editor)
modules = (grid_ops, browse_ops, asset_tab, files_tab, maps_tab, maps_ops, rig_tab, rig_ops, land_tab, land_ops, scatter_ops, mat_tab, anim_tab, anim_play, preview, settings_tab, tool_tabs, workspaces, quick_search)


def _redraw_on_change(self, context):
    tag_redraw()


def _search_changed(self, context):
    self.fp_browse_scroll = 0.0
    tag_redraw()


def register():
    try:
        _register()
    except Exception:
        _unregister_quietly()       # a half registration would leave Blender's node editors wrapped
        raise


def _register():
    host.register()
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc is not None:
        host.swallow_presses(kc)        # first: Blender merges add-on items in reverse, so these come after the tabs'
    wm = bpy.types.WindowManager
    wm.fp_browse_type = bpy.props.StringProperty(default="Outfit")
    wm.fp_browse_search = bpy.props.StringProperty(name="Search", options={'TEXTEDIT_UPDATE'}, update=_search_changed)
    wm.fp_browse_sort = bpy.props.EnumProperty(name="Sort", items=(('NAME', "Name", ""), ('RARITY', "Rarity", ""),
                                                                    ('SERIES', "Series", ""),
                                                                    ('NEWEST', "Newest", "Latest season first")),
                                         update=_redraw_on_change)
    wm.fp_browse_tile = bpy.props.IntProperty(name="Tile Size", default=96, min=grid_ops.TILE_MIN, max=grid_ops.TILE_MAX,
                                              subtype='PIXEL', update=_redraw_on_change)
    wm.fp_browse_selected = bpy.props.StringProperty()
    wm.fp_browse_names = bpy.props.BoolProperty(name="Show Names", description="Names under the tiles (off: on hover)",
                                                update=_redraw_on_change)
    wm.fp_browse_scroll = bpy.props.FloatProperty(min=0)
    for c in classes:
        bpy.utils.register_class(c)
    ui_blender.OPENERS["categories"] = cosmetics_page.open_categories
    tabs.register_tab(tabs.Tab("cosmetics", "Assets", 'USER', header=_cosmetics_header, draw=grid_draw.draw,
                               nav_icon="mdi_File", side=cosmetics_page.side, info=cosmetics_page.info,
                               hover=grid_ops.hover))
    for m in modules:
        m.register()        # tabs register in the bar's order: Files (and later Map) before Settings
    textures.install()
    host.on_draw, host.on_header = _draw_main, _header
    ui_blender.register()
    ui_blender.STATUS[0] = status_bar.draw
    logbook.register()
    remember.register()
    if kc is not None:
        ui_blender.register_keys(kc)        # last: Blender merges add-on items in reverse, so the shell's come first
    if not bpy.app.timers.is_registered(_tick):
        bpy.app.timers.register(_tick, first_interval=0.2, persistent=True)


def unregister():
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    host.on_draw = host.on_header = None
    remember.unregister()
    logbook.unregister()
    ui_blender.STATUS[0] = None
    ui_blender.unregister()
    for m in reversed(modules):
        m.unregister()
    ui_blender.OPENERS.pop("categories", None)
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
    tabs.unregister_tab("cosmetics")
    textures.uninstall()
    textures.clear()
    for p in ("fp_browse_type", "fp_browse_search", "fp_browse_sort", "fp_browse_tile", "fp_browse_selected", "fp_browse_scroll",
              "fp_browse_names"):
        delattr(bpy.types.WindowManager, p)
    host.unregister()


def _unregister_quietly():
    try:
        unregister()
    except Exception:
        host.unregister()
