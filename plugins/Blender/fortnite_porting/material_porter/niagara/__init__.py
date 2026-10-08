"""A Niagara system replayed from its cooked asset.

This redoes what the engine does around the scripts. A system keeps one instance of a data set
that its two scripts (spawn once, update every tick) write the emitters' state into (age, loops,
execution state, particles to spawn). Each CPU emitter then runs its update script over its
particles and its spawn script over the new ones (niagara_vm runs the scripts). The scripts read
the engine's values (time step, owner transform, particle counts) from constant blocks laid out
as the engine's structs, their own parameters from a store the asset cooks with each script, and
their curves from data interfaces.

The asset comes as the app exports it: the package's exports, each {name, type, outer, props}, in
package order (a reference's ObjectPath ends in the export's index).

GPU emitters keep no script to run (only a compiled shader), so niagara_gpu stands in for them
from what the asset keeps (spawn counts, curves, renderers). Stateless emitters have only
settings, and niagara_stateless works their particles out."""
from .datasets import (  # noqa: F401
    ACTIVE,
    COMPLETE,
    DISABLED,
    EMITTER_SIZE,
    GLOBAL_SIZE,
    HALVES,
    IDENTITY,
    INACTIVE,
    INACTIVE_CLEAR,
    Layout,
    NO_ID,
    OWNER_SIZE,
    QUALITY,
    SYSTEM_SIZE,
    Store,
    TYPES,
    _rich,
    _specifiers,
    _zeros,
    components,
    type_name,
)
from .interfaces import (  # noqa: F401
    Array,
    Camera,
    Curve,
    Distribution,
    INTERFACES,
    LIBRARY,
    NoMesh,
    Nothing,
    ParticleRead,
    PlatformSet,
    RendererInfo,
    Skeleton,
    VectorField,
    _Reader,
    _kind,
    _matrix_to_quaternion,
    _multiply,
    _quaternion,
)
from .system import (  # noqa: F401
    Emitter,
    Handler,
    Ids,
    Script,
    System,
)
