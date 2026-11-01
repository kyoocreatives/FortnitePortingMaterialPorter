"""Export Options: FP's Blender export settings (the app's own Export Options) in a pop-up panel. The fields come
from the engine (settings-blender); they become a property group once, each change is sent back
(settings-blender-set) and saved by the app, so the app and the engine agree."""
import bpy
from bpy.app.handlers import persistent

from . import import_options
from .state import STATE, client, settings_read, tag_redraw

_cls = None
_syncing = [False]


class Sender:
    """One request in flight per setting, the latest value sent next: a dragged slider's steps arrive in order and
    the app keeps what Blender shows."""

    def __init__(self, request):
        self.request, self.in_flight, self.pending = request, set(), {}

    def send(self, name, value):
        if name in self.in_flight:
            self.pending[name] = value
            return
        self.in_flight.add(name)
        self.request(name, value, lambda result: self._done(name, result))

    def _done(self, name, result):
        self.in_flight.discard(name)
        if isinstance(result, Exception):
            STATE["error"] = "Setting %s: %s" % (name, result)
            STATE["settings"] = None        # Blender now shows a value the app doesn't have
        if name in self.pending:
            self.send(name, self.pending.pop(name))
        tag_redraw()


_sender = Sender(lambda name, value, done: client().request("settings-blender-set", done, name=name, value=value))


def _updated(name):
    def update(self, context):
        if _syncing[0] or not settings_read():      # never saves a value that wasn't read
            return
        value = getattr(self, name)
        _sender.send(name, str(value).lower() if isinstance(value, bool) else str(value))
    return update


def _build(fields):
    global _cls
    annotations = {}
    for f in fields:
        kw = {"name": f["label"], "description": f.get("description") or "", "update": _updated(f["name"])}
        if f["type"] == "bool":
            annotations[f["name"]] = bpy.props.BoolProperty(**kw)
        elif f["type"] == "int":
            annotations[f["name"]] = bpy.props.IntProperty(min=int(f.get("min") or 0), max=int(f.get("max") or 100), **kw)
        elif f["type"] == "float":
            annotations[f["name"]] = bpy.props.FloatProperty(min=f.get("min") or 0.0, max=f.get("max") if f.get("max") is not None else 100.0,
                                                             precision=2, **kw)
        elif f["type"] == "enum":
            items = [(c, l, "") for c, l in zip(f["choices"], f.get("labels") or f["choices"])]
            annotations[f["name"]] = bpy.props.EnumProperty(items=items, **kw)
        else:
            annotations[f["name"]] = bpy.props.StringProperty(**kw)
    _cls = type("FPEditorExportSettings", (bpy.types.PropertyGroup,), {"__annotations__": annotations})
    bpy.utils.register_class(_cls)
    bpy.types.WindowManager.fp_export_settings = bpy.props.PointerProperty(type=_cls)


def _sync(fields):
    target = bpy.context.window_manager.fp_export_settings
    _syncing[0] = True
    try:
        for f in fields:
            if f["value"] is not None:
                setattr(target, f["name"], f["value"])
    finally:
        _syncing[0] = False


def ask():
    """The settings, asked once the engine is ready (the timer calls this)."""
    if STATE.get("settings") is not None:
        return
    STATE["settings"] = "asked"

    def done(result):
        if isinstance(result, Exception):
            STATE["settings"] = None
            return
        if _cls is None:
            _build(result)
            import_options.build(result)
        _sync(result)
        STATE["settings"] = result
        tag_redraw()
    client().request("settings-blender", done)


def sections(fields):
    """[(section, [field names])] in the engine's order."""
    out = []
    for f in fields:
        if not out or out[-1][0] != f["section"]:
            out.append((f["section"], []))
        out[-1][1].append(f["name"])
    return out


def _panel(ui, col):
    from .ui import fields as kit_fields
    context = bpy.context
    apps = context.preferences.apps
    # Blender's own switch (all editors): its arrows at hidden side bars cost the editor a margin on each side
    _, r = col.split("")
    kit_fields.checkbox(ui, "settings:arrows", r, apps.show_regions_visibility_toggle,
                        "Blender's Side Bar Arrows (all editors)",
                        kit_fields.write(apps, "show_regions_visibility_toggle", not apps.show_regions_visibility_toggle))
    col.separator()
    if not settings_read():
        col.label("Waiting for FP...", ui.t.text3)
        return
    got = STATE["settings"]
    target = context.window_manager.fp_export_settings
    kinds = {f["name"]: f["type"] for f in got}
    for section, names in sections(got):
        if col.section("settings:" + section, section):
            for name in names:
                col.prop(target, name, slider=kinds[name] == "float")


def open_settings(context, anchor, side="below"):
    """Export Options: FP's Blender export settings"""
    from .ui import popups
    return popups.open_panel(context, anchor, _panel, "settings", "Export Options", width=360, side=side)


@persistent
def _loaded(*_):
    STATE["settings"] = None        # a file load brings its own window manager: the values are the defaults again


def register():
    from .ui import blender as ub
    import_options.register()
    ub.OPENERS["settings"] = open_settings
    if _loaded not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_loaded)


def unregister():
    global _cls
    from .ui import blender as ub
    if _loaded in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_loaded)
    ub.OPENERS.pop("settings", None)
    import_options.unregister()
    if _cls is not None:
        del bpy.types.WindowManager.fp_export_settings
        bpy.utils.unregister_class(_cls)
        _cls = None
    STATE["settings"] = None
