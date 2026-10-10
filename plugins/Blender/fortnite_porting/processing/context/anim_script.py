"""Runs an anim blueprint's event graph (its Kismet bytecode, as CUE4Parse dumps it) the way the game ticks it, for the
values it binds to the dynamics nodes each frame: a skirt's alpha and simulation space, the alphas of corrections that
only apply crouching, skydiving or wearing a backpack. The character's state comes in as the native inputs (bIsCrouching,
bIsPlayingEmote...); anything from the running game (meshes, montages, other parts) reads as unset."""
import json

STEPS = 30          # update ticks: FInterpTo'd values settle on their target
DT = 1.0 / 30.0

# the native inputs each movement state of FortCharacterDynamicsParameters stands for
_STATES = {
    "OnGround_Standing": {"bIsOnGround": True},
    "OnGround_Moving": {"bIsOnGround": True, "bIsMoving2D": True},
    "OnGround_Moving_HighRotationRate": {"bIsOnGround": True, "bIsMoving2D": True},
    "OnGround_Falling": {"bIsFalling": True},
    "EmoteOrMelee": {"bIsOnGround": True, "bIsPlayingEmote": True},
    "Front_End": {"bIsOnGround": True, "bIsInFrontEnd": True},
    "Swimming": {"bIsSwimming": True},
    "Skydive_FloatingDown": {"bIsSkyDiving": True, "bIsSkydiveFloating": True},
    "Skydive_DivingDown": {"bIsSkyDiving": True, "bIsSkydiveDiveMode": True},
    "Skydive_GoingUp": {"bIsSkyDiving": True},
    "Skydive_Surfing": {"bIsSkyDiving": True},
    "Skydive_Umbrella": {"bIsSkyDiving": True, "bIsParachuteOpened": True},
    "Skydive_Parachuting": {"bIsParachuteOpened": True},
    "DBNO": {"bIsOnGround": True, "bIsDBNO": True},
    "DBNO_Carried": {"bIsDBNO": True},
    "Ziplining": {"bIsZiplining": True},
    "Tactical_Sprint": {"bIsOnGround": True, "bIsMoving2D": True, "bIsTacticalSprinting": True},
    "Riding_Standing": {"bIsRiding": True},
    "Riding_Moving": {"bIsRiding": True, "bIsMoving2D": True},
    "Riding_Moving_Targeting": {"bIsRiding": True, "bIsMoving2D": True, "bIsTargeting": True},
    "Rail_Grinding": {"bIsMoving2D": True},
}


def states():
    return list(_STATES)


def inputs(state):
    if state and state.startswith("DBNO") and state not in _STATES:
        return dict(_STATES["DBNO"])
    return dict(_STATES.get(state) or _STATES["OnGround_Standing"])


def _num(v):
    return float(v) if isinstance(v, (int, float, bool)) else 0.0


def _vec(v):
    return tuple(v) if isinstance(v, (list, tuple)) and len(v) == 3 else (0.0, 0.0, 0.0)


def _interp(cur, target, dt, speed):
    cur, target = _num(cur), _num(target)
    if _num(speed) <= 0.0 or (target - cur) ** 2 < 1e-8:
        return target
    return cur + (target - cur) * min(max(_num(dt) * _num(speed), 0.0), 1.0)


def _map_clamped(v, a, b, c, d):
    v, a, b, c, d = map(_num, (v, a, b, c, d))
    u = 0.0 if a == b else min(max((v - a) / (b - a), 0.0), 1.0)
    return c + (d - c) * u


# pure library functions: name -> callable over evaluated arguments
MATH = {
    "BooleanOR": lambda a, b: bool(a) or bool(b), "BooleanAND": lambda a, b: bool(a) and bool(b),
    "BooleanXOR": lambda a, b: bool(a) != bool(b), "BooleanNOR": lambda a, b: not (bool(a) or bool(b)),
    "BooleanNAND": lambda a, b: not (bool(a) and bool(b)), "Not_PreBool": lambda a: not bool(a),
    "EqualEqual_BoolBool": lambda a, b: bool(a) == bool(b), "NotEqual_BoolBool": lambda a, b: bool(a) != bool(b),
    "Conv_BoolToDouble": lambda a: 1.0 if a else 0.0, "Conv_BoolToFloat": lambda a: 1.0 if a else 0.0,
    "Conv_BoolToInt": lambda a: 1 if a else 0, "Conv_IntToDouble": _num, "Conv_ByteToDouble": _num,
    "SelectFloat": lambda a, b, pick: _num(a) if pick else _num(b), "SelectInt": lambda a, b, pick: a if pick else b,
    "SelectVector": lambda a, b, pick: _vec(a) if pick else _vec(b),
    "FInterpTo": _interp, "MapRangeClamped": _map_clamped,
    "MapRangeUnclamped": lambda v, a, b, c, d: c + (d - c) * ((_num(v) - _num(a)) / (_num(b) - _num(a)) if _num(b) != _num(a) else 0.0),
    "FClamp": lambda v, lo, hi: min(max(_num(v), _num(lo)), _num(hi)), "Lerp": lambda a, b, u: _num(a) + (_num(b) - _num(a)) * _num(u),
    "Add_DoubleDouble": lambda a, b: _num(a) + _num(b), "Subtract_DoubleDouble": lambda a, b: _num(a) - _num(b),
    "Multiply_DoubleDouble": lambda a, b: _num(a) * _num(b),
    "Divide_DoubleDouble": lambda a, b: _num(a) / _num(b) if _num(b) else 0.0,
    "Less_DoubleDouble": lambda a, b: _num(a) < _num(b), "Greater_DoubleDouble": lambda a, b: _num(a) > _num(b),
    "LessEqual_DoubleDouble": lambda a, b: _num(a) <= _num(b), "GreaterEqual_DoubleDouble": lambda a, b: _num(a) >= _num(b),
    "EqualEqual_DoubleDouble": lambda a, b, *_: _num(a) == _num(b), "NotEqual_DoubleDouble": lambda a, b, *_: _num(a) != _num(b),
    "EqualEqual_ByteByte": lambda a, b: _num(a) == _num(b), "NotEqual_ByteByte": lambda a, b: _num(a) != _num(b),
    "EqualEqual_IntInt": lambda a, b: _num(a) == _num(b), "NotEqual_IntInt": lambda a, b: _num(a) != _num(b),
    "Abs": lambda a: abs(_num(a)), "FMin": lambda a, b: min(_num(a), _num(b)), "FMax": lambda a, b: max(_num(a), _num(b)),
    "MakeVector": lambda x, y, z: (_num(x), _num(y), _num(z)),
    "IsValid": lambda o: o is not None,
}
# library functions answering through output parameters: name -> outputs from the evaluated first argument
OUTS = {"BreakVector": lambda v: _vec(v), "BreakRotator": lambda r: _vec(r),
        "BreakTransform": lambda t: (None, None, None)}

_VARS = ("EX_LocalVariable", "EX_LocalOutVariable", "EX_InstanceVariable", "EX_DefaultVariable", "EX_ClassSparseDataVariable")
_LETS = ("EX_Let", "EX_LetBool", "EX_LetObj", "EX_LetWeakObjPtr", "EX_LetDelegate", "EX_LetMulticastDelegate")
_CALLS = ("EX_CallMath", "EX_FinalFunction", "EX_LocalFinalFunction", "EX_VirtualFunction", "EX_LocalVirtualFunction")


def _name(variable):
    if not isinstance(variable, dict):
        return None
    prop = variable.get("Property")
    if isinstance(prop, dict):
        return prop.get("Name")
    path = variable.get("Path")
    return ".".join(path) if path else variable.get("Name")


def _function(e):
    f = e.get("Function")
    if isinstance(f, dict):
        return f.get("ObjectName", "").rstrip("'").split(":")[-1].split("'")[-1]
    return e.get("VirtualFunctionName")


def _default(kind):
    return {"BoolProperty": False, "DoubleProperty": 0.0, "FloatProperty": 0.0, "IntProperty": 0, "ByteProperty": 0,
            "EnumProperty": 0}.get(kind)


class Script:
    """One anim blueprint's event graph; run() ticks it from fresh for a set of native inputs."""

    def __init__(self, raw):
        cls = next((e for e in raw if e.get("Type") == "AnimBlueprintGeneratedClass"), {})
        cdo = next((e for e in raw if str(e.get("Name", "")).startswith("Default__")), {})
        self.types, self.functions, self.params = {}, {}, {}
        for p in cls.get("ChildProperties") or []:
            self.types[p.get("Name")] = p.get("Type")
        # an instance starts from the class defaults: the cooked ones, else the type's
        self.defaults = {n: _default(t) for n, t in self.types.items() if _default(t) is not None}
        self.defaults.update(cdo.get("Properties") or {})
        for e in raw:
            if e.get("Type") == "Function" and e.get("ScriptBytecode"):
                self.functions[e["Name"]] = e["ScriptBytecode"]
                props = e.get("ChildProperties") or []
                for p in props:
                    self.types.setdefault(p.get("Name"), p.get("Type"))
                self.params[e["Name"]] = [(p.get("Name"), "OutParm" in str(p.get("PropertyFlags", ""))) for p in props
                                          if "Parm" in str(p.get("PropertyFlags", "")) and "ReturnParm" not in str(p.get("PropertyFlags", ""))]
        self.offsets = {name: {st.get("StatementIndex"): i for i, st in enumerate(code)} for name, code in self.functions.items()}

    def run(self, given, steps=STEPS):
        """The blueprint's variables after it initialised and ticked `steps` times with the given native inputs."""
        self.vars, self.persistent = dict(self.defaults), {}
        self.vars.update(given)
        for event in ("BlueprintInitializeAnimation", "OnCharacterCustomizationLoaded", "BlueprintBeginPlay"):
            if event in self.functions:
                self._call(event, [])
        for _ in range(steps if "BlueprintUpdateAnimation" in self.functions else 0):
            self._call("BlueprintUpdateAnimation", [], {"DeltaTimeX": DT})
        return self.vars

    def _call(self, name, args, frame=None):
        if name.startswith("ExecuteUbergraph"):
            self.persistent["EntryPoint"] = args[0] if args else 0
            self._exec(name, self.persistent)
            return None
        frame = dict(frame or {})
        for (param, _), value in zip(self.params.get(name, []), args):
            frame.setdefault(param, value)
        for param, _ in self.params.get(name, []):
            frame.setdefault(param, _default(self.types.get(param)))
        self._exec(name, frame)
        return frame

    def _exec(self, name, frame):
        code, offsets, flow, pc = self.functions[name], self.offsets[name], [], 0
        for _ in range(200000):         # a runaway loop ends the tick, never the import
            if pc >= len(code):
                return
            st = code[pc]
            t = st.get("Token")
            if t == "EX_PushExecutionFlow":
                flow.append(st.get("PushingAddress"))
            elif t == "EX_PopExecutionFlow" or (t == "EX_PopExecutionFlowIfNot" and not self._eval(st.get("BooleanExpression"), frame)):
                if not flow:
                    return
                pc = offsets.get(flow.pop(), len(code))
                continue
            elif t == "EX_JumpIfNot":
                if not self._eval(st.get("BooleanExpression"), frame):
                    pc = offsets.get(st.get("CodeOffset"), len(code))
                    continue
            elif t == "EX_Jump":
                pc = offsets.get(st.get("CodeOffset"), len(code))
                continue
            elif t == "EX_ComputedJump":
                pc = offsets.get(int(_num(self._eval(st.get("CodeOffsetExpression"), frame))), len(code))
                continue
            elif t in ("EX_Return", "EX_EndOfScript"):
                return
            elif t != "EX_PopExecutionFlowIfNot":
                self._eval(st, frame)
            pc += 1

    def _read(self, e, frame):
        name = _name(e.get("Variable"))
        store = self.vars if e.get("Token") in ("EX_InstanceVariable", "EX_DefaultVariable", "EX_ClassSparseDataVariable") else frame
        if name in store:
            return store[name]
        if name in self.persistent:
            return self.persistent[name]
        return _default(self.types.get(name))

    def _write(self, target, value, frame):
        if not isinstance(target, dict) or target.get("Token") not in _VARS:
            return
        name = _name(target.get("Variable"))
        (self.vars if target.get("Token") in ("EX_InstanceVariable", "EX_DefaultVariable") else frame)[name] = value

    def _eval(self, e, frame):
        if not isinstance(e, dict):
            return None
        t = e.get("Token")
        if t in _VARS:
            return self._read(e, frame)
        if t in _LETS:
            target = e.get("Variable")
            self._write(target, self._eval(e.get("Expression") or e.get("AssignmentExpression"), frame), frame)
            return None
        if t == "EX_LetValueOnPersistentFrame":
            self.persistent[_name(e.get("DestinationProperty"))] = self._eval(e.get("AssignmentExpression"), frame)
            return None
        if t in _CALLS:
            return self._invoke(e, frame)
        if t in ("EX_Context", "EX_Context_FailSilent", "EX_ClassContext"):
            # calls on other objects (the mesh, the pawn, montages): the game isn't running, they answer unset
            return self._eval(e.get("ContextExpression"), frame) if self._eval(e.get("ObjectExpression"), frame) == "self" else None
        if t in ("EX_True", "EX_False"):
            return t == "EX_True"
        if t == "EX_Self":
            return "self"
        if t in ("EX_NoObject", "EX_Nothing", "EX_StructConst"):
            return None
        if t == "EX_VectorConst":
            return _vec([e.get("Value", {}).get(k, 0.0) for k in "XYZ"] if isinstance(e.get("Value"), dict) else e.get("Value"))
        if t.endswith("Const"):
            return e.get("Value")
        if t in ("EX_PrimitiveCast", "EX_Cast", "EX_DynamicCast", "EX_MetaCast", "EX_ObjToInterfaceCast",
                 "EX_CrossInterfaceCast", "EX_InterfaceToObjCast", "EX_InterfaceContext"):
            value = self._eval(e.get("Target") or e.get("Expression") or e.get("InterfaceValue"), frame)
            return None if t == "EX_DynamicCast" and value == "self" else value
        if t == "EX_StructMemberContext":
            return None
        if t == "EX_SwitchValue":
            index = self._eval(e.get("IndexTerm"), frame)
            for case in e.get("Cases") or []:
                if self._eval(case.get("CaseIndexValueTerm"), frame) == index:
                    return self._eval(case.get("CaseTerm"), frame)
            return self._eval(e.get("DefaultTerm"), frame)
        return None

    def _invoke(self, e, frame):
        name, params = _function(e), e.get("Parameters") or []
        if name in MATH:
            try:
                return MATH[name](*[self._eval(p, frame) for p in params])
            except (TypeError, ValueError, ZeroDivisionError):
                return None
        if name in OUTS:
            outs = OUTS[name](self._eval(params[0], frame) if params else None)
            for target, value in zip(params[1:], outs):
                self._write(target, value, frame)
            return None
        if name in self.functions:
            done = self._call(name, [self._eval(p, frame) for p in params])
            if done is not None:
                for (param, out), target in zip(self.params.get(name, []), params):
                    if out:
                        self._write(target, done.get(param), frame)
                return done.get("ReturnValue")
        return None         # native or engine calls about the running game


def load(path):
    try:
        return Script(json.load(open(path, encoding="utf-8")))
    except (OSError, ValueError):
        return None
