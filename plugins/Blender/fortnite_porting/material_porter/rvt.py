"""Fortnite's runtime virtual texture (RVT), baked: what landscapes write to it, read back by what stands on them.

In game a landscape material writes its colour into the RVT (RuntimeVirtualTextureOutput) and meshes read it back by
world XY (RuntimeVirtualTextureSample): Hera's grass takes the ground's colour under its root, rocks blend into it.
Here a material that writes it shows its Base Color pin as the AOV "mp_rvt" (build.py). bake() draws those objects
from above with that colour as their surface (Eevee, orthographic, flat emission) into one image per file ("MP RVT",
sRGB, alpha = covered) over their XY bounds, as the game's one RVT volume covers its map. Custom terrain writes
it when it carries a game landscape material.

The group "MP RVT" reads it at a UE world position: Base Color (0.5 grey where nothing was baked, the translator's old
stand-in) and Valid (1 where baked). A material's VirtualTextureFeatureSwitch takes its PC branch (Yes) where Valid is
1 and its no-VT branch (No) elsewhere, so a file with no bake looks as before.

Re-bake after repainting: bake(obj) redraws that object's rectangle when it lies inside the baked bounds (neighbours
under it are drawn too), else the bounds grow to take it in and everything baked so far is drawn again."""
import json
import math
import os
import tempfile
import time

import bpy
import numpy as np
from mathutils import Vector

NAME = "MP RVT"             # the lookup group and its image
AOV = "mp_rvt"              # a writing material's AOV output (build._material_node)
SOCKET = "RVT Base Color"   # the material root group's output the AOV reads
KEY = "mp_rvt"              # on the scene: {"bounds": [x0, y0, x1, y1] (m), "size": [w, h], "objects": [names]}
VERSION = 1
TEXEL = 0.1                 # m: the finest texel (the game's RVT is far finer; the grass reads it at mip 0)
MAX_SIZE = 4096             # px per side (bytes: 64 MB for a full image)
SCATTER = "FP Scatter"      # node groups of scatter layers (scatter.group): not drawn into the RVT
PACKED = "mp_rvt_packed"    # on the scene: when the bake its packed image holds was made
IMAGE = {"packed": None}    # the PACKED the image's pixels are from
UNDONE = []                 # fn(scene, packed) after undo or redo put the image back (the editor's Auto update)


# ------------------------------------------------------------------ lookup
def image():
    """The baked image (an empty 1 x 1 until a bake)."""
    im = bpy.data.images.get(NAME)
    if im is None:
        # settings only here: setting them again reloads the buffer (an unpacked image regenerates black)
        im = bpy.data.images.new(NAME, 1, 1, alpha=True)
        im.colorspace_settings.name = "sRGB"
        im.alpha_mode = 'STRAIGHT'
        im.pixels[:] = (0.5, 0.5, 0.5, 0.0)
    return im


def lookup_group():
    """The shared lookup group: Position (UE cm) -> Base Color, Valid."""
    t = bpy.data.node_groups.get(NAME)
    if t is not None and t.get("mp_version") == VERSION:
        return t
    from .world import _new_group
    t = _new_group(NAME, inputs=(("Position", 'NodeSocketVector'),),
                   outputs=(("Base Color", 'NodeSocketColor'), ("Valid", 'NodeSocketFloat')))
    N, L = t.nodes, t.links
    gi = N.new("NodeGroupInput"); gi.location = (-900, 0)
    go = N.new("NodeGroupOutput"); go.location = (700, 0)
    xyz = N.new("ShaderNodeSeparateXYZ"); xyz.location = (-700, 0)
    L.new(gi.outputs["Position"], xyz.inputs[0])
    uv = N.new("ShaderNodeCombineXYZ"); uv.location = (-300, 0)
    inside = None
    for i, axis in enumerate("uv"):
        # u = x_ue * scale + offset (UE cm, Y mirrored, to the image's 0..1): set by bake()
        m = N.new("ShaderNodeMath"); m.operation = 'MULTIPLY_ADD'; m.name = m.label = axis
        m.location = (-500, 120 - 240 * i)
        m.inputs[1].default_value, m.inputs[2].default_value = 0.0, -1.0
        L.new(xyz.outputs[i], m.inputs[0])
        L.new(m.outputs[0], uv.inputs[i])
        # inside the image: |t - 0.5| < 0.5
        c = N.new("ShaderNodeMath"); c.operation = 'SUBTRACT'; c.location = (-300, -200 - 120 * i)
        c.inputs[1].default_value = 0.5
        L.new(m.outputs[0], c.inputs[0])
        a = N.new("ShaderNodeMath"); a.operation = 'ABSOLUTE'; a.location = (-150, -200 - 120 * i)
        L.new(c.outputs[0], a.inputs[0])
        lt = N.new("ShaderNodeMath"); lt.operation = 'LESS_THAN'; lt.location = (0, -200 - 120 * i)
        lt.inputs[1].default_value = 0.5
        L.new(a.outputs[0], lt.inputs[0])
        if inside is None:
            inside = lt
        else:
            both = N.new("ShaderNodeMath"); both.operation = 'MULTIPLY'; both.location = (150, -260)
            L.new(inside.outputs[0], both.inputs[0])
            L.new(lt.outputs[0], both.inputs[1])
            inside = both
    tex = N.new("ShaderNodeTexImage"); tex.location = (-100, 200)
    tex.image = image()
    tex.interpolation = 'Linear'
    tex.extension = 'EXTEND'
    L.new(uv.outputs[0], tex.inputs["Vector"])
    covered = N.new("ShaderNodeMath"); covered.operation = 'MULTIPLY'; covered.location = (300, -100)
    L.new(inside.outputs[0], covered.inputs[0])
    L.new(tex.outputs["Alpha"], covered.inputs[1])
    baked = N.new("ShaderNodeValue"); baked.name = baked.label = "baked"; baked.location = (150, -420)
    baked.outputs[0].default_value = 0.0
    valid = N.new("ShaderNodeMath"); valid.operation = 'MULTIPLY'; valid.location = (450, -100)
    L.new(covered.outputs[0], valid.inputs[0])
    L.new(baked.outputs[0], valid.inputs[1])
    mix = N.new("ShaderNodeMix"); mix.data_type = 'RGBA'; mix.location = (450, 150)
    mix.inputs["A"].default_value = (0.5, 0.5, 0.5, 1.0)
    L.new(valid.outputs[0], mix.inputs["Factor"])
    L.new(tex.outputs["Color"], mix.inputs["B"])
    L.new(mix.outputs["Result"], go.inputs["Base Color"])
    L.new(valid.outputs[0], go.inputs["Valid"])
    t["mp_version"] = VERSION
    state = _state(bpy.context.scene)
    if state.get("bounds"):
        _aim(t, state["bounds"])
    return t


def _aim(t, bounds, baked=True):
    """Point the lookup at the image's bounds (m, Blender axes)."""
    x0, y0, x1, y1 = bounds
    w, h = max(x1 - x0, 1e-6), max(y1 - y0, 1e-6)
    # u = (0.01 x_ue - x0) / w ; v = (-0.01 y_ue - y0) / h
    t.nodes["u"].inputs[1].default_value, t.nodes["u"].inputs[2].default_value = 0.01 / w, -x0 / w
    t.nodes["v"].inputs[1].default_value, t.nodes["v"].inputs[2].default_value = -0.01 / h, -y0 / h
    t.nodes["baked"].outputs[0].default_value = 1.0 if baked else 0.0


def _state(scene):
    try:
        return json.loads(scene.get(KEY) or "{}")
    except (TypeError, ValueError):
        return {}


def baked(scene=None):
    """The scene's bake state ({} when none): bounds (m), size (px), objects drawn."""
    return _state(scene or bpy.context.scene)


def clear(scene=None):
    """Forget the bake: materials read their no-VT branch again."""
    scene = scene or bpy.context.scene
    if KEY in scene:
        del scene[KEY]
    t = bpy.data.node_groups.get(NAME)
    if t is not None and "baked" in t.nodes:
        t.nodes["baked"].outputs[0].default_value = 0.0


# ------------------------------------------------------------------ what is drawn
def _source(mat):
    """The socket holding what a material writes to the RVT (its AOV's input), or None."""
    if mat is None or mat.node_tree is None:
        return None
    aov = next((n for n in mat.node_tree.nodes if n.bl_idname == "ShaderNodeOutputAOV" and n.aov_name == AOV), None)
    return aov.inputs["Color"].links[0].from_socket if aov is not None and aov.inputs["Color"].is_linked else None


def _output(mat):
    tree = mat.node_tree
    try:
        out = tree.get_output_node('EEVEE')
    except (AttributeError, TypeError):
        out = None
    return out or next((n for n in tree.nodes if n.bl_idname == "ShaderNodeOutputMaterial"), None)


def writes(obj):
    """Whether an object is drawn into the RVT: a mesh with a material that writes it."""
    return obj.type == 'MESH' and any(_source(s.material) is not None for s in obj.material_slots)


def _objects(target, scene):
    if target is None:
        return [o for o in scene.objects if o.visible_get()]
    if isinstance(target, bpy.types.Collection):
        return [o for o in target.all_objects if o.type == 'MESH']
    if isinstance(target, bpy.types.Object):
        return [target] if target.type == 'MESH' else []
    return [o for o in target if isinstance(o, bpy.types.Object) and o.type == 'MESH']


def _bounds(objects):
    """XY bounds (m) of objects' world bounding boxes, and their Z range."""
    xs, ys, zs = [], [], []
    for o in objects:
        m = o.matrix_world
        for c in o.bound_box:
            p = m @ Vector(c)
            xs.append(p.x); ys.append(p.y); zs.append(p.z)
    return (min(xs), min(ys), max(xs), max(ys)), (min(zs), max(zs))


def _overlaps(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _inside(a, b):
    """a within b."""
    return a[0] >= b[0] and a[1] >= b[1] and a[2] <= b[2] and a[3] <= b[3]


# ------------------------------------------------------------------ bake
def bake(target=None, texel=TEXEL, max_size=MAX_SIZE, scene=None, rect=None):
    """Draw what `target` (an object, a collection, a list; None: every visible writer, from scratch) writes to the
    RVT into the file's image; rect (x0, y0, x1, y1 m): only that area of the last bake, redrawn from what it baked
    (a repainted stroke; outside the bounds, everything again). All but a stroke's pack the image, which undo reads
    back. Returns {"objects", "bounds", "size", "texel", "region", "packed", "ms"}."""
    t0 = time.perf_counter()
    scene = scene or bpy.context.scene
    stroke = rect is not None
    state = {} if target is None and rect is None else _state(scene)
    if rect is not None:
        if not state.get("bounds") or not _inside(rect, state["bounds"]):
            return bake(None, texel, max_size, scene)
        targets = [o for o in (scene.objects.get(n) for n in state.get("objects") or []) if o is not None and writes(o)]
    else:
        targets = [o for o in _objects(target, scene) if writes(o)]
    if not targets:
        return {"objects": 0, "reason": "nothing to bake"}
    region, zr = _bounds(targets)
    if rect is not None:
        region = tuple(rect)
    old = state.get("bounds")
    im = image()
    if old and _inside(region, old) and list(im.size) == list(state.get("size") or []):
        bounds, size, full = old, state["size"], False
        drawn_names = set(state.get("objects") or []) | {o.name for o in targets}
    else:
        if old:
            region = (min(region[0], old[0]), min(region[1], old[1]), max(region[2], old[2]), max(region[3], old[3]))
        span = max(region[2] - region[0], region[3] - region[1])
        step = max(texel, span / max_size)
        size = [max(1, math.ceil((region[2] - region[0]) / step)), max(1, math.ceil((region[3] - region[1]) / step))]
        bounds = [region[0], region[1], region[0] + size[0] * step, region[1] + size[1] * step]
        region, full = tuple(bounds), True
        drawn_names = set(state.get("objects") or []) | {o.name for o in targets}
    step = (bounds[2] - bounds[0]) / size[0]
    # the region in whole texels
    i0 = max(0, int(math.floor((region[0] - bounds[0]) / step)))
    j0 = max(0, int(math.floor((region[1] - bounds[1]) / step)))
    i1 = min(size[0], int(math.ceil((region[2] - bounds[0]) / step)))
    j1 = min(size[1], int(math.ceil((region[3] - bounds[1]) / step)))
    rect = (bounds[0] + i0 * step, bounds[1] + j0 * step, bounds[0] + i1 * step, bounds[1] + j1 * step)
    drawn = [o for o in (scene.objects.get(n) for n in drawn_names) if o is not None and writes(o)]
    drawn = [o for o in drawn if _overlaps(_bounds([o])[0], rect)]
    zr = _bounds(drawn)[1] if drawn else zr
    px = _render(drawn, rect, (i1 - i0, j1 - j0), zr)
    if full or tuple(im.size) != tuple(size):
        im.scale(size[0], size[1])
        buf = np.zeros((size[1], size[0], 4), dtype=np.float32)
        buf[..., :3] = 0.5
    else:
        buf = np.empty(size[0] * size[1] * 4, dtype=np.float32)
        im.pixels.foreach_get(buf)
        buf = buf.reshape(size[1], size[0], 4)
    buf[j0:j1, i0:i1] = _encode(px)
    im.pixels.foreach_set(buf.ravel())
    _watch_undo(scene)
    im.update()
    packed = False
    if full or not stroke:
        _pack(im)
        packed = im.packed_file is not None
    else:
        # packing encodes the whole image (most of a stroke's re-bake): done before the file saves instead
        _pack_on_save()
    if packed:
        # undo reads the pixels back from the packed bake this stamp names
        scene[PACKED] = IMAGE["packed"] = time.time()
    t = lookup_group()
    _aim(t, bounds)
    scene[KEY] = json.dumps({"bounds": bounds, "size": size, "objects": sorted(o.name for o in drawn)
                             if full else sorted(drawn_names)})
    return {"objects": len(drawn), "bounds": [round(v, 2) for v in bounds], "size": size, "texel": round(step, 4),
            "region": [i0, j0, i1, j1], "packed": packed, "ms": round((time.perf_counter() - t0) * 1000.0, 1)}


def _pack(im):
    try:
        im.pack()
    except RuntimeError:
        pass


@bpy.app.handlers.persistent
def _pack_before_save(*_):
    im = bpy.data.images.get(NAME)
    if im is not None and im.is_dirty:
        _pack(im)


def _pack_on_save():
    if _pack_before_save not in bpy.app.handlers.save_pre:
        bpy.app.handlers.save_pre.append(_pack_before_save)


@bpy.app.handlers.persistent
def _undone(*_):
    """Undo puts the scene's bake state back but not always the image's pixels (a partial re-bake leaves the image
    unchanged for undo): read them again from the packed bake when the state names another one."""
    scene = bpy.context.scene
    want, state = scene.get(PACKED), _state(scene)
    im = bpy.data.images.get(NAME)
    if im is not None and (want != IMAGE["packed"] or (state.get("size") and list(im.size) != list(state["size"]))):
        if im.packed_file is not None:
            im.reload()
        else:       # no bake: the empty image
            im.scale(1, 1)
            im.pixels[:] = (0.5, 0.5, 0.5, 0.0)
    IMAGE["packed"] = want
    for fn in UNDONE:
        fn(scene, want)


@bpy.app.handlers.persistent
def _loaded(*_):
    IMAGE["packed"] = bpy.context.scene.get(PACKED)


def _watch_undo(scene):
    """Undo handlers on from the first bake; the pixels until then are the loaded file's bake."""
    if _undone in bpy.app.handlers.undo_post:
        return
    IMAGE["packed"] = scene.get(PACKED)
    for name, fn in (("undo_post", _undone), ("redo_post", _undone), ("load_post", _loaded)):
        handlers = getattr(bpy.app.handlers, name)
        # one each, a reloaded module's replacing the old
        for h in [h for h in handlers if getattr(h, "__module__", None) == __name__ and h.__name__ == fn.__name__]:
            handlers.remove(h)
        handlers.append(fn)


def _encode(px):
    """Linear premultiplied RGBA -> straight sRGB-encoded RGBA (what a byte sRGB image holds)."""
    a = px[..., 3:4]
    rgb = np.where(a > 1e-4, px[..., :3] / np.maximum(a, 1e-4), 0.0)
    rgb = np.clip(rgb, 0.0, 1.0)
    rgb = np.where(rgb <= 0.0031308, rgb * 12.92, 1.055 * np.power(rgb, 1.0 / 2.4) - 0.055)
    return np.concatenate([rgb, np.clip(a, 0.0, 1.0)], axis=-1).astype(np.float32)


def _render(objects, rect, px, zr):
    """(h, w, 4) linear premultiplied RGBA of `objects` seen from above over rect (m), each drawn with what its
    materials write to the RVT."""
    w, h = px
    if not objects or w < 1 or h < 1:
        return np.zeros((max(h, 0), max(w, 0), 4), dtype=np.float32)
    tmp = bpy.data.scenes.new("MP RVT Bake")
    made = []
    path = os.path.join(tempfile.gettempdir(), "mp_rvt_%d.exr" % os.getpid())
    try:
        r = tmp.render
        r.engine = 'BLENDER_EEVEE'
        r.resolution_x, r.resolution_y, r.resolution_percentage = w, h, 100
        r.pixel_aspect_x = r.pixel_aspect_y = 1.0
        r.film_transparent = True
        r.filter_size = 0.5
        r.use_compositing = r.use_sequencer = False
        r.image_settings.file_format = 'OPEN_EXR'
        r.image_settings.color_mode = 'RGBA'
        r.image_settings.color_depth = '32'
        tmp.eevee.taa_render_samples = 4
        tmp.view_settings.view_transform = 'Standard'
        tmp.view_settings.look = 'None'
        tmp.view_settings.exposure, tmp.view_settings.gamma = 0.0, 1.0
        for o in objects:
            c = o.copy()
            c.hide_render = False
            # each slot drawn by its material's bake copy, on the copy alone (the mesh's slots stay)
            for slot in c.material_slots:
                if slot.material is not None:
                    mat = _bake_copy(slot.material)
                    slot.link = 'OBJECT'
                    slot.material = mat
            for m in list(c.modifiers):
                if m.type == 'NODES' and m.node_group is not None and m.node_group.name.startswith(SCATTER):
                    c.modifiers.remove(m)
            tmp.collection.objects.link(c)
            made.append(c)
        cam = bpy.data.objects.new("MP RVT Camera", bpy.data.cameras.new("MP RVT Camera"))
        made.append(cam)
        tmp.collection.objects.link(cam)
        tmp.camera = cam
        cam.data.type = 'ORTHO'
        cam.data.sensor_fit = 'AUTO'
        cam.data.ortho_scale = max(rect[2] - rect[0], rect[3] - rect[1])
        cam.data.clip_start = 0.01
        cam.data.clip_end = (zr[1] - zr[0]) + 20.0
        cam.location = ((rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2, zr[1] + 10.0)
        cam.rotation_euler = (0.0, 0.0, 0.0)
        bpy.ops.render.render(write_still=False, scene=tmp.name)
        result = bpy.data.images.get("Render Result")
        result.save_render(path, scene=tmp)
        im = bpy.data.images.load(path, check_existing=False)
        try:
            buf = np.empty(w * h * 4, dtype=np.float32)
            im.pixels.foreach_get(buf)
        finally:
            bpy.data.images.remove(im)
        return buf.reshape(h, w, 4)
    finally:
        for o in made:
            data = o.data if o.type == 'CAMERA' else None
            bpy.data.objects.remove(o)
            if data is not None:
                bpy.data.cameras.remove(data)
        bpy.data.scenes.remove(tmp)
        if os.path.isfile(path):
            os.remove(path)


def _signature(mat):
    """What a bake copy depends on: the material's links and values (a tint on its group node, an RGB or Value node's
    own output)."""
    def plain(v):
        return tuple(round(x, 5) for x in v) if hasattr(v, "__len__") else (round(v, 5) if isinstance(v, float) else str(v))
    vals = [mat.as_pointer()]
    vals += [(l.from_node.name, l.from_socket.identifier, l.to_node.name, l.to_socket.identifier)
             for l in mat.node_tree.links]
    for n in mat.node_tree.nodes:
        if n.bl_idname == "ShaderNodeGroup" and n.node_tree is not None:
            # a reconverted material's new root group has the same node names
            vals.append((n.name, n.node_tree.name, n.node_tree.as_pointer()))
        for i in n.inputs:
            if not i.is_linked and hasattr(i, "default_value"):
                vals.append((n.name, i.identifier, plain(i.default_value)))
        if n.bl_idname in ("ShaderNodeRGB", "ShaderNodeValue"):
            vals.append((n.name, plain(n.outputs[0].default_value)))
    return str(hash(tuple(vals)))


def _bake_copy(mat):
    """The material drawing a flat emission of what it writes to the RVT (transparent when it writes none). Kept
    between bakes, so its shader compiles once: rewiring the material itself recompiled a landscape's every stroke."""
    name = "MP RVT · " + mat.name
    sig = _signature(mat) if mat.node_tree is not None else ""
    copy = bpy.data.materials.get(name)
    if copy is not None and copy.get("mp_rvt_source") == mat.name and copy.get("mp_rvt_sig") == sig:
        return copy
    if copy is not None:
        bpy.data.materials.remove(copy)
    from . import build
    copy = mat.copy()
    # not a build: reuse by path or shape must never find it
    for k in (build.KEY_PATH, build.KEY_VARIANT, build.KEY_REV, build.KEY_SHAPE, build.KEY_FP):
        if k in copy:
            del copy[k]
    copy.name, copy["mp_rvt_source"], copy["mp_rvt_sig"] = name, mat.name, sig
    tree = copy.node_tree
    out = _output(copy) if tree is not None else None
    if out is not None:
        src = _source(copy)
        em = tree.nodes.new("ShaderNodeEmission" if src is not None else "ShaderNodeBsdfTransparent")
        em.label = "MP RVT bake"
        if src is not None:
            tree.links.new(src, em.inputs["Color"])
        tree.links.new(em.outputs[0], out.inputs["Surface"])
        copy.surface_render_method = 'DITHERED'
    return copy
