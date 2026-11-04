"""What the Effects tab plays: a copy of one of the scene's particle effects (its empties and pieces, sharing their
data) under a carrier empty in the preview scene, replayed the plugin's way (effect_replay.play) over a loop of the
preview's frames. Moving, turning or scaling the carrier while it plays keys it at the playhead; the replay that
follows reads those keys, so trails and world-space particles trail the motion. Writes data: never from a draw."""
import math

import bpy
from mathutils import Matrix, Vector

from . import preview, preview_scene

CARRIER = ".MP Preview Carrier"
LOOP_FRAMES = 90        # a looping effect's preview: 3 s at 30 fps
STATE = {"root": None, "copy": None, "carrier": None, "busy": None, "error": None, "muted": set(), "solo": None,
         "moved": False, "baked_s": 0.0}
TOOLS = (("view", "View"), ("move", "Move"), ("rotate", "Rotate"), ("scale", "Scale"))
TOOL = {"id": "view"}


def roots(scene=None):
    """The user's scene's replayable effects: [(owner name, [root])], owners sorted, "Scene" for unheld ones."""
    from ...material_porter import effects
    from ...material_porter.effect_replay import holder_of
    scene = scene or bpy.context.scene
    if scene.get(preview_scene.TAG):
        return []
    groups = {}
    for ob in scene.objects:
        if ob.get(effects.KEY) == "System" and ob.get("mp_effect_program") and not ob.get(preview_scene.TAG):
            holder = holder_of(ob)
            groups.setdefault(holder.name if holder is not None else "Scene", []).append(ob)
    return sorted((k, sorted(v, key=lambda o: o.name)) for k, v in groups.items())


def emitters(root):
    """[(emitter name, [materials])] of an effect, from its emitter empties' pieces."""
    from ...material_porter import effects
    out = []
    for node in root.children if root else ():
        name = node.get(effects.KEY_EMITTER)
        if not name:
            continue
        mats = []
        for piece in node.children:
            for slot in getattr(piece, "material_slots", ()):
                if slot.material is not None and slot.material not in mats:
                    mats.append(slot.material)
        out.append((name, mats))
    return out


def copy_root():
    ob = bpy.data.objects.get(STATE["copy"]) if STATE["copy"] else None
    return ob if ob is not None and ob.get(preview_scene.TAG) == 2 else None


def carrier():
    ob = bpy.data.objects.get(STATE["carrier"]) if STATE["carrier"] else None
    return ob if ob is not None and ob.get(preview_scene.TAG) == 2 else None


# ------------------------------------------------------------------ show one

def show(root, game=None):
    """Copy `root`'s effect (the user's) into the preview and replay it there. With `game` (a listing item), `root`
    is already the preview's own: a game effect appended from its library."""
    STATE.update(root=root.name if game is None else None, game=game, error=None, muted=set(), solo=None, moved=False)
    if game is None:
        preview_scene.clear()
    scene = preview_scene.ensure()
    made = {}
    for ob in [root] + list(root.children_recursive):
        if ob.get("mp_effect") == "Particles":
            continue        # the scene's own replay: the preview makes its own
        if game is not None:
            made[ob] = ob
            continue
        c = ob.copy()
        c.animation_data_clear()
        made[ob] = c
    for ob, c in made.items():
        c.parent = made.get(ob.parent)
        if ob.parent in made:
            c.matrix_parent_inverse = ob.matrix_parent_inverse.copy()
    copy = made[root]
    for key in ("Repeats", "Lengths", "mp_effect_bone", "mp_effect_offset"):
        if key in copy:
            del copy[key]
    hold = bpy.data.objects.new(CARRIER, None)
    hold.empty_display_size = 0.2
    copy.parent, copy.parent_type = hold, 'OBJECT'
    copy.matrix_parent_inverse.identity()
    copy.matrix_basis = Matrix.Scale(_size(root), 4)       # its own scale only: placed at the carrier
    preview_scene.track([hold] + ([] if game is not None else list(made.values())))     # (appended: tracked)
    preview_scene.add([hold] + list(made.values()))
    STATE.update(copy=copy.name, carrier=hold.name)
    scene.frame_start, scene.frame_end = 1, LOOP_FRAMES
    copy["Start Frame"], copy["Loop"] = 1, True
    preview.set_range(1, LOOP_FRAMES)
    bake()
    for v in preview.VIEWS.values():
        v.framed = False        # fit on the effect it shows; later replays (moves) keep the camera


def _size(root):
    return sum(root.matrix_world.to_scale()) / 3


def bake():
    """Replay the preview's copy (the carrier's keys moving it): its particles for every frame of the loop."""
    import time
    from ...material_porter import effect_replay
    copy = copy_root()
    if copy is None:
        return
    scene = preview_scene.get()
    _sync_matrices(scene)
    hold = carrier()
    # no recorded motion: replayed standing free where the carrier puts it (the plugin's plain path; a parent would
    # send it down the moving one), then put back under the carrier
    still = hold is not None and hold.animation_data is None
    if still:
        local = copy.matrix_basis.copy()
        world = copy.matrix_world.copy()
        copy.parent = None
        copy.matrix_world = world
    t0 = time.perf_counter()
    lines = []
    try:
        with bpy.context.temp_override(scene=scene, view_layer=scene.view_layers[0]):
            for line in effect_replay.play(copy):
                lines.append(line)
    except Exception as e:      # the replay failed: shown in the tab
        STATE["error"] = "Replay failed: %s" % e
    finally:
        if still:
            copy.parent = hold
            copy.matrix_parent_inverse.identity()
            copy.matrix_basis = local
    made = [o for o in scene.collection.all_objects if o.get("mp_effect") == "Particles" and not o.get(preview_scene.TAG)]
    preview_scene.track(made)
    STATE["baked_s"] = time.perf_counter() - t0
    STATE["log"] = lines[-1] if lines else ""
    apply_visibility()
    preview_scene.changed()
    preview.redraw()


def _sync_matrices(scene):
    """The replay reads matrix_world, which only the active scene's evaluation writes back: set it from the hidden
    scene's evaluation, parents first."""
    preview_scene.changed()
    graph = scene.view_layers[0].depsgraph

    def depth(ob):
        return 0 if ob.parent is None else 1 + depth(ob.parent)
    for ob in sorted(preview_scene.content(), key=depth):
        ob.matrix_world = ob.evaluated_get(graph).matrix_world.copy()


def show_game(item):
    """A game effect the scene doesn't have: exported, imported once into its library by a background Blender,
    appended into the preview and replayed looping there."""
    import hashlib
    from . import preview_library
    from .state import client
    STATE.update(game=item, root=None, error=None, busy="Exporting %s" % item["name"])
    preview.redraw()
    key = "fx_" + hashlib.md5(item["path"].encode()).hexdigest()[:16]

    def appended(error):
        STATE["busy"] = None
        if STATE.get("game") is not item:
            return      # another one was picked meanwhile
        if error:
            STATE["error"] = "%s: %s" % (item["name"], error)
        else:
            preview_scene.clear()
            objects = preview_library.append(key)
            root = next((o for o in objects if o.get("mp_effect") == "System"), None)
            if root is None:
                STATE["error"] = "%s: nothing replayable came in" % item["name"]
            else:
                show(root, game=item)
        preview.redraw()

    if preview_library.ready(key):
        appended(None)
        return

    def exported(result):
        if STATE.get("game") is not item:
            return
        if isinstance(result, Exception) or not result.get("Exports"):
            appended("export failed: %s" % (result if isinstance(result, Exception) else "nothing exported"))
            return
        STATE["busy"] = "Importing %s once (later previews start at once)" % item["name"]
        preview.redraw()
        preview_library.build_on(key, result, None, appended)
    client().request("browse-export", exported, timeout=600, type="Effect", path=item["path"])


def shown_root():
    """The effect the info column speaks of: the user's (In Scene) or the preview's own copy of a game effect."""
    if STATE.get("game") is not None:
        return copy_root()
    return bpy.data.objects.get(STATE["root"]) if STATE["root"] else None


# ------------------------------------------------------------------ emitters shown

def _emitter_of(particles):
    from ...material_porter import effects
    piece = bpy.data.objects.get(particles.name[:-len(" particles")]) if particles.name.endswith(" particles") else None
    node = piece.parent if piece is not None else None
    return node.get(effects.KEY_EMITTER) if node is not None else None


def apply_visibility():
    scene = preview_scene.get()
    if scene is None:
        return
    for ob in scene.collection.all_objects:
        if ob.get("mp_effect") != "Particles":
            continue
        name = _emitter_of(ob)
        hidden = name in STATE["muted"] or (STATE["solo"] is not None and name != STATE["solo"])
        ob.hide_viewport = ob.hide_render = hidden
    preview_scene.changed()


def toggle_mute(name):
    STATE["muted"] ^= {name}
    apply_visibility()


def toggle_solo(name):
    STATE["solo"] = None if STATE["solo"] == name else name
    apply_visibility()


# ------------------------------------------------------------------ move, rotate, scale

def tool(context, dx, dy, cam, height, fresh):
    """A drag in the preview with a tool on. Paused: places the effect (its recorded motion dropped); playing: keys
    the carrier at the playhead. Its drawn particles follow at once; the replay after the drag makes them trail."""
    hold = carrier()
    if hold is None or TOOL["id"] == "view":
        return
    playing = preview.PLAY["on"]
    if fresh:
        if not playing and hold.animation_data is not None:
            hold.animation_data_clear()
        STATE["grab"] = hold.matrix_basis.copy()
        # world-space particles (an effect that moves leaves them unparented): moved by hand until the replay
        STATE["loose"] = {o.name: o.matrix_world.copy() for o in preview_scene.content()
                          if o.get("mp_effect") == "Particles" and o.parent is None}
    start = STATE["grab"]
    loc, rot, scale = start.decompose()
    if TOOL["id"] == "move":
        unit = cam.dist * 2 * math.tan(cam.fov / 2) / max(height, 1)
        loc = loc + cam.view_matrix().inverted().to_3x3() @ Vector((dx * unit, dy * unit, 0))
    elif TOOL["id"] == "rotate":
        rot = Matrix.Rotation(dx * 0.01, 4, 'Z').to_quaternion() @ rot
    elif TOOL["id"] == "scale":
        scale = scale * (1.01 ** dx)
    hold.matrix_basis = Matrix.LocRotScale(loc, rot, scale)
    delta = hold.matrix_basis @ start.inverted()
    for name, was in STATE["loose"].items():
        ob = bpy.data.objects.get(name)
        if ob is not None:
            ob.matrix_world = delta @ was
    if playing:
        f = preview.PLAY["frame"]
        for path in ("location", "rotation_euler", "scale"):
            hold.keyframe_insert(path, frame=f)
    STATE["moved"] = True       # replayed once the drag ends
    preview_scene.changed()


def reset_motion():
    hold = carrier()
    if hold is None:
        return
    hold.animation_data_clear()
    hold.matrix_basis = Matrix()
    bake()


def set_background(rgb):
    scene = preview_scene.get()
    if scene is None or scene.world is None:
        return
    world = scene.world
    world.color = rgb
    node = world.node_tree.nodes.get("Background") if world.node_tree else None
    if node is not None:
        node.inputs["Color"].default_value = (*rgb, 1.0)
    preview_scene.changed()


# ------------------------------------------------------------------ user parameters

def parameters(root):
    """The effect's user parameters by game name: [(name, value)] for plain numbers."""
    if root is None:
        return []
    return sorted((k, root[k]) for k in root.keys() if k.startswith(("User.", "NPC.")) and isinstance(root[k], (int, float)))


def instances(root):
    """The scene's effects made from the same game effect as `root` (its program's exports)."""
    base = root.name.split(".")[0]
    return [r for _owner, rs in roots() for r in rs if r.name.split(".")[0] == base]


def set_parameter(name, value, everywhere=False):
    """The value on the preview's copy; replayed (with `everywhere` on the scene's instances too) by flush()."""
    copy = copy_root()
    if copy is not None:
        copy[name] = value
    STATE.setdefault("pending", {})[name] = (value, everywhere)


def flush():
    """The edited parameters replayed: the preview's copy, and the instances an edit went to."""
    from ...material_porter import effect_replay
    pending = STATE.pop("pending", {})
    if not pending:
        return
    bake()
    root = bpy.data.objects.get(STATE["root"]) if STATE["root"] else None
    wide = {n: v for n, (v, everywhere) in pending.items() if everywhere}
    if wide and root is not None:
        for r in instances(root):
            for n, v in wide.items():
                r[n] = v
            for _line in effect_replay.play(r):
                pass
        bpy.ops.ed.undo_push(message="Set %s" % ", ".join(n.split(".", 1)[1] for n in wide))
