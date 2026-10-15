"""Held prop for the official rig's extra hand controls: fingers stop on an object's surface instead of going through it.

Each finger segment's game bone follows a grip bone under Epic's control. A target at the segment's next joint starts
where the control puts it, keeps the segment's length and is pushed out of the prop by Shrinkwrap constraints (twice,
so the rigid joint really ends up outside); an optional wrap pulls it onto the surface first. The grip bone turns to
the target: about the curl axis only (a hinge), except the thumb's base, free to swing so it opposes over a handle.
The Shrinkwraps have no target and stay muted until a prop is picked on the side's Fingers control."""
import bpy

from . import rig_style
from .official_rig import MCH, PREFIX

GRIP, POSE, TARGET = MCH + "grip_", MCH + "grip_pose_", MCH + "grip_tgt_"
THICKNESS = 0.3         # a finger's half-thickness, of its segment's length
AMOUNTS = (("grip", 1.0, "Keep the fingers out of the held prop (0: off)"),
           ("thumb_wrap", 1.0, "Close the thumb onto the held prop"),
           ("finger_wrap", 0.0, "Close the fingers onto the held prop"))


def edit(edit_bones, chain, pivots):
    """Edit mode, one finger: grip, pose and target bones per segment; the next segment's pivot rides the grip."""
    from .official_hands import _bone
    for i, seg in enumerate(chain["segs"]):
        head, tail = chain["pts"][i], chain["pts"][i + 1]
        control = edit_bones[seg + "_ctrl"]
        grip = _bone(edit_bones, GRIP + seg, head, tail, chain["axis"], control)
        game = edit_bones[seg]
        pose = edit_bones.new(POSE + seg)
        pose.head, pose.tail, pose.roll = game.head.copy(), game.tail.copy(), game.roll
        pose.parent, pose.use_deform = grip, False
        _bone(edit_bones, TARGET + seg, tail, tail + chain["axis"] * (tail - head).length * 0.3, chain["axis"], control)
        if i + 1 < len(pivots):
            edit_bones[pivots[i + 1]].parent = grip


def _driver(obj, owner, path, holder, name):
    driver = owner.driver_add(path).driver
    driver.type = 'AVERAGE'
    var = driver.variables.new()
    var.name, var.type = "v", 'SINGLE_PROP'
    var.targets[0].id, var.targets[0].data_path = obj, 'pose.bones["%s"]["%s"]' % (holder, name)


def pose(obj, side, chains):
    """Pose: amounts on the Fingers control, the constraints, and the game bones following their grip pose bones."""
    pb = obj.pose.bones
    holder = "fingers_%s_ctrl" % side
    for name, value, desc in AMOUNTS:
        pb[holder][name] = value
        pb[holder].id_properties_ui(name).update(min=0.0, max=1.0, default=value, description=desc)
    for f, chain in chains.items():
        for i, seg in enumerate(chain["segs"]):
            target, control, grip = pb[TARGET + seg], seg + "_ctrl", pb[GRIP + seg]
            length = (chain["pts"][i + 1] - chain["pts"][i]).length

            def outside():
                c = target.constraints.new('LIMIT_DISTANCE')
                c.name, c.target, c.subtarget = PREFIX + "grip length", obj, control
                c.distance, c.limit_mode = length, 'LIMITDIST_ONSURFACE'
                c = target.constraints.new('SHRINKWRAP')
                c.name, c.shrinkwrap_type, c.wrap_mode = PREFIX + "grip out", 'NEAREST_SURFACE', 'OUTSIDE'
                c.distance, c.mute = length * THICKNESS, True

            for _ in range(2):
                outside()
            wrap = target.constraints.new('SHRINKWRAP')
            wrap.name, wrap.shrinkwrap_type, wrap.wrap_mode = PREFIX + "grip wrap", 'NEAREST_SURFACE', 'ON_SURFACE'
            wrap.distance, wrap.mute = length * THICKNESS, True
            _driver(obj, wrap, "influence", holder, "thumb_wrap" if f == "thumb" else "finger_wrap")
            for _ in range(2):
                outside()
            if f == "thumb" and i < 2:
                track = grip.constraints.new('DAMPED_TRACK')       # the thumb's base: a saddle joint
            else:
                track = grip.constraints.new('LOCKED_TRACK')       # a hinge: turns about the curl axis only
                track.track_axis, track.lock_axis = 'TRACK_Y', 'LOCK_X'
            track.name, track.target, track.subtarget = PREFIX + "grip", obj, target.name
            _driver(obj, track, "influence", holder, "grip")
            copy = next((c for c in pb[seg].constraints if c.name == PREFIX + "copy"), None)
            if copy is not None:
                copy.subtarget = POSE + seg
    for prefix in (GRIP, POSE, TARGET):
        for chain in chains.values():
            for seg in chain["segs"]:
                rig_style.assign(obj.data, prefix + seg, "Mechanics")


def set_prop(pose_bone, context=None):
    """The held prop picked (or cleared) on a Fingers control: its side's Shrinkwraps aim at it, or mute."""
    obj = pose_bone.id_data
    prop = pose_bone.fpmp_held_prop
    side = pose_bone.name.split("_")[1]
    scale = sum(obj.matrix_world.to_scale()) / 3.0          # Limit Distance works in world units
    # a Shrinkwrap's offset is in the prop's space: a gun scaled 2.2x held the fingers 2.2x a finger's thickness off it
    held = sum(prop.matrix_world.to_scale()) / 3.0 if prop is not None else 1.0
    for bone in obj.pose.bones:
        if not (bone.name.startswith(TARGET) and bone.name.endswith("_" + side)):
            continue
        length = obj.data.bones[bone.name.replace(TARGET, GRIP)].length * scale
        for c in bone.constraints:
            if c.type == 'SHRINKWRAP' and c.name.startswith(PREFIX + "grip"):
                c.target, c.mute, c.distance = prop, prop is None, length * THICKNESS / max(held, 1e-6)
            elif c.type == 'LIMIT_DISTANCE' and c.name.startswith(PREFIX + "grip length"):
                c.distance = length


def register():
    bpy.types.PoseBone.fpmp_held_prop = bpy.props.PointerProperty(
        type=bpy.types.Object, name="Held Prop", description="A mesh the hand holds: the fingers stop on its surface",
        poll=lambda _self, obj: obj.type == 'MESH', update=lambda self, context: set_prop(self, context))


def unregister():
    del bpy.types.PoseBone.fpmp_held_prop
