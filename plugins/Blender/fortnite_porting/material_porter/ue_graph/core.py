"""Values, graphs, function trees and the helpers the translator modules share."""
import hashlib
import json
import math
import os
import re
import sys

# Fortnite's layered masters nest expressions 130+ deep (reroute chains in a
# customization function), several Python frames each
sys.setrecursionlimit(max(sys.getrecursionlimit(), 20000))

try:
    from ..nodelib import SECTION_KEY
except ImportError:          # outside the build (a probe): no layout
    SECTION_KEY = "fpv4_section"


def socket_name(name):
    """Blender sockets hold 63 bytes: cut longer names and tag them with a hash so they stay distinct."""
    name = str(name)
    if len(name.encode("utf-8")) <= 63:
        return name
    import hashlib
    tag = hashlib.sha1(name.encode("utf-8")).hexdigest()[:6]
    cut = name
    while len((cut + "~" + tag).encode("utf-8")) > 63:
        cut = cut[:-1]
    return cut.rstrip() + "~" + tag


def section_name(fname):
    """"MF_Sky_HighAltitudeClouds_Ch7" -> "High Altitude Clouds"."""
    s = re.sub(r"^(MF|M)_", "", fname)
    s = re.sub(r"(^|_)(Ch\d+|Sky)(?=_|$)", "", s).strip("_")
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", s.replace("_", " "))
    return s.strip() or fname

UE_DEFAULTS = {
    ("Add", "ConstA"): 0.0, ("Add", "ConstB"): 1.0,
    ("Subtract", "ConstA"): 1.0, ("Subtract", "ConstB"): 1.0,
    ("Multiply", "ConstA"): 0.0, ("Multiply", "ConstB"): 1.0,
    ("Divide", "ConstA"): 1.0, ("Divide", "ConstB"): 2.0,
    ("Max", "ConstA"): 0.0, ("Max", "ConstB"): 1.0,
    ("Min", "ConstA"): 0.0, ("Min", "ConstB"): 1.0,
    ("LinearInterpolate", "ConstA"): 0.0, ("LinearInterpolate", "ConstB"): 1.0,
    ("LinearInterpolate", "ConstAlpha"): 0.5,
    ("Power", "ConstExponent"): 2.0,
    ("Step", "ConstY"): 0.0, ("Step", "ConstX"): 1.0,
    ("SmoothStep", "ConstMin"): 0.0, ("SmoothStep", "ConstMax"): 1.0,
    ("SmoothStep", "ConstValue"): 0.0,
    ("Distance", "ConstA"): 0.0, ("Distance", "ConstB"): 0.0,
    ("If", "ConstB"): 0.0,
}

# What a shipping PC build compiles per compile-time switch: the first of these
# pins that is linked (UE falls back to Default for an unlinked one).
#   QualitySwitch       Inputs[3] = Epic
#   FeatureLevelSwitch  Inputs[4] = SM6 (DX12)
#   ShadingPathSwitch   Inputs[0] = Deferred (serialized as "Inputs")
# Virtual-texture and distance-field switches take "No", the branch Blender can
# draw ("Yes" samples renderer-only data).
COMPILE_SWITCHES = {
    "QualitySwitch": ("Inputs[3]", "Default"),
    "FeatureLevelSwitch": ("Inputs[4]", "Default"),
    "ShadingPathSwitch": ("Inputs", "Default"),
    "ReflectionCapturePassSwitch": ("Default",),
    "ShaderStageSwitch": ("PixelShader",),
    "RayTracingQualitySwitch": ("Normal",),
    "PathTracingQualitySwitch": ("Normal",),
    "VirtualTextureFeatureSwitch": ("No",),
    "DistanceFieldsRenderingSwitch": ("No",),
    "PreviousFrameSwitch": ("CurrentFrame",),
    "NaniteReplace": ("Default",),
    "MaterialProxyReplace": ("Realtime",),
    "ShadowReplace": ("Default",),
    "RuntimeVirtualTextureReplace": ("Default",),
}

BOUNDS_CENTRE = "mp_bounds_centre"     # object bounds centre (local, Blender metres): UE's Object Position
BOUNDS_MIN = "mp_bounds_min"       # object bounding box (local, Blender metres): UE's local bounds,
BOUNDS_MAX = "mp_bounds_max"       # read per object (a shared function's group holds no object's numbers)
PART_BOUNDS_MIN = "mp_part_bounds_min"   # a mesh part's own bounding box (local, Blender metres), a point
PART_BOUNDS_MAX = "mp_part_bounds_max"   # attribute that stays with the part when parts are joined
HEAD_SOCKET = "mp_head_socket"     # object's armature head (local, Blender metres): what a game blueprint sets HeadSocketLocation to
SHELL_LAYER = "mp_shell_layer"     # shell fur (UE's ShellMesh; shells.py), per point of each copy: layer, 1 to count - 1 (0: the mesh itself)
SHELL_LAYER_N = "mp_shell_layer_n" # layer / (count - 1), the tip is 1
SHELL_COUNT = "mp_shell_count"     # layer count
SHELL_OFFSET = "mp_shell_offset"   # where the point goes (local, Blender units): vertex normal * depth * layer
SHELL_VECTOR = "mp_shell_vector"   # the full depth's offset (normal * depth): what the shell material's WPO moves it by
# Transform / TransformPosition spaces, by enum suffix
VECTOR_SPACES = {"Tangent": "tangent", "Local": "local", "World": "world", "View": "view", "Camera": "view",
                 "ParticleWorld": "world", "Instance": "local"}
POSITION_SPACES = {"Local": "local", "World": "world", "TranslatedWorld": "translated", "View": "view",
                   "Camera": "view", "Particle": "world", "Instance": "local", "PeriodicWorld": "periodic",
                   "FirstPersonTranslatedWorld": "translated"}

STATIC_BOOL_INPUT = "EFunctionInputType::FunctionInput_StaticBool"
ATTRIBUTES_INPUT = "EFunctionInputType::FunctionInput_MaterialAttributes"

# UE material attributes (FMaterialAttributeDefinitionMap): name, GUID as written in
# the dumps, width, default. GUIDs are matched against the dumps' Set nodes, whose
# input names spell the attribute out.
ATTRIBUTES = [
    ("BaseColor", "69B8D336-16ED4D49-9AA49729-2F050F7A", 3, (0.0, 0.0, 0.0)),
    ("Metallic", "57C3A161-7F064296-B00B24A5-A496F34C", 1, 0.0),
    ("Specular", "9FDAB399-25564CC9-8CD2D572-C12C8FED", 1, 0.5),
    ("Roughness", "D1DD967C-4CAD47D3-9E6346FB-08ECF210", 1, 0.5),
    ("Anisotropy", "55E2B4FB-C1C54DB2-9F11875F-7231EB1E", 1, 0.0),
    ("EmissiveColor", "B769B54D-D08D4440-ABC21BA6-CD27D0E2", 3, (0.0, 0.0, 0.0)),
    ("Opacity", "B8F50FBA-2A754EC1-9EF672CF-EB27BF51", 1, 1.0),
    ("OpacityMask", "679FFB17-2BB5422C-AD520483-166E0C75", 1, 1.0),
    ("Normal", "0FA2821A-200F4A4A-B719B789-C1259C64", 3, (0.0, 0.0, 1.0)),
    ("Tangent", "D5F8E9CF-CDC3468D-B10E4465-596A7BBA", 3, (1.0, 0.0, 0.0)),
    ("WorldPositionOffset", "F905F895-D5814314-916D2434-8C40CE9E", 3, (0.0, 0.0, 0.0)),
    ("SubsurfaceColor", "5B8FC679-51CE4082-9D777BEE-F4F72C44", 3, (1.0, 1.0, 1.0)),
    ("ClearCoat", "9E502E69-3C8F48FA-94645CFD-28E5428D", 1, 1.0),
    ("ClearCoatRoughness", "BE4F2FFD-12FC4296-B0124EEA-12C28D92", 1, 0.1),
    ("AmbientOcclusion", "E8EBD0AD-B1654CBE-B079C3A8-B39B9F15", 1, 1.0),
    ("Refraction", "D0B0FA03-14D74455-A851BAC5-81A0788B", 3, (1.0, 0.0, 0.0)),
    ("PixelDepthOffset", "0AC97EC3-E3D047BA-B610167D-C4D919FF", 1, 0.0),
    ("ShadingModel", "D9423FFF-D77E4D82-8FF9CF5E-055D1255", 1, 1.0),
    ("Displacement", "199A7166-C67041DC-A68EAD0D-7017D0AD", 1, 0.0),
    ("ClearCoatBottomNormal", "AA3D5C04-16294716-BBDEC869-6A27DD72", 3, (0.0, 0.0, 1.0)),
    ("BentNormal", "FBD7B46E-B1234824-BDE76B23-609F984C", 3, (0.0, 0.0, 1.0)),
    ("TransmittanceColor", "F2D8C70E-42ECA0D1-4652D0AD-B785A065", 3, (0.5, 0.5, 0.5)),
]
ATTRIBUTE_OF_GUID = {guid: name for name, guid, _, _ in ATTRIBUTES}
ATTRIBUTE_WIDTH = {name: w for name, _, w, _ in ATTRIBUTES}
ATTRIBUTE_DEFAULT = {name: d for name, _, _, d in ATTRIBUTES}
# the vertex-shader attributes (BlendMaterialAttributes' VertexAttributeBlendType)
VERTEX_ATTRIBUTES = {"WorldPositionOffset", "Displacement"} | {"CustomizedUV%d" % i for i in range(8)}
# BreakMaterialAttributes outputs in order (each index feeds the
# MakeMaterialAttributes pin of the same name)
BREAK_ORDER = (["BaseColor", "Metallic", "Specular", "Roughness", "Anisotropy", "EmissiveColor", "Opacity",
                "OpacityMask", "Normal", "Tangent", "WorldPositionOffset", "SubsurfaceColor", "ClearCoat",
                "ClearCoatRoughness", "AmbientOcclusion", "Refraction"]
               + ["CustomizedUV%d" % i for i in range(8)] + ["PixelDepthOffset", "ShadingModel", "Displacement"])
# MakeMaterialAttributes pin -> attribute
MAKE_PINS = dict({n: n for n in BREAK_ORDER if not n.startswith("CustomizedUV")},
                 CustomizedUVs="CustomizedUV0", **{"CustomizedUVs[%d]" % i: "CustomizedUV%d" % i for i in range(1, 8)})
# a material's separate output pins -> attribute (non-attribute materials)
PIN_ATTRIBUTE = dict({n: n for n in MAKE_PINS.values()}, CustomData0="ClearCoat", CustomData1="ClearCoatRoughness",
                     ShadingModelFromMaterialExpression="ShadingModel")
# Attributes a Blender material has a slot for: they cross function groups and
# are what material_attributes() returns
CARRIED = ("BaseColor", "Metallic", "Specular", "Roughness", "Anisotropy", "EmissiveColor", "Opacity",
           "OpacityMask", "Normal", "Tangent", "SubsurfaceColor", "ClearCoat", "ClearCoatRoughness",
           "AmbientOcclusion", "ShadingModel")
# EMaterialShadingModel, in order (the ShadingModel expression's value)
SHADING_MODELS = ["MSM_Unlit", "MSM_DefaultLit", "MSM_Subsurface", "MSM_PreintegratedSkin", "MSM_ClearCoat",
                  "MSM_SubsurfaceProfile", "MSM_TwoSidedFoliage", "MSM_Hair", "MSM_Cloth", "MSM_Eye",
                  "MSM_SingleLayerWater", "MSM_ThinTranslucent", "MSM_Strata"]


def pretty(name):
    """"EmissiveColor" -> "Emissive Color" (socket names)."""
    return re.sub(r"([a-z])([A-Z0-9])", r"\1 \2", name)


def attribute_name(guid, label=None):
    """An attribute GUID's name; an unknown one by the label its node gives it."""
    if guid in ATTRIBUTE_OF_GUID:
        return ATTRIBUTE_OF_GUID[guid]
    if label and label != "None":
        m = re.match(r"Customized ?UVs? ?(\d)", label)
        return "CustomizedUV%s" % m.group(1) if m else label.replace(" ", "")
    return guid


def attribute_default(name):
    """UE's default for an attribute a value doesn't set, as a constant Val."""
    d = ATTRIBUTE_DEFAULT.get(name, 0.0)
    if isinstance(d, tuple):
        return Val(d, ATTRIBUTE_WIDTH.get(name, 3))
    return Val(float(d), 1)


class Attrs:
    """A Material Attributes value. Each attribute is made on first read (an
    unread one never becomes nodes, as in UE) from `thunks` (the ones this
    value sets), else `base` (the value it was set on), else UE's default."""

    def __init__(self, base=None, thunks=None):
        self.base, self.thunks, self.vals = base, dict(thunks or {}), {}

    def get(self, name):
        if name not in self.vals:
            if name in self.thunks:
                v = self.thunks[name]()
            elif self.base is not None:
                v = self.base.get(name)
            else:
                v = attribute_default(name)
            self.vals[name] = v
        return self.vals[name]


class BlendAttrs(Attrs):
    """BlendMaterialAttributes: per attribute, lerp(A, B, Alpha), or A or B
    alone (the pixel / vertex blend types). The shading model can't be
    lerped: Alpha >= 0.5 picks B."""

    def __init__(self, tr, a, b, alpha, pixel, vertex):
        super().__init__()
        self.tr, self.a, self.b, self.alpha, self.pixel, self.vertex = tr, a, b, alpha, pixel, vertex
        self.base = None

    def get(self, name):
        if name not in self.vals:
            mode = self.vertex if name in VERTEX_ATTRIBUTES else self.pixel
            if mode == "UseA":
                v = self.a.get(name)
            elif mode == "UseB":
                v = self.b.get(name)
            else:
                va, vb = self.a.get(name), self.b.get(name)
                if va.const and vb.const and va.w == vb.w and va.s == vb.s:
                    v = va
                else:
                    tr = self.tr
                    prev = tr.activate()
                    try:
                        t = self.alpha()
                        if name == "ShadingModel":
                            ge = tr.binop('SUBTRACT', tr.const(1.0), tr.math('LESS_THAN', t, tr.const(0.5))) \
                                if not t.const else tr.const(1.0 if t.s >= 0.5 else 0.0)
                            v = tr.select(ge, va, vb, label="shading model")
                        else:
                            v = tr.lerp(va, vb, t, label="blend " + pretty(name))
                    finally:
                        prev.activate()
            self.vals[name] = v
        return self.vals[name]


class MapAttrs(Attrs):
    """Attributes combined one by one: fn(a.get(n), b.get(n), ...), made in
    translator tr when read (If, Switch, lerp over Material Attributes)."""

    def __init__(self, tr, fn, parts):
        super().__init__()
        self.tr, self.fn, self.parts = tr, fn, parts

    def get(self, name):
        if name not in self.vals:
            prev = self.tr.activate()
            try:
                self.vals[name] = self.fn(*[p.get(name) for p in self.parts])
            finally:
                prev.activate()
        return self.vals[name]


def as_attrs(v):
    """A Material Attributes operand: its Attrs, or UE's defaults."""
    return v.s if v is not None and isinstance(v.s, Attrs) else Attrs()


class OpenAttrs(Attrs):
    """A function's Material Attributes input: each CARRIED attribute a group
    socket, made when the body first reads it."""

    def __init__(self, ft, fid, label):
        super().__init__()
        self.ft, self.fid, self.label = ft, fid, label

    def get(self, name):
        if name not in self.vals:
            if name not in self.ft.tr.carried:
                v = attribute_default(name)
            else:
                w = ATTRIBUTE_WIDTH.get(name, 3)
                sock = self.ft._new_input("%s: %s" % (self.label, pretty(name)), w, ("ue_attr", (self.fid, name)))
                v = Val(self.ft.gi.outputs[sock], w)
            self.vals[name] = v
        return self.vals[name]


# Custom HLSL nodes rebuilt by hand: code with whitespace collapsed -> the
# Translator method that builds it (anything else is reported)
CUSTOM_SNIPPETS = {
    # MF_FPMeshCameraOffset: a first-person mesh's own offset where FP is set, else the one passed
    "if(FP) { return WPO_FP; } return WPO;": "custom_fp_select",
    # NaN: UE drops a vertex given one (shell layers culled at a distance, MF_Fur_Shells).
    # The If branch that gives it is never taken here: Blender keeps every layer
    "const float fNaN = 0.0f / 0.0f; return fNaN;": "custom_nan",
    "float HeightDensity = Density; if(NormAltitude<0.05 || NormAltitude>0.95) "
    "{ HeightDensity = 0.0; } return HeightDensity;": "custom_height_band",
    # 4-layer base samplers (MF_ConditionalColor/NormalTextureSample): a texture
    # read only where the layer shows
    "MaterialFloat4 Color = 0; BRANCH if ((bool)bDoWork) { Color = ProcessMaterialColorTextureLookup("
    "Texture2DSample(Tex, GetMaterialSharedSampler(TexSampler, View_MaterialTextureBilinearWrapedSampler), UVs)); } "
    "return Color;": "custom_conditional_color",
    "MaterialFloat4 Normal = MaterialFloat4(0, 0, 1, 0); BRANCH if ((bool)bDoWork) { Normal = UnpackNormalMap("
    "Texture2DSample(Tex, GetMaterialSharedSampler(TexSampler, View_MaterialTextureBilinearWrapedSampler), UVs)); } "
    "return Normal;": "custom_conditional_normal",
    "float2 box = step(float2(0, 0), uv) - step(float2(1,1), uv); return (box.x * box.y) * color;": "custom_uv_box",
    # post-process ambient cubemap tint (GetAmbientCubemapTint): none is set, the view keeps the default
    "View.AmbientCubemapTint": "custom_white4",
}


# Same, keyed by the first 8 hex digits of the collapsed code's SHA-1 (the
# census's "HLSL:<hash>" names), for long or generic code
CUSTOM_HASHES = {
    "1be3a427": "custom_ggx_d",             # GGXSpecular (engine): GGX's distribution term for a light
    "58c98080": "custom_dither5",           # Mod((uint)p.x + 2 * (uint)p.y, 5)  DitherTemporalAA
    "accf1f15": "custom_primitive_index",   # GetPrimitiveData(Parameters).PersistentPrimitiveIndex
    "b4a2625d": "custom_triplanar_normals",  # SelectAndTransformNormals (MF_Triplanar_TransformNormals)
    "5b88b88a": "custom_rain_occluder",     # if (In == 0) return 1; return OccluderOn;
    "52a2c6d4": "custom_array_conditional",  # Mask < 0.0001 ? Default : Texture2DArraySampleGrad(...)
    "de983c29": "custom_pcg3d16",           # pcg3d16 integer hash
    "9f847d24": "custom_select_0_15",       # Select in [0, 15] ? A : B
    "c98e3fcb": "custom_select4",           # inputs[clamp(int(selection - 1), 0, 3)]
    "1b33d4b8": "custom_array_sample",      # Texture2DArraySample(Tex, TexSampler, UV)
    "a57e06f4": "custom_identity",          # Vector
    "7b8cb983": "custom_identity",          # Scalar
    "1e3af51b": "custom_select3",           # Select == 1 ? A : Select == 2 ? B : C
    "85321e46": "custom_select_eq4",        # if Select == 1 A, == 2 B, == 3 C, else D (LEGO face atlas channels)
    "c8faff65": "custom_light_direction",   # return View.DirectionalLightDirection;
    "d30c1e33": "custom_light_color",       # return View.DirectionalLightColor;
    "fae396a2": "custom_world_to_clip",     # mul(float4(In.xyz, 1), WorldToClip)
    "2e04637e": "custom_smoothstep",        # return smoothstep(varMin, varMax, varA);
    "47504a59": "custom_one",               # return View.ViewportScaleUI;
    "ee5dcc61": "custom_sign",              # sign(x)
    "4da71b15": "custom_length",            # length(x)
    "bce084b5": "custom_atan2",             # return (atan2(y,x));
    "dd5fa5b0": "custom_acos",              # return acos(a);  (RotateVector, engine)
    "41a47d1f": "custom_rgb_to_hsv",        # RGBtoHSV (engine)
    "8fb148eb": "custom_cube_corner",       # Center + float3(-Size / 2)  (MF_ProjectUV_WiryPerk)
    "e200a58e": "custom_bounds_min",        # GetPrimitiveData(Parameters).LocalObjectBoundsMin.xyz
    "a48f54dd": "custom_bounds_max",        # ... LocalObjectBoundsMax.xyz
    "8ae85b7f": "custom_bounds_extent",     # ... InstanceLocalBoundsExtent.xyz
    "bcf2b682": "custom_bounds_center",     # ... InstanceLocalBoundsCenter.xyz
    "8b82dce0": "custom_zero",              # (Flags & ..._HAS_INSTANCE_LOCAL_BOUNDS) != 0 ? 1 : 0
    "cd6dc0c4": "custom_tan_half_fov",      # View.ClipToView[0][0]
    "77b1ed60": "custom_fov",               # atan(View.ClipToView[0][0])*2
    "fff78762": "custom_bitfield",          # BitFieldExtractU32(Scalar, BitCount, BitIndex)
    "bd366923": "custom_bitfield_asuint",   # BitFieldExtractU32(asuint(Scalar), BitCount, BitIndex)
    "1ec1b585": "custom_hue_to_rgb",        # Hue = fmod(Hue, 1); R = |6H - 3| - 1 ...
    "aaaad1e2": "custom_clip",              # clip(Opacity - Threshold); return Opacity;
    "5b90577e": "custom_raymarch_2d",       # for (d < marchDistance) { UV += marchDir * d / numSteps; light *= 1 - density } (MF_Raymarched_Smoke_Func)
    "190f0d13": "custom_eye_adaptation_inverse",  # LightValue * exp(-Alpha * log(Adaptation))
    "287306e9": "custom_oct_decode",        # hemi-octahedral -> normal
    "cee1e2ec": "custom_oct_encode",        # normal -> hemi-octahedral
    "eda214ed": "custom_main_pass",         # #if SHADOW_DEPTH_SHADER return Shadow; #endif return Main;
    "f8ae37db": "custom_dither5",           # ((uint)(p.x) + 2 * (uint)(p.y)) % 5
    "ee3c506f": "custom_taa_params",        # View.TemporalAAParams (float2 here)
    "ce3cc6b5": "custom_cloud_transmittance",   # exp(-CloudHeight * (Scattering + Absorption) * Density), averaged (sky dome clouds)
    "ba175d0d": "custom_cloud_light",           # single scattering of a cloud layer lit by the sun (Apollo sky dome)
    "e50d56a3": "custom_cloud_light",           # the same, its phase guarded (LEGO's sky)
    "3f24674e": "custom_clouds_aerial",         # (1 - T) * AerialPerspective.rgb + CloudLuminance * AerialPerspective.a
    "103e88cd": "custom_height_fog_coverage",   # 1 - exp(-height fog optical depth) (LEGO's fog)
    "3eb98e41": "custom_smin",                  # polynomial smooth minimum
    "5d6f76f9": "custom_cave_type",             # CaveType 0, 2: 0.5; else 1
    "d2fa8df2": "custom_refract",           # return refract(Ray, Normal, Index);
    "0eafe77b": "custom_packed_hsv",        # asuint(PackedHSV.x): H 12 bits, S and V 10 bits each
    "2658b7cc": "custom_euler_to_quat",     # roll/pitch/yaw degrees -> quaternion (MF_EulerToQUat)
    "2e4092f5": "custom_one_minus_exp",     # return 1-exp(-x);
    "791169ec": "custom_one_minus_exp",     # return 1.0-exp(-x);
    # Water plugin runtime: water body data, the zone's water info texture,
    # Gerstner waves (see Translator.water_depth())
    "1186187d": "custom_zero",              # GetWaterWaveParamIndex(Parameters): the water body's index
    "e9d7efe6": "custom_zero",              # GetWaterBodyData(i).WaterZoneIndex
    "71f271be": "custom_zero",              # GetWaterInfoTextureViewIndex(WaterZoneIndex)
    "729bc03c": "custom_water_body_fixed",  # FixedVelocity / FixedZHeight / FixedWaterDepth outputs
    "0aad0772": "custom_water_zone",        # GetWaterZoneData: Location; Extent, HeightExtent, GroundZMin
    "766f134a": "custom_water_info_z",      # DecodeWaterInfoZHeight(WaterInfoSample, zone)
    "58bda2dd": "custom_water_info_ground",  # DecodeWaterInfoGroundHeight(WaterInfoSample, zone)
    "a8eed0bd": "custom_water_info_velocity",  # DecodeWaterInfoVelocity(WaterInfoSample, MaxVelocity)
    "7462ade0": "custom_water_flow",        # Flow (speed / MaxVelocity, angle / 2 pi) -> velocity
    "92a4fca7": "custom_gerstner_waves",    # GetPerFrequencyGerstnerWavesNew: WPO and normals
    "2c319634": "custom_one",               # ComputeWaveDepthAttenuationFactorNew (no waves to attenuate)
    "e52cfc8a": "custom_hole_clip",         # if (ClipMask == 0) clip(-1); return NormalPassThru;
    "30028fa0": "custom_hole_clip",         # if (ClipMask == 0) clip(-1); return ScatteringPassThru;
    "647da244": "custom_hole_clip",         # if (HoleMask == 0) clip(-1); return NormalPassThru;
    "efd032f9": "custom_hit_proxy_clip",    # #if HIT_PROXY_SHADER clip(x); #endif return PassThrough;
    "92ef46a2": "custom_dynamic_bounds",    # clip() outside the water's dynamic bounds
    "8b65a300": "custom_dynamic_bounds",    # clip() inside them
}

# handlers that read only some of their inputs (the water info sample a
# decode stands in for never becomes nodes)
CUSTOM_LAZY = {"custom_water_body_fixed", "custom_water_zone", "custom_water_info_z", "custom_water_info_ground",
               "custom_water_info_velocity", "custom_gerstner_waves", "custom_zero", "custom_one"}


class LazyInputs(dict):
    """A Custom node's inputs, each evaluated when first read."""

    def __init__(self, evaluate, refs):
        super().__init__()
        self._evaluate, self._refs = evaluate, refs

    def __missing__(self, key):
        if key not in self._refs:
            raise KeyError(key)
        v = self[key] = self._evaluate(self._refs[key])
        return v

    def get(self, key, default=None):
        return self[key] if key in self._refs or dict.__contains__(self, key) else default

    def values(self):
        return [self[k] for k in self._refs]


# MSM_SingleLayerWater medium: SingleLayerWaterMaterialOutput pin, resulting
# attribute, default when unlinked
WATER_OUTPUTS = (("ScatteringCoefficients", "WaterScattering", (0.0, 0.0, 0.0)),
                 ("AbsorptionCoefficients", "WaterAbsorption", (0.0, 0.0, 0.0)),
                 ("PhaseG", "WaterPhaseG", 0.0),
                 ("ColorScaleBehindWater", "WaterColorScaleBehindWater", (1.0, 1.0, 1.0)))
# water depth (cm) where the env knows no ground below
WATER_DEPTH_DEFAULT = 300.0


def render_tan_half_fov(cam, render):
    """tan of half the field of view, across and up. The sensor fits the larger frame
    side (Auto), its width or its height; the other axis follows the aspect. Blender's
    angle_y is always the sensor height's: 1.5x too narrow on a square render."""
    rx = render.resolution_x * render.pixel_aspect_x
    ry = render.resolution_y * render.pixel_aspect_y
    fit = cam.sensor_fit
    if fit == 'VERTICAL' or fit == 'AUTO' and ry > rx:
        ty = cam.sensor_height / 2.0 / cam.lens if fit == 'VERTICAL' else cam.sensor_width / 2.0 / cam.lens
        return ty * rx / ry, ty
    tx = cam.sensor_width / 2.0 / cam.lens
    return tx, tx * ry / rx


def custom_code(p):
    return " ".join(str(p.get("Code", "")).split())


def custom_handler(p):
    """The Translator method that rebuilds a Custom node's HLSL, or None."""
    code = custom_code(p)
    return CUSTOM_SNIPPETS.get(code) or CUSTOM_HASHES.get(hashlib.sha1(code.encode("utf-8")).hexdigest()[:8])


# Pins the translator never reads: texture derivatives and mips, and the
# scene inputs of renderer-only stand-ins. The coverage census skips them.
UNREAD_PINS = {
    "*": {"CoordinatesDX", "CoordinatesDY", "MipValue", "AutomaticViewMipBiasValue"},
    "DDX": {"Value"}, "DDY": {"Value"},
    "DepthFade": {"FadeDistance"}, "SceneDepth": {"Input"}, "SceneColor": {"Input"},
    "SceneDepthWithoutWater": {"Input"},
    "SceneTexture": {"Coordinates"}, "DistanceFieldApproxAO": {"BaseDistance", "Radius", "Normal", "Position"},
    "SkyAtmosphereLightIlluminance": {"WorldPosition"}, "SamplePhysicsVectorField": {"WorldPosition"},
    "RuntimeVirtualTextureSampleParameter": {"WorldPosition", "Coordinates"},
    "RuntimeVirtualTextureSample": {"WorldPosition", "Coordinates"},
}


def object_path(path, name):
    """"/Game/X/T_A.0" (CUE4Parse's export index) -> "/Game/X/T_A.T_A"."""
    base, _, tail = path.rpartition(".")
    if not base or not tail.isdigit():
        return path
    return "%s.%s" % (base, name.split(":")[-1].split(".")[-1])


def short_name(key):
    """A texture's label: "/Game/X/T_A.T_A" -> "T_A"."""
    return key.rsplit("/", 1)[-1].split(".")[0]


def linked(ref):
    return isinstance(ref, dict) and ref.get("ExpressionName") not in (None, "None")


def handled_types():
    """Expression types the translator handles, read from this package's source so the census can't drift."""
    here = os.path.dirname(__file__)
    body = "".join(open(os.path.join(here, f), encoding="utf-8").read() for f in sorted(os.listdir(here)) if f.endswith(".py"))
    out = set(re.findall(r'\bt == "([A-Za-z0-9_]+)"', body))
    for grp in re.findall(r'\bt in \(([^)]*)\)', body):
        out |= set(re.findall(r'"([A-Za-z0-9_]+)"', grp))
    return out | set(COMPILE_SWITCHES) | {"Reroute", "NamedRerouteUsage", "NamedRerouteDeclaration"}


class TexRef:
    """A texture as a value: what TextureObject(Parameter) hands a sample."""
    __slots__ = ("name", "volume")

    def __init__(self, name, volume=False):
        # name: the env's key for it (an object path with env.asset_paths)
        self.name, self.volume = name, volume


class Val:
    """A value in the Blender graph: a socket (or Python constant) and a width.
    Blender vectors have three components; a UE float4 keeps its fourth as `a`,
    a width-1 Val."""
    __slots__ = ("s", "w", "a")

    def __init__(self, s, w, a=None):
        self.s, self.w, self.a = s, w, a

    @property
    def const(self):
        return not hasattr(self.s, "node")


def T(x):
    return (x.get("Type") or "").replace("MaterialExpression", "")


def inner_path(object_name):
    """"Class'Pkg:Outer.Name'" -> "Pkg:Outer.Name"."""
    return object_name.split("'")[1] if "'" in object_name else object_name


# Nodes each tree's translation made, by what they compute (Translator.reuse): the same
# value asked for twice gets the one node. merge_duplicates, which removes nodes, empties its tree's.
MADE = {}


def reuse_key(v):
    """A value as a node input, for Translator.reuse: its socket or number plus width;
    None for what no socket holds (Material Attributes, a texture reference)."""
    if v is None:
        return ()
    s = v.s
    if isinstance(s, (int, float)):
        return (float(s), v.w)
    if isinstance(s, tuple):
        return (s, v.w)
    if hasattr(s, "as_pointer"):
        return (s.as_pointer(), v.w)
    return None


_GRAPHS = {}


def load_graph(path):
    """A graph dump, parsed once per file version: a map's materials share masters and
    functions, and parsing them was a tenth of the build. Graphs are not modified once made."""
    try:
        st = os.stat(path)
        key = (path, st.st_mtime_ns, st.st_size)
    except OSError:
        return Graph(path)
    g = _GRAPHS.get(key)
    if g is None:
        if len(_GRAPHS) > 600:
            _GRAPHS.clear()
        g = _GRAPHS[key] = Graph(path)
    return g


PIXEL_NORMAL = "PixelNormalWS"     # a function group's PixelNormalWS input (env.group_input fills it)
NORMAL_PASS = "Normal pass"        # the frame the Normal's own pass is made in (Translator.normal_pass)


def comps3(s):
    return tuple(s)[:3] if isinstance(s, (tuple, list)) else (s, s, s)


class TexKey(str):
    """A texture key that remembers the texture parameter it came from."""
    param = None

    @staticmethod
    def of(key, param):
        k = TexKey(key)
        k.param = param
        return k


class Graph:
    """One dumped graph. Expressions are found by name; a name used twice (a collapsed
    subgraph keeps its own numbering) is found by its full object path instead, see key()."""

    def __init__(self, path):
        self.path = path
        self.name = os.path.splitext(os.path.basename(path))[0]
        self.o = json.load(open(path, encoding="utf-8"))
        # a 28.00 dump names a function input's and output's guid "ID", not "Id"; unmatched,
        # every input a call gives would read as unconnected
        for x in self.o:
            p = x.get("Properties")
            if isinstance(p, dict) and "Id" not in p and "ID" in p:
                p["Id"] = p["ID"]
        seen, self.dups = set(), set()
        for x in self.o:
            n = x.get("Name")
            (self.dups if n in seen else seen).add(n)
        self.by = {}
        for x in self.o:
            self.by[self.full_name(x) if x.get("Name") in self.dups else x.get("Name")] = x
        self.decl = {k: x for k, x in self.by.items() if T(x) == "NamedRerouteDeclaration"}
        self.inputs = {x["Properties"].get("Id"): x for x in self.o if T(x) == "FunctionInput"}
        self.outputs = {x["Properties"].get("Id"): x for x in self.o if T(x) == "FunctionOutput"}
        # the author's comment boxes, smallest first: a node inside one is
        # framed under its text
        self.comments = []
        for x in self.o:
            if T(x) != "Comment":
                continue
            p = x.get("Properties") or {}
            x0, y0 = p.get("MaterialExpressionEditorX", 0), p.get("MaterialExpressionEditorY", 0)
            w, h = p.get("SizeX", 0), p.get("SizeY", 0)
            text = " ".join(str(p.get("Text", "")).split())[:40].strip()
            if w and h and text:
                self.comments.append((w * h, x0, y0, x0 + w, y0 + h, text))
        self.comments.sort()

    @staticmethod
    def full_name(x):
        outer = inner_path(str((x.get("Outer") or {}).get("ObjectName", "")))
        return "%s%s%s" % (outer, "." if ":" in outer else ":", x.get("Name"))

    def key(self, ref):
        """The expression an input dict points at, as a key of `by`."""
        n = ref.get("ExpressionName")
        if n in self.dups:
            return inner_path(str((ref.get("Expression") or {}).get("ObjectName", ""))) or n
        return n

    def comment_of(self, x):
        p = x.get("Properties") or {}
        if "MaterialExpressionEditorX" not in p:
            return None
        px, py = p["MaterialExpressionEditorX"], p.get("MaterialExpressionEditorY", 0)
        for _, x0, y0, x1, y1, text in self.comments:
            if x0 <= px <= x1 and y0 <= py <= y1:
                return text
        return None

    def editor_data(self):
        return next((x for x in self.o if T(x) in ("MaterialEditorOnlyData",)), None)


SOCKET_OF_WIDTH = {1: 'NodeSocketFloat', 2: 'NodeSocketVector', 3: 'NodeSocketVector', 4: 'NodeSocketColor'}
WIDTH_OF_SOCKET = {'NodeSocketFloat': 1, 'NodeSocketVector': 3, 'NodeSocketColor': 3}
INPUT_WIDTH = {"EFunctionInputType::FunctionInput_Scalar": 1, "EFunctionInputType::FunctionInput_Bool": 1,
               STATIC_BOOL_INPUT: 1, "EFunctionInputType::FunctionInput_Vector2": 2,
               "EFunctionInputType::FunctionInput_Vector3": 3, "EFunctionInputType::FunctionInput_Vector4": 4}


class FunctionTree:
    """One UE material function as one node group, shared by every call.

    Its FunctionInputs are the group's inputs; outputs are built as first asked for.
    A call site gets its own group node and links only the inputs the asked-for output
    reaches: UE compiles each output on its own, and linking the rest can close a loop
    (the sky's horizon-hills mask feeds the chain that ends in the same function's colour
    input). What the body reads from the material (a parameter socket, the sun) becomes
    an input too, handed down by the caller (env.group_input).

    Static bool inputs aren't sockets: the function is compiled once per set of values
    (`statics`, input id -> bool; `label` names the variant).
    """
    PREFIX = "FPv4 Sky - "

    def __init__(self, parent, fname, fg, statics=None, label=None):
        import bpy
        self.fname, self.fg = fname, fg
        self.statics = statics or {}
        self.prefix = getattr(parent.env, "function_prefix", self.PREFIX)
        name = self.prefix + (label or section_name(fname))
        # a rebuild replaces its old group (the sky); an env that builds alongside other
        # materials keeps them (env.replace_groups = False: Blender numbers the new one)
        old = bpy.data.node_groups.get(name)
        if old is not None and getattr(parent.env, "replace_groups", True):
            bpy.data.node_groups.remove(old)
        self.tree = bpy.data.node_groups.new(name, "ShaderNodeTree")
        self.tr = Translator(self.tree, parent.env, parent=parent)
        self.tr.function = self
        self.tr.section = [section_name(fname)]
        self.gi = self.tree.nodes.new("NodeGroupInput")
        self.go = self.tree.nodes.new("NodeGroupOutput")
        self.gi[SECTION_KEY] = self.go[SECTION_KEY] = self.tr.section[0]
        self.kind = {}          # input socket name -> ("ue", input id) | ("env", name)
        self.ue_names = {}      # UE input id -> socket name
        self.previews = {}      # socket name -> preview expression ref, for callers that leave it
        self.outs = {}          # output id -> (socket name, width); width "attrs" for Material Attributes
        self.attr_outs = {}     # output id -> {attribute: ("socket", name, width) | ("const", Val)}
        self.needs = {}
        self.const_outs = {}    # socket name -> Val: an output known when built goes to callers as data
        self.textures = {}      # closure input socket name -> (texture key, sampler, image, own sampler, address)

    def _new_input(self, name, width, kind):
        base, i = name, 2
        name = socket_name(name)
        while any(it.item_type == 'SOCKET' and it.in_out == 'INPUT' and it.name == name
                  for it in self.tree.interface.items_tree):
            if self.kind.get(name) == kind:
                return name
            name = socket_name("%s %d" % (base, i))
            i += 1
        self.tree.interface.new_socket(name, in_out='INPUT', socket_type=SOCKET_OF_WIDTH[width])
        self.kind[name] = kind
        return name

    def ue_input(self, x):
        """The group input standing for a FunctionInput expression."""
        p = x.get("Properties") or {}
        fid = p.get("Id")
        if p.get("InputType") == STATIC_BOOL_INPUT:
            return self.tr.const(1.0 if self.statics.get(fid) else 0.0)
        if p.get("InputType") == ATTRIBUTES_INPUT:
            return Val(OpenAttrs(self, fid, p.get("InputName") or "Material Attributes"), 0)
        if fid not in self.ue_names:
            w = INPUT_WIDTH.get(p.get("InputType"), 3)
            name = self._new_input(p.get("InputName") or "Input", w, ("ue", fid))
            self.ue_names[fid] = name
            item = next(it for it in self.tree.interface.items_tree
                        if it.item_type == 'SOCKET' and it.in_out == 'INPUT' and it.name == name)
            v = p.get("PreviewValue", {})
            dflt = (v.get("X", 0.0), v.get("Y", 0.0), v.get("Z", 0.0))
            if p.get("bUsePreviewValueAsDefault"):
                item.default_value = dflt[0] if w == 1 else (dflt + (1.0,) if w == 4 else dflt)
                if isinstance(p.get("Preview"), dict) and p["Preview"].get("ExpressionName") not in (None, "None"):
                    self.previews[name] = p["Preview"]
            if p.get("Description"):
                item.description = str(p.get("Description"))[:200]
        name = self.ue_names[fid]
        w = INPUT_WIDTH.get(p.get("InputType"), 3)
        if w == 4:
            aname = socket_name(name + " (A)")
            if aname not in self.kind:
                self.tree.interface.new_socket(aname, in_out='INPUT', socket_type='NodeSocketFloat')
                self.kind[aname] = ("ue_alpha", fid)
            return Val(self.gi.outputs[name], 4, Val(self.gi.outputs[aname], 1))
        return Val(self.gi.outputs[name], min(w, 3))

    def env_input(self, key, socket_type, default=None, description=""):
        # the socket's name may be cut (socket_name); the env gets its key back whole
        name = socket_name(key)
        if name not in self.kind:
            self.tree.interface.new_socket(name, in_out='INPUT', socket_type=socket_type)
            item = next(it for it in self.tree.interface.items_tree
                        if it.item_type == 'SOCKET' and it.in_out == 'INPUT' and it.name == name)
            if default is not None:
                try:
                    item.default_value = default
                except Exception:
                    pass
            item.description = description
            self.kind[name] = ("env", key)
        return Val(self.gi.outputs[name], WIDTH_OF_SOCKET.get(socket_type, 3))

    def texture_input(self, spec, tname, sampler, img, own, address):
        """The Closure input a texture the body samples arrives on (Vector -> Color, Alpha).
        The image node stays at the material's root so the group holds no image and every
        material calling the function shares it. Named for the texture parameter, else the
        texture (an instance's texture doesn't rename it)."""
        key = ("tex", spec)
        for name, k in self.kind.items():
            if k == key:
                return self.gi.outputs[name]
        taken = {it.name for it in self.tree.interface.items_tree if it.item_type == 'SOCKET' and it.in_out == 'INPUT'}
        base = getattr(tname, "param", None) or short_name(str(tname)) or "Texture"
        name, i = socket_name(base), 2
        while name in taken:
            name = socket_name("%s %d" % (base, i))
            i += 1
        item = self.tree.interface.new_socket(name, in_out='INPUT', socket_type='NodeSocketClosure')
        item.description = "UE texture %s: its image node is in the material's Textures frame" % base
        self.kind[name] = key
        self.textures[name] = (tname, sampler, img, own, address)
        return self.gi.outputs[name]

    def output(self, fo):
        """Build a FunctionOutput once; returns (socket name, width)."""
        p = fo["Properties"]
        oid = p.get("Id")
        if oid in self.outs:
            return self.outs[oid]
        prev = self.tr.activate()
        try:
            v = self.tr.input(self.fg, p.get("A"), {"_fn": self, "_id": ("fn", self.fname)}, self.tr.const(0.0))
            name = socket_name(p.get("OutputName") or "Result")
            if name in [o[0] for o in self.outs.values()]:
                name = socket_name("%s %d" % (name, len(self.outs) + 1))
            if isinstance(v.s, Attrs):
                self.outs[oid] = (name, "attrs")
                passes = self.tr.passthrough(self.fg, p.get("A"), {"_fn": self, "_id": ("fn", self.fname)})
                self.attr_outs[oid] = self.attribute_outputs(name, v.s, passes)
                return self.outs[oid]
            w = min(max(v.w, 1), 3)
            self.tree.interface.new_socket(name, in_out='OUTPUT', socket_type=SOCKET_OF_WIDTH[w])
            a = v.a if v.w == 4 else None
            if v.const and (a is None or a.const):
                self.const_outs[name] = v
            if v.const:
                v = self.tr.solid(v)
            self.tr.L.new(v.s, self.go.inputs[name])
            if a is not None:
                # a float4: its fourth component on a socket of its own
                self.tree.interface.new_socket(socket_name(name + " (A)"), in_out='OUTPUT', socket_type='NodeSocketFloat')
                if a.const:
                    a = self.tr.solid(a)
                self.tr.L.new(a.s, self.go.inputs[socket_name(name + " (A)")])
                w = 4
        finally:
            prev.activate()
        self.outs[oid] = (name, w)
        return self.outs[oid]

    def attribute_outputs(self, name, attrs, passes):
        """A Material Attributes output: a socket per CARRIED attribute the function computes.
        Constants go to the caller as data; attributes passed unchanged from a Material
        Attributes input (`passes`) stay out of the group, the caller keeps its own."""
        out = {}
        for a in self.tr.carried:
            if a in passes:
                out[a] = ("pass", passes[a])
                continue
            v = attrs.get(a)
            if v.const:
                out[a] = ("const", v)
                continue
            sock = socket_name("%s: %s" % (name, pretty(a)))
            w = min(max(v.w, 1), 3)
            self.tree.interface.new_socket(sock, in_out='OUTPUT', socket_type=SOCKET_OF_WIDTH[w])
            self.tr.L.new(v.s, self.go.inputs[sock])
            out[a] = ("socket", sock, w)
        return out

    def reached(self, out_name):
        """The input sockets an output depends on; through a nested group, only those the
        output it reads depends on. Otherwise a function passing Material Attributes through
        another read every attribute for its Normal, and the Normal's pass copied the whole material."""
        if out_name not in self.needs:
            want = self.go.inputs[out_name].identifier
            names = {s.identifier: s.name for s in self.gi.outputs}
            self.needs[out_name] = {names[i] for i in group_deps(self.tree, {}).get(want, ()) if i in names}
        return self.needs[out_name]


class Loop(Exception):
    """A link into a shared call node that would close a loop through it."""


class LazyInput:
    """A function input, evaluated the first time the function reads it."""
    __slots__ = ("g", "ref", "scope", "done", "val", "section")

    def __init__(self, g, ref, scope, section):
        self.g, self.ref, self.scope, self.done, self.val = g, ref, scope, False, None
        # an input is the caller's expression: its nodes go in the caller's frame
        self.section = list(section)

    def get(self, tr):
        if not self.done:
            saved = tr.section
            tr.section = self.section
            try:
                self.val = tr.input(self.g, self.ref, self.scope, None)
            finally:
                tr.section = saved
            self.done = True
        return self.val


class Statics:
    """A function scope that knows only its static values (compile-time
    questions about a function, asked without building it)."""
    __slots__ = ("statics",)

    def __init__(self, statics):
        self.statics = statics


class SpecialInputs(list):
    """Special functions read their inputs by index; evaluate on access."""

    def __init__(self, tr, lazies):
        super().__init__(lazies)
        self.tr = tr

    def __getitem__(self, i):
        return list.__getitem__(self, i).get(self.tr)

    def __iter__(self):
        return (x.get(self.tr) for x in list.__iter__(self))


def group_deps(tree, memo):
    """{output socket identifier: {input socket identifiers it reads}} of a node group."""
    if tree.name in memo:
        return memo[tree.name]
    memo[tree.name] = {}        # a group inside itself can't be; guard anyway
    feeds, order, indeg = {}, [], {}
    nodes = {n.name: n for n in tree.nodes}
    for n in tree.nodes:
        indeg[n.name] = 0
    for l in tree.links:
        feeds.setdefault(l.to_node.name, []).append((l.to_socket.identifier, l.from_node.name, l.from_socket.identifier))
        indeg[l.to_node.name] += 1
    todo = [name for name, d in indeg.items() if d == 0]
    outs = {}
    for l in tree.links:
        outs.setdefault(l.from_node.name, []).append(l.to_node.name)
    while todo:
        name = todo.pop()
        order.append(name)
        for to in outs.get(name, ()):
            indeg[to] -= 1
            if indeg[to] == 0:
                todo.append(to)
    reads = {}      # node name -> {input identifier: {group inputs}}

    def out_set(name, ident):
        n = nodes[name]
        ins = reads.get(name, {})
        if n.bl_idname == "NodeGroupInput":
            return {ident}
        if n.bl_idname == "ShaderNodeGroup" and n.node_tree is not None:
            inner = group_deps(n.node_tree, memo).get(ident)
            if inner is not None:
                return set().union(*(ins.get(i, set()) for i in inner))
        return set().union(*ins.values())
    for name in order:
        r = reads.setdefault(name, {})
        for to_ident, frm, from_ident in feeds.get(name, ()):
            r.setdefault(to_ident, set()).update(out_set(frm, from_ident))
    go = next((n for n in tree.nodes if n.bl_idname == "NodeGroupOutput" and n.is_active_output), None) \
        or next((n for n in tree.nodes if n.bl_idname == "NodeGroupOutput"), None)
    memo[tree.name] = dict(reads.get(go.name, {})) if go is not None else {}
    return memo[tree.name]


SHELL_ATTRIBUTES = {"ShellMeshShellLayerIndex": SHELL_LAYER, "ShellMeshNormalizedShellLayer": SHELL_LAYER_N,
                     "ShellMeshShellCount": SHELL_COUNT}


SHELL_VECTORS = {"ShellMeshLocalShellOffset": SHELL_OFFSET, "ShellMeshLocalShellVector": SHELL_VECTOR}


# constant folding (merge_duplicates): Blender's maths, as its nodes do it
import math as _math


def blender_vmath(op, a, b=None):
    if op == "ADD": return tuple(x + y for x, y in zip(a, b))
    if op == "SUBTRACT": return tuple(x - y for x, y in zip(a, b))
    if op == "MULTIPLY": return tuple(x * y for x, y in zip(a, b))
    if op == "DIVIDE": return tuple(x / y if y != 0.0 else 0.0 for x, y in zip(a, b))
    if op == "SCALE": return tuple(x * b for x in a)
    if op == "MINIMUM": return tuple(min(x, y) for x, y in zip(a, b))
    if op == "MAXIMUM": return tuple(max(x, y) for x, y in zip(a, b))
    if op == "ABSOLUTE": return tuple(abs(x) for x in a)
    if op == "DOT_PRODUCT": return float(sum(x * y for x, y in zip(a, b)))
    if op == "LENGTH": return _math.sqrt(sum(x * x for x in a))
    if op == "DISTANCE": return _math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))
    if op == "CROSS_PRODUCT":
        return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])
    if op == "NORMALIZE":
        n = _math.sqrt(sum(x * x for x in a))
        return tuple(x / n for x in a) if n > 0.0 else (0.0, 0.0, 0.0)
    return None


PY = {
    "ADD": lambda a, b: a + b, "SUBTRACT": lambda a, b: a - b, "MULTIPLY": lambda a, b: a * b,
    "DIVIDE": lambda a, b: a / b if b else 0.0, "MAXIMUM": max, "MINIMUM": min,
    "POWER": lambda a, b: a ** b if a > 0 else 0.0,
}


# FunctionTree builds Translators; translator.py imports this module first
from .translator import Translator  # noqa: E402
