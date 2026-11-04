"""Run by preview_library in a background Blender of its own (never imported by the editor): imports one export payload
the plugin's usual way into an empty file, keeps what it made in one collection and saves it as a library .blend.

    blender -b --factory-startup --python preview_library_build.py -- <plugin parent> <package> <payload.json> <out.blend>
"""
import importlib
import json
import os
import sys

import bpy

parent, package, payload, out = sys.argv[sys.argv.index("--") + 1:][:4]
sys.path.insert(0, parent)
plugin = importlib.import_module(package)
plugin.register()
importer = importlib.import_module(package + ".processing.importer")

data = json.load(open(payload, encoding="utf-8"))
before = set(bpy.data.objects)
for export in data["Exports"]:
    importer.ImportContext(data["MetaData"]).run(export)
made = [o for o in bpy.data.objects if o not in before]
keep = bpy.data.collections.new("MP Library")
for ob in made:
    keep.objects.link(ob)
keep.use_fake_user = True
bpy.data.libraries.write(out, {keep}, path_remap='ABSOLUTE', fake_user=True)
print("MP_LIBRARY", json.dumps({"objects": len(made), "out": os.path.abspath(out)}), flush=True)
