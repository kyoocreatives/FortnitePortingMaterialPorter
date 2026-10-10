"""The small skeletal controls that follow a RigidBody node in Fortnite's anim blueprints: Constraint (a bone pulled
toward weighted targets with their reference-pose offsets) and ModifyBone (translation and rotation set or added in a
chosen space), as the engine evaluates them. Same interface as the other replayed nodes; scale is not carried."""
from mathutils import Quaternion, Vector

from ...logger import Log
from .dynamics_solver import compose, relative
from .rigid_bodies import rotator

ZERO_WEIGHT = 0.00001
IDENTITY = (Quaternion(), Vector((0.0, 0.0, 0.0)))


def _scaled(t, w):
    return Quaternion([x * w for x in t[0]]), t[1] * w


def _blend(parts):
    """Weighted transforms as the engine blends them: weights scaled down only when they sum past 1, shortest-way
    rotations accumulated, then normalised."""
    total = sum(w for _, w in parts)
    if total <= ZERO_WEIGHT:
        return None
    k = 1.0 / total if total > 1.0 else 1.0
    q, p = _scaled(parts[0][0], parts[0][1] * k)
    for t, w in parts[1:]:
        r = t[0] if t[0].dot(q) >= 0.0 else -t[0]
        q = Quaternion([a + b * w * k for a, b in zip(q, r)])
        p = p + t[1] * (w * k)
    return q.normalized(), p


class BoneControl:
    def __init__(self, node, parent_of, rest):
        self.node, self.parent_of = node, parent_of
        self.data = []
        if node["kind"] == "constraint" and node["bone"] in rest:
            source = rest[node["bone"]]
            for setup, weight in zip(node["setups"], node["weights"]):
                if setup["target"] not in rest:
                    continue
                target = rest[setup["target"]] if setup["offset"] != "None" else IDENTITY
                kind = setup["type"]
                if kind not in ("Parent", "Translation", "Rotation"):
                    Log.info("[Material Porter] dynamics: %s's %s constraint is not replayed" % (node["bone"], kind))
                    continue
                if setup["offset"] == "None":
                    offset = IDENTITY       # the engine takes the target as it is
                elif kind == "Parent":
                    offset = relative(source, target)
                elif kind == "Translation":
                    offset = (Quaternion(), source[1] - target[1])
                else:
                    offset = (target[0].conjugated() @ source[0], Vector())
                self.data.append((setup, weight, offset))
        if node["kind"] == "modify_bone" and node["scale_mode"] != "Ignore":
            Log.info("[Material Porter] dynamics: %s's scale change is not replayed" % node["bone"])

    def bones(self):
        extra = [s["target"] for s in self.node.get("setups", [])]
        parent = self.parent_of.get(self.node["bone"])
        return [self.node["bone"]] + [b for b in extra if b] + ([parent] if parent else [])

    def outputs(self):
        return [self.node["bone"]]

    def evaluate(self, dt, cs, root):
        bone = self.node["bone"]
        if bone not in cs:
            return {}
        if self.node["kind"] == "constraint":
            return self._constraint(cs, bone)
        return {bone: self._modify(cs, bone)}

    def _constraint(self, cs, bone):
        parents, moves, turns = [], [], []
        for setup, weight, offset in self.data:
            if weight <= ZERO_WEIGHT or setup["target"] not in cs:
                continue
            target = cs[setup["target"]]
            kind = setup["type"]
            if kind == "Parent":
                parents.append((compose(offset, target), weight))
            elif kind == "Translation":
                moved = target[1] + offset[1]
                moves.append((Vector([moved[i] if setup["axes"][i] else 0.0 for i in range(3)]), weight))
            else:
                turns.append(((target[0] @ offset[0], Vector()), weight))
        if not (parents or moves or turns):
            return {}
        if parents:
            return {bone: _blend(parents)}
        q, p = cs[bone]
        if moves:
            total = sum(w for _, w in moves)
            k = 1.0 / total if total > 1.0 else 1.0
            p = sum((v * (w * k) for v, w in moves), Vector())
        if turns:
            q = _blend(turns)[0]
        return {bone: (q, p)}

    def _space(self, cs, bone, space):
        if space == "ParentBoneSpace":
            parent = self.parent_of.get(bone)
            return cs.get(parent) if parent else None
        if space == "BoneSpace":
            return cs[bone]
        return None         # component and world space (the component sits at the origin)

    def _modify(self, cs, bone):
        n, t = self.node, cs[bone]
        if n["rotation_mode"] != "Ignore":
            frame = self._space(cs, bone, n["rotation_space"])
            local = relative(t, frame) if frame else t
            turn = rotator(*n["rotation"])
            local = (turn @ local[0] if n["rotation_mode"] == "Additive" else turn, local[1])
            t = compose(local, frame) if frame else local
        if n["translation_mode"] != "Ignore":
            frame = self._space(cs, bone, n["translation_space"])
            local = relative(t, frame) if frame else t
            move = Vector(n["translation"])
            local = (local[0], local[1] + move if n["translation_mode"] == "Additive" else move)
            t = compose(local, frame) if frame else local
        return t
