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
    if (names := mesh.get("MPLayerNames")) is not None:
        # a landscape's weight layers, by the LayerName its materials sample (and marked as a
        # landscape, an unpainted one's empty table too: its materials are built per set of
        # painted layers, hook.build_exact)
        for attribute in target.data.color_attributes:
            if attribute.name in names:
                attribute.name = names[attribute.name]
        target["mp_landscape"] = 1.0
    if cpd := mesh.get("MPPrimitiveData"):
        target["mp_cpd"] = 1.0
        # (how many: an index past them reads the material parameter's default, as in UE)
        target["mp_cpd_n"] = float(len(cpd))
        for j, x in enumerate(cpd):
            target["mp_cpd%d" % j] = float(x)
    # a component that casts no shadow in the game (a lamp's housing around its light)
    if mesh.get("MPCastShadow") is False:
        target.visible_shadow = False
    if pic := mesh.get("MPInstanceData"):
        target["mp_pic"] = float(len(pic))
        for j, x in enumerate(pic):
            target["mp_pic%d" % j] = float(x)


_UVS = {}       # material name -> (its session id, the UV maps its trees read)


def uvs_read(material):
    """The UV maps (by name) an exact material's trees read, through nested groups too; walked
    once per material (a name, its id: a material built again under the same name is walked again)."""
    name = material.name
    hit = _UVS.get(name)
    if hit is not None and hit[0] == material.session_uid:
        return hit[1]
    names = set()
    if name.startswith("MP ") and material.node_tree is not None:
        seen, todo = set(), [material.node_tree]
        while todo:
            tree = todo.pop()
            if tree is None or tree.name in seen:
                continue
            seen.add(tree.name)
            for node in tree.nodes:
                kind = node.type
                if kind == 'GROUP':
                    todo.append(node.node_tree)
                elif kind in ('UVMAP', 'NORMAL_MAP', 'TANGENT') and node.uv_map:
                    names.add(node.uv_map)
    _UVS[name] = (material.session_uid, names)
    return names


def ensure_uvs(mesh, material):
    """UE binds a texture coordinate a mesh doesn't have to the mesh's last UV channel (the vertex
    factory fills the remaining streams with it): each UV map the material reads that the mesh lacks
    becomes a copy of the mesh's last one (Blender samples one texel from a missing map). A mesh with
    no UV map gets none; a mesh's data is shared by its instances, so one that has the map is left."""
    layers = mesh.uv_layers
    count = len(layers)
    if not count:
        return
    missing = [n for n in sorted(uvs_read(material)) if n not in layers]
    if not missing or not len(mesh.loops):
        return
    import numpy
    buf = numpy.empty(len(mesh.loops) * 2, dtype=numpy.float32)
    layers[count - 1].data.foreach_get("uv", buf)
    for name in missing:
        try:
            layer = layers.new(name=name, do_init=False)
        except RuntimeError:        # (Blender's limit of eight UV maps)
            return
        if layer is None:
            return
        layer.data.foreach_set("uv", buf)


def ensure_slot_uvs(slot, material):
    """ensure_uvs for a mesh object's material slot (not a material read alone)."""
    obj = slot.id_data
    if material is not None and getattr(obj, "type", None) == 'MESH':
        ensure_uvs(obj.data, material)


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
