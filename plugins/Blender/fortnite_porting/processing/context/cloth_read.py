"""A mesh's cloth file (the app's `<mesh>.cloth.json`) as plain data for the solver: per clothing asset its simulation
mesh at the bind pose, bone weights by name, weight maps by name, configs over the engine's defaults, tethers and
collision shapes; plus the render mapping of the mesh LOD that was imported."""
import json
import os
import re

from mathutils import Vector

from ...logger import Log
from .rigid_bodies import rotator

# EChaosWeightMapTarget ids -> the names the runtime looks maps up by
TARGETS = {1: "MaxDistance", 2: "BackstopDistance", 3: "BackstopRadius", 4: "AnimDriveStiffness", 5: "AnimDriveDamping",
           6: "TetherStiffness", 7: "TetherScale", 8: "Drag", 9: "Lift", 10: "EdgeStiffness", 11: "BendingStiffness",
           12: "AreaStiffness", 13: "BucklingStiffness", 14: "Pressure", 15: "FlatnessRatio", 16: "OuterDrag", 17: "OuterLift"}

# UChaosClothConfig's in-class defaults (a cooked config stores only what differs); [low, high] pairs are weighted
CONFIG = {"MassMode": "Density", "UniformMass": 0.00015, "TotalMass": 0.5, "Density": 0.35, "MinPerParticleMass": 0.0001,
          "EdgeStiffnessWeighted": [1.0, 1.0], "BendingStiffnessWeighted": [1.0, 1.0], "bUseBendingElements": False,
          "BucklingRatio": 0.0, "BucklingStiffnessWeighted": [1.0, 1.0], "FlatnessRatio": [0.0, 1.0],
          "AreaStiffnessWeighted": [1.0, 1.0], "TetherStiffness": [1.0, 1.0], "TetherScale": [1.0, 1.0],
          "bUseGeodesicDistance": True, "CollisionThickness": 1.0, "FrictionCoefficient": 0.8, "bUseLegacyBackstop": False,
          "DampingCoefficient": 0.01, "LocalDampingCoefficient": 0.0, "bUsePointBasedWindModel": False,
          "Drag": [0.035, 1.0], "Lift": [0.035, 1.0], "bEnableOuterDrag": False, "OuterDrag": [0.035, 1.0],
          "bEnableOuterLift": False, "OuterLift": [0.035, 1.0], "bUseGravityOverride": False, "GravityScale": 1.0,
          "Gravity": [0.0, 0.0, -980.665], "Pressure": [0.0, 1.0], "AnimDriveStiffness": [0.0, 1.0],
          "AnimDriveDamping": [0.0, 1.0], "VelocityScaleSpace": "ReferenceBone", "LinearVelocityScale": [0.75, 0.75, 0.75],
          "AngularVelocityScale": 0.75, "FictitiousForcesModel": "FullScale", "FictitiousAngularScale": 1.0}
SUFFIX = r"_\d+_[0-9A-Za-z]+$"      # a blueprint struct field's cooked name ends in _<n>_<guid>
SHARED = {"IterationCount": 1, "MaxIterationCount": 10, "SubdivisionCount": 1}


def _xyz(d):
    return [float(d.get(k, 0.0)) for k in "XYZ"]


def _value(default, v):
    """A dump value in the default's shape: a weighted pair fills the sub-field it leaves out from the default."""
    if isinstance(default, list) and len(default) == 2 and isinstance(v, dict):
        return [float(v.get("Low", default[0])), float(v.get("High", default[1]))]
    if isinstance(default, list) and isinstance(v, dict):
        return _xyz(v)
    if isinstance(default, str):
        return str(v).split("::")[-1]
    if isinstance(default, bool):
        return bool(v)
    if isinstance(default, float) and isinstance(v, dict) and "Low" in v:
        return float(v["Low"])      # a scalar the asset stores as a weighted value (buckling ratio)
    return float(v) if isinstance(default, float) else v


def _config(props, defaults):
    out = dict((k, list(v) if isinstance(v, list) else v) for k, v in defaults.items())
    for k, v in (props or {}).items():
        if k in defaults:
            out[k] = _value(defaults[k], v)
    return out


def _outer(export):
    """An export's outer object name ("Type'Package:Name'" in the dump)."""
    outer = export.get("Outer")
    name = outer.get("ObjectName", "") if isinstance(outer, dict) else str(outer or "")
    return name.rstrip("'").split(":")[-1].split("'")[-1]


def _tethers(data):
    """Batches of (kinematic, dynamic, rest length)."""
    out = []
    for batch in (data or {}).get("Tethers") or []:
        rows = []
        for t in batch:
            v = list(t.values()) if isinstance(t, dict) else list(t)
            rows.append([int(v[0]), int(v[1]), float(v[2])])
        out.append(rows)
    return out


def _colliders(path):
    """The physics asset's shapes in bone space, in the order the cloth solver adds them: capsules (sphyls and tapered
    capsules, body by body), then the spheres, then the boxes."""
    if not path or not os.path.isfile(path):
        return []
    try:
        raw = json.load(open(path, encoding="utf-8"))
    except ValueError:
        return []
    capsules, spheres, boxes = [], [], []
    for e in raw:
        if e.get("Type") != "SkeletalBodySetup":
            continue
        p = e.get("Properties") or {}
        bone, geom = p.get("BoneName"), p.get("AggGeom") or {}

        def axis_ends(s, length):
            q = rotator(*[float((s.get("Rotation") or {}).get(k, 0.0)) for k in ("Pitch", "Yaw", "Roll")])
            c, half = Vector(_xyz(s.get("Center") or {})), q @ Vector((0.0, 0.0, length / 2.0))
            return q, c, [list(c + half), list(c - half)]

        for s in geom.get("SphylElems") or []:
            q, c, ends = axis_ends(s, float(s.get("Length", 1.0)))
            if float(s.get("Length", 1.0)) <= 0.0:
                spheres.append({"bone": bone, "kind": "sphere", "center": list(c), "radius": float(s.get("Radius", 1.0))})
            else:
                capsules.append({"bone": bone, "kind": "capsule", "ends": ends, "radius": float(s.get("Radius", 1.0))})
        for s in geom.get("SphereElems") or []:
            spheres.append({"bone": bone, "kind": "sphere", "center": _xyz(s.get("Center") or {}), "radius": float(s.get("Radius", 1.0))})
        for s in geom.get("TaperedCapsuleElems") or []:
            r = [float(s.get("Radius0", 1.0)), float(s.get("Radius1", 1.0))]
            length, one_sided = float(s.get("Length", 1.0)), bool(s.get("bOneSidedCollision", False))
            q, c, ends = axis_ends(s, length)
            if length <= 0.0 and not one_sided:
                spheres.append({"bone": bone, "kind": "sphere", "center": list(c), "radius": max(r)})
            else:
                capsules.append({"bone": bone, "kind": "tapered", "ends": ends, "radii": r, "one_sided": one_sided,
                                 "plane": list(q @ Vector((1.0, 0.0, 0.0)))})
        for s in geom.get("BoxElems") or []:
            q = rotator(*[float((s.get("Rotation") or {}).get(k, 0.0)) for k in ("Pitch", "Yaw", "Roll")])
            boxes.append({"bone": bone, "kind": "box", "center": _xyz(s.get("Center") or {}), "rotation": list(q),
                          "half": [float(s.get(k, 1.0)) / 2.0 for k in "XYZ"]})
    return capsules + spheres + boxes


def _reference(asset, parent_of, influences):
    """ReferenceBoneIndex is a skeleton index: the used bones name most; else the deepest common ancestor of every
    weighted bone, as the engine derives it."""
    names = dict(zip(asset.get("UsedBoneIndices") or [], asset.get("UsedBoneNames") or []))
    index = asset.get("ReferenceBoneIndex", 0)
    if index in names:
        return names[index]
    chains = []
    for bone in {b for inf in influences for b, w in inf if w > 1e-8}:
        chain = []
        while bone:
            chain.insert(0, bone)
            bone = parent_of.get(bone)
        chains.append(chain)
    common = None
    for i, bone in enumerate(chains[0] if chains else []):
        if all(len(c) > i and c[i] == bone for c in chains):
            common = bone
    return common or next((b for b, p in parent_of.items() if p is None), None)


def _cloth(asset, exports, physics, lod, parent_of):
    props = asset.get("Properties") or {}
    configs = {}
    for entry in props.get("ClothConfigs") or []:
        # "Type'Package:Outer.Name'": names repeat across outers (each asset's own ChaosClothConfig)
        path = str((entry.get("Value") or {}).get("ObjectName", "")).rstrip("'").split(":")[-1].split(".")
        found = [e for e in exports if e.get("Name") == path[-1] and e.get("Type") == entry.get("Key")]
        outer = [e for e in found if len(path) > 1 and _outer(e) == path[-2]]
        configs[entry.get("Key")] = (outer or found or [None])[0]
    mesh = ((props.get("LodData") or [{}])[lod] or {}).get("PhysicalMeshData") or {}
    positions = [_xyz(v) for v in mesh.get("Vertices") or []]
    n = len(positions)
    used = props.get("UsedBoneNames") or []
    influences = []
    for b in mesh.get("BoneData") or []:
        k = min(int(b.get("NumInfluences", 0)), 12)
        idx = [b.get("BoneIndices" if i == 0 else "BoneIndices[%d]" % i, 0) for i in range(k)]
        ws = [float(b.get("BoneWeights" if i == 0 else "BoneWeights[%d]" % i, 0.0)) for i in range(k)]
        influences.append([[used[i] if i < len(used) else used[0], w] for i, w in zip(idx, ws)])
    maps = {}
    for entry in mesh.get("WeightMaps") or []:
        values = (entry.get("Value") or {}).get("Values") or []
        name = TARGETS.get(int(entry.get("Key", 0)))
        if name and len(values) == n:
            maps[name] = [float(v) for v in values]
    config = _config((configs.get("ChaosClothConfig") or {}).get("Properties"), CONFIG)
    indices = mesh.get("Indices") or []
    return {"name": asset.get("Name"), "positions": positions, "normals": [_xyz(v) for v in mesh.get("Normals") or []],
            "triangles": [indices[i:i + 3] for i in range(0, len(indices) - 2, 3)], "influences": influences, "maps": maps,
            "reference": _reference(props, parent_of, influences), "config": config,
            "shared": _config((configs.get("ChaosClothSharedSimConfig") or {}).get("Properties"), SHARED),
            "tethers": _tethers(mesh.get("GeodesicTethers" if config["bUseGeodesicDistance"] else "EuclideanTethers")),
            "colliders": _colliders(physics)}


def _field(row, name):
    """A blueprint struct's field by its plain name (the cooked one ends in _<n>_<guid>)."""
    return next((v for k, v in (row or {}).items() if re.sub(SUFFIX, "", k) == name), None)


def _pair(v):
    return [float(v.get("X", 0.0)), float(v.get("Y", 0.0))] if isinstance(v, dict) else None


def _row(table, state):
    rows = {re.sub(SUFFIX, "", k): v for k, v in (table or {}).items()}
    return rows.get(state) or rows.get("OnGround_Standing")


def _settings(row, core):
    """What the cloth controller library pushes to the asset for one state's row and the core settings."""
    lin, ang, fict, gravity = (_field(row, n) for n in ("LinearVelocityScale", "AngularVelocityScale", "FictitiousAngularScale",
                                                         "GravityOverride"))
    out = {"LinearVelocityScale": _xyz(lin) if isinstance(lin, dict) else None,
           "AngularVelocityScale": None if ang is None else float(ang), "FictitiousAngularScale": None if fict is None else float(fict),
           "Drag": _pair(_field(row, "DragCoefficient")), "Lift": _pair(_field(row, "LiftCoefficient")),
           "FluidDensity": 1e-6, "TetherScale": [1.0, 1.0]}
    if isinstance(gravity, dict):       # a row without one leaves the asset's gravity
        out["gravity"], out["joint"] = _xyz(gravity), _field(row, "JointName") or "root"
    for key, name in (("EdgeStiffnessWeighted", "EdgeStiffness"), ("BendingStiffnessWeighted", "BendingStiffness"),
                      ("AreaStiffnessWeighted", "AreaStiffness"), ("TetherStiffness", "TetherStiffness"),
                      ("AnimDriveStiffness", "AnimDriveStiffness"), ("AnimDriveDamping", "AnimDriveDamping")):
        out[key] = _pair(_field(core, name))
    for key, name in (("CollisionThickness", "CollisionThickness"), ("FrictionCoefficient", "FrictionCoefficient"),
                      ("DampingCoefficient", "DampingCoefficient"), ("LocalDampingCoefficient", "LocalDampingCoefficient")):
        v = _field(core, name)
        out[key] = None if v is None else float(v)
    for key, name in (("IterationCount", "NumberIterations"), ("SubdivisionCount", "NumberSubsteps")):
        v = _field(core, name)
        out[key] = None if v is None else int(v)
    return {k: v for k, v in out.items() if v is not None}


def states(path, asset):
    """The asset's settings per movement state as the anim blueprint's cloth controller sets them each tick (with
    cloth on), with the mesh's teleport thresholds; {} when its blueprint sets none."""
    from . import anim_script
    script = anim_script.load(path) if path else None
    if script is None:
        return {}
    runs, out = {}, {}
    for state in anim_script.states():
        given = dict(anim_script.inputs(state), bClothEnabled=True)
        key = json.dumps(given, sort_keys=True)
        if key not in runs:
            runs[key] = script.run(given)
        found = (runs[key].get(anim_script.CLOTH) or {}).get(asset)
        if not found:
            continue
        row = _row(found["table"], state)
        if row is None:
            continue
        out[state] = _settings(row, found["core"])
        for name in anim_script.MESH_SETTINGS:
            if ("__" + name) in runs[key]:
                out[state]["teleport_rotation" if "Rotation" in name else "teleport_distance"] = float(runs[key]["__" + name])
    return out


def read(path, vertices, parent_of):
    """The cloth of the mesh LOD with `vertices` render vertices: {"sections", "clothes"}; None when the file is missing,
    unreadable or has no cloth on such a LOD."""
    try:
        raw = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError, TypeError) as e:
        Log.warn("[Material Porter] cloth: %s unreadable (%s)" % (path, e))
        return None
    mesh_lod = next((i for i, lod in enumerate(raw.get("Lods") or []) if lod.get("NumVertices") == vertices and lod.get("Sections")), None)
    if mesh_lod is None:
        return None
    exports = raw.get("Exports") or []
    physics = raw.get("PhysicsAssets") or []
    clothes = []
    for i, name in enumerate(raw.get("Assets") or []):
        asset = next((e for e in exports if e.get("Type") == "ClothingAssetCommon" and e.get("Name") == name), None)
        lod_map = ((asset or {}).get("Properties") or {}).get("LodMap") or []
        lod = lod_map[mesh_lod] if mesh_lod < len(lod_map) else -1
        clothes.append(_cloth(asset, exports, physics[i] if i < len(physics) else None, lod, parent_of)
                       if asset is not None and lod >= 0 else None)
    sections = [{"base": s["BaseVertexIndex"], "count": s["NumVertices"], "asset": s["Asset"], "mapping": s["Mapping"]}
                for s in raw["Lods"][mesh_lod]["Sections"] if 0 <= s.get("Asset", -1) < len(clothes) and clothes[s["Asset"]]]
    return {"sections": sections, "clothes": clothes} if sections else None
