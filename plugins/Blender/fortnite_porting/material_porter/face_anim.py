"""A LEGO figure's face, animated by an emote.

A LEGO emote's sequences carry the face rig's curves, named after the face material's parameters
(mouthpose, mouthu, browleftv, teethupperscaleu, eyelefthighlightscale...); UE sets each on the
face's material instance as the animation plays. The exact face material has those parameters as
inputs of its group node ("MouthPose"), so each curve becomes keys on that input (stepped where
UE's keys are constant, i.e. the poses, linear elsewhere) in one action per section, laid on the
material's NLA where the body's strips are.

The character accents (mustache, beard) have no curve: the face rig moves them with the mouth,
using a registration table per mouth pose. The face material holds that table ("mp_face_rig", from
the app), and the accents are keyed every frame from the mouth's curves.
"""
import json

import bpy

TRACK = "MP Face"


def _meshes(armature):
    return [o for o in bpy.data.objects if o.type == 'MESH' and
            (o.parent == armature or any(m.type == 'ARMATURE' and m.object == armature for m in o.modifiers))]


def face_materials(armature):
    """The exact face materials of the armature's meshes: (material, {lower-case parameter: input})."""
    found = {}
    for o in _meshes(armature):
        for slot in o.material_slots:
            mat = slot.material
            if mat is None or mat.node_tree is None or mat.name in found:
                continue
            for n in mat.node_tree.nodes:
                if n.type != 'GROUP':
                    continue
                # the exact material's parameters are its group node's inputs, named as in UE
                inputs = {i.name.lower(): i for i in n.inputs if i.type == 'VALUE'}
                if "mouthpose" in inputs or "eyeleftpose" in inputs or "browleftpose" in inputs:
                    found[mat.name] = (mat, inputs)
                    break
    return list(found.values())


def _mode(modes, name, index):
    m = modes.get(name) or ""
    if not m:
        return 'L'
    return m[min(index, len(m) - 1)]


def _evaluate(keys, modes, name, frame):
    if frame <= keys[0].frame:
        return keys[0].value
    for i in range(len(keys) - 1):
        a, b = keys[i], keys[i + 1]
        if a.frame <= frame < b.frame:
            if _mode(modes, name, i) == 'C' or b.frame == a.frame:
                return a.value
            t = (frame - a.frame) / (b.frame - a.frame)
            return a.value + (b.value - a.value) * t
    return keys[-1].value


def _fcurve(action, tree, socket):
    return action.fcurve_ensure_for_datablock(tree, socket.path_from_id("default_value"))


def _keys(fc, points, interpolations):
    fc.keyframe_points.add(len(points))
    fc.keyframe_points.foreach_set("co", [v for p in points for v in p])
    for kp, interp in zip(fc.keyframe_points, interpolations):
        kp.interpolation = interp
    fc.update()


def clear(armature):
    """Remove an earlier emote's face animation from the armature's faces."""
    for mat, _ in face_materials(armature):
        ad = mat.node_tree.animation_data
        if ad is None:
            continue
        ad.action = None
        for track in [t for t in ad.nla_tracks if t.name == TRACK]:
            ad.nla_tracks.remove(track)


def apply(armature, sections):
    """sections: the body's, in order, as dicts of name, dto (the .ueanim curves), frame (strip
    start), repeat, range (action frame range) and modes (the section's MPCurveModes).
    Returns the number of face materials animated."""
    clear(armature)
    if armature.get("fpmp_face_board_materials"):
        armature.fpmp_face_board = False        # the emote's face keys play, not the board
    # Place the face prints from the rest pose (PreSkinnedPosition), for a figure imported before
    # the importer kept it
    for o in _meshes(armature):
        o.add_rest_position_attribute = True
    animated = 0
    for mat, inputs in face_materials(armature):
        tree = mat.node_tree
        ad = tree.animation_data or tree.animation_data_create()
        rig = json.loads(mat["mp_face_rig"]) if "mp_face_rig" in mat else None
        track = None
        for sec in sections:
            curves = {c.name.lower(): c for c in sec["dto"].curves}
            modes = {k.lower(): v for k, v in (sec.get("modes") or {}).items()}
            used = [(name, inputs[name]) for name in curves if name in inputs]
            if not used:
                continue
            action = bpy.data.actions.new("%s (%s)" % (sec["name"], mat.name))
            ad.action = action
            for name, socket in used:
                keys = curves[name].keys
                if not keys:
                    continue
                _keys(_fcurve(action, tree, socket), [(k.frame, k.value) for k in keys],
                      ['CONSTANT' if _mode(modes, name, i) == 'C' else 'LINEAR' for i in range(len(keys))])

            # character accents, where the rig puts them for the mouth pose, every frame
            if rig and "mouthpose" in curves and curves["mouthpose"].keys:
                k = rig["k"]
                start, end = int(sec["range"][0]), int(sec["range"][1])
                pose_keys = curves["mouthpose"].keys
                (u_keys, u0), (v_keys, v0) = [
                    (curves["mouth" + axis].keys if "mouth" + axis in curves else None,
                     inputs["mouth" + axis].default_value if "mouth" + axis in inputs else 0.0)
                    for axis in ("u", "v")]
                for accent, table in rig["accents"].items():
                    targets = {axis: inputs.get("characteraccent%s%s" % (accent, axis)) for axis in ("u", "v", "pose")}
                    if targets["u"] is None or targets["v"] is None:
                        continue
                    values = {"u": [], "v": [], "pose": []}
                    for f in range(start, end + 1):
                        pose = int(round(_evaluate(pose_keys, modes, "mouthpose", f)))
                        x, z, accent_pose = table[max(0, min(pose, len(table) - 1))]
                        u = _evaluate(u_keys, modes, "mouthu", f) if u_keys else u0
                        v = _evaluate(v_keys, modes, "mouthv", f) if v_keys else v0
                        values["u"].append((f, u + x * k))
                        values["v"].append((f, v + z * k))
                        values["pose"].append((f, accent_pose))
                    for axis, points in values.items():
                        if targets[axis] is not None:
                            _keys(_fcurve(action, tree, targets[axis]), points, ['CONSTANT'] * len(points))

            action.use_frame_range = True
            action.frame_start, action.frame_end = sec["range"]
            ad.action = None
            if track is None:
                track = ad.nla_tracks.new(prev=None)
                track.name = TRACK
            strip = track.strips.new(sec["name"], int(sec["frame"]), action)
            strip.repeat = sec["repeat"]
        if track is not None:
            animated += 1
    return animated
