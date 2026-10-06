using System;
using System.Collections.Concurrent;

namespace FortnitePorting.Exporting.MaterialPorter;

/// <summary>
/// Material Porter fork: a failure the code steps over (an item that won't read: the next one), logged
/// as a warning - the first few of each place, then one line that the rest of that place's go unlogged
/// (an asset listing reads thousands of packages: an unmounted plugin's would each fail).
/// </summary>
public static class Failures
{
    const int PerPlace = 3;
    static readonly ConcurrentDictionary<string, int> Seen = new();

    public static void Note(string place, string what, Exception e)
    {
        var count = Seen.AddOrUpdate(place, 1, (_, n) => n + 1);
        if (count <= PerPlace)
            Serilog.Log.Warning("[Material Porter] {Place}: {What}: {Error}", place, what, e.Message);
        else if (count == PerPlace + 1)
            Serilog.Log.Warning("[Material Porter] {Place}: more failures, not logged", place);
    }
}
