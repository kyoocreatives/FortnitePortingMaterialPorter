"""The Rig tab's layouts, pure (no bpy): the body picker (controls at their rest places seen from the front, fingers in
a grid beside each hand) and the face board (knobs at their rest places, each moving along its own axes within its
range). Points come in the character's front plane: x to the screen's right (the character's left), z up."""
import re
from collections import namedtuple

Control = namedtuple("Control", "name side role chain x z")       # side L/R/C; role root/body/limb/finger/other
Switch = namedtuple("Switch", "name label side is_ik chain")
Axis = namedtuple("Axis", "index dx dz lo hi")       # local location index; board-plane move per unit; range
Knob = namedtuple("Knob", "bone group x z axes size", defaults=(0.0,))      # size: the knob's radius, board units
# outline: triangles as consecutive (x, z) points, or None; step: a bone board's frame (flipbook sliders land on it)
Board = namedtuple("Board", "kind knobs outline step", defaults=(0.0,))

class PaneView:
    """A pane's own zoom and pan over its fitted layout (pixels about the pane's centre); dots keep their size."""
    MIN, MAX = 0.5, 30.0

    def __init__(self, zoom=1.0, pan_x=0.0, pan_y=0.0):
        self.zoom, self.pan_x, self.pan_y = zoom, pan_x, pan_y

    def apply(self, x, y, cx, cy):
        return cx + (x - cx) * self.zoom + self.pan_x, cy + (y - cy) * self.zoom + self.pan_y

    def unapply(self, x, y, cx, cy):
        return cx + (x - cx - self.pan_x) / self.zoom, cy + (y - cy - self.pan_y) / self.zoom

    def zoom_at(self, x, y, cx, cy, steps):
        """Zoom by 1.2 per step, the point under the mouse (x, y) staying put."""
        qx, qy = self.unapply(x, y, cx, cy)
        self.zoom = min(max(self.zoom * 1.2 ** steps, self.MIN), self.MAX)
        self.pan_x, self.pan_y = x - cx - (qx - cx) * self.zoom, y - cy - (qy - cy) * self.zoom

    def pan(self, dx, dy):
        self.pan_x += dx
        self.pan_y += dy


def snap(value, lo, hi):
    """A knob value on tenths of its range (Ctrl while moving)."""
    step = (hi - lo) / 10.0
    return round(min(max(lo + round((value - lo) / step) * step, lo), hi), 9) if step > 0 else value


FINGERS = ("thumb", "index", "middle", "ring", "pinky")       # rig_source's grid columns
RADIUS = {"root": 9, "body": 8, "limb": 7, "finger": 5, "other": 6}
HOLLOW = ("cog", "root")        # controls that place the body: drawn as rings round what shares their spot
RING_GAP, RING_STEP, RING_BAND = 6, 6, 5        # px: ring to what it holds (past its click slop), ring to ring, band
FINGER_PITCH = 16   # px (UI scale 1) between a hand grid's dots
HAND_GAP = 18       # px between the wrist and its finger grid


def kind_of(control):
    """How the picker draws a control: fk (circle), ik (square), pole (ring), cog / root (hollow), finger, other."""
    low = control.name.lower()
    if control.role in ("finger", "root"):
        return control.role
    if "pole" in low or "_pv" in low:
        return "pole"
    if any(w in low for w in ("body_ctrl", "torso", "cog")):
        return "cog"
    if "_ik" in low or low.startswith("ik_"):
        return "ik"
    return "fk" if control.role in ("body", "limb") else "other"


_JOINT = re.compile(r"_0?([1-3])(_|$)")


def finger_part(name):
    """(finger, joint) of a finger control: joint 0 the metacarpal, 1-3 up the finger; None for what isn't one."""
    low = name.lower()
    finger = next((f for f in FINGERS if f in low), None)
    if finger is None or "curl" in low:
        return finger, None
    if "metacarpal" in low:
        return finger, 0
    m = _JOINT.search(low)
    return finger, int(m.group(1)) if m else None


TWEAKERS = "Tweaker"        # the board group Epic's tweaker head holds (collection "Face Tweaker")


def has_tweakers(board):
    groups = {k.group for k in board.knobs}
    return TWEAKERS in groups and len(groups) > 1


def board_part(board, part):
    """The board's knobs for one head: "face" (Epic's main board), "tweakers", or "all"; its outline cut between
    the two heads (each keeps the triangles on its side)."""
    if part == "all" or not has_tweakers(board):
        return board
    tweak = [k for k in board.knobs if k.group == TWEAKERS]
    face = [k for k in board.knobs if k.group != TWEAKERS]
    keep = tweak if part == "tweakers" else face
    outline = board.outline
    if outline:
        right = sum(k.x for k in tweak) / len(tweak) > sum(k.x for k in face) / len(face)       # the tweakers' side
        mid = ((max(k.x for k in face) + min(k.x for k in tweak)) if right
               else (min(k.x for k in face) + max(k.x for k in tweak))) / 2
        want_right = right == (part == "tweakers")
        tris = [outline[i:i + 3] for i in range(0, len(outline) - 2, 3)]
        outline = [p for t in tris if ((t[0][0] + t[1][0] + t[2][0]) / 3 > mid) == want_right for p in t] or None
    return board._replace(knobs=tuple(keep), outline=outline)


def board_points(board):
    """The board's knobs and the ends of their ranges, in the board plane."""
    points = [(k.x, k.z) for k in board.knobs]
    for k in board.knobs:
        for a in k.axes:
            points += [(k.x + a.dx * a.lo, k.z + a.dz * a.lo), (k.x + a.dx * a.hi, k.z + a.dz * a.hi)]
    return points


def aspect(points):
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return (max(xs) - min(xs)) / max(max(ys) - min(ys), 1e-9) if points else 1.0


def split(width, body_w, face_w, least=0.35):
    """The body pane's share of `width` px: each pane what its content wants at full height, both at least `least`."""
    share = body_w / (body_w + face_w) if body_w + face_w > 0 else 0.5
    return width * min(max(share, least), 1.0 - least)


def _fit(points, box, margin):
    """(scale, offset_x, offset_y) putting points' bounding box centred in box (x0, y0, x1, y1), aspect kept."""
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    span_x, span_y = max(xs) - min(xs), max(ys) - min(ys)
    room_x, room_y = max(1.0, box[2] - box[0] - 2 * margin), max(1.0, box[3] - box[1] - 2 * margin)
    scales = [room_x / span_x if span_x > 1e-9 else None, room_y / span_y if span_y > 1e-9 else None]
    scale = min((s for s in scales if s is not None), default=1.0)
    cx, cy = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
    return scale, (box[0] + box[2]) / 2 - cx * scale, (box[1] + box[3]) / 2 - cy * scale


class Picker:
    """`hidden`: the rig's inactive chain, placed all the same (nothing moves when IK/FK flips) but not clickable;
    `extent`: the body's silhouette (x0, z0, x1, z1), fitted in with the controls."""

    def __init__(self, controls, width, height, scale=1.0, hidden=(), view=None, extent=None):
        self.width, self.height, self.ui = width, height, scale
        self.ghost = {c.name for c in controls if c.name in hidden}
        self.palm = {c.name for c in controls if c.role == "finger" and c.z < 0}       # rig_source's palm row
        self.hollow = set()     # rings round a spot: clicked on their band
        fingers = [c for c in controls if c.role == "finger"]
        body = [c for c in controls if c.role != "finger"]
        self.place, self.body_fit, self.extent = {}, None, extent
        # past the controls: their dots, the bones' tips and the IK/FK switches beside the limbs
        margin = (max(RADIUS.values()) + 24) * scale
        pitch = FINGER_PITCH * scale
        hand_w = (len(FINGERS) - 1) * pitch + HAND_GAP * scale + RADIUS["finger"] * scale
        side_room = hand_w if fingers and width - 2 * hand_w > 260 * scale else 0.0     # else the grids go under the hands
        if body:
            self.body_fit = self._lay(body, (side_room, 0.0, width - side_room, height), margin)
        for side in "RL":
            hand = [c for c in fingers if c.side == side]
            if hand:
                self._hand(hand, side, body, pitch, beside=side_room > 0)
        if view is not None:
            cx, cy = width / 2, height / 2
            self.place = {n: view.apply(x, y, cx, cy) + (r,) for n, (x, y, r) in self.place.items()}
            if self.body_fit:
                s, ox, oy = self.body_fit
                self.body_fit = (s * view.zoom,) + view.apply(ox, oy, cx, cy)

    def _hand(self, hand, side, body, pitch, beside):
        """The hand's finger grid (rig_source's columns and joint rows, fingertips down as the arm hangs) beside its
        wrist, outboard; under it when the pane is narrow."""
        wrists = [self.place[c.name] for c in body if c.side == side and c.name in self.place and "hand" in c.name.lower()]
        out = -1 if side == "R" else 1
        if wrists:
            wx = (min if side == "R" else max)(w[0] for w in wrists)
            wy = sum(w[1] for w in wrists) / len(wrists)
            if beside and self.extent and self.body_fit:       # past the hand's own silhouette (the fingers)
                k, ox, _ = self.body_fit
                edge = ox + (self.extent[0] if side == "R" else self.extent[2]) * k
                wx = min(wx, edge) if side == "R" else max(wx, edge)
        else:       # no hand control: the grids sit low on their side
            wx, wy = self.width / 2 + out * self.width / 4, self.height * 0.4
        cols = len(FINGERS) - 1
        z_top = min(c.z for c in hand)      # the palm row (curls, spreads) when there is one
        if beside:
            left = wx + HAND_GAP * self.ui if out > 0 else wx - HAND_GAP * self.ui - cols * pitch
            top = wy + pitch
        else:
            left, top = wx - cols * pitch / 2, wy - HAND_GAP * self.ui
        left = min(max(left, 4 * self.ui), self.width - cols * pitch - 4 * self.ui)
        r = RADIUS["finger"] * self.ui
        for c in hand:
            self.place[c.name] = (left + c.x * pitch, top - (c.z - z_top) * pitch, r)

    def _lay(self, controls, box, margin):
        points = [(c.x, c.z) for c in controls]
        if self.extent:
            points += [self.extent[:2], self.extent[2:]]
        s, ox, oy = _fit(points, box, margin)
        spots = {}
        for c in controls:
            x, y = ox + c.x * s, oy + c.z * s
            spots.setdefault((round(x), round(y)), []).append((c, x, y))
        for stack in spots.values():
            self._stack(stack)
        return s, ox, oy

    def _stack(self, stack):
        """Controls on one spot: the solid ones side by side, centred (each clickable); the hollow ones that place
        things (COG, root, global) rings round them, nested outward, centred; the inactive chain's on the spot."""
        live = [e for e in stack if e[0].name not in self.ghost]
        solid = [e for e in live if kind_of(e[0]) not in HOLLOW]
        rings = sorted((e for e in live if kind_of(e[0]) in HOLLOW), key=lambda e: "global" in e[0].name.lower())     # global outermost
        step = 2 * max([RADIUS.get(c.role, 6) for c, _, _ in solid] or [6]) * self.ui + 2
        for c, x, y in stack:
            self.place[c.name] = (x, y, RADIUS.get(c.role, 6) * self.ui)
        for i, (c, x, y) in enumerate(solid):
            self.place[c.name] = (x + (i - (len(solid) - 1) / 2) * step, y, RADIUS.get(c.role, 6) * self.ui)
        inner = ((len(solid) - 1) * step / 2 + max(RADIUS.get(c.role, 6) for c, _, _ in solid) * self.ui
                 + RING_GAP * self.ui) if solid else 0.0
        for k, (c, x, y) in enumerate(rings):
            r = max(inner, RADIUS.get(c.role, 6) * self.ui * 1.3) + k * RING_STEP * self.ui
            self.place[c.name] = (x, y, r)
            self.hollow.add(c.name)

    def hit(self, x, y):
        """The solid control under (x, y), else the ring whose band it's on."""
        best, name, ring = None, None, None
        for n, (px, py, r) in self.place.items():
            if n in self.ghost:
                continue
            d = ((px - x) ** 2 + (py - y) ** 2) ** 0.5
            if n in self.hollow:
                if r - RING_BAND * self.ui <= d <= r + 3 * self.ui:
                    ring = n
            elif d <= r + 3 * self.ui and (best is None or d < best):
                best, name = d, n
        return name or ring

    def in_box(self, x0, y0, x1, y1):
        lo_x, hi_x, lo_y, hi_y = min(x0, x1), max(x0, x1), min(y0, y1), max(y0, y1)
        return [n for n, (px, py, _) in self.place.items()
                if n not in self.ghost and lo_x <= px <= hi_x and lo_y <= py <= hi_y]


class FaceBoard:
    KNOB = 8

    def __init__(self, board, width, height, scale=1.0):
        self.board, self.ui = board, scale
        self.knobs = {k.bone: k for k in board.knobs}
        points = board_points(board)
        self.width, self.height = width, height
        # the knobs and their ranges fill the pane: the outline's empty frame may run past it
        self.fit = _fit(points or [(0.0, 0.0)], (0.0, 0.0, width, height), 16 * scale)
        self.set_view(None)

    def set_view(self, view):
        """Zoom and pan this pane (None: the plain fit)."""
        s, ox, oy = self.fit
        if view is not None:
            s, (ox, oy) = s * view.zoom, view.apply(ox, oy, self.width / 2, self.height / 2)
        self.scale, self.ox, self.oy = s, ox, oy
        self.rest = {k.bone: self.to_pane(k.x, k.z) for k in self.board.knobs}

    def to_pane(self, x, z):
        return self.ox + x * self.scale, self.oy + z * self.scale

    def knob_at(self, bone, values):
        k, (x, y) = self.knobs[bone], self.rest[bone]
        for a in k.axes:
            v = values.get(a.index, 0.0)
            x, y = x + a.dx * v * self.scale, y + a.dz * v * self.scale
        return x, y

    def values_at(self, bone, x, y):
        """The knob's values that bring it nearest to (x, y), each within its range."""
        k, (rx, ry) = self.knobs[bone], self.rest[bone]
        dx, dz = (x - rx) / self.scale, (y - ry) / self.scale
        axes = k.axes
        if len(axes) == 2:
            a, b = axes
            det = a.dx * b.dz - a.dz * b.dx
            if abs(det) > 1e-12:
                t = ((dx * b.dz - dz * b.dx) / det, (a.dx * dz - a.dz * dx) / det)
                return {ax.index: min(max(v, ax.lo), ax.hi) for ax, v in zip(axes, t)}
        out = {}
        for a in axes:
            length = a.dx * a.dx + a.dz * a.dz
            t = (dx * a.dx + dz * a.dz) / length if length > 1e-12 else 0.0
            step = self.board.step if self.board.kind == "bones" and len(axes) == 1 else 0.0
            if step > 0:        # a flipbook slider: whole frames only
                t = a.lo + round((t - a.lo) / step) * step
            out[a.index] = min(max(t, a.lo), a.hi)
        return out

    def hit(self, x, y, values):
        """The knob under (x, y), drawn where `values` ({bone: {index: value}}) put it."""
        best, name = None, None
        for bone in self.knobs:
            kx, ky = self.knob_at(bone, values.get(bone, {}))
            d = (kx - x) ** 2 + (ky - y) ** 2
            if d <= ((self.KNOB + 3) * self.ui) ** 2 and (best is None or d < best):
                best, name = d, bone
        return name


def knob_label(board, knob, values):
    """A knob's name on the board; a bone board's slider says its frame ("Mouth 5 / 15"), a pad says so."""
    name = knob.bone[3:] if knob.bone.startswith("MB_") else knob.bone.replace("CR_Face_", "")
    if board.kind != "bones":
        return name
    if name.endswith("_UV"):
        return name[:-3] + " pad"
    name = name[:-4] if name.endswith("Pose") and len(name) > 4 else name
    if len(knob.axes) == 1 and board.step > 0:
        a = knob.axes[0]
        frame = round((values.get(a.index, 0.0) - a.lo) / board.step) + 1
        return "%s %d / %d" % (name, frame, round((a.hi - a.lo) / board.step) + 1)
    return name


_TWIN = (re.compile(r"_L_"), "_R_"), (re.compile(r"_R_"), "_L_")


def mirror_of(bone):
    """The left/right twin's name (MetaHuman `_L_`/`_R_`, flipbook `...L`/`...R`); None for a centre control."""
    for pattern, repl in _TWIN:
        if pattern.search(bone):
            return pattern.sub(repl, bone, count=1)
    m = re.match(r"(.*[a-z])([LR])$", bone)
    if m:
        return m.group(1) + ("R" if m.group(2) == "L" else "L")
    return None
