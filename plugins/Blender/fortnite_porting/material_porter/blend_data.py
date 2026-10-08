"""Data appended from FP's .blend files: duplicate images merged, what an import didn't use removed."""
import os

import bpy

# datablocks this session appended from FP's data files
appended_ids: list = []


def appended(data_to):
    for ids in (data_to.node_groups, data_to.materials, data_to.images, data_to.objects, data_to.fonts):
        appended_ids.extend(i for i in ids if i is not None)


def merge_duplicate_images():
    """Merge copies of the same file (same colour space and alpha) that appended node groups bring (image.001...). Returns the count removed."""
    kept, gone = {}, 0
    for img in sorted(bpy.data.images, key=lambda i: (len(i.name), i.name)):
        if img.source != 'FILE' or not img.filepath or img.library is not None or img.packed_file is not None:
            continue
        key = (os.path.normcase(os.path.abspath(bpy.path.abspath(img.filepath))), img.colorspace_settings.name, img.alpha_mode)
        first = kept.setdefault(key, img)
        if first is img:
            continue
        img.user_remap(first)
        bpy.data.images.remove(img)
        gone += 1
    if gone:
        print("[material_porter] %d duplicate images merged" % gone)
    return gone


def drop_unused_blend_data():
    """Remove appended datablocks nothing uses (shader library groups, packed textures, bone shapes)."""
    from ..utils import loaded_versions
    removed = 0
    while True:
        alive = []
        for i in appended_ids:
            try:
                i.users
            except ReferenceError:
                continue
            alive.append(i)
        appended_ids[:] = alive
        unused = [i for i in alive if i.users == 0 and not i.use_fake_user]
        if not unused:
            break
        bpy.data.batch_remove(unused)
        removed += len(unused)
    if removed:
        loaded_versions.clear()
    return removed
