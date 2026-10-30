"""The Landscape tab's input through the editor's "Fortnite" keymap: drag paints the picked layer (Ctrl erases, Shift
smooths), F / Shift+F resize the radius / strength with the mouse, [ and ] step the radius, the wheel zooms, middle mouse
pans, Home fits. Each stroke is one step of Blender's own undo (Ctrl+Z anywhere goes back one stroke)."""
import time

import bpy

from . import host, land_view, tabs
from .land_tab import LAND, readout, redraw_all, refresh_picture, world_at

WRITE_EVERY = 0.15      # seconds between writes to the pieces during a stroke (the viewport follows at that rate)
PICTURE_EVERY = 0.03    # seconds between picture updates


def _union(a, b):
    return b if a is None else (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def _ready(context):
    """The tab's store and frame when the Landscape tab shows a read landscape, else (None, None)."""
    if not tabs.is_tab(context, "landscape") or LAND["store"] is None:
        return None, None
    frame = LAND["views"].get(context.area.as_pointer())
    return (LAND["store"], frame) if frame is not None else (None, None)


MODES = [('paint', "Paint", ""), ('erase', "Erase", ""), ('smooth', "Smooth", "")]


class Stroke:
    """A brush stroke on the tab's store (a mixin: a registered operator subclassed by another breaks its keymap
    items' pass-through)."""

    def target(self, context, store):
        """The layer this stroke paints; None: the press isn't this brush's (Scatter mode has its own)."""
        return None if getattr(context.window_manager, "fp_land_mode", 'PAINT') == 'SCATTER' else \
            context.window_manager.fp_land_layer

    def invoke(self, context, event):
        store, frame = _ready(context)
        if store is None:
            return {'PASS_THROUGH'}
        self.layer = layer = self.target(context, store)
        if layer is None:
            return {'PASS_THROUGH'}
        if layer not in store.grid.layers:
            self.report({'INFO'}, "Pick a layer in the list first")
            return {'CANCELLED'}
        editing = [o.name for o in (p["obj"] if isinstance(p, dict) else p for p in store.pieces) if o.mode == 'EDIT']
        if editing:     # an edit-mode mesh overwrites its attributes when it leaves edit mode: paint would be lost
            self.report({'WARNING'}, "Leave Edit Mode on %s first" % editing[0])
            return {'CANCELLED'}
        self.store, self.frame = store, frame
        grid = store.grid
        # the layers a stroke changes: an independent layer alone, a blended one with the other blended layers
        self.layers = {n for i, n in enumerate(grid.layers) if grid.blended[i]} if grid.blended[grid.index(layer)] else {layer}
        self.dirty = None
        self.picture_box, self.last_picture = None, 0.0
        self.box = self.whole = None
        self.lacking, self.last_write = 0, 0.0
        self.last = None
        self._dab(context, event.mouse_region_x, event.mouse_region_y)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def _dab(self, context, x, y):
        wm = context.window_manager
        grid = self.store.grid
        wx, wy = world_at(self.frame, x, y)
        spacing = max(wm.fp_land_radius * 0.25, grid.cell)
        points = [(wx, wy)]
        if self.last is not None:       # dabs along the path, a quarter radius apart
            dx, dy = wx - self.last[0], wy - self.last[1]
            n = int((dx * dx + dy * dy) ** 0.5 / spacing)
            if n == 0:
                return
            points = [(self.last[0] + dx * i / n, self.last[1] + dy * i / n) for i in range(1, n + 1)]
        for px, py in points:
            region, amount = land_view.dab(grid, px, py, wm.fp_land_radius, wm.fp_land_falloff)
            if amount.size == 0:
                continue
            self.dirty = land_view.mark(grid, region, self.dirty)
            self.lacking += land_view.apply(grid, self.layer, region, amount, wm.fp_land_strength, self.mode)
            b = land_view.bounds(region)
            self.box = _union(self.box, b)
            self.whole = _union(self.whole, b)
            self.picture_box = _union(self.picture_box, b)
        self.last = (wx, wy)
        self._flush(force=False)

    def _flush(self, force):
        """The picture follows the brush closely, the pieces (and the viewport) a little behind."""
        now = time.time()
        if self.picture_box is not None and (force or now - self.last_picture >= PICTURE_EVERY):
            refresh_picture(self.picture_box)
            self.picture_box, self.last_picture = None, now
            redraw_all()
        if self.box is not None and (force or now - self.last_write >= WRITE_EVERY):
            self.store.write(self.store.touch(*self.box), self.layers, self.dirty)
            r0, c0, r1, c1 = self.box
            self.dirty[r0:r1, c0:c1] = False
            self.box, self.last_write = None, now

    def modal(self, context, event):
        x, y = event.mouse_region_x, event.mouse_region_y
        if event.type == 'MOUSEMOVE':
            LAND["hover"][context.area.as_pointer()] = (x, y)
            self._dab(context, x, y)
        elif event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            self._flush(force=True)
            self.store.keep()       # the stroke's data where Blender's undo step stores it
            LAND["readout"][context.area.as_pointer()] = readout(*world_at(self.frame, x, y))     # what it left
            LAND["message"] = ("%s isn't on part of the ground under the brush (%d cells left alone)" % (self.layer, self.lacking)
                               if self.lacking else "")
            self.ended(context)
            redraw_all()
            return {'FINISHED'}
        return {'RUNNING_MODAL'}

    def ended(self, context):
        """After the stroke (a scatter mask's changes nothing else)."""


class FP_OT_land_stroke(Stroke, bpy.types.Operator):
    bl_idname = "fp.land_stroke"
    bl_label = "Paint Layer"
    bl_options = {'INTERNAL', 'UNDO'}       # a step of Blender's undo: Ctrl+Z anywhere takes the stroke back
    mode: bpy.props.EnumProperty(items=MODES, options={'SKIP_SAVE'})

    def ended(self, context):
        """Auto update: the ground colour under the stroke re-baked once the strokes pause."""
        if self.whole is not None:
            from . import land_ground
            rect = land_ground.stroke_rect(self.store.grid, self.whole, context.window_manager.fp_land_radius)
            land_ground.after_stroke(context.scene, rect)


class FP_OT_land_bake(bpy.types.Operator):
    """Bake the ground's colour (what the landscapes write to the game's runtime virtual texture), which grass and
    rocks on it read. Again after repainting, or turn Auto update on"""
    bl_idname = "fp.land_bake"
    bl_label = "Bake Ground Colour"
    bl_options = {'INTERNAL', 'UNDO'}

    def execute(self, context):
        from . import land_ground
        done = land_ground.bake(context.scene)
        if not done.get("objects"):
            self.report({'WARNING'}, "Nothing to bake: no landscape here writes the ground colour")
            return {'CANCELLED'}
        self.report({'INFO'}, "Ground colour: %d object(s), %d×%d px in %.1f s" % (done["objects"], *done["size"],
                                                                                 done["ms"] / 1000.0))
        redraw_all()
        return {'FINISHED'}


class FP_OT_land_resize(bpy.types.Operator):
    """Brush radius (F) or strength (Shift+F) with the mouse; click or Enter confirms, Esc or right-click cancels"""
    bl_idname = "fp.land_resize"
    bl_label = "Brush Size"
    bl_options = {'INTERNAL'}
    what: bpy.props.EnumProperty(items=[('radius', "Radius", ""), ('strength', "Strength", "")], options={'SKIP_SAVE'})

    def invoke(self, context, event):
        store, frame = _ready(context)
        if store is None:
            return {'PASS_THROUGH'}
        wm = context.window_manager
        self.start_x = event.mouse_region_x
        self.before = wm.fp_land_radius if self.what == 'radius' else wm.fp_land_strength
        self.cells = store.grid.cell / frame.scale       # metres per pixel
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def _set(self, context, value):
        wm = context.window_manager
        if self.what == 'radius':
            wm.fp_land_radius = max(0.1, value)
        else:
            wm.fp_land_strength = min(max(value, 0.0), 1.0)
        context.area.header_text_set("%s %.2f   click / Enter: keep · Esc: cancel" % (self.what.title(), value))
        redraw_all()

    def modal(self, context, event):
        if event.type == 'MOUSEMOVE':
            dx = event.mouse_region_x - self.start_x
            self._set(context, self.before + dx * self.cells if self.what == 'radius' else self.before + dx / 200.0)
        elif event.type in {'LEFTMOUSE', 'RET', 'NUMPAD_ENTER', 'SPACE'} and event.value == 'PRESS':
            context.area.header_text_set(None)
            return {'FINISHED'}
        elif event.type in {'RIGHTMOUSE', 'ESC'} and event.value == 'PRESS':
            self._set(context, self.before)
            context.area.header_text_set(None)
            return {'CANCELLED'}
        return {'RUNNING_MODAL'}


class FP_OT_land_step(bpy.types.Operator):
    bl_idname = "fp.land_step"
    bl_label = "Brush Radius Step"
    bl_options = {'INTERNAL'}
    factor: bpy.props.FloatProperty(default=1.25)

    def invoke(self, context, event):
        if _ready(context)[0] is None:
            return {'PASS_THROUGH'}
        context.window_manager.fp_land_radius = max(0.1, context.window_manager.fp_land_radius * self.factor)
        redraw_all()
        return {'FINISHED'}


class FP_OT_land_zoom(bpy.types.Operator):
    bl_idname = "fp.land_zoom"
    bl_label = "Zoom"
    bl_options = {'INTERNAL'}
    steps: bpy.props.IntProperty(default=1)

    def invoke(self, context, event):
        store, frame = _ready(context)
        if store is None:
            return {'PASS_THROUGH'}
        frame.zoom_at(event.mouse_region_x, event.mouse_region_y, self.steps)
        context.region.tag_redraw()
        return {'FINISHED'}


class FP_OT_land_pan(bpy.types.Operator):
    bl_idname = "fp.land_pan"
    bl_label = "Pan"
    bl_options = {'INTERNAL'}

    def invoke(self, context, event):
        store, frame = _ready(context)
        if store is None:
            return {'PASS_THROUGH'}
        self.frame, self.last = frame, (event.mouse_region_x, event.mouse_region_y)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type == 'MOUSEMOVE':
            x, y = event.mouse_region_x, event.mouse_region_y
            self.frame.pan(x - self.last[0], y - self.last[1])
            self.last = (x, y)
            context.region.tag_redraw()
        elif event.type == 'MIDDLEMOUSE' and event.value == 'RELEASE':
            return {'FINISHED'}
        return {'RUNNING_MODAL'}


class FP_OT_land_fit(bpy.types.Operator):
    """Show the whole landscape (Home)"""
    bl_idname = "fp.land_fit"
    bl_label = "Fit"
    bl_options = {'INTERNAL'}

    def invoke(self, context, event):
        if _ready(context)[0] is None:
            return {'PASS_THROUGH'}
        LAND["views"].pop(context.area.as_pointer(), None)
        context.region.tag_redraw()
        return {'FINISHED'}


_GROUPS = []       # the dropdown's items, kept alive (Blender reads an enum callback's strings after it returns)


def _groups(self, context):
    from . import land_store
    found = land_store.groups(context.scene)
    _GROUPS[:] = [(n, "%s (%d)" % (n, len(p)), "") for n, p in sorted(found.items())] or [("", "No landscape", "")]
    return _GROUPS


class FP_OT_land_group(bpy.types.Operator):
    """Show this landscape"""
    bl_idname = "fp.land_group"
    bl_label = "Landscape"
    bl_options = {'INTERNAL'}
    name: bpy.props.EnumProperty(items=_groups)

    def execute(self, context):
        from . import land_store
        pieces = land_store.groups(context.scene).get(self.name)
        if not pieces:
            return {'CANCELLED'}
        if context.object is not None and context.object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        context.view_layer.objects.active = pieces[0]
        LAND["group"] = self.name
        redraw_all()
        return {'FINISHED'}


class FP_OT_land_layer(bpy.types.Operator):
    """Paint this layer (or biome)"""
    bl_idname = "fp.land_layer"
    bl_label = "Layer"
    bl_options = {'INTERNAL'}
    name: bpy.props.StringProperty()
    biome: bpy.props.BoolProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        from .land_tab import activate
        store = LAND["biomes"] if self.biome else LAND["layers"]
        if store is None or self.name not in store.grid.layers:
            return {'CANCELLED'}
        activate(store)
        context.window_manager.fp_land_layer = self.name
        redraw_all()
        return {'FINISHED'}


class FP_OT_land_show(bpy.types.Operator):
    """Show or hide this layer (or biome) in the picture and in the material (its paint is kept: shown again, it's
    back as it was)"""
    bl_idname = "fp.land_show"
    bl_label = "Show Layer"
    bl_options = {'INTERNAL', 'UNDO'}
    name: bpy.props.StringProperty()
    biome: bpy.props.BoolProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        from .land_tab import wanted_group
        store = LAND["biomes"] if self.biome else LAND["layers"]
        if store is None or self.name not in store.grid.layers:
            return {'CANCELLED'}
        hide = self.name not in LAND["hidden"]
        key = store.hide_key(self.name)
        for obj in wanted_group(context)[1]:        # the material reads it per mesh (an object attribute)
            if hide:
                obj[key] = 1.0
            elif key in obj:
                del obj[key]
            obj.update_tag()
        LAND["hidden"].symmetric_difference_update({self.name})
        LAND["dirty"] = True
        redraw_all()
        return {'FINISHED'}


class FP_OT_land_blend(bpy.types.Operator):
    """Linked: this layer shares 1 with the other linked layers (painting it lowers them, UE's weight blend).
Unlinked: it moves alone (a mask, as Fortnite's recent maps paint). Read from the landscape's weights; switch it here"""
    bl_idname = "fp.land_blend"
    bl_label = "Weight Blend"
    bl_options = {'INTERNAL'}
    name: bpy.props.StringProperty()

    def execute(self, context):
        store = LAND["layers"]
        if store is None or self.name not in store.grid.layers:
            return {'CANCELLED'}
        i = store.grid.index(self.name)
        store.grid.blended[i] = not store.grid.blended[i]
        LAND["blend_override"].setdefault(LAND["group"], {})[self.name] = bool(store.grid.blended[i])
        redraw_all()
        return {'FINISHED'}


class FP_OT_land_update(bpy.types.Operator):
    """Rebuild the landscape's material with the features changed in the Landscape tab (the FP app must be running)"""
    bl_idname = "fp.land_update"
    bl_label = "Update Material"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        from . import land_material, land_tab
        mat, piece, pieces = land_tab.shown_material(context)
        if mat is None:
            return {'CANCELLED'}
        try:
            land_material.update(piece, pieces)(context)
        except RuntimeError as e:
            self.report({'WARNING'}, str(e))
            return {'CANCELLED'}
        return {'FINISHED'}


classes = (FP_OT_land_stroke, FP_OT_land_bake, FP_OT_land_update, FP_OT_land_blend, FP_OT_land_resize, FP_OT_land_step, FP_OT_land_zoom, FP_OT_land_pan,
           FP_OT_land_fit, FP_OT_land_group, FP_OT_land_layer, FP_OT_land_show)
_keymaps = []


def register():
    for c in classes:
        bpy.utils.register_class(c)
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc is None:
        return
    add = host.adder(kc, _keymaps)
    items = []
    for mods, mode in (({}, 'paint'), ({"ctrl": True}, 'erase'), ({"shift": True}, 'smooth')):
        k = add(FP_OT_land_stroke.bl_idname, 'LEFTMOUSE', 'PRESS', **mods)
        k.properties.mode = mode
        items.append(k)
    for mods, what in (({}, 'radius'), ({"shift": True}, 'strength')):
        k = add(FP_OT_land_resize.bl_idname, 'F', 'PRESS', **mods)
        k.properties.what = what
        items.append(k)
    for key, factor in (('RIGHT_BRACKET', 1.25), ('LEFT_BRACKET', 0.8)):
        k = add(FP_OT_land_step.bl_idname, key, 'PRESS')
        k.properties.factor = factor
        items.append(k)
    for key, steps in (('WHEELUPMOUSE', 1), ('WHEELDOWNMOUSE', -1)):
        k = add(FP_OT_land_zoom.bl_idname, key, 'PRESS')
        k.properties.steps = steps
        items.append(k)
    items += [add(FP_OT_land_pan.bl_idname, 'MIDDLEMOUSE', 'PRESS'), add(FP_OT_land_fit.bl_idname, 'HOME', 'PRESS')]


def unregister():
    for km, k in _keymaps:
        try:
            km.keymap_items.remove(k)
        except (ReferenceError, RuntimeError):
            pass
    _keymaps.clear()
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
