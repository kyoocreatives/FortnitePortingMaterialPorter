"""Game meshes for previews (the mannequin, body types, props): each imported once into a cached library .blend by a
background Blender of its own (the importer works on the context's scene and active object: never the user's), then
appended into the preview scene in a moment."""
import json
import os
import subprocess
import tempfile

import bpy

from . import preview_scene
from .state import client

JOBS = {}       # key -> [callbacks] while it builds
ERRORS = {}     # key -> why its build failed
COLLECTION = "MP Library"


def folder():
    path = os.path.join(bpy.utils.user_resource('DATAFILES', path="fortnite_porting_mp"), "preview")
    os.makedirs(path, exist_ok=True)
    return path


def path(key):
    return os.path.join(folder(), key + ".blend")


def ready(key):
    return os.path.isfile(path(key))


def build(key, game_file, settings, done):
    """done(error or None) once `key`'s library exists (at once when it does)."""
    if ready(key):
        done(None)
        return
    if key in JOBS:
        JOBS[key].append(done)
        return
    JOBS[key] = [done]
    ERRORS.pop(key, None)
    client().request("files-export", lambda result: _exported(key, result), timeout=600, paths=game_file,
                     settings=settings)


def _finish(key, error):
    if error:
        ERRORS[key] = error
    for fn in JOBS.pop(key, []):
        fn(error)


def _exported(key, result):
    if isinstance(result, Exception) or not result.get("Exports"):
        _finish(key, "export failed: %s" % (result if isinstance(result, Exception) else "nothing exported"))
        return
    work = tempfile.mkdtemp(prefix="fp_preview_")
    payload = os.path.join(work, "payload.json")
    with open(payload, "w", encoding="utf-8") as f:
        json.dump(result, f)
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))      # the plugin's folder
    env = dict(os.environ, MATERIAL_PORTER_BRIDGE=client().url)
    for name in ("CONFIG", "EXTENSIONS", "SCRIPTS"):        # its own settings: never the user's
        env["BLENDER_USER_" + name] = os.path.join(work, name.lower())
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "preview_library_build.py")
    out = path(key) + ".part"
    cmd = [bpy.app.binary_path, "-b", "--factory-startup", "--python", script, "--",
           os.path.dirname(root), os.path.basename(root), payload, out]
    log = open(os.path.join(work, "build.log"), "w", encoding="utf-8", errors="replace")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.Popen(cmd, env=env, stdout=log, stderr=subprocess.STDOUT, creationflags=flags)

    def wait():
        if proc.poll() is None:
            return 0.25
        log.close()
        if os.path.isfile(out):
            os.replace(out, path(key))
            _finish(key, None)
        else:
            tail = open(log.name, encoding="utf-8", errors="replace").read().splitlines()[-3:]
            _finish(key, "build failed: %s" % " / ".join(tail))
        return None
    bpy.app.timers.register(wait, first_interval=0.5)


def append(key):
    """The library's objects appended into the preview scene (tracked as its content): [objects]."""
    before = {k: set(getattr(bpy.data, k)) for k in preview_scene.KINDS}
    with bpy.data.libraries.load(path(key), link=False) as (_src, dst):
        dst.collections = [COLLECTION]
    made = [idb for k in preview_scene.KINDS for idb in getattr(bpy.data, k) if idb not in before[k]]
    preview_scene.track(made)
    holder = dst.collections[0] if dst.collections else None
    objects = list(holder.all_objects) if holder is not None else []
    for ob in objects:
        for owner in list(ob.users_collection):
            owner.objects.unlink(ob)
    if holder is not None:
        bpy.data.collections.remove(holder)
    preview_scene.add(objects)
    return objects
