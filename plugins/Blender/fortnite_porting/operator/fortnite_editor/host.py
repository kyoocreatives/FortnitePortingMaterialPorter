"""The editor's home in stock Blender: a node tree type whose node editor it takes over. The tree's own node editor
draws nothing of its own (an opaque fill hides the grid), its header and sidebar are the editor's, and its keymap items
come before the node editor's. Each tab has its own tree and an area shows its tab's tree: switching tabs changes only
what the area shows (UI data, which Ctrl+Z never touches), so each area keeps its own tab through undo.
Other node editors (shaders, geometry nodes...) are left as they are."""
import bpy
from bpy.app.handlers import persistent

TREE = "FortniteEditorTree"
WHEEL = {"WHEELUPMOUSE", "WHEELDOWNMOUSE", "MIDDLEMOUSE"}       # View2D takes these before the editor's keymap
ARROWS = {"LEFT_ARROW", "RIGHT_ARROW", "UP_ARROW", "DOWN_ARROW"}       # and Frames (frame offset, keyframe jump) these
on_draw = None          # the editor's main-region drawing (__init__ sets it)
on_header = None        # the editor's header drawing
on_inset = None         # (area, region, left, right) -> the tab view's (left, right): the shell's columns
_handle = None
_header_draw = None
_hidden = []            # (class, its own poll or None): node editor panels hidden for our tree
OURS = __name__.rsplit(".", 1)[0]       # the editor's package: its own panels stay
_last = {}              # space pointer -> its tab: given back when an undo removed the tab's tree


class FortniteEditorTree(bpy.types.NodeTree):
    """FP's assets, game files, maps, rigs and landscapes"""
    bl_idname = TREE
    bl_label = "Fortnite"
    bl_icon = 'WORLD'
    bl_use_group_interface = False      # no Group tab (the node group's sockets) in the sidebar
    tab: bpy.props.StringProperty(name="Tab")      # set once, when the tab's tree is made


def is_editor(space):
    return space is not None and space.type == 'NODE_EDITOR' and space.tree_type == TREE


def is_editor_area(area):
    return area.type == 'NODE_EDITOR' and area.ui_type == TREE


def editor_areas(context=None):
    wm = (context or bpy.context).window_manager
    return [a for w in wm.windows for a in w.screen.areas if is_editor_area(a)]


def tab_id(space):
    tree = getattr(space, "node_tree", None)
    return tree.tab if tree is not None and tree.bl_idname == TREE else ""


def tree_for(tab):
    """The tab's tree, made (with a fake user) the first time. Not from a draw: it writes data."""
    tree = next((t for t in bpy.data.node_groups if t.bl_idname == TREE and t.tab == tab), None)
    if tree is None:
        tree = bpy.data.node_groups.new("Fortnite " + (tab.title() or "Editor"), TREE)
        tree.use_fake_user = True
        tree.tab = tab
    return tree


def ensure_tree(space):
    """The area's tree; the first tab's ("") when it shows none."""
    if space.node_tree is None or space.node_tree.bl_idname != TREE:
        space.node_tree = tree_for(_last.get(space.as_pointer(), ""))
    return space.node_tree


def set_tab(space, tab):
    space.node_tree = tree_for(tab)
    _last[space.as_pointer()] = tab


def show_regions(window, area, drawer=False):
    """A new editor area's regions: no toolbar (the shell's nav does it), the sidebar (the Options drawer) closed.
    With the window in context: a region's visibility update needs it (from a timer without it, Blender crashes)."""
    with bpy.context.temp_override(window=window, screen=window.screen, area=area):
        space = area.spaces.active
        space.show_region_toolbar = False
        space.show_region_ui = drawer


def _give_trees():
    live = set()
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if not is_editor_area(area):
                continue
            space = area.spaces.active
            live.add(space.as_pointer())
            if space.node_tree is None:
                fresh = space.as_pointer() not in _last         # not an undo that freed the tab's tree
                ensure_tree(space)
                if fresh:
                    show_regions(window, area)      # a new editor area: open with its strip and tools
                    _last[space.as_pointer()] = ""
    for gone in set(_last) - live:
        del _last[gone]
    return None


@persistent
def _forget(*_):
    _last.clear()       # another file: its spaces may reuse these addresses


# ------------------------------------------------------------------ drawing

def overlap(area, region):
    """(left, right): pixels of `region` under the toolbar and the sidebar when they overlap it (Region Overlap)."""
    left = right = 0
    for r in area.regions:
        if r.type not in ('TOOLS', 'UI') or r.width <= 1 or r.height <= 1:
            continue
        if r.x <= region.x < r.x + r.width:
            left = max(left, r.x + r.width - region.x)
        elif region.x < r.x < region.x + region.width:
            right = max(right, region.x + region.width - r.x)
    return left, right


def inset(area, region):
    """(left, right): pixels of `region` left and right of the tab's view (the shell's columns, the regions over it),
    so list-like tabs lay out in it. Zoomable views draw under them, as the 3D viewport does."""
    left, right = overlap(area, region)
    return on_inset(area, region, left, right) if on_inset is not None else (left, right)


def _draw():
    context = bpy.context
    if not is_editor(context.space_data):
        return
    region = context.region
    import gpu
    from gpu_extras.batch import batch_for_shader
    w, h = region.width, region.height
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    batch = batch_for_shader(shader, 'TRIS', {"pos": [(0, 0), (w, 0), (w, h), (0, h)]}, indices=[(0, 1, 2), (0, 2, 3)])
    shader.uniform_float("color", (*context.preferences.themes[0].properties.space.back, 1.0))
    batch.draw(shader)
    if on_draw is not None:
        on_draw()


def _header(self, context):
    space = context.space_data
    if not is_editor(space):
        return _header_draw(self, context)
    if space.node_tree is None and not bpy.app.timers.is_registered(_give_trees):
        bpy.app.timers.register(_give_trees, first_interval=0.0)
    if on_header is not None:
        on_header(self.layout, context)


# ------------------------------------------------------------------ the node editor's own panels, hidden for our tree

def _refuse(before):
    def poll(cls, context):
        return not is_editor(context.space_data) and (before is None or before(context))
    poll._fp_refuse = True
    return poll


def _id(cls):
    return cls.__dict__.get("bl_idname") or getattr(cls, "bl_idname", "") or cls.__name__     # unset: the class name


def _all_panels():
    seen, todo = [], list(bpy.types.Panel.__subclasses__())
    while todo:
        c = todo.pop()
        if c not in seen:
            seen.append(c)
            todo.extend(c.__subclasses__())
    return seen


def _hide_node_panels():
    """Every node editor sidebar / toolbar panel there is now (Blender's and other add-ons'), refused for our tree.
    A panel registered with a poll has it looked up at each call: assigning one is enough. One without a poll is
    registered again with it, only when nothing hangs under it (Blender drops a re-registered parent's children)."""
    panels = _all_panels()
    parents = {getattr(c, "bl_parent_id", "") for c in panels if getattr(c, "is_registered", False)}
    for c in panels:
        if (getattr(c, "bl_space_type", "") != 'NODE_EDITOR' or getattr(c, "bl_region_type", "") not in ('UI', 'TOOLS')
                or not getattr(c, "is_registered", False) or getattr(c, "bl_parent_id", "")
                or c.__module__.startswith(OURS) or any(h is c for h, _, _ in _hidden)):
            continue
        before = getattr(c, "poll", None)
        if before is not None:
            _hidden.append((c, c.__dict__.get("poll"), False))
            c.poll = classmethod(_refuse(before))
        elif _id(c) not in parents:
            try:
                bpy.utils.unregister_class(c)
                c.poll = classmethod(_refuse(None))
                bpy.utils.register_class(c)
                _hidden.append((c, None, True))
            except Exception as e:      # put it back as it was: never leave Blender's UI short of a panel
                print("[fortnite editor] left %s as it is: %s" % (_id(c), e))
                if "poll" in c.__dict__:
                    del c.poll
                if not getattr(c, "is_registered", False):
                    bpy.utils.register_class(c)


def _show_node_panels():
    while _hidden:
        c, own, again = _hidden.pop()
        if not getattr(getattr(c, "poll", None), "_fp_refuse", False):
            continue        # someone wrapped it after us: theirs stays
        if again and getattr(c, "is_registered", False):
            bpy.utils.unregister_class(c)
        if own is not None:
            c.poll = own
        elif "poll" in c.__dict__:
            del c.poll
        if again:
            bpy.utils.register_class(c)


# ------------------------------------------------------------------ keymaps

class FP_OT_editor_press(bpy.types.Operator):
    """A press nothing of the editor's took (empty space): ends here, so the node editor's box select doesn't start on
    the empty tree (each one an undo step)"""
    bl_idname = "fp.editor_press"
    bl_label = "Editor Press"
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return is_editor(context.space_data)

    def invoke(self, context, event):
        return {'FINISHED'}


_swallow = []


def swallow_presses(kc):
    """After every tab's items: the left and right presses they pass on stop in the editor."""
    add = adder(kc, _swallow)
    for key in ('LEFTMOUSE', 'RIGHTMOUSE'):
        for mods in ({}, {"shift": True}, {"ctrl": True}, {"alt": True}):
            add(FP_OT_editor_press.bl_idname, key, 'PRESS', **mods)


def adder(kc, made):
    """add(idname, type, value, **mods) -> the item, in the keymap that sees the event first; (keymap, item) pairs go
    into `made` for unregister. Operators keep their own tab check: they also run in other node editors."""
    editor = kc.keymaps.new("Node Editor", space_type='NODE_EDITOR')
    view2d = kc.keymaps.new("View2D", space_type='EMPTY')
    frames = kc.keymaps.new("Frames", space_type='EMPTY')

    def add(idname, type, value, **mods):
        km = view2d if type in WHEEL else frames if type in ARROWS else editor
        kmi = km.keymap_items.new(idname, type, value, **mods)
        made.append((km, kmi))
        return kmi
    return add


def register():
    global _handle, _header_draw
    if _handle is not None:
        return      # registered already (a reload that skipped unregister)
    bpy.utils.register_class(FortniteEditorTree)
    bpy.utils.register_class(FP_OT_editor_press)
    _handle = bpy.types.SpaceNodeEditor.draw_handler_add(_draw, (), 'WINDOW', 'POST_PIXEL')
    funcs = bpy.types.NODE_HT_header._dyn_ui_initialize()       # the list other add-ons append to
    if _header not in funcs:
        _header_draw, funcs[0] = funcs[0], _header      # Blender's own entry: their appends and removes keep working
    _hide_node_panels()
    if _forget not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_forget)


def unregister():
    global _handle, _header_draw
    if _handle is None:
        return
    _show_node_panels()
    funcs = getattr(bpy.types.NODE_HT_header.draw, "_draw_funcs", [])
    if _header in funcs and _header_draw is not None:
        funcs[funcs.index(_header)] = _header_draw
    _header_draw = None
    bpy.types.SpaceNodeEditor.draw_handler_remove(_handle, 'WINDOW')
    _handle = None
    if _forget in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_forget)
    if bpy.app.timers.is_registered(_give_trees):
        bpy.app.timers.unregister(_give_trees)
    for km, kmi in _swallow:
        try:
            km.keymap_items.remove(kmi)
        except (ReferenceError, RuntimeError):
            pass
    _swallow.clear()
    bpy.utils.unregister_class(FP_OT_editor_press)
    bpy.utils.unregister_class(FortniteEditorTree)
