"""Ground Blend on foliage whose game material doesn't read the RVT (Hera's grass, any grass or foliage): the
ground's baked colour (rvt.py) under the plant's root, as Apollo's grass reads it.

Per instance (Attribute nodes, INSTANCER: scatter layers, placed foliage; a hand-placed object's custom properties):
mp_ground_blend is the strength (0, or missing: the game's colour), mp_ground_style 0 for the whole blade, 1 root to
tip (the ground at the foot fading to the plant's colour at the top, Chapter 2's grass), mp_ground_off 1 forces a
material that does read the RVT onto its no-RVT side (env.virtual_texture_valid).

The plant's own variation stays: the ground colour is scaled by the base colour's brightness over Ground Reference,
the material's typical brightness."""
from .ue_graph import Val

ATTR_OFF = "mp_ground_off"
ATTR_BLEND = "mp_ground_blend"
ATTR_STYLE = "mp_ground_style"
MODES = ("Game", "On", "Off")
STYLES = ("Whole blade", "Root to tip")
PANEL = "Ground"
REFERENCE = "Ground Reference"
DEFAULTS = {REFERENCE: 0.12}      # Hera Grass01 blades' mean brightness (land_ground scenario)
CM = (100.0, -100.0, 100.0)     # Blender metres to UE centimetres (Y mirrored)


def values(mode, style, strength):
    """The per-instance attribute values for a mode (MODES), style (STYLES) and strength."""
    return {ATTR_OFF: 1.0 if mode == "Off" else 0.0,
            ATTR_BLEND: float(strength) if mode == "On" else 0.0,
            ATTR_STYLE: float(STYLES.index(style))}


def attribute(tr, name):
    """A per-instance attribute (0 where missing), one node per tree."""
    return tr.shared("attr " + name, lambda: Val(tr.node("ShaderNodeAttribute", name, attribute_type='INSTANCER',
                                                         attribute_name=name).outputs["Fac"], 1))


def blend(tr, base):
    """The base colour towards the ground under the root, by the instance's Ground Blend."""
    from . import foliage, rvt
    iface = tr.tree.interface
    sock = iface.new_socket(REFERENCE, in_out='INPUT', socket_type='NodeSocketFloat')
    sock.default_value, sock.min_value, sock.max_value = DEFAULTS[REFERENCE], 0.001, 1.0
    sock.description = "The plant's typical brightness: the ground colour is scaled by the blade's over this"
    gi = tr.node("NodeGroupInput", "ground reference")
    ref = Val(next(o for o in gi.outputs if o.identifier == sock.identifier), 1)

    loc = Val(tr.shared("object info", lambda: tr.node("ShaderNodeObjectInfo", "object info")).outputs["Location"], 3)
    look = tr.node("ShaderNodeGroup", "ground under the root")
    look.node_tree = rvt.lookup_group()
    tr.link(tr.vmath('MULTIPLY', loc, tr.const(CM, 3), out_w=3), look.inputs["Position"])
    ground, valid = Val(look.outputs["Base Color"], 3), Val(look.outputs["Valid"], 1)

    lum = Val(tr.node("ShaderNodeRGBToBW", "base brightness").outputs[0], 1)
    tr.link(tr.as3(base), lum.s.node.inputs[0])
    # at most 2: a pale blade over a dark reference would multiply the ground by 5 or more
    k = tr.math('MINIMUM', tr.math('DIVIDE', lum, tr.math('MAXIMUM', ref, tr.const(1e-3))), tr.const(2.0))
    whole = tr.vmath('SCALE', ground, k, out_w=3)
    h = Val(foliage.plant_height(tr), 1)
    tip = tr.lerp(whole, tr.as3(base), tr.math('POWER', h, tr.const(3.0)), label="root to tip")
    target = tr.lerp(whole, tip, attribute(tr, ATTR_STYLE), label="ground style")
    weight = tr.math('MULTIPLY', attribute(tr, ATTR_BLEND), valid)
    return tr.lerp(tr.as3(base), target, weight, label="ground blend")


def panel(root):
    """Put Ground Reference in a Ground panel after the Foliage one (closed)."""
    iface = root.interface
    it = next((it for it in iface.items_tree if it.item_type == 'SOCKET' and it.in_out == 'INPUT'
               and it.name == REFERENCE), None)
    if it is None:
        return
    p = iface.new_panel(PANEL, default_closed=True)
    iface.move_to_parent(it, p, 0)
    after = next((x for x in iface.items_tree if x.item_type == 'PANEL' and x.name in ("Foliage", "Textures")
                  and x.parent == p.parent), None)
    iface.move(p, after.position + 1 if after is not None else 0)


def reset(node):
    """A copied build's group node: the default Ground Reference."""
    sock = node.inputs.get(REFERENCE)
    if sock is not None:
        sock.default_value = DEFAULTS[REFERENCE]


def set_object(obj, mode, style, strength):
    """A hand-placed object's Ground Blend (its custom properties, read like instance attributes)."""
    for k, v in values(mode, style, strength).items():
        obj[k] = v
    obj["mp_ground_mode"] = mode


def get_object(obj):
    """{"mode", "style", "strength"} of a hand-placed object (Game when unset)."""
    style = STYLES[int(round(float(obj.get(ATTR_STYLE, 0.0))))]
    return {"mode": obj.get("mp_ground_mode", "Game"), "style": style,
            "strength": float(obj.get(ATTR_BLEND, 0.0)) or 1.0}
