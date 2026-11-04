"""What the Animation tab plays: a body in the preview scene (a cached game body, or copies of the user's character
sharing its data) and an animation on it, imported from the engine's export the plugin's own way, so Apply can
import exactly that onto the character. Writes data: runs from timers and actions, never from a draw."""
import contextlib

import bpy

from . import preview, preview_library, preview_scene
from .state import client

BASE = "FortniteGame/Content/Characters/Player/%s/Medium/Base/%s.uasset"
BODIES = (("mannequin", "Mannequin", BASE % ("Male", "SK_M_MALE_Base"), "Male"),
          ("female", "Female Base", BASE % ("Female", "SK_M_Female_Base_Skeleton"), "Female"))
SELECTED = "selected"
# the preview's own export: no sounds (it plays no audio), sections looped, the timeline left alone
PREVIEW_SETTINGS = "ImportSounds:false;LoopAnimation:true;UpdateTimelineLength:false"
LIBRARY_SETTINGS = "RigType:Default"

STATE = {"body": "mannequin", "armature": None, "item": None, "payload": None, "busy": None, "error": None,
         "loaded_body": None, "made": 0}


def body_label(key):
    if key == SELECTED:
        target = selected_character()
        return target.name if target else "Selected Character"
    return next((label for k, label, _p, _g in BODIES if k == key), key)


def gender():
    if STATE["body"] == SELECTED:
        target = selected_character()
        name = target.name.lower() if target else ""
        return "Female" if "female" in name or name.startswith("f_") else "Male"
    return next((g for k, _l, _p, g in BODIES if k == STATE["body"]), "Male")


def selected_character():
    """The user's armature to apply to: the active object, or the armature of the active mesh."""
    from ...processing.utils import get_selected_armature
    ob = bpy.context.view_layer.objects.active
    if ob is None or ob.get(preview_scene.TAG):
        return None
    return get_selected_armature()


def armature():
    ob = bpy.data.objects.get(STATE["armature"]) if STATE["armature"] else None
    return ob if ob is not None and ob.get(preview_scene.TAG) == 2 else None


# ------------------------------------------------------------------ the body

def body_missing():
    return armature() is None or STATE["loaded_body"] != STATE["body"] or STATE["made"] != preview_scene.STATE["made"]


def ensure_body(done=None):
    """The chosen body in the preview scene; done() once it is there (it may build its library first)."""
    if not body_missing():
        if done:
            done()
        return
    key = STATE["body"]
    if key == SELECTED:
        preview_scene.clear()
        _place(_copy_selected())
        if done:
            done()
        return
    _label, game_file = next((l, p) for k, l, p, _g in BODIES if k == key)

    def built(error):
        if error:
            STATE.update(busy=None, error="%s: %s" % (body_label(key), error))
            preview.redraw()
            return
        if STATE["body"] == key:
            preview_scene.clear()
            _place(preview_library.append(key))
            STATE["busy"] = None
            if done:
                done()
        preview.redraw()
    if not preview_library.ready(key):
        STATE["busy"] = "Importing the %s once (later previews start at once)" % body_label(key)
        preview.redraw()
    preview_library.build(key, game_file, LIBRARY_SETTINGS, built)


def _copy_selected():
    """Copies of the user's character (armature and its meshes) sharing its data: posing a copy changes only the copy."""
    src = selected_character()
    if src is None:
        STATE["error"] = "Select a character (its armature or a mesh of it) to preview on it"
        return []
    arm = src.copy()
    arm.animation_data_clear()
    copies = [arm]
    for child in src.children_recursive:
        if child.type not in {'MESH', 'EMPTY'}:
            continue
        c = child.copy()
        if c.parent == src:
            c.parent = arm
        for mod in c.modifiers:
            if mod.type == 'ARMATURE' and mod.object == src:
                mod.object = arm
        copies.append(c)
    preview_scene.track(copies)
    preview_scene.add(copies)
    return copies


def _place(objects):
    arm = next((o for o in objects if o.type == 'ARMATURE'), None)
    STATE.update(armature=arm.name if arm else None, loaded_body=STATE["body"], made=preview_scene.STATE["made"])
    if arm is not None:
        arm.location = (0, 0, 0)
    for v in preview.VIEWS.values():
        v.framed = False


def set_body(key):
    STATE["body"] = key
    STATE["error"] = None
    ensure_body(lambda: replay())


# ------------------------------------------------------------------ the animation

def play(kind, item):
    """Export `item` (an Emote or Animation listing entry) and play it; kind None: an action already in the file."""
    STATE.update(item=(kind, item), error=None)
    if kind is None:
        ensure_body(lambda: _play_action(item["action"]))
        return
    STATE["busy"] = "Exporting %s" % item.get("name", "")
    preview.redraw()
    if kind == "Emote":     # its Animation Type style: the body's gender's montage when the emote has one
        client().request("browse-styles", lambda styles: _export(kind, item, _style_for(styles)), timeout=600,
                         type=kind, path=item["path"])
    else:
        _export(kind, item, None)


def _style_for(styles):
    if isinstance(styles, Exception):
        return None
    channel = next((c for c in styles if c["channel"] == "Animation Type"), None)
    if channel is None:
        return None
    names = [o["name"] for o in channel["options"]]
    return next((n for n in names if n.startswith(gender() + " ")), channel.get("selected"))


def _export(kind, item, style):
    if STATE["item"] != (kind, item):
        return
    query = {"type": kind, "path": item["path"], "settings": PREVIEW_SETTINGS}
    if style:
        query["styles"] = "Animation Type:%s" % style
    STATE["style"] = style
    client().request("browse-export", lambda result: _exported(kind, item, result), timeout=600, **query)


def _exported(kind, item, result):
    if STATE["item"] != (kind, item):
        return      # another one was picked meanwhile
    STATE["busy"] = None
    if isinstance(result, Exception) or not result.get("Exports"):
        STATE["error"] = "Export of %s failed: %s" % (item.get("name"), result if isinstance(result, Exception)
                                                        else "nothing exported")
        preview.redraw()
        return
    STATE["payload"] = result
    ensure_body(replay)


def replay():
    """The current payload (or action) on the current body again."""
    if STATE["item"] and STATE["item"][0] is None:
        _play_action(STATE["item"][1]["action"])
        return
    arm, payload = armature(), STATE["payload"]
    if arm is None or payload is None:
        return
    try:
        import_payload(payload, arm, preview_scene.ensure())
    except Exception as e:      # the plugin's import failed: shown in the tab
        STATE["error"] = "Import failed: %s" % e
    _range_from(arm)
    preview.redraw()


def _play_action(name):
    arm, action = armature(), bpy.data.actions.get(name)
    if arm is None or action is None:
        return
    ad = arm.animation_data or arm.animation_data_create()
    for track in list(ad.nla_tracks):
        ad.nla_tracks.remove(track)
    ad.action = None
    track = ad.nla_tracks.new()
    track.name = "Sections"
    strip = track.strips.new(action.name, int(action.frame_range[0]), action)
    strip.name = action.name
    _range_from(arm)


def _range_from(arm):
    ad = arm.animation_data
    strips = [s for t in ad.nla_tracks for s in t.strips] if ad else []
    if strips:
        # a looped strip repeats 999 times: one pass of each is the preview's range
        end = max(s.frame_start + (s.action_frame_end - s.action_frame_start) * s.scale for s in strips)
        preview.set_range(int(min(s.frame_start for s in strips)), int(round(end)))
    preview_scene.STATE["dirty"] = True


@contextlib.contextmanager
def _scene_sequencer(scene):
    """The plugin's anim import clears and adds sound strips in the workspace's sequencer scene (the user's):
    during a preview import it gets the preview scene's own."""
    from ...processing.context import anim_context

    def own(version_profile=None):
        if not scene.sequence_editor:
            scene.sequence_editor_create()
        return scene.sequence_editor
    saved = anim_context.get_sequence_editor
    anim_context.get_sequence_editor = own
    try:
        yield
    finally:
        anim_context.get_sequence_editor = saved


def import_payload(payload, arm, scene=None, options=None):
    """The payload's animation exports imported onto `arm` the plugin's way: into `scene` (the preview's, with its
    own context) or, with none, the user's (Apply). `options` overrides the export's import settings."""
    from ...processing.importer import ImportContext
    meta = dict(payload["MetaData"], Settings=dict(payload["MetaData"].get("Settings") or {}, **(options or {})))
    for export in payload["Exports"]:
        ctx = ImportContext(meta)
        ctx.name, ctx.type = export.get("Name"), export.get("Type")
        ctx.scale = 0.01 if ctx.options.get("ScaleDown") else 1
        ctx.meshes, ctx.mesh_indices, ctx.metadata_cache, ctx.imported_meshes = [], {}, {}, []
        ctx.override_materials, ctx.override_parameters, ctx.override_morph_targets = [], [], []
        if scene is None:
            ctx.import_anim_data(export, override_skeleton=arm)
            continue
        made = {k: set(getattr(bpy.data, k)) for k in preview_scene.KINDS}
        with bpy.context.temp_override(scene=scene, view_layer=scene.view_layers[0], active_object=arm, object=arm), \
                _scene_sequencer(scene):
            ctx.import_anim_data(export, override_skeleton=arm)
        preview_scene.track([idb for k in preview_scene.KINDS for idb in getattr(bpy.data, k) if idb not in made[k]])


def can_preview(context):
    """The preview imports in Object Mode only: the importer switches modes on the context's object."""
    return context.mode == 'OBJECT'


# ------------------------------------------------------------------ apply

def apply(context):
    """What plays, onto the user's character: the same export imported the plugin's way (body, face, props on their
    sockets, sound strips) as NLA strips, with the preview's loop and speed. Returns a message on failure."""
    target = selected_character()
    if target is None:
        return "Select a character to apply to"
    if context.mode != 'OBJECT':
        return "Apply works in Object Mode"
    kind_item = STATE["item"]
    if kind_item is None:
        return "Nothing plays"
    loop, speed = preview.PLAY["loop"], preview.PLAY["speed"]
    if kind_item[0] is None:
        action = bpy.data.actions.get(kind_item[1]["action"])
        if action is None:
            return "%s is gone" % kind_item[1]["action"]
        ad = target.animation_data or target.animation_data_create()
        ad.action = None
        track = ad.nla_tracks.new()
        track.name = "Sections"
        strip = track.strips.new(action.name, int(action.frame_range[0]), action)
        strip.repeat = 999.0 if loop else 1.0
    elif STATE["payload"] is None:
        return "Still exporting"
    else:
        import_payload(STATE["payload"], target, None,
                       {"ImportSounds": True, "LoopAnimation": loop, "UpdateTimelineLength": True})
    if abs(speed - 1.0) > 1e-3:
        _retime(target, speed)
    bpy.ops.ed.undo_push(message="Apply %s" % kind_item[1]["name"])
    return None


def _retime(target, speed):
    """The applied strips played `speed` times as fast (their props' and face's with them)."""
    owners = [target] + list(target.children_recursive)
    datas = [o for o in owners] + [o.data.shape_keys for o in owners if o.type == 'MESH' and o.data.shape_keys]
    for idb in datas:
        ad = idb.animation_data
        for track in (ad.nla_tracks if ad else ()):
            if track.name != "Sections":
                continue
            # strips can't overlap: slowing down moves the last first, speeding up the first first
            strips = sorted(track.strips, key=lambda s: s.frame_start, reverse=speed < 1)
            for strip in strips:
                start = strip.frame_start / speed
                if speed < 1:
                    strip.frame_start_ui = start
                    strip.scale = strip.scale / speed
                else:
                    strip.scale = strip.scale / speed
                    strip.frame_start_ui = start
