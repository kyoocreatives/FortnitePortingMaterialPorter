"""Control rig for a creature armature (wolf, chicken, raptor, LEGO cow...), like Tasty's for the player.

Creature skeletons share no bone names (Battle Royale: QuadSpine_A_Pelvis_C, PawedLeg_A_Thigh_L;
LEGO Fortnite: pelvis, legB_01_l), so the rig reads the bone tree: the spine from pelvis to head,
side bones under centre bones as limbs, limbs reaching the ground as legs, the tail and the face.
Legs and arms with a hand get IK (foot/hand control plus pole), eyes track a control in front of
the face, everything else is FK with custom shapes. The original bones keep their names, rest pose
and hierarchy, so animations still play on them (turn the legs' IK off first).
"""

import re
from math import pi

import bpy
from mathutils import Matrix, Vector

from . import rig_shapes, rig_style

KEY = "is_creature_rig"
PREFIX = "CR_"          # prefix of the rig's own bones: CR_IK_<foot>, CR_Pole_<limb>

SIDE = re.compile(r"(^|[_.\-\s])(l|left)([_.\-\s]|$)|(^|[_.\-\s])(r|right)([_.\-\s]|$)", re.IGNORECASE)
FOOT = re.compile(r"^(foot|ankle|wrist|paw|hoof)[a-z]?$")      # matches a name word: footA, Ankle; not PawedArm
HEAD = re.compile(r"(^|_)head(_c|_jnt|_\d+)?$", re.IGNORECASE)          # includes head_01 (sidekicks)
HAND = re.compile(r"^(hand|wrist|paw)[a-z]?$")                            # an arm's IK end, matched per name word
EYE = re.compile(r"(^|_)eye(ball)?(_|$)", re.IGNORECASE)                  # eye_l, Eyeball_R; not eyelid, eyebrow
PELVIS = re.compile(r"pelvis|hips", re.IGNORECASE)
TAIL = re.compile(r"tail", re.IGNORECASE)
SHOULDER_BLADE = re.compile(r"clavicle|scapula", re.IGNORECASE)
HELPER = re.compile(r"^(ik_|fx_|vb |.*socket)|attach|null$|_effect$|twist|_end$|^interact$", re.IGNORECASE)


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
        self.foot = None            # bone the IK target copies
        self.helpers = []           # CR_MCH_<bone> chain IK solves when the bones don't meet

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
        self.tail = self._tail()
        self.limbs = self._limbs(edit_bones)

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
                kind = "leg" if low < self.ground + 0.2 * self.height and len(chain) >= 3 else "arm"
            limb = Limb(chain, kind)
            if kind == "leg":
                self._leg(limb)
            if limb.kind == "arm":
                self._arm(limb)
            limbs.append(limb)
        return limbs

    def _leg(self, limb):
        """Leg IK chain: first bone (after a clavicle/scapula) down to the bone above the foot."""
        names = limb.bones
        start = 1 if SHOULDER_BLADE.search(names[0]) and len(names) > 3 else 0
        foot = next((i for i in range(start + 1, len(names)) if any(FOOT.match(w.lower()) for w in re.split(r"[_\d]+", names[i]))), None)
        if foot is None:
            # no foot name: first bone touching the ground (claw tip, toe), else the one above the toe
            touching = [i for i in range(start + 2, len(names)) if self.bones[names[i]].head.z < self.ground + 0.06 * self.height]
            foot = touching[0] if touching else (len(names) - 2 if len(names) - start >= 3 else None)
        if foot is None or foot - start < 2:
            limb.kind = "arm"       # too short to bend: FK
            return
        limb.chain, limb.foot = names[start:foot], names[foot]

    def _arm(self, limb):
        """Arm IK chain if there is a hand: first bone (after a clavicle) to the bone above the hand."""
        names = limb.bones
        start = 1 if SHOULDER_BLADE.search(names[0]) else 0
        hand = next((i for i in range(start + 2, len(names)) if any(HAND.match(w.lower()) for w in re.split(r"[_\d]+", names[i]))), None)
        if hand is not None:
            limb.chain, limb.foot = names[start:hand], names[hand]


def _pole_angle(base, tip, pole):
    """Pole angle that keeps the chain's rest pose (base: first edit bone, tip: the foot's head)."""
    def signed(a, b, normal):
        angle = a.angle(b)
        return -angle if a.cross(b).angle(normal) < 1.0 else angle
    normal = (tip - base.head).cross(pole - base.head)
    axis = normal.cross(base.tail - base.head)
    return signed(base.x_axis, axis, base.tail - base.head)


def align_shape(obj, pose_bone, x=None, y=None, z=None):
    """Rotate a control's shape so its axes point along x/y/z (armature space; a ring lies in XZ,
    so y=up lays it flat). Missing axes complete a right-handed frame."""
    axes = [x, y, z]
    if axes.count(None) == 2:       # one given: any frame with it
        i = next(i for i in range(3) if axes[i] is not None)
        a = axes[i].normalized()
        other = Vector((1, 0, 0)) if abs(a.x) < 0.9 else Vector((0, 1, 0))
        b = a.cross(other).normalized()
        axes = [None] * 3
        axes[i], axes[(i + 1) % 3], axes[(i + 2) % 3] = a, b, a.cross(b)
    elif None in axes:
        i = axes.index(None)
        axes[i] = axes[(i + 1) % 3].cross(axes[(i + 2) % 3])
    want = Matrix([axes[0], axes[1], axes[2]]).transposed()      # columns are the shape's axes in armature space
    rest = obj.data.bones[pose_bone.name].matrix_local.to_3x3()
    pose_bone.custom_shape_rotation_euler = (rest.inverted() @ want).to_euler()


def create(obj):
    """Rig the armature object. Returns what it found, in a line."""
    armature = obj.data
    if armature.get(KEY):
        return "%s: already has a creature rig" % obj.name
    view_layer = bpy.context.view_layer
    for o in view_layer.objects:
        o.select_set(False)
    view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    edit = armature.edit_bones
    survey = Survey(edit)
    legs = [l for l in survey.limbs if l.chain]         # includes arms with a hand
    # Facing: pelvis to head projected on the ground, else -Y (UE forward).
    ahead = Vector((0.0, -1.0, 0.0))
    if survey.head and survey.pelvis:
        towards = survey.bones[survey.head].head - survey.bones[survey.pelvis].head
        towards.z = 0.0
        if towards.length > 1e-3 * survey.height:
            ahead = towards.normalized()

    # IK targets (copy of the foot under the root; for arms, the hand under the chest) and knee/elbow poles.
    poles = {}
    for leg in legs:
        foot, base, tip = edit[leg.foot], edit[leg.chain[0]], edit[leg.chain[-1]]
        holder = edit.get(survey.root) if leg.kind == "leg" else edit[leg.bones[0]].parent
        target = edit.new(PREFIX + "IK_" + leg.foot)
        target.head, target.tail, target.roll = foot.head.copy(), foot.tail.copy(), foot.roll
        target.parent = holder
        target.use_deform = False
        length = sum(edit[n].length for n in leg.chain)
        # The bend is the joint furthest off the hip-to-foot line; the pole goes out from it.
        line = foot.head - base.head
        off = lambda p: p - (base.head + line * ((p - base.head).dot(line) / max(line.length_squared, 1e-9)))
        middle = max([edit[n].head for n in leg.chain[1:]] or [base.tail], key=lambda p: off(p).length)
        bend = off(middle)
        if bend.length < 2e-3 * length and len(leg.chain) > 1:
            # Straight leg (LEGO wolf): IK can't pick a bend direction, so nudge the knee 1% of the leg
            # off the line (hind knee forward, front elbow back). The mesh doesn't move at rest.
            front = leg.kind == "arm" or (survey.pelvis and survey.head and (base.head - survey.bones[survey.pelvis].head).dot(ahead)
                                          > 0.5 * (survey.bones[survey.head].head - survey.bones[survey.pelvis].head).dot(ahead))
            bend = off(middle + (-ahead if front else ahead))
            if bend.length < 1e-6:
                bend = base.x_axis.copy()
            knee = edit[leg.chain[len(leg.chain) // 2]]
            nudge = bend.normalized() * length * 0.01
            if knee.parent is not None and knee.parent.name in leg.chain:
                knee.parent.tail = knee.parent.tail + nudge
            knee.head = knee.head + nudge if not knee.use_connect else knee.head
            middle = knee.head.copy()
            bend = off(middle)
        if bend.length < 1e-4 * length:
            bend = base.z_axis.copy()
        location = middle + bend.normalized() * length * 0.4      # close, so the knee points at it
        pole = edit.new(PREFIX + "Pole_" + leg.name)
        pole.head, pole.tail = location, location + Vector((0, 0, 0.1 * length))
        pole.parent = holder
        pole.use_deform = False
        poles[leg.name] = _pole_angle(base, foot.head, location)
        # Bones that don't meet (sidekick arm: gap between a tail and the next head) make Blender's IK
        # straighten the chain, since it uses each bone's own length. Solve a joint-to-joint chain
        # (CR_MCH_<bone>) instead and have the bones follow it.
        joints = [edit[n].head.copy() for n in leg.chain] + [foot.head.copy()]
        if any((edit[a].tail - b).length > 1e-3 * length for a, b in zip(leg.chain, joints[1:])):
            parent = edit[leg.chain[0]].parent
            for i, name in enumerate(leg.chain):
                mch = edit.new(PREFIX + "MCH_" + name)
                mch.head, mch.tail = joints[i], joints[i + 1]
                mch.align_roll(edit[name].z_axis)
                mch.parent = parent if i == 0 else edit[leg.helpers[-1]]
                mch.use_connect, mch.use_deform = i > 0, False
                follow = edit.new(PREFIX + "Follow_" + name)      # the bone's rest placement, parented to the helper
                follow.head, follow.tail, follow.roll = edit[name].head.copy(), edit[name].tail.copy(), edit[name].roll
                follow.parent, follow.use_deform = mch, False
                leg.helpers.append(mch.name)
            poles[leg.name] = _pole_angle(edit[leg.helpers[0]], foot.head, location)

    # Eyes track a control in front of the face. Each tracks a target down its own most-forward
    # axis, so there is no turn at rest.
    eyes = {}
    head_subtree = {c.name for c in survey.bones[survey.head].children_recursive} if survey.head else set()
    for name in sorted(n for n in head_subtree if EYE.search(n) and side_of(n) != "C" and not HELPER.search(n)):
        eye = edit[name]
        best = max(range(3), key=lambda i: abs(eye.matrix.col[i].to_3d().normalized().dot(ahead)))
        axis = eye.matrix.col[best].to_3d().normalized()
        sign = 1.0 if axis.dot(ahead) >= 0 else -1.0
        eyes[name] = ("TRACK_" + ("" if sign > 0 else "NEGATIVE_") + "XYZ"[best], axis * sign)
    if eyes:
        spread = [edit[n].head for n in eyes]
        across = max(((a - b).length for a in spread for b in spread), default=0.0)
        reach = max(survey.height * 0.6, across * 3.0)
        middle = sum(spread, Vector()) / len(spread)
        aim = edit.new(PREFIX + "Eyes")
        aim.head = middle + ahead * reach
        aim.tail = aim.head + Vector((0.0, 0.0, survey.height * 0.05))
        aim.parent, aim.use_deform = edit[survey.head], False
        for name, (track, axis) in list(eyes.items()):
            # target down the eye's axis, as far ahead as the control
            at = edit[name].head + axis * (reach / max(axis.dot(ahead), 0.2))
            target = edit.new(PREFIX + "Eye_" + name)
            target.head, target.tail = at, at + Vector((0.0, 0.0, survey.height * 0.03))
            target.parent, target.use_deform = aim, False
            eyes[name] = track
    # settings gear on the ground beside the left flank, halfway along
    left = Vector((0.0, 0.0, 1.0)).cross(ahead)
    usable = [b for b in edit if not HELPER.search(b.name) and not b.name.startswith(PREFIX)]
    across = max((abs(b.head.dot(left)) for b in usable), default=survey.size * 0.2)
    along = [b.head.dot(ahead) for b in usable] or [0.0]
    spot = ahead * (max(along) + min(along)) / 2.0 + left * (across + survey.size * 0.25)
    spot.z = survey.ground
    gear = edit.new(rig_style.SETTINGS)
    gear.head, gear.tail = spot, spot + ahead * survey.size * 0.1
    gear.parent, gear.use_deform = edit.get(survey.root), False
    bpy.ops.object.mode_set(mode='POSE')

    rig_style.collections(armature, fk=bool(legs))
    pose = obj.pose.bones
    h = survey.size
    up = Vector((0.0, 0.0, 1.0))
    forward = ahead
    RING = (pi / 2.0, 0.0, 0.0)            # a kit circle lies in XY; this stands it around the bone's Y axis

    def show(name, group, shape, size, role=None, secondary=False, ring=False):
        """A control: unit kit shape at `size` metres (radius or half-size), the kit's colour and width, its group."""
        if name not in pose:
            return
        bone = pose[name]
        bone.custom_shape = rig_shapes.ensure(shape)
        bone.use_custom_shape_bone_size = False
        bone.custom_shape_scale_xyz = (size, size, size)
        if ring:
            bone.custom_shape_rotation_euler = RING
        rig_style.style(bone, role or side_of(name), secondary=secondary)
        rig_style.assign(armature, name, group)

    if survey.root:
        show(survey.root, "Controls", "CR_CircleArrow", h * 0.65, role="main")
        align_shape(obj, pose[survey.root], y=forward, z=up)        # flat on the ground, chevron ahead
    for name in survey.spine:
        show(name, "Controls", "CR_Circle", h * (0.15 if name != survey.head else 0.11), ring=True)
    if survey.pelvis in pose:
        show(survey.pelvis, "Controls", "CR_Square", h * 0.22)
        align_shape(obj, pose[survey.pelvis], y=forward, z=up)
    # Bones between the spine and a limb or the tail (e.g. a hips bone the rear legs hang from).
    spine = set(survey.spine)
    for root in [l.bones[0] for l in survey.limbs if l.kind != "face"] + survey.tail[:1]:
        at = armature.bones[root].parent
        while at is not None and at.name not in spine and at.name != survey.root and side_of(at.name) == "C":
            show(at.name, "Controls", "CR_Circle", h * 0.15, ring=True)
            at = at.parent
    for i, name in enumerate(survey.tail):
        show(name, "Controls", "CR_Circle", h * max(0.07 - 0.006 * i, 0.03), ring=True)
    ik_driven = {n for leg in legs for n in leg.chain + [leg.foot]}
    for limb in survey.limbs:
        for name in limb.bones:
            if name in ik_driven:
                show(name, rig_style.FK, "CR_Circle", h * (0.035 if name == limb.foot else 0.055), ring=True)
            elif limb.kind == "face":
                show(name, "Secondary", "CR_Diamond", h * 0.018, secondary=True)
            elif limb.kind == "wing":
                show(name, "Secondary", "CR_Circle", h * 0.055, secondary=True, ring=True)
            elif limb.chain and limb.bones.index(name) < limb.bones.index(limb.chain[0]):
                # before the IK chain: shoulder blade, clavicle
                show(name, "Secondary", "CR_Circle", h * 0.035, secondary=True, ring=True)
            elif limb.foot and limb.bones.index(name) > limb.bones.index(limb.foot):
                # past the foot or hand: toes, claws, fingers
                show(name, "Secondary", "CR_Circle", h * 0.02, secondary=True, ring=True)
            else:
                show(name, "Controls", "CR_Circle", h * (0.035 if name == limb.foot or name in limb.bones[-2:] else 0.055), ring=True)
        if limb.kind != "face":
            # rest of the limb: fingers, toes, scapula
            for child in armature.bones[limb.bones[0]].children_recursive:
                if child.name not in limb.bones and not HELPER.search(child.name):
                    show(child.name, "Secondary", "CR_Circle", h * 0.02, secondary=True, ring=True)
    # Centre bones off the spine not shown yet (a fish's body behind its head).
    placed = lambda n: any(n in armature.collections[g].bones for g in armature.collections.keys()
                           if g in rig_style.COLLECTIONS + (rig_style.FK,))
    if survey.pelvis:
        head_bones = ({c.name for c in armature.bones[survey.head].children_recursive} | {survey.head}) if survey.head else set()
        for bone in armature.bones[survey.pelvis].children_recursive:
            if not placed(bone.name) and bone.name not in head_bones and side_of(bone.name) == "C" \
                    and bone.use_deform and not HELPER.search(bone.name):
                show(bone.name, "Controls", "CR_Circle", h * 0.1, ring=True)
    if survey.head:
        for child in armature.bones[survey.head].children_recursive:
            if placed(child.name) or HELPER.search(child.name) or child.name.startswith(PREFIX):
                continue
            show(child.name, "Secondary", "CR_Diamond", h * 0.018, secondary=True)

    settings = [("ik_" + leg.name, 1.0, "IK on this limb (0: FK, as an animation plays it)") for leg in legs]
    if eyes:
        settings.append(("eyes_aim", 1.0, "The eyes look at CR_Eyes"))
    rig_style.add_settings(obj, settings)
    show(rig_style.SETTINGS, "Controls", "CR_Gear", h * 0.08, role="settings")
    align_shape(obj, pose[rig_style.SETTINGS], y=forward, z=up)
    pose[rig_style.SETTINGS].lock_location = pose[rig_style.SETTINGS].lock_rotation = (True, True, True)
    pose[rig_style.SETTINGS].lock_scale = (True, True, True)
    for leg in legs:
        prop = "ik_" + leg.name
        target_name, pole_name = PREFIX + "IK_" + leg.foot, PREFIX + "Pole_" + leg.name
        if leg.kind == "arm":
            show(target_name, "Controls", "CR_Box", h * 0.03, role=side_of(leg.foot))
        else:
            # footprint on the ground under the foot, facing the creature's direction
            show(target_name, "Controls", "CR_Foot", h * 0.3, role=side_of(leg.foot))
            align_shape(obj, pose[target_name], y=forward, z=up)
            foot = armature.bones[leg.foot].head_local
            rig_shapes.place(obj, pose[target_name], Vector((foot.x, foot.y, survey.ground)))
        show(pole_name, "Controls", "CR_Diamond", h * 0.03, role=side_of(leg.foot))
        if leg.helpers:
            # IK on the helper chain (its tail is the foot's head); the real bones follow it
            ik = pose[leg.helpers[-1]].constraints.new('IK')
            ik.name = "CR IK"
            ik.target, ik.subtarget = obj, target_name
            ik.pole_target, ik.pole_subtarget = obj, pole_name
            ik.pole_angle = poles[leg.name]
            ik.chain_count = len(leg.helpers)
            for name in leg.chain:
                copy = pose[name].constraints.new('COPY_TRANSFORMS')
                copy.name = "CR IK"
                copy.target, copy.subtarget = obj, PREFIX + "Follow_" + name
                rig_style.driven(obj, copy, prop)
        else:
            # IK on the foot with use_tail off, so its head is the chain tip
            # (a reoriented bone's tail needn't meet its child's head)
            ik = pose[leg.foot].constraints.new('IK')
            ik.name = "CR IK"
            ik.use_tail = False
            ik.target, ik.subtarget = obj, target_name
            ik.pole_target, ik.pole_subtarget = obj, pole_name
            ik.pole_angle = poles[leg.name]
            ik.chain_count = len(leg.chain)     # the foot isn't a segment; its head is the tip
            rig_style.driven(obj, ik, prop)
        turn = pose[leg.foot].constraints.new('COPY_ROTATION')
        turn.name = "CR IK Foot"
        turn.target, turn.subtarget = obj, target_name
        rig_style.driven(obj, turn, prop)
    if eyes:
        aim = pose[PREFIX + "Eyes"]
        lefts = [n for n in eyes if side_of(n) == "L"]
        rights = [n for n in eyes if side_of(n) == "R"]
        across = (armature.bones[lefts[0]].head_local - armature.bones[rights[0]].head_local) if lefts and rights else forward.cross(up)
        half = max(across.length / 2.0, h * 0.04)
        show(aim.name, "Controls", "CR_Glasses", half, role="C")
        align_shape(obj, aim, x=across.normalized(), y=forward)          # rings face forward
        for name, track in eyes.items():
            look = pose[name].constraints.new('DAMPED_TRACK')
            look.name = "CR Look"
            look.target, look.subtarget = obj, PREFIX + "Eye_" + name
            look.track_axis = track
            rig_style.driven(obj, look, "eyes_aim")
    for name in armature.bones.keys():
        if not placed(name):
            rig_style.assign(armature, name, "Mechanics" if name.startswith(PREFIX) else "Game Bones")
    obj.show_in_front = True            # controls inside the body stay clickable
    armature[KEY] = True
    bpy.ops.object.mode_set(mode='OBJECT')
    parts = ["spine %d" % len(survey.spine), "legs %d" % sum(1 for l in legs if l.kind == "leg"),
             "arms %d (%d IK)" % (sum(1 for l in survey.limbs if l.kind == "arm"), sum(1 for l in legs if l.kind == "arm")),
             "eyes %d" % len(eyes),
             "wings %d" % sum(1 for l in survey.limbs if l.kind == "wing"), "tail %d" % len(survey.tail)]
    from . import face_board
    if face_board.add(obj, head=survey.head, left=Vector((0.0, 0.0, 1.0)).cross(ahead)):
        parts.append("face board")
    return "%s: creature rig (%s)" % (obj.name, ", ".join(parts))
