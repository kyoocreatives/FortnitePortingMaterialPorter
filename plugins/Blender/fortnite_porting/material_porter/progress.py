"""One log line per step of an import, with counts and timings, for the app's status line and log.

Stage lines are unindented (they take the status line); a step's items are indented four spaces.
ProgressMixin is ImportContext's first base, so it times FP's own steps without touching them.
"""
import time

import bpy

from ..logger import Log
from ..processing.enums import EExportType, EPrimitiveExportType

MAPS = (EExportType.WORLD, EExportType.PREFAB)
# fetched from the app per material, in the order the line names them
FETCHED = (("texture", "texture"), ("graph", "graph"), ("collection", "collection"))


def seconds(s):
    return "%d ms" % (s * 1000) if s < 1 else "%.1f s" % s


def took(t0):
    return seconds(time.perf_counter() - t0)


def stage(text):
    Log.info(text)


def item(text, t0=None):
    Log.info("    " + text + (" (%s)" % took(t0) if t0 is not None else ""))


def _plural(n, word, many=None):
    return "%d %s" % (n, word if n == 1 else many or word + "s")


def thousands(n):
    return "%.1fk" % (n / 1000) if n >= 1000 else str(n)


def _meshes(data):
    """The meshes an export imports (an outfit's style swaps replace the default part of their type)."""
    meshes = list(data.get("Meshes") or [])
    if data.get("OverrideMeshes"):
        swapped = {m.get("Type") for m in data["OverrideMeshes"]}
        meshes = list(data["OverrideMeshes"]) + [m for m in meshes if m.get("Type") not in swapped]
    out, stack = [], meshes
    while stack:
        mesh = stack.pop()
        out.append(mesh)
        stack.extend(mesh.get("Children") or [])
    return out


def export_begin(ctx, data):
    """What the export holds: its meshes and the materials they end up with."""
    from .mesh_hooks import final_materials
    kind = EPrimitiveExportType(data.get("PrimitiveType"))
    if kind != EPrimitiveExportType.MESH:
        stage("%s: %s" % (data.get("Name"), kind.name.lower().replace("_", " ")))
        return
    meshes = [m for m in _meshes(data) if not m.get("IsEmpty")]
    overrides = data.get("OverrideMaterials") or []
    paths = {m.get("Path") for mesh in meshes for m in final_materials(mesh, overrides).values()}
    stage("%s: %s, %s" % (data.get("Name"), _plural(len(meshes), "mesh", "meshes"), _plural(len(paths), "material")))


def mesh_line(ctx, path, obj, t0):
    if obj is None or ctx.type in MAPS:      # a map's thousands of meshes: actor counts instead
        return
    from ..processing.utils import get_armature_mesh
    parts = []
    mesh = get_armature_mesh(obj)
    if mesh is not None:
        me = mesh.data
        tris = len(me.loops) == 3 * len(me.polygons)
        parts.append("%s %s" % (thousands(len(me.polygons)), "tris" if tris else "faces"))
        if me.shape_keys is not None and len(me.shape_keys.key_blocks) > 1:
            parts.append(_plural(len(me.shape_keys.key_blocks) - 1, "morph"))
    if obj.type == 'ARMATURE':
        parts.append("skeleton of %s" % _plural(len(obj.data.bones), "bone"))
    item("mesh %s: %s" % (path.split(".")[-1], ", ".join(parts)), t0)


def app_waits(ctx):
    """(count, seconds) per bridge route this import asked so far."""
    from . import hook
    job = hook._job
    return dict(job["app"].waits) if job["key"] is ctx and job["app"] is not None else {}


def material_line(ctx, material, before, t0):
    """How a slot's material came to be: exact, reused, FP's shader or hidden, and what was fetched from the app for it."""
    record = getattr(ctx, "mp_material", None) or {}
    outcome = record.get("outcome")
    if outcome is None:
        outcome = "hidden" if material in ctx.full_vertex_crunch_materials else "FP shader"
    if ctx.type in MAPS and outcome in ("reused", "hidden"):
        return      # a map reuses most materials thousands of times
    after = app_waits(ctx)
    fetched = [_plural(after.get(route, (0, 0))[0] - before.get(route, (0, 0))[0], word)
               for route, word in FETCHED if after.get(route, (0, 0))[0] > before.get(route, (0, 0))[0]]
    wait = sum(v[1] for v in after.values()) - sum(v[1] for v in before.values())
    text = "material %s: %s" % (record.get("name") or material.name, outcome)
    if outcome != "reused":
        text += " (%s)" % took(t0)
    if fetched:
        text += ", %s from the app (%s)" % (", ".join(fetched), seconds(wait))
    item(text)
    for note in record.get("notes") or []:
        Log.info("        " + note)


class ProgressMixin:
    """Times the import's steps (ImportContext's first base)."""

    def run(self, data):
        export_begin(self, data)
        super().run(data)

    def load_blend_data(self):
        t0, groups = time.perf_counter(), len(bpy.data.node_groups)
        super().load_blend_data()
        if len(bpy.data.node_groups) > groups:
            item("FP's shader library appended", t0)

    def import_mesh(self, path, can_reorient=True):
        t0 = time.perf_counter()
        obj = super().import_mesh(path, can_reorient=can_reorient)
        mesh_line(self, path, obj, t0)
        return obj

    def import_material(self, material_slot, material_data, meta, as_material_data=False):
        t0, before, outer = time.perf_counter(), app_waits(self), getattr(self, "mp_material", None)
        self.mp_material = {"name": material_data.get("Name")}
        try:
            out = super().import_material(material_slot, material_data, meta, as_material_data)
            if not as_material_data:
                material_line(self, material_slot.material, before, t0)
            return out
        finally:
            self.mp_material = outer

    def import_pose_asset(self, path, override_skeleton=None):
        t0 = time.perf_counter()
        super().import_pose_asset(path, override_skeleton)
        if self.type not in MAPS:
            on = " on %s" % override_skeleton.name if override_skeleton is not None else ""
            item("pose asset %s%s" % (path.split(".")[-1], on), t0)

    def import_anim_data(self, data, override_skeleton=None):
        t0 = time.perf_counter()
        sections, sounds = len(data.get("Sections") or []), len(data.get("Sounds") or [])
        stage("Animation: %s%s" % (_plural(sections, "section"), ", %s" % _plural(sounds, "sound") if sounds else ""))
        super().import_anim_data(data, override_skeleton)
        item("done", t0)

    def import_anim(self, path, override_skeleton=None):
        t0 = time.perf_counter()
        action, dto = super().import_anim(path, override_skeleton)
        start, end = action.frame_range
        item("animation %s: %d frames" % (path.split(".")[-1], round(end - start) + 1), t0)
        return action, dto


def actor(ctx, index):
    """A map's actors as a count every so often, not a line each."""
    total = len(ctx.meshes)
    if getattr(ctx, "mp_actors_t0", None) is None:     # the first actor (index 0 may be an effect's empty)
        ctx.mp_actors_t0 = time.perf_counter()
        stage("Placing %s" % _plural(total, "actor"))
    step = max(100, total // 20)
    if index and index % step == 0:
        item("actors %d/%d" % (index, total), ctx.mp_actors_t0)


def actors_done(ctx):
    if ctx.type in MAPS and getattr(ctx, "mp_actors_t0", None) is not None:
        item("actors %d/%d" % (len(ctx.meshes), len(ctx.meshes)), ctx.mp_actors_t0)


def instances(name, index, total):
    if index == 0:
        item("%s: %s" % (name, _plural(total, "instance")))
