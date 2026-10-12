using System;
using System.Collections.Concurrent;
using System.IO;
using System.Threading.Tasks;

namespace FortnitePorting.Exporting.MaterialPorter;

/// <summary>
/// Outlines read from package maps for a tab listed unread (Animations, Effects), kept in a file per game build: a later listing reads
/// only the packages added since. Keyed on the build, not on file counts: Fortnite's on-demand content changes those during play,
/// which threw the whole cache away (over a minute to list again).
/// </summary>
public sealed class OutlineCache<T>(string what, Func<string[], T?> parse, Func<T, string> write) where T : class
{
    private ConcurrentDictionary<string, T> _known = new(StringComparer.OrdinalIgnoreCase);
    private string? _file, _key;

    /// <summary>The key of a build: its version, or the file counts when the version is unknown.</summary>
    public static string KeyOf(string buildVersion, int files, int vfs) =>
        buildVersion is { Length: > 0 } and not "unknown" ? buildVersion : $"{files} {vfs}";

    /// <summary>Outlines saved from an earlier listing of the same build (a line per package: the package, then the outline's fields).</summary>
    public void Recall(string file, string key)
    {
        _file = file;
        _key = key;
        _known = new ConcurrentDictionary<string, T>(StringComparer.OrdinalIgnoreCase);
        try
        {
            if (!File.Exists(file)) return;
            using var reader = new StreamReader(file);
            if (reader.ReadLine() != key) return;
            while (reader.ReadLine() is { } line)
            {
                var fields = line.Split('\t');
                if (fields.Length > 1 && parse(fields) is { } outline) _known[fields[0]] = outline;
            }
        }
        catch (Exception e)
        {
            Serilog.Log.Warning("[Material Porter] {What} listing cache unreadable, read again: {Error}", what, e.Message);
            _known.Clear();
        }
    }

    /// <summary>Keeps this listing's outlines for the next one.</summary>
    public void Remember()
    {
        if (_file is null || _key is null) return;
        try
        {
            using var writer = new StreamWriter(_file);
            writer.WriteLine(_key);
            foreach (var (package, outline) in _known)
                writer.WriteLine($"{package}\t{write(outline)}");
        }
        catch (Exception e)
        {
            Serilog.Log.Warning("[Material Porter] {What} listing cache not saved: {Error}", what, e.Message);
            // only the next listing is slower
        }
    }

    /// <summary>A package's outline: kept, else read and kept.</summary>
    public async Task<T?> Get(string package, Func<Task<T?>> read)
    {
        if (_known.TryGetValue(package, out var known)) return known;
        var outline = await read();
        if (outline is not null) _known[package] = outline;
        return outline;
    }
}
