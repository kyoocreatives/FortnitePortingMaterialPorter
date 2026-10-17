"""Checks overlay outlines (material_porter.outlines): a part's faces copied with its overlay material.

    blender -b --factory-startup --python-exit-code 1 -P tests/plugin/outline_check.py -- <plugin parent>

<plugin parent> holds the plugin as package fpmp_baseline (see translator_test.py). Exit code 0 when every check passes."""
import sys
from types import SimpleNamespace

import bpy

sys.path.insert(0, sys.argv[sys.argv.index("--") + 1])
import fpmp_baseline  # noqa: E402,F401
from fpmp_baseline.material_porter import outlines  # noqa: E402

FAILS, PASSES = [], [0]


def check(name, got, want):
    if got == want:
        PASSES[0] += 1
    else:
        FAILS.append(name)
        print("[outline_check] FAIL %s: got %r, want %r" % (name, got, want))


def cube(name, material):
    bpy.ops.mesh.primitive_cube_add()
    o = bpy.context.active_object
    o.name = name
    o.data.materials.append(material)
    return o


body, head, ink = (bpy.data.materials.new(n) for n in ("body", "head", "ink"))
part = cube("part", body)
other = cube("other", head)
# FP's own toon outline (a Solidify shell with M_FP_Outline) on the part, as FortnitePorting adds it to toon materials
fp_outline = bpy.data.materials.new("M_FP_Outline")
part.data.materials.append(fp_outline)
solid = part.modifiers.new("Outline", 'SOLIDIFY')
solid.material_offset = 1
context = SimpleNamespace()
outlines.remember(context, [body], ink)
outlines.apply(context, [part, other, None])


def evaluated(o):
    e = o.evaluated_get(bpy.context.evaluated_depsgraph_get())
    m = e.to_mesh()
    faces = [e.material_slots[p.material_index].material.name if p.material_index < len(e.material_slots) else None
             for p in m.polygons]
    e.to_mesh_clear()
    return faces


faces = evaluated(part)
check("the part's faces are copied once with the overlay material", (faces.count("body"), faces.count("ink")), (6, 6))
check("a part without an overlay is left alone", (len(other.modifiers), evaluated(other).count("ink")), (0, 0))
check("the overlay material sits in the part's slots, to pick and edit", "ink" in [m.name for m in part.data.materials], True)
check("FP's own outline gives way to the game's", ([m.type for m in part.modifiers], "M_FP_Outline" in [m.name for m in part.data.materials if m]),
      (['NODES'], False))
outlines.apply(context, [part])
check("applying again adds no second outline", len([m for m in part.modifiers if m.name == outlines.MODIFIER]), 1)

print("[outline_check] %d passed, %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
