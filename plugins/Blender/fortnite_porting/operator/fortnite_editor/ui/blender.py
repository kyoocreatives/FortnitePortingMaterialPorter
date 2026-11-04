"""The shell's Blender side: each editor area's last frame and input state, its draw (after the tab's view), and one
operator that routes the mouse to it (moves, presses with their release, double clicks, the wheel) before the tabs'
own keymap items. Command keys (G, R, S, I, Home...) stay the tabs' named keymap items."""
import time

import bpy
from bpy.app.handlers import persistent

from .. import host
from . import kit, paint, popup, router, shell
from .rect import Rect
from .theme import Theme

FRAMES, STORES = {}, {}         # area pointer -> its last Frame / its Store
STACKS = {}         # area pointer -> its popup.Stack
MODAL = {"on": False}       # FP_OT_popup is running (one serves every area)
OPENERS = {}        # name -> fn(context, anchor, side): what fp.popup_open opens (header buttons)
HELD = {"ctrl": False, "shift": False}      # the modifier keys of the event being routed (actions read them)
_keymaps = []


def theme(context=None):
    prefs = (context or bpy.context).preferences
    return Theme(prefs.system.ui_scale, prefs.themes[0])


def store(area):
    found = STORES.get(area.as_pointer())
    if found is None:
        from . import fields
        found = STORES[area.as_pointer()] = router.Store()
        found.on_commit = fields.commit
    return found


TOGGLE = 14     # px (UI scale 1) Blender keeps at a hidden toolbar's / sidebar's edge for its show arrow


def edges(area, region):
    """(left, right, frame) px: Blender's regions over the region's sides, and while Blender shows its arrows at
    hidden side bars, their width (the shell's margin then: even all round, the arrows in it)."""
    left, right = host.overlap(area, region)
    prefs = bpy.context.preferences
    if not prefs.apps.show_regions_visibility_toggle:       # no arrows (Blender's switch, all editors)
        return left, right, 0
    return left, right, TOGGLE * prefs.system.ui_scale


DEFAULT_WIDTHS = {}         # (tab id, "side" / "info") -> the width last dragged anywhere: new areas start with it


def widths(area, tab):
    """(list column, info column) widths the area's columns were dragged to for this tab (None: their defaults)."""
    w = store(area).widths
    key = tab.id if tab is not None else None
    return tuple(w.get((key, c), DEFAULT_WIDTHS.get((key, c))) for c in ("side", "info"))


STATUS = [None]        # fn(ui, cols): the editor's status bar (set by the editor: the shell only lays out its row)


def columns(area, region, tab, context=None):
    """The shell's columns in the area's main region, as its draw lays them out."""
    return shell.columns(tab, region.width, region.height, theme(context), *edges(area, region), *shown(tab, context),
                         *widths(area, tab), status=STATUS[0] is not None)


def well(area, region, tab):
    """The tab's view's rect in the region (the shell's columns around it, Blender's regions over it)."""
    return columns(area, region, tab)["well"]


def folded(context, tab):
    """{"side", "info"}: the tab's columns the area is too narrow for (their essentials go to the header)."""
    area = context.area
    region = next((r for r in area.regions if r.type == 'WINDOW'), None)
    if region is None or tab is None:
        return set()
    cols = shell.columns(tab, region.width, region.height, theme(context), *edges(area, region),
                         side_w=widths(area, tab)[0], info_w=widths(area, tab)[1], status=STATUS[0] is not None)
    return {name for name in ("side", "info") if getattr(tab, name) is not None and cols[name] is None}


def shown(tab, context=None):
    """(has_info, has_side): whether the tab's info and list columns have something to show."""
    context = context or bpy.context
    return tuple(tab is None or fn is None or bool(fn(context)) for fn in (getattr(tab, "has_info", None),
                                                                          getattr(tab, "has_side", None)))


def inset(area, region, left, right):
    from .. import tabs
    w = well(area, region, tabs.current(area.spaces.active))
    return w.x, region.width - w.right


def ground(context, tab):
    """The well's ground, under the tab's view (views that draw their own background cover it)."""
    area, region = context.area, context.region
    paint.begin(region)
    paint.rect(well(area, region, tab), theme(context).well, theme(context).r_card)
    paint.end()


def draw(context, tabs_list, current, actions):
    area, region = context.area, context.region
    from .frame import Frame
    frame = Frame(current.id if current else None)
    ui = kit.Ui(theme(context), frame, store(area), time.time())
    if STATUS[0] is not None:
        actions = dict(actions, status=STATUS[0])
    paint.begin(region)
    try:
        cols = shell.draw(ui, tabs_list, current, region.width, region.height, actions, *edges(area, region),
                          *shown(current, context), *widths(area, current), resize=_resizers(area, current))
        _draw_popups(ui, area, region, cols["status"])
        if ui.tooltip(region.width, region.height):
            _redraw_later(area, 0.55)
    finally:
        paint.end()
    FRAMES[area.as_pointer()] = frame


def _card(ui, r):
    """A pop-up's ground: a soft shadow, the card (concentric with the rows inside it: row radius + padding)."""
    t = ui.t
    radius = t.r_small + t.pad
    for grow, alpha in ((8 * t.s, 0.08), (3 * t.s, 0.16)):
        paint.rect(Rect(r.x - grow, r.y - grow - 2 * t.s, r.w + 2 * grow, r.h + 2 * grow), (0, 0, 0, alpha), radius + grow)
    paint.rect(r, t.surface, radius, edge=t.text1[:3] + (0.1,), border=t.s)


def _draw_popups(ui, area, region, status=None):
    stack = STACKS.get(area.as_pointer())
    if not stack:
        return
    t = ui.t
    left, right, _arrows = edges(area, region)
    m = shell.margin(t, _arrows)        # off Blender's side bars and their arrows, the shell's margin from the edges
    bounds = Rect(left + m, m, region.width - left - right - 2 * m, region.height - 2 * m)
    if status is not None:      # over the status bar's row only what can't fit elsewhere: above it
        bounds = Rect(bounds.x, status.top + t.pad, bounds.w, bounds.top - status.top - t.pad)
    for n, p in enumerate(stack.popups):
        if p.modal:
            paint.rect(Rect(0, 0, region.width, region.height), (0, 0, 0, 0.35))
        ui.frame.layer, ui.clip = n + 1, None
        p.rect = popup.place(p.anchor, p.size, p.side, bounds, t.pad)
        _card(ui, p.rect)
        p.draw(ui, p.rect.pad(t.pad), p)
    ui.frame.block(len(stack))
    ui.frame.layer, ui.clip = 0, None
    if ui.again:
        _redraw_later(area, 0.0)


def _window_region(area):
    return next((r for r in area.regions if r.type == 'WINDOW'), None)


def open(context, p):
    """Open pop-up `p` in the context's editor area (its main region), over the shell; the pop-ups of other areas
    close. The modal that hears the keys and the mouse starts with the first."""
    area = context.area
    key = area.as_pointer()
    for other, st in STACKS.items():
        if other != key and st:
            st.close_all()
    STACKS.setdefault(key, popup.Stack()).open(p)
    store(area).hot = None
    area.tag_redraw()
    if not MODAL["on"]:
        with context.temp_override(window=context.window, area=area, region=_window_region(area)):
            bpy.ops.fp.popup('INVOKE_DEFAULT')
    return p


def listen(context):
    """The modal that hears keys, for a field in a tab that just took them (no pop-up started it)."""
    if not MODAL["on"] and context.area is not None:
        with context.temp_override(window=context.window, area=context.area, region=_window_region(context.area)):
            bpy.ops.fp.popup('INVOKE_DEFAULT')


def close_all():
    for st in STACKS.values():
        st.close_all()
    for a in host.editor_areas():
        a.tag_redraw()


def is_open(area):
    return bool(STACKS.get(area.as_pointer()))


def _resizers(area, tab):
    """(set the list column's width, set the info column's width) for this area and tab: kept in its store."""
    key = tab.id if tab is not None else None

    def setter(column):
        def run(context, w):
            store(area).widths[(key, column)] = DEFAULT_WIDTHS[(key, column)] = max(0.0, w)
            area.tag_redraw()
        return run
    return setter("side"), setter("info")


def _redraw_later(area, delay):
    key = area.as_pointer()

    def tick():
        for a in host.editor_areas():
            if a.as_pointer() == key:
                a.tag_redraw()
        return None
    if not bpy.app.timers.is_registered(tick):
        bpy.app.timers.register(tick, first_interval=delay)


class FP_OT_ui_event(bpy.types.Operator):
    """The Fortnite editor's buttons and lists"""
    bl_idname = "fp.ui_event"
    bl_label = "Fortnite Editor Input"
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return host.is_editor(context.space_data) and context.region is not None and context.region.type == 'WINDOW'

    def _event(self, event):
        x, y = event.mouse_region_x, event.mouse_region_y
        if event.type == 'MOUSEMOVE':
            return router.Event("move", x, y, time=time.time())
        if event.type in {'WHEELUPMOUSE', 'WHEELDOWNMOUSE'}:
            if event.ctrl or event.shift or event.alt:
                return None         # zoom and the like: the tab's own
            return router.Event("wheel", x, y, -1 if event.type == 'WHEELUPMOUSE' else 1)
        if event.type == 'LEFTMOUSE':
            kind = {"PRESS": "press", "RELEASE": "release", "DOUBLE_CLICK": "double"}.get(event.value)
            return router.Event(kind, x, y) if kind else None
        return None

    def _run(self, context, effects):
        result = None
        for effect in effects:
            what = effect[0]
            if what == "redraw":
                context.area.tag_redraw()
            elif what == "run":
                try:
                    effect[1](context)
                except Exception as e:      # a button's action failed: say so, keep the editor going
                    self.report({'WARNING'}, str(e))
                context.area.tag_redraw()
            elif what == "capture":
                result = 'capture'
            elif what == "release":
                result = 'release'
            elif what == "pass":
                result = result or 'pass'
            elif what == "consume":
                result = result or 'consume'
        return result

    def _view_hover(self, context, frame, ev):
        """The tab's view hears the mouse when it's over the view, and once more when it leaves it."""
        from .. import tabs
        tab = tabs.current(context.space_data)
        if tab is None or tab.hover is None:
            return
        inside = (frame is not None and frame.well is not None and frame.well.contains(ev.x, ev.y)
                  and frame.at(ev.x, ev.y) is None)
        if tab.hover(context, ev.x if inside else None, ev.y if inside else None):
            context.region.tag_redraw()

    def invoke(self, context, event):
        HELD.update(ctrl=event.ctrl, shift=event.shift)
        ev = self._event(event)
        if ev is None:
            return {'PASS_THROUGH'}
        frame = FRAMES.get(context.area.as_pointer())
        if ev.type == "move":
            self._view_hover(context, frame, ev)
        result = self._run(context, router.route(frame, store(context.area), ev, context.preferences.system.ui_scale))
        if result == 'capture':
            context.window_manager.modal_handler_add(self)
            return {'RUNNING_MODAL'}
        if result in (None, 'consume'):
            return {'FINISHED'} if ev.type != "move" else {'PASS_THROUGH'}
        return {'PASS_THROUGH'}

    def modal(self, context, event):
        if event.type == 'MOUSEMOVE':       # a held drag handle follows the mouse
            ev = router.Event("drag", event.mouse_region_x, event.mouse_region_y)
            self._run(context, router.route(FRAMES.get(context.area.as_pointer()), store(context.area), ev))
            return {'RUNNING_MODAL'}
        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            ev = router.Event("release", event.mouse_region_x, event.mouse_region_y)
            self._run(context, router.route(FRAMES.get(context.area.as_pointer()), store(context.area), ev))
            return {'FINISHED'}
        if event.type in {'ESC', 'RIGHTMOUSE'}:
            store(context.area).pressed = store(context.area).dragging = None
            context.area.tag_redraw()
            return {'CANCELLED'}
        return {'RUNNING_MODAL'}


@persistent
def _forget(*_):
    FRAMES.clear()
    STORES.clear()
    STACKS.clear()
    MODAL["on"] = False
    kit.forget_glyphs()


def register_keys(kc):
    """After every tab's keymap items (Blender merges add-on items in reverse: these come first)."""
    add = host.adder(kc, _keymaps)
    for key, value in (('MOUSEMOVE', 'ANY'), ('LEFTMOUSE', 'PRESS'), ('LEFTMOUSE', 'DOUBLE_CLICK'),
                       ('WHEELUPMOUSE', 'PRESS'), ('WHEELDOWNMOUSE', 'PRESS')):
        kmi = add(FP_OT_ui_event.bl_idname, key, value)
        kmi.any = True


def register():
    from . import popup_ops
    bpy.utils.register_class(FP_OT_ui_event)
    for c in popup_ops.classes:
        bpy.utils.register_class(c)
    host.on_inset = inset
    if _forget not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_forget)


def unregister():
    for km, kmi in _keymaps:
        try:
            km.keymap_items.remove(kmi)
        except (ReferenceError, RuntimeError):
            pass
    _keymaps.clear()
    if _forget in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_forget)
    _forget()
    host.on_inset = None
    from . import popup_ops
    for c in reversed(popup_ops.classes):
        bpy.utils.unregister_class(c)
    bpy.utils.unregister_class(FP_OT_ui_event)
