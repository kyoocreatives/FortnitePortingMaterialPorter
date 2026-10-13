"""The Rig panel's lines for the kit's rigs (creatures, LEGO figures): rig on/off and Bake to Controls, then each limb's
FK / IK with group rows (All, Legs, Arms) that switch every limb they hold in one click."""
from . import rig_kit, rig_style

LABEL = 0.3


def groups(obj):
    """(label, limbs) rows: All, Front Legs, Hind Legs, Arms (when they split the limbs), then each limb."""
    limbs = rig_kit.meta(obj).get("limbs", [])
    rows = [("All", limbs)] if len(limbs) > 1 else []
    legs = [l for l in limbs if l.get("kind") == "leg"]
    for label, held in (("Front Legs", [l for l in legs if l.get("front")]), ("Hind Legs", [l for l in legs if not l.get("front")]),
                        ("Arms", [l for l in limbs if l.get("kind") == "arm"])):
        if len(held) > 1 and len(held) < len(limbs):
            rows.append((label, held))
    return rows + [(l.get("label") or l["name"].replace("_", " ").title(), [l]) for l in limbs]


def ui_top(layout, obj):
    """Rig on/off and Bake to Controls."""
    on = bool(obj.data.get(rig_kit.ON, True))
    row = layout.row(align=True)
    row.operator("fpmp.kit_rig_on", text="Rig", icon='CONSTRAINT_BONE', depress=on).on = not on
    row.operator("fpmp.kit_bake", text="Bake to Controls", icon='ACTION')


def ui_limbs(layout, obj):
    """Each limb's FK / IK (pressed when all its limbs agree)."""
    rows = groups(obj)
    if not rows:
        return
    settings = obj.pose.bones.get(rig_style.SETTINGS)
    col = layout.column(align=True)
    for label, held in rows:
        split = col.row(align=True).split(factor=LABEL, align=True)
        split.label(text=label)
        mode = split.row(align=True)
        ik = [settings is not None and settings.get(l["setting"], 1.0) >= 0.5 for l in held]
        names = ",".join(l["name"] for l in held)
        op = mode.operator("fpmp.kit_switch", text="FK", depress=not any(ik))
        op.limbs, op.ik = names, False
        op = mode.operator("fpmp.kit_switch", text="IK", depress=all(ik))
        op.limbs, op.ik = names, True
