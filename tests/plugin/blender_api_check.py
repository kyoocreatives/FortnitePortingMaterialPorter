"""Checks the node types the plugin creates against this Blender: each still exists, and no socket the plugin may
reach by index moved, lost its name or went away since the newest recorded Blender at or below this one.

    blender -b --factory-startup --python-exit-code 1 -P tests/plugin/blender_api_check.py -- <plugin parent> [--record]

<plugin parent> holds the plugin as package fpmp_baseline (see translator_test.py). --record writes this Blender's
layouts to blender_sockets/<major>.<minor>.json. On a new Blender: run the check against the previous snapshot, fix
or move to NAMED_ONLY every node it flags, then --record. Exit code 0 when every check passes."""
import glob
import json
import os
import re
import sys

import bpy

PARENT = sys.argv[sys.argv.index("--") + 1]
RECORD = "--record" in sys.argv
SNAPSHOTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "blender_sockets")
# the plugin reaches these nodes' sockets by name only, so moved sockets don't matter (renames still do)
NAMED_ONLY = {"ShaderNodeBsdfPrincipled", "ShaderNodeBsdfGlass", "ShaderNodeVectorTransform"}
TREES = (("ShaderNode", "ShaderNodeTree"), ("GeometryNode", "GeometryNodeTree"), ("FunctionNode", "GeometryNodeTree"),
         ("CompositorNode", "CompositorNodeTree"), ("Node", "ShaderNodeTree"))
FAILS = []
PASSES = [0]


def used_types():
    """Node bl_idnames named anywhere in the plugin's source."""
    names = set()
    for path in glob.glob(os.path.join(PARENT, "fpmp_baseline", "**", "*.py"), recursive=True):
        names.update(re.findall(r"[\"']((?:Shader|Geometry|Function|Compositor)?Node[A-Z]\w+)[\"']", open(path, encoding="utf-8").read()))
    return sorted(n for n in names if isinstance(getattr(bpy.types, n, None), type) and issubclass(getattr(bpy.types, n), bpy.types.Node))


def layout(name, trees):
    tree = trees[next(t for prefix, t in TREES if name.startswith(prefix))]
    node = tree.nodes.new(name)
    return {"in": [[s.identifier, s.name] for s in node.inputs], "out": [[s.identifier, s.name] for s in node.outputs]}


def fail(message):
    FAILS.append(message)
    print("[blender_api_check] FAIL " + message)


def compare(name, old, new):
    for side in ("in", "out"):
        now = {ident: (i, label) for i, (ident, label) in enumerate(new[side])}
        for i, (ident, label) in enumerate(old[side]):
            if ident not in now:
                fail("%s %s socket %d %r is gone" % (name, side, i, label))
            elif now[ident][1] != label:
                fail("%s %s socket %r is now named %r" % (name, side, label, now[ident][1]))
            elif now[ident][0] != i and name not in NAMED_ONLY:
                fail("%s %s socket %r moved %d -> %d (reach it by name, then add the node to NAMED_ONLY)"
                     % (name, side, label, i, now[ident][0]))
            else:
                PASSES[0] += 1


def snapshot():
    """The newest snapshot recorded at or below this Blender."""
    here = bpy.app.version[:2]
    found = []
    for path in glob.glob(os.path.join(SNAPSHOTS, "*.json")):
        version = tuple(int(v) for v in os.path.basename(path)[:-5].split("."))
        if version <= here:
            found.append((version, path))
    return max(found)[1] if found else None


def main():
    trees = {kind: bpy.data.node_groups.new("api check", kind) for kind in ("ShaderNodeTree", "GeometryNodeTree", "CompositorNodeTree")}
    current = {}
    for name in used_types():
        try:
            current[name] = layout(name, trees)
        except Exception as e:
            fail("%s can't be created: %s" % (name, e))
    if RECORD:
        os.makedirs(SNAPSHOTS, exist_ok=True)
        path = os.path.join(SNAPSHOTS, "%d.%d.json" % bpy.app.version[:2])
        json.dump({"blender": bpy.app.version_string, "nodes": current}, open(path, "w", encoding="utf-8"), indent=0, sort_keys=True)
        print("[blender_api_check] recorded %d node types -> %s" % (len(current), path))
        return
    path = snapshot()
    if path is None:
        fail("no snapshot at or below Blender %d.%d in %s" % (*bpy.app.version[:2], SNAPSHOTS))
        return
    old = json.load(open(path, encoding="utf-8"))["nodes"]
    for name in sorted(current):
        if name not in old:
            fail("%s isn't in %s: record this Blender's layouts (--record)" % (name, os.path.basename(path)))
        else:
            compare(name, old[name], current[name])
    print("[blender_api_check] against %s: %d node types" % (os.path.basename(path), len(current)))


main()
print("[blender_api_check] %d passed, %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
