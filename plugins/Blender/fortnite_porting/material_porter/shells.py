"""Material Porter fork: shell fur (UE's ShellMesh plugin), as Blender draws it.

The game draws a furry part's mesh as Count layers: layer 0 the mesh itself, with its slot's
base-layer material, layers 1 to Count - 1 copies of it at the mesh, with its slot's shell
material. The ShellMesh deformer gives each copy's vertices their layer (normalized, layer /
(Count - 1): 1 at the tip) and offset (along the normal, the asset's depth times that): the shell material's own
World Position Offset moves them - by a share of that offset rising from root to tip, a lean along
its flow map (combed fur), all scaled by its length map (fur ending where the map does) - and its
fur pattern, thickness and colour read the layer. The base layer's shrinks in under the fur.

Here a Geometry Nodes modifier makes the copies from the deformed mesh (after its armature: the
fur follows the pose), each point tagged with what the shell material reads (SHELL_LAYER,
SHELL_LAYER_N, SHELL_COUNT, SHELL_OFFSET, SHELL_VECTOR), on the faces whose material has a shell
material; the materials' World Position Offset is Blender's displacement (built with "moves").
Its Shells input sets how many are drawn (fewer: lighter, coarser fur); its Length scales the
depth.
"""
import bpy

from .ue_graph import SHELL_COUNT, SHELL_LAYER, SHELL_LAYER_N, SHELL_OFFSET, SHELL_VECTOR

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
    # each slot's own material (the game's name), before a base-layer material takes its place
    own = {i: (s.material.get("OriginalName") or s.material.name.removeprefix("MP ")).split(".")[0]
           for i, s in enumerate(slots) if s.material is not None}
    for data in shells.get("BaseMaterials") or []:
        slot = data.get("Slot", 0)
        if slot < len(slots) and slots[slot].material is not None:
            data["MPMoves"] = True      # (its World Position Offset: in under the fur)
            context.import_material(slots[slot], data, meta)
    pairs = context.__dict__.setdefault(KEY, [])
    for data in shells.get("Materials") or []:
        slot = data.get("Slot", 0)
        if slot >= len(slots) or slots[slot].material is None:
            continue
        # a slot whose "shell material" is its own (Crash's eyes and nose on his furry head's
        # mesh): a plain material the game draws no fur with
        if data.get("Name") in (own.get(slot), slots[slot].material.get("OriginalName")):
            continue
        base = slots[slot].material
        # FP builds a material into a slot: a slot of its own for the time it takes
        mesh_object.data.materials.append(bpy.data.materials.new("MP shell placeholder"))
        index = len(mesh_object.data.materials) - 1
        placeholder = mesh_object.data.materials[index]
        data["MPMoves"] = True          # (its World Position Offset places the layers)
        context.import_material(mesh_object.material_slots[index], data, meta)
        shell = mesh_object.material_slots[index].material
        mesh_object.data.materials.pop(index=index)
        if placeholder is not None and placeholder != shell and placeholder.users == 0:
            bpy.data.materials.remove(placeholder)
        if shell is None or shell == base:
            continue
        # a material that doesn't move its layers (FP's own, or no offset translated): pushed out here
        moves = any(out.inputs["Displacement"].is_linked for out in shell.node_tree.nodes
                    if out.bl_idname == "ShaderNodeOutputMaterial") and shell.displacement_method != 'BUMP'
        if not shells.get("CastShadows"):
            no_shadow(shell)
        pairs.append({"base": base, "shell": shell, "depth": float(shells.get("Depth", 1.0)) * context.scale,
                      "count": int(shells.get("Count", 16)), "moves": moves, "tuck": 0.02 * context.scale})


def no_shadow(mat):
    """The material casts no shadow (the game's shells don't: bCastShadows): seen through by
    shadow rays (Eevee's shadow maps too), drawn as it was for every other ray."""
    if mat.get("mp_no_shadow"):
        return
    tree = mat.node_tree
    for out in [n for n in tree.nodes if n.bl_idname == "ShaderNodeOutputMaterial"]:
        sock = out.inputs["Surface"]
        if not sock.is_linked:
            continue
        src = sock.links[0].from_socket
        path = tree.nodes.new("ShaderNodeLightPath")
        clear = tree.nodes.new("ShaderNodeBsdfTransparent")
        mix = tree.nodes.new("ShaderNodeMixShader")
        mix.label, clear.label = "no shadow (the game's shells cast none)", "shadow rays pass"
        x, y = out.location
        path.location, clear.location, mix.location = (x - 200, y + 300), (x - 200, y - 220), (x, y + 60)
        out.location = (x + 200, y)
        tree.links.new(path.outputs["Is Shadow Ray"], mix.inputs[0])
        tree.links.new(src, mix.inputs[1])
        tree.links.new(clear.outputs[0], mix.inputs[2])
        tree.links.new(mix.outputs[0], sock)
    if hasattr(mat, "use_transparent_shadow"):
        mat.use_transparent_shadow = True      # (Eevee reads the shadow ray's transparency only so)
    mat["mp_no_shadow"] = 1


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
        # Cycles counts each shell a ray crosses (in and out, camera and shadow rays alike, through
        # overlapping parts - a wrist under a furry cuff) as a transparent bounce; past its limit
        # (8 by default) the fur, and what it shadows, goes black
        scene = bpy.context.scene
        need = min(16 * count, 1024)
        if scene is not None and hasattr(scene, "cycles") and scene.cycles.transparent_max_bounces < need:
            scene.cycles.transparent_max_bounces = need
            _log("Cycles' transparent bounces raised to %d (shell fur)" % need)


def shell_group(name, pairs):
    """The modifier's node group: per base material, its faces copied Shells times as instances
    (cheap to join), each copy's layer stored, realized, its points tagged with their layer and
    offset (along the normal) and given the shell material - pushed out here only where the
    material doesn't move them itself; the mesh itself as it was."""
    t = bpy.data.node_groups.new("MP Shell Fur " + name, 'GeometryNodeTree')
    t.interface.new_socket("Geometry", in_out='INPUT', socket_type='NodeSocketGeometry')
    shells = t.interface.new_socket("Shells", in_out='INPUT', socket_type='NodeSocketInt')
    shells.default_value, shells.min_value, shells.max_value = 16, 0, 64
    shells.description = "How many layers, the mesh itself one (the game's nearest level of detail draws the asset's count)"
    length = t.interface.new_socket("Length", in_out='INPUT', socket_type='NodeSocketFloat')
    length.default_value, length.min_value = 1.0, 0.0
    length.description = "The shells' depth, of the asset's"
    t.interface.new_socket("Geometry", in_out='OUTPUT', socket_type='NodeSocketGeometry')
    N, L = t.nodes, t.links
    gi = N.new("NodeGroupInput"); gi.location = (-1400, 0)
    go = N.new("NodeGroupOutput"); go.location = (1100, 0)
    join = N.new("GeometryNodeJoinGeometry"); join.location = (900, 0)
    L.new(gi.outputs["Geometry"], join.inputs[0])
    for k, p in enumerate(pairs):
        y = -400 * (k + 1)

        def node(kind, x, dy=0, **props):
            n = N.new(kind)
            n.location = (x, y + dy)
            for key, v in props.items():
                setattr(n, key, v)
            return n

        def store(geometry, x, name, value, kind='FLOAT', domain='POINT'):
            n = node("GeometryNodeStoreNamedAttribute", x, data_type=kind, domain=domain)
            n.inputs["Name"].default_value = name
            L.new(geometry, n.inputs["Geometry"]); L.new(value, n.inputs["Value"])
            return n.outputs[0]
        sel = node("GeometryNodeMaterialSelection", -1200)
        sel.inputs["Material"].default_value = p["base"]
        sep = node("GeometryNodeSeparateGeometry", -1000, domain='FACE')
        L.new(gi.outputs["Geometry"], sep.inputs["Geometry"]); L.new(sel.outputs[0], sep.inputs["Selection"])
        inst = node("GeometryNodeGeometryToInstance", -820)
        L.new(sep.outputs["Selection"], inst.inputs[0])
        # layers 1 to Shells - 1 (layer 0: the mesh itself, the base layer)
        copies = node("ShaderNodeMath", -820, -200, operation='SUBTRACT')
        copies.inputs[1].default_value = 1.0
        L.new(gi.outputs["Shells"], copies.inputs[0])
        dup = node("GeometryNodeDuplicateElements", -640, domain='INSTANCE')
        L.new(inst.outputs[0], dup.inputs["Geometry"]); L.new(copies.outputs[0], dup.inputs["Amount"])
        index = node("ShaderNodeMath", -640, -200, operation='ADD')
        index.inputs[1].default_value = 1.0
        L.new(dup.outputs["Duplicate Index"], index.inputs[0])
        # the copy's layer, on its instance: realized, on each of its points
        geo = store(dup.outputs["Geometry"], -460, SHELL_LAYER, index.outputs[0], domain='INSTANCE')
        real = node("GeometryNodeRealizeInstances", -280)
        L.new(geo, real.inputs[0])
        layer = node("GeometryNodeInputNamedAttribute", -280, -200, data_type='FLOAT')
        layer.inputs["Name"].default_value = SHELL_LAYER
        last = node("ShaderNodeMath", -280, -360, operation='SUBTRACT')
        last.inputs[1].default_value = 1.0
        L.new(gi.outputs["Shells"], last.inputs[0])
        span = node("ShaderNodeMath", -100, -360, operation='MAXIMUM')
        span.inputs[1].default_value = 1.0
        L.new(last.outputs[0], span.inputs[0])
        frac = node("ShaderNodeMath", 80, -200, operation='DIVIDE')
        L.new(layer.outputs["Attribute"], frac.inputs[0]); L.new(span.outputs[0], frac.inputs[1])
        # the whole depth along the normal, and this layer's share of it
        depth = node("ShaderNodeMath", -100, -520, operation='MULTIPLY')
        depth.inputs[1].default_value = p["depth"]
        L.new(gi.outputs["Length"], depth.inputs[0])
        normal = node("GeometryNodeInputNormal", -100, -660)
        whole = node("ShaderNodeVectorMath", 80, -560, operation='SCALE')
        L.new(normal.outputs[0], whole.inputs[0]); L.new(depth.outputs[0], whole.inputs["Scale"])
        offset = node("ShaderNodeVectorMath", 260, -400, operation='SCALE')
        L.new(whole.outputs[0], offset.inputs[0]); L.new(frac.outputs[0], offset.inputs["Scale"])
        geo = store(real.outputs[0], 80, SHELL_LAYER_N, frac.outputs[0])
        geo = store(geo, 260, SHELL_COUNT, gi.outputs["Shells"])
        geo = store(geo, 440, SHELL_VECTOR, whole.outputs[0], kind='FLOAT_VECTOR')
        geo = store(geo, 620, SHELL_OFFSET, offset.outputs[0], kind='FLOAT_VECTOR')
        if not p.get("moves"):
            move = node("GeometryNodeSetPosition", 700, -200)
            L.new(geo, move.inputs["Geometry"]); L.new(offset.outputs[0], move.inputs["Offset"])
            geo = move.outputs[0]
        else:
            # a hair inside the mesh: a layer its material doesn't move (no fur there: Crash's eyes)
            # lies just behind the mesh, not on it - at the very same depth Eevee drew the layer
            # over the mesh (black where it's see-through)
            tuck = node("ShaderNodeVectorMath", 620, -560, operation='SCALE')
            tuck.inputs["Scale"].default_value = -p.get("tuck", 0.0002)
            L.new(normal.outputs[0], tuck.inputs[0])
            move = node("GeometryNodeSetPosition", 700, -200)
            L.new(geo, move.inputs["Geometry"]); L.new(tuck.outputs[0], move.inputs["Offset"])
            geo = move.outputs[0]
        mat = node("GeometryNodeSetMaterial", 780)
        mat.inputs["Material"].default_value = p["shell"]
        L.new(geo, mat.inputs["Geometry"])
        L.new(mat.outputs[0], join.inputs[0])
    L.new(join.outputs[0], go.inputs[0])
    return t
