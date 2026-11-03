"""The editor's tabs: each Fortnite area shows one (its node tree holds the id: see host). A tab brings its header
contents, its view (drawn in the shell's well), and optionally its list column and its info column (ui/shell.py)."""


from . import host

SIDEBAR = "Fortnite"     # the one sidebar category: its panels poll on the main tab, so no tab strip shows


class Tab:
    def __init__(self, id, label, icon, header=None, draw=None, tick=None, nav_icon=None, group="export", side=None,
                 info=None, hover=None, has_info=None, has_side=None, view=None):
        self.id, self.label, self.icon = id, label, icon
        self.nav_icon = nav_icon    # its icon in the shell's nav (icons/<name>.png)
        self.group = group          # the nav's group: export or tools
        self.header = header        # header(layout, context): the tab's tools in the editor's header
        self.draw = draw            # draw(): its view, a POST_PIXEL drawing in the region (host.inset: the well)
        self.side = side            # side(ui, rect): its list column (ui.kit widgets), None: no column
        self.view = view            # view(ui, rect): widgets in the well, over draw()'s view
        self.info = info            # info(ui, rect): its info column, None: no column
        self.has_info = has_info    # has_info(context) -> bool: the info column folds when it has nothing to show
        self.has_side = has_side    # has_side(context) -> bool: the same for the list column
        self.hover = hover          # hover(context, x, y) -> changed: the mouse over its view (x None: it left)
        self.tick = tick            # tick(): the editor's timer, for the tab's own engine traffic


_TABS = []


def register_tab(tab):
    for i, known in enumerate(_TABS):
        if known.id == tab.id:
            _TABS[i] = tab
            return
    _TABS.append(tab)


def unregister_tab(tab_id):
    _TABS[:] = [t for t in _TABS if t.id != tab_id]


def ids():
    return [t.id for t in _TABS]


def all_tabs():
    return list(_TABS)


def current(space):
    """The area's tab; an empty or unknown id (a tab whose module isn't loaded) is the first tab."""
    wanted = host.tab_id(space)
    return next((t for t in _TABS if t.id == wanted), _TABS[0] if _TABS else None)


def is_tab(context, tab_id):
    space = context.space_data
    tab = current(space) if host.is_editor(space) else None
    return tab is not None and tab.id == tab_id
