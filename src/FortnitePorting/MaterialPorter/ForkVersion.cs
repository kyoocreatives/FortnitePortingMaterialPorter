using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.RegularExpressions;

namespace FortnitePorting.MaterialPorter;

/// <summary>The fork's two-part version: 4.3.4-mp.1.12.3 is upstream FP 4.3.4 and Material Porter 1.12.3.</summary>
// The bridge tag form v4.3.4-mp.154-1.13.0 stays readable by apps from before this numbering, which want a whole number after "mp.".
public static partial class ForkVersion
{
    [GeneratedRegex(@"^v?(\d+\.\d+\.\d+)-mp\.(?:\d+-)?(\d+\.\d+\.\d+)$")]
    private static partial Regex Pattern();

    /// <summary>The Material Porter version, null for dev builds and the old mp.N numbering.</summary>
    public static Version? MaterialPorter(string version) =>
        Pattern().Match(version) is { Success: true } match ? new Version(match.Groups[2].Value) : null;

    /// <summary>The tag with the highest Material Porter version, null when none has one.</summary>
    public static string? Newest(IEnumerable<string> tags) =>
        tags.Where(tag => MaterialPorter(tag) is not null).MaxBy(MaterialPorter);

    /// <summary>fpv-4.3.4/mpv-1.12.3, or the version as given when it has no Material Porter part.</summary>
    public static string Display(string version) =>
        Pattern().Match(version) is { Success: true } match ? $"fpv-{match.Groups[1].Value}/mpv-{match.Groups[2].Value}" : version;
}
