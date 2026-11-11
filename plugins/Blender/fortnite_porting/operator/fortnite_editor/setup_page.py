"""The guided setup: until the engine is ready, the export tabs' well shows where the editor is on the way to it (find
the app, its first-time setup, the game's files loaded), what's happening now, and the one thing to do about it.
guide() is pure (the tests read it); draw() puts it in the well over whatever the tab drew there."""
import bpy

from .state import client
from .ui import paint
from .ui.rect import Rect

STEPS = ("Find the FP app", "Finish FP's first-time setup", "Load the game files")
LINES = 4       # the body's most lines

# state -> (title, body, the step it's on, its main action, its other action); actions: (label, icon, name)
OPEN, LOCATE, RETRY = ("Open FP", "mdi_Eye", "show"), ("Locate FP…", "mdi_FolderOpen", "locate"), \
    ("Retry", "mdi_Refresh", "retry")
START = ("Start FP", None, "start")
GUIDE = {
    "stopped": ("FP is stopped", "The editor runs the FP app hidden in the background as its engine. It starts only "
                "when you press Start; Stop in the status bar ends it.", 0, START, None),
    "unknown": ("Connecting to FP", "The editor runs the FP app hidden in the background as its engine. Looking for "
                "it…", 0, None, None),
    "starting": ("Starting FP", "FP starts hidden in the background. The first start takes a moment.", 1, None, OPEN),
    "no_exe": ("Find the FP app", "The editor uses the FP app (FortnitePortingMP.exe) as its engine. It isn't running "
               "and the editor doesn't know where it is.", 0, LOCATE, RETRY),
    "setup": ("Finish FP's first-time setup", "Open FP and pick your Fortnite install and game version. The editor "
              "carries on by itself once it's done.", 1, OPEN, None),
    "loading": ("Loading the game files", "FP is reading Fortnite's files. It takes a minute the first time; the tabs "
                "fill in when it's done.", 2, None, OPEN),
    "too_old": ("Update FP", None, 0, OPEN, RETRY),
    "error": ("FP couldn't start", None, 0, LOCATE, RETRY),
}


def guide(state, text=""):
    """What the well shows in this engine state: {title, body, note, steps: [(label, "done" / "now" / "todo")], main,
    other}, None once it's ready. `text`: the engine's own words (the body when the state has none, else the note)."""
    if state == "ready":
        return None
    title, body, at, main, other = GUIDE.get(state, GUIDE["unknown"])
    steps = [(label, "done" if i < at else "now" if i == at else "todo") for i, label in enumerate(STEPS)]
    note = text if body and text and state in ("starting", "loading") else ""
    return {"title": title, "body": body or text or "", "note": note, "steps": steps, "main": main, "other": other}


def needed(tab):
    """The export tabs ask the engine for everything they show; the tools work on the scene alone."""
    return tab is not None and tab.group == "export"


def _run(name):
    def run(context):
        if name == "start":
            bpy.ops.fp.engine_start()
        elif name == "show":
            bpy.ops.fp.engine_show()
        elif name == "locate":
            bpy.ops.fp.engine_locate('INVOKE_DEFAULT')
        else:
            bpy.ops.fp.browse_refresh()
    return run


def draw(ui, well):
    """The guide on a card centred in the well (the tab's own loading text covered), when the engine isn't ready."""
    e = client()
    g = guide(e.state, e.status_text)
    if g is None:
        return
    t = ui.t
    paint.rect(well, t.well, t.r_card)
    off = t.r_card - t.r_small      # the card's content inset: its buttons concentric with its corners
    w = min(well.w - 2 * t.big, 440 * t.s)
    line = 16 * t.s
    body = paint.wrap(g["body"], t.text, w - 2 * off, LINES)
    note = paint.fit(g["note"], t.caption, w - 2 * off) if g["note"] else ""
    actions = [a for a in (g["main"], g["other"]) if a]
    h = (t.row_pitch + t.pad + len(body) * line + (line if note else 0) + t.big + len(STEPS) * t.row_pitch
         + ((t.big + t.action) if actions else 0) + 2 * off)
    card = Rect(well.x + (well.w - w) / 2, well.y + max(0.0, (well.h - h) / 2), w, h)
    ui.panel(card)
    inner = card.pad(off)
    ui.label(inner.cut_top(t.row_pitch), g["title"], t.text1, t.title)
    inner.cut_top(t.pad)
    for text in body:
        ui.label(inner.cut_top(line), text, t.text2)
    if note:
        ui.label(inner.cut_top(line), note, t.text3, t.caption)
    inner.cut_top(t.big)
    for label, how in g["steps"]:
        _step(ui, inner.cut_top(t.row_pitch), label, how)
    if actions:
        inner.cut_top(t.big)
        row = inner.cut_top(t.action)
        bw = (row.w - t.pad * (len(actions) - 1)) / len(actions)       # a lone one fills the row: both corners concentric
        for i, (label, icon, name) in enumerate(actions):
            cell = row.cut_left(bw)
            row.cut_left(t.pad)
            ui.button("setup:" + name, cell, label, icon, _run(name), style="accent" if i == 0 and g["main"] else "control")


def _step(ui, r, label, how):
    t = ui.t
    mark = r.cut_left(t.icon)
    r.cut_left(t.pad)
    if how == "done":
        ui.icon(mark, "mdi_Check", t.ok)
    else:
        d = 8 * t.s
        dot = Rect(mark.x + (mark.w - d) / 2, mark.y + (mark.h - d) / 2, d, d)
        paint.rect(dot, t.accent if how == "now" else t.text3, d / 2)
    ui.label(r, label, t.text1 if how == "now" else t.text2 if how == "done" else t.text3)
