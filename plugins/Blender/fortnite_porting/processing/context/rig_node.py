"""The Control Rig node among the replayed ones: the character's rig program (rig_vm) run on the pose reaching it,
its changes handed on as bones' component transforms. They move what the later nodes simulate; only bones those
nodes simulate get the rig's result as an output (a twin): the rest is left to the deform rig's drivers."""
from mathutils import Quaternion, Vector

from .dynamics_solver import compose, relative
from .rig_vm import Machine
from .rig_units import UNITS

IDENTITY = (Quaternion(), Vector((0.0, 0.0, 0.0)))


class Hierarchy:
    """The rig's elements: skeleton bones plus its nulls, local transforms now and at rest, globals on demand."""

    def __init__(self, parent_of, rest, nulls):
        self.parent = dict(parent_of)
        self.rest_global = dict(rest)
        for null in nulls:      # "<bone>_null": a rest-pose reference for that bone, beside it under its parent
            bone = null[:-5] if null.endswith("_null") else None
            if bone in rest:
                self.parent[null] = parent_of.get(bone)
                self.rest_global[null] = rest[bone]
        self.rest_local = {n: relative(t, self.rest_global[self.parent[n]]) if self.parent.get(n) in self.rest_global else t
                           for n, t in self.rest_global.items()}
        self.local, self.cache, self.changed = {}, {}, set()

    def load(self, cs):
        """Locals from the component pose reaching the node; nulls stay at rest."""
        self.local, self.cache, self.changed = {}, {}, set()
        for n, t in cs.items():
            p = self.parent.get(n)
            self.local[n] = relative(t, cs[p]) if p in cs else t

    def has(self, name):
        return name in self.rest_global or name in self.local

    def get_local(self, name, initial=False):
        if initial or name not in self.local:
            return self.rest_local.get(name, IDENTITY)
        return self.local[name]

    def get_global(self, name, initial=False):
        if initial:
            return self.rest_global.get(name, IDENTITY)
        if name not in self.cache:
            p = self.parent.get(name)
            own = self.get_local(name)
            self.cache[name] = compose(own, self.get_global(p)) if p else own
        return self.cache[name]

    def set_local(self, name, t):
        if not self.has(name):
            return
        self.local[name] = (t[0].normalized(), t[1].copy())
        self.changed.add(name)
        self._dirty(name)

    def set_global(self, name, t):
        p = self.parent.get(name)
        self.set_local(name, relative(t, self.get_global(p)) if p else t)

    def _dirty(self, name):
        stack = [name]
        while stack:
            n = stack.pop()
            if self.cache.pop(n, None) is not None or n == name:
                stack += [c for c, p in self.parent.items() if p == n]


class RigNode:
    def __init__(self, node, parent_of, rest, state=None):
        self.node, self.parent_of = node, parent_of
        program = node["program"]
        self.h = Hierarchy(parent_of, rest, program.get("nulls") or [])
        self.machine = Machine(program, UNITS, node.get("variables") or {})
        self.reads = [b for b in program.get("bones") or [] if b in parent_of]
        self.writes = [b for b in program.get("writes") or [] if b in parent_of]
        self.keep = list(self.writes)

    def bones(self):
        return list(self.reads)

    def outputs(self):
        return list(self.keep)

    def restrict(self, simulated):
        self.keep = [b for b in self.writes if b in simulated]

    def evaluate(self, dt, cs, root):
        self.h.load(cs)
        self.machine.run(self.h, dt, self.node.get("variables"))
        return {n: self.h.get_global(n) for n in self.h.changed if n in self.parent_of}
