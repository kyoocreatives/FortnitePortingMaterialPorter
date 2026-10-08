"""Around one FP import: the app's status log hears it start and end, function groups lose what no material
reads, FP's unused library data goes, skinned meshes keep their rest positions."""
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
        # UE's PreSkinnedPosition: a LEGO face's prints stay put while the figure moves
        for o in bpy.data.objects:
            if o.name not in objects0 and o.type == 'MESH' and any(m.type == 'ARMATURE' for m in o.modifiers):
                o.add_rest_position_attribute = True
        # the imported meshes (not FP's bone-shape widgets, which carry no material)
        meshes = [o for o in bpy.data.objects if o.name not in objects0 and o.type == 'MESH' and len(o.material_slots) > 0]
        used = {s.material.name: s.material for o in meshes for s in o.material_slots if s.material is not None}
        exact = sum(1 for m in used.values() if m.name.startswith("MP "))
        status.post("%s %s: %d meshes, %d materials (%d exact) in %.1f s" % (
            "Import failed" if failed else "Imported", names, len(meshes), len(used), exact,
            time.perf_counter() - t0) + (" - %s: %s" % (type(failed).__name__, failed) if failed else ""), state="end")
