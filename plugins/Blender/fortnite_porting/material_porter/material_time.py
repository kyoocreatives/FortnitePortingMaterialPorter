"""The Animated Materials switch: Fortnite materials read their time from one frame-driven value; muting those drivers
holds every animated shader (panners, flipbooks, pulses) on its current look, unmuting resumes it."""
import bpy

STILL = "fpmp_materials_still"
LABELS = ("Time (s)", "Time (seconds)")


def is_animated(scene):
    return not scene.get(STILL, False)


def _time_drivers():
    trees = [m.node_tree for m in bpy.data.materials if m.node_tree] + [g for g in bpy.data.node_groups if g.bl_idname == "ShaderNodeTree"]
    for tree in trees:
        ad = tree.animation_data
        if ad is None:
            continue
        for fc in ad.drivers:
            if not fc.data_path.startswith('nodes["') or "frame" not in fc.driver.expression:
                continue
            node = tree.nodes.get(fc.data_path.split('"')[1])
            if node is not None and node.bl_idname == "ShaderNodeValue" and node.label in LABELS:
                yield fc


def set_animated(on, scene=None):
    scene = scene or bpy.context.scene
    scene[STILL] = not on
    for fc in _time_drivers():
        fc.mute = not on


def follow(fc):
    """A time driver just made follows the switch."""
    fc.mute = not is_animated(bpy.context.scene)
