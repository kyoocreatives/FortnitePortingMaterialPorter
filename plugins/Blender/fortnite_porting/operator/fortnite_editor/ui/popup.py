"""The editor's pop-ups: each area's stack of them, where one goes, and the keys while one is open. Pure: no bpy.

A pop-up's draw(ui, rect, popup) draws its content with the kit and lists what the keys can reach (popup.nav: (id,
action) in order; popup.cols > 1 for a tile grid). The mouse goes through the router as anywhere else: the frame's
layers keep what's under a pop-up from answering.

    key(stack, store, Key) -> [effect]: ("run", callable) ("redraw",) ("clipboard", text)
    press(stack, x, y) -> "inside" (the press goes on to the pop-up's hits) / "closed" (it closed them all: eat it)
"""
from .rect import Rect
from .textedit import Text

KINDS = ("menu", "list", "tiles", "panel", "dialog", "color")


class Key:
    __slots__ = ("type", "text", "ctrl", "shift", "clipboard")

    def __init__(self, type, text="", ctrl=False, shift=False, clipboard=None):
        self.type, self.text, self.ctrl, self.shift = type, text, ctrl, shift
        self.clipboard = clipboard      # [the clipboard's text]: a paste reads it, copy and cut append to it


class Popup:
    def __init__(self, id, draw, anchor=None, side="below", size=(240, 200), kind="menu", parent=None, modal=False,
                 search=False, keep_open=False, on_close=None, on_accept=None, on_cancel=None, title=""):
        self.id, self.draw, self.anchor, self.side, self.size, self.kind = id, draw, anchor, side, size, kind
        self.parent = parent            # the pop-up's id this one opened from (a submenu, an enum's menu)
        self.modal = modal              # a dialog: a press outside doesn't close it
        self.keep_open = keep_open      # Enter on an item runs it and stays open (panels, multi-pick)
        self.on_close, self.on_accept, self.on_cancel = on_close, on_accept, on_cancel
        self.title = title
        self.query = Text("") if search else None       # the type-to-filter text
        self.rect = None        # where its last draw put it
        self.nav = []           # (id, action or None) the keys move over, as last drawn
        self.cols = 1           # a tile grid's columns: the arrows move by them
        self.cursor = None      # the keyboard's item
        self.fields = []        # the focusable fields' ids, in Tab order
        self.state = {}         # the content's own (fold states, a picked tab...)
        self.stack = None       # the stack it's open in


class Stack:
    def __init__(self):
        self.popups = []

    def __len__(self):
        return len(self.popups)

    def __bool__(self):
        return bool(self.popups)

    @property
    def top(self):
        return self.popups[-1] if self.popups else None

    def find(self, id):
        return next((p for p in self.popups if p.id == id), None)

    def open(self, p):
        """On top; a root pop-up closes the others, a child closes what its parent had open; the same id: replaced."""
        same = self.find(p.id)
        if same is not None:
            self.close(same)
        parent = self.find(p.parent) if p.parent is not None else None
        if parent is None:
            self.close_all()
        else:
            above = self.popups.index(parent) + 1
            if above < len(self.popups):
                self.close(self.popups[above])
        p.stack = self
        self.popups.append(p)
        return p

    def close(self, p):
        """Close `p` and every pop-up above it (top first)."""
        if p not in self.popups:
            return
        n = self.popups.index(p)
        gone, self.popups = self.popups[n:], self.popups[:n]
        for q in reversed(gone):
            if q.on_close is not None:
                q.on_close()

    def close_all(self):
        if self.popups:
            self.close(self.popups[0])

    def layer(self, p):
        return self.popups.index(p) + 1


def place(anchor, size, side, bounds, gap):
    """The pop-up's rect: `size` (w, h) on `side` of `anchor` (below / above / right / left / at: its top-left corner
    at the anchor / center: in `bounds`), flipped to the other side when it doesn't fit, slid and shrunk into bounds."""
    w, h = min(size[0], bounds.w), min(size[1], bounds.h)
    if side == "center" or anchor is None:
        return Rect(bounds.x + (bounds.w - w) / 2, bounds.y + (bounds.h - h) / 2, w, h)
    if side in ("below", "above", "at"):
        if side == "at":
            x, below_top, above_y = anchor.x, anchor.y, anchor.y
        else:
            x, below_top, above_y = anchor.x, anchor.y - gap, anchor.top + gap
        room_below, room_above = below_top - bounds.y, bounds.top - above_y
        down = side != "above"
        if down and room_below < h and room_above > room_below:
            down = False
        elif not down and room_above < h and room_below > room_above:
            down = True
        if down:
            h = min(h, room_below) if room_below < h else h
            y = below_top - h
        else:
            h = min(h, room_above) if room_above < h else h
            y = above_y
        x = min(max(x, bounds.x), bounds.right - w)
        return Rect(x, y, w, h)
    # right / left: tops level, flipped when there's more room the other way
    room_right, room_left = bounds.right - (anchor.right + gap), (anchor.x - gap) - bounds.x
    right = side == "right"
    if right and room_right < w and room_left > room_right:
        right = False
    elif not right and room_left < w and room_right > room_left:
        right = True
    x = anchor.right + gap if right else anchor.x - gap - w
    x = min(max(x, bounds.x), bounds.right - w)
    y = min(max(anchor.top - h, bounds.y), bounds.top - h)
    return Rect(x, y, w, h)


def press(stack, x, y):
    """A press while pop-ups are open: in one of them, the ones above it close; outside all, they all close (a modal
    dialog on top keeps everything)."""
    top = stack.top
    if top is None:
        return "closed"
    for p in reversed(stack.popups):
        if p.rect is not None and p.rect.contains(x, y):
            n = stack.popups.index(p)
            if n + 1 < len(stack.popups):
                stack.close(stack.popups[n + 1])
            return "inside"
    if top.modal:
        return "inside"
    stack.close_all()
    return "closed"


# ------------------------------------------------------------------ focus

def focus(store, id, text):
    """Give a field the keys (`text`: its Text, or None: the field makes one from its value on its next draw)."""
    store.focus, store.focus_text = id, text
    if text is not None:
        text.before = text.value
        text.anchor, text.cursor = 0, len(text.value)       # all selected: typing replaces it


def commit(store):
    """The focused field's edit, kept (its on_commit hears it); the field lets go of the keys."""
    if store.focus is not None and store.focus_text is not None and store.on_commit is not None:
        store.on_commit(store.focus, store.focus_text)
    store.focus = store.focus_text = None


def _field_key(stack, store, ev):
    text = store.focus_text
    if ev.type == "ESC":
        if text is not None:
            text.revert()
        store.focus = store.focus_text = None
        return [("redraw",)]
    if ev.type in ("RET", "NUMPAD_ENTER"):
        commit(store)
        return [("redraw",)]
    if ev.type == "TAB":
        fields = stack.top.fields if stack.top is not None else []
        at = store.focus
        commit(store)
        if at in fields and fields:
            focus(store, fields[(fields.index(at) + (-1 if ev.shift else 1)) % len(fields)], None)
        return [("redraw",)]
    if text is None:
        return None
    before = len(ev.clipboard) if ev.clipboard is not None else 0
    if not text.key(ev.type, ev.text, ev.ctrl, ev.shift, ev.clipboard):
        return None
    out = [("redraw",)]
    if ev.clipboard is not None and len(ev.clipboard) > before:
        out.append(("clipboard", ev.clipboard[-1]))
    return out


# ------------------------------------------------------------------ keys

def _move(p, ev):
    ids = [i for i, _ in p.nav]
    if not ids:
        return False
    n = ids.index(p.cursor) if p.cursor in ids else None
    last = len(ids) - 1
    k, grid = ev.type, p.cols > 1
    if k == "HOME":
        n = 0
    elif k == "END":
        n = last
    elif k in ("DOWN_ARROW", "UP_ARROW"):
        step = p.cols if grid else 1
        down = k == "DOWN_ARROW"
        if n is None:
            n = 0 if down else last
        elif grid:
            n = min(n + step, last) if down else max(n - step, 0)
        else:
            n = (n + (1 if down else -1)) % len(ids)
    elif k in ("LEFT_ARROW", "RIGHT_ARROW") and grid:
        n = 0 if n is None else min(max(n + (1 if k == "RIGHT_ARROW" else -1), 0), last)
    else:
        return False
    p.cursor = ids[n]
    return True


def key(stack, store, ev):
    p = stack.top
    if p is None:       # a tab's field (no pop-up open): only the field hears keys
        return (_field_key(stack, store, ev) or [("redraw",)]) if getattr(store, "focus", None) is not None else []
    if getattr(store, "focus", None) is not None:
        out = _field_key(stack, store, ev)
        if out is not None:
            return out
    if ev.type == "ESC":
        out = [("run", p.on_cancel)] if p.on_cancel is not None else []
        stack.close(p)
        return out + [("redraw",)]
    if ev.type in ("RET", "NUMPAD_ENTER"):
        if p.kind == "dialog":
            out = [("run", p.on_accept)] if p.on_accept is not None else []
            stack.close(p)
            return out + [("redraw",)]
        actions = dict(p.nav)
        pick = p.cursor if p.cursor in actions else next((i for i, a in p.nav if a is not None), None)
        if pick is None or actions.get(pick) is None:
            return []
        if not p.keep_open:
            stack.close_all()
        return [("run", actions[pick]), ("redraw",)]
    if ev.type == "TAB" and p.fields:
        focus(store, p.fields[-1 if ev.shift else 0], None)
        return [("redraw",)]
    if _move(p, ev):
        store.hot = None        # the keyboard's item shows, not the mouse's
        return [("redraw",)]
    if p.query is not None and p.query.key(ev.type, ev.text, ev.ctrl, ev.shift, ev.clipboard):
        p.cursor = None
        return [("redraw",)]
    return []
