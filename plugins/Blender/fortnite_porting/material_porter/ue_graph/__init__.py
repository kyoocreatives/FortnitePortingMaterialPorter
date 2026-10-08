"""Translate a uedump material / material-function graph into Blender shader nodes.

    tr = Translator(tree, env)
    colour = tr.material_output("M_Sky_Ch7.json", "EmissiveColor")

Every UE expression becomes the Blender nodes that compute the same value. The
graph's own maths is carried over as is, nothing re-derived by hand. Conventions:

* Values carry a width (1-4). Width 1 is a float socket; 2-4 a vector socket
  (z = 0 for width 2; alpha dropped for width 4 unless read through an output
  index that means alpha). UE broadcasts only float1, and so does this.
* Unconnected pins use UE's class defaults, which the dumps pin down (the value
  is never serialized): Add.B 1, Subtract.A/B 1, Multiply.B 1, Divide.A 1 B 2,
  Lerp.B 1 alpha 0.5, Power exponent 2, Max/Min.B 1, Step X 1 Y 0,
  SmoothStep 0..1, Rotator centre 0.5 speed 0.25, Sine period 1 (sin 2 pi x),
  texture tiling 1.
* Everything is evaluated in UE space. The env hands in world vectors already
  mirrored (FP flips UE's Y). UVs run in UE's top-left space: a UV input is
  (u, 1 - v) of Blender's, and a texture sample flips back.
* Compile-time switches take the branch a shipping PC build compiles
  (COMPILE_SWITCHES): Epic quality, SM6, deferred, pixel shader, no ray
  tracing, no virtual textures or distance fields.
* Static switches are resolved while translating, as UE does when compiling:
  only the live branch becomes nodes. Values come from env.static_switch (the
  instance's overrides), else the graph's defaults. A material function taking
  a static bool gets one node group per set of values ("Foo [+UseX]").
* Material Attributes are a value of their own (Attrs). Each attribute becomes
  nodes only when something reads it. Across a function's group sockets they
  travel as one socket per CARRIED attribute ("Layer A: Base Color"); constant
  ones travel as data. The rest (world position offset, refraction, customized
  UVs...) have no Blender slot and stay inside.
  Translator.material_attributes(path) gives a material's CARRIED attributes.
* Renderer-only inputs (exposure, scene depth and colour, sky light, particles,
  derivatives, virtual textures...) are stand-ins: what a still frame of a plain
  mesh gets (exposure 1, no particle, a far scene), or an env hook's answer.
  Each is reported as a warning ("stand-in: ...").

What the graph reads from the world (camera vector, sun, atmosphere, time,
parameters, textures) comes from `env`; build_sky.py's SkyEnv is the sky's:

    env.camera_vector()               -> Val (3)   pixel to camera, UE space
    env.light_direction()             -> Val (3)   towards the sun, UE space
    env.world_position(camera_relative) -> Val (3) UE cm
    env.actor_position()              -> Val (3)   UE cm, camera relative
    env.view_luminance(direction_val) -> Val (3)
    env.time()                        -> Val (1)   seconds
    env.scalar(name, default)         -> Val (1)
    env.vector(name, default_rgba)    -> Val (3), alpha float
    env.collection(name)              -> Val (1 or 3)
    env.texture(name, sampler)        -> bpy.types.Image
                                         (env.asset_paths = True: textures and
                                         collections go by object path,
                                         "/Game/.../T_X.T_X", not name, and
                                         env.collection(name, path))
    env.cube(name)                    -> bpy.types.Image (equirect, UE-space lookup)
    env.uv(index)                     -> Val (2)   the mesh's UV set, UE space
    env.vertex_color()                -> (Val (3), Val (1))
    env.function(name)                -> path of that function's graph dump, or None
    env.special_function(name, inputs) -> {output index: Val} to replace a
                                          function outright, or None

Optional - each has a fallback built from Blender's own inputs (object,
geometry, camera), so an env only defines what it knows better:

    env.static_switch(name, default)  -> bool      a static bool parameter
    env.static_mask(name, default)    -> 4 bools   a static component mask
    env.landscape_layer(name)         -> bool      is that layer painted
    env.vertex_normal() / pixel_normal() / vertex_tangent() -> Val (3) UE space
    env.local_position()              -> Val (3)   UE cm, object space
    env.object_position(camera_relative) -> Val (3)
    env.local_bounds()                -> (min Val (3), max Val (3)), UE cm
    env.object_radius()               -> Val (1)
    env.pixel_depth()                 -> Val (1)   UE cm
    env.two_sided_sign()              -> Val (1)
    env.screen_uv()                   -> Val (2)   0..1, top left
    env.view_size()                   -> Val (2)   pixels
    env.view_property(name)           -> Val or None (MEVP_* without prefix)
    env.instance_random()             -> Val (1)
    env.instance_data(v, index, w)    -> Val (w): an instance's own floats over the default v, or None
    env.exposure()                    -> Val (1)   UE's EyeAdaptation (1)
    env.light_color()                 -> Val (3)   the sun's colour (1)
    env.sky_light(direction, roughness) -> Val (3) the sky light's radiance
    env.particle_color()              -> (Val (3), Val (1))
    env.dynamic_parameter(index, default) -> Val (4) or None
    env.texture_slices(name, sampler) -> [bpy.types.Image] a texture array
    env.texture_parameter(pname, default) -> the texture a parameter holds
    env.preskinned_position()         -> Val (3)   UE cm, object space, before skinning
    env.texture_nearest(name)         -> bool      texture samples unfiltered (Filter TF_Nearest)
    env.primitive_index()             -> Val (1)
    env.water_depth()                 -> Val (1)   UE cm from the water surface down to the ground
    env.water_flow()                  -> Val (2)   surface flow, DrawWaterInfo's encoding
                                                   (speed / MaxVelocity, angle / 2 pi)

A water material (MSM_SingleLayerWater) also gives its medium:
Translator.material_attributes() adds WaterScattering, WaterAbsorption (per cm),
WaterPhaseG and WaterColorScaleBehindWater from its SingleLayerWaterMaterialOutput.
water_path() (cm) is the view ray's length through the water, which is what
SceneDepthWithoutWater - PixelDepth measures.
"""
from .core import (  # noqa: F401
    ATTRIBUTES,
    ATTRIBUTES_INPUT,
    ATTRIBUTE_DEFAULT,
    ATTRIBUTE_OF_GUID,
    ATTRIBUTE_WIDTH,
    Attrs,
    BOUNDS_CENTRE,
    BOUNDS_MAX,
    BOUNDS_MIN,
    BREAK_ORDER,
    BlendAttrs,
    CARRIED,
    COMPILE_SWITCHES,
    CUSTOM_HASHES,
    CUSTOM_LAZY,
    CUSTOM_SNIPPETS,
    FunctionTree,
    Graph,
    HEAD_SOCKET,
    INPUT_WIDTH,
    MAKE_PINS,
    MapAttrs,
    NORMAL_PASS,
    OpenAttrs,
    PART_BOUNDS_MAX,
    PART_BOUNDS_MIN,
    PIN_ATTRIBUTE,
    PIXEL_NORMAL,
    POSITION_SPACES,
    SECTION_KEY,
    SHADING_MODELS,
    SHELL_COUNT,
    SHELL_LAYER,
    SHELL_LAYER_N,
    SHELL_OFFSET,
    SHELL_VECTOR,
    SOCKET_OF_WIDTH,
    STATIC_BOOL_INPUT,
    T,
    TexKey,
    TexRef,
    UE_DEFAULTS,
    UNREAD_PINS,
    VECTOR_SPACES,
    VERTEX_ATTRIBUTES,
    Val,
    WATER_DEPTH_DEFAULT,
    WATER_OUTPUTS,
    WIDTH_OF_SOCKET,
    _GRAPHS,
    _Lazy,
    _LazyInputs,
    _Loop,
    _MADE,
    _PY,
    _SHELL_ATTRIBUTES,
    _SHELL_VECTORS,
    _SpecialInputs,
    _Statics,
    _blender_vmath,
    _comps3,
    _group_deps,
    _ident,
    _inner,
    as_attrs,
    attribute_default,
    attribute_name,
    custom_code,
    custom_handler,
    handled_types,
    linked,
    load_graph,
    object_path,
    pretty,
    render_tan_half_fov,
    section_name,
    short_name,
    socket_name,
)
from .translator import (  # noqa: F401
    Translator,
)
from .merge import (  # noqa: F401
    _MATH_ARITY,
    _MERGE_IGNORED,
    _MERGE_PROPS,
    _MERGE_SKIP,
    _VMATH_ARITY,
    _blender_math,
    _merge_props,
    _plain,
    _socket_value,
    merge_duplicates,
)
