using System;
using System.Collections.Concurrent;
using System.IO;
using System.Threading;
using System.Threading.Tasks;
using CUE4Parse.FileProvider.Objects;
using CUE4Parse.UE4.Assets;

namespace FortnitePorting.Models.CUE4Parse;

// Uncooked packages read from disk (a UEFN project's) resolve their exports' classes by loading the Blueprints they import,
// which import more: each is kept once read (until its file changes), or one actor reloads a project's prefabs per import.
public partial class HybridFileProvider
{
    const long LooseCacheBytes = 768L << 20;      // dropped whole past this (texture packages are large)
    readonly ConcurrentDictionary<string, (DateTime Written, IPackage Package)> _loose = new(StringComparer.OrdinalIgnoreCase);
    long _looseBytes;

    public override IPackage LoadPackage(GameFile file) =>
        file is OsGameFile os ? Loose(os, () => base.LoadPackage(file)) : base.LoadPackage(file);

    public override async Task<IPackage> LoadPackageAsync(GameFile file) =>
        file is OsGameFile os ? Loose(os, () => base.LoadPackageAsync(file).GetAwaiter().GetResult()) : await base.LoadPackageAsync(file);

    IPackage Loose(OsGameFile file, Func<IPackage> load)
    {
        var written = File.GetLastWriteTimeUtc(file.ActualFile.FullName);
        if (_loose.TryGetValue(file.Path, out var kept) && kept.Written == written) return kept.Package;
        var package = load();
        if (Interlocked.Add(ref _looseBytes, file.Size) > LooseCacheBytes)
        {
            _loose.Clear();
            Interlocked.Exchange(ref _looseBytes, file.Size);
        }
        _loose[file.Path] = (written, package);
        return package;
    }
}
