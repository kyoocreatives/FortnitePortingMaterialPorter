"""FP's materials rebuilt exactly from their UE graphs.

FP's material import calls build_exact() once its parameters are merged. The app's bridge (localhost:24320)
serves the material, its graph, functions, textures and parameter collections; build.py (with ue_graph.py)
makes the node trees. When that can't be done (no bridge, Blender before 5.0, failed build) it returns None
and FP builds its own preset material.
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

# state of the current FP import: client (memoises app answers), built materials, shapes new instances can copy
_job = {"key": None, "app": None, "down": False, "built": {}, "shapes": {}, "notes": []}


KEY_OVERLAY = "mp_overlay"    # values the material was built with (JSON), excluding its wrap's
KEY_WRAP = "mp_wrap"          # name of the wrap item over it


def _session(context):
    """State of the current import, reset when a new import (context) starts."""
    if _job["key"] is not context:
        build.begin_session()
        _job.update(key=context, app=AppClient(URL), down=False, built={}, shapes={}, notes=[])
        # texture size cap (Max Texture Size setting), sent to the app with each texture request
        try:
            _job["app"].max_texture = int((getattr(context, "options", None) or {}).get("MaxTextureSize") or 0)
        except (TypeError, ValueError):
            pass
    return _job


def _log(message):
    Log.info("[Material Porter] " + message)


def _digest(text):
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]


def _texture_path(texture):
    return (texture or {}).get("Path")


def _overlay(texture_data, override_parameters, values=None):
    """Values FP puts over the material's own: building texture data (by layer), style parameter overrides,
    and `values` from the fork's map reader (dynamic instances, building texture data, weapon wraps; these set static switches too)."""
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
    # fallbacks (island materials without a graph) from an older fallback builder are rebuilt
    return next((m for m in bpy.data.materials if m.get(build.KEY_PATH) == path
                 and m.get(build.KEY_VARIANT, "") == variant and build.KEY_REPLACES not in m
                 and m.get(build.KEY_REV, 1) >= build.BUILD_REVISION
                 and m.get("mp_fallback", fallback.REVISION) >= fallback.REVISION), None)


def _material(job, entry, obj):
    """Build once per (path, variant) and import. An instance of an already built shape is a copy with its own values and images."""
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
    if mat is None:
        mat, n = build.build_one(dict(entry, target={}, variant=variant), app, [obj] if obj else [])
        if mat.get(build.KEY_SHAPE):
            shapes.setdefault(mat[build.KEY_SHAPE], mat)
        notes += ["%s: %s" % (entry["name"], x) for x in n]
    built[key] = mat
    return mat


def subsurface(context, material_data):
    """Subsurface settings (intensity, scale) from the app options, applied on top of the game's values.

    Skin uses Subsurface Intensity/Scale; shell fur layers and their base (material_porter.shells) use Fur Subsurface Intensity/Scale."""
    options = getattr(context, "options", None) or {}
    prefix = "FurSubsurface" if material_data.get("MPMoves") else "Subsurface"

    def value(key, default, most):
        v = options.get(prefix + key)
        return default if v is None else min(max(0.0, float(v)), most)
    return value("Intensity", 1.0, 1.0), value("Scale", 1.0, 1e4)


def exact_available(context):
    """Whether this import builds exact materials (Blender 5, and the app's bridge hasn't failed it)."""
    return bpy.app.version >= (5, 0, 0) and not _session(context)["down"]


def _sampler_limit():
    """Max image nodes Eevee can draw a material through, or None for no limit.

    OpenGL has 32 texture units, two used by Eevee, so 30; past that the material renders magenta ("too many samplers").
    Vulkan and Metal have no such cap."""
    try:
        import gpu
        backend = gpu.platform.backend_type_get()
    except Exception:
        backend = 'NONE'
    if backend == 'NONE':
        backend = getattr(bpy.context.preferences.system, "gpu_backend", 'OPENGL')
    return 30 if backend == 'OPENGL' else None


def _samplers(mat):
    """Count the image nodes that reach the material output, through groups (what Eevee binds)."""
    seen, count = set(), 0

    def walk(tree, outs):
        nonlocal count
        back = {}
        for l in tree.links:
            back.setdefault(l.to_node.name, []).append(l.from_node)
        todo, done = list(outs), set()
        while todo:
            n = todo.pop()
            if n.name in done:
                continue
            done.add(n.name)
            if n.bl_idname == "ShaderNodeTexImage" and n.image is not None:
                count += 1
            elif n.bl_idname == "ShaderNodeGroup" and n.node_tree is not None and n.node_tree.name not in seen:
                seen.add(n.node_tree.name)
                walk(n.node_tree, [x for x in n.node_tree.nodes if x.bl_idname == "NodeGroupOutput"])
            todo.extend(back.get(n.name, []))
    walk(mat.node_tree, [n for n in mat.node_tree.nodes if n.bl_idname == "ShaderNodeOutputMaterial"])
    return count


def _lightest_layer(obj, layers):
    """The painted layer of a landscape proxy with the least total weight."""
    import numpy as np
    total = {}
    for name in layers:
        a = obj.data.color_attributes.get(name)
        if a is None:
            total[name] = 0.0
            continue
        v = np.empty(len(a.data) * 4, dtype=np.float32)
        a.data.foreach_get("color", v)
        total[name] = float(v[0::4].sum())
    return min(layers, key=lambda n: total[n])


def build_exact(context, material_data, texture_data=None, override_parameters=None, obj=None):
    """The exact Blender material for FP's material data, or None (FP's presets then)."""
    if bpy.app.version < (5, 0, 0):
        return None
    job = _session(context)
    if job["down"]:
        return None
    build.mark_bounds([obj] if obj else [])     # UE's Object Position is per object even for a shared material
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
    # rebuilding a material (wrap.py): reapply the values it was built with
    for kind, values in (material_data.get("MPOverlay") or {}).items():
        overlay[kind] = dict(values, **(overlay.get(kind) or {}))
    base = json.dumps(overlay, sort_keys=True) if overlay else ""
    # wrap values go over those, except textures the material (or its style) sets itself
    # (weapon diffuse, normals, masks, customization mask)
    wrap = material_data.get("MPWrap")
    if wrap:
        own = set(entry.get("textures") or {}) | set(overlay.get("textures") or {})
        for kind, values in _overlay(None, None, wrap).items():
            if kind == "textures":
                values = {k: v for k, v in values.items() if k not in own}
            overlay[kind] = dict(overlay.get(kind) or {}, **values)
    # Rim Light off (default): zero the rim light of character materials (MF_RimV3 baseBrightness)
    if not (getattr(context, "options", None) or {}).get("RimLight"):
        overlay.setdefault("scalars", {})["baseBrightness"] = 0.0
        # fixed, not a control: Rim V3 folds away, and Post FX with it when nothing else is on
        entry["fixed"] = {"baseBrightness": 0.0}
    if overlay:
        for kind, values in overlay.items():
            entry[kind] = dict(entry.get(kind) or {}, **values)
        entry["variant"] = _digest(json.dumps(overlay, sort_keys=True))
    # flipbook sprite: UV0 is the sub-image the particle shows (env.uv)
    if sprite := material_data.get("MPSprite"):
        entry["sprite"] = [float(x) for x in sprite]
        entry["variant"] = _digest("%s %s" % (entry.get("variant", ""), entry["sprite"]))
    # ribbon: particle values are mesh attributes (env._particle_attr)
    if material_data.get("MPRibbon"):
        entry["ribbon"] = True
        entry["variant"] = _digest("%s ribbon" % entry.get("variant", ""))
    # particle effect piece: World Position Offset also moves its vertices (build.build_one)
    if obj is not None and obj.get("mp_effect") in ("Sprite", "Ribbon", "Mesh", "Decal"):
        entry["particle"] = True
        entry["variant"] = _digest("%s particle" % entry.get("variant", ""))
    # shell fur layer or its base (material_porter.shells): World Position Offset puts it where the game draws it
    if material_data.get("MPMoves"):
        entry["moves"] = True
        entry["variant"] = _digest("%s moves" % entry.get("variant", ""))
    # import's subsurface intensity and scale (separate ones for shell fur), multiplied with the game's
    sss = subsurface(context, material_data)
    # shell fur layer (material_porter.shells): scatters fully
    if material_data.get("MPShell"):
        entry["shell"] = True
        entry["variant"] = _digest("%s shell" % entry.get("variant", ""))
    if sss != (1.0, 1.0):
        entry["variant"] = _digest("%s sss %g %g" % ((entry.get("variant", ""),) + sss))
    # Landscape proxy (marked by placement.after_import; FP exports painted layers as colour attributes):
    # one material per set of layers, as UE compiles each component, so other layers' textures fold away
    # (the Ch4 jungle landscape samples 43 textures over all layers, past Eevee's 32, and rendered magenta)
    landscape_base = (entry.get("variant", ""), entry["name"])
    if obj is not None and obj.type == 'MESH' and obj.get("mp_landscape"):
        layers = sorted(a.name for a in obj.data.color_attributes if a.name != "COL0")
        entry["landscape_layers"] = layers
        entry["name"] = "%s (%s)" % (landscape_base[1], "+".join(layers) if layers else "no layers")
        entry["variant"] = _digest("%s layers %s" % (landscape_base[0], " ".join(layers)))
    # lay out each tree when a node editor first shows it (two fifths of build time, cosmetic)
    build.LAZY_LAYOUT = True
    try:
        mat = _material(job, entry, obj)
        # On OpenGL Eevee limits image samplers per material (past it: magenta). A proxy still over the limit
        # drops its least painted layer, repeatedly.
        limit = _sampler_limit() if entry.get("landscape_layers") else None
        while limit and len(entry["landscape_layers"]) > 1 and (samplers := _samplers(mat)) > limit:
            lightest = _lightest_layer(obj, entry["landscape_layers"])
            layers = [l for l in entry["landscape_layers"] if l != lightest]
            _log("%s: %d image samplers over Eevee's %d on OpenGL - its least painted layer left out" % (
                entry["name"], samplers, limit))
            entry["landscape_layers"] = layers
            entry["name"] = "%s (%s)" % (landscape_base[1], "+".join(layers))
            entry["variant"] = _digest("%s layers %s" % (landscape_base[0], " ".join(layers)))
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
        # the root group node is the only one in its tree with those inputs
        for n in mat.node_tree.nodes:
            if n.bl_idname == "ShaderNodeGroup" and build.SUBSURFACE_SCALE in n.inputs and build.SUBSURFACE_INTENSITY in n.inputs:
                n.inputs[build.SUBSURFACE_INTENSITY].default_value = sss[0]
                n.inputs[build.SUBSURFACE_SCALE].default_value = mat[build.KEY_SUBSURFACE] * sss[1]
    # LEGO face: rig placement of the character accents per mouth pose (face_anim.py)
    if rig := material_data.get("MPFaceRig"):
        mat["mp_face_rig"] = rig
    # remember the values under the wrap and the wrap's name, to change or remove it later
    for key, value in ((KEY_OVERLAY, base), (KEY_WRAP, str(wrap.get("Label") or "wrap") if wrap else "")):
        if value:
            mat[key] = value
        elif key in mat:
            del mat[key]
    return mat
