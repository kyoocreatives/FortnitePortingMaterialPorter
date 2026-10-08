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


def _mask(condition):
    return np.where(condition, I(-1), I(0))


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
    "step": lambda edge, x: np.where(x >= edge, F(1), F(0)),
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
    "b2f": ("i", lambda a: np.where(a != 0, F(1), F(0))),
    "i2b": ("i", lambda a: _mask(a != 0)),
    "b2i": ("i", lambda a: np.where(a != 0, I(1), I(0))),
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


def decode(code, functions):
    """The program as a list of (name, ...) tuples. functions: (inputs, outputs) per external function."""
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


def run(program, registers, count, constants, sets, functions, rng, ids=None):
    """Run a decoded program over count instances. constants: the table's bytes. sets: a Binding
    per data set. functions: a callable per external function, (count, inputs) -> outputs."""
    table = bytes(constants) + b"\0" * (-len(constants) % 4)
    cf, ci = np.frombuffer(table, F), np.frombuffer(table, I)
    r = [None] * max(registers, 1)
    places = {}     # acquireindex register -> (mask or None for all, count)

    def value(operand, kind):
        constant, at = operand
        if constant:
            return cf[at >> 2] if kind == "f" else ci[at >> 2]
        v = r[at]
        if kind == "x":
            return v
        return fl(v) if kind == "f" else it(v)

    with np.errstate(all="ignore"):
        for ins in program:
            name = ins[0]
            kinds = _kinds(name)
            if kinds is not None:
                r[ins[2]] = kinds[1](*[value(o, k) for o, k in zip(ins[1], kinds[0])])
            elif name in ("inputdata_float", "inputdata_int32", "inputdata_half"):
                b = sets[ins[1]]
                rows = b.source.ints if name == "inputdata_int32" else b.source.floats
                row = ins[2] + (b.source.half_base if name == "inputdata_half" else 0)
                v = rows[row, b.source_start:b.source_start + count]
                if len(v) < count:          # one instance read by all (an event payload)
                    v = rows[row, b.source_start]
                r[ins[3]] = v.copy() if b.source is b.target else v     # a spawn script reads where it writes
            elif name in ("inputdata_noadvance_float", "inputdata_noadvance_int32", "inputdata_noadvance_half"):
                b = sets[ins[1]]
                rows = b.source.ints if name == "inputdata_noadvance_int32" else b.source.floats
                r[ins[3]] = rows[ins[2] + (b.source.half_base if name.endswith("half") else 0), b.source_start]
            elif name in ("outputdata_float", "outputdata_int32", "outputdata_half"):
                b = sets[ins[1]]
                mask, n = places[ins[2]]
                rows = b.target.ints if name == "outputdata_int32" else b.target.floats
                v = value(ins[3], "i" if name == "outputdata_int32" else "f")
                if mask is not None and np.ndim(v):
                    v = v[mask]
                row = ins[4]
                if name == "outputdata_half":       # kept at half precision, like the engine's buffer
                    row += b.target.half_base
                    v = np.asarray(v, F).astype(np.float16).astype(F)
                rows[row, b.target_start:b.target_start + n] = v
            elif name == "acquireindex":
                b = sets[ins[1]]
                valid = value(ins[2], "i")
                if np.ndim(valid):
                    mask = valid != 0
                    n = int(mask.sum())
                    index = np.where(mask, np.cumsum(mask, dtype=I) - 1, I(-1))
                else:
                    mask, n = None, (count if valid != 0 else 0)
                    index = np.arange(count, dtype=I) if n else np.full(count, -1, I)
                    if not n:
                        mask = np.zeros(count, bool)
                places[ins[3]] = (mask, n)
                r[ins[3]] = index
                b.written = n
            elif name == "exec_index":
                r[ins[1]] = np.arange(count, dtype=I)
            elif name == "random":
                r[ins[2]] = rng.random(count, dtype=F) * value(ins[1][0], "f")
            elif name == "randomi":
                top = value(ins[1][0], "i")
                r[ins[2]] = np.minimum((rng.random(count) * (top.astype(np.float64) + 1)).astype(I), top)
            elif name == "external_func_call":
                outputs = functions[ins[1]](count, [value(o, "x") for o in ins[2]])
                for register, v in zip(ins[3], outputs):
                    if register != 0xffff:
                        r[register] = v
            elif name == "acquire_id":
                # persistent ID per new particle: (index, acquire tag)
                start = ids.take(count) if ids is not None else 0
                r[ins[2]] = np.arange(start, start + count, dtype=I)
                r[ins[3]] = np.full(count, ids.tag if ids is not None else 0, I)
            elif name == "update_id":
                pass
            else:
                raise Unsupported(name)
    return r
