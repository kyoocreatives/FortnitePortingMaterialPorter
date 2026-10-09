"""The fork's rig conventions, shared by its rigs: side colours, wire widths, four bone collections, a keyable
settings bone. Each rig builds its own bones and constraints, then styles them with this."""
import bpy

from . import rig_shapes

COLLECTIONS = ("Controls", "Secondary", "Mechanics", "Game Bones")
SHOWN = {"Controls": True, "Secondary": True, "Mechanics": False, "Game Bones": False}
COLORS = {"L": (0.15, 0.45, 1.0), "R": (1.0, 0.2, 0.15), "C": (1.0, 0.82, 0.1),
          "main": (1.0, 0.5, 0.05), "settings": (0.7, 0.3, 1.0)}
WIDTHS = {"main": 3.5, "primary": 2.5, "secondary": 1.5}
SETTINGS = "CR_Settings"
# collections of rigs not on the kit yet (creature, LEGO, vehicles built before it): their controls, then the rest
LEGACY_CONTROLS = ("Vehicle Controls", "Vehicle Wheel Controls", "Vehicle Parts", "Creature Controls", "Creature Face",
                   "Creature Limb FK", "LEGO Controls")
LEGACY = LEGACY_CONTROLS + ("Vehicle Wheels", "Vehicle Other", "Creature Other", "LEGO Other")


def collections(armature):
    """The four collections, in order, shown or hidden as the convention says; the game's own are hidden."""
    for collection in armature.collections:
        if collection.name not in COLLECTIONS:
            collection.is_visible = False
    found = {}
    for index, name in enumerate(COLLECTIONS):
        collection = armature.collections.get(name) or armature.collections.new(name)
        collection.is_visible = SHOWN[name]
        armature.collections.move(armature.collections.find(name), index)
        found[name] = collection
    return found


def assign(armature, bone_name, group):
    """Put a bone in exactly one of the kit's collections."""
    bone = armature.bones[bone_name]
    for name in COLLECTIONS:
        if name != group and name in armature.collections:
            armature.collections[name].unassign(bone)
    armature.collections[group].assign(bone)


def side_of(point, centre, left, width):
    """"L", "R" or "C" from where a point sits across the rig (names vary too much between skeletons)."""
    offset = (point - centre).dot(left)
    if abs(offset) < width * 0.1:
        return "C"
    return "L" if offset > 0.0 else "R"


def style(pose_bone, role, secondary=False, width=None):
    """Colour and wire width of a control; role "L", "R", "C", "main" or "settings"."""
    rgb = COLORS[role]
    if secondary:
        rgb = tuple(c + (1.0 - c) * 0.5 for c in rgb)
    rig_shapes.color(pose_bone, rgb)
    pose_bone.custom_shape_wire_width = width or WIDTHS["main" if role == "main" else "secondary" if secondary else "primary"]


def setting_path(name):
    return 'pose.bones["%s"]["%s"]' % (SETTINGS, name)


def add_settings(obj, settings):
    """The rig's settings as 0-1 properties of its settings bone: keyable with the pose."""
    bone = obj.pose.bones[SETTINGS]
    for name, default, description in settings:
        bone[name] = default
        bone.id_properties_ui(name).update(min=0.0, max=1.0, default=default, description=description)


def settings_owner(obj):
    """Where a rig keeps its settings: the settings bone, or the object for rigs built before it."""
    bone = obj.pose.bones.get(SETTINGS) if obj.pose else None
    return (bone, setting_path("%s")) if bone is not None else (obj, '["%s"]')


def driven(obj, constraint, name):
    """A constraint's influence follows a setting."""
    driver = constraint.driver_add("influence").driver
    driver.type = 'SCRIPTED'
    var = driver.variables.new()
    var.name, var.type = "on", 'SINGLE_PROP'
    var.targets[0].id = obj
    var.targets[0].data_path = setting_path(name)
    driver.expression = "on"


def controls(obj):
    """The pose bones an animator poses: in Controls or Secondary (or an older rig's control groups)."""
    shown = [obj.data.collections[n] for n in COLLECTIONS[:2] + LEGACY_CONTROLS if n in obj.data.collections]
    return [pb for pb in obj.pose.bones if any(pb.name in c.bones for c in shown)]
