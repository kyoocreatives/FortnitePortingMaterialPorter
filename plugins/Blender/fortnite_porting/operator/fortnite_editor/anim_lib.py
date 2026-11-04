"""The Animation tab's library: what each source lists. In Scene: the file's armature actions; Emotes: the game's
emotes; Lobby & Idles and Locomotion: the game's animations by use (the app's Animations listing, USE filter)."""
import bpy

from . import preview_scene
from .state import client

# id, label, browse type (None: the file's actions), the USE filter its items carry
SOURCES = (("scene", "In Scene", None, None), ("emotes", "Emotes", "Emote", None),
           ("idles", "Lobby & Idles", "Animation", "Lobby"), ("locomotion", "Locomotion", "Animation", "Locomotion"))
LISTS = {}      # browse type -> {"items": [...], "loaded", "total", "finished", "error"}
_asked = set()


def source(id):
    return next(s for s in SOURCES if s[0] == id)


def _armature_action(action):
    if action.get(preview_scene.TAG) or action.library is not None:
        return False
    slots = getattr(action, "slots", None)
    return any(s.target_id_type == 'OBJECT' for s in slots) if slots else action.id_root == 'OBJECT'


def items(id, search=""):
    """[(kind, item)] of the source, filtered by the search's words; kind None: an action in the file."""
    _id, _label, kind, use = source(id)
    words = search.lower().split()
    if kind is None:
        found = [(None, {"name": a.name, "action": a.name,
                         "detail": "%d fr" % (a.frame_range[1] - a.frame_range[0] + 1)})
                 for a in bpy.data.actions if _armature_action(a)]
    else:
        listing = LISTS.get(kind)
        if listing is None or not listing.get("finished"):
            fetch(kind)
            return []
        found = [(kind, it) for it in listing["items"] if use is None or use in it.get("filters", ())]
    return [f for f in found if all(w in f[1]["name"].lower() for w in words)]


def status(id):
    """What the list says while it has nothing to show: loading progress or the error."""
    kind = source(id)[2]
    if kind is None:
        return "No animations in this file"
    listing = LISTS.get(kind) or {}
    if listing.get("error"):
        return listing["error"]
    if not listing.get("finished"):
        total = listing.get("total") or 0
        return "Listing… %d / %d" % (listing.get("loaded", 0), total) if total else "Listing…"
    return "Nothing matches"


def fetch(kind):
    if kind in _asked:
        return
    _asked.add(kind)

    def done(result):
        _asked.discard(kind)
        if isinstance(result, Exception):
            LISTS[kind] = {"finished": False, "error": "The app didn't answer: %s" % result}
            return
        LISTS[kind] = result
        if not result.get("finished"):       # the app still loads it: ask again shortly
            bpy.app.timers.register(lambda: fetch(kind), first_interval=1.0)
    client().request("browse", done, timeout=600, type=kind)


def forget():
    LISTS.clear()
    _asked.clear()
