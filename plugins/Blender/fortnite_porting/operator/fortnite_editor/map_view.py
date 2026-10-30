"""The Map tab's view of a map picture: picture pixels (x right, y down, as the engine sends cells and POIs) to the
region (y up) and back, zoom about the mouse, pan, and which cells a click, a box or a POI covers. Pure: no bpy."""

FIT = 0.95
MAX_PIXELS = 40.0       # zoomed in until one picture pixel is this many region pixels


class MapView:
    def __init__(self, width, height, image_w, image_h, x=0.0, y=0.0):
        """The picture fitted in the box (x, y, width, height) of the region: the shell's well."""
        self.x, self.y, self.width, self.height, self.image_w, self.image_h = x, y, width, height, image_w, image_h
        self.fit = min(width / image_w, height / image_h) * FIT
        self.scale, self.pan_x, self.pan_y = self.fit, 0.0, 0.0

    def to_region(self, px, py):
        return (self.x + self.width / 2 + self.pan_x + (px - self.image_w / 2) * self.scale,
                self.y + self.height / 2 + self.pan_y - (py - self.image_h / 2) * self.scale)

    def to_image(self, x, y):
        return (self.image_w / 2 + (x - self.x - self.width / 2 - self.pan_x) / self.scale,
                self.image_h / 2 - (y - self.y - self.height / 2 - self.pan_y) / self.scale)

    def zoom_at(self, x, y, steps):
        px, py = self.to_image(x, y)
        self.scale = min(max(self.scale * 1.2 ** steps, self.fit * 0.5), MAX_PIXELS)
        nx, ny = self.to_region(px, py)
        self.pan_x += x - nx
        self.pan_y += y - ny

    def pan(self, dx, dy):
        self.pan_x += dx
        self.pan_y += dy

    def resize(self, width, height, x=0.0, y=0.0):
        """The box changed: same zoom and pan, about its new centre."""
        self.x, self.y, self.width, self.height = x, y, width, height

    def cell_at(self, cells, x, y):
        px, py = self.to_image(x, y)
        for cell in cells:
            half = cell["size"] / 2
            if abs(px - cell["x"]) <= half and abs(py - cell["y"]) <= half:
                return cell["id"]
        return None

    def cells_in(self, cells, x0, y0, x1, y1):
        (ax, ay), (bx, by) = self.to_image(x0, y0), self.to_image(x1, y1)
        lo_x, hi_x, lo_y, hi_y = min(ax, bx), max(ax, bx), min(ay, by), max(ay, by)
        return [c["id"] for c in cells if lo_x <= c["x"] <= hi_x and lo_y <= c["y"] <= hi_y]


def cells_near(cells, px, py, radius):
    """Cells whose centre is within radius picture pixels of a point (a POI's cells)."""
    return [c["id"] for c in cells if (c["x"] - px) ** 2 + (c["y"] - py) ** 2 <= radius * radius]


class Latest:
    """Answers for the map last asked only (a slow answer for the previous map is dropped)."""

    def __init__(self):
        self.key = None

    def ask(self, key):
        self.key = key

    def accept(self, key):
        return key == self.key
