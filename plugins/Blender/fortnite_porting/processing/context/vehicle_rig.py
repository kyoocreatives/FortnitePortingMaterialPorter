"""Material Porter fork: a control rig for a vehicle's armature (a Valet car, a Rocket Racing car, a
dirt bike, a tank...), after the big car rigs (Rigacar, Car-Rig Pro): drive, steer, drift, body and
suspension. Fortnite's vehicles share a layout - root > frame > body, and each wheel under a
differential: axle_pivot > steering_knuckle > wheel_steering > wheel_disc > tire (a tank's
road_wheel > rot_road_wheel) - which the rig reads by name, with fallbacks on position:

- CR_Main (its footprint on the ground) places and turns the vehicle; CR_Drive (under it, an arrow off the nose) is moved forward
  along its own axis to drive: every wheel spins by the distance over its radius (a Transformation
  constraint: driving a metre turns a wheel 1/r radians);
- CR_Drift (an arc behind the vehicle) turns about the front axle: the vehicle follows it (the root's
  Child Of), its rear swinging out, the front wheels counter-steering to keep pointing where it
  drives;
- CR_Steer (an arc around the front axle) turns the front wheels, the cockpit's steering wheel three
  times as much;
- CR_Body (a slab over the roof) moves and tilts the body on its suspension, the wheels staying put;
- a ring on each wheel (CR_Wheel_<bone>) lifts the wheel (a bump) and turns it by hand (a
  wheelspin); with a Ground (the armature object's), each wheel follows it too. The vehicle rises,
  pitches and rolls with the wheels' plane, each wheel taking what's left (the body leans out of a
  turn, if asked);
- the parts that move something (a turret, doors, a tailgate, mirrors) get a box around what they
  move. The original bones keep their names, rest pose and hierarchy."""

import re
from math import pi

import bpy
from mathutils import Matrix, Vector

KEY = "is_vehicle_rig"
PREFIX = "CR_"
SPIN = re.compile(r"^(tire|wheel_disc|rot_road_wheel|rot_drive_sprocket|rot_.*wheel)", re.IGNORECASE)
WHEELISH = re.compile(r"wheel|tire|tyre", re.IGNORECASE)
NOT_A_WHEEL = re.compile(r"steering|tilted|well|socket|fx_|spreader|hand|brake", re.IGNORECASE)
STEER = re.compile(r"^wheel_steering", re.IGNORECASE)
COCKPIT = re.compile(r"^(steering_wheel|handlebars?)$", re.IGNORECASE)
FRONT = re.compile(r"(^|_)(fr|fl|front|f)(_|$)", re.IGNORECASE)
BACK = re.compile(r"(^|_)(bk|br|bl|back|rear|b)(_|$)", re.IGNORECASE)
PART = re.compile(r"door|hatch|turret|gun|cannon|trunk|tailgate|hood|bonnet|boot|antenna|ramp|lid|flap|spoiler|"
                  r"wing|mirror|canopy|bucket|crane|(^|_)arm", re.IGNORECASE)
HELPER = re.compile(r"fx_|socket|physlight|passenger|driver|exit|choice|pushforce|^center$|watertest|lookahead|"
                    r"muzzle|attach|_hand_|^frontwheels$|^rearwheels$|thrust|lensflare|taillight", re.IGNORECASE)
STEER_LIMIT = pi / 3        # the steer control's full turn: the front wheels' (60 degrees)
COCKPIT_RATIO = 3.0         # the steering wheel turns this much more than the wheels
LEAN = 0.15                 # the body's roll out of a turn, per radian of steer (at Lean in Turns 1)
PLANE_WHEELS = 10           # past this many wheels (a tank's), the body's plane is its corner wheels'
COLORS = {"main": (0.96, 0.79, 0.05), "drive": (0.18, 0.55, 1.0), "steer": (1.0, 0.23, 0.19),
          "drift": (0.88, 0.25, 0.98), "body": (0.24, 0.86, 0.52), "wheel": (1.0, 0.58, 0.0), "arch": (0.6, 1.0, 0.25),
          "part": (0.13, 0.83, 0.93)}


def _axis(matrix, direction):
    """The bone's local axis (X, Y or Z) closest to a direction, and its sign."""
    best = max(range(3), key=lambda i: abs(matrix.col[i].to_3d().normalized().dot(direction)))
    return "XYZ"[best], 1.0 if matrix.col[best].to_3d().dot(direction) >= 0 else -1.0


class Survey:
    """What a vehicle's skeleton is made of (edit bones, armature space)."""

    def __init__(self, edit_bones):
        self.bones = {b.name: b for b in edit_bones}
        tops = [b for b in edit_bones if b.parent is None]
        self.root = next((b.name for b in tops if b.name.lower() == "root"), tops[0].name if tops else None)
        self.ground = self.bones[self.root].head.z if self.root else 0.0
        named = [n for n in self.bones if SPIN.search(n)]
        if not named:
            named = [n for n in self.bones if WHEELISH.search(n) and not NOT_A_WHEEL.search(n)]
        # one spinning bone a wheel: the topmost (a disc and its tire turn as one)
        self.wheels = [n for n in named if not any(p.name in named for p in self.bones[n].parent_recursive)]
        front = [n for n in self.wheels if FRONT.search(n)]
        back = [n for n in self.wheels if BACK.search(n)]
        centre = lambda names: sum((self.bones[n].head for n in names), Vector()) / len(names)
        if front and back:
            forward = centre(front) - centre(back)
        else:
            forward = Vector((1.0, 0.0, 0.0))
        forward.z = 0.0
        self.forward = forward.normalized() if forward.length > 1e-6 else Vector((1.0, 0.0, 0.0))
        self.left = Vector((0.0, 0.0, 1.0)).cross(self.forward)
        heads = [b.head for b in edit_bones] + [b.tail for b in edit_bones] or [Vector()]
        self.fit(heads)         # (then the meshes', if any)
        middle = sum(h.dot(self.forward) for h in heads) / len(heads)
        steering = [n for n in self.bones if STEER.search(n)]
        self.steering = [n for n in steering if FRONT.search(n) or (not BACK.search(n) and self.bones[n].head.dot(self.forward) > middle)]
        self.cockpit = [n for n in self.bones if COCKPIT.search(n)]
        self.front_axle = centre(self.steering) if self.steering else (centre(front) if front else None)
        self.wheel_chains = set()
        for n in self.wheels + self.steering:
            at = self.bones[n]
            self.wheel_chains.add(n)
            self.wheel_chains.update(c.name for c in at.children_recursive)
            for p in at.parent_recursive:
                if "differential" in p.name.lower() or p.name in (self.root, "frame", "body"):
                    break
                self.wheel_chains.add(p.name)

    def fit(self, points):
        """The vehicle's extent from points about it (armature space): its length, width and height,
        the middle of its footprint (on the ground) and how far its nose is."""
        along = [p.dot(self.forward) for p in points]
        side = [p.dot(self.left) for p in points]
        self.length = max(max(along) - min(along), 0.5)
        self.width = max(max(side) - min(side), 0.3)
        self.height = max(max(p.z for p in points) - self.ground, 0.3)
        self.centre = (self.forward * (max(along) + min(along)) + self.left * (max(side) + min(side))) / 2.0
        self.centre.z = self.ground
        self.nose, self.tail_end = max(along), min(along)

    def top(self, name):
        """A wheel's topmost bone under its differential (or the frame): what lifts it."""
        at = self.bones[name]
        while at.parent is not None and at.parent.name in self.wheel_chains:
            at = at.parent
        return at.name

    def radius(self, name):
        height = self.bones[name].head.z - self.ground
        return height if height > 0.05 else 0.35


def _transform(owner, target, subtarget, source, to_axis, scale, map_from, name=None):
    """A Transformation constraint: the target's local `source` (location along Y, or rotation about
    it) turns the owner about its local `to_axis`, times `scale` (extrapolated past the range)."""
    con = owner.constraints.new('TRANSFORM')
    con.name = name or ("CR Drive" if map_from == 'LOCATION' else "CR Steer")
    con.target, con.subtarget = target, subtarget
    con.target_space = con.owner_space = 'LOCAL'
    con.map_from, con.map_to = map_from, 'ROTATION'
    con.mix_mode_rot = 'ADD'
    con.use_motion_extrapolate = True
    if map_from == 'LOCATION':
        setattr(con, "from_min_" + source.lower(), -1.0)
        setattr(con, "from_max_" + source.lower(), 1.0)
    else:
        setattr(con, "from_min_" + source.lower() + "_rot", -1.0)
        setattr(con, "from_max_" + source.lower() + "_rot", 1.0)
    for axis in "xyz":
        setattr(con, "map_to_%s_from" % axis, source if axis == to_axis.lower() else ("X" if source != "X" else "Z"))
        setattr(con, "to_min_%s_rot" % axis, -scale if axis == to_axis.lower() else 0.0)
        setattr(con, "to_max_%s_rot" % axis, scale if axis == to_axis.lower() else 0.0)
    return con


def _moved(owner, target, subtarget, source, to_axis, scale, name):
    """A Transformation constraint: the target's local location along `source` moves the owner along
    its local `to_axis`, times `scale`."""
    con = owner.constraints.new('TRANSFORM')
    con.name = name
    con.target, con.subtarget = target, subtarget
    con.target_space = con.owner_space = 'LOCAL'
    con.map_from, con.map_to = 'LOCATION', 'LOCATION'
    con.mix_mode = 'ADD'
    con.use_motion_extrapolate = True
    setattr(con, "from_min_" + source.lower(), -1.0)
    setattr(con, "from_max_" + source.lower(), 1.0)
    for axis in "xyz":
        setattr(con, "map_to_%s_from" % axis, source if axis == to_axis.lower() else ("X" if source != "X" else "Z"))
        setattr(con, "to_min_%s" % axis, -scale if axis == to_axis.lower() else 0.0)
        setattr(con, "to_max_%s" % axis, scale if axis == to_axis.lower() else 0.0)
    return con


def _property(obj, name, value, description):
    obj[name] = value
    obj.id_properties_ui(name).update(min=0.0, max=1.0, description=description)


def _drive_channel(obj, pose_bone, path, index, expression, variables):
    """A driver on a pose bone's channel: `variables` are (name, bone, transform type) - a bone's own
    channel - or (name, property) - one of the armature object's."""
    driver = pose_bone.driver_add(path, index).driver
    driver.type = 'SCRIPTED'
    for variable in variables:
        var = driver.variables.new()
        var.name = variable[0]
        if len(variable) == 3:
            var.type = 'TRANSFORMS'
            var.targets[0].id = obj
            var.targets[0].bone_target = variable[1]
            var.targets[0].transform_type = variable[2]
            var.targets[0].transform_space = 'LOCAL_SPACE'
        else:
            var.type = 'SINGLE_PROP'
            var.targets[0].id = obj
            var.targets[0].data_path = '["%s"]' % variable[1]
    driver.expression = expression


def _sum(coefficients):
    """A linear sum in the drivers' simple-expression form: '0.25*w0-0.25*w1'."""
    text = "".join("%+.5f*%s" % (c, name) for name, c in coefficients if abs(c) > 1e-6)
    return text.lstrip("+") or "0"


def _signed(way, expression):
    return ("-(%s)" if way < 0 else "%s") % expression


def _ground_changed(self, context):
    """The armature object's Ground: its wheels' sensors project onto it (none: they stay put)."""
    ground = self.fpmp_ground
    for bone in getattr(self.pose, "bones", ()):
        for con in bone.constraints:
            if con.name == "CR Ground":
                con.target = ground
                con.mute = ground is None


def register():
    bpy.types.Object.fpmp_ground = bpy.props.PointerProperty(
        type=bpy.types.Object, name="Ground", update=_ground_changed, poll=lambda self, o: o.type == 'MESH',
        description="The mesh a vehicle rig's wheels follow (its terrain, a road)")


def unregister():
    del bpy.types.Object.fpmp_ground


def _parts(obj, meshes, inverse):
    """The points (armature space) each part bone moves - its vertices weighted over half to it."""
    points = {}
    for mesh in meshes:
        if not any(m.type == 'ARMATURE' and m.object == obj for m in mesh.modifiers):
            continue
        groups = {g.index: g.name for g in mesh.vertex_groups
                  if PART.search(g.name) and not HELPER.search(g.name) and not COCKPIT.search(g.name)}
        if not groups:
            continue
        to_armature = inverse @ mesh.matrix_world
        for vertex in mesh.data.vertices:
            for weight in vertex.groups:
                name = groups.get(weight.group)
                if name is not None and weight.weight > 0.5:
                    points.setdefault(name, []).append(to_armature @ vertex.co)
    return {name: found for name, found in points.items() if len(found) >= 12}


def create(obj):
    """Rig the armature object. Returns what it found, in a line."""
    from ...utils import ensure_blend_data
    from . import rig_shapes
    from .creature_rig import _driven, align_shape, sized
    armature = obj.data
    if armature.get(KEY):
        return "%s: already has a vehicle rig" % obj.name
    ensure_blend_data()             # the control shapes (CTRL_Root, CTRL_Box...)
    # the vehicle's extent is its meshes' (its bones' tails reach past it: effect points, sockets)
    inverse = obj.matrix_world.inverted()
    carried = set(obj.children_recursive)
    meshes = [o for o in bpy.data.objects if o.type == 'MESH' and (o in carried or any(
        m.type == 'ARMATURE' and m.object == obj for m in o.modifiers))]
    extent = [inverse @ (o.matrix_world @ Vector(corner)) for o in meshes for corner in o.bound_box]
    parts = _parts(obj, meshes, inverse)
    view_layer = bpy.context.view_layer
    for o in view_layer.objects:
        o.select_set(False)
    view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    edit = armature.edit_bones
    survey = Survey(edit)
    if survey.root is None:
        bpy.ops.object.mode_set(mode='OBJECT')
        return "%s: no bones" % obj.name
    if extent:
        survey.fit(extent)
    up, forward, left = Vector((0.0, 0.0, 1.0)), survey.forward, survey.left
    length, width, height = survey.length, survey.width, survey.height
    radii = {n: survey.radius(n) for n in survey.wheels}
    hub = sum(radii.values()) / len(radii) if radii else height * 0.3

    def new(name, head, tail, parent):
        bone = edit.new(PREFIX + name)
        bone.head, bone.tail, bone.roll, bone.parent, bone.use_deform = head, tail, 0.0, parent, False
        return bone

    # the controls (Y forward, on the ground under the vehicle) and the mechanism under them
    base = survey.bones[survey.root].head.copy()
    main = new("Main", base, base + forward * length * 0.6, None)
    drive = new("Drive", base, base + forward * length * 0.4, main)
    front = survey.front_axle
    pivot = Vector((front.x, front.y, base.z)) if front is not None else survey.centre.copy()
    reach = max(pivot.dot(forward) - survey.tail_end, length * 0.3)
    drift = new("Drift", pivot, pivot - forward * reach, drive)          # Y backwards: it swings the rear
    steer = new("Steer", pivot, pivot + up * length * 0.15, drift) if survey.steering and front is not None else None
    where = {n: (survey.bones[n].head.dot(forward), survey.bones[n].head.dot(left)) for n in survey.wheels}
    # the wheels the vehicle's plane goes through (a tank's: its corners)
    plane = list(survey.wheels)
    if len(plane) > PLANE_WHEELS:
        corner = lambda k: max(plane, key=lambda n: k[0] * where[n][0] + k[1] * where[n][1])
        plane = list(dict.fromkeys(corner(k) for k in ((1, 1), (1, -1), (-1, 1), (-1, -1))))
    # the suspension (the vehicle's root follows it) turns about the plane wheels' middle, at their hubs
    if plane:
        mean_a = sum(where[w][0] for w in plane) / len(plane)
        mean_s = sum(where[w][1] for w in plane) / len(plane)
        middle = forward * mean_a + left * mean_s
        middle.z = base.z + hub
    else:
        middle = survey.centre + up * hub
    suspension = new("Suspension", middle, middle + forward * length * 0.3, drift)
    lean = new("Lean", middle, middle + forward * length * 0.3, suspension)
    body = next((b for b in edit if b.name.lower() == "body"), None)
    body_control = new("Body", middle, middle + forward * length * 0.3, lean) if body else None
    if body:
        follow = new("Body_Follow", body.head, body.tail, body_control)  # the body where it rests, under CR_Body
        follow.roll = body.roll
    controls = {}                   # wheel: its ring, ground sensor, lift, arch; which way it points (+1 left)
    for name in survey.wheels:
        at = survey.bones[name].head.copy()
        out = 1.0 if (at - survey.centre).dot(left) >= 0 else -1.0
        under = Vector((at.x, at.y, base.z))
        sensor = new("Ground_" + name, under, under + left * out * radii[name] * 0.6, drift)
        ring = new("Wheel_" + name, at, at + left * out * radii[name] * 0.6, sensor)
        lift = new("Lift_" + name, at, at + left * out * radii[name] * 0.3, drift)
        # its arch: over the wheel, on the vehicle's side (it rides with the body)
        arch = None
        if survey.top(name) != name:
            side = abs((at - survey.centre).dot(left))
            outside = max(width * 0.5 - side, 0.0) if side > width * 0.1 else 0.0
            over = at + up * radii[name] * 1.3 + left * out * outside
            arch = new("Arch_" + name, over, over + left * out * radii[name] * 0.4, lean).name
        controls[name] = (ring.name, sensor.name, lift.name, out, arch)
    # the local axes each needs: which is up, across, forward (letter and sign)
    axes = {b.name: {"up": _axis(b.matrix, up), "left": _axis(b.matrix, left), "forward": _axis(b.matrix, forward)}
            for b in edit if b.name.startswith(PREFIX)}
    tops = {n: survey.top(n) for n in survey.wheels}
    top_up = {t: _axis(edit[t].matrix, up) for t in set(tops.values())}
    spins = {n: _axis(edit[n].matrix, left) for n in survey.wheels}
    spin_up = {n: _axis(edit[n].matrix, up) for n in survey.wheels}
    turns = {n: _axis(edit[n].matrix, up) for n in survey.steering}
    cockpits = {n: _axis(edit[n].matrix, -forward) for n in survey.cockpit}
    names = {k: (b.name if b is not None else None) for k, b in (
        ("drive", drive), ("drift", drift), ("steer", steer), ("suspension", suspension), ("lean", lean),
        ("body", body_control))}
    body_name = body.name if body else None
    bpy.ops.object.mode_set(mode='POSE')
    pose = obj.pose.bones
    view_layer.update()

    # the vehicle follows CR_Suspension (its root, from where it is at rest): drifting, on its wheels
    follow = pose[survey.root].constraints.new('CHILD_OF')
    follow.name = "CR Follow"
    follow.target, follow.subtarget = obj, names["suspension"]
    follow.inverse_matrix = (obj.matrix_world @ pose[names["suspension"]].matrix).inverted()

    _property(obj, "auto_wheels", 1.0, "The wheels spin as CR_Drive moves forward")
    _property(obj, "auto_steer", 1.0, "The front wheels and the steering wheel turn with CR_Steer")
    _property(obj, "countersteer", 1.0, "The front wheels turn against CR_Drift, pointing where the vehicle drives")
    _property(obj, "suspension", 1.0, "The vehicle rises, pitches and rolls with its wheels (lifted, on the ground)")
    _property(obj, "lean", 0.0, "The body rolls out of a turn as CR_Steer turns")
    for name, (axis, sign) in spins.items():
        # driving forward a metre turns the wheel 1/r radians about its axle (forward roll: about +left)
        con = _transform(pose[name], obj, names["drive"], "Y", axis, sign / radii[name], 'LOCATION')
        _driven(obj, con, "auto_wheels")
        # its ring turns it too (about the ring's Y, outwards)
        ring, sensor, lift, out, arch = controls[name]
        _transform(pose[name], obj, ring, "Y", axis, sign * out, 'ROTATION', name="CR Spin")
        # and lifts it, with its chain from the differential down, off the vehicle's plane
        letter, way = axes[lift]["up"]
        top_letter, top_way = top_up[tops[name]]
        _moved(pose[tops[name]], obj, lift, letter, top_letter, way * top_way, "CR Lift")
        if arch:
            # its arch moves its zone - the chain's top and what hangs off it (an upright, a fender, a
            # shock, a caliper) - and not the wheel: the spinning bone (the wheel's object on it) moves back
            letter, way = axes[arch]["up"]
            _moved(pose[tops[name]], obj, arch, letter, top_letter, way * top_way, "CR Arch")
            s_letter, s_way = spin_up[name]
            _moved(pose[name], obj, arch, letter, s_letter, -way * s_way, "CR Arch Keep")
        # the ground under it, once there is one (the armature object's Ground)
        con = pose[sensor].constraints.new('SHRINKWRAP')
        con.name = "CR Ground"
        con.shrinkwrap_type = 'PROJECT'
        con.project_axis, con.project_axis_space = 'NEG_Z', 'WORLD'
        con.use_project_opposite = True             # (a hill above it too)
        con.project_limit = max(height * 2.0, 1.0)
        con.target = getattr(obj, "fpmp_ground", None)
        con.mute = con.target is None
    drift_letter, drift_way = axes[names["drift"]]["up"]
    for name, (axis, sign) in list(turns.items()) + [(n, (a, s * COCKPIT_RATIO)) for n, (a, s) in cockpits.items()]:
        if names["steer"]:
            con = _transform(pose[name], obj, names["steer"], "Y", axis, sign, 'ROTATION')
            _driven(obj, con, "auto_steer")
        # drifting turns the vehicle about its front axle; its front wheels turn back the other way
        con = _transform(pose[name], obj, names["drift"], drift_letter, axis, -sign * drift_way, 'ROTATION', name="CR Counter")
        _driven(obj, con, "countersteer")
    if names["steer"]:
        # the steer control turns within the wheels' lock
        limit = pose[names["steer"]].constraints.new('LIMIT_ROTATION')
        limit.name = "CR Steer Lock"
        limit.use_limit_y, limit.min_y, limit.max_y = True, -STEER_LIMIT, STEER_LIMIT
        limit.owner_space = 'LOCAL'
        pose[names["steer"]].rotation_mode = 'YXZ'
        pose[names["steer"]].lock_rotation = (True, False, True)
        pose[names["steer"]].lock_location = (True, True, True)
    pose[names["drive"]].lock_location = (True, False, True)      # it drives along its own axis
    pose[names["drift"]].lock_location = (True, True, True)       # it turns about the front axle
    pose[names["drift"]].lock_rotation = tuple(letter != drift_letter for letter in "XYZ")
    for name, (ring, sensor, lift, out, arch) in controls.items():
        letter = axes[ring]["up"][0]
        pose[ring].lock_location = tuple(a != letter for a in "XYZ")       # up and down
        pose[ring].lock_rotation = (True, False, True)                     # about its axle
        if arch:
            pose[arch].lock_location = tuple(a != axes[arch]["up"][0] for a in "XYZ")
            pose[arch].lock_rotation = pose[arch].lock_scale = (True, True, True)

    # the suspension: each wheel's height is its ring's lift and its ground's; the vehicle rises by their
    # mean, pitches and rolls with the plane through them (least squares: linear in the heights), and
    # each wheel takes what's left (on a flat or sloping ground, nothing)
    if plane:
        index = {n: i for i, n in enumerate(survey.wheels)}

        def height_of(name):
            i = index[name]
            ring, sensor = controls[name][0], controls[name][1]
            (r_letter, r_way), (s_letter, s_way) = axes[ring]["up"], axes[sensor]["up"]
            variables = [("w%d" % i, ring, "LOC_" + r_letter), ("g%d" % i, sensor, "LOC_" + s_letter)]
            return "(%sw%d%sg%d)" % ("-" if r_way < 0 else "", i, "-" if s_way < 0 else "+", i), variables

        n = len(plane)
        spread_a = sum((where[w][0] - mean_a) ** 2 for w in plane)
        spread_s = sum((where[w][1] - mean_s) ** 2 for w in plane)
        heights = {w: height_of(w) for w in survey.wheels}
        plane_variables = [v for w in plane for v in heights[w][1]] + [("f", "suspension")]

        def through(coefficient):
            return _sum([(heights[w][0], coefficient(w)) for w in plane])

        bone = pose[names["suspension"]]
        bone.rotation_mode = 'XYZ'
        letter, way = axes[names["suspension"]]["up"]
        _drive_channel(obj, bone, "location", "XYZ".index(letter), _signed(way, "(%s)*f" % through(lambda w: 1.0 / n)), plane_variables)
        clamp = "asin(min(1,max(-1,%s)))*f"   # (a slope's sine: a wheel a metre ahead rises by the slope)
        if spread_a > 1e-4:
            # nose up when the front is higher: about +left, that's negative
            letter, way = axes[names["suspension"]]["left"]
            _drive_channel(obj, bone, "rotation_euler", "XYZ".index(letter),
                           _signed(-way, clamp % through(lambda w: (where[w][0] - mean_a) / spread_a)), plane_variables)
        if spread_s > 1e-4:
            # its left up when the left is higher: about +forward, that's positive
            letter, way = axes[names["suspension"]]["forward"]
            _drive_channel(obj, bone, "rotation_euler", "XYZ".index(letter),
                           _signed(way, clamp % through(lambda w: (where[w][1] - mean_s) / spread_s)), plane_variables)
        for name in survey.wheels:
            # what the plane leaves the wheel: its height less the plane's there
            a, s = where[name]

            def at_wheel(w):
                value = 1.0 / n
                if spread_a > 1e-4:
                    value += (a - mean_a) * (where[w][0] - mean_a) / spread_a
                if spread_s > 1e-4:
                    value += (s - mean_s) * (where[w][1] - mean_s) / spread_s
                return value

            lift = controls[name][2]
            letter, way = axes[lift]["up"]
            own, variables = heights[name]
            _drive_channel(obj, pose[lift], "location", "XYZ".index(letter),
                           _signed(way, "%s-f*(%s)" % (own, through(at_wheel))),
                           list({v[0]: v for v in variables + plane_variables}.values()))
    if names["steer"]:
        # a left turn (+ about up) rolls the body right: its left up - about +forward, positive
        letter, way = axes[names["lean"]]["forward"]
        pose[names["lean"]].rotation_mode = 'XYZ'
        _drive_channel(obj, pose[names["lean"]], "rotation_euler", "XYZ".index(letter),
                       _signed(way, "%g*l*st" % LEAN), [("l", "lean"), ("st", names["steer"], "ROT_Y")])
    if body_name:
        # the body where CR_Body has it (the wheels, on the frame, stay)
        copy = pose[body_name].constraints.new('COPY_TRANSFORMS')
        copy.name = "CR Body"
        copy.target, copy.subtarget = obj, PREFIX + "Body_Follow"

    # what shows: the controls, the wheels' rings, the parts that move something; the wheels' bones,
    # the mechanism and the game's helpers (sockets, effect points, seats) don't
    ours = ("Vehicle Controls", "Vehicle Wheel Controls", "Vehicle Parts", "Vehicle Wheels", "Vehicle Other")
    for collection in armature.collections:
        if collection.name not in ours:
            collection.is_visible = False
    collections = {}
    for name, visible in zip(ours, (True, True, True, False, False)):
        collections[name] = armature.collections.get(name) or armature.collections.new(name)
        collections[name].is_visible = visible
    centre = survey.centre

    main = pose[PREFIX + "Main"]
    sized(main, "CTRL_Box", "THEME09", 0.1, wire=3.0)
    # the vehicle's footprint, a chevron at its front
    main.custom_shape = rig_shapes.footprint(PREFIX + "Footprint_" + obj.name, length * 1.03, width * 1.03)
    align_shape(obj, main, x=-left, y=forward, z=up)
    rig_shapes.place(obj, main, centre)
    rig_shapes.color(main, COLORS["main"])
    drive = pose[names["drive"]]
    sized(drive, "CTRL_Box", "THEME04", 0.1, wire=3.0)
    drive.custom_shape = rig_shapes.ensure("CR_Arrow")
    drive.custom_shape_scale_xyz = (width * 0.9, length * 0.3, 1.0)
    align_shape(obj, drive, x=-left, y=forward, z=up)               # an arrow on the ground off its nose
    rig_shapes.place(obj, drive, centre + forward * (survey.nose - centre.dot(forward) + length * 0.06))
    rig_shapes.color(drive, COLORS["drive"])
    drift = pose[names["drift"]]
    sized(drift, "CTRL_Box", "THEME06", 0.1, wire=3.0)
    drift.custom_shape = rig_shapes.ensure("CR_Swing")
    radius = reach + length * 0.08
    drift.custom_shape_scale_xyz = (radius, radius, radius)
    align_shape(obj, drift, x=left, y=-forward, z=up)               # an arc behind it, about the front axle
    rig_shapes.place(obj, drift, pivot + up * height * 0.2)
    rig_shapes.color(drift, COLORS["drift"])
    shown = [names["drive"], names["drift"], PREFIX + "Main"]
    if names["steer"]:
        # an arc around the front axle, out past the nose at bonnet height, arrows at its ends
        steer = pose[names["steer"]]
        radius = max(survey.nose - pivot.dot(forward) + length * 0.04, width * 0.62)
        sized(steer, "CTRL_Box", "THEME01", 0.1, wire=3.0)
        steer.custom_shape = rig_shapes.ensure("CR_Turn")
        steer.custom_shape_scale_xyz = (radius, radius, radius)
        align_shape(obj, steer, x=-left, y=forward, z=up)
        rig_shapes.place(obj, steer, pivot + up * height * 0.45)
        rig_shapes.color(steer, COLORS["steer"])
        shown.append(names["steer"])
    if names["body"]:
        # a slab over the roof
        top = pose[names["body"]]
        sized(top, "CTRL_Box", "THEME03", 1.0, wire=3.0)
        top.custom_shape_scale_xyz = (length * 4.5, width * 5.5, height * 0.5)
        align_shape(obj, top, x=forward, y=left, z=up)
        rig_shapes.place(obj, top, centre + up * height * 1.15)
        rig_shapes.color(top, COLORS["body"])
        shown.append(names["body"])
    for name in shown:
        collections["Vehicle Controls"].assign(armature.bones[name])
    for name, (control, sensor, lift, out, arch) in controls.items():
        if arch:
            # a double arrow over the wheel's arch: up and down
            sized(pose[arch], "CTRL_Box", "THEME03", 0.1, wire=2.5)
            pose[arch].custom_shape = rig_shapes.ensure("CR_UpDown")
            pose[arch].custom_shape_scale_xyz = (radii[name] * 0.45,) * 3
            align_shape(obj, pose[arch], x=forward, y=up)
            rig_shapes.color(pose[arch], COLORS["arch"])
            collections["Vehicle Wheel Controls"].assign(armature.bones[arch])
        # a ring on the wheel's outer side
        ring = pose[control]
        sized(ring, "CTRL_Spine", "THEME02", radii[name] * 2.3, wire=2.5)
        at = armature.bones[control].head_local
        side = abs((at - centre).dot(left))
        outside = width * 0.5 - side if side > width * 0.1 else 0.0
        rig_shapes.place(obj, ring, at + left * out * max(outside, 0.0))
        rig_shapes.color(ring, COLORS["wheel"])
        collections["Vehicle Wheel Controls"].assign(armature.bones[control])
    for name in armature.bones.keys():
        if name.startswith(PREFIX):
            if not any(name in c.bones for c in collections.values()):
                collections["Vehicle Other"].assign(armature.bones[name])      # the mechanism
            continue
        bone = armature.bones[name]
        if name in survey.wheel_chains:
            collections["Vehicle Wheels"].assign(bone)
        elif name in parts:
            # a box around what it moves
            points = parts[name]
            along = [p.dot(forward) for p in points]
            across = [p.dot(left) for p in points]
            high = [p.z for p in points]
            middle = (forward * (max(along) + min(along)) + left * (max(across) + min(across))) / 2.0
            middle.z = (max(high) + min(high)) / 2.0
            collections["Vehicle Parts"].assign(bone)
            sized(pose[name], "CTRL_Box", "THEME10", 1.0, wire=2.0)
            pose[name].custom_shape_scale_xyz = tuple(max(hi - lo, 0.04) * 11.0 for lo, hi in (
                (min(along), max(along)), (min(across), max(across)), (min(high), max(high))))
            align_shape(obj, pose[name], x=forward, y=left, z=up)
            rig_shapes.place(obj, pose[name], middle)
            rig_shapes.color(pose[name], COLORS["part"])
        else:
            collections["Vehicle Other"].assign(bone)
    armature[KEY] = True
    bpy.ops.object.mode_set(mode='OBJECT')
    attached = _attach(obj, survey, radii)
    return "%s: vehicle rig (%d wheels, %d steering, %s, %s, %d part(s)%s)" % (
        obj.name, len(survey.wheels), len(survey.steering), "a steering wheel" if survey.cockpit else "no steering wheel",
        "a body" if body_name else "no body", len(parts),
        ", %d wheel object(s) put on their hubs" % attached if attached else "")


def _attach(obj, survey, radii):
    """What sits on the vehicle as an object of its own (a Rocket Racing car's wheels: an armature each
    at a hub; a part), parented to the armature object - it wouldn't follow the drive: put on a bone,
    where it is. One at a hub goes on that wheel's spinning bone, anything else on the body."""
    pose = obj.pose.bones
    body = next((n for n in ("body", "frame") if n in pose), survey.root)
    hubs = [(n, (obj.matrix_world @ pose[n].head)) for n in survey.wheels]
    put = 0
    bpy.context.view_layer.update()
    for child in list(obj.children):
        if child.parent_type != 'OBJECT' or any(m.type == 'ARMATURE' and m.object == obj for m in child.modifiers):
            continue
        where = child.matrix_world.translation
        near = min(hubs, key=lambda h: (h[1] - where).length, default=None)
        bone = near[0] if near is not None and (near[1] - where).length < 0.5 * radii[near[0]] else body
        world = child.matrix_world.copy()
        child.parent_type, child.parent_bone = 'BONE', bone
        tail = obj.matrix_world @ pose[bone].matrix @ Matrix.Translation((0.0, obj.data.bones[bone].length, 0.0))
        child.matrix_parent_inverse = tail.inverted()
        child.matrix_world = world
        if child.type == 'ARMATURE':
            child.hide_set(True)        # (a wheel's own bones: nothing to pose - they deform it hidden)
        put += bone != body
    return put
