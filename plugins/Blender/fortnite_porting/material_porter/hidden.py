"""Hidden actors (the Map page's Hidden Actors option): what the game hides, gathered in a "Hidden" collection under the
import's, switched off so the level looks as in the game."""
import bpy


def gather(root):
    tagged = [o for o in root.all_objects if o.get("mp_hidden")]
    if not tagged:
        return None
    hidden = bpy.data.collections.new("Hidden")
    root.children.link(hidden)
    tree = {root, *root.children_recursive} - {hidden}
    moved = set()
    for obj in tagged:
        for o in (obj, *obj.children_recursive):
            if o.name in moved:
                continue
            moved.add(o.name)
            for c in list(o.users_collection):
                if c in tree:
                    c.objects.unlink(o)
            hidden.objects.link(o)
    hidden.hide_render = True
    if (layer := _layer(bpy.context.view_layer.layer_collection, hidden)) is not None:
        layer.hide_viewport = True
    return hidden


def _layer(layer, collection):
    if layer.collection == collection:
        return layer
    for child in layer.children:
        if (hit := _layer(child, collection)) is not None:
            return hit
    return None
