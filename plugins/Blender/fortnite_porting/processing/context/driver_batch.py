"""Drivers built on a scratch object and copied whole onto their owner. Blender looks a variable's driver up among all
of its owner's drivers on every change, so filling thousands of drivers in place is quadratic (the RigLogic face's
63k variables took 85 s); a copy is one step.

    with Batch() as batch:
        driver = batch.new(pose_bone, '["key"]')      # fill it (variables, expression) before the next new()
"""
import bpy


class Batch:
    def __init__(self):
        self.scratch = bpy.data.objects.new("FPMP driver scratch", None)
        self.scratch.animation_data_create()
        self.pending = None

    def new(self, owner, path, index=-1):
        """A driver for owner's property path (relative to owner: a pose bone, a shape key, an ID), filled by the
        caller; it lands on the owner's ID at the next new() or at the end."""
        self.commit()
        full = owner.path_from_id(path) if not isinstance(owner, bpy.types.ID) else path
        fc = self.scratch.animation_data.drivers.new(full, index=max(index, 0))
        fc.driver.type = 'SCRIPTED'
        self.pending = (owner.id_data, fc)
        return fc.driver

    def commit(self):
        if self.pending is None:
            return
        target, fc = self.pending
        self.pending = None
        if target.animation_data is None:
            target.animation_data_create()
        drivers = target.animation_data.drivers
        old = drivers.find(fc.data_path, index=fc.array_index)
        if old is not None:         # a shared material another import drove: this one takes over
            drivers.remove(old)
        made = drivers.from_existing(src_driver=fc).driver
        # the copy keeps the scratch's "can't evaluate" flag (the path doesn't resolve there): setting its expression
        # clears it and has the owner's drivers re-read
        made.is_valid = True
        made.expression = made.expression
        self.scratch.animation_data.drivers.remove(fc)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        try:
            if exc[0] is None:
                self.commit()
        finally:
            bpy.data.objects.remove(self.scratch)
