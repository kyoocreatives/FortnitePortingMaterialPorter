"""The editor's Materials tab: the built materials of a scope in the list column (grouped by parent shader, feature or
mesh, ticked one by one or a group at once) and the parameters of the ticked ones in the well, in sections, with
their UE names. A mark shows where the ticked materials differ."""
import bpy

from . import host, mat_data, mat_edit, tabs
from .ui import colorpick, fields, paint
from .ui import blender as ui_blender
from .ui.frame import Hit
from .ui.rect import Rect

ID = "materials"
# scope / group: the list's; ticked: material names; folds: {group or section: open}
# relative: drags scale each material's value instead of setting one
STATE = {"scope": "SELECTION", "group": "SHADER", "ticked": set(), "folds": {}, "relative": False}


def redraw_all():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if host.is_editor_area(area) and host.tab_id(area.spaces.active) == ID:
                area.tag_redraw()


def shown_materials(context):
    """[(material, objects)] of the scope, filtered by the header's search."""
    words = context.window_manager.fp_mat_search.lower().split()
    return [(m, o) for m, o in mat_data.materials(context, STATE["scope"])
            if all(w in m.name.lower() or w in str(m.get("mp_master", "")).lower() for w in words)]


def ticked(context):
    names = STATE["ticked"]
    return [m for m, _o in shown_materials(context) if m.name in names]


# ------------------------------------------------------------------ the list column

def _set_ticks(names, on):
    def run(context):
        if on:
            STATE["ticked"] |= set(names)
        else:
            STATE["ticked"] -= set(names)
    return run


def _solo(name):
    def run(context):
        STATE["ticked"] = {name}
    return run


def _fold(key, default=True):
    def run(context):
        STATE["folds"][key] = not STATE["folds"].get(key, default)
    return run


def tick_box(ui, id, r, state, action, tip=""):
    """A checkbox: True, False or None (some of a group)."""
    t = ui.t
    size = 14 * t.s
    box = Rect(r.x + (r.w - size) / 2, r.y + (r.h - size) / 2, size, size)
    lit = ui.hot(id)
    paint.rect(box, t.accent if state else (t.hover[:3] + (1.0,) if lit else t.control), 4 * t.s)
    if state:
        ui.icon(box, "mdi_Check", t.text1)
    elif state is None:
        paint.rect(Rect(box.x + 3 * t.s, box.y + box.h / 2 - t.s, box.w - 6 * t.s, 2 * t.s), t.text1, t.s)
    ui.frame.add(Hit(id, r, ui.clip, "button", action, None, tip))


def _menu(id, choices, key):
    def run(context):
        from .ui import popups
        frame = ui_blender.FRAMES.get(context.area.as_pointer())
        hit = frame.find(id) if frame is not None else None
        items = [{"label": label, "check": STATE[key] == value, "action": _choose(key, value), "id": value}
                 for value, label in choices]
        popups.open_menu(context, hit.rect if hit is not None else Rect(0, 0, 0, 0), items, id, search=False)
    return run


def _choose(key, value):
    def run(context):
        STATE[key] = value
        redraw_all()
    return run


def _rows(context):
    """The list's rows: ("group", (title, materials, open)) and ("mat", (material, objects, group title))."""
    mats = shown_materials(context)
    users = {m.name: o for m, o in mats}
    rows = []
    for title, members in mat_data.groups(mats, STATE["group"]):
        key = "g:" + title
        is_open = STATE["folds"].get(key, True)
        rows.append(("group", (title, members, is_open)))
        if is_open:
            rows += [("mat", (m, users.get(m.name, []), title)) for m in members]
    return rows


def side(ui, r):
    t = ui.t
    context = bpy.context
    labels = dict(mat_data.SCOPES)
    for id, choices, key, tip in (("mat:scope", mat_data.SCOPES, "scope", "Which materials the list shows"),
                                  ("mat:group", mat_data.GROUPS, "group", "How the list groups them")):
        row = r.cut_top(t.row)
        r.cut_top(t.gap)
        text = (labels if key == "scope" else dict(mat_data.GROUPS))[STATE[key]]
        fields.dropdown(ui, id, row, text if key == "scope" else "By " + text, _menu(id, choices, key), tip=tip)
    r.cut_top(t.pad)
    rows = _rows(context)
    if not rows:
        paint.text_in("No built materials here", r.cut_top(t.row).pad(t.pad, 0), t.text3, t.text)
        return
    offset = ui.scroll("mat:list", r, len(rows) * t.row_pitch)
    y = r.top + offset
    names = STATE["ticked"]
    for kind, payload in rows:
        y -= t.row_pitch
        if y > r.top or y + t.row_pitch < r.y:
            continue
        cell = Rect(r.x, y + (t.row_pitch - t.row) / 2, r.w - ui.bar_room, t.row)
        box = Rect(cell.x, cell.y, t.row, t.row)
        if kind == "group":
            title, members, is_open = payload
            on = sum(m.name in names for m in members)
            state = True if on == len(members) else (False if not on else None)
            head = Rect(cell.x + t.row, cell.y, cell.w - t.row, cell.h)
            count = "  (%d)" % len(members)
            room = head.w - 2 * t.pad - t.icon - paint.measure(count, t.text)
            ui.group("mat:g:" + title, head, paint.fit(title, t.text, room) + count, is_open, _fold("g:" + title))
            tick_box(ui, "mat:gt:" + title, box, state, _set_ticks([m.name for m in members], state is not True),
                     "Untick the group" if state else "Tick the whole group")
        else:
            m, objs, group = payload
            rid = "mat:m:%s:%s" % (group, m.name)
            tip = "%s · %s" % (m.get("mp_master", ""), ", ".join(o.name for o in objs[:3]) or "no mesh")
            ui.row(rid, Rect(cell.x + t.row, cell.y, cell.w - t.row, cell.h), mat_data.short(m.name),
                   selected=m.name in names, action=_solo(m.name), tip=tip, depth=0)
            tick_box(ui, rid + ":tick", box, m.name in names, _set_ticks([m.name], m.name not in names))
            if (ui.store.hot or "").startswith(rid):        # hovered: the material's own reset, when it changed
                changed = [p for _s, ps in mat_data.gather([m]) for p in ps if mat_edit.resettable(p)]
                if changed:
                    ui.button(rid + ":reset", cell.copy().cut_right(t.row), icon="mdi_Restore", style="flat",
                              action=mat_edit.reset(changed, "Reset " + mat_data.short(m.name)),
                              tip="%d parameters back to the game's values" % len(changed))
    ui.end_scroll()


# ------------------------------------------------------------------ the parameters (the well)

def _fmt(v):
    return "%.3f" % v if abs(v) < 1000 else "%.1f" % v


def _type_in(p, comp, relative, v, integer, fmt):
    """A number field's click: a pop-up field with the keys (the shell's own fields hear no keys)."""
    def run(context):
        from .ui import popup, popups
        from .ui.textedit import Text
        if _field_id(p, comp) in fields._dragged:        # the release of a drag, not a click
            fields._dragged.discard(_field_id(p, comp))
            return
        frame = ui_blender.FRAMES.get(context.area.as_pointer())
        hit = frame.find(_field_id(p, comp)) if frame is not None else None
        fid = "mat:type:field"

        def keep(context, value):
            mat_edit.typed(p, comp, relative)(context, value)
            ui_blender.close_all()

        def content(ui, col):
            fields.number(ui, fid, col.take(), v, keep, integer, fmt=fmt)
        popups.open_panel(context, hit.rect if hit is not None else Rect(0, 0, 0, 0), content, "mat:type",
                          width=max(120.0, (hit.rect.w if hit is not None else 120.0) / ui_blender.theme(context).s))
        popup.focus(ui_blender.store(context.area), fid, Text(fmt % v))
    return run


def _field_id(p, comp=None):
    return "mat:v:%s:%s" % (p.name, p.kind) + ("" if comp is None else ":%d" % comp)


def _number(ui, p, r, v, comp=None):
    relative = STATE["relative"]
    integer = p.kind == "INT" and not relative
    fmt = "×%.3f" if relative else ("%d" if integer else "%.3f")
    shown = mat_edit.factor(p) if relative else v
    fields.number(ui, _field_id(p, comp), r, shown, mat_edit.drag(p, comp, relative), integer, fmt=fmt,
                  step=0.005 if relative else None)
    ui.frame.hits[-1].action = _type_in(p, comp, relative, shown, integer, fmt.replace("×", ""))


def _pick_colour(p, r):
    def run(context):
        first = p.sockets[0][1]

        def all_follow(context):
            for m, s in p.sockets:
                if s != first:
                    s.default_value = first.default_value
                mat_data.params.set_override(m, p.name, mat_data.value(s))
        all_follow(context)
        colorpick.open_for(context, r, first, "default_value", on_change=all_follow, title=p.name)
    return run


def _value_field(ui, p, r):
    """The value of the first ticked material that has it; a change goes to all of them."""
    t = ui.t
    v = p.values()[0]
    if p.kind == "COLOR":
        fields.swatch(ui, _field_id(p), r, colorpick.linear_to_srgb(v[:3]), _pick_colour(p, r.copy()),
                      "Pick a colour for the ticked materials")
    elif p.kind == "VECTOR":
        w = (r.w - 2 * t.gap) / 3
        for i in range(3):
            _number(ui, p, Rect(r.x + i * (w + t.gap), r.y, w, r.h), v[i], i)
    elif p.kind == "BOOL":
        fields.checkbox(ui, _field_id(p), r, bool(v), "", mat_edit.set_value(p, not v))
    else:
        _number(ui, p, r, v)


def _param_row(ui, p, r, count):
    t = ui.t
    back = r.cut_right(t.row)
    r.cut_right(t.gap)
    label = r.cut_left(max(r.w - 320 * t.s, r.w * 0.45))       # a field Blender's width, the name the rest
    mark = r.cut_left(t.row)
    if p.differs:
        paint.text_in("≠", mark, t.accent[:3] + (1.0,), t.title * 1.4, "center")
        ui.frame.add(Hit("mat:d:" + p.name, mark, ui.clip, "button", None, None,
                         "The ticked materials differ: the field shows the first, a change sets them all"))
    tip = p.name if p.master is None else "%s · parent shader's default %s" % (p.name, _short_value(p.master))
    if len(p.sockets) < count:
        tip += " · %d of %d materials" % (len(p.sockets), count)
    paint.text_in(paint.fit(p.name, t.text, label.w - t.pad), label.pad(t.pad, 0),
                  t.text1 if len(p.sockets) == count else t.text3, t.text)
    ui.frame.add(Hit("mat:l:" + p.name, label, ui.clip, "button", None, None, tip))
    _value_field(ui, p, r)
    if mat_edit.resettable(p):
        ui.button("mat:r:" + p.name, back, icon="mdi_Restore", action=mat_edit.reset([p], "Reset " + p.name),
                  style="flat", tip="Back to the game's value")


def _short_value(v):
    if isinstance(v, (list, tuple)):
        return "(%s)" % ", ".join(_fmt(float(x)) for x in v)
    return _fmt(float(v)) if isinstance(v, (int, float)) else str(v)


def _jump(m):
    """Show the material's group node in a Shader Editor (its object made active, the slot picked)."""
    def run(context):
        node = mat_data.params.root_node(m)
        editors = [a for w in context.window_manager.windows for a in w.screen.areas
                   if a.type == 'NODE_EDITOR' and not host.is_editor_area(a) and a.spaces.active.tree_type == 'ShaderNodeTree']
        obj = next((o for o in context.scene.objects if any(s.material == m for s in getattr(o, "material_slots", ()))), None)
        if obj is not None:
            context.view_layer.objects.active = obj
            obj.active_material_index = next(i for i, s in enumerate(obj.material_slots) if s.material == m)
        for n in m.node_tree.nodes:
            n.select = n == node
        m.node_tree.nodes.active = node
        if not editors:
            from . import logbook
            logbook.write("Open a Shader Editor to see %s's nodes (selected there)" % mat_data.short(m.name), "warning")
            return
        for a in editors:
            a.spaces.active.pin = False
            a.tag_redraw()
    return run


def rebuild_on(mats, path):
    """Rebuild the materials on the game material at `path` (None: their own); the ticks follow the new ones."""
    def run(context):
        from ...material_porter import rebase
        from . import logbook
        mat_edit.end()
        done, failed = rebase.rebuild(mats, path)
        STATE["ticked"] = {done.get(n, n) for n in STATE["ticked"]}
        what = path.rsplit("/", 1)[-1].split(".")[0] if path else "their own shader"
        if done:
            mat_edit.undo("Rebuild on " + what)
            logbook.write("%d material(s) rebuilt on %s" % (len(done), what))
        if failed:
            logbook.write("Not rebuilt (is the app open?): " + ", ".join(mat_data.short(n) for n in failed[:4]), "warning")
    return run


def _rebuild_items(mats):
    """Back to their own, the two-tone rim master, and every other game material in the file."""
    from ...material_porter import build, rebase
    ticked = {m.get(build.KEY_PATH) for m in mats}
    items = []
    if any(rebase.current(m) for m in mats):
        items.append({"label": "Their Own Shader", "icon": "mdi_Restore", "action": rebuild_on(mats, None)})
    items += [{"header": "Shaders"},
              {"label": "Two-Tone Rim (anime, Sabotage's blue/orange rim)", "action": rebuild_on(mats, rebase.TWO_TONE)},
              {"header": "Materials in this file"}]
    seen = {}
    for m in bpy.data.materials:
        path = m.get(build.KEY_PATH)
        if path and path not in ticked and path not in seen:
            seen[path] = "%s  ·  %s" % (mat_data.short(m.get("OriginalName") or m.name), m.get("mp_master", ""))
    items += [{"label": label, "action": rebuild_on(mats, path)} for path, label in sorted(seen.items(), key=lambda kv: kv[1].lower())]
    return items


def _open_rebuild(mats, cell):
    def run(context):
        from .ui import popups
        popups.open_list(context, cell, lambda: _rebuild_items(mats), "mat:rebuild", "Search materials", width=360)
    return run


def _toggle_relative(context):
    mat_edit.end()
    STATE["relative"] = not STATE["relative"]


def _toolbar(ui, r, mats, sections):
    t = ui.t
    all_params = [p for _s, ps in sections for p in ps]
    from ...material_porter import rebase
    rebased = {rebase.current(m) for m in mats} - {None}
    buttons = (("mat:rebuild", "Rebuild On…", True,
                "Rebuild the ticked materials on another game material's shader, keeping their values and edits"
                + ("\nNow on " + ", ".join(p.rsplit("/", 1)[-1] for p in sorted(rebased)) if rebased else "")),
               ("mat:nodes", "Node Editor", _jump(mats[0]), "%s's group node in the Shader Editor" % mat_data.short(mats[0].name)),
               ("mat:reset", "Reset to Game", mat_edit.reset(all_params) if any(map(mat_edit.resettable, all_params)) else None,
                "Every parameter of the ticked materials back to the game's value"),
               ("mat:paste", "Paste", mat_edit.paste(sections) if mat_edit.CLIP else None,
                "The copied values onto the ticked materials' parameters of the same names"),
               ("mat:copy", "Copy", mat_edit.copy(sections), "%s's values" % mat_data.short(mats[0].name)),
               ("mat:relative", "Relative", _toggle_relative, "Drags and typed values scale each material's own "
                "value (× factor) instead of setting one value"))
    for id, text, action, tip in buttons:
        cell = r.cut_right(paint.measure(text, t.text) + 2 * t.pad + 2 * t.pad)
        if id == "mat:rebuild":
            action = _open_rebuild(mats, cell)
        ui.button(id, cell, text, action=action, tip=tip, enabled=action is not None,
                  style="accent" if id == "mat:relative" and STATE["relative"] or id == "mat:rebuild" and rebased else "control")
        r.cut_right(t.gap)
    title = mat_data.short(mats[0].name) if len(mats) == 1 else "%d materials" % len(mats)
    paint.text_in(paint.fit(title, t.title, r.w - t.pad), r, t.text1, t.title)


def view(ui, well):
    t = ui.t
    context = bpy.context
    mats = ticked(context)
    inner = well.pad(t.big)
    if not mats:
        paint.text_in("Tick materials in the list to see their parameters", inner, t.text3, t.title, "center")
        return
    head = inner.cut_top(t.row)
    inner.cut_top(t.pad)
    sections = mat_data.gather(mats)
    _toolbar(ui, head, mats, sections)
    lines = sum(1 + (len(ps) if STATE["folds"].get("s:" + s, True) else 0) for s, ps in sections)
    offset = ui.scroll("mat:params", inner, lines * t.row_pitch)
    y = inner.top + offset
    width = min(inner.w - ui.bar_room - t.pad, 760 * t.s)     # clear of the scroll bar
    for title, ps in sections:
        is_open = STATE["folds"].get("s:" + title, True)
        y -= t.row_pitch
        if inner.y - t.row_pitch < y < inner.top:
            ui.group("mat:s:" + title, Rect(inner.x, y, width, t.row), title, is_open,
                     _fold("s:" + title))
        if not is_open:
            continue
        for p in ps:
            y -= t.row_pitch
            if inner.y - t.row_pitch < y < inner.top:
                _param_row(ui, p, Rect(inner.x, y + (t.row_pitch - t.row) / 2, width, t.row), len(mats))
    ui.end_scroll()


# ------------------------------------------------------------------ the header

def header(layout, context):
    row = layout.row(align=True)
    row.ui_units_x = 10
    row.prop(context.window_manager, "fp_mat_search", text="", icon='VIEWZOOM')


def _searched(self, context):
    redraw_all()


def tick():
    pressed = next((ui_blender.store(a).pressed for a in host.editor_areas() if ui_blender.store(a).pressed), None)
    mat_edit.tick(pressed)


def register():
    bpy.types.WindowManager.fp_mat_search = bpy.props.StringProperty(
        name="Search", description="Materials whose name or parent shader has these words",
        options={'TEXTEDIT_UPDATE'}, update=_searched)
    tabs.register_tab(tabs.Tab(ID, "Materials", 'MATERIAL', header=header, nav_icon="Wrap", group="tools", tick=tick,
                               side=side, view=view))
    # material names are long: a wider list column than the other tabs' (until dragged)
    ui_blender.DEFAULT_WIDTHS.setdefault((ID, "side"), 280 * bpy.context.preferences.system.ui_scale)


def unregister():
    tabs.unregister_tab(ID)
    del bpy.types.WindowManager.fp_mat_search
