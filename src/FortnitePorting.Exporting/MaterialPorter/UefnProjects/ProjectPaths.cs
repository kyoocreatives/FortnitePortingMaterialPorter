using System;
using System.Collections.Generic;

namespace FortnitePorting.Exporting.MaterialPorter;

/// <summary>Where local UEFN projects sit in the game's file provider: as game feature plugins,
/// "FortniteGame/Plugins/GameFeatures/<Mount>/Content/...", the keys CUE4Parse maps "/<Mount>/..." to (and back).</summary>
public static class ProjectPaths
{
    public const string GameFeatures = "FortniteGame/Plugins/GameFeatures/";

    /// <summary>The mounted projects' mounts (set when they're registered).</summary>
    public static IReadOnlySet<string> Mounts { get; set; } = new HashSet<string>();

    public static string Prefix(string mount) => GameFeatures + mount + "/";

    /// <summary>Whether a package path ("/Mount/A/B") or file key ("FortniteGame/Plugins/GameFeatures/Mount/...") is a project's.</summary>
    public static bool IsProject(string path)
    {
        if (string.IsNullOrEmpty(path)) return false;
        path = path.TrimStart('/');
        if (path.StartsWith(GameFeatures, StringComparison.OrdinalIgnoreCase)) path = path[GameFeatures.Length..];
        return Mounts.Contains(path.Split('/')[0]);
    }
}
