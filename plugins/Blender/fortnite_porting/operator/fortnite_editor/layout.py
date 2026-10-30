"""The asset grid's geometry: tiles left to right, rows from the top of the region (Blender's region coordinates have
their origin bottom-left), scrolled down by `scroll` pixels. Pure: no bpy, tested headless."""
import math

NAME_STRIP = 0.25      # tile height = width * (1 + NAME_STRIP): the icon is square, the name sits under it


class Grid:
    def __init__(self, width, height, tile, gap, count, scroll, x0=0, fill=False, y0=0, strip=NAME_STRIP):
        self.width, self.height, self.tile, self.gap, self.count = width, height, tile, gap, count
        self.y0 = y0        # the box's bottom in the region (x0: its left)
        self.tile_h = round(tile * (1 + strip))      # strip 0: names hidden, square tiles
        self.columns = max(1, (width - gap) // (tile + gap))
        if fill:        # tiles grown to fill the width exactly: `tile` is their smallest size
            tile = self.tile = max(tile, int((width - gap) / self.columns - gap))
            self.tile_h = round(tile * (1 + strip))
        self.rows = math.ceil(count / self.columns) if count else 0
        content = self.rows * (self.tile_h + gap) + gap if count else 0
        self.max_scroll = max(0, content - height)
        self.scroll = min(max(0, scroll), self.max_scroll)
        used = self.columns * (tile + gap) + gap
        self.left = x0 + max(0, (width - used) // 2)      # x0: where the uncovered part of the region starts
        step = self.tile_h + gap
        first = max(0, int((self.scroll - gap) // step))
        last = min(self.rows - 1, int((self.scroll + height) // step)) if count else -1
        self.visible = range(first * self.columns, min(count, (last + 1) * self.columns)) if count else range(0, 0)

    def rect(self, i):
        """(x, y, w, h) of item i, y its bottom edge."""
        row, col = divmod(i, self.columns)
        x = self.left + self.gap + col * (self.tile + self.gap)
        top = self.y0 + self.height - self.gap - row * (self.tile_h + self.gap) + self.scroll
        return x, top - self.tile_h, self.tile, self.tile_h

    def hit(self, x, y):
        """The item under (x, y), None in a gap or past the last item."""
        step_x, step_y = self.tile + self.gap, self.tile_h + self.gap
        col = (x - self.left - self.gap) // step_x
        if not self.y0 <= y < self.y0 + self.height:
            return None
        row = (self.y0 + self.height - self.gap + self.scroll - y) // step_y
        if col < 0 or col >= self.columns or row < 0:
            return None
        i = int(row * self.columns + col)
        if i >= self.count:
            return None
        rx, ry, rw, rh = self.rect(i)
        return i if rx <= x < rx + rw and ry <= y < ry + rh else None


def grid(width, height, tile, gap, count, scroll, x0=0, fill=False, y0=0, strip=NAME_STRIP):
    return Grid(int(width), int(height), int(tile), int(gap), int(count), float(scroll), int(x0), fill, int(y0), strip)
