"""The IK / FK table for the kit's rigs (creatures, LEGO figures): each limb's FK / IK with group rows (All, Legs, Arms)
that switch every limb they hold in one click."""
from . import official_panel, rig_kit, rig_style

LABEL = 0.3         # wider than the official table's: "Front Leg L"
_limbs = {}


def limbs(obj):
    """The kit's limbs, parsed once per change of its stored meta: the panel reads them on every redraw. Read only."""
    raw = obj.data.get(rig_kit.META) or ""
    hit = _limbs.get(obj.data.name_full)
    if hit is None or hit[0] != raw:
        hit = _limbs[obj.data.name_full] = (raw, rig_kit.meta(obj).get("limbs", []))
    return hit[1]


def groups(obj):
    """(label, limbs) rows: All, Front Legs, Hind Legs, Arms (when they split the limbs), then each limb."""
    every = limbs(obj)
    rows = [("All", every)] if len(every) > 1 else []
    legs = [l for l in every if l.get("kind") == "leg"]
    for label, held in (("Front Legs", [l for l in legs if l.get("front")]), ("Hind Legs", [l for l in legs if not l.get("front")]),
                        ("Arms", [l for l in every if l.get("kind") == "arm"])):
        if len(held) > 1 and len(held) < len(every):
            rows.append((label, held))
    return rows + [(l.get("label") or l["name"].replace("_", " ").title(), [l]) for l in every]


def _row(layout, settings, label, held):
    mode, _ = official_panel.cells(layout, label, LABEL)
    ik = [settings is not None and settings.get(l["setting"], 1.0) >= 0.5 for l in held]
    names = ",".join(l["name"] for l in held)
    op = mode.operator("fpmp.kit_switch", text="FK", depress=not any(ik))
    op.limbs, op.ik = names, False
    op = mode.operator("fpmp.kit_switch", text="IK", depress=all(ik))
    op.limbs, op.ik = names, True


def ui_limbs(layout, obj):
    """Each limb's FK / IK (pressed when all its limbs agree), in the official table's grid: group rows, then each
    limb folded under Each Side when there are groups."""
    rows = groups(obj)
    if not rows:
        return
    settings = obj.pose.bones.get(rig_style.SETTINGS)
    each = len(limbs(obj))
    grouped, sides = rows[:-each], rows[-each:]
    col = layout.column(align=True)
    for label, held in grouped or sides:
        _row(col, settings, label, held)
    if grouped:
        header, body = layout.panel("fpmp_kit_rig_sides", default_closed=True)
        header.label(text="Each Side")
        if body is not None:
            col = body.column(align=True)
            for label, held in sides:
                _row(col, settings, label, held)
