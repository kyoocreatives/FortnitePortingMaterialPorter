"""Material Porter fork: FP's materials rebuilt exactly from their UE graphs.

FP's material import calls build_exact() once its parameters are merged. The
FP app's Material Porter bridge (localhost:24320) describes the material and
serves its graph, functions, textures and parameter collections; the builder
(build.py, translated by ue_graph.py) makes the node trees. When that can't
be done (no bridge, Blender before 5.0, a failed build) it returns None and
FP builds its own preset material as usual.
"""
import hashlib
import json
import os
import traceback

import bpy

from . import build, fallback
from .app_client import AppClient, AppError
from ..logger import Log

URL = os.environ.get("MATERIAL_PORTER_BRIDGE", "http://localhost:24320")

# one FP import at a time: its client (a memo of what the app answered), what
# it built, and the shapes new instances can copy
_job = {"key": None, "app": None, "down": False, "built": {}, "shapes": {}, "notes": []}


KEY_OVERLAY = "mp_overlay"    # the values a material was built with (JSON), but for its wrap's
KEY_WRAP = "mp_wrap"          # the wrap over it (its item's name)


def _session(context):
    """The current import's state, begun afresh for each import (context)."""
    if _job["key"] is not context:
        build.begin_session()
        _job.update(key=context, app=AppClient(URL), down=False, built={}, shapes={}, notes=[])
    return _job


def _log(message):
    Log.info("[Material Porter] " + message)


def _texture_path(texture):
    return (texture or {}).get("Path")


def _overlay(texture_data, override_parameters, values=None):
    """What FP puts over the material's own values: a building's texture data
    (by layer, like FP), a style's parameter overrides, and the fork map
    reader's values (a dynamic instance's, a building's texture data, a
    weapon wrap's: those set static switches too)."""
    textures, scalars, vectors, switches = {}, {}, {}, {}
    if values:
        textures.update(values.get("Textures") or {})
        scalars.update(values.get("Scalars") or {})
        vectors.update(values.get("Vectors") or {})
        switches.update(values.get("Switches") or {})
    for data in texture_data or []:
        index = data.get("Index") or 0
        ts = "_Texture_%d" % (index + 1) if index > 0 else ""
        ss = "_%d" % (index + 1) if index > 0 else ""
        for key, name in (("Diffuse", "Diffuse" + ts), ("Normal", "Normals" + ts), ("Specular", "SpecularMasks" + ss)):
            if path := _texture_path(data.get(key)):
                textures[name] = path
    for parameters in override_parameters or []:
        for t in parameters.get("Textures") or []:
            if path := _texture_path(t.get("Texture")):
                textures[t.get("Name")] = path
        for s in parameters.get("Scalars") or []:
            scalars[s.get("Name")] = float(s.get("Value") or 0.0)
        for v in parameters.get("Vectors") or []:
            c = v.get("Value") or {}
            vectors[v.get("Name")] = [c.get("R", 0.0), c.get("G", 0.0), c.get("B", 0.0), c.get("A", 1.0)]
    return {k: v for k, v in (("textures", textures), ("scalars", scalars), ("vectors", vectors),
                              ("switches", switches)) if v}


def _built(path, variant):
    # a fallback (an island's material, no graph) an older fallback builder made is built again
    return next((m for m in bpy.data.materials if m.get(build.KEY_PATH) == path
                 and m.get(build.KEY_VARIANT, "") == variant and build.KEY_REPLACES not in m
                 and m.get(build.KEY_REV, 1) >= build.BUILD_REVISION
                 and m.get("mp_fallback", fallback.REVISION) >= fallback.REVISION), None)


def _material(job, entry, obj):
    """Built once per (path, variant) and import; an instance of a shape
    already built is a copy of it with its own values and images."""
    app, built, shapes, notes = job["app"], job["built"], job["shapes"], job["notes"]
    variant = entry.get("variant", "")
    key = (entry["path"], variant)
    if key in built:
        return built[key]
    mat = _built(*key)
    if mat is None:
        shape = build.shape_key(entry)
        src = shapes.get(shape) or build.find_shape(shape)
        if src is not None:
            try:
                made = build.build_like(src, dict(entry, target={}, variant=variant), app)
                if made is not None:
                    mat = made[0]
                    shapes[shape] = src
            except Exception as e:
                notes.append("%s: copy of %s failed (%s), built instead" % (entry["name"], src.name, e))
                mat = None
    if mat is None:
        mat, n = build.build_one(dict(entry, target={}, variant=variant), app, [obj] if obj else [])
        if mat.get(build.KEY_SHAPE):
            shapes.setdefault(mat[build.KEY_SHAPE], mat)
        notes += ["%s: %s" % (entry["name"], x) for x in n]
    built[key] = mat
    return mat


def subsurface(context, material_data):
    """The FP app's subsurface settings for a material: (how much light scatters where it scatters
    any, times the game's distance). Skin: Subsurface Intensity and Scale; a shell fur layer and its
    base (material_porter.shells): Fur Subsurface Intensity and Scale."""
    options = getattr(context, "options", None) or {}
    prefix = "FurSubsurface" if material_data.get("MPMoves") else "Subsurface"

    def value(key, default, most):
        v = options.get(prefix + key)
        return default if v is None else min(max(0.0, float(v)), most)
    return value("Intensity", 1.0, 1.0), value("Scale", 1.0, 1e4)


def exact_available(context):
    """Whether this import builds exact materials (Blender 5, and the app's bridge hasn't failed it)."""
    return bpy.app.version >= (5, 0, 0) and not _session(context)["down"]


def build_exact(context, material_data, texture_data=None, override_parameters=None, obj=None):
    """The exact Blender material for FP's material data, or None (FP's presets then)."""
    if bpy.app.version < (5, 0, 0):
        return None
    job = _session(context)
    if job["down"]:
        return None
    build.mark_bounds([obj] if obj else [])     # (UE's Object Position: each object's own, a shared material)
    path = material_data.get("Path")
    if not path:
        return None
    try:
        entry = dict(job["app"].get("material", path))
    except AppError as e:
        if "isn't answering" in str(e):
            job["down"] = True
            _log("the FP app's bridge isn't answering at %s: FP's own materials this import" % URL)
        else:
            _log("%s: %s - FP's own material" % (material_data.get("Name"), e))
        return None
    overlay = _overlay(texture_data, override_parameters, material_data.get("MPValues"))
    # a material built before, built again (wrap.py): what it was built with then
    for kind, values in (material_data.get("MPOverlay") or {}).items():
        overlay[kind] = dict(values, **(overlay.get(kind) or {}))
    base = json.dumps(overlay, sort_keys=True) if overlay else ""
    # a wrap: its values over those, but for the textures the material (or its style) sets itself -
    # a weapon's diffuse, normals, masks and customization mask stay its own
    wrap = material_data.get("MPWrap")
    if wrap:
        own = set(entry.get("textures") or {}) | set(overlay.get("textures") or {})
        for kind, values in _overlay(None, None, wrap).items():
            if kind == "textures":
                values = {k: v for k, v in values.items() if k not in own}
            overlay[kind] = dict(overlay.get(kind) or {}, **values)
    # "Rim Light" off (the default): the character materials' rim light (MF_RimV3's baseBrightness) at 0
    if not (getattr(context, "options", None) or {}).get("RimLight"):
        overlay.setdefault("scalars", {})["baseBrightness"] = 0.0
        # (fixed, not a control: Rim V3 folds away, and Post FX with it when nothing else is on)
        entry["fixed"] = {"baseBrightness": 0.0}
    if overlay:
        for kind, values in overlay.items():
            entry[kind] = dict(entry.get(kind) or {}, **values)
        entry["variant"] = hashlib.sha1(json.dumps(overlay, sort_keys=True).encode("utf-8")).hexdigest()[:8]
    # a sprite with a flipbook: its UV0 is the sub-image its particle shows (env.uv)
    if sprite := material_data.get("MPSprite"):
        entry["sprite"] = [float(x) for x in sprite]
        entry["variant"] = hashlib.sha1(("%s %s" % (entry.get("variant", ""), entry["sprite"])).encode("utf-8")).hexdigest()[:8]
    # a ribbon's: its particles' values are its mesh's attributes (env._particle_attr)
    if material_data.get("MPRibbon"):
        entry["ribbon"] = True
        entry["variant"] = hashlib.sha1(("%s ribbon" % entry.get("variant", "")).encode("utf-8")).hexdigest()[:8]
    # a particle effect's piece: its material's World Position Offset moves its vertices too (build.build_one)
    if obj is not None and obj.get("mp_effect") in ("Sprite", "Ribbon", "Mesh", "Decal"):
        entry["particle"] = True
        entry["variant"] = hashlib.sha1(("%s particle" % entry.get("variant", "")).encode("utf-8")).hexdigest()[:8]
    # a shell fur layer's or its base's (material_porter.shells): its World Position Offset puts it
    # where the game draws it
    if material_data.get("MPMoves"):
        entry["moves"] = True
        entry["variant"] = hashlib.sha1(("%s moves" % entry.get("variant", "")).encode("utf-8")).hexdigest()[:8]
    # the import's subsurface intensity and scale (shell fur's own): times the game's
    sss = subsurface(context, material_data)
    # a shell fur layer (material_porter.shells): it all scatters, as fur
    if material_data.get("MPShell"):
        entry["shell"] = True
        entry["variant"] = hashlib.sha1(("%s shell" % entry.get("variant", "")).encode("utf-8")).hexdigest()[:8]
    if sss != (1.0, 1.0):
        entry["variant"] = hashlib.sha1(("%s sss %g %g" % ((entry.get("variant", ""),) + sss)).encode("utf-8")).hexdigest()[:8]
    # each tree laid out when a node editor first shows it: two fifths of a build, and cosmetic
    build.LAZY_LAYOUT = True
    try:
        mat = _material(job, entry, obj)
    except Exception as e:
        at = traceback.extract_tb(e.__traceback__)[-1]
        _log("%s: not built (%s: %s, at %s:%d) - FP's own material" % (
            material_data.get("Name"), type(e).__name__, e, os.path.basename(at.filename), at.lineno))
        return None
    finally:
        build.LAZY_LAYOUT = False
    for note in job["notes"]:
        _log(note)
    job["notes"].clear()
    if build.KEY_SUBSURFACE in mat:
        # (its root group's node: the only one in its own tree with those inputs)
        for n in mat.node_tree.nodes:
            if n.bl_idname == "ShaderNodeGroup" and build.SUBSURFACE_SCALE in n.inputs                     and build.SUBSURFACE_INTENSITY in n.inputs:
                n.inputs[build.SUBSURFACE_INTENSITY].default_value = sss[0]
                n.inputs[build.SUBSURFACE_SCALE].default_value = mat[build.KEY_SUBSURFACE] * sss[1]
    # a LEGO figure's face: where its rig puts the character accents for each mouth pose (face_anim.py)
    if rig := material_data.get("MPFaceRig"):
        mat["mp_face_rig"] = rig
    # what it was built with under its wrap, and the wrap's name: to change or remove the wrap later
    for key, value in ((KEY_OVERLAY, base), (KEY_WRAP, str(wrap.get("Label") or "wrap") if wrap else "")):
        if value:
            mat[key] = value
        elif key in mat:
            del mat[key]
    return mat
