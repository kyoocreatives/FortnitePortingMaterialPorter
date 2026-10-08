"""A material whose master was cooked without its editor graph (a UEFN
island's own materials ship that way): nothing to translate, so it's built
the way FortnitePorting builds everything - a Principled BSDF fed by the
textures and values - with the FP Material Fixer's rules (fpisland) for what
each one is. A texture's role comes from its name first (T_X_BC, "BaseColor",
"..._OcclusionRoughnessMetallic", a Unity MaskMap), then its parameter's; a
lone texture nothing names is the base colour. Colours: the first colour
parameter the fixer reads as a base colour (Color, MainColor, Param...), one
brighter than 1 as a glow, a "Black" material black, a light's colour as its
emission, glass see-through. The textures nothing claims wait in an "Unused
Textures" frame, to wire by hand. The material says it's approximate
(mp_fallback, its value this builder's revision).

The master's cooked shader map says more than its names do (entry["shader"], from the app): the
textures the compiled shader really samples (a hard-coded one by its path, a parameter's through
the instance chain's value), the vector and scalar parameters it reads, and the folded value of a
tint times its brightness. When it's there, only the textures it samples are wired (the roles
still from texture and parameter names, the first layer's before a damage or dirt layer's), the
colour is picked among the vectors it reads, and the textures it never samples wait in a frame of
their own."""
import re
from collections import Counter

import bpy

# 2: the FP Material Fixer's texture names and colour rules
# 3: the textures an island master's graph samples (its package's imports), which came through empty
# 4: the compiled shader's own textures, colour parameters and folded tints (its shader map), the rest in a frame of their own
REVISION = 4

# the textures the shader never samples wait in a frame of their own (False: they aren't loaded at all, only counted in the notes)
PARK_UNSAMPLED = False

# a texture's name -> its role (the FP Material Fixer's img_class)
def _role_of_texture(path):
    s = path.rsplit("/", 1)[-1].split(".")[0].lower()
    toks = s.split("_")
    tok = toks[-1]
    if tok == "maskmap":
        return "unitymask"          # Unity HDRP: R metal, G AO, A smoothness
    if "metallicsmoothness" in s:
        return "metalsmooth"        # Unity: R metal, A smoothness
    if tok in ("h", "height", "hm", "disp", "displacement") or "height" in s or "caustic" in s:
        return "height"             # known, so never taken for a base colour; not wired
    if tok == "basemap" or ("color" in toks and "normal" not in s):
        return "base"
    if "occlusionroughnessmetallic" in s or tok in ("orm", "orme", "arm") or "_orm_" in s:
        return "orm"
    if tok == "rma":
        return "rma"
    if "normal" in s or tok in ("n", "nm", "nrm", "norm", "nor"):
        return "normal"
    if tok in ("s", "srm", "smr", "spec", "specular") or "specularmask" in s:
        return "specmasks"
    if "roughness" in s or tok in ("r", "rough"):
        return "roughness"
    if "metallic" in s or "metalness" in s or tok == "metal":
        return "metallic"
    if "opacity" in s or tok in ("alpha", "opacity", "op"):
        return "opacity"
    if "emissive" in s or "emission" in s or tok in ("e", "emissive", "emit"):
        return "emissive"
    if tok in ("ao", "occlusion") or "ambientocclusion" in s:
        return "ao"
    if any(k in s for k in ("basecolor", "base_color", "albedo", "diffuse")) or tok in ("d", "bc", "c", "co", "col", "color", "diff"):
        return "base"
    if tok in ("m", "msk", "mask", "masks"):
        return "mask?"              # weak: Fortnite's mask or a metallic, the parameter decides
    return None


# a parameter's name -> its role (the FP Material Fixer's label_class)
def _role_of_param(name):
    n = name.lower()
    if "emis" in n or n == "e" or "glow" in n:
        return "emissive"
    if "normal" in n:
        return "normal"
    if n in ("rma", "base rma"):
        return "rma"
    if n in ("orm", "orme") or "ao_roughness_metallic" in n or "occlusionroughnessmetallic" in n:
        return "orm"
    if "specularmask" in n or "specular mask" in n or n in ("smr_texture", "srm", "smr") or "specularroughness" in n or n.startswith("specular"):
        return "specmasks"
    if "roughness" in n:
        return "roughness"
    if "metallic" in n or "metalness" in n:
        return "metallic"
    if "opacity" in n or n == "alpha":
        return "opacity"
    if any(k in n for k in ("diffuse", "difuse", "albedo", "basecolor", "base color", "base_color", "colormap")) or n in ("bc", "color", "texture"):
        return "base"
    return None


def _role(name, path):
    by_texture = _role_of_texture(path)
    if by_texture == "mask?":
        return _role_of_param(name)
    return by_texture or _role_of_param(name)


# the colour parameters a flat material's colour is in, first found wins (the fixer's DIFFUSE_KEYS)
_BASE_COLOURS = ("color", "base color", "basecolor", "maincolor", "diffusecolor", "texturecolor", "albedo", "colour", "param")
# a colour tinting the base texture
_TINTS = ("maincolor", "color", "basecolor", "base color", "tint", "albedo", "diffusecolor", "diffuse color", "colour")

# words that make a parameter belong to a secondary layer or effect, not the surface itself
_SECONDARY = {"damage", "dirt", "detail", "wear", "snow", "moss", "top", "second", "extra", "overlay", "blend", "decal",
              "wet", "rain", "puddle", "macro", "mask", "gradient", "outline", "fresnel", "rim", "shadow", "emissive",
              "emission", "glow", "light", "sss", "subsurface", "spec", "specular", "fog", "sky", "edge", "line"}
# a scalar that scales a colour (a tint's brightness)
_BRIGHTNESS = ("bright", "intens", "mult", "strength", "boost", "gain", "exposure", "power", "scale")


def _norm(path):
    """A texture's path without its object name ("/G/T_A.T_A" and "/G/T_A.0" are one), lower case."""
    head, _, tail = path.rpartition("/")
    return (head + "/" + tail.split(".")[0]).lower()


def _tokens(name):
    """A parameter's words: "L2AlbedoT" -> l2, albedo, t; "Layer 1 Normal" -> layer, 1, normal."""
    spaced = re.sub(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Za-z])", " ", name)
    return [t for t in re.split(r"[^A-Za-z0-9]+", spaced.lower()) if t]


def _secondary(name):
    """0 for the surface's own, 1 for a secondary layer's or effect's (damage, dirt, layer 2...)."""
    toks = _tokens(name)
    if any(t in _SECONDARY for t in toks):
        return 1
    return 1 if any(len(t) == 1 and t in "23456789" for t in toks) else 0


def build_fallback(entry, app, objects, env_cls, settings, keys):
    """(material, notes). keys: build's (PREFIX, KEY_PATH, KEY_REV, BUILD_REVISION, KEY_VARIANT)."""
    prefix, key_path, key_rev, revision, key_variant = keys
    mat = bpy.data.materials.new(prefix + entry["name"])
    nt = mat.node_tree
    nt.nodes.clear()
    N, L = nt.nodes, nt.links
    env = env_cls(app, entry, objects)
    s = settings(entry)
    out = N.new("ShaderNodeOutputMaterial")
    out.location = (400, 0)
    bsdf = N.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (100, 0)
    L.new(bsdf.outputs[0], out.inputs["Surface"])

    textures = dict(entry.get("textures") or {})
    scalars = entry.get("scalars") or {}
    vectors = entry.get("vectors") or {}
    shader = entry.get("shader") if isinstance(entry.get("shader"), dict) else None
    what = (entry["name"] + " " + " ".join(entry.get("chain") or [])).lower()
    fixed = [(p.rsplit("/", 1)[-1].split(".")[0], p, False) for p in entry.get("referenced_textures") or [] if p not in textures.values()]
    skipped = []
    if shader is not None and shader.get("textures") is not None:
        # the textures the compiled shader samples: a hard-coded one by its path, a parameter's by the
        # chain's value (the master's default texture where the chain sets none). Only they are wired.
        bound, other, seen = [], [], set()
        defaults, shared = {}, Counter(t.get("param") for t in shader["textures"] if t.get("param"))
        for t in shader["textures"]:
            if t.get("param") and t.get("path"):
                defaults.setdefault(t["param"], set()).add(_norm(t["path"]))
        for t in shader["textures"]:
            param = t.get("param")
            chain = textures.get(param) if param else None
            if chain and _norm(chain) in defaults.get(param, ()):
                chain = None     # the master's own default (a name two parameters share, "Param", holds one value for both)
            path = chain or t.get("path")
            if not path or _norm(path) in seen:
                continue
            seen.add(_norm(path))
            # a name two parameters share, or the editor's default ("Param", "Param_1"), says nothing: the texture's own
            label = param if param and shared[param] == 1 and not re.fullmatch(r"param(_\d+)?", param.lower()) else path.rsplit("/", 1)[-1].split(".")[0]
            (bound if (t.get("kind") or "2D") == "2D" else other).append((label, path, bool(param)))
        bound.sort(key=lambda b: _secondary(b[0]))     # the first layer's before a damage or dirt layer's
        # the master's other textures: never read by the shader
        for name, path in [(n, p) for n, p in sorted(textures.items())] + [(n, p) for n, p, _ in fixed]:
            if _norm(path) not in seen:
                seen.add(_norm(path))
                skipped.append((name, path))
        candidates, shader_only = bound, [(n, p) for n, p, _ in other]    # a cube or a volume: not wired here
    else:
        candidates, shader_only = [(n, p, True) for n, p in sorted(textures.items())] + fixed, []
    roles, unused, unnamed = {}, list(shader_only), []
    for name, path, named in candidates:
        # a parameter's texture by its name, then the parameter's; a fixed one by its name only
        role = _role(name, path) if named else _role_of_texture(path)
        if role and role not in roles and role not in (("height", "ao") if named else ("height", "ao", "mask?")):
            roles[role] = (name, path)
        elif role:
            unused.append((name, path))
        else:
            unnamed.append((name, path))
    # a texture nothing names, alone or first: the base colour
    if "base" not in roles and unnamed:
        roles["base"] = unnamed.pop(0)
    unused += unnamed
    used_vec = set(shader.get("used_vectors") or []) if shader is not None and "used_vectors" in shader else None
    used_sc = set(shader.get("used_scalars") or []) if shader is not None and "used_scalars" in shader else None

    y = [300]

    def image(path, normal=False, label=None):
        n = N.new("ShaderNodeTexImage")
        n.image = env.texture(path, "SAMPLERTYPE_Normal" if normal else "")
        n.label = label or path.rsplit("/", 1)[-1].split(".")[0]
        n.location = (-700, y[0])
        y[0] -= 280
        return n

    consumed = set()        # scalars a folded tint already holds (its brightness)

    def value(names, default=None):
        for k, v in scalars.items():
            if k in consumed or used_sc is not None and k not in used_sc:
                continue
            if k.lower().replace("_", "").replace(" ", "") in names:
                return float(v)
        return default

    def pick(names, loose=False):
        """(name, value) of the first vector parameter called one of names (the shader's own, when it's known)."""
        for want in names:
            for k, v in vectors.items():
                if k.lower() == want and (used_vec is None or k in used_vec):
                    return k, v
        if loose and used_vec is not None:
            # the shader's own vectors that are colours of the surface itself: L1Color, Layer1ColorMultiplier
            for k, v in vectors.items():
                if k in used_vec and not _secondary(k) and any(t in ("color", "colour", "tint", "albedo", "diffuse") for t in _tokens(k)):
                    return k, v
        return None, None

    def vector(names):
        return pick(names)[1]

    def folded(name):
        """(the shader's own product of a colour and the scalars that scale it, those scalars), or (None, [])."""
        for p in (shader or {}).get("preshader_values") or []:
            v, params = p.get("value"), p.get("params") or []
            if name in params and p.get("product") and v and len(v) >= 3 and all(
                    q == name or q in scalars and any(w in q.lower() for w in _BRIGHTNESS) for q in params):
                return v, [q for q in params if q != name]
        return None, []

    colour_name, colour = pick(_BASE_COLOURS, loose=True)
    fold, scaled = folded(colour_name) if colour_name else (None, [])
    if fold is not None:
        colour = fold
    glow = None
    base = roles.get("base")
    if fold is not None and not base:
        consumed.update(scaled)     # a flat colour (or a light) holds the brightness it was folded with
    if colour is not None and max(colour[:3]) > 1.001:
        # brighter than a surface colour: a flat material's light; beside a base texture it's
        # something else (an unnamed "Param" vector, an offset...)
        glow, colour = (None if base else colour), None
    if base:
        img = image(base[1], label=base[0])
        col = img.outputs["Color"]
        tint_name, tint = pick(_TINTS, loose=True)
        tint_fold, tint_scaled = folded(tint_name) if tint_name else (None, [])
        if tint_fold is not None:
            tint = tint_fold
            consumed.update(tint_scaled)
        # a plain tint only darkens (a vector brighter than 1 beside a base texture is something else);
        # a folded one is the shader's own colour times its brightness, whichever way it goes
        if tint is not None and (max(tint[:3]) < 0.999 or tint_fold is not None and any(abs(c - 1.0) > 0.001 for c in tint[:3])):
            mix = N.new("ShaderNodeMix")
            mix.data_type = 'RGBA'
            mix.blend_type = 'MULTIPLY'
            mix.inputs["Factor"].default_value = 1.0
            mix.location = (-300, 300)
            mix.label = "UE tint"
            L.new(col, mix.inputs["A"])
            mix.inputs["B"].default_value = tuple(tint[:3]) + (1.0,)
            col = mix.outputs["Result"]
        L.new(col, bsdf.inputs["Base Color"])
        if s["blend"] != "BLEND_Opaque" and "opacity" not in roles:
            L.new(img.outputs["Alpha"], bsdf.inputs["Alpha"])
    elif colour is not None:
        bsdf.inputs["Base Color"].default_value = tuple(colour[:3]) + (1.0,)
    elif re.search(r"(^|[^a-z])black([^a-z]|$)", entry["name"].lower()):
        bsdf.inputs["Base Color"].default_value = (0.01, 0.01, 0.01, 1.0)

    def channel(img, i):
        sep = N.new("ShaderNodeSeparateColor")
        sep.location = (img.location[0] + 300, img.location[1])
        L.new(img.outputs["Color"], sep.inputs[0])
        return sep.outputs[i]

    def one_minus(sock, at):
        n = N.new("ShaderNodeMath")
        n.operation = 'SUBTRACT'
        n.inputs[0].default_value = 1.0
        n.location = at
        L.new(sock, n.inputs[1])
        return n.outputs[0]

    if "normal" in roles:
        img = image(roles["normal"][1], normal=True, label=roles["normal"][0])
        # UE normal maps are DirectX (green down)
        sep = N.new("ShaderNodeSeparateColor")
        sep.location = (-400, img.location[1])
        L.new(img.outputs["Color"], sep.inputs[0])
        inv = one_minus(sep.outputs[1], (-250, img.location[1] - 40))
        comb = N.new("ShaderNodeCombineColor")
        comb.location = (-100, img.location[1])
        L.new(sep.outputs[0], comb.inputs[0])
        L.new(inv, comb.inputs[1])
        L.new(sep.outputs[2], comb.inputs[2])
        nm = N.new("ShaderNodeNormalMap")
        nm.location = (-50, img.location[1] - 200)
        L.new(comb.outputs[0], nm.inputs["Color"])
        L.new(nm.outputs[0], bsdf.inputs["Normal"])
    if "specmasks" in roles:
        # Fortnite's SpecularMasks: R specular, G metallic, B roughness ("_SRM": G roughness, B metallic)
        img = image(roles["specmasks"][1], label=roles["specmasks"][0])
        srm = roles["specmasks"][1].rsplit("/", 1)[-1].split(".")[0].lower().endswith("_srm")
        L.new(channel(img, 0), bsdf.inputs["Specular IOR Level"])
        L.new(channel(img, 2 if srm else 1), bsdf.inputs["Metallic"])
        L.new(channel(img, 1 if srm else 2), bsdf.inputs["Roughness"])
    elif "orm" in roles:
        img = image(roles["orm"][1], label=roles["orm"][0])
        L.new(channel(img, 1), bsdf.inputs["Roughness"])
        L.new(channel(img, 2), bsdf.inputs["Metallic"])
    elif "rma" in roles:
        img = image(roles["rma"][1], label=roles["rma"][0])
        L.new(channel(img, 0), bsdf.inputs["Roughness"])
        L.new(channel(img, 1), bsdf.inputs["Metallic"])
    elif "unitymask" in roles or "metalsmooth" in roles:
        # Unity: R metallic, A smoothness (roughness = 1 - smoothness)
        name, path = roles.get("unitymask") or roles["metalsmooth"]
        img = image(path, label=name)
        L.new(channel(img, 0), bsdf.inputs["Metallic"])
        L.new(one_minus(img.outputs["Alpha"], (img.location[0] + 300, img.location[1] - 160)), bsdf.inputs["Roughness"])
    else:
        if "roughness" in roles:
            L.new(channel(image(roles["roughness"][1], label=roles["roughness"][0]), 0), bsdf.inputs["Roughness"])
        else:
            bsdf.inputs["Roughness"].default_value = max(0.0, min(1.0, value({"roughness"}, 0.5)))
        if "metallic" in roles:
            L.new(channel(image(roles["metallic"][1], label=roles["metallic"][0]), 0), bsdf.inputs["Metallic"])
        elif "glass" not in what:
            bsdf.inputs["Metallic"].default_value = max(0.0, min(1.0, value({"metallic", "metalness", "metalic"}, 0.0)))

    strength = value({"emissivestrength", "emissiveintensity", "emissive", "emissionstrength", "glow", "brightness", "intensity"}, 1.0)
    emissive_col = next((v for k, v in vectors.items() if ("emissive" in k.lower() or "emission" in k.lower())
                         and (used_vec is None or k in used_vec)), None)
    light = vector(("light color", "lightcolor"))
    if "emissive" in roles:
        img = image(roles["emissive"][1], label=roles["emissive"][0])
        L.new(img.outputs["Color"], bsdf.inputs["Emission Color"])
        bsdf.inputs["Emission Strength"].default_value = strength
    else:
        e = next((c for c in (emissive_col, light, glow) if c is not None and any(c[:3])), None)
        if e is not None:
            peak = max(e[:3])
            scale = peak if peak > 1.0 else 1.0
            bsdf.inputs["Emission Color"].default_value = tuple(x / scale for x in e[:3]) + (1.0,)
            bsdf.inputs["Emission Strength"].default_value = strength * scale

    see_through = s["blend"] != "BLEND_Opaque"
    if "opacity" in roles and see_through:
        L.new(channel(image(roles["opacity"][1], label=roles["opacity"][0]), 0), bsdf.inputs["Alpha"])
    elif see_through and not base:
        default = 0.15 if ("glass" in what or "translucent" in what) else 1.0
        bsdf.inputs["Alpha"].default_value = max(0.0, min(1.0, value({"opacity", "alpha", "opacityoverride"}, default)))
    if "invisible" in entry["name"].lower():
        bsdf.inputs["Alpha"].default_value = 0.0
        see_through = True

    if see_through:
        mat.surface_render_method = 'BLENDED' if "Masked" not in s["blend"] else 'DITHERED'
    mat.use_backface_culling = not s["two_sided"]

    def park(label, items, x):
        frame = N.new("NodeFrame")
        frame.label = label
        frame.location = (x, 300)
        yy = 0
        for name, path in items:
            n = N.new("ShaderNodeTexImage")
            n.image = env.texture(path, "")
            n.label = name
            n.parent = frame
            n.location = (0, yy)
            yy -= 280

    if unused:
        park("Unused Textures", unused, -1200)
    if skipped and PARK_UNSAMPLED:
        # the shader never samples these: the master lists them (parameters it doesn't read, a layer a switch turns off)
        park("Unused Textures (not sampled by the shader)", skipped, -1700)
    mat[key_path] = entry["path"]
    mat[key_rev] = revision
    mat["mp_fallback"] = REVISION
    if shader is not None:
        mat["mp_shader"] = len(candidates)      # the textures its shader samples (the others are in their own frame)
    if entry.get("variant"):
        mat[key_variant] = entry["variant"]
    notes = ["approximate: its master has no editor graph (an island's own material), built from %d texture(s) and its values"
             % (len(roles) + len(unused))] + list(env.notes)
    if shader is not None:
        notes.append("its shader map says which textures and parameters it reads: %d texture(s) sampled, %d parked as not sampled"
                     % (len(candidates) + len(shader_only), len(skipped)))
    return mat, notes
