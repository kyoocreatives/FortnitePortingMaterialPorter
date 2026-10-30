"""The Fortnite menu: ready-made workspaces (built from the current one on first use, switched to after) and the
editor in a window of its own. Later steps add theirs (FP Rig, FP Landscape) to LAYOUTS."""
import bpy

from . import host

LAYOUTS = {
    "FP Studio": "3D viewport and the Fortnite editor side by side",
    "FP Assets": "the Fortnite editor, full screen",
}


TAG = "fp_layout"       # on the workspaces this menu made: a user's workspace of the same name is never taken
_building = [False]


def ours(name):
    return next((w for w in bpy.data.workspaces if w.get(TAG) == name), None)


def _close_all_but_one(window):
    """Close the smallest area until one is left (areas are re-read each pass: closing frees or reshapes others)."""
    screen = window.screen
    for _ in range(32):
        if len(screen.areas) <= 1:
            break
        smallest = min(screen.areas, key=lambda a: a.width * a.height)
        with bpy.context.temp_override(window=window, screen=screen, area=smallest):
            if bpy.ops.screen.area_close.poll() is False:
                break
            bpy.ops.screen.area_close()
    return screen.areas[0]


def _build(window, name):
    screen = window.screen
    if name == "FP Assets":
        only = _close_all_but_one(window)
        only.ui_type = host.TREE
        host.set_tab(only.spaces.active, "cosmetics")
        host.show_regions(window, only)
        return
    big = max(screen.areas, key=lambda a: a.width * a.height)
    big.ui_type = host.TREE
    host.set_tab(big.spaces.active, "cosmetics")
    host.show_regions(window, big)
    known = {a.as_pointer() for a in screen.areas}
    with bpy.context.temp_override(window=window, screen=screen, area=big):
        bpy.ops.screen.area_split(direction='VERTICAL', factor=0.5, cursor=(big.x + big.width // 2, big.y + big.height // 2))
    new = next(a for a in screen.areas if a.as_pointer() not in known)
    new.type = 'VIEW_3D'        # the split's new half is the left one: the viewport, the editor on the right


class FP_OT_workspace(bpy.types.Operator):
    """Switch to this FP workspace (made from the current one the first time)"""
    bl_idname = "fp.workspace"
    bl_label = "FP Workspace"
    name: bpy.props.StringProperty()

    @classmethod
    def description(cls, context, properties):
        return "Switch to %s: %s" % (properties.name, LAYOUTS.get(properties.name, ""))

    @classmethod
    def poll(cls, context):
        return context.window is not None and not _building[0]

    def execute(self, context):
        if self.name not in LAYOUTS:
            self.report({'ERROR'}, "No FP workspace named %s" % self.name)
            return {'CANCELLED'}
        window = context.window
        existing = ours(self.name)
        if existing is not None:
            window.workspace = existing
            return {'FINISHED'}
        before = set(bpy.data.workspaces.keys())
        bpy.ops.workspace.duplicate()
        name, tries = self.name, [0]
        _building[0] = True

        def finish():       # the duplicate becomes active on the next event: lay it out, then name and tag it
            tries[0] += 1
            live = window in bpy.context.window_manager.windows[:]
            new = next((w for w in bpy.data.workspaces if w.name not in before), None)
            if not live or tries[0] > 40:
                _building[0] = False
                return None
            if new is None or window.workspace != new:
                if new is not None:
                    window.workspace = new
                return 0.05
            try:
                _build(window, name)
                new[TAG] = name
                new.name = name     # Blender adds .001 when a user's workspace already has the name
            except Exception as e:  # left as an untagged copy: asking again builds afresh
                print("[FP] workspace %s not built: %s" % (name, e))
            _building[0] = False
            return None
        bpy.app.timers.register(finish, first_interval=0.05)
        return {'FINISHED'}


class FP_OT_editor_window(bpy.types.Operator):
    """Open this Fortnite editor in a new window (a second monitor, a floating tab)"""
    bl_idname = "fp.editor_window"
    bl_label = "Open in New Window"

    @classmethod
    def poll(cls, context):
        return context.area is not None and host.is_editor_area(context.area)

    def execute(self, context):
        return bpy.ops.screen.area_dupli('INVOKE_DEFAULT')


def _switch(name):
    def run(context):
        bpy.ops.fp.workspace(name=name)
    return run


def _new_window(context):
    bpy.ops.fp.editor_window()


def menu_items():
    """The Fortnite menu: the FP workspaces, then the editor in a new window."""
    window = bpy.context.window
    here = window.workspace.name if window is not None else None
    items = [{"label": name, "tip": "Switch to %s: %s" % (name, what), "action": _switch(name), "id": name,
              "check": name == here} for name, what in LAYOUTS.items()]
    return items + [{"sep": True}, {"label": FP_OT_editor_window.bl_label, "tip": FP_OT_editor_window.__doc__,
                                    "action": _new_window, "id": "window"}]


def open_menu(context, anchor, side="below"):
    """Fortnite: FP workspaces and a new editor window"""
    from .ui import popups
    return popups.open_menu(context, anchor, menu_items, "fortnite", side=side)


classes = (FP_OT_workspace, FP_OT_editor_window)


def register():
    from .ui import blender as ub
    for c in classes:
        bpy.utils.register_class(c)
    ub.OPENERS["fortnite"] = open_menu


def unregister():
    from .ui import blender as ub
    ub.OPENERS.pop("fortnite", None)
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
