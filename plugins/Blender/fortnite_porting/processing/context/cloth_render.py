"""The render mesh follows its simulated cloth as the engine maps it: each cloth-section vertex sits on a simulation
triangle at its barycentric point, pushed along the interpolated normal, blended with its skinned position by how much
of its triangle moves. A hidden point cache holds the simulation per
frame (mesh space); a geometry nodes modifier after the Armature reads the current frame's and leaves every other vertex
(and every frame without data) skinned."""
import bpy
import numpy as np

GROUP = "FPMP Cloth"
MODIFIER = "Cloth"
UNSIMULATED = 0xFFFF        # the fourth index: how static the triangle is, 0xFFFF = plain skinning
_INTS = ("cloth_a", "cloth_b", "cloth_c")
FLIP = np.diag([1.0, -1.0, 1.0])        # the game's axes vs Blender's


def _ints(mesh, name):
    out = np.zeros(len(mesh.vertices), np.int32)
    mesh.attributes[name].data.foreach_get("value", out)
    return out.tolist()


def attach(obj, sections, offsets, unit, part=1):
    """Maps the mesh's cloth-section vertices onto the cache's points (each asset's particles from its offset; normal
    offsets in cm, times unit) and marks them with their part (for relink after a join); returns how many vertices
    follow the cloth."""
    mesh, n = obj.data, len(obj.data.vertices)
    tri = np.full((n, 3), -1, np.int32)
    bary = np.zeros((n, 3), np.float32)
    dist = np.zeros(n, np.float32)
    blend = np.zeros(n, np.float32)
    for s in sections:
        m = np.asarray(s["mapping"], np.float64)
        if not len(m) or len(m) != s["count"]:
            continue        # several influences per vertex: not replayed
        rows = np.arange(s["base"], s["base"] + s["count"])
        live = m[:, 15] != UNSIMULATED
        tri[rows[live]] = m[live, 12:15].astype(np.int32) + offsets[s["asset"]]
        bary[rows[live], :2] = m[live, 0:2]
        bary[rows[live], 2] = 1.0 - m[live, 0] - m[live, 1]       # the engine ignores the stored third one
        dist[rows[live]] = m[live, 3] * unit
        blend[rows[live]] = 1.0 - m[live, 15] / UNSIMULATED
    for i, name in enumerate(_INTS):
        a = mesh.attributes.get(name) or mesh.attributes.new(name, 'INT', 'POINT')
        a.data.foreach_set("value", tri[:, i])
    a = mesh.attributes.get("cloth_bary") or mesh.attributes.new("cloth_bary", 'FLOAT_VECTOR', 'POINT')
    a.data.foreach_set("vector", bary.ravel())
    a = mesh.attributes.get("cloth_dist") or mesh.attributes.new("cloth_dist", 'FLOAT', 'POINT')
    a.data.foreach_set("value", dist)
    a = mesh.attributes.get("cloth_blend") or mesh.attributes.new("cloth_blend", 'FLOAT', 'POINT')
    a.data.foreach_set("value", blend)
    a = mesh.attributes.get("cloth_part") or mesh.attributes.new("cloth_part", 'INT', 'POINT')
    a.data.foreach_set("value", np.where(tri[:, 0] >= 0, part, 0).astype(np.int32))
    return int((tri[:, 0] >= 0).sum())


def parts(obj):
    """The cloth parts whose vertices the mesh holds (joined meshes hold several)."""
    a = obj.data.attributes.get("cloth_part")
    return sorted(set(_ints(obj.data, "cloth_part")) - {0}) if a is not None else []


def relink(obj, offsets):
    """Moves each part's simulation indices to its place in the joined mesh's cache (offsets by part)."""
    part = np.array(_ints(obj.data, "cloth_part"))
    shift = np.array([offsets.get(int(p), 0) for p in part], np.int32)
    for name in _INTS:
        v = np.array(_ints(obj.data, name), np.int32)
        obj.data.attributes[name].data.foreach_set("value", np.where(part > 0, v + shift, v))


def cache(obj, points):
    """The mesh's hidden cache object (made on first use) holding `points` particles per frame, wired to its modifier."""
    name = obj.name + " cloth"
    c = bpy.data.objects.get(name)
    if c is not None and c.data.get("points") == points and MODIFIER in obj.modifiers:
        return c
    if c is None:
        c = bpy.data.objects.new(name, bpy.data.meshes.new(name))
        for col in obj.users_collection:
            col.objects.link(c)
        c.hide_viewport = c.hide_render = c.hide_select = True
    c.data.clear_geometry()
    c.data["points"], c.data["start"] = points, 0
    mod = obj.modifiers.get(MODIFIER)
    if mod is None:
        mod = obj.modifiers.new(MODIFIER, 'NODES')
        armature = [i for i, m in enumerate(obj.modifiers) if m.type == 'ARMATURE']
        if armature:        # right after skinning, before outlines and the like
            obj.modifiers.move(len(obj.modifiers) - 1, armature[-1] + 1)
    mod.node_group = _group()
    _inputs(obj, Cache=c, Points=points, Count=0)
    return c


def _to_mesh(obj, pos, nrm, unit):
    """World positions (cm) and normals in the game's axes -> the mesh's own space."""
    m = np.array(obj.matrix_world.inverted())
    p = (np.asarray(pos) @ FLIP) * unit @ m[:3, :3].T + m[:3, 3]
    n = np.asarray(nrm) @ FLIP @ m[:3, :3].T
    return p, n / np.maximum(np.linalg.norm(n, axis=-1), 1e-12)[..., None]


def store(frames, start, unit):
    """A bake's cloth, frames = per frame {mesh name: (positions, normals)} from the runner."""
    for name in frames[0] if frames else ():
        obj = bpy.data.objects.get(name)
        if obj is None:
            continue
        pos, nrm = zip(*[_to_mesh(obj, *f[name], unit) for f in frames])
        write(cache(obj, len(pos[0])), start, np.stack(pos), np.stack(nrm))


def store_frame(result, frame, unit):
    """One live frame of the runner's cloth."""
    for name, (pos, nrm) in result.items():
        obj = bpy.data.objects.get(name)
        if obj is not None:
            write_frame(cache(obj, len(pos)), frame, *_to_mesh(obj, pos, nrm, unit))


def _inputs(obj, **values):
    mod = obj.modifiers[MODIFIER]
    ids = {i.name: i.identifier for i in mod.node_group.interface.items_tree if i.in_out == 'INPUT'}
    for k, v in values.items():
        getattr(mod.properties.inputs, ids[k]).value = v


def _owner(c):
    return next(o for o in bpy.data.objects if o.type == 'MESH' and MODIFIER in o.modifiers
                and o.modifiers[MODIFIER].node_group is not None and o.name + " cloth" == c.name)


def _store(c, start, pos, nrm, valid):
    mesh = c.data
    f, n = pos.shape[:2]
    mesh.clear_geometry()
    mesh.vertices.add(f * n)
    mesh.vertices.foreach_set("co", np.ascontiguousarray(pos, np.float32).ravel())
    a = mesh.attributes.new("cloth_normal", 'FLOAT_VECTOR', 'POINT')
    a.data.foreach_set("vector", np.ascontiguousarray(nrm, np.float32).ravel())
    a = mesh.attributes.new("cloth_valid", 'FLOAT', 'POINT')
    a.data.foreach_set("value", np.repeat(valid.astype(np.float32), n))
    mesh["start"] = start
    mesh.update()
    owner = _owner(c)
    _inputs(owner, Start=start, Count=f)
    owner.update_tag()


def _read(c):
    mesh, n = c.data, c.data["points"]
    f = len(mesh.vertices) // n if n else 0
    pos = np.zeros(f * n * 3, np.float32)
    nrm = np.zeros(f * n * 3, np.float32)
    valid = np.zeros(f * n, np.float32)
    if f:
        mesh.vertices.foreach_get("co", pos)
        mesh.attributes["cloth_normal"].data.foreach_get("vector", nrm)
        mesh.attributes["cloth_valid"].data.foreach_get("value", valid)
    return mesh["start"], pos.reshape(f, n, 3), nrm.reshape(f, n, 3), valid.reshape(f, n)[:, 0] if f else valid


def write(c, start, pos, nrm):
    """The whole cache from frame `start` on: pos and nrm shaped (frames, points, 3)."""
    _store(c, start, np.asarray(pos), np.asarray(nrm), np.ones(len(pos), bool))


def write_frame(c, frame, pos, nrm):
    """One frame's particles, growing the cache to reach it (frames in between show the skinned mesh)."""
    start, p, q, valid = _read(c)
    n = c.data["points"]
    if not len(p):
        start = frame
    lo, hi = min(start, frame), max(start + len(p), frame + 1)
    grown = [np.zeros((hi - lo, n, 3), np.float32) for _ in range(2)] + [np.zeros(hi - lo, bool)]
    grown[0][start - lo:start - lo + len(p)], grown[1][start - lo:start - lo + len(p)] = p, q
    grown[2][start - lo:start - lo + len(p)] = valid > 0.5
    grown[0][frame - lo], grown[1][frame - lo], grown[2][frame - lo] = pos, nrm, True
    _store(c, lo, *grown)


def set_on(obj, on):
    mod = obj.modifiers.get(MODIFIER)
    if mod is not None:
        mod.show_viewport = mod.show_render = on


def _enabled(node):
    return [s for s in node.inputs if s.enabled]


def _group():
    g = bpy.data.node_groups.get(GROUP)
    if g is not None:
        return g
    g = bpy.data.node_groups.new(GROUP, 'GeometryNodeTree')
    for name, kind in (("Geometry", 'NodeSocketGeometry'), ("Cache", 'NodeSocketObject'), ("Start", 'NodeSocketInt'),
                       ("Count", 'NodeSocketInt'), ("Points", 'NodeSocketInt')):
        g.interface.new_socket(name, in_out='INPUT', socket_type=kind)
    g.interface.new_socket("Geometry", in_out='OUTPUT', socket_type='NodeSocketGeometry')
    n, link = g.nodes, g.links.new
    gi, go = n.new('NodeGroupInput'), n.new('NodeGroupOutput')
    info = n.new('GeometryNodeObjectInfo')
    info.transform_space = 'ORIGINAL'
    link(gi.outputs["Cache"], info.inputs["Object"])

    def imath(op, a, b):
        m = n.new('FunctionNodeIntegerMath')
        m.operation = op
        link(a, m.inputs[0])
        link(b, m.inputs[1])
        return m.outputs[0]

    def attr(name, kind):
        a = n.new('GeometryNodeInputNamedAttribute')
        a.data_type, a.inputs["Name"].default_value = kind, name
        return a.outputs["Attribute"]

    def sample(value, index, kind='FLOAT_VECTOR'):
        s = n.new('GeometryNodeSampleIndex')
        s.data_type, s.domain = kind, 'POINT'
        link(info.outputs["Geometry"], s.inputs["Geometry"])
        link(value, s.inputs["Value"])
        link(index, s.inputs["Index"])
        return s.outputs[0]

    def vmath(op, *ins):
        m = n.new('ShaderNodeVectorMath')
        m.operation = op
        for i, v in enumerate(ins):
            link(v, m.inputs[i])
        return m

    def compare(op, a, b=None, value=0):
        c = n.new('FunctionNodeCompare')
        c.data_type, c.operation = 'INT', op
        link(a, _enabled(c)[0])
        if b is None:
            _enabled(c)[1].default_value = value
        else:
            link(b, _enabled(c)[1])
        return c.outputs[0]

    def both(a, b):
        m = n.new('FunctionNodeBooleanMath')
        link(a, m.inputs[0])
        link(b, m.inputs[1])
        return m.outputs[0]

    frame = n.new('FunctionNodeFloatToInt')
    link(n.new('GeometryNodeInputSceneTime').outputs["Frame"], frame.inputs[0])
    rel = imath('SUBTRACT', frame.outputs[0], gi.outputs["Start"])
    base = imath('MULTIPLY', rel, gi.outputs["Points"])
    position, normal = n.new('GeometryNodeInputPosition').outputs[0], attr("cloth_normal", 'FLOAT_VECTOR')
    split = n.new('ShaderNodeSeparateXYZ')
    link(attr("cloth_bary", 'FLOAT_VECTOR'), split.inputs[0])
    dist, total, first = attr("cloth_dist", 'FLOAT'), None, None
    for name, axis in zip(_INTS, "XYZ"):
        index = imath('ADD', attr(name, 'INT'), base)
        first = first or index
        moved = vmath('MULTIPLY_ADD', sample(normal, index), dist, sample(position, index))     # p + n * d
        part = vmath('SCALE', moved.outputs[0])
        link(split.outputs[axis], part.inputs["Scale"])
        total = part if total is None else vmath('ADD', total.outputs[0], part.outputs[0])
    valid = n.new('FunctionNodeCompare')
    valid.data_type, valid.operation = 'FLOAT', 'GREATER_THAN'
    link(sample(attr("cloth_valid", 'FLOAT'), first, 'FLOAT'), _enabled(valid)[0])
    _enabled(valid)[1].default_value = 0.5
    selected = both(both(compare('GREATER_EQUAL', attr("cloth_a", 'INT'), value=0), compare('GREATER_EQUAL', rel, value=0)),
                    both(compare('LESS_THAN', rel, gi.outputs["Count"]), valid.outputs[0]))
    move = n.new('GeometryNodeSetPosition')
    link(gi.outputs["Geometry"], move.inputs["Geometry"])
    link(selected, move.inputs["Selection"])
    skinned = n.new('GeometryNodeInputPosition').outputs[0]
    toward = vmath('SUBTRACT', total.outputs[0], skinned)
    lerp = vmath('MULTIPLY_ADD', toward.outputs[0], attr("cloth_blend", 'FLOAT'), skinned)
    link(lerp.outputs[0], move.inputs["Position"])
    link(move.outputs[0], go.inputs[0])
    return g
