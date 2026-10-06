"""A light inside a blueprint (a street lamp's spot in its lantern, a car's headlights) is shadowed by the
blueprint's own meshes in Blender: the housing around it blocks it whole, and the lamp lights nothing. The
game draws the same geometry, but its lamps are authored with that in mind (the spot sits where the glass
is, the shadow map is coarse). Blender's shadow linking settles it: each such light gets a blocker
collection holding the actor's own meshes, excluded - they alone cast no shadow from it, everything else
still does (a collection of excluded objects only leaves the rest included).

The import marks meshes and lights with the actor they came from (mp_actor: the map reader's actor name,
placement.after_import and mesh_context.create_light); link_housings pairs them after a level import.
Cycles and Eevee both honour light linking (Blender 4.2 on).
"""
import bpy

from ..logger import Log

KEY = "mp_actor"                    # on a mesh or light object: the actor it came from
PREFIX = "Shadow Linking "          # the blocker collections, one per actor


def link_housings(context):
    """Pair each light with the meshes of its actor, as blockers excluded from its shadow."""
    objects = context.collection.all_objects if context.collection is not None else bpy.context.scene.objects
    meshes, lights = {}, []
    for o in objects:
        actor = o.get(KEY)
        if not actor:
            continue
        if o.type == 'MESH':
            meshes.setdefault(actor, []).append(o)
        elif o.type == 'LIGHT':
            lights.append(o)
    if not lights:
        return
    made, linked = {}, 0
    for light in lights:
        housing = meshes.get(light[KEY])
        if not housing:
            continue
        coll = made.get(light[KEY])
        if coll is None:
            coll = bpy.data.collections.new(PREFIX + str(light[KEY]))
            for o in housing:
                coll.objects.link(o)
            for member in coll.collection_objects:      # (no lookup by name on this collection)
                member.light_linking.link_state = 'EXCLUDE'
            made[light[KEY]] = coll
        light.light_linking.blocker_collection = coll
        linked += 1
    if linked:
        Log.info("[Material Porter] shadow linking: %d lights no longer shadowed by their own actor's meshes (%d actors)"
                 % (linked, len(made)))
