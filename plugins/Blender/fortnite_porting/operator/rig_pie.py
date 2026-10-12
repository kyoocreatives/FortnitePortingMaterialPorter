"""Shift+Q pie in Pose Mode for the official rig (as FortRig's): IK / FK, reset, the rig's switches, the picker.
Blender closes a pie after every click, so the operator opening it waits under it and opens it again at the same
place when the click changed something (not after picking a control: you then want to move it)."""
import bpy

from ..processing.context import deform_rig, dynamics_bake, face_board, rig_style

PIE = "FPMP_MT_rig_pie"
OFFICIAL = "fpmp_official_rig"
# the pie opens again after these
REOPEN_AFTER = {"FPMP_OT_official_rig_switch", "FPMP_OT_official_rig_setting", "FPMP_OT_rig_reset_pose",
                "FPMP_OT_official_rig_on", "FPMP_OT_official_rig_body_controls", "FPMP_OT_deform_rig_on",
                "FPMP_OT_dynamics_on", "FPMP_OT_dynamics_live"}
# events that can reach the waiting operator while the pie is still open
STILL_OPEN = {"TIMER", "TIMER_REPORT", "TIMERREGION", "NONE", "WINDOW_DEACTIVATE", "INBETWEEN_MOUSEMOVE"}


def _rig(context):
    obj = context.active_object
    return obj if obj is not None and obj.type == 'ARMATURE' and obj.data.get(OFFICIAL) else None


# A pie puts west and east between the diagonals, about a row apart: those stay one row high; north and south
# grow away from the centre and carry the boxes. Columns there are not aligned (FortRig: aligned rows squeeze).

def _reset(pie):
    row = pie.row(align=True)
    row.ui_units_x, row.scale_y = 9, 1.3
    row.operator("fpmp.rig_reset_pose", text="Reset Selected", icon='LOOP_BACK').scope = 'SELECTED'
    row.operator("fpmp.rig_reset_pose", text="All").scope = 'ALL'


def _rig_row(pie, obj):
    from ..processing.context import official_panel
    row = pie.row(align=True)
    row.ui_units_x, row.scale_y = 10, 1.3
    official_panel.ui_top(row, obj)


def _ikfk(pie, obj):
    from ..processing.context import official_panel
    box = pie.box()
    box.ui_units_x = 15
    box.label(text="IK / FK", icon='CON_KINEMATIC')
    col = box.column()
    for label, held in official_panel.groups(obj):
        official_panel._row(col, obj, label, held)


def _hands(pie, obj):
    from ..processing.context import official_panel
    if not official_panel.has_hands(obj):
        pie.separator()
        return
    box = pie.box()
    box.ui_units_x = 13
    box.label(text="Held Props", icon='VIEW_PAN')
    official_panel.ui_hands(box, obj)


def _select(pie):
    col = pie.column(align=True)
    col.ui_units_x, col.scale_y = 9, 1.2
    col.operator("wm.call_panel", text="Picker…", icon='RESTRICT_SELECT_OFF').name = FPMP_PT_rig_picker_popup.__name__
    col.operator("fpmp.rig_select_controls", text="Select All Controls", icon='GROUP_BONE')


def _show(pie, obj):
    from ..processing.context import official_panel
    box = pie.box()
    box.ui_units_x = 18
    grid = box.grid_flow(row_major=True, columns=3, even_columns=True, align=True)     # two rows: clear of north
    for group in [c for c in obj.data.collections if c.name in rig_style.COLLECTIONS + (face_board.GROUP,)]:
        grid.prop(group, "is_visible", text=group.name, toggle=True)
    official_panel.ui_body(grid, obj)


def _simulation(pie, obj):
    if not (obj.data.get(deform_rig.MARK) or obj.data.get(dynamics_bake.KEY)):
        pie.separator()
        return
    col = pie.column(align=True)
    col.ui_units_x = 11
    deform_rig.ui(col, obj)
    dynamics_bake.ui(col, obj)


def _face(pie, obj):
    if face_board.BOARD in obj.pose.bones or obj.data.get("fpmp_metahuman_board"):
        pie.prop(obj, "fpmp_face_board", text="Face Board", toggle=True, icon='USER')
    else:
        pie.separator()


class FPMP_MT_rig_pie(bpy.types.Menu):
    bl_idname = PIE
    bl_label = "Rig"

    @classmethod
    def poll(cls, context):
        return _rig(context) is not None

    def draw(self, context):
        obj = _rig(context)
        pie = self.layout.menu_pie()
        # slot order: west, east, south, north, north-west, north-east, south-west, south-east
        _reset(pie)
        _rig_row(pie, obj)
        _ikfk(pie, obj)
        _hands(pie, obj)
        _select(pie)
        _show(pie, obj)
        _simulation(pie, obj)
        _face(pie, obj)


class FPMP_PT_rig_picker_popup(bpy.types.Panel):
    """The Picker as a popup (from the pie)."""
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'HEADER'
    bl_label = "Picker"
    bl_ui_units_x = 13

    @classmethod
    def poll(cls, context):
        return _rig(context) is not None

    def draw(self, context):
        from ..processing.context import official_picker
        official_picker.ui_picker(self.layout, _rig(context))


def _last_op(context):
    ops = context.window_manager.operators
    return (ops[-1].as_pointer(), ops[-1].bl_idname) if len(ops) else (0, "")


def _values(obj):
    """What the pie's toggles show without an operator (changing them runs none)."""
    shown = [c.is_visible for c in obj.data.collections if c.name in rig_style.COLLECTIONS + (face_board.GROUP,)]
    return tuple(shown) + (bool(getattr(obj, "fpmp_face_board", False)),)


class FPMP_OT_rig_pie(bpy.types.Operator):
    bl_idname = "fpmp.rig_pie"
    bl_label = "Rig Pie"
    bl_description = "Open the official rig's posing pie"

    _active = None                                   # the one waiting under an open pie

    @classmethod
    def poll(cls, context):
        return _rig(context) is not None

    def invoke(self, context, event):
        self.obj_name = _rig(context).name
        self.center = (event.mouse_x, event.mouse_y)
        self.reopened = 0
        FPMP_OT_rig_pie._active = self               # an older one still waiting gives up
        # wait under the pie: it takes every event while open, so the first one reaching this operator means it's gone
        context.window_manager.modal_handler_add(self)
        self.open(context)
        return {'RUNNING_MODAL'}

    def open(self, context):
        obj = bpy.data.objects.get(self.obj_name)
        self.before = (_last_op(context)[0], _values(obj) if obj else ())
        bpy.ops.wm.call_menu_pie('INVOKE_DEFAULT', name=PIE)

    def reopen_wanted(self, context, event):
        if self.reopened >= 200 or event.type in {'ESC', 'RIGHTMOUSE'}:
            return False
        obj = bpy.data.objects.get(self.obj_name)
        if obj is None or _rig(context) != obj:
            return False
        ptr, name = _last_op(context)
        if ptr and ptr != self.before[0]:
            return name in REOPEN_AFTER
        return _values(obj) != self.before[1]       # a toggle changed (a property change runs no operator)

    def modal(self, context, event):
        if FPMP_OT_rig_pie._active is not self:
            return {'FINISHED', 'PASS_THROUGH'}          # a newer pie took over
        if event.type in STILL_OPEN or event.value == 'DOUBLE_CLICK':
            return {'PASS_THROUGH'}
        if self.reopen_wanted(context, event):
            self.reopened += 1
            context.window.cursor_warp(*self.center)     # the pie opens where it was
            self.open(context)
            return {'RUNNING_MODAL'}
        FPMP_OT_rig_pie._active = None
        return {'FINISHED', 'PASS_THROUGH'}


classes = (FPMP_MT_rig_pie, FPMP_PT_rig_picker_popup, FPMP_OT_rig_pie)
_keymaps = []


def _add_key():
    """The Shift+Q item, last in the add-on Pose keymap. Blender tries add-on items last-added first, and FortRig's
    Shift+Q pie opens on any armature (offering to build its rig): this one only on the official rig, so it must be
    tried first. FortRig registers after this plugin (extensions load after startup scripts): moved once loading ends."""
    _remove_keys()
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc is None:                                 # background mode has no keymaps
        return None
    km = kc.keymaps.new(name="Pose", space_type='EMPTY')
    _keymaps.append((km, km.keymap_items.new(FPMP_OT_rig_pie.bl_idname, 'Q', 'PRESS', shift=True)))
    return None


def _remove_keys():
    for km, kmi in _keymaps:
        try:
            km.keymap_items.remove(kmi)
        except (ReferenceError, RuntimeError):
            pass
    _keymaps.clear()


def register():
    for c in classes:
        bpy.utils.register_class(c)
    _add_key()
    bpy.app.timers.register(_add_key, first_interval=0.5)


def unregister():
    if bpy.app.timers.is_registered(_add_key):
        bpy.app.timers.unregister(_add_key)
    _remove_keys()
    FPMP_OT_rig_pie._active = None
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
