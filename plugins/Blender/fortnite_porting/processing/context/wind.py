"""Custom wind for the replayed dynamics: the scene's Blender Wind force fields read as one velocity per frame. A
field's +Z is the direction, its strength the speed (m/s); gusts swing the speed and turbulence turns the direction,
both on UE's Perlin noise over scene time, so Live and bakes see the same wind. Shape, falloff and Blender's own field
noise are ignored: the wind is the same everywhere."""
import math

import bpy
from mathutils import Matrix, Vector

from .dynamics_solver import WIND_ACCEL  # noqa: F401 (the bodies' push, kept beside the solvers)
from .perlin import perlin

SEEDS = (31.7, 77.1, 151.3)     # the gust's and the two turbulence turns' noise offsets


def fields(scene):
    """The scene's visible Wind force fields."""
    return [o for o in scene.objects if o.field and o.field.type == 'WIND' and o.visible_get()]


def scene_time(scene):
    return (scene.frame_current + scene.frame_subframe) * scene.render.fps_base / scene.render.fps


def velocity(found, time):
    """The summed wind (world, m/s) at scene time, None when no field blows."""
    total, blowing = Vector(), False
    for o in found:
        strength = o.field.strength
        basis = o.matrix_world.to_3x3().normalized()
        if not math.isfinite(strength) or abs(strength) < 1e-9 or not all(math.isfinite(c) for row in basis for c in row):
            continue
        rate = o.fpmp_wind_gust_speed
        speed = max(0.0, strength * (1.0 + o.fpmp_wind_gust * perlin(time * rate + SEEDS[0])))
        turb = math.radians(o.fpmp_wind_turbulence)
        turn = Matrix.Rotation(turb * perlin(time * rate * 0.5 + SEEDS[1]), 3, 'X') @ \
            Matrix.Rotation(turb * perlin(time * rate * 0.5 + SEEDS[2]), 3, 'Y')
        total += basis @ turn @ Vector((0.0, 0.0, 1.0)) * speed
        blowing = True
    return total if blowing else None


class FPMP_PT_CharacterWind(bpy.types.Panel):
    bl_label = "Character Wind"
    bl_space_type, bl_region_type, bl_context = 'PROPERTIES', 'WINDOW', "physics"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        o = context.object
        return o is not None and o.field is not None and o.field.type == 'WIND'

    def draw(self, context):
        col = self.layout.column()
        col.label(text="Pushes imported characters' dynamics (strength: m/s)")
        col.prop(context.object, "fpmp_wind_gust", slider=True)
        col.prop(context.object, "fpmp_wind_gust_speed")
        col.prop(context.object, "fpmp_wind_turbulence")


def register():
    bpy.types.Object.fpmp_wind_gust = bpy.props.FloatProperty(
        name="Gust", default=0.4, min=0.0, max=1.0, description="How far the wind's speed swings about its strength")
    bpy.types.Object.fpmp_wind_gust_speed = bpy.props.FloatProperty(
        name="Gust Speed", default=0.3, min=0.0, soft_max=5.0, unit='NONE', description="How fast the gusts come (Hz)")
    bpy.types.Object.fpmp_wind_turbulence = bpy.props.FloatProperty(
        name="Turbulence", default=15.0, min=0.0, max=90.0, description="How far the wind's direction wanders (degrees)")
    bpy.utils.register_class(FPMP_PT_CharacterWind)


def unregister():
    bpy.utils.unregister_class(FPMP_PT_CharacterWind)
    del bpy.types.Object.fpmp_wind_turbulence
    del bpy.types.Object.fpmp_wind_gust_speed
    del bpy.types.Object.fpmp_wind_gust
