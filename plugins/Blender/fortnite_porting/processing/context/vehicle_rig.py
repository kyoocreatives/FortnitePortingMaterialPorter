"""Control rig for a vehicle armature (Valet car, Rocket Racing car, dirt bike, tank...), in the style
of Rigacar / Car-Rig Pro: drive, steer, drift, body and suspension.

Fortnite vehicles share a layout: root > frame > body, each wheel under a differential:
axle_pivot > steering_knuckle > wheel_steering > wheel_disc > tire (tank: road_wheel > rot_road_wheel).
The rig reads it by name, falling back to bone positions.

- CR_Main (footprint on the ground) places and turns the vehicle. CR_Drive (under it, an arrow off
  the nose) is moved forward along its own axis to drive: each wheel spins by distance / radius
  (Transformation constraint: one metre turns a wheel 1/r radians).
- CR_Drift (arc behind the vehicle) turns about the front axle. The vehicle follows it (the root's
  Child Of), the rear swings out, and the front wheels counter-steer to keep pointing where it drives.
- CR_Steer (arc around the front axle) turns the front wheels, and the steering wheel three times as much.
- CR_Body (slab over the roof) moves and tilts the body on its suspension while the wheels stay put.
- A ring on each wheel (CR_Wheel_<bone>) lifts it (a bump) and turns it by hand (wheelspin). With a
  Ground set on the armature object each wheel follows it. The vehicle rises, pitches and rolls with
  the wheels' plane, each wheel taking what is left; the body optionally leans out of turns.
- Parts that move something (turret, doors, tailgate, mirrors) get a box around what they move.
The original bones keep their names, rest pose and hierarchy.
"""

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
STEER_LIMIT = pi / 3        # full lock of the steer control, in radians (60 degrees)
COCKPIT_RATIO = 3.0         # the steering wheel turns this many times as much as the road wheels
LEAN = 0.15                 # body roll per radian of steer when "Lean in Turns" is 1
PLANE_WHEELS = 10           # above this many wheels (a tank) the body plane uses only the corner wheels
COLORS = {"main": (0.96, 0.79, 0.05), "drive": (0.18, 0.55, 1.0), "steer": (1.0, 0.23, 0.19),
          "drift": (0.88, 0.25, 0.98), "body": (0.24, 0.86, 0.52), "wheel": (1.0, 0.58, 0.0), "arch": (0.6, 1.0, 0.25),
          "part": (0.13, 0.83, 0.93)}


def _axis(matrix, direction):
    """The bone's local axis letter (X, Y or Z) closest to a direction, and its sign."""
    best = max(range(3), key=lambda i: abs(matrix.col[i].to_3d().normalized().dot(direction)))
    return "XYZ"[best], 1.0 if matrix.col[best].to_3d().dot(direction) >= 0 else -1.0


class Survey:
    """What a vehicle skeleton is made of (edit bones, armature space)."""

    def __init__(self, edit_bones):
        self.bones = {b.name: b for b in edit_bones}
        tops = [b for b in edit_bones if b.parent is None]
        self.root = next((b.name for b in tops if b.name.lower() == "root"), tops[0].name if tops else None)
        self.ground = self.bones[self.root].head.z if self.root else 0.0
        named = [n for n in self.bones if SPIN.search(n)]
        if not named:
            named = [n for n in self.bones if WHEELISH.search(n) and not NOT_A_WHEEL.search(n)]
        # one spinning bone per wheel: the topmost (a disc and its tire turn as one)
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
        self.fit(heads)         # refitted to the meshes' bounds later, if there are any
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
        """Set the vehicle's length, width, height, footprint centre (on the ground) and nose/tail
        positions from points (armature space)."""
        along = [p.dot(self.forward) for p in points]
        side = [p.dot(self.left) for p in points]
        self.length = max(max(along) - min(along), 0.5)
        self.width = max(max(side) - min(side), 0.3)
        self.height = max(max(p.z for p in points) - self.ground, 0.3)
        self.centre = (self.forward * (max(along) + min(along)) + self.left * (max(side) + min(side))) / 2.0
        self.centre.z = self.ground
        self.nose, self.tail_end = max(along), min(along)

    def top(self, name):
        """The wheel's topmost bone under its differential (or the frame); this is the bone that lifts it."""
        at = self.bones[name]
        while at.parent is not None and at.parent.name in self.wheel_chains:
            at = at.parent
        return at.name

    def corner(self, name):
        """Bones the body carries at a wheel's corner (same suffix, e.g. _fr_l: shock top, wheel well spreader),
        which ride with the wheel's arch."""
        suffix = re.search(r"_[a-z]+_[lr]$", name, re.IGNORECASE)
        if suffix is None:
            return []
        return [n for n in self.bones if n.lower().endswith(suffix.group().lower()) and n not in self.wheel_chains
                and not HELPER.search(n) and not any(p.name in self.wheel_chains for p in self.bones[n].parent_recursive)]

    def radius(self, name):
        height = self.bones[name].head.z - self.ground
        return height if height > 0.05 else 0.35


def _transform(owner, target, subtarget, source, to_axis, scale, map_from, name=None):
    """Transformation constraint: the target's local `source` (location along Y, or rotation about it)
    rotates the owner about its local `to_axis` times `scale`, extrapolated past the range."""
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
    """Transformation constraint: the target's local location along `source` moves the owner along
    its local `to_axis` times `scale`."""
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


def _moved_along(owner, target, subtarget, source, direction, name):
    """Like _moved, along a direction in the owner's local space (a tilted bone still moves straight)."""
    con = _moved(owner, target, subtarget, source, "X", 1.0, name)
    for axis, amount in zip("xyz", direction):
        setattr(con, "map_to_%s_from" % axis, source)
        setattr(con, "to_min_%s" % axis, -amount)
        setattr(con, "to_max_%s" % axis, amount)
    return con


def _property(obj, name, value, description):
    obj[name] = value
    obj.id_properties_ui(name).update(min=0.0, max=1.0, description=description)


def _drive_channel(obj, pose_bone, path, index, expression, variables):
    """Add a driver to a pose bone channel. `variables` are (name, bone, transform type) for a bone
    channel or (name, property) for a custom property of the armature object."""
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
    """Linear sum as a driver expression, e.g. '0.25*w0-0.25*w1'."""
    text = "".join("%+.5f*%s" % (c, name) for name, c in coefficients if abs(c) > 1e-6)
    return text.lstrip("+") or "0"


def _signed(way, expression):
    return ("-(%s)" if way < 0 else "%s") % expression


def _ground_changed(self, context):
    """Update callback of the armature's Ground: wheel sensors project onto it; with none they are muted."""
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
    """Points (armature space) each part bone moves: vertices with more than half their weight on it."""
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
    ensure_blend_data()             # loads the control shapes (CTRL_Root, CTRL_Box...)
    # Use the meshes for the vehicle's extent: bone tails reach past it (effect points, sockets).
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

    # Controls (Y forward, on the ground under the vehicle) and the mechanism under them.
    base = survey.bones[survey.root].head.copy()
    main = new("Main", base, base + forward * length * 0.6, None)
    drive = new("Drive", base, base + forward * length * 0.4, main)
    front = survey.front_axle
    pivot = Vector((front.x, front.y, base.z)) if front is not None else survey.centre.copy()
    reach = max(pivot.dot(forward) - survey.tail_end, length * 0.3)
    drift = new("Drift", pivot, pivot - forward * reach, drive)          # Y points backwards; swings the rear
    steer = new("Steer", pivot, pivot + up * length * 0.15, drift) if survey.steering and front is not None else None
    where = {n: (survey.bones[n].head.dot(forward), survey.bones[n].head.dot(left)) for n in survey.wheels}
    # Wheels defining the vehicle's plane (a tank: its corner wheels).
    plane = list(survey.wheels)
    if len(plane) > PLANE_WHEELS:
        corner = lambda k: max(plane, key=lambda n: k[0] * where[n][0] + k[1] * where[n][1])
        plane = list(dict.fromkeys(corner(k) for k in ((1, 1), (1, -1), (-1, 1), (-1, -1))))
    # The suspension (the vehicle's root follows it) turns about the middle of the plane wheels, at hub height.
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
        follow = new("Body_Follow", body.head, body.tail, body_control)  # the body's rest placement, under CR_Body
        follow.roll = body.roll
    controls = {}                   # wheel -> (ring, ground sensor, lift, side: +1 left, arch)
    for name in survey.wheels:
        at = survey.bones[name].head.copy()
        out = 1.0 if (at - survey.centre).dot(left) >= 0 else -1.0
        under = Vector((at.x, at.y, base.z))
        sensor = new("Ground_" + name, under, under + left * out * radii[name] * 0.6, drift)
        ring = new("Wheel_" + name, at, at + left * out * radii[name] * 0.6, sensor)
        lift = new("Lift_" + name, at, at + left * out * radii[name] * 0.3, drift)
        # arch control: over the wheel, at the vehicle's side; rides with the body
        arch = None
        if survey.top(name) != name:
            side = abs((at - survey.centre).dot(left))
            outside = max(width * 0.5 - side, 0.0) if side > width * 0.1 else 0.0
            over = at + up * radii[name] * 1.3 + left * out * outside
            arch = new("Arch_" + name, over, over + left * out * radii[name] * 0.4, lean).name
        controls[name] = (ring.name, sensor.name, lift.name, out, arch)
    # Each control's local axes for up, left and forward (letter and sign).
    axes = {b.name: {"up": _axis(b.matrix, up), "left": _axis(b.matrix, left), "forward": _axis(b.matrix, forward)}
            for b in edit if b.name.startswith(PREFIX)}
    tops = {n: survey.top(n) for n in survey.wheels}
    top_up = {t: _axis(edit[t].matrix, up) for t in set(tops.values())}
    corners = {n: {b: edit[b].matrix.to_3x3().transposed() @ up for b in survey.corner(n)} for n in survey.wheels if controls[n][4]}
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

    # The vehicle root follows CR_Suspension from its rest position: drifting, riding on its wheels.
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
        # driving forward 1 m turns the wheel 1/r radians about its axle (forward roll is about +left)
        con = _transform(pose[name], obj, names["drive"], "Y", axis, sign / radii[name], 'LOCATION')
        _driven(obj, con, "auto_wheels")
        # the wheel ring turns it too (about the ring's Y, outwards)
        ring, sensor, lift, out, arch = controls[name]
        _transform(pose[name], obj, ring, "Y", axis, sign * out, 'ROTATION', name="CR Spin")
        # the ring also lifts it, with its chain from the differential down
        letter, way = axes[lift]["up"]
        top_letter, top_way = top_up[tops[name]]
        _moved(pose[tops[name]], obj, lift, letter, top_letter, way * top_way, "CR Lift")
        if arch:
            # The arch moves the wheel's zone (chain top and what hangs off it: upright, fender, shock,
            # caliper, plus the body's parts at that corner) but not the wheel, so the spinning bone moves back.
            letter, way = axes[arch]["up"]
            _moved(pose[tops[name]], obj, arch, letter, top_letter, way * top_way, "CR Arch")
            s_letter, s_way = spin_up[name]
            _moved(pose[name], obj, arch, letter, s_letter, -way * s_way, "CR Arch Keep")
            for part, part_up in corners[name].items():
                _moved_along(pose[part], obj, arch, letter, part_up * way, "CR Arch")
        # ground projection, active once the armature object has a Ground
        con = pose[sensor].constraints.new('SHRINKWRAP')
        con.name = "CR Ground"
        con.shrinkwrap_type = 'PROJECT'
        con.project_axis, con.project_axis_space = 'NEG_Z', 'WORLD'
        con.use_project_opposite = True             # also project upwards, for hills above the wheel
        con.project_limit = max(height * 2.0, 1.0)
        con.target = getattr(obj, "fpmp_ground", None)
        con.mute = con.target is None
    drift_letter, drift_way = axes[names["drift"]]["up"]
    for name, (axis, sign) in list(turns.items()) + [(n, (a, s * COCKPIT_RATIO)) for n, (a, s) in cockpits.items()]:
        if names["steer"]:
            con = _transform(pose[name], obj, names["steer"], "Y", axis, sign, 'ROTATION')
            _driven(obj, con, "auto_steer")
        # drifting turns the vehicle about its front axle; the front wheels counter-turn
        con = _transform(pose[name], obj, names["drift"], drift_letter, axis, -sign * drift_way, 'ROTATION', name="CR Counter")
        _driven(obj, con, "countersteer")
    if names["steer"]:
        # limit the steer control to the wheels' lock
        limit = pose[names["steer"]].constraints.new('LIMIT_ROTATION')
        limit.name = "CR Steer Lock"
        limit.use_limit_y, limit.min_y, limit.max_y = True, -STEER_LIMIT, STEER_LIMIT
        limit.owner_space = 'LOCAL'
        pose[names["steer"]].rotation_mode = 'YXZ'
        pose[names["steer"]].lock_rotation = (True, False, True)
        pose[names["steer"]].lock_location = (True, True, True)
    pose[names["drive"]].lock_location = (True, False, True)      # moves only along its own axis
    pose[names["drift"]].lock_location = (True, True, True)       # only rotates, about the front axle
    pose[names["drift"]].lock_rotation = tuple(letter != drift_letter for letter in "XYZ")
    for name, (ring, sensor, lift, out, arch) in controls.items():
        letter = axes[ring]["up"][0]
        pose[ring].lock_location = tuple(a != letter for a in "XYZ")       # moves up/down only
        pose[ring].lock_rotation = (True, False, True)                     # rotates about its axle only
        if arch:
            pose[arch].lock_location = tuple(a != axes[arch]["up"][0] for a in "XYZ")
            pose[arch].lock_rotation = pose[arch].lock_scale = (True, True, True)

    # Suspension: a wheel's height is its ring's lift plus its ground's. The vehicle rises by the mean
    # height and pitches/rolls with the least-squares plane through the wheels (linear in the heights);
    # each wheel takes the remainder, which is zero on flat or evenly sloping ground.
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
        clamp = "asin(min(1,max(-1,%s)))*f"   # the slope is a sine: a wheel 1 m ahead rises by it
        if spread_a > 1e-4:
            # nose up when the front is higher: that is negative about +left
            letter, way = axes[names["suspension"]]["left"]
            _drive_channel(obj, bone, "rotation_euler", "XYZ".index(letter),
                           _signed(-way, clamp % through(lambda w: (where[w][0] - mean_a) / spread_a)), plane_variables)
        if spread_s > 1e-4:
            # left side up when the left is higher: positive about +forward
            letter, way = axes[names["suspension"]]["forward"]
            _drive_channel(obj, bone, "rotation_euler", "XYZ".index(letter),
                           _signed(way, clamp % through(lambda w: (where[w][1] - mean_s) / spread_s)), plane_variables)
        for name in survey.wheels:
            # remainder: the wheel's height minus the plane's height there
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
        # a left turn (+ about up) rolls the body right (left side up): positive about +forward
        letter, way = axes[names["lean"]]["forward"]
        pose[names["lean"]].rotation_mode = 'XYZ'
        _drive_channel(obj, pose[names["lean"]], "rotation_euler", "XYZ".index(letter),
                       _signed(way, "%g*l*st" % LEAN), [("l", "lean"), ("st", names["steer"], "ROT_Y")])
    if body_name:
        # the body follows CR_Body; the wheels, on the frame, stay put
        copy = pose[body_name].constraints.new('COPY_TRANSFORMS')
        copy.name = "CR Body"
        copy.target, copy.subtarget = obj, PREFIX + "Body_Follow"

    # Shown: controls, wheel rings, parts that move. Hidden: wheel bones, the mechanism and the game's
    # helpers (sockets, effect points, seats).
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
    # footprint with a chevron at the front
    main.custom_shape = rig_shapes.footprint(PREFIX + "Footprint_" + obj.name, length * 1.03, width * 1.03)
    align_shape(obj, main, x=-left, y=forward, z=up)
    rig_shapes.place(obj, main, centre)
    rig_shapes.color(main, COLORS["main"])
    drive = pose[names["drive"]]
    sized(drive, "CTRL_Box", "THEME04", 0.1, wire=3.0)
    drive.custom_shape = rig_shapes.ensure("CR_Arrow")
    drive.custom_shape_scale_xyz = (width * 0.9, length * 0.3, 1.0)
    align_shape(obj, drive, x=-left, y=forward, z=up)               # arrow on the ground in front of the nose
    rig_shapes.place(obj, drive, centre + forward * (survey.nose - centre.dot(forward) + length * 0.06))
    rig_shapes.color(drive, COLORS["drive"])
    drift = pose[names["drift"]]
    sized(drift, "CTRL_Box", "THEME06", 0.1, wire=3.0)
    drift.custom_shape = rig_shapes.ensure("CR_Swing")
    radius = reach + length * 0.08
    drift.custom_shape_scale_xyz = (radius, radius, radius)
    align_shape(obj, drift, x=left, y=-forward, z=up)               # arc behind the vehicle, about the front axle
    rig_shapes.place(obj, drift, pivot + up * height * 0.2)
    rig_shapes.color(drift, COLORS["drift"])
    shown = [names["drive"], names["drift"], PREFIX + "Main"]
    if names["steer"]:
        # arc around the front axle past the nose at bonnet height, arrows at its ends
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
            # double arrow over the wheel arch
            sized(pose[arch], "CTRL_Box", "THEME03", 0.1, wire=2.5)
            pose[arch].custom_shape = rig_shapes.ensure("CR_UpDown")
            pose[arch].custom_shape_scale_xyz = (radii[name] * 0.45,) * 3
            align_shape(obj, pose[arch], x=forward, y=up)
            rig_shapes.color(pose[arch], COLORS["arch"])
            collections["Vehicle Wheel Controls"].assign(armature.bones[arch])
        # ring on the wheel's outer side
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
                collections["Vehicle Other"].assign(armature.bones[name])      # rig mechanism bones
            continue
        bone = armature.bones[name]
        if name in survey.wheel_chains:
            collections["Vehicle Wheels"].assign(bone)
        elif name in parts:
            # box around what the part moves
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
    """Re-parent objects parented to the armature object (e.g. a Rocket Racing car's wheels, one
    armature each) to a bone, since they would not follow the drive. An object at a hub goes on that
    wheel's spinning bone, anything else on the body. Returns the number put on wheels."""
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
            child.hide_set(True)        # a wheel's own armature has nothing to pose; it still deforms while hidden
        put += bone != body
    return put
