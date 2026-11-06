"""A pose asset's poses as shape keys, computed: each pose's bone matrices from the rest pose (Bone.convert_local_to_pose,
no depsgraph), the armature modifier's linear skinning in numpy, one foreach_set per key. Replaces FP's loop of mode
switches and modifier_apply_as_shapekey per pose (each one re-evaluates the scene: seconds per mesh). Same result as
the modifier where it is plain linear skinning; anything else (preserve volume, a vertex group mask, envelopes, other
modifiers first) keeps FP's way."""
import bpy
import numpy as np
from mathutils import Matrix, Quaternion, Vector

from ..ueformat.importer.archive.reader import FArchiveReader
from ..ueformat.importer.create.pose import create_pose
from ..ueformat.importer.create.space import to_blender_space
from ..ueformat.importer.import_context import parse_body, parse_header
from ..ueformat.importer.utils import (bone_has_parent, bone_hierarchy_has_vertex_groups, bone_swap_orig_parents,
                                       disable_constraints, first, get_armature_mesh, make_quat, make_vector)


def import_file(path, options):
    """A .uepose file onto the options' armature (pose_context's import, in place of UEFormatImport's)."""
    with open(path, "rb") as f, FArchiveReader(f.read()) as ar:
        header, body = parse_header(ar)
        dto = parse_body(header, body)
    to_blender_space(dto, header.file_version, options.scale_factor)
    create(dto, options, header.object_name)


def create(dto, options, name):
    """UEFormat's create_pose with the poses baked by bake(); its own when the skinning isn't plain."""
    armature = options.override_skeleton or bpy.context.active_object
    mesh = get_armature_mesh(armature)
    modifier = first(mesh.modifiers, lambda m: m.type == 'ARMATURE') if mesh else None
    if mesh is None or not _plain(mesh, modifier, armature):
        return create_pose(dto, options, name)
    original_lock, original_mode = mesh.show_only_shape_key, bpy.context.active_object.mode
    bpy.ops.object.mode_set(mode='OBJECT')
    mesh.show_only_shape_key = False
    bone_swap_orig_parents(armature)
    muted = disable_constraints(armature)
    if not mesh.data.shape_keys:
        mesh.shape_key_add(name="Basis", from_mix=False)
    original = {k.name: k.value for k in mesh.data.shape_keys.key_blocks if k.value != 0}
    for k in mesh.data.shape_keys.key_blocks:
        k.value = 0
    root = armature.pose.bones.get(options.root_bone) or armature.pose.bones[0]
    bake(armature, mesh, modifier, dto.poses, root)
    _curves(dto, mesh)
    for k in mesh.data.shape_keys.key_blocks:
        if k.name in original:
            k.value = original[k.name]
    bone_swap_orig_parents(armature)
    for c in muted:
        c.mute = False
    mesh.show_only_shape_key = original_lock
    if original_mode != 'OBJECT':       # back in the mode it started in, on the armature (as UEFormat does)
        bpy.context.view_layer.objects.active = armature
        bpy.ops.object.mode_set(mode=original_mode)
    bpy.context.view_layer.objects.active = mesh


def _curves(dto, mesh):
    """UEFormat's curve poses: mixes of the baked keys, added from the mix."""
    blocks = mesh.data.shape_keys.key_blocks
    for pose in dto.poses:
        if not pose.curves:
            continue
        name = "curve_" + pose.name if pose.name in blocks else pose.name
        contributed = False
        for curve in pose.curves:
            target = blocks.get(dto.curve_names[curve.curve_index])
            if target is None:
                continue
            if curve.influence < target.slider_min:
                target.slider_min = curve.influence - 1.0
            if curve.influence > target.slider_max:
                target.slider_max = curve.influence + 1.0
            target.value = curve.influence
            contributed = True
        if contributed:
            mesh.shape_key_add(name=name, from_mix=True)
        for k in blocks:
            k.value = 0


def _plain(mesh, modifier, armature):
    """The armature modifier is plain linear skinning on the mesh's own vertices."""
    first = next((m for m in mesh.modifiers if m.show_viewport), None)
    return (modifier is not None and first == modifier and modifier.object == armature and modifier.use_vertex_groups
            and not modifier.use_bone_envelopes and not modifier.use_deform_preserve_volume
            and not modifier.vertex_group and not modifier.use_multi_modifier)


def _weights(mesh, armature):
    """Per deform bone: (vertex indices, weights), and each vertex's total over all deform bones."""
    deform = {b.name for b in armature.data.bones if b.use_deform}
    names = {g.index: g.name for g in mesh.vertex_groups if g.name in deform}
    per, total = {}, np.zeros(len(mesh.data.vertices))
    for v in mesh.data.vertices:
        for g in v.groups:
            name = names.get(g.group)
            if name is not None:
                per.setdefault(name, ([], []))
                per[name][0].append(v.index)
                per[name][1].append(g.weight)
                total[v.index] += g.weight
    return {k: (np.array(i), np.array(w)) for k, (i, w) in per.items()}, total


def _basis(pose_bone, key):
    """FP's posing of a bone from a pose key, as its matrix_basis."""
    post = Quaternion(q) if (q := pose_bone.bone.get("post_quat")) else Quaternion()
    q = post.copy()
    q.rotate(make_quat(key.rotation))
    quat = post.copy()
    quat.rotate(q.conjugated())
    loc = make_vector(key.position)
    loc.rotate(post.conjugated())
    rot = quat.conjugated().normalized() if pose_bone.rotation_mode == 'QUATERNION' else Quaternion()
    scale = Vector((1, 1, 1)) + make_vector(key.scale)
    return Matrix.LocRotScale(loc, rot, scale)


def bake(armature, mesh, modifier, poses, root_bone):
    """Add each contributing pose as a shape key (the bones at rest, the keys' values 0)."""
    bones = armature.data.bones
    by_lower = {b.name.lower(): armature.pose.bones[b.name] for b in bones}
    weights, total = _weights(mesh, armature)
    reference = mesh.data.shape_keys.reference_key
    co = np.empty(len(reference.data) * 3)
    reference.data.foreach_get("co", co)
    co = co.reshape(-1, 3)
    premat = armature.matrix_world.inverted() @ mesh.matrix_world
    postmat = premat.inverted()
    has_weight = total > 1e-4
    for pose in poses:
        basis = {}
        for key in pose.keys:
            pb = by_lower.get(key.bone_name.lower())
            if pb is None or (root_bone and not bone_has_parent(pb, root_bone)) \
                    or not bone_hierarchy_has_vertex_groups(pb, mesh.vertex_groups):
                continue
            basis[pb.name] = _basis(pb, key)
        if not basis:
            continue
        moved = {}

        def pose_of(bone):
            """Armature-space pose matrix; None while it and its parents stay at rest."""
            if bone.name in moved:
                return moved[bone.name]
            parent = pose_of(bone.parent) if bone.parent else None
            local = basis.get(bone.name)
            if parent is None and local is None:
                moved[bone.name] = None
                return None
            local = local or Matrix.Identity(4)
            if bone.parent:
                p = parent if parent is not None else bone.parent.matrix_local
                m = bone.convert_local_to_pose(local, bone.matrix_local, parent_matrix=p,
                                               parent_matrix_local=bone.parent.matrix_local)
            else:
                m = bone.convert_local_to_pose(local, bone.matrix_local)
            moved[bone.name] = m
            return m

        delta = np.zeros_like(co)
        for name, (idx, w) in weights.items():
            m = pose_of(bones[name])
            if m is None:
                continue
            skin = np.array(postmat @ m @ bones[name].matrix_local.inverted() @ premat)
            pts = co[idx]
            delta[idx] += w[:, None] * (pts @ skin[:3, :3].T + skin[:3, 3] - pts)
        delta[has_weight] /= total[has_weight, None]
        block = mesh.shape_key_add(name=pose.name, from_mix=False)
        block.data.foreach_set("co", (co + delta).ravel())
        block.value = 0
