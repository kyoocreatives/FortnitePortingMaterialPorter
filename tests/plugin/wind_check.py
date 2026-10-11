"""Checks the custom wind: Blender Wind force fields read as one velocity per frame (gusts and turbulence on scene time),
the gust properties and their Physics-tab panel, and the dynamics runner handing it to the replayed nodes.

    blender -b --factory-startup --python-exit-code 1 -P tests/plugin/wind_check.py -- <plugin parent>

<plugin parent> holds the plugin as package fpmp_baseline (see translator_test.py). Exit code 0 when every check passes."""
import math
import sys
from types import SimpleNamespace

import bpy
from mathutils import Euler, Vector

sys.path.insert(0, sys.argv[sys.argv.index("--") + 1])
import fpmp_baseline  # noqa: E402,F401
from fpmp_baseline.processing.context import wind  # noqa: E402

FAILS, PASSES = [], [0]


def check(name, got, want, tol=1e-4):
    if isinstance(want, float):
        ok = got is not None and abs(got - want) <= tol
    elif isinstance(want, (tuple, list)) and want and all(isinstance(w, float) for w in want):
        ok = got is not None and len(got) == len(want) and all(abs(g - w) <= tol for g, w in zip(got, want))
    else:
        ok = got == want
    if ok:
        PASSES[0] += 1
    else:
        FAILS.append(name)
        print("[wind_check] FAIL %s: got %r, want %r" % (name, got, want))


wind.register()
scene = bpy.context.scene


def field(name, strength=2.0, rotation=(0.0, 0.0, 0.0), gust=0.0, turbulence=0.0, kind='WIND'):
    bpy.ops.object.effector_add(type=kind)        # a field's settings exist only once Blender adds one
    o = bpy.context.active_object
    o.name = name
    o.field.strength = strength
    o.rotation_euler = Euler(rotation)
    o.fpmp_wind_gust, o.fpmp_wind_turbulence = gust, turbulence
    bpy.context.view_layer.update()
    return o


def rounded(v):
    return tuple(round(c, 4) for c in v) if v is not None else None


check("a scene without wind fields has none", (wind.fields(scene), wind.velocity([], 0.0)), ([], None))
up = field("Up")
check("a still field blows along its +Z at its strength (m/s)", rounded(wind.velocity(wind.fields(scene), 1.0)), (0.0, 0.0, 2.0))
up.rotation_euler = Euler((math.radians(90.0), 0.0, 0.0))
bpy.context.view_layer.update()
check("a turned field blows along its turned Z", rounded(wind.velocity(wind.fields(scene), 1.0)), (0.0, -2.0, 0.0))
down = field("Down", rotation=(math.radians(-90.0), 0.0, 0.0))
check("two opposite equal fields cancel", rounded(wind.velocity(wind.fields(scene), 1.0)), (0.0, 0.0, 0.0))
down.hide_viewport = True
bpy.context.view_layer.update()
check("a hidden field doesn't blow", [o.name for o in wind.fields(scene)], ["Up"])
field("Vortex", kind='VORTEX')
check("other force fields are not wind", [o.name for o in wind.fields(scene)], ["Up"])
bpy.data.objects.remove(down)

up.fpmp_wind_gust, up.fpmp_wind_gust_speed = 0.4, 0.3
speeds = [wind.velocity([up], k * 0.1).length for k in range(400)]
check("gusts swing the speed within strength x (1 +- gust)", (min(speeds) >= 2.0 * 0.6 - 1e-6, max(speeds) <= 2.0 * 1.4 + 1e-6,
                                                             max(speeds) - min(speeds) > 0.4), (True, True, True))
check("the same time gives the same wind", rounded(wind.velocity([up], 3.7)), rounded(wind.velocity([up], 3.7)))
up.fpmp_wind_gust, up.fpmp_wind_turbulence = 0.0, 15.0
axis = (up.matrix_world.to_3x3() @ Vector((0.0, 0.0, 1.0))).normalized()
angles = [math.degrees(wind.velocity([up], k * 0.13).angle(axis)) for k in range(300)]
check("turbulence keeps the direction within its angle of the axis, and moves it", (max(angles) <= 15.0 * math.sqrt(2.0) + 1e-3,
                                                                                    max(angles) > 1.0), (True, True))
up.field.strength = 0.0
check("a still field blows nothing", wind.velocity([up], 1.0), None)
up.field.strength = float("nan")
check("a broken strength blows nothing", wind.velocity([up], 1.0), None)

# range: Blender's own falloff (effect.cc) at the character's root
up.field.strength, up.fpmp_wind_turbulence = 2.0, 0.0
up.rotation_euler = Euler((0.0, 0.0, 0.0))
up.location = (0.0, 0.0, 0.0)
bpy.context.view_layer.update()
f = up.field
check("default falloff: full strength anywhere", rounded(wind.velocity([up], 1.0, Vector((40.0, -7.0, 3.0)))), (0.0, 0.0, 2.0))
f.use_max_distance, f.distance_max = True, 3.0
check("a plane field reaches up to its max distance along its axis", rounded(wind.velocity([up], 1.0, Vector((50.0, 0.0, 2.5)))), (0.0, 0.0, 2.0))
check("and no further", wind.velocity([up], 1.0, Vector((0.0, 0.0, 3.5))), None)
f.shape = 'POINT'
check("a point field measures from its centre", wind.velocity([up], 1.0, Vector((2.5, 2.5, 0.0))), None)
f.use_max_distance, f.falloff_power = False, 2.0
check("power falloff: (1 + d)^-power", rounded(wind.velocity([up], 1.0, Vector((0.0, 1.0, 0.0)))), (0.0, 0.0, 0.5))
f.use_min_distance, f.distance_min = True, 2.0
check("inside min distance: full strength", rounded(wind.velocity([up], 1.0, Vector((0.0, 1.0, 0.0)))), (0.0, 0.0, 2.0))
check("past it the falloff starts from it", rounded(wind.velocity([up], 1.0, Vector((0.0, 3.0, 0.0)))), (0.0, 0.0, 0.5))
f.use_min_distance, f.falloff_power, f.shape = False, 0.0, 'PLANE'
f.z_direction = 'POSITIVE'
check("positive Z only: nothing behind the field", (wind.velocity([up], 1.0, Vector((0.0, 0.0, -1.0))),
                                                    rounded(wind.velocity([up], 1.0, Vector((0.0, 0.0, 1.0))))), (None, (0.0, 0.0, 2.0)))
f.z_direction, f.falloff_type = 'BOTH', 'TUBE'
f.use_radial_max, f.radial_max = True, 1.0
check("a tube reaches its radius off the axis", (rounded(wind.velocity([up], 1.0, Vector((0.5, 0.0, 4.0)))),
                                                 wind.velocity([up], 1.0, Vector((1.5, 0.0, 4.0)))), ((0.0, 0.0, 2.0), None))
f.falloff_type, f.use_radial_max = 'CONE', True
f.radial_max = 30.0
check("a cone reaches its angle off the axis", (rounded(wind.velocity([up], 1.0, Vector((1.0, 0.0, 4.0)))),
                                                wind.velocity([up], 1.0, Vector((4.0, 0.0, 1.0)))), ((0.0, 0.0, 2.0), None))
f.falloff_type, f.use_radial_max = 'SPHERE', False

fresh = bpy.data.objects.new("Fresh", None)
check("the gust properties' defaults", (round(fresh.fpmp_wind_gust, 4), round(fresh.fpmp_wind_gust_speed, 4), round(fresh.fpmp_wind_turbulence, 4)),
      (0.4, 0.3, 15.0))
check("the panel shows for wind fields only", (wind.FPMP_PT_CharacterWind.poll(SimpleNamespace(object=up)),
                                               wind.FPMP_PT_CharacterWind.poll(SimpleNamespace(object=fresh)),
                                               wind.FPMP_PT_CharacterWind.poll(SimpleNamespace(object=None))), (True, False, False))
scene.render.fps, scene.render.fps_base = 30, 1.0
scene.frame_set(45)
check("scene time from the frame", wind.scene_time(scene), 1.5)

print("[wind_check] %d passed, %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
