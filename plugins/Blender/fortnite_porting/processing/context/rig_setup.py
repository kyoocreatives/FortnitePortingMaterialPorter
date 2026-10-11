"""The units a rig's construction event runs (adding nulls and controls, re-parenting, keeping poses), the array and
name helpers it builds them with, and metadata tags. Same interface as rig_units."""
import copy
import json

from .dynamics_solver import compose, relative
from .rig_units import ELEMENT_BITS, _array, _get, _name, _set, element, mix_parents, num, transform, transform_out

GLOBAL = 1      # ERigVMTransformSpace::GlobalSpace


def _key(kind, name):
    return {"Type": "ERigElementType::" + kind, "Name": name}


def _parent(m, a):
    name = element(_get(m, a, "Parent"))
    return name if name and m.h.has(name) else None


def add_null(m, a, ops, pc):
    """A null at Transform, local to its parent or (global space) at that global on the parent's initial pose."""
    parent, t = _parent(m, a), transform(_get(m, a, "Transform"))
    if int(num(_get(m, a, "Space"))) == GLOBAL and parent:
        t = relative(t, m.h.get_global(parent, True))
    _set(m, a, "Item", _key("Null", m.h.add("Null", _name(_get(m, a, "Name")) or "NewNull", parent, t)))


def add_control(m, a, ops, pc):
    """A control whose global is its value, then its offset, then its parent."""
    parent = _parent(m, a)
    offset = transform(_get(m, a, "OffsetTransform"))
    if int(num(_get(m, a, "OffsetSpace"))) == GLOBAL and parent:
        offset = relative(offset, m.h.get_global(parent))
    value = transform(_get(m, a, "InitialValue"))
    if int(num((_get(m, a, "Settings") or {}).get("InitialSpace"))) == GLOBAL:
        value = relative(value, compose(offset, m.h.get_global(parent)) if parent else offset)
    name = m.h.add("Control", _name(_get(m, a, "Name")) or "NewControl", parent, compose(value, offset))
    m.h.offsets[name] = (offset, offset)
    _set(m, a, "Item", _key("Control", name))


def set_control_offset(m, a, ops, pc):
    name = _name(_get(m, a, "Control"))
    if name and m.h.kind.get(name) == "Control":
        m.h.set_offset(name, transform(_get(m, a, "Offset")), int(num(_get(m, a, "Space"))) == GLOBAL)


def set_default_parent(m, a, ops, pc):
    child, parent = element(_get(m, a, "Child")), element(_get(m, a, "Parent"))
    if child and parent:
        m.h.reparent(child, parent)


def get_pose(m, a, ops, pc):
    """The chosen elements' globals and locals (initial or current), in hierarchy order."""
    initial, mask = bool(_get(m, a, "Initial")), int(num(_get(m, a, "ElementType")))
    wanted = {element(i) for i in _get(m, a, "ItemsToGet") or []}
    elements = []
    for name in list(m.h.rest_global):
        kind = m.h.kind.get(name, "Bone")
        if not ELEMENT_BITS.get(kind, 0) & mask or (wanted and name not in wanted):
            continue
        parent = m.h.parent.get(name)
        elements.append({"Index": {"Key": _key(kind, name)}, "GlobalTransform": transform_out(m.h.get_global(name, initial)),
                         "LocalTransform": transform_out(m.h.get_local(name, initial)),
                         "ActiveParent": _key(m.h.kind.get(parent, "Bone"), parent) if parent else _key("None", "None")})
    _set(m, a, "Pose", {"Elements": elements})


def array_make(m, a, ops, pc):
    m.set(ops[-1], [copy.deepcopy(m.get(o)) for o in ops[:-1]])


def array_append(m, a, ops, pc):
    other = _array(m, ops[1])
    if other:
        m.set(ops[0], list(_array(m, ops[0])) + copy.deepcopy(other))


def array_union(m, a, ops, pc):
    """Array's distinct values in first-seen order, then Other's new ones; an empty Other changes nothing."""
    other = _array(m, ops[1])
    if not other:
        return
    seen, out = set(), []
    for v in list(_array(m, ops[0])) + list(other):
        h = json.dumps(v, sort_keys=True, default=str)
        if h not in seen:
            seen.add(h)
            out.append(copy.deepcopy(v))
    m.set(ops[0], out)


def to_string(m, a, ops, pc):
    v = m.get(ops[0])
    m.set(ops[-1], ("True" if v else "False") if isinstance(v, bool) else ("%f" % v) if isinstance(v, float) else _name(v))


def int_to_name(m, a, ops, pc):
    n = int(num(_get(m, a, "Number")))
    text = str(n)
    if n >= 0:
        text = text.zfill(min(8, int(num(_get(m, a, "PaddedSize")))))
    _set(m, a, "Result", text)


def make_absolute(m, a, ops, pc):
    g = compose(transform(_get(m, a, "Local")), transform(_get(m, a, "Parent")))
    _set(m, a, "Global", transform_out((g[0].normalized(), g[1])))


def parent_constraint_math(m, a, ops, pc):
    """Input (the child's initial global) carried by its parents from their initial to current globals, mixed."""
    child = transform(_get(m, a, "Input"))
    parents = [(element(p.get("Item")), max(num(p.get("Weight"), 1.0), 0.0)) for p in _get(m, a, "Parents") or [] if isinstance(p, dict)]
    parents = [(n, w) for n, w in parents if n and m.h.has(n) and w >= 1e-4]
    total = sum(w for _, w in parents)
    if total <= 1e-4:
        return
    gs = []
    for name, w in parents:
        g = compose(relative(child, m.h.get_global(name, True)), m.h.get_global(name))
        gs.append(((g[0].normalized(), g[1]), w / total))
    _set(m, a, "Output", transform_out(mix_parents(gs, _get(m, a, "AdvancedSettings"))))


def set_tag(m, a, ops, pc):
    name, tag = element(_get(m, a, "Item")), _name(_get(m, a, "Tag"))
    if name and tag and tag not in m.h.tags.setdefault(name, []):
        m.h.tags[name].append(tag)


def find_tagged(m, a, ops, pc):
    tag = _name(_get(m, a, "Tag"))
    _set(m, a, "Items", [_key(m.h.kind.get(n, "Bone"), n) for n in list(m.h.rest_global) if tag in m.h.tags.get(n, ())])


UNITS = {"HierarchyAddNull": add_null, "HierarchyAddControlTransform": add_control, "SetDefaultParent": set_default_parent,
         "HierarchyGetPoseItemArray": get_pose, "DISPATCH_RigVMDispatch_ArrayMake": array_make,
         "DISPATCH_RigVMDispatch_ArrayAppend": array_append, "DISPATCH_RigVMDispatch_ArrayUnion": array_union,
         "DISPATCH_RigDispatch_ToString": to_string, "MathIntToName": int_to_name, "MathTransformMakeAbsolute": make_absolute,
         "ParentConstraintMath": parent_constraint_math, "SetMetadataTag": set_tag, "FindItemsWithMetadataTag": find_tagged,
         "DISPATCH_RigDispatch_SetMetadata": lambda m, a, ops, pc: m.set(ops[-1], True),
         "DebugTransformArrayMutableNoSpace": lambda m, a, ops, pc: None, "SetControlOffset": set_control_offset}
