"""The Rig panel's switches as Object properties: each reads and writes the rig's own stored state through the
functions its operator calls (the operators stay, for the pie, keymaps and scripts). Not animatable: the state they
stand for isn't a single value."""
import bpy

from ..processing.context import deform_rig, dynamics_bake, dynamics_live, official_rig, rig_kit


def _data(obj, key):
    return obj.type == 'ARMATURE' and bool(obj.data.get(key))


def body_switch(obj):
    """Epic's Show Body Controls setting, when the rig has it."""
    name = official_rig.load()["solve"]["visibility"]
    return name if _data(obj, official_rig.MARK) and name in obj.pose.bones else None


def _get_rig_on(self):
    if _data(self, official_rig.MARK):
        return bool(self.data.get(official_rig.ON, True))
    return rig_kit.is_kit(self) and bool(self.data.get(rig_kit.ON, True))


def _set_rig_on(self, value):
    if _data(self, official_rig.MARK):
        official_rig.set_on(self, value)
    elif rig_kit.is_kit(self):
        rig_kit.set_on(self, value)


def _get_body(self):
    name = body_switch(self)
    return name is not None and official_rig.setting(self, name) >= 0.5


def _set_body(self, value):
    if body_switch(self) is not None:
        official_rig.set_body_controls(self, value)


def _get_correctives(self):
    return _data(self, deform_rig.MARK) and bool(self.data.get(deform_rig.ON, True))


def _set_correctives(self, value):
    if _data(self, deform_rig.MARK):
        deform_rig.set_on(self, value)


def _get_dynamics(self):
    return _data(self, dynamics_bake.KEY) and dynamics_bake.is_on(self)


def _set_dynamics(self, value):
    if _data(self, dynamics_bake.KEY):
        dynamics_bake.set_on(self, value)


def _get_live(self):
    return _data(self, dynamics_bake.KEY) and dynamics_live.is_live(self)


def _set_live(self, value):
    if _data(self, dynamics_bake.KEY):
        dynamics_live.set_live(self, value)


def _state_items(self, context):
    return dynamics_bake.state_items(self if _data(self, dynamics_bake.KEY) else None)


def _get_state(self):
    if not _data(self, dynamics_bake.KEY):
        return 0
    current = dynamics_bake.state(self)
    return next((i for s, _, _, i in dynamics_bake.state_items(self) if s == current), 0)


def _set_state(self, value):
    if _data(self, dynamics_bake.KEY):
        dynamics_bake.set_state(self, dynamics_bake.state_items(self)[value][0])


PROPS = {
    "fpmp_rig_on": bpy.props.BoolProperty(
        name="Rig", get=_get_rig_on, set=_set_rig_on, options=set(),
        description="The rig drives the bones (off: the bones play their own animation)"),
    "fpmp_body_controls": bpy.props.BoolProperty(
        name="Body Controls", get=_get_body, set=_set_body, options=set(),
        description="Show the fingers, toes, root, body, hips and neck controls"),
    "fpmp_correctives": bpy.props.BoolProperty(
        name="Correctives", get=_get_correctives, set=_set_correctives, options=set(),
        description="Fortnite's deform correctives follow the pose (off: they stay at rest)"),
    "fpmp_dynamics": bpy.props.BoolProperty(
        name="Dynamics", get=_get_dynamics, set=_set_dynamics, options=set(),
        description="Play the simulated dynamic bones and cloth (off: the animation's own)"),
    "fpmp_dynamics_live": bpy.props.BoolProperty(
        name="Live", get=_get_live, set=_set_live, options=set(),
        description="Simulate the dynamic bones as the animation plays forward (a jump restarts them); "
                    "Simulate bakes the final result"),
    "fpmp_dynamics_state": bpy.props.EnumProperty(
        name="State", items=_state_items, get=_get_state, set=_set_state, options=set(),
        description="The character state whose dynamics settings apply (gravity, how much body motion reaches the "
                    "chains); Simulate again after changing it"),
}


def register():
    for name, prop in PROPS.items():
        setattr(bpy.types.Object, name, prop)


def unregister():
    for name in PROPS:
        delattr(bpy.types.Object, name)
