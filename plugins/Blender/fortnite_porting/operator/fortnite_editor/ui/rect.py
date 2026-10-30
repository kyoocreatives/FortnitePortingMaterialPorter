"""Rectangles in region pixels (origin bottom-left, as Blender's regions): cut a side off, split, pad. Pure: no bpy."""


class Rect:
    __slots__ = ("x", "y", "w", "h")

    def __init__(self, x, y, w, h):
        self.x, self.y, self.w, self.h = float(x), float(y), max(0.0, float(w)), max(0.0, float(h))

    def __repr__(self):
        return "Rect(%g, %g, %g, %g)" % (self.x, self.y, self.w, self.h)

    def __eq__(self, other):
        return isinstance(other, Rect) and (self.x, self.y, self.w, self.h) == (other.x, other.y, other.w, other.h)

    @property
    def top(self):
        return self.y + self.h

    @property
    def right(self):
        return self.x + self.w

    def contains(self, x, y):
        return self.x <= x < self.x + self.w and self.y <= y < self.y + self.h

    def cut_left(self, w):
        """The left `w` pixels, taken off this rect (this rect keeps the rest)."""
        w = min(w, self.w)
        out = Rect(self.x, self.y, w, self.h)
        self.x, self.w = self.x + w, self.w - w
        return out

    def cut_right(self, w):
        w = min(w, self.w)
        self.w -= w
        return Rect(self.x + self.w, self.y, w, self.h)

    def cut_top(self, h):
        h = min(h, self.h)
        self.h -= h
        return Rect(self.x, self.y + self.h, self.w, h)

    def cut_bottom(self, h):
        h = min(h, self.h)
        out = Rect(self.x, self.y, self.w, h)
        self.y, self.h = self.y + h, self.h - h
        return out

    def pad(self, left, top=None, right=None, bottom=None):
        top = left if top is None else top
        right = left if right is None else right
        bottom = top if bottom is None else bottom
        return Rect(self.x + left, self.y + bottom, self.w - left - right, self.h - top - bottom)

    def copy(self):
        return Rect(self.x, self.y, self.w, self.h)

    def intersect(self, other):
        x0, y0 = max(self.x, other.x), max(self.y, other.y)
        x1, y1 = min(self.right, other.right), min(self.top, other.top)
        return Rect(x0, y0, x1 - x0, y1 - y0)
