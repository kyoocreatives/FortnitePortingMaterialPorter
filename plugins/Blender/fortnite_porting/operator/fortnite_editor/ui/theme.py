"""The editor's look: FP's structure (spacing 3/6/12, radii nested: outer = inner + padding; three grounds: the app,
its content panel, the well) in Blender's theme colours, so the area sits with the editors around it. Sizes are at UI
scale 1; `Theme(scale)` multiplies them."""


def _mix(a, b, t):
    return tuple(x + (y - x) * t for x, y in zip(a, b))


def _rgba(c, a=1.0):
    c = tuple(c)
    return c[:3] + (c[3] if len(c) > 3 else a,)


class Theme:
    def __init__(self, scale=1.0, blender_theme=None):
        s = self.s = scale
        # spacing and sizes (px at this scale)
        self.gap, self.pad, self.big = 3 * s, 6 * s, 12 * s
        self.row, self.row_pitch = 22 * s, 24 * s
        self.action = 28 * s        # the main action's row (Import, Export): FP's big buttons, scaled
        self.rail, self.nav_wide, self.side, self.info = 40 * s, 160 * s, 176 * s, 272 * s
        # the well and cards concentric with the grid's tiles inside them: card = tile + the gap between them
        self.r_small, self.r_tile = 8 * s, 16 * s
        self.r_card = self.r_tile + self.pad
        self.text, self.caption, self.title = 11 * s, 10 * s, 13 * s
        self.icon = 16 * s
        # colours: Blender's theme when given, its default dark otherwise
        back, panel, inner, text, sel = (0.188, 0.188, 0.188), (0.239, 0.239, 0.239), (0.329, 0.329, 0.329), \
            (0.9, 0.9, 0.9), (0.278, 0.447, 0.702)
        if blender_theme is not None:
            ui = blender_theme.user_interface
            back = tuple(blender_theme.properties.space.back)
            panel = tuple(ui.panel_back)[:3]
            inner = tuple(ui.wcol_regular.inner)[:3]
            text = tuple(ui.wcol_regular.text)[:3]
            sel = tuple(ui.wcol_list_item.inner_sel)[:3]
        light = sum(back[:3]) / 3 > 0.5
        self.app = _rgba(_mix(back, (0, 0, 0), 0.06 if light else 0.22))        # level 1: the nav's ground
        self.surface = _rgba(back)          # level 2: the content panel (the tab's columns)
        self.well = _rgba(_mix(back, (0, 0, 0), 0.1 if light else 0.33))         # level 3: the tab's view
        self.card = _rgba(panel)
        self.control = _rgba(inner)
        self.hover = _rgba(inner, 0.5)
        self.accent = _rgba(sel)
        self.text1 = _rgba(text)
        self.text2 = _rgba(text, 0.7)
        self.text3 = _rgba(text, 0.45)
        self.divider = _rgba(text, 0.04)
        self.selected = _rgba(sel, 0.4)        # a selected row: the theme's selection, quieter than a button
        self.ring = _rgba((1, 1, 1), 0.85)
        self.error = (0.9, 0.35, 0.3, 1.0)
        self.ok = (0.35, 0.8, 0.45, 1.0)
