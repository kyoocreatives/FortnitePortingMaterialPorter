using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Serilog;

namespace FortnitePorting.MaterialPorter;

/// <summary>
/// The UEFN islands Fortnite downloaded (InstalledBundles): a folder per downloaded version, each with its own key. Two
/// versions of one island hold the same package paths and mount with the same read order, so which one a path read was
/// arbitrary (an island updated and unlocked again kept showing its old content). Only each island's newest version is
/// mounted, as Fortnite plays only that one. An island is its container id (the hash of its plugin's name, in the
/// plugin.utoc header).
/// </summary>
public static class IslandBundles
{
    private const string Magic = "-==--==--==--==-";

    /// <summary>The folders under `directory` holding an older version of an island that a newer folder also holds.</summary>
    public static HashSet<string> Stale(DirectoryInfo directory)
    {
        var versions = new List<(ulong Island, DateTime Time, string Folder)>();
        foreach (var toc in directory.EnumerateFiles("plugin.utoc", new EnumerationOptions { RecurseSubdirectories = true, IgnoreInaccessible = true }))
            if (ContainerId(toc.FullName) is { } island && toc.DirectoryName is { } folder)
                versions.Add((island, toc.LastWriteTimeUtc, folder));
        var stale = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (var island in versions.GroupBy(v => v.Island).Where(g => g.Count() > 1))
        {
            var ordered = island.OrderByDescending(v => v.Time).ToList();
            foreach (var old in ordered.Skip(1))
            {
                stale.Add(old.Folder);
                Log.Information("[Material Porter] island bundle {Old} ({OldTime:yyyy-MM-dd HH:mm}) left out: {New} ({NewTime:yyyy-MM-dd HH:mm}) is a newer version",
                    Path.GetFileName(old.Folder), old.Time.ToLocalTime(), Path.GetFileName(ordered[0].Folder), ordered[0].Time.ToLocalTime());
            }
        }
        return stale;
    }

    /// <summary>The container id in an IoStore TOC's header (bytes 56-63), or null when the file isn't one.</summary>
    private static ulong? ContainerId(string path)
    {
        try
        {
            using var stream = File.OpenRead(path);
            var header = new byte[64];
            if (stream.Read(header, 0, header.Length) < header.Length) return null;
            if (System.Text.Encoding.ASCII.GetString(header, 0, 16) != Magic) return null;
            return BitConverter.ToUInt64(header, 56);
        }
        catch (IOException)
        {
            return null;        // a bundle Fortnite is writing: mounted as before
        }
    }
}
