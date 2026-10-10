"""UE curve expression stacks (CurveExpressionsDataAsset, e.g. FN_3LToLegacy_Main_Mapping) as the app exports them:
postfix elements of operators, curve names, function references and numbers. The same evaluation as the importer's
shape-key mapping (anim_context), which keeps its own copy inline in upstream code."""
import math

OPERATOR, NAME, FUNCTION, FLOAT = 0, 1, 2, 3
OPERATORS = (lambda a, b: -a, lambda a, b: a + b, lambda a, b: a - b, lambda a, b: a * b, lambda a, b: a / b,
             lambda a, b: a % b, lambda a, b: a ** b, lambda a, b: a // b)
# by function index: (argument count, function)
FUNCTIONS = ((3, lambda x, lo, hi: min(max(x, lo), hi)), (2, min), (2, max), (1, abs), (1, round), (1, math.ceil),
             (1, math.floor), (1, math.sin), (1, math.cos), (1, math.tan), (1, math.asin), (1, math.acos),
             (1, math.atan), (1, math.sqrt), (1, lambda x: 1 / math.sqrt(x)), (1, math.log), (1, math.exp),
             (0, lambda: math.e), (0, lambda: math.pi), (0, lambda: float("nan")))


def evaluate(stack, value_of):
    """The stack's value; value_of(lower-case curve name) gives a curve's value, None when the pose lacks it (0)."""
    values = []
    for element in stack:
        kind, value = element["ElementType"], element["Value"]
        if kind == OPERATOR:
            b = 1 if value == 0 else values.pop()       # negate takes one operand
            a = values.pop()
            values.append(OPERATORS[value](a, b))
        elif kind == NAME:
            values.append(value_of(str(value).lower()) or 0.0)
        elif kind == FUNCTION:
            count, function = FUNCTIONS[value]
            args = values[len(values) - count:] if count else []
            del values[len(values) - count:]
            values.append(function(*args))
        else:
            values.append(float(value))
    return values.pop()


# operators and functions as Blender's simple driver expressions write them (index as above; None: not writable)
INFIX = ("-", "+", "-", "*", "/", None, None, None)
CALLS = ("clamp", "min", "max", "abs", "round", "ceil", "floor", "sin", "cos", "tan", "asin", "acos", "atan", "sqrt",
         None, "log", "exp", None, "pi", None)


def text(stack, name):
    """The stack as a driver expression; name(curve name) gives the text a curve reads as. ValueError on an operator
    or function a simple expression can't write."""
    out = []
    for element in stack:
        kind, value = element["ElementType"], element["Value"]
        if kind == OPERATOR:
            if INFIX[value] is None:
                raise ValueError("operator %d" % value)
            if value == 0:
                out.append("(-%s)" % out.pop())
            else:
                b, a = out.pop(), out.pop()
                out.append("(%s%s%s)" % (a, INFIX[value], b))
        elif kind == NAME:
            out.append(name(str(value)))
        elif kind == FUNCTION:
            count, call = FUNCTIONS[value][0], CALLS[value]
            if call is None:
                raise ValueError("function %d" % value)
            args = out[len(out) - count:] if count else []
            del out[len(out) - count:]
            out.append("%s(%s)" % (call, ",".join(args)) if count else call)
        else:
            v = float(value)
            out.append(repr(round(v, 6)) if v >= 0 else "(%r)" % round(v, 6))
    return out.pop()
