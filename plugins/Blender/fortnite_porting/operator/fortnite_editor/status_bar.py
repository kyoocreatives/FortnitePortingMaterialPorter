"""The editor's status bar (FP's: the latest step of what an import is doing, its time, a Log button) and the log
drawer that opens over the panel's bottom from it: every line, newest first, with Copy and Clear."""
import time

import bpy

from . import logbook
from .state import STATE
from .ui import paint, shell
from .ui.rect import Rect

OPEN = set()        # area pointers whose log drawer is open
WARN = (0.95, 0.75, 0.3, 1.0)
DRAWER = 0.45       # of the panel's height


def _color(t, level, busy=False):
    if level == "error":
        return t.error
    if level == "warning":
        return WARN
    return t.text1 if busy else t.text2


def draw(ui, cols):
    t, s = ui.t, ui.t.s
    bar = cols["status"]
    key = bpy.context.area.as_pointer()
    if key in OPEN:
        _drawer(ui, cols, key)
    busy = logbook.LOG["job"] is not None
    line, level = logbook.latest()
    if not busy and STATE["busy"]:      # work that reports one line (a map's export queue, writing images)
        busy, line, level = True, STATE["busy"], "info"
    r = bar.copy()

    def toggle(context):
        OPEN.symmetric_difference_update({key})
    ui.button("status:log", r.cut_right(44 * s), "Log", action=toggle, style="flat", selected=key in OPEN,
              tip="Every step of the imports (newest first)")
    r.cut_right(t.pad)
    if logbook.LOG["job"] is not None:
        elapsed = "%.1f s" % (time.time() - logbook.LOG["started"])
        paint.text_in(elapsed, r.cut_right(paint.measure(elapsed, t.caption) + t.pad), t.text3, t.caption, "right")
    dot = r.cut_left(t.pad + 6 * s)
    paint.rect(Rect(dot.x + t.pad / 2, dot.y + dot.h / 2 - 3 * s, 6 * s, 6 * s), t.accent if busy else
               _color(t, level) if level != "info" else t.text3, 3 * s)
    r.cut_left(t.gap)
    paint.text_in(line, r, _color(t, level, busy), t.text)
    progress = logbook.LOG["progress"]
    if busy and progress is not None:       # "n/m" in the latest line: how far along
        track = Rect(r.x, bar.y, r.w, 2 * s)
        paint.rect(track, t.text1[:3] + (0.08,), s)
        paint.rect(Rect(track.x, track.y, track.w * progress, track.h), t.accent, s)


def _drawer(ui, cols, key):
    """Over the panel's bottom, its bottom corners concentric with the panel's."""
    t, s = ui.t, ui.t.s
    panel = cols["panel"]
    _tr, br, _tl, bl = shell.corners(t, cols)
    r = Rect(panel.x + t.pad, panel.y + t.pad, panel.w - 2 * t.pad, panel.h * DRAWER)
    paint.rect(r, t.card[:3] + (1.0,), corners=(t.r_card, br - t.pad, t.r_card, bl - t.pad), edge=t.text1[:3] + (0.1,),
               border=1)
    from .ui.frame import Hit
    ui.frame.add(Hit("status:drawer", r, None, "row"))        # clicks here stay here (not the grid under it)
    inner = r.pad(t.r_card - t.r_small)       # the Close button in its corner, concentric with it
    head = inner.cut_top(t.row)

    def close(context):
        OPEN.discard(key)

    def copy(context):
        context.window_manager.clipboard = logbook.text()

    def clear(context):
        logbook.clear()
    ui.button("log:close", head.cut_right(t.row), icon="mdi_Close", action=close, style="flat", tip="Close")
    head.cut_right(t.gap)
    ui.button("log:clear", head.cut_right(60 * s), "Clear", action=clear, style="flat")
    head.cut_right(t.gap)
    ui.button("log:copy", head.cut_right(76 * s), "Copy", "mdi_ContentCopy", copy, "Copy the log", style="flat")
    paint.text_in("Log", head.pad(t.pad, 0), t.text1, t.title)
    inner.cut_top(t.gap)
    lines = list(reversed(logbook.LOG["lines"]))
    pitch = t.text + 2 * t.gap
    offset = ui.scroll("log:lines", inner, len(lines) * pitch)
    first = int(offset // pitch)
    y = inner.top + offset - first * pitch
    stamp = paint.measure("00:00:00", t.caption) + t.big
    for when, level, _source, text in lines[first:first + int(inner.h // pitch) + 2]:
        y -= pitch
        row = Rect(inner.x, y, inner.w, pitch)
        paint.text_in(time.strftime("%H:%M:%S", time.localtime(when)), row.cut_left(stamp).pad(t.pad, 0), t.text3, t.caption)
        paint.text_in(text, row, _color(t, level, not text.startswith(" ")), t.text)
    ui.end_scroll()
    if not lines:
        paint.text_in("Nothing yet: imports write here as they go", inner, t.text3, t.text, "center")
