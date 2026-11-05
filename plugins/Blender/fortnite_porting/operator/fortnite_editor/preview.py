"""A live 3D preview in a tab's well: the hidden preview scene (preview_scene) drawn off screen through a 3D view
borrowed from a screen no window shows (or a shown one, put back after each draw; preview_raster when the file has
none), with its own orbit (drag), pan (Shift+drag), zoom (wheel) and frame (double click). A timer plays it at
FPS while a tab shows it and drops the scene once none does."""
import math
import time

import bpy
import gpu
from bpy.app.handlers import persistent
from mathutils import Matrix, Vector

from . import host, preview_raster, preview_scene
from .ui import paint
from .ui.frame import Hit

FPS = 30.0
SHADING = {"EEVEE": 'BLENDER_EEVEE', "WORKBENCH": 'BLENDER_WORKBENCH'}
STATS = {"stale": 0, "draws": 0, "draw_ms": 0.0, "step_ms": 0.0, "steps": 0, "path": ""}       # read by the checks


class Camera:
    """Orbit round `target`: yaw about Z, elevation above the floor, distance; fov in radians."""

    def __init__(self):
        self.target, self.yaw, self.elev, self.dist, self.fov = Vector((0, 0, 1)), 0.5, 0.15, 4.0, math.radians(35)
        self.held = None        # the camera as a drag found it

    def view_matrix(self):
        rot = Matrix.Rotation(self.yaw, 4, 'Z') @ Matrix.Rotation(math.pi / 2 - self.elev, 4, 'X')
        return (Matrix.Translation(self.target) @ rot @ Matrix.Translation((0, 0, self.dist))).inverted()

    def projection(self, w, h):
        near, far = max(self.dist * 0.01, 0.01), self.dist * 20 + 50
        f = 1.0 / math.tan(self.fov / 2)
        aspect = w / max(h, 1)
        return Matrix(((f / aspect, 0, 0, 0), (0, f, 0, 0),
                       (0, 0, (far + near) / (near - far), 2 * far * near / (near - far)), (0, 0, -1, 0)))

    def frame(self, objects, graph, frames=None):
        """Fit the objects' evaluated bounds (over `frames` of the preview scene: a moving body stays in view)."""
        lo, hi = Vector((1e9,) * 3), Vector((-1e9,) * 3)
        if frames:
            for f in frames:
                preview_scene.step(float(f))
                for p in _bounds(objects, preview_scene.get().view_layers[0].depsgraph):
                    lo, hi = Vector(map(min, lo, p)), Vector(map(max, hi, p))
            preview_scene.step(PLAY["frame"])
        else:
            for p in _bounds(objects, graph):
                lo, hi = Vector(map(min, lo, p)), Vector(map(max, hi, p))
        if lo.x > hi.x:
            return
        self.target = (lo + hi) / 2
        radius = max((hi - lo).length / 2, 0.1)
        self.dist = radius / math.sin(self.fov / 2) * 1.05


def _bounds(objects, graph):
    """The corners of the objects' evaluated bounds, in world space."""
    for ob in objects:
        ev = ob.evaluated_get(graph) if graph is not None else ob
        if ob.type not in {'MESH', 'CURVE', 'POINTCLOUD', 'CURVES', 'VOLUME'} or ob.hide_render or ob.hide_viewport:
            continue
        for corner in ev.bound_box:
            yield ev.matrix_world @ Vector(corner)


class View:
    """One preview widget: its camera, its offscreen buffer, when it last drew."""

    def __init__(self, owner):
        self.owner, self.camera = owner, Camera()
        self.offscreen, self.size, self.drawn, self.framed = None, (0, 0), 0.0, False
        self.engine, self.notch, self.drawn_once = "EEVEE", None, False

    def free(self):
        if self.offscreen is not None:
            self.offscreen.free()
        self.offscreen = None


VIEWS = {}      # owner (a tab id) -> View
ON_STEP = []    # fn(frame before, frame now, playing, wrapped): what follows the playhead (sound)
PLAY = {"on": True, "speed": 1.0, "start": 1, "end": 60, "loop": True, "frame": 1.0, "last": 0.0}


def view_for(owner):
    if owner not in VIEWS:
        VIEWS[owner] = View(owner)
    return VIEWS[owner]


# ------------------------------------------------------------------ the borrowed 3D view

def _shown_screens():
    return {w.screen.as_pointer() for w in bpy.context.window_manager.windows}


def borrowed():
    """(space, region, hidden): a 3D view to draw through, one on a screen no window shows first; None: there is none."""
    shown, found = _shown_screens(), None
    for screen in bpy.data.screens:
        hidden = screen.as_pointer() not in shown
        for area in screen.areas:
            if area.type != 'VIEW_3D':
                continue
            space = area.spaces.active
            # region.data is None until a window shows the area; region_3d reads the last region's data (the
            # main region's): None when it has none to draw with
            region = area.regions[-1] if len(area.regions) else None
            if region is None or region.type != 'WINDOW' or space.region_3d is None or space.local_view is not None:
                continue
            if hidden:
                return space, region, True
            found = found or (space, region, False)
    return found


def _render(v, w, h):
    """Draw the scene into v.offscreen through the borrowed view, its shading and overlays put back after."""
    scene = preview_scene.get()
    pick = borrowed()
    if scene is None:
        return False
    if pick is None:
        STATS["path"] = "raster"
        return preview_raster.draw(v.offscreen, scene, v.camera.view_matrix(), v.camera.projection(w, h))
    space, region, hidden = pick
    STATS["path"] = "hidden view" if hidden else "shown view"
    shading, overlays = space.shading.type, space.overlay.show_overlays
    if shading != 'RENDERED':
        space.shading.type = 'RENDERED'
    if overlays:
        space.overlay.show_overlays = False
    try:
        v.offscreen.draw_view3d(scene, scene.view_layers[0], space, region, v.camera.view_matrix(),
                                v.camera.projection(w, h), do_color_management=True)
    finally:
        if space.shading.type != shading:
            space.shading.type = shading
        if space.overlay.show_overlays != overlays:
            space.overlay.show_overlays = overlays
    return True


# ------------------------------------------------------------------ the widget

def draw(ui, r, owner, empty="", tool=None):
    """The preview in `r` of the well, with its input. Call from the tab's view(ui, well). `tool`:
    fn(context, dx, dy, camera, height, new press) a drag calls instead of orbiting."""
    t = ui.t
    v = view_for(owner)
    v.drawn = time.time()
    _start_timer()
    paint.rect(r, t.card, t.r_card)
    scene = preview_scene.get()
    if scene is None or not preview_scene.content():
        paint.text_in(empty or "Nothing to preview", r, t.text3, t.title, "center")
        return
    w, h = max(int(r.w), 16), max(int(r.h), 16)
    if v.offscreen is None or v.size != (w, h):
        v.free()
        v.offscreen, v.size, v.drawn_once = gpu.types.GPUOffScreen(w, h), (w, h), False
    t0 = time.perf_counter()
    if preview_scene.STATE["stale"]:     # the last picture until the timer evaluates the scene again
        STATS["stale"] += 1
        if v.drawn_once:
            paint.rect(r, (0, 0, 0, 0), t.r_card, image=v.offscreen.texture_color)
    elif _render(v, w, h):
        STATS["draws"] += 1
        STATS["draw_ms"] = (time.perf_counter() - t0) * 1000
        v.drawn_once = True
        paint.rect(r, (0, 0, 0, 0), t.r_card, image=v.offscreen.texture_color)
    _input(ui, r, v, tool)


def _input(ui, r, v, tool=None):
    from .ui.blender import HELD
    cam, store, sid = v.camera, ui.store, "preview:" + v.owner

    def drag(context, dx, dy):
        fresh = cam.held is None or cam.held[4] != store.press_at
        if tool is not None:
            if fresh:
                cam.held = (cam.yaw, cam.elev, cam.target.copy(), False, store.press_at)
            tool(context, dx, dy, cam, r.h, fresh)
            redraw()
            return
        if fresh:      # a new press
            cam.held = (cam.yaw, cam.elev, cam.target.copy(), HELD.get("shift"), store.press_at)
        yaw, elev, target, panning, _at = cam.held
        if panning:
            scale = cam.dist * 2 * math.tan(cam.fov / 2) / max(r.h, 1)
            rot = cam.view_matrix().inverted().to_3x3()
            cam.target = target - rot @ Vector((dx * scale, dy * scale, 0))
        else:
            cam.yaw = yaw - dx * 0.01
            cam.elev = min(max(elev - dy * 0.01, 0.0), 1.4)     # never under the floor (it hides everything)
        redraw()

    def refit(context):
        v.framed = False
        redraw()
    # the wheel scrolls an offset of its own (from mid-range, so it goes both ways): its change zooms
    store.scroll.setdefault(sid, 1e6)
    ui.frame.add(Hit(sid, r, ui.clip, "button", None, refit, "", (sid, 2e6), drag))
    notch = store.offset(sid)
    if v.notch is not None and notch != v.notch:
        cam.dist *= 1.1 ** ((notch - v.notch) / (48.0 * ui.t.s))
    v.notch = notch


def redraw():
    for area in host.editor_areas():
        if host.tab_id(area.spaces.active) in VIEWS:
            area.tag_redraw()


# ------------------------------------------------------------------ the timer

def set_range(start, end, loop_start=None):
    """Play [start, end]; looping goes back to `loop_start` (an emote's loop section after its intro), else start."""
    loop_start = start if loop_start is None else min(max(loop_start, start), end)
    PLAY.update(start=start, end=max(end, start), frame=float(start), loop_start=loop_start)


def _tick():
    showing = {host.tab_id(a.spaces.active) for a in host.editor_areas()} & set(VIEWS)
    if not showing:
        _drop()
        return None
    now = time.time()
    if not any(now - VIEWS[o].drawn < 1.0 for o in showing):
        return 0.25         # shown but not drawn (another window covers it): idle
    scene = preview_scene.ensure()      # (a save dropped it: back empty, the tab adds its content again)
    if preview_scene.STATE["stale"]:
        preview_scene.changed()
    dt, PLAY["last"] = min(now - PLAY["last"], 0.25) if PLAY["last"] else 0.0, now
    before, wrapped = PLAY["frame"], False
    if PLAY["on"]:
        back = PLAY.get("loop_start", PLAY["start"])
        span = PLAY["end"] - back + 1
        f = PLAY["frame"] + dt * scene.render.fps * PLAY["speed"]
        if f >= PLAY["end"] + 1:
            f = back + (f - back) % span if PLAY["loop"] else float(PLAY["end"])
            wrapped = True
        PLAY["frame"] = f
    for fn in ON_STEP:
        fn(before, PLAY["frame"], PLAY["on"], wrapped)
    for o in showing:
        v = VIEWS[o]
        engine = SHADING[v.engine]
        if scene.render.engine != engine:
            scene.render.engine = engine
    preview_scene.effects_shown(scene.render.engine != 'BLENDER_WORKBENCH')
    t0 = time.perf_counter()
    if preview_scene.step(PLAY["frame"]):
        STATS["steps"] += 1
        STATS["step_ms"] = (time.perf_counter() - t0) * 1000
    graph = scene.view_layers[0].depsgraph
    for o in showing:
        v = VIEWS[o]
        if not v.framed and preview_scene.content():
            span = PLAY["end"] - PLAY["start"]
            frames = [PLAY["start"] + span * i / 7 for i in range(8)] if span > 1 else None
            v.camera.frame(preview_scene.content(), graph, frames)
            v.framed = True
    redraw()
    # on a fixed beat: the tick and the redraw it asks for don't add to the interval
    due = PLAY.get("due", now) + 1.0 / FPS
    PLAY["due"] = due if due > now else now + 1.0 / FPS
    return max(PLAY["due"] - time.time(), 0.001)


def _start_timer():
    if not bpy.app.timers.is_registered(_tick):
        bpy.app.timers.register(_tick, first_interval=0.0)


def _drop():
    """No tab shows a preview: free the buffers, drop the scene."""
    for fn in ON_STEP:
        fn(PLAY["frame"], PLAY["frame"], False, False)
    for v in VIEWS.values():
        v.free()
    VIEWS.clear()
    PLAY["last"] = 0.0
    preview_scene.drop()


def _free_views(*_):
    for v in VIEWS.values():
        v.free()
    VIEWS.clear()


@persistent
def _before_exit(*_):
    # an Eevee buffer freed after Blender freed its images (Python's own cleanup at quit) crashes it
    _free_views()


def _imported(_label, _new, _message):
    if preview_scene.get() is not None:
        preview_scene.changed()     # an import may have replaced data the preview draws


def register():
    from . import importing
    preview_scene.register()
    if _imported not in importing.AFTER:
        importing.AFTER.append(_imported)
    if _before_exit not in bpy.app.handlers.exit_pre:
        bpy.app.handlers.exit_pre.append(_before_exit)


def unregister():
    from . import importing
    if _imported in importing.AFTER:
        importing.AFTER.remove(_imported)
    if _before_exit in bpy.app.handlers.exit_pre:
        bpy.app.handlers.exit_pre.remove(_before_exit)
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    _free_views()
    preview_scene.unregister()
