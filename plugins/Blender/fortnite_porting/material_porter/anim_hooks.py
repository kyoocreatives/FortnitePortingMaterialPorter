"""The fork's steps when an animation is imported onto an armature: rigs and face boards step aside so it plays, then
the dynamic bones follow it."""


def begin(armature):
    from ..processing.context import metahuman_board, official_rig
    official_rig.on_animation_import(armature)
    metahuman_board.on_animation_import(armature)


def end(armature):
    from ..logger import Log
    from ..processing.context import dynamics_bake
    if armature is None or not armature.data.get(dynamics_bake.KEY):
        return
    try:
        from ..processing.context.dynamics_runner import STATE
        armature.data[STATE] = "EmoteOrMelee"       # an emote plays: the game's emote state
        dynamics_bake.bake(armature)
    except Exception as e:
        Log.error("[Material Porter] dynamics bake (%s: %s)" % (type(e).__name__, e))
