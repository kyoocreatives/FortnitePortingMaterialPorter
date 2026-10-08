"""The geometry nodes groups that draw particles and ribbons."""

import bpy

from .stand import DEPTH_BIAS, FACINGS, GROUP, GROUP_VERSION, RIBBONS, TURNS


def group():
    """The node group that plays a points mesh: the current frame's points with the piece on each."""
    g = bpy.data.node_groups.get(GROUP)
    if g is not None and g.get("mp_version") == GROUP_VERSION:
        return g
    g = bpy.data.node_groups.new(GROUP, 'GeometryNodeTree')
    g["mp_version"] = GROUP_VERSION
    g.is_modifier = True
    face = g.interface
    face.new_socket(name="Geometry", in_out='INPUT', socket_type='NodeSocketGeometry')
    face.new_socket(name="Geometry", in_out='OUTPUT', socket_type='NodeSocketGeometry')
    face.new_socket(name="Piece", in_out='INPUT', socket_type='NodeSocketObject', description="What each particle draws")
    face.new_socket(name="Start Frame", in_out='INPUT', socket_type='NodeSocketInt', description="The scene frame the effect starts on").default_value = 1
    face.new_socket(name="Frames", in_out='INPUT', socket_type='NodeSocketInt', description="How many frames were replayed").default_value = 1
    face.new_socket(name="Loop", in_out='INPUT', socket_type='NodeSocketBool', description="Start over after the last replayed frame")
    turn = face.new_socket(name="Turn", in_out='INPUT', socket_type='NodeSocketInt', description=TURNS)
    turn.min_value, turn.max_value = 0, 6
    face.new_socket(name="Piece Offset", in_out='INPUT', socket_type='NodeSocketVector',
                    description="Where the piece sits on its particle, in the particle's own space: a sprite's pivot, a mesh's pivot offset")
    face.new_socket(name="Piece Rotation", in_out='INPUT', socket_type='NodeSocketRotation', description="The renderer's own rotation of the piece")
    face.new_socket(name="Piece Scale", in_out='INPUT', socket_type='NodeSocketVector', description="The renderer's own scale of the piece").default_value = (1.0, 1.0, 1.0)
    face.new_socket(name="Depth Bias", in_out='INPUT', socket_type='NodeSocketFloat', description=DEPTH_BIAS)

    nodes, links = g.nodes, g.links
    column = [0]

    def node(kind, **settings):
        n = nodes.new(kind)
        n.location = (column[0], 0)
        column[0] += 190
        for k, v in settings.items():
            setattr(n, k, v)
        return n

    def attribute(name, kind):
        n = node("GeometryNodeInputNamedAttribute", data_type=kind)
        n.inputs["Name"].default_value = name
        return n.outputs[0]

    def math_(op, a, b):
        n = node("ShaderNodeMath", operation=op)
        for socket, v in zip(n.inputs, (a, b)):
            if hasattr(v, "is_linked"):
                links.new(v, socket)
            else:
                socket.default_value = v
        return n.outputs[0]

    inputs, outputs = node("NodeGroupInput"), nodes.new("NodeGroupOutput")
    # the replay frame the scene is on
    time = node("GeometryNodeInputSceneTime")
    index = math_('SUBTRACT', time.outputs["Frame"], inputs.outputs["Start Frame"])
    looped = math_('FLOORED_MODULO', index, inputs.outputs["Frames"])
    which = node("GeometryNodeSwitch", input_type='FLOAT')
    links.new(inputs.outputs["Loop"], which.inputs["Switch"])
    links.new(index, which.inputs["False"])
    links.new(looped, which.inputs["True"])
    other = node("FunctionNodeCompare", data_type='FLOAT', operation='NOT_EQUAL')
    links.new(attribute("mp_frame", 'INT'), other.inputs[0])
    links.new(which.outputs[0], other.inputs[1])
    other.inputs["Epsilon"].default_value = 0.5
    current = node("GeometryNodeDeleteGeometry", domain='POINT')
    links.new(inputs.outputs["Geometry"], current.inputs["Geometry"])
    links.new(other.outputs[0], current.inputs["Selection"])

    # the piece, with the renderer's rotation and scale
    piece = node("GeometryNodeObjectInfo", transform_space='ORIGINAL')
    links.new(inputs.outputs["Piece"], piece.inputs["Object"])
    placed = node("GeometryNodeTransform")
    links.new(piece.outputs["Geometry"], placed.inputs["Geometry"])
    links.new(inputs.outputs["Piece Offset"], placed.inputs["Translation"])
    links.new(inputs.outputs["Piece Rotation"], placed.inputs["Rotation"])
    links.new(inputs.outputs["Piece Scale"], placed.inputs["Scale"])
    # UE transforms a particle mesh's normals by its scale, not the inverse (a sphere flattened into a
    # card keeps a round one's falloff): these are the normals that result once the piece is scaled
    squared = node("ShaderNodeVectorMath", operation='MULTIPLY')
    links.new(inputs.outputs["Piece Scale"], squared.inputs[0])
    links.new(inputs.outputs["Piece Scale"], squared.inputs[1])
    bent = node("ShaderNodeVectorMath", operation='MULTIPLY')
    links.new(node("GeometryNodeInputNormal").outputs[0], bent.inputs[0])
    links.new(squared.outputs[0], bent.inputs[1])
    unit = node("ShaderNodeVectorMath", operation='NORMALIZE')
    links.new(bent.outputs[0], unit.inputs[0])
    shaded = node("GeometryNodeSetMeshNormal", mode='FREE', domain='CORNER')
    links.new(placed.outputs[0], shaded.inputs["Mesh"])
    links.new(unit.outputs[0], shaded.inputs["Custom Normal"])

    # how the piece is turned on its particle
    camera = node("GeometryNodeObjectInfo", transform_space='RELATIVE')
    links.new(node("GeometryNodeInputActiveCamera").outputs[0], camera.inputs["Object"])
    toward = node("ShaderNodeVectorMath", operation='SUBTRACT')
    links.new(camera.outputs["Location"], toward.inputs[0])
    links.new(node("GeometryNodeInputPosition").outputs[0], toward.inputs[1])
    velocity = attribute("mp_velocity", 'FLOAT_VECTOR')
    along = attribute("mp_align", 'FLOAT_VECTOR')      # what a sprite's length runs along: its velocity, or the emitter's vector
    own = attribute("mp_rotation", 'QUATERNION')
    spin = node("FunctionNodeAxisAngleToRotation")
    spin.inputs["Axis"].default_value = (0.0, 0.0, 1.0)
    links.new(attribute("mp_spin", 'FLOAT'), spin.inputs["Angle"])

    def spun(rotation):
        n = node("FunctionNodeRotateRotation", rotation_space='LOCAL')
        links.new(rotation, n.inputs[0])
        links.new(spin.outputs[0], n.inputs[1])
        return n.outputs[0]

    def aligned(axis, to, rotation=None, pivot='AUTO'):
        n = node("FunctionNodeAlignRotationToVector", axis=axis, pivot_axis=pivot)
        if rotation is not None:
            links.new(rotation, n.inputs["Rotation"])
        if hasattr(to, "is_linked"):
            links.new(to, n.inputs["Vector"])
        else:
            n.inputs["Vector"].default_value = to
        return n.outputs[0]

    def mesh_facing(direction):
        # mesh X along the direction, Z as far up as that allows, then the particle's own rotation
        n = node("FunctionNodeRotateRotation", rotation_space='LOCAL')
        links.new(aligned('Z', (0.0, 0.0, 1.0), aligned('X', direction), 'X'), n.inputs[0])
        links.new(own, n.inputs[1])
        return n.outputs[0]

    turns = [
        own,
        spun(camera.outputs["Rotation"]),                                   # a sprite: the camera's plane, spun
        aligned('Z', toward.outputs[0], aligned('Y', along), 'Y'),          # length along the velocity, face to the camera
        spun(aligned('Z', attribute("mp_facing", 'FLOAT_VECTOR'))),         # face along the particle's own direction
        mesh_facing(velocity),
        mesh_facing(toward.outputs[0]),
        aligned('Y', along, aligned('Z', attribute("mp_facing", 'FLOAT_VECTOR')), 'Z'),     # face along its own direction, length along its own vector
    ]
    rotation = node("GeometryNodeIndexSwitch", data_type='ROTATION')
    while len(rotation.index_switch_items) < len(turns):
        rotation.index_switch_items.new()
    links.new(inputs.outputs["Turn"], rotation.inputs["Index"])
    for i, turn in enumerate(turns):
        links.new(turn, rotation.inputs[i + 1])

    # each particle is drawn toward the camera by its camera offset
    nearer = node("ShaderNodeVectorMath", operation='NORMALIZE')
    links.new(toward.outputs[0], nearer.inputs[0])
    by = node("ShaderNodeVectorMath", operation='SCALE')
    links.new(nearer.outputs[0], by.inputs[0])
    links.new(attribute("mp_camera_offset", 'FLOAT'), by.inputs["Scale"])
    offset = node("GeometryNodeSetPosition")
    links.new(current.outputs[0], offset.inputs["Geometry"])
    links.new(by.outputs[0], offset.inputs["Offset"])
    instances = node("GeometryNodeInstanceOnPoints")
    links.new(offset.outputs[0], instances.inputs["Points"])
    links.new(shaded.outputs[0], instances.inputs["Instance"])
    links.new(rotation.outputs[0], instances.inputs["Rotation"])
    links.new(attribute("mp_scale", 'FLOAT_VECTOR'), instances.inputs["Scale"])
    # depth bias: each instance is scaled about the camera so it moves along its own view rays
    # (it looks the same, only nearer)
    gap = node("ShaderNodeVectorMath", operation='DISTANCE')
    links.new(node("GeometryNodeInputPosition").outputs[0], gap.inputs[0])
    links.new(camera.outputs["Location"], gap.inputs[1])
    nearer_by = math_('MAXIMUM', math_('SUBTRACT', 1.0, math_('DIVIDE', inputs.outputs["Depth Bias"], math_('MAXIMUM', gap.outputs["Value"], 0.01))), 0.05)
    biased = node("GeometryNodeScaleInstances")
    links.new(instances.outputs[0], biased.inputs["Instances"])
    links.new(nearer_by, biased.inputs["Scale"])
    links.new(camera.outputs["Location"], biased.inputs["Center"])
    biased.inputs["Local Space"].default_value = False
    outputs.location = (column[0], 0)
    links.new(biased.outputs[0], outputs.inputs[0])
    return g


def ribbons():
    """The node group that plays a ribbon's points mesh: the current frame's points strung into
    ribbons (one per ribbon ID, in link order), as wide as each particle says, facing the camera."""
    g = bpy.data.node_groups.get(RIBBONS)
    if g is not None and g.get("mp_version") == GROUP_VERSION:
        return g
    g = bpy.data.node_groups.new(RIBBONS, 'GeometryNodeTree')
    g["mp_version"] = GROUP_VERSION
    g.is_modifier = True
    face = g.interface
    face.new_socket(name="Geometry", in_out='INPUT', socket_type='NodeSocketGeometry')
    face.new_socket(name="Geometry", in_out='OUTPUT', socket_type='NodeSocketGeometry')
    face.new_socket(name="Material", in_out='INPUT', socket_type='NodeSocketMaterial')
    face.new_socket(name="Start Frame", in_out='INPUT', socket_type='NodeSocketInt', description="The scene frame the effect starts on").default_value = 1
    face.new_socket(name="Frames", in_out='INPUT', socket_type='NodeSocketInt', description="How many frames were replayed").default_value = 1
    face.new_socket(name="Loop", in_out='INPUT', socket_type='NodeSocketBool', description="Start over after the last replayed frame")
    facing = face.new_socket(name="Facing", in_out='INPUT', socket_type='NodeSocketInt', description=FACINGS)
    facing.min_value, facing.max_value = 0, 2
    shape = face.new_socket(name="Shape", in_out='INPUT', socket_type='NodeSocketInt',
                            description="What is swept along the ribbon: 0: a plane; 1: several planes turned about it; 2: a tube")
    shape.min_value, shape.max_value = 0, 2
    sides = face.new_socket(name="Sides", in_out='INPUT', socket_type='NodeSocketInt', description="How many planes (Shape 1), how many sides the tube has (Shape 2)")
    sides.min_value, sides.default_value = 1, 2
    face.new_socket(name="Depth Bias", in_out='INPUT', socket_type='NodeSocketFloat', description=DEPTH_BIAS)

    nodes, links = g.nodes, g.links
    column = [0]

    def node(kind, **settings):
        n = nodes.new(kind)
        n.location = (column[0], 0)
        column[0] += 190
        for k, v in settings.items():
            setattr(n, k, v)
        return n

    def attribute(name, kind):
        n = node("GeometryNodeInputNamedAttribute", data_type=kind)
        n.inputs["Name"].default_value = name
        return n.outputs[0]

    def math_(op, a, b):
        n = node("ShaderNodeMath", operation=op)
        links.new(a, n.inputs[0])
        links.new(b, n.inputs[1])
        return n.outputs[0]

    def vector(op, a, b=None):
        n = node("ShaderNodeVectorMath", operation=op)
        links.new(a, n.inputs[0])
        if b is not None:
            links.new(b, n.inputs[1])
        return n.outputs[0]

    inputs, outputs = node("NodeGroupInput"), nodes.new("NodeGroupOutput")
    time = node("GeometryNodeInputSceneTime")
    index = math_('SUBTRACT', time.outputs["Frame"], inputs.outputs["Start Frame"])
    looped = math_('FLOORED_MODULO', index, inputs.outputs["Frames"])
    which = node("GeometryNodeSwitch", input_type='FLOAT')
    links.new(inputs.outputs["Loop"], which.inputs["Switch"])
    links.new(index, which.inputs["False"])
    links.new(looped, which.inputs["True"])
    other = node("FunctionNodeCompare", data_type='FLOAT', operation='NOT_EQUAL')
    links.new(attribute("mp_frame", 'INT'), other.inputs[0])
    links.new(which.outputs[0], other.inputs[1])
    other.inputs["Epsilon"].default_value = 0.5
    current = node("GeometryNodeDeleteGeometry", domain='POINT')
    links.new(inputs.outputs["Geometry"], current.inputs["Geometry"])
    links.new(other.outputs[0], current.inputs["Selection"])
    cloud = node("GeometryNodeMeshToPoints")
    links.new(current.outputs[0], cloud.inputs[0])

    strung = node("GeometryNodePointsToCurves")
    links.new(cloud.outputs[0], strung.inputs["Points"])
    links.new(attribute("mp_ribbon", 'INT'), strung.inputs["Curve Group ID"])
    links.new(attribute("mp_order", 'FLOAT'), strung.inputs["Weight"])
    smooth = node("GeometryNodeCurveSplineType", spline_type='CATMULL_ROM')
    links.new(strung.outputs[0], smooth.inputs["Curve"])
    fine = node("GeometryNodeSetSplineResolution")
    links.new(smooth.outputs[0], fine.inputs[0])
    fine.inputs["Resolution"].default_value = 4
    wide = node("GeometryNodeSetCurveRadius")
    links.new(fine.outputs[0], wide.inputs["Curve"])
    width = attribute("mp_width", 'FLOAT')
    links.new(width, wide.inputs["Radius"])

    # the ribbon's width runs across what it faces: the camera, or the particles' facing
    camera = node("GeometryNodeObjectInfo", transform_space='RELATIVE')
    links.new(node("GeometryNodeInputActiveCamera").outputs[0], camera.inputs["Object"])
    toward = vector('SUBTRACT', camera.outputs["Location"], node("GeometryNodeInputPosition").outputs[0])
    tangent = node("GeometryNodeInputTangent").outputs[0]
    own = attribute("mp_facing", 'FLOAT_VECTOR')
    facing = node("GeometryNodeIndexSwitch", data_type='VECTOR')
    while len(facing.index_switch_items) < 3:
        facing.index_switch_items.new()
    links.new(inputs.outputs["Facing"], facing.inputs["Index"])
    links.new(vector('CROSS_PRODUCT', tangent, toward), facing.inputs[1])      # across the view
    links.new(vector('CROSS_PRODUCT', tangent, own), facing.inputs[2])         # across the particles' facing
    links.new(own, facing.inputs[3])                                           # along the particles' side vector
    side = vector('NORMALIZE', facing.outputs[0])
    turned = node("GeometryNodeSetCurveNormal")
    links.new(wide.outputs[0], turned.inputs["Curve"])
    if "Mode" in turned.inputs:
        turned.inputs["Mode"].default_value = 'Free'
    else:
        turned.mode = 'FREE'
    links.new(side, turned.inputs["Normal"])

    # the profile swept along it: a plane a unit wide, several planes turned about the ribbon, or a tube a unit across
    line = node("GeometryNodeCurvePrimitiveLine")
    line.inputs["Start"].default_value = (-0.5, 0.0, 0.0)
    line.inputs["End"].default_value = (0.5, 0.0, 0.0)
    spots = node("GeometryNodePoints")
    links.new(inputs.outputs["Sides"], spots.inputs["Count"])
    half = node("ShaderNodeValue")
    half.outputs[0].default_value = 3.141592653589793
    turn = node("ShaderNodeMath", operation='MULTIPLY')         # each plane: half a turn over the count, on from the last
    links.new(node("GeometryNodeInputIndex").outputs[0], turn.inputs[0])
    links.new(math_('DIVIDE', half.outputs[0], inputs.outputs["Sides"]), turn.inputs[1])
    about = node("ShaderNodeCombineXYZ")
    links.new(turn.outputs[0], about.inputs[2])
    planes = node("GeometryNodeInstanceOnPoints")
    links.new(spots.outputs[0], planes.inputs["Points"])
    links.new(line.outputs[0], planes.inputs["Instance"])
    links.new(about.outputs[0], planes.inputs["Rotation"])
    several = node("GeometryNodeRealizeInstances")
    links.new(planes.outputs[0], several.inputs[0])
    tube = node("GeometryNodeCurvePrimitiveCircle")
    tube.inputs["Radius"].default_value = 0.5
    least = node("ShaderNodeValue")
    least.outputs[0].default_value = 3.0
    links.new(math_('MAXIMUM', inputs.outputs["Sides"], least.outputs[0]), tube.inputs["Resolution"])
    profile = node("GeometryNodeIndexSwitch", data_type='GEOMETRY')
    while len(profile.index_switch_items) < 3:
        profile.index_switch_items.new()
    links.new(inputs.outputs["Shape"], profile.inputs["Index"])
    links.new(line.outputs[0], profile.inputs[1])
    links.new(several.outputs[0], profile.inputs[2])
    links.new(tube.outputs[0], profile.inputs[3])
    across = node("GeometryNodeCaptureAttribute", domain='POINT')
    across.capture_items.new('FLOAT', "V")
    links.new(profile.outputs[0], across.inputs[0])
    links.new(node("GeometryNodeSplineParameter").outputs["Factor"], across.inputs["V"])
    ribbon = node("GeometryNodeCurveToMesh")
    links.new(turned.outputs[0], ribbon.inputs["Curve"])
    links.new(across.outputs[0], ribbon.inputs["Profile Curve"])
    if "Scale" in ribbon.inputs:        # Blender 5: the profile's scale is the node's own input, not the curve's radius
        links.new(width, ribbon.inputs["Scale"])
    # two UV sets: U as each point carries it (the renderer's way of laying it along the ribbon),
    # V across, between the two edge values
    mapped = ribbon
    for name, carried in (("UV0", "mp_uv0"), ("UV1", "mp_uv1")):
        parts = node("ShaderNodeSeparateXYZ")
        links.new(attribute(carried, 'FLOAT_VECTOR'), parts.inputs[0])
        v = node("ShaderNodeMix", data_type='FLOAT')
        links.new(across.outputs["V"], v.inputs[0])
        links.new(parts.outputs[1], v.inputs[2])
        links.new(parts.outputs[2], v.inputs[3])
        uv = node("ShaderNodeCombineXYZ")
        links.new(parts.outputs[0], uv.inputs[0])
        links.new(v.outputs[0], uv.inputs[1])
        store = node("GeometryNodeStoreNamedAttribute", data_type='FLOAT2', domain='CORNER')
        store.inputs["Name"].default_value = name
        links.new(mapped.outputs[0], store.inputs[0])
        links.new(uv.outputs[0], store.inputs["Value"])
        mapped = store
    material = node("GeometryNodeSetMaterial")
    links.new(mapped.outputs[0], material.inputs[0])
    links.new(inputs.outputs["Material"], material.inputs["Material"])
    # depth bias: each vertex moves nearer along its own view ray
    eye = node("GeometryNodeObjectInfo", transform_space='RELATIVE')
    links.new(node("GeometryNodeInputActiveCamera").outputs[0], eye.inputs["Object"])
    to_eye = vector('NORMALIZE', vector('SUBTRACT', eye.outputs["Location"], node("GeometryNodeInputPosition").outputs[0]))
    step = node("ShaderNodeVectorMath", operation='SCALE')
    links.new(to_eye, step.inputs[0])
    links.new(inputs.outputs["Depth Bias"], step.inputs["Scale"])
    biased = node("GeometryNodeSetPosition")
    links.new(material.outputs[0], biased.inputs["Geometry"])
    links.new(step.outputs[0], biased.inputs["Offset"])
    outputs.location = (column[0], 0)
    links.new(biased.outputs[0], outputs.inputs[0])
    return g
