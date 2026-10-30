"""What the editor's imports are doing, line by line: the plugin's own lines as it imports (heard live through
material_porter.status, the area redrawn during the blocking import), the app's lines while it exports (its
"status-log" route, polled), and the editor's own steps. The status bar shows the latest; the log drawer all of them."""
import re
import time
from collections import deque

import bpy

from .state import STATE, client, tag_redraw

MAX_LINES = 1000
POLL = 0.3      # s between asks for the app's lines while it exports
GRACE = 2.0     # s it's still asked after a job: the app writes its summary as the export returns
REDRAW = 0.2    # s between forced redraws during an import (Blender draws nothing until it returns otherwise)
LOG = {"lines": deque(maxlen=MAX_LINES), "since": None, "polled": 0.0, "asking": False, "route": True,
       "drawn": 0.0, "job": None, "started": 0.0, "ended": 0.0, "progress": None, "importing": False}
_CHATTER = re.compile(r"^Blender asked for (?!\S*export)")      # the bridge's per-request lines, but exports
_COUNT = re.compile(r"(\d+)\s*/\s*(\d+)")


def write(text, level="info", source="editor", when=None):
    """A line (newest last); an indented one is a detail, the others are steps the status bar shows. `when`: the time
    it happened, when that was earlier (the app's lines come a poll late): it goes in its place."""
    text = str(text).rstrip()
    if not text:
        return
    lines = LOG["lines"]
    entry = (when or time.time(), level, source, text)
    at = len(lines)
    while when is not None and at > 0 and lines[at - 1][0] > when:
        at -= 1
    if at == len(lines):
        lines.append(entry)
    else:
        if len(lines) == lines.maxlen:      # a full deque refuses insert: the oldest goes first
            lines.popleft()
            at = max(0, at - 1)
        lines.insert(at, entry)
    m = _COUNT.search(text)
    if m and int(m.group(2)) > 0:
        LOG["progress"] = min(1.0, int(m.group(1)) / int(m.group(2)))
    elif not text.startswith(" "):
        LOG["progress"] = None


def level_of(text):
    low = text.lower().lstrip()
    if low.startswith("error") or "failed" in low:
        return "error"
    return "warning" if low.startswith("warning") else "info"


def latest():
    """The newest step (unindented line), or "Ready"."""
    for _t, level, _s, text in reversed(LOG["lines"]):
        if not text.startswith(" "):
            return text, level
    return "Ready", "info"


def begin(what):
    """A job starts (an export, then its import): the status bar counts its time."""
    LOG["job"], LOG["started"], LOG["progress"] = what, time.time(), None
    write(what)


def end():
    LOG["job"], LOG["progress"], LOG["ended"] = None, None, time.time()


def clear():
    LOG["lines"].clear()
    LOG["progress"] = None


def text():
    return "\n".join("%s  %s" % (time.strftime("%H:%M:%S", time.localtime(t)), line) for t, _l, _s, line in LOG["lines"])


# ------------------------------------------------------------------ the plugin's lines, live

def _heard(line, state):
    """material_porter.status's listener: called on the main thread while the plugin imports."""
    if state == "begin":
        LOG["importing"] = True
    if line:
        if line.startswith("[Material Porter] "):       # the builder's notes outside an import keep FP's tag
            line = line[len("[Material Porter] "):]
        write(line, level_of(line), "blender")
    if state == "end":
        LOG["importing"] = False
    if LOG["importing"] or state:
        redraw_now()


def redraw_now():
    """The editor areas drawn now, mid-import (throttled): Blender redraws nothing until a script returns."""
    now = time.time()
    if now - LOG["drawn"] < REDRAW:
        return
    LOG["drawn"] = now
    tag_redraw()
    wm = bpy.context.window_manager
    for window in wm.windows:
        if any(a.type == 'NODE_EDITOR' for a in window.screen.areas):
            try:
                with bpy.context.temp_override(window=window):
                    bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
            except (RuntimeError, TypeError):
                pass
            break


# ------------------------------------------------------------------ the app's lines while it exports

def tick():
    """Asks the app for its new lines while a job runs and a moment after (the timer calls this, the engine ready)."""
    if LOG["job"]:
        tag_redraw()        # the elapsed time
    if LOG["since"] is None:
        _catch_up()
        return
    wanted = LOG["job"] or time.time() - LOG["ended"] < GRACE
    if not wanted or LOG["asking"] or not LOG["route"] or time.time() - LOG["polled"] < POLL:
        return
    LOG["asking"], LOG["polled"] = True, time.time()

    def done(result):
        LOG["asking"] = False
        if isinstance(result, Exception):
            if "404" in str(result) or "unknown" in str(result).lower():
                LOG["route"] = False        # an app without the route: the plugin's lines only
            return
        for line in result.get("lines") or []:
            text = line["text"]
            if not text.startswith("Blender  ") and not _CHATTER.match(text):     # the plugin's own: heard already
                write(text, level_of(text), "app", _today(line.get("time")))
        LOG["since"] = result.get("seq", LOG["since"])
        tag_redraw()
    client().request("status-log", done, timeout=10, since=LOG["since"])


def _today(stamp):
    """The app's "HH:mm:ss.f" as a time today (yesterday's if that's ahead of now); None when unreadable."""
    try:
        h, m, s = stamp.split(":")
        now = time.time()
        midnight = time.mktime(time.localtime(now)[:3] + (0, 0, 0, 0, 0, -1))
        when = midnight + int(h) * 3600 + int(m) * 60 + float(s.replace(",", "."))
        return when - 86400 if when > now + 60 else when
    except (AttributeError, ValueError):
        return None


def _catch_up():
    """Start from the app's current line, so the drawer doesn't replay its history (once, the engine ready)."""
    if LOG["asking"] or not LOG["route"]:
        return
    LOG["asking"] = True

    def done(result):
        LOG["asking"] = False
        if isinstance(result, Exception):
            LOG["route"] = False        # an app without the route: the plugin's lines only
        else:
            LOG["since"] = result.get("seq", 0)
    client().request("status-log", done, timeout=10, since=1 << 62)


def register():
    from ...material_porter import status
    listeners = getattr(status, "LISTENERS", None)
    if listeners is not None and _heard not in listeners:
        listeners.append(_heard)


def unregister():
    from ...material_porter import status
    listeners = getattr(status, "LISTENERS", None)
    if listeners is not None and _heard in listeners:
        listeners.remove(_heard)
