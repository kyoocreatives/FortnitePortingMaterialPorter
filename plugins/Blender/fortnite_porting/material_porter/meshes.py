"""Mesh data work shared by the imports (Material Porter's and the FortnitePorting fork's)."""
import bpy


def white_colors(me):
    """A mesh with no vertex colours reads white in UE (its vertex factory's
    default), black through Blender's Color Attribute node: give it a white
    COL0, so materials that blend by vertex colour pick the layers UE does."""
    if me.color_attributes.get("COL0") is not None:
        return
    a = me.color_attributes.new("COL0", 'BYTE_COLOR', 'CORNER')
    a.data.foreach_set("color", [1.0] * (4 * len(me.loops)))


def spline_bend(me, sp, scale=0.01):
    """A copy of a mesh bent as UE's spline mesh component bends it
    (USplineMeshComponent::CalcSliceTransform): each vertex's place along
    the forward axis is the Hermite segment's parameter; the other two
    coordinates go along the frame there (up x direction, rolled, scaled,
    offset). Worked in UE's component space (cm, Y mirrored back): `scale`
    is the import's metres per cm (0.01)."""
    import numpy as np
    out = me.copy()
    n = len(out.vertices)
    if n == 0:
        return out
    co = np.empty(n * 3, dtype=np.float32)
    out.vertices.foreach_get("co", co)
    flip = np.array([1.0, -1.0, 1.0]) / scale
    ue = co.reshape(n, 3).astype(np.float64) * flip
    ax = int(sp.get("axis", 0))
    lo, hi = float(sp.get("min", 0.0)), float(sp.get("max", 0.0))
    if lo == hi:
        lo, hi = float(ue[:, ax].min()), float(ue[:, ax].max())
    t = (ue[:, ax] - lo) / max(hi - lo, 1e-6)
    p0, t0, p1, t1 = (np.array(sp[k], dtype=np.float64) for k in ("p0", "t0", "p1", "t1"))
    tt, ttt = t * t, t * t * t
    pos = ((2 * ttt - 3 * tt + 1)[:, None] * p0 + (ttt - 2 * tt + t)[:, None] * t0
           + (-2 * ttt + 3 * tt)[:, None] * p1 + (ttt - tt)[:, None] * t1)
    c3 = 6 * p0 + 3 * t0 + 3 * t1 - 6 * p1
    d3 = -6 * p0 - 4 * t0 - 2 * t1 + 6 * p1
    fwd = tt[:, None] * c3 + t[:, None] * d3 + t0

    def unit(v):
        return v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-9)
    fwd = unit(fwd)
    up = np.array(sp.get("up", (0, 0, 1)), dtype=np.float64)
    bx = unit(np.cross(np.broadcast_to(up, fwd.shape), fwd))
    by = unit(np.cross(fwd, bx))
    h = t * t * (3 - 2 * t) if sp.get("smooth") else t
    o0, o1 = np.array(sp.get("o0", (0, 0)), dtype=np.float64), np.array(sp.get("o1", (0, 0)), dtype=np.float64)
    off = o0 + (o1 - o0) * h[:, None]
    pos = pos + off[:, :1] * bx + off[:, 1:] * by
    roll = float(sp.get("r0", 0.0)) + (float(sp.get("r1", 0.0)) - float(sp.get("r0", 0.0))) * h
    ca, sa = np.cos(roll)[:, None], np.sin(roll)[:, None]
    xv = ca * bx - sa * by
    yv = ca * by + sa * bx
    s0, s1 = np.array(sp.get("s0", (1, 1)), dtype=np.float64), np.array(sp.get("s1", (1, 1)), dtype=np.float64)
    sc = s0 + (s1 - s0) * h[:, None]
    # the slice's axes, as the forward axis picks them
    if ax == 0:
        a, b, ua, ub = ue[:, 1] * sc[:, 0], ue[:, 2] * sc[:, 1], xv, yv
    elif ax == 1:
        a, b, ua, ub = ue[:, 0] * sc[:, 1], ue[:, 2] * sc[:, 0], yv, xv
    else:
        a, b, ua, ub = ue[:, 0] * sc[:, 0], ue[:, 1] * sc[:, 1], xv, yv
    new = pos + a[:, None] * ua + b[:, None] * ub
    out.vertices.foreach_set("co", (new / flip).astype(np.float32).ravel())
    # custom normals turn with the frame (the scale's inverse on the side axes)
    try:
        nl = len(out.loops)
        cn = np.empty(nl * 3, dtype=np.float32)
        me.corner_normals.foreach_get("vector", cn)
        cn = cn.reshape(nl, 3).astype(np.float64) * np.array([1.0, -1.0, 1.0])
        vi = np.empty(nl, dtype=np.int32)
        out.loops.foreach_get("vertex_index", vi)
        sx, sy = np.maximum(np.abs(sc[vi]), 1e-6).T
        if ax == 0:
            nn = cn[:, :1] * fwd[vi] + (cn[:, 1] / sx)[:, None] * xv[vi] + (cn[:, 2] / sy)[:, None] * yv[vi]
        elif ax == 1:
            nn = cn[:, 1:2] * fwd[vi] + (cn[:, 0] / sy)[:, None] * yv[vi] + (cn[:, 2] / sx)[:, None] * xv[vi]
        else:
            nn = cn[:, 2:3] * fwd[vi] + (cn[:, 0] / sx)[:, None] * xv[vi] + (cn[:, 1] / sy)[:, None] * yv[vi]
        nn = unit(nn) * np.array([1.0, -1.0, 1.0])
        out.normals_split_custom_set([tuple(v) for v in nn])
    except Exception:
        pass        # smooth shading from the bent faces
    out.update()
    return out
