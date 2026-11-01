using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading.Tasks;
using CUE4Parse.FileProvider;
using CUE4Parse.UE4.Assets.Exports.Material;
using CUE4Parse.UE4.Objects.Core.Math;
using CUE4Parse.UE4.Objects.UObject;
using CUE4Parse.Utils;
using FortnitePorting.Models.Map;
using Serilog;

namespace FortnitePorting.MaterialPorter;

/// <summary>Islands read from the game's own map data, for a new season's that FP's online list doesn't have yet.</summary>
public static class GameMaps
{
    public static async Task<List<MapInfo>> ReadAsync(IFileProvider provider)
    {
        var maps = new List<MapInfo>();
        foreach (var file in provider.Files.Keys.Where(f => f.Contains("/Content/UI/IngameMap/", StringComparison.OrdinalIgnoreCase)
                                                            && f.EndsWith("MapUIData.uasset", StringComparison.OrdinalIgnoreCase)).ToList())
        {
            try
            {
                if (await ReadAsync(provider, file) is { } map) maps.Add(map);
            }
            catch (Exception e)
            {
                Log.Warning("[Material Porter] map UI data {File}: {Error}", file, e.Message);
            }
        }
        return maps;
    }

    private static async Task<MapInfo?> ReadAsync(IFileProvider provider, string file)
    {
        var package = await provider.LoadPackageAsync(file);
        var data = package.GetExports().FirstOrDefault(e => e.ExportType == "FortMapUIData");
        if (data is null || !data.TryGetValue(out string mapPath, "MapPath")) return null;

        var name = mapPath.SubstringAfterLast('/').Replace("_Terrain", "");
        var map = MapInfo.CreateNonDisplay(name, mapPath);      // without its picture or camera: one is drawn
        if (!data.TryGetValue(out FSoftObjectPath material, "MapMaterial")
            || !data.TryGetValue(out FSoftObjectPath cameraPath, "CaptureCameraFrontendData")
            || await material.LoadAsync() is not UMaterial loaded) return map;

        var textures = loaded.GetOrDefault("ReferencedDefaultTextures", Array.Empty<FPackageIndex>())
            .Select(t => t.ResolvedObject?.GetPathName()?.SubstringBeforeLast('.')).OfType<string>().ToList();
        var picture = textures.FirstOrDefault(t => t.Contains("Minimap", StringComparison.OrdinalIgnoreCase) && !t.Contains("Mask", StringComparison.OrdinalIgnoreCase));
        var camera = await cameraPath.LoadAsync();
        if (picture is null || !camera.TryGetValue(out int width, "OrthoWidth") || !camera.TryGetValue(out FVector2D center, "WorldOffset")) return map;

        // the camera's view fills the picture: as a drawn map's placement (WorldPartitionMap.Show), cells at half scale
        var scale = 2.0 * MapPreview.Size / width;
        var half = map.MinGridDistance / 2.0;
        var mask = textures.FirstOrDefault(t => t.Contains("Mask", StringComparison.OrdinalIgnoreCase));
        return new MapInfo(name, mapPath, picture, mask, (float) scale,
            (int) Math.Round(scale * (half - center.X)), (int) Math.Round(scale * (half - center.Y)),
            useMask: mask is not null, rotateGrid: false);
    }
}
