using System;
using System.Collections.Generic;
using System.Linq;
using CUE4Parse.UE4.Assets.Exports;
using CUE4Parse.UE4.Assets.Objects;
using CUE4Parse.UE4.Objects.Engine;
using CUE4Parse.UE4.Objects.UObject;
using CUE4Parse.Utils;
using Serilog;

namespace FortnitePorting.Exporting.Context;

public partial class ExportContext
{
    // For downloaded builds, fetches the asset's packages plus a map's streaming levels and partition cells (MaterialPorter.Prefetch)
    public void StartPrefetch(UObject asset)
    {
        if (!MaterialPorter.Prefetch.Enabled) return;

        var packages = new List<string> { asset.Owner?.Name ?? string.Empty };
        try
        {
            if (asset is UWorld world) packages.AddRange(WorldPackages(world));
        }
        catch (Exception e)
        {
            Log.Warning("[Material Porter] {World}: its levels not prefetched: {Error}", asset.Name, e.Message);
        }
        MaterialPorter.Prefetch.Start(FileProvider, packages);
    }

    private static IEnumerable<string> WorldPackages(UWorld world)
    {
        static string Package(UObject? levelStreaming) =>
            levelStreaming?.GetOrDefault<FSoftObjectPath>("WorldAsset").AssetPathName.Text.SubstringBefore(".") ?? string.Empty;

        foreach (var streamingLevelLazy in world.StreamingLevels)
            yield return Package(streamingLevelLazy.Load());

        if (world.PersistentLevel.Load() is not ULevel level
            || level.GetOrDefault<UObject>("WorldSettings")?.GetOrDefault<UObject>("WorldPartition")?.GetOrDefault<UObject>("RuntimeHash") is not { } runtimeHash)
            yield break;

        foreach (var streamingData in runtimeHash.GetOrDefault("RuntimeStreamingData", Array.Empty<FStructFallback>()))
        foreach (var cell in streamingData.GetOrDefault("SpatiallyLoadedCells", Array.Empty<FPackageIndex>())
                     .Concat(streamingData.GetOrDefault("NonSpatiallyLoadedCells", Array.Empty<FPackageIndex>())))
        {
            yield return Package(cell.Load()?.GetOrDefault<UObject?>("LevelStreaming"));
        }

        foreach (var streamingGrid in runtimeHash.GetOrDefault("StreamingGrids", Array.Empty<FStructFallback>()))
        foreach (var gridLevel in streamingGrid.GetOrDefault("GridLevels", Array.Empty<FStructFallback>()))
        foreach (var layerCell in gridLevel.GetOrDefault("LayerCells", Array.Empty<FStructFallback>()))
        foreach (var gridCell in layerCell.GetOrDefault("GridCells", Array.Empty<UObject>()))
        {
            yield return Package(gridCell.GetOrDefault<UObject?>("LevelStreaming"));
        }
    }
}
