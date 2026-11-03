"""FP's layout in the editor's main region: the nav (FP's left bar: labelled when the area is wide, an icon rail
otherwise), the tab's list column, the content well (the tab's own view, drawn under the shell and rounded by it), the
tab's info column. Narrow areas fold the info, then the list column (the header has their essentials)."""
from .rect import Rect

GROUPS = (("export", "Export"), ("tools", "Tools"))
WIDE, NO_INFO, NO_SIDE = 1500, 900, 700        # area widths (px at UI scale 1) where columns fold
ICON_COLUMN = 32        # px: the icon rail's width, and a list column's dragged down to icons only
COMPACT_AT, SIDE_MAX = 110, 360         # px: a list column dragged narrower shows icons only; the widest it gets
INFO_MIN, INFO_MAX = 220, 480


def margin(t, frame):
    """The space round the nav's icons and round the content panel: Blender's arrow strip when it shows one."""
    return frame or t.pad


def columns(tab, width, height, t, left=0, right=0, frame=0, has_info=True, has_side=True, side_w=None, info_w=None,
            status=False):
    """{nav, panel, side, info, well, wide, compact}: FP's three levels in a region of that size, past `left` /
    `right` px that Blender's own regions cover. The nav sits on the app's ground with the same margin each side of
    its icons; the content panel (the tab's columns and its well) keeps that margin to every edge. `side_w` /
    `info_w`: the columns' widths as dragged (None: their defaults); a list column dragged narrow shows icons only.
    `has_info` / `has_side` False fold an empty column, a narrow area folds the info column then the list column.
    `status`: a status bar along the bottom (on the app's ground, like FP's), the nav and panel above it."""
    s = t.s
    m = margin(t, frame)
    room = width - left - right
    wide = room >= WIDE * s
    bottom = m + t.row + t.pad if status else m
    nav = Rect(left + m, bottom, (t.nav_wide - 2 * t.pad) if wide else ICON_COLUMN * s, height - m - bottom)
    panel = Rect(nav.right + m, bottom, width - right - m - (nav.right + m), height - m - bottom)
    bar = Rect(nav.x, m, panel.right - nav.x, t.row) if status else None
    inner = panel.pad(t.pad)
    side = info = None
    compact = False
    if tab is not None and tab.side is not None and has_side and room >= NO_SIDE * s:
        want = t.side if side_w is None else side_w
        compact = want < COMPACT_AT * s
        side = inner.cut_left(ICON_COLUMN * s if compact else min(max(want, COMPACT_AT * s), SIDE_MAX * s))
        inner.cut_left(t.pad)
    if tab is not None and tab.info is not None and has_info and room >= NO_INFO * s:
        info = inner.cut_right(min(max(t.info if info_w is None else info_w, INFO_MIN * s), INFO_MAX * s))
        inner.cut_right(t.pad)
    return {"nav": nav, "panel": panel, "side": side, "info": info, "well": inner, "wide": wide, "compact": compact,
            "status": bar}


def corners(t, cols):
    """The panel's corner radii (top right, bottom right, top left, bottom left): concentric with what sits in each
    corner (the well and the info card round as cards, the list column's rows and the info column's buttons as
    controls), one panel padding out."""
    card, control = t.r_card + t.pad, t.r_small + t.pad
    left = control if cols["side"] is not None else card
    return (card, control if cols["info"] is not None else card, left, left)


def draw(ui, tabs_list, current, width, height, actions, left=0, right=0, frame=0, has_info=True, has_side=True,
         side_w=None, info_w=None, resize=None):
    """The shell over the tab's view. `actions`: {"tab": fn(tab_id) -> callable, "settings": callable,
    "status": fn(ui, cols) drawing the status bar and what opens from it (last: over everything), "well": fn(ui, well)
    drawing over the tab's view (the guided setup)};
    `resize`: (fn(column, width), ...) for the columns' drag handles."""
    from . import paint
    t = ui.t
    cols = columns(current, width, height, t, left, right, frame, has_info, has_side, side_w, info_w,
                   status="status" in actions)
    panel, well = cols["panel"], cols["well"]
    # level 2 (the panel) over whatever the tab's view drew round the well, the well's corners rounded into it
    paint.rect(Rect(panel.x, panel.y, well.x - panel.x, panel.h), t.surface)
    paint.rect(Rect(well.right, panel.y, panel.right - well.right, panel.h), t.surface)
    paint.rect(Rect(well.x, panel.y, well.w, well.y - panel.y), t.surface)
    paint.rect(Rect(well.x, well.top, well.w, panel.top - well.top), t.surface)
    paint.rect(well, t.surface, t.r_card, invert=True)
    # level 1 (the app) round the panel, its corners rounded into it
    paint.rect(Rect(0, 0, panel.x, height), t.app)
    paint.rect(Rect(panel.right, 0, width - panel.right, height), t.app)
    paint.rect(Rect(panel.x, 0, panel.w, panel.y), t.app)
    paint.rect(Rect(panel.x, panel.top, panel.w, height - panel.top), t.app)
    paint.rect(panel, t.app, corners=corners(t, cols), invert=True)
    ui.frame.well = well
    if getattr(current, "view", None) is not None:
        ui.clip = None
        current.view(ui, well)
    _nav(ui, cols["nav"], cols["wide"], tabs_list, current, actions)
    if cols["side"] is not None:
        ui.clip, ui.compact = None, cols["compact"]
        current.side(ui, cols["side"])
        ui.compact = False
        if resize:
            _handle(ui, "resize:side", Rect(cols["side"].right, well.y, t.pad, well.h), resize[0], cols["side"].w, 1)
    if cols["info"] is not None:
        ui.clip = None
        current.info(ui, cols["info"])
        if resize:
            _handle(ui, "resize:info", Rect(well.right, well.y, t.pad, well.h), resize[1], cols["info"].w, -1)
    ui.clip = None
    if "well" in actions:
        actions["well"](ui, well)
    if cols["status"] is not None:
        actions["status"](ui, cols)
        ui.clip = None
    return cols


def _handle(ui, id, gap, set_width, width, direction):
    """A column's drag handle on the gap beside it: a line while hot; dragging sets the column's width."""
    from . import paint
    t = ui.t
    grab = Rect(gap.x - t.gap, gap.y, gap.w + 2 * t.gap, gap.h)
    if ui.hot(id) or ui.pressed(id):
        mid = gap.x + gap.w / 2
        paint.rect(Rect(mid - t.s, gap.y + gap.h * 0.25, 2 * t.s, gap.h * 0.5), t.text3, t.s)

    def drag(context, dx, dy):       # from the width it had when pressed (a redraw mid-drag doesn't move it)
        set_width(context, width + dx * direction)
    ui.drag(id, grab, drag, "Drag to resize (narrow: icons only)" if direction > 0 else "Drag to resize")


def _nav(ui, r, wide, tabs_list, current, actions):
    t = ui.t
    inner = r.pad(0, t.pad)     # the icons' column: level with the panel's content
    settings = inner.cut_bottom(32 * t.s if not wide else 28 * t.s)
    inner.cut_bottom(t.gap)
    drawer = inner.cut_bottom(32 * t.s if not wide else 28 * t.s)
    groups = [(title, [tab for tab in tabs_list if tab.group == group]) for group, title in GROUPS]
    groups = [g for g in groups if g[1]]
    for n, (title, members) in enumerate(groups):
        if n:       # between groups: a divider (rail) or a gap (labelled), centred in it
            if wide:
                inner.cut_top(t.pad)
            else:
                ui.divider(inner.x + 4 * t.s, inner.right - 4 * t.s, inner.top - t.pad)
                inner.cut_top(2 * t.pad)
        if wide:
            ui.label(inner.cut_top(t.row_pitch), title, t.text3, t.caption)
        for k, tab in enumerate(members):
            if k:
                inner.cut_top(t.gap)
            cell = inner.cut_top(28 * t.s if wide else 32 * t.s)
            selected = tab is current
            if wide:
                ui.row("nav:" + tab.id, cell, tab.label, tab.nav_icon, selected, actions["tab"](tab.id))
            else:
                ui.button("nav:" + tab.id, cell, icon=tab.nav_icon, action=actions["tab"](tab.id), tip=tab.label,
                          selected=selected, style="flat")
    if wide:
        ui.row("nav:drawer", drawer, "Options", "mdi_Tune", actions["drawer_open"], actions["drawer"])
        ui.row("nav:settings", settings, "Export Options", "mdi_FileCog", False, actions["settings"])
    else:
        ui.button("nav:drawer", drawer, icon="mdi_Tune", action=actions["drawer"], tip="Options (the sidebar: N)",
                  selected=actions["drawer_open"], style="flat")
        ui.button("nav:settings", settings, icon="mdi_FileCog", action=actions["settings"], tip="Export Options",
                  style="flat")
