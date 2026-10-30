"""Pointer input against an area's last frame: hover, press, release, double click, wheel. Returns what to do as plain
effects; the Blender shell runs them in operator context. Pure: no bpy.

    route(frame, store, Event) -> [effect]: ("redraw",) ("run", callable) ("capture",) ("release",)
    (a press on a drag handle captures the mouse; "drag" events then move it)
    ("pass",): Blender and the tab's view get the event; ("consume",): it ends here (over the shell)
"""
WHEEL_STEP = 48.0       # px per wheel notch (times the UI scale)


class Event:
    __slots__ = ("type", "x", "y", "delta", "time")

    def __init__(self, type, x, y, delta=0, time=0.0):
        self.type, self.x, self.y, self.delta, self.time = type, x, y, delta, time      # type: move press release double wheel drag


class Store:
    """One area's input state: what's under the mouse, what a press holds, scroll offsets, the tooltip's start."""

    def __init__(self):
        self.hot, self.pressed, self.scroll, self.tip_since = None, None, {}, 0.0
        self.over = None            # the scrolled list under the mouse (a compact column shows its bar then)
        self.dragging = None        # the drag handle a press holds, and where the press was
        self.press_at = (0, 0)
        self.widths = {}            # (tab id, "side" / "info") -> a column's width as dragged
        self.focus = self.focus_text = None     # the field the keys go to (a pop-up's), its textedit.Text
        self.on_commit = None       # fn(id, text): a focused field's edit kept

    def offset(self, id):
        return self.scroll.get(id, 0.0)


def route(frame, store, ev, scale=1.0):
    if frame is None:
        return [("pass",)]
    if ev.type == "move":
        hit = frame.at(ev.x, ev.y)
        hot = hit.id if hit is not None else None
        out = [("pass",)]        # hover never ends an event: the tab's view and Blender see the move too
        lst = frame.scroller(ev.x, ev.y)
        over = lst.scroll[0] if lst is not None else None
        if hot != store.hot or over != store.over:
            if hot != store.hot:
                store.hot, store.tip_since = hot, ev.time
            store.over = over
            out.insert(0, ("redraw",))
        return out
    if ev.type == "press":
        hit = frame.at(ev.x, ev.y)
        if hit is None:
            return [("consume",)] if frame.on_shell(ev.x, ev.y) else [("pass",)]
        store.tip_since = float("inf")      # a click hides the tooltip until the mouse moves to something else
        if hit.kind == "row":
            return [("redraw",)] + ([("run", hit.action)] if hit.action else [])
        store.pressed = hit.id
        store.dragging, store.press_at = hit.drag, (ev.x, ev.y)
        return [("redraw",), ("capture",)]
    if ev.type == "drag":
        if store.dragging is None:
            return []
        fn, dx, dy = store.dragging, ev.x - store.press_at[0], ev.y - store.press_at[1]
        return [("redraw",), ("run", lambda context: fn(context, dx, dy))]
    if ev.type == "release":
        pressed, store.pressed, store.dragging = store.pressed, None, None
        hit = frame.at(ev.x, ev.y)
        out = [("redraw",), ("release",)]
        if pressed is not None and hit is not None and hit.id == pressed and hit.action:
            out.append(("run", hit.action))
        return out
    if ev.type == "double":
        hit = frame.at(ev.x, ev.y)
        if hit is not None and hit.double:
            return [("redraw",), ("run", hit.double)]
        return [("consume",)] if hit is not None or frame.on_shell(ev.x, ev.y) else [("pass",)]
    if ev.type == "wheel":
        hit = frame.scroller(ev.x, ev.y)
        if hit is None:
            return [("consume",)] if frame.on_shell(ev.x, ev.y) else [("pass",)]
        sid, most = hit.scroll
        before = store.offset(sid)
        store.scroll[sid] = min(max(0.0, before + ev.delta * WHEEL_STEP * scale), most)
        return [("redraw",)] if store.scroll[sid] != before else [("consume",)]
    return [("pass",)]
