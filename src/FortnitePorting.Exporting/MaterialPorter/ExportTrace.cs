using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.Linq;
using System.Runtime.CompilerServices;
using System.Threading;
using CUE4Parse_Conversion.Options;
using CUE4Parse.UE4.Assets.Exports;
using CUE4Parse.UE4.Assets.Exports.Actor;
using CUE4Parse.UE4.Assets.Exports.Animation;
using CUE4Parse.UE4.Assets.Exports.SkeletalMesh;
using CUE4Parse.UE4.Assets.Exports.Sound;
using CUE4Parse.UE4.Assets.Exports.StaticMesh;
using CUE4Parse.UE4.Assets.Exports.Texture;
using CUE4Parse.UE4.Objects.Engine.Animation;
using FortnitePorting.Exporting.Models;
using Serilog;

namespace FortnitePorting.Exporting.MaterialPorter;

/// <summary>An export step by step for the app's status log: the asset, each mesh, material and file, then a summary.</summary>
// Item lines are indented, so they don't take the status line. Past DetailLines they are summed up every few
// seconds instead: a map export writes thousands of files.
public sealed class ExportTrace
{
    const int DetailLines = 150;
    const int LevelLines = 40;
    const int AssetLines = 20;
    const long SumEveryMs = 3000;

    static readonly ConditionalWeakTable<ExportDataMeta, ExportTrace> Traces = new();

    public static ExportTrace Of(ExportDataMeta meta) => Traces.GetValue(meta, m => new ExportTrace(m));

    readonly ExportDataMeta _meta;
    readonly object _gate = new();
    readonly Stopwatch _clock = new();
    readonly List<string> _pending = [];
    readonly ConcurrentDictionary<string, byte> _seen = new(StringComparer.OrdinalIgnoreCase);
    string? _first;
    int _assets, _meshes, _materials, _textures, _animations, _sounds, _others, _onDisk, _failed, _levels;
    int _lines, _levelLines;
    long _lastSum;

    ExportTrace(ExportDataMeta meta) => _meta = meta;

    /// <summary>An asset's export begins (ExportSession.CreateExport).</summary>
    public void Asset(string name, EExportType type, int styles)
    {
        int n;
        lock (_gate)
        {
            if (_pending.Count == 0) Reset();
            _pending.Add(name);
            n = ++_assets;
            _first ??= name;
        }
        var line = styles > 0 ? $"Exporting {name} ({type}, {Count(styles, "style")})" : $"Exporting {name} ({type})";
        if (n <= AssetLines) Write(line);
        else Sum(line);     // a map's streamed levels come one world each
    }

    /// <summary>An asset's files are written (BaseExport.WaitForExports): the last one sums the export up.</summary>
    public void Waited(string name)
    {
        string what;
        lock (_gate)
        {
            if (!_pending.Remove(name) || _pending.Count > 0) return;
            what = _assets == 1 ? _first! : $"{_assets} assets";
        }
        var verb = _meta.CancellationToken.IsCancellationRequested ? "Cancelled" : "Exported";
        Write(string.Create(CultureInfo.InvariantCulture, $"{verb} {what}: {Totals()} in {_clock.Elapsed.TotalSeconds:0.0} s"));
    }

    /// <summary>A mesh converted (ExportContext.Mesh): its LODs and how long the conversion took.</summary>
    public void Mesh(UObject mesh, long startedAt)
    {
        if (!First(mesh)) return;
        Interlocked.Increment(ref _meshes);
        Item($"    mesh {mesh.Name}{Lods(mesh)} {Ms(startedAt)} ms");
    }

    public void Material(ExportMaterial material)
    {
        if (!_seen.TryAdd("material:" + material.Path, 0)) return;
        Interlocked.Increment(ref _materials);
        var parent = material.BaseMaterial is { } master && master.Name != material.Name ? $" ({master.Name})" : "";
        Item($"    material {material.Name}{parent}: {Count(material.Textures.Count, "texture")}, " +
             $"{Count(material.Scalars.Count, "scalar")}, {Count(material.Vectors.Count, "vector")}");
    }

    /// <summary>A file written by ExportContext.ExportAsync, timed; a failure is counted and rethrown.</summary>
    public void File(UObject asset, int textureCap, Action export)
    {
        var started = Stopwatch.GetTimestamp();
        try { export(); }
        catch (Exception e)
        {
            // a locked file is the same file being written by another export: FP skips it, not a failure
            if (e is not System.IO.IOException || (e.HResult & 0xFFFF) != 32) Interlocked.Increment(ref _failed);
            throw;
        }
        if (!First(asset)) return;
        Interlocked.Increment(ref KindCount(asset));
        Item($"    {Describe(asset, textureCap)} {Ms(started)} ms");
    }

    /// <summary>A file already exported at this size: counted, not written again.</summary>
    public string OnDisk(UObject asset, string result)
    {
        if (First(asset))
        {
            Interlocked.Increment(ref KindCount(asset));
            Interlocked.Increment(ref _onDisk);
        }
        return result;
    }

    /// <summary>A map level read (the map reader's per-level line).</summary>
    public void Level(string line)
    {
        Interlocked.Increment(ref _levels);
        if (Interlocked.Increment(ref _levelLines) <= LevelLines) Write(line);
        else Sum(line);
    }

    void Item(string line)
    {
        var n = Interlocked.Increment(ref _lines);
        if (n <= DetailLines)
        {
            Write(line);
            return;
        }
        if (n == DetailLines + 1) Write($"    … more steps summed up every {SumEveryMs / 1000} s");
        Sum(line);
    }

    // past the budget a line only reaches FP's debug log; the status log gets the totals now and then
    void Sum(string line)
    {
        Log.Debug("[Material Porter] {Line}", line);
        var now = _clock.ElapsedMilliseconds;
        var last = Interlocked.Read(ref _lastSum);
        if (now - last < SumEveryMs || Interlocked.CompareExchange(ref _lastSum, now, last) != last) return;
        Write(string.Create(CultureInfo.InvariantCulture, $"    … {Totals()} so far ({_clock.Elapsed.TotalSeconds:0} s)"));
    }

    string Totals()
    {
        var parts = new[]
        {
            (_levels, "level"), (_meshes, "mesh"), (_materials, "material"), (_textures, "texture"),
            (_animations, "animation"), (_sounds, "sound"), (_others, "other file"),
        }.Where(p => p.Item1 > 0).Select(p => Count(p.Item1, p.Item2)).ToList();
        var text = parts.Count > 0 ? string.Join(", ", parts) : "nothing new";
        if (_onDisk > 0) text += $" ({_onDisk} already on disk)";
        if (_failed > 0) text += $", {_failed} failed";
        return text;
    }

    void Reset()
    {
        _seen.Clear();
        _first = null;
        _assets = _meshes = _materials = _textures = _animations = _sounds = _others = _onDisk = _failed = _levels = 0;
        _lines = _levelLines = 0;
        _lastSum = 0;
        _clock.Restart();
    }

    bool First(UObject asset) => _seen.TryAdd(asset.GetPathName(), 0);

    ref int KindCount(UObject asset)
    {
        switch (asset)
        {
            case USkeletalMesh or UStaticMesh or USkeleton or ALandscapeProxy: return ref _meshes;
            case UTexture: return ref _textures;
            case UAnimationAsset: return ref _animations;
            case USoundWave: return ref _sounds;
            default: return ref _others;
        }
    }

    string Describe(UObject asset, int textureCap) => asset switch
    {
        USkeletalMesh or UStaticMesh => $"mesh {asset.Name}{Lods(asset)}",
        USkeleton => $"skeleton {asset.Name}",
        ALandscapeProxy => $"landscape {asset.Name}",
        UTexture texture => $"texture {asset.Name}{TextureSize(texture, textureCap)}",
        UAnimSequenceBase anim => string.Create(CultureInfo.InvariantCulture, $"animation {asset.Name} ({anim.SequenceLength:0.0} s)"),
        UPoseAsset => $"pose asset {asset.Name}",
        USoundWave => $"sound {asset.Name}",
        _ => $"{asset.ExportType} {asset.Name}",
    };

    /// <summary>" (3 LODs → LOD0)": how many the asset has and which the export settings keep.</summary>
    string Lods(UObject mesh)
    {
        var total = mesh switch
        {
            USkeletalMesh skeletal => skeletal.LODModels?.Length ?? 0,
            UStaticMesh staticMesh => staticMesh.RenderData?.LODs?.Length ?? 0,
            _ => 0,
        };
        if (total <= 1) return "";
        var kept = _meta.Settings.MeshQuality switch
        {
            EMeshQuality.Highest => "LOD0",
            EMeshQuality.Lowest => $"LOD{total - 1}",
            _ => "all",
        };
        return $" ({total} LODs → {kept})";
    }

    /// <summary>" 1024² (of 2048²)": the mip written (a size cap picks a smaller one) and the largest.</summary>
    static string TextureSize(UTexture texture, int cap)
    {
        try
        {
            var mips = texture.PlatformData?.Mips;
            var first = texture.GetFirstMipIndex();
            if (mips is not { Length: > 0 } || first < 0) return "";
            var used = cap > 0 && texture is not UTextureCube ? texture.GetMipIndexByMaxSize(cap) : first;
            if (used < first || used >= mips.Length) used = first;
            return used > first ? $" {Size(mips[used])} (of {Size(mips[first])})" : $" {Size(mips[first])}";
        }
        catch (Exception)
        {
            return "";     // a texture streamed in a way the mip list doesn't show
        }
    }

    static string Size(FTexture2DMipMap mip) => mip.SizeX == mip.SizeY ? $"{mip.SizeX}²" : $"{mip.SizeX}×{mip.SizeY}";

    static long Ms(long startedAt) => (long) Stopwatch.GetElapsedTime(startedAt).TotalMilliseconds;

    static string Count(int n, string what) => n == 1 ? $"1 {what}" : what.EndsWith("sh") ? $"{n} {what}es" : $"{n} {what}s";

    static void Write(string line) => Log.Information("[Material Porter] {Line}", line);
}
