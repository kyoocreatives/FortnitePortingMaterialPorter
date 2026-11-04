"""A library list's sort and tags (the Animation and Effects tabs): a Sort menu and a Tags menu over the app's own
filter titles (the Assets page's), an item kept when it carries every ticked tag. Filtering and sorting are cached
per listing, search, sort and tags: a draw of a 19 000-item list only cuts its visible rows."""
import bpy

from .state import client
from .ui import fields, paint, popups
from .ui import blender as ui_blender
from .ui.rect import Rect

RARITY = ("Common", "Uncommon", "Rare", "Epic", "Legendary", "Mythic", "Exotic", "Transcendent")
SORTS = {"name": "Name", "newest": "Newest", "rarity": "Rarity", "folder": "Folder"}
_cache = {}
LISTS = {}      # browse type -> the app's listing: {"items": [...], "filters", "loaded", "total", "finished", "error"}
_asked = set()


def fetch(kind):
    """The app's listing of an asset type (Emote, Animation, Effect) into LISTS, asked again while it loads."""
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


def listing(kind):
    """The listing's items, None while it loads (asked for)."""
    got = LISTS.get(kind)
    if got is None or not got.get("finished"):
        fetch(kind)
        return None
    return got["items"]


def status(kind):
    """What a list says while it has nothing to show: loading progress or the error."""
    got = LISTS.get(kind) or {}
    if got.get("error"):
        return got["error"]
    if not got.get("finished"):
        total = got.get("total") or 0
        return "Listing… %d / %d (the first time takes minutes)" % (got.get("loaded", 0), total) if total else "Listing…"
    return "Nothing matches"


def _sort_key(sort):
    if sort == "newest":
        return lambda kv: (-(kv[1].get("season") or -1), kv[1]["name"].lower())
    if sort == "rarity":
        return lambda kv: (-(RARITY.index(kv[1].get("rarity")) if kv[1].get("rarity") in RARITY else -1),
                           kv[1]["name"].lower())
    if sort == "folder":
        return lambda kv: (kv[1].get("path", "").rsplit("/", 1)[0].lower(), kv[1]["name"].lower())
    return lambda kv: kv[1]["name"].strip().lower()


def apply(key, found, search, state):
    """`found` [(kind, item)] searched (every word in the name), tag-filtered and sorted; cached for `key`."""
    words = tuple(search.lower().split())
    tags = frozenset(state["tags"])
    memo = (key, len(found), id(found[0][1]) if found else 0, words, state["sort"], tags)
    if _cache.get(key, (None,))[0] == memo:
        return _cache[key][1]
    out = [f for f in found if all(w in f[1]["name"].lower() for w in words)
           and tags.issubset(f[1].get("filters") or ())]
    out.sort(key=_sort_key(state["sort"]))
    _cache[key] = (memo, out)
    return out


def tag_titles(listing):
    """The listing's filter titles, in the app's order."""
    seen = []
    for group in (listing or {}).get("filters") or ():
        for f in group.get("filters") or ():
            if f["title"] not in seen:
                seen.append(f["title"])
    return seen


def _anchor(context, id):
    frame = ui_blender.FRAMES.get(context.area.as_pointer())
    hit = frame.find(id) if frame is not None else None
    return hit.rect if hit is not None else Rect(0, 0, 0, 0)


def _sort_menu(id, sorts, state, changed):
    def pick(value):
        def run(context):
            state["sort"] = value
            changed(context)
        return run

    def run(context):
        items = [{"label": SORTS[s], "check": state["sort"] == s, "action": pick(s), "id": s} for s in sorts]
        popups.open_menu(context, _anchor(context, id), items, id, search=False)
    return run


def _tags_menu(id, titles, state, changed):
    def toggle(title):
        def run(context):
            state["tags"] ^= {title}
            changed(context)
        return run

    def clear(context):
        state["tags"].clear()
        changed(context)

    def run(context):
        items = [{"label": "Any", "check": not state["tags"], "action": clear, "id": "*"}]
        items += [{"label": t, "check": t in state["tags"], "action": toggle(t), "id": t} for t in titles]
        popups.open_menu(context, _anchor(context, id), items, id, keep_open=True, search=len(titles) > 12)
    return run


def controls(ui, r, prefix, sorts, titles, state, changed):
    """[Sort ▾] [Tags ▾] in row `r`; `changed(context)` after a pick (scroll back to the top)."""
    t = ui.t
    if titles:
        tag_w = (r.w - t.gap) / 2
        sort_r = r.cut_left(r.w - tag_w - t.gap)
        r.cut_left(t.gap)
        label = "Tags" if not state["tags"] else ", ".join(sorted(state["tags"]))
        fields.dropdown(ui, prefix + ":tags", r, paint.fit(label, t.text, r.w - t.icon - 2 * t.pad),
                        _tags_menu(prefix + ":tags", titles, state, changed),
                        tip="Only items with every ticked tag")
    else:
        sort_r = r
    fields.dropdown(ui, prefix + ":sort", sort_r, "By " + SORTS[state["sort"]],
                    _sort_menu(prefix + ":sort", sorts, state, changed), tip="How the list is sorted")
