"""FP Material Fixer panel (from the fpisland extension), in the Fortnite Porting tab.

Fix FP Materials wires textures FP left unlinked (fixer.py). Recover Island Textures (owner builds
only) fetches a UEFN map's missing textures and flat colours from the app (island.py).
Both work on the whole file or only the selected objects.
"""
import threading
import time
import types

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, PointerProperty, StringProperty

from . import fixer, island
from ...material_porter import hook
from ...material_porter.app_client import AppClient, AppError

REPORT_TEXT = 'FP Fixer Report'
RETRY = 30.0        # seconds between asks while the app is closed
# App capabilities (fork-caps), None until the app answered. Island recovery is shown only when the app supports it.
_caps = {'islands': None}
_probe = {'thread': None, 'stop': None}
_running = False


def islands_available():
    return bool(_caps['islands'])


def _ask(stop):
    """Worker thread: ask the app until it answers (it may open after Blender)."""
    while not stop.is_set():
        try:
            _caps['islands'] = bool(AppClient(hook.URL).get('fork-caps', '').get('islands'))
            return
        except AppError:
            stop.wait(RETRY)


def _redraw_when_known():
    if _caps['islands'] is None:
        thread = _probe['thread']
        return 1.0 if thread is not None and thread.is_alive() else None
    tag_redraw(bpy.context)
    return None


def start_probe():
    """Ask the app for its capabilities off the main thread, so no draw waits on the network."""
    if _probe['thread'] is not None and _probe['thread'].is_alive():
        return
    stop = threading.Event()
    _probe['thread'] = threading.Thread(target=_ask, args=(stop,), name='fpmp-fork-caps', daemon=True)
    _probe['stop'] = stop
    _probe['thread'].start()
    if not bpy.app.timers.is_registered(_redraw_when_known):
        bpy.app.timers.register(_redraw_when_known, first_interval=1.0)


def stop_probe():
    if _probe['stop'] is not None:
        _probe['stop'].set()
    if _probe['thread'] is not None:
        _probe['thread'].join(timeout=5)
    _probe['thread'] = _probe['stop'] = None


def scope_materials(context, scope):
    if scope == 'ALL':
        return [m for m in bpy.data.materials if m.node_tree and not m.library]
    mats, seen = [], set()

    def add_obj(o):
        for s in getattr(o, 'material_slots', []):
            if s.material and s.material.name not in seen:
                seen.add(s.material.name)
                mats.append(s.material)
        if o.instance_type == 'COLLECTION' and o.instance_collection:
            for co in o.instance_collection.all_objects:
                add_obj(co)

    for o in context.selected_objects:
        add_obj(o)
    return mats


def write_report(title, sections):
    txt = bpy.data.texts.get(REPORT_TEXT) or bpy.data.texts.new(REPORT_TEXT)
    txt.clear()
    txt.write(f"{title}   ({time.strftime('%Y-%m-%d %H:%M')})\n\n")
    for head, lines in sections:
        if not lines:
            continue
        txt.write(f"== {head} ({len(lines)})\n")
        for line in lines:
            txt.write(f"  {line}\n")
        txt.write("\n")


def tag_redraw(context):
    for w in context.window_manager.windows:
        for a in w.screen.areas:
            if a.type in ('VIEW_3D', 'NODE_EDITOR'):
                a.tag_redraw()


class FPMP_FixerProps(bpy.types.PropertyGroup):
    scope: EnumProperty(name='Scope', items=[
        ('ALL', 'All Materials', 'Every material in the file'),
        ('SELECTED', 'Selected Objects', 'Only the materials of the selected objects (and of selected collection instances)'),
    ], default='ALL')
    preview: BoolProperty(name='Preview Only', default=False, description='Only write the report, change nothing')
    apply_colors: BoolProperty(name='Apply Flat Colours', default=True,
                               description='Materials that are plain colours in UE get their colour, roughness, glass opacity and light colour')
    status: StringProperty(default='')
    progress: FloatProperty(default=0.0, min=0.0, max=1.0, subtype='FACTOR')


def scope_objects(context, scope):
    """All objects, or the selected ones plus their collection instances' objects."""
    if scope == 'ALL':
        return list(bpy.data.objects)
    objs, seen = [], set()

    def add(o):
        if o.name in seen:
            return
        seen.add(o.name)
        objs.append(o)
        if o.instance_type == 'COLLECTION' and o.instance_collection:
            for co in o.instance_collection.all_objects:
                add(co)

    for o in context.selected_objects:
        add(o)
    return objs


def rebuild_fallbacks(context, scope, dry):
    """Rebuild island materials made by an older fallback builder (mp_fallback < REVISION). Returns (count, notes)."""
    from ...material_porter import fallback
    users = {}
    for o in scope_objects(context, scope):
        for i, s in enumerate(getattr(o, 'material_slots', [])):
            m = s.material
            if m is not None and m.get('mp_fallback') and m.get('mp_fallback') < fallback.REVISION and m.get('mp_path'):
                users.setdefault(m, []).append((o, i))
    if dry or not users:
        return len(users), []
    job = types.SimpleNamespace(options={'RimLight': False}, type=types.SimpleNamespace(name='WORLD'))
    done, notes = 0, []
    for old, slots in users.items():
        new = hook.build_exact(job, {'Path': old['mp_path'], 'Name': old.get('OriginalName', old.name)}, obj=slots[0][0])
        if new is None or new is old:
            notes.append(f"{old.name}: not rebuilt (is the FP app open with the island loaded?)")
            continue
        for key in ('Hash', 'OriginalName', 'MPRimLight', 'MPSubsurface'):
            if key in old:
                new[key] = old[key]
        for o, i in slots:
            o.material_slots[i].material = new
        name = old.name
        if old.users == 0:
            bpy.data.materials.remove(old)
            new.name = name
        done += 1
        notes.append(f"{name}: rebuilt")
    return done, notes


class FPMP_OT_FixMaterials(bpy.types.Operator):
    bl_idname = 'fpmp.fix_materials'
    bl_label = 'Fix FP Materials'
    bl_description = ('Wire textures FortnitePorting left unlinked (BC/ORM/RMA/SRM/Unity masks...), fix foliage alpha, '
                      'wrong links and colour spaces, and rebuild the fork\'s older island materials (with the app open). '
                      'Only empty inputs are filled')
    bl_options = {'REGISTER', 'UNDO'}

    ask: BoolProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def invoke(self, context, event):
        if self.ask:
            return context.window_manager.invoke_props_dialog(self)
        return self.execute(context)

    def draw(self, context):
        props = context.scene.fpmp_fixer
        self.layout.row().prop(props, 'scope', expand=True)
        self.layout.prop(props, 'preview')

    def execute(self, context):
        props = context.scene.fpmp_fixer
        run = fixer.fix_materials(scope_materials(context, props.scope), dry=props.preview)
        rebuilt, notes = rebuild_fallbacks(context, props.scope, props.preview)
        write_report('Fix FP Materials' + (' (preview)' if props.preview else ''), [
            ('Material fixes', [f"{m}: {msg}" for m, msgs in run.report.items() for msg in msgs]),
            ('Colour spaces', [f"{n}: {a} -> {b}" for n, a, b in run.cs_changes]),
            ("The fork's island materials", notes if notes else ([f"{rebuilt} would be rebuilt"] if props.preview and rebuilt else []))])
        n = len([m for m, v in run.report.items() if v])
        # one line in the sidebar: counts first, the preview marked
        props.status = f"{n} materials, {len(run.cs_changes)} colour spaces" + (
            f", {rebuilt} island materials" if rebuilt else "") + (" (preview)" if props.preview else "")
        self.report({'INFO'}, f"FP Fixer {'would change' if props.preview else 'changed'}: {props.status}. "
                              f"Details in Text Editor > '{REPORT_TEXT}'")
        return {'FINISHED'}


class FPMP_OT_RecoverIsland(bpy.types.Operator):
    bl_idname = 'fpmp.recover_island'
    bl_label = 'Recover Island Textures'
    bl_description = ("For a UEFN map's materials FortnitePorting imported without a base colour: find the island "
                      "material each was made from, wire the textures it really references and set its flat colours "
                      "(the FP app must be open with the island loaded). Esc cancels")
    bl_options = {'REGISTER', 'UNDO'}

    ask: BoolProperty(options={'HIDDEN', 'SKIP_SAVE'})
    _timer = None

    @classmethod
    def poll(cls, context):
        return not _running

    def draw(self, context):
        props = context.scene.fpmp_fixer
        self.layout.row().prop(props, 'scope', expand=True)
        self.layout.prop(props, 'apply_colors')

    def execute(self, context):
        # the dialog's OK: the run itself is modal, started as from a button
        bpy.ops.fpmp.recover_island('INVOKE_DEFAULT')
        return {'FINISHED'}

    def invoke(self, context, event):
        global _running
        if self.ask:
            return context.window_manager.invoke_props_dialog(self)
        props = context.scene.fpmp_fixer
        targets = island.build_targets(scope_materials(context, props.scope))
        if not targets:
            self.report({'INFO'}, 'Nothing to recover: every FP material in scope already has a base colour')
            return {'CANCELLED'}
        self.app = AppClient(hook.URL)
        try:
            _caps['islands'] = bool(self.app.get('fork-caps', '').get('islands'))
        except AppError as e:
            self.report({'ERROR'}, "The FP app isn't answering: open FortnitePorting MP with the island loaded (%s)" % e)
            return {'CANCELLED'}
        if not _caps['islands']:
            self.report({'ERROR'}, "Island texture recovery is only in the owner's builds")
            return {'CANCELLED'}
        self.n_targets = len(targets)
        self.steps = island.resolve(self.app, targets)
        self.records = None
        props.status = f'Running: {self.n_targets} materials'
        props.progress = 0.0
        _running = True
        self._timer = context.window_manager.event_timer_add(0.05, window=context.window)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        props = context.scene.fpmp_fixer
        if event.type == 'ESC':
            self._finish(context)
            props.status = 'Cancelled'
            return {'CANCELLED'}
        if event.type != 'TIMER':
            return {'PASS_THROUGH'}
        # Work in ~0.15 s slices per timer tick so the UI stays responsive.
        until = time.perf_counter() + 0.15
        try:
            while time.perf_counter() < until:
                done, total, label = next(self.steps)
                props.progress = done / max(1, total)
                props.status = f'Running: {done}/{total} {label}'
        except StopIteration as stop:
            self.records = stop.value
        except Exception as e:
            self._finish(context)
            props.status = 'Failed, see report'
            write_report('Recover Island Textures: FAILED', [('Error', [str(e)])])
            self.report({'ERROR'}, "Island recovery failed: %s" % e)
            return {'CANCELLED'}
        tag_redraw(context)
        if self.records is None:
            return {'RUNNING_MODAL'}
        self._finish(context)
        return self._apply(context)

    def _finish(self, context):
        global _running
        _running = False
        if self._timer:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None

    def _apply(self, context):
        props = context.scene.fpmp_fixer
        records = self.records
        resolved = island.resolved_records(records)
        problems = []
        added, run = island.apply_textures(self.app, resolved, problems)
        color_log = island.apply_colors(resolved) if props.apply_colors else []
        done = {x['mat'] for x in resolved}
        unresolved = [f"{r['mat']}: {r['status']}" + (f" ({len(r.get('candidates', []))} same-named materials)"
                                                      if r.get('candidates') else '')
                      for r in records if r['mat'] not in done]
        n_tex = len({t['path'] for r in resolved for t in r.get('textures', [])})
        write_report('Recover Island Textures', [
            ('Texture wiring', [f"{m}: {msg}" for m, msgs in run.report.items() for msg in msgs]),
            ('Flat colours / params', color_log),
            ('Colour spaces', [f"{n}: {a} -> {b}" for n, a, b in run.cs_changes]),
            ('Textures the app could not export', problems),
            ('Not resolved (per-object overrides, or an island not loaded in the app)', unresolved),
        ])
        props.progress = 1.0
        props.status = (f"{len(resolved)}/{len(records)} materials found, {n_tex} textures, "
                        f"{added} nodes added, {len(color_log)} colour fixes")
        self.report({'INFO'}, f"FP Fixer: {props.status}. Details in Text Editor > '{REPORT_TEXT}'")
        tag_redraw(context)
        return {'FINISHED'}

    def cancel(self, context):
        self._finish(context)


def _checkbox(layout, props, name):
    col = layout.column()
    col.use_property_split = True
    col.use_property_decorate = False
    col.prop(props, name)


class FPMP_PT_fixer_base:
    """Repair FP materials in place; the report goes to the "FP Fixer Report" text"""
    bl_label = 'Material Fixer'
    bl_region_type = 'UI'
    bl_category = 'Fortnite Porting'

    def draw(self, context):
        props = context.scene.fpmp_fixer
        layout = self.layout
        layout.row().prop(props, 'scope', expand=True)
        _checkbox(layout, props, 'preview')
        layout.operator(FPMP_OT_FixMaterials.bl_idname, icon='CHECKMARK')
        if islands_available():
            header, body = layout.panel('fpmp_fixer_island', default_closed=True)
            header.label(text='Recover Island Textures')
            if body:
                _checkbox(body, props, 'apply_colors')
                body.operator(FPMP_OT_RecoverIsland.bl_idname, text='Recover', icon='IMPORT')
        if _running:
            layout.progress(factor=props.progress, text=props.status[9:][:60] or 'Working...')
        elif props.status:
            layout.label(text=props.status, icon='INFO', translate=False)


class FPMP_PT_fixer_view3d(FPMP_PT_fixer_base, bpy.types.Panel):
    bl_idname = 'FPMP_PT_fixer_view3d'
    bl_space_type = 'VIEW_3D'
    bl_parent_id = 'FPMP_PT_exact'
    bl_options = {'DEFAULT_CLOSED'}


class FPMP_PT_fixer_nodes(FPMP_PT_fixer_base, bpy.types.Panel):
    bl_idname = 'FPMP_PT_fixer_nodes'
    bl_space_type = 'NODE_EDITOR'

    @classmethod
    def poll(cls, context):
        return context.space_data.tree_type == 'ShaderNodeTree'


classes = (FPMP_FixerProps, FPMP_OT_FixMaterials, FPMP_OT_RecoverIsland, FPMP_PT_fixer_view3d, FPMP_PT_fixer_nodes)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.fpmp_fixer = PointerProperty(type=FPMP_FixerProps)
    if not bpy.app.background:
        start_probe()


def unregister():
    stop_probe()
    del bpy.types.Scene.fpmp_fixer
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
