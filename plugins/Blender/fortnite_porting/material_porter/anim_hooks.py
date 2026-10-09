"""The fork's steps when an animation is imported onto an armature: rigs and face boards step aside so it plays."""


def begin(armature):
    from ..processing.context import metahuman_board, official_rig
    official_rig.on_animation_import(armature)
    metahuman_board.on_animation_import(armature)
