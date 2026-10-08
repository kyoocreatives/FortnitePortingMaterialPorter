namespace FortnitePorting.Models.Assets;

/// <summary>A wrap option on an asset's page: a wrap item's path, "" for no wrap, null for the item as it is.</summary>
public partial class WrapStyleData : BaseStyleData
{
    public string? Path { get; }

    public WrapStyleData(string name, string? path)
    {
        StyleName = name;
        Path = path;
        ShowName = true;
    }
}

/// <summary>A pickaxe's Effects option: without its own effects, or with them.</summary>
public partial class EffectsStyleData : BaseStyleData
{
    public bool On { get; }

    public EffectsStyleData(string name, bool on)
    {
        StyleName = name;
        On = on;
        ShowName = true;
    }
}

/// <summary>A weapon mod option for a slot: a mod item's path, "" for none, null for the weapon's own.</summary>
public partial class WeaponModStyleData : BaseStyleData
{
    public string Slot { get; }
    public string? Path { get; }

    public WeaponModStyleData(string name, string slot, string? path)
    {
        StyleName = name;
        Slot = slot;
        Path = path;
        ShowName = true;
    }
}
