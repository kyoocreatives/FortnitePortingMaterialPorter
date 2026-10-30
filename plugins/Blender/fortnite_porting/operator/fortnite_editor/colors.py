"""Locker tile colours: the item's series colours when it has a series, else its rarity's (as the game's locker
shows them: a bright top fading to a dark bottom, an accent for outlines). Pure: tested headless."""

# rarity display name -> (top, bottom) sRGB hex
RARITY = {
    "Common": ("#8C8C8C", "#3A3A3A"),
    "Uncommon": ("#69BB1E", "#175117"),
    "Rare": ("#49ACF2", "#143977"),
    "Epic": ("#B15BE2", "#4B2483"),
    "Legendary": ("#E8873A", "#78371D"),
    "Mythic": ("#FFD24C", "#9E6A0B"),
    "Exotic": ("#76D6E3", "#1C5B67"),
    "Unattainable": ("#FF3B45", "#6E0B0B"),
}


def rgba(hex_color, alpha=1.0):
    h = hex_color.lstrip("#")
    return (int(h[0:2], 16) / 255.0, int(h[2:4], 16) / 255.0, int(h[4:6], 16) / 255.0, alpha)


def lighter(color, amount=0.35):
    return tuple(c + (1 - c) * amount for c in color[:3]) + (color[3],)


def tile(rarity, series_colors):
    """(top, bottom, accent) RGBA for a tile."""
    if series_colors and len(series_colors) >= 3:
        return rgba(series_colors[0]), rgba(series_colors[2]), lighter(rgba(series_colors[1]))
    top, bottom = RARITY.get(rarity, RARITY["Uncommon"])
    return rgba(top), rgba(bottom), lighter(rgba(top))
