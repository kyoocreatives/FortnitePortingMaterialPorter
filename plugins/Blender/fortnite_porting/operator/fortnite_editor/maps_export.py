"""The Map tab's export queue: picked cells go to the engine one at a time, each lands in Blender as it finishes; Cancel
drops the rest and stops the cell in flight. Pure: the tab's timer sends what next() gives."""

TERRAIN = "terrain"     # the map's own level (Include Terrain): a step of its own, first


class Queue:
    def __init__(self):
        self.map_id, self.cells, self.state, self.current, self.total = None, [], {}, None, 0
        self.options, self.cancelled = {}, False

    def start(self, map_id, cells, options=None):
        """options: the export settings as Export was pressed (flags, filter, layers), kept for every cell."""
        self.options, self.cancelled = dict(options or {}), False
        cells = ([TERRAIN] if self.options.get("main") else []) + list(cells)
        self.map_id, self.cells, self.total, self.current = map_id, cells, len(cells), None
        self.state = {cell: "queued" for cell in self.cells}

    @property
    def busy(self):
        return self.current is not None or bool(self.cells)

    def next(self):
        """The cell to send now, or None while one is in flight or nothing is left."""
        if self.current is not None or not self.cells:
            return None
        self.current = self.cells.pop(0)
        self.state[self.current] = "exporting"
        return self.current

    def finished(self, cell, ok):
        """ok: True landed, False failed, None cancelled."""
        if cell != self.current:
            return
        self.state[cell] = {True: "done", False: "failed", None: "cancelled"}[ok]
        self.current = None

    def cancel(self):
        self.cancelled = True
        for cell in self.cells:
            self.state.pop(cell, None)
        self.cells = []

    def progress(self):
        if not self.busy:
            return ""
        if self.cancelled:
            return "Cancelling..."
        if self.current == TERRAIN:
            return "Exporting terrain"
        return "Exporting cell %d / %d" % (self.total - len(self.cells), self.total)
