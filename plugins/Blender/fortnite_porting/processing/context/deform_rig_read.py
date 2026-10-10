"""A character's deform Control Rig (`<Char>_CtrlRig`, its dump written by the app): the tuning it passes to Fortnite's
FortDeformRig (part switches, per-part offset/scale values) and the extras it runs after (spherical pose readers
weighting additive transforms, rotation constraints). Spec 2026-10-10-deform-correctives.

The shared logic (bones, blends, drivers) is Epic's library, decoded once into deform_rig.json."""
import json
import os
import re

LIBRARY = os.path.join(os.path.dirname(__file__), "deform_rig.json")
SUFFIX = re.compile(r"^(.*)_\d+_[0-9A-F]{32}$")         # a Blueprint struct member's generated suffix
SCALES = ("PositiveWidth", "NegativeWidth", "PositiveHeight", "NegativeHeight")
SKIP = ("BeginExecution", "Sequence")
_cache = {}


class DeformRigError(Exception):
    """The dump isn't a deform rig this reads."""


def library():
    if "library" not in _cache:
        with open(LIBRARY, encoding="utf-8") as f:
            _cache["library"] = json.load(f)
    return _cache["library"]


def _member(name):
    m = SUFFIX.match(name)
    return m.group(1) if m else name


def _vec(v):
    if v is None:           # an unversioned zero: the literal is all zero
        return [0.0, 0.0, 0.0]
    return [float(v.get(k, 0.0)) for k in "XYZ"] if isinstance(v, dict) else [float(x) for x in v]


def _item(v):
    """A RigElementKey as the reader gets it ("Bone/hand_l" or {"Type", "Name"}): its name, or None."""
    if isinstance(v, dict):
        v = v.get("Name")
    if not isinstance(v, str) or v.endswith("/None") or v in ("None", ""):
        return None
    return v.split("/", 1)[-1]


class _Rig:
    def __init__(self, dump):
        vm = next((e for e in dump if e.get("Type") == "RigVM"), None)
        if vm is None:
            raise DeformRigError("no RigVM")
        lit, work = vm["LiteralMemoryStorage"], vm["DefaultWorkMemoryStorage"]
        self.lit_names = [d["Name"] for d in lit["PropertyDescs"]]
        self.lit_paths = [p["SegmentPath"] for p in lit.get("PropertyPathDescriptions") or []]
        self.lit = lit.get("Values") or {}
        self.work_paths = [p["SegmentPath"] for p in work.get("PropertyPathDescriptions") or []]
        self.functions = vm["FunctionNamesStorage"]
        self.args = {f["Name"]: [a["LocalNameString"] for a in f.get("Arguments") or []]
                     for f in vm["LocalizedRegistry"]["Functions"]}
        bc = vm["ByteCodeStorage"]
        self.code, self.callables = bc["Instructions"], bc["CallableInfos"]
        entries = [e for e in bc["Entries"] if "Forwards Solve" in str(e)]
        if not entries:
            raise DeformRigError("no forward solve")
        self.start = int(re.search(r"InstructionIndex=(\d+)", str(entries[0])).group(1))
        self.state = {}

    def resolve(self, op):
        """An operand's value: a literal, what the work register holds, or 1.0 for a rig variable (its default)."""
        if op is None:
            return None
        kind, index, offset = op["MemoryType"], op["RegisterIndex"], op["RegisterOffset"]
        if kind == 1:
            value = self.lit.get(self.lit_names[index])
            if offset != 65535 and offset < len(self.lit_paths) and isinstance(value, dict):
                value = value.get(self.lit_paths[offset], value)
            return value
        if kind == 0:
            seg = self.work_paths[offset] if offset != 65535 and offset < len(self.work_paths) else None
            return self.state.get((index, seg), self.state.get((index, None)))
        return 1.0

    def target(self, op):
        offset = op["RegisterOffset"]
        seg = self.work_paths[offset] if offset != 65535 and offset < len(self.work_paths) else None
        return op["RegisterIndex"], seg

    def named(self, x, names):
        return {n: x["Arguments"][k] for k, n in enumerate(names) if k < len(x["Arguments"])}


def _tuning(bools, values):
    switches = {_member(k): bool(v) for k, v in (bools or {}).items()}
    out = {}
    for k, v in (values or {}).items():
        if isinstance(v, dict) and "Translation" in v:
            out[_member(k)] = {"t": _vec(v["Translation"]), "s": _vec(v.get("Scale3D") or {"X": 1, "Y": 1, "Z": 1})}
    return switches, out


def _spr(rig, a):
    def get(name):
        return rig.resolve(a.get(name))

    def factors(v):
        return [float((v or {}).get(k, 1.0)) for k in SCALES]
    return {"type": "spr", "driver": _item(get("DriverItem")), "parent": _item(get("OptionalParentItem")),
            "axis": _vec(get("DriverAxis") or {"X": 1}), "offset": _vec(get("RotationOffset") or {}),
            "region": float(get("ActiveRegionSize") or 0.1), "region_scale": factors(get("ActiveRegionScaleFactors")),
            "falloff": float(get("FalloffSize") or 0.2), "falloff_scale": factors(get("FalloffRegionScaleFactors")),
            "flip_width": bool(get("FlipWidthScaling")), "flip_height": bool(get("FlipHeightScaling"))}


def read(path):
    """{"switches": {part: bool}, "values": {field: {"t", "s"}}, "extras": [op...]} from a deform rig dump."""
    with open(path, encoding="utf-8") as f:
        rig = _Rig(json.load(f))
    switches, values, extras, called = {}, {}, [], False
    for x in rig.code[rig.start:]:
        op = x.get("OpCode")
        if op == 80:
            break
        if op == 104:
            c = rig.callables[x["CallableIndex"]]
            if c["Name"].endswith("FN_DeformRig_FNC"):
                a = rig.named(x, [arg["Name"] for arg in c["Arguments"]])
                switches, values = _tuning(rig.resolve(a.get("DeformRig_Bool")), rig.resolve(a.get("DeformRig_Value")))
                called = True
            continue
        if op == 68:
            value = rig.resolve(x["Source"])
            index, seg = rig.target(x["Target"])
            if seg and seg.endswith("Transform.Rotation") and isinstance(rig.state.get((index, None)), list):
                entry = rig.state[(index, None)][int(seg.split(".")[0])]
                entry["Transform"] = dict(entry.get("Transform") or {}, Rotation=value)
            else:
                rig.state[(index, seg)] = json.loads(json.dumps(value)) if isinstance(value, (list, dict)) else value
            continue
        if op != 101:
            continue
        name = rig.functions[x["FunctionIndex"]].split("::")[0].replace("FRigUnit_", "").replace("FRigVMFunction_", "")
        a = rig.named(x, rig.args.get(rig.functions[x["FunctionIndex"]], []))
        if name == "FortDeformRig":
            switches, values = _tuning(rig.resolve(a.get("Bool")), rig.resolve(a.get("Value")))
            called = True
        elif not called or name in SKIP:
            continue
        elif name == "SphericalPoseReader":
            rig.state[rig.target(a["OutputParam"])] = _spr(rig, a)
        elif name == "MathQuaternionFromEuler":
            rig.state[rig.target(a["Result"])] = {"euler": _vec(rig.resolve(a["Euler"])), "order": int(rig.resolve(a["RotationOrder"]) or 0)}
        elif name in ("MathDoubleMul", "MathFloatMul"):
            values_ab = [rig.resolve(a.get("A")), rig.resolve(a.get("B"))]
            weight = next((v for v in values_ab if isinstance(v, dict)), None)
            factor = 1.0
            for v in values_ab:
                factor *= v if isinstance(v, (int, float)) else 1.0
            rig.state[rig.target(a["Result"])] = factor if weight is None else dict(weight, scale=factor * weight.get("scale", 1.0))
        elif name == "ModifyTransforms":
            mode = rig.resolve(a.get("Mode"))
            if mode is not None and str(mode).split("::")[-1] not in ("2", "AdditiveLocal"):
                print("[Deform rig] extras stop at a %s modify (not read)" % mode)
                break
            weight = rig.resolve(a.get("Weight"))
            for entry in rig.resolve(a["ItemToModify"]) or []:
                t = entry.get("Transform") or {}
                rotation = t.get("Rotation")
                extras.append({"op": "modify", "bone": _item(entry.get("Item")), "t": _vec(t.get("Translation") or {}),
                               "s": _vec(t.get("Scale3D") or {"X": 1, "Y": 1, "Z": 1}),
                               "euler": rotation.get("euler") if isinstance(rotation, dict) and "euler" in rotation else None,
                               "order": rotation.get("order") if isinstance(rotation, dict) and "euler" in rotation else None,
                               "weight": weight if isinstance(weight, dict) else {"type": "const", "value": float(weight or 1.0)}})
        elif name in ("RotationConstraintLocalSpaceOffset", "RotationConstraint"):
            if a.get("bMaintainOffset") is not None and not rig.resolve(a.get("bMaintainOffset")):
                print("[Deform rig] extras stop at a rotation constraint without offset (not read)")
                break
            parents = rig.resolve(a.get("Parents")) or []
            extras.append({"op": "rotation_constraint", "bone": _item(rig.resolve(a.get("Child"))),
                           "parents": [{"bone": _item(p.get("Item")), "weight": float(p.get("Weight", 1.0))} for p in parents
                                       if isinstance(p, dict)], "maintain_offset": bool(rig.resolve(a.get("bMaintainOffset")))})
        else:
            print("[Deform rig] extras stop at %s (not read)" % name)
            break
    if not called:
        raise DeformRigError("no FortDeformRig call")
    return {"switches": switches, "values": values, "extras": extras}


def tuning(paths):
    """The character's tuning over Epic's library defaults, from the first dump (a path or several: one per part with a
    Control Rig) that calls FortDeformRig; the library alone when none does."""
    lib = library()
    out = {"switches": dict(lib["switches"]), "values": {k: dict(v) for k, v in lib["values"].items()}, "extras": []}
    got = None
    for path in [paths] if isinstance(paths, str) else paths or []:
        try:
            got = read(path)
            break
        except Exception as e:         # any dump this doesn't read falls back, never fails the import
            print("[Deform rig] %s: not read (%s: %s)" % (os.path.basename(path), type(e).__name__, e))
    if got is None:
        return out
    out["switches"].update(got["switches"])
    out["values"].update(got["values"])
    out["extras"] = got["extras"]
    return out
