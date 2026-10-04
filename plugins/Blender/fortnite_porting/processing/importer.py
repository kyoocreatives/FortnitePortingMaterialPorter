import json
import time
import bpy
from .context import ImportContext
from .legacy.context import LegacyImportContext
from ..server import Server
from ..utils import addon_version
from ..export_profile import resolve_export_profile


class Importer:
    _version_checked = False

    @staticmethod
    def Import(data: str):
        json_data = json.loads(data)

        meta = json_data.get("MetaData")
        exports = json_data.get("Exports")

        if not Importer._version_checked:
            Importer._version_checked = True
            Importer._check_version(meta)

        profile = resolve_export_profile(bpy.app.version)
        context_type = LegacyImportContext if profile.uses_legacy_materials else ImportContext

        # Material Porter fork: the app's status log hears the import start, end, and what it made
        from ..material_porter import status
        names = ", ".join(str(e.get("Name")) for e in exports or [])
        status.post("Importing %s" % names, state="begin")
        t0 = time.perf_counter()
        objects0 = set(o.name for o in bpy.data.objects)
        failed = None
        # Material Porter fork: the function groups the import made lose what no material reads
        from ..material_porter.build import pruning
        try:
            with pruning():
                for export in exports:
                    context = context_type(meta)
                    context.run(export)
        except Exception as e:
            failed = e
            raise
        finally:
            # skinned meshes keep their rest positions for the shaders (UE's PreSkinnedPosition:
            # a LEGO face's prints stay put while the figure moves)
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

    @staticmethod
    def _check_version(meta: dict):
        try:
            app_version_str = (meta or {}).get("Version", "").lstrip("v").split("-")[0]
            if not app_version_str:
                return
            app_parts = tuple(int(p) for p in app_version_str.split(".") if p.isdigit())
            meta_version = addon_version()
            if app_parts != meta_version:
                plugin_str = ".".join(str(x) for x in meta_version)
                Server.instance.send_dialog(
                    f"Your Fortnite Porting Blender plugin is out of date.\n"
                    f"The currently installed version is v{plugin_str}, but the latest version available is v{app_version_str}."
            
                )
        except Exception:
            pass
