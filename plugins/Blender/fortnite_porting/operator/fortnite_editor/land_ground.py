"""Ground colour in the editor: the Landscape tab's Bake (rvt.bake, one undo step) and Auto update (the painted area
re-baked a second after the last paint stroke), the Ground Blend rows of a scatter layer or placed foliage
(scatter.set_ground, instances.set_ground), and the sidebar panel of a hand-placed plant (ground.set_object).

After an undo rvt reads the image again from what was packed with that state (rvt._undone), and with Auto update on,
the areas re-baked since that bake are baked again."""
import time

import bpy
from bpy.app.handlers import persistent

from . import land_store, tabs
from .ui import paint
from .ui.rect import Rect

DELAY = 1.0                 # s after the last stroke before Auto update re-bakes
AT = "mp_rvt_at"            # a scene's: when the editor last baked (time.time())

# rect: the strokes' area waiting (m), scene: its name
PENDING = {"rect": None, "scene": None}
SINCE = {}                  # {rvt.PACKED stamp: the area Auto update re-baked after that bake (m)}


def core():
    from ...material_porter import rvt
    return rvt


def _union(a, b):
    return b if a is None else (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def _redraw(objects=()):
    from .land_tab import refresh
    refresh(objects)


# ------------------------------------------------------------------ baking

def bake(scene, rect=None):
    """rvt.bake on the scene (rect: only that area), stamped for the status line; a stroke's area kept for undo."""
    done = core().bake(scene=scene, rect=rect)
    if not done.get("objects"):
        return done
    scene[AT] = time.time()
    if rect is not None and not done.get("packed"):
        key = scene.get(core().PACKED)
        SINCE[key] = _union(SINCE.get(key), tuple(rect))
    return done


def stroke_rect(grid, box, radius):
    """A stroke's cells (r0, c0, r1, c1) as world XY metres, padded by the brush radius."""
    r0, c0, r1, c1 = box
    x0, y0 = grid.to_world(c0, r0)
    x1, y1 = grid.to_world(c1 - 1, r1 - 1)
    return (x0 - radius, y0 - radius, x1 + radius, y1 + radius)


def after_stroke(scene, rect):
    """A paint stroke ended over rect (m): with Auto update on and a bake, its area re-bakes DELAY s after the last
    stroke (each stroke re-arms the timer, the areas joined). True when armed."""
    bounds = core().baked(scene).get("bounds") if land_store.auto_update(scene) else None
    if not bounds:
        return False
    # within the bounds: a stroke at the edge isn't a reason for a full bake
    rect = (max(rect[0], bounds[0]), max(rect[1], bounds[1]), min(rect[2], bounds[2]), min(rect[3], bounds[3]))
    if rect[0] >= rect[2] or rect[1] >= rect[3]:
        return False
    PENDING["rect"] = _union(PENDING["rect"] if PENDING["scene"] == scene.name else None, rect)
    PENDING["scene"] = scene.name
    if bpy.app.timers.is_registered(_fire):
        bpy.app.timers.unregister(_fire)
    bpy.app.timers.register(_fire, first_interval=DELAY)
    return True


def _fire():
    rect, name = PENDING["rect"], PENDING["scene"]
    PENDING.update(rect=None, scene=None)
    scene = bpy.data.scenes.get(name) if name else None
    if scene is not None and rect is not None and core().baked(scene):
        bake(scene, rect)
        _redraw()
    return None


def _undone(scene, packed):
    """After rvt read the image back from its packed bake: Auto update re-bakes the strokes since that bake."""
    again = SINCE.get(packed)
    if again is not None:
        after_stroke(scene, again)
    _redraw()


@persistent
def _loaded(*_):
    if bpy.app.timers.is_registered(_fire):
        bpy.app.timers.unregister(_fire)
    PENDING.update(rect=None, scene=None)
    SINCE.clear()


# ------------------------------------------------------------------ the Ground Colour section (Scatter mode)

def ago(seconds):
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return "%d min ago" % (seconds // 60)
    if seconds < 86400:
        return "%d h ago" % (seconds // 3600)
    return "%d d ago" % (seconds // 86400)


def status(scene):
    state = core().baked(scene)
    if not state.get("size"):
        return "Not baked"
    n = len(state.get("objects") or [])
    text = "Baked %d×%d px, %d object%s" % (state["size"][0], state["size"][1], n, "" if n == 1 else "s")
    at = scene.get(AT)
    return text + (", " + ago(time.time() - at) if at else "")


def colour_rows(scene):
    """The section's rows: (kind, payload)."""
    return [("title", "Ground Colour"), ("status", status(scene)), ("bake", None),
            ("auto", land_store.auto_update(scene))]


def _run(op):
    def run(context):
        getattr(bpy.ops.fp, op)()
    return run


def check_box(ui, cell, rid, label, on, action, tip=""):
    """A checkbox row (the info column's look)."""
    t = ui.t
    ui.row(rid, cell, "", None, False, action, tip=tip)
    inner = cell.pad(t.pad, 0)
    slot = inner.cut_left(t.icon)
    size = 12 * t.s
    box = Rect(slot.x + (slot.w - size) / 2, slot.y + (slot.h - size) / 2, size, size)
    # inside the row's hover fill, a padding in: radius = row radius - padding
    paint.rect(box, t.accent if on else (0, 0, 0, 0), max(t.r_small - t.pad, t.s), edge=t.text3, border=0 if on else 1)
    if on:
        ui.icon(box, "mdi_Check", t.text1)
    inner.cut_left(t.pad)
    paint.text_in(label, inner, t.text1 if ui.hot(rid) else t.text2, t.text)


def colour(ui, r):
    """The section at the column's foot, bottom up: Bake and Auto update on a row, the status, the title."""
    t = ui.t
    rows = dict(colour_rows(bpy.context.scene))
    cell = r.cut_bottom(t.action)
    r.cut_bottom(t.pad)
    half = (cell.w - t.pad) / 2
    ui.button("ground:bake", Rect(cell.x, cell.y, half, cell.h), "Bake", None, _run("land_bake"), style="control",
              tip="Bake the ground's colour, which grass and rocks on it read (again after repainting)")
    box = Rect(cell.x + half + t.pad, cell.y + (cell.h - t.row) / 2, half, t.row)
    check_box(ui, box, "ground:auto", "Auto update", rows["auto"], _run("land_auto_update"),
              tip="Re-bake the painted area a second after each paint stroke")
    line = r.cut_bottom(t.row_pitch * 0.75)
    paint.text_in(paint.fit(rows["status"], t.caption, line.w - 2 * t.pad), line.pad(t.pad, 0), t.text3, t.caption)
    head = r.cut_bottom(t.row_pitch)
    paint.text_in(rows["title"], head.pad(t.pad, 0), t.text3, t.text)
    r.cut_bottom(t.pad)


# ------------------------------------------------------------------ a layer's or placed foliage's Ground Blend

def _modes():
    from ...material_porter import ground
    return ground


def ground_of(e):
    """{"mode", "style", "strength"} of a scatter entry (its first piece's layer, or the placed points)."""
    from ...material_porter import instances, scatter
    if e["kind"] == "placed":
        return instances.ground_of(bpy.data.objects[e["object"]])
    name, layer, _mod = e["members"][0]
    return scatter.ground_of(bpy.data.objects[name], layer)


def set_ground(e, **change):
    """A scatter entry's Ground Blend changed (mode, style, strength), on every piece of a layer."""
    from ...material_porter import instances, scatter
    g = dict(ground_of(e), **change)
    if e["kind"] == "placed":
        obj = bpy.data.objects[e["object"]]
        instances.set_ground(obj, g["mode"], g["style"], g["strength"])
        objs = [obj]
    else:
        objs = []
        for name, layer, _mod in e["members"]:
            obj = bpy.data.objects.get(name)
            if obj is not None:
                scatter.set_ground(obj, layer, g["mode"], g["style"], g["strength"])
                objs.append(obj)
    from .land_scatter import SCATTER
    SCATTER["synced"] = None
    _redraw(objs)


def info_rows(e):
    """The info column's Ground rows (kind, section, payload): Style and Strength only where they apply (On)."""
    from .land_scatter import SCATTER
    out = [("group", "Ground", "Ground")]
    if not SCATTER["open"].get("s:Ground", True):
        return out
    g = ground_of(e)
    out.append(("value", "Ground", ("Ground", g["mode"])))
    if g["mode"] == "On":
        out += [("value", "Ground", ("Style", g["style"])), ("value", "Ground", ("Strength", "%.2f" % g["strength"]))]
    return out


def _controls(layout, owner, mode):
    col = layout.column()
    col.use_property_split, col.use_property_decorate = True, False
    col.row().prop(owner, "fp_ground_mode", expand=True)
    sub = col.column()
    sub.active = mode == "On"
    sub.prop(owner, "fp_ground_style")
    sub.prop(owner, "fp_ground_strength", slider=True)


def _picked():
    from .land_scatter import selected
    return selected()


def _scene_get(field):
    def get(scene):
        e = _picked()
        g = ground_of(e) if e is not None else {"mode": "Game", "style": _modes().STYLES[0], "strength": 1.0}
        return _index(field, g)
    return get


def _scene_set(field):
    def set_(scene, value):
        from .land_select import entries
        for e in entries():     # every picked layer and placed foliage
            set_ground(e, **{field: _value(field, value)})
    return set_


def _index(field, g):
    ground = _modes()
    return {"mode": lambda: ground.MODES.index(g["mode"]), "style": lambda: ground.STYLES.index(g["style"]),
            "strength": lambda: g["strength"]}[field]()


def _value(field, value):
    ground = _modes()
    return {"mode": lambda: ground.MODES[value], "style": lambda: ground.STYLES[value],
            "strength": lambda: round(value, 3)}[field]()


# ------------------------------------------------------------------ a hand-placed plant (the sidebar)

def is_foliage(obj):
    """A mesh with a built foliage material: its root group has Ground Reference (ground.blend's input)."""
    ref = _modes().REFERENCE
    for slot in getattr(obj, "material_slots", ()):
        tree = slot.material.node_tree if slot.material is not None else None
        if tree is not None and any(n.bl_idname == "ShaderNodeGroup" and n.inputs.get(ref) is not None for n in tree.nodes):
            return True
    return False


def _object_get(field):
    def get(obj):
        return _index(field, _modes().get_object(obj))
    return get


def _object_set(field):
    def set_(obj, value):
        ground = _modes()
        g = dict(ground.get_object(obj), **{field: _value(field, value)})
        ground.set_object(obj, g["mode"], g["style"], g["strength"])
        _redraw([obj])
    return set_


class FP_PT_ground_object(bpy.types.Panel):
    """A hand-placed plant's Ground Blend: the ground's baked colour under its root"""
    bl_space_type = 'NODE_EDITOR'
    bl_region_type = 'UI'
    bl_category = tabs.SIDEBAR
    bl_label = "Ground"

    @classmethod
    def poll(cls, context):
        from .tool_tabs import REST, shown
        obj = context.active_object
        return obj is not None and obj.type == 'MESH' and shown(context, {REST, "landscape"}) and is_foliage(obj)

    def draw(self, context):
        obj = context.active_object
        _controls(self.layout, obj, _modes().get_object(obj)["mode"])


class FP_OT_land_auto_update(bpy.types.Operator):
    """Re-bake the painted area a second after each paint stroke"""
    bl_idname = "fp.land_auto_update"
    bl_label = "Auto Update Ground Colour"
    bl_options = {'INTERNAL', 'UNDO'}

    def execute(self, context):
        land_store.set_auto_update(context.scene, not land_store.auto_update(context.scene))
        _redraw()
        return {'FINISHED'}


def _props(get, set_):
    ground = _modes()
    return dict(
        fp_ground_mode=bpy.props.EnumProperty(name="Ground", items=[(m, m, "") for m in ground.MODES], get=get("mode"),
                                              set=set_("mode"), description="Game: as the game has it. On: the "
                                              "ground's baked colour under the root. Off: never the ground's colour"),
        fp_ground_style=bpy.props.EnumProperty(name="Style", items=[(s, s, "") for s in ground.STYLES],
                                               get=get("style"), set=set_("style"),
                                               description="The whole blade, or the ground at the root fading to "
                                               "the plant's colour at the tip"),
        fp_ground_strength=bpy.props.FloatProperty(name="Strength", min=0.0, max=1.0, get=get("strength"),
                                                   set=set_("strength"), description="How much of the ground's colour"))


HANDLERS = (("load_post", _loaded),)
classes = (FP_PT_ground_object, FP_OT_land_auto_update)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    for owner, (get, set_) in ((bpy.types.Scene, (_scene_get, _scene_set)), (bpy.types.Object, (_object_get, _object_set))):
        for name, prop in _props(get, set_).items():
            setattr(owner, name, prop)
    for name, fn in HANDLERS:
        getattr(bpy.app.handlers, name).append(fn)
    core().UNDONE.append(_undone)


def unregister():
    if _undone in core().UNDONE:
        core().UNDONE.remove(_undone)
    for name, fn in HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if fn in handlers:
            handlers.remove(fn)
    _loaded()
    for owner in (bpy.types.Scene, bpy.types.Object):
        for name in ("fp_ground_mode", "fp_ground_style", "fp_ground_strength"):
            delattr(owner, name)
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
