"""A text field's editing (cursor, selection, word jumps, clipboard) and numbers typed into fields. Pure: no bpy."""
import ast
import operator

WORD = str.isalnum


class Text:
    def __init__(self, value=""):
        self.value = self.before = value
        self.cursor = self.anchor = len(value)      # anchor != cursor: a selection between them

    def set(self, value):
        self.value = self.before = value
        self.cursor = self.anchor = len(value)

    def revert(self):
        self.value = self.before
        self.cursor = self.anchor = len(self.value)

    def selection(self):
        return min(self.cursor, self.anchor), max(self.cursor, self.anchor)

    def _replace(self, text):
        a, b = self.selection()
        self.value = self.value[:a] + text + self.value[b:]
        self.cursor = self.anchor = a + len(text)

    def _word(self, i, step):
        """The next word boundary from i going `step` (-1 / 1)."""
        n = len(self.value)
        if step < 0:
            while i > 0 and not WORD(self.value[i - 1]):
                i -= 1
            while i > 0 and WORD(self.value[i - 1]):
                i -= 1
        else:
            while i < n and not WORD(self.value[i]):
                i += 1
            while i < n and WORD(self.value[i]):
                i += 1
        return i

    def _move(self, to, shift):
        self.cursor = max(0, min(len(self.value), to))
        if not shift:
            self.anchor = self.cursor

    def key(self, name, text="", ctrl=False, shift=False, clipboard=None):
        """One key: Blender's event type name, its text, modifiers. `clipboard`: a list ([text] to paste; copy and cut
        append to it). True when it was an editing key (the field ate it)."""
        a, b = self.selection()
        if ctrl and name == "A":
            self.anchor, self.cursor = 0, len(self.value)
        elif ctrl and name in ("C", "X"):
            if clipboard is not None and a != b:
                clipboard.append(self.value[a:b])
            if name == "X":
                self._replace("")
        elif ctrl and name == "V":
            if clipboard:
                self._replace(clipboard[-1].replace("\n", " ").replace("\r", ""))
        elif name == "LEFT_ARROW":
            self._move(self._word(self.cursor, -1) if ctrl else (a if a != b and not shift else self.cursor - 1), shift)
        elif name == "RIGHT_ARROW":
            self._move(self._word(self.cursor, 1) if ctrl else (b if a != b and not shift else self.cursor + 1), shift)
        elif name == "HOME":
            self._move(0, shift)
        elif name == "END":
            self._move(len(self.value), shift)
        elif name == "BACK_SPACE":
            if a == b:
                self.anchor = self._word(a, -1) if ctrl else max(0, a - 1)
            self._replace("")
        elif name == "DEL":
            if a == b:
                self.anchor = self._word(a, 1) if ctrl else min(len(self.value), a + 1)
            self._replace("")
        elif text and not ctrl and text.isprintable():
            self._replace(text)
        else:
            return False
        return True


_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.Mod: operator.mod, ast.USub: operator.neg, ast.UAdd: operator.pos}


def _eval(node):
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 64:
            raise ValueError("too big")
        return _OPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.operand))
    raise ValueError("not a number")


def number(text, integer=False, lo=None, hi=None):
    """A typed value: a number or arithmetic (2*3, (1+1)**3), rounded for ints, clamped; None when it isn't one."""
    try:
        value = _eval(ast.parse(text.strip().replace(",", "."), mode="eval"))
    except (SyntaxError, ValueError, ZeroDivisionError, TypeError, OverflowError, RecursionError):
        return None
    if integer:
        value = int(round(value))
    if lo is not None:
        value = max(lo, value)
    if hi is not None:
        value = min(hi, value)
    return value
