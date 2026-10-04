"""Material Porter fork: shell fur (UE's ShellMesh plugin), as Blender draws it.

The game draws a furry part's mesh again Count times, copy n (1 to Count) pushed out along its
normals by Depth * n / Count, with its slot's shell material - whose fur pattern, length map and
root-to-tip colour read the copy's layer (ShellMeshNormalizedShellLayer: n / Count) - and the mesh
itself with its slot's base-layer material. Here a Geometry Nodes modifier makes the copies from
the deformed mesh (after its armature: the fur follows the pose), each tagged with its layer
(SHELL_LAYER, SHELL_LAYER_N, SHELL_COUNT: what the shell material reads), on the faces whose
material has a shell material. Its Shells input sets how many are drawn (fewer: lighter, coarser
fur); its Length scales how far they reach.
"""
import bpy

from .ue_graph import SHELL_COUNT, SHELL_LAYER, SHELL_LAYER_N

KEY = "mp_shells"           # on the context: [{"base", "shell", "depth", "count"}] for apply
MODIFIER = "Shell Fur"


def _log(message):
    print("[FNPORTING] [Material Porter] " + message)


def prepare(context, mesh_object, shells, meta):
    """A part's shells as it's imported (ExportContext.ShellFur): its slots take their base-layer
    materials, its shell materials are built, and what apply needs is kept on the context."""
    if not shells or mesh_object is None:
        return
    slots = mesh_object.material_slots
    for data in shells.get("BaseMaterials") or []:
        slot = data.get("Slot", 0)
        if slot < len(slots) and slots[slot].material is not None:
            context.import_material(slots[slot], data, meta)
    pairs = context.__dict__.setdefault(KEY, [])
    for data in shells.get("Materials") or []:
        slot = data.get("Slot", 0)
        if slot >= len(slots) or slots[slot].material is None:
            continue
        base = slots[slot].material
        # FP builds a material into a slot: a slot of its own for the time it takes
        mesh_object.data.materials.append(bpy.data.materials.new("MP shell placeholder"))
        index = len(mesh_object.data.materials) - 1
        placeholder = mesh_object.data.materials[index]
        context.import_material(mesh_object.material_slots[index], data, meta)
        shell = mesh_object.material_slots[index].material
        mesh_object.data.materials.pop(index=index)
        if placeholder is not None and placeholder != shell and placeholder.users == 0:
            bpy.data.materials.remove(placeholder)
        if shell is None or shell == base:
            continue
        pairs.append({"base": base, "shell": shell, "depth": float(shells.get("Depth", 1.0)) * context.scale,
                      "count": int(shells.get("Count", 16))})


def apply(context, objects):
    """The shell modifier on each of these meshes whose materials have shells (an outfit's joined
    mesh, else each part)."""
    pairs = context.__dict__.get(KEY) or []
    if not pairs:
        return
    for o in objects:
        try:
            if o is None or o.type != 'MESH':
                continue
        except ReferenceError:      # (a part joined into the body is gone)
            continue
        mats = set(m for m in o.data.materials if m is not None)
        mine = [p for p in pairs if p["base"] in mats]
        if not mine:
            continue
        count = max(p["count"] for p in mine)
        mod = o.modifiers.new(MODIFIER, 'NODES')
        mod.node_group = shell_group(o.name, mine)
        from ..processing.utils import set_geo_nodes_param
        set_geo_nodes_param(mod, "Shells", count, getattr(context, "version_profile", None))
        _log("%s: shell fur, %d shells over %s" % (o.name, count, ", ".join(p["base"].name for p in mine)))


def shell_group(name, pairs):
    """The modifier's node group: per base material, its faces copied Shells times as instances
    (cheap to join), each copy's layer stored, realized, pushed out along its normals and given
    the shell material; the mesh itself as it was."""
    t = bpy.data.node_groups.new("MP Shell Fur " + name, 'GeometryNodeTree')
    t.interface.new_socket("Geometry", in_out='INPUT', socket_type='NodeSocketGeometry')
    shells = t.interface.new_socket("Shells", in_out='INPUT', socket_type='NodeSocketInt')
    shells.default_value, shells.min_value, shells.max_value = 16, 0, 64
    shells.description = "How many shells are drawn (the game's nearest level of detail draws the asset's count)"
    length = t.interface.new_socket("Length", in_out='INPUT', socket_type='NodeSocketFloat')
    length.default_value, length.min_value = 1.0, 0.0
    length.description = "How far the shells reach, of the asset's depth"
    t.interface.new_socket("Geometry", in_out='OUTPUT', socket_type='NodeSocketGeometry')
    N, L = t.nodes, t.links
    gi = N.new("NodeGroupInput"); gi.location = (-1400, 0)
    go = N.new("NodeGroupOutput"); go.location = (900, 0)
    join = N.new("GeometryNodeJoinGeometry"); join.location = (700, 0)
    L.new(gi.outputs["Geometry"], join.inputs[0])
    for k, p in enumerate(pairs):
        y = -300 * (k + 1)

        def node(kind, x, **props):
            n = N.new(kind)
            n.location = (x, y)
            for key, v in props.items():
                setattr(n, key, v)
            return n
        sel = node("GeometryNodeMaterialSelection", -1200)
        sel.inputs["Material"].default_value = p["base"]
        sep = node("GeometryNodeSeparateGeometry", -1000, domain='FACE')
        L.new(gi.outputs["Geometry"], sep.inputs["Geometry"]); L.new(sel.outputs[0], sep.inputs["Selection"])
        inst = node("GeometryNodeGeometryToInstance", -820)
        L.new(sep.outputs["Selection"], inst.inputs[0])
        dup = node("GeometryNodeDuplicateElements", -640, domain='INSTANCE')
        L.new(inst.outputs[0], dup.inputs["Geometry"]); L.new(gi.outputs["Shells"], dup.inputs["Amount"])
        # layer n = the copy's index + 1, on its instance: realized, on each of its points
        one = node("ShaderNodeMath", -460, operation='ADD')
        one.inputs[1].default_value = 1.0
        L.new(dup.outputs["Duplicate Index"], one.inputs[0])
        store = node("GeometryNodeStoreNamedAttribute", -460, data_type='FLOAT', domain='INSTANCE')
        store.location = (-460, y + 160)
        store.inputs["Name"].default_value = SHELL_LAYER
        L.new(dup.outputs["Geometry"], store.inputs["Geometry"]); L.new(one.outputs[0], store.inputs["Value"])
        real = node("GeometryNodeRealizeInstances", -280)
        L.new(store.outputs[0], real.inputs[0])
        layer = node("GeometryNodeInputNamedAttribute", -280, data_type='FLOAT')
        layer.location = (-280, y - 160)
        layer.inputs["Name"].default_value = SHELL_LAYER
        frac = node("ShaderNodeMath", -100, operation='DIVIDE')
        frac.location = (-100, y - 160)
        L.new(layer.outputs["Attribute"], frac.inputs[0]); L.new(gi.outputs["Shells"], frac.inputs[1])
        store_n = node("GeometryNodeStoreNamedAttribute", -100, data_type='FLOAT', domain='POINT')
        store_n.inputs["Name"].default_value = SHELL_LAYER_N
        L.new(real.outputs[0], store_n.inputs["Geometry"]); L.new(frac.outputs[0], store_n.inputs["Value"])
        store_c = node("GeometryNodeStoreNamedAttribute", 80, data_type='FLOAT', domain='POINT')
        store_c.inputs["Name"].default_value = SHELL_COUNT
        L.new(store_n.outputs[0], store_c.inputs["Geometry"]); L.new(gi.outputs["Shells"], store_c.inputs["Value"])
        # pushed out along its normals: depth * n / count
        reach = node("ShaderNodeMath", 80, operation='MULTIPLY')
        reach.location = (80, y - 160)
        L.new(frac.outputs[0], reach.inputs[0]); L.new(gi.outputs["Length"], reach.inputs[1])
        depth = node("ShaderNodeMath", 260, operation='MULTIPLY')
        depth.location = (260, y - 160)
        depth.inputs[1].default_value = p["depth"]
        L.new(reach.outputs[0], depth.inputs[0])
        normal = node("GeometryNodeInputNormal", 260)
        normal.location = (260, y - 320)
        scale = node("ShaderNodeVectorMath", 440, operation='SCALE')
        scale.location = (440, y - 220)
        L.new(normal.outputs[0], scale.inputs[0]); L.new(depth.outputs[0], scale.inputs["Scale"])
        move = node("GeometryNodeSetPosition", 440)
        L.new(store_c.outputs[0], move.inputs["Geometry"]); L.new(scale.outputs[0], move.inputs["Offset"])
        mat = node("GeometryNodeSetMaterial", 600)
        mat.inputs["Material"].default_value = p["shell"]
        L.new(move.outputs[0], mat.inputs["Geometry"])
        L.new(mat.outputs[0], join.inputs[0])
    L.new(join.outputs[0], go.inputs[0])
    return t
