"""A road along a curve the way Fortnite builds its roads: a road segment mesh (exported with its exact material) repeated
along a spline and bent to it. Here an Array fitted to the curve and a Curve modifier, so the curve stays editable."""
import bpy
from mathutils import Matrix, Vector

from .meshes import white_colors

AXES = ("X", "Y", "Z")


def _forward(me):
    """The segment's length axis (its longest side; X, Unreal's spline mesh forward, on a tie) and where it starts."""
    if not me.vertices:
        return 0, 0.0
    lo = [min(v.co[i] for v in me.vertices) for i in range(3)]
    hi = [max(v.co[i] for v in me.vertices) for i in range(3)]
    size = [h - l for h, l in zip(hi, lo)]
    axis = max(range(3), key=lambda i: (round(size[i], 6), -i))
    return axis, lo[axis]


def road_from_curve(segment, curve):
    """A new object sharing the segment's mesh, repeated along the curve from its start and bent to it."""
    me = segment.data
    white_colors(me)        # road materials blend by vertex colour; none would read black
    axis, start = _forward(me)
    road = bpy.data.objects.new("%s Road" % curve.name, me)
    for coll in curve.users_collection or [bpy.context.scene.collection]:
        coll.objects.link(road)
    shift = Vector((0.0, 0.0, 0.0))
    shift[axis] = -start        # the segment's start on the curve's start
    road.parent = curve         # follows the curve; its space is the one the Curve modifier bends in
    road.matrix_basis = Matrix.Translation(shift)
    # the repeats stretched over the whole curve: whole segments end to end, as the game's spline meshes fill a spline
    curve.data.use_stretch = curve.data.use_deform_bounds = True
    array = road.modifiers.new("Road Array", 'ARRAY')
    array.fit_type, array.curve = 'FIT_CURVE', curve
    array.relative_offset_displace = [1.0 if i == axis else 0.0 for i in range(3)]
    array.use_merge_vertices, array.merge_threshold = True, 0.001
    bend = road.modifiers.new("Road Curve", 'CURVE')
    bend.object, bend.deform_axis = curve, 'POS_' + AXES[axis]
    return road
