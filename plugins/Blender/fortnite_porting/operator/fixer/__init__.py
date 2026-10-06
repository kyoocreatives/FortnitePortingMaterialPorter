"""Material Porter fork: the FP Material Fixer (from the fpisland extension), in the Fortnite Porting tab.

Fix FP Materials wires what FP's shader left unlinked (fixer.py). Recover Island Textures (the owner's
builds, which read islands) gets a UEFN map's missing textures and flat colours from the app (island.py).
Both take the whole file's materials or only the selected objects'.
"""
import time
import types

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, PointerProperty, StringProperty

from . import fixer, island
from ...material_porter import hook
from ...material_porter.app_client import AppClient, AppError

REPORT_TEXT = 'FP Fixer Report'
# what the app's build does (fork-caps): None until asked; island recovery shows only where islands are
_caps = {'islands': None, 'asking': False, 'next': 0.0}
_running = False


def _ask_caps():
    _caps['asking'] = False
    try:
        _caps['islands'] = bool(AppClient(hook.URL).get('fork-caps', '').get('islands'))
    except Exception:
        _caps['next'] = time.time() + 30      # the app isn't open: ask again later, not on every redraw
        return None
    tag_redraw(bpy.context)
    return None


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
    """The objects whose slots a fix may change: every one, or the selected (and their collection instances')."""
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
    """The fork's own materials for an island's materials (no graph: built from the cooked textures and
    values, mp_fallback) that an older fallback builder made, built again by the current one (the FP
    Material Fixer's names and colour rules) and put in their slots. (count, notes)."""
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

    def execute(self, context):
        props = context.scene.fpmp_fixer
        run = fixer.fix_materials(scope_materials(context, props.scope), dry=props.preview)
        rebuilt, notes = rebuild_fallbacks(context, props.scope, props.preview)
        write_report('Fix FP Materials' + (' (preview)' if props.preview else ''), [
            ('Material fixes', [f"{m}: {msg}" for m, msgs in run.report.items() for msg in msgs]),
            ('Colour spaces', [f"{n}: {a} -> {b}" for n, a, b in run.cs_changes]),
            ("The fork's island materials", notes if notes else ([f"{rebuilt} would be rebuilt"] if props.preview and rebuilt else []))])
        n = len([m for m, v in run.report.items() if v])
        verb = 'would change' if props.preview else 'changed'
        props.status = f"{verb} {n} materials, {len(run.cs_changes)} colour spaces" + (
            f", {'would rebuild' if props.preview else 'rebuilt'} {rebuilt} island materials" if rebuilt else "")
        self.report({'INFO'}, f"FP Fixer: {props.status}. Details in Text Editor > '{REPORT_TEXT}'")
        return {'FINISHED'}


class FPMP_OT_RecoverIsland(bpy.types.Operator):
    bl_idname = 'fpmp.recover_island'
    bl_label = 'Recover Island Textures'
    bl_description = ("For a UEFN map's materials FortnitePorting imported without a base colour: find the island "
                      "material each was made from, wire the textures it really references and set its flat colours "
                      "(the FP app must be open with the island loaded)")
    bl_options = {'REGISTER', 'UNDO'}

    _timer = None

    @classmethod
    def poll(cls, context):
        return not _running

    def invoke(self, context, event):
        global _running
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
        # a slice of the work per tick, so the window keeps answering
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


class FPMP_PT_fixer_base:
    bl_label = 'Material Fixer'
    bl_region_type = 'UI'
    bl_category = 'Fortnite Porting'

    def draw(self, context):
        props = context.scene.fpmp_fixer
        lay = self.layout
        lay.prop(props, 'scope', expand=True)

        box = lay.box()
        box.label(text='Fix FP Materials', icon='NODE_MATERIAL')
        box.prop(props, 'preview')
        box.operator(FPMP_OT_FixMaterials.bl_idname, icon='CHECKMARK')

        if _caps['islands'] is None and not _caps['asking'] and time.time() >= _caps['next']:
            _caps['asking'] = True
            bpy.app.timers.register(_ask_caps, first_interval=0.1)
        if _caps['islands']:
            box = lay.box()
            box.label(text='Recover Island Textures', icon='WORLD')
            box.prop(props, 'apply_colors')
            row = box.row()
            row.enabled = not _running
            row.operator(FPMP_OT_RecoverIsland.bl_idname, text='Recover', icon='IMPORT')
            if _running:
                box.progress(factor=props.progress, text=props.status[9:][:60] or 'Working...')
                box.label(text='Esc to cancel', icon='INFO')

        if props.status and not _running and not props.status.startswith('Running'):
            col = lay.column(align=True)
            col.label(text=props.status, icon='INFO')
            if REPORT_TEXT in bpy.data.texts:
                col.label(text=f"Report: Text Editor > {REPORT_TEXT}")


class FPMP_PT_fixer_view3d(FPMP_PT_fixer_base, bpy.types.Panel):
    bl_idname = 'FPMP_PT_fixer_view3d'
    bl_space_type = 'VIEW_3D'


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


def unregister():
    del bpy.types.Scene.fpmp_fixer
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
