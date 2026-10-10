"""Node-building helpers for the FPv4 Baked Clouds builder.

Copy of fpv4_sprite/tools/nodelib.py (same names and signatures) without the sprite's tuned defaults.
Keep the two in step.
"""
import bpy
from types import SimpleNamespace

# node property sec() tags a section into; layout reads and removes it
SECTION_KEY = "fpv4_section"

# Tuned defaults overriding sock() values, as in fpv4_sprite. The clouds have none.
TUNED = {}


def sock(tree, name, kind, panel=None, default=None, lo=None, hi=None,
         desc="", toggle=False):
    if name in TUNED:
        default = TUNED[name]
    # no Factor socket type in the interface enum: use Float with subtype FACTOR
    factor = kind == 'NodeSocketFloatFactor'
    if factor:
        kind = 'NodeSocketFloat'
    s = tree.interface.new_socket(name, in_out='INPUT', socket_type=kind,
                                  parent=panel)
    if factor:
        try:
            s.subtype = 'FACTOR'
        except TypeError:
            pass
    if default is not None:
        s.default_value = default
    if lo is not None:
        s.min_value = lo
    if hi is not None:
        s.max_value = hi
    s.description = desc
    if toggle:
        s.is_panel_toggle = True
    return s


def evaluator(tree, x, y, label=""):
    """Evaluate Closure taking a Vector, returning Color + Alpha.

    Same signature as FPv4's texture closures (processing/material/utils.py setup_closure).
    """
    ev = tree.nodes.new("NodeEvaluateClosure")
    ev.input_items.new('VECTOR', 'Vector')
    ev.output_items.new('RGBA', 'Color')
    ev.output_items.new('FLOAT', 'Alpha')
    ev.location = (x, y)
    ev.label = label
    return ev


# Items FPv4 Material Attributes packs into its bundle, in order.
# Combine Bundle is order-sensitive: a different order is a different type and won't connect.
BUNDLE_ITEMS = [
    ("FX Mask", 'RGBA'),
    ("FX Mask Alpha", 'FLOAT'),
    ("M", 'RGBA'),
    ("Base Color", 'RGBA'),
    ("Metallic", 'FLOAT'),
    ("Roughness", 'FLOAT'),
    ("Alpha", 'FLOAT'),
    ("Normal Map", 'RGBA'),
    ("Normal Strength", 'FLOAT'),
    ("Subsurface Weight", 'FLOAT'),
    ("Specular", 'FLOAT'),
    ("Specular Tint", 'RGBA'),
    ("Anisotropic", 'FLOAT'),
    ("Anisotropic Rotation", 'FLOAT'),
    ("Anisotropic Tangent", 'VECTOR'),
    ("Clearcoat Weight", 'FLOAT'),
    ("Clearcoat Roughness", 'FLOAT'),
    ("Clearcoat Normal Map", 'RGBA'),
    ("Emission", 'RGBA'),
]


def bundle_io(tree, gi, go, x_in, x_out):
    """Separate/Combine pair carrying FPv4's MaterialAttributes through a module.

    Returns (separate_node, combine_node). Every item is pre-linked straight across.
    """
    IF = tree.interface
    IF.new_socket("MaterialAttributes", in_out='OUTPUT', socket_type='NodeSocketBundle')
    IF.new_socket("MaterialAttributes", in_out='INPUT', socket_type='NodeSocketBundle')

    sep = tree.nodes.new("NodeSeparateBundle")
    sep.location = (x_in, 0)
    sep.label = "incoming attributes"
    com = tree.nodes.new("NodeCombineBundle")
    com.location = (x_out, 0)
    com.label = "outgoing attributes"
    for name, kind in BUNDLE_ITEMS:
        sep.bundle_items.new(kind, name)
        com.bundle_items.new(kind, name)
    tree.links.new(gi.outputs["MaterialAttributes"], sep.inputs["Bundle"])
    tree.links.new(com.outputs["Bundle"], go.inputs["MaterialAttributes"])
    for name, _ in BUNDLE_ITEMS:
        tree.links.new(sep.outputs[name], com.inputs[name])
    return sep, com


def helpers(tree, gi):
    """Node wrappers used by build_sprite_shader.py."""
    N, L, IF = tree.nodes, tree.links, tree.interface

    def node(kind, x, y, label="", **kw):
        n = N.new(kind)
        n.location = (x, y)
        n.label = label
        for k, v in kw.items():
            setattr(n, k, v)
        return n

    def mres(n):
        return next(s for s in n.outputs if s.enabled)

    def menu(name, items, data_type, panel, x, y, label="", desc="", default=None):
        """Menu Switch whose Menu input is exposed as a group enum socket.

        Create the NodeSocketMenu first: linking from the Group Input's virtual socket
        doesn't create one (it rebinds an existing socket instead).
        """
        ms = node("GeometryNodeMenuSwitch", x, y, label or name, data_type=data_type)
        ms.enum_definition.enum_items.clear()
        for it in items:
            ms.enum_definition.enum_items.new(it)
        s = IF.new_socket(name, in_out='INPUT', socket_type='NodeSocketMenu', parent=panel)
        s.description = desc
        L.new(gi.outputs[name], ms.inputs["Menu"])
        if default is not None:
            try:
                s.default_value = default
            except Exception:
                pass
        return ms

    def math(op, x, y, a=None, b=None, clamp=False, label=""):
        n = node("ShaderNodeMath", x, y, label or op, operation=op, use_clamp=clamp)
        for i, v in ((0, a), (1, b)):
            if v is None:
                continue
            if hasattr(v, "node"):
                L.new(v, n.inputs[i])
            else:
                n.inputs[i].default_value = v
        return n.outputs[0]

    def vmath(op, x, y, a=None, b=None, label=""):
        n = node("ShaderNodeVectorMath", x, y, label or op, operation=op)
        for i, v in ((0, a), (1, b)):
            if v is None:
                continue
            if hasattr(v, "node"):
                L.new(v, n.inputs[i])
            else:
                n.inputs[i].default_value = v
        return n

    def mix_f(x, y, fac, a, b, label="mix"):
        n = node("ShaderNodeMix", x, y, label, data_type='FLOAT')
        for s, v in ((n.inputs[0], fac), (n.inputs[2], a), (n.inputs[3], b)):
            L.new(v, s) if hasattr(v, "node") else setattr(s, "default_value", v)
        return mres(n)

    def mix_c(x, y, fac, a, b, label="mix"):
        n = node("ShaderNodeMix", x, y, label, data_type='RGBA')
        for s, v in ((n.inputs[0], fac), (n.inputs[6], a), (n.inputs[7], b)):
            L.new(v, s) if hasattr(v, "node") else setattr(s, "default_value", v)
        return mres(n)

    def mix_v(x, y, fac, a, b, label="mix"):
        n = node("ShaderNodeMix", x, y, label, data_type='VECTOR')
        for s_, v_ in ((n.inputs[0], fac), (n.inputs[4], a), (n.inputs[5], b)):
            L.new(v_, s_) if hasattr(v_, "node") else setattr(s_, "default_value", v_)
        return mres(n)

    def mul_c(x, y, a, b, label="multiply"):
        n = node("ShaderNodeMix", x, y, label, data_type='RGBA', blend_type='MULTIPLY')
        n.inputs[0].default_value = 1.0
        L.new(a, n.inputs[6]) if hasattr(a, "node") else setattr(n.inputs[6], "default_value", a)
        L.new(b, n.inputs[7]) if hasattr(b, "node") else setattr(n.inputs[7], "default_value", b)
        return mres(n)

    def add_c(x, y, a, b, label="add"):
        n = node("ShaderNodeMix", x, y, label, data_type='RGBA', blend_type='ADD')
        n.inputs[0].default_value = 1.0
        L.new(a, n.inputs[6]) if hasattr(a, "node") else setattr(n.inputs[6], "default_value", a)
        L.new(b, n.inputs[7]) if hasattr(b, "node") else setattr(n.inputs[7], "default_value", b)
        return mres(n)

    def as_color(x, y, v, label=""):
        n = node("ShaderNodeCombineColor", x, y, label)
        for s in ("Red", "Green", "Blue"):
            L.new(v, n.inputs[s])
        return n.outputs["Color"]

    def scene_time(x, y):
        """Seconds, driven off the scene frame rate."""
        tval = node("ShaderNodeValue", x, y, "Time (seconds)")
        fc = tval.outputs[0].driver_add("default_value")
        fc.driver.type = 'SCRIPTED'
        fc.driver.expression = "frame / fps"
        v = fc.driver.variables.new()
        v.name = "fps"
        v.type = 'SINGLE_PROP'
        v.targets[0].id_type = 'SCENE'
        v.targets[0].id = bpy.context.scene
        v.targets[0].data_path = "render.fps"
        from .material_time import follow
        follow(fc)
        return tval.outputs[0]

    current = [""]

    def sec(*path):
        """Start a layout section: layout frames every node made until the next sec() under `path`
        ("Reaper", "Matcap UV" is a Matcap UV frame inside a Reaper frame).
        Tagging sweeps the whole tree, so nodes made via N.new or evaluator() are caught and a path can be
        re-entered. Nodes made before the first call stay unframed.
        sec() with no path closes the section: nodes added afterwards are placed next to what they read.
        """
        if current[0] is not None:
            for n in N:
                if SECTION_KEY not in n:
                    n[SECTION_KEY] = current[0]
        current[0] = "/".join(path) if path else None

    return SimpleNamespace(N=N, L=L, IF=IF, node=node, mres=mres, menu=menu,
                           math=math, vmath=vmath, mix_f=mix_f, mix_c=mix_c,
                           mix_v=mix_v, mul_c=mul_c, add_c=add_c,
                           as_color=as_color, scene_time=scene_time, sec=sec)
