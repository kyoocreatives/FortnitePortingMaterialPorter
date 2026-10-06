"""A level's placed Niagara effects (Exports[0]["Effects"], the Material Porter fork's map reader): each
system's own effect export, fetched from the app and replayed where the level places it."""


def import_effects(context, effects):
    """Place `effects` (the payload's list, may be None) into the import's collection."""
    if not effects:
        return
