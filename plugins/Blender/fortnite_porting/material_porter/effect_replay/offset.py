"""An effect's Offset (the side panel's slider on its empty): its replay played that many frames later (negative:
earlier), shifted in place without replaying. Replay Effect keeps it (play starts at Start Frame + Offset)."""
import re

import bpy

from .stand import KEY_ROOT

APPLIED = "mp_offset_applied"      # on an effect's empty: the frames its replay is shifted by now


def shift(root, frames):
    """Move the effect's replay `frames` along: each player's Start Frame and the replay's keyframes (lights, bound values)."""
    for obj in bpy.data.objects:
        if obj.get(KEY_ROOT) != root:
            continue
        modifier = obj.modifiers.get("Particles") if obj.type == 'MESH' else None
        if modifier is not None and modifier.node_group is not None:
            for item in modifier.node_group.interface.items_tree:
                if item.item_type == 'SOCKET' and item.in_out == 'INPUT' and item.name == "Start Frame":
                    socket = getattr(modifier.properties.inputs, item.identifier)
                    socket.value = socket.value + frames
            obj.update_tag()        # an input set this way doesn't tag the object for evaluation
        for owner in (obj, obj.data):
            action = owner.animation_data.action if owner is not None and getattr(owner, "animation_data", None) else None
            # the replay's own ("<name> replay"; a light's data's gets a .001 beside its object's)
            if action is None or not re.search(r" replay(\.\d+)?$", action.name):
                continue
            for curve in (c for layer in action.layers for strip in layer.strips for bag in strip.channelbags for c in bag.fcurves):
                for key in curve.keyframe_points:
                    key.co.x += frames
                    key.handle_left.x += frames
                    key.handle_right.x += frames
                curve.update()
            owner.update_tag(refresh={'TIME'})       # its animation evaluated again
    root[APPLIED] = int(root.get(APPLIED, 0)) + frames


def _changed(self, context):
    if (frames := self.fpmp_effect_offset - int(self.get(APPLIED, 0))) != 0:
        shift(self, frames)
        # the frame shown evaluated again with the shifted keyframes
        context.scene.frame_set(context.scene.frame_current)


def register():
    bpy.types.Object.fpmp_effect_offset = bpy.props.IntProperty(
        name="Offset", default=0, soft_min=-250, soft_max=250, update=_changed,
        description="Frames the effect plays later (negative: earlier), on top of its Start Frame")


def unregister():
    del bpy.types.Object.fpmp_effect_offset
