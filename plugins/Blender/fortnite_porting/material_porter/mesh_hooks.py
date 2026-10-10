"""What the fork adds to FP's mesh import (processing/context/mesh_context.py calls these)."""
from math import radians

import bmesh
import bpy

from ..logger import Log
from ..processing.enums import EExportType, ERigType


def begin(ctx, selected_armature):
    # a contrail goes on the character selected when it was sent (effects.finish); a character's
    # own effects play once its skeleton is final (effects.settle)
    ctx.mp_selected_armature = selected_armature
    ctx.mp_deferred_effects = [] if ctx.type in [EExportType.OUTFIT, EExportType.FALL_GUYS_OUTFIT] else None


def after_meshes(ctx):
    # depth under water surfaces, read by their exact materials
    if ctx.type in [EExportType.WORLD, EExportType.PREFAB]:
        from .placement import after_world
        after_world(ctx.collection.all_objects)


def after_lights(ctx, data):
    # a level's decals and placed effects; a lamp's housing casts no shadow from its light
    from .decals import import_decals
    from .level_effects import import_effects
    from .shadow_linking import link_housings
    import_decals(ctx, data.get("Decals"))
    import_effects(ctx, data.get("Effects"))
    link_housings(ctx)


def before_merge(ctx):
    # an outfit's unmerged parts share the whole character's bounds, as in the game (their materials read them)
    if ctx.type in [EExportType.OUTFIT, EExportType.FALL_GUYS_OUTFIT, EExportType.LEGO_OUTFIT, EExportType.LEGO_WILDLIFE] \
            and not ctx.options.get("MergeArmatures"):
        from .build import mark_shared_bounds
        mark_shared_bounds([imported_mesh.get("Mesh") for imported_mesh in ctx.imported_meshes])


def after_merge(master_mesh):
    # joined mesh's bounds and head, read by its materials (built per part before the merge)
    from .build import mark_bounds
    mark_bounds([master_mesh])


def settle_effects(ctx):
    if ctx.mp_deferred_effects is not None:
        from . import effects
        effects.settle(ctx)


# imports that get their own rig (creature, vehicle, LEGO) rather than Tasty's or the official one
RIGGED_APART = (EExportType.WILDLIFE, EExportType.LEGO_WILDLIFE, EExportType.SIDEKICK, EExportType.SPRITE,
                EExportType.VEHICLE, EExportType.LEGO_OUTFIT)


def _alive(o):
    try:        # a part's armature merged into the body's is gone
        return o is not None and o.name in bpy.data.objects and o.type == 'ARMATURE'
    except ReferenceError:
        return False


def _tasty(skeleton):
    """FP's character rig in the kit's colours, and a face board when the face is a flipbook; never fails the import."""
    from ..processing.context import tasty_style
    try:
        if tasty_style.style(skeleton):
            tasty_style.add_face_board(skeleton)
    except Exception as e:
        Log.error("%s: Tasty restyle (%s: %s)" % (skeleton.name, type(e).__name__, e))
        if bpy.context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')


def _official(ctx):
    """UEFN's own mannequin rig on the humanoid master skeleton, and the face board when the face is a flipbook."""
    from ..processing.context import face_board, official_rig
    for skeleton in {m.get("Skeleton") for m in ctx.imported_meshes if _alive(m.get("Skeleton"))}:
        if not official_rig.fits(skeleton):
            continue
        try:
            Log.info(official_rig.create(skeleton))
            face_board.add(skeleton, head="head")
        except Exception as e:
            Log.error("%s: official rig (%s: %s)" % (skeleton.name, type(e).__name__, e))
            if bpy.context.mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')


def _metahuman(ctx):
    """Epic's MetaHuman face board on a MetaHuman-style head (FACIAL bones, expression shape keys)."""
    from ..processing.context import metahuman_board
    for skeleton in {m.get("Skeleton") for m in ctx.imported_meshes if _alive(m.get("Skeleton"))}:
        try:
            if metahuman_board.fits(skeleton):
                # the head part's meta names it; the board may sit on the body's armature (merged parts)
                dna = next((m["Meta"].get("FaceDNA") for m in ctx.imported_meshes
                            if isinstance(m.get("Meta"), dict) and m["Meta"].get("FaceDNA")), None)
                Log.info("%s: MetaHuman board, %d drivers" % (skeleton.name, metahuman_board.add(skeleton, dna=dna)))
        except Exception as e:
            Log.error("%s: MetaHuman board (%s: %s)" % (skeleton.name, type(e).__name__, e))
            if bpy.context.mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')


def after_parts(ctx, rig_type, tasty):
    """Shell fur, the character's effects (not played yet, parts not merged) and a rig for what Tasty's doesn't fit."""
    from . import shells
    shells.apply(ctx, [m.get("Mesh") for m in ctx.imported_meshes])
    settle_effects(ctx)

    if rig_type == tasty:
        for skeleton in {m.get("Skeleton") for m in ctx.imported_meshes if _alive(m.get("Skeleton"))}:
            if skeleton.data.get("is_tasty"):
                _tasty(skeleton)

    if rig_type == ERigType.OFFICIAL and ctx.type not in RIGGED_APART:
        _official(ctx)
    _metahuman(ctx)

    # a creature, sidekick, sprite, vehicle or LEGO figure armature gets its own rig
    if rig_type not in (tasty, ERigType.OFFICIAL) or ctx.type not in RIGGED_APART:
        return
    from ..processing.context import lego_rig
    skeletons = [m.get("Skeleton") for m in ctx.imported_meshes if _alive(m.get("Skeleton"))]
    if ctx.type == EExportType.LEGO_OUTFIT:
        skeletons = [o for o in skeletons if lego_rig.fits(o)]
    skeleton = skeletons[0] if skeletons else None
    if skeleton is None or [k for k in ("is_creature_rig", "is_vehicle_rig", "is_lego_rig") if skeleton.data.get(k)]:
        return
    if ctx.type == EExportType.VEHICLE:
        from ..processing.context.vehicle_rig import create as create_rig
    elif ctx.type == EExportType.LEGO_OUTFIT:
        create_rig = lego_rig.create
    else:
        from ..processing.context.creature_rig import create as create_rig
    try:
        Log.info(create_rig(skeleton))
    except Exception as e:
        Log.error("%s: no rig (%s: %s)" % (skeleton.name, type(e).__name__, e))
        if bpy.context.object is not None and bpy.context.object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')


def make_empty(ctx, mesh, name):
    # a particle effect's sprite or ribbon is a plane with its material
    from . import effects
    return effects.make(ctx, mesh, name)


def finish_empty(ctx, mesh, empty_object, imported_children):
    # a particle effect's CPU emitters, replayed over the scene's frames; a time of day (from the
    # owner's private overlay module, absent elsewhere)
    from . import effects
    effects.finish(ctx, mesh, empty_object)
    if mesh.get("MPTimeOfDay"):
        try:
            from . import sky
        except ImportError:
            return
        sky.finish(ctx, mesh, empty_object, imported_children)


def tris_to_quads(imported_mesh):
    # Tris to Quads with its defaults (UVs compared), on the mesh data without mode switches
    bm = bmesh.new()
    bm.from_mesh(imported_mesh.data)
    bmesh.ops.join_triangles(bm, faces=bm.faces[:], cmp_seam=False, cmp_sharp=False, cmp_uvs=True,
                             cmp_vcols=False, cmp_materials=False,
                             angle_face_threshold=radians(40), angle_shape_threshold=radians(40))
    bm.to_mesh(imported_mesh.data)
    bm.free()
    imported_mesh.data.update()


def after_model(ctx, mesh, imported_object, imported_mesh):
    # a spline mesh's bend, custom data, white vertex colours
    from .placement import after_import
    after_import(mesh, imported_object, imported_mesh, ctx.scale)


def final_materials(mesh, override_materials):
    """Each slot's material as the import ends. A style's swap replaces the default, so only that one
    is built (an exact material takes a second or two). A style swaps by the name the slot shows
    (TextureData's override material on slot 0)."""
    td_override = next((td.get("OverrideMaterial") for td in mesh.get("TextureData") if td.get("OverrideMaterial")), None)
    final = {}
    for material in mesh.get("Materials") + mesh.get("OverrideMaterials"):
        final[material.get("Slot")] = material
    for variant_override_material in override_materials:
        for index, material in list(final.items()):
            shown = td_override if td_override and material.get("Slot") == 0 else material
            if shown.get("Name") == variant_override_material.get("MaterialNameToSwap"):
                final[index] = variant_override_material.get("Material")
    return final


def prepare_shells(ctx, mesh, imported_mesh, meta):
    # shell fur: the base layer's materials, then the shells
    if mesh.get("MPShells") and imported_mesh is not None:
        from . import shells
        shells.prepare(ctx, imported_mesh, mesh.get("MPShells"), meta)


def reparent_duplicate_bones(master_skeleton):
    """Move what hangs from a joined skeleton's duplicate bone (a head effect on "root.001") to the
    bone it duplicates before that one is deleted, keeping its rest pose. This goes tail space to
    tail space because the join's evaluated matrices aren't current yet and reoriented bones
    differ in length and turn."""
    import re
    from mathutils import Matrix
    bones = master_skeleton.data.bones
    for child in master_skeleton.children:
        if child.parent_type != 'BONE' or not re.search(r"\.\d\d\d$", child.parent_bone):
            continue
        base_name = re.sub(r"\.\d\d\d$", "", child.parent_bone)
        if base_name not in bones or child.parent_bone not in bones:
            continue
        old, new = bones[child.parent_bone], bones[base_name]
        old_space = old.matrix_local @ Matrix.Translation((0.0, old.length, 0.0))
        new_space = new.matrix_local @ Matrix.Translation((0.0, new.length, 0.0))
        child.matrix_basis = new_space.inverted() @ old_space @ child.matrix_parent_inverse @ child.matrix_basis
        child.matrix_parent_inverse = Matrix.Identity(4)
        child.parent_bone = base_name
