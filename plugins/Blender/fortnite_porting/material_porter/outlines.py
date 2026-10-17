"""Overlay materials (a character part's OverlayMaterialData), as Blender draws them.

The game draws a part's overlay material over its whole mesh as a second pass. The anime skins' ink outlines are one:
an inverted hull, whose World Position Offset pushes the mesh out along its normals (thicker far from the camera, by
its thickness map) and whose opacity mask keeps only the faces turned away, so a dark rim shows around the body.

Here the overlay material is built with "moves" (its World Position Offset is Blender's displacement) and a Geometry
Nodes modifier copies the part's faces (those with the part's materials) with it. Parts are joined after import, and a
join keeps the slots but not the modifiers: the modifier goes on after it.
"""
import bpy

KEY = "mp_outlines"         # on the context: [{"bases": [materials], "outline": material}]
MODIFIER = "MP Outline"
FP_OUTLINE, FP_MATERIAL = "Outline", "M_FP_Outline"     # FortnitePorting's own toon outline (a Solidify shell)


def prepare(context, mesh_object, overlay, meta):
    """A part's overlay material at import: built into a new slot of the part (no face uses it), kept for apply."""
    if not overlay or mesh_object is None:
        return
    bases = [m for m in mesh_object.data.materials if m is not None]
    # a slot as the mesh's own arrive: holding a material of the game's name, which the build replaces
    mesh_object.data.materials.append(bpy.data.materials.get(overlay.get("Name")) or bpy.data.materials.new(overlay.get("Name") or "Overlay"))
    slot = mesh_object.material_slots[len(mesh_object.data.materials) - 1]
    context.import_material(slot, dict(overlay, MPMoves=True, MPOutline=True), meta)
    if slot.material is not None and bases:
        remember(context, bases, slot.material)
        print("[FNPORTING] [Material Porter] %s: outline %s" % (mesh_object.name, slot.material.name))


def remember(context, bases, outline):
    context.__dict__.setdefault(KEY, []).append({"bases": list(bases), "outline": outline})


def apply(context, objects):
    """The outline modifier on each of these meshes whose materials have an overlay (an outfit's joined mesh, else
    each part)."""
    pairs = context.__dict__.get(KEY) or []
    for o in objects:
        try:
            if o is None or o.type != 'MESH' or o.modifiers.get(MODIFIER) is not None:
                continue
        except ReferenceError:      # a part joined into the body is gone
            continue
        mats = {m for m in o.data.materials if m is not None}
        mine = [p for p in pairs if any(b in mats for b in p["bases"])]
        if not mine:
            continue
        _drop_fp_outline(o)
        for p in mine:
            if p["outline"] not in o.data.materials[:]:
                o.data.materials.append(p["outline"])
        o.modifiers.new(MODIFIER, 'NODES').node_group = _group(o.name, mine)


def _drop_fp_outline(o):
    """FortnitePorting outlines its toon materials itself (a uniform shell over the whole mesh): the game's own
    replaces it."""
    fp = o.modifiers.get(FP_OUTLINE)
    if fp is not None and fp.type == 'SOLIDIFY':
        o.modifiers.remove(fp)
    for i in reversed(range(len(o.data.materials))):
        m = o.data.materials[i]
        if m is not None and m.name.split(".")[0] == FP_MATERIAL:
            o.data.materials.pop(index=i)


def _group(name, pairs):
    """Per overlay, the faces of its part's materials copied with the overlay material; the mesh itself is kept."""
    t = bpy.data.node_groups.new("MP Outline " + name, 'GeometryNodeTree')
    t.interface.new_socket("Geometry", in_out='INPUT', socket_type='NodeSocketGeometry')
    t.interface.new_socket("Geometry", in_out='OUTPUT', socket_type='NodeSocketGeometry')
    N, L = t.nodes, t.links
    gi = N.new("NodeGroupInput"); gi.location = (-900, 0)
    go = N.new("NodeGroupOutput"); go.location = (500, 0)
    join = N.new("GeometryNodeJoinGeometry"); join.location = (300, 0)
    L.new(gi.outputs["Geometry"], join.inputs[0])
    for k, p in enumerate(pairs):
        y = -300 * (k + 1)
        picked = None
        for j, base in enumerate(p["bases"]):
            sel = N.new("GeometryNodeMaterialSelection"); sel.location = (-700, y - 120 * j)
            sel.inputs["Material"].default_value = base
            if picked is None:
                picked = sel.outputs[0]
            else:
                either = N.new("FunctionNodeBooleanMath"); either.operation = 'OR'; either.location = (-520, y - 120 * j)
                L.new(picked, either.inputs[0]); L.new(sel.outputs[0], either.inputs[1])
                picked = either.outputs[0]
        sep = N.new("GeometryNodeSeparateGeometry"); sep.domain = 'FACE'; sep.location = (-300, y)
        L.new(gi.outputs["Geometry"], sep.inputs["Geometry"]); L.new(picked, sep.inputs["Selection"])
        mat = N.new("GeometryNodeSetMaterial"); mat.location = (0, y)
        mat.inputs["Material"].default_value = p["outline"]
        L.new(sep.outputs["Selection"], mat.inputs["Geometry"])
        L.new(mat.outputs[0], join.inputs[0])
    L.new(join.outputs[0], go.inputs[0])
    return t
