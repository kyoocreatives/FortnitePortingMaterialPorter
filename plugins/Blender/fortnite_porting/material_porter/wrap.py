"""Change or remove the wrap over the selected objects' materials.

A wrap is picked on a weapon's or vehicle's page in the app and comes with the export (a material's
MPWrap: the values the wrap's material sets, which the game lays over the asset's own material). A
built material keeps what it was built with under its wrap (hook.KEY_OVERLAY), so each exact
material of the selected objects can be rebuilt with another wrap, or none (the panel's Remove
Wrap), and take the old one's slots. FP's own (non-exact) materials are left alone.
"""
import json

from . import build, hook


def targets(context):
    """The selected mesh objects, plus the meshes under other selected objects (a weapon's armature,
    a vehicle's empty)."""
    found, seen = [], set()

    def add(o):
        if o.name in seen:
            return
        seen.add(o.name)
        if o.type == 'MESH':
            found.append(o)
        for child in o.children:
            add(child)

    for o in context.selected_objects:
        add(o)
    return found


def lay(job_context, objects, wrap):
    """Rebuild each exact material of the objects with the wrap over it (None: without one).
    Returns (materials changed, objects touched, materials that aren't exact)."""
    rebuilt, touched, skipped = {}, set(), set()
    for o in objects:
        for slot in o.material_slots:
            mat = slot.material
            if mat is None:
                continue
            path = mat.get(build.KEY_PATH)
            if not path:
                skipped.add(mat.name)
                continue
            if wrap is None and not mat.get(hook.KEY_WRAP):
                continue
            new = rebuilt.get(mat.name)
            if new is None:
                data = {"Path": path, "Name": mat.get("OriginalName") or mat.name, "MPWrap": wrap}
                if overlay := mat.get(hook.KEY_OVERLAY):
                    data["MPOverlay"] = json.loads(overlay)
                new = hook.build_exact(job_context, data, obj=o)
                if new is None:
                    skipped.add(mat.name)
                    continue
                for key in ("Hash", "OriginalName", "MPRimLight", "MPSubsurface"):
                    if key in mat and key not in new:
                        new[key] = mat[key]
                rebuilt[mat.name] = new
            if new is not mat:
                slot.material = new
                touched.add(o.name)
    return len(rebuilt), len(touched), sorted(skipped)
