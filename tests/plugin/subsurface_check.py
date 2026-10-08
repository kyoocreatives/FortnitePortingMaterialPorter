"""Checks the exact materials' subsurface controls: which values an import resolves (skin, base, distance, profile
colour; fur its own) and what the built graph scatters (amount = max(base, game x skin), radius = mix(neutral,
profile, colour)).

    blender -b --factory-startup --python-exit-code 1 -P tests/plugin/subsurface_check.py -- <plugin parent>

<plugin parent> is a folder holding the plugin as a package named fpmp_baseline (see translator_test.py). Exit code 0
when every check passes."""
import sys
from types import SimpleNamespace

import bpy

sys.path.insert(0, sys.argv[sys.argv.index("--") + 1])
from fpmp_baseline.material_porter import hook  # noqa: E402
from fpmp_baseline.processing.enums import EExportType  # noqa: E402

FAILS = []
PASSES = [0]


def check(name, got, want, tol=1e-4):
    ok = all(abs(g - w) <= tol for g, w in zip(got, want)) if isinstance(want, tuple) else \
        (abs(got - want) <= tol if isinstance(want, float) else got == want)
    if ok:
        PASSES[0] += 1
    else:
        FAILS.append(name)
        print("[subsurface_check] FAIL %s: got %r, want %r" % (name, got, want))


def context(kind, **options):
    return SimpleNamespace(type=kind, options=options)


# options per import
s = hook.subsurface(context(EExportType.OUTFIT), {})
check("defaults on an outfit", tuple(s), (1.0, 0.0, 1.0, 0.5))
s = hook.subsurface(context(EExportType.OUTFIT, SubsurfaceIntensity=0.7, BaseSubsurface=0.3, SubsurfaceScale=4.0,
                            ProfileColour=0.2), {})
check("set on an outfit", tuple(s), (0.7, 0.3, 4.0, 0.2))
s = hook.subsurface(context(EExportType.WORLD, BaseSubsurface=0.3), {})
check("no base on a world", s.base, 0.0)
s = hook.subsurface(context(EExportType.OUTFIT, FurSubsurfaceIntensity=0.6, FurSubsurfaceScale=3.0, BaseSubsurface=0.3,
                            ProfileColour=0.2), {"MPMoves": True})
check("fur keeps its own", tuple(s), (0.6, 0.0, 3.0, 1.0))
s = hook.subsurface(context(EExportType.OUTFIT, SubsurfaceScale=50.0, BaseSubsurface=2.0), {})
check("ranges clamp", (s.scale, s.base), (10.0, 1.0))
for kind, want in ((EExportType.OUTFIT, True), (EExportType.PICKAXE, True), (EExportType.LEGO_OUTFIT, True),
                   (EExportType.LEGO_PROP, False), (EExportType.VEHICLE, True), (EExportType.FALL_GUYS_OUTFIT, True),
                   (EExportType.WORLD, False), (EExportType.PREFAB, False), (EExportType.MESH, False),
                   (EExportType.EFFECT, False)):
    check("cosmetic %s" % kind.name, hook.is_cosmetic(kind), want)

# the convert, unwrap and fixer operators pass the export type as a namespace with its name
s = hook.subsurface(SimpleNamespace(type=SimpleNamespace(name="MESH"), options={"BaseSubsurface": 0.3}), {})
check("operator context: no base on a mesh", s.base, 0.0)
check("operator context: an outfit is a cosmetic", hook.is_cosmetic(SimpleNamespace(name="OUTFIT")), True)

# reuse: a material built under other subsurface values (or not as a cosmetic's) isn't reused
from fpmp_baseline.material_porter.material_choice import ExactChoice  # noqa: E402
plain = ExactChoice(context(EExportType.OUTFIT), {}, None, True)
check("reused under the same values", plain.reusable({"MPSubsurface": ExactChoice(context(EExportType.OUTFIT), {}, None, True).subsurface}), True)
check("not reused under another base", ExactChoice(context(EExportType.OUTFIT, BaseSubsurface=0.3), {}, None, True).reusable({"MPSubsurface": plain.subsurface}), False)
check("not reused under another profile colour", ExactChoice(context(EExportType.OUTFIT, ProfileColour=1.0), {}, None, True).reusable({"MPSubsurface": plain.subsurface}), False)
check("a world's material isn't reused by a cosmetic", plain.reusable({"MPSubsurface": ExactChoice(context(EExportType.WORLD), {}, None, True).subsurface}), False)

# the built graph: a small material through the translator and build.assemble, as build_one does
import json, os, runpy, tempfile  # noqa: E401,E402
from fpmp_baseline.material_porter import build  # noqa: E402
from fpmp_baseline.material_porter.ue_graph import Translator  # noqa: E402

T = runpy.run_path(os.path.join(os.path.dirname(os.path.abspath(__file__)), "translator_test.py"))  # its env and evaluator
TMP = tempfile.mkdtemp(prefix="sss_check_")


def built(shading, opacity=None, profile=None, cosmetic=True, shell=False):
    """(material, its group node) for a material scattering by `shading` with Opacity `opacity` (None: no pin)."""
    nodes = []
    props = {}
    if opacity is not None:
        nodes.append({"Type": "MaterialExpressionScalarParameter", "Name": "Op", "Properties": {"ParameterName": "op", "DefaultValue": opacity}})
        props["Opacity"] = {"ExpressionName": "Op", "OutputIndex": 0}
    nodes.append({"Type": "MaterialEditorOnlyData", "Name": "MaterialEditorOnlyData_0", "Properties": props})
    path = os.path.join(TMP, "M_%d.json" % len(os.listdir(TMP)))
    json.dump(nodes, open(path, "w", encoding="utf-8"))
    mat = bpy.data.materials.new("sss")
    mat.node_tree.nodes.clear()
    root = bpy.data.node_groups.new(mat.name, "ShaderNodeTree")
    env = T["TestEnv"]()
    tr = Translator(root, env)
    env.h[0] = tr
    vals = tr.material_attributes(path, build.CARRIED)
    entry = {"asset": {"ShadingModel": "EMaterialShadingModel::" + shading}, "subsurface": profile,
             "cosmetic": cosmetic, "shell": shell, "name": "sss"}
    build.assemble(tr, mat, vals, build.settings(entry))
    node = mat.node_tree.nodes.new("ShaderNodeGroup")
    node.node_tree = root
    return mat, node


def bsdf_input(node, name):
    bsdf = next(n for n in node.node_tree.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled")
    return T["ev_input"](bsdf.inputs[name], (node,))


rose = {"radius": [0.95, 0.332, 0.414], "scale": 0.003}
mat, node = built("MSM_SubsurfaceProfile", opacity=0.8, profile=rose)
check("skin inputs", [i.name for i in node.inputs if "ubsurface" in i.name or i.name in (build.SCATTER_DISTANCE, build.PROFILE_COLOUR)],
      [build.SKIN_SUBSURFACE_INPUT, build.BASE_SUBSURFACE, build.SCATTER_DISTANCE, build.SUBSURFACE_RADIUS, build.PROFILE_COLOUR])
check("skin amount: the game's x skin", bsdf_input(node, "Subsurface Weight"), 0.8)
node.inputs[build.SKIN_SUBSURFACE_INPUT].default_value = 0.5
check("skin amount x 0.5", bsdf_input(node, "Subsurface Weight"), 0.4)
node.inputs[build.BASE_SUBSURFACE].default_value = 0.6
check("base over a lower skin amount", bsdf_input(node, "Subsurface Weight"), 0.6)
node.inputs[build.SKIN_SUBSURFACE_INPUT].default_value = 0.0
node.inputs[build.BASE_SUBSURFACE].default_value = 0.0
check("skin 0 and base 0 scatter nothing", bsdf_input(node, "Subsurface Weight"), 0.0)
mean = sum(rose["radius"]) / 3
node.inputs[build.PROFILE_COLOUR].default_value = 1.0
check("profile colour 1: the profile's", bsdf_input(node, "Subsurface Radius"), tuple(rose["radius"]))
node.inputs[build.PROFILE_COLOUR].default_value = 0.0
check("profile colour 0: neutral", bsdf_input(node, "Subsurface Radius"), (mean, mean, mean))
node.inputs[build.PROFILE_COLOUR].default_value = 0.5
check("profile colour default 0.5", bsdf_input(node, "Subsurface Radius"), tuple(0.5 * (r + mean) for r in rose["radius"]))
check("profile colour default value", node.inputs[build.PROFILE_COLOUR].default_value, 0.5)

flat = {"radius": [0.6, 0.6, 0.6], "scale": 0.003}
mat, node = built("MSM_SubsurfaceProfile", opacity=1.0, profile=flat)
node.inputs[build.PROFILE_COLOUR].default_value = 0.0
check("a neutral profile doesn't change with profile colour", bsdf_input(node, "Subsurface Radius"), (0.6, 0.6, 0.6))

mat, node = built("MSM_DefaultLit", cosmetic=True)
check("a cosmetic the game doesn't scatter has the inputs", build.BASE_SUBSURFACE in node.inputs, True)
check("...and scatters nothing at base 0", bsdf_input(node, "Subsurface Weight"), 0.0)
node.inputs[build.BASE_SUBSURFACE].default_value = 0.5
check("...base alone drives it", bsdf_input(node, "Subsurface Weight"), 0.5)
check("...over skin's neutral distance", node.inputs[build.SCATTER_DISTANCE].default_value, build.SKIN_SCALE)
check("...no profile colour without a profile", build.PROFILE_COLOUR in node.inputs, False)
check("...but a radius input", build.SUBSURFACE_RADIUS in node.inputs, True)
mat, node = built("MSM_SubsurfaceProfile", opacity=0.8)
check("skin without a profile: no profile colour", build.PROFILE_COLOUR in node.inputs, False)

mat, node = built("MSM_DefaultLit", cosmetic=False)
check("a world material the game doesn't scatter gets nothing", build.BASE_SUBSURFACE in node.inputs, False)

mat, node = built("MSM_DefaultLit", cosmetic=True, shell=True)
check("fur: its own inputs", (build.SUBSURFACE_INTENSITY in node.inputs, build.BASE_SUBSURFACE in node.inputs,
                              build.PROFILE_COLOUR in node.inputs), (True, False, False))
check("fur scatters fully", bsdf_input(node, "Subsurface Weight"), 1.0)
node.inputs[build.SCATTER_DISTANCE].default_value = 0.0
check("distance 0 is accepted", bsdf_input(node, "Subsurface Scale"), 0.0)

mat, node = built("MSM_Subsurface", opacity=0.5)
check("a Subsurface material's own radius gets no profile colour", build.PROFILE_COLOUR in node.inputs, False)
check("...but keeps its radius input", build.SUBSURFACE_RADIUS in node.inputs, True)
check("shapes differ by cosmetic", build.shape_key({"graph": "g", "cosmetic": True}) != build.shape_key({"graph": "g", "cosmetic": False}), True)

print("[subsurface_check] %d passed, %d failed" % (PASSES[0], len(FAILS)))
if FAILS:
    sys.exit(1)
