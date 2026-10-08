using FortnitePorting.Exporting.Styles;

namespace FortnitePorting.Exporting.MaterialPorter;

/// <summary>A wrap picked on an asset's page: the wrap item's path, "" for none, null for the item as it is.</summary>
public class ExportWrapStyle : ExportStyleBase
{
    public string? Path;
}

/// <summary>Whether a pickaxe's own effects (trail, swing, idle) go with it.</summary>
public class ExportEffectsStyle : ExportStyleBase
{
    public bool On;
}

/// <summary>A weapon mod picked for a slot: the mod item's path, "" for none, null for the weapon's own.</summary>
public class ExportWeaponModStyle : ExportStyleBase
{
    public string Slot = "";
    public string? Path;
}
