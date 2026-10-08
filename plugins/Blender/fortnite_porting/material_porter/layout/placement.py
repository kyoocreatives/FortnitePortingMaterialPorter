"""Places the nodes of one frame: Sugiyama layering, crossing reduction, Brandes-Koepf straightening."""

from collections import defaultdict

from .metrics import ALIGN, GAP_BOX_X, GAP_BOX_Y, GAP_NODE_X, GAP_NODE_Y, LABEL, PAD, PULL_UP, ROUTE_LANES
from .routing import _pull_right, _pull_up, _vgap, _wire_routes


# ------------------------------------------------------------------ boxes
class Box:
    def __init__(self, path, parent):
        self.path = path
        self.parent = parent
        self.kids = {}
        self.nodes = []
        self.links = []
        self.x = self.y = 0.0
        self.w = self.h = 0.0
        self.off = {}                             # node name -> y below our top
        self.rel = {}                             # own node name -> (x, y) in content
        self.lanes = []
        self.order = 0

    @property
    def depth(self):
        return len(self.path)

    def pad_top(self):
        if not self.path:
            return 0.0
        return PAD + LABEL.get(self.depth, LABEL[2]) * 1.4

    def pad_side(self):
        return PAD if self.path else 0.0


def _build_boxes(tree, tags):
    root = Box((), None)
    index = {}
    for i, n in enumerate(tree.nodes):
        if n.name not in tags:
            continue
        index[n.name] = i
        b = root
        for d, part in enumerate(tags[n.name]):
            if part not in b.kids:
                b.kids[part] = Box(tags[n.name][:d + 1], b)
                b.kids[part].order = i
            b = b.kids[part]
        b.nodes.append(n)
    for link in tree.links:
        pu, pv = tags.get(link.from_node.name), tags.get(link.to_node.name)
        if pu is None or pv is None:
            continue
        k = 0
        while k < min(len(pu), len(pv)) and pu[k] == pv[k]:
            k += 1
        b = root
        for part in pu[:k]:
            b = b.kids[part]
        b.links.append((link, pu, pv, k))
    return root, index


# ------------------------------------------------------------------ sugiyama
class _It:
    __slots__ = ("key", "kind", "obj", "w", "h", "x", "y", "layer", "pos",
                 "order", "pin", "dot")

    def __init__(self, key, kind, obj, w, h, order, pin=False):
        self.key, self.kind, self.obj = key, kind, obj
        self.w, self.h = w, h
        self.x = self.y = 0.0
        self.layer = 0
        self.pos = 0.0
        self.order = order
        self.pin = pin
        # A reroute's wire goes straight through, so it lines up by socket rather than top edge.
        # Lanes line up by top edge like the nodes they run beside; mixing the two lets a chain creep a
        # socket's height per sweep until it drifts away from its neighbours.
        self.dot = kind == "node" and obj.bl_idname == "NodeReroute"


def _wmedian(cands):
    cands.sort()
    total = sum(w for _, w in cands)
    acc = 0.0
    for v, w in cands:
        acc += w
        if acc >= total * 0.5:
            return v
    return cands[-1][0]


def _settle(col, desired, weight):
    """Closest positions to `desired` that keep `col`'s order and gaps.

    Weighted isotonic regression (pool adjacent violators) on y minus each item's cumulative minimum
    offset from the top of the column.
    """
    n = len(col)
    c = [0.0] * n
    for i in range(1, n):
        c[i] = c[i - 1] + col[i - 1].h + _vgap(col[i - 1], col[i])
    blocks = []
    for i in range(n):
        blocks.append([desired[i] - c[i], weight[i], 1])
        while len(blocks) > 1 and blocks[-2][0] > blocks[-1][0]:
            t2, w2, n2 = blocks.pop()
            t1, w1, n1 = blocks.pop()
            blocks.append([(t1 * w1 + t2 * w2) / (w1 + w2), w1 + w2, n1 + n2])
    i = 0
    for t, _, k in blocks:
        for _ in range(k):
            col[i].y = t + c[i]
            i += 1


def _crossings(segs):
    """Pairs of (a, b) segments that cross: a Fenwick-tree inversion count."""
    if len(segs) < 2:
        return 0
    segs = sorted(segs)
    ranks = {v: i + 1 for i, v in enumerate(sorted({b for _, b in segs}))}
    tree = [0] * (len(ranks) + 1)
    count = 0
    for seen, (_, b) in enumerate(segs):
        r = ranks[b]
        # how many already placed have a larger b
        i, le = r, 0
        while i > 0:
            le += tree[i]
            i -= i & -i
        count += seen - le
        i = r
        while i < len(tree):
            tree[i] += 1
            i += i & -i
    return count


def _brandes_koepf(layers, ins, outs):
    """y for the ordered columns (y down): Brandes and Koepf's placement, by sockets.

    Each item joins a block with its median neighbour in the column before (or after), the wire between them straight
    socket to socket, unless that would cross a block already made or a long wire's lane (lanes stay straight first).
    Blocks then pack as close as the column gaps allow. Four passes (blocks to the left or right, packed up or down)
    are balanced: each item at the mean of its two middle positions, then settled back into its column's order and gaps.

    ins/outs: id(item) -> [(upper, lower, upper port, lower port, weight)]."""
    if not layers:
        return
    # type 1 conflicts: a wire that would cross a lane (dummy to dummy) gives way
    pos = {id(it): i for col in layers for i, it in enumerate(col)}
    conflicts = set()
    for l in range(1, len(layers)):
        prev, col = layers[l - 1], layers[l]
        k0, scan = 0, 0
        for i, v in enumerate(col):
            inner = next((s for s in ins[id(v)] if s[0].kind == "dummy" and v.kind == "dummy"), None)
            if inner is None and i != len(col) - 1:
                continue
            k1 = pos[id(inner[0])] if inner is not None else len(prev) - 1
            for w in col[scan:i + 1]:
                for s in ins[id(w)]:
                    k = pos[id(s[0])]
                    if (k < k0 or k > k1) and not (s[0].kind == "dummy" and w.kind == "dummy"):
                        conflicts.add((id(s[0]), id(w)))
            scan, k0 = i + 1, k1
    items = [it for col in layers for it in col]
    runs = []
    for left in (True, False):          # blocks follow the column before or after
        for down in (True, False):      # columns read top down (packed up) or bottom up
            cols = layers if left else layers[::-1]
            cols = [col if down else col[::-1] for col in cols]
            lpos = {id(it): i for col in cols for i, it in enumerate(col)}
            root = {id(it): it for it in items}
            align = {id(it): it for it in items}
            off = {id(it): 0.0 for it in items}     # item y minus its block root's y
            for col in cols[1:]:
                r = -1
                for v in col:
                    if left:
                        ns = [(s[0], s[2], s[3], (id(s[0]), id(v))) for s in ins[id(v)]]
                    else:
                        ns = [(s[1], s[3], s[2], (id(v), id(s[1]))) for s in outs[id(v)]]
                    if not ns:
                        continue
                    ns.sort(key=lambda t: lpos[id(t[0])])
                    d = len(ns)
                    for m in sorted({(d - 1) // 2, d // 2}):
                        u, pu, pv, key = ns[m]
                        if align[id(v)] is not v:
                            break
                        if key in conflicts or r >= lpos[id(u)] or align[id(u)] is not u and \
                                not (align[id(u)] is root[id(u)]):
                            continue
                        # u's block may only grow at its end: u must be its last item
                        if align[id(u)] is not root[id(u)]:
                            continue
                        align[id(u)] = v
                        root[id(v)] = root[id(u)]
                        align[id(v)] = root[id(v)]
                        off[id(v)] = off[id(u)] + pu - pv
                        r = lpos[id(u)]
            # pack: in real column order a above b needs y(b) >= y(a) + h(a) + gap
            succ = defaultdict(list)
            indeg = defaultdict(int)
            roots = {id(root[id(it)]) for it in items}
            for col in layers:
                for a, b in zip(col, col[1:]):
                    ra, rb = id(root[id(a)]), id(root[id(b)])
                    if ra == rb:
                        continue
                    sep = a.h + _vgap(a, b) + off[id(a)] - off[id(b)]
                    if down:
                        succ[ra].append((rb, sep))
                    else:
                        succ[rb].append((ra, sep))
                    indeg[rb if down else ra] += 1
            ybase = {r: 0.0 for r in roots}
            queue = [r for r in roots if indeg[r] == 0]
            seen = 0
            while queue:
                r = queue.pop()
                seen += 1
                for q, sep in succ[r]:
                    ybase[q] = max(ybase[q], ybase[r] + sep)
                    indeg[q] -= 1
                    if indeg[q] == 0:
                        queue.append(q)
            if seen < len(roots):
                continue        # a cycle: leave this pass out
            sign = 1.0 if down else -1.0
            runs.append({id(it): sign * ybase[id(root[id(it)])] + off[id(it)] for it in items})
    if not runs:
        return
    # line the runs up on the smallest one's top, then put each item at the mean of its middle two
    spans = [(max(r[id(it)] + it.h for it in items) - min(r.values()), i) for i, r in enumerate(runs)]
    tops = [min(r.values()) for r in runs]
    for r, top in zip(runs, tops):
        for k in r:
            r[k] -= top
    for it in items:
        ys = sorted(r[id(it)] for r in runs)
        it.y = (ys[(len(ys) - 1) // 2] + ys[len(ys) // 2]) * 0.5
    # the mean of two runs can overlap neighbours slightly, so each column is settled back
    for col in layers:
        _settle(col, [it.y for it in col], [1.0] * len(col))


def _sugiyama(items, edges):
    """Place `items`, x and y relative to the block's top-left.

    edges: (u, v, u_port, v_port, weight, wire) with ports measured from each
    item's top edge and wire = (from node, output id, to node, input id).

    Returns (w, h, routes): the reroutes that carry wires between frames, each
    (source node, output id, [(x, y) per reroute], [(i, j) links between them], [(reader node, input id, i)]),
    with i = -1 for the source itself.
    """
    if not items:
        return 0.0, 0.0, []
    # --- break cycles (frames can feed each other both ways)
    succ = defaultdict(list)
    for e in edges:
        succ[id(e[0])].append(e[1])
    state, back = {}, set()
    for root in sorted(items, key=lambda it: it.order):
        if id(root) in state:
            continue
        stack = [(root, iter(succ[id(root)]))]
        state[id(root)] = 1
        while stack:
            u, it = stack[-1]
            v = next(it, None)
            if v is None:
                state[id(u)] = 2
                stack.pop()
                continue
            s = state.get(id(v))
            if s == 1:
                back.add((id(u), id(v)))
            elif s is None:
                state[id(v)] = 1
                stack.append((v, iter(succ[id(v)])))
    dag = [e for e in edges if (id(e[0]), id(e[1])) not in back]

    # --- layers, as late as possible: sinks on the right, everything else just before its nearest consumer.
    # Entry and exit ports then get a column of their own on the far left and far right, the frame's edges.
    out = defaultdict(set)
    for e in dag:
        out[id(e[0])].add(e[1])
    rank = {}
    for it in sorted(items, key=lambda it: it.order):
        stack = [it]
        while stack:
            u = stack[-1]
            if id(u) in rank:
                stack.pop()
                continue
            pending = [v for v in out[id(u)] if id(v) not in rank]
            if pending:
                stack.extend(pending)
                continue
            rank[id(u)] = 1 + max((rank[id(v)] for v in out[id(u)]), default=-1)
            stack.pop()
    top = max(rank.values())
    first = 1 if any(it.pin == "first" for it in items) else 0
    for it in items:
        if it.pin == "first":
            it.layer = 0
        elif it.pin == "last":
            it.layer = top + first + 1
        else:
            it.layer = top - rank[id(it)] + first
    layers = [[] for _ in range(max(it.layer for it in items) + 1)]
    for it in items:
        layers[it.layer].append(it)
    layers = [col for col in layers if col]
    for l, col in enumerate(layers):
        for it in col:
            it.layer = l

    # --- long edges become chains of dummies, one chain per source socket, shared by every reader of it
    segs = []                                     # (a, b, pa, pb, w)
    trunks = {}                                   # source socket -> chain
    reads = defaultdict(list)                     # source socket -> [(v, v port, wire)]
    trunk_port = {}                               # source socket -> its port
    for u, v, pu, pv, w, wire in dag:
        if v.layer <= u.layer:
            continue
        if v.layer == u.layer + 1:
            segs.append((u, v, pu, pv, w))
            continue
        key = (wire[0], wire[1])
        chain = trunks.setdefault(key, [u])
        trunk_port[key] = pu
        reads[key].append((v, pv, wire))
        while chain[-1].layer < v.layer - 1:
            prev = chain[-1]
            d = _It(key, "dummy", u, 0.0, 0.0, u.order)
            d.layer = prev.layer + 1
            layers[d.layer].append(d)
            segs.append((prev, d, pu if prev is u else 0.0, 0.0, 3.0))
            chain.append(d)
        last = chain[v.layer - u.layer - 1]
        segs.append((last, v, pu if last is u else 0.0, pv, w))

    ins, outs = defaultdict(list), defaultdict(list)
    for s in segs:
        outs[id(s[0])].append(s)
        ins[id(s[1])].append(s)

    # --- order within layers: barycentre sweeps from creation order. Frames keep the order the builder made
    # them in (Golden above CheatMaster above LootHacker reads better than whatever minimises crossings);
    # only nodes and lanes move around them.
    def reorder(col, key):
        boxes = sorted((it for it in col if it.kind == "box"), key=lambda it: it.order)
        col.sort(key=key)
        slots = [i for i, it in enumerate(col) if it.kind == "box"]
        for i, b in zip(slots, boxes):
            col[i] = b
        for i, it in enumerate(col):
            it.pos = i

    for col in layers:
        reorder(col, lambda it: it.order)

    def frac(it, port):
        return 0.99 * port / it.h if it.h > 0 else 0.0

    def count_all():
        total = 0
        for col in layers:
            total += _crossings([(a.pos + frac(a, pa), b.pos + frac(b, pb))
                                 for a in col for (_, b, pa, pb, _) in outs[id(a)]])
        return total

    best = count_all()
    snapshot = [list(col) for col in layers]
    for sweep in range(16):
        down = sweep % 2 == 0
        rng = range(1, len(layers)) if down else range(len(layers) - 2, -1, -1)
        for l in rng:
            keys = {}
            for it in layers[l]:
                nb = ([s[0].pos + frac(s[0], s[2]) for s in ins[id(it)]] if down else
                      [s[1].pos + frac(s[1], s[3]) for s in outs[id(it)]])
                keys[id(it)] = sum(nb) / len(nb) if nb else it.pos
            reorder(layers[l], lambda it: keys[id(it)])
        c = count_all()
        if c < best:
            best, snapshot = c, [list(col) for col in layers]
    layers = snapshot
    for col in layers:
        for i, it in enumerate(col):
            it.pos = i

    # --- y: stack, then pull every item level with what it connects to.
    # Level means top edges, not sockets: Blender draws a node's outputs above its inputs, so lining sockets up
    # makes every chain climb a step per node and a long one ends up diagonal. Tops keep chains in rows and
    # wires only dip by a socket or two. Reroutes and lanes are the exception (a wire goes straight through
    # them) and line up by socket. Frames chase nothing: they stay stacked from the top of their column,
    # packing like a masonry wall instead of leaving holes where two frames tried to line up with different neighbours.
    for col in layers:
        y = 0.0
        for i, it in enumerate(col):
            if i:
                y += col[i - 1].h + _vgap(col[i - 1], it)
            it.y = y

    def target(it, other, p_it, p_other):
        if it.dot or other.dot or ALIGN == "socket":
            return other.y + p_other - p_it
        if ALIGN == "centre":
            return other.y + other.h * 0.5 - it.h * 0.5
        return other.y

    def pull(col, downward):
        desired, weight = [], []
        for it in col:
            if it.kind == "box":
                desired.append(it.y)
                weight.append(1e6)
                continue
            if downward:
                cands = [(target(it, s[0], s[3], s[2]), s[4]) for s in ins[id(it)]]
            else:
                cands = [(target(it, s[1], s[2], s[3]), s[4]) for s in outs[id(it)]]
            if cands:
                desired.append(_wmedian(cands))
                weight.append(sum(w for _, w in cands))
            else:
                desired.append(it.y)
                weight.append(0.01)
        _settle(col, desired, weight)

    if ALIGN == "bk":
        _brandes_koepf(layers, ins, outs)
    else:
        for _ in range(10):
            for l in range(1, len(layers)):
                pull(layers[l], True)
            for l in range(len(layers) - 2, -1, -1):
                pull(layers[l], False)
        for l in range(1, len(layers)):
            pull(layers[l], True)

    # close any band no column uses: two chains sharing nothing can end up far apart,
    # and the frame would carry the empty stretch between them
    hole = GAP_BOX_Y if any(it.kind == "box" for it in items) else 2 * GAP_NODE_Y
    shift, bottom = 0.0, None
    for it in sorted((it for col in layers for it in col), key=lambda it: it.y):
        if bottom is not None and it.y - shift > bottom + hole:
            shift += it.y - shift - bottom - hole
        it.y -= shift
        bottom = it.y + it.h if bottom is None else max(bottom, it.y + it.h)

    real = [it for col in layers for it in col if it.kind != "dummy"]
    y0 = min(it.y for it in real)
    for col in layers:
        for it in col:
            it.y -= y0
    _straighten(layers, trunks, reads, trunk_port)

    # --- x: columns, wider gaps where more wires cross. Frames sit on the column's left edge so the gap
    # before them stays clear; nodes sit on its right edge, so a column's outputs line up and a small node
    # sharing a column with a wide frame stays next to what it feeds.
    x, col_left, col_right = 0.0, {}, {}
    for l, col in enumerate(layers):
        width = max((it.w for it in col if it.kind != "dummy"), default=0.0)
        col_left[l], col_right[l] = x, x + width
        for it in col:
            if it.kind == "dummy":
                it.x, it.w = x, width
            else:
                it.x = x if it.kind == "box" else x + width - it.w
        if l + 1 < len(layers):
            n_wires = sum(len(outs[id(it)]) for it in col)
            boxes = any(it.kind == "box" for it in col + layers[l + 1])
            gap = (GAP_BOX_X if boxes else GAP_NODE_X) + min(60.0, 2.0 * n_wires)
            x += width + gap
        else:
            x += width

    _pull_right(layers, dag)
    if PULL_UP:
        _pull_up(layers, dag, trunks, reads, trunk_port)
    _snap_sources(layers, dag)

    placed = []
    if ROUTE_LANES:
        placed += _wire_routes(dag, real, trunks)
    return x, max(it.y + it.h for it in real), placed


def _straighten(layers, trunks, reads, trunk_port):
    """Put each long wire's lane at one height where its columns leave room (every step in a lane is a pair
    of reroutes): its source's socket height if possible (no bend leaving it), else a reader's (none arriving),
    else one its stretches already have. A reroute source (a frame's port) moves level with its lane instead,
    where its own column allows."""
    where = {id(it): (l, i) for l, col in enumerate(layers) for i, it in enumerate(col)}

    def room(it, y, h):
        l, i = where[id(it)]
        col = layers[l]
        if i > 0 and col[i - 1].y + col[i - 1].h + _vgap(col[i - 1], it) > y + 0.01:
            return False
        if i + 1 < len(col) and y + h + _vgap(it, col[i + 1]) > col[i + 1].y + 0.01:
            return False
        return True
    for key, chain in trunks.items():
        u, ds = chain[0], chain[1:]
        if not ds:
            continue
        pu = trunk_port[key]
        cands = [u.y + pu] + [v.y + pv for v, pv, _ in reads[key]] + [d.y for d in ds]
        best = next((y for y in cands if all(room(d, y, 0.0) for d in ds)), None)
        if best is None:
            continue
        if abs(best - (u.y + pu)) > 0.5 and u.dot and room(u, best - pu, u.h):
            u.y = best - pu
        for d in ds:
            d.y = best


def _snap_sources(layers, dag):
    """Put an input that feeds one node only (a Group Input copy, a texture coordinate) level with it, wherever
    the columns left it: a short wire across, not one down half the frame from a node that rose into free space above."""
    real = [it for col in layers for it in col if it.kind != "dummy"]
    lanes = [it for col in layers for it in col if it.kind == "dummy"]
    ins, outs = defaultdict(list), defaultdict(list)
    for e in dag:
        outs[id(e[0])].append(e)
        ins[id(e[1])].append(e)
    for it in real:
        if it.kind != "node" or it.pin or ins[id(it)] or not outs[id(it)]:
            continue
        if len({id(e[1]) for e in outs[id(it)]}) != 1:
            continue
        u, v, pu, pv = outs[id(it)][0][:4]
        y = v.y + pv - pu if it.dot or v.dot or ALIGN != "top" else v.y
        if abs(y - it.y) < 1.0:
            continue
        top, bottom = y - GAP_NODE_Y * 0.5, y + it.h + GAP_NODE_Y * 0.5
        left, right = it.x - GAP_NODE_X * 0.5, it.x + it.w + GAP_NODE_X * 0.5
        if not any(o is not it and o.x < right and o.x + o.w > left and o.y <= bottom and o.y + o.h >= top
                   for o in real + lanes):
            it.y = y
