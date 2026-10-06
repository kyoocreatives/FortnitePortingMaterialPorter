"""A level's placed Niagara effects (Exports[0]["Effects"], the Material Porter fork's map reader): each
system's own effect export, fetched from the app and replayed where the level places it.

An entry is {"Name", "Location", "Rotation", "Scale", "System", "Parameters"}. For each one the app's
bridge is asked for the system's Effect export (the Effects tab's: fork-export-asset?type=Effect),
once per system and import, and that export is imported as the tab imports it (the same context,
effects.make / effects.finish / effect_replay.play), under an empty "Effect <system>" at the entry's
transform: the replay sees the effect where it stands (a world-space emitter's particles are in the
world's frame, as in the game), and the entry's user parameters are set on the effect's root before it
plays (its "User.<name>" properties, which Replay Effect reads again).

A system the replay can't draw (no emitter it plays: GPU emitters it can't approximate) is logged and
counted and nothing of it is imported; one that plays nothing under its parameters stays, hidden, as
the effects tab leaves it, for the user to set and replay. A bridge that fails logs and skips.
"""
import http.client
import json
import os
import time
import traceback
import urllib.parse

import bpy

from . import effect_replay, effects, hook
from .app_client import AppError

CONNECT_TIMEOUT = 3.0       # seconds: an app that's there answers at once
READ_TIMEOUT = 900.0        # an effect's export can take the app a while (its materials, its scripts)

_PLACED = {}                # the import context's class -> the class that puts an effect under a placement


def _name(path):
    """A system's name: the last part of its object path."""
    return str(path).replace("\\", "/").rstrip("/").rsplit("/", 1)[-1].split(".")[0] or "Effect"


def _fetch(system):
    """The system's Effect export (the bridge's JSON text), or an AppError: down is True on it when the app
    isn't answering at all."""
    parts = urllib.parse.urlsplit(hook.URL)
    connection = http.client.HTTPConnection(parts.hostname or "localhost", parts.port or 80, timeout=CONNECT_TIMEOUT)
    try:
        try:
            connection.connect()
        except OSError as e:
            error = AppError("the Material Porter app isn't answering at %s (%s)" % (hook.URL, e))
            error.down = True
            raise error
        connection.sock.settimeout(READ_TIMEOUT)
        try:
            connection.request("GET", "/fork-export-asset?" + urllib.parse.urlencode({"path": system, "type": "Effect"}))
            response = connection.getresponse()
            body = response.read()
        except (http.client.HTTPException, OSError) as e:
            raise AppError("the app's answer broke off (%s)" % e)
    finally:
        connection.close()
    if response.status != 200:
        try:
            message = json.loads(body.decode("utf-8")).get("error", str(response.status))
        except Exception:
            message = "HTTP %d" % response.status
        raise AppError(str(message))
    return body.decode("utf-8")


def _placed_class(base):
    """The import context's class with its collection the level's and its effect's root under a placement:
    the effect is imported as its tab imports it, in place."""
    if base not in _PLACED:
        class Placed(base):
            # (import_mesh_data makes the context's own collection: the level's takes its place)
            collection = property(lambda self: self.mp_collection, lambda self, value: None)

            def import_model(self, mesh, parent=None, *args, **kwargs):
                return super().import_model(mesh, self.mp_place if parent is None else parent, *args, **kwargs)
        _PLACED[base] = Placed
    return _PLACED[base]


def _user_parameters(entry):
    """The entry's user parameters as the effect's root properties ("User.<name>": a number, a bool, or floats)."""
    found = {}
    for name, value in (entry.get("Parameters") or {}).items():
        if isinstance(value, dict):         # (a colour or a vector the exporter spelled by its parts)
            value = [value[k] for k in ("X", "Y", "Z", "W") if k in value] or [value[k] for k in ("R", "G", "B", "A") if k in value]
        if isinstance(value, (list, tuple)):
            value = [float(v) for v in value]
        elif not isinstance(value, (bool, int, float)):
            continue
        name = str(name)
        found[name if name.startswith(("User.", "NPC.")) else "User." + name] = value
    return found


def _place(context, entry, name):
    """The empty an effect goes under: at the entry's transform (the level's, as a mesh's), in the level's collection."""
    from ..processing.utils import make_euler, make_vector
    place = bpy.data.objects.new("Effect " + name, None)
    place.empty_display_type = 'PLAIN_AXES'
    place.empty_display_size = 0.5
    place.rotation_euler = make_euler(entry.get("Rotation") or {"Pitch": 0.0, "Yaw": 0.0, "Roll": 0.0})
    place.location = make_vector(entry.get("Location") or {"X": 0.0, "Y": 0.0, "Z": 0.0}, unreal_coords_correction=True) * context.scale
    place.scale = make_vector(entry.get("Scale") or {"X": 1.0, "Y": 1.0, "Z": 1.0})
    place["mp_level_effect"] = str(entry.get("Name") or "")
    place["mp_level_system"] = str(entry.get("System") or "")
    context.collection.objects.link(place)
    return place


def _import(context, job, payload, entry, name):
    """One placed effect: its export imported under its placement. Returns (the placement, the effect's
    root, what the replay drew: the number of particle objects), or raises."""
    export = payload["Exports"][0]
    root_mesh = export["Meshes"][0]
    params = _user_parameters(entry)
    if params:
        fx = root_mesh.setdefault("MPEffect", {})
        fx["User"] = dict(fx.get("User") or {}, **params)
    place = _place(context, entry, name)
    # the effect's own context (its settings the level's: the same scale, the same materials), its
    # materials built in the level's job (the app's answers, what the level built already)
    settings = dict(context.options)
    settings["ImportIntoCollection"] = False
    sub = _placed_class(type(context))({"Settings": settings, "AssetsRoot": context.assets_root})
    sub.mp_place, sub.mp_collection = place, context.collection
    known = set(bpy.data.objects.keys())
    job["key"] = sub
    try:
        sub.run(export)
    except Exception:
        # what it made so far goes with it
        for o in [o for o in bpy.data.objects if o.name not in known]:
            bpy.data.objects.remove(o, do_unlink=True)
        bpy.data.objects.remove(place, do_unlink=True)
        raise
    finally:
        job["key"] = context
    made = [o for o in bpy.data.objects if o.name not in known and o is not place]
    roots = [o for o in made if o.get(effects.KEY) == "System" and o.parent is place]
    root = roots[0] if roots else None
    drew = sum(1 for o in made if o.get(effects.KEY) == "Particles")
    # (a world-space emitter's particles stand where the replay put them, not under the effect: they follow the
    # placement now, as they are)
    inverse = place.matrix_basis.inverted_safe()
    for o in made:
        if o.get(effects.KEY) == "Particles" and o.parent is None:
            o.parent = place
            o.matrix_parent_inverse = inverse
    if root is not None:
        # a played piece is out of sight already (its particles draw it): what's left in sight is what the replay
        # didn't play (an emitter that spawned nothing, a mesh its particles don't draw), laid out in a row to pick
        # from - out of sight too, at the effect
        for node in root.children:
            node.location = (0.0, 0.0, 0.0)
            for piece in node.children:
                if piece.type in ('MESH', 'LIGHT') and piece.get(effects.KEY) != "Particles" and not piece.hide_render:
                    piece.hide_render = piece.hide_viewport = True
    # the user parameters the system doesn't take (effect_replay drops them from the root)
    if root is not None and params:
        have = {k.lower() for k in root.keys()}
        ignored = [k for k in params if k.lower() not in have]
        if ignored:
            hook._log("%s: parameters this system doesn't take: %s" % (place.name, ", ".join(sorted(ignored)[:8])))
    return place, root, drew


def import_effects(context, effects_list):
    """Place `effects_list` (the payload's list, may be None) into the import's collection."""
    if not effects_list or getattr(context, "mp_place", None) is not None:      # (an effect's own context places nothing)
        return
    job = hook._session(context)
    t0 = time.perf_counter()
    cache = {}              # system path -> its export's text, or the error (an AppError) it came with
    played, undrawn, silent, skipped, failed, particles = 0, {}, {}, {}, {}, 0
    seconds = {}            # per system: what its placements took
    down = job["down"]
    for entry in effects_list:
        system = entry.get("System")
        if not system:
            continue
        name = _name(system)
        if down:
            skipped[name] = skipped.get(name, 0) + 1
            continue
        if system not in cache:
            t = time.perf_counter()
            try:
                cache[system] = _fetch(system)
                hook._log("Effect %s: fetched from the app (%d KB, %.1f s)" % (name, len(cache[system]) // 1024, time.perf_counter() - t))
            except AppError as e:
                cache[system] = e
                if getattr(e, "down", False):
                    down = True
                    hook._log("Effects: the app's bridge isn't answering at %s: no placed effects this import" % hook.URL)
                else:
                    hook._log("Effect %s (%s): not fetched (%s) - skipped" % (name, system, e))
        text = cache[system]
        if isinstance(text, AppError):
            skipped[name] = skipped.get(name, 0) + 1
            continue
        try:
            payload = json.loads(text)
            fx = ((payload.get("Exports") or [{}])[0].get("Meshes") or [{}])[0].get("MPEffect") or {}
            if not fx.get("Exports"):
                # no emitter the replay plays (GPU emitters it can't approximate): nothing of it is imported
                if name not in undrawn:
                    hook._log("Effect %s: not drawn (no emitter the replay plays: GPU only, or none)" % name)
                undrawn[name] = undrawn.get(name, 0) + 1
                continue
            t = time.perf_counter()
            place, root, drew = _import(context, job, payload, entry, name)
            seconds[name] = seconds.get(name, 0.0) + time.perf_counter() - t
        except Exception as e:
            at = traceback.extract_tb(e.__traceback__)[-1]
            hook._log("Effect %s (%s): not imported (%s: %s, at %s:%d)" % (
                name, entry.get("Name"), type(e).__name__, e, os.path.basename(at.filename), at.lineno))
            failed[name] = failed.get(name, 0) + 1
            continue
        if drew:
            played += 1
            particles += drew
        else:
            silent[name] = silent.get(name, 0) + 1
            hook._log("%s: nothing played in the replay: its pieces hidden (set its User.* properties on %s, then Replay Effect)" % (
                place.name, root.name if root is not None else "its root"))

    if seconds:
        hook._log("Effects: seconds per system: " + ", ".join("%s %.1f" % kv for kv in sorted(seconds.items(), key=lambda kv: -kv[1])[:12]))

    def count(d):
        return "%d (%s)" % (sum(d.values()), ", ".join("%s x%d" % kv for kv in sorted(d.items())[:12])) if d else "0"
    hook._log("Effects: %d of %d placed in %.1f s (%d systems fetched): %d played (%d particle objects), %d placed but silent %s, "
              "not drawn %s, skipped %s, failed %s" % (
                  played + sum(silent.values()), len(effects_list), time.perf_counter() - t0,
                  sum(1 for v in cache.values() if not isinstance(v, AppError)), played, particles,
                  sum(silent.values()), "(%s)" % ", ".join(sorted(silent)[:12]) if silent else "",
                  count(undrawn), count(skipped), count(failed)))
