using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using Avalonia.Threading;
using CUE4Parse.UE4.Objects.Core.Math;
using CUE4Parse.UE4.Objects.Engine;
using CUE4Parse.Utils;
using FortnitePorting.Exporting.MaterialPorter;
using FortnitePorting.Exporting.Models;
using FortnitePorting.MaterialPorter;
using Serilog;

namespace FortnitePorting.Models.Map;

/// <summary>A UEFN project's map on the Map page. Its cells come from its actor packages (ProjectMapIndex), read when the map
/// is opened; an export reads the picked cells' actors (MPLevels) as the map's level, in one export.</summary>
public sealed class ProjectMap : WorldPartitionMap
{
    public MountedProject Project { get; }
    public ProjectMapIndex? Index { get; private set; }
    bool _indexing;

    public ProjectMap(MountedProject project, string mapPath, bool main)
        : base(MapInfo.CreateNonDisplay(main ? project.Name : project.Name + " / " + mapPath.SubstringAfterLast('/'), mapPath))
    {
        Project = project;
    }

    // The map's world and level read off the UI thread first (an uncooked map resolves its imports as it's read; the
    // Map page loads maps on the UI thread), then kept for the base load.
    public override async Task Load()
    {
        await Task.Run(() =>
        {
            if (UEParse.Provider.TryLoadPackageObject<UWorld>(MapInfo.MapPath, out var world)) world.PersistentLevel?.Load();
        });
        await base.Load();
    }

    /// <summary>A grid map's path for a cell: the map's, with the cell after '#'.</summary>
    public static string CellOf(string gridMapPath) => gridMapPath.SubstringAfterLast('#');

    public override void EnsurePreview()
    {
        if (Index != null || _indexing)
        {
            if (Index != null) base.EnsurePreview();
            return;
        }
        _indexing = true;
        PreviewStatus = "Reading the project's actors...";
        _ = Task.Run(async () =>
        {
            try
            {
                var index = await ProjectMapIndex.GetAsync(UEParse.Provider, MapInfo.MapPath, ProjectFiles.MapCacheDir, CancellationToken.None);
                Log.Information("[Material Porter] {Map}: {Actors} actors in {Cells} cells ({Seconds:0.0} s)", MapInfo.Name,
                    index.Cells.Values.Sum(c => c.Count), index.Cells.Count, index.Seconds);
                Dispatcher.UIThread.Post(() =>
                {
                    Index = index;
                    Fill(index);
                    MPLayers.Clear();
                    foreach (var layer in index.Layers.Keys.Order(StringComparer.OrdinalIgnoreCase)) MPLayers.Add(new MapLayerToggle(layer));
                    HasMPLayers = MPLayers.Count > 0;
                    PreviewStatus = string.Empty;
                    base.EnsurePreview();
                });
            }
            catch (Exception e)
            {
                Log.Error(e, "[Material Porter] {Map}: its actors couldn't be read", MapInfo.Name);
                Dispatcher.UIThread.Post(() => PreviewStatus = "The project's actors couldn't be read");
            }
            finally
            {
                _indexing = false;
            }
        });
    }

    void Fill(ProjectMapIndex index)
    {
        Grids.Clear();
        foreach (var cell in index.AllCells())
        {
            var xy = cell.Split('_').Select(int.Parse).ToArray();
            var grid = new WorldPartitionGrid(new FVector(xy[0] * index.CellSize, xy[1] * index.CellSize, 0), MapInfo);
            grid.AddMap(MapInfo.MapPath + "#" + cell);
            Grids.Add(grid);
        }
    }

    protected override List<string> PreviewLevels() =>
        Index?.Cells.Values.SelectMany(c => c).ToList() ?? [MapInfo.MapPath];

    /// <summary>The export meta for these cells: the Map page's toggles, and their actors in place of the map's level.</summary>
    public ExportDataMeta CellsExportMeta(IEnumerable<string> cells) =>
        CellsExportMeta(WorldExportMeta(), cells, MPLayers.Where(t => t.IsChecked).Select(t => t.Name));

    /// <summary>These cells' actors on a given meta, with these data layers (events that start unloaded) by layer.</summary>
    public ExportDataMeta CellsExportMeta(ExportDataMeta meta, IEnumerable<string> cells, IEnumerable<string> layers)
    {
        var picked = cells.ToList();
        meta.MPLevels = picked.SelectMany(c => Index?.Cells.GetValueOrDefault(c) ?? []).ToList();
        meta.MPLayerOf = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        foreach (var layer in layers)
            if (Index?.Layers.GetValueOrDefault(layer) is { } byCell)
                foreach (var key in picked.SelectMany(c => byCell.GetValueOrDefault(c) ?? []))
                    if (meta.MPLayerOf.TryAdd(key, layer)) meta.MPLevels.Add(key);
        return meta;
    }

    /// <summary>An export of these cells on the map's own export meta, so the map's Cancel reaches it.</summary>
    public async Task<T> RunCellsAsync<T>(IEnumerable<string> cells, Func<ExportDataMeta, Task<T>> run)
    {
        var meta = _exportMeta = CellsExportMeta(cells);
        try
        {
            return await run(meta);
        }
        finally
        {
            meta.Dispose();
            _exportMeta = null;
        }
    }

    public override async Task Export()
    {
        if (Index == null || SelectedMaps.Count == 0) return;
        IsExporting = true;
        var picked = SelectedMaps.ToArray();
        try
        {
            picked.ForEach(map => map.Status = EWorldPartitionGridMapStatus.Exporting);
            var world = await UEParse.Provider.SafeLoadPackageObjectAsync<UWorld>(MapInfo.MapPath);
            if (world is null) return;
            var cells = picked.Select(map => CellOf(map.Path)).ToList();
            var exported = !TerrainUnderCells || await RunCellsAsync(cells, meta =>
            {
                meta.WorldFlags = EWorldFlags.Landscape;
                meta.MPTerrainAreas = PickedAreas();
                return Exporter.Export(world, EExportType.World, meta);
            });
            if (!TerrainOnly) exported &= await RunCellsAsync(cells, meta => Exporter.Export(world, EExportType.World, meta));
            picked.ForEach(map => map.Status = EWorldPartitionGridMapStatus.None);
            if (exported) ClearSelectedMaps();
        }
        finally
        {
            picked.ForEach(map => map.Status = EWorldPartitionGridMapStatus.None);
            IsExporting = false;
        }
    }
}
