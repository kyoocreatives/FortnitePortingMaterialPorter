"""Run by preview_library in a background Blender of its own (never imported by the editor): imports one export payload
the plugin's usual way into an empty file, keeps what it made in one collection and saves it as a library .blend.
With a base library (a body), the payload's animation is imported onto that body first: what the animation brings
(props on their sockets, the master skeleton they follow, effects) is kept, the body only as what they point at.

    blender -b --factory-startup --python preview_library_build.py -- <plugin parent> <package> <payload.json> <out.blend> [<base.blend>]
"""
import importlib
import json
import os
import sys

import bpy

args = sys.argv[sys.argv.index("--") + 1:]
parent, package, payload, out = args[:4]
base = args[4] if len(args) > 4 else None
sys.path.insert(0, parent)
plugin = importlib.import_module(package)
plugin.register()
importer = importlib.import_module(package + ".processing.importer")

if base:
    with bpy.data.libraries.load(base, link=False) as (_src, dst):
        dst.collections = ["MP Library"]
    body = dst.collections[0]
    body.name = "MP Base"
    bpy.context.scene.collection.children.link(body)
    for ob in body.all_objects:
        ob["mp_base"] = 1       # the preview points these at its own body
    arm = next(o for o in body.all_objects if o.type == 'ARMATURE')
    bpy.context.view_layer.objects.active = arm
    arm.select_set(True)

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
