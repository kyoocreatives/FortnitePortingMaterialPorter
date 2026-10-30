"""The Rig tab's input through the editor's "Fortnite" keymap. Both panes: click selects (shift adds/removes), drag on
empty space boxes, A / Alt+A all or none, I keys, the wheel zooms, middle mouse pans, Home fits. Body pane: double-click
selects a control and the controls under it, an IK/FK pill flips its limb. Face pane: drag a knob (a selected one drags
the selection; Shift held: fine, Ctrl: snap), G moves the selected knobs as Blender's grab does, Alt+G and double-click
reset. Each action is one undo step; moves key when auto-key is on."""
import bpy

from . import host, rig_layout, rig_source, rig_tab, tabs
from .rig_tab import RIG, armature, redraw_main, rig_of, usable, values_of

LOC = 'location'


def _view(context):
    if not tabs.is_tab(context, "rig"):
        return None
    return RIG["views"].get(context.area.as_pointer())


def _shown(context):
    """(view, armature) when the tab's last drawing is of the armature it shows now; else redraw and (None, None)."""
    view = _view(context)
    arm = armature(context)
    if not view or arm is None:
        return None, None
    if view.get("arm") != arm.name:     # drawn for another character: its hit areas don't apply
        context.region.tag_redraw()
        return None, None
    return view, arm


def _inside(box, x, y):
    return box is not None and box[0] <= x <= box[2] and box[1] <= y <= box[3]


def _pose_mode(context, arm):
    """The armature active in pose mode; False when it can't be (Blender says why in the status bar)."""
    layer = context.view_layer
    if not usable(arm, layer):
        return False
    try:
        if layer.objects.active is not arm:
            if context.object is not None and context.object.mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')
            layer.objects.active = arm
        arm.select_set(True)
        if arm.mode != 'POSE':
            bpy.ops.object.mode_set(mode='POSE')
    except RuntimeError:
        return False
    return True


def select(context, arm, names, extend=False, toggle=False):
    if not _pose_mode(context, arm):
        return False
    bones = arm.pose.bones
    if not extend:
        for pb in bones:
            pb.select = False
    for name in names:
        if name in bones:
            bones[name].select = not bones[name].select if toggle else True
    if names and names[0] in arm.data.bones and bones[names[0]].select:
        arm.data.bones.active = arm.data.bones[names[0]]
    redraw_main()
    return True


def _autokey(context, arm, bones):
    if context.scene.tool_settings.use_keyframe_insert_auto:
        for bone in bones:
            arm.pose.bones[bone].keyframe_insert(LOC, group=bone)


def _pane(view, x, y):
    """("body" or "face", its box) under (x, y), or (None, None)."""
    for name, key in (("body", "picker_box"), ("face", "board_box")):
        if _inside(view.get(key), x, y):
            return name, view[key]
    return None, None


def _button(view, x, y):
    """(id, kind, payload) of the view's button under (x, y): finger letters, joint ticks, the board's head switch."""
    for key, (r, kind, payload, _tip) in (view.get("buttons") or {}).items():
        if r.x <= x <= r.right and r.y <= y <= r.top:
            return key, kind, payload
    return None


def _switch(view, x, y):
    for name, (sx, sy, w, h) in (view.get("switches") or {}).items():
        if sx <= x <= sx + w and sy <= y <= sy + h:
            return name
    return None


def _selected_knobs(arm, face):
    return [b for b in face.knobs if b in arm.pose.bones and arm.pose.bones[b].select]


class KnobMove:
    """Knobs moved together by a pane offset from where they started, each within its range (mirror twins follow)."""

    def __init__(self, arm, face, bones, mirror):
        self.arm, self.face = arm, face
        self.bones = [b for b in bones if b in face.knobs]
        self.twins = {}
        if mirror:
            for b in self.bones:
                twin = rig_layout.mirror_of(b)
                if twin in face.knobs and twin not in self.bones:
                    self.twins[twin] = b
        self.before = {b: values_of(arm, b) for b in self.bones + list(self.twins)}
        self.start = {b: face.knob_at(b, self.before[b]) for b in self.bones}

    def update(self, dx, dy, snapped=False):
        face = self.face
        for b in self.bones:
            sx, sy = self.start[b]
            values = face.values_at(b, sx + dx, sy + dy)
            if snapped:
                values = {a.index: rig_layout.snap(values[a.index], a.lo, a.hi) for a in face.knobs[b].axes}
            rig_source.set_knob(self.arm, b, values)
        for twin, b in self.twins.items():
            axes = {a.index: a for a in face.knobs[twin].axes}
            rig_source.set_knob(self.arm, twin, {i: min(max(v, axes[i].lo), axes[i].hi)
                                                 for i, v in values_of(self.arm, b).items() if i in axes})

    def restore(self):
        for b, values in self.before.items():
            rig_source.set_knob(self.arm, b, values)

    def moved(self):
        return [b for b, v in self.before.items() if values_of(self.arm, b) != v]


class FP_OT_rig_press(bpy.types.Operator):
    bl_idname = "fp.rig_press"
    bl_label = "Rig Pick"
    bl_options = {'INTERNAL', 'UNDO'}
    extend: bpy.props.BoolProperty(options={'SKIP_SAVE'})       # shift: add/remove (SKIP_SAVE: a plain click must not reuse it)
    only_select: bpy.props.BoolProperty(options={'SKIP_SAVE'})  # ctrl: a knob is selected, not moved

    def invoke(self, context, event):
        view, arm = _shown(context)
        if not view:
            return {'PASS_THROUGH'}
        x, y = event.mouse_region_x, event.mouse_region_y
        self.arm, self.key, self.view = arm, context.area.as_pointer(), view
        button = _button(view, x, y)
        if button is not None:
            _key, kind, payload = button
            if kind == "part":
                context.window_manager.fp_rig_face_part = payload
                return {'FINISHED'}
            return {'FINISHED'} if select(context, arm, payload, extend=self.extend) else {'CANCELLED'}
        name = _switch(view, x, y)
        if name is not None:
            switch = next((s for s in rig_of(arm)["rig"].switches if s.name == name), None)
            if switch is None or not _pose_mode(context, arm):
                return {'CANCELLED'}
            rig_source.set_switch(arm, switch, not switch.is_ik)
            redraw_main()
            return {'FINISHED'}
        pane, box = _pane(view, x, y)
        if pane == "body":
            name = view["picker"].hit(x - box[0], y - box[1])
            if name is not None:
                select(context, arm, [name], extend=self.extend, toggle=self.extend)
                return {'FINISHED'}
        elif pane == "face":
            face = view["board"]
            bone = face.hit(x - box[0], y - box[1], {b: values_of(arm, b) for b in face.knobs})
            if bone is not None:
                if self.extend or self.only_select:
                    select(context, arm, [bone], extend=self.extend, toggle=self.extend)
                    return {'FINISHED'}
                picked = _selected_knobs(arm, face)
                if bone not in picked:      # a selected knob drags the whole selection along
                    if not select(context, arm, [bone], extend=False):
                        return {'CANCELLED'}
                    picked = [bone]
                elif not _pose_mode(context, arm):
                    return {'CANCELLED'}
                self.mode, self.bone = "drag", bone
                self.move = KnobMove(arm, face, picked, context.window_manager.fp_rig_mirror)
                self.last, self.total = (x, y), [0.0, 0.0]
                context.window_manager.modal_handler_add(self)
                return {'RUNNING_MODAL'}
        else:
            return {'PASS_THROUGH'}
        self.mode, self.pane, self.start = "box", pane, (x, y)
        RIG["box"][self.key] = (self.start, self.start)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        x, y = event.mouse_region_x, event.mouse_region_y
        if event.type == 'MOUSEMOVE':
            if self.mode == "box":
                RIG["box"][self.key] = (self.start, (x, y))
            else:       # Shift held: a tenth of the mouse's move
                gain = 0.1 if event.shift else 1.0
                self.total[0] += (x - self.last[0]) * gain
                self.total[1] += (y - self.last[1]) * gain
                self.last = (x, y)
                self.move.update(self.total[0], self.total[1], snapped=event.ctrl)
            context.region.tag_redraw()
        elif event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            if self.mode == "box":
                RIG["box"].pop(self.key, None)
                if abs(x - self.start[0]) + abs(y - self.start[1]) > 3:
                    select(context, self.arm, self._boxed(x, y), extend=self.extend)
                elif not self.extend:
                    select(context, self.arm, [], extend=False)
            else:       # keyed only when knobs moved; a plain click selects just that knob
                moved = self.move.moved()
                _autokey(context, self.arm, moved)
                if not moved and self.total == [0.0, 0.0]:
                    select(context, self.arm, [self.bone], extend=False)
            redraw_main()
            return {'FINISHED'}
        elif event.type in {'ESC', 'RIGHTMOUSE'}:
            RIG["box"].pop(self.key, None)
            if self.mode == "drag":
                self.move.restore()
            redraw_main()
            return {'CANCELLED'}
        return {'RUNNING_MODAL'}

    def _boxed(self, x, y):
        if self.pane == "body":
            box = self.view["picker_box"]
            return self.view["picker"].in_box(self.start[0] - box[0], self.start[1] - box[1], x - box[0], y - box[1])
        box, face = self.view["board_box"], self.view["board"]
        lo_x, hi_x = sorted((self.start[0] - box[0], x - box[0]))
        lo_y, hi_y = sorted((self.start[1] - box[1], y - box[1]))
        places = {b: face.knob_at(b, values_of(self.arm, b)) for b in face.knobs}
        return [b for b, (kx, ky) in places.items() if lo_x <= kx <= hi_x and lo_y <= ky <= hi_y]


class FP_OT_rig_grab(bpy.types.Operator):
    """Move the selected face knobs with the mouse (X/Y: across/up only, Shift: fine, Ctrl: tenths of their range)"""
    bl_idname = "fp.rig_grab"
    bl_label = "Move Knobs"
    bl_options = {'INTERNAL', 'UNDO'}

    def invoke(self, context, event):
        view, arm = _shown(context)
        x, y = event.mouse_region_x, event.mouse_region_y
        if not view or _pane(view, x, y)[0] != "face":
            return {'PASS_THROUGH'}
        face = view["board"]
        picked = _selected_knobs(arm, face)
        if not picked:
            self.report({'INFO'}, "Select face knobs first")
            return {'CANCELLED'}
        if not _pose_mode(context, arm):
            return {'CANCELLED'}
        self.arm, self.axis = arm, None
        self.move = KnobMove(arm, face, picked, context.window_manager.fp_rig_mirror)
        self.last, self.total = (x, y), [0.0, 0.0]
        self._status(context)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def _status(self, context):
        lock = {"X": "  along X", "Y": "  along Y"}.get(self.axis, "")
        context.area.header_text_set("Move %d knob%s%s   X/Y lock · Shift fine · Ctrl snap · Click/Enter confirm · Esc cancel"
                                     % (len(self.move.bones), "s" if len(self.move.bones) > 1 else "", lock))

    def _apply(self, event):
        dx, dy = self.total
        if self.axis == "X":
            dy = 0.0
        elif self.axis == "Y":
            dx = 0.0
        self.move.update(dx, dy, snapped=event.ctrl)

    def modal(self, context, event):
        x, y = event.mouse_region_x, event.mouse_region_y
        if event.type == 'MOUSEMOVE':
            gain = 0.1 if event.shift else 1.0
            self.total[0] += (x - self.last[0]) * gain
            self.total[1] += (y - self.last[1]) * gain
            self.last = (x, y)
            self._apply(event)
            redraw_main()
        elif event.type in {'X', 'Y'} and event.value == 'PRESS':
            self.axis = None if self.axis == event.type else event.type
            self._apply(event)
            self._status(context)
            redraw_main()
        elif event.type in {'LEFTMOUSE', 'RET', 'NUMPAD_ENTER', 'SPACE'} and event.value == 'PRESS':
            context.area.header_text_set(None)
            _autokey(context, self.arm, self.move.moved())
            redraw_main()
            return {'FINISHED'}
        elif event.type in {'RIGHTMOUSE', 'ESC'} and event.value == 'PRESS':
            context.area.header_text_set(None)
            self.move.restore()
            redraw_main()
            return {'CANCELLED'}
        return {'RUNNING_MODAL'}


class FP_OT_rig_knob_reset(bpy.types.Operator):
    """Reset the selected face knobs to rest (Alt+G)"""
    bl_idname = "fp.rig_knob_reset"
    bl_label = "Reset Knobs"
    bl_options = {'INTERNAL', 'UNDO'}

    def invoke(self, context, event):
        view, arm = _shown(context)
        if not view or _pane(view, event.mouse_region_x, event.mouse_region_y)[0] != "face":
            return {'PASS_THROUGH'}
        picked = _selected_knobs(arm, view["board"])
        for b in picked:
            rig_source.set_knob(arm, b, {i: 0.0 for i in values_of(arm, b)})
        _autokey(context, arm, picked)
        redraw_main()
        return {'FINISHED'}


class FP_OT_rig_select_all(bpy.types.Operator):
    """Select every control (or knob) of the pane under the mouse, or none"""
    bl_idname = "fp.rig_select_all"
    bl_label = "Select All"
    bl_options = {'INTERNAL', 'UNDO'}
    action: bpy.props.EnumProperty(items=[('SELECT', "Select", ""), ('DESELECT', "Deselect", "")])

    def invoke(self, context, event):
        view, arm = _shown(context)
        pane = _pane(view, event.mouse_region_x, event.mouse_region_y)[0] if view else None
        if pane is None:
            return {'PASS_THROUGH'}
        names = list(view["picker"].place) if pane == "body" else list(view["board"].knobs)
        if self.action == 'SELECT':
            select(context, arm, names, extend=True)
        elif _pose_mode(context, arm):
            for n in names:
                if n in arm.pose.bones:
                    arm.pose.bones[n].select = False
            redraw_main()
        return {'FINISHED'}


class FP_OT_rig_insert_key(bpy.types.Operator):
    """Key the selected controls and knobs (I)"""
    bl_idname = "fp.rig_insert_key"
    bl_label = "Insert Key"
    bl_options = {'INTERNAL'}

    def invoke(self, context, event):
        view, arm = _shown(context)
        if not view or _pane(view, event.mouse_region_x, event.mouse_region_y)[0] is None:
            return {'PASS_THROUGH'}
        bpy.ops.fp.rig_key(what='SELECTED')
        return {'FINISHED'}


class FP_OT_rig_locked(bpy.types.Operator):
    bl_idname = "fp.rig_locked"
    bl_label = "Rotate/Scale Knobs"
    bl_options = {'INTERNAL'}

    def invoke(self, context, event):
        view, arm = _shown(context)
        if not view or _pane(view, event.mouse_region_x, event.mouse_region_y)[0] != "face":
            return {'PASS_THROUGH'}
        self.report({'INFO'}, "Face knobs only move (G): Epic's board locks their rotation and scale")
        return {'CANCELLED'}


class FP_OT_rig_zoom(bpy.types.Operator):
    bl_idname = "fp.rig_zoom"
    bl_label = "Zoom Pane"
    bl_options = {'INTERNAL'}
    steps: bpy.props.IntProperty(default=1)

    def invoke(self, context, event):
        view = _view(context)
        x, y = event.mouse_region_x, event.mouse_region_y
        pane, box = _pane(view, x, y) if view else (None, None)
        if pane is None:
            return {'PASS_THROUGH'}
        pv = RIG["panes"].setdefault((context.area.as_pointer(), pane), rig_layout.PaneView())
        pv.zoom_at(x - box[0], y - box[1], (box[2] - box[0]) / 2, (box[3] - box[1]) / 2, self.steps)
        context.region.tag_redraw()
        return {'FINISHED'}


class FP_OT_rig_pan(bpy.types.Operator):
    bl_idname = "fp.rig_pan"
    bl_label = "Pan Pane"
    bl_options = {'INTERNAL'}

    def invoke(self, context, event):
        view = _view(context)
        x, y = event.mouse_region_x, event.mouse_region_y
        pane = _pane(view, x, y)[0] if view else None
        if pane is None:
            return {'PASS_THROUGH'}
        self.pv = RIG["panes"].setdefault((context.area.as_pointer(), pane), rig_layout.PaneView())
        self.last = (x, y)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type == 'MOUSEMOVE':
            x, y = event.mouse_region_x, event.mouse_region_y
            self.pv.pan(x - self.last[0], y - self.last[1])
            self.last = (x, y)
            context.region.tag_redraw()
        elif event.type == 'MIDDLEMOUSE' and event.value == 'RELEASE':
            return {'FINISHED'}
        return {'RUNNING_MODAL'}


class FP_OT_rig_fit(bpy.types.Operator):
    """Show the whole pane under the mouse (Home)"""
    bl_idname = "fp.rig_fit"
    bl_label = "Fit Pane"
    bl_options = {'INTERNAL'}

    def invoke(self, context, event):
        view = _view(context)
        pane = _pane(view, event.mouse_region_x, event.mouse_region_y)[0] if view else None
        if pane is None:
            return {'PASS_THROUGH'}
        RIG["panes"].pop((context.area.as_pointer(), pane), None)
        context.region.tag_redraw()
        return {'FINISHED'}


class FP_OT_rig_double(bpy.types.Operator):
    bl_idname = "fp.rig_double"
    bl_label = "Rig Double-Click"
    bl_options = {'INTERNAL', 'UNDO'}

    def invoke(self, context, event):
        view, arm = _shown(context)
        if not view:
            return {'PASS_THROUGH'}
        x, y = event.mouse_region_x, event.mouse_region_y
        box = view.get("picker_box")
        if _inside(box, x, y):
            name = view["picker"].hit(x - box[0], y - box[1])
            if name is None:
                return {'PASS_THROUGH'}
            rig = rig_of(arm)["rig"]
            under, grew = {name}, True
            while grew:       # the control and every control under it
                more = {c.name for c in rig.controls if c.chain in under} - under
                grew, under = bool(more), under | more
            select(context, arm, [name] + sorted((under - {name}) & set(view["picker"].place)), extend=event.shift)
            return {'FINISHED'}
        box = view.get("board_box")
        if _inside(box, x, y):
            face = view["board"]
            bone = face.hit(x - box[0], y - box[1], {b: values_of(arm, b) for b in face.knobs})
            if bone is None:
                return {'PASS_THROUGH'}
            twin = rig_layout.mirror_of(bone) if context.window_manager.fp_rig_mirror else None
            done = [b for b in (bone, twin) if b in face.knobs]
            for b in done:
                rig_source.set_knob(arm, b, {i: 0.0 for i in values_of(arm, b)})
            _autokey(context, arm, done)
            redraw_main()
            return {'FINISHED'}
        return {'PASS_THROUGH'}


def hover(context, x, y):
    """The control or knob under (x, y) (None: off the panes) named by the view; True when that changed."""
    found = None
    view, arm = _shown(context) if x is not None else (None, None)
    pane, box = _pane(view, x, y) if view else (None, None)
    button = _button(view, x, y) if view else None
    switch = _switch(view, x, y) if view else None
    if button is not None:
        found = ("button", button[0])
    elif switch is not None:
        found = ("switch", switch)
    elif pane == "body":
        name = view["picker"].hit(x - box[0], y - box[1])
        found = ("body", name) if name else None
    elif pane == "face":
        face = view["board"]
        bone = face.hit(x - box[0], y - box[1], {b: values_of(arm, b) for b in face.knobs})
        found = ("face", bone) if bone else None
    key = context.area.as_pointer()
    if found == RIG["hover"].get(key):
        return False
    RIG["hover"][key] = found
    return True


def _controls(arm):
    entry = rig_of(arm)
    rig = entry["rig"]
    return [c.name for c in rig.controls], [k.bone for k in rig.board.knobs] if rig.board else []


class FP_OT_rig_reset(bpy.types.Operator):
    """Reset the picked controls (or every control and knob) to their rest pose"""
    bl_idname = "fp.rig_reset"
    bl_label = "Reset Pose"
    bl_options = {'REGISTER', 'UNDO'}
    scope: bpy.props.EnumProperty(items=[('SELECTED', "Selected", ""), ('ALL', "All", "")])

    @classmethod
    def poll(cls, context):
        return armature(context) is not None

    def execute(self, context):
        arm = armature(context)
        body, knobs = _controls(arm)
        names = body + knobs
        if self.scope == 'SELECTED':
            names = [n for n in names if n in arm.pose.bones and arm.pose.bones[n].select]
        for n in names:
            pb = arm.pose.bones.get(n)
            if pb is not None:
                pb.location = (0.0, 0.0, 0.0)
                pb.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
                pb.rotation_euler = (0.0, 0.0, 0.0)
                pb.rotation_axis_angle = (0.0, 0.0, 1.0, 0.0)
                pb.scale = (1.0, 1.0, 1.0)
        redraw_main()
        return {'FINISHED'}


def key_switches(arm):
    """Key every IK/FK switch at this frame, stepped: a later flip then leaves the frames keyed before it alone."""
    rig = rig_of(arm)["rig"]
    paths = {p for p in (rig_source.switch_path(arm, s) for s in rig_source.switches(arm, rig.kind)) if p}
    done = {p for p in paths if arm.keyframe_insert(p, group="IK/FK")}
    for curve in rig_tab.curves(arm):
        if curve.data_path in done:
            for point in curve.keyframe_points:
                point.interpolation = 'CONSTANT'
    return len(done)


class FP_OT_rig_key(bpy.types.Operator):
    """Key the picked controls and the IK/FK switches (Key), or every face knob (Key Face), at this frame"""
    bl_idname = "fp.rig_key"
    bl_label = "Key"
    bl_options = {'REGISTER', 'UNDO'}
    what: bpy.props.EnumProperty(items=[('SELECTED', "Selected", ""), ('FACE', "Face", "")])

    @classmethod
    def poll(cls, context):
        return armature(context) is not None

    def execute(self, context):
        arm = armature(context)
        body, knobs = _controls(arm)
        if self.what == 'FACE':
            names, paths = knobs, (LOC,)
        else:
            names = [n for n in body + knobs if n in arm.pose.bones and arm.pose.bones[n].select]
            paths = (LOC, "rotation", "scale")
        for n in names:
            pb = arm.pose.bones.get(n)
            if pb is None:
                continue
            for path in paths:
                if path == "rotation":
                    path = {'QUATERNION': "rotation_quaternion", 'AXIS_ANGLE': "rotation_axis_angle"}.get(pb.rotation_mode, "rotation_euler")
                pb.keyframe_insert(path, group=n)
        switched = key_switches(arm) if self.what == 'SELECTED' else 0
        self.report({'INFO'}, "Keyed %d controls" % len(names) + (" and %d IK/FK switches" % switched if switched else ""))
        return {'FINISHED'}


_ARMATURES = []    # the dropdown's items, kept alive (Blender reads an enum callback's strings after it returns)


def _armatures(self, context):
    _ARMATURES[:] = [(o.name, o.name, "") for o in rig_tab.rigged(context)] or [("", "No rigged character", "")]
    return _ARMATURES


class FP_OT_rig_use(bpy.types.Operator):
    """Show this character"""
    bl_idname = "fp.rig_use"
    bl_label = "Character"
    bl_options = {'INTERNAL'}
    name: bpy.props.EnumProperty(items=_armatures)

    def execute(self, context):
        arm = bpy.data.objects.get(self.name)
        if arm is None:
            return {'CANCELLED'}
        RIG["arm"] = arm.name
        if context.object is not None and context.object.mode != 'OBJECT' and context.object is not arm:
            bpy.ops.object.mode_set(mode='OBJECT')
        context.view_layer.objects.active = arm
        redraw_main()
        return {'FINISHED'}


classes = (FP_OT_rig_press, FP_OT_rig_grab, FP_OT_rig_knob_reset, FP_OT_rig_select_all, FP_OT_rig_insert_key, FP_OT_rig_locked,
           FP_OT_rig_zoom, FP_OT_rig_pan, FP_OT_rig_fit, FP_OT_rig_double, FP_OT_rig_reset, FP_OT_rig_key,
           FP_OT_rig_use)
_keymaps = []


def register():
    for c in classes:
        bpy.utils.register_class(c)
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc is None:
        return
    add = host.adder(kc, _keymaps)
    items = [add(FP_OT_rig_press.bl_idname, 'LEFTMOUSE', 'PRESS'), add(FP_OT_rig_double.bl_idname, 'LEFTMOUSE', 'DOUBLE_CLICK'),
             add(FP_OT_rig_double.bl_idname, 'LEFTMOUSE', 'DOUBLE_CLICK', shift=True)]
    k = add(FP_OT_rig_press.bl_idname, 'LEFTMOUSE', 'PRESS', shift=True)
    k.properties.extend = True
    items.append(k)
    k = add(FP_OT_rig_press.bl_idname, 'LEFTMOUSE', 'PRESS', ctrl=True)
    k.properties.only_select = True
    items.append(k)
    items += [add(FP_OT_rig_grab.bl_idname, 'G', 'PRESS'), add(FP_OT_rig_knob_reset.bl_idname, 'G', 'PRESS', alt=True),
              add(FP_OT_rig_insert_key.bl_idname, 'I', 'PRESS'), add(FP_OT_rig_locked.bl_idname, 'R', 'PRESS'),
              add(FP_OT_rig_locked.bl_idname, 'S', 'PRESS'), add(FP_OT_rig_pan.bl_idname, 'MIDDLEMOUSE', 'PRESS'),
              add(FP_OT_rig_fit.bl_idname, 'HOME', 'PRESS')]
    for action, alt in (('SELECT', False), ('DESELECT', True)):
        k = add(FP_OT_rig_select_all.bl_idname, 'A', 'PRESS', alt=alt)
        k.properties.action = action
        items.append(k)
    for key, steps in (('WHEELUPMOUSE', 1), ('WHEELDOWNMOUSE', -1)):
        k = add(FP_OT_rig_zoom.bl_idname, key, 'PRESS')
        k.properties.steps = steps
        items.append(k)


def unregister():
    for km, k in _keymaps:
        try:
            km.keymap_items.remove(k)
        except (ReferenceError, RuntimeError):
            pass
    _keymaps.clear()
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
