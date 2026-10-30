"""The editor's Rig tab: the scene's rigged characters in the list column; in the well, the shown character's body
controls as a picker (seen from the front, fingers in hand insets, an IK/FK pill by each switched limb) and its face
board with draggable knobs, side by side. It works on the scene alone: the active object's armature (the last one
used when the active object has none). The rig's keyable switches stay Blender panels in the Options drawer."""
import re

import bpy
import gpu
from bpy.app.handlers import persistent
from gpu_extras.batch import batch_for_shader

from . import host, rig_layout, rig_source, tabs
from .ui import blender as ui_blender, paint
from .ui.rect import Rect

SIDE_COLORS = {"L": (0.35, 0.62, 1.0), "R": (1.0, 0.38, 0.38), "C": (1.0, 0.82, 0.25)}     # Blender's bone sides
KNOB_COLOR = (1.0, 0.8, 0.1)        # Epic's board knobs
KINDS = {"official": "Official", "tasty": "Tasty", "kit": "Kit"}
_BONE_PATH = re.compile(r'pose\.bones\["(.+?)"\]')
LIMB_WORDS = {"arm": ("upperarm", "lowerarm", "hand"), "leg": ("thigh", "calf", "foot", "ball"),
              "spine": ("spine", "chest", "hips"), "neck": ("neck", "head"), "hand": ("hand",), "foot": ("foot",)}

# arm: the armature shown last; cache: {armature name: (key, Rig, {(w, h): FaceBoard}, outline batch)}
RIG = {"arm": None, "cache": {}, "views": {}, "hover": {}, "box": {}, "panes": {}, "keys": None}      # panes: {(area, pane): PaneView}


def usable(arm, view_layer):
    """An armature the tab can make active and pose: in the view layer, shown, editable (not linked)."""
    return (arm is not None and arm.name in view_layer.objects and not arm.hide_viewport
            and getattr(arm, "is_editable", arm.library is None))


def armature(context):
    """The armature the tab shows: the active object's, else the last one shown."""
    layer = context.view_layer
    found = rig_source.armature_of(layer.objects.active)
    if found is not None and usable(found, layer):
        RIG["arm"] = found.name
        return found
    arm = bpy.data.objects.get(RIG["arm"] or "")
    return arm if arm is not None and arm.type == 'ARMATURE' and usable(arm, layer) else None


def rig_of(arm):
    """The armature's Rig read once (again when its bones change)."""
    key = (hash(tuple(arm.data.bones.keys())), arm.data.get("fpmp_official_rig"), arm.data.get("fpmp_metahuman_board"),
           arm.data.get("is_tasty"), arm.data.get("fpmp_rig_kit"))
    entry = RIG["cache"].get(arm.name)
    if entry is None or entry["key"] != key:
        entry = {"key": key, "rig": rig_source.read(arm), "boards": {}}
        RIG["cache"][arm.name] = entry
    return entry


def rigged(context):
    """The scene's armatures the tab can show: usable, with body controls or a face board."""
    layer = context.view_layer
    return [o for o in context.scene.objects if o.type == 'ARMATURE' and usable(o, layer) and rig_of(o)["rig"].kind != "none"]


def _has_controls(arm):
    rig = rig_of(arm)["rig"]
    return bool(rig.controls) or rig.board is not None


def values_of(arm, bone):
    return rig_source.knob_values(arm, bone)


def curves(arm):
    """The armature's action's F-curves (layered actions too)."""
    action = arm.animation_data and arm.animation_data.action
    if action is None:
        return []
    out = list(getattr(action, "fcurves", ()) or ())
    for layer in getattr(action, "layers", ()):
        for strip in layer.strips:
            for bag in getattr(strip, "channelbags", ()):
                out += list(bag.fcurves)
    return out


def keyed(arm, data_path):
    """Whether the armature's action has a curve on data_path."""
    return any(c.data_path == data_path for c in curves(arm))


def shown_areas():
    return [area for window in bpy.context.window_manager.windows for area in window.screen.areas
            if host.is_editor_area(area) and host.tab_id(area.spaces.active) == "rig"]


def redraw_main():
    for area in shown_areas():
        for region in area.regions:
            if region.type == 'WINDOW':
                region.tag_redraw()


# ------------------------------------------------------------------ drawing

def _draw(kind, pos, col, width=1.0):
    if not pos:
        return
    shader = gpu.shader.from_builtin('SMOOTH_COLOR')
    gpu.state.blend_set('ALPHA')
    if width != 1.0:
        gpu.state.line_width_set(width)
    batch_for_shader(shader, kind, {"pos": pos, "color": col}).draw(shader)
    if width != 1.0:
        gpu.state.line_width_set(1.0)


def _alpha(color, a):
    return tuple(color[:3]) + (a,)


def _lighter(color, k):
    return tuple(c + (1.0 - c) * k for c in color[:3]) + (color[3] if len(color) > 3 else 1.0,)


def _bone_of(data_path):
    m = _BONE_PATH.match(data_path)
    return m.group(1) if m else None


def key_state(arm, frame):
    """(bones with curves, bones keyed on this frame), worked out again when the action, frame or key count changes."""
    fcs = curves(arm)
    action = arm.animation_data and arm.animation_data.action
    key = (action.as_pointer() if action else 0, frame, sum(len(c.keyframe_points) for c in fcs))
    if RIG["keys"] is None or RIG["keys"][0] != key:
        animated, now = set(), set()
        for c in fcs:
            bone = _bone_of(c.data_path)
            if bone is None:
                continue
            animated.add(bone)
            if bone not in now and any(abs(k.co[0] - frame) < 0.5 for k in c.keyframe_points):
                now.add(bone)
        RIG["keys"] = (key, animated, now)
    return RIG["keys"][1], RIG["keys"][2]


def _shape(rects, kind, x, y, r, color, s, ring=0.0, out=0.0):
    """A control's shape at (x, y), r its half size: circles for FK and fingers, a square for IK effectors; `ring`: only
    a band that wide inside its edge; `out`: grown that much all round, corners too (rings round it stay concentric)."""
    if kind == "ik":
        rects.add(Rect(x - r, y - r, 2 * r, 2 * r).pad(-out), color, 6 * s + out, ring=ring)
    elif kind == "palm":        # hand-wide (curl, spread, cup): a pill on the palm
        rects.add(Rect(x - 1.4 * r, y - 0.8 * r, 2.8 * r, 1.6 * r).pad(-out), color, 0.8 * r + out, ring=ring)
    elif kind == "root":        # a pill on the ground, its ends round: nested ones stay concentric
        rects.add(Rect(x - r - 8 * s, y - r, 2 * r + 16 * s, 2 * r).pad(-out), color, r + out, ring=ring)
    else:
        rects.add(Rect(x - r, y - r, 2 * r, 2 * r).pad(-out), color, r + out, ring=ring)


def _limb(rig, switch):
    """The controls a switch flips between (both chains)."""
    stem = switch.name.split("_fk")[0].replace("use_ik_", "")
    part = stem.split("_")[0]
    words = LIMB_WORDS.get(part, (part,))
    return [c.name for c in rig.controls if c.side == switch.side and c.role != "finger"
            and any(w in c.name.lower() for w in words)]


def _limb_anchor(picker, rig, switch):
    """Where a switch goes: beside its limb's controls (both chains: the place holds when the limb flips), outward."""
    pts = [picker.place[n] for n in _limb(rig, switch) if n in picker.place]
    if not pts:
        return None
    edge = min if switch.side == "R" else max       # the cluster's outer edge: the switch goes past it
    return edge(p[0] for p in pts), sum(p[1] for p in pts) / len(pts)


def _nearest_joint(points, x, y):
    """The skeleton point nearest (x, y) (a pole's leader goes to its knee or elbow)."""
    return min(points, key=lambda p: (p[0] - x) ** 2 + (p[1] - y) ** 2) if points else None


def _silhouette(entry, arm):
    """(batch, extent) of the character's meshes seen from the front (rig_source.silhouette), built again when its
    meshes change; (None, None) for a bare armature."""
    meshes = rig_source.body_meshes(arm)
    key = tuple((o.name, len(o.data.vertices)) for o in meshes)
    if entry.get("sil") is None or entry["sil"][0] != key:
        pts = rig_source.silhouette(arm, meshes) if meshes else None
        batch = extent = None
        if pts is not None:
            batch = batch_for_shader(gpu.shader.from_builtin('UNIFORM_COLOR'), 'TRIS', {"pos": pts})
            lo, hi = pts.min(axis=0), pts.max(axis=0)
            extent = (float(lo[0]), float(lo[1]), float(hi[0]), float(hi[1]))
        entry["sil"] = (key, batch, extent)
    return entry["sil"][1:]


def _mix(a, b, k):
    return tuple(a[i] + (b[i] - a[i]) * k for i in range(3)) + (1.0,)


def _draw_body(t, batch, fit, x0, y0):
    """The silhouette, opaque (its triangles overlap), with a soft rim: the same shape nudged around it first."""
    k, ox, oy = fit
    s = t.s
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    gpu.state.blend_set('ALPHA')
    rim = 1.5 * s
    for dx, dy, color in [(dx * rim, dy * rim, _mix(t.well, t.text1, 0.2)) for dx, dy in
                          ((1, 0), (-1, 0), (0, 1), (0, -1), (0.7, 0.7), (-0.7, 0.7), (0.7, -0.7), (-0.7, -0.7))]             + [(0.0, 0.0, _mix(t.well, t.text1, 0.07))]:
        with gpu.matrix.push_pop():
            gpu.matrix.translate((x0 + ox + dx, y0 + oy + dy))
            gpu.matrix.scale((k, k))
            shader.uniform_float("color", color)
            batch.draw(shader)


def _draw_picker(t, view, entry, rig, arm, box, hover, hidden, pane_view):
    x0, y0, x1, y1 = box
    s = t.s
    body, extent = _silhouette(entry, arm)
    picker = rig_layout.Picker(rig.controls, x1 - x0, y1 - y0, s, hidden, view=pane_view, extent=extent)
    view.update(picker=picker, picker_box=box, switches={})
    # the body itself: its meshes' silhouette, its deform bones inside, in the picker's front view
    joints = []
    if picker.body_fit and body is not None:
        _draw_body(t, body, picker.body_fit, x0, y0)
    if picker.body_fit and rig.skeleton:
        k, ox, oy = picker.body_fit
        joints = [p for bx0, bz0, bx1, bz1 in rig.skeleton for p in ((x0 + ox + bx0 * k, y0 + oy + bz0 * k),
                                                                     (x0 + ox + bx1 * k, y0 + oy + bz1 * k))]
        _draw('LINES', joints, [_alpha(t.text1, 0.12 if body is not None else 0.2)] * len(joints), 2.0)
    bones = arm.pose.bones
    active = arm.data.bones.active
    animated, now = key_state(arm, bpy.context.scene.frame_current)
    hovered = hover[1] if hover and hover[0] == "body" else None
    switch = next((w for w in rig.switches if hover and hover[0] == "switch" and w.name == hover[1]), None)
    lit = set(_limb(rig, switch)) if switch else set()       # a hovered switch shows the limb it flips
    leaders, rects = [], paint.Rects()
    for side in "LR":       # the palm row on a plate: those controls work the whole hand
        row = [picker.place[n] for n in picker.palm if n in picker.place and rig_source.side_of(n) == side]
        if row:
            left, right = min(p[0] for p in row), max(p[0] for p in row)
            rects.add(Rect(x0 + left - 12 * s, y0 + row[0][1] - 9 * s, right - left + 24 * s, 18 * s),
                      _alpha(t.text1, 0.05), 9 * s)
    edge = (0.0, 0.0, 0.0, 0.55)
    for c in rig.controls:
        place = picker.place.get(c.name)
        if place is None:
            continue
        x, y, r = place
        x, y = x0 + x, y0 + y
        kind = "palm" if c.name in picker.palm else rig_layout.kind_of(c)
        side = SIDE_COLORS.get(c.side, SIDE_COLORS["C"])
        half = r * (1.1 if kind == "ik" else 1.0)       # rings (cog, root) come sized round what they hold
        if c.name in lit:
            _shape(rects, kind, x, y, half, _alpha(t.text1, 0.45), s, 1.5 * s, 4 * s)
        if c.name in picker.ghost:      # the inactive chain: kept in place, not shown
            continue
        if kind == "pole":
            joint = _nearest_joint(joints, x, y)
            if joint is not None:
                leaders += [(x, y), joint]
        selected = c.name in bones and bones[c.name].select
        if selected:        # a ring 2 px out: the active control's brighter
            ring = _alpha(t.ring, 0.95 if active is not None and active.name == c.name else 0.55)
            _shape(rects, kind, x, y, half, ring, s, 2 * s, 4 * s)
        fill = side + (1.0 if selected or c.name == hovered else 0.8 if kind != "other" else 0.6,)
        if c.name == hovered:
            fill = _lighter(fill, 0.2)
        hollow = kind in ("pole", "cog", "root")        # they place things, they aren't parts of the body
        _shape(rects, kind, x, y, half, edge, s, 3 * s if hollow else 0.0, 1 * s)
        _shape(rects, kind, x, y, half, fill, s, 2 * s if hollow else 0.0)
        if c.name in animated:      # a core: dark when animated, white when keyed on this frame
            core = 2 * s
            rects.add(Rect(x - core, y - core, 2 * core, 2 * core), (1, 1, 1, 0.95) if c.name in now else (0, 0, 0, 0.6), core)
    _draw('LINES', leaders, [_alpha(t.text1, 0.3)] * len(leaders))
    rects.draw()
    _draw_switches(t, view, rig, arm, picker, box, switch)
    _finger_picks(t, view, rig, picker, box, hover)
    if hovered in picker.place:
        x, y, r = picker.place[hovered]
        _chip(t, hovered, x0 + x, y0 + y, r, box)


def _draw_switches(t, view, rig, arm, picker, box, hovered=None):
    """A segmented FK | IK by each switched limb, the live side filled; a dot when the switch is keyed."""
    x0, y0, x1, y1 = box
    s = t.s
    seg, h = 22 * s, 18 * s      # the live side a pill inset in it: room for its label
    w = 2 * seg
    keyed_paths = {c.data_path for c in curves(arm)}
    for i, switch in enumerate(rig.switches):
        anchor = _limb_anchor(picker, rig, switch)
        if anchor is None:
            px, py = t.pad + i * (w + t.gap), (y1 - y0) - h - t.pad
        else:
            out = -1 if switch.side == "R" else 1        # centre parts (spine, neck): beside their controls too
            px = min(max(anchor[0] + out * (12 * s + w / 2 + rig_layout.RADIUS["limb"] * s) - w / 2, 2), (x1 - x0) - w - 2)
            py = anchor[1] - h / 2
        pill = Rect(x0 + px, y0 + py, w, h)
        paint.rect(pill, _alpha(t.control, 0.9) if switch != hovered else _lighter(t.control, 0.12), t.r_small,
                   edge=_alpha(t.text1, 0.15), border=1)
        inset = 2 * s
        on = Rect(pill.x + (seg if switch.is_ik else 0), pill.y, seg, h).pad(inset)
        paint.rect(on, _alpha(t.text1, 0.85), min(t.r_small, h / 2) - inset)
        for j, word in enumerate(("FK", "IK")):
            live = (j == 1) == switch.is_ik
            paint.text_in(word, Rect(pill.x + j * seg, pill.y, seg, h), t.well if live else t.text3, t.caption, "center")
        path = rig_source.switch_path(arm, switch)
        if path and path in keyed_paths:
            paint.rect(Rect(pill.right + 3 * s, pill.y + h / 2 - 1.5 * s, 3 * s, 3 * s), t.text2, 1.5 * s)
        view["switches"][switch.name] = (pill.x, pill.y, w, h)


def _finger_picks(t, view, rig, picker, box, hover):
    """Each finger's letter under its tip picks the whole finger; a tick at the grid's outer side picks that joint on
    every finger of the hand."""
    x0, y0 = box[0], box[1]
    s = t.s
    fingers, rows = {}, {}
    for c in rig.controls:
        if c.role != "finger" or c.name not in picker.place or c.name in picker.palm or c.name in picker.ghost:
            continue
        word, joint = rig_layout.finger_part(c.name)
        if word is None or joint is None:
            continue
        fingers.setdefault((c.side, word), []).append(c.name)
        rows.setdefault((c.side, joint), []).append(c.name)
    buttons = view.setdefault("buttons", {})
    hot = hover[1] if hover and hover[0] == "button" else None
    for (side, word), names in fingers.items():
        x, y = picker.place[names[0]][0], min(picker.place[n][1] for n in names)
        letter, key = word[0].upper(), "finger:%s:%s" % (side, word)
        cell = Rect(x0 + x - 8 * s, y0 + y - 12 * s - t.caption, 16 * s, t.caption + 6 * s)
        paint.text_in(letter, cell, t.text1 if key == hot else t.text3, t.caption, "center")
        buttons[key] = (cell, "select", names, "Pick the %s" % word)
    for (side, joint), names in rows.items():
        xs = [picker.place[n][0] for n in names]
        x = max(xs) + 14 * s if side == "L" else min(xs) - 14 * s        # outboard: clear of the body
        y = picker.place[names[0]][1]
        key = "row:%s:%d" % (side, joint)
        cell = Rect(x0 + x - 6 * s, y0 + y - 6 * s, 12 * s, 12 * s)
        paint.rect(Rect(cell.x + 2 * s, cell.y + 5 * s, 8 * s, 2 * s), t.text1 if key == hot else t.text3, s)
        buttons[key] = (cell, "select", names, "Pick this joint on every finger")


def _tip(t, text, cell, well):
    """A button's tip under it (beside it, a segmented control's next segment would be covered)."""
    s = t.s
    w, h = paint.measure(text, t.text) + 8 * s, t.text + 8 * s
    x = min(max(cell.x + cell.w / 2 - w / 2, well.x), well.right - w)
    y = cell.y - h - 4 * s if cell.y - h - 4 * s > well.y else cell.top + 4 * s
    chip = Rect(x, y, w, h)
    paint.rect(chip, _alpha(t.card, 0.95), t.r_small, edge=_alpha(t.text1, 0.12), border=1)
    paint.text_in(text, chip, t.text1, t.text, "center")


def _chip(t, name, x, y, r, box):
    """The control's name in a card beside it (flipped at the pane's edge)."""
    s = t.s
    text = name
    for prefix in ("MB_", "CTRL_"):
        text = text[len(prefix):] if text.startswith(prefix) else text
    w = paint.measure(text, t.text) + 8 * s
    h = t.text + 8 * s
    cx = x + r + 8 * s
    if cx + w > box[2]:
        cx = x - r - 8 * s - w
    chip = Rect(cx, y - h / 2, w, h)
    paint.rect(chip, _alpha(t.card, 0.95), t.r_small, edge=_alpha(t.text1, 0.12), border=1)
    paint.text_in(text, chip, t.text1, t.text, "center")


def _board(entry, part):
    """The board's shown head (rig_layout.board_part) and its outline's batch, kept per head."""
    parts = entry.setdefault("parts", {})
    if part not in parts:
        board = rig_layout.board_part(entry["rig"].board, part)
        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        parts[part] = (board, batch_for_shader(shader, 'TRIS', {"pos": board.outline}) if board.outline else None)
    return parts[part]


def _part_switch(t, view, entry, box, hover):
    """Face | Tweakers | All at the pane's top left, for boards with Epic's tweaker head."""
    if not rig_layout.has_tweakers(entry["rig"].board):
        return
    s, wm = t.s, bpy.context.window_manager
    hot = hover[1] if hover and hover[0] == "button" else None
    words = (("FACE", "Face"), ("TWEAKERS", "Tweakers"), ("ALL", "All"))
    widths = [paint.measure(w, t.caption) + 14 * s for _, w in words]
    h = 18 * s
    x, y = box[0] + t.pad, box[3] - t.pad - h
    paint.rect(Rect(x, y, sum(widths), h), _alpha(t.control, 0.9), t.r_small, edge=_alpha(t.text1, 0.15), border=1)
    for (value, word), w in zip(words, widths):
        cell, key = Rect(x, y, w, h), "part:" + value
        live = wm.fp_rig_face_part == value
        inset = 2 * s       # a segment inside the switch: its radius the switch's less the inset (concentric)
        if live:
            paint.rect(cell.pad(inset), _alpha(t.text1, 0.85), min(t.r_small, h / 2) - inset)
        elif key == hot:
            paint.rect(cell.pad(inset), t.hover, min(t.r_small, h / 2) - inset)
        paint.text_in(word, cell, t.well if live else t.text2, t.caption, "center")
        tip = "Show both heads" if value == "ALL" else "Show the %s" % word.lower()
        view.setdefault("buttons", {})[key] = (cell, "part", value, tip)
        x += w


def _draw_board(t, view, entry, arm, box, hover, mirror, pane_view):
    x0, y0, x1, y1 = box
    s = t.s
    part = bpy.context.window_manager.fp_rig_face_part.lower()
    board, batch = _board(entry, part)
    size = (x1 - x0, y1 - y0, s, part)
    face = entry["boards"].get(size)
    if face is None:
        entry["boards"] = {}        # one size kept: the area's current one
        face = entry["boards"][size] = rig_layout.FaceBoard(board, size[0], size[1], s)
    face.set_view(pane_view)
    view.update(board=face, board_box=box)
    if batch is not None:
        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        gpu.state.blend_set('ALPHA')
        with gpu.matrix.push_pop():
            gpu.matrix.translate((x0 + face.ox, y0 + face.oy))
            gpu.matrix.scale((face.scale, face.scale))
            shader.uniform_float("color", _alpha(t.text1, 0.2))        # under the knobs: the board's art recedes
            batch.draw(shader)
    range_color = _alpha(t.text1, 0.28)
    tracks, pads, pad_fill = [], [], []
    for k in board.knobs:
        rx, ry = face.rest[k.bone]
        if len(k.axes) == 1:
            a = k.axes[0]
            tracks += [(x0 + rx + a.dx * a.lo * face.scale, y0 + ry + a.dz * a.lo * face.scale),
                       (x0 + rx + a.dx * a.hi * face.scale, y0 + ry + a.dz * a.hi * face.scale)]
        else:
            a, b = k.axes[:2]
            corners = [(x0 + rx + (a.dx * u + b.dx * v) * face.scale, y0 + ry + (a.dz * u + b.dz * v) * face.scale)
                       for u, v in ((a.lo, b.lo), (a.hi, b.lo), (a.hi, b.hi), (a.lo, b.hi))]
            for i in range(4):
                pads += [corners[i], corners[(i + 1) % 4]]
            pad_fill += [corners[0], corners[1], corners[2], corners[0], corners[2], corners[3]]
    _draw('TRIS', pad_fill, [_alpha(t.text1, 0.04)] * len(pad_fill))
    _draw('LINES', tracks, [_alpha(t.text1, 0.22)] * len(tracks), 3.0)      # sliders: a track the knob runs in
    _draw('LINES', pads, [range_color] * len(pads))
    bones, active = arm.pose.bones, arm.data.bones.active
    hovered = hover[1] if hover and hover[0] == "face" and hover[1] in face.knobs else None
    twin = rig_layout.mirror_of(hovered) if hovered and mirror else None
    rects, leaders = paint.Rects(), []
    for k in board.knobs:
        kx, ky = face.knob_at(k.bone, values_of(arm, k.bone))
        kx, ky = x0 + kx, y0 + ky
        rx, ry = face.rest[k.bone]
        if abs(kx - x0 - rx) + abs(ky - y0 - ry) > 2 * s:      # moved: a line back to where it rests
            leaders += [(x0 + rx, y0 + ry), (kx, ky)]
        r = min(max(k.size * face.scale, 4.5 * s), 6.5 * s) if k.size else 5.5 * s      # Epic's knob size, kept legible
        knob = Rect(kx - r, ky - r, 2 * r, 2 * r)
        if k.bone in bones and bones[k.bone].select:
            rects.add(knob.pad(-4 * s), _alpha(t.ring, 0.95 if active is not None and active.name == k.bone else 0.55), r + 4 * s,
                      ring=2 * s)
        elif k.bone == twin:
            rects.add(knob.pad(-3 * s), _alpha(t.ring, 0.45), r + 3 * s, ring=1 * s)
        rects.add(knob.pad(-1 * s), (0.0, 0.0, 0.0, 0.6), r + s)
        rects.add(knob, _lighter(KNOB_COLOR + (1.0,), 0.25) if k.bone == hovered else KNOB_COLOR + (0.9,), r)
    _draw('LINES', leaders, [KNOB_COLOR + (0.4,)] * len(leaders))
    rects.draw()
    if board.kind == "bones":       # a few sliders and pads: each named over its start, a slider ticked per frame
        ticks = []
        for k in board.knobs:
            label = rig_layout.knob_label(board, k, values_of(arm, k.bone))
            rx, ry = face.rest[k.bone]
            a = k.axes[0]
            if len(k.axes) == 1:
                sx, sy = x0 + rx + a.dx * a.lo * face.scale, y0 + ry + a.dz * a.lo * face.scale
                paint.text(label, sx, sy + 10 * s, t.text2, t.text)
                frames = round((a.hi - a.lo) / board.step) + 1 if board.step else 0
                for i in range(frames if frames <= 64 else 0):
                    tx = x0 + rx + a.dx * (a.lo + i * board.step) * face.scale
                    ty = y0 + ry + a.dz * (a.lo + i * board.step) * face.scale
                    ticks += [(tx - a.dz * 4 * s, ty - a.dx * 4 * s), (tx + a.dz * 4 * s, ty + a.dx * 4 * s)]
            else:
                paint.text(label, x0 + rx - paint.measure(label, t.text) / 2,
                           y0 + ry + max(abs(a.hi), abs(a.lo)) * face.scale + 8 * s, t.text2, t.text)
        _draw('LINES', ticks, [range_color] * len(ticks))
    if hovered:
        kx, ky = face.knob_at(hovered, values_of(arm, hovered))
        _chip(t, hovered + ("  (mirrored)" if twin else ""), x0 + kx, y0 + ky, 6 * s, box)


def _body_share(t, entry, arm, well):
    """The body pane's width: each pane as wide as its content wants at the well's height (rig_layout.split)."""
    rig = entry["rig"]
    _, extent = _silhouette(entry, arm)
    points = [(c.x, c.z) for c in rig.controls if c.role != "finger"] + ([extent[:2], extent[2:]] if extent else [])
    hands = any(c.role == "finger" for c in rig.controls)
    hand_w = (4 * rig_layout.FINGER_PITCH + rig_layout.HAND_GAP + 30) * t.s       # a finger grid and its ticks
    body_w = well.h * rig_layout.aspect(points) + (2 * hand_w if hands else 0.0)
    board, _ = _board(entry, bpy.context.window_manager.fp_rig_face_part.lower())
    face_w = well.h * rig_layout.aspect(rig_layout.board_points(board))
    return rig_layout.split(well.w - t.pad, body_w, face_w)


def _pane(t, r):
    """A pane's ground, rounded as the well, drawing clipped to it; returns its box (x0, y0, x1, y1)."""
    paint.rect(r, t.well, t.r_card)
    paint.push_clip(r)
    return r.x, r.y, r.right, r.top


def _message(t, view, well, text):
    view["message"] = text
    paint.rect(well, t.well, t.r_card)
    paint.text_in(text, well, t.text2, t.title, "center")


def draw():
    context = bpy.context
    region, area = context.region, context.area
    t = ui_blender.theme(context)
    view = RIG["views"].setdefault(area.as_pointer(), {})
    view.clear()
    well = ui_blender.well(area, region, tabs.current(context.space_data))
    paint.begin(region)
    try:
        paint.rect(well, t.surface)     # the ground between the panes
        _draw_panes(context, t, view, well)
    finally:
        paint.end()


def _draw_panes(context, t, view, well):
    key, wm = context.area.as_pointer(), context.window_manager
    arm = armature(context)
    if arm is None:
        _message(t, view, well, "Select a character (its armature or one of its meshes)")
        return
    entry = rig_of(arm)
    rig = entry["rig"]
    view["arm"] = arm.name          # the input operators act on the armature drawn here
    panes = wm.fp_rig_panes
    body = bool(rig.controls) and panes in {'BOTH', 'BODY'}
    face = rig.board is not None and panes in {'BOTH', 'FACE'}
    if not rig.controls and rig.board is None:
        _message(t, view, well, "No controls or face board on %s" % arm.name)
        return
    if not (body or face):
        _message(t, view, well, "This rig has no %s" % ("face board" if panes == 'FACE' else "body controls"))
        return
    hover = RIG["hover"].get(key)
    entry["rig"] = rig = rig._replace(switches=rig_source.switches(arm, rig.kind))      # switches flip outside the tab
    rest = well.copy()
    if body:
        r = rest.cut_left(_body_share(t, entry, arm, well)) if face else rest
        _draw_picker(t, view, entry, rig, arm, _pane(t, r), hover, rig_source.hidden(arm), RIG["panes"].get((key, "body")))
        paint.pop_clip()
        rest.cut_left(t.pad)
    if face:
        board_box = _pane(t, rest)
        _draw_board(t, view, entry, arm, board_box, hover, wm.fp_rig_mirror, RIG["panes"].get((key, "face")))
        _part_switch(t, view, entry, board_box, hover)
        paint.pop_clip()
    if hover and hover[0] == "button" and hover[1] in (view.get("buttons") or {}):
        cell, _kind, _payload, tip = view["buttons"][hover[1]]
        _tip(t, tip, cell, well)
    box = RIG["box"].get(key)
    if box:
        (bx0, by0), (bx1, by1) = box
        paint.rect(Rect(min(bx0, bx1), min(by0, by1), abs(bx1 - bx0), abs(by1 - by0)), _alpha(t.accent, 0.15),
                   edge=_alpha(t.accent, 0.8), border=1)


# ------------------------------------------------------------------ the list column, the header

def _use(name):
    def run(context):
        bpy.ops.fp.rig_use(name=name)
    return run


def side(ui, r):
    """The scene's rigged characters, the shown one selected."""
    t, context = ui.t, bpy.context
    arms = rigged(context)
    if not arms:
        ui.label(r.cut_top(t.row_pitch), "No rigged character", t.text3)
        return
    shown = armature(context)
    offset = ui.scroll("rig:characters", r, len(arms) * t.row_pitch)
    y = r.top + offset
    for arm in arms:
        y -= t.row_pitch
        if y > r.top or y + t.row_pitch < r.y:
            continue
        cell = Rect(r.x, y + (t.row_pitch - t.row) / 2, r.w - ui.bar_room, t.row)
        kind = KINDS.get(rig_of(arm)["rig"].kind)
        ui.row("rig:" + arm.name, cell, arm.name, "mdi_Bone", shown is arm, _use(arm.name),
               tip="Show this character" + (" (%s rig)" % kind if kind else ""))
    ui.end_scroll()


def character_items(context):
    """The character pop-up's rows: fp.rig_use's own choices, the shown one marked."""
    from .rig_ops import _armatures
    shown = armature(context)
    return [{"label": label, "icon": "mdi_Bone", "check": shown is not None and name == shown.name, "action": _use(name),
             "id": name} if name else {"label": label, "id": "none"} for name, label, _ in _armatures(None, context)]


def open_characters(context, anchor, side="below"):
    """Show another character (type to search)"""
    from .ui import popups
    return popups.open_list(context, anchor, lambda: character_items(bpy.context), "rig:characters",
                            "Search characters", side=side)


def _side_folded(context):
    """Whether the area is too narrow for the characters column (the header's dropdown stands in)."""
    return "side" in ui_blender.folded(context, tabs.current(context.space_data))


def _several(context):
    """The characters column only when there's a choice; one character is named in the header."""
    return len(rigged(context)) > 1


def header(layout, context):
    wm = context.window_manager
    arm = armature(context)
    if _side_folded(context) or not _several(context):
        layout.operator("fp.popup_open", text=arm.name if arm else "No character", icon='ARMATURE_DATA').name = "rig:characters"
    layout = layout.row()
    layout.active = arm is not None and _has_controls(arm)       # nothing to act on: greyed, as Blender does
    narrow = _side_folded(context)      # the header's room is short: icons for the less used tools
    layout.prop(wm, "fp_rig_panes", expand=True)
    layout.prop(wm, "fp_rig_mirror", text="" if narrow else "Mirror", icon='MOD_MIRROR')
    row = layout.row(align=True)
    row.operator("fp.rig_reset", text="Reset", icon='LOOP_BACK').scope = 'SELECTED'
    row.operator("fp.rig_reset", text="All").scope = 'ALL'
    row = layout.row(align=True)
    row.operator("fp.rig_key", text="Key", icon='KEY_HLT').what = 'SELECTED'
    row.operator("fp.rig_key", text="" if narrow else "Key Face", icon='KEYINGSET').what = 'FACE'


# ------------------------------------------------------------------ redraws when the pose changes elsewhere

@persistent
def _depsgraph(scene, depsgraph):
    if not shown_areas():
        return
    found = rig_source.armature_of(bpy.context.view_layer.objects.active)
    if found is not None and found.name != RIG["arm"]:      # another character picked in the viewport
        redraw_main()
        return
    arm = bpy.data.objects.get(RIG["arm"] or "")
    if arm is None:
        return
    for update in depsgraph.updates:
        if update.id.original in (arm, arm.data):
            redraw_main()
            return


@persistent
def _frame(scene, *_):
    if RIG["arm"] and shown_areas():
        redraw_main()


@persistent
def _loaded(*_):
    """Another file: its armatures are new, the shown ones gone."""
    RIG.update(arm=None, cache={}, views={}, hover={}, box={}, panes={}, keys=None)


HANDLERS = (("depsgraph_update_post", _depsgraph), ("frame_change_post", _frame), ("load_post", _loaded))


def register():
    from .rig_ops import hover
    wm = bpy.types.WindowManager
    wm.fp_rig_panes = bpy.props.EnumProperty(name="Panes", default='BOTH', items=[
        ('BOTH', "Both", "Body controls and face board"), ('BODY', "Body", "Body controls only"),
        ('FACE', "Face", "Face board only")])
    wm.fp_rig_mirror = bpy.props.BoolProperty(name="Mirror", description="Dragging a left knob moves its right twin too (and back)")
    wm.fp_rig_face_part = bpy.props.EnumProperty(name="Head", default='FACE', items=[
        ('FACE', "Face", "Epic's face board"), ('TWEAKERS', "Tweakers", "Epic's tweaker head"), ('ALL', "All", "Both heads")],
        update=lambda self, context: redraw_main())
    tabs.register_tab(tabs.Tab("rig", "Rig", 'ARMATURE_DATA', header=header, draw=draw, nav_icon="mdi_Bone",
                               group="tools", side=side, hover=hover, has_side=_several))
    ui_blender.OPENERS["rig:characters"] = open_characters
    for name, fn in HANDLERS:
        getattr(bpy.app.handlers, name).append(fn)


def unregister():
    for name, fn in HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if fn in handlers:
            handlers.remove(fn)
    tabs.unregister_tab("rig")
    ui_blender.OPENERS.pop("rig:characters", None)
    for p in ("fp_rig_panes", "fp_rig_mirror", "fp_rig_face_part"):
        delattr(bpy.types.WindowManager, p)
