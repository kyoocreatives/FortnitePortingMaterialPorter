"""The Animation tab's library: what each source lists. In Scene: the file's armature actions; Emotes: the game's
emotes; Lobby & Idles and Locomotion: the game's animations by use (the app's Animations listing, USE filter)."""
import bpy

from . import lib_list, preview_scene
from .lib_list import LISTS

# id, label, browse type (None: the file's actions), the USE filter its items carry, filters they must not carry
SOURCES = (("scene", "In Scene", None, None, ()), ("emotes", "Emotes", "Emote", None, ()),
           # the Lobby use takes pickaxes' lobby intros too: not idles
           ("idles", "Lobby & Idles", "Animation", "Lobby", ("Pickaxes",)),
           ("locomotion", "Locomotion", "Animation", "Locomotion", ()))


def source(id):
    return next(s for s in SOURCES if s[0] == id)


def _armature_action(action):
    if action.get(preview_scene.TAG) or action.library is not None:
        return False
    slots = getattr(action, "slots", None)
    return any(s.target_id_type == 'OBJECT' for s in slots) if slots else action.id_root == 'OBJECT'


def items(id):
    """[(kind, item)] of the source; kind None: an action in the file."""
    _id, _label, kind, use, without = source(id)
    if kind is None:
        found = [(None, {"name": a.name, "action": a.name,
                         "detail": "%d fr" % (a.frame_range[1] - a.frame_range[0] + 1)})
                 for a in bpy.data.actions if _armature_action(a)]
    else:
        got = lib_list.listing(kind)
        if got is None:
            return []
        found = [(kind, it) for it in got if (use is None or use in it.get("filters", ()))
                 and not any(f in it.get("filters", ()) for f in without)]
    return found


def sorts(id):
    """The sorts the source's items can take."""
    kind = source(id)[2]
    return ("name",) if kind is None else ("name", "newest", "rarity") if kind == "Emote" else ("name", "folder")


def tags(id):
    """The tags the source's items can carry (the app's filters), without the one that picks the source."""
    _id, _label, kind, use, without = source(id)
    if kind is None:
        return []
    return [t for t in lib_list.tag_titles(LISTS.get(kind)) if t != use and t not in without]


def status(id):
    """What the list says while it has nothing to show: loading progress or the error."""
    kind = source(id)[2]
    return "No animations in this file" if kind is None else lib_list.status(kind)
