"""The previews' hidden scene: its own world, sun and floor, and whatever a tab shows in it (content), stepped through
its frames by the preview's timer. Nothing in it is shared with the user's scenes; it never saves (dropped before a
save) and is dropped when no tab shows a preview. Writes data: call from timers or operators, never from a draw.
Everything made for it carries TAG, so a clear finds it again after an undo."""
import bpy
from bpy.app.handlers import persistent

SCENE = ".MP Preview"       # a leading dot keeps it out of Blender's scene menus
TAG = "mp_preview"          # 1: the scene's own (world, sun, floor), 2: content
KINDS = ("objects", "meshes", "armatures", "actions", "materials", "images", "node_groups", "collections", "lights",
         "cameras", "curves", "textures", "shape_keys", "worlds")
STATE = {"frame": 1.0, "dirty": True, "made": 0, "stale": False}
# stale: the file changed outside the preview (an import, an undo) and its scene isn't evaluated since: a draw now
# could meet data that is gone       # made: counts scenes made (a tab re-adds its content)


def get():
    return bpy.data.scenes.get(SCENE)


def ensure():
    scene = get()
    if scene is not None:
        return scene
    scene = bpy.data.scenes.new(SCENE)
    scene.render.engine = 'BLENDER_EEVEE'
    scene.eevee.taa_samples = 4
    scene.render.fps = 30
    shading = scene.display.shading
    # TEXTURE: exact materials leave their viewport colour grey; their base colour texture shows
    shading.light, shading.color_type, shading.show_shadows = 'STUDIO', 'TEXTURE', True
    scene.view_settings.view_transform = 'Standard'
    world = bpy.data.worlds.new(SCENE)
    world.color = (0.05, 0.05, 0.055)
    background = world.node_tree.nodes.get("Background") if world.node_tree else None
    if background is not None:
        background.inputs["Color"].default_value = (0.05, 0.05, 0.055, 1.0)
    scene.world = world
    sun = bpy.data.lights.new(SCENE + " Sun", 'SUN')
    sun.energy, sun.angle = 3.0, 0.1
    sun_ob = bpy.data.objects.new(sun.name, sun)
    sun_ob.rotation_euler = (0.75, 0.0, 0.6)
    floor = _floor()
    for idb in (scene, world, sun, sun_ob, floor, floor.data, floor.data.materials[0]):
        idb[TAG] = 1
    scene.collection.objects.link(sun_ob)
    scene.collection.objects.link(floor)
    # the depsgraph made and evaluated once; later frames step it without frame_set (that redraws every window)
    scene.frame_set(1)
    STATE.update(frame=1.0, dirty=True, made=STATE["made"] + 1)
    return scene


def _floor():
    import bmesh
    mesh = bpy.data.meshes.new(SCENE + " Floor")
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=1, y_segments=1, size=6.0)
    bm.to_mesh(mesh)
    bm.free()
    mat = bpy.data.materials.new(mesh.name)
    mat.diffuse_color = (0.3, 0.3, 0.31, 1.0)
    bsdf = mat.node_tree.nodes.get("Principled BSDF") if mat.node_tree else None
    if bsdf is not None:
        bsdf.inputs["Base Color"].default_value = (0.18, 0.18, 0.19, 1.0)
        bsdf.inputs["Roughness"].default_value = 0.8
    mesh.materials.append(mat)
    return bpy.data.objects.new(mesh.name, mesh)


def track(ids):
    """Data the content brought in (appended objects, meshes, actions...): removed with it."""
    for idb in ids:
        if idb is not None and idb.library is None:
            idb[TAG] = 2


def add(objects):
    """Link the content's objects into the scene."""
    scene = ensure()
    for ob in objects:
        if scene.collection.objects.get(ob.name) is None:
            scene.collection.objects.link(ob)
        for slot in ob.material_slots:
            if slot.material is not None and slot.material.get(TAG) == 2:
                _show_diffuse(slot.material)
    changed()


def _show_diffuse(mat):
    """Workbench's Texture colour draws the active image node: make it the diffuse (game textures end in _D)."""
    images = [n for n in mat.node_tree.nodes if n.type == 'TEX_IMAGE' and n.image is not None] if mat.node_tree else []
    if not images:
        return

    def name(n):
        return (n.label or n.image.name).rsplit(".", 1)[0].upper()
    pick = next((n for n in images if name(n).endswith("_D")), None) or next(
        (n for n in images if n.image.colorspace_settings.name == 'sRGB' and "_FX" not in name(n)), None)
    if pick is not None:
        mat.node_tree.nodes.active = pick


def content():
    scene = get()
    return [ob for ob in scene.collection.all_objects if ob.get(TAG) != 1] if scene else []


def _tagged(levels):
    return [idb for kind in KINDS for idb in getattr(bpy.data, kind) if idb.get(TAG) in levels]


def clear():
    """Remove the content and the data it brought in."""
    scene = get()
    if scene is not None:
        for ob in content():
            scene.collection.objects.unlink(ob)
    gone = _tagged((2,))
    if gone:
        bpy.data.batch_remove(gone)
    changed()


def drop():
    """The whole preview scene away, with everything made for it."""
    scene = get()
    gone = _tagged((1, 2))
    if scene is not None:
        bpy.data.scenes.remove(scene)
    if gone:
        bpy.data.batch_remove([idb for idb in gone if idb.name])
    STATE["dirty"] = True


def changed():
    """Content was added, removed or replayed: evaluated now, so no draw meets objects whose data is gone."""
    STATE["dirty"] = True
    scene = get()
    graph = scene.view_layers[0].depsgraph if scene is not None else None
    if graph is not None:
        graph.update()
    STATE["stale"] = False


def step(frame):
    """Show `frame` (float). Evaluates the scene only when something changed: True when it did."""
    scene = get()
    if scene is None or abs(frame - STATE["frame"]) < 1e-4 and not STATE["dirty"]:
        return False
    whole = int(frame)
    scene.frame_current = whole         # its notifier names this scene: the user's editors ignore it
    scene.frame_subframe = frame - whole
    graph = scene.view_layers[0].depsgraph
    if graph is not None:
        graph.update()
    STATE.update(frame=frame, dirty=False, stale=False)
    return True


@persistent
def _before_save(*_):
    if get() is not None or _tagged((1, 2)):
        drop()


@persistent
def _changed(*_):
    STATE.update(dirty=True, stale=True)        # undo, redo or another file: the scene may be gone or older


@persistent
def _elsewhere(scene, _graph=None):
    # the user's scene evaluated (an import, an edit): the preview's timer evaluates it once before the next draw
    # (evaluating here, on every update of an import, froze Blender)
    if scene is not None and scene.name != SCENE and get() is not None:
        STATE.update(dirty=True, stale=True)


_HANDLERS = ((bpy.app.handlers.save_pre, _before_save), (bpy.app.handlers.undo_post, _changed),
             (bpy.app.handlers.redo_post, _changed), (bpy.app.handlers.load_post, _changed),
             (bpy.app.handlers.depsgraph_update_post, _elsewhere))


def register():
    for handlers, fn in _HANDLERS:
        if fn not in handlers:
            handlers.append(fn)


def unregister():
    for handlers, fn in _HANDLERS:
        if fn in handlers:
            handlers.remove(fn)
    if get() is not None:
        drop()
