using System.Collections.Generic;
using System.Linq;
using FortnitePorting.Models.Assets.Asset;

namespace FortnitePorting.Models.Assets.Filters;

public partial class FilterItem
{
    /// <summary>
    /// A group whose checked items match any of them (Swimming or Skydiving), where other filters must all match: for filters that
    /// exclude each other (an animation's one use), which together would match nothing.
    /// </summary>
    public string? MPAnyOf { get; init; }

    /// <summary>The checked filters' verdict: those of an <see cref="MPAnyOf"/> group any, the rest all.</summary>
    public static bool MPMatches(IEnumerable<FilterItem> filters, AssetItem item) =>
        filters.Where(f => f.MPAnyOf is null).All(f => f.Predicate.Invoke(item))
        && filters.Where(f => f.MPAnyOf is not null).GroupBy(f => f.MPAnyOf).All(group => group.Any(f => f.Predicate.Invoke(item)));
}
