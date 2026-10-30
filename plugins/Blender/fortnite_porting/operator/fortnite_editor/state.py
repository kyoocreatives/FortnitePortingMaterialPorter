"""What the editor knows between redraws: the engine, the categories, each tab's items, the drawn grid (for clicks),
icons asked for, the import in flight; and the editor's own preferences file (the engine exe override)."""
import json
import os

import bpy

from . import host

STATE = {
    "engine": None,
    "categories": None,     # [{category, tabs}]
    "tabs": {},             # type -> {"finished", "loaded", "total", "items"}
    "tab_asked": {},        # type -> time the last browse was asked
    "grid": None,           # the layout.Grid last drawn (any area) and its items: the header's count
    "grid_items": [],
    "grids": {},            # area pointer -> (Grid, items) that area drew: its clicks hit-test against them
    "hover": {},            # area pointer -> item index under the mouse
    "want_icons": [],       # icon paths the last draw needed and didn't have
    "icons_asked": set(),
    "icons_failed": {},     # icon path -> time it may be asked again
    "styles": {},           # asset path -> its style channels ("asked" while the engine reads them)
    "picks": {},            # asset path -> {channel: option} picked in the Asset tab
    "items_rev": 0,         # bumped when an item's flags change in place (a favourite)
    "picked": [],           # asset paths picked together (ctrl/shift-click), the active one last
    "queue": [],            # (kind, item, style picks) imports waiting for the one in flight
    "settings": None,       # the Settings tab's fields ("asked" while the engine reads them)
    "busy": None,           # "Exporting <name>..." while an import runs
    "error": None,          # the last failed request, shown in the header
}

RARITY_RANK = {"Unattainable": 0, "Exotic": 1, "Mythic": 2, "Legendary": 3, "Epic": 4, "Rare": 5, "Uncommon": 6, "Common": 7}


ICON_RETRY = 30.0


def icon_failed(path, now):
    STATE["icons_asked"].discard(path)
    STATE["icons_failed"][path] = now + ICON_RETRY


def icon_wanted(path, now):
    """Not asked yet, and not a recent failure (a broken icon would be asked again every tick)."""
    return path not in STATE["icons_asked"] and STATE["icons_failed"].get(path, 0) <= now


def prefs_path():
    return os.path.join(bpy.utils.user_resource('CONFIG'), "fp_editor.json")


def prefs():
    try:
        with open(prefs_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_prefs(**values):
    data = prefs()
    data.update(values)
    os.makedirs(os.path.dirname(prefs_path()), exist_ok=True)
    with open(prefs_path(), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)


def client():
    if STATE["engine"] is None:
        from .engine import Engine
        from ...material_porter import hook
        STATE["engine"] = Engine(os.environ.get("MATERIAL_PORTER_BRIDGE") or hook.URL, exe_hint=prefs().get("exe"))
    return STATE["engine"]


def reset_client():
    STATE["engine"] = None


APP_FILTERS = ("Favorites", "Hidden Items")      # FP's own filters, from the items' flags (the others come with the tab)


def active_filters(wm):
    return [f for f in wm.fp_browse_filters.split("|") if f]


def filter_groups(wm):
    """[(category, [(title, any_of group or None)])]: FP's filters for the current tab, its APPLICATION ones first."""
    tab = STATE["tabs"].get(wm.fp_browse_type) or {}
    groups = [("APPLICATION", [(t, None) for t in APP_FILTERS])]
    return groups + [(g["category"], [(f["title"], f.get("any")) for f in g["filters"]]) for g in tab.get("filters") or []]


def _passes(item, on, any_of):
    """FP's rule: hidden items only with Hidden Items; the other filters all match, an any-of group's any."""
    if bool(item.get("hidden")) != ("Hidden Items" in on):
        return False
    has = set(item.get("filters") or ()) | ({"Favorites"} if item.get("favorite") else set())
    plain = [f for f in on if f != "Hidden Items" and f not in any_of]
    if not all(f in has for f in plain):
        return False
    groups = {}
    for f in on:
        if f in any_of:
            groups.setdefault(any_of[f], []).append(f)
    return all(any(f in has for f in fs) for fs in groups.values())


_shown = {"key": None, "items": []}


def items(wm):
    """The current tab's items, filtered (FP's filters), searched (every word in the name, set, series or path) and
    sorted; worked out again only when one of those changes (each draw asks)."""
    tab = STATE["tabs"].get(wm.fp_browse_type) or {}
    found = tab.get("items") or []
    key = (id(found), len(found), STATE["items_rev"], wm.fp_browse_type, wm.fp_browse_filters, wm.fp_browse_search,
           wm.fp_browse_sort)
    if _shown["key"] != key:
        _shown.update(key=key, items=_items(wm, found))
    return _shown["items"]


def _items(wm, found):
    known = {t: g for _, fs in filter_groups(wm) for t, g in fs}
    on = [f for f in active_filters(wm) if f in known]       # filters of another tab don't apply here
    any_of = {t: g for t, g in known.items() if g}
    found = [i for i in found if _passes(i, on, any_of)]
    words = wm.fp_browse_search.lower().split()
    if words:
        def text(i):
            return " ".join(filter(None, (i.get("name"), i.get("set"), i.get("series"), i.get("path")))).lower()
        found = [i for i in found if all(w in text(i) for w in words)]
    if wm.fp_browse_sort == 'RARITY':
        found = sorted(found, key=lambda i: (RARITY_RANK.get(i.get("rarity"), 9), (i.get("name") or "").lower()))
    elif wm.fp_browse_sort == 'SERIES':
        found = sorted(found, key=lambda i: (i.get("series") is None, i.get("series") or "", (i.get("name") or "").lower()))
    elif wm.fp_browse_sort == 'NEWEST':       # FP's Season sort, latest first; no season: last
        found = _newest_ids(sorted(found, key=lambda i: (i.get("season") is None, -(i.get("season") or 0))))
    return found


def _newest_ids(found):
    """Within a season, later ids first (Epic's ids carry no date: the order the game added them is the best guess)."""
    out, run = [], []
    for item in found + [None]:
        if run and (item is None or item.get("season") != run[0].get("season")):
            out += sorted(run, key=lambda i: i.get("id") or "", reverse=True)
            run = []
        if item is not None:
            run.append(item)
    return out


def picked(wm):
    """The picked items' paths, the active one last (ctrl/shift-click picks more)."""
    paths = [p for p in STATE["picked"] if p]
    if wm.fp_browse_selected and wm.fp_browse_selected not in paths:
        paths.append(wm.fp_browse_selected)
    return paths


def selected(wm):
    tab = STATE["tabs"].get(wm.fp_browse_type) or {}
    return next((i for i in tab.get("items") or [] if i["path"] == wm.fp_browse_selected), None)


def tag_redraw(main_only=False):
    """The editor areas redrawn; `main_only`: their main region alone (the header shows nothing that changed)."""
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if host.is_editor_area(area):
                if not main_only:
                    area.tag_redraw()
                    continue
                for region in area.regions:
                    if region.type == 'WINDOW':
                        region.tag_redraw()
