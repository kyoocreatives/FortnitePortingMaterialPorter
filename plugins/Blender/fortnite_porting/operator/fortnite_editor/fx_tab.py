"""The editor's Effects tab: the scene's particle effects in the list column (grouped by what holds them), the picked
one looping in the live preview, where Move, Rotate and Scale drag it (trails follow the motion once replayed); the
info column mutes or solos its emitters, opens their materials in the Materials tab and edits its user parameters
by their game names, on the preview or on every instance in the scene."""
import bpy

from . import fx_play, host, lib_list, mat_tab, preview, tabs
from .ui import fields, paint
from .ui import blender as ui_blender
from .ui.rect import Rect

ID = "effects"
BACKGROUNDS = (("dark", "Dark", (0.05, 0.05, 0.055)), ("grey", "Grey", (0.22, 0.22, 0.23)),
               ("light", "Light", (0.75, 0.75, 0.76)))
STATE = {"background": "dark", "everywhere": False, "folds": {}, "source": None, "lists": {}}


def _fold(key):
    def run(context):
        STATE["folds"][key] = not STATE["folds"].get(key, True)
    return run


def _pick(root):
    def run(context):
        if context.mode != 'OBJECT':
            fx_play.STATE["error"] = "Previews play in Object Mode"
            return
        fx_play.show(root)
    return run


SOURCES = (("scene", "In Scene"), ("game", "Game Effects"))


def _list_state(source):
    return STATE["lists"].setdefault(source, {"sort": "name", "tags": set()})


def _list_changed(context):
    ui_blender.store(context.area).scroll["fx:list"] = 0.0


def _set_source(value):
    def run(context):
        STATE["source"] = value
        _list_changed(context)
    return run


def _pick_game(item):
    def run(context):
        if context.mode != 'OBJECT':
            fx_play.STATE["error"] = "Previews play in Object Mode"
            return
        fx_play.show_game(item)
    return run


def side(ui, r):
    t = ui.t
    if STATE["source"] is None:     # the scene's effects when it has some, the game's otherwise
        STATE["source"] = "scene" if fx_play.roots() else "game"
    row = r.cut_top(t.row)
    r.cut_top(t.gap)
    fields.dropdown(ui, "fx:source", row, dict(SOURCES)[STATE["source"]],
                    _menu("fx:source", SOURCES, STATE["source"], _set_source),
                    tip="The scene's effects, or the game's (preview them, then import)")
    search = bpy.context.window_manager.fp_fx_search
    if STATE["source"] == "game":
        _game_list(ui, r, search)
    else:
        _scene_list(ui, r, search)


def _game_list(ui, r, search):
    t = ui.t
    state = _list_state("game")
    row = r.cut_top(t.row)
    r.cut_top(t.pad)
    lib_list.controls(ui, row, "fx", ("name", "folder"), lib_list.tag_titles(lib_list.LISTS.get("Effect")), state,
                      _list_changed)
    items = lib_list.listing("Effect")
    found = lib_list.apply("fx:game", [("Effect", it) for it in items] if items else [], search, state) if items else []
    if not found:
        paint.text_in(lib_list.status("Effect"), r.cut_top(t.row).pad(t.pad, 0), t.text3, t.text)
        return
    shown = fx_play.STATE.get("game")
    offset = ui.scroll("fx:list", r, len(found) * t.row_pitch)
    first = max(0, int(offset // t.row_pitch))
    for i in range(first, min(len(found), first + int(r.h // t.row_pitch) + 2)):
        item = found[i][1]
        y = r.top + offset - (i + 1) * t.row_pitch
        cell = Rect(r.x, y + (t.row_pitch - t.row) / 2, r.w - ui.bar_room, t.row)
        ui.row("fx:game:%d" % i, cell, item["name"], "Effect", selected=shown is not None and shown["path"] == item["path"],
               action=_pick_game(item), tip=item.get("description") or item["path"])
    ui.end_scroll()


def _scene_list(ui, r, search):
    t = ui.t
    r.cut_top(t.pad)
    words = search.lower().split()
    groups = [(o, [x for x in rs if all(w in x.name.lower() for w in words)]) for o, rs in fx_play.roots()]
    groups = [(o, rs) for o, rs in groups if rs]
    if not groups:
        text = "Nothing matches" if words else "No replayable effects in this scene: Game Effects has the game's"
        paint.text_in(text, r.cut_top(t.row).pad(t.pad, 0), t.text3, t.text)
        return
    rows = []
    for owner, rs in groups:
        rows.append(("group", owner, rs))
        if STATE["folds"].get(owner, True):
            rows += [("fx", owner, root) for root in rs]
    offset = ui.scroll("fx:list", r, len(rows) * t.row_pitch)
    for i, (kind, owner, payload) in enumerate(rows):
        y = r.top + offset - (i + 1) * t.row_pitch
        if y > r.top or y + t.row_pitch < r.y:
            continue
        cell = Rect(r.x, y + (t.row_pitch - t.row) / 2, r.w - ui.bar_room, t.row)
        if kind == "group":
            ui.group("fx:g:" + owner, cell, "%s  (%d)" % (owner, len(payload)), STATE["folds"].get(owner, True),
                     _fold(owner))
        else:
            ui.row("fx:item:%d" % i, cell, payload.name, "Effect", selected=payload.name == fx_play.STATE["root"],
                   action=_pick(payload), depth=1, tip=payload.name)
    ui.end_scroll()


# ------------------------------------------------------------------ the well

def _set_tool(id):
    def run(context):
        fx_play.TOOL["id"] = id
    return run


def _toggle_play(context):
    preview.PLAY["on"] = not preview.PLAY["on"]
    preview.PLAY["last"] = 0.0


def _bar(ui, r):
    t = ui.t
    for id, label in fx_play.TOOLS:
        cell = r.cut_left(paint.measure(label, t.text) + 2 * t.pad + t.pad)
        tip = {"view": "Drag orbits, Shift+drag pans, wheel zooms",
               "move": "Drag moves the effect: paused, places it; playing, records the motion (trails follow)",
               "rotate": "Drag turns it about Z (paused: places, playing: records)",
               "scale": "Drag scales it (paused: places, playing: records)"}[id]
        ui.button("fx:tool:" + id, cell, label, action=_set_tool(id), selected=fx_play.TOOL["id"] == id, tip=tip)
        r.cut_left(t.gap)
    r.cut_left(t.pad)
    play = r.cut_left(t.row * 2.6)
    ui.button("fx:play", play, "Pause" if preview.PLAY["on"] else "Play", action=_toggle_play)
    r.cut_left(t.pad)
    reset = r.cut_right(paint.measure("Reset Motion", t.text) + 3 * t.pad)
    ui.button("fx:reset", reset, "Reset Motion", action=lambda context: fx_play.reset_motion(),
              enabled=fx_play.carrier() is not None and fx_play.carrier().animation_data is not None,
              tip="The effect back where it started, its recorded motion dropped")
    r.cut_right(t.pad)
    p = preview.PLAY
    paint.text_in("%d / %d" % (int(p["frame"]), p["end"]), r, t.text2, t.caption, "right")


def view(ui, well):
    t = ui.t
    inner = well.pad(t.pad)
    bar = inner.cut_bottom(t.row)
    inner.cut_bottom(t.pad)
    v = preview.view_for(ID)
    v.engine = "EEVEE"      # the effects' materials need it
    tool = fx_play.tool if fx_play.TOOL["id"] != "view" else None
    preview.draw(ui, inner, ID, "Pick an effect to play it here", tool=tool)
    note = fx_play.STATE["error"] or fx_play.STATE["busy"]
    if note:
        line = Rect(inner.x + t.big, inner.top - t.big - t.row, inner.w - 2 * t.big, t.row)
        paint.text_in(paint.fit(note, t.text, line.w), line,
                      (0.95, 0.55, 0.45, 1) if fx_play.STATE["error"] else t.text2, t.text)
    if fx_play.STATE["copy"]:
        _bar(ui, bar)


# ------------------------------------------------------------------ the info column

def _menu(id, choices, current, pick):
    def run(context):
        from .ui import popups
        frame = ui_blender.FRAMES.get(context.area.as_pointer())
        hit = frame.find(id) if frame is not None else None
        items = [{"label": label, "check": value == current, "action": pick(value), "id": value}
                 for value, label in choices]
        popups.open_menu(context, hit.rect if hit is not None else Rect(0, 0, 0, 0), items, id, search=False)
    return run


def _set_background(value):
    def run(context):
        STATE["background"] = value
        fx_play.set_background(next(c for k, _l, c in BACKGROUNDS if k == value))
    return run


def _open_material(mat):
    def run(context):
        mat_tab.STATE.update(scope="FILE", ticked={mat.name})
        host.set_tab(context.space_data, mat_tab.ID)
    return run


def _speed(context, value):
    preview.PLAY["speed"] = value
    preview.PLAY["last"] = 0.0


def _param(name):
    def run(context, value):
        fx_play.set_parameter(name, value, STATE["everywhere"])
    return run


class FP_OT_effect_freeze(bpy.types.Operator):
    """Hold replayed effects on the scene's current frame, for stills: moving the timeline no longer changes them
    (run again to let them play)"""
    bl_idname = "fp.effect_freeze"
    bl_label = "Freeze Effects at Frame"
    bl_options = {'REGISTER', 'UNDO'}

    every: bpy.props.BoolProperty(name="Every Effect", description="All the scene's replayed effects, not only the "
                                  "selected ones")
    root: bpy.props.StringProperty(options={'HIDDEN'})

    def execute(self, context):
        from ...material_porter.effect_replay import freeze, roots as picked_roots
        scene = context.scene
        if self.root:
            rs = [bpy.data.objects[self.root]] if self.root in bpy.data.objects else []
        elif self.every:
            rs = [r for _o, group in fx_play.roots(scene) for r in group]
        else:
            rs = picked_roots(context.selected_objects)
        if not rs:
            self.report({'INFO'}, "No replayed effect to freeze")
            return {'CANCELLED'}
        thaw = all(freeze.frozen(r, scene) is not None for r in rs)
        for r in rs:
            if thaw:
                freeze.thaw(r, scene)
            else:
                freeze.freeze(r, scene, scene.frame_current)
        self.report({'INFO'}, "%d effect%s %s" % (len(rs), "" if len(rs) == 1 else "s",
                                                 "play again" if thaw else "held on frame %d" % scene.frame_current))
        return {'FINISHED'}


def _freeze(root_name, every=False):
    def run(context):
        bpy.ops.fp.effect_freeze(root=root_name or "", every=every)
    return run


def info(ui, r):
    t = ui.t
    root, game = fx_play.shown_root(), fx_play.STATE.get("game")
    if root is None or fx_play.copy_root() is None:
        paint.text_in("Click an effect in the list", r.cut_top(t.row), t.text3, t.caption)
        return
    label_w = 72 * t.s
    name = game["name"] if game is not None else root.name
    paint.text_in(paint.fit(name, t.title, r.w), r.cut_top(t.title + 2 * t.gap), t.text1, t.title)
    took = "replayed in %.1f s" % fx_play.STATE["baked_s"]
    paint.text_in(took, r.cut_top(t.caption + 2 * t.gap), t.text3, t.caption)
    r.cut_top(t.pad)
    if game is not None:
        imp = r.cut_bottom(t.action)
        ui.button("fx:import", imp, "Import into Scene", style="accent", action=_import(game),
                  tip="The effect into the scene the usual way (it plays over the scene's frames)")
        r.cut_bottom(t.pad)
    else:
        _freeze_row(ui, r.cut_top(t.row), root)
    r.cut_top(t.pad)
    row = r.cut_top(t.row)
    paint.text_in("Background", row.cut_left(label_w), t.text2, t.text)
    fields.dropdown(ui, "fx:bg", row, dict((k, l) for k, l, _c in BACKGROUNDS)[STATE["background"]],
                    _menu("fx:bg", [(k, l) for k, l, _c in BACKGROUNDS], STATE["background"], _set_background))
    r.cut_top(t.gap)
    row = r.cut_top(t.row)
    paint.text_in("Time Scale", row.cut_left(label_w), t.text2, t.text)
    fields.number(ui, "fx:speed", row, preview.PLAY["speed"], _speed, lo=0.05, hi=4.0, step=0.05, fmt="%.2f")
    _emitters_and_params(ui, r, root, game, label_w)


def _freeze_row(ui, row, root):
    t = ui.t
    from ...material_porter.effect_replay import freeze
    scene = bpy.context.scene
    held = freeze.frozen(root, scene)
    half = row.cut_left((row.w - t.gap) / 2)
    row.cut_left(t.gap)
    ui.button("fx:freeze", half, "Unfreeze (frame %d)" % held if held is not None else
              "Freeze at Frame %d" % scene.frame_current, action=_freeze(root.name),
              selected=held is not None, tip="In the scene: hold this effect on the current frame for stills (the "
              "timeline no longer moves it); again: it plays")
    ui.button("fx:freeze_all", row, "Freeze All", action=_freeze("", True),
              tip="Every replayed effect in the scene held on the current frame (again: they play)")


def _import(item):
    def run(context):
        from . import importing
        importing.start("Effect", item, {})
    return run


def _emitters_and_params(ui, r, root, game, label_w):
    t = ui.t
    r.cut_top(t.big)
    paint.text_in("Emitters", r.cut_top(t.row), t.text3, t.caption)
    for name, mats in fx_play.emitters(root):
        row = r.cut_top(t.row)
        r.cut_top(t.gap)
        muted, solo = name in fx_play.STATE["muted"], fx_play.STATE["solo"] == name
        ui.button("fx:mute:" + name, row.cut_left(t.row), "", "mdi_EyeOff" if muted else "mdi_Eye",
                  action=lambda context, n=name: fx_play.toggle_mute(n), style="flat",
                  tip="Show" if muted else "Hide this emitter in the preview")
        ui.button("fx:solo:" + name, row.cut_left(t.row), "S", action=lambda context, n=name: fx_play.toggle_solo(n),
                  style="flat", selected=solo, tip="Only this emitter")
        row.cut_left(t.gap)
        if mats:
            ui.row("fx:mat:" + name, row, paint.fit(name, t.text, row.w - t.pad), detail=mats[0].name,
                   action=_open_material(mats[0]), tip="%s: open %s in the Materials tab" % (name, mats[0].name))
        else:
            paint.text_in(paint.fit(name, t.text, row.w), row, t.text2, t.text)
    params = fx_play.parameters(fx_play.copy_root())
    if params:
        r.cut_top(t.big)
        head = r.cut_top(t.row)
        if game is None:
            count = len(fx_play.instances(root))
            fields.checkbox(ui, "fx:everywhere", head, STATE["everywhere"], "Parameters: all %d in scene" % count,
                            lambda context: STATE.update(everywhere=not STATE["everywhere"]),
                            tip="Edits go to every instance of this effect in the scene (replayed there), not just here")
        else:
            paint.text_in("Parameters", head, t.text3, t.caption)
        for name, value in params:
            row = r.cut_top(t.row)
            r.cut_top(t.gap)
            if row.y < r.y:
                break
            paint.text_in(paint.fit(name.split(".", 1)[1], t.text, label_w * 1.4), row.cut_left(label_w * 1.5),
                          t.text2, t.text)
            fields.number(ui, "fx:p:" + name, row, value, _param(name), integer=isinstance(value, int))


# ------------------------------------------------------------------ the timer

def tick():
    """A drag that moved the effect while it played, or edited a parameter: replayed once the press ends."""
    if not fx_play.STATE["moved"] and not fx_play.STATE.get("pending"):
        return
    area = next((a for a in host.editor_areas() if host.tab_id(a.spaces.active) == ID), None)
    if area is not None and ui_blender.store(area).pressed is None:
        if fx_play.STATE.get("pending"):
            fx_play.flush()
        else:
            fx_play.bake()
        fx_play.STATE["moved"] = False


def header(layout, context):
    row = layout.row(align=True)
    row.ui_units_x = 10
    row.prop(context.window_manager, "fp_fx_search", text="", icon='VIEWZOOM')


def register():
    bpy.types.WindowManager.fp_fx_search = bpy.props.StringProperty(
        name="Search", description="Effects whose name has these words", options={'TEXTEDIT_UPDATE'},
        update=lambda self, context: preview.redraw())
    bpy.utils.register_class(FP_OT_effect_freeze)
    tabs.register_tab(tabs.Tab(ID, "Effects", 'PARTICLES', header=header, nav_icon="Effect", group="tools",
                               tick=tick, side=side, view=view, info=info))


def unregister():
    tabs.unregister_tab(ID)
    bpy.utils.unregister_class(FP_OT_effect_freeze)
    del bpy.types.WindowManager.fp_fx_search
