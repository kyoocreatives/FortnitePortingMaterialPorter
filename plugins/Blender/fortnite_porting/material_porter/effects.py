"""Material Porter fork: a particle effect (a Niagara system) in Blender.

The app's export (ExportContext.Effects) is a tree: the system, an empty per emitter, and under
each what its renderers draw. A mesh renderer's meshes come as meshes; a sprite or ribbon
renderer has no mesh, only a material: a 1 m plane is made for it here (facing the scene camera
when the sprite does and there is one).

A CPU emitter's particles are then played (effect_replay: its scripts run over the scene's
frames, and each drawn piece instanced on the particles, frame by frame). A GPU emitter keeps no
script to run: its pieces stay as they are, laid out in a row to pick from.

Every drawn piece carries the values a particle system gives its particles, which the exact
materials read (env.particle_color...): mp_particle = 1 with mp_particle_color (RGBA), mp_dynamic
(four flags: which Dynamic Parameters are set) with mp_dynamic0..3, mp_subimage (a flipbook's
frame), mp_age. On a still piece they are the object's properties, to set by hand; on a played
one, each particle's own (its instance's attributes).
"""
import bpy
from mathutils import Matrix

KEY = "mp_effect"           # on an effect's objects: "System", "Emitter (GPU)", "Sprite", "Mesh"...
KEY_EMITTER = "mp_emitter"  # on an emitter's empty: the emitter's name
KEY_RENDERER = "mp_renderer"    # on a drawn piece: its renderer's name in the asset
KEY_ROLE = "mp_effect_role"     # on an item's own effect's empty: "trail", "swing", "idle" or "event"
KEY_SKIP = "mp_effect_skip"     # on a piece that isn't drawn: FP's importer hides its material (an anime outline's shell)
KEY_BONE = "mp_effect_bone"     # on an effect put on a bone: the bone
KEY_OFFSET = "mp_effect_offset"     # and its offset there in the game's frame (16 floats)


def particle_values(obj):
    """The per-particle values an effect's object carries for its materials."""
    obj["mp_particle"] = 1.0
    obj["mp_particle_color"] = [1.0, 1.0, 1.0, 1.0]
    ui = obj.id_properties_ui("mp_particle_color")
    ui.update(subtype='COLOR', min=0.0, soft_max=1.0, description="Particle Color: what the effect gives each particle")


def make(context, mesh, name):
    """The object for an empty node of an export: an effect's sprite or ribbon as a plane with
    its material, its system and emitters as tagged empties; else a plain empty."""
    fx = mesh.get("MPEffect") or {}
    kind = fx.get("Kind")
    if kind == "Decal":
        return _decal(context, fx, name)
    if kind == "Light":
        # a light renderer's piece: a point light (a played one's particles each get their own)
        light = bpy.data.lights.new(name, 'POINT')
        light.energy, light.shadow_soft_size = 5.0, 0.05
        obj = bpy.data.objects.new(name, light)
        obj[KEY] = "Light"
        obj[KEY_RENDERER] = fx.get("Renderer") or ""
        return obj
    if kind not in ("Sprite", "Ribbon"):
        obj = bpy.data.objects.new(name, None)
        if kind == "Emitter":
            obj[KEY] = "Emitter (%s)" % fx.get("Sim")
            obj[KEY_EMITTER] = name
            obj.empty_display_type = 'SPHERE'
            obj.empty_display_size = 0.1
        elif kind == "System":
            obj[KEY] = "System"
        return obj

    # a 1 m quad in the object's XY plane (a ribbon: 1 m wide, 4 m long), UVs over the whole texture
    # (a flipbook's sub-image is the material's to pick: mp_subimage)
    length = 4.0 if kind == "Ribbon" else 1.0
    data = bpy.data.meshes.new(name)
    data.from_pydata([(-0.5, -length / 2, 0.0), (0.5, -length / 2, 0.0), (0.5, length / 2, 0.0), (-0.5, length / 2, 0.0)], [], [(0, 1, 2, 3)])
    uv = data.uv_layers.new(name="UV0")
    for loop, (x, y) in zip(uv.data, ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))):
        loop.uv = (x, y)
    obj = bpy.data.objects.new(name, data)
    obj[KEY] = kind
    obj[KEY_RENDERER] = fx.get("Renderer") or ""
    data.materials.append(bpy.data.materials.new(name))
    context.import_material(obj.material_slots[0], fx["Material"], {})
    particle_values(obj)
    bake_tangents(data)
    sub = fx.get("SubImages") or [1.0, 1.0]
    if kind == "Sprite" and sub[0] * sub[1] > 1:
        obj["mp_subimage"] = 0.0
        obj.id_properties_ui("mp_subimage").update(min=0.0, max=sub[0] * sub[1] - 1, step=100, description="The flipbook's sub-image the sprite shows")
    obj.visible_shadow = False
    camera = bpy.context.scene.camera
    if kind == "Sprite" and "Camera" in str(fx.get("Facing")) and camera is not None:
        track = obj.constraints.new('TRACK_TO')
        track.target = camera
        track.track_axis = 'TRACK_Z'
        track.up_axis = 'UP_Y'
    return obj


def _decal(context, fx, name):
    """A decal renderer's piece: a 1 m quad across the decal's projection (its local YZ plane: a decal
    projects along its X), with the decal's material. The scene it projects onto isn't here: the
    quad stands for the patch it covers (flat ground under a ground decal)."""
    data = bpy.data.meshes.new(name)
    # facing back along the projection (a ground decal's quad faces up)
    data.from_pydata([(0.0, -0.5, -0.5), (0.0, -0.5, 0.5), (0.0, 0.5, 0.5), (0.0, 0.5, -0.5)], [], [(0, 1, 2, 3)])
    uv = data.uv_layers.new(name="UV0")
    for loop, (x, y) in zip(uv.data, ((0.0, 0.0), (0.0, 1.0), (1.0, 1.0), (1.0, 0.0))):
        loop.uv = (x, y)
    obj = bpy.data.objects.new(name, data)
    obj[KEY] = "Decal"
    obj[KEY_RENDERER] = fx.get("Renderer") or ""
    obj["mp_decal_fade"] = 1.0      # its material's Decal Lifetime Opacity (a played decal's: its particle's)
    data.materials.append(bpy.data.materials.new(name))
    context.import_material(obj.material_slots[0], fx["Material"], {})
    if obj.material_slots[0].material is not None:
        obj.material_slots[0].material.use_backface_culling = False     # a decal has no side: seen from anywhere
    particle_values(obj)
    bake_tangents(data)
    obj.visible_shadow = False
    return obj


def bake_tangents(mesh):
    """The mesh's UV tangents as its "mp_tangent" corner attribute: Cycles works out a displacement
    without tangents (strips thickened along theirs came out flat); the materials read this there."""
    if not mesh.uv_layers or "mp_tangent" in mesh.attributes:
        return
    try:
        mesh.calc_tangents(uvmap=mesh.uv_layers[0].name)
    except RuntimeError:        # (an n-gon: no tangents)
        return
    values = [0.0] * (len(mesh.loops) * 3)
    mesh.loops.foreach_get("tangent", values)
    mesh.attributes.new("mp_tangent", 'FLOAT_VECTOR', 'CORNER').data.foreach_set("vector", values)
    mesh.free_tangents()


def tag_mesh(obj, fx):
    """An effect's mesh (a mesh renderer's): its particle values."""
    obj[KEY] = "Mesh"
    obj[KEY_RENDERER] = fx.get("Renderer") or ""
    obj["mp_mesh_index"] = int(fx.get("Index") or 0)
    particle_values(obj)
    obj.visible_shadow = False
    if obj.type == 'MESH':
        bake_tangents(obj.data)


def ue_rest(bone):
    """A bone's rest frame as the game has it (armature space). FP's bone reorientation (always on with
    the Tasty rig) turns a bone's rest to point down its children, and the Tasty rig moves some heads,
    tails and rolls: both leave the original on the bone (orig_quat with post_quat; orig_head,
    orig_tail, orig_roll), from which the game's frame - the one its sockets and effects are placed
    in - is rebuilt."""
    from mathutils import Quaternion, Vector
    keys = bone.keys()
    if any(k in keys for k in ("orig_head", "orig_tail", "orig_roll")):
        head = Vector(bone["orig_head"]) if "orig_head" in keys else bone.head_local.copy()
        tail = Vector(bone["orig_tail"]) if "orig_tail" in keys else bone.tail_local.copy()
        roll = float(bone["orig_roll"]) if "orig_roll" in keys else bpy.types.Bone.AxisRollFromMatrix(bone.matrix_local.to_3x3())[1]
        frame = Matrix.Translation(head) @ bpy.types.Bone.MatrixFromAxisRoll((tail - head).normalized(), roll).to_4x4()
    else:
        frame = bone.matrix_local.copy()
    if "orig_quat" in keys and "post_quat" in keys:
        # (reoriented: rest = original @ post, with post = orig_quat @ post_quat; not: post is identity)
        post = Quaternion(bone["orig_quat"]) @ Quaternion(bone["post_quat"])
        frame = frame @ post.to_matrix().to_4x4().inverted()
    return frame


def ue_offset(bone):
    """From a bone's frame as it is in Blender to the game's (its local space): identity for a bone
    the import left as the game has it."""
    return bone.matrix_local.inverted() @ ue_rest(bone)


def on_bone(obj, bone, offset=None):
    """Put an object on a bone (a socket) of the armature it is under: at the bone, following it, with
    the offset the game gives it there (a Blender matrix). False where the armature has no such bone."""
    armature = obj.parent
    if armature is None or armature.type != 'ARMATURE':
        return False
    rest = next((b for b in armature.data.bones if b.name.lower() == bone.lower()), None)
    if rest is None:
        return False
    obj.parent_type = 'BONE'
    obj.parent_bone = rest.name
    obj.matrix_parent_inverse = Matrix.Identity(4)
    if offset is None:
        offset = Matrix.Identity(4)
    # (a bone's children hang from its tail; the offset is in the game's frame of the bone)
    obj.matrix_basis = Matrix.Translation((0.0, -rest.length, 0.0)) @ ue_offset(rest) @ offset
    # where it is put, for settle(): FP's later steps reshape the bones (a head shortened, Tasty's arms)
    obj[KEY_BONE] = rest.name
    obj[KEY_OFFSET] = [v for row in offset for v in row]
    return True


def socket_matrix(socket, scale):
    """Where a skeleton's socket sits on its bone, as a Blender matrix (socket: Location, Rotation, Scale as the app exports them)."""
    from ..processing.utils import make_euler, make_vector
    return Matrix.Translation(make_vector(socket.get("Location"), unreal_coords_correction=True) * scale) \
        @ make_euler(socket.get("Rotation")).to_matrix().to_4x4() @ Matrix.Diagonal((*make_vector(socket.get("Scale")), 1.0))


def _has(rig, name, sockets):
    """Whether an armature can place something on a bone or socket: its own bone, or a socket's bone."""
    bones = {b.name.lower() for b in rig.data.bones}
    socket = {k.lower(): v for k, v in (sockets or {}).items()}.get(name.lower())
    return name.lower() in bones or (socket is not None and str(socket.get("Bone")).lower() in bones)


def lacks_bones(entries, rig, sockets=None):
    """Whether any of an animation's effects sits on a bone or socket the armature can't place."""
    return any(entry.get("SocketName") and not _has(rig, entry["SocketName"], sockets) for entry in entries)


def from_animation(context, entries, rig, sockets=None, skeleton=None):
    """The effects an animation plays (its Niagara notifies): each on the animated armature, on its
    socket with its offsets, replayed from each of its notifies' frames (a timed one kept going
    until its end). skeleton: the game's whole skeleton under the armature, where there is one."""
    from ..processing.utils import time_to_frame
    if not hasattr(context, "collection"):      # (an animation import makes no collection of its own)
        context.collection = rig.users_collection[0] if rig.users_collection else bpy.context.scene.collection
    for entry in entries:
        # on the animated armature; on the game's whole skeleton (animated alike) where there is one: it has
        # every bone an effect may sit on or read
        context.mp_selected_armature = skeleton if skeleton is not None else rig
        mesh = entry.get("Effect") or {}
        fx = mesh.get("MPEffect")
        if fx is None:
            fx = mesh["MPEffect"] = {"Kind": "System"}
        fx["Attach"] = True
        fx["Bone"] = entry.get("SocketName")
        fx["Table"] = sockets or {}
        fx["Offset"] = socket_matrix({"Location": entry.get("LocationOffset"), "Rotation": entry.get("RotationOffset"), "Scale": entry.get("Scale")}, context.scale)
        # its notifies' frames (the animation's: 30 a second), earliest first
        plays = sorted(zip(entry.get("Times") or [0.0], entry.get("Durations") or [0.0]))
        fx["Start"] = time_to_frame(plays[0][0])
        fx["Repeats"] = [time_to_frame(t) - fx["Start"] for t, _ in plays]
        fx["Lengths"] = [time_to_frame(d) if d else 0 for _, d in plays]
        context.import_model(mesh)


PICKAXE_ROLES = ("trail", "swing", "idle", "impact")


def _hold(rig):
    """The pickaxe the armature is to swing, put in its hand (on weapon_r, else hand_r, as the game
    holds it) when the scene has one pickaxe not held yet: its topmost object. None if there is
    none, or several."""
    tops = set()
    for o in bpy.context.scene.objects:
        if o.get(KEY) == "System" and o.get(KEY_ROLE) in PICKAXE_ROLES:
            top = o
            while top.parent is not None:
                top = top.parent
            tops.add(top)
    loose = [t for t in tops if t is not rig]
    if len(loose) != 1:
        return None
    hand = next((b for b in rig.data.bones if b.name.lower() == "weapon_r"), None) or next((b for b in rig.data.bones if b.name.lower() == "hand_r"), None)
    if hand is None:
        return None
    axe = loose[0]
    axe.parent, axe.parent_type, axe.parent_bone = rig, 'BONE', hand.name
    axe.matrix_parent_inverse = Matrix.Identity(4)
    # (a bone child sits at the bone's tail: back to its head, where the hand grips, in the game's frame)
    axe.matrix_basis = Matrix.Translation((0.0, -hand.length, 0.0)) @ ue_offset(hand)
    return axe


def swing(windows, rig, hits=()):
    """A swing animation's trail windows ([on, off] times) and hits (times) given to the effects of
    the pickaxe the armature holds (put in its hand first when the scene has one that isn't held):
    its trail and swing effects play in each window, its hit effects at each hit; all replayed on
    it (its idle effect too: it moves now). Without a held pickaxe: every pickaxe's in the scene,
    where they are."""
    from . import effect_replay
    from .hook import _log
    from ..processing.utils import time_to_frame

    def under(roles):
        return [o for o in rig.children_recursive if o.get(KEY) == "System" and o.get(KEY_ROLE) in roles]
    if not under(PICKAXE_ROLES) and (axe := _hold(rig)) is not None:
        _log("%s put in %s's hand for the swing" % (axe.name, rig.name))
    held = bool(under(PICKAXE_ROLES))
    everywhere = [o for o in bpy.context.scene.objects if o.get(KEY) == "System"]

    def replay(root):
        try:
            for line in effect_replay.play(root):
                _log(line)
        except Exception as e:
            _log("%s: not replayed on the swing (%s: %s)" % (root.name, type(e).__name__, e))

    trails = under(("trail", "swing")) if held else [o for o in everywhere if o.get(KEY_ROLE) in ("trail", "swing")]
    if windows and trails:
        frames = sorted((time_to_frame(on), time_to_frame(off)) for on, off in windows)
        for root in trails:
            root[effect_replay.KEY_START] = frames[0][0]
            root[effect_replay.KEY_REPEATS] = [on - frames[0][0] for on, _ in frames]
            root[effect_replay.KEY_LENGTHS] = [max(off - on, 1) for on, off in frames]
            replay(root)
        _log("the swing's %d trail window(s) given to %s%s" % (len(frames), ", ".join(o.name for o in trails),
                                                            "" if held else " (no pickaxe held: every pickaxe trail in the scene)"))
    impacts = under(("impact",)) if held else [o for o in everywhere if o.get(KEY_ROLE) == "impact"]
    # one hit effect a pickaxe: the one for the Default surface (the others - a weak point's, water's -
    # wait for Replay Effect)
    plain = [o for o in impacts if "Default" in str(o.get("mp_effect_surfaces", "Default")).split(",")]
    impacts = plain or impacts[:1]
    if hits and impacts:
        frames = sorted({time_to_frame(t) for t in hits})
        for root in impacts:
            root[effect_replay.KEY_START] = frames[0]
            root[effect_replay.KEY_REPEATS] = [f - frames[0] for f in frames]
            root[effect_replay.KEY_LENGTHS] = []
            replay(root)
        _log("the swing's %d hit(s) given to %s" % (len(frames), ", ".join(o.name for o in impacts)))
    for root in under(("idle",)) if held else []:
        replay(root)


ANIMATION_ROLES = ("trail", "swing", "event", "impact")     # played by an animation: its own replay


def on_character(rig):
    """The effects on an armature that aren't an animation's (an outfit's idle ones, a held item's)."""
    return [o for o in rig.children_recursive if o.get(KEY) == "System" and o.get(KEY_ROLE) not in ANIMATION_ROLES]


def follow(rig, roots):
    """An animation put on the armature: its effects that were already there ('roots': an outfit's
    idle ones) replayed on the moving bones over the scene's frames - they were played on the pose the
    character had then and stayed there."""
    from . import effect_replay
    from .hook import _log
    done = []
    for root in roots:
        if root.name not in bpy.data.objects or root.get(effect_replay.KEY_PROGRAM) is None:
            continue
        try:
            for line in effect_replay.play(root):
                _log(line)
            done.append(root.name)
        except Exception as e:
            _log("%s: not replayed on the animation (%s: %s)" % (root.name, type(e).__name__, e))
    if done:
        _log("%s replayed on %s's animation" % (", ".join(done), rig.name))


def settle(context):
    """A character's effects, once FP is done with its skeleton (its parts merged, its bones
    reoriented and reshaped, Tasty's rig made): each put back on its bone - a child hangs from its
    bone's tail, and FP shortens some bones after the effects are placed (Exalted Ice King's eyes
    sank to the neck) - then played, reading the bones as they now stand (Tasty's lowered arms)."""
    from . import effect_replay
    from .hook import _log
    roots, context.mp_deferred_effects = getattr(context, "mp_deferred_effects", None) or [], None
    for root in roots:
        try:
            if root.name not in bpy.data.objects:
                continue
            if root.get(KEY_BONE) and root.parent is not None:
                values = list(root.get(KEY_OFFSET) or [])
                offset = Matrix([values[i:i + 4] for i in range(0, 16, 4)]) if len(values) == 16 else None
                on_bone(root, root[KEY_BONE], offset)
            for line in effect_replay.play(root):
                _log(line)
        except Exception as e:      # the pieces stay as imported
            _not_replayed(root, e)


def _not_replayed(root, e):
    import os
    import traceback
    from .hook import _log
    at = traceback.extract_tb(e.__traceback__)[-1]
    _log("%s: not replayed (%s: %s, at %s:%d)" % (root.name, type(e).__name__, e, os.path.basename(at.filename), at.lineno))


FX_CYCLES = "mp_fx_cycles"      # scene: 1 while it renders with Cycles (effect materials read it)
TRANSPARENT_BOUNCES = 64        # Cycles: see-through surfaces a ray passes before it stops (8 by default)
FX_CAM_POS, FX_CAM_FWD = "mp_fx_cam_pos", "mp_fx_cam_fwd"
_ENGINE_OWNER = object()


def sync_engine(*_):
    """Each scene with effects: ["mp_fx_cycles"] 1 when it renders with Cycles, else 0. Their soft
    particles (DepthFade) cast a ray behind the pixel: EEVEE's reads the screen's depth, Cycles' hits
    the other particles too and faded them all, so under Cycles they don't fade."""
    for sc in bpy.data.scenes:
        if FX_CYCLES in sc:
            want = int(sc.render.engine == 'CYCLES')
            changed = sc[FX_CYCLES] != want
            if changed:
                sc[FX_CYCLES] = want
            if want and _keep_camera(sc):
                changed = True
            if changed:
                sc.update_tag()         # (a Python write to a custom property tags nothing)


def _keep_camera(sc):
    """Under Cycles: the scene camera's position and direction (world) in ["mp_fx_cam_pos"] and
    ["mp_fx_cam_fwd"]. Cycles works out a displacement once per mesh, without a camera: effect
    materials whose vertices turn to the camera (strips thickened towards it) read these. True if
    they changed."""
    cam = sc.camera
    if cam is None:
        return False
    m = cam.matrix_world
    pos = tuple(round(v, 5) for v in m.translation)
    fwd = tuple(round(-v, 5) for v in m.col[2].xyz.normalized())
    changed = False
    for key, value in ((FX_CAM_POS, pos), (FX_CAM_FWD, fwd)):
        if tuple(sc.get(key, ())) != value:
            sc[key] = value
            changed = True
    return changed


def _watch_engine():
    bpy.msgbus.clear_by_owner(_ENGINE_OWNER)
    bpy.msgbus.subscribe_rna(key=(bpy.types.RenderSettings, "engine"), owner=_ENGINE_OWNER, args=(), notify=sync_engine)


@bpy.app.handlers.persistent
def _on_load(*_):
    _watch_engine()
    sync_engine()


@bpy.app.handlers.persistent
def _on_render(*_):
    sync_engine()


def register():
    bpy.app.handlers.load_post.append(_on_load)
    bpy.app.handlers.render_pre.append(_on_render)
    bpy.app.handlers.frame_change_post.append(_on_render)      # (an animated camera: each frame's)
    _watch_engine()


def unregister():
    for handlers, fn in ((bpy.app.handlers.load_post, _on_load), (bpy.app.handlers.render_pre, _on_render),
                         (bpy.app.handlers.frame_change_post, _on_render)):
        if fn in handlers:
            handlers.remove(fn)
    bpy.msgbus.clear_by_owner(_ENGINE_OWNER)


def finish(context, mesh, root):
    """Once a system's tree is imported: its CPU emitters replayed, their pieces played on the particles."""
    fx = mesh.get("MPEffect") or {}
    if fx.get("Kind") != "System":
        return
    from . import effect_replay
    from .hook import _log
    scene = bpy.context.scene
    if FX_CYCLES not in scene:
        scene[FX_CYCLES] = int(scene.render.engine == 'CYCLES')
    # Cycles stops a ray after 8 see-through surfaces by default (black where more particles overlap)
    cycles = getattr(scene, "cycles", None)
    if cycles is not None and cycles.transparent_max_bounces < TRANSPARENT_BOUNCES:
        cycles.transparent_max_bounces = TRANSPARENT_BOUNCES
        _log("Cycles' transparent bounces raised to %d for effects (overlapping particles)" % TRANSPARENT_BOUNCES)
    # what FP's importer hides on a character (an anime outline's shell: its material draws ink lines
    # from the scene's depth, which a Blender material can't read) isn't drawn as a white shell here
    hidden = list(getattr(context, "full_vertex_crunch_materials", None) or ())
    for node in root.children:
        for piece in node.children:
            if piece.type == 'MESH' and len(piece.material_slots) and all(any(s.material == m for m in hidden) for s in piece.material_slots):
                piece[KEY_SKIP] = True
                piece.hide_render = piece.hide_viewport = True
                _log("%s: %s not drawn (an outline shell: its lines come from the scene's depth)" % (root.name, piece.name))
    # a contrail, an animation's effect: on the armature selected when it was sent
    rig = getattr(context, "mp_selected_armature", None)
    if fx.get("Attach") and rig is not None:
        effect_replay.attach(root, rig)
    # a pickaxe's own effect, an animation's: on its socket
    bone, offset = mesh.get("MPParentBone") or fx.get("Bone"), fx.get("Offset")
    if offset is None and fx.get("Place"):      # where an item's own effect sits on its socket
        offset = socket_matrix(fx["Place"], context.scale)
    if not bone and offset is not None:
        root.matrix_basis = offset
    table = {k.lower(): v for k, v in (fx.get("Table") or {}).items()}
    if bone and not on_bone(root, bone, offset):
        # a socket the armature doesn't have: on the socket's bone, where the skeleton puts it
        socket = table.get(bone.lower())
        there = socket_matrix(socket, context.scale) @ (offset if offset is not None else Matrix.Identity(4)) if socket is not None else None
        placed = socket is not None and on_bone(root, socket["Bone"], there)
        if not placed and socket is not None and not socket.get("Bone") and root.parent is not None:
            root.matrix_basis = there       # a static mesh's socket: on the mesh itself
            placed = True
        if not placed:
            if offset is not None:
                root.matrix_basis = offset
            _log("%s: no bone or socket %s to put it on: at the armature's origin" % (root.name, bone))
    if role := fx.get("Role"):
        root[KEY_ROLE] = role
    if fx.get("Start") is not None:
        root[effect_replay.KEY_START] = int(fx["Start"])
    if fx.get("Repeats"):
        root[effect_replay.KEY_REPEATS] = [int(x) for x in fx["Repeats"]]
        root[effect_replay.KEY_LENGTHS] = [int(x) for x in fx.get("Lengths") or []]
    if not fx.get("Exports"):
        # nothing of it can be played (GPU emitters only): on something, its still pieces are put out of sight
        if root.parent is not None:
            for node in root.children:
                for piece in node.children:
                    piece.hide_render = piece.hide_viewport = True
            _log("%s: not played (GPU emitters only), its pieces hidden" % root.name)
        return
    try:
        effect_replay.store(root, fx["Exports"], fx.get("Fields"), context.scale, table)
        if fx.get("Sockets"):       # the two sockets a pickaxe's trail runs between
            root[effect_replay.KEY_SOCKETS] = ",".join(fx["Sockets"])
        for name, value in (fx.get("User") or {}).items():
            root[name] = value
        if fx.get("Surfaces"):     # a hit effect's surfaces (Default: what a swing hits here)
            root["mp_effect_surfaces"] = ",".join(fx["Surfaces"])
        if fx.get("Role") in ("event", "impact"):
            # one the game plays on an event (a weapon's reload, its level up) or where a swing hits: it
            # waits for a swing animation's hits or Replay Effect
            for node in root.children:
                for piece in node.children:
                    piece.hide_render = piece.hide_viewport = True
            _log("%s: %s: select it and press Replay Effect to play it (from its Start Frame)" % (
                root.name, "a hit's effect (%s): a swing animation on the character holding the pickaxe plays it at each hit" % ", ".join(fx.get("Surfaces") or ["Default"])
                if fx.get("Role") == "impact" else "the game plays it on an event"))
            return
        waiting = getattr(context, "mp_deferred_effects", None)
        if waiting is not None:
            # a character's: played once its skeleton is final (settle)
            waiting.append(root)
            return
        for line in effect_replay.play(root):
            _log(line)
    except Exception as e:      # the pieces stay as imported
        _not_replayed(root, e)
