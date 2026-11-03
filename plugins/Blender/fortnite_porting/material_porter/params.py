"""What the editor's Materials tab reads of a built material: its parameters (the root group's inputs) with their UE
names and the master's defaults, the values the build set (Reset to game), its parent shader and its features.
Custom properties only: the trees are the same with or without them."""
import json
import re

import bpy

KEY_UI = "mp_ui"                # root group: {input identifier: [UE name, kind P / V / A, master's default]} (JSON)
KEY_GAME = "mp_game"            # material: {input identifier: value} as the build left it (JSON)
KEY_MASTER = "mp_master"        # material: the parent shader's (master material's) name
KEY_FEATURES = "mp_features"    # material: [Outline, SSS, Emissive, Glass, Hair] it has
# material: {parameter name: value} the user set (the Materials tab), put back after every build; never part of a
# reuse key (a material edited is still the same game material)
KEY_OVERRIDES = "mp_overrides"

FEATURES = ("Outline", "SSS", "Emissive", "Glass", "Hair")
GLASS_MODELS = ("MSM_ThinTranslucent",)
SSS_MODELS = {"MSM_Subsurface", "MSM_SubsurfaceProfile", "MSM_PreintegratedSkin"}
EMISSIVE = re.compile(r"emissive|glow", re.IGNORECASE)


def tag_build(mat, root, env, entry, s, vals, translucent):
    """At the end of build_one: the parameters, the master, the features the graph decides."""
    ui = {}
    for key, (item, _v, _value, _panel) in env._params.items():
        kind, name = key[0], key[3:]
        default = env.graph_defaults.get(("V: " if kind == "A" else key[:3]) + name)
        if kind == "A" and isinstance(default, tuple):
            default = default[3]
        ui[item.identifier] = [name, kind, list(default) if isinstance(default, tuple) else default]
    root[KEY_UI] = json.dumps(ui)
    master = str(entry.get("master") or entry.get("path") or "")
    mat[KEY_MASTER] = master.rsplit("/", 1)[-1].split(".")[0]
    models = set(s.get("models") or ()) | {s["shading"]}
    found = []
    # the emissive output alone says little (cel and rim light shaders light through it): its parameters all on
    glow = [value for key, (_item, _v, value, _panel) in env._params.items()
            if key[0] in "PV" and EMISSIVE.search(key[3:]) and "EyeAdaptation" not in key]
    emissive = vals.get("EmissiveColor")
    if emissive is not None and not emissive.const and glow and all(max(_values(v)[:3]) > 0.0 for v in glow):
        found.append("Emissive")
    if models & SSS_MODELS:
        found.append("SSS")
    if (s["blend"] in translucent and s["shading"] != "MSM_Unlit") or models & set(GLASS_MODELS):
        found.append("Glass")
    if "MSM_Hair" in models or "hair" in mat[KEY_MASTER].lower():     # Fortnite's hair is mostly lit, not MSM_Hair
        found.append("Hair")
    mat[KEY_FEATURES] = found


def _values(v):
    return tuple(v) if isinstance(v, (tuple, list)) else (float(v),)


def add_feature(mat, name):
    have = list(mat.get(KEY_FEATURES) or ())
    if name not in have:
        mat[KEY_FEATURES] = have + [name]


def root_node(mat):
    """The material's group node holding its parameters (the one whose tree has mp_ui), None for others."""
    if mat is None or mat.node_tree is None:
        return None
    return next((n for n in mat.node_tree.nodes if n.bl_idname == "ShaderNodeGroup" and n.node_tree is not None
                 and KEY_UI in n.node_tree), None)


def _plain(v):
    try:
        return [float(x) for x in v]
    except TypeError:
        return float(v)


def snapshot(mat):
    """The group node's values as the build (and the import's subsurface values) left them: Reset to game's."""
    node = root_node(mat)
    if node is None:
        return
    mat[KEY_GAME] = json.dumps({s.identifier: _plain(s.default_value) for s in node.inputs
                                if not s.is_linked and hasattr(s, "default_value")})


def input_names(node):
    """{input identifier: its parameter's name} of a root group node (the UE name, "(A)" for a vector's alpha; a
    fork input its own name). Overrides go by these: a rebuild can number the sockets differently."""
    try:
        ui = json.loads(node.node_tree.get(KEY_UI) or "{}")
    except ValueError:
        ui = {}
    out = {}
    for s in node.inputs:
        ue = ui.get(s.identifier)
        out[s.identifier] = ue[0] + (" (A)" if ue[1] == "A" else "") if ue else s.name
    return out


def overrides(mat):
    try:
        return json.loads(mat.get(KEY_OVERRIDES) or "{}")
    except ValueError:
        return {}


def set_override(mat, name, value):
    """The user's value for a parameter (None: back to the game's)."""
    have = overrides(mat)
    if value is None:
        have.pop(name, None)
    else:
        have[name] = _plain(value)
    if have:
        mat[KEY_OVERRIDES] = json.dumps(have)
    elif KEY_OVERRIDES in mat:
        del mat[KEY_OVERRIDES]


def carry_overrides(mat):
    """A material built again (a new revision, a rebuild) takes the edits of the one it replaces: same game path and
    variant."""
    if KEY_OVERRIDES in mat:
        return
    path, variant = mat.get("mp_path"), mat.get("mp_variant", "")
    old = next((m for m in bpy.data.materials if m != mat and KEY_OVERRIDES in m and m.get("mp_path") == path
                and m.get("mp_variant", "") == variant), None)
    if old is not None:
        mat[KEY_OVERRIDES] = old[KEY_OVERRIDES]


def apply_overrides(mat):
    """The user's values back on the group node (after the build and the import's own values)."""
    have = overrides(mat)
    node = root_node(mat) if have else None
    if node is None:
        return 0
    names, n = input_names(node), 0
    for s in node.inputs:
        v = have.get(names[s.identifier])
        if v is None or s.is_linked or not hasattr(s, "default_value"):
            continue
        try:
            if isinstance(v, list):
                size = len(tuple(s.default_value))
                s.default_value = tuple(v[:size]) + tuple(s.default_value)[len(v):size]
            else:
                s.default_value = type(s.default_value)(v)
            n += 1
        except (TypeError, ValueError):     # the input changed type since: the game's value stands
            pass
    return n


def features(mat):
    """The material's features: the build's tags."""
    have = list(mat.get(KEY_FEATURES) or ())
    if any(k in str(mat.get("mp_path") or "") for k in ("Outline", "InvHull")) and "Outline" not in have:
        have.append("Outline")      # built before the tag, or not through an overlay
    return [f for f in FEATURES if f in have]
