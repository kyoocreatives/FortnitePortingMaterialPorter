"""The editor's Animation tab: a library of animations (the file's actions, the game's emotes, idles and locomotion)
in the list column, each played on a body in the live preview when clicked; the info column picks the
body, how it plays, and applies exactly what plays to the user's character (body, face, props, sound: NLA strips)."""
import bpy

from . import anim_hand, anim_lib, anim_play, host, preview, tabs
from .ui import fields, paint
from .ui import blender as ui_blender
from .ui.rect import Rect

ID = "animation"
STATE = {"source": "emotes", "picked": None, "lists": {}}      # lists: source -> {"sort", "tags"}


def redraw_all():
    preview.redraw()


def _key(kind, item):
    return (kind, item.get("path") or item.get("action"))


# ------------------------------------------------------------------ the list column

def _menu(id, choices, current, pick):
    def run(context):
        from .ui import popups
        frame = ui_blender.FRAMES.get(context.area.as_pointer())
        hit = frame.find(id) if frame is not None else None
        items = [{"label": label, "check": value == current, "action": pick(value), "id": value}
                 for value, label in (choices() if callable(choices) else choices)]
        popups.open_menu(context, hit.rect if hit is not None else Rect(0, 0, 0, 0), items, id, search=False)
    return run


def _list_state(source):
    return STATE["lists"].setdefault(source, {"sort": "name", "tags": set()})


def _list_changed(context):
    ui_blender.store(context.area).scroll["anim:list"] = 0.0
    redraw_all()


def _set_source(value):
    def run(context):
        STATE["source"] = value
        ui_blender.store(context.area).scroll["anim:list"] = 0.0
        redraw_all()
    return run


def _play(kind, item):
    def run(context):
        STATE["picked"] = _key(kind, item)
        if not anim_play.can_preview(context):
            anim_play.STATE["error"] = "Previews play in Object Mode"
            return
        anim_play.play(kind, item)
    return run


def side(ui, r):
    t = ui.t
    choices = [(s[0], s[1]) for s in anim_lib.SOURCES]
    row = r.cut_top(t.row)
    r.cut_top(t.pad)
    fields.dropdown(ui, "anim:source", row, anim_lib.source(STATE["source"])[1],
                    _menu("anim:source", choices, STATE["source"], _set_source), tip="What the list shows")
    src = STATE["source"]
    state = _list_state(src)
    if state["sort"] not in anim_lib.sorts(src):
        state["sort"] = "name"
    row = r.cut_top(t.row)
    r.cut_top(t.pad)
    from . import lib_list
    lib_list.controls(ui, row, "anim", anim_lib.sorts(src), anim_lib.tags(src), state, _list_changed)
    found = lib_list.apply("anim:" + src, anim_lib.items(src), bpy.context.window_manager.fp_anim_search, state)
    if not found:
        paint.text_in(anim_lib.status(STATE["source"]), r.cut_top(t.row).pad(t.pad, 0), t.text3, t.text)
        return
    offset = ui.scroll("anim:list", r, len(found) * t.row_pitch)
    first = max(0, int(offset // t.row_pitch))
    for i in range(first, min(len(found), first + int(r.h // t.row_pitch) + 2)):
        kind, item = found[i]
        y = r.top + offset - (i + 1) * t.row_pitch
        cell = Rect(r.x, y + (t.row_pitch - t.row) / 2, r.w - ui.bar_room, t.row)
        key = _key(kind, item)
        ui.row("anim:item:%d" % i, cell, item["name"].strip(), "Emote" if kind == "Emote" else "Animation",
               selected=key == STATE["picked"], action=_play(kind, item), detail=item.get("detail", ""),
               tip=item.get("description") or item["name"])
    STATE["rows"] = found
    ui.end_scroll()


# ------------------------------------------------------------------ the well: preview and transport

def _toggle_play(context):
    preview.PLAY["on"] = not preview.PLAY["on"]
    preview.PLAY["last"] = 0.0


def _seek(bar, store):
    def at(x):
        p = preview.PLAY
        f = p["start"] + min(max((x - bar.x) / max(bar.w, 1), 0.0), 1.0) * (p["end"] - p["start"])
        p.update(frame=f, on=False)
        preview.redraw()

    def drag(context, dx, dy):
        at(store.press_at[0] + dx)

    def click(context):
        at(store.press_at[0])
    return drag, click


def _transport(ui, r):
    t = ui.t
    p = preview.PLAY
    if anim_play.STATE["item"] is None:
        return
    play = r.cut_left(t.row * 2.6)
    ui.button("anim:play", play, "Pause" if p["on"] else "Play", action=_toggle_play,
              tip="Space bar plays the 3D views: this plays the preview")
    r.cut_left(t.pad)
    label = "%d / %d" % (int(p["frame"]), p["end"])
    text = r.cut_right(paint.measure("0000 / 0000", t.caption) + t.pad)
    paint.text_in(label, text, t.text2, t.caption, "right")
    r.cut_right(t.pad)
    bar = Rect(r.x, r.y + r.h / 2 - 3 * t.s, r.w, 6 * t.s)
    paint.rect(bar, t.control, 3 * t.s)
    span = max(p["end"] - p["start"], 1)
    done = (p["frame"] - p["start"]) / span
    knob_x = bar.x + bar.w * min(max(done, 0.0), 1.0)
    paint.rect(Rect(bar.x, bar.y, max(knob_x - bar.x, bar.h), bar.h), t.accent, 3 * t.s)
    knob = 12 * t.s
    paint.rect(Rect(knob_x - knob / 2, r.y + (r.h - knob) / 2, knob, knob), t.text1, knob / 2)
    drag, click = _seek(bar, ui.store)
    from .ui.frame import Hit
    ui.frame.add(Hit("anim:scrub", r, ui.clip, "button", click, None, "Drag to scrub", None, drag))


def view(ui, well):
    t = ui.t
    inner = well.pad(t.pad)
    bar = inner.cut_bottom(t.row)
    inner.cut_bottom(t.pad)
    v = preview.view_for(ID)
    v.engine = "EEVEE" if STATE.get("materials") else "WORKBENCH"     # Workbench: light, no noisy shadows
    preview.draw(ui, inner, ID, "Pick an animation to play it here", shows=anim_play.armature() is not None)
    note = anim_play.STATE["error"] or anim_play.STATE["busy"]
    if note:
        line = Rect(inner.x + t.big, inner.top - t.big - t.row, inner.w - 2 * t.big, t.row)
        paint.text_in(paint.fit(note, t.text, line.w), line,
                      (0.95, 0.55, 0.45, 1) if anim_play.STATE["error"] else t.text2, t.text)
    _transport(ui, bar)


# ------------------------------------------------------------------ the info column

def _set_body(value):
    def run(context):
        anim_play.set_body(value)
    return run


def _toggle_materials(context):
    STATE["materials"] = not STATE.get("materials")


def _set_hand(value):
    def run(context):
        anim_hand.STATE["item"] = value or None
        anim_hand.show(anim_play.armature())
        preview.redraw()
    return run


def _set_loop(context):
    preview.PLAY["loop"] = not preview.PLAY["loop"]


def _set_speed(context, value):
    preview.PLAY["speed"] = value
    preview.PLAY["last"] = 0.0


def _apply(context):
    message = anim_play.apply(context)
    if message:
        anim_play.STATE["error"] = message


def info(ui, r):
    t = ui.t
    label_w = 56 * t.s
    row = r.cut_top(t.row)
    paint.text_in("On", row.cut_left(label_w), t.text2, t.text)
    bodies = [(k, l) for k, l, _p, _g in anim_play.BODIES] + [(anim_play.SELECTED, "Selected Character")]
    fields.dropdown(ui, "anim:body", row, anim_play.body_label(anim_play.STATE["body"]),
                    _menu("anim:body", bodies, anim_play.STATE["body"], _set_body),
                    tip="The body the preview plays on")
    r.cut_top(t.gap)
    row = r.cut_top(t.row)
    paint.text_in("In Hand", row.cut_left(label_w), t.text2, t.text)
    held = anim_hand.STATE["item"]
    # listed when the menu opens: walking the user's scene on every draw cost a second per frame in a big scene
    choices = lambda: [("", "Nothing")] + [(o.name, o.name) for o in anim_hand.candidates(anim_play.selected_character())]
    fields.dropdown(ui, "anim:hand", row, held or "Nothing", _menu("anim:hand", choices, held or "", _set_hand),
                    tip="An item from the scene held on the hand socket (applied: a Child Of constraint on it)")
    r.cut_top(t.gap)
    row = r.cut_top(t.row)
    paint.text_in("Speed", row.cut_left(label_w), t.text2, t.text)
    fields.number(ui, "anim:speed", row, preview.PLAY["speed"], _set_speed, lo=0.05, hi=4.0, step=0.05,
                  fmt="%.2f")
    r.cut_top(t.gap)
    row = r.cut_top(t.row)
    row.cut_left(label_w)
    fields.checkbox(ui, "anim:loop", row, preview.PLAY["loop"], "Loop", _set_loop,
                    tip="Play it again from the start (applied: the strips repeat)")
    r.cut_top(t.gap)
    row = r.cut_top(t.row)
    row.cut_left(label_w)
    fields.checkbox(ui, "anim:mirror", row, anim_play.STATE["mirror"], "Mirror",
                    lambda context: anim_play.set_mirror(not anim_play.STATE["mirror"]),
                    tip="Left and right swapped (applied: the mirrored keys)")
    r.cut_top(t.gap)
    row = r.cut_top(t.row)
    row.cut_left(label_w)
    fields.checkbox(ui, "anim:materials", row, bool(STATE.get("materials")), "Materials", _toggle_materials,
                    tip="Draw the game materials (Eevee: heavier) instead of their textures (Workbench)")
    target = anim_play.selected_character()
    ready = anim_play.STATE["payload"] is not None or (anim_play.STATE["item"] or (1,))[0] is None
    text = "Apply to %s" % target.name if target else "Select a character to apply to"
    apply = r.cut_bottom(t.action)
    ui.button("anim:apply", apply, paint.fit(text, t.text, apply.w - 2 * t.pad), style="accent",
              action=_apply if target and ready and anim_play.STATE["item"] else None,
              enabled=bool(target and ready and anim_play.STATE["item"]),
              tip="What plays here onto the character as NLA strips: body, face, props on their sockets, sound")
    r.cut_bottom(t.pad)
    picked = anim_play.STATE["item"]
    if not picked:
        r.cut_top(t.big)
        paint.text_in("Click an animation in the list", r.cut_top(t.row), t.text3, t.caption)
    else:
        r.cut_top(t.big)
        paint.text_in(paint.fit(picked[1]["name"].strip(), t.title, r.w), r.cut_top(t.title + 2 * t.gap), t.text1, t.title)
        facts = anim_play.STATE.get("style") or (picked[0] or "Action in this file")
        paint.text_in(paint.fit(facts, t.caption, r.w), r.cut_top(t.caption + 2 * t.gap), t.text3, t.caption)


# ------------------------------------------------------------------ the header and the timer

def header(layout, context):
    row = layout.row(align=True)
    row.ui_units_x = 10
    row.prop(context.window_manager, "fp_anim_search", text="", icon='VIEWZOOM')


def _searched(self, context):
    redraw_all()


def tick():
    """The body shows with the tab."""
    area = next((a for a in host.editor_areas() if host.tab_id(a.spaces.active) == ID), None)
    if area is None:
        return
    st = anim_play.STATE
    if anim_play.body_missing() and bpy.context.mode == 'OBJECT' and not st["busy"] and not st["error"] and \
            (st["body"] != anim_play.SELECTED or anim_play.selected_character() is not None):
        anim_play.ensure_body(anim_play.replay)     # the body shows with the tab (and again after a drop)


def register():
    bpy.types.WindowManager.fp_anim_search = bpy.props.StringProperty(
        name="Search", description="Animations whose name has these words", options={'TEXTEDIT_UPDATE'},
        update=_searched)
    tabs.register_tab(tabs.Tab(ID, "Animation", 'ARMATURE_DATA', header=header, nav_icon="Animation", group="tools",
                               tick=tick, side=side, view=view, info=info))


def unregister():
    tabs.unregister_tab(ID)
    del bpy.types.WindowManager.fp_anim_search
