"""A physics asset from its dump (CUE4Parse JSON) as plain data: bodies with their shapes and settings, constraints
with their frames, limits and drives, the pairs that never collide. Values the cooked asset leaves out are the
engine's defaults."""
import json
import os

from ...logger import Log


def _enum(v, default):
    return str(v).split("::")[-1] if v is not None else default


def _vec(d, default=(0.0, 0.0, 0.0)):
    return [float(d.get(k, 0.0)) for k in "XYZ"] if isinstance(d, dict) else [float(x) for x in default]


def _rot(d):
    """An FRotator as (pitch, yaw, roll) degrees."""
    d = d or {}
    return [float(d.get("Pitch", 0.0)), float(d.get("Yaw", 0.0)), float(d.get("Roll", 0.0))]


def _shapes(geom):
    out = []
    for s in geom.get("SphylElems") or []:
        out.append({"kind": "capsule", "center": _vec(s.get("Center")), "rotation": _rot(s.get("Rotation")),
                    "radius": float(s.get("Radius", 1.0)), "length": float(s.get("Length", 1.0)),
                    "mass": s.get("bContributeToMass", True), "collides": _enum(s.get("CollisionEnabled"), "QueryAndPhysics")})
    for s in geom.get("SphereElems") or []:
        out.append({"kind": "sphere", "center": _vec(s.get("Center")), "rotation": [0.0, 0.0, 0.0],
                    "radius": float(s.get("Radius", 1.0)), "length": 0.0,
                    "mass": s.get("bContributeToMass", True), "collides": _enum(s.get("CollisionEnabled"), "QueryAndPhysics")})
    for s in geom.get("BoxElems") or []:
        out.append({"kind": "box", "center": _vec(s.get("Center")), "rotation": _rot(s.get("Rotation")),
                    "size": [float(s.get(k, 1.0)) for k in ("X", "Y", "Z")],
                    "mass": s.get("bContributeToMass", True), "collides": _enum(s.get("CollisionEnabled"), "QueryAndPhysics")})
    skipped = [k for k in ("ConvexElems", "TaperedCapsuleElems", "LevelSetElems", "SkinnedLevelSetElems") if geom.get(k)]
    return out, skipped


def _body(p):
    inst = p.get("DefaultInstance") or {}
    shapes, skipped = _shapes(p.get("AggGeom") or {})
    if skipped:
        Log.info("[Material Porter] dynamics: %s's %s shapes are left out" % (p.get("BoneName"), ", ".join(skipped)))
    return {"bone": p.get("BoneName"), "type": _enum(p.get("PhysicsType"), "PhysType_Default").replace("PhysType_", ""),
            "shapes": shapes,
            "mass": float(inst.get("MassInKgOverride", 100.0)) if inst.get("bOverrideMass") else None,
            "mass_scale": float(inst.get("MassScale", 1.0)),
            "inertia_scale": _vec(inst.get("InertiaTensorScale"), (1.0, 1.0, 1.0)),
            "com_nudge": _vec(inst.get("COMNudge")),
            "linear_damping": float(inst.get("LinearDamping", 0.01)), "angular_damping": float(inst.get("AngularDamping", 0.0)),
            "gravity": bool(inst.get("bEnableGravity", True)),
            "position_iterations": int(inst.get("PositionSolverIterationCount", 8)),
            "velocity_iterations": int(inst.get("VelocitySolverIterationCount", 2))}


def _soft(d, motion_keys):
    d = d or {}
    out = {k: _enum(d.get(k), "ACM_Free").replace("ACM_", "") for k in motion_keys}
    out.update({"soft": bool(d.get("bSoftConstraint", True)), "stiffness": float(d.get("Stiffness", 50.0)),
                "damping": float(d.get("Damping", 5.0)), "restitution": float(d.get("Restitution", 0.0)),
                "contact_distance": float(d.get("ContactDistance", 1.0))})
    return out


def _drive(d):
    d = d or {}
    return {"stiffness": float(d.get("Stiffness", 50.0)), "damping": float(d.get("Damping", 1.0)),
            "max_force": float(d.get("MaxForce", 0.0)), "position": bool(d.get("bEnablePositionDrive", False)),
            "velocity": bool(d.get("bEnableVelocityDrive", False))}


def _constraint(p):
    inst = p.get("DefaultInstance") or {}
    prof = inst.get("ProfileInstance") or {}
    lin = prof.get("LinearLimit") or {}
    cone = _soft(prof.get("ConeLimit"), ("Swing1Motion", "Swing2Motion"))
    cone.update(swing1=float((prof.get("ConeLimit") or {}).get("Swing1LimitDegrees", 45.0)),
                swing2=float((prof.get("ConeLimit") or {}).get("Swing2LimitDegrees", 45.0)))
    twist = _soft(prof.get("TwistLimit"), ("TwistMotion",))
    twist.update(limit=float((prof.get("TwistLimit") or {}).get("TwistLimitDegrees", 45.0)))
    ang = prof.get("AngularDrive") or {}
    linear_drive = prof.get("LinearDrive") or {}
    return {"bone1": inst.get("ConstraintBone1"), "bone2": inst.get("ConstraintBone2"),
            "pos1": _vec(inst.get("Pos1")), "pri1": _vec(inst.get("PriAxis1"), (1, 0, 0)), "sec1": _vec(inst.get("SecAxis1"), (0, 1, 0)),
            "pos2": _vec(inst.get("Pos2")), "pri2": _vec(inst.get("PriAxis2"), (1, 0, 0)), "sec2": _vec(inst.get("SecAxis2"), (0, 1, 0)),
            "angular_offset": _rot(inst.get("AngularRotationOffset")),
            "linear": {"limit": float(lin.get("Limit", 0.0)),
                       "motions": [_enum(lin.get(k), "LCM_Locked").replace("LCM_", "") for k in ("XMotion", "YMotion", "ZMotion")],
                       "soft": bool(lin.get("bSoftConstraint", False)), "stiffness": float(lin.get("Stiffness", 0.0)),
                       "damping": float(lin.get("Damping", 0.0))},
            "cone": cone, "twist": twist,
            "drive": {"mode": _enum(ang.get("AngularDriveMode"), "SLERP"), "slerp": _drive(ang.get("SlerpDrive")),
                      "swing": _drive(ang.get("SwingDrive")), "twist": _drive(ang.get("TwistDrive")),
                      "target": _rot(ang.get("OrientationTarget")), "spin": _vec(ang.get("AngularVelocityTarget")),
                      "linear": [_drive(linear_drive.get(k)) for k in ("XDrive", "YDrive", "ZDrive")],
                      "linear_target": _vec(linear_drive.get("PositionTarget"))},
            "projection": {"on": bool(prof.get("bEnableProjection", True)),
                           "linear_tolerance": float(prof.get("ProjectionLinearTolerance", 5.0)),
                           "angular_tolerance": float(prof.get("ProjectionAngularTolerance", 180.0)),
                           "linear_alpha": float(prof.get("ProjectionLinearAlpha", 1.0)),
                           "angular_alpha": float(prof.get("ProjectionAngularAlpha", 0.0))},
            "parent_dominates": bool(prof.get("bParentDominates", False)),
            "mass_conditioning": bool(prof.get("bEnableMassConditioning", True)),
            "disable_collision": bool(prof.get("bDisableCollision", False))}


def asset(path):
    """The physics asset at a dump path; None when missing or unreadable."""
    if not path or not os.path.isfile(path):
        return None
    try:
        raw = json.load(open(path, encoding="utf-8"))
    except ValueError as e:
        Log.warn("[Material Porter] dynamics: %s unreadable (%s)" % (path, e))
        return None
    pa = next((e for e in raw if e.get("Type") == "PhysicsAsset"), None)
    if pa is None:
        return None
    bodies = [_body(e.get("Properties") or {}) for e in raw if e.get("Type") == "SkeletalBodySetup"]
    # the table indexes the asset's own body list, which the dump gives as export references in that order
    order = [ref.get("ObjectName", "").split(":")[-1].rstrip("'") for ref in (pa.get("Properties") or {}).get("SkeletalBodySetups") or []]
    by_name = {e.get("Name"): _body(e.get("Properties") or {}) for e in raw if e.get("Type") == "SkeletalBodySetup"}
    if order and all(n in by_name for n in order):
        bodies = [by_name[n] for n in order]
    disabled = [[bodies[i]["bone"] for i in entry["Key"]["Indices"]] for entry in pa.get("CollisionDisableTable") or []
                if all(i < len(bodies) for i in entry["Key"]["Indices"])]
    solver = (pa.get("Properties") or {}).get("SolverSettings") or {}
    return {"bodies": bodies, "constraints": [_constraint(e.get("Properties") or {}) for e in raw if e.get("Type") == "PhysicsConstraintTemplate"],
            "disabled": disabled,
            "solver": {"position_iterations": int(solver.get("PositionIterations", 6)), "velocity_iterations": int(solver.get("VelocityIterations", 1)),
                       "projection_iterations": int(solver.get("ProjectionIterations", 1)), "cull_distance": float(solver.get("CullDistance", 3.0)),
                       "max_depenetration": float(solver.get("MaxDepenetrationVelocity", 0.0)), "fixed_step": float(solver.get("FixedTimeStep", 0.0)),
                       "linear_solver": bool(solver.get("bUseLinearJointSolver", True))}}
