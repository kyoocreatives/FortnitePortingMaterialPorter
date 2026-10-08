"""The depth under water surfaces, for water materials (MSM_SingleLayerWater).

At run time the Water plugin draws each water body into its zone's water
info texture: the water's height and the ground's under it, which the water
materials read as the water's depth (how much of the ground's colour the
water absorbs, how shallow water shines). Nothing of it is cooked; here it
is measured on the scene's own meshes, as the ground the texture is drawn
from: rays from each vertex of a surface down to the other meshes, stored
on the surface mesh as the "mp_water" attribute (x: metres down to the
ground, y: 1) the materials read (env.MaterialEnv.water_depth). The mesh is
cut finer first, since the depth is read between its vertices (a lake's
mesh is a fan of long triangles from its shore)."""
import math
import time

import bpy
import bmesh
from mathutils import Vector
from mathutils.bvhtree import BVHTree

from .build import KEY_WATER
from .env import WATER_ATTRIBUTE

MAX_FACES = 40000       # per surface: the finest cut is 1 m, coarser on big water
FINEST = 1.0            # metres
NO_GROUND = 100.0       # metres: nothing under the surface (open sea past the land); deep water
LEVEL = 0.5             # a normal's z from which water counts as level
REACH = 200.0           # metres: how far rays look for the ground (or a bank over the surface's edge)


def is_water(obj):
    return obj.type == 'MESH' and any(s.material is not None and s.material.get(KEY_WATER)
                                      for s in obj.material_slots)


def _world_bounds(obj):
    pts = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    return Vector(tuple(min(p[i] for p in pts) for i in range(3))), Vector(tuple(max(p[i] for p in pts) for i in range(3)))


def _refine(obj):
    """The surface cut into triangles no longer than its edge size (world
    metres): long edges halved until none is left. Its own copy of the mesh
    when others share it (each place has its own ground)."""
    me = obj.data
    if me.users > 1:
        me = obj.data = me.copy()
    m3 = obj.matrix_world.to_3x3()
    bm = bmesh.new()
    bm.from_mesh(me)
    # UE's water info meshes are loose triangles: one surface first
    bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=1e-4)
    bmesh.ops.triangulate(bm, faces=bm.faces[:])
    area = sum((m3 @ (f.verts[1].co - f.verts[0].co)).cross(m3 @ (f.verts[2].co - f.verts[0].co)).length * 0.5
               for f in bm.faces)
    # halving leaves edges of size / 2..size: about 4 area / size^2 triangles
    size = max(FINEST, math.sqrt(4.0 * area / MAX_FACES))
    for _ in range(24):
        long = [e for e in bm.edges if (m3 @ (e.verts[1].co - e.verts[0].co)).length > size]
        if not long or len(bm.faces) > 2 * MAX_FACES:
            break
        bmesh.ops.subdivide_edges(bm, edges=long, cuts=1, use_grid_fill=False)
        bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 3])
    bm.to_mesh(me)
    bm.free()
    # the surface's own custom normals don't survive the cut: its faces' (water faces up)
    if me.attributes.get("custom_normal") is not None:
        me.attributes.remove(me.attributes["custom_normal"])
    me.shade_smooth()
    return me, size


class _Ground:
    """A mesh the surfaces may lie over, its ray tree made when first cast at."""

    def __init__(self, obj, depsgraph):
        self.obj, self.dg = obj, depsgraph
        self.lo, self.hi = _world_bounds(obj)
        self._tree = None
        self.inv = obj.matrix_world.inverted()
        self.inv3 = self.inv.to_3x3()

    def tree(self):
        if self._tree is None:
            self._tree = BVHTree.FromObject(self.obj, self.dg)
        return self._tree

    def cast(self, p, d):
        """How far a ray from world point p along world direction d meets
        this mesh (world metres), or None."""
        hit = self.tree().ray_cast(self.inv @ p, (self.inv3 @ d).normalized())
        if hit[0] is None:
            return None
        return ((self.obj.matrix_world @ hit[0]) - p).length


def _level_share(me, mw):
    """How much of a surface's area faces within 60 degrees of up (or down)."""
    import numpy as np
    k = len(me.polygons)
    if not k:
        return 1.0
    nf = np.empty(k * 3, dtype=np.float32)
    me.polygons.foreach_get("normal", nf)
    area = np.empty(k, dtype=np.float32)
    me.polygons.foreach_get("area", area)
    nf = nf.reshape(k, 3).astype(np.float64) @ np.linalg.inv(mw[:3, :3])
    nz = np.abs(nf[:, 2]) / np.maximum(np.linalg.norm(nf, axis=1), 1e-9)
    total = float(area.sum())
    return float(area[nz >= LEVEL].sum()) / total if total > 0 else 1.0


def _crosses(o, d, lo, hi, reach):
    """Which rays (origins o, directions d; n x 3) pass through the box
    lo..hi within `reach` - the slab test, for all rays at once."""
    import numpy as np
    with np.errstate(divide="ignore", invalid="ignore"):
        inv = 1.0 / np.where(np.abs(d) < 1e-12, 1e-12, d)
        t1, t2 = (np.asarray(lo) - o) * inv, (np.asarray(hi) - o) * inv
    near = np.minimum(t1, t2).max(axis=1)
    far = np.maximum(t1, t2).min(axis=1)
    return (far >= np.maximum(near, 0.0)) & (near <= reach)


def bake_water(objects):
    """Bake the depth under each water surface among `objects` (the others
    are its possible ground). The depth is measured along the surface's
    normal: straight down under level water, into the rock behind a
    waterfall. Returns a note."""
    t0 = time.perf_counter()
    # a zero scale (flat to nothing: nothing to hit, no inverse) is left out
    objects = [o for o in objects if o.type == 'MESH' and abs(o.matrix_world.determinant()) > 1e-12]
    water = [o for o in objects if is_water(o)]
    if not water:
        return None
    dg = bpy.context.evaluated_depsgraph_get()
    ground = [_Ground(o, dg) for o in objects if o not in water and not o.hide_render]
    import numpy as np
    verts = 0
    for obj in water:
        me, size = _refine(obj)
        n = len(me.vertices)
        co = np.empty(n * 3, dtype=np.float32)
        me.vertices.foreach_get("co", co)
        nl = np.empty(n * 3, dtype=np.float32)
        me.vertices.foreach_get("normal", nl)
        mw = np.array(obj.matrix_world, dtype=np.float64)
        pts = co.reshape(n, 3).astype(np.float64) @ mw[:3, :3].T + mw[:3, 3]
        # normals to world space (the inverse transpose), level where one is missing
        nrm = nl.reshape(n, 3).astype(np.float64) @ np.linalg.inv(mw[:3, :3])
        length = np.linalg.norm(nrm, axis=1, keepdims=True)
        nrm = np.where(length > 1e-9, nrm / np.maximum(length, 1e-9), (0.0, 0.0, 1.0))
        # level water (within 60 degrees of up) is measured straight down, as UE's
        # water info texture measures it; only steep water (a waterfall) by its normal.
        # A surface that is level nearly all over is level all over (a river mesh's
        # overlapping triangles leave a few vertices with no sensible normal)
        nrm = np.where(np.abs(nrm[:, 2:3]) >= LEVEL, (0.0, 0.0, 1.0), nrm)
        if _level_share(me, mw) > 0.9:
            nrm[:] = (0.0, 0.0, 1.0)
        behind = np.full(n, np.inf)      # to the ground behind the surface (under it)
        front = np.full(n, np.inf)       # to ground over it, where none is behind
        level = bool((nrm[:, 2] == 1.0).all())
        # rays straight up and down only meet what spans the surface's own x, y
        pad = np.array((0.0, 0.0, REACH) if level else (REACH, REACH, REACH))
        lo, hi = pts.min(axis=0) - pad, pts.max(axis=0) + pad
        near = [g for g in ground if all(g.lo[k] <= hi[k] and g.hi[k] >= lo[k] for k in range(3))]
        for up in (False, True):
            # behind first; in front only where nothing is behind (the surface's edge under a bank)
            dirs = nrm if up else -nrm
            # from just outside the surface: ground at the waterline is behind it
            starts = pts - dirs * 0.01
            todo = np.isinf(behind) if up else np.ones(n, dtype=bool)
            for g in near:
                if level:
                    cand = np.nonzero(todo & (pts[:, 0] >= g.lo.x) & (pts[:, 0] <= g.hi.x) &
                                      (pts[:, 1] >= g.lo.y) & (pts[:, 1] <= g.hi.y))[0]
                else:
                    cand = np.nonzero(todo & _crosses(starts, dirs, g.lo, g.hi, REACH))[0]
                best = front if up else behind
                for i in cand:
                    dist = g.cast(Vector(starts[i]), Vector(dirs[i]))
                    if dist is not None and dist - 0.01 < best[i]:
                        best[i] = dist - 0.01
        depth = np.where(np.isfinite(behind), behind, np.where(front <= REACH, -front, NO_GROUND))
        depth = np.minimum(depth, NO_GROUND)
        attr = me.attributes.get(WATER_ATTRIBUTE)
        if attr is not None and (attr.data_type != 'FLOAT2' or attr.domain != 'POINT'):
            me.attributes.remove(attr)
            attr = None
        if attr is None:
            attr = me.attributes.new(WATER_ATTRIBUTE, 'FLOAT2', 'POINT')
        attr.data.foreach_set("vector", np.stack([depth, np.ones(n)], axis=1).astype(np.float32).ravel())
        me.update()
        verts += n
    note = "water depth measured under %d surface(s), %d points (%.1f s)" % (len(water), verts,
                                                                          time.perf_counter() - t0)
    ee = getattr(bpy.context.scene, "eevee", None)
    if ee is not None and not getattr(ee, "use_raytracing", True):
        note += "; the ground shows through the water with Render > Raytracing on (off, it refracts the sky)"
    return note
