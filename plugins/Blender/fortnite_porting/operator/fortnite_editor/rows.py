"""A list's geometry in a region: rows of fixed height from the top (region coordinates are bottom-left), scrolled
down by `scroll` pixels. Pure: tested headless."""


class Rows:
    def __init__(self, width, height, row_h, count, scroll, x0=0):
        self.width, self.height, self.row_h, self.count = int(width), int(height), int(row_h), int(count)
        self.x0 = int(x0)       # where the uncovered part of the region starts
        self.max_scroll = max(0, self.count * self.row_h - self.height)
        self.scroll = min(max(0.0, float(scroll)), self.max_scroll)
        first = int(self.scroll // self.row_h)
        last = min(self.count, int((self.scroll + self.height) // self.row_h) + 1)
        self.visible = range(first, last) if self.count else range(0, 0)

    def rect(self, i):
        """(x, y, w, h) of row i, y its bottom edge."""
        return self.x0, int(self.height - (i + 1) * self.row_h + self.scroll), self.width, self.row_h

    def hit(self, x, y):
        if not self.x0 <= x < self.x0 + self.width:
            return None
        i = int((self.height + self.scroll - y) // self.row_h)
        return i if 0 <= i < self.count else None
