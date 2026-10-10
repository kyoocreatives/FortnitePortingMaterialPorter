"""Fortnite's AnimDynamics nodes from a part's anim blueprint dump (CUE4Parse JSON), in the order the graph runs them,
with the engine's defaults where the cooked asset leaves a value out."""
import json
import os

from ...logger import Log


def _enum(v, default):
    return str(v).split("::")[-1] if v is not None else default


def _vec(d, default=(0.0, 0.0, 0.0)):
    return [float(d.get(k, 0.0)) for k in "XYZ"] if isinstance(d, dict) else [float(x) for x in default]


def _bone(ref):
    name = (ref or {}).get("BoneName")
    return None if name in (None, "None") else name


def _body(d):
    setup = d.get("ConstraintSetup") or {}
    box = _vec(d.get("BoxExtents"), (10.0, 10.0, 10.0))
    if 0.0 in box and any(box):         # no volume: the engine's inertia would be NaN
        Log.warn("[Material Porter] dynamics: %s has a flat box, simulated as a unit cube" % _bone(d.get("BoundBone")))
        box = [1.0, 1.0, 1.0]
    return {"bone": _bone(d.get("BoundBone")), "box": box, "offset": _vec(d.get("LocalJointOffset")),
            "lin_types": [_enum(setup.get("Linear%sLimitType" % a), "Limited") for a in "XYZ"],
            "lin_min": _vec(setup.get("LinearAxesMin")), "lin_max": _vec(setup.get("LinearAxesMax")),
            "angular": _enum(setup.get("AngularConstraintType"), "Angular"),
            "twist": _enum(setup.get("TwistAxis"), "AxisX")[-1], "target_axis": _enum(setup.get("AngularTargetAxis"), "AxisX")[-1],
            "cone": float(setup.get("ConeAngle", 0.0)),
            "ang_min": _vec(setup.get("AngularLimitsMin")), "ang_max": _vec(setup.get("AngularLimitsMax")),
            "target": _vec(setup.get("AngularTarget")),
            "collision": _enum(d.get("CollisionType"), "CoM"), "radius": float(d.get("SphereCollisionRadius", 10.0))}


def _transform(t):
    r = (t or {}).get("Rotation") or {}
    return [[r.get("W", 1.0), r.get("X", 0.0), r.get("Y", 0.0), r.get("Z", 0.0)], _vec((t or {}).get("Translation"))]


def node(p):
    """One AnimDynamics node's settings as plain data."""
    space = p.get("SimSpaceSettings") or {}
    out = {
        "bone": _bone(p.get("BoundBone")), "chain_end": _bone(p.get("ChainEnd")),
        "space": _enum(p.get("SimulationSpace"), "Component"), "relative_bone": _bone(p.get("RelativeSpaceBone")),
        "bodies": [_body(d) for d in p.get("PhysicsBodyDefinitions") or [{"BoundBone": p.get("BoundBone")}]],
        "lin_damping": float(p.get("LinearDampingOverride", 0.0)) if p.get("bOverrideLinearDamping") else None,
        "ang_damping": float(p.get("AngularDampingOverride", 0.0)) if p.get("bOverrideAngularDamping") else None,
        "bias": float(p.get("AngularBiasOverride", 0.0)) if p.get("bOverrideAngularBias") else None,
        "gravity_scale": float(p.get("GravityScale", 1.0)),
        "gravity_override": _vec(p.get("GravityOverride")) if p.get("bUseGravityOverride") else None,
        "gravity_in_sim": bool(p.get("bGravityOverrideInSimSpace", False)),
        "lin_spring": float(p.get("LinearSpringConstant", 0.0)) if p.get("bLinearSpring") else None,
        "ang_spring": float(p.get("AngularSpringConstant", 0.0)) if p.get("bAngularSpring") else None,
        "external_force": _vec(p.get("ExternalForce")),
        "space_alpha": float(space.get("SimSpaceAngularAlpha", 0.0)),
        "max_ang_vel": float(space.get("MaxAngularVelocity", 10000.0)),
        "max_ang_acc": float(space.get("MaxAngularAcceleration", 10000.0)),
        "external_ang_vel": _vec(space.get("ExternalAngularVelocity")),
        "planar": [{"bone": _bone(lim.get("DrivingBone")), "plane": _transform(lim.get("PlaneTransform"))}
                   for lim in p.get("PlanarLimits") or []] if p.get("bUsePlanarLimit", True) else [],
        "spherical": [{"bone": _bone(lim.get("DrivingBone")), "offset": _vec(lim.get("SphereLocalOffset")),
                       "radius": float(lim.get("LimitRadius", 30.0)), "type": _enum(lim.get("LimitType"), "Outer")}
                      for lim in p.get("SphericalLimits") or []] if p.get("bUseSphericalLimits") else [],
        "pre": int(p.get("NumSolverIterationsPreUpdate", 4)), "post": int(p.get("NumSolverIterationsPostUpdate", 1)),
        "alpha": float(p.get("Alpha", 1.0))}
    ignored = [what for what, on in (("wind", p.get("bEnableWind")),
                                     ("retargeting", (p.get("RetargetingSettings") or {}).get("bEnabled")),
                                     ("its alpha input", _enum(p.get("AlphaInputType"), "Float") != "Float")) if on]
    if ignored:
        Log.info("[Material Porter] dynamics: %s ignores %s" % (out["bone"], ", ".join(ignored)))
    return out


def rigid(p, physics):
    """A RigidBody node's settings and the physics asset it simulates."""
    space = p.get("SimSpaceSettings") or {}
    return {"kind": "rigid_body", "space": _enum(p.get("SimulationSpace"), "ComponentSpace"),
            "base_bone": _bone(p.get("BaseBoneRef")),
            "gravity": _vec(p.get("OverrideWorldGravity")) if p.get("bOverrideWorldGravity") else None,
            "use_default_as_simulated": bool(p.get("bUseDefaultAsSimulated", False)),
            "external_force": _vec(p.get("ExternalForce")),
            "component_acc": {"acc_scale": _vec(p.get("ComponentLinearAccScale")), "vel_scale": _vec(p.get("ComponentLinearVelScale")),
                              "clamp": _vec(p.get("ComponentAppliedLinearAccClamp"), (10000.0, 10000.0, 10000.0))},
            "sim_space": {"world_alpha": float(space.get("WorldAlpha", 0.0)), "velocity_scale_z": float(space.get("VelocityScaleZ", 1.0)),
                          "damping_alpha": float(space.get("DampingAlpha", 1.0)),
                          "max_linear_velocity": float(space.get("MaxLinearVelocity", 10000.0)),
                          "max_angular_velocity": float(space.get("MaxAngularVelocity", 10000.0)),
                          "max_linear_acceleration": float(space.get("MaxLinearAcceleration", 10000.0)),
                          "max_angular_acceleration": float(space.get("MaxAngularAcceleration", 10000.0)),
                          "drag": _vec(space.get("ExternalLinearDragV")), "linear_velocity": _vec(space.get("ExternalLinearVelocity")),
                          "angular_velocity": _vec(space.get("ExternalAngularVelocity"))},
            "alpha": float(p.get("Alpha", 1.0)), "physics": physics}


def constraint(p):
    """A Constraint node: the bone pulled toward weighted targets."""
    setups = [{"target": _bone(c.get("TargetBone")), "offset": _enum(c.get("OffsetOption"), "Offset_RefPose"),
               "type": _enum(c.get("TransformType"), "Translation"),
               "axes": [bool((c.get("PerAxis") or {}).get(k, True)) for k in ("bX", "bY", "bZ")]}
              for c in p.get("ConstraintSetup") or []]
    weights = [float(w) for w in p.get("ConstraintWeights") or []]
    return {"kind": "constraint", "bone": _bone(p.get("BoneToModify")), "setups": setups,
            "weights": weights + [0.0] * (len(setups) - len(weights)), "alpha": float(p.get("Alpha", 1.0))}


def modify_bone(p):
    """A ModifyBone node: translation, rotation and scale set or added in a chosen space."""
    rot = p.get("Rotation") or {}
    out = {"kind": "modify_bone", "bone": _bone(p.get("BoneToModify")), "translation": _vec(p.get("Translation")),
           "rotation": [float(rot.get("Pitch", 0.0)), float(rot.get("Yaw", 0.0)), float(rot.get("Roll", 0.0))],
           "scale": _vec(p.get("Scale"), (1.0, 1.0, 1.0)), "alpha": float(p.get("Alpha", 1.0))}
    for k in ("Translation", "Rotation", "Scale"):
        out[k.lower() + "_mode"] = _enum(p.get(k + "Mode"), "BMM_Ignore").replace("BMM_", "")
        out[k.lower() + "_space"] = _enum(p.get(k + "Space"), "BCS_ComponentSpace").replace("BCS_", "")
    return out


READERS = {"AnimGraphNode_AnimDynamics": lambda p, phys: dict(node(p), kind="anim_dynamics"),
           "AnimGraphNode_RigidBody": lambda p, phys: rigid(p, phys) if phys else None,
           "AnimGraphNode_Constraint": lambda p, phys: constraint(p),
           "AnimGraphNode_ModifyBone": lambda p, phys: modify_bone(p)}


def _reader(name):
    return next((r for prefix, r in READERS.items() if name == prefix or name.startswith(prefix + "_")), None)


def _part(raw, physics=None):
    cls = next((e for e in raw if e.get("Type") == "AnimBlueprintGeneratedClass"), None)
    cdo = next((e for e in raw if str(e.get("Name", "")).startswith("Default__")), None)
    if cls is None or cdo is None:
        return []
    # a pose link's LinkID indexes the class's anim node properties in field order
    names = [c.get("Name") for c in cls.get("ChildProperties") or [] if str(c.get("Name", "")).startswith("AnimGraphNode_")]
    props = cdo.get("Properties") or {}
    order, seen = [], set()

    def visit(name):            # inputs first: the order the graph evaluates
        if name in seen or not isinstance(props.get(name), dict):
            return
        seen.add(name)
        for v in props[name].values():
            for link in v if isinstance(v, list) else [v]:      # blends take arrays of pose links
                if isinstance(link, dict) and isinstance(link.get("LinkID"), int) and 0 <= link["LinkID"] < len(names):
                    visit(names[link["LinkID"]])
        order.append(name)

    for root in [n for n in names if n.startswith("AnimGraphNode_Root")]:
        visit(root)
    unreached = [n for n in names if _reader(n) and n not in seen and n in props]
    if unreached:           # state machine states aren't linked from the root
        Log.info("[Material Porter] dynamics: %d nodes outside the main graph run last" % len(unreached))
    skipped = [n for n in order if not _reader(n) and "ComponentPose" in props[n]]
    if skipped:
        Log.info("[Material Porter] dynamics: not replayed: %s" % ", ".join(n.replace("AnimGraphNode_", "") for n in skipped))
    found = [_reader(n)(props[n], physics) for n in order + unreached if _reader(n)]
    return [n for n in found if n and (n["kind"] == "rigid_body" or n["bone"])]


def nodes(paths, physics=None):
    """Every part's replayed nodes, parts in import order (the body's first: the others copy its pose); physics names
    each part's physics asset dump."""
    from .physics_read import asset
    out = []
    for i, path in enumerate(paths):
        if not path or not os.path.isfile(path):
            continue
        try:
            raw = json.load(open(path, encoding="utf-8"))
        except ValueError as e:
            Log.warn("[Material Porter] dynamics: %s unreadable (%s)" % (path, e))
            continue
        out += [dict(n, part=i) for n in _part(raw, asset(physics[i]) if physics and i < len(physics) else None)]
    return out
