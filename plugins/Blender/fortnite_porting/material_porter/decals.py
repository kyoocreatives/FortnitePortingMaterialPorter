"""A level's decals (Exports[0]["Decals"], the Material Porter fork's map reader): each a quad in the
decal's own Y-Z plane, projected along its X onto the meshes under it, with the decal material."""


def import_decals(context, decals):
    """Place `decals` (the payload's list, may be None) into the import's collection."""
    if not decals:
        return
