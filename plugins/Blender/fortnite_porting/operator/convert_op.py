"""Convert FP-imported materials to the fork's exact materials.

An FP material keeps only its asset name ("OriginalName"). The app bridge finds assets by that
name (find-materials) and material_porter.hook.build_exact builds the exact material, which
takes over the FP material's slots. If two assets share the name, the one whose textures the
FP material shows wins. Style overrides FP applied (colour swaps, building texture data) are
not stored on the FP material, so the exact one uses the asset's own values.
"""
import re
import types

import bpy

from ..material_porter import build, hook
from ..material_porter.app_client import AppClient, AppError

# Names per find-materials request (they go in the URL).
CHUNK = 40


def _asset_name(mat):
    """Asset name: "OriginalName", else the Blender name minus FP's style hash and the .001 suffix."""
    return mat.get("OriginalName") or re.sub(r"(_[0-9a-f]{8})?(\.\d{3})?$", "", mat.name)


def _from_fp(mat):
    """True for FP materials: they carry Hash/OriginalName; older imports only have UE-style names."""
    return "Hash" in mat or "OriginalName" in mat or re.match(r"(M|MI|MM|MAT|MIC)_", mat.name) is not None


def _image_names(mat):
    """Lowercase texture names of the images the material shows."""
    names = set()
    if not mat.node_tree:
        return names
    todo, seen = [mat.node_tree], set()
    while todo:
        tree = todo.pop()
        if tree.name in seen:
            continue
        seen.add(tree.name)
        for n in tree.nodes:
            if n.type == 'TEX_IMAGE' and n.image:
                names.add(re.sub(r"(\.\d{3})?(\.[a-z]{3,4})?$", "", n.image.name, flags=re.I).lower())
            elif n.type == 'GROUP' and n.node_tree and not n.node_tree.name.startswith("FPv4"):
                todo.append(n.node_tree)
    return names


def _pick(app, mat, paths):
    """The candidate sharing the most textures with the FP material (first on a tie)."""
    if len(paths) == 1:
        return paths[0]
    shown = _image_names(mat)
    best, score = paths[0], -1
    for path in paths:
        try:
            textures = (app.get("material", path).get("textures") or {}).values()
        except AppError:
            continue
        s = sum(1 for t in textures if t.split("/")[-1].split(".")[0].lower() in shown)
        if s > score:
            best, score = path, s
    return best


class FPMP_OT_ConvertExact(bpy.types.Operator):
    """Rebuild the FP materials of these objects as the fork's exact materials (the FP app must be running)"""
    bl_idname = "fpmp.convert_exact"
    bl_label = "Convert to Exact Materials"
    bl_options = {'REGISTER', 'UNDO'}

    scope: bpy.props.EnumProperty(
        name="Objects",
        items=[('SELECTED', "Selected", "The selected objects' materials"),
               ('SCENE', "Scene", "Every object's materials in the scene")],
        default='SELECTED')
    rim_light: bpy.props.BoolProperty(
        name="Rim Light", default=False,
        description="Keep the rim light of Fortnite's character materials (as the Blender export setting)")

    def execute(self, context):
        if bpy.app.version < (5, 0, 0):
            self.report({'ERROR'}, "Exact materials need Blender 5.0 or newer")
            return {'CANCELLED'}
        objects = context.selected_objects if self.scope == 'SELECTED' else context.scene.objects
        # FP material -> its (object, slot index) users
        slots = {}
        for obj in objects:
            for i, slot in enumerate(getattr(obj, "material_slots", [])):
                mat = slot.material
                if mat is None or build.KEY_PATH in mat or not _from_fp(mat):
                    continue
                slots.setdefault(mat, []).append((obj, i))
        if not slots:
            self.report({'INFO'}, "No FP materials here (they are exact already, or not FortnitePorting's)")
            return {'CANCELLED'}

        app = AppClient(hook.URL)
        names = sorted({_asset_name(m) for m in slots})
        found = {}
        try:
            for k in range(0, len(names), CHUNK):
                found.update(app.get("find-materials", ",".join(names[k:k + CHUNK])))
        except AppError as e:
            self.report({'ERROR'}, "The FP app isn't answering: open FortnitePorting MP and load the game (%s)" % e)
            return {'CANCELLED'}

        # One import session: with many materials the build lays out lazily, as for a world.
        job = types.SimpleNamespace(options={"RimLight": self.rim_light},
                                    type=types.SimpleNamespace(name="WORLD" if len(slots) > 20 else "MESH"))
        converted, missing, failed = 0, [], []
        wm = context.window_manager
        wm.progress_begin(0, len(slots))
        for n, (mat, users) in enumerate(slots.items()):
            wm.progress_update(n)
            name = _asset_name(mat)
            paths = found.get(name) or []
            if not paths:
                missing.append(name)
                continue
            exact = hook.build_exact(job, {"Path": _pick(app, mat, paths), "Name": name}, obj=users[0][0])
            if exact is None:
                failed.append(name)
                continue
            for key in ("Hash", "OriginalName"):
                if key in mat:
                    exact[key] = mat[key]
            exact["MPRimLight"] = self.rim_light
            for obj, i in users:
                obj.material_slots[i].material = exact
            converted += 1
        wm.progress_end()

        msg = "%d material%s converted" % (converted, "" if converted == 1 else "s")
        if missing:
            msg += "; %d not found in the game (%s)" % (len(missing), ", ".join(missing[:5]) + (" ..." if len(missing) > 5 else ""))
        if failed:
            msg += "; %d not built, kept as FP's (see the log: %s)" % (len(failed), ", ".join(failed[:5]) + (" ..." if len(failed) > 5 else ""))
        self.report({'WARNING'} if missing or failed else {'INFO'}, msg)
        return {'FINISHED'}


class FPMP_OT_RemoveWrap(bpy.types.Operator):
    """Take the wrap off the selected objects' materials (the one picked on the asset's page in the
    app, or a weapon's own), with the FP app open"""
    bl_idname = "fpmp.remove_wrap"
    bl_label = "Remove Wrap"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        from ..material_porter import wrap
        job = types.SimpleNamespace(options={'RimLight': False}, type=types.SimpleNamespace(name='MESH'))
        materials, objects, _ = wrap.lay(job, wrap.targets(context), None)
        if materials == 0:
            self.report({'INFO'}, "No wrapped materials on the selection")
            return {'CANCELLED'}
        self.report({'INFO'}, "Wrap removed from %d material(s) on %d object(s)" % (materials, objects))
        return {'FINISHED'}


class FPMP_OT_ReplayEffect(bpy.types.Operator):
    """Replay the selected particle effects over the scene's frame range, with their User properties.
    With an armature selected too, the effects are put on it first: they read its bones and sockets,
    and follow its animation (a contrail on a character, a trail on a swinging pickaxe)"""
    bl_idname = "fpmp.replay_effect"
    bl_label = "Replay Effect"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        from ..material_porter import effect_replay
        roots = effect_replay.roots(context.selected_objects)
        if not roots:
            self.report({'INFO'}, "Select an imported effect (its empty, or anything under it)")
            return {'CANCELLED'}
        rigs = [o for o in context.selected_objects if o.type == 'ARMATURE']
        rig = context.active_object if context.active_object in rigs else rigs[0] if len(rigs) == 1 else None
        for root in roots:
            if rig is not None and effect_replay.rig_of(root) is not rig:
                effect_replay.attach(root, rig)
            for line in effect_replay.play(root):
                self.report({'INFO'}, line)
        return {'FINISHED'}


class FPMP_OT_CreatureRig(bpy.types.Operator):
    """Give the selected armature a creature rig: IK legs (a foot control and a knee pole each), FK spine,
    tail, head and the rest, read from its skeleton (a wolf's, a chicken's, a raptor's...)"""
    bl_idname = "fpmp.creature_rig"
    bl_label = "Rig Creature"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'ARMATURE' and not obj.data.get("is_creature_rig") and not obj.data.get("is_tasty") and not obj.data.get("is_lego_rig")

    def execute(self, context):
        from ..processing.context.creature_rig import create
        self.report({'INFO'}, create(context.active_object))
        return {'FINISHED'}


class FPMP_OT_VehicleRig(bpy.types.Operator):
    """Give the selected armature a vehicle rig: CR_Main places it, CR_Drive moved forward drives it (the
    wheels spin by the distance), CR_Steer turns the front wheels and the steering wheel"""
    bl_idname = "fpmp.vehicle_rig"
    bl_label = "Rig Vehicle"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'ARMATURE' and not obj.data.get("is_vehicle_rig") and not obj.data.get("is_creature_rig") and not obj.data.get("is_tasty") and not obj.data.get("is_lego_rig")

    def execute(self, context):
        from ..processing.context.vehicle_rig import create
        self.report({'INFO'}, create(context.active_object))
        return {'FINISHED'}


class FPMP_OT_LegoRig(bpy.types.Operator):
    """Give the selected LEGO figure's armature a rig: its root a footprint, a plate at the hips, rings on
    the torso and head, dials on the hips and shoulders (legs swing about their hip's axis only)"""
    bl_idname = "fpmp.lego_rig"
    bl_label = "Rig LEGO Figure"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        from ..processing.context import lego_rig
        obj = context.active_object
        return lego_rig.fits(obj) and not obj.data.get("is_lego_rig")

    def execute(self, context):
        from ..processing.context.lego_rig import create
        self.report({'INFO'}, create(context.active_object))
        return {'FINISHED'}


class FPMP_OT_MaterialAnimation(bpy.types.Operator):
    bl_idname = "fpmp.material_animation"
    bl_label = "Animated Materials"
    bl_description = "Play the materials' own animation (panners, flipbooks, pulses), or hold every material on its current look"
    bl_options = {'REGISTER', 'UNDO'}
    on: bpy.props.BoolProperty(default=True)

    def execute(self, context):
        from ..material_porter import material_time
        material_time.set_animated(self.on, context.scene)
        return {'FINISHED'}


class FPMP_PT_Exact(bpy.types.Panel):
    bl_label = "Exact Materials"
    bl_idname = "FPMP_PT_exact"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Fortnite Porting"

    def draw(self, context):
        col = self.layout.column(align=True)
        col.label(text="Rebuild FP materials exactly:")
        row = col.row(align=True)
        row.operator(FPMP_OT_ConvertExact.bl_idname, text="Selected").scope = 'SELECTED'
        row.operator(FPMP_OT_ConvertExact.bl_idname, text="Scene").scope = 'SCENE'
        # Wraps are applied in the app (Wrap list on a weapon/vehicle page); removed here.
        col.separator()
        col.operator(FPMP_OT_RemoveWrap.bl_idname, text="Remove Wrap from Selected")
        # Effects replay at import; this replays them on a character or another frame range.
        col.separator()
        col.operator(FPMP_OT_ReplayEffect.bl_idname, text="Replay Effect")
        from ..material_porter.material_time import is_animated
        on = is_animated(context.scene)
        col.operator(FPMP_OT_MaterialAnimation.bl_idname, text="Animated Materials", icon='TIME', depress=on).on = not on


class FPMP_OT_AddShellFur(bpy.types.Operator):
    """Shell fur on the selected faces of the active mesh (select them in Edit Mode)"""
    bl_idname = "fpmp.add_shell_fur"
    bl_label = "Add Shell Fur to Selected Faces"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and context.active_object.type == 'MESH'

    def execute(self, context):
        from ..material_porter import custom_fur
        obj = context.active_object
        was_edit = obj.mode == 'EDIT'
        if was_edit:
            bpy.ops.object.mode_set(mode='OBJECT')      # flushes the edit-mode selection to the mesh
        try:
            msg = custom_fur.add_fur(obj)
        except ValueError as e:
            self.report({'WARNING'}, str(e))
            return {'CANCELLED'}
        finally:
            if was_edit:
                bpy.ops.object.mode_set(mode='EDIT')
        self.report({'INFO'}, msg)
        return {'FINISHED'}


class FPMP_OT_RemoveShellFur(bpy.types.Operator):
    """The active mesh's custom shell fur removed"""
    bl_idname = "fpmp.remove_shell_fur"
    bl_label = "Remove Shell Fur"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'MESH' and obj.mode == 'OBJECT'

    def execute(self, context):
        from ..material_porter import custom_fur
        self.report({'INFO'}, custom_fur.remove_fur(context.active_object))
        return {'FINISHED'}


class FPMP_PT_ShellFur(bpy.types.Panel):
    bl_label = "Shell Fur"
    bl_idname = "FPMP_PT_shell_fur"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Fortnite Porting"

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and context.active_object.type == 'MESH'

    def draw(self, context):
        from ..material_porter import custom_fur
        col = self.layout.column(align=True)
        col.operator(FPMP_OT_AddShellFur.bl_idname, icon='ADD')
        if custom_fur.draw_inputs(self.layout, context.active_object):
            self.layout.operator(FPMP_OT_RemoveShellFur.bl_idname, icon='X')


classes = (FPMP_OT_ConvertExact, FPMP_OT_RemoveWrap, FPMP_OT_ReplayEffect, FPMP_OT_MaterialAnimation, FPMP_PT_Exact, FPMP_OT_CreatureRig, FPMP_OT_VehicleRig, FPMP_OT_LegoRig,
           FPMP_OT_AddShellFur, FPMP_OT_RemoveShellFur, FPMP_PT_ShellFur)


def _owner_panels():
    """Panels of the owner-only overlay; empty when it isn't packed in."""
    try:
        from ..material_porter import tod_panel
    except ImportError:
        return []
    return [tod_panel]


def register():
    from ..processing.context import vehicle_rig
    from . import rig_ui
    for c in classes:
        bpy.utils.register_class(c)
    rig_ui.register()
    vehicle_rig.register()          # registers the armature's fpmp_ground property
    for m in _owner_panels():
        m.register()
    from ..material_porter import effects
    effects.register()              # soft fade follows the render engine


def unregister():
    from ..processing.context import vehicle_rig
    from ..material_porter import effects
    effects.unregister()
    for m in _owner_panels():
        m.unregister()
    vehicle_rig.unregister()
    from . import rig_ui
    rig_ui.unregister()
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
