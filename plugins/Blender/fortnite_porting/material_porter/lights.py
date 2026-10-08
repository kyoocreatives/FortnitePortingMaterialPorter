"""Lights as UE has them: point, spot and rect, their units, falloff and colour temperature."""
from math import radians, cos, pi

import bpy
import numpy as np
from mathutils import Matrix

from ..processing.utils import make_vector, make_euler

# Blender watts per UE candela. FP has always read a point light's candelas as watts (a street lamp shows at
# night; the physical 4 pi / 683 would show nothing), so spot and rect lights follow it.
LIGHT_CANDELA_TO_WATTS = 1.0


def spot_solid_angle(inner, outer):
    """The solid angle (sr) of a UE spot light's cone: UE clamps its half angle between the inner angle and 89 degrees."""
    half = min(max(radians(outer), radians(min(inner, 89.0)) + 0.001), radians(89.0) + 0.001)
    return 2 * pi * (1.0 - cos(half))


def light_candelas(light, solid_angle):
    """
    A UE light's Intensity as candelas along its axis (ULocalLightComponent::GetUnitsConversionFactor): candelas as
    they are, lumens over the solid angle the light fills, and the legacy Unitless 16 / 10,000 of a candela (the 5000 a
    light was placed with is 8 candelas). EV and Nits don't occur on a placed local light: read as candelas.
    A light with the older falloff (Fortnite's street lamps and floodlights: bUseInverseSquaredFalloff off) has a
    brightness instead, whatever its units say, that fades as (1 - (d / reach)^2)^exponent: it is given the candelas
    that light a surface the same a third of the way out (as the particle lights of effect_replay are).
    A light of no brightness (a car's headlights, which its Blueprint turns on when it drives: Intensity 0) is 0 candelas
    whatever its units; a missing, negative or non-finite Intensity counts as 0 too.
    """
    intensity = light.get("Intensity")
    if intensity is None or not np.isfinite(intensity) or intensity <= 0.0:
        return 0.0
    if not light.get("InverseSquaredFalloff", True):
        d = (light.get("AttenuationRadius") or 0.0) / 3.0
        return intensity * (8.0 / 9.0) ** max(light.get("FalloffExponent", 8.0), 0.0) * (d * d + 1.0) / 1e4
    units = light.get("IntensityUnits") or "Candelas"
    if units == "Lumens":
        return intensity / max(solid_angle, 1e-6)
    if units == "Unitless":
        return intensity / 625.0
    return intensity


def kelvin_tint(kelvin):
    """A colour temperature's linear sRGB tint at unit luminance (UE's FLinearColor::MakeFromColorTemperature: Krystek's
    Planckian locus in CIE 1960 UCS, 6500 K being about white)."""
    t = min(max(kelvin, 1000.0), 15000.0)
    u = (0.860117757 + 1.54118254e-4 * t + 1.28641212e-7 * t * t) / (1.0 + 8.42420235e-4 * t + 7.08145163e-7 * t * t)
    v = (0.317398726 + 4.22806245e-5 * t + 4.20481691e-8 * t * t) / (1.0 - 2.89741816e-5 * t + 1.61456053e-7 * t * t)
    d = 2.0 * u - 8.0 * v + 4.0
    x = 3.0 * u / d
    y = 2.0 * v / d
    x_, z_ = x / y, (1.0 - x - y) / y
    return (max(3.2404542 * x_ - 1.5371385 - 0.4985314 * z_, 0.0),
            max(-0.9692660 * x_ + 1.8760108 + 0.0415560 * z_, 0.0),
            max(0.0556434 * x_ - 0.2040259 + 1.0572252 * z_, 0.0))


class LightImportMixin:
    """MeshImportContext's lights: FP's point lights plus the level's spot and rect lights."""

    def import_mp_lights(self, lights, parent=None):
        for spot_light in lights.get("SpotLights") or []:
            self.import_spot_light(spot_light, parent)
        for rect_light in lights.get("RectLights") or []:
            self.import_rect_light(rect_light, parent)

    def import_point_light(self, point_light, parent=None):
        light, light_data = self.create_light(point_light, 'POINT', 4 * pi, parent)
        # (a point light looks the same whichever way it faces)
        light.scale = make_vector(point_light.get("Scale"))
        light_data.shadow_soft_size = point_light.get("Radius") * self.scale

    def import_spot_light(self, spot_light, parent=None):
        outer = max(spot_light.get("OuterConeAngle"), 0.5)
        inner = min(max(spot_light.get("InnerConeAngle"), 0.0), outer)
        light, light_data = self.create_light(spot_light, 'SPOT', spot_solid_angle(inner, outer), parent)
        # UE's cone angles are from the axis, Blender's size is the whole cone; its blend is the fraction of the cone
        # that fades out, where UE's inner angle is where the fade starts
        light_data.spot_size = radians(min(max(outer * 2, 1.0), 179.0))
        light_data.spot_blend = min(max(1.0 - inner / outer, 0.0), 1.0)
        light_data.shadow_soft_size = spot_light.get("Radius") * self.scale

    def import_rect_light(self, rect_light, parent=None):
        # (a rect light's lumens leave one face, as a Lambertian surface's do: candelas along its axis are lumens / pi)
        light, light_data = self.create_light(rect_light, 'AREA', pi, parent)
        light_data.shape = 'RECTANGLE'
        # the light is turned so that its X is UE's up (SourceHeight) and its Y UE's right (SourceWidth)
        light_data.size = rect_light.get("SourceHeight") * self.scale
        light_data.size_y = rect_light.get("SourceWidth") * self.scale

    def create_light(self, data, light_type, solid_angle, parent=None):
        """
        A light object where UE has the light, with its colour (and temperature), brightness, reach and shadows.
        The caller gives its shape (spot cone, rect size...) and the solid angle its lumens spread over.
        """
        name = data.get("Name")
        light_data = bpy.data.lights.new(name=name, type=light_type)
        light = bpy.data.objects.new(name=name, object_data=light_data)
        self.collection.objects.link(light)

        light.parent = parent
        rotation = make_euler(data.get("Rotation"))
        if light_type != 'POINT':
            # a UE spot or rect light shines along its X axis, a Blender one along its -Z
            rotation = (rotation.to_matrix() @ Matrix.Rotation(radians(-90), 3, 'Y')).to_euler()
        light.rotation_euler = rotation
        light.location = make_vector(data.get("Location"), unreal_coords_correction=True) * self.scale

        color = data.get("Color")
        rgb = [color["R"], color["G"], color["B"]]
        if data.get("UseTemperature"):
            rgb = [c * t for c, t in zip(rgb, kelvin_tint(data.get("Temperature") or 6500.0))]
        # (a light's colour stops at white: what a hot tint goes past it moves to its power)
        peak = max(max(rgb), 1.0)
        light_data.color = [c / peak for c in rgb]
        light_data.energy = float(light_candelas(data, solid_angle) * LIGHT_CANDELA_TO_WATTS * peak)
        light_data.use_custom_distance = True
        light_data.cutoff_distance = data.get("AttenuationRadius") * self.scale
        light_data.use_shadow = data.get("CastShadows")
        # what UE stored, to retune the power by
        light_data["ue_intensity"] = data.get("Intensity") or 0.0
        light_data["ue_units"] = data.get("IntensityUnits") or "Candelas"
        # the actor it came from: its own meshes won't shadow it (material_porter.shadow_linking)
        if data.get("Actor"):
            light["mp_actor"] = str(data["Actor"])
        return light, light_data
