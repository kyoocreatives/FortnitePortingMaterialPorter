"""Merging duplicate nodes and groups after a material is built."""

import math as _math

from .core import NORMAL_PASS, SECTION_KEY, _MADE, _blender_vmath, _group_deps

# ------------------------------------------------------------------ duplicates
# Translation makes a node per expression pin and per call output: a texture read
# through R, G and A became three image nodes, a function called once and read
# through five outputs five group nodes. merge_duplicates() folds a finished tree.
_MERGE_SKIP = {"NodeFrame", "NodeReroute", "NodeGroupInput", "NodeGroupOutput", "ShaderNodeOutputMaterial",
               "ShaderNodeOutputAOV", "NodeClosureInput", "NodeClosureOutput"}


_MERGE_IGNORED = {"TexMapping", "ColorMapping", "ImageUser"}     # left at their defaults by the build


_MERGE_PROPS = {}


def _merge_props(n):
    """What besides its inputs decides a node's result: [(property, kind)], None when
    something can't be compared (a colour ramp's points, a curve)."""
    k = n.bl_idname
    if k not in _MERGE_PROPS:
        import bpy
        base = {p.identifier for t in (bpy.types.Node, bpy.types.ShaderNode) for p in t.bl_rna.properties}
        props = []
        for p in n.bl_rna.properties:
            ident = p.identifier
            if ident in base or ident.startswith("active_"):
                continue
            if p.type == 'POINTER':
                if ident in ("image", "node_tree", "object"):
                    props.append((ident, "id"))
                elif p.fixed_type.identifier not in _MERGE_IGNORED:
                    props = None
                    break
            else:
                props.append((ident, "items" if p.type == 'COLLECTION' else "value"))
        _MERGE_PROPS[k] = props
    return _MERGE_PROPS[k]


def _plain(v):
    if isinstance(v, float):
        return round(v, 6)
    if v is None or isinstance(v, (int, str, bool)):
        return v
    try:
        return tuple(_plain(x) for x in v)
    except TypeError:
        return repr(v)


def merge_duplicates(tree, memo=None):
    """Fold nodes that compute the same thing into one: same type, settings and inputs (the
    same upstream sockets or values). Group nodes of one group also merge when every input
    both read agrees (a call read through several outputs is one group node), unless that
    would close a loop (UE compiles each output of a call on its own: one output's inputs may
    need another's result). Also: a vector split and recombined in order (UE's swizzles, free
    in HLSL, are nodes here) is the vector again; maths that hands a value on unchanged
    (x * 1, x + 0) goes; so does what reaches no output. Returns how many nodes went."""
    memo = {} if memo is None else memo
    _MADE.get(tree.as_pointer(), {}).clear()
    nodes = {n.as_pointer(): n for n in tree.nodes}
    ins, outs, indeg = {}, {}, dict.fromkeys(nodes, 0)
    for l in tree.links:
        a, b = l.from_node.as_pointer(), l.to_node.as_pointer()
        ins.setdefault(b, {})[l.to_socket.identifier] = (a, l.from_socket.identifier)
        outs.setdefault(a, []).append((l.from_socket.identifier, b, l.to_socket.identifier))
        indeg[b] += 1
    order, todo = [], [p for p, d in indeg.items() if d == 0]
    while todo:
        p = todo.pop()
        order.append(p)
        for _f, b, _t in outs.get(p, ()):
            indeg[b] -= 1
            if indeg[b] == 0:
                todo.append(b)
    canon = {}

    def root(p):
        while p in canon:
            p = canon[p]
        return p

    def socket(node, ident, output):
        return next(s for s in (node.outputs if output else node.inputs) if s.identifier == ident)

    def value(p, s):
        got = ins.get(p, {}).get(s.identifier)
        if got is not None:
            return ("link", root(got[0]), got[1])
        return _plain(getattr(s, "default_value", None))

    def signature(p, n, props):
        vals = []
        for ident, kind in props:
            v = getattr(n, ident)
            if kind == "id":
                v = v.name if v is not None else None
            elif kind == "items":
                v = tuple((it.name, getattr(it, "socket_type", getattr(it, "data_type", ""))) for it in v)
            vals.append(_plain(v))
        sig = [n.bl_idname, tuple(vals), tuple((s.identifier, value(p, s)) for s in n.inputs if s.enabled),
               tuple(sorted((k, _plain(n[k])) for k in n.keys() if k != SECTION_KEY))]
        if n.bl_idname in ("ShaderNodeValue", "ShaderNodeRGB"):
            sig.append(_plain(n.outputs[0].default_value))
        return tuple(sig)

    def upstream(target, starts):
        """Whether `target` feeds any of `starts`."""
        seen, todo = set(), list(starts)
        while todo:
            p = root(todo.pop())
            if p == target:
                return True
            if p in seen:
                continue
            seen.add(p)
            todo += [a for a, _i in ins.get(p, {}).values()]
        return False

    def needs(p, n):
        deps = _group_deps(n.node_tree, memo)
        every = {s.identifier for s in n.inputs}
        got = set()
        for f, _b, _t in outs.get(p, ()):
            got |= deps.get(f, every)
        return got

    def section(a, b):
        pa, pb = str(a.get(SECTION_KEY, "")).split("/"), str(b.get(SECTION_KEY, "")).split("/")
        # what the Normal's own pass shares with the rest stays with the rest
        if (pa[0] == NORMAL_PASS) != (pb[0] == NORMAL_PASS):
            return "/".join(pb if pa[0] == NORMAL_PASS else pa)
        common = []
        for x, y in zip(pa, pb):
            if x != y:
                break
            common.append(x)
        return "/".join(common)

    def fold(dup, keep):
        d, k = nodes[dup], nodes[keep]
        for f, b, t in outs.pop(dup, []):
            if b not in nodes:
                continue
            tree.links.new(socket(k, f, True), socket(nodes[b], t, False))
            outs.setdefault(keep, []).append((f, b, t))
            ins.setdefault(b, {})[t] = (keep, f)
        if SECTION_KEY in k:
            k[SECTION_KEY] = section(k, d)
        ins.pop(dup, None)
        del nodes[dup]
        tree.nodes.remove(d)
        canon[dup] = keep

    def try_group(p, n, kept):
        """Fold group node p into an earlier one of its group that agrees where both read."""
        need = needs(p, n)
        mine = {s.identifier: value(p, s) for s in n.inputs}
        my_srcs = [v[1] for i, v in mine.items() if i in need and isinstance(v, tuple) and v[:1] == ("link",)]
        for q in kept:
            if q not in nodes:
                continue
            m = nodes[q]
            need_q = needs(q, m)
            theirs = {s.identifier: value(q, s) for s in m.inputs}
            if any(mine[i] != theirs.get(i) for i in need & need_q):
                continue
            q_srcs = [v[1] for i, v in theirs.items() if i in need_q and isinstance(v, tuple) and v[:1] == ("link",)]
            if upstream(q, my_srcs) or upstream(p, q_srcs):
                continue
            for i in sorted(need - need_q):
                s = socket(m, i, False)
                v = mine[i]
                if isinstance(v, tuple) and v[:1] == ("link",):
                    tree.links.new(socket(nodes[v[1]], v[2], True), s)
                    ins.setdefault(q, {})[i] = (v[1], v[2])
                    outs.setdefault(v[1], []).append((v[2], q, i))
                else:
                    if i in ins.get(q, {}):
                        for l in s.links:
                            tree.links.remove(l)
                        del ins[q][i]
                    if hasattr(s, "default_value") and v is not None:
                        s.default_value = v
            fold(p, q)
            return q
        return None

    def round_trip(p, n):
        """A Combine XYZ of one Separate XYZ's X, Y and Z in order is its vector: its readers
        take that vector (the source itself), and the pair goes where nothing else reads it."""
        got = ins.get(p, {})
        src = [got.get(i) for i in ("X", "Y", "Z")]
        if any(v is None for v in src):
            return False
        sep = root(src[0][0])
        if any(root(v[0]) != sep or v[1] != i for v, i in zip(src, ("X", "Y", "Z"))) \
                or sep not in nodes or nodes[sep].bl_idname != "ShaderNodeSeparateXYZ":
            return False
        feed = ins.get(sep, {}).get("Vector")
        if feed is None:
            return False
        a, ia = root(feed[0]), feed[1]
        if a not in nodes:
            return False
        out = socket(nodes[a], ia, True)
        readers = [(b, t) for _f, b, t in outs.get(p, ()) if b in nodes]
        # a colour carries alpha and turns to a float by luminance, where the vector averages:
        # it stands in only where it's read as a vector
        if out.type not in ('VECTOR', 'VALUE') and not (
                out.type == 'RGBA' and all(socket(nodes[b], t, False).type == 'VECTOR' for b, t in readers)):
            return False
        for b, t in readers:
            tree.links.new(out, socket(nodes[b], t, False))
            outs.setdefault(a, []).append((ia, b, t))
            ins.setdefault(b, {})[t] = (a, ia)
        outs.pop(p, None)
        ins.pop(p, None)
        tree.nodes.remove(nodes.pop(p))
        rest = [e for e in outs.get(sep, ()) if e[1] != p and e[1] in nodes]
        outs[sep] = rest
        if not rest:
            ins.pop(sep, None)
            tree.nodes.remove(nodes.pop(sep))
            return 2
        return 1

    # (operation: (the value that hands the other input on, the inputs that value may sit on))
    no_op = {"ShaderNodeMath": {"MULTIPLY": (1.0, (0, 1)), "ADD": (0.0, (0, 1)), "SUBTRACT": (0.0, (1,)),
                                "DIVIDE": (1.0, (1,)), "POWER": (1.0, (1,))},
             "ShaderNodeVectorMath": {"MULTIPLY": ((1.0, 1.0, 1.0), (0, 1)), "ADD": ((0.0, 0.0, 0.0), (0, 1)),
                                      "SUBTRACT": ((0.0, 0.0, 0.0), (1,)), "DIVIDE": ((1.0, 1.0, 1.0), (1,))}}

    def passthrough(p, n):
        """x * 1, x + 0, x - 0, x / 1, x ^ 1 (and a vector scaled by 1): its readers read x, the
        node goes - where x is of the node's own type (Blender converts a vector to a float by
        its average, a colour by its luminance: a reader of another type would read it else)."""
        rules = no_op.get(n.bl_idname)
        if rules is None or getattr(n, "use_clamp", False):
            return False
        if n.bl_idname == "ShaderNodeVectorMath" and n.operation == 'SCALE':
            keep = 0 if not n.inputs["Scale"].is_linked and _plain(n.inputs["Scale"].default_value) == 1.0 else None
        else:
            rule = rules.get(n.operation)
            keep = None
            for i in (rule[1] if rule else ()):
                if n.inputs[i].identifier not in ins.get(p, {}) and _plain(n.inputs[i].default_value) == rule[0]:
                    keep = 1 - i
                    break
        if keep is None:
            return False
        feed = ins.get(p, {}).get(n.inputs[keep].identifier)
        out = n.outputs[0]
        if feed is None or any(f != out.identifier for f, _b, _t in outs.get(p, ())):
            return False
        a, ia = root(feed[0]), feed[1]
        if a not in nodes or socket(nodes[a], ia, True).type != out.type:
            return False
        src = socket(nodes[a], ia, True)
        for _f, b, t in outs.pop(p, []):
            if b in nodes:
                tree.links.new(src, socket(nodes[b], t, False))
                outs.setdefault(a, []).append((ia, b, t))
                ins.setdefault(b, {})[t] = (a, ia)
        ins.pop(p, None)
        tree.nodes.remove(nodes.pop(p))
        return True

    def const_in(p, s):
        """What an input holds when nothing feeds it (plain numbers), else None."""
        if s.identifier in ins.get(p, {}) or not hasattr(s, "default_value"):
            return None
        return _plain(s.default_value)

    def hand_on(p, feed, out):
        """p's readers read what feeds it (feed: (node, output)) instead; p goes - where that is
        of p's output's own type (Blender converts between types its own way)."""
        if any(f != out.identifier for f, b, _t in outs.get(p, ()) if b in nodes):
            return False
        a, ia = root(feed[0]), feed[1]
        if a not in nodes:
            return False
        src = socket(nodes[a], ia, True)
        if src.type != out.type:
            return False
        for _f, b, t in outs.pop(p, []):
            if b in nodes:
                tree.links.new(src, socket(nodes[b], t, False))
                outs.setdefault(a, []).append((ia, b, t))
                ins.setdefault(b, {})[t] = (a, ia)
        ins.pop(p, None)
        tree.nodes.remove(nodes.pop(p))
        return True

    def constant(p, values):
        """p's readers hold constants ({output identifier: float or 3-tuple}) where they read
        it; p goes. Not where a reader can't hold one."""
        plan = []
        for f, b, t in outs.get(p, ()):
            if b not in nodes:
                continue
            if f not in values:
                return False
            s = socket(nodes[b], t, False)
            v = _socket_value(values[f], s)
            if v is None:
                return False
            plan.append((b, t, s, v))
        for b, t, s, v in plan:
            for l in list(s.links):
                tree.links.remove(l)
            s.default_value = v
            ins.get(b, {}).pop(t, None)
        outs.pop(p, None)
        ins.pop(p, None)
        tree.nodes.remove(nodes.pop(p))
        return True

    def settle(p, n):
        """A node whose result is known as the tree is built - every input a constant, a
        product with 0, a mix whose factor is 0 or 1 - is that result: its readers take it (a
        game-only effect left off, its parameter built in at 0, folds away with what fed it)."""
        if n.mute:
            return False
        kind = n.bl_idname
        if kind == "ShaderNodeMix":
            dt = n.data_type
            key = {"FLOAT": "_Float", "VECTOR": "_Vector", "RGBA": "_Color"}.get(dt)
            if key is None or dt == 'RGBA' and n.clamp_result:
                return False
            fac = socket(n, "Factor_Vector" if dt == 'VECTOR' and n.factor_mode == 'NON_UNIFORM' else "Factor_Float", False)
            f = const_in(p, fac)
            if isinstance(f, tuple):
                f = f[0] if len(set(f)) == 1 else None
            out = socket(n, "Result" + key, True)
            a_in, b_in = socket(n, "A" + key, False), socket(n, "B" + key, False)
            fa, fb = ins.get(p, {}).get(a_in.identifier), ins.get(p, {}).get(b_in.identifier)
            if fa is not None and fb is not None and (root(fa[0]), fa[1]) == (root(fb[0]), fb[1]) \
                    and (dt != 'RGBA' or n.blend_type == 'MIX'):
                return hand_on(p, fa, out)       # a mix of a value with itself
            if f in (0.0, 1.0) and not (f == 1.0 and dt == 'RGBA' and n.blend_type != 'MIX'):
                pick = socket(n, ("B" if f == 1.0 else "A") + key, False)
                feed = ins.get(p, {}).get(pick.identifier)
                if feed is not None:
                    return hand_on(p, feed, out)
                return dt != 'RGBA' and constant(p, {out.identifier: const_in(p, pick)})
            if f is None or dt == 'RGBA':
                return False
            a, b = const_in(p, socket(n, "A" + key, False)), const_in(p, socket(n, "B" + key, False))
            if a is None or b is None:
                return False
            if n.clamp_factor:
                f = min(max(f, 0.0), 1.0)
            if dt == 'FLOAT':
                return constant(p, {out.identifier: a + (b - a) * f})
            return constant(p, {out.identifier: tuple(x + (y - x) * f for x, y in zip(a, b))})
        if kind == "ShaderNodeMath":
            k = _MATH_ARITY.get(n.operation)
            if k is None:
                return False
            vals = [const_in(p, n.inputs[i]) for i in range(k)]
            if n.use_clamp and n.operation == 'MULTIPLY' and 1.0 in vals:
                # a saturate of a value a clamped node made: that value
                feed = ins.get(p, {}).get(n.inputs[vals.index(1.0) ^ 1].identifier)
                src = nodes.get(root(feed[0])) if feed is not None else None
                if src is not None and src.bl_idname == "ShaderNodeMath" and src.use_clamp:
                    return hand_on(p, feed, n.outputs[0])
            if n.operation == 'MULTIPLY' and 0.0 in vals:
                return constant(p, {n.outputs[0].identifier: 0.0})
            if any(v is None for v in vals):
                return False
            r = _blender_math(n.operation, *vals)
            if r is None:
                return False
            if n.use_clamp:
                r = min(max(r, 0.0), 1.0)
            return constant(p, {n.outputs[0].identifier: r})
        if kind == "ShaderNodeVectorMath":
            op = n.operation
            if op == 'SCALE':
                vals = [const_in(p, n.inputs[0]), const_in(p, n.inputs["Scale"])]
            else:
                k = _VMATH_ARITY.get(op)
                if k is None:
                    return False
                vals = [const_in(p, n.inputs[i]) for i in range(k)]
            if op in ('MULTIPLY', 'SCALE') and any(v == 0.0 or v == (0.0, 0.0, 0.0) for v in vals):
                return constant(p, {n.outputs["Vector"].identifier: (0.0, 0.0, 0.0)})
            if any(v is None for v in vals):
                return False
            r = _blender_vmath(op, *vals)
            if r is None:
                return False
            out = n.outputs["Value"] if isinstance(r, float) else n.outputs["Vector"]
            return constant(p, {out.identifier: r})
        if kind == "ShaderNodeClamp" and n.clamp_type == 'MINMAX':
            vals = [const_in(p, n.inputs[k]) for k in ("Value", "Min", "Max")]
            if any(v is None for v in vals):
                return False
            return constant(p, {n.outputs[0].identifier: min(max(vals[0], vals[1]), vals[2])})
        if kind == "ShaderNodeCombineXYZ":
            vals = [const_in(p, s) for s in n.inputs]
            if any(v is None for v in vals):
                return False
            return constant(p, {n.outputs[0].identifier: tuple(vals)})
        if kind == "ShaderNodeSeparateXYZ":
            v = const_in(p, n.inputs[0])
            if v is None:
                return False
            return constant(p, {s.identifier: v[i] for i, s in enumerate(n.outputs)})
        return False

    seen, sig_of, groups, gone = {}, {}, {}, 0
    for p in order:
        n = nodes.get(p)
        if n is None or n.bl_idname in _MERGE_SKIP:
            continue
        if settle(p, n):
            gone += 1
            continue
        if n.bl_idname == "ShaderNodeCombineXYZ":
            cut = round_trip(p, n)
            if cut:
                gone += cut
                continue
        if passthrough(p, n):
            gone += 1
            continue
        props = _merge_props(n)
        if props is None:
            continue
        sig = signature(p, n, props)
        twin = seen.get(sig)
        if twin is not None and twin in nodes:
            fold(p, twin)
            gone += 1
            continue
        if n.bl_idname == "ShaderNodeGroup" and n.node_tree is not None:
            kept = groups.setdefault(n.node_tree.name, [])
            q = try_group(p, n, kept)
            if q is not None:
                # its inputs changed: what it now matches
                if seen.get(sig_of.get(q)) == q:
                    del seen[sig_of[q]]
                sig_of[q] = signature(q, nodes[q], props)
                seen[sig_of[q]] = q
                gone += 1
                continue
            kept.append(p)
        seen[sig] = p
        sig_of[p] = sig
    # what reaches no output goes (a branch translation made that no attribute kept)
    outputs = ("NodeGroupOutput", "ShaderNodeOutputMaterial", "ShaderNodeOutputAOV", "ShaderNodeOutputWorld")
    live, todo = set(), [p for p, n in nodes.items() if n.bl_idname in outputs]
    if todo:
        while todo:
            p = todo.pop()
            if p not in live:
                live.add(p)
                todo += [root(a) for a, _i in ins.get(p, {}).values()]
        for p, n in list(nodes.items()):
            if p not in live and n.bl_idname not in ("NodeFrame", "NodeGroupInput"):
                tree.nodes.remove(n)
                del nodes[p]
                gone += 1
    return gone


_MATH_ARITY = {"ADD": 2, "SUBTRACT": 2, "MULTIPLY": 2, "DIVIDE": 2, "MAXIMUM": 2, "MINIMUM": 2, "POWER": 2,
               "LESS_THAN": 2, "GREATER_THAN": 2, "MODULO": 2, "MULTIPLY_ADD": 3, "COMPARE": 3,
               "ABSOLUTE": 1, "SIGN": 1, "FLOOR": 1, "CEIL": 1, "FRACT": 1, "SQRT": 1, "SINE": 1, "COSINE": 1,
               "ROUND": 1, "TRUNC": 1}


_VMATH_ARITY = {"ADD": 2, "SUBTRACT": 2, "MULTIPLY": 2, "DIVIDE": 2, "MINIMUM": 2, "MAXIMUM": 2,
                "DOT_PRODUCT": 2, "DISTANCE": 2, "CROSS_PRODUCT": 2, "ABSOLUTE": 1, "LENGTH": 1, "NORMALIZE": 1}


def _blender_math(op, a, b=None, c=None):
    if op == "ADD": return a + b
    if op == "SUBTRACT": return a - b
    if op == "MULTIPLY": return a * b
    if op == "DIVIDE": return a / b if b != 0.0 else 0.0
    if op == "MAXIMUM": return max(a, b)
    if op == "MINIMUM": return min(a, b)
    if op == "POWER":
        if a >= 0.0 or b == _math.floor(b):
            try:
                return float(a ** b) if (a != 0.0 or b >= 0.0) else 0.0
            except (OverflowError, ZeroDivisionError):
                return None
        return 0.0
    if op == "LESS_THAN": return 1.0 if a < b else 0.0
    if op == "GREATER_THAN": return 1.0 if a > b else 0.0
    if op == "MODULO": return _math.fmod(a, b) if b != 0.0 else 0.0
    if op == "MULTIPLY_ADD": return a * b + c
    if op == "COMPARE": return 1.0 if abs(a - b) <= max(c, 1e-5) else 0.0
    if op == "ABSOLUTE": return abs(a)
    if op == "SIGN": return float((a > 0) - (a < 0))
    if op == "FLOOR": return float(_math.floor(a))
    if op == "CEIL": return float(_math.ceil(a))
    if op == "FRACT": return a - _math.floor(a)
    if op == "SQRT": return _math.sqrt(a) if a > 0.0 else 0.0
    if op == "SINE": return _math.sin(a)
    if op == "COSINE": return _math.cos(a)
    if op == "ROUND": return float(_math.floor(a + 0.5))
    if op == "TRUNC": return float(_math.trunc(a))
    return None


def _socket_value(v, s):
    """A constant (float or tuple) as an input socket holds it - Blender reads a vector as a
    float by its average, a float as a vector in every component, a vector as an opaque
    colour - or None where the socket holds no such value."""
    # (a reroute's socket has a type but nowhere to keep a value: writing one crashes Blender)
    if not hasattr(s, "default_value") or s.type not in ('VALUE', 'VECTOR', 'RGBA') or s.node.bl_idname == "NodeReroute":
        return None
    vec = isinstance(v, tuple)
    if vec:
        v = tuple(v[:3])
    if s.type == 'VALUE':
        return sum(v) / len(v) if vec else float(v)
    if s.type == 'RGBA':
        return (v if vec else (float(v),) * 3) + (1.0,)
    k = len(s.default_value)
    if not vec:
        return (float(v),) * k
    return (v + (0.0,) * k)[:k]
