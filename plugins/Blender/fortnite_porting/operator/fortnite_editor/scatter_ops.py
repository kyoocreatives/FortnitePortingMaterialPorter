"""The Landscape tab's Scatter mode operators: pick, show, remove and edit a scatter layer; add a game grass type
(foliage-grass-type through the app, each variety's mesh through scatter_sources), objects, or a map landscape's own
grass; evaluate its grass masks; and the brush: drag paints the picked layer's mask (Ctrl erases, Shift smooths), or
erases placed instances. Each change is one step of Blender's undo."""
import json
import time

import bpy
import numpy as np

from . import host, land_scatter, land_select, land_view, logbook, scatter_sources
from .land_ops import MODES, WRITE_EVERY, Stroke, _ready
from .land_scatter import GRASS, PLACED, SCATTER, core, invalidate, modifiers, selected, shown_group, short
from .land_tab import LAND, redraw_all, refresh, world_at

SKIPPED = "mp_grass_skipped"    # a piece's: the grass outputs Add Game Grass skipped as empty (JSON list)


def _entry(context, key):
    return next((e for e in land_scatter.rows(context) if e["key"] == key), None)


def _undo_push(message):
    """An undo step for what an answer of the app added (no operator ran for it)."""
    windows = bpy.context.window_manager.windows
    try:
        with bpy.context.temp_override(window=windows[0]) if windows else bpy.context.temp_override():
            bpy.ops.ed.undo_push(message=message)
    except RuntimeError:
        pass


def _changed():
    invalidate()
    refresh()


# ------------------------------------------------------------------ adding layers

def add_layer_to(pieces, settings, instance, weight=None, label=None, taken=None):
    """A layer (`settings`: the scatter schema) on each piece under one label, numbered within its grass type (custom:
    within the custom ones); returns its list key (added to `taken`, the keys in use, when given). A map's many pieces
    get the big-terrain defaults (Display % down, camera cull) from their total area."""
    sc = core()
    kind = settings.get("type") or ""
    if taken is None:
        taken = {e["key"] for e in land_scatter.entries(pieces, bpy.context.scene)}
    base = label or short(settings.get("mesh")) or instance.name
    label, k = base, 2
    while land_scatter.layer_key(label, kind) in taken:
        label, k = "%s %d" % (base, k), k + 1
    taken.add(land_scatter.layer_key(label, kind))
    settings = dict(settings)
    if len(pieces) > 1 and "display" not in settings and "camera_cull" not in settings:
        expected = sum(sc._area(o) for o in pieces) / 100.0 * float(settings.get("density", 400.0))
        if expected > sc.VIEW_LIMIT:
            settings.update(display=max(1.0, round(100.0 * sc.VIEW_LIMIT / expected, 1)), camera_cull=True)
    for obj in pieces:
        try:
            sc.add_layer(obj, settings, instance, label=label, weight=weight)
        except (ValueError, KeyError) as e:       # the weight isn't on this piece: the layer stays, unweighted
            logbook.write("Warning: %s on %s: %s (no weight there)" % (label, obj.name, e), "warning")
    return land_scatter.layer_key(label, kind)


def usable(varieties):
    """The varieties that place anything: a mesh, and a density above 0 (the game's own at Epic quality)."""
    return [v for v in varieties if v.get("mesh") and float(v.get("density") or 0.0) > 0.0]


def add_varieties(names, varieties, weight, type_name, pick=True):
    """One grass type's varieties (add_groups)."""
    add_groups(names, [(type_name, varieties, weight)], pick)


def add_groups(names, groups, pick=True):
    """Layers of grass varieties on the pieces named: groups [(type name, varieties, weight)]. Every mesh imports first
    (shared by identical mesh and materials, the list noting what's left), then all the layers go on in one step, in
    the groups' order: the pieces' modifier stacks evaluate once, one undo step, the first layer picked (`pick`).
    Varieties placing nothing are skipped (logged)."""
    todo = []
    for type_name, varieties, weight in groups:
        kept = usable(varieties)
        skipped = [v.get("variety", 0) + 1 for v in varieties if v not in kept]
        if skipped:
            logbook.write("%s: skipped variet%s %s (no mesh or density 0)" %
                          (type_name, "y" if len(skipped) == 1 else "ies", ", ".join(map(str, skipped))))
        todo += [(type_name, v, weight) for v in kept]
    if not todo:
        return
    pending = SCATTER["pending"]
    for type_name, _v, _w in todo:
        pending[type_name] = pending.get(type_name, 0) + 1
    landed = {}

    def done(k, obj):
        type_name = todo[k][0]
        left = pending.get(type_name, 1) - 1
        if left > 0:
            pending[type_name] = left
        else:
            pending.pop(type_name, None)
        landed[k] = obj
        if len(landed) == len(todo):
            _add_landed(names, todo, landed, pick)
        else:
            redraw_all()        # the list's note counts down

    for k, (_t, v, _w) in enumerate(todo):
        scatter_sources.ensure_source(v["mesh"], v.get("materials") or [], lambda obj, k=k: done(k, obj))


def _add_landed(names, todo, landed, pick):
    pieces = [p for p in (bpy.data.objects.get(n) for n in names) if p is not None]
    taken = {e["key"] for e in land_scatter.entries(pieces, bpy.context.scene)}
    keys, failed = [], []
    for k, (type_name, v, weight) in enumerate(todo):
        if landed.get(k) is None or not pieces:
            failed.append("%s variety %d" % (type_name, v.get("variety", 0) + 1))
            continue
        keys.append(add_layer_to(pieces, v, landed[k], weight, taken=taken))
    if failed:
        logbook.write("Error: no mesh imported for " + ", ".join(failed), "error")
    if not keys:
        _changed()
        return
    if pick:
        land_scatter.pick(keys[0])
    types = list(dict.fromkeys(t for t, _v, _w in todo))
    logbook.write("Scattered %s: %d layer%s" % (types[0] if len(types) == 1 else "%d grass types" % len(types), len(keys),
                                               "" if len(keys) == 1 else "s"))
    _undo_push("Add %s" % (types[0] if len(types) == 1 else "Game Grass"))
    _changed()


def update_masks(pieces, names=None):
    """The grass masks (grass_masks.write) of each piece: its MPGrass outputs (`names` only), or every input of its
    material's grass output; unknown expressions to the status log. Returns an error text, None when all went."""
    from ...material_porter import grass_masks
    unknown, written, failed = {}, 0, None
    for obj in pieces:
        own = json.loads(obj[GRASS]) if obj.get(GRASS) else {}
        outputs = own.get("outputs")
        if names is not None:
            outputs = [o for o in outputs or [] if o.get("name") in names] or list(names)
        try:
            report = grass_masks.write(obj, outputs)
        except Exception as e:      # the app's answer, the graph: any failure is the user's to read, not a crash
            failed = "%s: %s" % (obj.name, e or type(e).__name__)
            continue
        written += len(report.get("written") or [])
        for k, c in (report.get("unknown") or {}).items():
            unknown[k] = unknown.get(k, 0) + c
    logbook.write("Grass masks: %d written over %d piece%s" % (written, len(pieces), "" if len(pieces) == 1 else "s"))
    if unknown:
        logbook.write("Warning: grass masks read these expressions as 1: " +
                      ", ".join("%s (%d)" % kv for kv in sorted(unknown.items())), "warning")
    if failed:
        logbook.write("Error: grass masks failed on " + failed, "error")
    return failed


def _missing_masks(pieces, names):
    sc = core()
    return [n for n in names if any(sc.GRASS + n not in p.data.attributes for p in pieces)]


def empty_outputs(pieces, names):
    """The grass outputs whose mask is 0 on every piece (one not evaluated isn't empty: it scatters unweighted)."""
    sc = core()
    out = []
    for n in names:
        attrs = [p.data.attributes.get(sc.GRASS + n) for p in pieces]
        if not attrs or any(a is None or a.data_type != 'FLOAT' for a in attrs):
            continue
        empty = True
        for a in attrs:
            v = np.empty(len(a.data), np.float32)
            a.data.foreach_get("value", v)
            if len(v) and float(v.max()) > 0.0:
                empty = False
                break
        if empty:
            out.append(n)
    return out


def _skipped(pieces):
    """The grass outputs Add Game Grass left out as empty (Update Grass Masks adds them once painted)."""
    names = []
    for p in pieces:
        try:
            names += [n for n in json.loads(p.get(SKIPPED) or "[]") if n not in names]
        except (TypeError, ValueError):
            pass
    return names


def _set_skipped(pieces, names):
    for p in pieces:
        p[SKIPPED] = json.dumps(sorted(names))


def variety_key(v):
    return (v.get("type") or "").split(".")[0].lower(), v.get("variety", 0)


def scattered_keys(context):
    """variety_key()s of the group's game layers."""
    return {(e["type"].split(".")[0].lower(), e["variety"]) for e in land_scatter.rows(context) if e["kind"] == "game"}


def _grass_todo(pieces, context, only=None):
    """[(output, varieties not scattered yet)] of the group's grass outputs (`only`: these names)."""
    payload = land_scatter.grass_payload(pieces) or {}
    have = scattered_keys(context)
    todo = []
    for out in payload.get("outputs") or []:
        if only is not None and out.get("name") not in only:
            continue
        new = [v for v in out.get("type") or [] if variety_key(v) not in have]
        if usable(new):
            todo.append((out["name"], new))
    return todo


def _add_outputs(pieces, todo, pick=True):
    """Layers for these grass outputs, each weighted by its mask, the first type's first layer picked (`pick`)."""
    add_groups([p.name for p in pieces], [(short(varieties[0].get("type")) or output, varieties, {"attribute": output})
                                          for output, varieties in todo], pick)


_TYPES = []     # the search's items, kept alive (Blender reads an enum callback's strings after it returns)


def _origin(path):
    root = path.strip("/").split("/")[0]
    return root[:8] if len(root) == 36 and root.count("-") == 4 else root


def _type_items(self, context):
    _TYPES[:] = [(t["path"], "%s · %s" % (t["name"], _origin(t["path"])),
                  "%s (%d varieties)" % (t["path"], t.get("varieties", 0))) for t in SCATTER["types"] or []] \
        or [("", "No grass types", "")]
    return _TYPES


class FP_OT_scatter_add_type(bpy.types.Operator):
    """Scatter a grass type of the game files on this landscape: a layer per variety, weighted by the landscape's grass
    mask for it when its material has one"""
    bl_idname = "fp.scatter_add_type"
    bl_label = "Add Grass Type"
    bl_options = {'INTERNAL'}
    bl_property = "type"
    type: bpy.props.EnumProperty(name="Grass Type", items=_type_items)

    def invoke(self, context, event):
        if not shown_group(context)[1]:
            return {'CANCELLED'}
        if not SCATTER["types"]:
            SCATTER["types_asked"] = 0.0
            land_scatter._ask_types()
            self.report({'INFO'}, "Reading the game's grass types: try again in a moment")
            return {'CANCELLED'}
        from . import land_popups
        from .ui import popup_ops
        land_popups.open_types(context, *popup_ops.anchor_from_event(context, event))
        return {'FINISHED'}

    def execute(self, context):
        from .state import client
        name, pieces = shown_group(context)
        path = self.type
        if not pieces or not path:
            return {'CANCELLED'}
        output = land_scatter.output_for(pieces, path)
        if output and _missing_masks(pieces, [output]):
            update_masks(pieces, [output])
        weight = {"attribute": output} if output else None
        names = [p.name for p in pieces]

        def got(result):
            if isinstance(result, Exception):
                logbook.write("Error: grass type %s: %s" % (short(path), result), "error")
                return
            add_varieties(names, result.get("varieties") or [], weight, result.get("name") or short(path))
        logbook.write("Adding %s%s" % (short(path), " (weighted by its %s mask)" % output if output else ""))
        client().request("foliage-grass-type", got, path=path)
        return {'FINISHED'}


class FP_OT_scatter_game_grass(bpy.types.Operator):
    """Scatter every grass the landscape's material outputs, each weighted by its grass mask (the masks evaluated
    first when missing); outputs with nothing painted here wait until Update Grass Masks finds them painted"""
    bl_idname = "fp.scatter_game_grass"
    bl_label = "Add Game Grass"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        name, pieces = shown_group(context)
        todo = _grass_todo(pieces, context)
        if not todo:
            self.report({'INFO'}, "The landscape's grass is already scattered")
            return {'CANCELLED'}
        missing = _missing_masks(pieces, [n for n, _ in todo])
        if missing and update_masks(pieces, missing):
            self.report({'WARNING'}, "Grass masks failed (see the status log): the layers scatter unweighted there")
        # outputs with nothing painted here: no mesh imported, no layer (Update Grass Masks adds them once painted)
        empty = empty_outputs(pieces, [n for n, _ in todo])
        _set_skipped(pieces, empty)
        if empty:
            logbook.write("Add Game Grass: skipped %d empty grass output%s (%s)" %
                          (len(empty), "" if len(empty) == 1 else "s", ", ".join(empty)))
        todo = [(n, v) for n, v in todo if n not in empty]
        if not todo:
            self.report({'INFO'}, "Every grass output left is empty on this landscape")
            return {'FINISHED'}
        _add_outputs(pieces, todo)
        return {'FINISHED'}


class FP_OT_scatter_output(bpy.types.Operator):
    """Scatter this grass output of the landscape's material (or one of its varieties), weighted by its grass mask"""
    bl_idname = "fp.scatter_output"
    bl_label = "Scatter Grass Output"
    bl_options = {'INTERNAL'}
    output: bpy.props.StringProperty()
    variety: bpy.props.IntProperty(default=-1, options={'SKIP_SAVE'})

    def execute(self, context):
        name, pieces = shown_group(context)
        found = _grass_todo(pieces, context, [self.output])
        varieties = [v for _n, new in found for v in usable(new) if self.variety < 0 or v.get("variety", 0) == self.variety]
        if not varieties:
            return {'CANCELLED'}
        if _missing_masks(pieces, [self.output]):
            if update_masks(pieces, [self.output]):
                self.report({'WARNING'}, "Grass masks failed (see the status log): the layer scatters unweighted there")
            from . import land_grass_list
            land_grass_list.refresh(pieces)
        _add_outputs(pieces, [(self.output, varieties)])
        return {'FINISHED'}


class FP_OT_scatter_add_object(bpy.types.Operator):
    """Scatter the selected mesh objects here (none selected: the active collection's), a layer each"""
    bl_idname = "fp.scatter_add_object"
    bl_label = "Add from Object"
    bl_options = {'INTERNAL', 'UNDO'}

    def execute(self, context):
        name, pieces = shown_group(context)
        if not pieces:
            return {'CANCELLED'}
        skip = set(pieces)
        coll = bpy.data.collections.get(scatter_sources.COLLECTION)
        if coll is not None:
            skip |= set(coll.objects)
        objs = [o for o in context.selected_objects if o.type == 'MESH' and o not in skip and o.get(PLACED) is None]
        if not objs and context.collection is not None and context.collection != context.scene.collection:
            objs = [o for o in context.collection.all_objects if o.type == 'MESH' and o not in skip]
        if not objs:
            self.report({'WARNING'}, "Select the mesh objects to scatter (or make their collection active)")
            return {'CANCELLED'}
        keys = [add_layer_to(pieces, {"source": "custom"}, o, label=o.name) for o in objs]
        land_scatter.pick(keys[0])
        _changed()
        self.report({'INFO'}, "Scattering %s" % ", ".join(k[2:] for k in keys))
        return {'FINISHED'}


class FP_OT_scatter_masks(bpy.types.Operator):
    """Evaluate the landscape material's grass masks again over the pieces (after painting its layers), and scatter the
    outputs Add Game Grass skipped as empty that are painted now; expressions it can't evaluate read as 1 and are
    listed in the status log"""
    bl_idname = "fp.scatter_masks"
    bl_label = "Update Grass Masks"
    bl_options = {'INTERNAL', 'UNDO'}

    def execute(self, context):
        name, pieces = shown_group(context)
        if not pieces:
            return {'CANCELLED'}
        failed = update_masks(pieces)
        # outputs Add Game Grass skipped as empty and painted since: their layers now (the others stay as they are)
        skipped = _skipped(pieces)
        if skipped:
            still = empty_outputs(pieces, skipped)
            todo = _grass_todo(pieces, context, [n for n in skipped if n not in still])
            _set_skipped(pieces, still)
            if todo:
                logbook.write("Grass masks: adding %s (painted since Add Game Grass skipped %s)" %
                              (", ".join(n for n, _ in todo), "it" if len(todo) == 1 else "them"))
                _add_outputs(pieces, todo, pick=False)
        invalidate(reload=True)
        redraw_all()
        if failed:
            self.report({'WARNING'}, "Grass masks failed on " + failed)
        return {'FINISHED'}


# ------------------------------------------------------------------ a layer

class FP_OT_scatter_pick(bpy.types.Operator):
    """Paint and edit this layer (Ctrl: add or remove it, Shift: the range from the last pick)"""
    bl_idname = "fp.scatter_pick"
    bl_label = "Scatter Layer"
    bl_options = {'INTERNAL'}
    key: bpy.props.StringProperty()
    group: bpy.props.StringProperty(options={'SKIP_SAVE'})      # a grass type header's key: its layers
    extend: bpy.props.BoolProperty(options={'SKIP_SAVE'})
    span: bpy.props.BoolProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        if self.group:
            land_select.pick_group(self.group, self.extend, context)
        elif _entry(context, self.key) is None:
            return {'CANCELLED'}
        else:
            land_select.click(self.key, self.extend, self.span, context)
        redraw_all()
        return {'FINISHED'}


class FP_OT_scatter_show(bpy.types.Operator):
    """Show or hide this layer in the viewport and renders"""
    bl_idname = "fp.scatter_show"
    bl_label = "Show Scatter Layer"
    bl_options = {'INTERNAL', 'UNDO'}
    key: bpy.props.StringProperty()

    def execute(self, context):
        e = _entry(context, self.key)
        if e is None:
            return {'CANCELLED'}
        show = e["hidden"]
        if e["kind"] == "placed":
            obj = bpy.data.objects.get(e["object"])
            obj.hide_viewport = obj.hide_render = not show
        else:
            for obj, mod in modifiers(e):
                mod.show_viewport = mod.show_render = show
        _changed()
        return {'FINISHED'}


class FP_OT_scatter_remove(bpy.types.Operator):
    """Remove the picked layer (its modifiers and paint mask; placed foliage: its points object)"""
    bl_idname = "fp.scatter_remove"
    bl_label = "Remove Scatter Layer"
    bl_options = {'INTERNAL', 'UNDO'}

    def invoke(self, context, event):
        found = land_select.entries(context)
        if not found:
            return {'CANCELLED'}
        from . import land_popups
        land_popups.confirm_remove(context)        # its OK runs this again with EXEC
        return {'FINISHED'}

    def execute(self, context):
        found = land_select.entries(context)
        if not found:
            return {'CANCELLED'}
        for e in found:
            if e["kind"] == "placed":
                obj = bpy.data.objects.get(e["object"])
                if obj is not None:
                    bpy.data.objects.remove(obj)
            else:
                for name, layer, _mod in e["members"]:
                    obj = bpy.data.objects.get(name)
                    if obj is not None:
                        core().remove_layer(obj, layer)
        SCATTER["selected"] = None
        _changed()
        return {'FINISHED'}


class FP_OT_scatter_set(bpy.types.Operator):
    """Switch this setting"""
    bl_idname = "fp.scatter_set"
    bl_label = "Scatter Setting"
    bl_options = {'INTERNAL', 'UNDO'}
    name: bpy.props.StringProperty()
    on: bpy.props.BoolProperty()

    def execute(self, context):
        found = land_select.layers(context)
        if not found:
            return {'CANCELLED'}
        sc = core()
        for obj, mod in (m for e in found for m in modifiers(e)):
            sc._set(mod, self.name, self.on)
            obj.update_tag()
        SCATTER["synced"] = None
        _changed()
        return {'FINISHED'}


class FP_OT_scatter_shadow(bpy.types.Operator):
    """The instanced object casts shadows or not (everywhere it's used: Fortnite's grass casts none)"""
    bl_idname = "fp.scatter_shadow"
    bl_label = "Scatter Shadows"
    bl_options = {'INTERNAL', 'UNDO'}

    def execute(self, context):
        found = [(e, mods) for e in land_select.layers(context) for mods in [modifiers(e)] if mods]
        insts = [i for _e, mods in found for i in [core()._get(mods[0][1], "Instance")] if i is not None]
        if not insts:
            return {'CANCELLED'}
        on = not all(i.visible_shadow for i in insts)       # differing: all on
        for name, layer, _mod in (m for e, _mods in found for m in e["members"]):
            core().update_layer(bpy.data.objects[name], layer, {"cast_shadow": on})
        _changed()
        return {'FINISHED'}


class FP_OT_scatter_weight(bpy.types.Operator):
    """Weight the layer by this (times its paint mask)"""
    bl_idname = "fp.scatter_weight"
    bl_label = "Scatter Weight"
    bl_options = {'INTERNAL', 'UNDO'}
    weight: bpy.props.StringProperty()

    def execute(self, context):
        found = land_select.layers(context)
        if not found:
            return {'CANCELLED'}
        weight = json.loads(self.weight or "null")
        for name, layer, _mod in (m for e in found for m in e["members"]):
            try:
                core().set_weight(bpy.data.objects[name], layer, weight)
            except (ValueError, KeyError) as error:
                self.report({'WARNING'}, "%s: %s" % (name, error))
        invalidate(reload=True)
        refresh()
        return {'FINISHED'}


# ------------------------------------------------------------------ the brush

class FP_OT_scatter_stroke(Stroke, bpy.types.Operator):
    """Paint the picked scatter layer's mask: drag adds, Ctrl erases, Shift smooths"""
    bl_idname = "fp.scatter_stroke"
    bl_label = "Paint Scatter Mask"
    bl_options = {'INTERNAL', 'UNDO'}
    mode: bpy.props.EnumProperty(items=MODES, options={'SKIP_SAVE'})

    def target(self, context, store):
        if not land_scatter.is_on(context) or store is not SCATTER["store"]:
            return None
        e = selected(context)
        if e is not None and e["kind"] == "placed":
            return None     # the erase brush's
        return store.grid.layers[0] if store.grid.layers else ""


class FP_OT_scatter_erase(bpy.types.Operator):
    """Erase the picked placed foliage's instances under the brush (Strength: the share erased per pass)"""
    bl_idname = "fp.scatter_erase"
    bl_label = "Erase Instances"
    bl_options = {'INTERNAL', 'UNDO'}

    def invoke(self, context, event):
        store, frame = _ready(context)
        e = selected(context) if store is not None and land_scatter.is_on(context) else None
        if e is None or e["kind"] != "placed":
            return {'PASS_THROUGH'}
        obj, xy = land_scatter.dots(e)
        if obj is None:
            return {'CANCELLED'}
        if obj.mode == 'EDIT':
            self.report({'WARNING'}, "Leave Edit Mode on %s first" % obj.name)
            return {'CANCELLED'}
        self.obj_name, self.xy, self.frame = obj.name, xy, frame
        self.keep = np.ones(len(xy), bool)
        self.rng = np.random.default_rng()
        self.last, self.erased, self.last_write = None, 0, 0.0
        self._dab(context, event.mouse_region_x, event.mouse_region_y)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def _dab(self, context, x, y):
        wm = context.window_manager
        wx, wy = world_at(self.frame, x, y)
        radius = max(wm.fp_land_radius, 1e-3)
        points = [(wx, wy)]
        if self.last is not None:       # dabs along the path, a quarter radius apart
            dx, dy = wx - self.last[0], wy - self.last[1]
            n = int((dx * dx + dy * dy) ** 0.5 / (radius * 0.25))
            if n == 0:
                return
            points = [(self.last[0] + dx * i / n, self.last[1] + dy * i / n) for i in range(1, n + 1)]
        for px, py in points:
            d = np.hypot(self.xy[:, 0] - px, self.xy[:, 1] - py) / radius
            near = np.flatnonzero((d <= 1.0) & self.keep)
            if not len(near):
                continue
            chance = land_view.fade(d[near], wm.fp_land_falloff) * min(max(wm.fp_land_strength, 0.0), 1.0)
            gone = near[self.rng.random(len(near)) < chance]
            self.keep[gone] = False
            self.erased += len(gone)
        self.last = (wx, wy)
        self._flush(force=False)

    def _flush(self, force):
        """The points object follows the brush a little behind (rebuilding its mesh)."""
        if self.keep.all() or (not force and time.time() - self.last_write < WRITE_EVERY):
            return
        obj = bpy.data.objects.get(self.obj_name)
        if obj is None:
            return
        land_scatter.keep_points(obj.data, self.keep)
        self.xy = self.xy[self.keep]
        self.keep = np.ones(len(self.xy), bool)
        SCATTER["dots"] = (self.obj_name, self.xy)
        self.last_write = time.time()
        redraw_all()

    def modal(self, context, event):
        x, y = event.mouse_region_x, event.mouse_region_y
        if event.type == 'MOUSEMOVE':
            LAND["hover"][context.area.as_pointer()] = (x, y)
            self._dab(context, x, y)
        elif event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            self._flush(force=True)
            LAND["message"] = "%d instance%s erased" % (self.erased, "" if self.erased == 1 else "s") if self.erased else ""
            _changed()
            return {'FINISHED'}
        return {'RUNNING_MODAL'}


classes = (FP_OT_scatter_add_type, FP_OT_scatter_game_grass, FP_OT_scatter_output, FP_OT_scatter_add_object, FP_OT_scatter_masks,
           FP_OT_scatter_pick, FP_OT_scatter_show, FP_OT_scatter_remove, FP_OT_scatter_set, FP_OT_scatter_shadow,
           FP_OT_scatter_weight, FP_OT_scatter_stroke,
           FP_OT_scatter_erase)
_keymaps = []


def register():
    for c in classes:
        bpy.utils.register_class(c)
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc is None:
        return
    add = host.adder(kc, _keymaps)
    for mods, mode in (({}, 'paint'), ({"ctrl": True}, 'erase'), ({"shift": True}, 'smooth')):
        add(FP_OT_scatter_stroke.bl_idname, 'LEFTMOUSE', 'PRESS', **mods).properties.mode = mode
    for mods in ({}, {"ctrl": True}):
        add(FP_OT_scatter_erase.bl_idname, 'LEFTMOUSE', 'PRESS', **mods)


def unregister():
    for km, k in _keymaps:
        try:
            km.keymap_items.remove(k)
        except (ReferenceError, RuntimeError):
            pass
    _keymaps.clear()
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
