"""The official rig's panel sections: the rig's own switches (top), the IK / FK table and the held props.
Group rows (All, Arms, Legs) switch every part they hold in one click; a group's button shows pressed when all agree."""
from .official_rig import ON, _chains, load, setting

LABEL = 0.26        # the part column
BUTTONS = 0.5       # FK | IK against Stretch | Local


def parts(obj):
    """Each switched part: {name, switch, stretch, softness, local} (names absent on this rig are None)."""
    data, pb = load(), obj.pose.bones
    s = data["solve"]
    local = {x["space"].split("_")[1] if x["space"].startswith("upperarm") else "neck": x["setting"] for x in s["local"]}
    limbs = {c["switch"]: c for c in s["ik"]}
    out = []

    def built(name):
        return name if name and name in pb else None

    for chain in _chains(data, set(pb.keys())):
        switch = chain["switch"]
        part = s[chain["part"]] if chain.get("part") else limbs[switch]
        side = switch.split("_")[1] if switch.startswith("arm_") else ("neck" if chain.get("part") == "neck" else None)
        out.append({"name": switch.replace("_fk_ik_switch", "").replace("_", " ").title(), "switch": switch,
                    "stretch": built(part["stretch"]), "softness": built(part.get("softness")), "local": built(local.get(side))})
    return out


def groups(obj):
    """(label, parts) rows: All, then Arms and Legs (both sides), then the single parts (Spine, Neck)."""
    every = parts(obj)
    rows = [("All", every)]
    for prefix, label in (("arm_", "Arms"), ("leg_", "Legs")):
        held = [p for p in every if p["switch"].startswith(prefix)]
        if held:
            rows.append((label, held))
    rows += [(p["name"], [p]) for p in every if not p["switch"].startswith(("arm_", "leg_"))]
    return rows


def _setting_toggle(row, obj, names, text):
    """One button for a setting on several parts: pressed when all are on; a click turns all off, else all on."""
    names = [n for n in names if n]
    if not names:
        row.label(text="")
        return
    on = all(setting(obj, n) >= 0.5 for n in names)
    op = row.operator("fpmp.official_rig_setting", text=text, depress=on)
    op.name, op.value = ",".join(names), 0.0 if on else 1.0


def _row(layout, obj, label, held, soft=False):
    row = layout.row(align=True)
    split = row.split(factor=LABEL, align=True)
    split.label(text=label)
    cells = split.split(factor=BUTTONS, align=True)
    mode = cells.row(align=True)
    switches = ",".join(p["switch"] for p in held)
    ik = [setting(obj, p["switch"]) >= 0.5 for p in held]
    fk_op = mode.operator("fpmp.official_rig_switch", text="FK", depress=not any(ik))
    fk_op.switch, fk_op.ik = switches, False
    ik_op = mode.operator("fpmp.official_rig_switch", text="IK", depress=all(ik))
    ik_op.switch, ik_op.ik = switches, True
    flags = cells.row(align=True)
    _setting_toggle(flags, obj, [p["stretch"] for p in held], "Stretch")
    _setting_toggle(flags, obj, sorted({p["local"] for p in held if p["local"]}), "Local")
    if soft:
        name = held[0]["softness"]
        if name:
            flags.prop(obj.pose.bones[name], '["%s"]' % name, text="Soft")
        else:
            flags.label(text="")


def ui_top(layout, obj):
    """Rig on/off and Bake to Controls."""
    on = bool(obj.data.get(ON, True))
    row = layout.row(align=True)
    row.operator("fpmp.official_rig_on", text="Rig", icon='CONSTRAINT_BONE', depress=on).on = not on
    row.operator("fpmp.official_rig_bake", text="Bake to Controls", icon='ACTION')


def ui_body(layout, obj):
    """Epic's Show Body Controls (fingers, toes, root, body, hips, neck)."""
    show = load()["solve"]["visibility"]
    if show in obj.pose.bones:
        shown = setting(obj, show) >= 0.5
        layout.operator("fpmp.official_rig_body_controls", text="Body Controls",
                        icon='HIDE_OFF' if shown else 'HIDE_ON', depress=shown).show = not shown


def ui_ik(layout, obj):
    """The IK / FK table: group rows, the sides folded under them, the feet's roll blend."""
    col = layout.column(align=True)
    for label, held in groups(obj):
        _row(col, obj, label, held)
    sides = [p for p in parts(obj) if p["switch"].startswith(("arm_", "leg_"))]
    if sides:
        header, body = layout.panel("fpmp_official_rig_sides", default_closed=True)
        header.label(text="Each Side")
        if body is not None:
            col = body.column(align=True)
            for p in sides:
                _row(col, obj, p["name"], [p], soft=True)
    pb = obj.pose.bones
    rolls = [r for r in load()["solve"].get("foot_roll", []) if r["blend"] in pb]
    if rolls:
        row = layout.row(align=True)
        for roll in rolls:
            row.prop(pb[roll["blend"]], '["%s"]' % roll["blend"], text="Roll %s" % roll["side"].upper(), slider=True)


def has_hands(obj):
    return any(hasattr(obj.pose.bones.get("fingers_%s_ctrl" % s), "fpmp_held_prop") for s in ("l", "r"))


def ui_hands(layout, obj):
    """Each hand's held prop (official_grip) and, with one, how the fingers wrap it."""
    pb = obj.pose.bones
    for side in ("l", "r"):
        holder = pb.get("fingers_%s_ctrl" % side)
        if holder is None or not hasattr(holder, "fpmp_held_prop"):
            continue
        col = layout.column(align=True)
        col.prop(holder, "fpmp_held_prop", text="Held " + side.upper())
        if holder.fpmp_held_prop is not None:
            row = col.row(align=True)
            for name, label in (("grip", "Grip"), ("thumb_wrap", "Thumb"), ("finger_wrap", "Fingers")):
                row.prop(holder, '["%s"]' % name, text=label)
