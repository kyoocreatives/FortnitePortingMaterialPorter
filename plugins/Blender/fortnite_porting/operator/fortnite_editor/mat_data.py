"""The Materials tab's data: the built materials in a scope, grouped (parent shader, feature, mesh), and the parameters
of the ticked ones merged by UE name into sections. A parameter is an input of the material's group node
(material_porter.params): reading and writing it touches that socket only, never a tree."""
import json
import re

import bpy

from ...material_porter import params

SCOPES = (("SELECTION", "Selection"), ("CHARACTER", "Active Character"), ("COLLECTION", "Active Collection"),
          ("FILE", "Whole File"))
GROUPS = (("SHADER", "Parent Shader"), ("FEATURE", "Feature"), ("MESH", "Mesh"))
SOCKETS = {"NodeSocketFloat", "NodeSocketFloatFactor", "NodeSocketColor", "NodeSocketVector", "NodeSocketInt",
           "NodeSocketBool"}
# sections by the panel's (the function's) or the input's name; the rest keep their panel's name
SECTIONS = (("Subsurface", re.compile(r"subsurface|sss|scatter|profile colour", re.I)),
            ("Outline", re.compile(r"outline|invhull|ink", re.I)),
            ("Cel", re.compile(r"anime|cel|toon|lut|twotone|shadow ?colou?r", re.I)),
            ("Emissive", re.compile(r"emissive|glow", re.I)))


# ------------------------------------------------------------------ which materials

def _character(obj):
    """The armature an object belongs to (itself, a parent, an Armature modifier's), else None."""
    while obj is not None:
        if obj.type == 'ARMATURE':
            return obj
        mod = next((m for m in getattr(obj, "modifiers", ()) if m.type == 'ARMATURE' and m.object is not None), None)
        if mod is not None:
            return mod.object
        obj = obj.parent
    return None


def scope_objects(context, scope):
    if scope == "SELECTION":
        objs = list(context.selected_objects)
        if context.active_object is not None and context.active_object not in objs:
            objs.append(context.active_object)
        return objs
    if scope == "CHARACTER":
        rig = _character(context.active_object)
        if rig is None:
            return []
        return [o for o in context.scene.objects if _character(o) == rig]
    if scope == "COLLECTION":
        return list(context.collection.all_objects) if context.collection is not None else []
    return None


def materials(context, scope):
    """[(material, [objects using it in the scope])], built materials only, in name order."""
    found = {}
    objs = scope_objects(context, scope)
    if objs is None:
        for m in bpy.data.materials:
            if params.root_node(m) is not None:
                found[m.name] = (m, [])
        for o in context.scene.objects:
            for s in getattr(o, "material_slots", ()):
                if s.material is not None and s.material.name in found:
                    found[s.material.name][1].append(o)
    else:
        for o in objs:
            for s in getattr(o, "material_slots", ()):
                m = s.material
                if m is None:
                    continue
                if m.name not in found:
                    if params.root_node(m) is None:
                        continue
                    found[m.name] = (m, [])
                if o not in found[m.name][1]:
                    found[m.name][1].append(o)
    return [found[k] for k in sorted(found)]


def short(name):
    return name[3:] if name.startswith("MP ") else name


def groups(mats, by):
    """[(group title, [material])]: materials can sit in several feature groups."""
    out = {}
    for m, objs in mats:
        if by == "SHADER":
            keys = [m.get(params.KEY_MASTER) or "Unknown shader"]
        elif by == "FEATURE":
            keys = params.features(m) or ["Other"]
        else:
            keys = [o.name for o in objs] or ["No mesh"]
        for k in keys:
            out.setdefault(k, []).append(m)
    order = list(params.FEATURES) + ["Other"] if by == "FEATURE" else None
    return sorted(out.items(), key=lambda kv: (order.index(kv[0]) if order else 0, kv[0].lower()))


# ------------------------------------------------------------------ their parameters

class Param:
    """One parameter over the ticked materials: its sockets (one per material that has it)."""
    __slots__ = ("key", "name", "kind", "section", "sockets", "game", "master")

    def __init__(self, key, name, kind, section):
        self.key, self.name, self.kind, self.section = key, name, kind, section
        self.sockets = []       # [(material, socket)]
        self.game = []          # per socket: the build's value (None: unknown)
        self.master = None      # the parent shader's default

    def values(self):
        return [value(s) for _m, s in self.sockets]

    @property
    def differs(self):
        vals = self.values()
        return any(not same(v, vals[0]) for v in vals[1:])


def value(sock):
    v = sock.default_value
    try:
        return tuple(float(x) for x in v)
    except TypeError:
        return float(v) if not isinstance(v, bool) else v


def same(a, b, eps=1e-5):
    if isinstance(a, tuple) and isinstance(b, tuple):
        return len(a) == len(b) and all(abs(x - y) <= eps for x, y in zip(a, b))
    if isinstance(a, tuple) or isinstance(b, tuple):
        return False
    return abs(float(a) - float(b)) <= eps


def kind_of(sock):
    t = sock.bl_idname
    if t == "NodeSocketColor":
        return "COLOR"
    if t == "NodeSocketVector":
        return "VECTOR"
    if t == "NodeSocketBool":
        return "BOOL"
    return "INT" if t == "NodeSocketInt" else "FLOAT"


def _panels(tree):
    """{input identifier: its panel's name} of a group's interface."""
    return {it.identifier: getattr(it.parent, "name", "") for it in tree.interface.items_tree
            if it.item_type == 'SOCKET' and it.in_out == 'INPUT'}


def section_of(panel, name):
    for title, rx in SECTIONS:
        if rx.search(name) or rx.search(panel):
            return title
    return panel or "Material"


def _json(owner, key):
    try:
        return json.loads(owner.get(key) or "{}")
    except ValueError:
        return {}


def gather(mats):
    """[section title, [Param]] of the materials, in first-seen order within a section."""
    found = {}
    for m in mats:
        node = params.root_node(m)
        if node is None:
            continue
        ui, game, panels = _json(node.node_tree, params.KEY_UI), _json(m, params.KEY_GAME), _panels(node.node_tree)
        names = params.input_names(node)
        for sock in node.inputs:
            if sock.bl_idname not in SOCKETS or sock.is_linked or not sock.enabled:
                continue
            ue = ui.get(sock.identifier)
            name = names[sock.identifier]
            kind = kind_of(sock)
            key = (name, kind)
            p = found.get(key)
            if p is None:
                p = found[key] = Param(key, name, kind, section_of(panels.get(sock.identifier, ""), name))
                p.master = ue[2] if ue else None
            p.sockets.append((m, sock))
            g = game.get(sock.identifier)
            p.game.append(tuple(g) if isinstance(g, list) else g)
    out = {}
    for p in found.values():
        out.setdefault(p.section, []).append(p)
    fixed = [s for s, _rx in SECTIONS]
    return sorted(out.items(), key=lambda kv: (kv[0] not in fixed, fixed.index(kv[0]) if kv[0] in fixed else 0,
                                                kv[0] != "Material", kv[0].lower()))
