"""Importing from the editor: the engine builds the export (with the picked styles) and hands it back over the bridge;
Blender imports it itself, so the asset lands in the Blender that asked. The header shows it until done or failed."""
import json

import bpy

from . import import_options, logbook
from .state import STATE, client, tag_redraw


def styles_query(picks):
    """{channel: option} as the browse-export route takes it: "Channel:Option;Channel:Option"."""
    return ";".join("%s:%s" % (channel, option) for channel, option in picks.items())


def start_all(entries):
    """[(kind, item, style picks)] imported one after another (the header counts what's left), all with the
    import options set now."""
    settings = import_options.query()
    STATE["queue"] += [(kind, item, picks, settings) for kind, item, picks in entries]
    _next()


def _next():
    if STATE["busy"] or not STATE["queue"]:
        return
    start(*STATE["queue"].pop(0))


def start(kind, item, picks, settings=None):
    return run("browse-export", item.get("name") or item["path"].rsplit(".", 1)[-1],
               type=kind, path=item["path"], styles=styles_query(picks),
               settings=import_options.query() if settings is None else settings)


def run(route, label, **query):
    """An export route's payload imported here; the header says "Exporting <label>..." until done or failed."""
    if STATE["busy"]:
        return False
    left = len(STATE["queue"])
    STATE["busy"] = "Exporting %s..." % label + (" (%d more)" % left if left else "")
    STATE["error"] = None
    logbook.begin("Exporting %s" % label.strip() + (" (%d more after it)" % left if left else ""))
    client().request(route, lambda result: _done(label, result), timeout=1800, **query)
    tag_redraw()
    return True


AFTER = []      # fn(label, new objects, message) once an import landed (message None: all imported): grid_menu.py


def _done(label, result):
    STATE["busy"] = None
    before = set(bpy.data.objects)
    message = land(label, result)
    if AFTER:
        new = [o for o in bpy.data.objects if o not in before]
        for fn in AFTER:
            fn(label, new, message)
    undo_step()
    if message:
        logbook.write(message, logbook.level_of(message))
    error = message or (STATE["error"] if STATE["queue"] else None)
    if not STATE["queue"]:
        logbook.end()
    _next()
    STATE["error"] = error      # a failure stays shown while the rest import
    tag_redraw()


def land(label, result):
    """Imports an export route's answer here; the message to show (None: all imported)."""
    if isinstance(result, Exception):
        return "Export of %s failed: %s" % (label, result)
    if not result.get("Exports"):
        skipped = [s for s in result.get("skipped") or [] if s.rsplit(".", 1)[0] != label]       # not the file again
        return "Nothing FP can export in %s" % label + (" (%s)" % ", ".join(skipped) if skipped else "")
    from ...processing.importer import Importer
    parts = len(result["Exports"])
    logbook.write("Exported %s: %d part%s, importing" % (label.strip(), parts, "" if parts == 1 else "s"))
    logbook.redraw_now()
    _step[:] = ["Import %s" % label.strip()]       # pushed by undo_step(), after whatever finishes the import
    try:
        Importer.Import(json.dumps(result))
    except Exception as e:      # the plugin's own import failed: shown, not raised in a timer
        return "Import of %s failed: %s" % (label, e)
    if result.get("skipped"):
        return "Skipped (no exporter): %s" % ", ".join(result["skipped"])
    return None


_step = []      # the undo step land() owes ("Import <label>"), none when the export failed before Blender changed


def undo_step():
    """One undo step for the import that just landed: it ran in a timer, which pushes none, so without it Ctrl+Z
    took the import back with the step before it, and Ctrl+Shift+Z couldn't bring it back."""
    if not _step:
        return
    message = _step.pop()
    wm = bpy.context.window_manager
    window = bpy.context.window or next(iter(wm.windows), None)
    if window is None:
        return
    with bpy.context.temp_override(window=window, screen=window.screen):
        bpy.ops.ed.undo_push(message=message)
