"""What one draw of an area left to click: its hits in draw order (the last drawn is on top), each with what a click,
a double click or the wheel does. Input is tested against the area's last frame. Pure: no bpy."""


class Hit:
    __slots__ = ("id", "rect", "clip", "kind", "action", "double", "tip", "scroll", "drag", "layer")

    def __init__(self, id, rect, clip=None, kind="button", action=None, double=None, tip="", scroll=None, drag=None):
        self.id, self.rect, self.clip, self.kind = id, rect, clip, kind
        self.action = action        # callable(context): run on click (buttons: on release inside; rows: on press)
        self.double = double        # callable(context): run on a double click
        self.tip = tip              # tooltip text
        self.scroll = scroll        # (id, max offset): the wheel scrolls this container
        self.drag = drag            # callable(context, dx, dy): a press holds it, moves since the press call it
        self.layer = 0              # 0: the shell and its tab; n: the n-th open pop-up

    def over(self, x, y):
        return self.rect.contains(x, y) and (self.clip is None or self.clip.contains(x, y))


class Frame:
    def __init__(self, key=None):
        self.key, self.hits = key, []
        self.well = None        # the tab's own view: input outside it belongs to the shell
        self.layer = 0          # the layer hits added now go to (a pop-up's draw raises it)
        self.blocked = 0        # hits under this layer don't answer (a pop-up is open over them)

    def add(self, hit):
        hit.layer = self.layer
        self.hits.append(hit)
        return hit

    def block(self, layer):
        self.blocked = layer

    def at(self, x, y, kind=None):
        """The topmost widget under (x, y) (of `kind` when given; scroll containers only when asked), else None."""
        for hit in reversed(self.hits):
            if hit.layer >= self.blocked and (hit.kind == kind if kind else hit.kind != "scroll") and hit.over(x, y):
                return hit
        return None

    def scroller(self, x, y):
        for hit in reversed(self.hits):
            if hit.layer >= self.blocked and hit.scroll is not None and hit.over(x, y):
                return hit
        return None

    def on_shell(self, x, y):
        return self.blocked > 0 or (self.well is not None and not self.well.contains(x, y))

    def find(self, id):
        return next((h for h in self.hits if h.id == id), None)
