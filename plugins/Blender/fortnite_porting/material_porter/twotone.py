"""Two-tone rim (the Materials tab): an anime cel material rebuilt on the game's two-tone rim master, the one
Sabotage's body uses (M_AnimeShader_Body_TwotoneRimLight: the anime master plus MF_RimLight_TwoTone over Emissive).
That master takes every parameter of the anime one, so the material keeps its values; the rim's own come from the
master's defaults. Turning it off rebuilds on the material's own master.
"""
import json
import types

import bpy

from . import build, hook, outlines, params

MASTER = "/Game/Global/Materials/Character_Parent_2023/Anime_Parent_2023/TwotoneRim/M_AnimeShader_Body_TwotoneRimLight"
ANIME = "M_AnimeShader_2023"
KEY = "mp_twotone"     # built on the two-tone master by this option (not by the game)


def is_anime(mat):
    """An exact material on the anime master, or put on the two-tone one by this option."""
    return bool(mat.get(build.KEY_PATH)) and (ANIME in str(mat.get(params.KEY_MASTER, "")) or bool(mat.get(KEY)))


def switch(mats, on):
    """Rebuild each material on (or off) the two-tone master and put it in every slot using it. Returns
    ({old name: new name}, names not built)."""
    job = types.SimpleNamespace(options={'RimLight': False}, type=types.SimpleNamespace(name='MESH'))
    done, failed = {}, []
    for mat in mats:
        if bool(mat.get(KEY)) == on or not is_anime(mat):
            continue
        users = [(o, s) for o in bpy.data.objects for s in getattr(o, "material_slots", ()) if s.material == mat]
        data = {"Path": mat[build.KEY_PATH], "Name": mat.get("OriginalName") or mat.name, "MPTwoTone": on}
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
