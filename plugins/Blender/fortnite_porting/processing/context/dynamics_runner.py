"""The replayed nodes of an armature stepped over its pose: sample the evaluated pose in the game's numbers, tick every
node between two frames, turn the result into the twins' pose. The bake and the live preview both drive it."""
import json

import bpy
from mathutils import Matrix, Quaternion

from .dynamics_solver import Sim, compose, relative

KEY = "fpmp_dynamics"
STATE = "fpmp_dynamics_state"       # the movement state whose dynamics parameters apply (emote, standing, skydiving...)
PREROLL = 1.0       # seconds held on the first pose: in game the character was already idling when the emote began
FLIP = Matrix(((1.0, 0.0, 0.0), (0.0, -1.0, 0.0), (0.0, 0.0, 1.0)))     # the game's axes vs Blender's


def to_game(m, offset, unit):
    """A pose bone's armature matrix -> its game transform (rotation, position in cm)."""
    g = m @ offset
    return (FLIP @ g.to_3x3().normalized() @ FLIP).to_quaternion(), FLIP @ g.translation / unit


def from_game(t, offset_inv, unit):
    m = (FLIP @ t[0].to_matrix() @ FLIP).to_4x4()
    m.translation = FLIP @ t[1] * unit
    return m @ offset_inv


def blend(a, b, u):
    """Between two keys as the engine interpolates them: normalized lerp on the short way, linear position."""
    qb = -b[0] if a[0].dot(b[0]) < 0.0 else b[0]
    return Quaternion([x + (y - x) * u for x, y in zip(a[0], qb)]).normalized(), a[1].lerp(b[1], u)


def _node(n, parent_of, bones, unit, state=None):
    """The replayer for one stored node, by its kind; None when the armature lacks what it needs."""
    from ...material_porter.effects import ue_offset
    from .bone_controls import BoneControl
    from .rigid_solver import RigidSim
    kind = n.get("kind", "anim_dynamics")
    if kind == "anim_dynamics":
        return Sim(n, parent_of, state) if n["bone"] in bones else None
    if kind == "rigid_body":
        return RigidSim(n, parent_of, state)
    if n["bone"] not in bones:
        return None
    named = [n["bone"]] + [s["target"] for s in n.get("setups", []) if s["target"] in bones]
    rest = {b: to_game(bones[b].matrix_local, ue_offset(bones[b]), unit) for b in named}
    return BoneControl(n, parent_of, rest)


class Runner:
    """Fresh node state for one run; `sims` is empty when the armature has nothing to replay."""

    def __init__(self, obj, rate=60):
        from ...material_porter.effects import ue_offset
        self.obj, self.sims = obj, []
        raw = obj.data.get(KEY)
        if not raw:
            return
        stored = json.loads(raw)
        bones = obj.data.bones
        self.parent_of = parent_of = {b.name: b.parent.name if b.parent else None for b in bones}
        self.unit = unit = stored.get("scale", 0.01)
        self.state = obj.data.get(STATE)
        sims = [s for s in (_node(n, parent_of, bones, unit, self.state) for n in stored["nodes"]) if s is not None]
        self.sims = [s for s in sims if s.outputs() and all(name in bones for name in s.bones())]
        self.root = next(b.name for b in bones if b.parent is None)
        need = {self.root}
        for s in self.sims:
            for name in s.bones():
                while name and name not in need:
                    need.add(name)
                    name = parent_of[name]
        depth = {}
        for b in bones:
            depth[b.name] = depth[b.parent.name] + 1 if b.parent else 0
        self.order = sorted(need, key=lambda n: depth[n])        # parents before children
        self.offsets = {n: ue_offset(bones[n]) for n in self.order}
        self.inv = {n: o.inverted() for n, o in self.offsets.items()}
        self.simulated = list(dict.fromkeys(b for s in self.sims for b in s.outputs()))
        self.alpha = {b: s.node.get("alpha", 1.0) for s in self.sims for b in s.outputs()}
        self.children = {n: [c for c in self.order if parent_of[c] == n] for n in self.order}
        scene = obj.users_scene[0] if obj.users_scene else bpy.context.scene
        fps = scene.render.fps / scene.render.fps_base
        self.steps = max(1, round(rate / fps))
        self.dt = 1.0 / (fps * self.steps)

    def sample(self, matrices=None):
        """The evaluated pose as locals in the game's numbers; matrices: armature matrices to read instead for some
        bones (the live preview's inputs)."""
        pose, parent_of, matrices = self.obj.pose.bones, self.parent_of, matrices or {}
        cs = {n: to_game(pose[n].matrix, self.offsets[n], self.unit) for n in self.order}
        # locals against the evaluated parents: the given matrices are built on them too
        own = {n: to_game(m, self.offsets[n], self.unit) for n, m in matrices.items() if n in cs}
        return {n: relative(own.get(n, cs[n]), cs[parent_of[n]]) if parent_of[n] in cs else own.get(n, cs[n]) for n in self.order}

    def tick(self, loc):
        parent_of = self.parent_of
        loc, cs = dict(loc), {}         # the locals reaching each node: earlier nodes' results included
        for n in self.order:
            cs[n] = compose(loc[n], cs[parent_of[n]]) if parent_of[n] in cs else loc[n]
        for s in self.sims:
            for name, t in s.evaluate(self.dt, cs, self.root).items():
                a, par = self.alpha[name], cs.get(parent_of[name])
                if a < 1e-5:
                    continue
                local = relative(t, par) if par else t
                if a < 1.0 - 1e-5:      # the engine blends a partial node in the bone's local space
                    local = blend(loc[name], local, a)
                loc[name] = local
                cs[name] = compose(local, par) if par else local
                stack = list(self.children[name])
                while stack:            # its children follow from their own locals
                    c = stack.pop()
                    cs[c] = compose(loc[c], cs[parent_of[c]])
                    stack += self.children[c]
        return cs

    def start(self, loc):
        """The first frame: the bodies settle on it for the pre-roll."""
        for _ in range(round(PREROLL / self.dt)):
            cs = self.tick(loc)
        return cs

    def advance(self, prev, loc):
        """One frame on, ticking through poses interpolated from the last frame's."""
        for k in range(1, self.steps + 1):
            cs = self.tick({n: blend(prev[n], loc[n], k / self.steps) for n in self.order})
        return cs

    def basis(self, cs, name):
        """A simulated bone's result as its twin's local pose (location, rotation)."""
        bone = self.obj.data.bones[name]
        rest = bone.parent.matrix_local.inverted() @ bone.matrix_local if bone.parent else bone.matrix_local
        par = self.parent_of[name]
        m = from_game(cs[name], self.inv[name], self.unit)
        if par in cs:
            m = from_game(cs[par], self.inv[par], self.unit).inverted() @ m
        loc, q, _ = (rest.inverted() @ m).decompose()
        return loc, q
