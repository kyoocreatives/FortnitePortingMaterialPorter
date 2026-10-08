using CUE4Parse.FileProvider;
using CUE4Parse.UE4.Assets;
using CUE4Parse.UE4.Assets.Exports;

namespace FortnitePorting.Exporting.MaterialPorter;

/// <summary>An asset listed without being read (the game's Niagara systems read at once took over 40 GB); <see cref="Read"/> loads it.</summary>
public static class Unloaded
{
    private sealed record Marker(string Path, object? Detail);

    /// <summary>What a listing learns of an asset from its package's maps: its class, and what else its tab says.</summary>
    public interface IOutline
    {
        string? Class { get; }
    }

    public static UObject Create(string package, string name, string className, object? detail = null) => new()
    {
        Name = name,
        Outer = new ResolvedLoadedObject(new UObject { Name = package }),
        Class = new ResolvedLoadedObject(new UObject { Name = className }),
        CustomGameData = new Marker($"{package}.{name}", detail),
    };

    public static bool Is(UObject asset) => asset.CustomGameData is Marker;

    public static object? Detail(UObject asset) => (asset.CustomGameData as Marker)?.Detail;

    /// <summary>The asset itself where this stands for it.</summary>
    public static UObject Read(UObject asset, IFileProvider provider) =>
        asset.CustomGameData is Marker marker ? provider.LoadPackageObject(marker.Path) : asset;
}
