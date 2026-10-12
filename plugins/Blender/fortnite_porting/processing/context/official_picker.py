"""The official rig's picker: a front view of the body as buttons (the character's right on the left, as in UE's
picker). A cell holds its part's controls in order; it shows the first one not hidden, so a limb in IK shows its IK
controls. Click selects, Shift adds."""

COLUMNS = 5
_ = None


def _limb(side):
    s = side
    return {"arm": [("Arm", "upperarm_%s_fk_ctrl" % s)],
            "fore": [("Fore", "lowerarm_%s_fk_ctrl" % s), ("Elbow", "arm_%s_pv_ik_ctrl" % s)],
            "hand": [("Hand", "hand_%s_ik_ctrl" % s), ("Hand", "hand_%s_fk_ctrl" % s)],
            "clav": [("Clav", "clavicle_%s_ctrl" % s)],
            "thigh": [("Thigh", "thigh_%s_fk_ctrl" % s)],
            "calf": [("Calf", "calf_%s_fk_ctrl" % s), ("Knee", "leg_%s_pv_ik_ctrl" % s)],
            "foot": [("Foot", "foot_%s_ik_ctrl" % s), ("Foot", "foot_%s_fk_ctrl" % s)],
            "toes": [("Toes", "ball_%s_ik_ctrl" % s), ("Toes", "ball_%s_fk_ctrl" % s)],
            "roll": [("Roll", "foot_roll_%s_ctrl" % s)]}


R, L = _limb("r"), _limb("l")
BODY = [      # R outer, R inner, middle, L inner, L outer
    [_, _, [("Head", "head_ik_ctrl"), ("Head", "head_ctrl")], _, _],
    [_, _, [("Neck", "neck_02_ctrl")], _, _],
    [_, R["clav"], [("Neck", "neck_01_ctrl")], L["clav"], _],
    [R["arm"], _, [("Chest", "chest_ctrl"), ("Spine 3", "spine_03_ctrl")], _, L["arm"]],
    [R["fore"], _, [("Chest Tan", "chest_tan_ctrl"), ("Spine 2", "spine_02_ctrl")], _, L["fore"]],
    [R["hand"], _, [("Hips Tan", "hips_tan_ctrl"), ("Spine 1", "spine_01_ctrl")], _, L["hand"]],
    [_, _, [("Body", "body_ctrl")], _, _],
    [_, _, [("Hips", "hips_ctrl")], _, _],
    [_, R["thigh"], _, L["thigh"], _],
    [_, R["calf"], _, L["calf"], _],
    [_, R["foot"], _, L["foot"], _],
    [R["roll"], R["toes"], _, L["toes"], L["roll"]],
]
GROUND = [("Global", "global_ctrl"), ("Root", "root_ctrl"), ("Offset", "body_offset_ctrl")]     # a full-width row
SIDE, MIDDLE = 0.36, 0.44       # each side's two cells, then the middle cell's share of the rest
FINGERS = ("thumb", "index", "middle", "ring", "pinky")


def pick(obj, cell):
    """(label, control) a cell shows: its first control on this rig that isn't hidden; None for none."""
    pb = obj.pose.bones
    return next(((label, name) for label, name in cell or () if name in pb and not pb[name].hide), None)


def finger(obj, side, name):
    """A finger's controls, root to tip (the metacarpal first when the rig has one)."""
    pb = obj.pose.bones
    names = ["%s_metacarpal_%s_ctrl" % (name, side)] + ["%s_%02d_%s_ctrl" % (name, i, side) for i in (1, 2, 3)]
    return [n for n in names if n in pb and not pb[n].hide]


def _cells(layout, n):
    """n layouts of equal width side by side (an aligned row shares its width by content: blanks shrink)."""
    out = []
    for i in range(n - 1):
        split = layout.split(factor=1.0 / (n - i), align=True)
        out.append(split.row(align=True))       # a split's first child takes its factor: made in order
        layout = split.row(align=True)
    return out + [layout]


def _button(layout, obj, text, names):
    if not names:
        layout.label(text="")
        return
    pb = obj.pose.bones
    op = layout.operator("fpmp.rig_pick", text=text, depress=all(pb[n].select for n in names))
    op.names = ",".join(names)


def ui_picker(layout, obj):
    col = layout.column(align=True)
    sides = col.split(factor=0.5)
    sides.label(text="R")
    end = sides.row()
    end.alignment = 'RIGHT'
    end.label(text="L")
    for cells in BODY:
        split = col.row(align=True).split(factor=SIDE, align=True)
        left = split.row(align=True)         # a split's first child takes its factor: made in order
        rest = split.split(factor=MIDDLE, align=True)
        centre, right = rest.row(align=True), rest.row(align=True)
        for layout, cell in zip(_cells(left, 2) + [centre] + _cells(right, 2), cells):
            got = pick(obj, cell)
            _button(layout, obj, got[0] if got else "", [got[1]] if got else [])
    row = col.row(align=True)
    for label, name in GROUND:
        _button(row, obj, label, [name] if pick(obj, [(label, name)]) else [])
    col = layout.column(align=True)
    for side, text in (("r", "Fingers R"), ("l", "Fingers L")):
        split = col.row(align=True).split(factor=0.34, align=True)
        _button(split, obj, text, [n for f in FINGERS for n in finger(obj, side, f)])
        row = split.row(align=True)
        for f in FINGERS:
            _button(row, obj, f[0].upper(), finger(obj, side, f))
