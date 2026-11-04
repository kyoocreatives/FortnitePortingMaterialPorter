"""Rebuild On (the Materials tab, the Files tab): a material rebuilt on another game material's shader, keeping its own
values. The other material gives the master (its graph) and its own values under the material's; textures stay the
material's (another skin's maps don't fit its UVs). Going back rebuilds on the material's own shader.
"""
import json
import types

import bpy

from . import build, hook, outlines, params

KEY = "mp_rebase"      # the game material a material was rebuilt on (absent: its own)
TWO_TONE = "/Game/Global/Materials/Character_Parent_2023/Anime_Parent_2023/TwotoneRim/M_AnimeShader_Body_TwotoneRimLight"
KINDS = ("switches", "scalars", "vectors", "masks")     # taken from the other material, under the material's own


def onto(entry, other):
    """The material's app entry put on the other material's shader (hook.build_exact)."""
    entry["master"], entry["graph"] = other["master"], other["graph"]
    for kind in KINDS:
        entry[kind] = dict(other.get(kind) or {}, **(entry.get(kind) or {}))
    entry["variant"] = hook._digest("%s rebase %s" % (entry.get("variant", ""), other["path"]))
    # the two-tone rim draws where the FX map's red allows: without one of its own, the master's constant
    if "TwotoneRim" in other["master"] and "FX_Map" not in (entry.get("textures") or {}):
        entry["scalars"] = dict(entry["scalars"], UseFXMap=0.0)


def current(mat):
    """The game material it was rebuilt on, else None (1.13.9-10's Two-Tone Rim marked its own)."""
    return mat.get(KEY) or (TWO_TONE if mat.get("mp_twotone") else None)


def _job(mat):
    """The import's options the material was built with (material_choice's MPSubsurface: skin, base, scale, colour,
    cosmetic), so going back finds the import's own build."""
    options = {"RimLight": bool(mat.get("MPRimLight"))}
    sss = str(mat.get("MPSubsurface") or "").split()
    if len(sss) == 5:
        options.update(zip(("SubsurfaceIntensity", "BaseSubsurface", "SubsurfaceScale", "ProfileColour"), map(float, sss[:4])))
    kind = "OUTFIT" if sss[4:] == ["True"] else "MESH"
    return types.SimpleNamespace(options=options, type=types.SimpleNamespace(name=kind))


def rebuild(mats, path):
    """Rebuild each exact material on the game material at `path` (None: back on its own) and put it in every slot
    using it. Returns ({old name: new name}, names not built)."""
    done, failed = {}, []
    for mat in mats:
        if not mat.get(build.KEY_PATH) or current(mat) == path or mat.get(build.KEY_PATH) == path:
            continue
        job = _job(mat)
        users = [(o, s) for o in bpy.data.objects for s in getattr(o, "material_slots", ()) if s.material == mat]
        data = {"Path": mat[build.KEY_PATH], "Name": mat.get("OriginalName") or mat.name, "MPRebase": path}
        if overlay := mat.get(hook.KEY_OVERLAY):
            data["MPOverlay"] = json.loads(overlay)
        new = hook.build_exact(job, data, obj=users[0][0] if users else None)
        if new is None:
            failed.append(mat.name)
            continue
        for key in ("Hash", "OriginalName", "MPRimLight", "MPSubsurface", params.KEY_OVERRIDES):
            if key in mat and key not in new:
                new[key] = mat[key]
        params.apply_overrides(new)     # the user's edits: a new variant doesn't find them by itself
        for _o, s in users:
            s.material = new
        outlines.retarget(mat, new)
        done[mat.name] = new.name
    return done, failed
