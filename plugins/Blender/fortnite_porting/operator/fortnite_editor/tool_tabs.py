"""The FP tools in the editor's sidebar: the 3D viewport's FP panels registered a second time for the Fortnite editor
(copies: same draw code). The sidebar follows the main tab: the Rig tab shows the rig's panels, the Landscape tab the
landscape's, the others the scene tools. Parts the main view already does (the picker, the layer list) stay out."""
import bpy

from . import host, tabs

REST = "*"      # every main tab without tools of its own
# (panel id, main tabs it shows on), parents first: a sub-panel follows its parent
PANELS = [
    ("FPMP_PT_creature_rig", {"rig"}),
    ("FPMP_PT_rig_ik", {"rig"}),
    ("FPMP_PT_rig_hands", {"rig"}),
    ("FPMP_PT_rig_simulation", {"rig"}),
    ("FPMP_PT_landscape_biomes", {"landscape"}),
    ("FPMP_PT_landscape_features", {"landscape"}),
    ("FPMP_PT_landscape_material", {"landscape"}),
    ("FPMP_PT_effect", {REST}),
    ("FPMP_PT_shell_fur", {REST, "landscape"}),
    ("FPMP_PT_exact", {REST}),
    ("FPMP_PT_fixer_view3d", {REST}),
]
OWN = {"rig", "landscape"}
FLAT = {"FPMP_PT_landscape_layers"}      # parents left out whose sub-panels stand on their own (the parent has no body)
SUFFIX = "_fortnite"
_made = []


def shown(context, where):
    space = context.space_data
    tab = tabs.current(space) if host.is_editor(space) else None
    if tab is None:
        return False
    return tab.id in where or (REST in where and tab.id not in OWN)


def _code(base):
    """A panel class's own attributes, mixins included. Not a subclass: registering one takes the registered panel's
    Python class away, and the viewport's panel then draws empty."""
    ns = {"__doc__": base.__doc__}
    for k in reversed(base.__mro__):
        if k is object or issubclass(bpy.types.Panel, k):
            continue
        ns.update({n: v for n, v in vars(k).items() if not n.startswith("__") and n != "bl_rna"})
    return ns


def _poll(base, where):
    own = getattr(base, "poll", None)

    def poll(cls, context):
        return shown(context, where) and (own is None or own(context))
    return classmethod(poll)


def _make():
    made = set()
    for idname, where in PANELS:
        base = getattr(bpy.types, idname, None)
        if base is None:
            continue
        attrs = {"bl_space_type": 'NODE_EDITOR', "bl_region_type": 'UI', "bl_category": tabs.SIDEBAR,
                 "bl_idname": idname + SUFFIX, "poll": _poll(base, where)}
        parent = getattr(base, "bl_parent_id", "")
        if parent in made:
            attrs["bl_parent_id"] = parent + SUFFIX
        elif parent in FLAT:
            attrs["bl_parent_id"] = ""      # a panel of its own
        elif parent:
            continue        # its parent is left out of the editor
        cls = type(base.__name__ + "Fortnite", (bpy.types.Panel,), {**_code(base), **attrs})
        try:
            bpy.utils.register_class(cls)
        except (RuntimeError, TypeError, ValueError) as e:
            print("[fortnite editor] %s not in the sidebar: %s" % (idname, e))
            continue
        _made.append(cls)
        made.add(idname)
    return None


def register():
    # after the plugin's own panels, which register around this module
    bpy.app.timers.register(_make, first_interval=0.1, persistent=True)     # Blender started with a .blend loads it first


def unregister():
    if bpy.app.timers.is_registered(_make):
        bpy.app.timers.unregister(_make)
    while _made:
        try:
            bpy.utils.unregister_class(_made.pop())
        except RuntimeError:
            pass
