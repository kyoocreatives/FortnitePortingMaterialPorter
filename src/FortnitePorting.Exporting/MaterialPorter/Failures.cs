using System;
using System.Collections.Concurrent;

namespace FortnitePorting.Exporting.MaterialPorter;

/// <summary>Logs a failure the code steps over: the first few per place, then one line saying the rest go unlogged.</summary>
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
