"""Data appended from FP's .blend files: duplicate images merged, what an import didn't use removed."""
import os

import bpy

# the groups, materials, images... this session appended from FP's data files
appended_ids: list = []


def appended(data_to):
    for ids in (data_to.node_groups, data_to.materials, data_to.images, data_to.objects, data_to.fonts):
        appended_ids.extend(i for i in ids if i is not None)


def merge_duplicate_images():
    """An appended node group brings its images again (image.001, .002...), each a copy in GPU memory: copies of the
    same file (colour space and alpha alike) are merged into the first. Returns how many went."""
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
    """What FP's data files brought that the import didn't use goes again (its shader library's groups, their packed
    textures, bone shapes); the next import appends what it needs."""
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
