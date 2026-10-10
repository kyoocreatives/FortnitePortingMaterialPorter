"""Checks the Animated Materials switch: material time drivers muted and resumed, new materials following the switch.

    blender -b --factory-startup --python-exit-code 1 -P tests/plugin/material_time_check.py -- <plugin parent>

<plugin parent> holds the plugin as package fpmp_baseline (see translator_test.py). Exit code 0 when every check passes."""
import sys

import bpy

sys.path.insert(0, sys.argv[sys.argv.index("--") + 1])
import fpmp_baseline  # noqa: E402,F401
from fpmp_baseline.material_porter import material_time as mt  # noqa: E402

FAILS, PASSES = [], [0]


def check(name, got, want, tol=1e-4):
    ok = abs(got - want) <= tol if isinstance(want, float) else got == want
    if ok:
        PASSES[0] += 1
    else:
        FAILS.append(name)
        print("[material_time_check] FAIL %s: got %r, want %r" % (name, got, want))


scene = bpy.context.scene
scene.render.fps, scene.render.fps_base = 30, 1.0


def timed_material(name, label="Time (s)"):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    node = mat.node_tree.nodes.new("ShaderNodeValue")
    node.label = label
    d = node.outputs[0].driver_add("default_value").driver
    d.type = 'SCRIPTED'
    d.expression = "frame / 30"
    return mat, node


old, old_node = timed_material("old")
mesh = bpy.data.meshes.new("holder")
holder = bpy.data.objects.new("holder", mesh)
scene.collection.objects.link(holder)
mesh.materials.append(old)          # only materials in use evaluate their drivers
scene.frame_set(30)
check("time follows the frame", old_node.outputs[0].default_value, 1.0)
mt.set_animated(False)
scene.frame_set(60)
check("switched off, the material holds its time", old_node.outputs[0].default_value, 1.0)
check("the switch reads off", mt.is_animated(scene), False)
group = bpy.data.node_groups.new("grouped", "ShaderNodeTree")
gnode = group.nodes.new("ShaderNodeValue")
gnode.label = "Time (seconds)"
fc = gnode.outputs[0].driver_add("default_value")
fc.driver.expression = "frame / 30"
mt.follow(fc)
check("a time driver made while off starts muted", fc.mute, True)
user = old.node_tree.nodes.new("ShaderNodeGroup")
user.node_tree = group
mt.set_animated(True)
scene.frame_set(90)
check("switched back on, time moves again", old_node.outputs[0].default_value, 3.0)
check("and the group's too", gnode.outputs[0].default_value, 3.0)
other = bpy.data.materials.new("plain")
other.use_nodes = True
v = other.node_tree.nodes.new("ShaderNodeValue")
v.label = "Roughness"
dd = v.outputs[0].driver_add("default_value")
dd.driver.expression = "frame / 30"
mt.set_animated(False)
check("other drivers are left alone", dd.mute, False)
mt.set_animated(True)

from fpmp_baseline.operator import convert_op  # noqa: E402
try:
    convert_op.register()
except ValueError:
    pass
check("the switch operator runs", bpy.ops.fpmp.material_animation(on=False), {'FINISHED'})
check("and turns time off", mt.is_animated(scene), False)

print("[material_time_check] %d passed, %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
