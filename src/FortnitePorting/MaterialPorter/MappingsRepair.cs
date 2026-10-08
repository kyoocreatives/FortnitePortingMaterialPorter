using System;
using System.Linq;
using System.Threading;
using CUE4Parse.MappingsProvider;
using CUE4Parse.MappingsProvider.Usmap;
using Serilog;

namespace FortnitePorting.MaterialPorter;

/// <summary>Restores real classes that some builds' mappings shadow with editor template structs.</summary>
// 38.00's mappings hold template structs named like real classes (ActorComponent, Skeleton, PhysicsAsset).
// CUE4Parse keeps the last struct of a name, so the real class is lost and every component fails with
// "Unknown property". The file is re-read keeping every struct, and such names go back to the real class.
public static class MappingsRepair
{
    public static void Repair(ITypeMappingsProvider provider, string path)
    {
        if (provider.MappingsForGame is not { } mappings) return;
        try
        {
            var all = new UsmapParser(path, comparer: new EveryOneApart()).Mappings;
            if (all is null) return;

            // only an empty struct of another parent hiding one with properties; other shared names
            // (each Anim Blueprint's generated data, Verse tuples) keep the struct CUE4Parse kept
            var repaired = all.Types.Values
                .GroupBy(s => s.Name, StringComparer.Ordinal)
                .Where(g => g.Count() > 1)
                .Select(g => g.MaxBy(s => s.PropertyCount)!)
                .Where(real => mappings.Types.TryGetValue(real.Name, out var kept) && kept.PropertyCount == 0
                               && real.PropertyCount > 0 && kept.SuperType != real.SuperType)
                .ToList();
            foreach (var real in repaired)
                mappings.Types[real.Name] = new Struct(mappings, real.Name, real.SuperType, real.Properties, real.PropertyCount);

            if (repaired.Count > 0)
                Log.Warning("[Material Porter] mappings repaired: {Classes} were hidden by same-named structs",
                    string.Join(", ", repaired.Select(s => s.Name)));
        }
        catch (Exception e)
        {
            Log.Warning("[Material Porter] mappings not checked for hidden classes: {Error}", e.Message);
        }
    }

    // a comparer under which no two names are equal, so every struct of the file is kept
    private sealed class EveryOneApart : StringComparer
    {
        private int _next;

        public override int Compare(string? x, string? y) => string.CompareOrdinal(x, y);
        public override bool Equals(string? x, string? y) => false;
        public override int GetHashCode(string obj) => Interlocked.Increment(ref _next);
    }
}
