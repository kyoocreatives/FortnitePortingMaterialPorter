"""A particle effect's CPU emitters, played in Blender.

The emitters' scripts are run over the scene's frame range (niagara.py) and each frame's result is
kept: a mesh of points per drawn piece holding every frame's particles, each point with its frame
and its particle's values. A geometry nodes modifier keeps the current frame's points and puts the
piece (the sprite's plane, the mesh renderer's mesh) on each: turned to the camera or along its
velocity as the renderer says, sized, and carrying the particle's colour and material values as
instance attributes, which the exact materials read.

The replay runs at the engine's 60 ticks a second or so (a whole number of ticks per frame). It
stops where the system completes; an effect that never completes fills the scene's frame range.

What the replay takes is kept with the effect (a text in the file), so it can be replayed over
another frame range (from the empty's Start Frame), with other user parameters (its User.*
properties), or on a character. An effect under an armature reads its bones and sockets frame by
frame (a contrail's hands and feet, a pickaxe's trail sockets), and one that moves (its own
animation, or its parent's) leaves its world-space particles where they were spawned, as a trail."""
from .stand import (  # noqa: F401
    _animated,
    attach,
    _between,
    can_move,
    DECAL_DOWN,
    DEPTH_BIAS,
    FACINGS,
    GROUP,
    GROUP_VERSION,
    holder_of,
    KEY_LENGTHS,
    KEY_LOOP,
    KEY_PROGRAM,
    KEY_REPEATS,
    KEY_ROOT,
    KEY_SCALE,
    KEY_SOCKETS,
    KEY_START,
    LIGHTS_MAX,
    MOST_POINTS,
    plain_world,
    program,
    replay,
    RIBBONS,
    rig_of,
    roots,
    Stand,
    store,
    TURN_CAMERA,
    TURN_CAMERA_VELOCITY,
    TURN_FACING,
    TURN_FACING_ALIGNED,
    TURN_MESH_CAMERA,
    TURN_MESH_VELOCITY,
    TURN_OWN,
    TURNS,
    _ue,
    _variable,
)
from .points import (  # noqa: F401
    _attribute,
    bind_parameters,
    bindings,
    bound,
    BOUND_WIDTHS,
    _keys,
    particle_lights,
    points,
    _ribbon_runs,
    _ribbon_uv,
    Track,
)
from .nodes import (  # noqa: F401
    group,
    ribbons,
)
from .play import (  # noqa: F401
    bias_by_order,
    _camera,
    clear,
    DRAW_STEP,
    _enabled,
    FRONT_END,
    play,
    _set,
    _title,
    _together,
    _user,
)
