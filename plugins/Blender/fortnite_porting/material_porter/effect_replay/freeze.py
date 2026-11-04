"""A replayed effect held on one frame (for stills): whatever frame the scene is on, its players show the replay's
frame F. Each player's Start Frame input gets a driver `frame - k` (the node group shows scene frame - Start Frame),
the replay's keyed values (lights, bound parameters) are muted with their frame-F values set. Thawing undoes both."""
import re

from .stand import KEY_ROOT

FROZEN = "mp_effect_frozen"         # on the root: the scene frame it is held on
_KEPT = "mp_unfrozen_start"         # on a player: its Start Frame before the hold


def _start_socket(modifier):
    for item in modifier.node_group.interface.items_tree:
        if item.item_type == 'SOCKET' and item.in_out == 'INPUT' and item.name == "Start Frame":
            return getattr(modifier.properties.inputs, item.identifier)
    return None


def _players(root, scene):
    for obj in scene.objects:
        if obj.get(KEY_ROOT) != root or obj.type not in {'MESH', 'LIGHT'}:
            continue
        yield obj


def _replay_curves(obj):
    for owner in (obj, obj.data):
        action = owner.animation_data.action if owner is not None and getattr(owner, "animation_data", None) else None
        if action is None or not re.search(r" replay(\.\d+)?$", action.name):
            continue
        for curve in (c for layer in action.layers for strip in layer.strips for bag in strip.channelbags for c in bag.fcurves):
            yield owner, curve


def _set(owner, path, index, value):
    base, _dot, name = path.rpartition(".")
    try:
        holder = owner.path_resolve(base) if base else owner
        current = getattr(holder, name)
        if hasattr(current, "__len__"):
            current[index] = value
        else:
            setattr(holder, name, value)
    except (AttributeError, ValueError, TypeError, IndexError):
        pass        # a value it can't hold on: it keeps the frame it was on


def freeze(root, scene, frame):
    """Hold `root`'s replay on what it shows at scene frame `frame`. Returns how many players were held."""
    thaw(root, scene)
    held = 0
    for obj in _players(root, scene):
        modifier = obj.modifiers.get("Particles") if obj.type == 'MESH' else None
        socket = _start_socket(modifier) if modifier is not None and modifier.node_group is not None else None
        if socket is not None:
            obj[_KEPT] = socket.value
            curve = socket.driver_add("value")
            curve.driver.type = 'SCRIPTED'
            curve.driver.expression = "frame - %d" % (frame - int(socket.value))
            obj.update_tag()
            held += 1
        for owner, curve in _replay_curves(obj):
            _set(owner, curve.data_path, curve.array_index, curve.evaluate(frame))
            curve.mute = True       # (a muted curve leaves the value as set)
    root[FROZEN] = int(frame)
    return held


def thaw(root, scene):
    """The replay plays with the timeline again."""
    if FROZEN not in root:
        return
    for obj in _players(root, scene):
        modifier = obj.modifiers.get("Particles") if obj.type == 'MESH' else None
        socket = _start_socket(modifier) if modifier is not None and modifier.node_group is not None else None
        if socket is not None and _KEPT in obj:
            socket.driver_remove("value")
            socket.value = obj[_KEPT]
            del obj[_KEPT]
            obj.update_tag()
        for _owner, curve in _replay_curves(obj):
            curve.mute = False
    del root[FROZEN]


def frozen(root, scene):
    """The frame `root` is held on, None when it plays (a replay since a freeze plays again)."""
    if FROZEN not in root or not any(_KEPT in o for o in _players(root, scene)):
        return None
    return root[FROZEN]
