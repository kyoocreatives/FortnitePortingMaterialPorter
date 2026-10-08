"""Reroutes for wires that would pass behind nodes, and the final nudges."""

import bisect
import heapq
from collections import defaultdict

from .metrics import (
    GAP_BOX_X,
    GAP_BOX_Y,
    GAP_DUMMY_Y,
    GAP_LANE_BOX,
    GAP_NODE_X,
    GAP_NODE_Y,
    GAP_PART_X,
    GAP_PART_Y,
    ROUTE_BEND,
    ROUTE_BEND_W,
    ROUTE_GRID,
    ROUTE_LONG,
    ROUTE_M,
    ROUTE_SEP,
    ROUTE_SLANT,
    ROUTE_SNAP,
    ROUTE_SPAN,
)


def _crosses(xa, ya, xb, yb, rects, skip):
    """Whether the wire Blender draws from (xa, ya) to (xb, yb) runs behind one of `rects`."""
    lo, hi = min(ya, yb), max(ya, yb)
    near = [it for it in rects if it.x < xb and it.x + it.w > xa and it.y < hi and it.y + it.h > lo
            and it not in skip]
    if not near:
        return False
    d = 0.4 * abs(xb - xa)
    for k in range(1, 32):
        t = k / 32.0
        s = 1.0 - t
        x = s * s * s * xa + 3 * s * s * t * (xa + d) + 3 * s * t * t * (xb - d) + t * t * t * xb
        y = (s * s * s + 3 * s * s * t) * ya + (3 * s * t * t + t * t * t) * yb
        for it in near:
            if it.x + 4 < x < it.x + it.w - 4 and it.y + 4 < y < it.y + it.h - 4:
                return True
    return False


def _wire_routes(dag, real, trunks):
    """Reroutes for wires that would run behind a node or frame, long wires that would cross the graph on a slant,
    and a bundle read in several columns. Such a wire runs level and bends only where there is room, a dot at each
    end of a bend: few bends, none away from where it goes and back. Routed on the finished positions (the columns'
    lanes are candidate heights, not the route), longest wires first; a lane already laid keeps the next off its height.
    A wire into a multi-input socket stays plain (a Join reads its links in order; relinking would change it).

    Returns [(source node, output id, [(x, y)], [(i, j)], [(reader node, input id, i)])]."""
    nets = defaultdict(list)
    for u, v, pu, pv, _, wire in dag:
        if v.layer > u.layer and not wire[4]:
            nets[(wire[0], wire[1])].append((u, v, pu, pv, wire))
    runs, routes = [], []
    reach = lambda es: max(v.x - es[0][0].x - es[0][0].w for _, v, *_ in es)
    for key, es in sorted(nets.items(), key=lambda kv: -reach(kv[1])):
        u, pu = es[0][0], es[0][2]
        xa, ya = u.x + u.w, u.y + pu
        bus = es[0][4][5] and len({v.layer for _, v, *_ in es}) > 1
        want = []
        for _, v, _, pv, wire in es:
            xb, yb = v.x, v.y + pv
            if xb < xa + GAP_NODE_X * 0.5:
                continue
            # a wire to the next column that clips a node stays plain (its dots would crowd)
            if (bus or v.layer > u.layer + 1 and _crosses(xa, ya, xb, yb, real, (u, v))
                    or xb - xa > ROUTE_LONG and abs(yb - ya) > ROUTE_SLANT):
                want.append((v, xb, yb, wire))
        if not want:
            continue
        lane = [d.y for d in trunks.get(key, [])[1:]]
        r = _route_net(u, xa, ya, want, real, runs, lane)
        if r:
            routes.append((key[0], key[1]) + r)
    return routes


def _route_net(u, xa, ya, want, real, runs, lane):
    """One source's routed wires: the cheapest path over a grid of heights just clear of obstacles and the x where
    something starts or ends. Level stretches are free, each bend costs (more when steep), and a run away from
    the source's height costs a little, so a wire leaves level and bends late and wires to several readers share their start."""
    readers = {id(v) for v, *_ in want}
    x_end = max(xb for _, xb, _, _ in want)
    ys_all = [ya] + [yb for _, _, yb, _ in want]
    lo, hi = min(ys_all) - ROUTE_SPAN, max(ys_all) + ROUTE_SPAN
    rects = []
    for it in real:
        if it is u or it.x >= x_end or it.x + it.w <= xa or it.y > hi or it.y + it.h < lo:
            continue
        m = 0.0 if id(it) in readers else ROUTE_M
        rects.append((it.x - m, it.y - m, it.x + it.w + m, it.y + it.h + m))
    lanes = [r for r in runs if r[0] < x_end and r[1] > xa]

    def grid(fixed, extra):
        # ends exact; the rest thinned where they crowd (rows of nodes share their edges)
        out = sorted(set(fixed))
        for v in sorted(extra):
            j = bisect.bisect_left(out, v)
            if (j == len(out) or out[j] - v > ROUTE_SNAP) and (j == 0 or v - out[j - 1] > ROUTE_SNAP):
                out.insert(j, v)
        return out
    X = grid([xa, *(xb for _, xb, _, _ in want)], [x for r in rects for x in (r[0], r[2]) if xa < x < x_end])
    # a wide empty stretch gives room to bend near either end, not only right across it
    X = grid(X, [x for a, b in zip(X, X[1:]) if b - a > 4 * ROUTE_BEND_W
                 for x in (a + 2 * ROUTE_BEND_W, b - 2 * ROUTE_BEND_W)])
    Y = grid(ys_all, [*lane, *(y for r in rects for y in (r[1] - 1.0, r[3] + 1.0) if lo < y < hi)])
    if len(X) * len(Y) > ROUTE_GRID:
        return None
    xi = {x: i for i, x in enumerate(X)}
    yi = {y: i for i, y in enumerate(Y)}
    across = {}

    def blocks(i, j):
        """What stands between X[i] and X[j]: rects and lanes."""
        if (i, j) not in across:
            x0, x1 = X[i], X[j]
            across[i, j] = ([r for r in rects if r[0] < x1 - 0.5 and r[2] > x0 + 0.5],
                            [r[2] for r in lanes if r[0] < x1 and r[1] > x0])
        return across[i, j]

    def level(i, y):
        rs, ls = blocks(i, i + 1)
        return not any(r[1] < y < r[3] for r in rs) and not any(abs(ly - y) < ROUTE_SEP for ly in ls)

    def span(i, j, y):
        """The heights a bend from y across X[i]..X[j] can reach, up to what is above and down to what is below."""
        rs = blocks(i, j)[0]
        top = max((r[3] for r in rs if r[1] < y), default=-1e18)
        bottom = min((r[1] for r in rs if r[3] > y), default=1e18)
        return top, bottom

    start = (0, yi[ya])
    goals = {(xi[xb], yi[yb]) for _, xb, yb, _ in want}
    goal_xy = [(xb, yb) for _, xb, yb, _ in want]

    def h(i, k):
        # A*: what is left at the least cost: its level run, plus a bend if it isn't level with any reader
        x, y = X[i], Y[k]
        return min((xb - x) * 1e-3 + (0.0 if yb == y else ROUTE_BEND) for xb, yb in goal_xy if xb >= x)             if any(xb >= x for xb, _ in goal_xy) else 1e18
    dist, prev = {start: 0.0}, {}
    heap, done = [(h(*start), 0.0, start)], set()
    while heap and not goals <= done:
        _, c, s = heapq.heappop(heap)
        if s in done:
            continue
        done.add(s)
        i, k = s
        y = Y[k]
        moves = []
        if i + 1 < len(X) and level(i, y):
            moves.append(((i + 1, k), (X[i + 1] - X[i]) * (1e-3 if y == ya else 2e-3)))
        # bends: across the narrowest gap wide enough, and the next two wider ones
        j0 = bisect.bisect_left(X, X[i] + ROUTE_BEND_W, i + 1)
        for j in range(j0, min(j0 + 3, len(X))):
            w = X[j] - X[i]
            top, bottom = span(i, j, y)
            for k2 in range(bisect.bisect_left(Y, top), bisect.bisect_right(Y, bottom)):
                if k2 != k:
                    dy = abs(Y[k2] - y)
                    moves.append(((j, k2), ROUTE_BEND + 0.05 * dy + 40.0 * max(0.0, dy / w - 2.0) + w * 4e-3))
        for s2, cost in moves:
            if c + cost < dist.get(s2, float("inf")):
                dist[s2], prev[s2] = c + cost, s
                heapq.heappush(heap, (c + cost + h(*s2), c + cost, s2))
    paths = []
    for v, xb, yb, wire in want:
        g = (xi[xb], yi[yb])
        if g not in done:
            continue
        p = [g]
        while p[-1] != start:
            p.append(prev[p[-1]])
        paths.append((wire, p[::-1]))
    if not paths:
        return None
    # a dot where a bend starts or ends and where readers part; none at the source or a reader
    bend_end = set()
    for _, p in paths:
        for a, b in zip(p, p[1:]):
            if a[1] != b[1]:
                bend_end.update((a, b))
    inner = {s for _, p in paths for s in p[:-1]}
    pts, index, links, feeds = [], {}, [], []
    for wire, p in paths:
        cur = -1
        for s in p[1:]:
            if s in bend_end and (s in inner or s != p[-1]):
                if s not in index:
                    index[s] = len(pts)
                    pts.append((X[s[0]], Y[s[1]]))
                if (cur, index[s]) not in links:
                    links.append((cur, index[s]))
                cur = index[s]
        feeds.append((wire[2], wire[3], cur))
        run = None
        for a, b in zip(p, p[1:]):
            if a[1] == b[1]:
                run = (run[0] if run else X[a[0]], X[b[0]], Y[a[1]])
            elif run:
                runs.append(run)
                run = None
        if run:
            runs.append(run)
    if not pts:
        return None
    return pts, links, feeds


def _pull_up(layers, dag, trunks=None, reads=None, trunk_port=None):
    """Move rows up into the free space above them.

    A row is the items its level wires join (top edges equal, as Sugiyama lined them up), moved as one so its
    wires stay straight; it rises until something overlapping it horizontally is in the way. (Columns are stacked
    whole, so a short column under a long frame's end would otherwise sit as low as the tallest.)
    A long wire's lane moves as one with what it lines up with at either end."""
    real = [it for col in layers for it in col]
    up = {id(it): id(it) for it in real}

    def find(a):
        while up[a] != a:
            up[a] = up[up[a]]
            a = up[a]
        return a
    for u, v, *_ in dag:
        if id(u) in up and id(v) in up and abs(u.y - v.y) < 0.5:
            up[find(id(u))] = find(id(v))
    for key, chain in (trunks or {}).items():
        for a, b in zip(chain[1:], chain[2:]):
            up[find(id(a))] = find(id(b))
        pu = trunk_port[key] if trunk_port else 0.0
        if abs(chain[0].y - chain[1].y) < 0.5 or abs(chain[0].y + pu - chain[1].y) < 0.5:
            up[find(id(chain[0]))] = find(id(chain[1]))
        for v, pv, _ in reads[key]:
            last = chain[v.layer - chain[0].layer - 1]
            if abs(v.y - last.y) < 0.5 or abs(v.y + pv - last.y) < 0.5:
                up[find(id(v))] = find(id(last))
    rows = defaultdict(list)
    for it in real:
        rows[find(id(it))].append(it)
    # a row packs close under what it is wired to and further from the rest, so separate parts of the
    # shader stay apart instead of setting into one block
    wired = defaultdict(set)
    for u, v, *_ in dag:
        wired[id(u)].add(find(id(v)))
        wired[id(v)].add(find(id(u)))
    placed = []
    for row in sorted(rows.values(), key=lambda r: min(it.y for it in r)):
        shift = None
        mine = find(id(row[0]))
        for it in row:
            floor = 0.0
            for o in placed:
                if o.x < it.x + it.w + 10.0 and it.x < o.x + o.w + 10.0:
                    lane = o.kind == "dummy" or it.kind == "dummy"
                    gap = _vgap(o, it) if lane or mine in wired[id(o)] else max(_vgap(o, it), GAP_PART_Y)
                    floor = max(floor, o.y + o.h + gap)
            room = it.y - floor
            shift = room if shift is None else min(shift, room)
        if shift and shift > 0.5:
            for it in row:
                it.y -= shift
        placed += row


def _pull_right(layers, dag):
    """Move each item right to just before the nearest thing that reads it, as far as nothing in its rows is in the way.

    A column is as wide as its widest item, so a short chain beside a long frame would otherwise wait at the left
    end of a wire as long as the frame. Readers go first so a chain follows its last node; frame ports keep their edge columns."""
    real = [it for col in layers for it in col if it.kind != "dummy"]
    lanes = [it for col in layers for it in col if it.kind == "dummy"]
    readers = defaultdict(list)
    for u, v, *_ in dag:
        if v.layer > u.layer:
            readers[id(u)].append(v)

    def gap(a, b):
        return GAP_BOX_X if a.kind == "box" or b.kind == "box" else GAP_NODE_X

    for it in sorted(real, key=lambda it: -it.layer):
        if it.pin or not readers[id(it)]:
            continue
        want = min(v.x - gap(it, v) for v in readers[id(it)]) - it.w
        if want <= it.x:
            continue
        top, bottom = it.y - GAP_DUMMY_Y, it.y + it.h + GAP_DUMMY_Y
        mine = {id(v) for v in readers[id(it)]}
        for o in real + [d for d in lanes if d.obj is not it]:
            # whatever is further right in the rows it spans stops it short (further off if it doesn't
            # read this: another part), and so does a lane running through them
            if o is not it and o.x >= it.x + it.w and o.y < bottom and o.y + o.h >= top:
                want = min(want, o.x - (gap(it, o) if id(o) in mine else max(gap(it, o), GAP_PART_X)) - it.w)
        if want > it.x:
            it.x = want


def _vgap(a, b):
    if a.kind == "box" and b.kind == "box":
        return GAP_BOX_Y
    if a.kind == "box" or b.kind == "box":
        return GAP_LANE_BOX
    if a.dot or b.dot or a.kind == "dummy" or b.kind == "dummy":
        return GAP_DUMMY_Y
    return GAP_NODE_Y
