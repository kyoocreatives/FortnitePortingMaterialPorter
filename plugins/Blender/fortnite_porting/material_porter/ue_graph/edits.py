"""Fewer tree updates. Each location, value, setting, link or node set through bpy makes Blender update the
whole tree (about a microsecond per node, far more per group node: a link change redeclares every one), so
a big material's build was quadratic: 40 s for a LEGO head."""

import struct


def _f32(x):
    return struct.unpack("f", struct.pack("f", x))[0]


def holds(cur, v):
    """Whether a property or socket value `cur` already is v as Blender would store it (float32)."""
    try:
        if isinstance(cur, float):
            return isinstance(v, (int, float)) and not isinstance(v, bool) and cur == _f32(v)
        if isinstance(cur, (str, bool, int)) or cur is None:
            return type(cur) is type(v) and cur == v
        if hasattr(cur, "as_pointer"):     # an ID (image, node tree)
            return v is not None and hasattr(v, "as_pointer") and cur.as_pointer() == v.as_pointer()
        vals = tuple(cur)
        return len(vals) == len(v) and all(isinstance(c, float) and holds(c, x) for c, x in zip(vals, v))
    except (TypeError, OverflowError):
        return False


class Placing:
    """Node locations held back while a material builds and set in one go per tree (foreach_set
    updates nothing). place() sets them; nodes made after that are placed as made."""

    def __init__(self):
        self.trees = {}     # id(tree) -> (tree, {node pointer: (x, y)}); a removed tree's address can come back

    def add(self, tree, node, x, y):
        self.trees.setdefault(id(tree), (tree, {}))[1][node.as_pointer()] = (x, y)

    def place(self):
        trees, self.trees = self.trees, None
        for tree, locs in trees.values():
            try:
                count = len(tree.nodes)
            except ReferenceError:      # a function nothing used (Translator.prune_unused)
                continue
            flat = [0.0] * (2 * count)
            tree.nodes.foreach_get("location", flat)
            for i, n in enumerate(tree.nodes):
                xy = locs.get(n.as_pointer())
                if xy is not None:
                    flat[2 * i], flat[2 * i + 1] = xy
            tree.nodes.foreach_set("location", flat)
