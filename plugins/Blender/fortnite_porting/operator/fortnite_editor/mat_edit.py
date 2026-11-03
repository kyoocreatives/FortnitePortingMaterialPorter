"""The Materials tab's writes: a parameter set on every ticked material's socket (or scaled from where each was,
relative), reset to the game's value, copied and pasted. A drag is one gesture: its writes start from the values the
press saw and make one undo step when the mouse lets go (the tab's tick). Sockets only, never the trees."""
import bpy

from . import mat_data

# key: the parameter being dragged; start: its sockets' values at the press; name: for the undo step
GESTURE = {"key": None, "start": None, "name": "", "factor": 1.0}
CLIP = {}       # parameter name and kind -> value (Copy)


def _put(sock, v):
    if isinstance(v, tuple):
        size = len(tuple(sock.default_value))
        v = tuple(v[:size]) + tuple(sock.default_value)[len(v):size]
    elif sock.bl_idname == "NodeSocketInt":
        v = int(round(v))
    elif sock.bl_idname == "NodeSocketBool":
        v = bool(v)
    sock.default_value = v


def _scaled(v, k):
    return tuple(x * k for x in v) if isinstance(v, tuple) else v * k


def _begin(p):
    if GESTURE["key"] != p.key:
        end()
        GESTURE.update(key=p.key, start=p.values(), name=p.name, factor=1.0)


def drag(p, comp=None, relative=False):
    """fn(context, v) for a number field: sets every socket (component `comp` of a vector), or with `relative`
    multiplies each one's value at the press by v."""
    def run(context, v):
        _begin(p)
        if relative:
            GESTURE["factor"] = v
        for (m, s), s0 in zip(p.sockets, GESTURE["start"]):
            if relative:
                new = _scaled(s0, v) if comp is None else s0[:comp] + (s0[comp] * v,) + s0[comp + 1:]
            elif comp is not None:
                new = s0[:comp] + (float(v),) + s0[comp + 1:]
            else:
                new = v
            _put(s, new)
    return run


def typed(p, comp=None, relative=False):
    """fn(context, v) for a typed value: the drag's write, then its undo step at once."""
    def run(context, v):
        drag(p, comp, relative)(context, v)
        end()
    return run


def set_value(p, v):
    """An action: every socket to v (a colour, a checkbox), one undo step."""
    def run(context):
        for _m, s in p.sockets:
            _put(s, v)
        undo("Set " + p.name)
    return run


def factor(p):
    """The relative field's value: the dragged factor, else 1."""
    return GESTURE["factor"] if GESTURE["key"] == p.key else 1.0


def end():
    """The gesture's undo step (a drag let go, a value typed)."""
    if GESTURE["key"] is not None:
        name = GESTURE["name"]
        GESTURE.update(key=None, start=None, name="", factor=1.0)
        undo("Set " + name)


def tick(pressed):
    """The editor's timer: a gesture whose press ended (`pressed`: any area's held widget) is done."""
    if GESTURE["key"] is not None and pressed is None:
        end()


def undo(message):
    try:
        bpy.ops.ed.undo_push(message=message)
    except RuntimeError:
        pass


# ------------------------------------------------------------------ reset, copy, paste

def resettable(p):
    return any(g is not None and not mat_data.same(v, g) for v, g in zip(p.values(), p.game))


def reset(params, message="Reset to game"):
    """An action: each socket back to the value the build gave it."""
    def run(context):
        for p in params:
            for (_m, s), g in zip(p.sockets, p.game):
                if g is not None:
                    _put(s, g)
        undo(message)
    return run


def copy(sections):
    """An action: the first ticked material's values, by parameter."""
    def run(context):
        CLIP.clear()
        for _title, ps in sections:
            for p in ps:
                CLIP[p.key] = p.values()[0]
    return run


def paste(sections):
    def run(context):
        n = 0
        for _title, ps in sections:
            for p in ps:
                if p.key in CLIP:
                    for _m, s in p.sockets:
                        _put(s, CLIP[p.key])
                    n += 1
        undo("Paste %d parameters" % n)
    return run
