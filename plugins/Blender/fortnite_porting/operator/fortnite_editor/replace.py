"""Replace Selected: an imported character or prop in the scene swapped for an asset imported from the editor.

What counts as one import (its "asset"): its root and the objects under it that came with it. Editor imports tag
what they bring (TAG, an id per import); an untagged descendant belongs to the root when it shares one of its
collections (an import puts its objects in one collection). A character's root is its armature.

The new import takes the old root's place: its parent (and parent bone), its local transform (deltas too), its
collection, and for armatures the old action (when at least half its animated bones are in the new skeleton) with its
slot and settings, the NLA tracks, and the pose (bones by name). Objects parented under the old root that didn't come
with it (a weapon on the hand) move to the new root. Then the old asset's objects are removed, and of the data they
used only what nothing uses any more (actions are never removed: they're animation work).

Pure bpy, no editor state: tests/plugin/editor_replace_check.py builds scenes and checks it headless."""
import re

import bpy

TAG = "_fp_import"      # the editor import an object came with ("_": hidden from the Custom Properties panel)
REMOVABLE = {'OBJECT', 'MESH', 'ARMATURE', 'CURVE', 'CURVES', 'MATERIAL', 'IMAGE', 'NODETREE', 'TEXTURE', 'LATTICE',
             'POINTCLOUD', 'VOLUME', 'GREASEPENCIL', 'GREASEPENCIL_V3', 'CACHEFILE', 'PARTICLE', 'LIGHT', 'CAMERA',
             'SPEAKER', 'LIGHT_PROBE', 'METABALL', 'FONT'}
BONE_PATH = re.compile(r'pose\.bones\["((?:[^"\\]|\\.)*)"\]')


def tag(obj):
    return obj.get(TAG, "") if obj is not None else ""


def tag_objects(objects, import_id):
    for o in objects:
        if o.library is None:
            o[TAG] = import_id


# ------------------------------------------------------------------ what one import is

def _with_root(root, obj):
    """Whether `obj`, under `root`, came with root's import."""
    mine, theirs = tag(root), tag(obj)
    if theirs:
        return theirs == mine
    return not obj.users_collection or bool(set(obj.users_collection) & set(root.users_collection))


def import_root(obj):
    """The root of the import `obj` came with: up its parents while they came with it; an armature is a root."""
    o = obj
    while o.parent is not None and o.type != 'ARMATURE':
        p = o.parent
        if tag(o) or tag(p):
            if tag(o) != tag(p):
                break
        elif not set(p.users_collection) & set(o.users_collection):
            break
        o = p
    return o


def asset_objects(root):
    """(the root and what came with it, the descendants that didn't)."""
    mine, foreign = [root], []
    for d in root.children_recursive:
        if any(a in foreign for a in _ancestors(d, root)):
            continue        # under a foreign child: goes with it
        (mine if _with_root(root, d) else foreign).append(d)
    return mine, foreign


def _ancestors(obj, stop):
    out, p = [], obj.parent
    while p is not None and p != stop:
        out.append(p)
        p = p.parent
    return out


def _has_mesh(obj):
    return obj.type == 'MESH' or any(c.type == 'MESH' for c in obj.children_recursive)


def find_target(context):
    """(root, None) for the selected character or prop, else (None, why not)."""
    selected = list(getattr(context, "selected_objects", None) or [])
    if not selected:
        return None, "Select an imported character or prop in the scene"
    roots = list(dict.fromkeys(import_root(o) for o in selected))
    if len(roots) > 1:
        return None, "Select one character or prop (%d are selected)" % len(roots)
    root = roots[0]
    if root.library is not None or root.override_library is not None:
        return None, "%s is linked from another file" % root.name
    if root.type not in ('ARMATURE', 'MESH', 'EMPTY') or (root.type == 'EMPTY' and not _has_mesh(root)):
        return None, "%s is not a character or prop (%s)" % (root.name, root.type.title())
    return root, None


def kind_of(root):
    return "character" if root.type == 'ARMATURE' else "prop"


# ------------------------------------------------------------------ the new import's root

def _shapes(objects):
    """Objects used as bone shapes (rig widgets the import may have made): never the asset's root."""
    found = set()
    for o in objects:
        if o.type == 'ARMATURE' and o.pose is not None:
            found.update(pb.custom_shape for pb in o.pose.bones if pb.custom_shape is not None)
    return found


def new_roots(new_objects, character):
    """(main root, other roots) of what an import brought; (None, []) when it brought nothing placeable."""
    new = set(new_objects)
    shapes = _shapes(new_objects)
    found = [o for o in new_objects if (o.parent is None or o.parent not in new) and o.users_collection and o not in shapes]
    if not found:
        return None, []
    size = {o: len(o.children_recursive) for o in found}
    arms = [o for o in found if o.type == 'ARMATURE']
    pool = arms if (character and arms) else found
    main = max(pool, key=lambda o: (o.type == 'ARMATURE', size[o]))
    cols = set(main.users_collection)
    others = [o for o in found if o is not main and set(o.users_collection) & cols and o.type != 'ARMATURE']
    return main, others


# ------------------------------------------------------------------ carrying over

TRANSFORM = ("location", "rotation_mode", "rotation_quaternion", "rotation_euler", "rotation_axis_angle", "scale",
             "delta_location", "delta_rotation_quaternion", "delta_rotation_euler", "delta_scale")


def copy_transform(old, new):
    for name in TRANSFORM:
        value = getattr(old, name)
        setattr(new, name, value if isinstance(value, str) else tuple(value))


def copy_parent(old, new):
    new.parent = old.parent
    if old.parent is not None:
        new.parent_type = old.parent_type
        if old.parent_type == 'BONE':
            new.parent_bone = old.parent_bone
    new.matrix_parent_inverse = old.matrix_parent_inverse.copy()


def animated_bones(action, slot):
    """Bone names the action's channels for `slot` animate (any slot's when slot is None)."""
    names = set()
    for layer in getattr(action, "layers", ()):
        for strip in layer.strips:
            bags = [strip.channelbag(slot)] if slot is not None else list(getattr(strip, "channelbags", ()))
            for bag in bags:
                for fc in (bag.fcurves if bag is not None else ()):
                    m = BONE_PATH.match(fc.data_path)
                    if m:
                        names.add(m.group(1).replace('\\"', '"'))
    return names


def copy_action(old, new, notes):
    """The old armature's action (and slot) on the new one, when its bones are there. True when assigned."""
    ad = old.animation_data
    if ad is None or ad.action is None:
        return False
    action, slot = ad.action, ad.action_slot
    animated = animated_bones(action, slot)
    missing = sorted(animated - set(new.data.bones.keys()))
    if animated and len(missing) * 2 > len(animated):
        notes.append("action %s kept off: %d of its %d bones aren't in %s" % (action.name, len(missing), len(animated),
                                                                              new.name))
        return False
    nd = new.animation_data or new.animation_data_create()
    nd.action = action
    if slot is not None and nd.action_slot != slot:
        nd.action_slot = slot
    nd.action_extrapolation, nd.action_blend_type, nd.action_influence = (ad.action_extrapolation, ad.action_blend_type,
                                                                          ad.action_influence)
    notes.append("action %s" % action.name + (" (%d bones not in the new skeleton)" % len(missing) if missing else ""))
    return True


STRIP = ("frame_start", "frame_end", "action_frame_start", "action_frame_end", "scale", "repeat", "blend_type",
         "extrapolation", "influence", "use_animated_influence", "blend_in", "blend_out", "use_auto_blend", "mute",
         "use_reverse", "use_sync_length")


def copy_nla(old, new, notes):
    ad = old.animation_data
    if ad is None or not ad.nla_tracks:
        return
    nd = new.animation_data or new.animation_data_create()
    skipped = 0
    for track in ad.nla_tracks:
        t = nd.nla_tracks.new()
        t.name, t.mute, t.lock = track.name, track.mute, track.lock
        for s in track.strips:
            if s.type != 'CLIP' or s.action is None:
                skipped += 1
                continue
            n = t.strips.new(s.name, int(s.frame_start), s.action)
            for name in STRIP:
                try:
                    setattr(n, name, getattr(s, name))
                except (AttributeError, TypeError, RuntimeError):
                    pass
            if getattr(s, "action_slot", None) is not None:
                n.action_slot = s.action_slot
    notes.append("%d NLA track%s" % (len(ad.nla_tracks), "" if len(ad.nla_tracks) == 1 else "s")
                 + (" (%d meta/transition strips left out)" % skipped if skipped else ""))


POSE = ("rotation_mode", "location", "rotation_quaternion", "rotation_euler", "rotation_axis_angle", "scale")


def copy_pose(old, new):
    """The pose, bone by bone (by name), with numeric bone properties the new bone has too (IK/FK switches)."""
    n = 0
    for pb in old.pose.bones:
        nb = new.pose.bones.get(pb.name)
        if nb is None:
            continue
        for name in POSE:
            value = getattr(pb, name)
            setattr(nb, name, value if isinstance(value, str) else tuple(value))
        for key in pb.keys():
            if key in nb.keys() and isinstance(pb[key], (int, float)) and not isinstance(nb[key], str):
                try:
                    nb[key] = pb[key]
                except TypeError:
                    pass
        n += 1
    return n


def _scene_master(col):
    return next((s for s in bpy.data.scenes if s.collection == col), None) is not None


def _parents_of(col):
    """The collections (scene masters too) that hold `col`."""
    out = [c for c in bpy.data.collections if col.name in c.children and c.children[col.name] == col]
    out += [s.collection for s in bpy.data.scenes if col.name in s.collection.children and s.collection.children[col.name] == col]
    return out


def place_in_collections(old_root, old_set, new_main, new_set, moved):
    """The new import where the old one was. When the old root sat alone in its import's collection, the new
    import's collection goes where that one was (and the old one is removed once empty); else the new import's
    objects go into the old root's collections. Returns the old collections to remove after the old objects go."""
    old_cols = list(old_root.users_collection)
    new_cols = [c for c in new_main.users_collection if not _scene_master(c)
                and all(o in new_set for o in c.objects)]
    own = [c for c in old_cols if not _scene_master(c) and not c.children and all(o in old_set for o in c.objects)]
    if len(old_cols) == 1 and own and len(new_cols) == 1:
        old_col, new_col = own[0], new_cols[0]
        homes = _parents_of(old_col)
        for p in _parents_of(new_col):
            if p not in homes:
                p.children.unlink(new_col)
        for p in homes:
            if new_col.name not in p.children:
                p.children.link(new_col)
        new_col.hide_viewport, new_col.hide_render = old_col.hide_viewport, old_col.hide_render
        return [old_col], "collection %s (in %s's place)" % (new_col.name, old_col.name)
    for o in moved:
        for c in old_cols:
            if o.name not in c.objects:
                c.objects.link(o)
        for c in list(o.users_collection):
            if c not in old_cols and c in new_cols:
                c.objects.unlink(o)
    emptied = [c for c in new_cols if not c.objects and not c.children]
    for c in emptied:
        bpy.data.collections.remove(c)
    return [], "collection %s" % ", ".join(c.name for c in old_cols)


# ------------------------------------------------------------------ removing the old import

def _closure(seeds):
    """Every ID the seeds use, directly or through others (bpy.data.user_map inverted)."""
    uses = {}
    for idb, users in bpy.data.user_map().items():
        for u in users:
            uses.setdefault(u, set()).add(idb)
    seen, todo = set(seeds), list(seeds)
    while todo:
        for y in uses.get(todo.pop(), ()):
            if y not in seen:
                seen.add(y)
                todo.append(y)
    return seen - set(seeds)


def remove_objects(objects):
    """Remove the objects, then of the data they used what has no user left (repeated: a material freed frees its
    images). Never actions, collections, scenes or linked data. Returns (objects removed, data removed)."""
    objects = [o for o in objects if o.library is None]
    used = _closure(objects)
    count = len(objects)
    bpy.data.batch_remove(objects)
    removed = 0
    while True:
        dead = []
        for idb in list(used):
            try:
                if idb.users == 0 and idb.id_type in REMOVABLE and idb.library is None:
                    dead.append(idb)
            except ReferenceError:
                used.discard(idb)
        if not dead:
            break
        used.difference_update(dead)
        removed += len(dead)
        bpy.data.batch_remove(dead)
    return count, removed


# ------------------------------------------------------------------ the replace

def replace(old_root, new_objects, view_layer=None):
    """Put the import that brought `new_objects` in old_root's place and remove old_root's import.
    Returns (new root or None, notes): None when the import brought nothing to place (the old one stays)."""
    character = old_root.type == 'ARMATURE'
    main, others = new_roots(new_objects, character)
    if main is None:
        return None, ["the import brought nothing to put in its place: %s stays" % old_root.name]
    if view_layer is not None:
        view_layer.update()     # the new objects' world matrices
    new_set = set(new_objects)
    old_set, foreign = asset_objects(old_root)
    old_set = [o for o in old_set if o not in new_set]
    notes = []
    old_world = old_root.matrix_world.copy()
    for o in old_set:       # an object in edit or pose mode can't be removed safely
        if o.mode != 'OBJECT' and view_layer is not None and view_layer.objects.active == o:
            bpy.ops.object.mode_set(mode='OBJECT')
    import_world = main.matrix_world.copy()
    copy_parent(old_root, main)
    copy_transform(old_root, main)
    notes.append("transform" + (", parent %s" % old_root.parent.name if old_root.parent else ""))
    for o in others:        # extra roots keep where they were relative to the main one
        o.matrix_world = old_world @ import_world.inverted_safe() @ o.matrix_world
    if character and main.type == 'ARMATURE':
        had_action = copy_action(old_root, main, notes)
        copy_nla(old_root, main, notes)
        posed = copy_pose(old_root, main)
        if not had_action:
            notes.append("pose (%d bones)" % posed)
    bones = set(main.data.bones.keys()) if main.type == 'ARMATURE' else set()
    for o in foreign:
        if o.parent == old_root:        # the new root sits where the old one did: the same parenting holds
            o.parent = main
            if o.parent_type == 'BONE' and o.parent_bone not in bones:
                o.parent_type = 'OBJECT'
        else:       # under one of the old objects: onto the new root, where it is now
            world = o.matrix_world.copy()
            o.parent, o.parent_type = main, 'OBJECT'
            o.matrix_parent_inverse = old_world.inverted_safe()
            o.matrix_basis = world
    if foreign:
        notes.append("%d attached object%s moved over" % (len(foreign), "" if len(foreign) == 1 else "s"))
    moved = [main] + [d for d in main.children_recursive if d in new_set]
    for o in others:
        moved += [o] + [d for d in o.children_recursive if d in new_set]
    stale, where = place_in_collections(old_root, set(old_set), main, new_set, moved)
    notes.append(where)
    name = old_root.name
    count, data = remove_objects(old_set)
    for c in stale:
        if not c.objects and not c.children:
            bpy.data.collections.remove(c)
    notes.append("removed %s: %d object%s, %d unused data" % (name, count, "" if count == 1 else "s", data))
    if view_layer is not None:
        view_layer.update()     # the removed objects' bases go
    if view_layer is not None and main.name in view_layer.objects:
        for o in view_layer.objects:
            if o is not None and o.select_get():
                o.select_set(False)
        main.select_set(True)
        view_layer.objects.active = main
    return main, notes
