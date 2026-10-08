"""A level's decals (Exports[0]["Decals"]): each a quad in the decal's own Y-Z plane, projected
along its X onto the meshes under it, with the decal material.

UE projects a deferred decal's material through a box (DecalSize is the half extents, times the
component's scale) onto whatever the box holds. Eevee has no such projection, so the quad is a grid
bent onto the surfaces: each vertex moves along the decal's X (both ways, within the box depth) onto
the nearest surface that faces the decal's origin side, else the nearest, and is lifted off it along
the normal. A vertex that meets nothing stays on the plane. The surfaces are the scene's meshes when
the decals are placed (after the meshes), hit with one ray per vertex and direction against each
mesh's own BVH (scene.ray_cast walks every object per ray: 15 ms each on an island's 30,000 objects).

UVs: the texture runs along the decal's Z (U, left to right) and Y (V, rows down, image top at -Y),
the box's -1..1 mapped to 0..1. Seen from the origin side looking along X, the image is upright and
unmirrored, its right along +Z. This was checked on the Cristaline island's logo decals (1280x731
logo on boxes with Z 1.65 times Y, roll 90): Y for U and Z for V turns them on their side. In
Blender's frame (the import mirrors UE's Y) the grid's V runs along +Y.
"""
import time

import bpy
import numpy as np
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree

from ..logger import Log

KEY = "mp_decal"                # on a decal object: the decal material's name
KEY_SORT = "mp_decal_sort"      # SortOrder (UE draws a higher one over a lower)
KEY_BOX = "mp_decal_box"        # box half extents, UE cm (X is the projection depth)
KEY_HITS = "mp_decal_hits"      # vertices that found a surface
KEY_FACING = "mp_decal_facing"  # of those, on a surface facing the decal's origin side
KEY_VERTS = "mp_decal_verts"    # total vertices
KEY_FADE = "mp_decal_fade_screen_size"

LIFT = 1.0                      # cm off the surface...
LIFT_SORT = 0.2                 # ...plus 2 mm per SortOrder step (avoids z-fighting between layers)
CELL = 100.0                    # cm: grid cell width (MIN_CELLS..MAX_CELLS across)
MIN_CELLS, MAX_CELLS = 8, 32
MARGIN = 2.0                    # cm: how far outside a decal's box a mesh's bounds still count
MAX_PASSES = 4                  # max back-facing hits a ray skips looking for a face toward the decal


def _log(message):
    Log.info("[Material Porter] " + message)


class _Surfaces:
    """The scene's meshes as ray targets: world bounds to find those under a decal, and a BVH per
    mesh built on first use (in object space, cached because a road is under many decals)."""

    def __init__(self, scene):
        self.depsgraph = bpy.context.evaluated_depsgraph_get()
        self.objects = [o for o in scene.objects if o.type == 'MESH' and not o.hide_viewport and not o.hide_render
                        and o.display_type not in ('BOUNDS', 'WIRE')]
        n = len(self.objects)
        corners = np.zeros((n, 8, 3), np.float64)
        mats = np.zeros((n, 4, 4), np.float64)
        keep = np.ones(n, bool)
        for i, o in enumerate(self.objects):
            try:
                corners[i] = o.bound_box
            except (ValueError, TypeError):     # empty mesh has no bounds
                keep[i] = False
            mats[i] = o.matrix_world
        world = np.einsum("nij,nkj->nki", mats[:, :3, :3], corners) + mats[:, None, :3, 3]
        self.low, self.high = world.min(axis=1), world.max(axis=1)
        self.keep = keep
        self.bvh = {}           # object index -> (tree, matrix, inverse, normal matrix) or None
        self.built = 0

    def under(self, low, high):
        """Indices of the meshes whose world bounds meet the box low..high."""
        hit = self.keep & np.all((self.low <= high) & (self.high >= low), axis=1)
        return np.nonzero(hit)[0]

    def tree(self, i):
        got = self.bvh.get(i, False)
        if got is False:
            got = None
            o = self.objects[i]
            try:
                if len(o.data.polygons):
                    m = o.matrix_world.copy()
                    got = (BVHTree.FromObject(o, self.depsgraph), m, m.inverted_safe(), m.to_3x3().inverted_safe().transposed())
                    self.built += 1
            except Exception:       # no evaluated mesh: skip this surface
                pass
            self.bvh[i] = got
        return got


def _segments(extent_cm):
    return int(min(MAX_CELLS, max(MIN_CELLS, np.ceil(extent_cm / CELL))))


def _slab(origins, direction, depth, low, high):
    """Slab test: which boxes (m x 3 bounds) a segment from each origin (n x 3) along +-direction,
    up to depth, can meet. Returns n x m bool."""
    p = origins[:, None, :]
    lo, hi = low[None, :, :], high[None, :, :]
    d = np.where(np.abs(direction) < 1e-9, 1e-9, direction)[None, None, :]
    t1, t2 = (lo - p) / d, (hi - p) / d
    near, far = np.minimum(t1, t2).max(axis=2), np.maximum(t1, t2).min(axis=2)
    return (near <= far) & (near <= depth) & (far >= -depth)


def _project(surfaces, indices, origins, direction, depth, pad):
    """Per origin, the surface a decal paints there along +-direction within depth: (distance or inf,
    hit point, outward normal, faces the decal's origin side). One ray per origin and direction
    against the meshes it can reach.

    The surface is the nearest one facing the decal's origin side, else the nearest of any. So a
    decal inside a hollow box (a trailer logo, its plane inside the wall) lands on the wall's outer
    face, not the inner face a ray meets first. A ray that meets a back-facing face goes on past it."""
    n = len(origins)
    front = np.full(n, np.inf)
    anyhit = np.full(n, np.inf)
    point = np.zeros((2, n, 3))
    normal = np.zeros((2, n, 3))
    if not len(indices):
        return anyhit, point[1], normal[1], np.zeros(n, bool)
    reach = _slab(origins, direction, depth, surfaces.low[indices] - pad, surfaces.high[indices] + pad)
    d = Vector(direction)
    for j, i in enumerate(indices):
        rows = np.nonzero(reach[:, j])[0]
        if not len(rows):
            continue
        tree = surfaces.tree(i)
        if tree is None:
            continue
        bvh, m, inv, nm = tree
        inv3 = inv.to_3x3()
        for sign in (1.0, -1.0):
            local_ray = inv3 @ (d * sign)
            scale = local_ray.length
            if scale == 0.0:
                continue
            local_ray = local_ray / scale
            limit = depth * scale
            step = 1e-4 * scale         # to get past a face the ray went through
            for r in rows:
                o = Vector(origins[r])
                start = inv @ o
                travel = 0.0
                for _ in range(MAX_PASSES):
                    hit, nor, _, _ = bvh.ray_cast(start + local_ray * travel, local_ray, limit - travel)
                    if hit is None:
                        break
                    world = m @ hit
                    t = (world - o).length
                    if t > depth:
                        break
                    outward = (nm @ nor).normalized()
                    faces = outward.dot(d) < 0.0
                    if t < anyhit[r]:
                        anyhit[r] = t
                        point[1, r] = world
                        normal[1, r] = outward
                    if faces:
                        if t < front[r]:
                            front[r] = t
                            point[0, r] = world
                            normal[0, r] = outward
                        break
                    travel = (hit - start).length + step
                    if travel >= limit:
                        break
    use = np.isfinite(front)
    distance = np.where(use, front, anyhit)
    return distance, np.where(use[:, None], point[0], point[1]), np.where(use[:, None], normal[0], normal[1]), use


def _kind(mat):
    """How the material was made: "exact" (translated from its graph), "fallback" (island material
    without an editor graph: its textures and values) or "FP" (FortnitePorting's own shader)."""
    from . import build
    if mat is None:
        return "none"
    if build.KEY_PATH in mat:
        return "fallback" if "mp_fallback" in mat else "exact"
    return "FP"


def _material(context, obj, entry):
    """Put the decal's material on the object. FP's material import builds the exact one
    (hook.build_exact, or the fallback for a graph-less island material), else FP's own shader.
    One material per hash is shared by all decals."""
    mesh = obj.data
    placeholder = bpy.data.materials.new(entry.get("Name") or "Decal")
    mesh.materials.append(placeholder)
    try:
        context.import_material(obj.material_slots[0], entry, {})
    except Exception:
        bpy.data.materials.remove(placeholder)
        raise
    mat = obj.material_slots[0].material
    if mat is not placeholder and placeholder.users == 0:
        bpy.data.materials.remove(placeholder)
    return mat


def _is_stain(entry):
    """Whether the blend is UE's Stain: DecalBlendMode is a name or EDecalBlendMode's number (Translucent 0, Stain 1)."""
    mode = entry.get("DecalBlendMode")
    if isinstance(mode, str):
        return mode.endswith("Stain")
    return mode == 1 and not isinstance(mode, bool)


def _stain(mat):
    """A Stain decal multiplies what it lies on by its colour: the surface becomes a Transparent BSDF
    tinted by the base colour (fading to white with opacity), which is Eevee's multiply. Only works
    when the surface is one Principled BSDF (built materials' groups hide theirs). True when done."""
    tree = mat.node_tree
    out = next((n for n in tree.nodes if n.bl_idname == 'ShaderNodeOutputMaterial' and n.is_active_output), None)
    if out is None or not out.inputs['Surface'].links:
        return False
    bsdf = out.inputs['Surface'].links[0].from_node
    if bsdf.bl_idname != 'ShaderNodeBsdfPrincipled':
        return False

    def copy(src, dst):
        if src.links:
            tree.links.new(src.links[0].from_socket, dst)
        else:
            dst.default_value = src.default_value
    mix = tree.nodes.new('ShaderNodeMix')
    mix.data_type, mix.label = 'RGBA', "stain"
    mix.inputs['A'].default_value = (1.0, 1.0, 1.0, 1.0)
    copy(bsdf.inputs['Alpha'], mix.inputs['Factor'])
    copy(bsdf.inputs['Base Color'], mix.inputs['B'])
    tint = tree.nodes.new('ShaderNodeBsdfTransparent')
    tint.label = "multiply"
    tree.links.new(mix.outputs['Result'], tint.inputs['Color'])
    tree.links.new(tint.outputs[0], out.inputs['Surface'])
    mix.location, tint.location = (bsdf.location[0], bsdf.location[1] - 600), (bsdf.location[0] + 200, bsdf.location[1] - 600)
    mat.surface_render_method = 'BLENDED'
    return True


def _finish_material(mat, entry, done):
    """Once per material: draw it from both sides (the quad lands on surfaces facing any way) and
    apply a Stain decal's multiply. Blending otherwise comes from the import."""
    if mat is None or mat.name in done:
        return
    done.add(mat.name)
    mat.use_backface_culling = False
    if _is_stain(entry) and not mat.get("mp_decal_stain"):
        mat["mp_decal_stain"] = _stain(mat)


def _one(context, entry, surfaces, done, stats):
    from ..processing.utils import make_euler, make_vector
    material = entry.get("Material")
    if not material:
        stats["no material"] += 1
        return None
    scale = float(context.scale)
    size = entry.get("DecalSize") or {"X": 128.0, "Y": 256.0, "Z": 256.0}
    sc3 = entry.get("Scale") or {"X": 1.0, "Y": 1.0, "Z": 1.0}
    # box half extents (UE cm), signed like the scale (a mirrored decal mirrors its texture)
    half = [float(size[k]) * float(sc3[k]) for k in "XYZ"]
    sort_order = int(entry.get("SortOrder") or 0)
    location = make_vector(entry["Location"], unreal_coords_correction=True) * scale
    rotation = make_euler(entry["Rotation"]).to_matrix()
    world = Matrix.Translation(location) @ rotation.to_4x4()

    # grid on the decal's plane; UE's local Y is Blender's local -Y
    nu, nv = _segments(2 * abs(half[1])), _segments(2 * abs(half[2]))
    ty = np.linspace(-1.0, 1.0, nu + 1)
    tz = np.linspace(-1.0, 1.0, nv + 1)
    gy, gz = np.meshgrid(ty, tz, indexing="ij")             # [iu, iv]
    local = np.zeros((nu + 1, nv + 1, 3))
    local[..., 1] = -gy * half[1] * scale
    local[..., 2] = gz * half[2] * scale
    local = local.reshape(-1, 3)
    w3 = np.array(world.to_3x3())
    origins = local @ w3.T + np.array(location)
    axis = np.array(rotation @ Vector((1.0, 0.0, 0.0)))     # projection direction (UE's local X)
    depth = abs(half[0]) * scale

    # meshes the box can reach, by world bounds
    corners = np.array([[sx, sy, sz] for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)], float)
    extent = np.array([depth, abs(half[1]) * scale, abs(half[2]) * scale])
    box = (corners * extent) @ w3.T + np.array(location)
    pad = MARGIN * scale
    indices = surfaces.under(box.min(axis=0) - pad, box.max(axis=0) + pad)

    best, point, normal, facing = _project(surfaces, indices, origins, axis, depth, pad)
    hit = np.isfinite(best)
    lift = (LIFT + LIFT_SORT * sort_order) * scale
    placed = np.where(hit[:, None], point + normal * lift, origins)
    # back to object space (the object sits at the decal's centre)
    inverse = np.array(world.inverted_safe())
    verts = placed @ inverse[:3, :3].T + inverse[:3, 3]

    name = "Decal %s" % material.get("Name")
    mesh = bpy.data.meshes.new(name)
    faces = [(iu * (nv + 1) + iv, (iu + 1) * (nv + 1) + iv, (iu + 1) * (nv + 1) + iv + 1, iu * (nv + 1) + iv + 1)
             for iu in range(nu) for iv in range(nv)]
    mesh.from_pydata(verts.tolist(), [], faces)
    # the winding makes the normal UE's -X, toward the decal's origin side
    uv = mesh.uv_layers.new(name="UV0")
    u = (gz.reshape(-1) + 1.0) * 0.5
    v = (1.0 - gy.reshape(-1)) * 0.5
    loops = np.empty(len(mesh.loops) * 2, np.float32)
    index = np.empty(len(mesh.loops), np.int32)
    mesh.loops.foreach_get("vertex_index", index)
    loops[0::2], loops[1::2] = u[index], v[index]
    uv.data.foreach_set("uv", loops)
    mesh.update()

    obj = bpy.data.objects.new(name, mesh)
    obj.matrix_world = world
    context.collection.objects.link(obj)
    obj.visible_shadow = False
    obj[KEY] = material.get("Name") or ""
    obj[KEY_SORT] = sort_order
    obj[KEY_BOX] = [abs(h) for h in half]
    obj[KEY_HITS] = int(hit.sum())
    obj[KEY_FACING] = int(facing.sum())
    obj[KEY_VERTS] = len(hit)
    if entry.get("FadeScreenSize") is not None:
        obj[KEY_FADE] = float(entry["FadeScreenSize"])

    try:
        mat = _material(context, obj, material)
        _finish_material(mat, material, done)
        from .effects import bake_tangents
        bake_tangents(mesh)
    except Exception:
        # leave no half-made decal in the scene
        bpy.data.objects.remove(obj)
        bpy.data.meshes.remove(mesh)
        raise
    stats["decals"] += 1
    stats["verts"] += len(hit)
    stats["hits"] += int(hit.sum())
    stats["facing"] += int(facing.sum())
    stats[_kind(mat)] += 1
    return obj


def import_decals(context, decals):
    """Place `decals` (the payload's list, may be None) into the import's collection."""
    if not decals:
        return
    start = time.time()
    stats = {"decals": 0, "verts": 0, "hits": 0, "facing": 0, "exact": 0, "fallback": 0, "FP": 0, "none": 0, "no material": 0, "failed": 0}
    surfaces = _Surfaces(bpy.context.scene)
    prepared = time.time()
    done = set()
    for entry in decals:
        try:
            _one(context, entry, surfaces, done, stats)
        except Exception as e:
            stats["failed"] += 1
            import traceback
            _log("decal %s: not placed (%s: %s)" % (entry.get("Name"), type(e).__name__, e))
            traceback.print_exc()
    context.mp_decals = stats
    _log("decals: %d placed (%d exact, %d fallback, %d FP's own; %d without a material, %d failed), %d of %d vertices on a surface (%d facing the decal); "
         "%d meshes' bounds, %d trees built; %.1f s (%.1f s of it getting the meshes ready)" % (
             stats["decals"], stats["exact"], stats["fallback"], stats["FP"], stats["no material"], stats["failed"],
             stats["hits"], stats["verts"], stats["facing"], len(surfaces.objects), surfaces.built, time.time() - start, prepared - start))
