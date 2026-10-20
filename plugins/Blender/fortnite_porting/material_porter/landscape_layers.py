"""A landscape's paint layers, three to a colour attribute (Attribute1's red, green and blue, then Attribute2's...).

Eevee draws a material through at most 14 mesh attributes (past it: magenta), and Fortnite's big landscape materials
blend 20 layers and more; packed, 36 layers take 12. The mesh keeps which channel holds which layer
(obj["mp_layers"]: {layer: "Attribute1.R"}); the materials built for it read those channels."""
import numpy as np

KEY = "mp_layers"
ALL = "mp_layers_all"       # a mesh a material was put on: every layer built, painted or not, ready to paint
PREFIX = "Attribute"
CHANNELS = "RGB"


def table(obj):
    """{layer: (attribute, channel index)} of a mesh's packed layers; empty when its layers aren't packed."""
    out = {}
    for layer, where in dict(obj.get(KEY) or {}).items():
        attribute, _, channel = str(where).partition(".")
        if channel in CHANNELS:
            out[layer] = (attribute, CHANNELS.index(channel))
    return out


def _values(attribute):
    v = np.empty(len(attribute.data) * 4, dtype=np.float32)
    attribute.data.foreach_get("color", v)
    return v.reshape(-1, 4)


def weight(obj, layer):
    """A layer's total weight on the mesh: its packed channel, or (unpacked) its own attribute's red."""
    attribute, channel = table(obj).get(layer, (layer, 0))
    a = obj.data.color_attributes.get(attribute)
    if a is None or not len(a.data):
        return 0.0
    return float(_values(a)[:, channel].sum())


def layers(obj):
    """Every layer on the mesh, packed or not (its other colour attributes, COL0 aside)."""
    packed = table(obj)
    if packed:
        return sorted(packed)
    return sorted(a.name for a in obj.data.color_attributes if a.name != "COL0" and not a.name.startswith("mp_"))


def painted(obj):
    return [layer for layer in layers(obj) if weight(obj, layer) > 0.0]


def built(obj):
    """The layers a material for the mesh blends: all of them on a mesh a material was put on, else the painted ones
    (a map's landscape component, as UE compiles it)."""
    return layers(obj) if obj.get(ALL) else painted(obj)


def key(obj, chosen=None):
    """What a material built for the mesh depends on: its layers (or `chosen`) and where each sits."""
    packed = table(obj)
    return "+".join("%s@%s.%s" % ((layer,) + (packed[layer][0], CHANNELS[packed[layer][1]])) if layer in packed else layer
                    for layer in (built(obj) if chosen is None else chosen))


def _attribute(mesh, name, data_type, domain):
    a = mesh.color_attributes.get(name)
    if a is None:
        a = mesh.color_attributes.new(name, data_type, domain)
        black = np.zeros((len(a.data), 4), dtype=np.float32)
        black[:, 3] = 1.0
        a.data.foreach_set("color", black.ravel())
    return a


def _place(obj, names, data_type, domain):
    """Free channels for `names` (in order), after the ones already packed: {layer: (attribute, channel)}."""
    packed = table(obj)
    used = {(a, c) for a, c in packed.values()}
    slots = ((PREFIX + str(i), c) for i in range(1, 1000) for c in range(len(CHANNELS)))
    for layer in names:
        if layer in packed:
            continue
        slot = next(s for s in slots if s not in used)
        _attribute(obj.data, slot[0], data_type, domain)
        packed[layer] = slot
        used.add(slot)
    obj[KEY] = {layer: "%s.%s" % (a, CHANNELS[c]) for layer, (a, c) in packed.items()}
    return packed


def pack(obj, names):
    """Move a mesh's single-layer attributes (`names`: its layers) into packed channels and drop them."""
    mesh = obj.data
    sources = [mesh.color_attributes[n] for n in sorted(names) if n in mesh.color_attributes]
    if not sources:
        return {}
    first = sources[0]
    weights = {s.name: _values(s)[:, 0].copy() for s in sources}
    data_type, domain = first.data_type, first.domain
    for s in sources:
        mesh.color_attributes.remove(s)
    packed = _place(obj, sorted(weights), data_type, domain)
    _write(obj, {layer: weights[layer] for layer in weights}, packed)
    return packed


def make(obj, names):
    """Packed channels for `names` on a mesh that lacks them, unpainted; every layer built from then on."""
    packed = _place(obj, [n for n in names if n not in table(obj)], 'FLOAT_COLOR', 'POINT')
    obj[ALL] = 1.0
    return packed


def _write(obj, weights, packed):
    by_attribute = {}
    for layer, w in weights.items():
        attribute, channel = packed[layer]
        by_attribute.setdefault(attribute, []).append((channel, w))
    for attribute, channels in by_attribute.items():
        a = obj.data.color_attributes[attribute]
        v = _values(a)
        for channel, w in channels:
            v[:, channel] = w
        a.data.foreach_set("color", v.ravel())
