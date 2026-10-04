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


def mark_part_bounds(parts):
    """Each part's own bounds (its centre and half size, world space) on its vertices, before
    "Merge Armatures" joins the parts into one mesh: UE's Object Position, bounds and radius
    are a part's own component's (Cyclo's storm core is centred on the head's), and the joined
    object's would be the whole outfit's. settle_part_bounds brings them into the joined mesh."""
    import numpy as np
    from mathutils import Vector
    from .ue_graph import PART_CENTRE, PART_HALF
    for part in parts:
        obj = part.get("Mesh")
        if obj is None or obj.type != 'MESH' or not len(obj.data.vertices):
            continue
        me = obj.data
        co = np.empty(len(me.vertices) * 3, dtype=np.float32)
        me.vertices.foreach_get("co", co)
        co = co.reshape(-1, 3)
        lo, hi = co.min(0), co.max(0)
        m = obj.matrix_world
        centre = m @ Vector(((lo + hi) * 0.5).tolist())
        scale = m.to_scale()
        half = [float(h) * abs(k) for h, k in zip((hi - lo) * 0.5, scale)]
        for name, v in ((PART_CENTRE, tuple(centre)), (PART_HALF, tuple(half))):
            a = me.attributes.get(name) or me.attributes.new(name, 'FLOAT_VECTOR', 'POINT')
            a.data.foreach_set("vector", np.tile(np.array(v, dtype=np.float32), len(me.vertices)))


def settle_part_bounds(master, parts):
    """The joined mesh's part bounds in its own space (mark_part_bounds wrote them in world
    space); a part that stayed apart (attached to a socket) keeps its object's own bounds."""
    import numpy as np
    from .ue_graph import PART_CENTRE, PART_HALF
    for part in parts:
        obj = part.get("Mesh")
        try:
            if obj is None or obj == master or obj.type != 'MESH':
                continue
        except ReferenceError:
            continue        # joined into the master
        for name in (PART_CENTRE, PART_HALF):
            if (a := obj.data.attributes.get(name)) is not None:
                obj.data.attributes.remove(a)
    if master is None or master.type != 'MESH':
        return
    me = master.data
    centre, half = me.attributes.get(PART_CENTRE), me.attributes.get(PART_HALF)
    if centre is None or half is None:
        return
    n = len(me.vertices)
    c = np.empty(n * 3, dtype=np.float32)
    centre.data.foreach_get("vector", c)
    c = c.reshape(-1, 3)
    inv = np.array(master.matrix_world.inverted(), dtype=np.float32)
    c = c @ inv[:3, :3].T + inv[:3, 3]
    centre.data.foreach_set("vector", c.ravel())
    h = np.empty(n * 3, dtype=np.float32)
    half.data.foreach_get("vector", h)
    scale = np.array([abs(k) or 1.0 for k in master.matrix_world.to_scale()], dtype=np.float32)
    half.data.foreach_set("vector", (h.reshape(-1, 3) / scale).ravel())


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
