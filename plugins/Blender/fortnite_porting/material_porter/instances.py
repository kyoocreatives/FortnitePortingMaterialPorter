"""A map's instanced meshes (one ISM component's instances: PCG grass, scatter props) as one points object: a vertex per
instance with its rotation, scale and custom data, and a geometry nodes modifier putting the component's mesh object on
each (that object waits in the FP Instance Sources collection). Materials read the custom data from the instance
(Attribute nodes of type INSTANCER, env.instance_data / env.primitive_data)."""
import bpy
import numpy as np

from ..processing.utils import set_geo_nodes_param

SOURCES = "FP Instance Sources"
GROUP = "FP Instances"
GROUP_VERSION = 1
PLACED = "mp_placed"        # on a points object: {mesh, class, component, actor, count}, what the Scatter tab lists
TAGS = ("mp_hidden", "mp_layer")        # moved to the points object: hidden.gather/gather_layers move what carries them


def place(ctx, mesh, source, parent, cursor):
    """Turn the imported `source` object into the instance source of a new points object, returned."""
    data = mesh["MPInstances"]
    n = int(data["Count"])
    t = np.asarray(data["Transforms"], dtype=np.float64).reshape(n, 9)
    points = bpy.data.meshes.new(source.name + " Instances")
    points.vertices.add(n)
    # as processing.utils make_vector / make_euler place a single record
    points.vertices.foreach_set("co", (t[:, 0:3] * (1.0, -1.0, 1.0) * ctx.scale).astype(np.float32).ravel())
    _attribute(points, "rotation", 'QUATERNION', "value", _quaternions(np.radians(t[:, 5]), -np.radians(t[:, 3]), -np.radians(t[:, 4])))
    _attribute(points, "scale", 'FLOAT_VECTOR', "vector", t[:, 6:9])
    floats = int(data.get("Floats") or 0)
    if floats:
        values = np.asarray(data["Data"], dtype=np.float32).reshape(n, floats)
        names = ["mp_pic"] + (["mp_cpd", "mp_cpd_n"] if data.get("PrimitiveData") else [])
        heads = {"mp_pic": float(floats), "mp_cpd": 1.0, "mp_cpd_n": float(floats)}
        for name in names:
            _attribute(points, name, 'FLOAT', "value", np.full(n, heads[name]))
        for j in range(floats):
            _attribute(points, "mp_pic%d" % j, 'FLOAT', "value", values[:, j])
            if data.get("PrimitiveData"):
                _attribute(points, "mp_cpd%d" % j, 'FLOAT', "value", values[:, j])

    obj = bpy.data.objects.new(points.name, points)
    for c in source.users_collection:
        c.objects.link(obj)
    obj.parent = parent
    if cursor:
        obj.location = bpy.context.scene.cursor.location
    obj[PLACED] = {"mesh": mesh.get("Path", ""), "class": data.get("Class", ""), "component": data.get("Component", ""),
                   "actor": data.get("Actor", ""), "count": n}
    for tag in TAGS:
        if tag in source:
            obj[tag] = source[tag]
            del source[tag]
    for flag in ("visible_camera", "visible_diffuse", "visible_glossy", "visible_transmission", "visible_volume_scatter", "visible_shadow"):
        setattr(obj, flag, getattr(source, flag))
    modifier = obj.modifiers.new(GROUP, 'NODES')
    modifier.node_group = group()
    set_geo_nodes_param(modifier, "Object", source)

    for c in list(source.users_collection):
        c.objects.unlink(source)
    sources(ctx).objects.link(source)
    source.parent = None        # its record sits at the origin
    return obj


def sources(ctx):
    """The import's FP Instance Sources collection, excluded from the view layer (the instances still draw)."""
    root = ctx.collection
    found = next((c for c in root.children if c.get("mp_instance_sources")), None)
    if found is not None:
        return found
    found = bpy.data.collections.new(SOURCES)
    found["mp_instance_sources"] = 1
    root.children.link(found)
    if (layer := _layer(bpy.context.view_layer.layer_collection, found)) is not None:
        layer.exclude = True
    return found


def group():
    """Points -> instances of the Object input, turned and scaled by each point's rotation and scale."""
    g = bpy.data.node_groups.get(GROUP)
    if g is not None and g.get("mp_version") == GROUP_VERSION:
        return g
    g = bpy.data.node_groups.new(GROUP, 'GeometryNodeTree')
    g["mp_version"] = GROUP_VERSION
    g.is_modifier = True
    face = g.interface
    face.new_socket(name="Geometry", in_out='INPUT', socket_type='NodeSocketGeometry')
    face.new_socket(name="Geometry", in_out='OUTPUT', socket_type='NodeSocketGeometry')
    face.new_socket(name="Object", in_out='INPUT', socket_type='NodeSocketObject', description="The mesh placed on each point")
    nodes, links = g.nodes, g.links
    inputs, outputs = nodes.new("NodeGroupInput"), nodes.new("NodeGroupOutput")
    info = nodes.new("GeometryNodeObjectInfo")
    info.transform_space = 'ORIGINAL'
    info.inputs["As Instance"].default_value = True
    links.new(inputs.outputs["Object"], info.inputs["Object"])
    rotation = nodes.new("GeometryNodeInputNamedAttribute")
    rotation.data_type = 'QUATERNION'
    rotation.inputs["Name"].default_value = "rotation"
    scale = nodes.new("GeometryNodeInputNamedAttribute")
    scale.data_type = 'FLOAT_VECTOR'
    scale.inputs["Name"].default_value = "scale"
    on = nodes.new("GeometryNodeInstanceOnPoints")
    links.new(inputs.outputs["Geometry"], on.inputs["Points"])
    links.new(info.outputs["Geometry"], on.inputs["Instance"])
    links.new(rotation.outputs["Attribute"], on.inputs["Rotation"])
    links.new(scale.outputs["Attribute"], on.inputs["Scale"])
    links.new(on.outputs["Instances"], outputs.inputs["Geometry"])
    for x, n in enumerate((inputs, info, rotation, scale, on, outputs)):
        n.location = (x * 200, 0)
    return g


def _quaternions(x, y, z):
    """Blender's XYZ Euler as (w, x, y, z) quaternions (mathutils' eul_to_quat), for arrays of angles."""
    ci, cj, ch = np.cos(x / 2), np.cos(y / 2), np.cos(z / 2)
    si, sj, sh = np.sin(x / 2), np.sin(y / 2), np.sin(z / 2)
    cc, cs, sc, ss = ci * ch, ci * sh, si * ch, si * sh
    return np.stack([cj * cc + sj * ss, cj * sc - sj * cs, cj * ss + sj * cc, cj * cs - sj * sc], axis=1)


def _attribute(mesh, name, kind, field, values):
    mesh.attributes.new(name, kind, 'POINT').data.foreach_set(field, np.ascontiguousarray(values, dtype=np.float32).ravel())


def _layer(layer, collection):
    if layer.collection == collection:
        return layer
    for child in layer.children:
        if (hit := _layer(child, collection)) is not None:
            return hit
    return None
