"""The render mesh follows its simulated cloth as the engine maps it (cloth_solver.mapped): each cloth-section vertex
gets a slot whose position the bake or live preview computes per frame, blended with its skinned position by how much
of its triangle moves. Hidden point caches hold the slots (mesh space): the bake's per frame, the live preview's for its
one frame; a geometry nodes modifier after the Armature reads the shown one's current frame and leaves every other
vertex (and every frame without data) skinned."""
import uuid

import bpy
import numpy as np

GROUP = "FPMP Cloth"
MODIFIER = "Cloth"
UNSIMULATED = 0xFFFF        # the fourth index: how static the triangle is, 0xFFFF = plain skinning
FLIP = np.diag([1.0, -1.0, 1.0])        # the game's axes vs Blender's
ID = "fpmp_cloth"           # on a cloth mesh: the id the armature's cloth entries name it by
CACHE = "fpmp_cloth_cache"  # on a cache object: its mesh's id; on cache data: "<id>/bake" or "<id>/live"


def _ints(mesh, name):
    out = np.zeros(len(mesh.vertices), np.int32)
    mesh.attributes[name].data.foreach_get("value", out)
    return out.tolist()


def _set(mesh, name, kind, values):
    a = mesh.attributes.get(name) or mesh.attributes.new(name, kind, 'POINT')
    a.data.foreach_set("value", values)


def attach(obj, sections, offsets, part=1):
    """Gives the mesh's cloth-section vertices their slots (marked with their part, for relink after a join); returns
    the slots' mapping onto the simulation particles (each asset's from its offset), as cloth_solver.mapped reads it."""
    mesh, n = obj.data, len(obj.data.vertices)
    slot, blend = np.full(n, -1, np.int32), np.zeros(n, np.float32)
    tri, bary, dist, weight, count = [], [], [], [], 0
    for s in sections:
        m = np.asarray(s["mapping"], np.float64)
        if not s["count"] or len(m) % s["count"]:
            continue
        k = len(m) // s["count"]
        m = m.reshape(s["count"], k, 17)
        valid = m[:, :, 15] < UNSIMULATED
        w = np.where(valid, m[:, :, 16] if k > 1 else 1.0, 0.0)
        share = np.where(valid, 1.0 - m[:, :, 15] / UNSIMULATED, 0.0).sum(1) / k
        keep = np.where((w.sum(1) > 1e-4) & (share > 0.0))[0]
        rows = s["base"] + keep
        slot[rows] = count + np.arange(len(keep))
        count += len(keep)
        blend[rows] = share[keep]
        tri.append(m[keep, :, 12:15].astype(np.int64) + offsets[s["asset"]])
        bary.append(m[keep, :, 0:2])
        dist.append(m[keep, :, 3])
        weight.append(w[keep])
    _set(mesh, "cloth_slot", 'INT', slot)
    _set(mesh, "cloth_blend", 'FLOAT', blend)
    _set(mesh, "cloth_part", 'INT', np.where(slot >= 0, part, 0).astype(np.int32))
    if not tri:
        return {"tri": [], "bary": [], "dist": [], "weight": []}
    k = max(t.shape[1] for t in tri)

    def pad(arrays, fill):
        return np.concatenate([np.pad(a, [(0, 0), (0, k - a.shape[1])] + [(0, 0)] * (a.ndim - 2), constant_values=fill) for a in arrays])
    return {"tri": pad(tri, 0).tolist(), "bary": pad(bary, 0.0).tolist(), "dist": pad(dist, 0.0).tolist(), "weight": pad(weight, 0.0).tolist()}


def join(parts_slots):
    """Several parts' slots as one joined mesh's, from (part, slots, particles) in cache order: the slots and each part's
    first slot. Particle indices move past the earlier parts' particles; fewer influences are padded with weight 0."""
    k = max([len(s["tri"][0]) for _, s, _ in parts_slots if s["tri"]] or [1])
    out, offsets, particles = {"tri": [], "bary": [], "dist": [], "weight": []}, {}, 0
    for part, s, count in parts_slots:
        offsets[part] = len(out["tri"])
        for t, b, d, w in zip(s["tri"], s["bary"], s["dist"], s["weight"]):
            pad = k - len(t)
            out["tri"].append([[i + particles for i in tri] for tri in t] + [[0, 0, 0]] * pad)
            out["bary"].append(list(b) + [[0.0, 0.0]] * pad)
            out["dist"].append(list(d) + [0.0] * pad)
            out["weight"].append(list(w) + [0.0] * pad)
        particles += count
    return out, offsets


def parts(obj):
    """The cloth parts whose vertices the mesh holds (joined meshes hold several)."""
    a = obj.data.attributes.get("cloth_part")
    return sorted(set(_ints(obj.data, "cloth_part")) - {0}) if a is not None else []


def relink(obj, offsets):
    """Moves each part's slots to their place in the joined mesh's cache (offsets by part)."""
    part = np.array(_ints(obj.data, "cloth_part"))
    shift = np.array([offsets.get(int(p), 0) for p in part], np.int32)
    v = np.array(_ints(obj.data, "cloth_slot"), np.int32)
    obj.data.attributes["cloth_slot"].data.foreach_set("value", np.where(part > 0, v + shift, v))


def tag(obj):
    """The mesh's cloth id (kept through renames), given on first use."""
    if not obj.get(ID):
        obj[ID] = uuid.uuid4().hex
    return obj[ID]


def find(cloth_id):
    return next((o for o in bpy.data.objects if o.type == 'MESH' and o.get(ID) == cloth_id), None)


def _holder(obj):
    """The mesh's hidden cache object, made on first use. It stays the modifier's input for good: a hidden object the
    modifier stops reading drops out of evaluation, so bake and live swap its data instead."""
    c = next((o for o in bpy.data.objects if o.get(CACHE) == tag(obj)), None)
    if c is None:
        c = bpy.data.objects.new(obj.name + " cloth", bpy.data.meshes.new(obj.name + " cloth"))
        for col in obj.users_collection:
            col.objects.link(c)
        c.hide_viewport = c.hide_render = c.hide_select = True
        c[CACHE] = obj[ID]
    return c


def cache(obj, points, live=False):
    """The mesh's cache data for the bake (or the live preview's, one frame), made on first use and shown."""
    key = "%s/%s" % (tag(obj), "live" if live else "bake")
    data = next((m for m in bpy.data.meshes if m.get(CACHE) == key), None)
    if data is None:
        data = bpy.data.meshes.new(obj.name + (" cloth live" if live else " cloth bake"))
        data[CACHE], data["owner"] = key, obj[ID]
    if data.get("points") != points:
        data.clear_geometry()
        data["points"], data["start"], data["frames"] = points, 0, 0
    mod = obj.modifiers.get(MODIFIER)
    if mod is None:
        mod = obj.modifiers.new(MODIFIER, 'NODES')
        armature = [i for i, m in enumerate(obj.modifiers) if m.type == 'ARMATURE']
        if armature:        # right after skinning, before outlines and the like
            obj.modifiers.move(len(obj.modifiers) - 1, armature[-1] + 1)
    mod.node_group = _group()
    show(obj, live)
    return data


def show(obj, live):
    """Shows the mesh's bake cache or its live one (nothing there: skinned)."""
    if MODIFIER not in obj.modifiers:
        return
    key = "%s/%s" % (obj.get(ID), "live" if live else "bake")
    data = next((m for m in bpy.data.meshes if m.get(CACHE) == key), None)
    holder = _holder(obj)
    if data is None:
        _inputs(obj, Cache=holder, Count=0)
    else:
        holder.data = data
        _inputs(obj, Cache=holder, Points=data["points"], Start=data["start"], Count=data["frames"])
    obj.update_tag()


def _to_mesh(obj, arm, pos, unit):
    """Component-space positions (cm, the game's axes) -> the mesh's own space."""
    m = np.array(obj.matrix_world.inverted() @ arm.matrix_world)
    return (np.asarray(pos) @ FLIP) * unit @ m[:3, :3].T + m[:3, 3]


def store(frames, start, unit, arm):
    """A bake's cloth, frames = per frame {cloth id: slot positions} from the runner."""
    for cloth_id in frames[0] if frames else ():
        obj = find(cloth_id)
        if obj is not None:
            pos = np.stack([_to_mesh(obj, arm, f[cloth_id], unit) for f in frames])
            write(cache(obj, pos.shape[1]), start, pos)


def store_frame(result, frame, unit, arm):
    """One live frame of the runner's cloth, in the live cache."""
    for cloth_id, pos in result.items():
        obj = find(cloth_id)
        if obj is not None:
            _store(cache(obj, len(pos), live=True), frame, _to_mesh(obj, arm, pos, unit)[None], np.ones(1, bool))


def _inputs(obj, **values):
    mod = obj.modifiers[MODIFIER]
    ids = {i.name: i.identifier for i in mod.node_group.interface.items_tree if i.in_out == 'INPUT'}
    for k, v in values.items():
        getattr(mod.properties.inputs, ids[k]).value = v


def _store(data, start, pos, valid):
    f, n = pos.shape[:2]
    data.clear_geometry()
    data.vertices.add(f * n)
    data.vertices.foreach_set("co", np.ascontiguousarray(pos, np.float32).ravel())
    _set(data, "cloth_valid", 'FLOAT', np.repeat(valid.astype(np.float32), n))
    data["start"], data["frames"] = start, f
    data.update()
    owner = find(data["owner"])
    if owner is not None:
        show(owner, data[CACHE].endswith("/live"))


def write(data, start, pos):
    """A whole cache from frame `start` on: pos shaped (frames, slots, 3)."""
    _store(data, start, np.asarray(pos), np.ones(len(pos), bool))


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

    def sample(value, index, kind):
        s = n.new('GeometryNodeSampleIndex')
        s.data_type, s.domain = kind, 'POINT'
        link(info.outputs["Geometry"], s.inputs["Geometry"])
        link(value, s.inputs["Value"])
        link(index, s.inputs["Index"])
        return s.outputs[0]

    def compare(kind, op, a, b=None, value=0):
        c = n.new('FunctionNodeCompare')
        c.data_type, c.operation = kind, op
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
    slot = attr("cloth_slot", 'INT')
    index = imath('ADD', slot, imath('MULTIPLY', rel, gi.outputs["Points"]))
    skinned = n.new('GeometryNodeInputPosition').outputs[0]
    cloth = sample(skinned, index, 'FLOAT_VECTOR')
    valid = compare('FLOAT', 'GREATER_THAN', sample(attr("cloth_valid", 'FLOAT'), index, 'FLOAT'), value=0.5)
    selected = both(both(compare('INT', 'GREATER_EQUAL', slot, value=0), compare('INT', 'GREATER_EQUAL', rel, value=0)),
                    both(compare('INT', 'LESS_THAN', rel, gi.outputs["Count"]), valid))
    toward = n.new('ShaderNodeVectorMath')
    toward.operation = 'SUBTRACT'
    link(cloth, toward.inputs[0])
    link(skinned, toward.inputs[1])
    lerp = n.new('ShaderNodeVectorMath')
    lerp.operation = 'MULTIPLY_ADD'
    link(toward.outputs[0], lerp.inputs[0])
    link(attr("cloth_blend", 'FLOAT'), lerp.inputs[1])
    link(skinned, lerp.inputs[2])
    move = n.new('GeometryNodeSetPosition')
    link(gi.outputs["Geometry"], move.inputs["Geometry"])
    link(selected, move.inputs["Selection"])
    link(lerp.outputs[0], move.inputs["Position"])
    link(move.outputs[0], go.inputs[0])
    return g
