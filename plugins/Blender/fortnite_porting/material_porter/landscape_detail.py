"""Landscape geometry that keeps what its materials read (UV0's quad coordinates, UV1, a colour attribute per paint
layer): a terrain imported at less detail, and a remeshed copy given those back from the original."""
import bpy
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

# values per element of each attribute type, and the foreach key that reads them
_WIDTH = {"FLOAT": 1, "INT": 1, "BOOLEAN": 1, "INT8": 1, "FLOAT2": 2, "INT32_2D": 2, "INT16_2D": 2, "FLOAT_VECTOR": 3,
          "FLOAT_COLOR": 4, "BYTE_COLOR": 4, "QUATERNION": 4, "FLOAT4X4": 16}
_DTYPE = {"INT": np.int32, "BOOLEAN": bool, "INT8": np.int32, "INT32_2D": np.int32, "INT16_2D": np.int32}


def _key(attr):
    return "color" if attr.data_type in ("FLOAT_COLOR", "BYTE_COLOR") else "vector" if attr.data_type in ("FLOAT_VECTOR", "FLOAT2") else "value"


def _get(attr):
    width = _WIDTH[attr.data_type]
    out = np.zeros(len(attr.data) * width, _DTYPE.get(attr.data_type, np.float32))
    attr.data.foreach_get(_key(attr), out)
    return out.reshape(len(attr.data), width)


def _set(mesh, name, data_type, domain, values):
    attr = mesh.attributes.get(name) or mesh.attributes.new(name, data_type, domain)
    attr.data.foreach_set(_key(attr), np.ascontiguousarray(values).ravel())
    return attr


def _loops(mesh):
    vertex = np.zeros(len(mesh.loops), np.int32)
    mesh.loops.foreach_get("vertex_index", vertex)
    return vertex


def _per_vertex_uv(mesh, name):
    uv = np.zeros((len(mesh.vertices), 2), np.float32)
    uv[_loops(mesh)] = _get(mesh.attributes[name])
    return uv


def _skip(attr):
    # Blender's own (positions, internal ones) and custom normals, which live in each corner's own frame
    return attr.name.startswith(".") or attr.name in ("position", "custom_normal", "sharp_face", "material_index") \
        or attr.data_type not in _WIDTH or attr.domain not in ('POINT', 'CORNER', 'FACE')


def reduce(mesh, step):
    """The landscape mesh at every step-th grid line (each component's last line kept, so neighbours still meet), as a
    new mesh; the mesh itself when step is 1 or it isn't a landscape grid as LandscapeMeshDto writes it (components of
    (n+1)^2 vertices, x fastest, UV0 the landscape's quad coordinates)."""
    n = len(mesh.vertices)
    if step <= 1 or not n or "UV0" not in mesh.uv_layers:
        return mesh
    u = _per_vertex_uv(mesh, "UV0")[:, 0]
    wraps = np.nonzero(np.diff(u) < 0)[0]
    row = int(wraps[0]) + 1 if len(wraps) else n
    if row < 2 or n % (row * row) or not np.allclose(np.diff(u[:row]), 1.0, atol=1e-3):
        return mesh
    size = row - 1
    keep = np.array(sorted(set(range(0, size + 1, step)) | {size}))
    m, components = len(keep), n // (row * row)
    grid = (keep[None, :] + keep[:, None] * row).ravel()
    old = (np.arange(components)[:, None] * row * row + grid[None, :]).ravel()
    xs, ys = np.meshgrid(np.arange(m - 1), np.arange(m - 1))
    a = (np.arange(components)[:, None] * m * m + (xs + ys * m).ravel()[None, :]).ravel()
    quads = np.stack([a, a + m, a + m + 1, a + 1], 1)          # the originals' winding: (x,y) (x,y+1) (x+1,y+1) (x+1,y)
    co = np.zeros(n * 3, np.float32)
    mesh.vertices.foreach_get("co", co)
    new = bpy.data.meshes.new(mesh.name)
    new.from_pydata(co.reshape(n, 3)[old].tolist(), [], quads.tolist())
    for material in mesh.materials:
        new.materials.append(material)
    corner_of = np.zeros(n, np.int64)                          # a corner of each old vertex
    loops = _loops(mesh)
    corner_of[loops] = np.arange(len(loops))
    totals = np.zeros(len(mesh.polygons), np.int32)
    mesh.polygons.foreach_get("loop_total", totals)
    face_of_corner = np.repeat(np.arange(len(mesh.polygons)), totals)
    new_loops = old[_loops(new)]
    face_old = face_of_corner[corner_of[old[quads[:, 0]]]]
    for attr in [a_ for a_ in mesh.attributes if not _skip(a_)]:
        values = _get(attr)
        picked = values[old] if attr.domain == 'POINT' else values[corner_of[new_loops]] if attr.domain == 'CORNER' else values[face_old]
        _set(new, attr.name, attr.data_type, attr.domain, picked)
    mats = np.zeros(len(mesh.polygons), np.int32)
    mesh.polygons.foreach_get("material_index", mats)
    new.polygons.foreach_set("material_index", mats[face_old])
    if mesh.has_custom_normals:
        normals = np.zeros(n * 3, np.float32)
        mesh.vertices.foreach_get("normal", normals)
        new.normals_split_custom_set_from_vertices(normals.reshape(n, 3)[old].tolist())
    if mesh.uv_layers.active is not None and mesh.uv_layers.active.name in new.uv_layers:
        new.uv_layers.active = new.uv_layers[mesh.uv_layers.active.name]
    if mesh.color_attributes.active_color_name in new.color_attributes:
        new.color_attributes.active_color = new.color_attributes[mesh.color_attributes.active_color_name]
    return new


def _sampler(source):
    """Where a point (the source's own space) lies on the source seen from above: its triangle's three vertices, their
    weights, and the face; the nearest point when nothing lies straight below or above."""
    mesh = source.data
    mesh.calc_loop_triangles()
    co = [v.co for v in mesh.vertices]
    tris = [tuple(t.vertices) for t in mesh.loop_triangles]
    faces = [t.polygon_index for t in mesh.loop_triangles]
    tree = BVHTree.FromPolygons(co, tris)
    top = max((v.co.z for v in mesh.vertices), default=0.0) + 1.0

    def sample(p):
        hit = tree.ray_cast(Vector((p.x, p.y, top)), Vector((0.0, 0.0, -1.0)))
        if hit[0] is None:
            hit = tree.find_nearest(p)
        if hit[0] is None:
            return None
        loc, i = hit[0], hit[2]
        a, b, c = (co[k] for k in tris[i])
        # barycentric weights in the ground plane (a landscape is a height field)
        det = (b.y - c.y) * (a.x - c.x) + (c.x - b.x) * (a.y - c.y)
        if abs(det) < 1e-12:
            w = (1.0, 0.0, 0.0)
        else:
            w0 = ((b.y - c.y) * (loc.x - c.x) + (c.x - b.x) * (loc.y - c.y)) / det
            w1 = ((c.y - a.y) * (loc.x - c.x) + (a.x - c.x) * (loc.y - c.y)) / det
            w = (w0, w1, 1.0 - w0 - w1)
        return tris[i], w, faces[i]
    return sample


def restore(target, source):
    """The original landscape's UV maps (refitted from position: they are flat projections), paint layers (sampled from
    the surface under each vertex), materials and mark, onto a remeshed copy. Returns what it gave back."""
    src, dst = source.data, target.data
    to_source = source.matrix_world.inverted() @ target.matrix_world
    points = [to_source @ v.co for v in dst.vertices]
    xy = np.array([(p.x, p.y, 1.0) for p in points])
    src_co = np.zeros(len(src.vertices) * 3, np.float32)
    src.vertices.foreach_get("co", src_co)
    src_xy = np.c_[src_co.reshape(-1, 3)[:, :2], np.ones(len(src.vertices))]
    loops = _loops(dst)
    done = {"uvs": [], "layers": []}
    for layer in src.uv_layers:
        uv = _per_vertex_uv(src, layer.name)
        fit, *_ = np.linalg.lstsq(src_xy, uv, rcond=None)
        if np.abs(src_xy @ fit - uv).max() > 1e-3 * max(1.0, float(np.ptp(uv))):
            continue        # not a flat projection: not a landscape UV
        _set(dst, layer.name, 'FLOAT2', 'CORNER', (xy @ fit)[loops])
        done["uvs"].append(layer.name)
    sample = _sampler(source)
    hits = [sample(p) for p in points]
    for attr in [a for a in src.color_attributes]:
        values = _get(attr)
        if attr.domain == 'CORNER':
            per_vertex = np.zeros((len(src.vertices), values.shape[1]), values.dtype)
            per_vertex[_loops(src)] = values
            values = per_vertex
        out = np.array([sum(w * values[k].astype(np.float64) for k, w in zip(h[0], h[1])) if h else np.zeros(values.shape[1])
                        for h in hits])
        if attr.domain == 'CORNER':
            out = out[loops]
        _set(dst, attr.name, attr.data_type, attr.domain, out.astype(values.dtype) if attr.data_type != 'BYTE_COLOR' else out)
        done["layers"].append(attr.name)
    if not dst.materials:
        for material in src.materials:
            dst.materials.append(material)
        src_mats = np.zeros(len(src.polygons), np.int32)
        src.polygons.foreach_get("material_index", src_mats)
        centres = [to_source @ f.center for f in dst.polygons]
        dst.polygons.foreach_set("material_index", [int(src_mats[h[2]]) if (h := sample(c)) else 0 for c in centres])
    target["mp_landscape"] = 1.0
    dst.update()
    return done
