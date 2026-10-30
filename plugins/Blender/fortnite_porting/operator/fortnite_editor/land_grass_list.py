"""The Landscape tab's Scatter mode, "Grass of this material": a pop-up tree of the shown landscape's material, its grass
outputs, under each its LandscapeGrassType and meshes. Picking an output or a mesh adds it as a layer weighted by the
output's mask (fp.scatter_output); scattered and empty ones are marked.

The outputs are MPGrass: a map piece carries it (mp_grass); on a custom terrain the app answers for its material's
mp_path (foliage-material-grass), kept here per material, not on the mesh: the material can change. Telling an empty
output needs its mask, so opening the list reads the missing ones a piece per timer step."""
import time

import bpy

from . import land_material, land_scatter as ls, land_scatter_info, logbook
from .ui import paint, popups
from .ui.popup import Popup

ID = "scatter_grass"
BUTTON = "scatter:grass"
SIZE = (380, 440)       # fixed: a fold doesn't move the rows under the mouse
ASK_RETRY = 10.0        # s before a material is asked again while the app isn't up
STEP = 0.01             # s between pieces while reading masks

ANSWERS = {}            # {material path: the app's MPGrass, or {"error": text, "outputs": []}}
# open: the pop-up shown; started: its masks asked for; todo, total: pieces left to read; empty: outputs with a mask all
# 0; unknown: expressions the masks read as 1; folds: {output: open}; asked: {material path: time}
STATE = {"open": False, "started": False, "todo": [], "total": 0, "empty": set(), "unknown": {}, "folds": {}, "asked": {}}


def _path(pieces):
    mat = land_material.material(pieces, None)[0]
    return mat.get("mp_path") if mat is not None else None


def answered(pieces):
    """The app's MPGrass for the group's material, None until it answered."""
    return ANSWERS.get(_path(pieces))


def ask(pieces):
    """A terrain without MPGrass: the app asked for its material's, once (the mode's tick)."""
    if not pieces or any(p.get(ls.GRASS) for p in pieces):
        return
    path = _path(pieces)
    if not path or path in ANSWERS or time.time() - STATE["asked"].get(path, 0.0) < ASK_RETRY:
        return
    from .state import client
    e = client()
    if e.state != "ready":
        return
    STATE["asked"][path] = time.time()

    def done(result):
        ANSWERS[path] = {"error": str(result), "outputs": []} if isinstance(result, Exception) else result
        from .land_tab import redraw_all
        redraw_all()
    e.request("foliage-material-grass", done, material=path)


def heading(pieces):
    """(the landscape material's name, a detail: its master): a map proxy's components each have their instance, so no
    one of them names it: (master, "N instances")."""
    master = ls.short((ls.grass_payload(pieces) or {}).get("material")) or None
    paths = {s.material.get("mp_path") for p in pieces for s in p.material_slots if s.material is not None} - {None}
    if len(paths) > 1:
        return master, "%d instances" % len(paths)
    return ls.short(_path(pieces)) or None, master


# ------------------------------------------------------------------ the masks

def _outputs(pieces):
    return (ls.grass_payload(pieces) or {}).get("outputs") or []


def refresh(pieces):
    """The empty marks read again."""
    from .scatter_ops import empty_outputs
    STATE["empty"] = set(empty_outputs(pieces, [o["name"] for o in _outputs(pieces)]))


def _missing(obj, outputs):
    sc = ls.core()
    return [o for o in outputs if o.get("type") and sc.GRASS + o["name"] not in obj.data.attributes]


def start_masks(pieces):
    if not STATE["open"]:
        return
    outputs = _outputs(pieces)
    STATE["started"] = True
    STATE["todo"] = [p.name for p in pieces if _missing(p, outputs)]
    STATE["total"] = len(STATE["todo"])
    if STATE["todo"] and not bpy.app.timers.is_registered(step):
        bpy.app.timers.register(step, first_interval=0.05)


def step():
    """One piece's missing masks, written; after the last the marks show."""
    from ...material_porter import grass_masks
    if not STATE["todo"]:
        return None
    name = STATE["todo"].pop(0)
    _, pieces = ls.shown_group()
    obj = bpy.data.objects.get(name)
    if obj is not None:
        try:
            report = grass_masks.write(obj, _missing(obj, _outputs(pieces)))
            for k, c in (report.get("unknown") or {}).items():
                STATE["unknown"][k] = STATE["unknown"].get(k, 0) + c
        except Exception as e:      # the app's answer, the graph: the user's to read, not a crash
            logbook.write("Error: grass masks failed on %s: %s" % (name, e or type(e).__name__), "error")
            STATE["todo"] = []
    from .land_tab import redraw_all
    redraw_all()
    if STATE["todo"]:
        return STEP
    if STATE["unknown"]:
        logbook.write("Warning: grass masks read these expressions as 1: " +
                      ", ".join("%s (%d)" % kv for kv in sorted(STATE["unknown"].items())), "warning")
        STATE["unknown"] = {}
    refresh(pieces)
    return None


# ------------------------------------------------------------------ the rows

def _toggle(name):
    def run(context):
        STATE["folds"][name] = not STATE["folds"].get(name, False)
    return run


def _pick(output, variety=-1):
    def run(context):
        bpy.ops.fp.scatter_output(output=output, variety=variety)
    return run


def _has(text, words):
    low = text.lower()
    return all(w in low for w in words)


def _output_rows(o, have, words):
    from .scatter_ops import usable, variety_key
    name, varieties = o["name"], o.get("type") or []
    kept = usable(varieties)
    done = [v for v in kept if variety_key(v) in have]
    grass = ls.short(varieties[0].get("type")) if varieties else ""
    meshes = [(v, ls.short(v.get("mesh")) or "no mesh") for v in varieties]
    named = _has(name + " " + grass, words)
    meshes_shown = meshes if named else [m for m in meshes if _has(m[1], words)]
    if not named and not meshes_shown:
        return []
    importing = grass in ls.SCATTER["pending"]
    empty = name in STATE["empty"]
    marks = []
    if importing:
        marks.append("importing")
    elif not varieties:
        marks.append("no grass type")
    elif not kept:
        marks.append("nothing to place")
    elif done and len(done) < len(kept):
        marks.append("%d of %d" % (len(done), len(kept)))
    if empty:
        marks.append("empty")
    full = bool(kept) and len(done) == len(kept)
    can_add = bool(kept) and not full and not importing
    is_open = bool(words) or STATE["folds"].get(name, False)
    out = [{"id": "o:" + name, "label": name, "depth": 0, "detail": " · ".join(marks), "check": full, "dim": empty or not kept,
            "fold": (is_open, _toggle(name)) if varieties else None, "action": _pick(name) if can_add else None,
            "tip": "Already scattered" if full else "Add %s's layers, weighted by this mask" % grass if kept else ""}]
    if not is_open or not varieties:
        return out
    out.append({"id": "t:" + name, "label": grass, "depth": 1, "tip": varieties[0].get("type"), "dim": True,
                "detail": "%d varieties" % len(varieties) if len(varieties) > 1 else ""})
    for v, mesh in meshes_shown:
        k = v.get("variety", 0)
        ok = v in kept
        scattered = ok and variety_key(v) in have
        detail = land_scatter_info._fmt("Density", [float(v.get("density") or 0.0)]) if ok else ("density 0" if v.get("mesh") else "")
        out.append({"id": "v:%s:%d" % (name, k), "label": mesh, "depth": 2, "detail": detail, "check": scattered, "dim": not ok,
                    "action": _pick(name, k) if ok and not scattered and not importing else None, "tip": v.get("mesh") or ""})
    return out


def items(context=None, query=""):
    """The pop-up's rows (popups.py's item dicts; "depth", "fold": a tree's): folded outputs show alone, a query opens
    the ones it finds."""
    from .scatter_ops import scattered_keys
    _, pieces = ls.shown_group(context)
    payload = ls.grass_payload(pieces)
    if payload is None:
        return [{"header": "Reading the material's grass…"}]
    if payload.get("error"):
        return [{"header": "Couldn't read its grass: " + payload["error"]}]
    outputs = payload.get("outputs") or []
    if not outputs:
        return [{"header": "This material has no grass outputs"}]
    out = [{"header": "Reading grass masks %d / %d…" % (STATE["total"] - len(STATE["todo"]), STATE["total"])}] \
        if STATE["todo"] else []
    have, words = scattered_keys(context), query.lower().split()
    for o in outputs:
        out += _output_rows(o, have, words)
    return out or [{"header": "Nothing matches"}]


# ------------------------------------------------------------------ the pop-up

def _draw(ui, r, p):
    t = ui.t
    _, pieces = ls.shown_group()
    if not STATE["started"] and ls.grass_payload(pieces) is not None:
        refresh(pieces)
        start_masks(pieces)
    material, note = heading(pieces)
    head = r.cut_top(t.row).pad(t.gap, 0)
    count = len(_outputs(pieces))
    detail = " · ".join(x for x in (("%d output%s" % (count, "" if count == 1 else "s")) if count else "", note) if x)
    if detail:
        paint.text_in(detail, head.cut_right(min(paint.measure(detail, t.caption) + t.pad, head.w * 0.5)), t.text3, t.caption, "right")
    paint.text_in(paint.fit(material or "Landscape material", t.text, head.w), head, t.text1, t.text)
    r.cut_top(t.gap)
    popups._search_row(ui, p, r.cut_top(t.row), "Search outputs and meshes")
    r.cut_top(t.pad)
    popups._rows(ui, p, r, items(bpy.context, p.query.value), True)


def _closed():
    STATE.update(open=False, started=False, todo=[], total=0)


def open_list(context, anchor, side="left"):
    from .ui import blender as ub
    t = ub.theme(context)
    _, pieces = ls.shown_group(context)
    path = _path(pieces)
    if (ANSWERS.get(path) or {}).get("error"):     # asked again on every opening
        del ANSWERS[path]
        STATE["asked"].pop(path, None)
    p = popups._open(context, Popup(ID, _draw, anchor, side, (SIZE[0] * t.s, SIZE[1] * t.s), "list", search=True,
                                    keep_open=True, on_close=_closed))
    STATE.update(open=True, started=False)
    return p


def open_beside(context):
    """The info column's button: to its left."""
    from .ui import blender as ub
    return open_list(context, ub.FRAMES[context.area.as_pointer()].find(BUTTON).rect.copy())
