"""The replayed nodes of an armature stepped over its pose: sample the evaluated pose in the game's numbers, tick every
node between two frames, turn the result into the twins' pose. The bake and the live preview both drive it."""
import json

import bpy
import numpy as np
from mathutils import Matrix, Quaternion, Vector

from .dynamics_solver import Sim, compose, relative

KEY = "fpmp_dynamics"
STATE = "fpmp_dynamics_state"       # the movement state whose dynamics parameters apply (emote, standing, skydiving...)
PREROLL = 1.0       # seconds held on the first pose: in game the character was already idling when the emote began
FLIP = Matrix(((1.0, 0.0, 0.0), (0.0, -1.0, 0.0), (0.0, 0.0, 1.0)))     # the game's axes vs Blender's
COMPONENT = "__component__"     # a sampled pose's entry for the armature object (the game's mesh component) in the world


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
    n = dict(n, **(n.get("by_state") or {}).get(state or "OnGround_Standing", {}))      # what the game binds in that state
    if n.get("alpha", 1.0) < 1e-5:          # switched off in this state: the game skips it
        return None
    kind = n.get("kind", "anim_dynamics")
    if kind == "anim_dynamics":
        return Sim(n, parent_of, state) if n["bone"] in bones else None
    if kind == "rigid_body":
        return RigidSim(n, parent_of, state)
    if kind == "control_rig":
        from .rig_node import RigNode
        rest = {b.name: to_game(b.matrix_local, ue_offset(b), unit) for b in bones}
        return RigNode(n, parent_of, rest, state)
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
        for rig in [s for s in sims if hasattr(s, "restrict")]:
            rig.restrict({name for other in sims if not hasattr(other, "restrict") for name in other.outputs()})
        self.sims = [s for s in sims if (s.outputs() or hasattr(s, "restrict")) and all(name in bones for name in s.bones())]
        self.root = next(b.name for b in bones if b.parent is None)
        self.clothes = []
        if stored.get("cloth"):
            from .cloth_solver import ClothSim
            rest = {b.name: to_game(b.matrix_local, ue_offset(b), unit) for b in bones}
            self.clothes = [{"id": c["id"], "slots": c["slots"], "sims": [ClothSim(x, rest, self.state, self.root) for x in c["clothes"]]}
                            for c in stored["cloth"]]
        need = {self.root}
        wanted = [name for s in self.sims for name in s.bones()]
        wanted += [name for c in self.clothes for s in c["sims"] for name in s.bones_needed() if name in bones]
        for name in wanted:
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
        self.twinned = set(self.simulated)
        self.alpha = {b: s.node.get("alpha", 1.0) for s in self.sims for b in s.outputs()}     # the last writer's, for reports
        self.children = {n: [c for c in self.order if parent_of[c] == n] for n in self.order}
        scene = obj.users_scene[0] if obj.users_scene else bpy.context.scene
        fps = scene.render.fps / scene.render.fps_base
        self.steps = max(1, round(rate / fps))
        self.dt = 1.0 / (fps * self.steps)
        self.component = (Quaternion(), Vector())

    def sample(self, matrices=None):
        """The evaluated pose as locals in the game's numbers; matrices: armature matrices to read instead for some
        bones (the live preview's inputs)."""
        pose, parent_of, matrices = self.obj.pose.bones, self.parent_of, matrices or {}
        cs = {n: to_game(pose[n].matrix, self.offsets[n], self.unit) for n in self.order}
        # locals against the evaluated parents: the given matrices are built on them too
        own = {n: to_game(m, self.offsets[n], self.unit) for n, m in matrices.items() if n in cs}
        loc = {n: relative(own.get(n, cs[n]), cs[parent_of[n]]) if parent_of[n] in cs else own.get(n, cs[n]) for n in self.order}
        loc[COMPONENT] = to_game(self.obj.matrix_world, Matrix.Identity(4), self.unit)
        return loc

    def tick(self, loc):
        parent_of = self.parent_of
        self.input = loc                # what Blender evaluates under the twins
        loc, cs = dict(loc), {}         # the locals reaching each node: earlier nodes' results included
        for n in self.order:
            cs[n] = compose(loc[n], cs[parent_of[n]]) if parent_of[n] in cs else loc[n]
        self._wind()
        for s in self.sims:
            for name, t in s.evaluate(self.dt, cs, self.root).items():
                a, par = s.node.get("alpha", 1.0), cs.get(parent_of[name])        # each node blends its own result
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
        for c in self.clothes:      # the engine simulates cloth from the finished pose
            c["out"] = [s.step(self.dt, cs, self.component) for s in c["sims"]]
        return cs

    def cloth(self):
        """The last tick's cloth by mesh id: its render slots in component space (cm, the game's axes)."""
        from .cloth_solver import mapped
        q, t = self.component
        to_component = np.array(q.conjugated().to_matrix())
        return {c["id"]: (mapped(c["slots"], np.concatenate([p for p, _ in c["out"]]), np.concatenate([n for _, n in c["out"]]))
                          - np.array(t)) @ to_component.T
                for c in self.clothes if c.get("out")}

    def _wind(self):
        """The scene's custom wind fields at this frame, handed to the nodes in the game's numbers (cm/s): the bodies
        in the component's space, the cloth in the world."""
        from . import wind
        scene = bpy.context.scene
        now = wind.scene_time(scene)
        if getattr(self, "wind_at", (None,))[0] != now:       # read once a frame: a map scene has many objects to scan
            root = self.obj.matrix_world @ self.obj.pose.bones[self.root].head      # where the character feels it
            v = wind.velocity(wind.fields(scene), now, root)
            self.wind_at = (now, None if v is None else FLIP @ (v / scene.unit_settings.scale_length) / self.unit)
        world = self.wind_at[1]
        local = None if world is None else self.component[0].conjugated() @ world
        for s in self.sims:
            if hasattr(s, "wind"):
                s.wind = local
        for c in self.clothes:
            for s in c["sims"]:
                s.wind = None if world is None else np.array(world)

    def _component(self, motion):
        for s in self.sims:
            if hasattr(s, "component_motion"):
                s.component_motion = motion

    def start(self, loc):
        """The first frame: the bodies settle on it for the pre-roll."""
        self.component_velocity = Vector()
        self._component(None)
        self.component = loc.get(COMPONENT, self.component)
        for _ in range(round(PREROLL / self.dt)):
            cs = self.tick(loc)
        return cs

    def advance(self, prev, loc):
        """One frame on, ticking through poses interpolated from the last frame's."""
        if COMPONENT in prev and COMPONENT in loc:      # the character's own movement this frame, as RBAN measures it
            frame = self.dt * self.steps
            velocity = (loc[COMPONENT][1] - prev[COMPONENT][1]) / frame
            accel = (velocity - getattr(self, "component_velocity", Vector())) / frame
            self.component_velocity = velocity
            self._component((velocity, accel, loc[COMPONENT][0]))
        for k in range(1, self.steps + 1):
            if COMPONENT in prev and COMPONENT in loc:
                self.component = blend(prev[COMPONENT], loc[COMPONENT], k / self.steps)
            cs = self.tick({n: blend(prev[n], loc[n], k / self.steps) for n in self.order})
        return cs

    def _evaluated(self, cs, name, loc):
        """A bone as Blender poses it under the twins: a twinned one at its result, any other at its input local under
        its parent (a Control Rig's change to it only reaches the simulation)."""
        if name in self.twinned or loc is None:
            return cs[name]
        par = self.parent_of[name]
        return compose(loc[name], self._evaluated(cs, par, loc)) if par in cs else loc[name]

    def basis(self, cs, name, loc=None):
        """A simulated bone's result as its twin's local pose (location, rotation); loc: the frame's input (the last
        tick's by default)."""
        bone = self.obj.data.bones[name]
        rest = bone.parent.matrix_local.inverted() @ bone.matrix_local if bone.parent else bone.matrix_local
        par = self.parent_of[name]
        m = from_game(cs[name], self.inv[name], self.unit)
        if par in cs:
            m = from_game(self._evaluated(cs, par, loc or getattr(self, "input", None)), self.inv[par], self.unit).inverted() @ m
        loc, q, _ = (rest.inverted() @ m).decompose()
        return loc, q
