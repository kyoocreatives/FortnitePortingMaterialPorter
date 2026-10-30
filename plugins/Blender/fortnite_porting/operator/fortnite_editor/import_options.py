"""Import options: LOD, rig, texture size, FP shaders and materials for the editor's imports only, over the Export
Options (which stay as they are). The engine applies them to a copy of the Blender export settings for each export
(settings=Name:Value;... on browse-export, files-export, maps-export).
They stay set until Reset, never saved: imports come in runs (a set of props at LOD 1), and the Import button names
them while they are set, so they can't go unnoticed."""
import bpy

from .state import tag_redraw
from .ui import paint

# (setting, short form for the Import button: value -> text); MaxTextureSize goes as MapTextureSize too (maps, prefabs)
OPTIONS = (
    ("TargetLOD", lambda v: "LOD %d" % v),
    ("RigType", lambda v: "%s Rig" % v),
    ("MaxTextureSize", lambda v: "Full Textures" if v == "Full" else "Textures %s" % v.removeprefix("Size")),
    ("PreferFPShaders", lambda v: "FP Shaders" if v else "Exact Shaders"),
    ("ExportMaterials", lambda v: "Materials" if v else "No Materials"),
)
MAP_OPTIONS = ("TargetLOD", "MaxTextureSize", "ExportMaterials")      # what a map export reads of them
LABELS = {"MaxTextureSize": "Max Texture Size", "PreferFPShaders": "Prefer FP Shaders", "ExportMaterials": "Export Materials"}
OPENED = ["assets"]     # where the popover was opened from: assets, files or map (its rows, its texture setting)
_cls = None


def _take_global(name):
    def update(self, context):
        if getattr(self, "use_" + name):        # starts from the Export Options' value
            setattr(self, name, getattr(context.window_manager.fp_export_settings, _global_name(name)))
        tag_redraw()
    return update


def _redraw(self, context):
    tag_redraw()


def build(fields):
    """The options' property group, from the settings fields (settings-blender) once they came."""
    global _cls
    if _cls is not None:
        return
    by_name = {f["name"]: f for f in fields}
    annotations = {}
    for name, _short in OPTIONS:
        f = by_name.get(name)
        if f is None:
            continue
        label = LABELS.get(name, f["label"])
        annotations["use_" + name] = bpy.props.BoolProperty(name="Override " + label, description="Use this value for "
                                                             "these imports instead of the Export Options'",
                                                             update=_take_global(name))
        kw = {"name": label, "description": f.get("description") or "", "update": _redraw}
        if f["type"] == "bool":
            annotations[name] = bpy.props.BoolProperty(**kw)
        elif f["type"] == "int":
            annotations[name] = bpy.props.IntProperty(min=int(f.get("min") or 0), max=int(f.get("max") or 100), **kw)
        elif f["type"] == "enum":
            items = [(c, l, "") for c, l in zip(f["choices"], f.get("labels") or f["choices"])]
            annotations[name] = bpy.props.EnumProperty(items=items, **kw)
    _cls = type("FPEditorImportOptions", (bpy.types.PropertyGroup,), {"__annotations__": annotations})
    bpy.utils.register_class(_cls)
    bpy.types.WindowManager.fp_import_options = bpy.props.PointerProperty(type=_cls)


def _global_name(name, opened=None):
    if name == "MaxTextureSize" and (opened or OPENED[0]) == "map":
        return "MapTextureSize"
    return name


def active(wm=None, opened="assets"):
    """[(setting, value)] set now, of the ones `opened` (assets, files, map) reads."""
    options = getattr(wm or bpy.context.window_manager, "fp_import_options", None)
    if options is None:
        return []
    names = MAP_OPTIONS if opened == "map" else [n for n, _ in OPTIONS]
    return [(n, getattr(options, n)) for n in names if getattr(options, "use_" + n, False)]


def query(wm=None, opened="assets"):
    """The settings= value: "Name:Value;..." ("" with none set); the texture size for maps and prefabs as well."""
    pairs = []
    for name, value in active(wm, opened):
        text = str(value).lower() if isinstance(value, bool) else str(value)
        pairs.append("%s:%s" % (name, text))
        if name == "MaxTextureSize":
            pairs.append("MapTextureSize:%s" % text)
    return ";".join(pairs)


def summary(wm=None, opened="assets"):
    """"LOD 1, Tasty Rig": the options set now, short ("" with none)."""
    short = dict(OPTIONS)
    return ", ".join(short[n](v) for n, v in active(wm, opened))


def import_label(base, opened="assets"):
    """An Import button's text: `base`, then the options it imports with."""
    text = summary(opened=opened)
    return "%s  ·  %s" % (base, text) if text else base


def rows(wm, opened):
    """The panel's rows: [(setting, "Override <label>", set, owner, property)]; owner.property is the value shown: the
    option's own while it's set, else the Export Options' (what imports use now)."""
    options = wm.fp_import_options
    names = MAP_OPTIONS if opened == "map" else [n for n, _ in OPTIONS]
    out = []
    for name in names:
        if not hasattr(options, name):
            continue
        key = _global_name(name, opened)
        label = "Map Texture Size" if key == "MapTextureSize" else options.bl_rna.properties[name].name
        use = bool(getattr(options, "use_" + name))
        out.append((name, "Override " + label, use, options if use else wm.fp_export_settings, name if use else key))
    return out


def _value(ui, col, r, row):
    """The value field of a row, greyed while the option isn't set."""
    from .ui import fields
    from .ui.fields import Column
    from .ui.popups import open_menu_at
    from .ui.rect import Rect
    name, _label, use, owner, key = row
    if owner.bl_rna.properties[key].type == 'BOOLEAN':
        value = getattr(owner, key)
        fields.checkbox(ui, "import_options:" + name, r, value, "On" if value else "Off",
                        fields.write(owner, key, not value), enabled=use)
        return
    sub = Column(ui, Rect(r.x, r.y, r.w, r.h), col.p, open_menu=open_menu_at)
    sub.enabled = use
    sub.prop(owner, key, text="", id="import_options:" + name)


def _panel(ui, col):
    from .ui import fields
    wm = bpy.context.window_manager
    if getattr(wm, "fp_import_options", None) is None or getattr(wm, "fp_export_settings", None) is None:
        col.label("Waiting for FP...", ui.t.text3)
        return
    t = ui.t
    for row in rows(wm, OPENED[0]):
        _, r = col.split("")
        left = r.cut_left(r.w * 0.58)
        r.cut_left(t.pad)
        name, label, use = row[:3]
        fields.checkbox(ui, "import_options:use_" + name, left, use, label,
                        fields.write(wm.fp_import_options, "use_" + name, not use),
                        tip="Use this value for these imports instead of the Export Options'")
        _value(ui, col, r, row)
    col.separator()

    def reset(context):
        bpy.ops.fp.import_options_reset()
    col.button("import_options:reset", "Reset to Export Options", "mdi_Restore", reset,
               enabled=bool(active(wm, OPENED[0])))


def open_options(context, anchor, opened, side="above"):
    """Import Options: what the next imports use instead of the Export Options"""
    from .ui import popups
    OPENED[0] = opened
    return popups.open_panel(context, anchor, _panel, "import_options", "Import Options", width=360, side=side)


def open_popover(opened, anchor=None):
    """An action opening the panel (by `anchor`, else at the area's top right)."""
    def run(context):
        from .ui.rect import Rect
        r = anchor
        if r is None:
            region = context.region
            r = Rect(region.width - 40, region.height - 40, 30, 30)
        open_options(context, r, opened, "below" if anchor is None else "above")
    return run


def button(ui, r, id, opened):
    """The options' button by an Import button: outlined, its icon in the accent colour, while some are set."""
    t = ui.t
    ui.button(id, r, icon="mdi_Tune", action=open_popover(opened, r),
              tip="Import Options: LOD, rig, texture size, shaders, materials for these imports (Export Options stay)")
    if active(opened=opened):
        paint.rect(r, (0, 0, 0, 0), t.r_small, edge=t.accent, border=t.s)
        ui.icon(r, "mdi_Tune", t.accent)


class FP_OT_import_options_reset(bpy.types.Operator):
    """Import with the Export Options again"""
    bl_idname = "fp.import_options_reset"
    bl_label = "Reset to Export Options"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        options = getattr(context.window_manager, "fp_import_options", None)
        for name, _short in OPTIONS:
            if options is not None and hasattr(options, "use_" + name):
                setattr(options, "use_" + name, False)
        tag_redraw()
        return {'FINISHED'}


classes = (FP_OT_import_options_reset,)


def register():
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    global _cls
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
    if _cls is not None:
        del bpy.types.WindowManager.fp_import_options
        bpy.utils.unregister_class(_cls)
        _cls = None
