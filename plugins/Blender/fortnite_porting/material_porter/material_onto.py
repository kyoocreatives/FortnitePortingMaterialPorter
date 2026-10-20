"""A material sent on its own (the app's Material export) goes onto the selected meshes: built into the active one's
active slot, the same material then in each other selected mesh's active slot. What the material reads off a mesh is
made on them when missing: a landscape material's layers, three to a colour attribute (Attribute1's red, green and
blue...: landscape_layers; the first painted all over so it shows, the others to paint: import it again after painting
to bring those layers in), other geometry attributes it samples, and its UV maps (copies of the first)."""
import bpy

from ..logger import Log

# attributes every mesh has, or that Blender works out
BUILT_IN = {"position", "normal", "uv", "uvmap", "color", "col", "uv0", ""}


def onto_selected(ctx, materials):
    """Import the materials onto the selected meshes; False (nothing done) when no mesh is selected."""
    meshes = [o for o in bpy.context.selected_objects if o.type == 'MESH']
    if not meshes:
        return False
    active = bpy.context.active_object if bpy.context.active_object in meshes else meshes[0]
    for material in materials:
        if not active.material_slots:
            active.data.materials.append(None)
        slot = active.material_slots[active.active_material_index]
        # a new material in the slot: the import builds into it, the mesh's former one stays as it was
        slot.material = bpy.data.materials.new(material.get("Name") or "Material")
        ctx.import_material(slot, material, {})
        built = slot.material
        made = set()
        if _layers(built):
            # a landscape material: its layers made on the meshes (marked as landscapes), then built again for those
            # layers as a landscape import does (one material per set of painted layers, within Eevee's samplers)
            for o in meshes:
                made |= make_attributes(o, built)
                o["mp_landscape"] = 1.0
            first = built
            slot.material = bpy.data.materials.new(material.get("Name") or "Material")
            ctx.import_material(slot, material, {})
            built = slot.material
            if first is not built and not first.users:
                bpy.data.materials.remove(first)
        for o in meshes:
            if o is not active:
                if not o.material_slots:
                    o.data.materials.append(built)
                else:
                    o.material_slots[o.active_material_index].material = built
        for o in meshes:
            made |= make_attributes(o, built)
        Log.info("%s onto %s%s" % (built.name, ", ".join(o.name for o in meshes),
                                   ": made " + ", ".join(sorted(made)) if made else ""))
    return True


def _layers(material):
    """A landscape material's layers: the names of its landscape layer nodes ("layer <name>")."""
    return {n.label[len("layer "):] for tree in _trees(material) for n in tree.nodes
            if n.bl_idname == "ShaderNodeVertexColor" and n.label.startswith("layer ")}


def _trees(material):
    """A material's node tree and every group tree under it."""
    seen, todo = {}, [material.node_tree] if material and material.node_tree else []
    while todo:
        tree = todo.pop()
        if tree is None or tree.name in seen:
            continue
        seen[tree.name] = tree
        todo += [n.node_tree for n in tree.nodes if n.bl_idname == "ShaderNodeGroup"]
    return list(seen.values())


def _reads(material):
    """What a material's trees read off a mesh: (colour layer names, geometry attribute names, UV map names)."""
    colors, attributes, uvs = set(), set(), set()
    seen, todo = set(), [material.node_tree] if material and material.node_tree else []
    while todo:
        tree = todo.pop()
        if tree is None or tree.name in seen:
            continue
        seen.add(tree.name)
        for n in tree.nodes:
            kind = n.bl_idname
            if kind == "ShaderNodeGroup":
                todo.append(n.node_tree)
            elif kind == "ShaderNodeVertexColor" and n.layer_name and not n.label.startswith("layer "):
                colors.add(n.layer_name)
            elif kind == "ShaderNodeAttribute" and n.attribute_type == 'GEOMETRY' and n.attribute_name:
                attributes.add(n.attribute_name)
            elif kind in ("ShaderNodeUVMap", "ShaderNodeNormalMap", "ShaderNodeTangent") and getattr(n, "uv_map", ""):
                uvs.add(n.uv_map)
    return colors, attributes - colors, uvs


def make_attributes(obj, material):
    """The attributes `material` reads that `obj`'s mesh lacks, made; returns their names."""
    mesh = obj.data
    colors, attributes, uvs = _reads(material)
    made = set()
    layers = sorted(_layers(material))
    if layers:
        from . import landscape_layers
        have = set(landscape_layers.table(obj))
        landscape_layers.make(obj, layers)
        made |= {"%s (%s)" % (l, obj[landscape_layers.KEY][l]) for l in layers if l not in have}
    for name in sorted(colors) + sorted(attributes):
        # the plugin's own attributes are made by its imports (mp_tangent: baked below), not left empty
        if name.lower() in BUILT_IN or name in mesh.attributes or name.startswith("mp_"):
            continue
        mesh.color_attributes.new(name, 'FLOAT_COLOR', 'POINT')
        made.add(name)
    for name in sorted(uvs):
        if name and name not in mesh.uv_layers:
            mesh.uv_layers.new(name=name)
            made.add(name)
    if "mp_tangent" in attributes and "mp_tangent" not in mesh.attributes:
        from .effects import bake_tangents
        bake_tangents(mesh)
        if "mp_tangent" in mesh.attributes:
            made.add("mp_tangent")
    return made
