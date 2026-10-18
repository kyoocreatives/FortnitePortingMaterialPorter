using System;
using System.Collections.Concurrent;
using System.IO;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using CUE4Parse.FileProvider.Objects;
using CUE4Parse.UE4.Assets;

namespace FortnitePorting.Models.CUE4Parse;

// Uncooked packages read from disk (a UEFN project's) resolve their exports' classes by loading the Blueprints they import,
// which import more: each is loaded once (one thread loads it, the others wait) and kept until its file changes, or one
// actor reloads a project's prefabs per import, and parallel readers each reload its map.
public partial class HybridFileProvider
{
    const long LooseCacheBytes = 768L << 20;      // dropped whole past this (texture packages are large)
    readonly ConcurrentDictionary<string, (DateTime Written, Lazy<IPackage> Package)> _loose = new(StringComparer.OrdinalIgnoreCase);
    long _looseBytes;

    public override IPackage LoadPackage(GameFile file) =>
        file is OsGameFile os ? Loose(os, () => base.LoadPackage(file)) : base.LoadPackage(file);

    // (the loose read is synchronous: blocking on base.LoadPackageAsync deadlocks when called from the UI thread)
    public override async Task<IPackage> LoadPackageAsync(GameFile file) =>
        file is OsGameFile os ? Loose(os, () => base.LoadPackage(file)) : await base.LoadPackageAsync(file);

    IPackage Loose(OsGameFile file, Func<IPackage> load)
    {
        var written = File.GetLastWriteTimeUtc(file.ActualFile.FullName);
        var entry = _loose.AddOrUpdate(file.Path,
            _ => Added(file, written, load),
            (_, kept) => kept.Written == written ? kept : Added(file, written, load));
        try
        {
            var package = entry.Package.Value;
            if (TexturesOnly(package) && _loose.TryRemove(new(file.Path, entry))) Interlocked.Add(ref _looseBytes, -file.Size);
            return package;
        }
        catch
        {
            _loose.TryRemove(new(file.Path, entry));     // a failed read isn't kept
            throw;
        }
    }

    // A texture's package imports nothing to resolve, and its decoded pixels are large: not kept
    static bool TexturesOnly(IPackage package) => package is Package { ExportMap.Length: > 0 } p &&
        p.ExportMap.Where(e => e.OuterIndex.IsNull).All(e => e.ClassIndex.IsImport &&
            p.ImportMap[-e.ClassIndex.Index - 1].ObjectName.Text.StartsWith("Texture", StringComparison.Ordinal));

    (DateTime, Lazy<IPackage>) Added(OsGameFile file, DateTime written, Func<IPackage> load)
    {
        if (Interlocked.Add(ref _looseBytes, file.Size) > LooseCacheBytes)
        {
            _loose.Clear();
            Interlocked.Exchange(ref _looseBytes, file.Size);
        }
        return (written, new Lazy<IPackage>(load, LazyThreadSafetyMode.ExecutionAndPublication));
    }
}
