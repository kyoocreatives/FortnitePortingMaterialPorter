using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Collections.Specialized;
using System.IO;
using System.Linq;
using System.Numerics;
using System.Threading.Tasks;
using Avalonia.Media.Imaging;
using Avalonia.Threading;
using CUE4Parse.UE4.Assets.Exports;
using CUE4Parse.UE4.Assets.Exports.Engine;
using CUE4Parse.UE4.Objects.Core.i18N;
using CUE4Parse.UE4.Objects.Core.Math;
using CUE4Parse.UE4.Objects.Engine;
using CUE4Parse.UE4.Objects.UObject;
using CUE4Parse.Utils;
using FortnitePorting.Application;
using FortnitePorting.Exporting;
using FortnitePorting.Exporting.Models;
using FortnitePorting.Models.Map;
using FortnitePorting.Services;
using FortnitePorting.Shared.Extensions;

namespace FortnitePorting.MaterialPorter;

// The Map page as data, for the Fortnite editor's Map tab. Pixels are in the map's picture (x right, y down), as the
// page lays its cells over it. Nothing blocks on a load: a map still loading answers {loading: true, status}.
//   maps-list                                        {loading, maps:[{id, name, kind: br|feature|project, minimap}]}
//   maps-open?id=                                    {id, image, width, height, cells:[{id, x, y, size, levels}],
//                                                    layers:[names], pois:[{name, kind: poi|landmark, x, y}]}
//   maps-export?id=&cells=0;4&flags=Actors,..&filter=&layers=&main=1   the export payload, or {cancelled: true}
//                                                    (main=1: the map's own level too; with no cells, alone;
//                                                    settings=Name:Value;: Blender export settings for this export)
//   maps-cancel?run=                                 cancels the map exports in flight, and that run's cells still on their way
//   maps-images?id=&kinds=minimap,height,normal,weight   {files:[{kind, path}]}
public partial class MaterialPorterService
{
    private static readonly ConcurrentDictionary<ExportDataMeta, string> MapExports = new();      // export -> its run
    private static readonly ConcurrentDictionary<string, byte> CancelledRuns = new();      // each Export press: its own id
    private static readonly ConcurrentDictionary<WorldPartitionMap, (Bitmap Bitmap, string Path)> MapImages = new();
    private static Dictionary<string, string>? _mapMarkers;

    private static Task<object?>? MapsRoute(string route, NameValueCollection query) => route switch
    {
        "maps-list" => MapsListAsync(),
        "maps-open" => MapsOpenAsync(query["id"] ?? throw new ArgumentException("id missing")),
        "maps-export" => MapsExportAsync(query),
        "maps-cancel" => Task.FromResult<object?>(new { cancelled = CancelMapExports(query["run"]) }),
        "maps-images" => MapsImagesAsync(query["id"] ?? throw new ArgumentException("id missing"), (query["kinds"] ?? "minimap").Split(',')),
        _ => null,
    };

    private static Task<T> OnUI<T>(Func<T> read) => Dispatcher.UIThread.InvokeAsync(read).GetTask();

    private static async Task<object?> MapsListAsync()
    {
        var vm = AppServices.MapVM;
        var loading = await OnUI(() =>
        {
            if (!vm.IsInitialized)      // as the Map page does when first opened (again after a game reload)
            {
                MapImages.Clear();
                _mapMarkers = null;
                vm.IsInitialized = true;
                TaskService.Run(vm.Initialize);
                return true;
            }
            return vm.IsLoading;
        });
        var maps = await OnUI(() => vm.Maps.Where(m => m.DataLoaded).Select(m => new
        {
            id = m.MapInfo.MapPath, name = m.MapInfo.Name,
            kind = MapKind(m),
            minimap = m.MapBitmap is not null && !m.DrawnPreview,
        }).ToList());
        return new { loading, maps };
    }

    private static string MapKind(WorldPartitionMap map)
    {
        var kind = map.MapInfo.IsNonDisplay ? "feature" : "br";
        ProjectMapKind(map, ref kind);
        return kind;
    }

    // UEFN projects' maps: their group in the list, and exports through the project's index
    static partial void ProjectMapFailed(string? status, ref bool failed);
    static partial void ProjectMapKind(WorldPartitionMap map, ref string kind);
    static partial void ProjectMapCells(WorldPartitionMap map, ExportDataMeta meta, List<string> gridPaths, string[] layers, ref bool handled);

    private static Task<WorldPartitionMap> FindMap(string id) => OnUI(() =>
        AppServices.MapVM.Maps.FirstOrDefault(m => m.MapInfo.MapPath.Equals(id, StringComparison.OrdinalIgnoreCase))
        ?? throw new KeyNotFoundException("no map " + id));

    private static async Task<object?> MapsOpenAsync(string id)
    {
        var map = await FindMap(id);
        var state = await OnUI(() =>
        {
            if (!map.IsDrawingPreview && map.MapBitmap is null && MapFailed(map.PreviewStatus))
                return map.PreviewStatus;
            map.EnsurePreview();
            if (map.DataLoaded && map.Grids.Count == 0 && !map.IsDrawingPreview && map.PreviewStatus.Length == 0)
                return NoCells;
            return map.DataLoaded && map.Grids.Count > 0 && map.MapBitmap is not null && !map.IsDrawingPreview ? null : map.PreviewStatus;
        });
        if (state == NoCells || MapFailed(state))
            throw new ArgumentException(state);
        if (state is not null) return new { loading = true, status = state.Length == 0 ? "Loading " + map.MapInfo.Name + "..." : state };

        var markers = await PoiMarkersAsync(map.MapInfo.MapPath);
        return await OnUI(() =>
        {
            var image = MapImage(map);
            var (w, h) = (map.MapBitmap!.PixelSize.Width, map.MapBitmap.PixelSize.Height);
            var info = map.MapInfo;
            // where the page centres a cell's square: at half its margin from the picture's centre
            (double X, double Y) Pixel(double left, double top) => (w / 2.0 + left / 2, h / 2.0 + top / 2);
            var g = (double) info.MinGridDistance;
            var pois = markers.Select(p =>
            {
                var q = new Vector2((float) (p.X - g / 2), (float) (p.Y - g / 2));     // cells sit at their corner
                if (info.RotateGrid) q = new Vector2(q.Y, -q.X);
                var (x, y) = Pixel(q.X * info.Scale + info.XOffset, q.Y * info.Scale + info.YOffset);
                return new { name = p.Name, kind = p.Kind, x, y };
            }).ToList();
            return (object) new
            {
                id, image, width = w, height = h,
                cells = map.Grids.Select((c, i) =>
                {
                    var (x, y) = Pixel(c.OffsetMargin.Left, c.OffsetMargin.Top);
                    return new { id = i, x, y, size = c.CellSize, levels = c.Maps.Count };
                }).ToList(),
                layers = map.MPLayers.Select(l => l.Name).ToList(),
                pois,
            };
        });
    }

    private const string NoCells = "This map has no streamed cells";

    // a preview status that won't change by asking again
    private static bool MapFailed(string? status)
    {
        var failed = status is "Nothing to draw a map from" or "The map couldn't be drawn";
        ProjectMapFailed(status, ref failed);
        return failed;
    }

    // The map's picture as a PNG for Blender, written again only when the page's picture changed.
    private static string MapImage(WorldPartitionMap map)
    {
        if (MapImages.TryGetValue(map, out var kept) && ReferenceEquals(kept.Bitmap, map.MapBitmap) && File.Exists(kept.Path))
            return kept.Path;
        var folder = Path.Combine(map.MapsFolder.FullName, map.WorldName);
        Directory.CreateDirectory(folder);
        // maps whose paths end alike share the folder: the file is named after the whole path
        var path = Path.Combine(folder, $"Editor_Map_{Convert.ToHexString(System.Security.Cryptography.SHA1.HashData(System.Text.Encoding.UTF8.GetBytes(map.MapInfo.MapPath.ToLowerInvariant())))[..8]}.png");
        map.MapBitmap!.Save(path);
        MapImages[map] = (map.MapBitmap, path);
        return path;
    }

    private record PoiMarker(string Name, string Kind, double X, double Y);

    // Named places from the map's UI data (its front-end markers table): Athena.Location.POI.* named, UnNamedPOI landmarks.
    private static async Task<List<PoiMarker>> PoiMarkersAsync(string mapPath)
    {
        var provider = AppServices.UEParse.Provider!;
        _mapMarkers ??= await Task.Run(async () =>
        {
            var found = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            foreach (var file in provider.Files.Keys.Where(f => f.EndsWith("MapUIData.uasset", StringComparison.OrdinalIgnoreCase)).ToList())
            {
                UObject? data;
                try { data = (await provider.LoadPackageAsync(file)).GetExports().FirstOrDefault(e => e.ExportType == "FortMapUIData"); }
                catch (Exception) { continue; }
                if (data is not null && data.TryGetValue(out string map, "MapPath")
                                     && data.TryGetValue(out FSoftObjectPath markers, "FrontEndMapMarkers") && !markers.AssetPathName.IsNone)
                    found.TryAdd(map.SubstringAfterLast("/"), markers.AssetPathName.Text);
            }
            return found;
        });
        if (!_mapMarkers.TryGetValue(mapPath.SubstringAfterLast("/"), out var table)
            || await provider.SafeLoadPackageObjectAsync<UDataTable>(table) is not { } rows)
            return [];
        return rows.RowMap.Select(row => row.Value.TryGetValue(out FText text, "Text") && row.Value.TryGetValue(out FVector at, "WorldLocation")
                ? new PoiMarker(text.Text, row.Key.Text.StartsWith("Athena.Location.POI.") ? "poi" : "landmark", at.X, at.Y)
                : null)
            .OfType<PoiMarker>().ToList();
    }

    private static async Task<object?> MapsExportAsync(NameValueCollection query)
    {
        var run = query["run"] ?? "";
        using var status = StatusLog.Instance.Exporting("Exporting map cells");
        var (meta, settings) = EditorExportMeta(query["settings"]);
        MapExports[meta] = run;
        try
        {
            if (run.Length > 0 && CancelledRuns.ContainsKey(run)) meta.Cancel();
            return await MapsExportAsync(query, meta, settings);
        }
        catch (OperationCanceledException)
        {
            return new { cancelled = true };
        }
        finally
        {
            MapExports.TryRemove(meta, out _);
            meta.Dispose();
        }
    }

    private static async Task<object?> MapsExportAsync(NameValueCollection query, ExportDataMeta meta, ViewModels.BaseExportSettings settings)
    {
        var map = await FindMap(query["id"] ?? throw new ArgumentException("id missing"));
        var picked = (query["cells"] ?? "").Split(';', StringSplitOptions.RemoveEmptyEntries).Select(int.Parse).ToList();
        var levels = await OnUI(() => picked.Where(i => i >= 0 && i < map.Grids.Count).Select(i => map.Grids[i])
            .SelectMany(g => g.Maps).Select(m => m.Path).Distinct(StringComparer.OrdinalIgnoreCase).ToList());
        var main = query["main"] == "1";       // the map's own level (its terrain), as the page's Include Terrain
        if (levels.Count == 0 && !main) throw new ArgumentException("no such cells");

        meta.WorldFlags = 0;
        foreach (var flag in (query["flags"] ?? "").Split(',', StringSplitOptions.RemoveEmptyEntries))
            meta.WorldFlags |= Enum.Parse<EWorldFlags>(flag.Trim(), true);
        meta.ActorFilter = string.IsNullOrWhiteSpace(query["filter"]) ? null : query["filter"]!.Trim();
        var provider = AppServices.UEParse.Provider!;
        var worlds = new List<UWorld>();
        var handled = false;
        if (levels.Count > 0)
            ProjectMapCells(map, meta, levels, (query["layers"] ?? "").Split(';', StringSplitOptions.RemoveEmptyEntries), ref handled);
        if (handled) worlds.Add(await provider.LoadPackageObjectAsync<UWorld>(map.MapInfo.MapPath));
        else
        {
            if (main) levels.Insert(0, map.MapInfo.MapPath);
            foreach (var level in levels)
                if (await provider.SafeLoadPackageObjectAsync<UWorld>(level) is { } world) worlds.Add(world);
        }

        if (meta.CancellationToken.IsCancellationRequested) return new { cancelled = true };
        StatusLog.Instance.Write($"Map {map.MapInfo.Name}: {worlds.Count} levels from {picked.Count} cells{(main ? " and its own level" : "")}, {meta.WorldFlags}");
        await WorldGate.WaitAsync(meta.CancellationToken);
        try
        {
            var session = new ExportSession(meta);
            var data = await Task.Run(() => session.RunAsync(() => worlds.Select(w => session.CreateExport(w.Name, w, EExportType.World, []))));
            if (meta.CancellationToken.IsCancellationRequested) return new { cancelled = true };
            return ExportPayload(data, settings);
        }
        finally
        {
            WorldGate.Release();
        }
    }

    private static int CancelMapExports(string? run)
    {
        // a run's exports only (another Blender's go on); no run: all of them
        if (!string.IsNullOrEmpty(run)) CancelledRuns[run] = 0;
        var metas = MapExports.Where(e => string.IsNullOrEmpty(run) || e.Value == run).Select(e => e.Key).ToList();
        foreach (var meta in metas)
        {
            try { meta.Cancel(); }
            catch (ObjectDisposedException) { }     // that export just ended
        }
        return metas.Count;
    }

    private static async Task<object?> MapsImagesAsync(string id, string[] kinds)
    {
        var map = await FindMap(id);
        var files = new List<object>();
        var folder = Path.Combine(map.MapsFolder.FullName, map.WorldName);
        foreach (var kind in kinds.Select(k => k.Trim().ToLowerInvariant()).Distinct())
        {
            if (kind == "minimap")
            {
                var path = await OnUI(() => map.MapBitmap is null ? null : MapImage(map));
                if (path is not null) files.Add(new { kind, path });
                continue;
            }
            var type = Enum.Parse<EMapTextureExportType>(kind, true);
            var since = DateTime.Now.AddSeconds(-1);
            await Task.Run(() => map.ExportLandscapeMaps(type, open: false));
            if (!Directory.Exists(folder)) continue;
            files.AddRange(Directory.EnumerateFiles(folder, Capitalized(kind) + "_*.png", SearchOption.AllDirectories)
                .Where(f => File.GetLastWriteTime(f) >= since).Select(f => new { kind, path = f }));
        }
        return new { files };
    }

    private static string Capitalized(string s) => char.ToUpperInvariant(s[0]) + s[1..];
}
