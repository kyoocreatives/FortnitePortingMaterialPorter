using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using CUE4Parse.FileProvider.Objects;
using CUE4Parse.FileProvider.Vfs;
using CUE4Parse.UE4.IO.Objects;
using Serilog;

namespace FortnitePorting.Exporting.MaterialPorter;

/// <summary>
/// For a build downloaded from its manifest, reads the export's packages (roots plus their imports, with bulk data)
/// from the CDN in parallel ahead of the export, so it finds their chunks cached.
/// </summary>
public static class Prefetch
{
    /// <summary>Set by the app when the files are downloaded (a local install has nothing to fetch).</summary>
    public static bool Enabled;

    const int Parallel = 12;
    const int Depth = 6;
    const int MostPackages = 20000;

    private static CancellationTokenSource? _running;

    /// <summary>Starts fetching (the last export's fetch stops): the packages named, and what they import.</summary>
    public static void Start(AbstractVfsFileProvider provider, IEnumerable<string> packageNames)
    {
        if (!Enabled || Environment.GetEnvironmentVariable("MATERIAL_PORTER_PREFETCH") == "0") return; // 0: off, for timing

        var cancel = new CancellationTokenSource();
        Interlocked.Exchange(ref _running, cancel)?.Cancel();
        var roots = packageNames.Where(n => !string.IsNullOrWhiteSpace(n)).Distinct(StringComparer.OrdinalIgnoreCase).ToList();
        _ = Task.Run(() => Run(provider, roots, cancel.Token));
    }

    private static void Run(AbstractVfsFileProvider provider, List<string> roots, CancellationToken cancel)
    {
        var watch = Stopwatch.StartNew();
        try
        {
            // roots first, then each level of their imports
            var seen = new HashSet<FPackageId>();
            var order = new List<GameFile>();
            var level = roots.Select(FPackageId.FromName).Where(seen.Add).ToList();
            for (var depth = 0; depth <= Depth && level.Count > 0 && order.Count < MostPackages; depth++)
            {
                var next = new List<FPackageId>();
                foreach (var id in level)
                {
                    if (provider.FilesById.TryGetValue(id, out var file)) order.Add(file);
                    if (provider.TryFindStoreEntry(id)?.ImportedPackages is { } imports)
                        next.AddRange(imports.Where(seen.Add));
                }
                level = next;
            }

            var files = order.SelectMany(file => new[] { file }.Concat(Bulk(provider, file))).ToList();
            var fetched = 0;
            System.Threading.Tasks.Parallel.ForEach(files,
                new ParallelOptions { MaxDegreeOfParallelism = Parallel, CancellationToken = cancel },
                file =>
                {
                    try
                    {
                        file.Read();
                        Interlocked.Increment(ref fetched);
                    }
                    catch (Exception e) when (e is not OperationCanceledException)
                    {
                        // the export reads it again and reports the error
                    }
                });
            Log.Information("[Material Porter] prefetched {Fetched} files ({Packages} packages) in {Seconds:0.0} s",
                fetched, order.Count, watch.Elapsed.TotalSeconds);
        }
        catch (OperationCanceledException)
        {
            Log.Information("[Material Porter] prefetch stopped after {Seconds:0.0} s: another export started", watch.Elapsed.TotalSeconds);
        }
        catch (Exception e)
        {
            Log.Warning("[Material Porter] prefetch failed: {Error}", e.Message);
        }
    }

    // bulk data (mesh LODs, texture mips) is in separate files
    private static IEnumerable<GameFile> Bulk(AbstractVfsFileProvider provider, GameFile package)
    {
        var path = package.PathWithoutExtension;
        foreach (var extension in new[] { ".ubulk", ".uptnl" })
            if (provider.Files.TryGetValue(path + extension, out var bulk))
                yield return bulk;
    }
}
