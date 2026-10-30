"""The pop-ups' Blender side: one window modal that hears every event while a pop-up is open (mouse through the
router on the pop-up layer, keys to popup.key), and fp.popup_open for Blender header buttons. handle() takes any
object with an event's fields, so scenarios feed it synthetic events."""
import time

import bpy

from .. import host
from . import blender, popup, popups, router
from .rect import Rect

MOUSE = {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE', 'LEFTMOUSE', 'RIGHTMOUSE', 'MIDDLEMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE',
         'TRACKPADPAN', 'TRACKPADZOOM', 'MOUSEROTATE', 'MOUSESMARTZOOM', 'WINDOW_DEACTIVATE', 'TIMER', 'TIMER_REPORT',
         'TIMERREGION', 'NONE'}


def _area():
    """The editor area whose pop-ups are open."""
    for a in host.editor_areas():
        if blender.STACKS.get(a.as_pointer()):
            return a
    return None


def _run(context, effects):
    for effect in effects:
        if effect[0] == "run" and effect[1] is not None:
            try:
                effect[1](context)
            except Exception as e:      # an item's action failed: say so, keep going
                print("[fp popup] %s" % e)
                from .. import logbook
                logbook.write("Pop-up: %s" % e, "warning")
        elif effect[0] == "clipboard":
            context.window_manager.clipboard = effect[1]


def handle(context, area, ev):
    """One event while pop-ups are open in `area`: 'eat' (it ends here) or 'pass' (Blender gets it too)."""
    region = blender._window_region(area)
    key = area.as_pointer()
    stack, st, frame = blender.STACKS.get(key), blender.store(area), blender.FRAMES.get(key)
    if not stack or region is None:
        return "pass"
    x, y = ev.mouse_x - region.x, ev.mouse_y - region.y
    inside = 0 <= x < region.width and 0 <= y < region.height
    kind, value = ev.type, ev.value
    out = []
    if kind == 'WINDOW_DEACTIVATE':
        stack.close_all()
        out = [("redraw",)]
    elif kind in ('MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'):
        if st.dragging is not None:
            out = router.route(frame, st, router.Event("drag", x, y))
        elif inside:
            out = router.route(frame, st, router.Event("move", x, y, time=time.time()))
        if not inside:
            return "pass"
    elif kind == 'LEFTMOUSE':
        if value == 'RELEASE':
            out = router.route(frame, st, router.Event("release", x, y))
        elif not inside:
            stack.close_all()
            out = [("redraw",)]
        else:
            if popup.press(stack, x, y) == "closed":
                out = [("redraw",)]
            else:
                if frame is not None:
                    frame.block(len(stack))
                hit = frame.at(x, y) if frame is not None else None
                if st.focus is not None and (hit is None or hit.id != st.focus):
                    popup.commit(st)
                if value == 'DOUBLE_CLICK':
                    out = router.route(frame, st, router.Event("double", x, y))
                    if not any(e[0] == "run" for e in out):
                        out = router.route(frame, st, router.Event("press", x, y))
                else:
                    out = router.route(frame, st, router.Event("press", x, y))
    elif kind == 'RIGHTMOUSE':
        if value == 'PRESS' and (not inside or popup.press(stack, x, y) == "closed"):
            stack.close_all()
            out = [("redraw",)]
    elif kind in ('WHEELUPMOUSE', 'WHEELDOWNMOUSE'):
        if not inside:
            return "pass"
        notch = -1 if kind == 'WHEELUPMOUSE' else 1
        top = stack.top
        if ev.ctrl and top is not None and top.rect is not None and top.rect.contains(x, y) and popups.zoom(top, notch):
            out = [("redraw",)]
        else:
            out = router.route(frame, st, router.Event("wheel", x, y, notch))
    elif kind in MOUSE:
        return "pass"
    elif value in ('PRESS', 'REPEAT') or getattr(ev, "is_repeat", False):
        wm = context.window_manager
        clip = [wm.clipboard]
        out = popup.key(stack, st, popup.Key(kind, ev.unicode or "", ev.ctrl or ev.oskey, ev.shift, clip))
    with context.temp_override(area=area, region=region):
        _run(context, out)
    if frame is not None:       # what closed stops blocking now, not at the next draw (a click may come first)
        frame.block(len(stack))
    area.tag_redraw()
    return "eat"


class FP_OT_popup(bpy.types.Operator):
    """The Fortnite editor's pop-ups"""
    bl_idname = "fp.popup"
    bl_label = "Fortnite Editor Pop-up"
    bl_options = {'INTERNAL'}

    def invoke(self, context, event):
        if blender.MODAL["on"]:
            return {'CANCELLED'}
        blender.MODAL["on"] = True
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        area = _area()
        if area is None:
            blender.MODAL["on"] = False
            return {'FINISHED', 'PASS_THROUGH'}
        result = handle(context, area, event)
        if _area() is None:
            blender.MODAL["on"] = False
            return {'FINISHED'} if result == "eat" else {'FINISHED', 'PASS_THROUGH'}
        return {'RUNNING_MODAL'} if result == "eat" else {'PASS_THROUGH'}

    def cancel(self, context):
        blender.MODAL["on"] = False


def header_anchor(context, event):
    """Where a header button was pressed, in the area's main region: under it (or over it, a header at the bottom)."""
    region = blender._window_region(context.area)
    x = event.mouse_x - region.x
    if context.region.y >= region.y + region.height - 1:        # the header over the main region
        return Rect(x - 8, region.height, 16, 0), "below"
    return Rect(x - 8, 0, 16, 0), "above"


def anchor_from_event(context, event):
    """(anchor, side) for a pop-up an operator opens: under a header button, or at the mouse in the main region."""
    region = blender._window_region(context.area)
    if context.region is not None and context.region.type == 'HEADER':
        return header_anchor(context, event)
    return Rect(event.mouse_x - region.x, event.mouse_y - region.y, 0, 0), "at"


class FP_OT_popup_open(bpy.types.Operator):
    """Open this menu"""
    bl_idname = "fp.popup_open"
    bl_label = "Open"
    bl_options = {'INTERNAL'}
    name: bpy.props.StringProperty(options={'SKIP_SAVE'})

    @classmethod
    def description(cls, context, properties):
        fn = blender.OPENERS.get(properties.name)
        return (fn.__doc__ or "").strip().split("\n")[0] if fn is not None else ""

    def invoke(self, context, event):
        fn = blender.OPENERS.get(self.name)
        if fn is None or not host.is_editor(context.space_data):
            return {'CANCELLED'}
        anchor, side = header_anchor(context, event)
        with context.temp_override(region=blender._window_region(context.area)):
            fn(context, anchor, side)
        return {'FINISHED'}


classes = (FP_OT_popup, FP_OT_popup_open)
