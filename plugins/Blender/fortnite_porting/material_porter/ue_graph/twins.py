"""Function groups an earlier material built, found before translating them again (build.build_one).

A function's group follows from its graph, static inputs and build settings (the key) and from what the material's
env answers while the body is translated. A new group records those answers (Recording); a later material whose env
gives the same answers uses that group (find) instead of building a twin for share_groups to throw away."""

import copy
import hashlib

import bpy

from .core import MADE, SECTION_KEY, SpecialInputs, Val, WIDTH_OF_SOCKET, load_graph

# env methods a body may ask that can be asked again of another material's env: plain arguments, and side effects
# that material's own build would have (parameter bookkeeping, images, notes)
METHODS = {
    "function", "special_function", "function_key", "scalar", "vector", "group_input", "static_switch", "static_mask",
    "texture_parameter", "texture", "texture_address", "texture_nearest", "normal_mode", "virtual_texture_available",
    "world_position", "camera_vector", "camera_position", "actor_position", "uv", "local_position",
    "preskinned_position", "light_direction", "light_color", "collection",
}
PLUMBING = {"current", "set_current", "h", "tr", "caller"}
_MISSING = "<missing>"
_SPECIAL = "<special inputs>"
_CACHE = {}         # key -> finished groups (FunctionTree copies), newest last
_PER_KEY = 4


class Raised:
    """An env question that ended in an error."""

    def __init__(self, e):
        self.kind = type(e).__name__


class Unknown(Exception):
    """An answer that can't be compared: the group is not reused."""


def clear():
    _CACHE.clear()


def real(env):
    return env._env if isinstance(env, Recording) else env


class Facts(list):
    """What a function body asked its env, in order, with group uses and graph reads in between."""

    def __init__(self):
        super().__init__()
        self.ok = True
        self.graphs = set()


class Recording:
    """The env as a function body sees it: answers as the env does and logs what was asked. Anything it can't
    log (an unlisted method, a write, the env's own dict) makes the group one-off."""

    def __init__(self, env, facts):
        object.__setattr__(self, "_env", env)
        object.__setattr__(self, "_facts", facts)

    def __getattr__(self, name):
        env, facts = self._env, self._facts
        v = getattr(env, name, _MISSING)
        if name in PLUMBING or name.startswith("__"):
            if v is _MISSING:
                raise AttributeError(name)
            return v
        if v is _MISSING:
            facts.append(("attr", name, _MISSING))
            raise AttributeError(name)
        if getattr(v, "__self__", None) is env:
            if name not in METHODS:
                facts.ok = False
                return v

            def ask(*a, **k):
                section = tuple(env.tr.section or ())
                try:
                    r = v(*a, **k)
                except Exception as e:      # (a caller may take the error as an answer)
                    facts.append(("call", name, a, k, section, Raised(e)))
                    raise
                facts.append(("call", name, a, k, section, r))
                return r
            return ask
        facts.append(("attr", name, v))
        return v

    def __setattr__(self, name, value):
        if name not in PLUMBING:
            self._facts.ok = False
        setattr(self._env, name, value)

    @property
    def __dict__(self):
        self._facts.ok = False
        return self._env.__dict__


class Session:
    """One material's build: the file's fingerprint -> group table (build._known) and a scratch tree to ask in."""

    def __init__(self, known, fp_key):
        self.known, self.fp_key = known, fp_key
        self.scratch = None


def _key(tr, key):
    env = real(tr.env)
    return (key, type(env), getattr(env, "function_prefix", None), tuple(tr.carried))


# ------------------------------------------------------------------ recording (the first build)
def record(tr, ft, key):
    """Start logging a new function group's body."""
    env = real(tr.env)
    if tr.twins is None or tr.rvt_reads or getattr(env, "probe", None) is not None \
            or not getattr(env, "whole_functions", False) or getattr(env, "replace_groups", True):
        return
    ft.key = _key(tr, key)
    ft.facts = Facts()
    ft.tr.env = Recording(env, ft.facts)


def used(tr, key, ft):
    """A body called a function: the group it reuses is part of what it built."""
    fn = tr.function
    if fn is not None and fn.facts is not None:
        fn.facts.append(("nested", _key(tr, key), ft))


def graph_read(tr, path):
    fn = tr.function
    if fn is not None and fn.facts is not None and path not in fn.facts.graphs:
        fn.facts.graphs.add(path)
        fn.facts.append(("graph", path))


def function_path(tr, path, objpath):
    real(tr.env).__dict__.setdefault("function_paths", {})[path] = objpath
    fn = tr.function
    if fn is not None and fn.facts is not None:
        fn.facts.append(("path", path, objpath))


def fail(ft):
    if ft.facts is not None:
        ft.facts.ok = False


# ------------------------------------------------------------------ comparing answers
class _GI:
    """env_input's socket where answers are checked (no group there)."""
    __slots__ = ("kind",)

    def __init__(self, kind):
        self.kind = kind


class _Stub:
    """The function a checked answer is built for: parameters become markers, not sockets."""
    textures = {}

    def env_input(self, key, socket_type, default=None, description=""):
        return Val(_GI(("env", key)), WIDTH_OF_SOCKET.get(socket_type, 3))

    def texture_input(self, *a):
        raise Unknown("texture input")


class _Untouchable:
    def __getattr__(self, name):
        raise Unknown("special function inputs")

    def __len__(self):
        raise Unknown("special function inputs")

    def __iter__(self):
        raise Unknown("special function inputs")

    def __getitem__(self, i):
        raise Unknown("special function inputs")


_SKIP = {"location", "width", "height", "dimensions", "select", "name", "label", "parent", "color",
         "use_custom_color", "show_options", "show_preview", "hide", "mute", "bl_idname", "location_absolute"}


class Hasher:
    """What a socket's value is in a tree: its node's kind, settings and inputs, upstream to the group's inputs."""

    def __init__(self, tree, kinds):
        self.tree, self.kinds = tree, kinds
        self.feeds, self.done = None, {}

    def socket(self, s):
        n = s.node
        if n.id_data.as_pointer() != self.tree.as_pointer():
            raise Unknown("socket of another tree")
        if n.bl_idname == "NodeGroupInput":
            return ("gi",) + tuple(self.kinds.get(s.name, ("?", s.name)))
        return (self.node(n), s.identifier)

    def node(self, n):
        p = n.as_pointer()
        if p in self.done:
            return self.done[p]
        if self.feeds is None:
            self.feeds = {}
            for l in self.tree.links:
                self.feeds.setdefault(l.to_node.as_pointer(), {})[l.to_socket.identifier] = l.from_socket
        parts = [n.bl_idname]
        for prop in n.bl_rna.properties:
            ident = prop.identifier
            if ident in _SKIP or prop.is_readonly and ident not in ("node_tree", "image"):
                continue
            v = getattr(n, ident, None)
            if ident in ("node_tree", "image"):
                v = v.name if v is not None else None
            elif not isinstance(v, (int, float, str, bool)):
                continue
            parts.append("%s=%r" % (ident, v))
        for k in n.keys():
            if k != SECTION_KEY:
                parts.append("%s:%r" % (k, n[k]))
        feeds = self.feeds.get(p, {})
        for sock in n.inputs:
            src = feeds.get(sock.identifier)
            if src is not None:
                parts.append("%s<%r" % (sock.identifier, self.socket(src)))
            elif hasattr(sock, "default_value"):
                v = sock.default_value
                parts.append("%s=%r" % (sock.identifier, tuple(v) if hasattr(v, "__len__") else v))
        if n.bl_idname in ("ShaderNodeValue", "ShaderNodeRGB"):
            v = n.outputs[0].default_value
            parts.append(repr(tuple(v) if hasattr(v, "__len__") else v))
        self.done[p] = hashlib.sha1("|".join(parts).encode()).hexdigest()
        return self.done[p]


def _summary(v, hasher):
    if isinstance(v, Raised):
        return ("raised", v.kind)
    if v is None or isinstance(v, (bool, int, float)):
        return (type(v).__name__, v)
    if isinstance(v, str):
        return ("str", str(v), getattr(v, "param", None))
    if isinstance(v, (tuple, list)):
        return ("seq",) + tuple(_summary(x, hasher) for x in v)
    if isinstance(v, Val):
        a = _summary(v.a, hasher) if v.a is not None else None
        if isinstance(v.s, _GI):
            return ("val", v.w, a, ("gi",) + v.s.kind)
        if v.const:
            return ("val", v.w, a, _summary(v.s, hasher))
        if hasher is None:
            raise Unknown("socket")
        return ("val", v.w, a, hasher.socket(v.s))
    if isinstance(v, bpy.types.Image):
        return ("img", v.name)
    raise Unknown(type(v).__name__)


def _plain_arg(a):
    if a is None or isinstance(a, (bool, int, float, str, bpy.types.Image)):
        return a
    if isinstance(a, tuple) and all(_plain_arg(x) is x for x in a):
        return a
    raise Unknown("argument %s" % type(a).__name__)


def _freeze(f, hasher):
    kind = f[0]
    if kind == "attr":
        return ("attr", f[1], _summary(f[2], None) if f[2] is not _MISSING else _MISSING)
    if kind != "call":
        return f
    _, name, a, k, section, r = f
    if name == "special_function":
        if r is not None:
            raise Unknown("special function")
        a = tuple(_SPECIAL if isinstance(x, SpecialInputs) else x for x in a)
    elif name == "group_input" and not str(a[0]).startswith(("P: ", "V: ")):
        raise Unknown("group input %s" % a[0])
    a = tuple(x if x is _SPECIAL else _plain_arg(x) for x in a)
    k = {n: _plain_arg(x) for n, x in k.items()}
    return ("call", name, a, k, section, _summary(r, hasher))


def _scratch(tr):
    s = tr.twins
    if s.scratch is None:
        s.scratch = bpy.data.node_groups.new(".MP twin check", "ShaderNodeTree")
    st = type(tr)(s.scratch, real(tr.env))
    st.function = _Stub()
    return st


def _ask(tr, st, f):
    """The answer the current material's env gives to a recorded question."""
    env = real(tr.env)
    if f[0] == "attr":
        v = getattr(env, f[1], _MISSING)
        return _MISSING if v is _MISSING else _summary(v, None)
    _, name, a, k, section, _r = f
    st.section = list(section)
    prev = st.activate()
    try:
        r = getattr(env, name)(*[_Untouchable() if x is _SPECIAL else x for x in a], **k)
    except Unknown:
        return None
    except Exception as e:
        return ("raised", type(e).__name__)
    finally:
        prev.activate()
    try:
        return _summary(r, Hasher(st.tree, {}))
    except Exception:       # (an answer that can't be compared: not the same group)
        return None


def _stamp(tree):
    return (len(tree.nodes), len(tree.links), tuple(it.identifier for it in tree.interface.items_tree))


def _alive(tr, c):
    try:
        tree = c.tree
        tree.name
    except ReferenceError:
        return False
    s = tr.twins
    return tree.get(s.fp_key) == c.fp and s.known().get(c.fp) == tree and _stamp(tree) == c.stamp


def _valid(tr, st, c, picked):
    if not _alive(tr, c):
        return False
    picked[c.key] = c
    for f in c.facts:
        kind = f[0]
        if kind == "nested":
            key, inner = f[1], f[2]
            have = tr.functions.get(key[0])
            if have is not None:
                if getattr(have, "base", None) is not inner:
                    return False
            elif key in picked:
                if picked[key] is not inner:
                    return False
            elif not _valid(tr, st, inner, picked):
                return False
        elif kind == "graph":
            if f[1] not in tr.graphs:
                tr.graphs[f[1]] = load_graph(f[1])
        elif kind == "path":
            real(tr.env).__dict__.setdefault("function_paths", {})[f[1]] = f[2]
        elif _ask(tr, st, f) != f[-1]:
            return False
    return True


class _Light:
    """A reused group's translator, as far as the build reads it after translation."""

    def __init__(self, graphs, warnings):
        self.graphs, self.warnings = graphs, warnings


def find(tr, key, label):
    """An earlier material's group for this call whose body this material's env answers the same, adopted into
    this material's functions (with the groups it calls); None where there is none."""
    if tr.twins is None or tr.rvt_reads:
        return None
    found = _CACHE.get(_key(tr, key))
    if not found:
        return None
    st = _scratch(tr)
    try:
        for c in reversed(found):
            picked = {}
            if _valid(tr, st, c, picked):
                # one reused group per tree: users_here counts a tree's users, the twins had their own
                trees = [ft.tree.as_pointer() for ft in tr.functions.values() if ft.reused]
                trees += [p.tree.as_pointer() for p in picked.values()]
                if len(trees) == len(set(trees)):
                    return _adopt(tr, picked, label)
        return None
    finally:
        env = real(tr.env)
        ptr = st.tree.as_pointer()
        once = getattr(env, "_once", None)
        if once:
            for k in [k for k in once if k[0] == ptr]:
                del once[k]
        MADE.pop(ptr, None)
        st.tree.nodes.clear()


def _adopt(tr, picked, label):
    """Put the picked groups in the material's functions, each with an empty group of the name its own build
    would have had (holding the name until share_groups drops it, as the twin did)."""
    env = real(tr.env)
    prefix = getattr(env, "function_prefix", "")
    top = None
    for c in picked.values():
        own = label if top is None else tr.variant_label(c.fname, c.fg, c.statics)
        ft = copy.copy(c)
        ft.base, ft.reused = c, True
        ft.placeholder = bpy.data.node_groups.new(prefix + own, "ShaderNodeTree")
        ft.tr = _Light(tr.graphs, c.warnings)
        ft.site = None
        tr.functions[c.key[0]] = ft
        top = top or ft
    return top


def users_here(tr):
    """{tree pointer: group nodes using it} in the material's own tree and its functions' groups."""
    count = {}
    for t in [tr.tree] + [ft.tree for ft in tr.functions.values()]:
        for n in t.nodes:
            if n.bl_idname == "ShaderNodeGroup" and n.node_tree is not None:
                p = n.node_tree.as_pointer()
                count[p] = count.get(p, 0) + 1
    return count


# ------------------------------------------------------------------ keeping (after the first build)
def prepare(tr):
    """Before merging: the recorded groups' answers as comparable values, and every output's inputs (a reuse
    links from these, as the twin's build would have, before merging folded anything)."""
    out = []
    for ft in tr.functions.values():
        if ft.reused or ft.facts is None or not ft.facts.ok:
            continue
        try:
            hasher = Hasher(ft.tree, ft.kind)
            facts = [_freeze(f, hasher) for f in ft.facts]
            for it in ft.tree.interface.items_tree:
                if it.item_type == 'SOCKET' and it.in_out == 'OUTPUT':
                    ft.reached(it.name)
        except Unknown:
            continue
        out.append([ft, facts, ft.tree.name, len(ft.tree.interface.items_tree)])
    return out


def inputs_kept(prepared):
    """Those drop_unread_inputs left whole (a twin's callers link sockets the dropped one lacks)."""
    return [p for p in prepared if len(p[0].tree.interface.items_tree) == p[3]]


def held(tr):
    """The empty name holders of reused groups (and the scratch tree): share_groups removes them."""
    out = [ft.placeholder for ft in tr.functions.values() if ft.reused and ft.placeholder is not None]
    if tr.twins is not None and tr.twins.scratch is not None:
        out.append(tr.twins.scratch)
        tr.twins.scratch = None
    return out


def register(tr, prepared, gone):
    """After sharing: keep each recorded group (or the twin it went to) for later materials."""
    s = tr.twins
    entries = {}
    for ft, facts, name, _n in prepared:
        final = gone.get(name) or ft.tree
        fp = final.get(s.fp_key)
        if fp is None:
            continue
        c = copy.copy(ft)
        c.tree, c.gi, c.go, c.tr, c.site = final, None, None, None, None
        c.facts, c.fp, c.stamp, c.warnings = facts, fp, _stamp(final), list(ft.tr.warnings)
        entries[id(ft)] = c
    # a group calling one that isn't kept can't be reused either
    inner_of = {i: [f[2] for f in c.facts if f[0] == "nested"] for i, c in entries.items()}
    changed = True
    while changed:
        changed = False
        for i in list(entries):
            if any(not ft.reused and id(ft) not in entries for ft in inner_of[i]):
                del entries[i]
                changed = True
    for c in entries.values():
        c.facts = [("nested", f[1], f[2].base if f[2].reused else entries[id(f[2])]) if f[0] == "nested" else f
                   for f in c.facts]
        kept = _CACHE.setdefault(c.key, [])
        kept.append(c)
        del kept[:-_PER_KEY]
