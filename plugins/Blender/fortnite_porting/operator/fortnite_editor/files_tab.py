"""The editor's Files tab: every game file searched by name (search first), folders browsed when
the search is empty; a dense list in the well, the folder tree in the list column, the picked file's card in the info
column (texture picture, kind, folder, Import, Properties as a text, Copy Path). The engine answers files-* routes;
Blender imports the payloads."""
import time

import bpy

from . import host, import_options, importing, rows, tabs, textures
from .state import STATE, client, tag_redraw
from .ui import blender as ub
from .ui import paint
from .ui.rect import Rect

KINDS = ("Mesh", "Texture", "Material", "Animation", "Sound", "Map", "Blueprint", "Other")
KIND_COLORS = {"Mesh": (0.35, 0.65, 0.95, 1), "Texture": (0.95, 0.55, 0.30, 1), "Material": (0.55, 0.85, 0.40, 1),
               "Animation": (0.85, 0.45, 0.85, 1), "Sound": (0.95, 0.80, 0.30, 1), "Map": (0.30, 0.85, 0.75, 1),
               "Blueprint": (0.40, 0.55, 0.95, 1), "Other": (0.6, 0.6, 0.6, 1)}


class Search:
    """Debounced search: asked after the typing pauses; an answer to an older query is dropped; while the app still
    builds its file list the query is asked again a second later."""

    def __init__(self, delay=0.25):
        self.delay, self.pending, self.asked_at, self.next_id, self.sent_id, self.result = delay, None, 0.0, 0, None, None
        self.sent, self.answered_id = None, None

    @property
    def waiting(self):
        """A query is typed or sent and its answer not in yet."""
        return self.pending is not None or (self.sent_id is not None and self.answered_id != self.sent_id)

    def ask(self, query, kind, now):
        if not query:       # an empty box shows the folder instead: nothing to ask, the old answer goes
            self.pending, self.result, self.sent_id = None, None, None
            return
        self.pending, self.asked_at = (query, kind), now

    def due(self, now):
        if self.pending is None or now - self.asked_at < self.delay:
            return None
        self.next_id += 1
        self.sent_id = self.next_id
        self.sent, self.pending = self.pending, None
        return (self.sent_id,) + self.sent

    def accept(self, query_id, result, now=0.0):
        if query_id != self.sent_id:
            return False
        if isinstance(result, dict) and result.get("loading"):
            if self.pending is None:
                self.pending, self.asked_at = self.sent, now + 1.0 - self.delay
            return False
        self.answered_id, self.result = query_id, result
        return True


FILES = {"search": Search(), "folder": None, "folder_asked": None, "folder_retry": 0.0, "folder_error": None,
         "selected": [], "anchor": None, "info": {}, "rows": {}, "hover": {}, "pictures": {}}


def reset():
    """Forget the folder and the search (Refresh, after FP loaded another game version)."""
    FILES.update(search=Search(), folder=None, folder_asked=None, folder_retry=0.0, folder_error=None, info={})


def got_folder(folder, result, now):
    if isinstance(result, dict) and result.get("loading"):     # the app still builds its file list: ask again soon
        FILES["folder_asked"], FILES["folder_retry"] = None, now + 1.0
        return False
    if isinstance(result, Exception):
        FILES["folder_error"] = "%s" % result
    elif bpy.context.window_manager.fp_files_folder == folder:
        FILES["folder"], FILES["folder_error"] = result, None
    tag_redraw()


def shown(wm):
    """The rows on screen: the search's answer, or the current folder's files when the search is empty."""
    if wm.fp_files_query.strip():
        found = (FILES["search"].result or {}).get("items") or []
    else:
        found = (FILES["folder"] or {}).get("files") or []
    return found


def kind_label(item, info):
    """The kind a row shows: read from the file once picked (its class when it's none of KINDS), the name's guess
    before; nothing when the guess is "Other"."""
    if isinstance(info, dict):
        return info["class"] if info["kind"] == "Other" else info["kind"]
    return "" if item["kind"] == "Other" else item["kind"]


# ------------------------------------------------------------------ engine traffic (the editor's timer)

def tick():
    e, wm = client(), bpy.context.window_manager
    if e.state != "ready":
        return
    due = FILES["search"].due(time.time())
    if due is not None:
        query_id, query, kind = due

        def done(result):
            if isinstance(result, Exception):
                if query_id == FILES["search"].sent_id:
                    STATE["error"] = "Search: %s" % result
                    FILES["search"].accept(query_id, None)
            elif FILES["search"].accept(query_id, result, time.time()):
                for area_rows in FILES["rows"].values():
                    area_rows["scroll"] = 0.0
            else:
                return False        # an older query's answer, or "still loading": nothing shown changed
            tag_redraw()
        e.request("files-search", done, q=query, **({"type": kind} if kind != 'ALL' else {}))
    folder = wm.fp_files_folder
    if FILES["folder_asked"] != folder and time.time() >= FILES["folder_retry"]:
        FILES["folder_asked"], FILES["folder_error"] = folder, None
        e.request("files-folder", lambda result, f=folder: got_folder(f, result, time.time()), path=folder)
    picked = FILES["selected"][-1] if FILES["selected"] else None
    if picked and picked not in FILES["info"]:
        FILES["info"][picked] = "asked"

        def got_info(result, path=picked):
            FILES["info"][path] = None if isinstance(result, Exception) else result
            if FILES["info"][path] and FILES["info"][path]["kind"] == "Texture":
                e.request("texture", lambda rec, p=path: _picture_file(p, rec), path=_object_path(path), cap=512)
            tag_redraw()
        e.request("files-info", got_info, path=picked)


def _object_path(path):
    """A game file's object path, as the texture route takes it (/Game/... or /<Plugin>/...)."""
    p = path.rsplit(".", 1)[0]
    if p.startswith("FortniteGame/Content/"):
        p = "/Game/" + p[len("FortniteGame/Content/"):]
    elif "/Plugins/" in p and "/Content/" in p:
        plugin = p.split("/Content/", 1)[0].rsplit("/", 1)[-1]
        p = "/%s/%s" % (plugin, p.split("/Content/", 1)[1])
    name = p.rsplit("/", 1)[-1]
    return "%s.%s" % (p, name)


def _picture_file(path, rec):
    if isinstance(rec, Exception):
        return
    FILES["pictures"][path] = rec.get("file") or rec.get("File")
    tag_redraw()


def picture(path):
    """The picked texture's picture as a GPU texture, None before it's written (or when Blender can't read it)."""
    file, key = FILES["pictures"].get(path), "files:" + path
    return textures.get(key) if file and textures.add(key, file) else None


# ------------------------------------------------------------------ the well: the file list

def row_rect(state, i):
    """Row i's rect in region pixels (the list is laid out in the well, `y0` up from the region's bottom)."""
    x, y, w, h = state["layout"].rect(i)
    return Rect(x, y + state["y0"], w, h)


def row_at(state, x, y):
    layout = state["layout"]
    if not 0 <= y - state["y0"] < layout.height:
        return None         # the well's margin: rows scrolled out of view are there
    return layout.hit(x, y - state["y0"])


def draw():
    context = bpy.context
    area, region, wm = context.area, context.region, context.window_manager
    t = ub.theme(context)
    body = ub.well(area, region, tabs.current(area.spaces.active)).pad(t.r_card - t.r_small)     # rows concentric with the well
    found = shown(wm)
    state = FILES["rows"].setdefault(area.as_pointer(), {"scroll": 0.0})
    layout = rows.Rows(body.w, body.h, t.row_pitch, len(found), state["scroll"], body.x)
    state.update(layout=layout, items=found, y0=body.y)
    paint.begin(region)
    try:
        paint.push_clip(body)
        if found:
            _list(t, state, found, FILES["hover"].get(area.as_pointer()))
        else:
            paint.text_in(_empty_text(wm), body, t.text2, t.title, "center")
    finally:
        paint.end()


def _empty_text(wm):
    if wm.fp_files_query.strip():
        return "Searching..." if FILES["search"].waiting else "No file matches"
    got = FILES["folder"]
    if got and got.get("folders"):
        return ""           # only folders here: they're in the list column
    return FILES["folder_error"] or ("No files in this folder" if got else "Loading files...")


def _list(t, state, found, hover):
    layout, selected = state["layout"], set(FILES["selected"])
    searching = bool(bpy.context.window_manager.fp_files_query.strip())
    with_pak = layout.width >= 700 * t.s
    kind_w = 96 * t.s
    name_w = layout.width * (0.36 if searching else 0.6)
    rest = layout.width - name_w - kind_w - 3 * t.big
    for i in layout.visible:
        item, r = found[i], row_rect(state, i)
        if item["path"] in selected:
            paint.rect(r, t.selected, t.r_small)
        elif i == hover:
            paint.rect(r, t.hover, t.r_small)
        elif i % 2:
            paint.rect(r, t.text1[:3] + (0.025,), t.r_small)
        info = FILES["info"].get(item["path"])
        kind = info["kind"] if isinstance(info, dict) else item["kind"]
        color = KIND_COLORS.get(kind, KIND_COLORS["Other"])
        inner = r.pad(t.pad, 0)
        dot = inner.cut_left(8 * t.s)
        paint.rect(Rect(dot.x, dot.y + (dot.h - dot.w) / 2, dot.w, dot.w), color, 2 * t.s)
        inner.cut_left(t.pad)
        paint.text_in(item["name"], inner.cut_left(name_w - 14 * t.s - 2 * t.pad), t.text1, t.text)
        inner.cut_left(t.big)
        paint.text_in(kind_label(item, info), inner.cut_left(kind_w), color, t.text)
        inner.cut_left(t.big)
        if searching:
            paint.text_in(item["folder"], inner.cut_left(rest * (0.75 if with_pak else 1.0)), t.text2, t.text)
            inner.cut_left(t.big)
        if with_pak:
            paint.text_in(item.get("container") or "", inner, t.text3, t.text)
    if layout.max_scroll > 0:
        h = layout.height
        bar = max(t.big * 2, h * h / (h + layout.max_scroll))
        y = state["y0"] + (h - bar) * (1 - layout.scroll / layout.max_scroll)
        paint.rect(Rect(layout.x0 + layout.width - 3 * t.s, y, 2 * t.s, bar), t.text3[:3] + (0.35,), t.s)


def hover(context, x, y):
    """The row under (x, y) (x None: the mouse left the well) highlighted; True when that changed."""
    state = _rows(context)
    i = row_at(state, x, y) if state and x is not None else None
    key = context.area.as_pointer()
    if i == FILES["hover"].get(key):
        return False
    FILES["hover"][key] = i
    return True


# ------------------------------------------------------------------ the list column: folders

def _go(path):
    def run(context):
        wm = context.window_manager
        wm.fp_files_folder, wm.fp_files_query = path, ""
        FILES["folder"] = None          # its old subfolders aren't this folder's
        for state in FILES["rows"].values():
            state["scroll"] = 0.0
    return run


def side(ui, r):
    t, folder = ui.t, bpy.context.window_manager.fp_files_folder
    parent = folder.rsplit("/", 1)[0] if "/" in folder else None
    cell = r.cut_top(t.row_pitch)
    cell = Rect(cell.x, cell.y + (t.row_pitch - t.row) / 2, cell.w, t.row)
    if parent:
        ui.row("files:up", cell, folder.rsplit("/", 1)[-1], "mdi_ArrowUp", action=_go(parent),
               tip="Up to " + parent.rsplit("/", 1)[-1])
    else:       # the root: nowhere to go up to
        inner = cell.pad(t.pad, 0)
        ui.icon(inner.cut_left(t.icon), "mdi_ArrowUp", t.text3)
        inner.cut_left(t.pad)
        ui.label(inner, folder, t.text2)
    got = FILES["folder"]
    if got is None:
        text = FILES["folder_error"] or ("Loading..." if client().state == "ready" else "Waiting for FP...")
        ui.label(r.cut_top(t.row_pitch).pad(t.pad, 0), text, t.text3)
        return
    subs = got.get("folders") or []
    offset = ui.scroll("files:folders:" + folder, r, len(subs) * t.row_pitch)     # per folder: going back keeps it
    y = r.top + offset
    for sub in subs:
        y -= t.row_pitch
        if y > r.top or y + t.row_pitch < r.y:
            continue
        cell = Rect(r.x, y + (t.row_pitch - t.row) / 2, r.w, t.row)
        ui.row("folder:" + sub["path"], cell, sub["name"], "mdi_Folder", action=_go(sub["path"]))
    ui.end_scroll()


# ------------------------------------------------------------------ the info column: the picked file

def info(ui, r):
    t = ui.t
    if not FILES["selected"]:
        return
    _export_row(ui, r.cut_bottom(t.action))         # the cards' width: one right edge down the column
    r.cut_bottom(t.pad)
    _card(ui, r, FILES["selected"][-1])


def _card(ui, r, path):
    t = ui.t
    got, many = FILES["info"].get(path), len(FILES["selected"])
    folder = path.rsplit("/", 1)[0]
    if got == "asked":
        facts = "Reading..."
    elif got:
        facts = got["class"] if got["kind"] == "Other" else "%s  ·  %s" % (got["kind"], got["class"])
    else:
        facts = ""
    text_h = t.title + 2 * t.gap + t.row + (t.text + 2 * t.gap if facts else 0) + (t.caption + 2 * t.gap if many > 1 else 0)
    tex, pic_w, pic_h = picture(path), 0, 0
    if tex is not None:
        w, h = textures.image("files:" + path).size
        pic_w = r.w - 2 * t.pad
        pic_h = min(pic_w * h / max(w, 1), r.h - text_h - 3 * t.pad)
        pic_w = min(pic_w, pic_h * w / max(h, 1))
        if pic_h < t.big * 2:       # a short area: the facts matter more
            tex = None
    card = r.cut_top(text_h + 2 * t.pad + (pic_h + t.pad if tex is not None else 0))
    ui.panel(card)
    inner = card.pad(t.pad)
    if tex is not None:
        box = inner.cut_top(pic_h)
        paint.rect(Rect(box.x + (box.w - pic_w) / 2, box.y, pic_w, pic_h), (0, 0, 0, 0), t.r_card - t.pad, image=tex)
        inner.cut_top(t.pad)
    name = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    paint.text_in(name, inner.cut_top(t.title + 2 * t.gap), t.text1, t.title)
    if facts:
        paint.text_in(facts, inner.cut_top(t.text + 2 * t.gap), t.text2, t.text)
    room = inner.w - 2 * t.pad - t.icon - t.pad
    ui.row("info:folder", inner.cut_top(t.row), _tail(folder, t.text, room), "mdi_Folder", action=_go(folder),
           tip=folder)
    if many > 1:
        paint.text_in("%d files picked" % many, inner.cut_top(t.caption + 2 * t.gap), t.text3, t.caption)


def _tail(text, size, width):
    """The text cut at the front with an ellipsis to fit `width` (a path's last folders say the most)."""
    if paint.measure(text, size) <= width:
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi) // 2
        if paint.measure("…" + text[mid:], size) <= width:
            hi = mid
        else:
            lo = mid + 1
    return "…" + text[lo:]


def _export_row(ui, r):
    t = ui.t
    for id, icon, op, tip in (("info:copy", "mdi_ContentCopy", "files_copy_path", "Copy Path"),
                              ("info:properties", "mdi_CodeJson", "files_properties", "Properties (as text)")):
        ui.button(id, r.cut_right(r.h), icon=icon, action=_op(op), tip=tip)
        r.cut_right(t.gap)
    picked = FILES["selected"][-1] if FILES["selected"] else None
    info = FILES["info"].get(picked)
    if isinstance(info, dict) and info["kind"] == "Material":
        ui.button("info:rebuild", r.cut_right(r.h), icon="mdi_Brush", action=_rebuild_on(_object_path(picked)),
                  tip="Rebuild On: the Materials tab's ticked materials on this material's shader, keeping their values")
        r.cut_right(t.gap)
    import_options.button(ui, r.cut_right(r.h), "info:import_options", "files")
    r.cut_right(t.gap)
    busy = STATE["busy"]
    ui.button("info:import", r, busy or import_options.import_label("Import", "files"), "mdi_Download", _op("files_import"),
              "Import into this scene (or double-click the row)", enabled=not busy, style="accent")


def _rebuild_on(path):
    def run(context):
        from . import logbook, mat_tab
        mats = mat_tab.ticked(context)
        if not mats:
            logbook.write("Tick materials in the Materials tab first", "warning")
            return
        mat_tab.rebuild_on(mats, path)(context)
    return run


def _op(name):
    def run(context):
        getattr(bpy.ops.fp, name)()
    return run


# ------------------------------------------------------------------ input over the well (the editor's "Fortnite" keymap)

def _rows(context):
    if not tabs.is_tab(context, "files"):
        return None
    return FILES["rows"].get(context.area.as_pointer())


class FP_OT_files_click(bpy.types.Operator):
    bl_idname = "fp.files_click"
    bl_label = "Pick File"
    bl_options = {'INTERNAL'}
    extend: bpy.props.BoolProperty(options={'SKIP_SAVE'})     # SKIP_SAVE: a plain click must not reuse ctrl's
    range: bpy.props.BoolProperty(options={'SKIP_SAVE'})

    def invoke(self, context, event):
        state = _rows(context)
        i = row_at(state, event.mouse_region_x, event.mouse_region_y) if state else None
        if i is None:
            return {'PASS_THROUGH'}
        items, path = state["items"], state["items"][i]["path"]
        if self.range and FILES["anchor"] is not None:
            a = next((n for n, it in enumerate(items) if it["path"] == FILES["anchor"]), i)
            FILES["selected"] = [it["path"] for it in items[min(a, i):max(a, i) + 1]]
        elif self.extend:
            sel = FILES["selected"]
            if path in sel:
                sel.remove(path)
            else:
                sel.append(path)
            FILES["anchor"] = path
        else:
            FILES["selected"], FILES["anchor"] = [path], path
        tag_redraw()
        return {'FINISHED'}


class FP_OT_files_open(bpy.types.Operator):
    bl_idname = "fp.files_open"
    bl_label = "Import File"
    bl_options = {'INTERNAL'}

    def invoke(self, context, event):
        state = _rows(context)
        i = row_at(state, event.mouse_region_x, event.mouse_region_y) if state else None
        if i is None:
            return {'PASS_THROUGH'}
        FILES["selected"], FILES["anchor"] = [state["items"][i]["path"]], state["items"][i]["path"]
        _import(FILES["selected"])
        return {'FINISHED'}


class FP_OT_files_scroll(bpy.types.Operator):
    bl_idname = "fp.files_scroll"
    bl_label = "Scroll"
    bl_options = {'INTERNAL'}
    rows: bpy.props.FloatProperty(default=3)

    def invoke(self, context, event):
        state = _rows(context)
        if state is None:
            return {'PASS_THROUGH'}
        layout = state["layout"]
        step = layout.height if abs(self.rows) >= 10 else layout.row_h * self.rows
        state["scroll"] = min(max(0.0, layout.scroll + (step if self.rows > 0 else -abs(step))), layout.max_scroll)
        context.region.tag_redraw()
        return {'FINISHED'}


class FP_OT_files_import(bpy.types.Operator):
    """Import the picked files (FP exports them with your Blender export settings and import options, Blender imports them here)"""
    bl_idname = "fp.files_import"
    bl_label = "Import"

    @classmethod
    def poll(cls, context):
        return bool(FILES["selected"]) and not STATE["busy"]

    def execute(self, context):
        _import(list(FILES["selected"]))
        return {'FINISHED'}


def _import(paths):
    """Import these files; does nothing while another import runs (the header shows that one)."""
    label = paths[0].rsplit("/", 1)[-1].rsplit(".", 1)[0] + (" +%d" % (len(paths) - 1) if len(paths) > 1 else "")
    return importing.run("files-export", label, paths=";".join(paths), settings=import_options.query(opened="files"))


class FP_OT_files_properties(bpy.types.Operator):
    """The picked file's properties as JSON, in a Text datablock (open it in a Text Editor)"""
    bl_idname = "fp.files_properties"
    bl_label = "Properties"

    @classmethod
    def poll(cls, context):
        return bool(FILES["selected"])

    def execute(self, context):
        path = FILES["selected"][-1]
        name = path.rsplit("/", 1)[-1].rsplit(".", 1)[0] + ".json"

        def done(result):
            if isinstance(result, Exception):
                STATE["error"] = "Properties: %s" % result
                return
            text = bpy.data.texts.get(name) or bpy.data.texts.new(name)
            text.clear()
            text.write(result["json"])
            for window in bpy.context.window_manager.windows:
                for area in window.screen.areas:
                    if area.type == 'TEXT_EDITOR':
                        area.spaces.active.text = text
            tag_redraw()
        client().request("files-properties", done, path=path)
        return {'FINISHED'}


class FP_OT_files_copy_path(bpy.types.Operator):
    """Copy the picked file's path"""
    bl_idname = "fp.files_copy_path"
    bl_label = "Copy Path"

    @classmethod
    def poll(cls, context):
        return bool(FILES["selected"])

    def execute(self, context):
        context.window_manager.clipboard = FILES["selected"][-1]
        return {'FINISHED'}


# ------------------------------------------------------------------ header

def _open_folder(path):
    def run(context):
        _go(path)(context)
        tag_redraw()
    return run


def folder_items(folder, listing):
    """The folder pop-up's rows: Up (naming where it goes), then `listing`'s subfolders (None: still listing)."""
    items = []
    if "/" in folder:
        parent = folder.rsplit("/", 1)[0]
        items += [{"label": "Up", "icon": "mdi_ArrowUp", "detail": parent.rsplit("/", 1)[-1],
                   "action": _open_folder(parent), "id": "up"}, {"sep": True}]
    if listing is None:
        return items + [{"label": "Loading folders...", "id": "loading"}]
    for sub in listing.get("folders") or []:
        items.append({"label": sub["name"], "icon": "mdi_Folder", "action": _open_folder(sub["path"]), "id": sub["path"]})
    return items


def open_folders(context, anchor, side="below"):
    """Open a folder: up, or one in this folder (type to search)"""
    from .ui import popups
    wm = context.window_manager
    return popups.open_list(context, anchor, lambda: folder_items(wm.fp_files_folder, FILES["folder"]), "files:folders",
                            "Search folders", side=side)


def header(layout, context):
    wm = context.window_manager
    folded = ub.folded(context, tabs.current(context.space_data))
    if "side" in folded:
        row = layout.row()
        row.ui_units_x = 6
        name = wm.fp_files_folder.rsplit("/", 1)[-1]
        row.operator("fp.popup_open", text=name, icon='FILE_FOLDER').name = "files:folders"
    if "info" in folded and FILES["selected"]:
        layout.operator(FP_OT_files_import.bl_idname, text="Import", icon='IMPORT')
    row = layout.row(align=True)
    row.ui_units_x = 8 if folded else 14
    row.prop(wm, "fp_files_query", text="", icon='VIEWZOOM')
    layout.prop(wm, "fp_files_kind", text="")
    if wm.fp_files_query.strip() and FILES["search"].result:
        total, count = FILES["search"].result.get("total", 0), len(FILES["search"].result.get("items") or [])
        layout.label(text=("%d" % total) if total <= count else "first %d of %d" % (count, total))


classes = (FP_OT_files_click, FP_OT_files_open, FP_OT_files_scroll, FP_OT_files_import, FP_OT_files_properties,
           FP_OT_files_copy_path)
_keymaps = []


def _query_changed(self, context):
    FILES["search"].ask(self.fp_files_query.strip(), self.fp_files_kind, time.time())
    tag_redraw()


def register():
    wm = bpy.types.WindowManager
    wm.fp_files_query = bpy.props.StringProperty(name="Search Files", options={'TEXTEDIT_UPDATE'}, update=_query_changed,
                                                 description="Every word must be in the file's path")
    wm.fp_files_kind = bpy.props.EnumProperty(name="Type", items=[('ALL', "All Types", "")] + [(k, k, "") for k in KINDS],
                                              update=_query_changed)
    wm.fp_files_folder = bpy.props.StringProperty(default="FortniteGame")
    for c in classes:
        bpy.utils.register_class(c)
    ub.OPENERS["files:folders"] = open_folders
    tabs.register_tab(tabs.Tab("files", "Files", 'FILE_FOLDER', header=header, draw=draw, tick=tick,
                               nav_icon="mdi_Folder", side=side, info=info, hover=hover,
                               has_info=lambda context: bool(FILES["selected"])))
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc is None:
        return
    add = host.adder(kc, _keymaps)
    add(FP_OT_files_click.bl_idname, 'LEFTMOUSE', 'PRESS')
    add(FP_OT_files_open.bl_idname, 'LEFTMOUSE', 'DOUBLE_CLICK')
    add(FP_OT_files_click.bl_idname, 'LEFTMOUSE', 'PRESS', ctrl=True).properties.extend = True
    add(FP_OT_files_click.bl_idname, 'LEFTMOUSE', 'PRESS', shift=True).properties.range = True
    for key, n in (('WHEELDOWNMOUSE', 3), ('WHEELUPMOUSE', -3), ('PAGE_DOWN', 10), ('PAGE_UP', -10)):
        add(FP_OT_files_scroll.bl_idname, key, 'PRESS').properties.rows = n


def unregister():
    for km, k in _keymaps:
        try:
            km.keymap_items.remove(k)
        except (ReferenceError, RuntimeError):
            pass
    _keymaps.clear()
    tabs.unregister_tab("files")
    ub.OPENERS.pop("files:folders", None)
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
    for p in ("fp_files_query", "fp_files_kind", "fp_files_folder"):
        delattr(bpy.types.WindowManager, p)
