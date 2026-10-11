"""A character's deform Control Rig (`<Char>_CtrlRig`, its dump written by the app): the tuning it passes to Fortnite's
FortDeformRig (part switches, per-part offset/scale values) and the extras it runs after (spherical pose readers
weighting additive transforms, rotation constraints). Spec 2026-10-10-deform-correctives.

The shared logic (bones, blends, drivers) is Epic's library, decoded once into deform_rig.json."""
import json
import os
import re

from .rig_vm import entry_index

LIBRARY = os.path.join(os.path.dirname(__file__), "deform_rig.json")
SUFFIX = re.compile(r"^(.*)_\d+_[0-9A-F]{32}$")         # a Blueprint struct member's generated suffix
SCALES = ("PositiveWidth", "NegativeWidth", "PositiveHeight", "NegativeHeight")
SKIP = ("BeginExecution", "Sequence", "SetCurveValue")      # SetCurveValue: a curve for the anim graph, no bone moves
MOVES = ("ModifyTransforms", "OffsetTransformForItem", "RotationConstraintLocalSpaceOffset", "RotationConstraint",
         "ParentConstraint", "AimItem", "AimConstraintLocalSpaceOffset")     # units that move bones: after the library call only
ATTRIBUTE = ("DISPATCH_RigDispatch_GetAnimAttribute", "DISPATCH_RigDispatch_SetAnimAttribute")
OPERANDS = {"Add": ("A", "B"), "Sub": ("A", "B"), "Div": ("A", "B"), "Clamp": ("Value", "Minimum", "Maximum"),
            "Remap": ("Value", "SourceMinimum", "SourceMaximum", "TargetMinimum", "TargetMaximum", "bClamp"),
            "Pow": ("A", "B"), "BoolNot": ("Value",), "BoolAnd": ("A", "B"), "BoolOr": ("A", "B"), "BoolToFloat": ("Value",)}
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
        self.branches = bc.get("BranchInfos") or []
        entries = [e for e in bc["Entries"] if "Forwards Solve" in str(e)]
        if not entries:
            raise DeformRigError("no forward solve")
        self.start = entry_index(entries[0])
        self.state, self.attributes = {}, {}
        # the rig's bool variables by memory index (its movement states: DBNO, Crouch, Skydive...): their defaults,
        # false unless the class stores one, i.e. standing still
        cls = next((e for e in dump if e.get("Type") == "ControlRigBlueprintGeneratedClass"), {})
        cdo = next((e for e in dump if str(e.get("Name", "")).startswith("Default__")), {}).get("Properties") or {}
        self.bools = {k: bool(cdo.get(p.get("Name"), False)) for k, p in enumerate(cls.get("ChildProperties") or [])
                      if p.get("Type") == "BoolProperty"}
        # number variables the game drives (blink, jumping...): their defaults, zero unless stored, i.e. at rest
        self.numbers = {k: float(cdo.get(p.get("Name")) or 0.0) for k, p in enumerate(cls.get("ChildProperties") or [])
                        if p.get("Type") in ("DoubleProperty", "FloatProperty")}

    def resolve(self, op):
        """An operand's value: a literal, what the work register holds, a rig variable the solve wrote or its default
        (bools and numbers), or 1.0 for another rig variable."""
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
        if kind == 2 and (("X", index), None) in self.state:
            return self.state[(("X", index), None)]
        if kind == 2 and index in self.bools:
            return self.bools[index]
        if kind == 2 and index in self.numbers:
            return self.numbers[index]
        return 1.0

    def target(self, op):
        if op["MemoryType"] == 2:           # a rig variable the solve writes
            return ("X", op["RegisterIndex"]), None
        offset = op["RegisterOffset"]
        seg = self.work_paths[offset] if offset != 65535 and offset < len(self.work_paths) else None
        return op["RegisterIndex"], seg

    def named(self, x, names):
        return {n: x["Arguments"][k] for k, n in enumerate(names) if k < len(x["Arguments"])}

    def call(self, x):
        """An Execute's unit name and its operands by argument name (a dispatch names them in its signature only)."""
        function = self.functions[x["FunctionIndex"]]
        names = self.args.get(function)
        if names is None:
            signature = function.split("::", 1)[1] if "::" in function else ""
            names = [p.split(":")[0] for p in signature.split(",")] if ":" in signature else []
        name = function.split("::")[0].replace("FRigUnit_", "").replace("FRigVMFunction_", "")
        return name, self.named(x, names)

    def copy(self, x):
        value = self.resolve(x["Source"])
        if value is None and x["Source"]["MemoryType"] == 0:
            return          # a register nothing wrote: its target keeps what it holds (a variable its default)
        index, seg = self.target(x["Target"])
        if seg and seg.endswith("Transform.Rotation") and isinstance(self.state.get((index, None)), list):
            entry = self.state[(index, None)][int(seg.split(".")[0])]
            entry["Transform"] = dict(entry.get("Transform") or {}, Rotation=value)
        else:
            self.state[(index, seg)] = json.loads(json.dumps(value)) if isinstance(value, (list, dict)) else value


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


def _library(rig):
    """The pose weights Epic's library publishes as anim attributes (deformRig_l_thigh_up_alpha...), for a character's
    own graph reading them back: its pose readers and copies, run over the byte code before the forward solve."""
    for x in rig.code[:rig.start]:
        if x.get("OpCode") == 68:
            rig.copy(x)
        elif x.get("OpCode") == 101:
            name, a = rig.call(x)
            if name == "SphericalPoseReader" and a.get("OutputParam") is not None:
                rig.state[rig.target(a["OutputParam"])] = _spr(rig, a)
            elif name == ATTRIBUTE[1]:
                value = rig.resolve(a.get("Value"))
                if isinstance(value, dict) and value.get("driver"):
                    rig.attributes[rig.resolve(a.get("Name"))] = value
    rig.state = {}


def _number(v):
    if v is None:           # a blank literal: zero
        return 0.0
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    raise TypeError("a value from the pose")


def _flag(v):
    if v is None or isinstance(v, bool):
        return bool(v)
    raise TypeError("a value from the pose")


def _step(v, a, b, lo=None, hi=None, power=None):
    """(v ** power) * a + b, clamped to [lo, hi]: on a number its value, on a pose weight one more step after it."""
    if isinstance(v, dict):
        return dict(v, post=list(v.get("post") or []) + [[a, b, lo, hi] + ([power] if power is not None else [])])
    v = (_number(v) ** power if power is not None else _number(v)) * a + b
    return v if lo is None else min(max(v, lo), hi)


def _math(op, v):
    """A math unit (OPERANDS) on numbers, bools and at most one pose weight, as UE computes it; TypeError otherwise."""
    if any(isinstance(x, dict) and x.get("type") == "position" for x in v):
        raise TypeError("a bone's place, not a weight")
    if op.startswith("Bool"):
        return {"BoolNot": lambda: not _flag(v[0]), "BoolAnd": lambda: _flag(v[0]) and _flag(v[1]),
                "BoolOr": lambda: _flag(v[0]) or _flag(v[1]), "BoolToFloat": lambda: float(_flag(v[0]))}[op]()
    if op == "Clamp":
        return _step(v[0], 1.0, 0.0, _number(v[1]), _number(v[2]))
    if op == "Remap":
        s0, s1, t0, t1 = (_number(x) for x in v[1:5])
        a = 0.0 if abs(s1 - s0) < 1e-8 else 1.0 / (s1 - s0)     # equal source ends: the ratio is 0
        ratio = _step(v[0], a, -s0 * a, *((0.0, 1.0) if v[5] else (None, None)))
        return _step(ratio, t1 - t0, t0)
    if op == "Add" and all(isinstance(x, dict) for x in v):       # two pose weights added: driven as their sum
        terms = [t for x in v for t in (x["terms"] if x.get("type") == "sum" and not x.get("post") else [x])]
        return {"type": "sum", "terms": terms}
    if sum(isinstance(x, dict) for x in v) > 1 or (op == "Div" and isinstance(v[1], dict)):
        raise TypeError("a value from the pose")
    if op == "Div":
        b = _number(v[1])
        return 0.0 if abs(b) < 1e-8 else _step(v[0], 1.0 / b, 0.0)
    if op == "Pow":
        if isinstance(v[1], dict):
            raise TypeError("a value from the pose")
        return _step(v[0], 1.0, 0.0, power=_number(v[1]))
    if op == "Add":
        w, c = (v[1], v[0]) if isinstance(v[1], dict) else (v[0], v[1])
        return _step(w, 1.0, _number(c))
    return _step(v[1], -1.0, _number(v[0])) if isinstance(v[1], dict) else _step(v[0], 1.0, -_number(v[1]))


def _modify(item, transform, weight):
    """An additive local change to a bone, weighted by a constant or a pose weight."""
    rotation = transform.get("Rotation")
    euler = isinstance(rotation, dict) and "euler" in rotation
    return {"op": "modify", "bone": _item(item), "t": _vec(transform.get("Translation") or {}),
            "s": _vec(transform.get("Scale3D") or {"X": 1, "Y": 1, "Z": 1}),
            "euler": rotation.get("euler") if euler else None, "order": rotation.get("order") if euler else None,
            "weight": weight if isinstance(weight, dict) else {"type": "const", "value": 1.0 if weight is None else float(weight)}}


def _point(v):
    """An aim target given as a location in a bone's space ({Target, Kind, Space}): {bone, offset}, None otherwise."""
    if not isinstance(v, dict) or str(v.get("Kind", "")).split("::")[-1] != "Location":
        return None
    return {"bone": _item(v.get("Space")), "offset": _vec(v.get("Target"))}


def _aim(rig, name, a):
    """An aim (AimItem, AimConstraintLocalSpaceOffset) as an extra; None when it isn't one this reads (a direction
    target, a partial aim, several parents, some axes only)."""
    weight = rig.resolve(a.get("Weight"))
    if not isinstance(weight, dict):
        weight = {"type": "const", "value": 1.0 if weight is None else float(weight)}
    if name == "AimItem":
        primary, secondary = rig.resolve(a.get("Primary")) or {}, rig.resolve(a.get("Secondary")) or {}
        target, up = _point(primary), _point(secondary) if float(secondary.get("Weight", 0.0) or 0.0) > 0.0 else None
        if target is None or abs(float(primary.get("Weight", 1.0) or 0.0) - 1.0) > 1e-6:
            return None
        return {"op": "aim", "bone": _item(rig.resolve(a.get("Item"))), "aim": _vec(primary.get("Axis")), "target": target,
                "up_axis": _vec(secondary.get("Axis")) if up else None, "up": up, "keep_offset": False, "weight": weight}
    parents = [p for p in rig.resolve(a.get("Parents")) or [] if isinstance(p, dict) and float(p.get("Weight", 1.0)) > 0.0]
    filt = rig.resolve(a.get("Filter")) or {}
    if len(parents) != 1 or not all(filt.get(b, True) for b in ("bX", "bY", "bZ")):
        return None
    up = _point(rig.resolve(a.get("WorldUp")))
    return {"op": "aim", "bone": _item(rig.resolve(a.get("Child"))), "aim": _vec(rig.resolve(a.get("AimAxis"))),
            "target": {"bone": _item(parents[0].get("Item")), "offset": [0.0, 0.0, 0.0]},
            "up_axis": _vec(rig.resolve(a.get("UpAxis"))) if up else None, "up": up,
            "keep_offset": bool(rig.resolve(a.get("bMaintainOffset"))), "weight": weight}


def _untaken(rig, pc, condition):
    """The instruction range of the side a ControlFlowBranch doesn't run: by its condition when known; else (a value
    from the game's native units) its False side when that does nothing, else None."""
    jump = rig.code[pc + 1] if pc + 1 < len(rig.code) else {}
    sides = {b["Label"]: (b["FirstInstruction"], b["LastInstruction"]) for b in rig.branches
             if b.get("InstructionIndex") == pc + 1}
    if jump.get("OpCode") != 100 or "True" not in sides or "False" not in sides:
        return None
    if isinstance(condition, bool):
        return sides["False"] if condition else sides["True"]
    first, last = sides["False"]
    return None if any(rig.code[k].get("OpCode") == 101 for k in range(first, last + 1)) else sides["False"]


def _interp(rig, a):
    """AlphaInterp settled (no time to interpolate over): the range mapped, scaled, biased and clamped, as UE's
    FInputScaleBiasClamp; a reader's weight gets it as a step after it. TypeError when a setting comes from the pose."""
    def get(name):
        return rig.resolve(a.get(name))
    slope, offset = 1.0, 0.0
    if get("bMapRange"):
        rin, rout = get("InRange") or {}, get("OutRange") or {}
        lo_in, hi_in = float(rin.get("Min", 0.0)), float(rin.get("Max", 1.0))
        lo_out, hi_out = float(rout.get("Min", 0.0)), float(rout.get("Max", 1.0))
        slope = (hi_out - lo_out) / (hi_in - lo_in) if hi_in != lo_in else 0.0
        offset = lo_out - lo_in * slope
    scale, bias = float(get("Scale") or 0.0), float(get("Bias") or 0.0)
    step = [slope * scale, offset * scale + bias] + ([float(get("ClampMin") or 0.0), float(get("ClampMax") or 0.0)]
                                                     if get("bClampResult") else [None, None])
    value = get("Value")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        v = value * step[0] + step[1]
        return v if step[2] is None else min(max(v, step[2]), step[3])
    if isinstance(value, dict):
        return dict(value, post=list(value.get("post") or []) + [step])
    return None


def read(path):
    """{"switches": {part: bool}, "values": {field: {"t", "s"}}, "extras": [op...]} from a deform rig dump."""
    with open(path, encoding="utf-8") as f:
        rig = _Rig(json.load(f))
    _library(rig)
    switches, values, extras, called, skip = {}, {}, [], False, []
    for pc in range(rig.start, len(rig.code)):
        if any(first <= pc <= last for first, last in skip):
            continue        # a branch's side not taken
        x = rig.code[pc]
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
            rig.copy(x)
            continue
        if op != 101:
            continue
        name, a = rig.call(x)
        unit = re.sub(r"^Math(Double|Float)?", "", name)
        why = "[Deform rig] extras stop at %s (not read)" % name
        for _once in (None,):       # a unit that stops the extras breaks out here
            if name == "FortDeformRig":
                switches, values = _tuning(rig.resolve(a.get("Bool")), rig.resolve(a.get("Value")))
                called = True
                for flag in ("bInitialized", "bContinueExecution"):       # what it says once the game has run it a frame
                    if a.get(flag) is not None:
                        rig.state[rig.target(a[flag])] = True
            elif name in SKIP or (not called and name in MOVES):
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
                    why = ("[Deform rig] extras stop at a %s modify (not read)" % mode)
                    break
                weight = rig.resolve(a.get("Weight"))
                for entry in rig.resolve(a["ItemToModify"]) or []:
                    extras.append(_modify(entry.get("Item"), entry.get("Transform") or {}, weight))
            elif name in ("AimItem", "AimConstraintLocalSpaceOffset"):
                aim = _aim(rig, name, a)
                if aim is None:
                    why = ("[Deform rig] extras stop at an aim this doesn't read (a direction, part way, several parents or some axes)")
                    break
                if aim["weight"]["type"] != "const" or aim["weight"]["value"] > 0.0:      # switched off: no effect
                    extras.append(aim)
            elif name == "OffsetTransformForItem":
                # UE applies the offset in the item's own space, blended by Weight: an additive local modify
                extras.append(_modify(rig.resolve(a.get("Item")), rig.resolve(a.get("OffsetTransform")) or {}, rig.resolve(a.get("Weight"))))
            elif unit in OPERANDS and "Result" in a:
                try:
                    rig.state[rig.target(a["Result"])] = _math(unit, [rig.resolve(a.get(k)) for k in OPERANDS[unit]])
                except TypeError:
                    why = ("[Deform rig] extras stop at %s on values from the pose (not read)" % name)
                    break
            elif name == "GetTransform":
                # a bone's place in the world, read for a distance; any other use of it is the pose's, not read
                if int(_number(rig.resolve(a.get("Space")))) != 1 or rig.resolve(a.get("bInitial")):
                    why = ("[Deform rig] extras stop at a bone's local or initial transform (not read)")
                    break
                rig.state[rig.target(a["Transform"])] = {"type": "position", "bone": _item(rig.resolve(a.get("Item")))}
            elif name == "MathVectorDistance":
                ends = [rig.resolve(a.get(k)) for k in ("A", "B")]
                if not all(isinstance(e, dict) and e.get("type") == "position" for e in ends):
                    why = ("[Deform rig] extras stop at a distance to a point that isn't a bone (not read)")
                    break
                rig.state[rig.target(a["Result"])] = {"type": "distance", "a": ends[0]["bone"], "b": ends[1]["bone"]}
            elif name == "KalmanFloat":
                rig.state[rig.target(a["Result"])] = rig.resolve(a.get("Value"))      # settled: the average of one value
            elif name == ATTRIBUTE[0]:
                published = rig.attributes.get(rig.resolve(a.get("Name")))
                if published is None:
                    why = ("[Deform rig] extras stop at an anim attribute the game's animation sets (not read)")
                    break
                rig.state[rig.target(a["Value"])] = published
                rig.state[rig.target(a["Found"])] = True
            elif name in ("RotationConstraintLocalSpaceOffset", "RotationConstraint"):
                if a.get("bMaintainOffset") is not None and not rig.resolve(a.get("bMaintainOffset")):
                    why = ("[Deform rig] extras stop at a rotation constraint without offset (not read)")
                    break
                parents = rig.resolve(a.get("Parents")) or []
                extras.append({"op": "rotation_constraint", "bone": _item(rig.resolve(a.get("Child"))),
                               "parents": [{"bone": _item(p.get("Item")), "weight": float(p.get("Weight", 1.0))} for p in parents
                                           if isinstance(p, dict)], "maintain_offset": bool(rig.resolve(a.get("bMaintainOffset")))})
            elif name == "ControlFlowBranch":
                other = _untaken(rig, pc, rig.resolve(a.get("Condition")))
                if other is None:
                    why = ("[Deform rig] extras stop at a branch on a value not read, with work on both sides (not read)")
                    break
                skip.append(other)
            elif name == "AlphaInterp":
                try:
                    rig.state[rig.target(a["Result"])] = _interp(rig, a)
                except TypeError:
                    why = ("[Deform rig] extras stop at an alpha interp set by the pose (not read)")
                    break
            elif name == "ParentConstraint":
                # the parents' weighted mix with each one's offset kept, filtered to whole channels, blended in by Weight
                if not rig.resolve(a.get("bMaintainOffset")):
                    why = ("[Deform rig] extras stop at a parent constraint without offset (not read)")
                    break
                filt = rig.resolve(a.get("Filter")) or {}
                axes = [[bool((filt.get(k) or {}).get(b, True)) for b in ("bX", "bY", "bZ")]
                        for k in ("TranslationFilter", "RotationFilter", "ScaleFilter")]
                if any(len(set(x)) > 1 for x in axes):
                    why = ("[Deform rig] extras stop at a parent constraint on some axes only (not read)")
                    break
                parents = rig.resolve(a.get("Parents")) or []
                weight = rig.resolve(a.get("Weight"))       # a constant, or a pose weight blending it in
                if not isinstance(weight, dict):
                    weight = float(weight or 0.0)     # a blank literal is zero: no effect
                    if weight <= 0.0:
                        continue
                extras.append({"op": "parent_constraint", "bone": _item(rig.resolve(a.get("Child"))),
                               "parents": [{"bone": _item(p.get("Item")), "weight": float(p.get("Weight", 1.0))} for p in parents
                                           if isinstance(p, dict)],
                               "weight": weight, "copy": [c for c, x in zip(("translation", "rotation", "scale"), axes) if x[0]]})
            else:
                why = ("[Deform rig] extras stop at %s (not read)" % name)
                break
        else:
            continue
        if called:
            print(why)
            break
        # before the library call only values are read (its pose readers feed the later units): nothing stops there
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
