"""Decides per material slot whether to build an exact material, reuse one, or leave it to FP's shaders
(called from processing/context/material_context.py)."""
import json

import bpy

from ..processing.enums import EExportType
from ..processing.material.enums import ENodeType
from ..processing.material.mappings import DefaultMappings
from ..processing.material.mappings_registry import find_all_matching_mappings
from ..processing.material.utils import get_param
from ..utils import any, hash_code

# the exports "Prefer FP Shaders for Characters" is about
FP_SHADER_TYPES = [EExportType.OUTFIT, EExportType.BACKPACK, EExportType.PICKAXE, EExportType.GLIDER, EExportType.PET,
                   EExportType.KICKS, EExportType.SIDEKICK, EExportType.FALL_GUYS_OUTFIT]


def has_fp_shader(material_data):
    """Whether FP has a shader for the material: a matching base mapping, or a known default diffuse texture."""
    if any(find_all_matching_mappings(material_data), lambda m: m.type == ENodeType.NT_Base):
        return True
    diffuse = {s.name.casefold() for s in DefaultMappings.textures if s.slot == "Diffuse"}
    return any(material_data.get("Textures") or [], lambda t: (t.get("Name") or "").casefold() in diffuse)


def is_toon(material_data):
    """Whether FP picks its toon shader (the last matching mapping). Such materials get FP's outline whoever builds them."""
    mappings = find_all_matching_mappings(material_data)
    return bool(mappings) and mappings[-1].node_name == "FPv4 Base Toon"


class ExactChoice:
    """One slot's choice. A material built under different settings (prefer FP, Rim Light, subsurface, landscape layers) isn't reused."""

    def __init__(self, ctx, material_data, material_slot, as_material_data):
        from .hook import cosmetic_material, subsurface as subsurface_of
        self.ctx = ctx
        self.prefer_fp = bool(ctx.options.get("PreferFPShaders") and ctx.type in FP_SHADER_TYPES and has_fp_shader(material_data))
        self.rim_light = bool(ctx.options.get("RimLight"))
        self.subsurface = "%g %g %g %g %s" % (tuple(subsurface_of(ctx, material_data)) + (cosmetic_material(ctx, material_data),))
        # a landscape proxy gets one material per set of painted layers (see hook.build_exact)
        slot_object = None if as_material_data else material_slot.id_data
        self.landscape = "+".join(sorted(a.name for a in slot_object.data.color_attributes if a.name != "COL0")) \
            if slot_object is not None and slot_object.type == 'MESH' and slot_object.get("mp_landscape") else None

    def reusable(self, existing):
        return (bool(existing.get("MPPreferFP")) == self.prefer_fp
                and bool(existing.get("MPRimLight", self.rim_light)) == self.rim_light
                and existing.get("MPSubsurface", self.subsurface) == self.subsurface
                and existing.get("MPLandscapeLayers") == self.landscape)

    def reused(self, material_slot, existing, material_data):
        """A reused material still needs this mesh's UV maps, outline and hidden elements."""
        from .placement import ensure_slot_uvs
        ensure_slot_uvs(material_slot, existing)
        if is_toon(material_data):
            self.ctx.add_toon_outline = True
        if hide := existing.get("MPHideElements"):
            self.ctx.partial_vertex_crunch_materials[existing] = json.loads(hide)

    def use_exact(self, material):
        # time of day gets its sky and clouds later, once the day is known (material_porter.sky)
        from .hook import exact_available
        if self.prefer_fp:
            material["MPPreferFP"] = True
        return exact_available(self.ctx) and not self.prefer_fp and getattr(self.ctx, "type", None) != EExportType.TIME_OF_DAY

    def build(self, material, material_data, meta, override_parameters, material_slot, as_material_data,
              switches, scalars, material_hash, hash_key, material_name, material_hash_cache, material_name_cache):
        """The exact material in the slot; False when it can't be built (FP's shader then)."""
        from .hook import build_exact
        exact = build_exact(self.ctx, material_data, meta.get("TextureData"), override_parameters,
                            None if as_material_data else material_slot.id_data)
        if not exact:
            return False
        # outline is FP's: a Solidify shell drawn with M_FP_Outline
        if is_toon(material_data):
            self.ctx.add_toon_outline = True
        exact["Hash"] = hash_code(material_hash)
        exact["MPRimLight"] = self.rim_light
        exact["MPSubsurface"] = self.subsurface
        exact["OriginalName"] = material_data.get("Name")
        if self.landscape is not None:
            exact["MPLandscapeLayers"] = self.landscape
        elif "MPLandscapeLayers" in exact:
            del exact["MPLandscapeLayers"]
        # elements a style hides (Hide Element 0X, by vertex colour): FP's vertex crunch removes their faces
        if get_param(switches, "Use Vertex Colors for Mask"):
            hide = {scalar.get("Name"): scalar.get("Value") for scalar in scalars if "Hide Element" in scalar.get("Name")}
            self.ctx.partial_vertex_crunch_materials[exact] = hide
            exact["MPHideElements"] = json.dumps(hide)
        elif "MPHideElements" in exact:
            del exact["MPHideElements"]
        if not as_material_data:
            from .placement import ensure_slot_uvs
            material_slot.material = exact
            ensure_slot_uvs(material_slot, exact)
        if material.users == 0:
            bpy.data.materials.remove(material)
        material_hash_cache[hash_key] = exact
        material_name_cache[material_name.casefold()] = exact
        return True
