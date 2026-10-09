"""A flipbook face's frames from Fortnite's facial curves, as the head's Control Rig picks them.

Flipbook heads run Fortnite_Flipbook2_Face_Mapping_CtrlRig: it reads the legacy *_pose curves and writes the
flipbook_face_*_index material parameters. The rules below are its byte code, decoded once (spec
2026-10-09-flipbook-emote-faces, Appendix A); within a block a later match overwrites an earlier one, as in the rig.
"""
import bpy

from . import curve_expressions, face_anim

OUTPUTS = ("flipbook_face_l_brow_index", "flipbook_face_r_brow_index", "flipbook_face_l_eye_index",
           "flipbook_face_r_eye_index", "flipbook_face_mouth_index")
THRESHOLD = 0.25        # the rig's `threshold` variable


def _brow(v, side, out):
    down, up = v("%s_brow_down_pose" % side), v("%s_brow_up_pose" % side)
    glabella_down, glabella_up = v("c_glabella_down_pose"), v("c_glabella_up_pose")
    if glabella_down >= THRESHOLD:
        out = 8
    if glabella_up >= 0.9:
        out = 7
    if down >= THRESHOLD:
        out = 6
    if up >= THRESHOLD:
        out = 5
    if glabella_down >= 0.5:
        if down >= 0.75:
            out = 4
        if up >= 0.75:
            out = 2
    if glabella_up >= 0.9:
        if down >= 0.75:
            out = 3
        if up >= 0.75:
            out = 1
    return out


def _eye(v, side, brow):
    out = 0
    if v("%s_blink_pose" % side) >= 0.5:
        out = 3
        if v("%s_squint_inner_pose" % side) >= 0.7:
            out = 2
            if v("%s_squeeze_pose" % side) >= 0.7:
                out = 1
        return out
    if v("%s_wide_pose" % side) >= 0.75:
        out = 7
    # the eye follows a lowered brow
    return {6: 6, 4: 5, 3: 4}.get(brow, out)


def _mouth(v):
    t = THRESHOLD
    l_snear = v("l_upper_lip_raiser_pose") >= t or v("l_lower_lip_down_pose") >= t
    r_snear = v("r_upper_lip_raiser_pose") >= 0.5 or v("r_lower_lip_down_pose") >= 0.5     # the rig's own asymmetry
    l_frown, r_frown = v("l_frown_pose") >= t, v("r_frown_pose") >= t
    l_smile, r_smile = v("l_smile_pose") >= t, v("r_smile_pose") >= t
    out = 0
    if v("jaw_open_pose") >= t:
        out = 7
        if r_snear:
            out = 6
        if l_snear:
            out = 5
        if l_frown or r_frown:
            out = 4
        if r_smile:
            out = 3
        if l_smile:
            out = 2
        return out
    # the larger jaw side at or over the threshold (equal: the later, left)
    largest = 0.0
    for curve, index in (("jaw_right_pose", 13), ("jaw_left_pose", 14)):
        if v(curve) >= t and v(curve) >= largest:
            largest, out = v(curve), index
    if r_snear:
        out = 14
    if l_snear:
        out = 13
    if v("phoneme_oo_pose") >= t or v("phoneme_ch_pose") >= t:
        out = 1
    if l_frown or r_frown:
        out = 12
    if l_smile or r_smile:
        out = 11
    if v("l_upper_lip_raiser_pose") >= t and v("r_upper_lip_raiser_pose") >= t:
        out = 10
    if l_frown and r_frown and (l_snear or r_snear):
        out = 10
    if r_smile and r_snear:
        out = 9
    if l_smile and l_snear:
        out = 8
    return out


def indices(values):
    """{output: index} from one frame's curve values (lower-case names; a missing curve reads 0). When the pose
    bypasses the mapping, the incoming flipbook curves pass through and an absent one is None (left as imported)."""
    v = lambda name: values.get(name, 0.0)
    if v("flipbook_face_bypass_mapping") >= 0.5:
        return {o: int(round(values[o])) if o in values else None for o in OUTPUTS}
    l_brow, r_brow = _brow(v, "l", 0), _brow(v, "r", 0)
    return {
        "flipbook_face_l_brow_index": l_brow,
        "flipbook_face_r_brow_index": r_brow,
        "flipbook_face_l_eye_index": _eye(v, "l", l_brow),
        "flipbook_face_r_eye_index": _eye(v, "r", r_brow),
        "flipbook_face_mouth_index": _mouth(v),
    }


# the curves the rules read: a section carrying any of them drives the face itself (spec Appendix B)
READS = frozenset("%s_%s_pose" % (s, c) for s in "lr" for c in (
    "brow_down", "brow_up", "blink", "squint_inner", "squeeze", "wide", "upper_lip_raiser", "lower_lip_down",
    "frown", "smile")) | {"c_glabella_down_pose", "c_glabella_up_pose", "jaw_open_pose", "jaw_right_pose",
                          "jaw_left_pose", "phoneme_oo_pose", "phoneme_ch_pose", "flipbook_face_bypass_mapping"}
BLINKS = ("l_blink_pose", "r_blink_pose")


def _faces(armature):
    from ..processing.context import face_board
    return [(mat, inputs) for mat, inputs in face_board.faces(armature) if any(o in inputs for o in OUTPUTS)]


def has_faces(armature):
    return bool(_faces(armature))


def _values(curves, modes, frame):
    return {name: face_anim._evaluate(c.keys, modes, name, frame) for name, c in curves.items() if c.keys}


def _metahuman(section, mapping):
    """A MetaHuman (3L) section's legacy curves come from the 3L-to-legacy expressions, as the head's mapping does."""
    return [m for m in mapping or [] if m["Name"].lower() in READS] if any(
        c.name.lower() == "is_3l" for c in section["dto"].curves) else []


def _faced(section, mapping=None):
    return bool(_metahuman(section, mapping)) or any(c.name.lower() in READS and c.keys for c in section["dto"].curves)


def _frames(section, idle_curves, idle_modes, period, start, end, mapping=None):
    """Each frame's curve values: the emote's, or the idle's when the emote has no face curves; the idle's blinks
    show through an emote's open eyes."""
    curves = {c.name.lower(): c for c in section["dto"].curves}
    modes = {k.lower(): v for k, v in (section.get("modes") or {}).items()}
    faced, legacy = _faced(section, mapping), _metahuman(section, mapping)
    for f in range(start, end + 1):
        under = _values(idle_curves, idle_modes, (f - start) % period if period else 0)
        if not faced:
            yield f, under
            continue
        values = _values(curves, modes, f)
        for m in legacy:
            values[m["Name"].lower()] = curve_expressions.evaluate(m["ExpressionStack"], values.get)
        for blink in BLINKS:
            values[blink] = max(values.get(blink, 0.0), under.get(blink, 0.0))
        yield f, values


def apply(armature, sections, idle, mapping=None):
    """Key the flipbook index inputs of the armature's faces for each section (dicts as face_anim.apply's), with
    `idle` ({"dto", "modes", "length"} or None) under them; `mapping`: the payload's MetahumanToLegacyMappings.
    Returns the number of face materials animated."""
    faces = _faces(armature)
    if not faces:
        return 0
    face_anim.clear(armature)
    if armature.get("fpmp_face_board_materials"):
        armature.fpmp_face_board = False        # the emote's face plays, not the board
    idle_curves = {c.name.lower(): c for c in idle["dto"].curves} if idle else {}
    idle_modes = {k.lower(): v for k, v in ((idle or {}).get("modes") or {}).items()}
    period = (idle or {}).get("length") or max((k.frame for c in idle_curves.values() for k in c.keys), default=0)
    for mat, inputs in faces:
        tree = mat.node_tree
        ad = tree.animation_data or tree.animation_data_create()
        track = None
        for sec in sections:
            outputs = [o for o in OUTPUTS if o in inputs]
            points = {o: [] for o in outputs}
            start, end, repeat = int(sec["range"][0]), int(sec["range"][1]), sec["repeat"]
            if not _faced(sec, mapping) and period and repeat > 1:
                # a looping emote without a face: the idle keeps its own loop, not the emote's
                end, repeat = start + period, (end - start) * repeat / period
            for f, values in _frames(sec, idle_curves, idle_modes, period, start, end, mapping):
                picked = indices(values)
                for o in outputs:
                    index = picked[o]
                    points[o].append((f, float(index) if index is not None else inputs[o].default_value))
            action = bpy.data.actions.new("%s (%s)" % (sec["name"], mat.name))
            ad.action = action
            for o, pts in points.items():
                face_anim._keys(face_anim._fcurve(action, tree, inputs[o]), pts, ['CONSTANT'] * len(pts))
            action.use_frame_range = True
            action.frame_start, action.frame_end = start, end
            ad.action = None
            if track is None:
                track = ad.nla_tracks.new(prev=None)
                track.name = face_anim.TRACK
            strip = track.strips.new(sec["name"], int(sec["frame"]), action)
            strip.repeat = repeat
    return len(faces)


def import_emote(anim_context, armature, sections, idle_section, mapping):
    """The emote import's hook: a flipbook face plays the emote; a failure is logged, never fails the import."""
    from ..logger import Log
    try:
        if not has_faces(armature):
            return
        idle = None
        if idle_section:
            action, dto = anim_context.import_anim(idle_section["Path"], armature)
            fps = dto.metadata.frames_per_second if getattr(dto, "metadata", None) else 30
            idle = {"dto": dto, "modes": idle_section.get("MPCurveModes"), "length": int(round(idle_section["Length"] * fps))}
            if action.users == 0:
                bpy.data.actions.remove(action)
        Log.info("[Material Porter] %d flipbook face(s) animated by the emote" % apply(armature, sections, idle, mapping))
    except Exception as e:
        Log.error("[Material Porter] the emote's flipbook face failed: %s: %s" % (type(e).__name__, e))
