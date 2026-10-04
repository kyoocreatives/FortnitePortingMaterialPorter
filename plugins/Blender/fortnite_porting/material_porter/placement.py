"""Material Porter fork: what Material Porter's map reader adds to a placed mesh
(the fork's MaterialPorterMesh fields), applied once FP has made its object."""
import bpy
from mathutils import Matrix

from .meshes import spline_bend, white_colors


def after_import(mesh, obj, mesh_obj, scale):
    """A spline mesh's bend (UE's slice transform, on a copy of the mesh),
    white vertex colours where the mesh has none (what UE reads there), and
    the component's custom primitive data / an instance's custom data as
    the object properties the exact materials read (mp_cpd<i>, mp_pic<i>)."""
    if obj is not None and (bone := mesh.get("MPParentBone")):
        follow_bone(obj, bone)
    if (fx := mesh.get("MPEffect") or {}).get("Kind") == "Mesh":
        from . import effects
        effects.tag_mesh(mesh_obj if mesh_obj is not None else obj, fx)
    target = mesh_obj if mesh_obj is not None else obj
    if target is None or target.type != 'MESH':
        return
    if sp := mesh.get("MPSpline"):
        target.data = spline_bend(target.data, sp, scale)
    if bpy.app.version >= (5, 0, 0):
        white_colors(target.data)
    if names := mesh.get("MPLayerNames"):
        # a landscape's weight layers, by the LayerName its materials sample
        for attribute in target.data.color_attributes:
            if attribute.name in names:
                attribute.name = names[attribute.name]
    if cpd := mesh.get("MPPrimitiveData"):
        target["mp_cpd"] = 1.0
        for j, x in enumerate(cpd):
            target["mp_cpd%d" % j] = float(x)
    if pic := mesh.get("MPInstanceData"):
        target["mp_pic"] = float(len(pic))
        for j, x in enumerate(pic):
            target["mp_pic%d" % j] = float(x)


def follow_bone(obj, bone):
    """A weapon's mod follows its attach bone: the object FP parented to the weapon's armature
    (placed in the armature's space) is parented to the bone instead, where it is."""
    armature = obj.parent
    if armature is None or armature.type != 'ARMATURE':
        return
    rest = armature.data.bones.get(bone)
    if rest is None:
        return
    # a bone's children hang from its tail
    bone_space = rest.matrix_local @ Matrix.Translation((0.0, rest.length, 0.0))
    local = obj.matrix_basis.copy()
    obj.parent_type = 'BONE'
    obj.parent_bone = rest.name
    obj.matrix_parent_inverse = Matrix.Identity(4)
    obj.matrix_basis = bone_space.inverted() @ local


def after_world(objects):
    """A world import's water surfaces measured: the depth under each
    (Material Porter's water.bake_water), which their materials read."""
    if bpy.app.version < (5, 0, 0):
        return
    try:
        from .water import bake_water
        bake_water(objects)
    except Exception as e:
        print("[Material Porter] water depth not measured (%s: %s)" % (type(e).__name__, e))
