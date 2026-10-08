"""Playing an effect in the scene: draw order, camera, user parameters."""

import re
import bpy
import numpy as np

from .. import effects, niagara

from .stand import (
    KEY_LENGTHS,
    KEY_LOOP,
    KEY_REPEATS,
    KEY_ROOT,
    KEY_SCALE,
    KEY_SOCKETS,
    KEY_START,
    Stand,
    TURN_CAMERA,
    TURN_CAMERA_VELOCITY,
    TURN_FACING,
    TURN_FACING_ALIGNED,
    TURN_MESH_CAMERA,
    TURN_MESH_VELOCITY,
    TURN_OWN,
    _moves,
    _plain_world,
    holder_of,
    program,
    replay,
)
from .points import Track, _bind, _lights, bindings, bound, points
from .nodes import group, ribbons


def _set(modifier, tree, name, value):
    identifier = next(i.identifier for i in tree.interface.items_tree if i.item_type == 'SOCKET' and i.in_out == 'INPUT' and i.name == name)
    getattr(modifier.properties.inputs, identifier).value = value


DRAW_STEP = 0.03        # m: under Cycles each renderer is drawn this much nearer than the one before


def bias_by_order(obj, modifier, tree, scene):
    """The player's Depth Bias: draw order x DRAW_STEP while the scene renders with Cycles (a driver on
    the scene's ["mp_fx_cycles"], which the plugin maintains), 0 under EEVEE."""
    identifier = next((i.identifier for i in tree.interface.items_tree if i.item_type == 'SOCKET' and i.in_out == 'INPUT'
                       and i.name == "Depth Bias"), None)
    order = int(obj.get("mp_draw_order", 0))
    if identifier is None or order == 0:
        return
    socket = getattr(modifier.properties.inputs, identifier)
    fc = socket.driver_add("value")
    fc.keyframe_points.clear()
    for m in list(fc.modifiers):
        fc.modifiers.remove(m)
    d = fc.driver
    d.type = 'SCRIPTED'
    var = d.variables[0] if len(d.variables) else d.variables.new()
    var.name = "cycles"
    var.type = 'SINGLE_PROP'
    var.targets[0].id_type = 'SCENE'
    var.targets[0].id = scene
    var.targets[0].data_path = '["%s"]' % effects.FX_CYCLES
    d.expression = "cycles * %r" % round(order * DRAW_STEP, 4)


def _camera(scene, root, scale, world):
    """The scene camera as a script sees it: position, forward, up and right in UE axes and units,
    in world space or the effect's own space. None without a camera."""
    if scene.camera is None:
        return None
    camera, there = _plain_world(scene.camera), _plain_world(root)
    if camera is None or there is None:
        bpy.context.view_layer.update()
        camera, there = scene.camera.matrix_world, root.matrix_world
    m = camera if world else there.inverted() @ camera
    flip = lambda v: (v.x, -v.y, v.z)
    position = tuple(c / scale for c in flip(m.translation))
    return (position, flip(-m.col[2].xyz.normalized()), flip(m.col[1].xyz.normalized()), flip(m.col[0].xyz.normalized()))


def clear(root):
    """Remove an effect's played particles and show its pieces again, as imported."""
    for obj in [o for o in bpy.data.objects if o.get(effects.KEY) == "Particles" and o.get(KEY_ROOT) == root]:
        data = obj.data
        actions = [a.action for a in (obj.animation_data, getattr(data, "animation_data", None)) if a is not None and a.action is not None]
        bpy.data.objects.remove(obj, do_unlink=True)
        if data is not None and not data.users:
            (bpy.data.lights if isinstance(data, bpy.types.Light) else bpy.data.meshes).remove(data)
        for action in actions:
            if not action.users:
                bpy.data.actions.remove(action)
    for node in root.children:
        for piece in node.children:
            if piece.get(effects.KEY) in ("Sprite", "Mesh", "Ribbon", "Decal", "Light"):
                piece.hide_render = piece.hide_viewport = bool(piece.get(effects.KEY_SKIP))


def _enabled(system, emitter, renderer):
    """Whether a renderer draws: its Renderer Enabled binding's value (a system, emitter or user bool)
    as the replay left it. A renderer bound to nothing draws."""
    binding = renderer.get("RendererEnabledBinding") or {}
    name = str(binding.get("DataSetName") or (binding.get("ParamMapVariable") or {}).get("Name") or "")
    if not name or name == "None":
        return True
    if name.startswith("Emitter."):
        name = emitter.name + name[len("Emitter"):]
    value = system.read(name)
    if value is None and name in system.user.offsets:
        value = np.frombuffer(system.user.raw(name)[:4], np.int32)
    if value is None or not len(value):
        return True
    return int(np.asarray(value).view(np.int32)[0]) != 0


# user parameters the game sets in its front end (lobby, locker); on for an import
FRONT_END = {"user.bisfrontend", "user.bisfrontendpreview"}


def _user(root, system):
    """Put the system's user parameters and the parameter collection values it reads (the time of day)
    on the effect's empty as properties (first made from the asset's own values), and tell the
    system about them."""
    for name, kind, value in system.users():
        if name not in root:
            # shown as in the lobby and locker (as in the shop and locker pictures); some effects
            # play only there (Eternal Wanderer's hair globs)
            if name.lower() in FRONT_END:
                value = True
            root[name] = value
    # UE's names are case-insensitive (a glider's bisFullyDeployed is the bIsFullyDeployed the game sets)
    spelled = {n.lower(): n for n in list(system.user.offsets) + list(system.shared.offsets)}
    for name in [k for k in root.keys() if k.startswith(("User.", "NPC."))]:
        own = spelled.get(name.lower())
        if own is None:     # set by the export but not taken by this system
            del root[name]
            continue
        if own != name:
            root[own] = root[name]
            del root[name]
            name = own
        value = root[name]
        system.set_user(name, list(value) if hasattr(value, "__len__") else value)


def _together(runs, names):
    """Several plays of an effect as one: each frame holds all plays' particles."""
    length = max([offset + len(track) for offset, tracks in runs for track in tracks.values()] or [0])
    merged = {}
    for name in names:
        frames = []
        blank = next((t[name][0] for _, t in runs if t.get(name)), None)
        for frame in range(length if blank is not None else 0):
            parts = [(i, tracks[name][frame - offset]) for i, (offset, tracks) in enumerate(runs)
                     if name in tracks and 0 <= frame - offset < len(tracks[name])]
            parts = [(i, p) for i, p in parts if p[0].shape[1]]
            if not parts:
                frames.append((blank[0][:, :0], blank[1][:, :0], np.zeros(0, np.int32)))
                continue
            frames.append((np.concatenate([p[0] for _, p in parts], axis=1), np.concatenate([p[1] for _, p in parts], axis=1),
                           np.concatenate([np.full(p[0].shape[1], i, np.int32) for i, p in parts])))
        merged[name] = frames
    return merged


def _title(root):
    """An effect as the log names it: a pickaxe's own, with what it is."""
    role = root.get(effects.KEY_ROLE)
    return "%s (%s)" % (root.name, role) if role else root.name


def play(root):
    """Replay the effect under the root (emitter empties and their pieces) over the scene's frame
    range and make its pieces play. An effect under an armature reads that character's bones and
    sockets; one that moves leaves its world-space particles where they were spawned. Yields
    messages for the user."""
    stored = program(root)
    if stored is None:
        yield "%s: nothing to replay it from" % root.name
        return
    exports, fields, table = stored
    scale = float(root.get(KEY_SCALE, 0.01))
    drawn_order = {}        # per piece (its layers: Fire, Fire001...): the players in creation order
    scene = bpy.context.scene
    clear(root)
    sockets = [s for s in str(root.get(KEY_SOCKETS) or "").split(",") if s]
    system = niagara.System(exports, fields=fields, sockets=sockets)
    _user(root, system)
    rig = holder_of(root)
    fps = scene.render.fps / scene.render.fps_base
    # scene frame the effect starts on: the range's first, unless its Start Frame property says otherwise
    if KEY_START not in root:
        root[KEY_START] = scene.frame_start
    start = int(root[KEY_START])
    frames = max(1, scene.frame_end - start + 1)
    # loops, except one timed by an animation or played on a swing or an event
    if KEY_LOOP not in root:
        root[KEY_LOOP] = not root.get(KEY_REPEATS) and root.get(effects.KEY_ROLE) not in ("trail", "swing", "event", "impact")
    loop = bool(root[KEY_LOOP])
    stand = Stand(scene, root, rig, system.reads, scale, table) if _moves(root) else None
    camera = _camera(scene, root, scale, stand is not None)
    if camera is not None:
        system.camera = camera
    now = scene.frame_current
    # each play (one per animation notify, else the single one): its own run of the system, from its frame
    repeats = [int(x) for x in root.get(KEY_REPEATS) or [0]]
    lengths = [int(x) for x in root.get(KEY_LENGTHS) or []]
    # variables the renderers' materials are bound to, kept over the (first) replay
    watch = set()
    for node in root.children:
        emitter = next((e for e in system.emitters if e.name == node.get(effects.KEY_EMITTER)), None)
        for piece in node.children if emitter is not None else ():
            renderer = next((e["props"] for e in exports if e["name"] == piece.get(effects.KEY_RENDERER) and e["outer"] == emitter.export["name"]), None)
            watch.update(name for _, name, _ in bindings(renderer or {}, emitter))
    runs = []
    try:
        for i, offset in enumerate(repeats):
            if offset >= frames:
                break
            one = system
            if i:
                one = niagara.System(exports, fields=fields, seed=1 + i, sockets=sockets)
                _user(root, one)
                one.camera = system.camera
            last = start + offset + lengths[i] if i < len(lengths) and lengths[i] > 0 else None
            runs.append((offset, replay(one, fps, frames - offset, stand, start + offset, last, watch if i == 0 else ())))
    finally:
        if stand is not None and stand.animated:
            scene.frame_set(now)
    tracks = runs[0][1] if len(runs) == 1 else _together(runs, [e.name for e in system.emitters])
    emitters = {e.name: e for e in system.emitters}
    played, most, length = [], 0, 0
    bound_params, clear_ones = set(), []
    for node in root.children:
        emitter = emitters.get(node.get(effects.KEY_EMITTER))
        if emitter is None or not tracks.get(emitter.name):
            continue
        track = Track(emitter, tracks[emitter.name])
        if not track.total:
            continue
        if track.has("Color") and float(track.get("Color", (1, 1, 1, 1))[:, 3].max()) <= 1e-4:
            clear_ones.append(emitter.name)     # drawn, but see-through all along (the game raises the alpha)
        drawn = 0
        for piece in list(node.children):
            kind = piece.get(effects.KEY)
            renderer = next((e["props"] for e in exports if e["name"] == piece.get(effects.KEY_RENDERER) and e["outer"] == emitter.export["name"]), None)
            if kind not in ("Sprite", "Mesh", "Ribbon", "Decal", "Light") or renderer is None or piece.type not in ('MESH', 'LIGHT') or piece.get(effects.KEY_SKIP):
                continue
            if not _enabled(system, emitter, renderer):
                # a renderer the system switches off (a variant's, e.g. System.IsGold): hide its piece
                piece.hide_render = piece.hide_viewport = True
                continue
            keep = None
            if kind == "Mesh" and track.has(bound(renderer, "MeshIndexBinding", "MeshIndex")):
                keep = track.get(bound(renderer, "MeshIndexBinding", "MeshIndex"), (0,))[:, 0] == int(piece.get("mp_mesh_index", 0))
            elif kind == "Mesh" and int(piece.get("mp_mesh_index", 0)) > 0:
                continue        # without a mesh index every particle draws the first mesh
            # an emitter with several renderers says which draws each particle: the particle's
            # visibility tag is the renderer's (without the attribute every renderer draws it)
            tag = bound(renderer, "RendererVisibilityTagBinding", "VisibilityTag")
            if track.has(tag):
                mine = track.get(tag, (0,))[:, 0].astype(np.int64) == int(renderer.get("RendererVisibility", 0))
                keep = mine if keep is None else keep & mine
            if keep is not None and not keep.any():
                piece.hide_render = piece.hide_viewport = True      # none of this replay's particles belong to it
                continue
            if kind == "Light":
                made = _lights(piece, track, renderer, keep, scale, start, loop, node if stand is None or emitter.local else None, root)
                piece.hide_render = piece.hide_viewport = True
                drawn += bool(made)
                continue
            tree = ribbons() if kind == "Ribbon" else group()
            data = points(piece.name + " particles", track, renderer, kind, scale, keep)
            obj = bpy.data.objects.new(piece.name + " particles", data)
            # a moving effect's world-space particles stay where spawned, not under the effect
            if stand is None or emitter.local:
                obj.parent = node
            obj[effects.KEY] = "Particles"
            obj[KEY_ROOT] = root
            obj.visible_shadow = False
            # layers of one piece (a mesh's shells, a sprite's copies) are drawn in renderer order
            layer = re.sub(r"[\d.]+", "", piece.name)
            obj["mp_draw_order"] = drawn_order.get(layer, 0)
            drawn_order[layer] = obj["mp_draw_order"] + 1
            for collection in piece.users_collection:
                collection.objects.link(obj)
            modifier = obj.modifiers.new("Particles", 'NODES')
            modifier.node_group = tree
            _set(modifier, tree, "Start Frame", start)
            _set(modifier, tree, "Frames", len(track.frames))
            _set(modifier, tree, "Loop", loop)
            bias_by_order(obj, modifier, tree, scene)
            tied = bindings(renderer, emitter)
            if tied:
                bound_params.update(_bind(piece, (piece, obj), tied, system.history, start, loop))
            # the drawn piece, when selected in the viewport, shows the piece's materials (the same ones:
            # an edit there changes what the particles draw; the points themselves draw nothing)
            for slot in piece.material_slots:
                data.materials.append(slot.material)
            if kind == "Ribbon":
                _set(modifier, tree, "Material", piece.material_slots[0].material if piece.material_slots else None)
                facing = str(renderer.get("FacingMode"))
                _set(modifier, tree, "Facing", 2 if "CustomSideVector" in facing else 1 if "Custom" in facing else 0)
                shape = str(renderer.get("Shape"))
                _set(modifier, tree, "Shape", 1 if "MultiPlane" in shape else 2 if "Tube" in shape else 0)
                _set(modifier, tree, "Sides", int(renderer.get("TubeSubdivisions", 3)) if "Tube" in shape else int(renderer.get("MultiPlaneCount", 2)))
                piece.hide_render = True
                piece.hide_viewport = True
                drawn += 1
                continue
            _set(modifier, tree, "Piece", piece)
            if kind == "Sprite":
                lengthwise = any(x in str(renderer.get("Alignment")) for x in ("VelocityAligned", "CustomAlignment"))
                turn = (TURN_FACING_ALIGNED if lengthwise else TURN_FACING) if "CustomFacing" in str(renderer.get("FacingMode")) else \
                    TURN_CAMERA_VELOCITY if lengthwise else TURN_CAMERA
                # the pivot (UV space: 0.5, 0.5 is the middle; V runs down) is at the particle
                pivot = renderer.get("PivotInUVSpace") or {}
                _set(modifier, tree, "Piece Offset", (0.5 - float(pivot.get("X", 0.5)), float(pivot.get("Y", 0.5)) - 0.5, 0.0))
                for constraint in list(piece.constraints):      # the still piece's turn to the camera is the modifier's now
                    piece.constraints.remove(constraint)
            else:
                facing = str(renderer.get("FacingMode"))
                turn = TURN_MESH_VELOCITY if "Velocity" in facing else TURN_MESH_CAMERA if "Camera" in facing else TURN_OWN
                _set(modifier, tree, "Piece Rotation", piece.rotation_euler)
                _set(modifier, tree, "Piece Scale", piece.scale)
                # the renderer's pivot offset for this mesh (UE units and axes, in mesh space)
                listed = renderer.get("Meshes") or []
                at = int(piece.get("mp_mesh_index", 0))
                offset = (listed[at].get("PivotOffset") if at < len(listed) else None) or {}
                _set(modifier, tree, "Piece Offset", (float(offset.get("X", 0.0)) * scale, -float(offset.get("Y", 0.0)) * scale, float(offset.get("Z", 0.0)) * scale))
            _set(modifier, tree, "Turn", turn)
            # the piece itself, which the particles draw, is hidden
            piece.hide_render = True
            piece.hide_viewport = True
            drawn += 1
        if drawn:
            node.location = (0.0, 0.0, 0.0)     # played where the effect is, not in the row of pieces
            played.append(emitter.name)
            most = max(most, int(track.counts.max()))
            length = max(length, len(track.frames))
    # an effect on something (a character's contrail, a pickaxe's own): hide what isn't played
    worn = rig is not None or bool(root.get(effects.KEY_ROLE))
    if played or worn:
        # emitters left as pieces: in a row beside the effect, 2 m apart
        left = [node for node in root.children if node.get(effects.KEY_EMITTER) and node.get(effects.KEY_EMITTER) not in played]
        for at, node in enumerate(left):
            node.location = (0.0, 0.0, 0.0) if worn else (0.0, -200.0 * (at + 1) * scale, 0.0)
            for piece in node.children if worn else ():
                piece.hide_render = piece.hide_viewport = True
    if played:
        yield "%s: %s played %sover %d frames from frame %d (%d particles at most)%s" % (
            _title(root), ", ".join(played), "%d times " % len(runs) if len(runs) > 1 else "", length, start, most,
            ", on %s" % rig.name if rig is not None else "")
        if root.get(effects.KEY_ROLE) in ("trail", "swing") and stand is not None and stand.still:
            yield "%s: a %s shows on what moves: animate it, set the effect's Start Frame where it starts, then Replay Effect" % (
                _title(root), root[effects.KEY_ROLE])
    if bound_params:
        yield "%s: material parameters from the effect's values: %s" % (root.name, ", ".join(sorted(bound_params)[:8]))
    if clear_ones:
        # their colour alpha stays 0 (Salvador's flames wait on User.Dissolve Progress), so user
        # parameters at 0 are the likely switch
        zero = [name for name, kind, value in system.users() if name.startswith("User.") and kind in ("NiagaraFloat", "NiagaraBool", "NiagaraInt32")
                and not (value if not hasattr(value, "__len__") else any(value))]
        yield "%s: %s drawn see-through all along (their colour's alpha 0: waiting on something the game sets%s)" % (
            root.name, ", ".join(clear_ones[:6]), ": its %s at 0 - set on the effect's empty, then Replay Effect" % ", ".join(zero[:5]) if zero else "")
    approximate = [name for name in played if name in system.approximate and name not in system.gpu]
    if approximate:
        yield "%s: %s: stateless emitters, played from their settings (the engine's random draws apart)" % (root.name, ", ".join(approximate))
    gpu = [name for name in played if name in system.gpu]
    if gpu:
        guessed = [e.name for e in system.emitters if e.name in gpu and getattr(e, "guessed", False)]
        yield "%s: %s: GPU emitters, approximated (their counts, curves and materials the asset's; their particles held still around them, as where they go isn't kept%s)" % (
            root.name, ", ".join(gpu), "; %s spawned at a stand-in rate: what makes the game spawn it isn't in the replay" % ", ".join(guessed) if guessed else "")
    idle = [e.name for e in system.emitters if e.name not in played and not sum(f[0].shape[1] for f in tracks.get(e.name) or [])]
    if idle:
        # user parameters the game sets that are still off here (a burst count, a switch) are the likely wait
        off = [name for name, kind, value in system.users() if name.startswith("User.") and not (value if not hasattr(value, "__len__") else any(value))]
        yield "%s: %s spawned nothing in the replay (waiting on something the game sets%s, or on an emitter left out): %s" % (
            root.name, ", ".join(idle), ": its %s at 0 - set on the effect's empty, then Replay Effect" % ", ".join(off[:4]) if off else "",
            "hidden" if worn else "left as pieces")
    if system.reads and rig is None:
        yield "%s: reads a character's bones or sockets (%s): with none, each sits at the effect's origin. Select the effect and an armature, then Replay Effect" % (
            root.name, ", ".join(sorted(system.reads)[:6]))
    elif rig is not None and system.unresolved():
        yield "%s: %s has no bone or socket named %s: each sits at its origin" % (root.name, rig.name, ", ".join(system.unresolved()[:8]))
    if system.meshless:
        yield "%s: samples a static mesh's surface or sockets (in the game, the mesh it is on): with none here, as the engine without one, from the effect's origin" % root.name
    left = ["%s (%s)" % s for s in system.skipped]
    if left:
        yield "%s: %s: %s" % (root.name, "not played, their pieces hidden" if worn else "left as pieces", ", ".join(left))
