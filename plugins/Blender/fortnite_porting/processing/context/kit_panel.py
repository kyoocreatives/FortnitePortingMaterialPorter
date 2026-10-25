"""The IK / FK table for the kit's rigs (creatures, LEGO figures): each limb's FK / IK with group rows (All, Legs, Arms)
that switch every limb they hold in one click."""
from . import rig_kit, rig_style

LABEL = 0.3
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


def ui_limbs(layout, obj):
    """Each limb's FK / IK (pressed when all its limbs agree)."""
    rows = groups(obj)
    if not rows:
        return
    settings = obj.pose.bones.get(rig_style.SETTINGS)
    col = layout.column(align=True)
    for label, held in rows:
        split = col.row(align=True).split(factor=LABEL, align=True)
        split.label(text=label, translate=False)
        mode = split.row(align=True)
        ik = [settings is not None and settings.get(l["setting"], 1.0) >= 0.5 for l in held]
        names = ",".join(l["name"] for l in held)
        op = mode.operator("fpmp.kit_switch", text="FK", depress=not any(ik))
        op.limbs, op.ik = names, False
        op = mode.operator("fpmp.kit_switch", text="IK", depress=all(ik))
        op.limbs, op.ik = names, True
