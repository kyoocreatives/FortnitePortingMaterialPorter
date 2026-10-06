"""Material Porter fork: shell fur on any faces, by hand (the Shell Fur panel).

The faces selected in Edit Mode are marked (a face attribute, FUR); a Geometry Nodes modifier
copies them Shells times, each copy pushed out along the normal by its share of Length (and down
by Gravity, more at the tip), and gives each copy its face's material's fur twin: the material
itself, cut into strands (MP Fur Strands: a Voronoi cell per strand on the mesh's UVs, thinner
toward the tip, some shorter) and darker at the root. Everything the strands need comes from the
modifier as attributes, so its inputs are the only controls: Shells, Length, Gravity, Density,
Thickness, Length Variation, Root Shadow.
"""
import bpy

from . import shells as game_shells

FUR = "mp_fur"                      # the face attribute marking fur faces
MODIFIER = "Custom Shell Fur"
STRANDS = "MP Fur Strands"          # the shader group cutting a layer into strands
TWIN = "mp_fur_twin_of"             # on a fur twin material: the material it is the twin of
LAYER = "mp_shell_layer_n"          # a layer's height, 0 at the root, 1 at the tip
ATTRS = {"Density": "mp_fur_density", "Thickness": "mp_fur_thickness", "Root Shadow": "mp_fur_shadow",
         "Length Variation": "mp_fur_vary", "Clumping": "mp_fur_clump", "Clump Size": "mp_fur_clump_size"}
STRANDS_REV = 2                     # MP Fur Strands' revision (2: clumping): an older one is rebuilt
ROOT = "mp_fur_root"                # where a layer's point sits on the surface (meshes without UVs)

INPUTS = (  # name, kind, default, min, max, description
    ("Shells", 'NodeSocketInt', 16, 1, 64, "How many layers above the surface"),
    ("Length", 'NodeSocketFloat', 0.02, 0.0, 1.0, "How long the fur is (metres)"),
    ("Gravity", 'NodeSocketFloat', 0.0, -1.0, 1.0, "How much the tips droop (of the length)"),
    ("Density", 'NodeSocketFloat', 300.0, 1.0, 5000.0, "Strands across one UV unit"),
    ("Thickness", 'NodeSocketFloat', 0.6, 0.0, 1.0, "How thick a strand is at the root (of its cell)"),
    ("Length Variation", 'NodeSocketFloat', 0.3, 0.0, 1.0, "How much shorter some strands are"),
    ("Root Shadow", 'NodeSocketFloat', 0.5, 0.0, 1.0, "How much darker the fur is at the root"),
    ("Flow Strength", 'NodeSocketFloat', 0.3, 0.0, 2.0, "How much a noise flow leans the fur (of its length), most at the tips"),
    ("Flow Scale", 'NodeSocketFloat', 5.0, 0.01, 200.0, "The flow noise's size: higher, smaller swirls"),
    ("Clumping", 'NodeSocketFloat', 0.3, 0.0, 1.0, "How much strands gather into clumps toward their tips"),
    ("Clump Size", 'NodeSocketFloat', 8.0, 1.0, 100.0, "How many strands across a clump"),
)


def _log(message):
    print("[FNPORTING] [Material Porter] " + message)


# ------------------------------------------------------------------ the strands (shader)
def strands_group():
    """MP Fur Strands: (Vector: the surface coordinate) -> Alpha (strand or not), Shade (root dark).
    A Voronoi cell per strand; strands in a clump (a larger cell) lean to its centre toward the tip."""
    t = bpy.data.node_groups.get(STRANDS)
    if t is not None and t.get("mp_rev", 1) >= STRANDS_REV:
        return t
    if t is None:
        t = bpy.data.node_groups.new(STRANDS, 'ShaderNodeTree')
    else:
        # (an older one, rebuilt in place: the materials using it keep it)
        t.nodes.clear()
        t.interface.clear()
    t["mp_rev"] = STRANDS_REV
    t.interface.new_socket("Vector", in_out='INPUT', socket_type='NodeSocketVector')
    t.interface.new_socket("Alpha", in_out='OUTPUT', socket_type='NodeSocketFloat')
    t.interface.new_socket("Shade", in_out='OUTPUT', socket_type='NodeSocketFloat')
    N, L = t.nodes, t.links
    gi = N.new("NodeGroupInput"); gi.location = (-1300, 0)
    go = N.new("NodeGroupOutput"); go.location = (900, 0)

    def attr(name, y):
        a = N.new("ShaderNodeAttribute"); a.attribute_type = 'GEOMETRY'; a.attribute_name = name
        a.location = (-1300, y)
        return a.outputs["Fac"]

    def math(op, a, b, x, y, clamp=False):
        m = N.new("ShaderNodeMath"); m.operation = op; m.use_clamp = clamp; m.location = (x, y)
        for k, v in enumerate((a, b)):
            if isinstance(v, (int, float)):
                m.inputs[k].default_value = v
            else:
                L.new(v, m.inputs[k])
        return m.outputs[0]

    def vmath(op, a, b, x, y, scale=None):
        m = N.new("ShaderNodeVectorMath"); m.operation = op; m.location = (x, y)
        L.new(a, m.inputs[0])
        if b is not None:
            L.new(b, m.inputs[1])
        if scale is not None:
            L.new(scale, m.inputs["Scale"])
        return m.outputs[0]
    n = attr(LAYER, -200)
    density, thickness = attr(ATTRS["Density"], -350), attr(ATTRS["Thickness"], -500)
    vary, shadow = attr(ATTRS["Length Variation"], -650), attr(ATTRS["Root Shadow"], -800)
    clump, clump_size = attr(ATTRS["Clumping"], -950), attr(ATTRS["Clump Size"], -1100)
    # the coordinate in strands (one a unit), and its clump's centre (a cell Clump Size strands across)
    p = vmath('SCALE', gi.outputs["Vector"], None, -1000, 0, scale=density)
    inv = math('DIVIDE', 1.0, clump_size, -1000, -150)
    pc = vmath('SCALE', p, None, -850, -100, scale=inv)
    cl = N.new("ShaderNodeTexVoronoi"); cl.voronoi_dimensions = '3D'; cl.feature = 'F1'; cl.location = (-700, -100)
    L.new(pc, cl.inputs["Vector"])
    centre = vmath('SCALE', cl.outputs["Position"], None, -550, -100, scale=clump_size)
    # toward the tip a strand from root s sits at s + (centre - s) * k: the root under p is
    # (p - centre * k) / (1 - k) - looked up there, its cross-section shrinks by 1 - k
    k = math('MINIMUM', math('MULTIPLY', clump, n, -700, -300), 0.9, -550, -300)
    pull = vmath('SCALE', centre, None, -400, -100, scale=k)
    q0 = vmath('SUBTRACT', p, pull, -250, 0)
    widen = math('DIVIDE', 1.0, math('SUBTRACT', 1.0, k, -400, -300), -250, -300)
    q = vmath('SCALE', q0, None, -100, 0, scale=widen)
    cell = N.new("ShaderNodeTexVoronoi"); cell.voronoi_dimensions = '3D'; cell.feature = 'F1'
    cell.location = (50, 0)
    L.new(q, cell.inputs["Vector"])
    # the strand's radius: Thickness (of half a cell) at the root, a fifth of it at the tip
    taper = math('MULTIPLY', n, 0.8, 50, -250)
    keep = math('SUBTRACT', 1.0, taper, 200, -250)
    radius = math('MULTIPLY', math('MULTIPLY', thickness, 0.5, 200, -400), keep, 350, -300)
    inside = math('LESS_THAN', cell.outputs["Distance"], radius, 500, -100)
    # some strands shorter: those whose random value is under the variation end early
    rand = N.new("ShaderNodeSeparateColor"); rand.location = (250, 150)
    L.new(cell.outputs["Color"], rand.inputs[0])
    reach = math('SUBTRACT', 1.0, math('MULTIPLY', rand.outputs[0], vary, 400, 200), 550, 200)
    short = math('LESS_THAN', n, reach, 650, 150)
    alpha = math('MULTIPLY', inside, short, 750, 0)
    L.new(alpha, go.inputs["Alpha"])
    dark = math('MULTIPLY', shadow, math('SUBTRACT', 1.0, n, 500, -600), 700, -600, clamp=True)
    L.new(dark, go.inputs["Shade"])
    return t


def _twin(mat, use_uv):
    """The fur twin of a material: a copy whose surface is cut into strands (MP Fur Strands) and
    darkened at the root; seen through by shadow rays like the game's shells."""
    name = (mat.name if mat else "Fur") + " (fur)"
    twin = next((m for m in bpy.data.materials if m.get(TWIN) == (mat.name if mat else "") and m.get("mp_fur_uv") == use_uv), None)
    if twin is not None:
        return twin
    if mat is not None:
        twin = mat.copy()
    else:
        twin = bpy.data.materials.new("Fur")
        twin.use_nodes = True
    twin.name = name
    twin[TWIN] = mat.name if mat else ""
    twin["mp_fur_uv"] = use_uv
    t = twin.node_tree
    out = next((n for n in t.nodes if n.bl_idname == "ShaderNodeOutputMaterial" and n.is_active_output), None) \
        or next((n for n in t.nodes if n.bl_idname == "ShaderNodeOutputMaterial"), None)
    if out is None or not out.inputs["Surface"].is_linked:
        return twin
    src = out.inputs["Surface"].links[0].from_socket
    x, y = out.location
    if use_uv:
        coord = t.nodes.new("ShaderNodeUVMap")
        coord.uv_map = use_uv
        coord_out = coord.outputs["UV"]
    else:
        coord = t.nodes.new("ShaderNodeAttribute"); coord.attribute_type = 'GEOMETRY'; coord.attribute_name = ROOT
        coord_out = coord.outputs["Vector"]
    coord.location = (x - 600, y - 300)
    g = t.nodes.new("ShaderNodeGroup"); g.node_tree = strands_group(); g.location = (x - 400, y - 300)
    g.label = "fur strands"
    t.links.new(coord_out, g.inputs["Vector"])
    dark = t.nodes.new("ShaderNodeBsdfDiffuse"); dark.inputs["Color"].default_value = (0, 0, 0, 1)
    dark.location = (x - 400, y - 150)
    shade = t.nodes.new("ShaderNodeMixShader"); shade.label = "root shadow"; shade.location = (x - 200, y)
    t.links.new(g.outputs["Shade"], shade.inputs[0]); t.links.new(src, shade.inputs[1]); t.links.new(dark.outputs[0], shade.inputs[2])
    clear = t.nodes.new("ShaderNodeBsdfTransparent"); clear.location = (x - 200, y - 150)
    cut = t.nodes.new("ShaderNodeMixShader"); cut.label = "strands"; cut.location = (x, y)
    t.links.new(g.outputs["Alpha"], cut.inputs[0]); t.links.new(clear.outputs[0], cut.inputs[1]); t.links.new(shade.outputs[0], cut.inputs[2])
    t.links.new(cut.outputs[0], out.inputs["Surface"])
    out.location = (x + 200, y)
    twin.surface_render_method = 'DITHERED'
    twin.use_backface_culling = False
    game_shells.no_shadow(twin)
    return twin


# ------------------------------------------------------------------ the layers (geometry nodes)
def _fur_group(obj, pairs, use_uv):
    """The object's modifier group: its fur faces copied Shells times, pushed out, each copy given
    its material's twin and the attributes the strands read; the mesh itself as it was."""
    name = "MP Custom Fur " + obj.name
    old = bpy.data.node_groups.get(name)
    if old is not None:
        bpy.data.node_groups.remove(old)
    t = bpy.data.node_groups.new(name, 'GeometryNodeTree')
    t.interface.new_socket("Geometry", in_out='INPUT', socket_type='NodeSocketGeometry')
    for nm, kind, default, lo, hi, desc in INPUTS:
        s = t.interface.new_socket(nm, in_out='INPUT', socket_type=kind)
        s.default_value, s.min_value, s.max_value, s.description = default, lo, hi, desc
    t.interface.new_socket("Geometry", in_out='OUTPUT', socket_type='NodeSocketGeometry')
    N, L = t.nodes, t.links
    x = [-1600]

    def node(kind, y=0, **props):
        n = N.new(kind)
        n.location = (x[0], y)
        x[0] += 190
        for key, v in props.items():
            setattr(n, key, v)
        return n

    def store(geometry, name, value, kind='FLOAT', domain='POINT'):
        n = node("GeometryNodeStoreNamedAttribute", data_type=kind, domain=domain)
        n.inputs["Name"].default_value = name
        L.new(geometry, n.inputs["Geometry"]); L.new(value, n.inputs["Value"])
        return n.outputs[0]
    gi = node("NodeGroupInput")
    fur = node("GeometryNodeInputNamedAttribute", -250, data_type='BOOLEAN')
    fur.inputs["Name"].default_value = FUR
    sep = node("GeometryNodeSeparateGeometry", domain='FACE')
    L.new(gi.outputs["Geometry"], sep.inputs["Geometry"]); L.new(fur.outputs["Attribute"], sep.inputs["Selection"])
    geo = sep.outputs["Selection"]
    if not use_uv:
        pos = node("GeometryNodeInputPosition", -250)
        geo = store(geo, ROOT, pos.outputs[0], 'FLOAT_VECTOR')
    # the surface's normal before the copies move
    nrm = node("GeometryNodeInputNormal", -250)
    geo = store(geo, "mp_fur_normal", nrm.outputs[0], 'FLOAT_VECTOR')
    inst = node("GeometryNodeGeometryToInstance")
    L.new(geo, inst.inputs[0])
    dup = node("GeometryNodeDuplicateElements", domain='INSTANCE')
    L.new(inst.outputs[0], dup.inputs["Geometry"]); L.new(gi.outputs["Shells"], dup.inputs["Amount"])
    one = node("ShaderNodeMath", -250, operation='ADD'); one.inputs[1].default_value = 1.0
    L.new(dup.outputs["Duplicate Index"], one.inputs[0])
    geo = store(dup.outputs["Geometry"], "mp_fur_layer", one.outputs[0], 'FLOAT', 'INSTANCE')
    real = node("GeometryNodeRealizeInstances")
    L.new(geo, real.inputs[0])
    layer = node("GeometryNodeInputNamedAttribute", -250, data_type='FLOAT')
    layer.inputs["Name"].default_value = "mp_fur_layer"
    n = node("ShaderNodeMath", -250, operation='DIVIDE')
    L.new(layer.outputs["Attribute"], n.inputs[0]); L.new(gi.outputs["Shells"], n.inputs[1])
    # the offset: normal * Length * n, down by Gravity * Length * n^2
    normal = node("GeometryNodeInputNamedAttribute", -400, data_type='FLOAT_VECTOR')
    normal.inputs["Name"].default_value = "mp_fur_normal"
    reach = node("ShaderNodeMath", -400, operation='MULTIPLY')
    L.new(n.outputs[0], reach.inputs[0]); L.new(gi.outputs["Length"], reach.inputs[1])
    up = node("ShaderNodeVectorMath", -400, operation='SCALE')
    L.new(normal.outputs["Attribute"], up.inputs[0]); L.new(reach.outputs[0], up.inputs["Scale"])
    sq = node("ShaderNodeMath", -550, operation='MULTIPLY')
    L.new(reach.outputs[0], sq.inputs[0]); L.new(n.outputs[0], sq.inputs[1])
    droop = node("ShaderNodeMath", -550, operation='MULTIPLY')
    L.new(sq.outputs[0], droop.inputs[0]); L.new(gi.outputs["Gravity"], droop.inputs[1])
    down = node("ShaderNodeCombineXYZ", -550)
    neg = node("ShaderNodeMath", -700, operation='MULTIPLY'); neg.inputs[1].default_value = -1.0
    L.new(droop.outputs[0], neg.inputs[0]); L.new(neg.outputs[0], down.inputs["Z"])
    offset = node("ShaderNodeVectorMath", -400, operation='ADD')
    L.new(up.outputs[0], offset.inputs[0]); L.new(down.outputs[0], offset.inputs[1])
    # the flow: a noise field's direction along the surface, leaning the fur most at the tips
    # (sampled at the rest position where the mesh has one: the pattern stays put as it poses)
    pos = node("GeometryNodeInputPosition", -850)
    rest = node("GeometryNodeInputNamedAttribute", -1000, data_type='FLOAT_VECTOR')
    rest.inputs["Name"].default_value = "rest_position"
    where = node("GeometryNodeSwitch", -850, input_type='VECTOR')
    L.new(rest.outputs["Exists"], where.inputs["Switch"])
    L.new(pos.outputs[0], where.inputs["False"]); L.new(rest.outputs["Attribute"], where.inputs["True"])
    noise = node("ShaderNodeTexNoise", -850, noise_dimensions='3D')
    L.new(where.outputs[0], noise.inputs["Vector"]); L.new(gi.outputs["Flow Scale"], noise.inputs["Scale"])
    noise.inputs["Detail"].default_value = 1.0
    centred = node("ShaderNodeVectorMath", -850, operation='SUBTRACT')
    L.new(noise.outputs["Color"], centred.inputs[0]); centred.inputs[1].default_value = (0.5, 0.5, 0.5)
    along = node("ShaderNodeVectorMath", -1000, operation='DOT_PRODUCT')
    L.new(centred.outputs[0], along.inputs[0]); L.new(normal.outputs["Attribute"], along.inputs[1])
    off_normal = node("ShaderNodeVectorMath", -1000, operation='SCALE')
    L.new(normal.outputs["Attribute"], off_normal.inputs[0]); L.new(along.outputs["Value"], off_normal.inputs["Scale"])
    flat = node("ShaderNodeVectorMath", -850, operation='SUBTRACT')
    L.new(centred.outputs[0], flat.inputs[0]); L.new(off_normal.outputs[0], flat.inputs[1])
    # (Length * n * n: the lean grows toward the tip; the noise's +-0.5 doubled)
    bend = node("ShaderNodeMath", -1000, operation='MULTIPLY')
    L.new(sq.outputs[0], bend.inputs[0]); L.new(gi.outputs["Flow Strength"], bend.inputs[1])
    twice = node("ShaderNodeMath", -1150, operation='MULTIPLY'); twice.inputs[1].default_value = 2.0
    L.new(bend.outputs[0], twice.inputs[0])
    lean = node("ShaderNodeVectorMath", -850, operation='SCALE')
    L.new(flat.outputs[0], lean.inputs[0]); L.new(twice.outputs[0], lean.inputs["Scale"])
    total = node("ShaderNodeVectorMath", -400, operation='ADD')
    L.new(offset.outputs[0], total.inputs[0]); L.new(lean.outputs[0], total.inputs[1])
    offset = total
    setpos = node("GeometryNodeSetPosition")
    L.new(real.outputs[0], setpos.inputs["Geometry"]); L.new(offset.outputs[0], setpos.inputs["Offset"])
    geo = store(setpos.outputs[0], LAYER, n.outputs[0])
    for nm, attr in ATTRS.items():
        geo = store(geo, attr, gi.outputs[nm])
    for base, twin in pairs:
        sel = node("GeometryNodeMaterialSelection", -250)
        sel.inputs["Material"].default_value = base
        setm = node("GeometryNodeSetMaterial")
        L.new(geo, setm.inputs["Geometry"]); L.new(sel.outputs[0], setm.inputs["Selection"])
        setm.inputs["Material"].default_value = twin
        geo = setm.outputs[0]
    join = node("GeometryNodeJoinGeometry")
    L.new(gi.outputs["Geometry"], join.inputs[0]); L.new(geo, join.inputs[0])
    go = node("NodeGroupOutput")
    L.new(join.outputs[0], go.inputs[0])
    return t


def add_fur(obj):
    """Fur on the object's selected faces (with any it already has). Returns a message."""
    me = obj.data
    selected = [p.index for p in me.polygons if p.select]
    if not selected:
        raise ValueError("No faces selected: select faces in Edit Mode first")
    attr = me.attributes.get(FUR)
    if attr is None or attr.domain != 'FACE' or attr.data_type != 'BOOLEAN':
        if attr is not None:
            me.attributes.remove(attr)
        attr = me.attributes.new(FUR, 'BOOLEAN', 'FACE')
    marked = [False] * len(me.polygons)
    attr.data.foreach_get("value", marked)
    for i in selected:
        marked[i] = True
    attr.data.foreach_set("value", marked)
    use_uv = me.uv_layers.active.name if me.uv_layers.active else ""
    # each material on a fur face, and its twin
    slots = {me.polygons[i].material_index for i in range(len(me.polygons)) if marked[i]}
    pairs = []
    for i in sorted(slots):
        base = obj.material_slots[i].material if i < len(obj.material_slots) else None
        if base is None:
            continue
        twin = _twin(base, use_uv)
        if twin.name not in [m.name for m in me.materials if m]:
            me.materials.append(twin)     # (in the slots, to edit; no face of the mesh's own uses it)
        pairs.append((base, twin))
    mod = obj.modifiers.get(MODIFIER)
    keep = {}
    if mod is not None and mod.node_group is not None:
        keep = {it.name: _get_input(mod, it.identifier) for it in mod.node_group.interface.items_tree
                if it.item_type == 'SOCKET' and it.in_out == 'INPUT' and it.name != "Geometry"}
    else:
        mod = obj.modifiers.new(MODIFIER, 'NODES')
    mod.node_group = _fur_group(obj, pairs, use_uv)
    from ..processing.utils import set_geo_nodes_param
    for name, value in keep.items():
        if value is not None:
            try:
                set_geo_nodes_param(mod, name, value)
            except Exception:
                pass
    # the strands are see-through layers: Cycles counts each as a transparent bounce
    scene = bpy.context.scene
    if hasattr(scene, "cycles"):
        scene.cycles.transparent_max_bounces = max(scene.cycles.transparent_max_bounces, 4 * 64)
    msg = "%s: shell fur on %d faces (%d in all)" % (obj.name, len(selected), sum(marked))
    _log(msg)
    return msg


def _get_input(mod, identifier):
    try:
        return getattr(mod.properties.inputs, identifier).value
    except AttributeError:
        try:
            return mod[identifier]
        except (KeyError, TypeError):
            return None


def remove_fur(obj):
    """The object's custom fur gone: its modifier, group, face marks and the twin slots."""
    mod = obj.modifiers.get(MODIFIER)
    if mod is not None:
        group = mod.node_group
        obj.modifiers.remove(mod)
        if group is not None and group.users == 0:
            bpy.data.node_groups.remove(group)
    me = obj.data
    attr = me.attributes.get(FUR)
    if attr is not None:
        me.attributes.remove(attr)
    for i in reversed(range(len(me.materials))):
        m = me.materials[i]
        if m is not None and TWIN in m:
            me.materials.pop(index=i)
    return "%s: shell fur removed" % obj.name


def draw_inputs(layout, obj):
    """The modifier's inputs, as sliders."""
    mod = obj.modifiers.get(MODIFIER)
    if mod is None or mod.node_group is None:
        return False
    col = layout.column(align=True)
    holder = getattr(getattr(mod, "properties", None), "inputs", None)
    for it in mod.node_group.interface.items_tree:
        if it.item_type != 'SOCKET' or it.in_out != 'INPUT' or it.name == "Geometry":
            continue
        if holder is not None and hasattr(holder, it.identifier):
            col.prop(getattr(holder, it.identifier), "value", text=it.name)
        else:
            col.prop(mod, '["%s"]' % it.identifier, text=it.name)
    return True
