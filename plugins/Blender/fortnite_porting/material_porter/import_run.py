"""Wraps one FP import: status log start/end, pruning, unused data cleanup, rest positions on skinned meshes."""
import time
from contextlib import contextmanager

import bpy


@contextmanager
def import_run(exports):
    from . import status
    from .blend_data import drop_unused_blend_data
    from .build import pruning

    names = ", ".join(str(e.get("Name")) for e in exports or [])
    status.post("Importing %s" % names, state="begin")
    t0 = time.perf_counter()
    objects0 = {o.name for o in bpy.data.objects}
    failed = None
    try:
        with pruning():
            yield
        drop_unused_blend_data()
    except Exception as e:
        failed = e
        raise
    finally:
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
