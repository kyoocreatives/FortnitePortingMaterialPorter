"""The preview without any 3D view in the file to draw through: the scene's evaluated meshes, lit by one light in
their materials' viewport colours. Plainer than a real view; only for files without one."""
import gpu
import numpy as np

_shader = None
_topology = {}      # object name -> (vertex count, triangle count, [(material slot, indices)])


def _get_shader():
    global _shader
    if _shader is None:
        info = gpu.types.GPUShaderCreateInfo()
        info.push_constant('MAT4', "mvp")
        info.push_constant('VEC4', "color")
        info.push_constant('VEC3', "light")
        info.vertex_in(0, 'VEC3', "pos")
        info.vertex_in(1, 'VEC3', "nor")
        iface = gpu.types.GPUStageInterfaceInfo("mp_preview_iface")
        iface.smooth('VEC3', "n")
        info.vertex_out(iface)
        info.fragment_out(0, 'VEC4', "frag")
        info.vertex_source("void main() { n = nor; gl_Position = mvp * vec4(pos, 1.0); }")
        info.fragment_source("void main() { float d = max(dot(normalize(n), light), 0.0);"
                             " frag = vec4(pow(color.rgb * (0.3 + 0.7 * d), vec3(1.0 / 2.2)), 1.0); }")     # to display
        _shader = gpu.shader.create_from_info(info)
    return _shader


def _parts(name, mesh):
    """The mesh's triangles per material slot, kept while its counts stay (a deforming mesh keeps its topology)."""
    nv, nt = len(mesh.vertices), len(mesh.loop_triangles)
    known = _topology.get(name)
    if known is not None and known[:2] == (nv, nt):
        return known[2]
    tris = np.empty(nt * 3, np.int32)
    mesh.loop_triangles.foreach_get("vertices", tris)
    mats = np.empty(nt, np.int32)
    mesh.loop_triangles.foreach_get("material_index", mats)
    tris = tris.reshape(-1, 3)
    parts = [(int(i), gpu.types.GPUIndexBuf(type='TRIS', seq=tris[mats == i])) for i in np.unique(mats)]
    _topology[name] = (nv, nt, parts)
    return parts


def _format():
    fmt = gpu.types.GPUVertFormat()
    fmt.attr_add(id="pos", comp_type='F32', len=3, fetch_mode='FLOAT')
    fmt.attr_add(id="nor", comp_type='F32', len=3, fetch_mode='FLOAT')
    return fmt


def draw(offscreen, scene, view, projection):
    graph = scene.view_layers[0].depsgraph
    if graph is None:
        return False
    shader = _get_shader()
    with offscreen.bind():
        fb = gpu.state.active_framebuffer_get()
        fb.clear(color=(0.25, 0.25, 0.26, 1.0), depth=1.0)
        gpu.state.depth_test_set('LESS_EQUAL')
        gpu.state.depth_mask_set(True)
        shader.bind()
        shader.uniform_float("light", (0.4, -0.5, 0.77))
        for ob in scene.collection.all_objects:
            if ob.type != 'MESH':
                continue
            ev = ob.evaluated_get(graph)
            mesh = ev.data          # evaluated: deformed by its modifiers
            if not len(mesh.loop_triangles):
                continue
            co = np.empty(len(mesh.vertices) * 3, np.float32)
            mesh.vertices.foreach_get("co", co)
            nor = np.empty(len(mesh.vertices) * 3, np.float32)
            mesh.vertex_normals.foreach_get("vector", nor)
            rot = np.array(ev.matrix_world.to_3x3(), np.float32)
            vbo = gpu.types.GPUVertBuf(_format(), len(mesh.vertices))
            vbo.attr_fill("pos", co.reshape(-1, 3))
            vbo.attr_fill("nor", nor.reshape(-1, 3) @ rot.T)
            shader.uniform_float("mvp", projection @ view @ ev.matrix_world)
            slots = ev.material_slots
            for slot, ibo in _parts(ob.name, mesh):
                mat = slots[slot].material if slot < len(slots) else None
                shader.uniform_float("color", tuple(mat.diffuse_color) if mat else (0.7, 0.7, 0.7, 1.0))
                gpu.types.GPUBatch(type='TRIS', buf=vbo, elem=ibo).draw(shader)
        gpu.state.depth_test_set('NONE')
        gpu.state.depth_mask_set(False)
    return True


def forget():
    _topology.clear()
