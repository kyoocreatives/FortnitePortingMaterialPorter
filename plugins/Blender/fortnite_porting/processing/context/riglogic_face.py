"""Epic's RigLogic on FP's facial bones, as plain drivers: knobs drive the raw controls (the board's formulas), those
drive the correctives, and both drive coefficient sums on mechanism bones in the game's frame that the facial bones
copy. Motion is added to FP's rest pose (spec 2026-10-10-riglogic-face)."""
import math

import bpy
from mathutils import Matrix

from .driver_batch import Batch

EXPR, MCH, POSE, COPY = "RL_Expressions", "RL_", "RL_pose_", "RL copy"
LONGEST = 250       # characters per chunk expression: Blender keeps 255
MECHANICS = "Face Mechanics"
FLIP = Matrix(((1, 0, 0), (0, -1, 0), (0, 0, 1)))     # the dump's frame mirrors Y against the game's


def _num(v):
    return "%.6g" % v


def _names():
    """a, b, ... z, aa, ab ... for driver variables."""
    i = 0
    while True:
        s, n = "", i
        while True:
            s = chr(97 + n % 26) + s
            n = n // 26 - 1
            if n < 0:
                break
        yield s
        i += 1


def _prop_var(driver, name, obj, path):
    v = driver.variables.new()
    v.name, v.type = name, 'SINGLE_PROP'
    v.targets[0].id, v.targets[0].data_path = obj, path
    return name


def _input_path(rig, i):
    name = rig["raw"][i] if i < rig["raw_count"] else "psd%d" % (i - rig["raw_count"])
    return 'pose.bones["%s"]["%s"]' % (EXPR, name)


def _properties(obj, rig, formulas, var, batch):
    """The raw controls (from the board's formulas) and the correctives, as properties on one hidden bone."""
    pb = obj.pose.bones[EXPR]
    for name in rig["raw"]:
        pb[name] = 0.0
        if name in formulas:
            driver = batch.new(pb, '["%s"]' % name)
            driver.expression = var(driver, formulas[name])
    for k, p in enumerate(rig["psds"]):
        name = "psd%d" % k
        pb[name] = 0.0
        driver = batch.new(pb, '["%s"]' % name)
        terms = ["min(1,max(0,%s))" % _prop_var(driver, v, obj, _input_path(rig, i)) for v, i in zip(_names(), p["inputs"])]
        driver.expression = "min(1,%s*%s)" % (_num(p["weight"]), "*".join(terms))


def _bones(obj, rig):
    """Edit mode: the expressions bone, and per driven joint a mechanism bone at its game frame under the real parent,
    with a child at the real bone's rest. Returns the driven joints built."""
    from ...material_porter.effects import ue_rest
    edit = obj.data.edit_bones
    root = edit.get("FACIAL_C_FacialRoot") or edit["head"]
    expr = edit.new(EXPR)
    expr.head, expr.tail = root.head.copy(), root.head + (root.tail - root.head) * 0.5
    expr.parent, expr.use_deform = root, False
    built = []
    for name in sorted({rig["joints"][j] for j, _ in rig["channels"]}):
        if name not in edit or edit[name].parent is None:
            continue
        frame = ue_rest(obj.data.bones[name])
        mch = edit.new(MCH + name)        # a new bone has no length: placed by head, tail and roll, not its matrix
        mch.head = frame.to_translation()
        mch.tail = mch.head + frame.to_3x3().col[1].normalized() * edit[name].length
        mch.align_roll(frame.to_3x3().col[2])
        mch.parent, mch.use_deform = edit[edit[name].parent.name], False
        pose = edit.new(POSE + name)
        pose.head, pose.tail, pose.roll = edit[name].head.copy(), edit[name].tail.copy(), edit[name].roll
        pose.parent, pose.use_deform = mch, False
        built.append(name)
    return built


def _chunk(obj, pb, terms, chunks, batch):
    """One chunk property summing coef * input over as many terms as fit an expression; returns the terms left."""
    key = "c%d" % chunks[0]
    chunks[0] += 1
    pb[key] = 0.0
    driver = batch.new(pb, '["%s"]' % key)
    names, text, used = _names(), "", 0
    for input_path, c in terms:
        name = next(names)
        # the sign leads the term: Blender's simple expressions misread "a*-0.5"
        term = "%s%s*%s" % ("-" if c < 0 else ("+" if text else ""), name, _num(abs(c)))
        if text and len(text) + len(term) > LONGEST:
            break
        _prop_var(driver, name, obj, input_path)
        text += term
        used += 1
    driver.expression = text
    return key, terms[used:]


def _sum(obj, pb, path, index, terms, offset, chunks, batch):
    """Drive pb's channel to offset + sum(coef * input), through chunk properties."""
    parts = []
    while terms:
        key, terms = _chunk(obj, pb, terms, chunks, batch)
        parts.append(key)
    driver = batch.new(pb, path, index)
    names = _names()
    driver.expression = "+".join([_num(offset)] + [_prop_var(driver, next(names), obj, 'pose.bones["%s"]["%s"]' % (pb.name, k))
                                                   for k in parts])


def _mechanics(obj, names):
    group = obj.data.collections.get("Mechanics") or obj.data.collections.get(MECHANICS) or obj.data.collections.new(MECHANICS)
    group.is_visible = False
    for name in names:
        bone = obj.data.bones[name]
        for c in list(bone.collections):
            c.unassign(bone)
        group.assign(bone)


def build(obj, rig, formulas, var):
    """RigLogic's face on the armature (see the module docstring). `formulas`: raw control name -> the board's formula
    for it; `var(driver, formula)` adds the formula's variables to the driver and returns its expression. Returns the
    number of driven joints built."""
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')
    built = _bones(obj, rig)
    bpy.ops.object.mode_set(mode='OBJECT')
    _mechanics(obj, [EXPR] + [MCH + n for n in built] + [POSE + n for n in built])
    with Batch() as batch:
        _properties(obj, rig, formulas, var, batch)
        _channels(obj, rig, built, batch)
    return len(built)


def _channels(obj, rig, built, batch):
    """Per driven joint, its mechanism bone's channels as coefficient sums, and the joint copying it."""
    from ...material_porter.effects import ue_rest
    index = {n: i for i, n in enumerate(rig["joints"])}
    for name in built:
        b = obj.data.bones[name]
        local = (ue_rest(b.parent).inverted() @ ue_rest(b)).to_3x3()
        to_bone = local.transposed() @ FLIP * 0.01          # parent-frame cm -> the bone's own frame, metres
        pb = obj.pose.bones[MCH + name]
        pb.rotation_mode = 'XYZ'
        rows = {a: rig["channels"].get((index[name], a), []) for a in range(9)}
        chunks = [0]
        for axis in range(3):
            terms = {}
            for k in range(3):
                for i, c in rows[k]:
                    terms[i] = terms.get(i, 0.0) + to_bone[axis][k] * c
            terms = [(_input_path(rig, i), c) for i, c in terms.items() if abs(c) > 1e-12]
            if terms:
                _sum(obj, pb, "location", axis, terms, 0.0, chunks, batch)
        for axis, sign in ((0, -1.0), (1, 1.0), (2, -1.0)):          # Euler degrees, mirrored like FLIP
            if rows[3 + axis]:
                _sum(obj, pb, "rotation_euler", axis,
                     [(_input_path(rig, i), sign * math.radians(c)) for i, c in rows[3 + axis]], 0.0, chunks, batch)
        for axis in range(3):
            if rows[6 + axis]:
                _sum(obj, pb, "scale", axis, [(_input_path(rig, i), c) for i, c in rows[6 + axis]], 1.0, chunks, batch)
        copy = obj.pose.bones[name].constraints.new('COPY_TRANSFORMS')
        copy.name, copy.target, copy.subtarget = COPY, obj, POSE + name


def set_on(obj, on):
    """The board drives the facial bones (on), or they play their own keys (off)."""
    for pb in obj.pose.bones:
        for c in pb.constraints:
            if c.name == COPY:
                c.mute = not on
    for fc in (obj.animation_data.drivers if obj.animation_data else []):
        if fc.data_path.startswith('pose.bones["%s' % MCH):
            fc.mute = not on
