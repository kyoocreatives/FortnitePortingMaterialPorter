"""The ground colour bake (material_porter/rvt.py), headless: a terrain whose material writes the RVT from a painted
colour attribute bakes each painted side's colour; a scene with no writer bakes nothing.

    python plugin_test.py tests/plugin/rvt_check.py --name rvt
"""
import os
import sys
import tempfile

import bpy
import numpy as np

sys.path.insert(0, sys.argv[sys.argv.index("--") + 1])
from fpmp_baseline.material_porter import rvt  # noqa: E402

FAILS, PASSES = [], [0]
GRAVEL, GRASS = (0.6, 0.5, 0.4), (0.1, 0.3, 0.1)


def check(name, ok, detail=""):
    if ok:
        PASSES[0] += 1
    else:
        FAILS.append("%s %s" % (name, detail))
        print("FAIL", FAILS[-1])


def srgb(c):
    c = np.asarray(c, np.float64)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * np.power(c, 1.0 / 2.4) - 0.055)


def writer_material(name):
    """A surface whose RVT colour is the mesh's "paint" attribute (as build._material_node wires a landscape's)."""
    mat = bpy.data.materials.new(name)
    t = mat.node_tree
    attr = t.nodes.new("ShaderNodeAttribute")
    attr.attribute_name = "paint"
    aov = t.nodes.new("ShaderNodeOutputAOV")
    aov.aov_name = rvt.AOV
    t.links.new(attr.outputs["Color"], aov.inputs["Color"])
    return mat


def terrain(name, size=10.0, n=20):
    """A flat size x size m grid from (0, 0): left half painted GRAVEL, right half GRASS."""
    verts = [(i * size / n, j * size / n, 0.0) for j in range(n + 1) for i in range(n + 1)]
    faces = [(j * (n + 1) + i, j * (n + 1) + i + 1, (j + 1) * (n + 1) + i + 1, (j + 1) * (n + 1) + i)
             for j in range(n) for i in range(n)]
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    a = me.color_attributes.new("paint", 'FLOAT_COLOR', 'POINT')
    col = np.array([GRAVEL + (1.0,) if v[0] < size / 2 else GRASS + (1.0,) for v in verts], np.float32)
    a.data.foreach_set("color", col.ravel())
    obj = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(obj)
    me.materials.append(writer_material(name))
    return obj


def pixel(u, v):
    im = bpy.data.images[rvt.NAME]
    w, h = im.size
    px = np.empty(w * h * 4, np.float32)
    im.pixels.foreach_get(px)
    return px.reshape(h, w, 4)[int(v * (h - 1)), int(u * (w - 1))]


def main():
    # nothing writes the RVT: nothing baked, no image made
    bpy.ops.mesh.primitive_cube_add()
    done = rvt.bake()
    check("nothing to bake", done.get("objects") == 0 and done.get("reason") == "nothing to bake", done)
    im = bpy.data.images.get(rvt.NAME)
    check("...no image", im is None or tuple(im.size) == (1, 1), None if im is None else tuple(im.size))

    obj = terrain("ground")
    check("a painted terrain writes", rvt.writes(obj))
    done = rvt.bake()
    check("baked", done.get("objects") == 1, done)
    for label, u, want in (("gravel side", 0.25, GRAVEL), ("grass side", 0.75, GRASS)):
        got = pixel(u, 0.5)
        check(label, np.abs(got[:3] - srgb(want)).max() < 0.03 and got[3] > 0.99, (got, srgb(want)))
    check("sides differ", np.abs(pixel(0.25, 0.5)[:3] - pixel(0.75, 0.5)[:3]).max() > 0.05)
    print("[rvt] bake %s" % done)

    # a colour changed on a node inside the material (not a group input) re-makes its bake copy
    t = obj.active_material.node_tree
    rgb = t.nodes.new("ShaderNodeRGB")
    rgb.outputs[0].default_value = (0.8, 0.1, 0.1, 1.0)
    aov = next(n for n in t.nodes if n.bl_idname == "ShaderNodeOutputAOV")
    old = aov.inputs["Color"].links[0].from_socket
    t.links.new(rgb.outputs[0], aov.inputs["Color"])
    rvt.bake()
    rgb.outputs[0].default_value = (0.1, 0.1, 0.8, 1.0)
    rvt.bake()
    got = pixel(0.25, 0.5)
    check("a changed RGB node re-makes the bake copy", np.abs(got[:3] - srgb((0.1, 0.1, 0.8))).max() < 0.03, got)
    t.links.new(old, aov.inputs["Color"])
    t.nodes.remove(rgb)
    rvt.bake()

    # a hand-placed object's Ground Blend (custom properties) reaches the shader's INSTANCER Attribute nodes
    from fpmp_baseline.material_porter import ground
    bpy.data.objects.remove(bpy.data.objects["ground"])
    rvt.clear()
    obj = terrain("placed")
    t = obj.active_material.node_tree
    attr = next(n for n in t.nodes if n.bl_idname == "ShaderNodeAttribute")
    attr.attribute_type, attr.attribute_name = 'INSTANCER', ground.ATTR_BLEND
    ground.set_object(obj, "On", "Whole blade", 0.7)
    rvt.bake()
    got = pixel(0.5, 0.5)
    check("an object's Ground Blend reads per instance", abs(got[0] - srgb(0.7)) < 0.03, got)
    check("...and comes back", ground.get_object(obj) == {"mode": "On", "style": "Whole blade", "strength": 0.7},
          ground.get_object(obj))

    # placed foliage (instances.py): its points object's Ground Blend reaches each instance
    from fpmp_baseline.material_porter import instances
    bpy.data.objects.remove(obj)
    rvt.clear()
    blade = terrain("blade", size=2.0, n=2)
    t = blade.active_material.node_tree
    attr = next(n for n in t.nodes if n.bl_idname == "ShaderNodeAttribute")
    attr.attribute_type, attr.attribute_name = 'INSTANCER', ground.ATTR_BLEND
    blade.hide_render = True
    me = bpy.data.meshes.new("placed points")
    me.from_pydata([(3.0, 3.0, 0.0), (5.0, 5.0, 0.0)], [], [])
    points = bpy.data.objects.new("placed points", me)
    bpy.context.scene.collection.objects.link(points)
    g = bpy.data.node_groups.new("on points", 'GeometryNodeTree')
    g.interface.new_socket("Geometry", in_out='INPUT', socket_type='NodeSocketGeometry')
    g.interface.new_socket("Geometry", in_out='OUTPUT', socket_type='NodeSocketGeometry')
    gi, go = g.nodes.new("NodeGroupInput"), g.nodes.new("NodeGroupOutput")
    info, on = g.nodes.new("GeometryNodeObjectInfo"), g.nodes.new("GeometryNodeInstanceOnPoints")
    info.inputs["Object"].default_value = blade
    g.links.new(gi.outputs[0], on.inputs["Points"])
    g.links.new(info.outputs["Geometry"], on.inputs["Instance"])
    g.links.new(on.outputs[0], go.inputs[0])
    points.modifiers.new("on points", 'NODES').node_group = g
    points.data.materials.append(blade.active_material)
    instances.set_ground(points, "On", "Whole blade", 0.4)
    done = rvt.bake([points])
    check("placed foliage baked", done.get("objects") == 1, done)
    got = pixel(0.5, 0.5)
    check("placed foliage's Ground Blend reaches its instances", abs(got[0] - srgb(0.4)) < 0.03, got)
    check("...and comes back", instances.ground_of(points)["mode"] == "On", instances.ground_of(points))


def root_group(colour):
    """A built material's root group (mp_params) whose RVT Base Color is a flat colour."""
    g = bpy.data.node_groups.new("root", "ShaderNodeTree")
    g["mp_params"] = "{}"
    g.interface.new_socket(rvt.SOCKET, in_out='OUTPUT', socket_type='NodeSocketColor')
    rgb, go = g.nodes.new("ShaderNodeRGB"), g.nodes.new("NodeGroupOutput")
    rgb.outputs[0].default_value = colour + (1.0,)
    g.links.new(rgb.outputs[0], go.inputs[0])
    return g


def built():
    """A built material's bake copy is never found as the build (reuse by path or shape); a material reconverted
    in-session (a new root group, same node names) re-makes its copy."""
    from fpmp_baseline.material_porter import build, hook
    for o in list(bpy.context.scene.objects):
        bpy.data.objects.remove(o)
    rvt.clear()
    obj = terrain("rooted")
    # named after "MP RVT · ..." so the sorted ID list gives the copy first
    mat = bpy.data.materials.new("Rooted")
    t = mat.node_tree
    node = t.nodes.new("ShaderNodeGroup")
    node.name, node.node_tree = "root", root_group(GRAVEL)
    aov = t.nodes.new("ShaderNodeOutputAOV")
    aov.aov_name = rvt.AOV
    t.links.new(node.outputs[0], aov.inputs["Color"])
    path, shape = "/Game/Rooted.Rooted", "rooted shape"
    mat[build.KEY_PATH], mat[build.KEY_VARIANT], mat[build.KEY_REV] = path, "", build.BUILD_REVISION
    mat[build.KEY_SHAPE], mat[build.KEY_FP] = shape, "rooted fp"
    obj.data.materials[0] = mat
    rvt.bake()
    check("reuse by path finds the build, not its bake copy", hook._built(path, "") == mat,
          getattr(hook._built(path, ""), "name", None))
    check("reuse by shape too", build.find_shape(shape) == mat, getattr(build.find_shape(shape), "name", None))
    copy = bpy.data.materials.get("MP RVT · Rooted")
    check("...the copy drops the build's keys", copy is not None and not any(
        k in copy for k in (build.KEY_PATH, build.KEY_VARIANT, build.KEY_REV, build.KEY_SHAPE, build.KEY_FP)))
    check("rooted baked", np.abs(pixel(0.5, 0.5)[:3] - srgb(GRAVEL)).max() < 0.03, pixel(0.5, 0.5))
    node.node_tree = root_group(GRASS)
    rvt.bake()
    got = pixel(0.5, 0.5)
    check("a new root group re-makes the bake copy", np.abs(got[:3] - srgb(GRASS)).max() < 0.03, got)


def undo():
    """The public Bake operator, twice (other bounds, same size), then undo: the state and the pixels are the first
    bake's (Blender's undo restores the state but not the image's buffer)."""
    from fpmp_baseline.operator import convert_op
    bpy.utils.register_class(convert_op.FPMP_OT_BakeRVT)
    for o in list(bpy.context.scene.objects):
        bpy.data.objects.remove(o)
    rvt.clear()
    land = terrain("undo land")
    bpy.ops.ed.undo_push(message="Setup")
    bpy.ops.fpmp.bake_rvt()
    bpy.ops.ed.undo_push(message="Bake A")
    first, first_px = rvt.baked(), pixel(0.25, 0.5).copy()
    land.location.x = 20.0
    a = land.data.color_attributes["paint"]
    a.data.foreach_set("color", list(GRASS + (1.0,)) * len(a.data))
    land.data.update()
    bpy.ops.ed.undo_push(message="Move")
    bpy.ops.fpmp.bake_rvt()
    bpy.ops.ed.undo_push(message="Bake B")
    second = rvt.baked()
    check("undo: two bakes, same size, other bounds", second["size"] == first["size"]
          and second["bounds"] != first["bounds"] and np.abs(pixel(0.25, 0.5) - first_px).max() > 0.05, (first, second))
    bpy.ops.ed.undo()
    check("undo: the first bake's state", rvt.baked() == first, (rvt.baked(), first))
    got = pixel(0.25, 0.5)
    check("undo: ...and its pixels", np.abs(got - first_px).max() < 1e-6, (got, first_px))
    bpy.ops.ed.redo()
    got = pixel(0.25, 0.5)
    check("redo: the second's", rvt.baked() == second and np.abs(got[:3] - srgb(GRASS)).max() < 0.03, (rvt.baked(), got))
    # the selected terrain, repainted, inside the bounds: a partial re-bake, undone the same way
    land = bpy.data.objects["undo land"]        # (undo freed the old references)
    a = land.data.color_attributes["paint"]
    a.data.foreach_set("color", list(GRAVEL + (1.0,)) * len(a.data))
    land.data.update()
    land.select_set(True)
    bpy.ops.ed.undo_push(message="Repaint")
    bpy.ops.fpmp.bake_rvt()
    bpy.ops.ed.undo_push(message="Bake C")
    check("selected re-bake: its pixels", np.abs(pixel(0.25, 0.5)[:3] - srgb(GRAVEL)).max() < 0.03, pixel(0.25, 0.5))
    bpy.ops.ed.undo()
    got = pixel(0.25, 0.5)
    check("undo a selected re-bake: the pixels back", np.abs(got[:3] - srgb(GRASS)).max() < 0.03, got)
    bpy.utils.unregister_class(convert_op.FPMP_OT_BakeRVT)


def rebake():
    """A painted 20 m square of a 1 km terrain re-baked alone: only its pixels change; how long it takes warm."""
    for o in list(bpy.context.scene.objects):
        bpy.data.objects.remove(o)
    rvt.clear()
    land = terrain("km", size=1000.0, n=100)
    done = rvt.bake()
    im = bpy.data.images[rvt.NAME]
    w, h = im.size
    before = np.empty(w * h * 4, np.float32)
    im.pixels.foreach_get(before)
    # repaint a 20 m square inside the grass half
    rect = (700.0, 400.0, 720.0, 420.0)
    a = land.data.color_attributes["paint"]
    col = np.empty(len(a.data) * 4, np.float32)
    a.data.foreach_get("color", col)
    col = col.reshape(-1, 4)
    co = np.empty(len(land.data.vertices) * 3, np.float32)
    land.data.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    inside = (co[:, 0] >= rect[0]) & (co[:, 0] <= rect[2]) & (co[:, 1] >= rect[1]) & (co[:, 1] <= rect[3])
    col[inside, :3] = GRAVEL
    a.data.foreach_set("color", col.ravel())
    land.data.update()
    # the terrain's own material is never touched (a rewired landscape shader recompiles: seconds per stroke)
    touched = []

    def watch(scene, depsgraph):
        touched.extend(u.id.name for u in depsgraph.updates if isinstance(u.id, bpy.types.Material))
    bpy.app.handlers.depsgraph_update_post.append(watch)
    times = [rvt.bake(rect=rect)["ms"] for _ in range(3)]
    bpy.app.handlers.depsgraph_update_post.remove(watch)
    check("rect re-bake: the terrain's material untouched", land.active_material.name not in touched, set(touched))
    after = np.empty(w * h * 4, np.float32)
    im.pixels.foreach_get(after)
    before, after = before.reshape(h, w, 4), after.reshape(h, w, 4)
    step = (done["bounds"][2] - done["bounds"][0]) / w
    i0, j0 = int(rect[0] / step) - 1, int(rect[1] / step) - 1
    i1, j1 = int(rect[2] / step) + 2, int(rect[3] / step) + 2
    outside = np.ones((h, w), bool)
    outside[j0:j1, i0:i1] = False
    check("rect re-bake: nothing outside changes", np.array_equal(before[outside], after[outside]))
    check("rect re-bake: the painted square changes", np.abs(before[~outside] - after[~outside]).max() > 0.05)
    print("[rvt] rebake 20x20 m of a 1 km terrain (%d x %d px): %s ms" % (w, h, ", ".join("%.0f" % t for t in times)))
    # a re-bake packs when the file saves: the repainted square survives a reload
    painted = after[~outside].copy()
    path = os.path.join(tempfile.mkdtemp(prefix="rvt_check_"), "rebaked.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path)
    bpy.ops.wm.open_mainfile(filepath=path)
    im = bpy.data.images[rvt.NAME]
    again = np.empty(w * h * 4, np.float32)
    im.pixels.foreach_get(again)
    check("rect re-bake: saved with the file", np.abs(again.reshape(h, w, 4)[~outside] - painted).max() < 1e-6)


main()
built()
undo()
rebake()
print("[rvt_check] %d passed, %d failed" % (PASSES[0], len(FAILS)))
if FAILS:
    sys.exit(1)
