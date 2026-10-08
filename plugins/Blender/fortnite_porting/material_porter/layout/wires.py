"""The graph as wires: section tags, dead nodes parked, pure sources copied into each frame that reads them."""

from collections import Counter, defaultdict

from ..nodelib import SECTION_KEY

from .metrics import PURE_SOURCES, STAND_IN, UNUSED


# ------------------------------------------------------------------ links
# Blender answers `socket.links` by walking every link in the tree, and any read after a link edit rebuilds
# its topology cache first; on the Variants group a few thousand reads take a minute. So the links are read
# once into plain records, the passes below work on those, and each pass writes its edits back in one go.
#
# Sockets are kept by identifier, not by handle: a reroute rebuilds its sockets when the first link gives it
# a type, and a handle taken before that points at freed memory.
class Wire:
    __slots__ = ("link", "a", "ia", "type", "b", "ib")

    def __init__(self, link, a, ia, type_, b, ib):
        self.link, self.a, self.ia, self.type, self.b, self.ib = link, a, ia, type_, b, ib


def output_socket(node, ident):
    return next(s for s in node.outputs if s.identifier == ident)


def input_socket(node, ident):
    return next(s for s in node.inputs if s.identifier == ident)


def scan_wires(tree):
    wires = []
    for l in tree.links:
        sa = l.from_socket
        wires.append(Wire(l, l.from_node, sa.identifier, sa.type,
                           l.to_node, l.to_socket.identifier))
    return wires


def adjacency(wires):
    ins, outs = defaultdict(list), defaultdict(list)
    for w in wires:
        outs[w.a.name].append(w)
        ins[w.b.name].append(w)
    return ins, outs


# ------------------------------------------------------------------ tags
def read_tags(tree):
    tags = {}
    for n in tree.nodes:
        if n.bl_idname == "NodeFrame":
            continue
        raw = n.get(SECTION_KEY)
        tags[n.name] = None if raw is None else tuple(p for p in raw.split("/") if p)
    return tags


def is_source(n, ins):
    if n.bl_idname in PURE_SOURCES:
        return True
    if n.bl_idname == "NodeSeparateBundle":
        feeds = ins.get(n.name, [])
        return bool(feeds) and all(w.a.type == 'GROUP_INPUT' for w in feeds)
    return n.bl_idname == "ShaderNodeValue" and _driver(n) is not None


def resolve_untagged(tree, tags, wires):
    """Nodes an insertion pass added after the builder finished.

    They go with the node they read from, which is where every pass inserts: the mirror after the
    object-space read, the grade after the variant colour, the cell variation after the LootHacker base.
    """
    ins, outs = adjacency(wires)
    pending = [n for n in tree.nodes if n.name in tags and tags[n.name] is None]
    while pending:
        rest, moved = [], False
        for n in pending:
            votes = Counter()
            for w in ins[n.name]:
                t = tags.get(w.a.name)
                if t is not None and not is_source(w.a, ins):
                    votes[t] += 2
            for w in outs[n.name]:
                t = tags.get(w.b.name)
                if t is not None:
                    votes[t] += 1
            if votes:
                tags[n.name] = votes.most_common(1)[0][0]
                moved = True
            else:
                rest.append(n)
        if not moved:
            for n in rest:
                tags[n.name] = ()
            break
        pending = rest


def park_dead(tree, tags, wires):
    """Tag whatever no longer reaches the output into the Unused frame."""
    ins, _ = adjacency(wires)
    live = set()
    stack = [n.name for n in tree.nodes if n.type in ('GROUP_OUTPUT', 'OUTPUT_MATERIAL')]
    while stack:
        name = stack.pop()
        if name in live:
            continue
        live.add(name)
        stack.extend(w.a.name for w in ins[name])
    parked = 0
    for n in tree.nodes:
        if n.name not in tags or n.name in live or n.type == 'GROUP_INPUT':
            continue
        top = tags[n.name][0] if tags[n.name] else "Misc"
        tags[n.name] = (UNUSED, top)
        parked += 1
    return parked


# ------------------------------------------------------------------ sources
def _driver(n):
    ad = n.id_data.animation_data
    if ad is None:
        return None
    path = 'nodes["%s"].outputs[0].default_value' % n.name
    return next((fc for fc in ad.drivers if fc.data_path == path), None)


def clone_node(tree, src):
    c = tree.nodes.new(src.bl_idname)
    c.label = src.label
    c.width = src.width
    for prop in PURE_SOURCES.get(src.bl_idname, ()):
        setattr(c, prop, getattr(src, prop))
    if src.bl_idname == "NodeSeparateBundle":
        c.bundle_items.clear()
        for it in src.bundle_items:
            c.bundle_items.new(it.socket_type, it.name)
        c.define_signature = src.define_signature
    if src.bl_idname == "ShaderNodeValue":
        c.outputs[0].default_value = src.outputs[0].default_value
        old = _driver(src)
        fc = c.outputs[0].driver_add("default_value")
        fc.driver.type = old.driver.type
        fc.driver.expression = old.driver.expression
        for v in old.driver.variables:
            nv = fc.driver.variables.new()
            nv.name, nv.type = v.name, v.type
            for i, t in enumerate(v.targets):
                nt = nv.targets[i]
                if v.type == 'SINGLE_PROP':
                    nt.id_type = t.id_type
                nt.id = t.id
                nt.data_path = t.data_path
    return c


def _key(n):
    props = tuple(str(getattr(n, p)) for p in PURE_SOURCES.get(n.bl_idname, ()))
    if n.bl_idname == "ShaderNodeValue":
        props = (_driver(n).driver.expression,)
    if n.bl_idname == "NodeSeparateBundle":
        props = tuple((it.name, it.socket_type) for it in n.bundle_items)
    return (n.bl_idname, props)


def relink(tree, a, ia, b, ib):
    """Point input `ib` of `b` at output `ia` of `a`.

    A new link into a single input replaces the existing one, saving a separate remove
    (every edit costs Blender a tree update).
    """
    sb = input_socket(b, ib)
    if sb.is_multi_input:
        for l in [l for l in tree.links if l.to_socket == sb]:
            tree.links.remove(l)
    return tree.links.new(output_socket(a, ia), sb)


def source_output(node, ident, stand_ins):
    """Output `ident` of a node, or of the Group Input a stand-in stands for."""
    idents = stand_ins.get(node.name)
    if idents is None:
        return output_socket(node, ident)
    outs = [s for s in node.outputs if s.identifier != "__extend__"]
    if ident in idents:
        return outs[idents.index(ident)]
    # Wires scanned after localizing name the stand-in's own outputs (Item_N), not the Group Input's (Socket_N).
    # A lane routed out of a stand-in into a nested frame arrives here with one of those; the two never collide.
    return output_socket(node, ident)


def _stand_in(tree, gi, idents):
    ph = tree.nodes.new("NodeSeparateBundle")
    ph.bundle_items.clear()
    kept = []
    for s in gi.outputs:
        if s.identifier in idents:
            kind = {'VALUE': 'FLOAT', 'CUSTOM': 'FLOAT'}.get(s.type, s.type)
            ph.bundle_items.new(kind, s.name or s.identifier)
            kept.append(s.identifier)
    ph[STAND_IN] = 1
    ph.width = gi.width
    return ph, kept


def localize_sources(tree, tags, wires):
    """Copy every pure input node into each frame that reads it, and a Group Input to each node that reads it,
    showing only what that node reads: a parameter sits beside its reader (one Group Input feeding a whole frame
    sent dozens of wires across it). A parameter bundle's Separate Bundle goes to each column of its readers with
    a Group Input of its own (one per frame sent its items across the frame).

    Returns (copies made, {stand-in name: Group Input output ids}).
    """
    ins, outs = adjacency(wires)
    order = sorted((n for n in tree.nodes if n.name in tags and is_source(n, ins)),
                   key=lambda n: 0 if n.bl_idname == "NodeSeparateBundle" else 1)
    sources = {n.name for n in order}
    # what each frame (or reader) reads from each source, so a stand-in can have exactly those outputs;
    # key (source, path, reader or None)
    stays, moves = set(), defaultdict(list)
    rank = node_ranks(tree, outs)
    for src in order:
        own = tags[src.name]
        split = src.bl_idname == "NodeSeparateBundle"
        for w in outs[src.name]:
            if src.type == 'GROUP_INPUT' and w.b.name not in sources:
                moves[(src.name, tags[w.b.name], w.b.name)].append(w)
            elif split and input_socket(w.b, w.ib).is_multi_input:
                stays.add(src.name)     # a Join reads its links in order; relinking would change it
            elif split:
                moves[(src.name, tags[w.b.name], ("column", rank.get(w.b.name, 0), ins[src.name][0].ia))].append(w)
            elif tags[w.b.name] == own:
                stays.add(src.name)
            else:
                moves[(src.name, tags[w.b.name], None)].append(w)
    # the reader taking most of a Group Input's wires keeps the original
    best = {}
    for k, group in moves.items():
        if k[2] is not None and len(group) > len(moves.get(best.get(k[0]), ())):
            best[k[0]] = k
    for name, k in best.items():
        if name not in stays:
            stays.add(name)
            tags[name] = k[1]
            del moves[k]
    needs = defaultdict(set)
    for (name, path, reader), group in moves.items():
        src = tree.nodes[name]
        if src.type == 'GROUP_INPUT':
            needs[(_key(src), path, reader)].update(w.ia for w in group)
        elif src.bl_idname == "NodeSeparateBundle":
            feed = ins[name][0]
            needs[(_key(feed.a), path, ("split", reader))].add(feed.ia)

    local, stand_ins = {}, {}

    def copy_in(src, path, reader=None):
        k = (_key(src), path, reader)
        if k not in local:
            if src.type == 'GROUP_INPUT':
                c, stand_ins_ids = _stand_in(tree, src, needs[k])
                stand_ins[c.name] = stand_ins_ids
            else:
                c = clone_node(tree, src)
            tags[c.name] = path
            local[k] = c
            if src.bl_idname == "NodeSeparateBundle":
                feed = ins[src.name][0]
                gi = copy_in(feed.a, path, ("split", reader))
                tree.links.new(source_output(gi, feed.ia, stand_ins), c.inputs[0])
        return local[k]

    for (name, path, reader), group in moves.items():
        c = copy_in(tree.nodes[name], path, reader)
        for w in group:
            sb = input_socket(w.b, w.ib)
            tree.links.new(source_output(c, w.ia, stand_ins), sb)
    # a parameter bundle's Separate Bundle stays where most of its readers are, with its own Group Input
    # beside it (the first one's could be a frame away)
    for name in sorted(stays):
        n = tree.nodes.get(name)
        if n is None or n.bl_idname != "NodeSeparateBundle" or not ins[name] or ins[name][0].a.type != 'GROUP_INPUT':
            continue
        feed = ins[name][0]
        needs[(_key(feed.a), tags[name], ("split", name))].add(feed.ia)
        gi = copy_in(feed.a, tags[name], ("split", name))
        tree.links.new(source_output(gi, feed.ia, stand_ins), n.inputs[0])
    # an original nothing reads any more is clutter, but keep the first Group Input, which older code
    # looks the interface up through (a material's own tree has none)
    keep = next((n for n in tree.nodes if n.type == 'GROUP_INPUT'), None)
    for src in order:
        if src != keep and src.name not in stays:
            del tags[src.name]
            tree.nodes.remove(src)
    return len(local), stand_ins


def node_ranks(tree, outs):
    """Each node's longest path to an output, in links. Nodes with the same value share a column
    (the layering puts each just before its nearest reader)."""
    rank = {}
    for n in tree.nodes:
        stack = [n.name]
        while stack:
            a = stack[-1]
            if a in rank:
                stack.pop()
                continue
            pending = [w.b.name for w in outs[a] if w.b.name not in rank and w.b.name != a]
            if pending:
                stack.extend(pending)
                continue
            rank[a] = 1 + max((rank.get(w.b.name, 0) for w in outs[a]), default=-1)
            stack.pop()
    return rank
