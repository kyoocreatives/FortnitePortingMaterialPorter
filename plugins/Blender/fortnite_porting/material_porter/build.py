"""Builds materials from the app's bundles: translate the graph, assemble a Principled BSDF as UE's blend mode
and shading model ask, lay it out, and put it on the selected objects or in place of an FP material.

A material is one group node (its graph, the "root" node group) into the Material Output;
the parameters are that node's inputs, holding the instance's values (env.MaterialEnv)."""
import hashlib
import json

import bpy

from . import layout
from .app_client import AppClient
from . import world
from .env import MaterialEnv, fit_socket, match_names
from .nodelib import SECTION_KEY
from .ue_graph import BOUNDS_CENTRE, BOUNDS_MAX, BOUNDS_MIN, PART_BOUNDS_MAX, PART_BOUNDS_MIN, HEAD_SOCKET, CARRIED, SHADING_MODELS, Translator, Val, merge_duplicates

PREFIX = "MP "            # prefix of built materials: "MP MI_Foo"
KEY_PATH = "mp_path"      # game object the material translates
KEY_REV = "mp_rev"        # build revision that made it (older ones are rebuilt, not reused)
BUILD_REVISION = 67       # bump when a builder change should rebuild existing materials
KEY_REPLACES = "mp_replaces"
KEY_FP = "mp_fp"          # function group fingerprint, for sharing groups
KEY_VARIANT = "mp_variant"  # hash of a style's parameter values over the instance
KEY_SHAPE = "mp_shape"    # what decides a build's trees beyond parameter values (build_like reuses it)
KEY_LAYOUT = "mp_layout_pending"  # tree built without layout; arranged when a node editor first shows it

# Map imports build hundreds of materials nobody opens: layout (cosmetic, two thirds of a build) waits
# until a node editor shows one.
LAZY_LAYOUT = False
KEY_WATER = "mp_water"    # water material (MSM_SingleLayerWater): its objects get the depth under them
KEY_SUBSURFACE = "mp_subsurface_scale"  # the game's scattering distance (metres), set on the group node input at build
SKIN_SUBSURFACE_INPUT = "Skin Subsurface"   # x the game's skin amount, where its shading model scatters
BASE_SUBSURFACE = "Base Subsurface"         # over the whole surface (cosmetics); the amount is max(base, skin)
SCATTER_DISTANCE = "Scatter Distance"       # metres
SUBSURFACE_RADIUS = "Subsurface Radius"     # per-colour distance (the profile's, skin's, shell fur's)
PROFILE_COLOUR = "Profile Colour"           # how much of the profile's per-colour spread the radius keeps
SUBSURFACE_INTENSITY = "Subsurface Intensity"   # shell fur's amount (everywhere on a layer)
FUR_RADIUS = (1.0, 0.8, 0.65)                   # default radius for fur
SKIN_SCALE = 0.003                              # metres: Fortnite skin's (SS_HeroSkin_02), for skin without a profile
SKIN_SUBSURFACE = "SkinSubsurfaceIntensity"     # a character's skin scattering parameter


def _enum(v, default):
    return str(v if v is not None else default).split("::")[-1]


def _is_head(entry):
    return "head" in str(entry.get("name") or "").lower()


def _models(field):
    """UE's FMaterialShadingModelField (a bit per EMaterialShadingModel) as model names."""
    bits = field.get("ShadingModelField") if isinstance(field, dict) else None
    if not isinstance(bits, int):
        return None
    return {name for i, name in enumerate(SHADING_MODELS) if bits >> i & 1}


def settings(entry):
    """Blend mode, shading model and the rest: instance overrides, else the master's asset, else UE's defaults."""
    asset, over = entry.get("asset") or {}, entry.get("overrides") or {}
    pick = lambda k, d: over.get(k, asset.get(k, d))
    return {
        "blend": _enum(pick("BlendMode", None), "BLEND_Opaque"),
        "shading": _enum(pick("ShadingModel", None), "MSM_DefaultLit"),
        # shading models it compiles (UE's ShadingModels; None if the app doesn't say)
        "models": _models(asset.get("ShadingModels")),
        "two_sided": bool(pick("TwoSided", False)),
        "clip": float(pick("OpacityMaskClipValue", 0.3333)),
        "tangent_normal": bool(asset.get("bTangentSpaceNormal", True)),
        # how a lit translucent material is lit (None if the app doesn't say: as a surface)
        "lighting": _enum(asset["TranslucencyLightingMode"], "") if "TranslucencyLightingMode" in asset else None,
        # the Subsurface Profile skin scatters by: {"radius": [r, g, b], "scale": metres}
        "profile": entry.get("subsurface"),
        # shell fur layer (shells): scatters fully, as fur
        "shell": bool(entry.get("shell")),
        # a cosmetic's material (hook.is_cosmetic): subsurface controls even where the game doesn't scatter
        "cosmetic": bool(entry.get("cosmetic")),
        # a character's head (lips are lit, not skin, in the game's shading; see _per_pixel_models)
        "head": _is_head(entry),
    }


# ------------------------------------------------------------------ assembly
def assemble(tr, mat, a, s):
    """Put the attributes on a Principled BSDF (UE -> Blender) feeding the tree's output:
    the Material Output, or a node group's "Surface" output."""
    moved = False       # whether World Position Offset moves its vertices
    with tr.at("Output"):
        bsdf = tr.node("ShaderNodeBsdfPrincipled", "UE surface")
        if tr.tree == mat.node_tree:
            out = tr.node("ShaderNodeOutputMaterial", "output")
        else:
            tr.tree.interface.new_socket("Surface", in_out='OUTPUT', socket_type='NodeSocketShader')
            out = tr.node("NodeGroupOutput", "output")
        link = lambda v, name: tr.link(v, bsdf.inputs[name]) if v is not None else None
        shading = s["shading"]
        per_pixel = None
        if shading == "MSM_FromMaterialExpression":
            sm = a.get("ShadingModel")
            if sm is not None and not sm.const:
                # the graph picks the model per pixel (skin here, lit there); masks below
                per_pixel = sm
            shading = SHADING_MODELS[int(sm.s)] if sm is not None and sm.const and 0 <= int(sm.s) < len(SHADING_MODELS) \
                else "MSM_DefaultLit"
        # the water medium comes from the graph's SingleLayerWaterMaterialOutput
        water = shading == "MSM_SingleLayerWater" and "WaterAbsorption" in a

        # UE lights translucent materials from the translucency volume: diffuse only (no specular; Metallic only darkens).
        # Lit per vertex or without direction, the Normal is unused (smoke sprites' sphere normals would shade each puff as a ball).
        volumetric = s["blend"] in TRANSLUCENT and s.get("lighting") in VOLUMETRIC_LIGHTING
        flat = volumetric and s["lighting"] in NORMAL_UNUSED
        if not water:
            # water: BaseColor and Metallic belong to the surface layer, below
            if volumetric:
                base, metal = a["BaseColor"], a["Metallic"]
                if base is not None and metal is not None and not (metal.const and _comps(metal.s)[0] == 0.0):
                    base = tr.vmath('SCALE', tr.as3(base), tr.math('SUBTRACT', tr.const(1.0), metal), out_w=3)
                link(base, "Base Color")
            else:
                link(a["BaseColor"], "Base Color")
                link(a["Metallic"], "Metallic")
        link(a["Roughness"], "Roughness")
        if volumetric:
            bsdf.inputs["Specular IOR Level"].default_value = 0.0
        elif not water:
            # UE's Specular 0.5 is F0 0.04, same as Blender's Specular IOR Level 0.5 (IOR 1.5)
            link(a["Specular"], "Specular IOR Level")
        emissive = a["EmissiveColor"]
        if not (emissive.const and not any(_comps(emissive.s)[:3])):
            link(emissive, "Emission Color")
            bsdf.inputs["Emission Strength"].default_value = 1.0

        n = a["Normal"]
        bsdf_n = None
        if not flat and not (n.const and _comps(n.s)[:3] == (0.0, 0.0, 1.0)):
            # UE's tangent space is DirectX (green down); a world-space normal is in UE space
            # (read by the PixelNormalWS node, when the material uses it)
            bsdf_n = tr.material_normal(n, s["tangent_normal"])
            tr.link(bsdf_n, bsdf.inputs["Normal"])

        if per_pixel is not None:
            _per_pixel_models(tr, bsdf, a, per_pixel, s["profile"], s.get("head"), s.get("models"))
        elif shading in ("MSM_Subsurface", "MSM_TwoSidedFoliage"):
            # UE's subsurface models read Opacity as scattering amount and SubsurfaceColor as its colour
            link(a["Opacity"], "Subsurface Weight")
            link(a["SubsurfaceColor"], "Subsurface Radius")
        elif shading in ("MSM_SubsurfaceProfile", "MSM_PreintegratedSkin"):
            # skin: scattering comes from the Subsurface Profile, not SubsurfaceColor
            link(a["Opacity"], "Subsurface Weight")
            _profile(bsdf, s["profile"])
        elif shading == "MSM_ClearCoat":
            link(a["ClearCoat"], "Coat Weight")
            link(a["ClearCoatRoughness"], "Coat Roughness")
        elif shading == "MSM_Cloth":
            # Cloth: CustomData0 is the fuzz amount, SubsurfaceColor the fuzz colour
            link(a["ClearCoat"], "Sheen Weight")
            link(a["SubsurfaceColor"], "Sheen Tint")

        surface = Val(bsdf.outputs[0], 3)
        if water:
            surface = _water(tr, mat, a, bsdf, bsdf_n, out)
        if shading == "MSM_Unlit":
            em = tr.node("ShaderNodeEmission", "unlit")
            tr.link(emissive, em.inputs["Color"])
            surface = Val(em.outputs[0], 3)

        blend = s["blend"]
        if blend == "BLEND_Masked":
            m = a["OpacityMask"]
            cut = m if m.const else tr.math('GREATER_THAN', m, tr.const(s["clip"] - 1e-4), label="opacity mask clip")
            if cut.const:
                cut = tr.const(1.0 if cut.s > s["clip"] else 0.0)
            for keep in tr.clips:
                # the graph's clip()s: not drawn where one is 0
                cut = tr.binop('MULTIPLY', cut, keep, label="clip()")
            if not (cut.const and cut.s == 1.0):
                if shading == "MSM_Unlit" or water:
                    surface = _with_alpha(tr, surface, cut)
                else:
                    link(cut, "Alpha")
        elif blend in TRANSLUCENT:
            mat.surface_render_method = 'BLENDED'
            _see_through_shadows(mat)
            if shading == "MSM_Unlit":
                surface = _with_alpha(tr, surface, a["Opacity"])
            else:
                link(a["Opacity"], "Alpha")
        elif blend in MODULATE:
            # scene behind, multiplied by the emissive colour
            mat.surface_render_method = 'BLENDED'
            _see_through_shadows(mat)
            tp = tr.node("ShaderNodeBsdfTransparent", "modulate")
            tr.link(emissive, tp.inputs["Color"])
            surface = Val(tp.outputs[0], 3)
        elif blend == "BLEND_Additive":
            mat.surface_render_method = 'BLENDED'
            _see_through_shadows(mat)
            add = tr.node("ShaderNodeAddShader", "additive")
            tp = tr.node("ShaderNodeBsdfTransparent", "see-through")
            em = tr.node("ShaderNodeEmission", "additive light")
            # UE adds Emissive * Opacity to the scene (a flash's shape is its opacity)
            light, opacity = emissive, a["Opacity"]
            if not (opacity.const and _comps(opacity.s)[0] == 1.0):
                light = tr.binop('MULTIPLY', emissive, opacity, label="additive opacity")
            tr.link(light, em.inputs["Color"])
            tr.L.new(tp.outputs[0], add.inputs[0])
            tr.L.new(em.outputs[0], add.inputs[1])
            surface = Val(add.outputs[0], 3)
        tr.L.new(surface.s, out.inputs["Surface"])
        offset = a.get("WorldPositionOffset")
        if offset is not None and not (offset.const and not any(_comps(offset.s)[:3])) and tr.tree != mat.node_tree:
            # UE's World Position Offset (cm, UE axes) moves each vertex; Eevee's displacement does the same
            # (no bump: the offset isn't a height)
            with tr.at("World Position Offset"):
                metres = tr.vmath('MULTIPLY', tr.as3(offset), tr.const((0.01, -0.01, 0.01), 3), out_w=3)
                move = tr.node("ShaderNodeVectorDisplacement", "world position offset", space='WORLD')
                tr.link(metres, move.inputs["Vector"])
                move.inputs["Midlevel"].default_value = 0.0
                move.inputs["Scale"].default_value = 1.0
            tr.tree.interface.new_socket("Displacement", in_out='OUTPUT', socket_type='NodeSocketVector')
            tr.L.new(move.outputs[0], out.inputs["Displacement"])
            mat.displacement_method = 'DISPLACEMENT'
            if hasattr(mat, "max_vertex_displacement"):
                mat.max_vertex_displacement = 50.0
            moved = True
    # Amount and distance of light scattering under skin or fur sit on the material's own group node so an artist
    # can tune them (the import settings set them too). Skin scatters where the game's shading model does (Skin
    # Subsurface scales the game's amount, keeping its soft edges); Base Subsurface scatters a cosmetic's whole
    # surface (amount max(base, skin)); Profile Colour blends the profile's per-colour radius towards its mean.
    # A shell fur layer scatters everywhere by its own amount. These nodes go in the Output frame with the surface.
    with tr.at("Output"):
        weight = bsdf.inputs["Subsurface Weight"]
        scatters = weight.is_linked or weight.default_value > 0.0
        shell, cosmetic = s.get("shell"), s.get("cosmetic")
        if tr.tree != mat.node_tree and (scatters or shell or cosmetic):
            if not scatters and not shell:
                # a cosmetic the game doesn't scatter: Base Subsurface alone, over skin's neutral radius and distance
                bsdf.inputs["Subsurface Radius"].default_value = SKIN_RADIUS
                bsdf.inputs["Subsurface Scale"].default_value = SKIN_SCALE
            scale = bsdf.inputs["Subsurface Scale"].default_value

            def new_input(name, default, description, most=1.0, kind='NodeSocketFloat'):
                sock = tr.tree.interface.new_socket(name, in_out='INPUT', socket_type=kind)
                sock.default_value, sock.min_value, sock.description = default, 0.0, description
                if kind == 'NodeSocketFloat':
                    sock.max_value = most
                gi = tr.node("NodeGroupInput", name.lower())
                return next(o for o in gi.outputs if o.identifier == sock.identifier)

            def node(op, a, b, label):
                n = tr.node("ShaderNodeMath", label, operation=op)
                tr.L.new(a, n.inputs[0])
                tr.L.new(b, n.inputs[1])
                return n.outputs[0]

            game = weight.links[0].from_socket if weight.is_linked else None
            if shell:
                tr.L.new(new_input(SUBSURFACE_INTENSITY, 1.0, "How much light scatters under the fur"), weight)
            else:
                skin = new_input(SKIN_SUBSURFACE_INPUT, 1.0, "How much light scatters where the game's skin scatters (x its amount)")
                amount = None
                if game is not None:
                    # the game's amount relative to its skin's (SkinSubsurfaceIntensity, e.g. Helsie's face 0.4,
                    # lips 0): 1 on skin, the game's ratio elsewhere (keeps soft edges); without that parameter,
                    # the game's amount itself
                    param = next((it for it in tr.tree.interface.items_tree if it.item_type == 'SOCKET'
                                  and it.in_out == 'INPUT' and it.name == SKIN_SUBSURFACE), None)
                    ratio = tr.node("ShaderNodeMath", "game's scattering over its skin's", operation='DIVIDE', use_clamp=True)
                    tr.L.new(game, ratio.inputs[0])
                    if param is not None:
                        gi = tr.node("NodeGroupInput", "skin subsurface intensity")
                        least = tr.node("ShaderNodeMath", "skin's scattering (not 0)", operation='MAXIMUM')
                        tr.L.new(next(o for o in gi.outputs if o.identifier == param.identifier), least.inputs[0])
                        least.inputs[1].default_value = 1e-4
                        tr.L.new(least.outputs[0], ratio.inputs[1])
                    else:
                        ratio.inputs[1].default_value = 1.0
                    amount = node('MULTIPLY', ratio.outputs[0], skin, "skin subsurface")
                elif scatters:
                    amount = skin      # a constant amount: no ratio, the flat Skin Subsurface
                if cosmetic:
                    base = new_input(BASE_SUBSURFACE, 0.0, "How much light scatters over the whole surface, clothes included")
                    amount = node('MAXIMUM', base, amount, "base or skin subsurface") if amount is not None else base
                if amount is not None:
                    tr.L.new(amount, weight)
            tr.L.new(new_input(SCATTER_DISTANCE, scale, "How far light scatters under the surface (metres)", most=1e4),
                     bsdf.inputs["Subsurface Scale"])
            if shell:
                # the game's fur radius (Post FX SubsurfaceColor) is about black: a near-neutral one, red furthest
                tr.L.new(new_input(SUBSURFACE_RADIUS, FUR_RADIUS, "How far each colour scatters, x the distance",
                                   kind='NodeSocketVector'), bsdf.inputs["Subsurface Radius"])
            else:
                # skin radius (the profile's, else SKIN_RADIUS), blended towards its mean by Profile Colour;
                # a radius the graph makes (Subsurface's or foliage's SubsurfaceColor) keeps its own colour
                radius = bsdf.inputs["Subsurface Radius"]
                target = radius if not radius.is_linked else None
                profiled = not scatters or (per_pixel is None and shading in ("MSM_SubsurfaceProfile", "MSM_PreintegratedSkin"))
                if radius.is_linked and radius.links[0].from_node.bl_idname == "ShaderNodeMix" \
                        and radius.links[0].from_node.label == "subsurface radius" \
                        and not radius.links[0].from_node.inputs[5].is_linked:
                    target, profiled = radius.links[0].from_node.inputs[5], True
                if target is not None:
                    profile = new_input(SUBSURFACE_RADIUS, tuple(target.default_value)[:3],
                                        "How far each colour scatters under skin, x the distance", kind='NodeSocketVector')
                if target is not None and not profiled:
                    tr.L.new(profile, target)
                elif target is not None:
                    colour = new_input(PROFILE_COLOUR, 0.5, "How much of the profile's colour the scattering keeps (0: neutral)")
                    parts = tr.node("ShaderNodeSeparateXYZ", "profile radius")
                    tr.L.new(profile, parts.inputs[0])
                    total = tr.node("ShaderNodeMath", "radius sum", operation='ADD')
                    tr.L.new(parts.outputs[0], total.inputs[0])
                    tr.L.new(parts.outputs[1], total.inputs[1])
                    total3 = tr.node("ShaderNodeMath", "radius sum", operation='ADD')
                    tr.L.new(total.outputs[0], total3.inputs[0])
                    tr.L.new(parts.outputs[2], total3.inputs[1])
                    avg = tr.node("ShaderNodeMath", "radius mean", operation='DIVIDE')
                    tr.L.new(total3.outputs[0], avg.inputs[0])
                    avg.inputs[1].default_value = 3.0
                    neutral = tr.node("ShaderNodeCombineXYZ", "neutral radius")
                    for i in range(3):
                        tr.L.new(avg.outputs[0], neutral.inputs[i])
                    blend = tr.node("ShaderNodeMix", "profile colour", data_type='VECTOR')
                    tr.L.new(colour, blend.inputs[0])
                    tr.L.new(neutral.outputs[0], blend.inputs[4])
                    tr.L.new(profile, blend.inputs[5])
                    tr.L.new(blend.outputs[1], target)
            mat[KEY_SUBSURFACE] = scale
    # a strip its material thickens towards the camera has no side of its own, and the mirrored import
    # turns away the one it has: draw both sides
    mat.use_backface_culling = not s["two_sided"] and not moved


# UE 5 names plain translucency BLEND_TranslucentGreyTransmittance (Substrate adds the coloured one);
# all blend by Opacity
TRANSLUCENT = ("BLEND_Translucent", "BLEND_TranslucentGreyTransmittance", "BLEND_TranslucentColoredTransmittance",
               "BLEND_AlphaComposite", "BLEND_AlphaHoldout")
# Modulate is also BLEND_ColoredTransmittanceOnly (same value; Tempest's eye glow multiplies its helmet)
MODULATE = ("BLEND_Modulate", "BLEND_ColoredTransmittanceOnly")
# Translucency lighting from the translucency volume (UE's default is the first): diffuse only;
# all but the per-pixel directional one leave the Normal unused
VOLUMETRIC_LIGHTING = ("TLM_VolumetricNonDirectional", "TLM_VolumetricPerVertexNonDirectional",
                       "TLM_VolumetricPerVertexDirectional", "TLM_VolumetricDirectional")
NORMAL_UNUSED = VOLUMETRIC_LIGHTING[:3]


def _see_through_shadows(mat):
    """Make a blended material's shadow follow its alpha, not its whole surface."""
    for prop in ("use_transparent_shadow", "use_transparency_overlap"):
        if hasattr(mat, prop):
            setattr(mat, prop, True)


SKIN_RADIUS = (1.0, 0.8, 0.65)     # near-neutral skin radius, red furthest


def _profile(bsdf, profile):
    """Set a Subsurface Profile's radius and scale on the BSDF (no profile: SKIN_RADIUS)."""
    radius = tuple(profile["radius"]) if profile else SKIN_RADIUS
    bsdf.inputs["Subsurface Radius"].default_value = radius
    if profile:
        bsdf.inputs["Subsurface Scale"].default_value = float(profile["scale"])
    return radius


def _per_pixel_models(tr, bsdf, a, sm, profile=None, head=False, models=None):
    """A shading model the graph picks per pixel: each model's lobe weighted by a mask of where it is picked
    (subsurface, clear coat, cloth). Only models the material compiles (`models`, UE's ShadingModels; None: any)
    are built: a building that picks lit or clear coat gets no subsurface or its controls
    (assemble adds them where the weight is linked)."""
    def mask(*names):
        m = None
        for name in names:
            if models is not None and name not in models:
                continue
            eq = tr.math('COMPARE', sm, tr.const(float(SHADING_MODELS.index(name))), tr.const(0.5),
                         label="shading model is " + name[4:])
            m = eq if m is None else tr.math('ADD', m, eq)
        return m

    def link(v, name):
        if v is not None:
            tr.link(v, bsdf.inputs[name])

    # A head's lips are lit, not skin, in the game (Sand Witch Helsie's: unscattered next to scattered skin,
    # a hard edge). They scatter as skin with full Opacity wherever there is skin.
    skin_models = ("MSM_SubsurfaceProfile", "MSM_PreintegratedSkin")
    if head and (models is None or any(m in models for m in skin_models)):
        skin_models += ("MSM_DefaultLit",)
    sss = mask("MSM_Subsurface", "MSM_TwoSidedFoliage", *skin_models)
    if sss is not None:
        link(tr.math('MULTIPLY', sss, a["Opacity"], label="subsurface amount"), "Subsurface Weight")
        # SubsurfaceColor for Subsurface / foliage; the skin profile radius elsewhere (its scale for all)
        skin = mask(*skin_models)
        radius = _profile(bsdf, profile)
        link(a["SubsurfaceColor"] if skin is None else
             tr.lerp(a["SubsurfaceColor"], tr.const(radius, 3), skin, label="subsurface radius"), "Subsurface Radius")
    coat = mask("MSM_ClearCoat")
    if coat is not None:
        link(tr.math('MULTIPLY', coat, a["ClearCoat"], label="clear coat amount"), "Coat Weight")
        link(a["ClearCoatRoughness"], "Coat Roughness")
    cloth = mask("MSM_Cloth")
    if cloth is not None:
        # Cloth: CustomData0 is the fuzz amount, SubsurfaceColor the fuzz colour
        link(tr.math('MULTIPLY', cloth, a["ClearCoat"], label="cloth fuzz amount"), "Sheen Weight")
        link(a["SubsurfaceColor"], "Sheen Tint")


IOR_WATER = 1.333
# dielectric reflectance at normal incidence from the IOR, as Blender's Principled BSDF: ((n - 1) / (n + 1))^2
F0_WATER = ((IOR_WATER - 1.0) / (IOR_WATER + 1.0)) ** 2


def _water(tr, mat, a, bsdf, normal, out):
    """UE's single layer water (MSM_SingleLayerWater) in Eevee. Returns the surface shader.

    UE lights the surface itself (specular from Specular and Roughness, a BaseColor surface layer where Opacity > 0),
    then adds the water the view ray crosses to the scene behind it, per colour channel (coefficients per cm, path in cm):
        T = exp(-(Scattering + Absorption) * path)
        the scene behind * T * ColorScaleBehindWater
        + (1 - F) * Scattering (1 - T) / (Scattering + Absorption) * (sun * phase + ambient / 4 pi)
    Here the surface is a Principled BSDF refracting at water's IOR (Eevee's raytraced transmission shows the ground)
    with transmission tinted T * ColorScaleBehindWater. The in-scattered light is a diffuse lobe lit by the scene,
    albedo = scattered amount / 4 * (1 - F) (isotropic phase 1 / 4 pi against a diffuse lobe's 1 / pi).
    The path comes from the translator (env depth over view steepness). Like UE's water meshes it casts no shadow."""
    path = tr.water_path()
    zero3, one3 = tr.const((0.0, 0.0, 0.0), 3), tr.const((1.0, 1.0, 1.0), 3)
    # UE: max(0, coefficients)
    sc = tr.vmath('MAXIMUM', tr.as3(a["WaterScattering"]), zero3, label="scattering (per cm)", out_w=3)
    ab = tr.vmath('MAXIMUM', tr.as3(a["WaterAbsorption"]), zero3, label="absorption (per cm)", out_w=3)
    ext = tr.vmath('ADD', sc, ab, label="extinction", out_w=3)
    depth = tr.vmath('SCALE', ext, path, label="optical depth", out_w=3)
    trans = tr.combine([tr.math('EXPONENT', tr.binop('MULTIPLY', c, tr.const(-1.0))) for c in tr.comps(depth)])
    scattered = tr.vmath('DIVIDE', tr.vmath('MULTIPLY', sc, tr.vmath('SUBTRACT', one3, trans, out_w=3), out_w=3),
                         tr.vmath('MAXIMUM', ext, tr.const((1e-5,) * 3, 3), out_w=3), label="scattered amount", out_w=3)
    tint = trans
    cs = a["WaterColorScaleBehindWater"]
    if not (cs.const and _comps(cs.s)[:3] == (1.0, 1.0, 1.0)):
        tint = tr.vmath('MULTIPLY', trans, tr.vmath('MAXIMUM', tr.as3(cs), zero3, out_w=3), label="colour behind water",
                        out_w=3)
    # the surface: reflects, and refracts the ground through the water's tint
    tr.link(tint, bsdf.inputs["Base Color"])
    bsdf.inputs["Transmission Weight"].default_value = 1.0
    bsdf.inputs["IOR"].default_value = IOR_WATER
    bsdf.label = "UE water surface"
    # UE's F0 is 0.08 * Specular; Blender's is F0(IOR) * 2 * Specular IOR Level
    tr.link(tr.binop('MULTIPLY', a["Specular"], tr.const(0.08 / (2.0 * F0_WATER)), label="specular level"),
            bsdf.inputs["Specular IOR Level"])
    # the lit water under the surface
    fres = tr.node("ShaderNodeFresnel", "water fresnel")
    fres.inputs["IOR"].default_value = IOR_WATER
    if normal is not None:
        tr.link(normal, fres.inputs["Normal"])
    albedo = tr.vmath('SCALE', scattered, tr.binop('MULTIPLY', tr.binop('SUBTRACT', tr.const(1.0), Val(fres.outputs[0], 1)),
                                                   tr.const(0.25)), label="in-scattered albedo", out_w=3)
    medium = tr.node("ShaderNodeBsdfDiffuse", "water medium (in-scattered light)")
    tr.link(albedo, medium.inputs["Color"])
    add = tr.node("ShaderNodeAddShader", "water")
    tr.L.new(bsdf.outputs[0], add.inputs[0])
    tr.L.new(medium.outputs[0], add.inputs[1])
    surface = Val(add.outputs[0], 3)
    # Opacity: the surface layer (foam, debris) over the water
    op = a["Opacity"]
    if not (op.const and op.s <= 0.0):
        layer = tr.node("ShaderNodeBsdfPrincipled", "UE water surface layer")
        for name, pin in (("Base Color", "BaseColor"), ("Metallic", "Metallic"), ("Roughness", "Roughness"),
                          ("Specular IOR Level", "Specular")):
            tr.link(a[pin], layer.inputs[name])
        if normal is not None:
            tr.link(normal, layer.inputs["Normal"])
        mix = tr.node("ShaderNodeMixShader", "surface opacity")
        tr.link(tr.saturate(op), mix.inputs[0])
        tr.L.new(add.outputs[0], mix.inputs[1])
        tr.L.new(layer.outputs[0], mix.inputs[2])
        surface = Val(mix.outputs[0], 3)
    # UE's water meshes cast no shadow; the ground under the water stays sunlit
    path_node = tr.node("ShaderNodeLightPath", "light path")
    no_shadow = tr.node("ShaderNodeMixShader", "casts no shadow")
    see = tr.node("ShaderNodeBsdfTransparent", "see-through")
    tr.L.new(path_node.outputs["Is Shadow Ray"], no_shadow.inputs[0])
    tr.L.new(surface.s, no_shadow.inputs[1])
    tr.L.new(see.outputs[0], no_shadow.inputs[2])
    surface = Val(no_shadow.outputs[0], 3)
    _see_through_shadows(mat)
    # one refracting interface (thickness 0), traced through the scene
    zero = tr.node("ShaderNodeValue", "thickness: one interface")
    zero.outputs[0].default_value = 0.0
    if tr.tree == mat.node_tree:
        tr.L.new(zero.outputs[0], out.inputs["Thickness"])
    else:
        tr.tree.interface.new_socket("Thickness", in_out='OUTPUT', socket_type='NodeSocketFloat')
        tr.L.new(zero.outputs[0], out.inputs["Thickness"])
    mat.surface_render_method = 'DITHERED'
    if hasattr(mat, "use_raytrace_refraction"):
        mat.use_raytrace_refraction = True
    mat[KEY_WATER] = 1
    return surface


def _with_alpha(tr, surface, alpha):
    mix = tr.node("ShaderNodeMixShader", "alpha")
    tp = tr.node("ShaderNodeBsdfTransparent", "see-through")
    tr.link(alpha, mix.inputs[0])
    tr.L.new(tp.outputs[0], mix.inputs[1])
    tr.L.new(surface.s, mix.inputs[2])
    return Val(mix.outputs[0], 3)


def _material_node(mat, root, label, values, width):
    """The material's tree: the group node with the parameter values in its sockets, feeding the Material Output."""
    tree = mat.node_tree
    node = tree.nodes.new("ShaderNodeGroup")
    node.node_tree = root
    node.label = label
    node.width = width
    by_id = {s.identifier: s for s in node.inputs}
    for ident, value in values:
        sock = by_id.get(ident)
        if sock is not None:
            sock.default_value = fit_socket(sock, value) if isinstance(value, (tuple, list)) else value
    out = tree.nodes.new("ShaderNodeOutputMaterial")
    tree.links.new(node.outputs["Surface"], out.inputs["Surface"])
    if "Thickness" in node.outputs:
        tree.links.new(node.outputs["Thickness"], out.inputs["Thickness"])
    if "Displacement" in node.outputs:
        tree.links.new(node.outputs["Displacement"], out.inputs["Displacement"])
    node.location = (-width - 60.0, 0.0)
    out.location = (0.0, 0.0)
    tree.nodes.active = node
    # layout sections: these two at top level (the textures get a frame of their own)
    node[SECTION_KEY] = out[SECTION_KEY] = ""
    return node


def _texture_zones(mat, node, env):
    """Textures the material's group samples live outside it: each is a closure zone around an image node
    in the material's own tree (Textures frame), feeding the group's Closure input."""
    if not env._textures:
        return False
    tr = Translator(mat.node_tree, env)
    prev = tr.activate()
    try:
        by_id = {s.identifier: s for s in node.inputs}
        # in the group's Textures panel order so the wires run straight across
        for item, _sock, (tname, img, own, address) in sorted(env._textures.values(), key=lambda t: t[0].name.lower()):
            target = by_id.get(item.identifier)
            if target is not None:
                mat.node_tree.links.new(tr._texture_zone(tname, img, own, address), target)
    finally:
        prev.activate()
    return True


def _comps(s):
    """A constant's components (a scalar broadcasts, as UE casts it)."""
    return tuple(s) if isinstance(s, (tuple, list)) else (s, s, s)


# ------------------------------------------------------------------ sharing
def _fingerprint(tree, memo):
    """Hash what a group computes (nodes, settings, values, wiring, images), not node positions or Blender names,
    so two builds of one function compare equal. Each node is hashed with what feeds it (sources first), the group
    by what reaches its outputs and its interface. Trees differing only in node name numbering are twins."""
    if tree.name in memo:
        return memo[tree.name]
    skip = {"location", "width", "height", "dimensions", "select", "name", "label", "parent", "color",
            "use_custom_color", "show_options", "show_preview", "hide", "mute", "bl_idname", "location_absolute"}
    feeds = {}
    for l in tree.links:
        feeds.setdefault(l.to_node.name, {})[l.to_socket.identifier] = (l.from_node, l.from_socket.identifier)
    done = {}

    def node_hash(root):
        # sources first, without recursion (function chains run hundreds of nodes deep)
        stack = [root]
        while stack:
            n = stack[-1]
            if n.name in done:
                stack.pop()
                continue
            pending = [src for src, _i in feeds.get(n.name, {}).values() if src.name not in done]
            if pending:
                stack.extend(pending)
                continue
            stack.pop()
            parts = [n.bl_idname]
            for prop in n.bl_rna.properties:
                if prop.identifier in skip or prop.is_readonly and prop.identifier not in ("node_tree", "image"):
                    continue
                v = getattr(n, prop.identifier, None)
                if prop.identifier == "node_tree" and v is not None:
                    # world and collection groups: one each per file, growing as materials ask for more
                    # (hashing their contents would make every group around them differ)
                    world = "mp_path" in v or v.name.startswith(("MP World", "MP Collection"))
                    v = "world:" + v.name if world else v.get(KEY_FP) or _fingerprint(v, memo)
                elif prop.identifier == "image" and v is not None:
                    v = v.filepath or v.name
                elif not isinstance(v, (int, float, str, bool)):
                    continue
                parts.append("%s=%r" % (prop.identifier, v))
            for k in n.keys():
                # mp_image: which image a texture read when built (a twin reads its own)
                if k not in (SECTION_KEY, "fpv4_group_input", "mp_image"):
                    parts.append("%s:%r" % (k, n[k]))
            for sock in n.inputs:
                src = feeds.get(n.name, {}).get(sock.identifier)
                if src is not None:
                    parts.append("%s<%s.%s" % (sock.identifier, done[src[0].name], src[1]))
                elif hasattr(sock, "default_value"):
                    v = sock.default_value
                    parts.append("%s=%r" % (sock.identifier, tuple(round(x, 6) for x in v) if hasattr(v, "__len__") else round(v, 6)))
            if n.bl_idname == "ShaderNodeValue":
                parts.append(repr(round(n.outputs[0].default_value, 6)))
            done[n.name] = hashlib.sha1("|".join(parts).encode()).hexdigest()
        return done[root.name]
    h = hashlib.sha1()
    for out in sorted(node_hash(n) for n in tree.nodes if n.bl_idname == "NodeGroupOutput"):
        h.update(out.encode())
    for it in tree.interface.items_tree:
        if it.item_type == 'SOCKET':
            h.update(("%s:%s:%s" % (it.in_out, it.name, it.socket_type)).encode())
    memo[tree.name] = h.hexdigest()
    return memo[tree.name]


def drop_unread_inputs(tree):
    """Remove a function group's inputs nothing in it reads (their readers went in merge_duplicates),
    so it matches the twin that never had them."""
    read = {s.identifier for n in tree.nodes if n.bl_idname == "NodeGroupInput" for s in n.outputs if s.is_linked}
    for it in [it for it in tree.interface.items_tree
               if it.item_type == 'SOCKET' and it.in_out == 'INPUT' and it.identifier not in read]:
        tree.interface.remove(it)


# fingerprint -> group, kept between builds: reading every group's fingerprint per material
# grew with the scene (hundreds of materials per map)
_KNOWN = None


def begin_session():
    """Start a new job: re-read the known groups from the file (they may have come or gone) and recheck images against their files."""
    global _KNOWN
    _KNOWN = None
    from . import env
    env._LOADED.clear()


def _known():
    global _KNOWN
    if _KNOWN is None:
        _KNOWN = {g[KEY_FP]: g for g in bpy.data.node_groups if KEY_FP in g}
    return _KNOWN


def _alive(g, fp):
    try:
        return g.get(KEY_FP) == fp
    except ReferenceError:
        return False


def share_groups(trees, owners=()):
    """Replace groups identical to one an earlier build made with that one (in these trees and the owners',
    the only places a new group is used). Returns (how many were replaced, the ones kept)."""
    memo = {}
    known = _known()
    # innermost first: an outer group's fingerprint includes its inner groups
    order, seen = [], set()

    def visit(t):
        if t.name in seen:
            return
        seen.add(t.name)
        for n in t.nodes:
            if n.bl_idname == "ShaderNodeGroup" and n.node_tree is not None and n.node_tree in trees:
                visit(n.node_tree)
        order.append(t)
    for t in trees:
        visit(t)
    # group nodes that may point at a new group; repointing just these replaces user_remap,
    # which walks every datablock of the file
    users = [n for tr in list(trees) + list(owners) for n in tr.nodes
             if n.bl_idname == "ShaderNodeGroup" and n.node_tree is not None]
    shared, kept, gone = 0, [], []
    for t in order:
        fp = _fingerprint(t, memo)
        twin = known.get(fp)
        if twin is not None and not _alive(twin, fp):
            del known[fp]
            twin = None
        if twin is not None and twin != t and not _outputs_cover(twin, t, users):
            # twin was pruned of an output this build reads (prune_groups): keep this one whole
            twin = None
        if twin is not None and twin != t:
            for n in users:
                if n.node_tree == t:
                    n.node_tree = twin
            gone.append(t)
            shared += 1
        else:
            t[KEY_FP] = fp
            known[fp] = t
            kept.append(t)
    if gone:
        bpy.data.batch_remove(gone)
    return shared, kept


def _outputs(tree, kind='OUTPUT'):
    return [it for it in tree.interface.items_tree if it.item_type == 'SOCKET' and it.in_out == kind]


def _outputs_cover(twin, t, users):
    """Whether twin still has every output of t that t's group nodes read."""
    have = {it.identifier for it in _outputs(twin)}
    return all(s.identifier in have for n in users if n.node_tree == t for s in n.outputs if s.is_linked)


# ------------------------------------------------------------------ pruning after an import
_OUTPUT_NODES = ("ShaderNodeOutputMaterial", "ShaderNodeOutputWorld", "ShaderNodeOutputLight", "ShaderNodeOutputAOV")


def _live_outputs():
    """{group: output identifiers a live node reads}, over the file: from every material, world and light output
    through the group nodes they reach (a group node that is read at all reads all its inputs)."""
    used, live = {}, {}

    def walk(tree, starts):
        feeds = {}
        for l in tree.links:
            if l.is_valid:
                feeds.setdefault(l.to_node.as_pointer(), []).append(l)
        seen = live.setdefault(tree.as_pointer(), set())
        stack, grew = list(starts), False
        while stack:
            node, sock = stack.pop()
            p = node.as_pointer()
            if node.bl_idname == "NodeGroupOutput":
                links = [l for l in feeds.get(p, ()) if l.to_socket.identifier == sock]
            else:
                if node.bl_idname == "ShaderNodeGroup" and node.node_tree is not None and sock is not None:
                    got = used.setdefault(node.node_tree, set())
                    if sock not in got:
                        got.add(sock)
                        grew = True
                if p in seen:
                    continue
                seen.add(p)
                links = feeds.get(p, ())
            for l in links:
                stack.append((l.from_node, l.from_socket.identifier))
        return grew
    for owner in list(bpy.data.materials) + list(bpy.data.worlds) + list(bpy.data.lights):
        tree = getattr(owner, "node_tree", None)
        if tree is not None:
            walk(tree, [(n, None) for n in tree.nodes if n.bl_idname in _OUTPUT_NODES])
    for _ in range(64):
        grew = False
        for g, socks in list(used.items()):
            outs = [n for n in g.nodes if n.bl_idname == "NodeGroupOutput"]
            for sock in list(socks):
                grew = walk(g, [(n, sock) for n in outs]) or grew
        if not grew:
            break
    return used


def _drop_dead(tree, kinds=None):
    """Remove nodes that reach none of the tree's outputs, and unread Group Input nodes.
    With `kinds`, only nodes of those kinds (a material's own tree: texture zones nothing reads)."""
    outs = ("NodeGroupOutput",) + _OUTPUT_NODES
    feeds = {}
    for l in tree.links:
        feeds.setdefault(l.to_node.as_pointer(), []).append(l.from_node)
    live, todo = set(), [n for n in tree.nodes if n.bl_idname in outs]
    while todo:
        n = todo.pop()
        if n.as_pointer() not in live:
            live.add(n.as_pointer())
            todo += feeds.get(n.as_pointer(), [])
    dead = [n for n in tree.nodes if n.as_pointer() not in live and n.bl_idname != "NodeFrame"
            and (kinds is None or n.bl_idname in kinds)]
    inputs = [n for n in tree.nodes if n.bl_idname == "NodeGroupInput"]
    if inputs and all(n in dead for n in inputs):
        dead.remove(inputs[0])          # keep one Group Input so the interface shows
    for n in dead:
        tree.nodes.remove(n)
    for f in [n for n in tree.nodes if n.bl_idname == "NodeFrame"]:
        if not any(n.parent == f for n in tree.nodes):
            tree.nodes.remove(f)
    return len(dead)


def prune_groups(names):
    """After an import, prune the function groups it made (by name): drop outputs no material in the file reads,
    the nodes only those fed, inputs nothing reads any more, and unused groups; the materials' own groups lose
    what fed those inputs. Returns how many nodes went.

    Each group was built whole, so a later material's call shares it unless that call reads a dropped output
    (share_groups then keeps that call's own copy)."""
    made = [g for g in (bpy.data.node_groups.get(n) for n in names) if g is not None]
    groups = [g for g in made if KEY_FP in g]
    roots = [g for g in made if KEY_FP not in g and bpy.data.materials.get(g.name) is not None]
    gone, touched = 0, set()
    for _ in range(16):
        used = _live_outputs()
        changed = False
        for g in groups:
            if g.users == 0:
                continue
            drop = [it for it in _outputs(g) if it.identifier not in used.get(g, set())]
            if not drop or len(drop) == len(_outputs(g)):
                # read only by dead nodes, which go below, so then nothing reads it
                continue
            for it in drop:
                g.interface.remove(it)
            gone += _drop_dead(g)
            drop_unread_inputs(g)
            touched.add(g.name)
            changed = True
        for t in roots + groups:
            if t.users:
                k = _drop_dead(t)
                if k:
                    gone += k
                    touched.add(t.name)
                    changed = True
                    if t in groups:
                        drop_unread_inputs(t)
        if not changed:
            break
    # groups that only pass inputs on, or wrap one node, are replaced by that node (or the wire)
    for _ in range(8):
        k = _inline_trivial([g for g in groups if g.users], touched)
        if not k:
            break
        gone += k
    # materials' parameters and textures nothing reads any more (an effect folded away)
    for t in roots:
        read = {s.identifier for n in t.nodes if n.bl_idname == "NodeGroupInput" for s in n.outputs if s.is_linked}
        drop = [it for it in _outputs(t, 'INPUT') if it.identifier not in read]
        for it in drop:
            t.interface.remove(it)
        if drop:
            _drop_empty_panels(t)
            for m in bpy.data.materials:
                if m.node_tree is not None and any(n.bl_idname == "ShaderNodeGroup" and n.node_tree == t for n in m.node_tree.nodes):
                    gone += _drop_dead(m.node_tree, kinds=_ZONE_NODES)
            touched.add(t.name)
    unused = [g for g in groups if g.users == 0]
    gone += sum(len(g.nodes) for g in unused)
    if unused:
        bpy.data.batch_remove(unused)
    for name in touched:
        t = bpy.data.node_groups.get(name)
        if t is None:
            continue
        if LAZY_LAYOUT or KEY_LAYOUT in t:      # a tree waiting to be seen is laid out then
            t[KEY_LAYOUT] = 1
            continue
        try:
            layout.arrange(t)
        except Exception:       # cosmetic
            pass
    return gone


_ZONE_NODES = {"NodeClosureInput", "NodeClosureOutput", "ShaderNodeTexImage", "NodeReroute"}


def _drop_empty_panels(tree):
    for it in [it for it in tree.interface.items_tree if it.item_type == 'PANEL']:
        if not any(getattr(c, "parent", None) == it for c in tree.interface.items_tree if c != it):
            tree.interface.remove(it)


_COPY_SKIP = {"name", "location", "location_absolute", "width", "height", "dimensions", "select", "parent",
              "color", "use_custom_color", "show_options", "show_preview", "hide", "mute", "bl_idname", "label",
              "show_texture", "bl_label", "bl_description", "bl_icon", "bl_static_type", "bl_width_default",
              "bl_width_min", "bl_width_max", "bl_height_default", "bl_height_min", "bl_height_max", "type",
              "internal_links", "inputs", "outputs", "rna_type", "is_active_output", "warning_propagation"}
_INLINE = {"ShaderNodeGroup", "ShaderNodeMath", "ShaderNodeVectorMath", "ShaderNodeMix", "ShaderNodeSeparateXYZ",
           "ShaderNodeCombineXYZ", "ShaderNodeVectorRotate", "ShaderNodeClamp", "ShaderNodeMapRange"}


def _through(sock):
    """The output a link into sock comes from, past reroutes (None if unlinked)."""
    while sock.is_linked:
        frm = sock.links[0].from_socket
        if frm.node.bl_idname != "NodeReroute":
            return frm
        sock = frm.node.inputs[0]
    return None


def _inline_trivial(groups, touched):
    """Replace instances of a group that only passes inputs on (its work folded away) or holds one plain node
    (UE's Break Out Float3 Components: one Separate XYZ; a wrapper of one call) with the wire, or a copy of that node
    in the caller, where every value passed on keeps its type. Returns how many group nodes went."""
    from .ue_graph import _socket_value
    gone = 0
    users = {}
    for t in list(bpy.data.node_groups) + [m.node_tree for m in bpy.data.materials if m.node_tree is not None]:
        for n in t.nodes:
            if n.bl_idname == "ShaderNodeGroup" and n.node_tree is not None:
                users.setdefault(n.node_tree.name, []).append((t, n))
    for g in groups:
        work = [n for n in g.nodes if n.bl_idname not in ("NodeFrame", "NodeGroupInput", "NodeGroupOutput", "NodeReroute")]
        if len(work) > 1 or work and work[0].bl_idname not in _INLINE:
            continue
        inner = work[0] if work else None
        go = next((n for n in g.nodes if n.bl_idname == "NodeGroupOutput" and n.is_active_output), None) \
            or next((n for n in g.nodes if n.bl_idname == "NodeGroupOutput"), None)
        if go is None or inner is not None and any(
                l.to_node not in (go,) and l.to_node.bl_idname != "NodeReroute" for o in inner.outputs for l in o.links):
            continue
        for t, node in users.get(g.name, []):
            if _inline_one(t, node, g, go, inner, _socket_value):
                gone += 1
                touched.add(t.name)
    return gone


def _inline_one(t, node, g, go, inner, socket_value):
    def outer_source(gi_out):
        """What feeds the group node input named by gi_out: (output socket, None) or (None, value)."""
        sock = next(s for s in node.inputs if s.identifier == gi_out.identifier)
        src = _through(sock)
        return (src, None) if src is not None else (None, getattr(sock, "default_value", None)), sock
    plan_links, plan_values = [], []
    copy = None
    if inner is not None:
        # the node's inputs: what the caller feeds the group, or the node's own values
        feeds = []
        for s in inner.inputs:
            src = _through(s)
            if src is None:
                feeds.append((s, None, None))
                continue
            if src.node.bl_idname != "NodeGroupInput":
                return False
            (osrc, value), outer = outer_source(src)
            if outer.type != s.type:
                return False
            feeds.append((s, osrc, value))
    # the group node's outputs: what the wire or node gives
    readers = []
    for j, out in enumerate(node.outputs):
        if not out.is_linked:
            continue
        gin = next((s for s in go.inputs if s.identifier == out.identifier), None)
        src = _through(gin) if gin is not None else None
        if src is None:
            v = getattr(gin, "default_value", None) if gin is not None else None
            if v is None:
                return False
            v = tuple(v)[:3] if hasattr(v, "__len__") else float(v)
            for l in out.links:
                if socket_value(v, l.to_socket) is None:
                    return False
                plan_values.append((l.to_socket, socket_value(v, l.to_socket)))
            continue
        if src.type != out.type:
            return False
        if src.node.bl_idname == "NodeGroupInput":
            (osrc, value), outer = outer_source(src)
            if outer.type != out.type:
                return False
            for l in out.links:
                if osrc is not None:
                    plan_links.append((osrc, l.to_socket))
                else:
                    v = tuple(value)[:3] if hasattr(value, "__len__") else value
                    if socket_value(v, l.to_socket) is None:
                        return False
                    plan_values.append((l.to_socket, socket_value(v, l.to_socket)))
        elif src.node == inner:
            readers.append((src.identifier, [l.to_socket for l in out.links]))
        else:
            return False
    if inner is not None:
        copy = t.nodes.new(inner.bl_idname)
        for prop in inner.bl_rna.properties:
            k = prop.identifier
            if k in _COPY_SKIP or prop.is_readonly and k != "node_tree":
                continue
            try:
                setattr(copy, k, getattr(inner, k))
            except (AttributeError, TypeError, ValueError):
                pass
        copy.location, copy.parent = node.location, node.parent
        copy.label = inner.label if inner.bl_idname == "ShaderNodeGroup" else g.name
        for k in node.keys():
            copy[k] = node[k]
        for s, osrc, value in feeds:
            c = next(x for x in copy.inputs if x.identifier == s.identifier)
            if osrc is not None:
                t.links.new(osrc, c)
            elif value is not None and hasattr(c, "default_value"):
                c.default_value = value
            elif hasattr(s, "default_value") and hasattr(c, "default_value"):
                c.default_value = s.default_value
        for ident, socks in readers:
            o = next(x for x in copy.outputs if x.identifier == ident)
            for to in socks:
                t.links.new(o, to)
    for frm, to in plan_links:
        t.links.new(frm, to)
    for to, v in plan_values:
        for l in list(to.links):
            t.links.remove(l)
        to.default_value = v
    t.nodes.remove(node)
    return True


class pruning:
    """With-block for an import: prunes the function groups made in it afterwards (prune_groups)."""

    def __enter__(self):
        self.before = {g.name for g in bpy.data.node_groups}
        self.gone = 0
        return self

    def __exit__(self, kind, _value, _tb):
        if kind is None:
            self.gone = prune_groups([g.name for g in bpy.data.node_groups if g.name not in self.before])
            # then bundle their Material Attributes, module to module (bundles.py)
            from . import bundles
            new = [g for g in bpy.data.node_groups if g.name not in self.before and KEY_FP in g]
            self.bundled = bundles.bundle_attributes(new, KEY_FP, _KNOWN)
        return False


# ------------------------------------------------------------------ layout when seen
def arrange_pending(tree):
    """Lay out a tree built with LAZY_LAYOUT (once; a failure just leaves it unarranged)."""
    if tree is None or KEY_LAYOUT not in tree:
        return False
    del tree[KEY_LAYOUT]
    try:
        layout.arrange(tree)
    except Exception as e:      # cosmetic
        print("[material_porter] layout of %s: %s" % (tree.name, e))
    return True


def _layout_in_view():
    """Timer: lay out the trees node editors show (a material, the entered group) if pending."""
    try:
        for win in bpy.context.window_manager.windows:
            for area in win.screen.areas:
                if area.type != 'NODE_EDITOR':
                    continue
                space = area.spaces.active
                done = False
                for tree in (getattr(space, "edit_tree", None), getattr(space, "node_tree", None)):
                    done |= arrange_pending(tree)
                if done:
                    area.tag_redraw()
    except Exception:
        pass        # no window (background mode) or the file is closing
    return 0.5


def ensure_layout_timer():
    if not bpy.app.timers.is_registered(_layout_in_view):
        bpy.app.timers.register(_layout_in_view, first_interval=0.5, persistent=True)


# ------------------------------------------------------------------ one material
def mark_bounds(objects):
    """UE's Object Position is an object's bounds centre. Each mesh object carries its own (local, Blender metres)
    for its materials to read, so materials stay shared between objects."""
    for o in objects:
        if o is not None and o.type == 'MESH':
            o[BOUNDS_CENTRE] = [sum(c[i] for c in o.bound_box) / 8.0 for i in range(3)]
            o[BOUNDS_MIN] = [min(c[i] for c in o.bound_box) for i in range(3)]
            o[BOUNDS_MAX] = [max(c[i] for c in o.bound_box) for i in range(3)]
            # the same box on the mesh's points (UE's PreSkinnedLocalBounds, env.preskinned_bounds);
            # a part keeps its own when parts are joined, a joined mesh isn't marked over
            me = o.data
            if PART_BOUNDS_MIN not in me.attributes and len(me.vertices):
                for name, v in ((PART_BOUNDS_MIN, o[BOUNDS_MIN]), (PART_BOUNDS_MAX, o[BOUNDS_MAX])):
                    me.attributes.new(name, 'FLOAT_VECTOR', 'POINT').data.foreach_set("vector", list(v) * len(me.vertices))
            # the armature's head (local, Blender metres), what the game sets HeadSocketLocation to:
            # the head's effects bone if it has one (Cyclo's HeadFX: his dome's centre), else the head bone (at the neck)
            arm = o.find_armature()
            bones = {b.name.lower(): b for b in arm.data.bones} if arm is not None else {}
            bone = bones.get("headfx") or bones.get("head")
            if bone is not None:
                o[HEAD_SOCKET] = list(o.matrix_world.inverted() @ (arm.matrix_world @ bone.head_local))


def mark_shared_bounds(objects):
    """Give each part of an outfit (each its own object) the union of all parts' bounds, in its own space.

    In the game every part takes the whole character's bounds (a feet-to-head gradient runs over the head too:
    Gummi Team Leader's pink to purple). Merge Armatures' joined mesh already has it."""
    from mathutils import Vector
    objs = [o for o in objects if o is not None and o.type == 'MESH']
    if len(objs) < 2:
        return
    pts = [o.matrix_world @ Vector(c) for o in objs for c in o.bound_box]
    lo = [min(p[i] for p in pts) for i in range(3)]
    hi = [max(p[i] for p in pts) for i in range(3)]
    corners = [Vector((x, y, z)) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]
    for o in objs:
        inv = o.matrix_world.inverted()
        local = [inv @ c for c in corners]
        o[BOUNDS_MIN] = [min(p[i] for p in local) for i in range(3)]
        o[BOUNDS_MAX] = [max(p[i] for p in local) for i in range(3)]
        o[BOUNDS_CENTRE] = [(a + b) * 0.5 for a, b in zip(o[BOUNDS_MIN], o[BOUNDS_MAX])]


def build_one(entry, app, objects=(), make_env=None):
    """Build a Blender material for one bundle entry. Returns (material, notes).

    make_env(app, entry, objects) supplies the caller's own env (a time of day's, whose values follow its hour)."""
    mark_bounds(objects)
    if not entry.get("graph"):
        # master cooked without its graph (UEFN island material): approximate it
        from .fallback import build_fallback
        return build_fallback(entry, app, objects, MaterialEnv, settings,
                              (PREFIX, KEY_PATH, KEY_REV, BUILD_REVISION, KEY_VARIANT))
    mat = bpy.data.materials.new(PREFIX + entry["name"])
    tree = mat.node_tree
    tree.nodes.clear()
    root = bpy.data.node_groups.new(mat.name, "ShaderNodeTree")
    env = make_env(app, entry, objects) if make_env else MaterialEnv(app, entry, objects)
    env.root = root
    tr = Translator(root, env)
    env.h[0] = tr
    # a particle's material also moves its vertices (World Position Offset: a zero-width lightning strip
    # thickened towards the camera, a mesh bent along a spline)
    names = CARRIED + ("WorldPositionOffset",) if entry.get("particle") or entry.get("moves") else CARRIED
    vals = tr.material_attributes(app.local(entry["graph"]), names)
    # UE clamps emissive at 0 unless bAllowNegativeEmissiveColor is set; a negative glow
    # (a weapon's time-of-day emissive, -5 by day: Bonerattler SMG) lost its colour here, turning green into magenta
    emissive = vals.get("EmissiveColor")
    if emissive is not None and not (entry.get("asset") or {}).get("bAllowNegativeEmissiveColor") \
            and not (emissive.const and min(_comps(emissive.s)[:3]) >= 0.0):
        with tr.at("Output"):
            vals["EmissiveColor"] = tr.vmath('MAXIMUM', tr.as3(emissive), tr.const((0.0, 0.0, 0.0), 3), out_w=3)
    assemble(tr, mat, vals, settings(entry))
    groups = [ft.tree for ft in tr.functions.values()]
    # Merge alike nodes translation made (a texture per pin read, a group node per output of one call) and
    # drop nodes reaching no output. Runs before sharing, so twins compare folded groups, and before the
    # parameters are settled (a parameter only a dropped node read goes with it).
    memo = {}
    for t in groups + [root]:
        merge_duplicates(t, memo)
    for t in groups:
        drop_unread_inputs(t)
    values = env.finish_parameters()
    _subsurface_panel(root)
    # twins of earlier materials' groups go first: laying out a group about to be thrown away
    # was half of all layout time
    shared, kept = share_groups(groups, owners=[root])
    if LAZY_LAYOUT:
        for t in kept + [root]:
            t[KEY_LAYOUT] = 1
        ensure_layout_timer()
    else:
        for t in kept + [root]:
            try:
                layout.arrange(t)
            except Exception as e:     # cosmetic: never lose a build to layout
                env.note("layout of %s: %s" % (t.name, e))
    node = _material_node(mat, root, entry["name"], values, env.node_width())
    if _texture_zones(mat, node, env):
        if LAZY_LAYOUT:
            mat.node_tree[KEY_LAYOUT] = 1
        else:
            try:
                layout.arrange(mat.node_tree)
            except Exception as e:     # cosmetic
                env.note("layout of %s: %s" % (mat.name, e))
    mat[KEY_PATH] = entry["path"]
    mat[KEY_REV] = BUILD_REVISION
    if entry.get("variant"):
        mat[KEY_VARIANT] = entry["variant"]
    if env.reusable:
        _record_shape(mat, root, env, entry)
    notes = list(env.notes) + sorted(set(w for w in tr.warnings + [w for ft in tr.functions.values()
                                                                    for w in ft.tr.warnings]))
    if shared:
        notes.append("%d function groups shared with earlier materials" % shared)
    # the time of day's height fog, if the file has one
    world.apply_fog(mat)
    return mat, notes


def _subsurface_panel(root):
    """Put the subsurface inputs (see assemble) in the Pre FX function's panel if there is one
    (a character's: beside SkinSubsurfaceIntensity), else beside the game's own subsurface parameter."""
    iface = root.interface
    items = list(iface.items_tree)
    names = (SKIN_SUBSURFACE_INPUT, SUBSURFACE_INTENSITY, BASE_SUBSURFACE, SCATTER_DISTANCE, SUBSURFACE_RADIUS, PROFILE_COLOUR)
    mine = sorted((it for it in items if it.item_type == 'SOCKET' and it.in_out == 'INPUT' and it.name in names),
                  key=lambda it: names.index(it.name))
    if not mine:
        return
    games = sorted((it for it in items if it.item_type == 'SOCKET' and it.in_out == 'INPUT' and it.name not in names
                    and getattr(it.parent, "name", "") and ("subsurface" in it.name.lower() or "sss" in it.name.lower())),
                   key=lambda it: "skinsubsurface" not in it.name.lower())
    panel = next((it for it in items if it.item_type == 'PANEL' and it.name.endswith("Pre FX")), None)
    if panel is not None:
        game = next((it for it in games if it.parent == panel), None)
    else:
        game = games[0] if games else None
        panel = game.parent if game is not None else None
    if panel is None:
        return
    at = game.position + 1 if game is not None else len(panel.interface_items)
    for k, it in enumerate(mine):
        iface.move_to_parent(it, panel, at + k)


# ------------------------------------------------------------------ same shape, other values
def shape_key(entry):
    """What decides a build's node trees beyond its parameter values: the master's graph, static switches and masks,
    blend/shading overrides and subsurface profile. Instances alike in all of it differ only in parameter values
    (the material's group-node inputs) and texture images."""
    k = {g: entry.get(g) for g in ("graph", "master", "switches", "masks", "overrides", "asset", "subsurface", "sprite", "ribbon", "particle", "moves", "shell", "cosmetic", "fixed", "landscape_layers")}
    k["head"] = _is_head(entry)
    # with a time of day in the file collections stay live; without one they are folded in
    k["day"] = world.has_day()
    return hashlib.sha1(json.dumps(k, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _reachable(root):
    out, todo = [], [root]
    while todo:
        t = todo.pop()
        if t in out:
            continue
        out.append(t)
        todo += [n.node_tree for n in t.nodes if n.bl_idname == "ShaderNodeGroup" and n.node_tree is not None]
    return out


def _record_shape(mat, root, env, entry):
    """Mark a build as copyable by other instances of its shape: each image node with the texture parameters it came
    from, each input with its parameter and graph default."""
    key_of = {img.name: key for key, img in env._images.items() if img is not None}
    for t in [mat.node_tree] + _reachable(root):
        for n in t.nodes:
            if n.bl_idname != "ShaderNodeTexImage" or n.image is None:
                continue
            key = key_of.get(n.image.name)
            if key in env.tex_params:
                # the parameter the translator says it samples, else every one that gave this texture
                exact = n.get("mp_tex_param")
                n["mp_tex"] = [exact] if exact else sorted(env.tex_params[key])
                n["mp_key"] = key
                n["mp_normal"] = key in env.tex_normal
    params = []
    for key, (item, _v, _value, _panel) in env._params.items():
        kind, name = key[0], key[3:]
        default = env.graph_defaults.get(("V: " if kind == "A" else key[:3]) + name)
        params.append([item.identifier, kind, name, list(default) if isinstance(default, tuple) else default])
    root["mp_params"] = json.dumps(params)
    mat["mp_tex_defaults"] = json.dumps(env.tex_default)
    mat[KEY_SHAPE] = shape_key(entry)


def _root_of(mat):
    return next((n.node_tree for n in mat.node_tree.nodes
                 if n.bl_idname == "ShaderNodeGroup" and n.node_tree is not None and "mp_params" in n.node_tree), None)


def find_shape(shape):
    """A material this file already built for that shape, current and copyable."""
    return next((m for m in bpy.data.materials if m.get(KEY_SHAPE) == shape and m.get(KEY_REV, 1) >= BUILD_REVISION
                 and _root_of(m) is not None), None)


def build_like(src, entry, app):
    """Another instance of src's shape without translating it: src's trees are copied with swapped images where
    images differ and shared where they don't, with the parameter values on its own group node.
    None when a node's parameters now point at different textures (can't tell which one it is)."""
    root1 = _root_of(src)
    if root1 is None:
        return None
    textures = match_names(entry)["textures"]
    try:
        defaults = json.loads(src.get("mp_tex_defaults", "{}"))
        params = json.loads(root1["mp_params"])
    except (ValueError, KeyError):
        return None
    # a colour socket would clamp a negative value of this instance's, so build anew
    colour = {it.identifier for it in root1.interface.items_tree
              if it.item_type == 'SOCKET' and it.in_out == 'INPUT' and it.socket_type == 'NodeSocketColor'}
    vectors = entry.get("vectors") or {}
    if any(kind == "V" and ident in colour and any(float(x) < 0.0 for x in tuple(vectors.get(name) or ())[:3])
           for ident, kind, name, _default in params):
        return None
    ambiguous = object()

    def new_key(names):
        keys = {textures.get(p, defaults.get(p)) for p in names}
        return keys.pop() if len(keys) == 1 else ambiguous
    changed = {}

    def changes(tree):
        if tree.name in changed:
            return changed[tree.name]
        changed[tree.name] = False      # guards against cycles
        c = False
        for n in tree.nodes:
            if n.bl_idname == "ShaderNodeTexImage" and "mp_tex" in n:
                k = new_key(list(n["mp_tex"]))
                if k is ambiguous:
                    raise LookupError(n.name)
                c = c or k != n["mp_key"]
            elif n.bl_idname == "ShaderNodeGroup" and n.node_tree is not None:
                c = changes(n.node_tree) or c
        changed[tree.name] = c
        return c
    try:
        changes(root1)
    except LookupError:
        return None
    # the material's own tree holds the textures (the group has none)
    if any(new_key(list(n["mp_tex"])) is ambiguous for n in src.node_tree.nodes
           if n.bl_idname == "ShaderNodeTexImage" and "mp_tex" in n):
        return None
    env = MaterialEnv(app, entry, [])
    copies = {}

    def clone(tree):
        if not changed.get(tree.name):
            return tree
        if tree.name in copies:
            return copies[tree.name]
        t2 = tree.copy()
        copies[tree.name] = t2
        if KEY_FP in t2:
            del t2[KEY_FP]      # no longer the twin of its source
        for n in t2.nodes:
            if n.bl_idname == "ShaderNodeTexImage" and "mp_tex" in n:
                k = new_key(list(n["mp_tex"]))
                if k != n["mp_key"]:
                    n.image = env.texture(k, "Normal" if n.get("mp_normal") else "")
                    n["mp_key"] = k
            elif n.bl_idname == "ShaderNodeGroup" and n.node_tree is not None:
                n.node_tree = clone(n.node_tree)
        return t2

    mat = src.copy()
    mat.name = PREFIX + entry["name"]
    swapped = 0
    for n in mat.node_tree.nodes:
        if n.bl_idname == "ShaderNodeTexImage" and "mp_tex" in n:
            k = new_key(list(n["mp_tex"]))
            if k != n["mp_key"]:
                n.image = env.texture(k, "Normal" if n.get("mp_normal") else "")
                n["mp_key"] = k
                swapped += 1
    root2 = clone(root1)
    if root2 is not root1:
        root2.name = mat.name
    node = next(n for n in mat.node_tree.nodes if n.bl_idname == "ShaderNodeGroup" and n.node_tree == root1)
    node.node_tree = root2
    node.label = entry["name"]
    scalars, vectors = entry.get("scalars") or {}, entry.get("vectors") or {}
    by_id = {s.identifier: s for s in node.inputs}
    for ident, kind, name, default in params:
        sock = by_id.get(ident)
        if sock is None:
            continue
        if kind == "P":
            sock.default_value = float(scalars.get(name, default if default is not None else sock.default_value))
        else:
            rgba = vectors.get(name, default)
            if rgba is None:
                continue
            rgba = tuple(rgba) + (1.0,) * (4 - len(rgba))
            sock.default_value = fit_socket(sock, rgba) if kind == "V" else float(rgba[3])
    mat[KEY_PATH] = entry["path"]
    mat[KEY_REV] = BUILD_REVISION
    mat["mp_copied_from"] = src.name
    if entry.get("variant"):
        mat[KEY_VARIANT] = entry["variant"]
    elif KEY_VARIANT in mat:
        del mat[KEY_VARIANT]
    world.apply_fog(mat)
    return mat, list(env.notes) + ["same shape as %s: copied, %d texture(s) and %d group(s) with other images"
                                   % (src.name, swapped, len(copies))]
