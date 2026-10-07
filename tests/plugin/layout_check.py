"""Proves the plugin's material_porter/layout.py moved nodes around without changing what they compute.

Builds every tree translator_test.py builds (it runs that script's body, so they are the translator's own
output), lays out a copy of each and compares the two graphs the way a shader would see them: every input of
every node resolved through reroutes to the node and output that really feeds it, with pure input nodes (Group
Input and the bundle split, Geometry, Texture Coordinate, ...) compared by what they are rather than by name,
since the layout copies them. Also compares every unlinked input's value and the group interface, and checks
that no link is invalid and no reroute is left dangling (and, for a tree whose nodes all carry a section tag,
that every node is framed but the bundle/interface nodes).

    blender -b --factory-startup --python-exit-code 1 -P tests/plugin/layout_check.py -- <plugin parent>

<plugin parent> is a folder holding the plugin as a package named fpmp_baseline (see translator_test.py). Exit
code 0 when every check passes (and translator_test's own do).
"""
import os
import runpy
import sys

import bpy

sys.path.insert(0, sys.argv[sys.argv.index("--") + 1])
from fpmp_baseline.material_porter import layout, nodelib  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
failed = 0


def check(ok, msg):
    global failed
    if not ok:
        failed += 1
    print("[layout_check] %s %s" % ("ok  " if ok else "FAIL", msg))


def source_key(node, ins):
    """What a pure input node is, independent of which copy it is."""
    if node.type == 'GROUP_INPUT':
        return ("GROUP_INPUT",)
    if node.bl_idname == "NodeSeparateBundle":
        feeds = ins.get(node.name, [])
        while len(feeds) == 1 and feeds[0].from_node.bl_idname == "NodeReroute":
            feeds = ins.get(feeds[0].from_node.name, [])
        if feeds and all(l.from_node.type == 'GROUP_INPUT' for l in feeds):
            return ("BUNDLE_SPLIT",) + tuple((i.name, i.socket_type) for i in node.bundle_items)
    if node.bl_idname in layout.PURE_SOURCES:
        return (node.bl_idname,) + tuple(str(getattr(node, p))
                                         for p in layout.PURE_SOURCES[node.bl_idname])
    if node.bl_idname == "ShaderNodeValue" and layout._driver(node) is not None:
        return ("TIME", layout._driver(node).driver.expression)
    return None


def semantics(tree):
    ins = {}
    by_to = {}
    for l in tree.links:
        ins.setdefault(l.to_node.name, []).append(l)
        by_to[(l.to_node.name, l.to_socket.identifier)] = l

    def resolve(node, ident):
        seen = 0
        while node.bl_idname == "NodeReroute":
            l = by_to.get((node.name, node.inputs[0].identifier))
            if l is None:
                return ("DANGLING",)
            node, ident = l.from_node, l.from_socket.identifier
            seen += 1
            if seen > 10000:
                return ("LOOP",)
        key = source_key(node, ins)
        return (key or node.name, ident)

    edges, values = set(), {}
    for n in tree.nodes:
        if n.bl_idname in ("NodeReroute", "NodeFrame") or source_key(n, ins):
            continue
        for s in n.inputs:
            l = by_to.get((n.name, s.identifier))
            if l is not None:
                edges.add((n.name, s.identifier, l.is_muted) + resolve(l.from_node, l.from_socket.identifier))
            elif hasattr(s, "default_value"):
                v = s.default_value
                try:
                    v = tuple(round(x, 6) for x in v)
                except TypeError:
                    v = round(v, 6) if isinstance(v, float) else v
                values[(n.name, s.identifier)] = v
    return edges, values


def interface(tree):
    out = []
    for it in tree.interface.items_tree:
        row = (it.item_type, it.name, getattr(it, "in_out", ""),
               getattr(it, "socket_type", ""), it.parent.name if it.parent else "")
        dv = getattr(it, "default_value", None)
        if dv is not None:
            try:
                dv = tuple(round(x, 6) for x in dv)
            except TypeError:
                dv = round(dv, 6) if isinstance(dv, float) else dv
        out.append(row + (dv,))
    return out


def invariants(tree):
    bad = [l for l in tree.links if not l.is_valid]
    check(not bad, "%s: every link valid (%d invalid)" % (tree.name, len(bad)))
    linked_in = {l.to_node.name for l in tree.links}
    dangling = [n.name for n in tree.nodes
                if n.bl_idname == "NodeReroute" and n.name not in linked_in]
    check(not dangling, "%s: no dangling reroute (%s)" % (tree.name, dangling[:5]))
    # (the layout frames by the sections the builder tagged: a tree no builder tagged, like most of the translator's
    # test trees, only has its tagged nodes framed)
    if not all(nodelib.SECTION_KEY in n for n in tree.nodes if n.bl_idname != "NodeFrame"):
        return
    loose = [n.name for n in tree.nodes
             if n.bl_idname not in ("NodeFrame",) and n.parent is None
             and n.bl_idname not in ("NodeGroupInput", "NodeGroupOutput",
                                     "NodeSeparateBundle", "NodeCombineBundle",
                                     "NodeReroute")]
    check(not loose, "%s: every node framed (%s)" % (tree.name, loose[:5]))


def main():
    # (translator_test builds its trees into bpy.data and exits 1 when one of its own checks fails)
    try:
        runpy.run_path(os.path.join(HERE, "translator_test.py"), run_name="translator_test")
    except SystemExit as e:
        check(not e.code, "translator_test's own checks pass")
    trees = [t for t in bpy.data.node_groups if len(t.nodes)]
    check(len(trees) > 0, "translator_test built node groups (%d)" % len(trees))
    for raw in trees:
        copy = raw.copy()
        before = semantics(raw)
        iface = interface(raw)
        print("[layout_check] %s: %s" % (raw.name, layout.arrange(copy)))
        after = semantics(copy)
        check(before[0] == after[0], "%s: every input fed by the same source (%d links; "
              "%d lost, %d new)" % (raw.name, len(before[0]),
                                     len(before[0] - after[0]), len(after[0] - before[0])))
        for e in sorted(before[0] - after[0])[:5]:
            print("    lost", e)
        for e in sorted(after[0] - before[0])[:5]:
            print("    new ", e)
        check(before[1] == after[1], "%s: every unlinked value unchanged (%d)"
              % (raw.name, len(before[1])))
        check(iface == interface(copy), "%s: interface unchanged" % raw.name)
        invariants(copy)
    print("[layout_check] %d check(s) failed" % failed)
    if failed:
        sys.exit(1)


main()
