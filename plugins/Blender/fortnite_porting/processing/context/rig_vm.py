"""A character Control Rig's own functions run on the dynamics' pose: the cooked RigVM program (a CUE4Parse dump of
`<Char>_CtrlRig`) compiled to plain data at import, then executed each tick before the AnimDynamics nodes, as the
anim graph orders them. The shared deform function stays with deform_rig (drivers); this covers what characters add
after it, like a skirt kept off the legs. Units: rig_units."""
import copy
import json

# opcodes of ERigVMOpCode used by cooked rigs
ZERO, FALSE, TRUE, COPY = 65, 66, 67, 68
JUMP_ABS, JUMP_FWD, JUMP_BACK, JUMP_ABS_IF, JUMP_FWD_IF, JUMP_BACK_IF = 73, 74, 75, 76, 77, 78
EXIT, BEGIN_BLOCK, END_BLOCK, JUMP_TO_BRANCH, EXECUTE, RUN, CALL = 80, 81, 82, 100, 101, 102, 104
SKIP = ("FN_DeformRig_FNC", "ControlDebug")      # deform_rig builds the first; the second only draws
NONE_OFFSET = 65535
WRITERS = ("ModifyTransforms", "SetTransform", "ParentConstraint", "PositionConstraintLocalSpaceOffset",
           "RotationConstraintLocalSpaceOffset", "PositionConstraint", "RotationConstraint", "ScaleConstraint")


class RigVMError(Exception):
    """The dump isn't a rig program this runs."""


def _op(o):
    if not o:
        return None
    off = o["RegisterOffset"]
    return [o["MemoryType"], o["RegisterIndex"], -1 if off == NONE_OFFSET else off]


def _short(function):
    return function.split("::")[0].replace("FRigUnit_", "").replace("FRigVMFunction_", "")


def _strings(value, out):
    """Element names a literal mentions ("Bone/x" strings or {"Type", "Name"} keys)."""
    if isinstance(value, str) and "/" in value:
        kind, name = value.split("/", 1)
        if kind in ("Bone", "Null") and name != "None":
            out.add((kind, name))
    elif isinstance(value, dict):
        if "Name" in value and "Type" in value and isinstance(value["Name"], str):
            kind = str(value["Type"]).split("::")[-1]
            if value["Name"] != "None":
                out.add(("Null" if kind in ("Null", "2") else "Bone", value["Name"]))
        for v in value.values():
            _strings(v, out)
    elif isinstance(value, list):
        for v in value:
            _strings(v, out)


def compile_rig(dump):
    """The program from a rig dump: instructions from its forward solve (skipping SKIP's callables), the literals and
    work defaults they use, its variables in memory order, and the elements it names."""
    vm = next((e for e in dump if e.get("Type") == "RigVM"), None)
    cls = next((e for e in dump if e.get("Type") == "ControlRigBlueprintGeneratedClass"), None)
    if vm is None:
        raise RigVMError("no RigVM")
    bc = vm["ByteCodeStorage"]
    entry = next((e for e in bc["Entries"] if "Forwards Solve" in str(e)), None)
    if entry is None:
        raise RigVMError("no forward solve")
    start = int(str(entry).split("InstructionIndex=")[1].split(")")[0]) if isinstance(entry, str) else entry["InstructionIndex"]
    lit, work = vm["LiteralMemoryStorage"], vm["DefaultWorkMemoryStorage"]
    lit_names = [d["Name"] for d in lit["PropertyDescs"]]
    work_names = [d["Name"] for d in work["PropertyDescs"]]
    names = vm["FunctionNamesStorage"]
    args = {f["Name"]: [a["LocalNameString"] for a in f.get("Arguments") or []] for f in vm["LocalizedRegistry"]["Functions"]}
    callables = []
    for c in bc["CallableInfos"]:
        callables.append({"name": c["Name"].rsplit(":", 1)[-1], "first": c["FirstInstruction"], "last": c["LastInstruction"],
                          "args": [{"name": a["Name"], "fwd": _op(a.get("ForwardedOperand")), "dir": a.get("Direction", 0)}
                                   for a in c.get("Arguments") or []]})
    skipped = {i for i, c in enumerate(callables) if any(c["name"].endswith(s) for s in SKIP)}
    code, units = [], {}
    for x in bc["Instructions"]:
        op = x["OpCode"]
        item = {"o": op}
        if op == EXECUTE or op <= 64:
            f = names[x["FunctionIndex"]]
            short = _short(f)
            item.update(o=EXECUTE, f=short, a=[_op(a) for a in x["Arguments"]])
            units[short] = args.get(f) or units.get(short) or []
        elif op == CALL:
            item.update(c=x["CallableIndex"], a=[_op(a) for a in x["Arguments"]], skip=x["CallableIndex"] in skipped)
        elif op == COPY:
            item.update(s=_op(x["Source"]), t=_op(x["Target"]))
        elif op in (ZERO, FALSE, TRUE):
            item.update(t=_op(x["Arg"]))
        elif op in (JUMP_ABS, JUMP_FWD, JUMP_BACK):
            item.update(i=x["InstructionIndex"])
        elif op in (JUMP_ABS_IF, JUMP_FWD_IF, JUMP_BACK_IF):
            item.update(i=x["InstructionIndex"], arg=_op(x.get("Arg")), cond=bool(x.get("Condition")))
        elif op == JUMP_TO_BRANCH:
            item.update(arg=_op(x["Arg"]), branches={b["Label"]: b["FirstInstruction"] for b in bc["BranchInfos"]
                                                     if b["InstructionIndex"] == len(code)})
        elif op == RUN:
            item.update(arg=_op(x["Arg"]), first=x["StartInstruction"], last=x["EndInstruction"])
        code.append(item)
    # what the kept code reads: literals by register, work defaults by register
    used_lit, used_work = set(), set()

    def mark(o):
        if o:
            (used_lit if o[0] == 1 else used_work if o[0] == 0 else set()).add(o[1])
    # the instructions that run: the solve and the callables it reaches, skipped ones aside
    live, todo, seen = set(), [(start, len(code) - 1)], set()
    in_callable = {i for c in callables for i in range(c["first"], c["last"] + 1)}
    while todo:
        first, last = todo.pop()
        for i in range(first, last + 1):
            if i in live or (i in in_callable and (first, last) == (start, len(code) - 1)):
                continue
            live.add(i)
            if code[i]["o"] == CALL and not code[i]["skip"] and code[i]["c"] not in seen:
                seen.add(code[i]["c"])
                c = callables[code[i]["c"]]
                todo.append((c["first"], c["last"]))
    for i, item in enumerate(code):
        if i not in live:
            if i not in in_callable or not any(c["first"] <= i <= c["last"] for k, c in enumerate(callables) if k in seen):
                code[i] = {"o": item["o"] if item["o"] in (EXIT,) else 0}
            continue
        for key in ("a", "s", "t", "arg"):
            v = item.get(key)
            for o in ((v or []) if key == "a" else [v]):
                mark(o)
    for k in seen:
        for a in callables[k]["args"]:
            mark(a["fwd"])
    lit_values, work_values = lit.get("Values") or {}, work.get("Values") or {}
    literals = {str(i): lit_values.get(lit_names[i]) for i in sorted(used_lit) if i < len(lit_names)}
    defaults = {str(i): work_values.get(work_names[i]) for i in sorted(used_work)
                if i < len(work_names) and work_values.get(work_names[i]) is not None}
    elements, writes = set(), set()
    for v in literals.values():
        _strings(v, elements)
    for i in live:          # the bones its transform-setting units name
        x = code[i]
        if x["o"] == EXECUTE and x["f"] in WRITERS:
            named = dict(zip(units.get(x["f"]) or [], x["a"]))
            mode = named.get("Mode")
            additive = mode is not None and mode[0] == 1 and str(lit_values.get(lit_names[mode[1]])).split("::")[-1] in ("2", "3", "AdditiveLocal", "AdditiveGlobal")
            for name, o in named.items():
                if name in ("ItemToModify", "Item", "Child") and o and o[0] == 1:
                    value = lit_values.get(lit_names[o[1]])
                    if additive and isinstance(value, list):        # an additive identity changes nothing
                        value = [e for e in value if not identity(e.get("Transform"))]
                    _strings(value, writes)
    hierarchy = next((e for e in dump if e.get("Type") == "RigHierarchy"), {})
    nulls = sorted({(e.get("LoadedKey") or {}).get("Name") for e in hierarchy.get("Elements") or []
                    if (e.get("LoadedKey") or {}).get("Type") == 2})
    return {"start": start, "code": code, "callables": callables, "units": units, "literals": literals, "defaults": defaults,
            "lit_paths": [p["SegmentPath"] for p in lit.get("PropertyPathDescriptions") or []],
            "work_paths": [p["SegmentPath"] for p in work.get("PropertyPathDescriptions") or []],
            "ext_paths": [p["SegmentPath"] for p in vm.get("ExternalPropertyPathDescriptions") or []],
            "variables": [p["Name"] for p in (cls or {}).get("ChildProperties") or []],
            "bones": sorted(n for k, n in elements if k == "Bone"), "nulls": [n for n in nulls if n],
            "writes": sorted(n for k, n in writes if k == "Bone")}


def identity(t, tol=1e-4):
    t = t or {}
    r, p, s = t.get("Rotation") or {}, t.get("Translation") or {}, t.get("Scale3D") or {"X": 1.0, "Y": 1.0, "Z": 1.0}
    return (abs(abs(float(r.get("W", 1.0) or 0.0)) - 1.0) < tol and all(abs(float(p.get(k, 0.0) or 0.0)) < 1e-3 for k in "XYZ")
            and all(abs(float(s.get(k, 1.0) or 0.0) - 1.0) < tol for k in "XYZ"))


def _walk(value, path, create=False):
    """(container, key) for a segment path like "Translation.Z" or "0.Transform.Rotation"."""
    parts = path.split(".")
    for p in parts[:-1]:
        key = int(p) if isinstance(value, list) else p
        nxt = value[key] if isinstance(value, list) else value.get(key)
        if nxt is None and create and isinstance(value, dict):
            nxt = value[key] = {}
        value = nxt
    last = parts[-1]
    return value, int(last) if isinstance(value, list) else last


class Machine:
    """One rig program's memory and its run; units come from rig_units.UNITS, the pose from a hierarchy."""

    def __init__(self, program, units, variables=None):
        self.p, self.units = program, units
        self.lit = {int(k): v for k, v in program["literals"].items()}
        self.state = {}         # units' state between runs (interpolations), by instruction
        self.missing = set()    # units this program runs that aren't ported (they do nothing)
        self.reset(variables or {})

    def reset(self, variables):
        self.work = {int(k): copy.deepcopy(v) for k, v in self.p["defaults"].items()}
        self.ext = [variables.get(n, False) for n in self.p["variables"]]

    # memory
    def get(self, o):
        if o is None:
            return None
        kind, index, off = o
        if kind == 1:
            value, paths = self.lit.get(index), self.p["lit_paths"]
        elif kind == 0:
            value, paths = self.work.get(index), self.p["work_paths"]
        else:
            value, paths = self.ext[index] if index < len(self.ext) else None, self.p["ext_paths"]
        if off >= 0 and off < len(paths) and value is not None:
            try:
                container, key = _walk(value, paths[off])
                value = container[key] if isinstance(container, list) else (container or {}).get(key)
            except (IndexError, KeyError, TypeError, ValueError, AttributeError):
                return None
        return value

    def set(self, o, value):
        if o is None or o[0] == 1:
            return
        kind, index, off = o
        store, paths = (self.work, self.p["work_paths"]) if kind == 0 else (dict(enumerate(self.ext)), self.p["ext_paths"])
        if kind == 2 and index >= len(self.ext):
            return
        if off < 0 or off >= len(paths):
            if kind == 2:
                self.ext[index] = value
            else:
                self.work[index] = value
            return
        base = store.get(index)
        if base is None:
            base = {}
            if kind == 2:
                self.ext[index] = base
            else:
                self.work[index] = base
        try:
            container, key = _walk(base, paths[off], create=True)
            if isinstance(container, list):
                while len(container) <= key:
                    container.append(None)
            container[key] = value
        except (IndexError, KeyError, TypeError, ValueError, AttributeError):
            pass

    # execution
    def run(self, hierarchy, dt, variables=None):
        for k, n in enumerate(self.p["variables"]):         # what the anim blueprint pushes before each run
            if variables and n in variables:
                self.ext[k] = variables[n]
        self.h, self.dt, self.lazy = hierarchy, dt, set()
        self._range(self.p["start"], len(self.p["code"]) - 1)

    def _range(self, first, last):
        code, pc = self.p["code"], first
        for _ in range(100000):         # a runaway loop ends the tick, never Blender
            if pc > last or pc >= len(code):
                return
            x = code[pc]
            op = x["o"]
            if op == EXIT:
                return
            if op == EXECUTE:
                unit = self.units.get(x["f"])
                if unit is None:
                    self.missing.add(x["f"])
                else:
                    names = self.p["units"].get(x["f"]) or []
                    unit(self, dict(zip(names, x["a"])), x["a"], pc)
            elif op == COPY:
                self.set(x["t"], copy.deepcopy(self.get(x["s"])))
            elif op == ZERO:
                self.set(x["t"], None)
            elif op in (FALSE, TRUE):
                self.set(x["t"], op == TRUE)
            elif op == JUMP_ABS:
                pc = x["i"]
                continue
            elif op == JUMP_FWD:
                pc += x["i"]
                continue
            elif op == JUMP_BACK:
                pc -= x["i"]
                continue
            elif op in (JUMP_ABS_IF, JUMP_FWD_IF, JUMP_BACK_IF):
                if bool(self.get(x["arg"])) == x["cond"]:
                    pc = x["i"] if op == JUMP_ABS_IF else pc + x["i"] if op == JUMP_FWD_IF else pc - x["i"]
                    continue
            elif op == JUMP_TO_BRANCH:
                label = self.get(x["arg"])
                target = x["branches"].get(str(label) if label else "Completed")
                if target is not None:
                    pc = target
                    continue
            elif op == RUN:
                if x["first"] not in self.lazy:     # a lazy branch runs once per call
                    self.lazy.add(x["first"])
                    self._range(x["first"], x["last"])
            elif op == CALL and not x.get("skip"):
                self._call(self.p["callables"][x["c"]], x["a"])
            pc += 1

    def _call(self, c, operands):
        for a, o in zip(c["args"], operands):
            if a["dir"] != 1:
                self.set(a["fwd"], copy.deepcopy(self.get(o)))
        outer, self.lazy = self.lazy, set()
        try:
            self._range(c["first"], c["last"])
        finally:
            self.lazy = outer
        for a, o in zip(c["args"], operands):
            if a["dir"] == 1:
                self.set(o, copy.deepcopy(self.get(a["fwd"])))


def load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return compile_rig(json.load(f))
    except (OSError, ValueError, KeyError, RigVMError, IndexError, TypeError):
        return None
