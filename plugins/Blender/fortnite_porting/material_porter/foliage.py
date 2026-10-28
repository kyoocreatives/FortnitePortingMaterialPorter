"""Base darkening on foliage (grass, leaves, bushes, trees): an artist control the game doesn't have.

The base colour (and so the subsurface colour, which Blender takes from it) is multiplied towards a dark tint near the
plant's foot, fading up over a share of its height. Height is the point's object-space Z over its mesh's top
(build.mark_bounds' part bounds): 0 at the pivot, where Fortnite foliage meets the ground (Hera grass sinks up to half
its height below it), 1 at the top. Object space and the part bounds travel with instances, so scattered and placed
copies each fade from their own foot. Vertex colour B, the game's own per-blade gradient on grass meshes, isn't used:
the same grass master is on meshes whose colour is flat (SM_IvyUnderbush: B 0.027 everywhere).

Darkening 0 (the default) leaves the game's colour as it is."""
from .ue_graph import PART_BOUNDS_MAX, PART_BOUNDS_MIN, Val

PANEL = "Foliage"
DARKENING = "Base Darkening"
HEIGHT = "Base Height"
TINT = "Base Tint"
INPUTS = (DARKENING, HEIGHT, TINT)
# a dense field shows only its blades' upper half (Hera grass seen at eye level: height 0.6 to 0.9), so the fade
# covers the whole plant by default
DEFAULTS = {DARKENING: 0.0, HEIGHT: 1.0, TINT: (0.25, 0.32, 0.22, 1.0)}


def is_foliage(shading, models, two_sided, blend, entry):
    """UE's foliage model, or two-sided and scattering (subsurface or foliage, picked or compiled), opaque or masked:
    every grass, leaf, bush and tree master on Hera, bark included (tree masters compile subsurface for their
    leaves). The one-sided prop master P_FN_Base_2022 compiles foliage too and stays out."""
    if entry.get("cosmetic") or entry.get("particle") or entry.get("shell"):
        return False
    if blend not in ("BLEND_Opaque", "BLEND_Masked"):
        return False
    compiled = set(models or ()) | {shading}
    return shading == "MSM_TwoSidedFoliage" or \
        (two_sided and bool(compiled & {"MSM_Subsurface", "MSM_TwoSidedFoliage"}))


def darken(tr, base, specular):
    """(base colour, specular) darkened: x mix(1, Base Tint, Base Darkening x a linear fade from the foot to
    Base Height), specular by the tint's brightness."""
    iface = tr.tree.interface

    def new_input(name, kind, description, most=1.0):
        sock = iface.new_socket(name, in_out='INPUT', socket_type=kind)
        sock.default_value, sock.description = DEFAULTS[name], description
        if kind == 'NodeSocketFloat':
            sock.min_value, sock.max_value = 0.0, most
        gi = tr.node("NodeGroupInput", name.lower())
        return next(o for o in gi.outputs if o.identifier == sock.identifier)

    amount = new_input(DARKENING, 'NodeSocketFloat', "How much darker foliage gets at its foot (0: the game's colour)")
    height = new_input(HEIGHT, 'NodeSocketFloat', "How far up the plant the darkening fades out (share of its height)")
    tint = new_input(TINT, 'NodeSocketColor', "The colour the foot is multiplied by at full darkening")
    node = _maker(tr)
    h = plant_height(tr)
    # 1 at the foot to 0 at Base Height
    span = node("ShaderNodeMath", "base height (not 0)", (0, height), operation='MAXIMUM')
    span.inputs[1].default_value = 1e-3
    fade = node("ShaderNodeMapRange", "base fade", (0, h), (2, span.outputs[0]))
    fade.inputs[1].default_value = 0.0
    fade.inputs[3].default_value = 1.0
    fade.inputs[4].default_value = 0.0
    weight = node("ShaderNodeMath", "base darkening", (0, fade.outputs[0]), (1, amount), operation='MULTIPLY')
    mix = node("ShaderNodeMix", "foliage base", (0, weight.outputs[0]), (7, tint), data_type='RGBA', blend_type='MULTIPLY')
    tr.link(tr.as3(base), mix.inputs[6])
    # specular darkens by the tint's brightness too, as occlusion would: a dark foot doesn't go grey under the sky
    grey = node("ShaderNodeRGBToBW", "tint brightness", (0, tint))
    dimmed = node("ShaderNodeMath", "specular x tint brightness", (1, grey.outputs[0]), operation='MULTIPLY')
    tr.link(specular, dimmed.inputs[0])
    spec = node("ShaderNodeMix", "foliage base specular", (0, weight.outputs[0]), (3, dimmed.outputs[0]), data_type='FLOAT')
    tr.link(specular, spec.inputs[2])
    return Val(mix.outputs[2], 3), Val(spec.outputs[0], 1)


def _maker(tr):
    """node(kind, label, (input, socket)..., **props): a node with its links."""
    def node(kind, label, *links, **props):
        n = tr.node(kind, label, **props)
        for i, s in links:
            tr.L.new(s, n.inputs[i])
        return n
    return node


def plant_height(tr):
    """The point's height over its mesh's top (0 at the foot, 1 at the top), shared by the tree; unmarked meshes (no
    part bounds) take their generated Z (0 to 1 over the box)."""
    def make():
        node = _maker(tr)

        def attr(name):
            return node("ShaderNodeAttribute", name, attribute_type='GEOMETRY', attribute_name=name).outputs["Vector"]
        tc = node("ShaderNodeTexCoord", "texture coordinate")
        z = node("ShaderNodeSeparateXYZ", "local position", (0, tc.outputs["Object"])).outputs["Z"]
        top = node("ShaderNodeSeparateXYZ", "part top", (0, attr(PART_BOUNDS_MAX))).outputs["Z"]
        top = node("ShaderNodeMath", "part top (not 0)", (0, top), operation='MAXIMUM')
        top.inputs[1].default_value = 1e-4
        own = node("ShaderNodeMath", "height over the top", (0, z), (1, top.outputs[0]), operation='DIVIDE')
        size = node("ShaderNodeVectorMath", "part size", (0, attr(PART_BOUNDS_MAX)), (1, attr(PART_BOUNDS_MIN)),
                    operation='DISTANCE')
        marked = node("ShaderNodeMath", "has part bounds", (0, size.outputs["Value"]), operation='GREATER_THAN')
        marked.inputs[1].default_value = 1e-9
        generated = node("ShaderNodeSeparateXYZ", "generated", (0, tc.outputs["Generated"])).outputs["Z"]
        return node("ShaderNodeMix", "plant height", (0, marked.outputs[0]), (2, generated), (3, own.outputs[0]),
                    data_type='FLOAT').outputs[0]
    return tr.shared("plant height", make)


def panel(root):
    """Put the inputs darken made in a Foliage panel, after the textures (closed, like the parameter panels)."""
    iface = root.interface
    mine = sorted((it for it in iface.items_tree if it.item_type == 'SOCKET' and it.in_out == 'INPUT'
                   and it.name in INPUTS), key=lambda it: INPUTS.index(it.name))
    if not mine:
        return
    p = iface.new_panel(PANEL, default_closed=True)
    for i, it in enumerate(mine):
        iface.move_to_parent(it, p, i)
    textures = next((it for it in iface.items_tree if it.item_type == 'PANEL' and it.name == "Textures"
                     and it.parent == p.parent), None)
    iface.move(p, textures.position + 1 if textures is not None else 0)


def reset(node):
    """A copied build's (build_like) group node: the defaults, not what was set on the material it copies."""
    for name in INPUTS:
        sock = node.inputs.get(name)
        if sock is not None:
            sock.default_value = DEFAULTS[name]
