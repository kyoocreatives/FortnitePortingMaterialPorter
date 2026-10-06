using System;
using CUE4Parse.UE4.Assets.Exports;
using System.Collections.Generic;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using FortnitePorting.Exporting.MaterialPorter;
using Serilog;

namespace FortnitePorting.Models.Assets.Loading;

/// <summary>
/// Material Porter fork: assets listed unread, the asset registry's and others. The cooked registry
/// keeps few of the game's own Niagara systems (the Effects tab listed 1,379, nearly all islands');
/// the others are only reachable by file path, and holding them all read took over 40 GB.
/// </summary>
public partial class AssetLoader
{
    /// <summary>
    /// The packages to list, given the registry's entries of this loader's classes: (package path,
    /// object name). Each one whose object's class (read from the package's export map, with an
    /// outline of the system: Effects.Outline) is one of <see cref="ClassNames"/> is listed unread
    /// (<see cref="Unloaded"/>): the export reads it. The registry's entries aren't listed otherwise.
    /// </summary>
    public Func<IReadOnlyList<(string Package, string Name)>, IReadOnlyList<(string Package, string Name)>>? MPUnregistered;

    /// <summary>Before and after the listing (an outline cache to read, then write).</summary>
    public Func<Task>? MPBeforeListing;
    public Action? MPAfterListing;

    /// <summary>Once every package's outline is read, before the items are made: what needs them all (an owner found for one, given to its folder).</summary>
    public Action<IReadOnlyList<Unloaded.IOutline>>? MPOutlined;

    /// <summary>An item's icon's path, where the asset's own data can't say (one listed unread).</summary>
    public Func<UObject, string?>? MPIconPath;

    /// <summary>Each listed package's outline (its class first), from its package's maps.</summary>
    public Func<string, string, Task<Unloaded.IOutline?>> MPOutline = async (package, name) =>
        await Effects.ReadOutline(UEParse.Provider, package, name);

    private async Task LoadUnregistered(IReadOnlyList<(string Package, string Name)> packages, CancellationToken token)
    {
        if (packages.Count == 0) return;
        if (MPBeforeListing is not null) await MPBeforeListing();
        int start = LoadedAssets, done = 0;
        var found = new System.Collections.Concurrent.ConcurrentBag<(string Package, string Name, string Class, Unloaded.IOutline Outline)>();
        await Parallel.ForEachAsync(packages, new ParallelOptions { MaxDegreeOfParallelism = Math.Max(2, Environment.ProcessorCount / 2) },
            async (package, ct) =>
            {
                if (token.IsCancellationRequested) return;
                await WaitIfPausedAsync();
                try
                {
                    if (await MPOutline(package.Package, package.Name) is { Class: { } className } outline && ClassNames.Contains(className))
                        found.Add((package.Package, package.Name, className, outline));
                }
                catch (Exception e)
                {
                    Log.Error("{0}", e);
                }

                LoadedAssets = start + Interlocked.Increment(ref done);
            });
        if (token.IsCancellationRequested) return;
        MPOutlined?.Invoke(found.Select(f => f.Outline).ToList());
        foreach (var f in found)
            await LoadAsset(Unloaded.Create(f.Package, f.Name, f.Class, f.Outline));
        MPAfterListing?.Invoke();
    }
}
