"""Wraps one FP import: status log start/end, pruning, unused data cleanup once imports go quiet, rest positions on
skinned meshes."""
import time
from contextlib import contextmanager

import bpy


def _landscape_layers(value, out=None):
    """Every landscape layer name in an import's exports (MPLayerNames)."""
    out = set() if out is None else out
    if isinstance(value, dict):
        names = value.get("MPLayerNames")
        if isinstance(names, dict):
            out.update(str(n) for n in names.values())
        for v in value.values():
            if isinstance(v, (dict, list)):
                _landscape_layers(v, out)
    elif isinstance(value, list):
        for v in value:
            _landscape_layers(v, out)
    return out


DROP_DELAY = 10.0


def _drop_when_idle():
    """Timer: drops unused appended data once no export waits. Dropping after every import made each of a map's
    cells append FP's shader library again (about 20 s each, even for empty cells)."""
    from ..server import Server
    if Server.instance is not None and Server.instance.queue:
        return DROP_DELAY
    from .blend_data import drop_unused_blend_data
    dropped = drop_unused_blend_data()
    if dropped:
        print("[material_porter] %d unused datablocks dropped" % dropped)
    return None


def _schedule_drop():
    if bpy.app.timers.is_registered(_drop_when_idle):
        bpy.app.timers.unregister(_drop_when_idle)
    bpy.app.timers.register(_drop_when_idle, first_interval=DROP_DELAY)


@contextmanager
def import_run(exports):
    from . import progress, status
    from .build import pruning

    from . import landscape_layers
    names = ", ".join(str(e.get("Name")) for e in exports or [])
    landscape_layers.INCOMING = _landscape_layers(exports)
    status.post("Importing %s" % names, state="begin")
    t0 = time.perf_counter()
    objects0 = {o.name for o in bpy.data.objects}
    failed = None
    try:
        with pruning() as pruned:
            yield
            t1 = time.perf_counter()
            progress.stage("Pruning node groups")
        _schedule_drop()
        prune, bundle = pruned.seconds
        progress.item("%d nodes pruned (%s), attributes bundled (%s)" % (
            pruned.gone, progress.seconds(prune), progress.seconds(bundle)), t1)
    except Exception as e:
        failed = e
        raise
    finally:
        landscape_layers.INCOMING = set()
        # UE's PreSkinnedPosition (LEGO face prints must not follow the skinning)
        for o in bpy.data.objects:
            if o.name not in objects0 and o.type == 'MESH' and any(m.type == 'ARMATURE' for m in o.modifiers):
                o.add_rest_position_attribute = True
        # imported meshes only (bone-shape widgets have no material)
        meshes = [o for o in bpy.data.objects if o.name not in objects0 and o.type == 'MESH' and len(o.material_slots) > 0]
        used = {s.material.name: s.material for o in meshes for s in o.material_slots if s.material is not None}
        exact = sum(1 for m in used.values() if m.name.startswith("MP "))
        status.post("%s %s: %d meshes, %d materials (%d exact) in %.1f s" % (
            "Import failed" if failed else "Imported", names, len(meshes), len(used), exact,
            time.perf_counter() - t0) + (" - %s: %s" % (type(failed).__name__, failed) if failed else ""), state="end")
