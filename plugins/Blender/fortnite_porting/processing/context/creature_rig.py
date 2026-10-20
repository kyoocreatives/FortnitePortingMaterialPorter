"""Control rig for a creature armature (wolf, chicken, raptor, LEGO cow...), on the kit (rig_kit): the official rig's
standard for skeletons that share no bone names.

Creature skeletons share no bone names (Battle Royale: QuadSpine_A_Pelvis_C, PawedLeg_A_Thigh_L; LEGO Fortnite: pelvis,
legB_01_l), so the rig reads the bone tree: the spine from pelvis to head, side bones under centre bones as limbs,
limbs reaching the ground as legs, the tail and the face. Controls, as Epic's:
- global_ctrl (hexagon on the ground), root_ctrl (arrow ahead), body_ctrl (octagon around the hips: the centre of
  gravity), the pelvis's control (hips ring);
- spine rounded squares shaded red to yellow, neck and head rings, tail rings, each sized to the mesh around it;
- legs (and arms with a hand) IK: a box at the foot, a pole sphere out from the knee with a line to it; their FK rings
  show when the limb is switched to FK;
- toes, claws: arrows along the bone; the eyes look at a control ahead of the face;
- the face: the jaw a flat box at the chin, chains (ears, tongue, whiskers) rings around each bone, the other face
  bones (lips, brows, lids, cheeks) small balls on their joints.
The game bones keep their names, rest pose and hierarchy and copy their controls (rig off: an animation plays on them).
"""

import re

import bpy
from mathutils import Vector

from . import rig_kit, rig_style

KEY = "is_creature_rig"
PREFIX = "CR_"          # the rig's own eye bones: CR_Eyes, CR_Eye_<bone>

SIDE = re.compile(r"(^|[_.\-\s])(l|left)([_.\-\s]|$)|(^|[_.\-\s])(r|right)([_.\-\s]|$)", re.IGNORECASE)
FOOT = re.compile(r"^(foot|ankle|wrist|paw|hoof)[a-z]?$")      # matches a name word: footA, Ankle; not PawedArm
HEAD = re.compile(r"(^|_)head(_c|_jnt|_\d+)?$", re.IGNORECASE)          # includes head_01 (sidekicks)
HAND = re.compile(r"^(hand|wrist|paw)[a-z]?$")                            # an arm's IK end, matched per name word
BALL = re.compile(r"^ball$")                                              # a paw or hoof's ball: where it meets the ground
GAME_IK = re.compile(r"^ik_foot_(?!.*root)", re.IGNORECASE)               # the game's own IK goal bones (IK_Foot_Rear_L)
EYE = re.compile(r"(^|_)eye(ball)?(_|$)", re.IGNORECASE)                  # eye_l, Eyeball_R; not eyelid, eyebrow
JAW = re.compile(r"(^|_)jaw(_|$)", re.IGNORECASE)                          # jaw, Jaw_C; not reverse_jaw
PELVIS = re.compile(r"pelvis|hips", re.IGNORECASE)
TAIL = re.compile(r"tail", re.IGNORECASE)
SHOULDER_BLADE = re.compile(r"clavicle|scapula", re.IGNORECASE)
HELPER = re.compile(r"^(ik_|fx_|vb |.*socket)|attach|null$|_effect$|twist|_end$|^interact$", re.IGNORECASE)
CTRL = "_ctrl"


def side_of(name):
    """"L", "R" or "C" from a bone's name (thigh_l, PawedLeg_A_Thigh_L, L_Wing...)."""
    m = SIDE.search(name)
    if m is None:
        return "C"
    return "L" if m.group(2) else "R"


class Limb:
    def __init__(self, bones, kind):
        self.bones = bones          # names, from the top down
        self.kind = kind            # "leg", "arm", "wing", "face"
        self.chain = []             # IK chain bone names, top down
        self.foot = None            # the bone after the chain (its head ends it), or None: the chain ends at its tail
        self.stub = False           # a nub too small to bend (a sprite's feet and hands): FK, ball controls

    @property
    def name(self):
        return self.chain[0] if self.chain else self.bones[0]


class Survey:
    """What a skeleton is made of, read from its edit bones (armature space)."""

    def __init__(self, edit_bones):
        self.bones = {b.name: b for b in edit_bones}
        usable = [b for b in edit_bones if not HELPER.search(b.name)]
        points = [p for b in usable for p in (b.head, b.tail)] or [Vector()]
        zs = [p.z for p in points]
        spread = max(max(p[i] for p in points) - min(p[i] for p in points) for i in (0, 1))
        self.ground, self.height = min(zs), max(max(zs) - min(zs), 1e-6)
        # controls' scale: a flat skeleton (a fish or snake on the ground) still gets ones it can see
        self.size = max(self.height, 0.25 * spread, 1e-3)
        tops = [b for b in edit_bones if b.parent is None]
        self.root = next((b.name for b in tops if b.name.lower() == "root"), tops[0].name if tops else None)
        self.pelvis = self._pelvis(edit_bones)
        self.spine = self._spine()
        self.head = next((n for n in self.spine if HEAD.search(n)), None)
        if self.head is None:
            # the head off the pelvis's branch (LEGO cow: root > body > upper_body > neck > head, the pelvis beside):
            # the spine is the head's ancestry under the root
            heads = [b for b in edit_bones if HEAD.search(b.name) and side_of(b.name) == "C" and not HELPER.search(b.name)]
            if heads:
                head = min(heads, key=lambda b: len(b.parent_recursive))
                self.head = head.name
                self.spine = [b.name for b in reversed(head.parent_recursive) if b.name != self.root] + [head.name]
        self.tail = self._tail()
        self.ik_ends = self._ik_ends(edit_bones)
        self.limbs = self._limbs(edit_bones)

    def _ik_ends(self, edit_bones):
        """The bones the game's own IK goals sit on (IK_Foot_Front_L on PawedArm_A_Ball_L): where its leg IK ends."""
        ends = set()
        for goal in (b for b in edit_bones if GAME_IK.search(b.name)):
            near = [b for b in edit_bones if not HELPER.search(b.name) and side_of(b.name) == side_of(goal.name)
                    and (b.head - goal.head).length < 1e-3 * self.height]
            if near:
                ends.add(min(near, key=lambda b: (b.head - goal.head).length).name)
        return ends

    def _end(self, names, start):
        """Index of the bone a limb's IK ends at (its head), from the game's IK goals, else a ball bone; or None."""
        for i in range(start + 2, len(names)):
            if names[i] in self.ik_ends:
                return i
        if not self.ik_ends:
            return next((i for i in range(start + 2, len(names))
                         if any(BALL.match(w.lower()) for w in re.split(r"[_\d]+", names[i]))), None)
        return None

    def _pelvis(self, edit_bones):
        named = [b.name for b in edit_bones if PELVIS.search(b.name) and side_of(b.name) == "C" and not HELPER.search(b.name)]
        if named:
            return min(named, key=lambda n: len(self.bones[n].parent_recursive))
        # fall back to the root's centre child with the most bones under it
        root = self.bones.get(self.root)
        children = [c for c in (root.children if root else []) if not HELPER.search(c.name)]
        return max(children, key=lambda c: len(c.children_recursive)).name if children else self.root

    def _spine(self):
        """Pelvis to head, stepping to the centre child with the most bones under it (not the tail)."""
        path, at = [], self.pelvis
        while at is not None:
            path.append(at)
            if HEAD.search(at):
                break
            centre = [c for c in self.bones[at].children if side_of(c.name) == "C" and not TAIL.search(c.name)
                      and not HELPER.search(c.name) and c.children_recursive]
            at = max(centre, key=lambda c: len(c.children_recursive)).name if centre else None
        return path

    def _tail(self):
        starts = [b for b in self.bones.values() if TAIL.search(b.name) and side_of(b.name) == "C"
                  and (b.parent is None or not TAIL.search(b.parent.name))]
        if not starts:
            return []
        start = min(starts, key=lambda b: len(b.parent_recursive))
        chain, at = [], start
        while at is not None:
            chain.append(at.name)
            at = max(at.children, key=lambda c: len(c.children_recursive)) if at.children else None
        return chain

    def _limbs(self, edit_bones):
        head_subtree = set(c.name for c in self.bones[self.head].children_recursive) | {self.head} if self.head else set()
        limbs = []
        for b in edit_bones:
            if side_of(b.name) == "C" or HELPER.search(b.name) or b.parent is None or side_of(b.parent.name) != "C":
                continue
            # follow the child with the most bones under it
            chain, at = [], b
            while at is not None:
                chain.append(at.name)
                kids = [c for c in at.children if not HELPER.search(c.name)]
                at = max(kids, key=lambda c: len(c.children_recursive)) if kids else None
            if b.name in head_subtree or b.parent.name in head_subtree:
                kind = "face"
            elif "wing" in b.name.lower():
                kind = "wing"
            else:
                low = min(min(self.bones[n].head.z, self.bones[n].tail.z) for n in chain)
                kind = "leg" if low < self.ground + 0.2 * self.height and len(chain) >= 2 else "arm"
            limb = Limb(chain, kind)
            if kind in ("leg", "arm") and sum(self.bones[n].length for n in chain) < 0.15 * self.height:
                limb.kind, limb.stub = "arm", True
            elif kind == "leg":
                self._leg(limb)
            if limb.kind == "arm" and not limb.stub:
                self._arm(limb)
            limbs.append(limb)
        return limbs

    def _leg(self, limb):
        """Leg IK chain: first bone (after a clavicle/scapula) down to the bone above the foot. A leg of two bones and
        no foot (LEGO cow) bends both, ending at the lower one's tail."""
        names = limb.bones
        start = 1 if SHOULDER_BLADE.search(names[0]) and len(names) > 3 else 0
        foot = self._end(names, start)
        if foot is None:
            foot = next((i for i in range(start + 1, len(names)) if any(FOOT.match(w.lower()) for w in re.split(r"[_\d]+", names[i]))), None)
        if foot is None:
            # no foot name: first bone touching the ground (claw tip, toe), else the one above the toe
            touching = [i for i in range(start + 2, len(names)) if self.bones[names[i]].head.z < self.ground + 0.06 * self.height]
            foot = touching[0] if touching else (len(names) - 2 if len(names) - start >= 3 else None)
        if foot is None and len(names) - start == 2:
            limb.chain = names[start:]
            return
        if foot is None or foot - start < 2:
            limb.kind = "arm"       # too short to bend: FK
            return
        limb.chain, limb.foot = names[start:foot], names[foot]

    def _arm(self, limb):
        """Arm IK chain if there is a hand: first bone (after a clavicle) to the bone above the hand."""
        names = limb.bones
        start = 1 if SHOULDER_BLADE.search(names[0]) else 0
        hand = self._end(names, start)
        if hand is None:
            hand = next((i for i in range(start + 2, len(names)) if any(HAND.match(w.lower()) for w in re.split(r"[_\d]+", names[i]))), None)
        if hand is not None:
            limb.chain, limb.foot = names[start:hand], names[hand]


def create(obj):
    """Rig the armature object. Returns what it found, in a line."""
    armature = obj.data
    if armature.get(KEY):
        return "%s: already has a creature rig" % obj.name
    fit = rig_kit.Fit(obj)
    view_layer = bpy.context.view_layer
    for o in view_layer.objects:
        o.select_set(False)
    view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    edit = armature.edit_bones
    survey = Survey(edit)
    h = survey.size
    up = Vector((0.0, 0.0, 1.0))
    # facing: pelvis to head on the ground, else -Y (UE forward)
    ahead = Vector((0.0, -1.0, 0.0))
    if survey.head and survey.pelvis:
        towards = survey.bones[survey.head].head - survey.bones[survey.pelvis].head
        towards.z = 0.0
        if towards.length > 1e-3 * survey.height:
            ahead = towards.normalized()
    left = up.cross(ahead)
    legs = [l for l in survey.limbs if l.chain]         # includes arms with a hand
    eyes = {}
    head_subtree = {c.name for c in survey.bones[survey.head].children_recursive} if survey.head else set()
    for name in sorted(n for n in head_subtree if EYE.search(n) and side_of(n) != "C" and not HELPER.search(n)):
        eye = edit[name]
        best = max(range(3), key=lambda i: abs(eye.matrix.col[i].to_3d().normalized().dot(ahead)))
        axis = eye.matrix.col[best].to_3d().normalized()
        sign = 1.0 if axis.dot(ahead) >= 0 else -1.0
        eyes[name] = ("TRACK_" + ("" if sign > 0 else "NEGATIVE_") + "XYZ"[best], axis * sign)

    # where the body stands: its mesh, else its bones
    usable = [b for b in edit if not HELPER.search(b.name)]
    bounds = fit.bounds() or (Vector([min(p[i] for b in usable for p in (b.head, b.tail)) for i in range(3)]),
                              Vector([max(p[i] for b in usable for p in (b.head, b.tail)) for i in range(3)]))
    centre = (bounds[0] + bounds[1]) / 2.0
    ground = Vector((centre.x, centre.y, survey.ground))
    reach = max((Vector((bounds[1].x - bounds[0].x, bounds[1].y - bounds[0].y, 0.0))).length / 2.0, h * 0.25)

    # the controls: the global one, a copy of each game bone the animator turns (parents first), the body
    glob = rig_kit.new_bone(edit, "global_ctrl", ground, ground + ahead * h * 0.2, None, up)
    controls = {}               # game bone -> its control
    if survey.root:
        controls[survey.root] = rig_kit.copy_bone(edit, edit[survey.root], survey.root + CTRL, glob).name
    top = edit.get(controls.get(survey.root, glob.name))
    body = None
    if survey.pelvis and survey.pelvis != survey.root:
        at = edit[survey.pelvis].head.copy()
        body = rig_kit.new_bone(edit, "body_ctrl", at, at + up * h * 0.1, top, ahead)
    skipped = set(eyes) | {survey.root}
    driven = {n for leg in legs for n in leg.chain + ([leg.foot] if leg.foot else [])}
    for bone in sorted(usable, key=lambda b: len(b.parent_recursive)):
        if bone.name in skipped or bone.name.startswith(PREFIX):
            continue
        holder = bone.parent
        while holder is not None and holder.name not in controls:
            holder = holder.parent
        parent = edit[controls[holder.name]] if holder is not None else top
        if holder is not None and holder.name in driven and bone.name not in driven:
            # off an IK limb (toes, claws, a scapula beside the shoulder): hang from the bone as it is, IK or FK,
            # not from its FK control
            parent = edit[holder.name]
        # Epic's: everything hangs from the body, the hips and the spine side by side (turning the hips leaves the spine)
        if body is not None and (parent.name == top.name or bone.name == survey.pelvis
                                 or (len(survey.spine) > 1 and survey.spine[0] == survey.pelvis and bone.name == survey.spine[1])):
            parent = body
        controls[bone.name] = rig_kit.copy_bone(edit, bone, bone.name + CTRL, parent).name
    limbs = []
    for leg in legs:
        tip = edit[leg.foot].head if leg.foot else edit[leg.chain[-1]].tail
        holder = edit[leg.chain[0]].parent
        while holder is not None and holder.name not in controls:
            holder = holder.parent
        mch_parent = controls[holder.name] if holder is not None else top.name
        front = leg.kind == "arm" or bool(survey.pelvis and survey.head and (edit[leg.chain[0]].head - edit[survey.pelvis].head).dot(ahead)
                                           > 0.5 * (edit[survey.head].head - edit[survey.pelvis].head).dot(ahead))
        length = sum(edit[n].length for n in leg.chain)
        limb, angle = rig_kit.ik_limb(edit, leg.name, leg.chain, leg.foot, tip.copy(), mch_parent,
                                      controls.get(survey.root, glob.name), ahead, length * 0.15, front=front)
        side = side_of(leg.chain[0])
        label = ("Arm" if leg.kind == "arm" else "Front Leg" if front else "Hind Leg") + ("" if side == "C" else " " + side)
        limb.update({"angle": angle, "kind": leg.kind, "side": side, "setting": "ik_" + leg.name, "front": front, "label": label,
                     "fk": [controls[b] for b in limb["bones"]], "ik": [limb["target"], limb["pole"], limb["line"]]})
        limbs.append(limb)
    # the eyes look at a control ahead of the face (each down its own most-forward axis: no turn at rest)
    if eyes:
        spread = [edit[n].head for n in eyes]
        across = max(((a - b).length for a in spread for b in spread), default=0.0)
        ahead_by = max(survey.height * 0.6, across * 3.0)
        middle = sum(spread, Vector()) / len(spread)
        aim = rig_kit.new_bone(edit, PREFIX + "Eyes", middle + ahead * ahead_by, middle + ahead * ahead_by + up * h * 0.05,
                               edit[controls[survey.head]] if survey.head in controls else top, -ahead)
        for name, (track, axis) in list(eyes.items()):
            at = edit[name].head + axis * (ahead_by / max(axis.dot(ahead), 0.2))
            rig_kit.new_bone(edit, PREFIX + "Eye_" + name, at, at + up * h * 0.03, aim, -ahead)
            eyes[name] = track
    rig_kit.new_bone(edit, rig_style.SETTINGS, ground, ground + up * h * 0.05, top)
    bpy.ops.object.mode_set(mode='POSE')

    rig_style.collections(armature, fk=bool(legs))
    pose, bones = obj.pose.bones, armature.bones
    W = rig_kit.WIDTHS

    def ring(bone, shape, color, width, group, k=1.15, fallback=0.06, at_mid=False, along=None):
        """A gizmo around a game bone's control, sized to the mesh around the bone (plus a margin). `along`: the
        chain's direction at the bone (to the next joint), for a bone whose own axis doesn't follow it."""
        b = bones[bone]
        head, tail = b.head_local, (b.head_local + along if along is not None else b.tail_local)
        r = fit.radius([bone], head, tail, max((tail - head).length * 0.35, h * fallback * 0.5)) * k
        r = min(r, h * 0.45)
        rig_kit.look(obj, controls[bone], shape, r, color, width, group, ring=True, at=(head + tail) / 2.0 if at_mid else None)
        if along is not None:
            n = along.normalized()
            ref = up if abs(n.dot(up)) < 0.9 else ahead
            rig_kit.align(obj, pose[controls[bone]], y=(ref - n * ref.dot(n)).normalized(), z=n)

    def chain_dirs(names):
        """Each bone's direction along a chain: to the next bone's head (the last: its own, else the one before)."""
        dirs, last = {}, None
        for i, name in enumerate(names):
            b = bones[name]
            d = bones[names[i + 1]].head_local - b.head_local if i + 1 < len(names) else b.tail_local - b.head_local
            if i + 1 == len(names) and d.length < 0.02 * h and last is not None:
                d = last
            if d.length > 1e-6:
                dirs[name] = last = d
        return dirs

    def point(bone, color, group):
        """A ball on a bone's head: a point the skin is pinned to (a sprite's squash bones, a nub), where a ring sized to
        the mesh around it would circle the whole body."""
        rig_kit.look(obj, controls[bone], "Sphere_Thick", h * 0.03, color, W["digit"], group, at=bones[bone].head_local)

    whole = fit.bounds()

    def off_axis(bone):
        """The bone's head out toward the body's sides or front/back (past 45% of the way to its edge)."""
        if whole is None:
            return False
        middle, half, at = (whole[0] + whole[1]) / 2.0, (whole[1] - whole[0]) / 2.0, bones[bone].head_local
        return any(abs((at - middle).dot(axis)) > 0.45 * max(abs(half.dot(axis)), 1e-6) for axis in (left, ahead))

    def arrow(bone, color, group, width=W["digit"]):
        """An arrow along a bone (a digit, an ear): its length the bone's."""
        b = bones[bone]
        length = max(b.length, h * 0.02)
        rig_kit.look(obj, controls[bone], "Arrow_Thick", length / 2.0, color, width, group,
                     at=b.head_local + (b.tail_local - b.head_local) * 0.5)

    face_heads = [bones[n].head_local for n in controls if n in head_subtree]

    def face(name, side):
        """A face bone's control: the jaw a flat box at the chin, a bone continuing into one other (an ear, the tongue,
        a whisker) a ring around it, the last of such a chain too, any other a small ball on its joint."""
        b, color = bones[name], rig_kit.tone(side, "digit")
        kids = [c for c in b.children if c.name in controls and c.name in head_subtree]
        if JAW.search(name) and not name.lower().startswith("reverse"):
            # the chin: the front and bottom of the skin the jaw moves (its bone's tail is made up when it ends there)
            skin = fit.bounds([name] + [c.name for c in b.children_recursive if c.name in head_subtree])
            if skin:
                middle, half = (skin[0] + skin[1]) / 2.0, (skin[1] - skin[0]) / 2.0
                reach = abs(half.dot(ahead))
                chin = middle + ahead * reach * 0.6
                chin.z = skin[0].z
                size = max(reach * 0.35, h * 0.015)
            else:
                tips = [c.head_local for c in b.children_recursive if c.name in head_subtree] + [b.tail_local]
                chin = max(tips, key=lambda p: (p - b.head_local).length)
                size = max((chin - b.head_local).length * 0.25, h * 0.015)
            rig_kit.look(obj, controls[name], "Box_Thick", size, color, W["digit"], "Secondary", at=chin, stretch=(1.0, 1.0, 0.35))
            rig_kit.align(obj, pose[controls[name]], y=ahead, z=up)
            return
        parent = b.parent
        chained = (parent is not None and parent.name in controls and parent.name in head_subtree and parent.name != survey.head
                   and len([c for c in parent.children if c.name in controls]) == 1 and not JAW.search(parent.name))
        along = kids[0].head_local - b.head_local if len(kids) == 1 else             (b.head_local - parent.head_local if chained and not kids else None)
        if along is not None and along.length > h * 0.005:
            # a chain's last bone has a made-up tail: its ring on its joint, not past the tip
            ring(name, "Circle_Thick", color, W["digit"], "Secondary", k=1.1, fallback=0.02, at_mid=bool(kids), along=along)
            return
        near = min(((p - b.head_local).length for p in face_heads if (p - b.head_local).length > 1e-6), default=h * 0.05)
        rig_kit.look(obj, controls[name], "Sphere_Thick", min(max(near * 0.35, h * 0.006), h * 0.025), color, W["digit"],
                     "Secondary", at=b.head_local)

    # global, root, body
    rig_kit.look(obj, "global_ctrl", "Hexagon_Thin", reach * 1.1, rig_kit.COLORS["root"], W["root"], "Controls")
    rig_kit.align(obj, pose["global_ctrl"], y=ahead, z=up)
    if survey.root in controls:
        rig_kit.look(obj, controls[survey.root], "Arrow_Thin", reach * 0.25, rig_kit.COLORS["root"], W["root"], "Controls",
                     at=ground + ahead * reach * 0.7)
        rig_kit.align(obj, pose[controls[survey.root]], y=ahead, z=up)
    if body is not None:
        pelvis = bones[survey.pelvis]
        width = fit.bounds([n for n in survey.spine[:2]]) if fit.bounds([n for n in survey.spine[:2]]) else None
        across = ((width[1] - width[0]).dot(left) if width else 0.0)
        r = max(abs(across) * 0.75, fit.radius([survey.pelvis], pelvis.head_local, pelvis.tail_local, h * 0.12) * 1.5)
        rig_kit.look(obj, "body_ctrl", "Octagon_Thin", min(r, reach), rig_kit.COLORS["body"], W["root"], "Controls")
        rig_kit.align(obj, pose["body_ctrl"], y=ahead, z=up)
    # spine, neck, head, tail
    spine = [n for n in survey.spine if n in controls]
    neck_from = next((i for i, n in enumerate(spine) if "neck" in n.lower()), len(spine) - 1 if survey.head else len(spine))
    along = chain_dirs(spine)
    for i, name in enumerate(spine):
        if name == survey.pelvis:
            ring(name, "Circle_Thick", rig_kit.COLORS["hips"], W["limb"], "Controls", k=1.25)
        elif name == survey.head:
            ring(name, "Circle_Thick", rig_kit.COLORS["neck"], W["limb"], "Controls", k=1.2, at_mid=True, along=along.get(name))
        elif i >= neck_from:
            ring(name, "Circle_Thick", rig_kit.COLORS["neck"], W["limb"], "Controls", k=1.05, along=along.get(name))
        else:
            ring(name, "RoundedSquare_Thick", rig_kit.spine_tone(i / max(neck_from - 1, 1)), W["limb"], "Controls", k=1.1,
                 at_mid=True, along=along.get(name))
    tail = [n for n in survey.tail if n in controls]
    along = chain_dirs(tail)
    for name in tail:
        ring(name, "Circle_Thick", rig_kit.COLORS["hips"], W["limb"], "Controls", k=1.2, at_mid=True, along=along.get(name))
    # limbs
    ik_driven = {n for limb in limbs for n in limb["bones"]}
    placed = set(spine) | set(survey.tail) | {survey.root}
    for limb in survey.limbs:
        side = side_of(limb.bones[0])
        for name in limb.bones:
            if name not in controls:
                continue
            placed.add(name)
            if limb.stub:
                point(name, rig_kit.tone(side), "Controls")
            elif name in ik_driven:
                ring(name, "Circle_Thick", rig_kit.tone(side), W["limb"], rig_style.FK, k=1.2, at_mid=True)
            elif limb.kind == "face":
                face(name, side)
            elif limb.chain and limb.bones.index(name) < limb.bones.index(limb.chain[0]):
                # before the IK chain: a shoulder blade or clavicle, as Epic's clavicle
                b = bones[name]
                rig_kit.look(obj, controls[name], "Arrow_Solid", max(b.length, h * 0.03) / 2.0, rig_kit.tone(side), W["limb"],
                             "Controls", at=(b.head_local + b.tail_local) / 2.0)
            elif limb.foot and limb.bones.index(name) > limb.bones.index(limb.foot):
                arrow(name, rig_kit.tone(side, "digit"), "Secondary")
            elif limb.kind == "wing":
                ring(name, "Circle_Thick", rig_kit.tone(side), W["limb"], "Controls", k=1.1, at_mid=True)
            else:
                ring(name, "Circle_Thick", rig_kit.tone(side), W["limb"], "Controls", k=1.2, at_mid=True)
        if limb.kind != "face":
            for child in bones[limb.bones[0]].children_recursive:
                if child.name in controls and child.name not in placed:
                    placed.add(child.name)
                    arrow(child.name, rig_kit.tone(side, "digit"), "Secondary")
    for limb in limbs:
        side, length = limb["side"], sum(bones[n].length for n in limb["mch"])
        foot = limb["foot"] or limb["bones"][-1]
        b = bones[foot]
        r = fit.radius([foot], b.head_local, b.tail_local, length * 0.1) * 1.3
        rig_kit.look(obj, limb["target"], "Box_Thick", max(min(r, length * 0.25), length * 0.06), rig_kit.tone(side),
                     W["limb"], "Controls", stretch=(1.0, 1.0, 0.6))
        rig_kit.align(obj, pose[limb["target"]], y=ahead, z=up)
        rig_kit.look(obj, limb["pole"], "Sphere_Solid", length * 0.05, rig_kit.tone(side), W["limb"], "Controls")
        rig_style.assign(armature, limb["line"], "Controls")
    # the rest: centre bones off the spine (a hump, an udder), the face's other bones
    for name, control in controls.items():
        if name in placed or name == survey.root:
            continue
        b = bones[name]
        if name in head_subtree:
            face(name, side_of(name))
        elif not b.children and off_axis(name):
            point(name, rig_kit.COLORS["hips"] if side_of(name) == "C" else rig_kit.tone(side_of(name)), "Controls")
        elif b.length > h * 0.04:
            side = side_of(name)
            ring(name, "Circle_Thick", rig_kit.COLORS["hips"] if side == "C" else rig_kit.tone(side), W["limb"], "Controls",
                 k=1.15, at_mid=True)
        else:
            arrow(name, rig_kit.tone(side_of(name), "digit"), "Secondary")
    # the game bones copy their controls; IK limbs blend in their mechanism chains
    settings = [(l["setting"], 1.0, "IK on this limb (0: FK)") for l in limbs]
    if eyes:
        settings.append(("eyes_aim", 1.0, "The eyes look at CR_Eyes"))
    rig_style.add_settings(obj, settings)
    for name, control in controls.items():
        rig_kit.follow(obj, name, control)
    for limb in limbs:
        rig_kit.limb_constraints(obj, limb, limb["angle"], limb["setting"])
        rig_kit.show(obj, limb, True)
    if eyes:
        aim = pose[PREFIX + "Eyes"]
        lefts = [n for n in eyes if side_of(n) == "L"]
        rights = [n for n in eyes if side_of(n) == "R"]
        across = (bones[lefts[0]].head_local - bones[rights[0]].head_local) if lefts and rights else left
        half = max(across.length / 2.0, h * 0.04)
        rig_kit.look(obj, aim.name, "RoundedSquare_Thick", half * 1.6, rig_kit.COLORS["neck"], W["limb"], "Controls",
                     stretch=(1.0, 0.5, 1.0))
        rig_kit.align(obj, aim, x=across.normalized(), z=-ahead)
        for name, track in eyes.items():
            target = PREFIX + "Eye_" + name
            rig_kit.look(obj, target, "Circle_Thick", half * 0.45, rig_kit.tone(side_of(name)), W["digit"], "Controls")
            rig_kit.align(obj, pose[target], x=across.normalized(), z=-ahead)
            look = pose[name].constraints.new('DAMPED_TRACK')
            look.name = rig_kit.PREFIX + "look"
            look.target, look.subtarget = obj, target
            look.track_axis = track
            rig_style.driven(obj, look, "eyes_aim")
    for name in bones.keys():
        if not any(name in armature.collections[g].bones for g in rig_style.COLLECTIONS + (rig_style.FK,) if g in armature.collections):
            rig_style.assign(armature, name, "Mechanics" if name.startswith((rig_kit.MCH, PREFIX)) or name == rig_style.SETTINGS
                             or name.endswith(CTRL) else "Game Bones")
    rig_kit.save(obj, {"follow": {c: b for b, c in controls.items()}, "limbs": limbs,
                       "controls": [c for c in controls.values()] + ["global_ctrl"] + (["body_ctrl"] if body is not None else [])
                       + [l["target"] for l in limbs] + [l["pole"] for l in limbs] + ([PREFIX + "Eyes"] if eyes else [])})
    armature[rig_kit.ON] = True
    obj.show_in_front = True            # controls inside the body stay clickable
    armature[KEY] = True
    bpy.ops.object.mode_set(mode='OBJECT')
    parts = ["spine %d" % len(survey.spine), "legs %d" % sum(1 for l in legs if l.kind == "leg"),
             "arms %d (%d IK)" % (sum(1 for l in survey.limbs if l.kind == "arm"), sum(1 for l in legs if l.kind == "arm")),
             "eyes %d" % len(eyes),
             "wings %d" % sum(1 for l in survey.limbs if l.kind == "wing"), "tail %d" % len(survey.tail)]
    from . import face_board
    if face_board.add(obj, head=survey.head, left=left):
        parts.append("face board")
    return "%s: creature rig (%s)" % (obj.name, ", ".join(parts))
