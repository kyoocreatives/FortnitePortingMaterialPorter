import os.path
import bpy
import numpy as np
from math import radians, cos, pi
from mathutils import Matrix

from ..mappings import *
from ..enums import *
from ..utils import *
from ...utils import *
from ...logger import Log
from ...ueformat.importer.import_context import UEFormatImport
from ...ueformat.options import UEModelOptions

VERTEX_CRUNCH_NAME = "FPv4 Vertex Crunch"
FULL_VERTEX_CRUNCH_NAME = "FPv4 Full Vertex Crunch"

# Material Porter fork: a light's brightness in Blender watts per UE candela. FP has read a UE point light's candelas
# as watts since it first imported them (a street lamp's candelas show in a night scene; the physical 4 pi / 683
# watts a candela would show nothing), and spot and rect lights follow it; retune every imported light with this.
LIGHT_CANDELA_TO_WATTS = 1.0


def spot_solid_angle(inner, outer):
    """The solid angle (sr) of a UE spot light's cone: UE clamps its half angle between the inner angle and 89 degrees."""
    half = min(max(radians(outer), radians(min(inner, 89.0)) + 0.001), radians(89.0) + 0.001)
    return 2 * pi * (1.0 - cos(half))


def light_candelas(light, solid_angle):
    """
    A UE light's Intensity as candelas along its axis (ULocalLightComponent::GetUnitsConversionFactor): candelas as
    they are, lumens over the solid angle the light fills, and the legacy Unitless 16 / 10,000 of a candela (the 5000 a
    light was placed with is 8 candelas). EV and Nits don't occur on a placed local light: read as candelas.
    A light with the older falloff (Fortnite's street lamps and floodlights: bUseInverseSquaredFalloff off) has a
    brightness instead, whatever its units say, that fades as (1 - (d / reach)^2)^exponent: it is given the candelas
    that light a surface the same a third of the way out (as the particle lights of effect_replay are).
    """
    intensity = light.get("Intensity")
    if not light.get("InverseSquaredFalloff", True):
        d = light.get("AttenuationRadius") / 3.0
        return intensity * (8.0 / 9.0) ** max(light.get("FalloffExponent", 8.0), 0.0) * (d * d + 1.0) / 1e4
    units = light.get("IntensityUnits") or "Candelas"
    if units == "Lumens":
        return intensity / solid_angle
    if units == "Unitless":
        return intensity / 625.0
    return intensity


def kelvin_tint(kelvin):
    """A colour temperature's linear sRGB tint at unit luminance (UE's FLinearColor::MakeFromColorTemperature: Krystek's
    Planckian locus in CIE 1960 UCS, 6500 K being about white)."""
    t = min(max(kelvin, 1000.0), 15000.0)
    u = (0.860117757 + 1.54118254e-4 * t + 1.28641212e-7 * t * t) / (1.0 + 8.42420235e-4 * t + 7.08145163e-7 * t * t)
    v = (0.317398726 + 4.22806245e-5 * t + 4.20481691e-8 * t * t) / (1.0 - 2.89741816e-5 * t + 1.61456053e-7 * t * t)
    d = 2.0 * u - 8.0 * v + 4.0
    x = 3.0 * u / d
    y = 2.0 * v / d
    x_, z_ = x / y, (1.0 - x - y) / y
    return (max(3.2404542 * x_ - 1.5371385 - 0.4985314 * z_, 0.0),
            max(-0.9692660 * x_ + 1.8760108 + 0.0415560 * z_, 0.0),
            max(0.0556434 * x_ - 0.2040259 + 1.0572252 * z_, 0.0))

class MeshImportContext:
    def import_mesh_data(self, data):
        rig_type = ERigType(self.options.get("RigType"))
        
        if rig_type == ERigType.TASTY:
            self.options["MergeArmatures"] = True
            self.options["ReorientBones"] = True
        
        self.override_materials = data.get("OverrideMaterials")
        self.override_parameters = data.get("OverrideParameters")
        self.override_morph_targets = data.get("OverrideMorphTargets")

        pre_import_selected_armature = get_selected_armature()
        # Material Porter fork: a contrail goes on the character selected when it was sent (effects.finish)
        self.mp_selected_armature = pre_import_selected_armature
        # and a character's own effects are played once its skeleton is final (effects.settle)
        self.mp_deferred_effects = [] if self.type in [EExportType.OUTFIT, EExportType.FALL_GUYS_OUTFIT] else None
        pre_import_selected_armature_active = pre_import_selected_armature is not None and pre_import_selected_armature.select_get()
        
        self.collection = create_or_get_collection(self.name) if self.options.get("ImportIntoCollection") else bpy.context.scene.collection

        # (Material Porter fork: a sidekick's styles swap its parts too - a skin's own materials and fur)
        if self.type in [EExportType.OUTFIT, EExportType.BACKPACK, EExportType.PICKAXE, EExportType.FALL_GUYS_OUTFIT,
                         EExportType.SIDEKICK]:
            target_meshes = data.get("OverrideMeshes")
            normal_meshes = data.get("Meshes")
            for mesh in normal_meshes:
                if not any(target_meshes, lambda target_mesh: target_mesh.get("Type") == mesh.get("Type")):
                    target_meshes.append(mesh)
        else:
            target_meshes = data.get("Meshes")

        self.meshes = target_meshes
        # Material Porter fork: each mesh's index for the progress log, without a scan per object
        self.mesh_index = {id(m): i for i, m in enumerate(target_meshes)}
        for mesh in target_meshes:
            self.import_model(mesh, can_spawn_at_3d_cursor=True)

        # Material Porter fork: the depth under water surfaces, which their exact materials read
        if self.type in [EExportType.WORLD, EExportType.PREFAB]:
            from ...material_porter.placement import after_world
            after_world(self.collection.all_objects)

        self.import_light_data(data.get("Lights"))
        # Material Porter fork: a level's decals and placed effects (material_porter.decals, level_effects)
        from ...material_porter.decals import import_decals
        from ...material_porter.level_effects import import_effects
        import_decals(self, data.get("Decals"))
        import_effects(self, data.get("Effects"))
                
        if self.type in [EExportType.OUTFIT]:
            for imported_mesh in self.imported_meshes:
                self.parent_deform_bones(imported_mesh["Skeleton"], ["dfrm_", "deform_"])
                self.parent_bones(imported_mesh["Skeleton"], extra_deform_mappings)
            
        # Material Porter fork: an outfit's parts left apart share the whole character's bounds, as in
        # the game (their materials read them: a gradient from the feet to the top of the head)
        if self.type in [EExportType.OUTFIT, EExportType.FALL_GUYS_OUTFIT, EExportType.LEGO_OUTFIT, EExportType.LEGO_WILDLIFE] and not self.options.get("MergeArmatures"):
            from ...material_porter.build import mark_shared_bounds
            mark_shared_bounds([imported_mesh.get("Mesh") for imported_mesh in self.imported_meshes])

        # Material Porter fork: a LEGO figure's or creature's parts too (the body's armature takes them)
        if self.type in [EExportType.OUTFIT, EExportType.FALL_GUYS_OUTFIT, EExportType.LEGO_OUTFIT, EExportType.LEGO_WILDLIFE] and self.options.get("MergeArmatures"):
            master_skeleton = merge_parts(self.imported_meshes)
            master_mesh = get_armature_mesh(master_skeleton)
            # (Material Porter fork: the joined mesh's bounds and head, which its materials read -
            # they were built as each part came in, on the part's own skeleton and size)
            from ...material_porter.build import mark_bounds
            mark_bounds([master_mesh])
            # Update attribute to account for joined mesh
            self.update_preskinned_bounds(master_mesh)
            
            for material, elements in self.partial_vertex_crunch_materials.items():
                vertex_crunch_modifier = master_mesh.modifiers.new(VERTEX_CRUNCH_NAME, type="NODES")
                vertex_crunch_modifier.node_group = bpy.data.node_groups.get(VERTEX_CRUNCH_NAME)

                set_geo_nodes_param(vertex_crunch_modifier, "Material", material, self.version_profile)
                for name, value in elements.items():
                    set_geo_nodes_param(vertex_crunch_modifier, name, value == 1, self.version_profile)
                    
            for material in self.full_vertex_crunch_materials:
                vertex_crunch_modifier = master_mesh.modifiers.new(FULL_VERTEX_CRUNCH_NAME, type="NODES")
                vertex_crunch_modifier.node_group = bpy.data.node_groups.get(FULL_VERTEX_CRUNCH_NAME)
                set_geo_nodes_param(vertex_crunch_modifier, "Material", material, self.version_profile)

            if self.add_toon_outline:
                master_mesh.data.materials.append(bpy.data.materials.get("M_FP_Outline"))

                solidify = master_mesh.modifiers.new(name="Outline", type='SOLIDIFY')
                solidify.thickness = 0.001
                solidify.offset = 1
                solidify.thickness_clamp = 5.0
                solidify.use_rim = False
                solidify.use_flip_normals = True
                solidify.material_offset = len(master_mesh.data.materials) - 1
                
            # Material Porter fork: Tasty's rig is for Fortnite's humanoid skeleton, not a LEGO figure's or creature's
            if rig_type == ERigType.TASTY and self.type not in [EExportType.LEGO_OUTFIT, EExportType.LEGO_WILDLIFE]:
                self.create_tasty_rig(master_skeleton, self.get_metadata("MasterSkeletalMesh"))

            if self.mp_deferred_effects is not None:
                from ...material_porter import effects as mp_effects
                mp_effects.settle(self)

            if anim_data := data.get("Animation"):
                self.import_anim_data(anim_data, master_skeleton)

        if self.type in [EExportType.SIDEKICK]:
            master_mesh = self.imported_meshes[0]["Mesh"]
            for material in self.full_vertex_crunch_materials:
                vertex_crunch_modifier = master_mesh.modifiers.new(FULL_VERTEX_CRUNCH_NAME, type="NODES")
                vertex_crunch_modifier.node_group = bpy.data.node_groups.get(FULL_VERTEX_CRUNCH_NAME)
                set_geo_nodes_param(vertex_crunch_modifier, "Material", material, self.version_profile)

            for material, elements in self.partial_vertex_crunch_materials.items():
                vertex_crunch_modifier = master_mesh.modifiers.new(VERTEX_CRUNCH_NAME, type="NODES")
                vertex_crunch_modifier.node_group = bpy.data.node_groups.get(VERTEX_CRUNCH_NAME)
                set_geo_nodes_param(vertex_crunch_modifier, "Material", material, self.version_profile)
                for name, value in elements.items():
                    set_geo_nodes_param(vertex_crunch_modifier, name, value == 1, self.version_profile)

            shape_keys = master_mesh.data.shape_keys
            if (len(self.override_morph_targets) > 0) and shape_keys is not None:
                for morph_target in self.override_morph_targets:
                    if key := best(shape_keys.key_blocks, lambda block: block.name.lower(), morph_target.get("Name").lower()):
                        key.value = morph_target.get("Value")
                        
        # Material Porter fork: shell fur on the meshes it's for (the joined one, else each part)
        from ...material_porter import shells as mp_shells
        mp_shells.apply(self, [m.get("Mesh") for m in self.imported_meshes])

        # Material Porter fork: a character's effects not played yet (no merge of its parts): now
        if getattr(self, "mp_deferred_effects", None) is not None:
            from ...material_porter import effects as mp_effects
            mp_effects.settle(self)

        # Material Porter fork: a creature's, a sidekick's, a vehicle's or a LEGO figure's armature gets a rig of its own, as an
        # outfit's gets Tasty's
        if rig_type == ERigType.TASTY and self.type in [EExportType.WILDLIFE, EExportType.LEGO_WILDLIFE, EExportType.SIDEKICK,
                                                         EExportType.VEHICLE, EExportType.LEGO_OUTFIT]:
            from . import lego_rig

            def armature(o):
                try:        # (a part's armature merged into the body's is gone)
                    return o is not None and o.name in bpy.data.objects and o.type == 'ARMATURE'
                except ReferenceError:
                    return False
            skeletons = [m.get("Skeleton") for m in self.imported_meshes if armature(m.get("Skeleton"))]
            if self.type == EExportType.LEGO_OUTFIT:
                skeletons = [o for o in skeletons if lego_rig.fits(o)]
            skeleton = skeletons[0] if skeletons else None
            if skeleton is not None and not [k for k in ("is_creature_rig", "is_vehicle_rig", "is_lego_rig") if skeleton.data.get(k)]:
                if self.type == EExportType.VEHICLE:
                    from .vehicle_rig import create as create_rig
                elif self.type == EExportType.LEGO_OUTFIT:
                    create_rig = lego_rig.create
                else:
                    from .creature_rig import create as create_rig
                try:
                    Log.info(create_rig(skeleton))
                except Exception as e:
                    Log.error("%s: no rig (%s: %s)" % (skeleton.name, type(e).__name__, e))
                    if bpy.context.object is not None and bpy.context.object.mode != 'OBJECT':
                        bpy.ops.object.mode_set(mode='OBJECT')

        if self.type in [EExportType.KICKS]:

            kick_armature = get_selected_armature()
            kick_armature["is_kicks"] = True
            
            if pre_import_selected_armature_active and "pelvis" in pre_import_selected_armature.data.bones and not pre_import_selected_armature.get("is_kicks"):
                merge_armatures(pre_import_selected_armature, [kick_armature])
                bpy.data.collections.remove(self.collection)
            
            
            

    def import_model(self, mesh, parent=None, can_reorient=True, can_spawn_at_3d_cursor=False):
        path = mesh.get("Path")
        name = mesh.get("Name")
        part_type = EFortCustomPartType(mesh.get("Type"))
        
        if mesh.get("IsEmpty"):
            # Material Porter fork: a particle effect's sprite or ribbon is a plane with its material
            from ...material_porter import effects
            empty_object = effects.make(self, mesh, name)

            empty_object.parent = parent
            empty_object.rotation_euler = make_euler(mesh.get("Rotation"))
            empty_object.location = make_vector(mesh.get("Location"), unreal_coords_correction=True) * self.scale
            empty_object.scale = make_vector(mesh.get("Scale"))
            
            self.collection.objects.link(empty_object)
            
            imported_children = [(child, self.import_model(child, parent=empty_object)) for child in mesh.get("Children")]

            # Material Porter fork: a particle effect's CPU emitters, replayed over the scene's frames
            effects.finish(self, mesh, empty_object)
            # Material Porter fork: a time of day (the owner's private overlay's module, absent elsewhere)
            if mesh.get("MPTimeOfDay"):
                try:
                    from ...material_porter import sky
                except ImportError:
                    sky = None
                if sky is not None:
                    sky.finish(self, mesh, empty_object, imported_children)
            return empty_object
        
        if self.type in [EExportType.PREFAB, EExportType.WORLD] and (index := self.mesh_index.get(id(mesh))) is not None:
            Log.info(f"Importing Actor: {name} {index} / {len(self.meshes)}")

        mesh_name = path.split(".")[1]
        if self.type in [EExportType.PREFAB, EExportType.WORLD] and (existing_mesh_data := bpy.data.meshes.get(mesh_name + "_LOD0")):
            imported_object = bpy.data.objects.new(name, existing_mesh_data)
            self.collection.objects.link(imported_object)
            
            imported_mesh = get_armature_mesh(imported_object)
        else:
            imported_object = self.import_mesh(path, can_reorient=can_reorient)
            if imported_object is None:
                Log.warn(f"Import failed for object at path: {path}")
                return imported_object
            imported_object.name = name

            imported_mesh = get_armature_mesh(imported_object)

            if EPolygonType(self.options.get("PolygonType")) == EPolygonType.QUADS and imported_mesh is not None:
                # Material Porter fork: Tris to Quads (its defaults, UVs compared) on the mesh data, no mode switches
                import bmesh
                bm = bmesh.new()
                bm.from_mesh(imported_mesh.data)
                bmesh.ops.join_triangles(bm, faces=bm.faces[:], cmp_seam=False, cmp_sharp=False, cmp_uvs=True,
                                         cmp_vcols=False, cmp_materials=False,
                                         angle_face_threshold=radians(40), angle_shape_threshold=radians(40))
                bm.to_mesh(imported_mesh.data)
                bm.free()
                imported_mesh.data.update()

        if (override_vertex_colors := mesh.get("OverrideVertexColors")) and len(override_vertex_colors) > 0:
            imported_mesh.data = imported_mesh.data.copy()

            vertex_color = imported_mesh.data.color_attributes.new(
                domain="CORNER",
                type="BYTE_COLOR",
                name="INSTCOL0",
            )

            color_data = []
            for col in override_vertex_colors:
                color_data.append((col["R"], col["G"], col["B"], col["A"]))

            for polygon in imported_mesh.data.polygons:
                for vertex_index, loop_index in zip(polygon.vertices, polygon.loop_indices):
                    if vertex_index >= len(color_data):
                        continue
                        
                    color = color_data[vertex_index]
                    vertex_color.data[loop_index].color = color[0] / 255, color[1] / 255, color[2] / 255, color[3] / 255

        # Only add preskinned attributes if they don't already exist
        if imported_mesh is not None and imported_mesh.data.attributes.get("PS_LOCAL_POSITION") is None:
            mesh_data = imported_mesh.data
            vert_count = len(mesh_data.vertices)
            positions = np.empty(vert_count * 3, dtype=np.float32)
            normals = np.empty(vert_count * 3, dtype=np.float32)
            mesh_data.vertices.foreach_get("co", positions)
            mesh_data.vertices.foreach_get("normal", normals)

            # Bulk foreach_set — per-vertex bpy writes are multi-minute on large meshes.
            preskinned_pos = mesh_data.attributes.new(domain="POINT", type="FLOAT_VECTOR", name="PS_LOCAL_POSITION")
            preskinned_normal = mesh_data.attributes.new(domain="POINT", type="FLOAT_VECTOR", name="PS_LOCAL_NORMAL")
            preskinned_pos.data.foreach_set("vector", positions)
            preskinned_normal.data.foreach_set("vector", normals)

            self.update_preskinned_bounds(imported_mesh, True)

        imported_object.parent = parent
        imported_object.rotation_euler = make_euler(mesh.get("Rotation"))
        imported_object.location = make_vector(mesh.get("Location"), unreal_coords_correction=True) * self.scale
        imported_object.scale = make_vector(mesh.get("Scale"))
        
        if self.options.get("ImportAt3DCursor") and can_spawn_at_3d_cursor:
            imported_object.location += bpy.context.scene.cursor.location

        # Material Porter fork: a spline mesh's bend, custom data, white vertex colours
        from ...material_porter.placement import after_import
        after_import(mesh, imported_object, imported_mesh, self.scale)

        # (Material Porter fork: a particle effect's pieces aren't parts of the item: an outfit's
        # armatures are merged and its bones reparented over these)
        if not mesh.get("MPEffect"):
            self.imported_meshes.append({
                "Skeleton": imported_object,
                "Mesh": imported_mesh,
                "Type": part_type,
                "Meta": mesh.get("Meta")
            })

        # metadata handling
        meta = self.gather_metadata("PoseAsset")

        # pose asset (Material Porter fork: not for a world's meshes; making each one active
        # resyncs the view layer per object. Nor for a particle effect's pieces: an outfit's
        # idle effect comes after its head, whose poses are no business of a sprite's)
        if imported_mesh is not None and self.type not in [EExportType.WORLD, EExportType.PREFAB] and not mesh.get("MPEffect"):
            bpy.context.view_layer.objects.active = imported_mesh
            self.import_pose_asset_data(meta, get_selected_armature(), part_type)

        # end

        match part_type:
            case EFortCustomPartType.BODY:
                meta.update(self.gather_metadata("SkinColor"))
            case EFortCustomPartType.HEAD:
                meta.update(self.gather_metadata("MorphNames", "HatType"))
                meta["IsHead"] = True
                shape_keys = imported_mesh.data.shape_keys
                if (morphs := meta.get("MorphNames")) and (morph_name := morphs.get(meta.get("HatType"))) and shape_keys is not None:
                    for key in shape_keys.key_blocks:
                        if key.name.casefold() == morph_name.casefold():
                            key.value = 1.0

        meta["TextureData"] = mesh.get("TextureData")

        # Material Porter fork: each slot's material built once, the one it ends with - not a default a
        # style swaps out (an exact material costs a second or two to build)
        # (a style swaps by the name the slot shows: TextureData's override material's on slot 0, import_material)
        td_override = next((td.get("OverrideMaterial") for td in mesh.get("TextureData") if td.get("OverrideMaterial")), None)
        final = {}
        for material in mesh.get("Materials") + mesh.get("OverrideMaterials"):
            final[material.get("Slot")] = material
        for variant_override_material in self.override_materials:
            for index, material in list(final.items()):
                shown = td_override if td_override and material.get("Slot") == 0 else material
                if shown.get("Name") == variant_override_material.get("MaterialNameToSwap"):
                    final[index] = variant_override_material.get("Material")

        for material in mesh.get("Materials"):
            index = material.get("Slot")
            if index >= len(imported_mesh.material_slots) or final.get(index) is not material:
                continue

            self.import_material(imported_mesh.material_slots[index], material, meta)

        for override_material in mesh.get("OverrideMaterials"):
            index = override_material.get("Slot")
            if index >= len(imported_mesh.material_slots) or final.get(index) is not override_material:
                continue

            self.import_material(imported_mesh.material_slots[index], override_material, meta)

        for variant_override_material in self.override_materials:
            swap_material = variant_override_material.get("Material")
            slots = [slot for index, slot in enumerate(imported_mesh.material_slots) if final.get(index) is swap_material]
            for slot in slots:
                self.import_material(slot, swap_material, meta)
                
        for texture_data in mesh.get("TextureData"):
            if not (td_override_material := texture_data.get("OverrideMaterial")):
                continue
                
            Log.info(f"TextureData Override {td_override_material.get('Path')}")

            index = td_override_material.get("Slot")
            if index >= len(imported_mesh.material_slots):
                continue

            overridden_material = imported_mesh.material_slots[index]
            slots = where(imported_mesh.material_slots,
                          lambda slot: slot.name == overridden_material.name)
            for slot in slots:
                self.import_material(slot, td_override_material, meta)
                
        # Material Porter fork: shell fur (material_porter.shells): the base layer's materials, the shells' built
        if mesh.get("MPShells") and imported_mesh is not None:
            from ...material_porter import shells as mp_shells
            mp_shells.prepare(self, imported_mesh, mesh.get("MPShells"), meta)

        self.import_light_data(mesh.get("Lights"), imported_object)

        for child in mesh.get("Children"):
            self.import_model(child, parent=imported_object)
            
        instances = mesh.get("Instances")
        if len(instances) > 0:
            mesh_data = imported_mesh.data

            instance_materials = []
            for slot_mat in imported_mesh.material_slots:
                instance_materials.append(slot_mat.material)

            imported_object.select_set(True)
            bpy.ops.object.delete()
            
            instance_parent = bpy.data.objects.new("InstanceParent_" + name, None)
            instance_parent.parent = parent
            instance_parent.rotation_euler = make_euler(mesh.get("Rotation"))
            instance_parent.location = make_vector(mesh.get("Location"), unreal_coords_correction=True) * self.scale
            instance_parent.scale = make_vector(mesh.get("Scale"))
            bpy.context.collection.objects.link(instance_parent)
            
            for instance_index, instance_transform in enumerate(instances):
                instance_name = f"Instance_{instance_index}_" + name
                
                Log.info(f"Importing Instance: {instance_name} {instance_index} / {len(instances)}")
                
                instance_object = bpy.data.objects.new(f"Instance_{instance_index}_" + name, mesh_data)
                self.collection.objects.link(instance_object)
    
                instance_object.parent = instance_parent
                instance_object.rotation_euler = make_euler(instance_transform.get("Rotation"))
                instance_object.location = make_vector(instance_transform.get("Location"), unreal_coords_correction=True) * self.scale
                instance_object.scale = make_vector(instance_transform.get("Scale"))
            
                for i, slot_mat in enumerate(instance_materials):
                    instance_object.material_slots[i].material = slot_mat


        return imported_object
    

    def update_preskinned_bounds(self, imported_mesh, new_attribute=False):
        mesh_data = imported_mesh.data
        vert_count = len(mesh_data.vertices)
        if vert_count == 0:
            return

        corners = imported_mesh.bound_box
        x_coords, y_coords, z_coords = zip(*corners)
        bbox_min = np.array(
            (min(x_coords), min(y_coords), min(z_coords)),
            dtype=np.float32,
        )
        bbox_max = np.array(
            (max(x_coords), max(y_coords), max(z_coords)),
            dtype=np.float32,
        )
        ranges = bbox_max - bbox_min
        safe_ranges = np.where(ranges != 0.0, ranges, 1.0)

        positions = np.empty(vert_count * 3, dtype=np.float32)
        mesh_data.vertices.foreach_get("co", positions)
        pts = positions.reshape(vert_count, 3)
        mapped = (pts - bbox_min) / safe_ranges
        mapped = np.where(ranges != 0.0, mapped, 0.0).astype(np.float32, copy=False)

        if new_attribute:
            preskinned_bounds = mesh_data.attributes.new(
                domain="POINT", type="FLOAT_VECTOR", name="PS_LOCAL_BOUNDS"
            )
        else:
            preskinned_bounds = mesh_data.attributes.get("PS_LOCAL_BOUNDS")
            if preskinned_bounds is None:
                return

        preskinned_bounds.data.foreach_set("vector", mapped.ravel())
            
    def parent_deform_bones(self, skeleton, prefixes):
        bpy.context.view_layer.objects.active = skeleton
        bpy.ops.object.mode_set(mode='EDIT')
    
        edit_bones = skeleton.data.edit_bones
    
        for bone in edit_bones:
            for prefix in prefixes:
                if bone.name.startswith(prefix):
                    parent_name = bone.name[len(prefix):]
    
                    if parent_bone := edit_bones.get(parent_name):
                        bone.parent = parent_bone
                        bone.use_connect = False
                    break
    
        bpy.ops.object.mode_set(mode='OBJECT')

    def parent_bones(self, skeleton, mappings):
        bpy.context.view_layer.objects.active = skeleton
        bpy.ops.object.mode_set(mode='EDIT')

        edit_bones = skeleton.data.edit_bones

        for target_name, parent_name in mappings.items():
            if not (target_bone := edit_bones.get(target_name)):
                continue
                
            if not (parent_bone := edit_bones.get(parent_name)):
                continue

            target_bone.parent = parent_bone
            target_bone.use_connect = False

        bpy.ops.object.mode_set(mode='OBJECT')
    
    def import_light_data(self, lights, parent=None):
        if not lights:
            return

        for point_light in lights.get("PointLights") or []:
            self.import_point_light(point_light, parent)

        # Material Porter fork: a level's spot and rect lights (FP's own export has point lights only)
        for spot_light in lights.get("SpotLights") or []:
            self.import_spot_light(spot_light, parent)

        for rect_light in lights.get("RectLights") or []:
            self.import_rect_light(rect_light, parent)

    def import_point_light(self, point_light, parent=None):
        light, light_data = self.create_light(point_light, 'POINT', 4 * pi, parent)
        # (a point light looks the same whichever way it faces)
        light.scale = make_vector(point_light.get("Scale"))
        light_data.shadow_soft_size = point_light.get("Radius") * self.scale

    def import_spot_light(self, spot_light, parent=None):
        outer = max(spot_light.get("OuterConeAngle"), 0.5)
        inner = min(max(spot_light.get("InnerConeAngle"), 0.0), outer)
        light, light_data = self.create_light(spot_light, 'SPOT', spot_solid_angle(inner, outer), parent)
        # UE's cone angles are from the axis, Blender's size is the whole cone; its blend is the fraction of the cone
        # that fades out, where UE's inner angle is where the fade starts
        light_data.spot_size = radians(min(max(outer * 2, 1.0), 179.0))
        light_data.spot_blend = min(max(1.0 - inner / outer, 0.0), 1.0)
        light_data.shadow_soft_size = spot_light.get("Radius") * self.scale

    def import_rect_light(self, rect_light, parent=None):
        # (a rect light's lumens leave one face, as a Lambertian surface's do: candelas along its axis are lumens / pi)
        light, light_data = self.create_light(rect_light, 'AREA', pi, parent)
        light_data.shape = 'RECTANGLE'
        # the light is turned so that its X is UE's up (SourceHeight) and its Y UE's right (SourceWidth)
        light_data.size = rect_light.get("SourceHeight") * self.scale
        light_data.size_y = rect_light.get("SourceWidth") * self.scale

    def create_light(self, data, light_type, solid_angle, parent=None):
        """
        A light object where UE has the light, with its colour (and temperature), brightness, reach and shadows.
        The caller gives its shape (spot cone, rect size...) and the solid angle its lumens spread over.
        """
        name = data.get("Name")
        light_data = bpy.data.lights.new(name=name, type=light_type)
        light = bpy.data.objects.new(name=name, object_data=light_data)
        self.collection.objects.link(light)

        light.parent = parent
        rotation = make_euler(data.get("Rotation"))
        if light_type != 'POINT':
            # a UE spot or rect light shines along its X axis, a Blender one along its -Z
            rotation = (rotation.to_matrix() @ Matrix.Rotation(radians(-90), 3, 'Y')).to_euler()
            light.scale = (1, 1, 1)
        light.rotation_euler = rotation
        light.location = make_vector(data.get("Location"), unreal_coords_correction=True) * self.scale

        color = data.get("Color")
        rgb = [color["R"], color["G"], color["B"]]
        if data.get("UseTemperature"):
            rgb = [c * t for c, t in zip(rgb, kelvin_tint(data.get("Temperature") or 6500.0))]
        # (a light's colour stops at white: what a hot tint goes past it moves to its power)
        peak = max(max(rgb), 1.0)
        light_data.color = [c / peak for c in rgb]
        light_data.energy = light_candelas(data, solid_angle) * LIGHT_CANDELA_TO_WATTS * peak
        light_data.use_custom_distance = True
        light_data.cutoff_distance = data.get("AttenuationRadius") * self.scale
        light_data.use_shadow = data.get("CastShadows")
        # what UE stored, to retune the power by
        light_data["ue_intensity"] = data.get("Intensity")
        light_data["ue_units"] = data.get("IntensityUnits") or "Candelas"
        return light, light_data

    def import_mesh(self, path: str, can_reorient=True):
        options = UEModelOptions(scale_factor=self.scale,
                                 reorient_bones=self.options.get("ReorientBones") and can_reorient,
                                 bone_length=self.options.get("BoneLength"),
                                 import_sockets=self.options.get("ImportSockets"),
                                 import_virtual_bones=self.options.get("ImportVirtualBones"),
                                 import_collision=self.options.get("ImportCollision"),
                                 target_lod=self.options.get("TargetLOD"),
                                 allowed_reorient_children=allowed_reorient_children)

        path = path[1:] if path.startswith("/") else path

        mesh_path = os.path.join(self.assets_root, path.split(".")[0] + ".uemodel")

        return UEFormatImport(options).import_file(mesh_path)
