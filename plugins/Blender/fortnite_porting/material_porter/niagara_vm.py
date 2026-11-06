"""Niagara's script VM (the VectorVM), in numpy.

A CPU emitter keeps its compiled scripts in the cooked asset (CachedScriptVM.ByteCode): a flat
program over registers, each holding one value per particle. It reads particle attributes from a
data set (inputdata) and constants from a table (the engine's and the script's parameters, then
the script's literals), computes, and writes the attributes back (outputdata) at the places
acquireindex hands out. A particle the script marks dead gets no place, which is how particles
die. One numpy array per register runs a script over every particle at once, as the engine does.

Registers and constants are untyped 32 bits and each operation says how it reads them. A register
is kept here as a float32 or int32 array (or numpy scalar) and viewed as the other when needed.
"""
import numpy as np

F, I = np.float32, np.int32

OPS = """done add sub mul div mad lerp rcp rsq sqrt neg abs exp exp2 log log2 sin cos tan asin acos atan atan2
ceil floor fmod frac trunc clamp min max pow round sign step random noise
cmplt cmple cmpgt cmpge cmpeq cmpneq select
addi subi muli divi clampi mini maxi absi negi signi randomi cmplti cmplei cmpgti cmpgei cmpeqi cmpneqi
bit_and bit_or bit_xor bit_not bit_lshift bit_rshift logic_and logic_or logic_xor logic_not
f2i i2f f2b b2f i2b b2i
inputdata_float inputdata_int32 inputdata_half inputdata_noadvance_float inputdata_noadvance_int32 inputdata_noadvance_half
outputdata_float outputdata_int32 outputdata_half acquireindex external_func_call exec_index noise2D noise3D
enter_stat_scope exit_stat_scope update_id acquire_id""".split()


class Unsupported(Exception):
    """The script uses something this VM doesn't run."""


def fl(x):
    return x if x.dtype == F else x.view(F)


def it(x):
    return x if x.dtype == I else x.view(I)


def spread(x, count):
    """x as count values: as it is when it has them, else repeated (np.full is cheaper than broadcast_to)."""
    if x.shape == (count,):
        return x
    return np.full(count, x) if not x.shape else np.broadcast_to(x, (count,))


def _mask(condition):
    return -condition.astype(I)


def _divi(a, b):
    ok = b != 0
    return np.where(ok, np.trunc(a.astype(np.float64) / np.where(ok, b, 1)), 0).astype(I)


def _select(m, a, b):
    if a.dtype != b.dtype:
        b = b.view(a.dtype)
    return np.where(m != 0, a, b)


# The engine's VM guards against infinities and NaNs: division by (nearly) zero, roots or powers
# of zero and logarithms of zero give 0 (its "safe" kernels), so no infinity reaches a position.
SMALL = F(1e-8)


def _safe(ok, value):
    return np.where(ok, value, F(0)).astype(F, copy=False)


def _div(a, b):
    ok = np.abs(b) > SMALL
    return _safe(ok, a / np.where(ok, b, F(1)))


def _fmod(a, b):
    ok = np.abs(b) > SMALL
    return _safe(ok, np.fmod(a, np.where(ok, b, F(1))))


def _pow(a, b):
    ok = a > SMALL
    return _safe(ok, np.power(np.where(ok, a, F(1)), b))


def _root(a):
    ok = a > SMALL
    return _safe(ok, np.sqrt(np.where(ok, a, F(1))))


def _log(kind):
    def log(a):
        ok = a > 0
        return _safe(ok, kind(np.where(ok, a, F(1))))
    return log


# name: (operand kinds, function). Kinds: f float, i int, b mask (int), x untyped.
FLOAT1 = {
    "rcp": lambda a: _div(F(1), a), "rsq": lambda a: _div(F(1), _root(a)), "sqrt": _root, "neg": np.negative, "abs": np.abs,
    "exp": np.exp, "exp2": np.exp2, "log": _log(np.log), "log2": _log(np.log2), "sin": np.sin, "cos": np.cos, "tan": np.tan,
    "asin": lambda a: np.arcsin(np.clip(a, F(-1), F(1))), "acos": lambda a: np.arccos(np.clip(a, F(-1), F(1))),
    "atan": np.arctan, "ceil": np.ceil, "floor": np.floor,
    "frac": lambda a: a - np.floor(a), "trunc": np.trunc, "round": np.rint,
    "sign": lambda a: np.where(a >= 0, F(1), F(-1)),
}
FLOAT2 = {
    "add": np.add, "sub": np.subtract, "mul": np.multiply, "div": _div, "atan2": np.arctan2, "fmod": _fmod,
    "min": np.minimum, "max": np.maximum, "pow": _pow,
    "step": lambda edge, x: (x >= edge).astype(F),
}
FLOAT3 = {
    "mad": lambda a, b, c: a * b + c,
    "lerp": lambda a, b, t: a * (F(1) - t) + b * t,
    "clamp": lambda x, lo, hi: np.minimum(np.maximum(x, lo), hi),
}
COMPARE = {"cmplt": np.less, "cmple": np.less_equal, "cmpgt": np.greater, "cmpge": np.greater_equal, "cmpeq": np.equal, "cmpneq": np.not_equal}
INT1 = {"absi": np.abs, "negi": np.negative, "signi": np.sign, "bit_not": np.invert, "logic_not": np.invert}
INT2 = {
    "addi": np.add, "subi": np.subtract, "muli": np.multiply, "divi": _divi, "mini": np.minimum, "maxi": np.maximum,
    "bit_and": np.bitwise_and, "bit_or": np.bitwise_or, "bit_xor": np.bitwise_xor,
    "bit_lshift": np.left_shift, "bit_rshift": np.right_shift,
    "logic_and": np.bitwise_and, "logic_or": np.bitwise_or, "logic_xor": np.bitwise_xor,
}
CONVERT = {
    "f2i": ("f", lambda a: np.trunc(a).astype(I)),
    "i2f": ("i", lambda a: a.astype(F)),
    "f2b": ("f", lambda a: _mask(a != 0)),
    "b2f": ("i", lambda a: (a != 0).astype(F)),
    "i2b": ("i", lambda a: _mask(a != 0)),
    "b2i": ("i", lambda a: (a != 0).astype(I)),
}


def _kinds(name):
    """(operand kinds, function) of a plain operation, or None."""
    if name in FLOAT1:
        return "f", FLOAT1[name]
    if name in FLOAT2:
        return "ff", FLOAT2[name]
    if name in FLOAT3:
        return "fff", FLOAT3[name]
    if name in COMPARE:
        compare = COMPARE[name]
        return "ff", lambda a, b: _mask(compare(a, b))
    if name in INT1:
        return "i", INT1[name]
    if name in INT2:
        return "ii", INT2[name]
    if name.endswith("i") and name[:-1] in COMPARE:
        compare = COMPARE[name[:-1]]
        return "ii", lambda a, b: _mask(compare(a, b))
    if name == "clampi":
        return "iii", lambda x, lo, hi: np.minimum(np.maximum(x, lo), hi)
    if name in CONVERT:
        return CONVERT[name]
    if name == "select":
        return "ixx", _select
    return None


def _decode(code, functions):
    """The program as a list of (name, ...) tuples."""
    program, at, size = [], 0, len(code)

    def u8():
        nonlocal at
        if at >= size:
            raise Unsupported("bytecode ends mid-instruction")
        at += 1
        return code[at - 1]

    def u16():
        return u8() | (u8() << 8)

    while True:
        op = u8()
        if op >= len(OPS):
            raise Unsupported("opcode %d" % op)
        name = OPS[op]
        if name == "done":
            break
        kinds = _kinds(name)
        if kinds is not None or name in ("random", "randomi", "noise", "noise2D", "noise3D"):
            count = len(kinds[0]) if kinds else {"noise2D": 2, "noise3D": 3}.get(name, 1)
            types = u8()
            src = [(bool(types >> i & 1), u16()) for i in range(count)]
            program.append((name, src, u16()))
        elif name.startswith("inputdata"):
            program.append((name, u16(), u16(), u16()))             # set, component, register
        elif name.startswith("outputdata"):
            types = u8()
            program.append((name, u16(), u16(), (bool(types & 1), u16()), u16()))   # set, index register, source, component
        elif name == "acquireindex":
            types = u8()
            program.append((name, u16(), (bool(types & 1), u16()), u16()))          # set, valid, register
        elif name == "external_func_call":
            index = u16()
            if index >= len(functions):
                raise Unsupported("external function %d of %d" % (index, len(functions)))
            inputs, outputs = functions[index]
            # an input is a register when its top bit is set, else a constant
            src = [(not v & 0x8000, v & 0x7fff) for v in (u16() for _ in range(inputs))]
            program.append((name, index, src, [u16() for _ in range(outputs)]))
        elif name == "exec_index":
            program.append((name, u16()))
        elif name == "enter_stat_scope":
            u16()
        elif name == "exit_stat_scope":
            pass
        elif name in ("update_id", "acquire_id"):
            program.append((name, u16(), u16(), u16()))
        else:
            raise Unsupported(name)
    if at != size:
        raise Unsupported("bytecode continues after done")
    return program


class Buffer:
    """A data set's instances: one row per float component, one per int32 component."""

    def __init__(self, floats, ints, capacity=0):
        self.floats = np.zeros((floats, capacity), F)
        self.ints = np.zeros((ints, capacity), I)
        self.count = 0
        self.half_base = floats     # where the half-precision rows start among the float rows

    def reserve(self, capacity):
        if capacity > self.floats.shape[1]:
            grow = max(capacity, self.floats.shape[1] * 2)
            for name in ("floats", "ints"):
                old = getattr(self, name)
                new = np.zeros((old.shape[0], grow), old.dtype)
                new[:, :old.shape[1]] = old
                setattr(self, name, new)


class Binding:
    """A data set as a script run sees it: where it reads (source, from start) and writes (target, from start)."""

    def __init__(self, source=None, source_start=0, target=None, target_start=0):
        self.source, self.source_start = source, source_start
        self.target, self.target_start = target, target_start
        self.written = 0        # instances the run gave a place to


class Program(list):
    """A decoded program; it keeps its compiled forms (many instances, one) once run."""
    compiled = None


_decoded = {}


def decode(code, functions):
    """The program as a list of (name, ...) tuples, shared by every script with the same bytecode.
    functions: (inputs, outputs) per external function."""
    key = (bytes(code), tuple(map(tuple, functions)))
    program = _decoded.get(key)
    if program is None:
        program = _decoded[key] = Program(_decode(code, functions))
    return program


def _acquire(valid, count):
    """(mask or None for all, count, index register) of an acquireindex."""
    if np.ndim(valid):
        mask = valid != 0
        return mask, int(mask.sum()), np.where(mask, np.cumsum(mask, dtype=I) - 1, I(-1))
    n = count if valid != 0 else 0
    if n:
        return None, n, np.arange(count, dtype=I)
    return np.zeros(count, bool), 0, np.full(count, -1, I)


def _randomi(rng, count, top):
    return np.minimum((rng.random(count) * (top.astype(np.float64) + 1)).astype(I), top)


# written as operators: the same ufuncs, called faster, and on numpy scalars (system scripts) no ufunc call
OPERATORS = {"add": "{0} + {1}", "sub": "{0} - {1}", "mul": "{0} * {1}", "mad": "{0} * {1} + {2}",
             "lerp": "{0} * (_one - {2}) + {1} * {2}",
             "addi": "{0} + {1}", "subi": "{0} - {1}", "muli": "{0} * {1}",
             "bit_and": "{0} & {1}", "bit_or": "{0} | {1}", "bit_xor": "{0} ^ {1}",
             "logic_and": "{0} & {1}", "logic_or": "{0} | {1}", "logic_xor": "{0} ^ {1}"}
for _name, _symbol in (("cmplt", "<"), ("cmple", "<="), ("cmpgt", ">"), ("cmpge", ">="), ("cmpeq", "=="), ("cmpneq", "!=")):
    OPERATORS[_name] = OPERATORS[_name + "i"] = "-({0} %s {1}).astype(I)" % _symbol


def _held(operand, types):
    """The dtype kind an untyped operand holds: a constant reads as int."""
    constant, at = operand
    return "i" if constant else types.get(at)


def _result(name, kinds, src, types):
    """The dtype kind an operation writes ("f", "i"), None when it depends on run-time values."""
    if name == "select":
        return _held(src[1], types)
    if name in CONVERT:
        return "f" if name.endswith("2f") else "i"
    return "f" if name in FLOAT1 or name in FLOAT2 or name in FLOAT3 else "i"


def _compile(program, single):
    """The program as one Python function, run(count, cf, ci, sets, functions, rng, ids): each operation's
    kinds, register views and constant reads resolved once, registers as locals. single: for one instance
    (system scripts), whose attributes are read as numpy scalars: the same float32 math without array calls."""
    env = {"np": np, "F": F, "I": I, "fl": fl, "it": it, "Unsupported": Unsupported, "_one": F(1),
           "_acquire": _acquire, "_randomi": _randomi}
    bound, consts, calls, regs, places = {}, {}, set(), set(), set()
    types = {}      # register -> dtype kind it holds at this point of the program, None if unknown
    body = []

    def bind(obj):
        name = bound.get(id(obj))
        if name is None:
            name = bound[id(obj)] = "_h%d" % len(bound)
            env[name] = obj
        return name

    def arg(operand, kind):
        constant, at = operand
        if constant:
            name = "%s%d" % ("f" if kind == "f" else "i", at >> 2)
            consts[name] = "%s[%d]" % ("cf" if kind == "f" else "ci", at >> 2)
            return name
        regs.add(at)
        reg = "r%d" % at
        have = types.get(at)
        if kind == "x" or have == kind:
            return reg
        if have is None:
            return "%s(%s)" % ("fl" if kind == "f" else "it", reg)
        return "%s.view(%s)" % (reg, "F" if kind == "f" else "I")

    def put(register, expr, kind):
        regs.add(register)
        body.append("r%d = %s" % (register, expr))
        types[register] = kind

    for ins in program:
        name = ins[0]
        kinds = _kinds(name)
        if kinds is not None:
            args = [arg(o, k) for o, k in zip(ins[1], kinds[0])]
            if name in OPERATORS:
                expr = OPERATORS[name].format(*args)
            elif name == "select" and _held(ins[1][1], types) == _held(ins[1][2], types) is not None:
                # no view needed: both sides one dtype
                expr = ("({1} if {0} != 0 else {2})" if single else "np.where({0} != 0, {1}, {2})").format(*args)
            else:
                expr = "%s(%s)" % (bind(kinds[1]), ", ".join(args))
            put(ins[2], expr, _result(name, kinds, ins[1], types))
        elif name.startswith("inputdata"):
            ints, half = name.endswith("int32"), name.endswith("half")
            row = "%d + src.half_base" % ins[2] if half else "%d" % ins[2]
            rows = "src.ints" if ints else "src.floats"
            body.append("b = sets[%d]" % ins[1])
            body.append("src = b.source")
            if "noadvance" in name:
                put(ins[3], "%s[%s, b.source_start]" % (rows, row), "i" if ints else "f")
                continue
            if single:
                put(ins[3], "%s[%s, b.source_start]" % (rows, row), "i" if ints else "f")
                continue
            body.append("v = %s[%s, b.source_start:b.source_start + count]" % (rows, row))
            body.append("if len(v) < count:")        # one instance read by all (an event payload)
            body.append("    v = %s[%s, b.source_start]" % (rows, row))
            put(ins[3], "v.copy() if src is b.target else v", "i" if ints else "f")     # a spawn script reads where it writes
        elif name.startswith("outputdata"):
            if ins[2] not in places:
                body.append("raise KeyError(%d)" % ins[2])
                continue
            ints, half = name.endswith("int32"), name.endswith("half")
            body.append("b = sets[%d]" % ins[1])
            body.append("v = %s" % arg(ins[3], "i" if ints else "f"))
            body.append("if m%d is not None and np.ndim(v):" % ins[2])
            body.append("    v = v[m%d]" % ins[2])
            row = "%d" % ins[4]
            if half:        # kept at half precision, like the engine's buffer
                row += " + b.target.half_base"
                body.append("v = np.asarray(v, F).astype(np.float16).astype(F)")
            body.append("b.target.%s[%s, b.target_start:b.target_start + n%d] = v" % ("ints" if ints else "floats", row, ins[2]))
        elif name == "acquireindex":
            body.append("b = sets[%d]" % ins[1])
            regs.add(ins[3])
            places.add(ins[3])
            body.append("m{0}, n{0}, r{0} = _acquire({1}, count)".format(ins[3], arg(ins[2], "i")))
            types[ins[3]] = "i"
            body.append("b.written = n%d" % ins[3])
        elif name == "exec_index":
            put(ins[1], "np.arange(count, dtype=I)", "i")
        elif name == "random":
            put(ins[2], "rng.random(count, dtype=F) * %s" % arg(ins[1][0], "f"), "f")
        elif name == "randomi":
            put(ins[2], "_randomi(rng, count, %s)" % arg(ins[1][0], "i"), "i")
        elif name == "external_func_call":
            calls.add(ins[1])
            body.append("o = call%d(count, [%s])" % (ins[1], ", ".join(arg(o, "x") for o in ins[2])))
            body.append("if type(o) is not list:")
            body.append("    o = list(o)")
            for j, register in enumerate(ins[3]):
                if register != 0xffff:
                    regs.add(register)
                    body.append("if len(o) > %d:" % j)
                    body.append("    r%d = o[%d]" % (register, j))
                    types[register] = None
        elif name == "acquire_id":
            # persistent ID per new particle: (index, acquire tag)
            body.append("start = ids.take(count) if ids is not None else 0")
            put(ins[2], "np.arange(start, start + count, dtype=I)", "i")
            put(ins[3], "np.full(count, ids.tag if ids is not None else 0, I)", "i")
        elif name == "update_id":
            pass
        else:
            body.append("raise Unsupported(%r)" % name)
    # a register read before any write holds None, as in the engine's untouched register file
    head = ["r%d = None" % at for at in sorted(regs)]
    head += ["%s = %s" % kv for kv in sorted(consts.items())]
    head += ["call%d = functions[%d]" % (k, k) for k in sorted(calls)]
    source = "def run(count, cf, ci, sets, functions, rng, ids):\n" + "".join("    %s\n" % line for line in head + body + ["pass"])
    exec(compile(source, "<niagara script>", "exec"), env)
    return env["run"]


def run(program, registers, count, constants, sets, functions, rng, ids=None):
    """Run a decoded program over count instances. constants: the table's bytes. sets: a Binding
    per data set. functions: a callable per external function, (count, inputs) -> outputs."""
    compiled = getattr(program, "compiled", None)
    if compiled is None:
        compiled = {}
        if isinstance(program, Program):
            program.compiled = compiled
    single = count == 1
    step = compiled.get(single)
    if step is None:
        step = compiled[single] = _compile(program, single)
    table = bytes(constants) + b"\0" * (-len(constants) % 4)
    cf, ci = np.frombuffer(table, F), np.frombuffer(table, I)
    with np.errstate(all="ignore"):
        step(count, cf, ci, sets, functions, rng, ids)
