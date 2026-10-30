"""Grass meshes for scatter layers: each (mesh, override materials) exported once by the engine
(foliage-grass-mesh: the static mesh with the grass variety's OverrideMaterials over its slots), imported here and
kept in the hidden collection "FP Scatter Sources", where scatter layers instance it from. Reused while it's there.
The exports run on the engine's workers a few ahead of the imports (the app stays free for the materials' textures);
their answers import one per timer step, so the editor redraws between. FP's shader library stays appended over a run
of imports (each import would drop it unused and append it again) and is dropped once at the end."""
import collections

import bpy

from . import import_options, importing
from .state import client, tag_redraw

COLLECTION = "FP Scatter Sources"
KEY = "mp_scatter_source"       # object prop: the source's key (mesh;material;...)
STEP = 0.02                     # s between two imports (the editor redraws in between)
AHEAD = 2                       # exports asked and not imported yet, at most

_waiting = {}       # key -> [done callbacks] while its export runs or waits to import
_asks = collections.deque()         # (key, label, query) not asked yet
_arrived = collections.deque()      # (key, label, answer) to import, in arrival order
_open = [0]         # asked, not imported yet
_held = []          # FP's library datablocks given a fake user for the run


def _materials(materials):
    """Override materials without trailing keep-the-slot's entries: [a, ""] and [a] export the same."""
    out = [m or "" for m in materials or []]
    while out and not out[-1]:
        out.pop()
    return out


def key_of(mesh_path, materials):
    return ";".join([mesh_path] + _materials(materials))


def collection(scene=None):
    """The hidden sources collection, linked under the scene (made on first use)."""
    scene = scene or bpy.context.scene
    coll = bpy.data.collections.get(COLLECTION)
    if coll is None:
        coll = bpy.data.collections.new(COLLECTION)
        coll.hide_viewport = coll.hide_render = True        # instances still draw; the source itself doesn't
    if coll.name not in scene.collection.children:
        scene.collection.children.link(coll)
    return coll


def find(mesh_path, materials):
    coll = bpy.data.collections.get(COLLECTION)
    key = key_of(mesh_path, materials)
    # (older scenes keyed trailing empty entries too)
    return next((o for o in coll.objects if o.get(KEY) and key_of(*_split(o[KEY])) == key), None) if coll else None


def _split(key):
    mesh, *materials = key.split(";")
    return mesh, materials


def ensure_source(mesh_path, materials, done):
    """Calls done(obj) with the source object for this mesh and materials (a game path each; None/"" keeps the
    slot's own), exporting and importing it first if needed; done(None) when that fails (the logbook says why).
    Identical (mesh, materials) asked again while in flight share the one export."""
    obj = find(mesh_path, materials)
    if obj is not None:
        done(obj)
        return
    key = key_of(mesh_path, materials)
    if key in _waiting:
        _waiting[key].append(done)
        return
    _waiting[key] = [done]
    label = mesh_path.rsplit("/", 1)[-1].split(".")[0]
    _asks.append((key, label, {"mesh": mesh_path, "materials": ";".join(_materials(materials)),
                               "settings": import_options.query(opened="files")}))
    _ask()


def _ask():
    while _asks and _open[0] < AHEAD:
        key, label, query = _asks.popleft()
        _open[0] += 1
        client().request("foliage-grass-mesh", lambda result, key=key, label=label: _arrive(key, label, result),
                         timeout=1800, **query)


def _arrive(key, label, result):
    """An answer queued for the import timer (the engine's pump hands over every answer in one go)."""
    _arrived.append((key, label, result))
    if not bpy.app.timers.is_registered(_step):
        bpy.app.timers.register(_step, first_interval=0.0, persistent=True)
    return False        # nothing shown changed yet


def _step():
    if not _arrived:
        return None
    _hold(True)
    key, label, result = _arrived.popleft()
    try:
        _landed(key, label, result)
    finally:
        for done in _waiting.pop(key, []):      # (_landed raised: its waiters hear of the failure)
            done(None)
        _open[0] = max(0, _open[0] - 1)
        _ask()
        if not _waiting:        # the run's last import
            _hold(False)
    return STEP if _arrived else None


def _hold(on):
    """FP's shader library kept appended (a fake user each) while a run imports; dropped unused at its end."""
    from ...material_porter import blend_data
    if on and not _held:
        from ...utils import ensure_blend_data
        try:
            ensure_blend_data()
        except Exception:       # (each import appends it itself then)
            return
        for i in blend_data.appended_ids:
            try:
                if not i.use_fake_user:
                    i.use_fake_user = True
                    _held.append(i)
            except ReferenceError:
                pass
    elif not on and _held:
        for i in _held:
            try:
                i.use_fake_user = False
            except ReferenceError:
                pass
        _held.clear()
        blend_data.drop_unused_blend_data()


def _landed(key, label, result):
    objects_before, collections_before = set(bpy.data.objects), set(bpy.data.collections)
    layer = bpy.context.view_layer
    active = layer.objects.active
    message = importing.land(label, result)
    if active is not None and active.name in layer.objects:        # the landscape stays the active object
        layer.objects.active = active
    new = [o for o in bpy.data.objects if o not in objects_before]
    meshes = [o for o in new if o.type == 'MESH']
    # the mesh's own object (FP names it after the mesh): a first import also brings FP's data objects (the rig's
    # control shapes), hidden with it
    obj = next((o for o in meshes if o.name.split(".")[0] == label), meshes[0] if meshes else None)
    if obj is not None:
        coll = collection()
        for o in new:
            for user in list(o.users_collection):
                user.objects.unlink(o)
            coll.objects.link(o)
        obj[KEY] = key
        for c in set(bpy.data.collections) - collections_before:
            if not c.objects and not c.children:
                bpy.data.collections.remove(c)
    if message:
        from . import logbook
        logbook.write(message, logbook.level_of(message))
    for done in _waiting.pop(key, []):
        done(obj)
    tag_redraw()
