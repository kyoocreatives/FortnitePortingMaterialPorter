using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using CUE4Parse.FileProvider;
using CUE4Parse.FileProvider.Objects;
using CUE4Parse.UE4.Assets;
using CUE4Parse.UE4.Assets.Exports;
using CUE4Parse.UE4.Objects.Core.Math;
using CUE4Parse.UE4.Objects.UObject;
using Newtonsoft.Json;

namespace FortnitePorting.Exporting.MaterialPorter;

/// <summary>A UEFN project map's placed actors by cell. World Partition keeps each actor in its own package under
/// "Content/__ExternalActors__/<map path>/"; the ones the game doesn't show at start are left out of every cell, by reason.
/// Kept on disk until the actor packages change (Stamp).</summary>
public sealed class ProjectMapIndex
{
    public const int DefaultCellSize = 12800;
    public const int CurrentVersion = 2;        // 2: the unloaded layers' actors kept (Layers)

    public string Stamp { get; init; } = "";
    public int CellSize { get; init; } = DefaultCellSize;
    /// <summary>"x_y" (floor of the actor's root location / CellSize) -> its actor package keys.</summary>
    public Dictionary<string, List<string>> Cells { get; init; } = new();
    public Dictionary<string, int> Skipped { get; init; } = new();
    /// <summary>Runtime data layers that start unloaded (the map's events, which Verse loads): layer name -> "x_y" -> actor
    /// package keys. Exported only when picked; their actors are counted in Skipped too.</summary>
    public Dictionary<string, Dictionary<string, List<string>>> Layers { get; init; } = new();
    public int Version { get; init; }

    /// <summary>Every cell holding something: the map's own and its layers'.</summary>
    public IEnumerable<string> AllCells() => Cells.Keys.Concat(Layers.Values.SelectMany(l => l.Keys)).Distinct();
    [JsonIgnore] public double Seconds { get; private set; }

    static readonly HashSet<string> WorldInfo = new(StringComparer.Ordinal)
        { "WorldDataLayers", "WorldPartitionMiniMap", "LevelBounds", "WorldSettings", "FortWorldSettings" };

    /// <summary>The map's actor package keys (sorted), from its package path ("/Mount/Maps/M").</summary>
    public static IReadOnlyList<string> ActorPackages(IFileProvider provider, string mapPath)
    {
        var map = provider.FixPath(mapPath + ".umap");
        var at = map.IndexOf("/Content/", StringComparison.OrdinalIgnoreCase);
        if (at < 0) return [];
        var content = map[..(at + "/Content/".Length)];
        var prefix = content + "__ExternalActors__/" + map[content.Length..map.LastIndexOf('.')] + "/";
        return provider.Files.Keys
            .Where(k => k.StartsWith(prefix, StringComparison.OrdinalIgnoreCase) && k.EndsWith(".uasset", StringComparison.OrdinalIgnoreCase))
            .Select(k => k[..^".uasset".Length])
            .OrderBy(k => k, StringComparer.OrdinalIgnoreCase)
            .ToList();
    }

    /// <summary>How many actor packages, and the newest one's write time: an edit in UEFN changes it.</summary>
    public static string StampOf(IFileProvider provider, IReadOnlyList<string> keys)
    {
        var newest = 0L;
        foreach (var key in keys)
            if (provider.Files.TryGetValue(key + ".uasset", out var f) && f is OsGameFile os)
                newest = Math.Max(newest, File.GetLastWriteTimeUtc(os.ActualFile.FullName).Ticks);
        return keys.Count + "-" + newest;
    }

    /// <summary>Where a map's index is kept.</summary>
    public static string CacheFile(string cacheDir, string mapPath) =>
        Path.Combine(cacheDir, string.Join("~", mapPath.Split('/', StringSplitOptions.RemoveEmptyEntries)) + ".json");

    /// <summary>The map's index: the kept one when its stamp still matches, else read again (and kept).</summary>
    public static async Task<ProjectMapIndex> GetAsync(IFileProvider provider, string mapPath, string cacheDir, CancellationToken ct)
    {
        var sw = Stopwatch.StartNew();
        var keys = ActorPackages(provider, mapPath);
        var stamp = StampOf(provider, keys);
        var file = CacheFile(cacheDir, mapPath);
        if (await KeptAsync(file, ct) is { } kept && kept.Stamp == stamp && kept.Version == CurrentVersion)
        {
            kept.Seconds = sw.Elapsed.TotalSeconds;
            return kept;
        }
        var index = await Task.Run(() => Build(provider, keys, stamp, ct), ct);
        Directory.CreateDirectory(cacheDir);
        var written = file + "." + Environment.ProcessId + ".tmp";
        await File.WriteAllTextAsync(written, JsonConvert.SerializeObject(index), ct);
        File.Move(written, file, overwrite: true);
        index.Seconds = sw.Elapsed.TotalSeconds;
        return index;
    }

    // The kept index, or null when there's none or it can't be read (cut short: read again)
    static async Task<ProjectMapIndex?> KeptAsync(string file, CancellationToken ct)
    {
        try
        {
            return File.Exists(file) ? JsonConvert.DeserializeObject<ProjectMapIndex>(await File.ReadAllTextAsync(file, ct)) : null;
        }
        catch (Exception e) when (e is JsonException or IOException)
        {
            return null;
        }
    }

    sealed record Actor(string Key, string Type, FVector Location, bool EditorOnly, string[] Layers);

    static ProjectMapIndex Build(IFileProvider provider, IReadOnlyList<string> keys, string stamp, CancellationToken ct)
    {
        var actors = new Actor?[keys.Count];
        var unreadable = 0;
        // layer instances (in the WorldDataLayers actor): layer path -> starts activated
        var activated = new ConcurrentDictionary<string, bool>(StringComparer.OrdinalIgnoreCase);
        Parallel.For(0, keys.Count, new ParallelOptions { CancellationToken = ct }, i =>
        {
            try
            {
                // only the exports read here are deserialized (an actor's components resolve their classes' Blueprints)
                if (provider.LoadPackage(keys[i]) is not Package package) { Interlocked.Increment(ref unreadable); return; }
                IPackage lazy = package;
                UObject? actor = null;
                for (var x = 0; x < package.ExportMap.Length; x++)
                {
                    var export = package.ExportMap[x];
                    if (ClassName(package, export.ClassIndex) == "DataLayerInstanceWithAsset")
                        Activated(lazy.GetExport(x), activated);
                    else if (actor == null && export.OuterIndex is { IsImport: true } outer
                             && package.ImportMap[-outer.Index - 1].ObjectName.Text == "PersistentLevel")
                        actor = lazy.GetExport(x);
                }
                if (actor == null) { Interlocked.Increment(ref unreadable); return; }
                var location = actor.TryGetValue(out FPackageIndex root, "RootComponent") && root.Load() is { } component
                    ? component.GetOrDefault("RelativeLocation", FVector.ZeroVector) : FVector.ZeroVector;
                var layers = actor.TryGetValue(out FSoftObjectPath[] paths, "DataLayerAssets")
                    ? paths.Select(p => Layer(p.AssetPathName.Text)).ToArray() : [];
                actors[i] = new Actor(keys[i], actor.ExportType, location, actor.GetOrDefault("bIsEditorOnlyActor", false), layers);
            }
            catch (Exception e) when (e is not OperationCanceledException)
            {
                Interlocked.Increment(ref unreadable);
            }
        });

        // a layer hides its actors unless it's a runtime layer that starts activated (no state: unloaded)
        var runtime = new Dictionary<string, bool>(StringComparer.OrdinalIgnoreCase);
        bool Hidden(string layer)
        {
            if (!activated.TryGetValue(layer, out var on)) return false;     // not this world's layer
            if (!runtime.TryGetValue(layer, out var isRuntime))
                runtime[layer] = isRuntime = !(provider.TryLoadPackageObject(layer, out var asset)
                                               && asset.TryGetValue(out FName type, "DataLayerType") && type.Text.EndsWith("Editor", StringComparison.Ordinal));
            return isRuntime && !on;
        }

        var index = new ProjectMapIndex { Stamp = stamp, Version = CurrentVersion };
        if (unreadable > 0) index.Skipped["unreadable"] = unreadable;
        foreach (var a in actors)
        {
            if (a == null) continue;
            var reason = a.Type.StartsWith("WorldPartitionHLOD", StringComparison.Ordinal) ? "hlod actors"
                : a.Type.EndsWith("Volume", StringComparison.Ordinal) ? "volumes"
                : WorldInfo.Contains(a.Type) ? "world info"
                : a.EditorOnly ? "editor-only actors"
                : a.Layers.Any(Hidden) ? "unloaded data layers"
                : null;
            var cell = $"{(int) Math.Floor(a.Location.X / index.CellSize)}_{(int) Math.Floor(a.Location.Y / index.CellSize)}";
            if (reason != null)
            {
                index.Skipped[reason] = index.Skipped.GetValueOrDefault(reason) + 1;
                if (reason == "unloaded data layers")
                    foreach (var layer in a.Layers.Where(Hidden).Select(l => l[(l.LastIndexOf('/') + 1)..]).Distinct())
                    {
                        if (!index.Layers.TryGetValue(layer, out var byCell)) index.Layers[layer] = byCell = new();
                        if (!byCell.TryGetValue(cell, out var inCell)) byCell[cell] = inCell = [];
                        inCell.Add(a.Key);
                    }
                continue;
            }
            if (!index.Cells.TryGetValue(cell, out var list)) index.Cells[cell] = list = [];
            list.Add(a.Key);
        }
        return index;
    }

    // A layer instance of the WorldDataLayers actor: its layer, and whether it starts activated (no state: unloaded).
    static void Activated(UObject? instance, ConcurrentDictionary<string, bool> activated)
    {
        if (instance == null || !instance.TryGetValue(out FPackageIndex asset, "DataLayerAsset") || asset.ResolvedObject is not { } layer) return;
        activated[Layer(layer.GetPathName())] = instance.TryGetValue(out FName state, "InitialRuntimeState")
                                                && state.Text.EndsWith("Activated", StringComparison.Ordinal);
    }

    static string ClassName(Package package, FPackageIndex index) =>
        index.IsImport ? package.ImportMap[-index.Index - 1].ObjectName.Text
        : index.IsExport ? package.ExportMap[index.Index - 1].ObjectName.Text : "";

    // "/Mount/Layers/L.L" or "FortniteGame/Plugins/GameFeatures/Mount/Content/Layers/L.L" -> "/Mount/Layers/L"
    static string Layer(string path)
    {
        var dot = path.LastIndexOf('.');
        if (dot > path.LastIndexOf('/')) path = path[..dot];
        if (path.StartsWith(ProjectPaths.GameFeatures, StringComparison.OrdinalIgnoreCase))
        {
            var rest = path[ProjectPaths.GameFeatures.Length..];
            var mount = rest.Split('/')[0];
            var content = rest.IndexOf("/Content/", StringComparison.OrdinalIgnoreCase);
            path = "/" + mount + "/" + (content >= 0 ? rest[(content + "/Content/".Length)..] : rest[(mount.Length + 1)..]);
        }
        return path;
    }
}
