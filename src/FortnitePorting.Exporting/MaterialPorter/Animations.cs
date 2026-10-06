using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;
using System.Threading.Tasks;
using CUE4Parse.FileProvider;
using CUE4Parse.UE4.Assets.Exports;
using CUE4Parse.UE4.Objects.UObject;

namespace FortnitePorting.Exporting.MaterialPorter;

/// <summary>
/// Material Porter fork: the Animations tab. The cooked asset registry lists few of the game's
/// animations (2,248 sequences, mostly islands'), so they are found by path (an animation folder, an
/// anim or montage in the name: 210,000 candidates of 2 million packages), each checked and outlined
/// from its package's export and import maps without being read: its class, its skeleton, what it
/// is for (<see cref="Kinds"/>, the tab's filters).
/// </summary>
public static partial class Animations
{
    public static readonly string[] Classes = ["AnimSequence", "AnimMontage"];

    /// <summary>What an animation is for, from its path and skeleton, the first that matches: the tab's filters.</summary>
    public static readonly (string Kind, Regex Match)[] Kinds =
    [
        ("Gliders", new Regex("glider", RegexOptions.IgnoreCase)),
        ("Back Blings", new Regex("backpack|backbling|/capes?/|_cape", RegexOptions.IgnoreCase)),
        ("Pickaxes", new Regex("pickaxe|/melee/", RegexOptions.IgnoreCase)),
        ("Emotes", new Regex("/emotes?/|/dances?/|emote_", RegexOptions.IgnoreCase)),
        ("Creatures", new Regex("junocreature|wildlife|creature|/animals?/|/ai/|/npcs?/", RegexOptions.IgnoreCase)),
        ("Pets", new Regex("companion|/pets?/|petcarrier", RegexOptions.IgnoreCase)),
        ("Vehicles", new Regex("vehicle|/cars?/|valet", RegexOptions.IgnoreCase)),
        ("Weapons", new Regex("/weapons?/|weapon", RegexOptions.IgnoreCase)),
        ("LEGO", new Regex("^/juno|figurecosmetics|lego|minifig|sk_figure", RegexOptions.IgnoreCase)),
        ("Characters", new Regex("mainplayer|/characters?/|/player/|player_skeleton|base_skeleton|female|male", RegexOptions.IgnoreCase)),
    ];

    [GeneratedRegex(@"/anim|anim_|_anim|montage|/emotes?/|/dances?/", RegexOptions.IgnoreCase)]
    private static partial Regex Candidate();

    public sealed record Outline(string Package, string? Class, string? Skeleton, string Kind) : Unloaded.IOutline
    {
        /// <summary>The item it belongs to, where one is found (<see cref="Assign"/>).</summary>
        public Owner? Owner { get; set; }

        public string Folder => Package[..Math.Max(0, Package.LastIndexOf('/'))];

        public string Describe(string folder) =>
            $"{(Owner is null ? "" : $"{Owner.Name}'s · ")}{(Class == "AnimMontage" ? "Montage" : "Sequence")}{(Skeleton is null ? "" : $" · {Skeleton}")} · {folder}";
    }

    /// <summary>
    /// An item animations belong to (a glider, a back bling, a pickaxe, an emote): its name and icon.
    /// An animation is its item's when it moves the skeleton of the item's mesh (a skeleton only that
    /// item's), or sits in the folder of the item's own animations (an emote's montage, a glider's
    /// rider animations beside the glider's).
    /// </summary>
    public sealed record Owner(string Name, string? Icon);

    // skeleton name -> its items, folder -> its items
    private static readonly ConcurrentDictionary<string, ConcurrentDictionary<Owner, byte>> _bySkeleton = new(StringComparer.OrdinalIgnoreCase);
    private static readonly ConcurrentDictionary<string, ConcurrentDictionary<Owner, byte>> _byFolder = new(StringComparer.OrdinalIgnoreCase);
    private const int SharedMost = 3;       // a skeleton or folder of more items than this is no item's (the pickaxes' shared melee skeleton)

    public static void ClearOwners()
    {
        _bySkeleton.Clear();
        _byFolder.Clear();
    }

    public static void OwnSkeleton(string skeleton, Owner owner) => _bySkeleton.GetOrAdd(skeleton, _ => new ConcurrentDictionary<Owner, byte>())[owner] = 0;

    public static void OwnFolder(string folder, Owner owner) => _byFolder.GetOrAdd(folder, _ => new ConcurrentDictionary<Owner, byte>())[owner] = 0;

    /// <summary>The item a skeleton or folder is of: one, or a few that share it (their names together, the first's icon).</summary>
    private static Owner? Of(ConcurrentDictionary<string, ConcurrentDictionary<Owner, byte>> map, string? key)
    {
        if (key is null || !map.TryGetValue(key, out var owners) || owners.IsEmpty || owners.Count > SharedMost) return null;
        var sorted = owners.Keys.OrderBy(o => o.Name, StringComparer.OrdinalIgnoreCase).ToList();
        return sorted.Count == 1 ? sorted[0] : new Owner(string.Join(" / ", sorted.Select(o => o.Name).Distinct()), sorted.FirstOrDefault(o => o.Icon is not null)?.Icon);
    }

    private static string Up(string folder) => folder[..Math.Max(0, folder.LastIndexOf('/'))];

    /// <summary>
    /// Each animation's item: by its skeleton, else by its folder (or the folder above); then the
    /// animations beside or under one found so (a glider's rider's, under the glider's own) take its
    /// item too.
    /// </summary>
    public static void Assign(IReadOnlyList<Unloaded.IOutline> outlines)
    {
        var all = outlines.OfType<Outline>().ToList();
        var beside = new Dictionary<string, Owner?>(StringComparer.OrdinalIgnoreCase);
        foreach (var o in all)
        {
            o.Owner = Of(_bySkeleton, o.Skeleton) ?? Of(_byFolder, o.Folder) ?? Of(_byFolder, Up(o.Folder));
            if (o.Owner is not null)
                beside[o.Folder] = beside.TryGetValue(o.Folder, out var had) && had != o.Owner ? null : o.Owner;
        }
        foreach (var o in all)
        {
            if (o.Owner is not null) continue;
            var folder = o.Folder;
            for (var up = 0; up < 3 && folder.Length > 0; up++, folder = Up(folder))
                if (beside.TryGetValue(folder, out var owner))
                {
                    o.Owner = owner;
                    break;         // (one shared by several items above stays none)
                }
        }
    }

    /// <summary>
    /// An animation's name in the tab: its own (underscores as spaces: what a tile shows, under its
    /// item's icon), then its item's, where it has one (what the search finds it by).
    /// </summary>
    public static string DisplayName(UObject animation)
    {
        var outline = Unloaded.Detail(animation) as Outline;
        var name = Short(animation.Name, outline?.Folder ?? "").Replace('_', ' ');
        return outline?.Owner is { } owner ? $"{name} · {owner.Name}" : name;
    }

    private static readonly string[] Prefixes = ["AM_", "AS_", "A_", "Anim_", "Emote_"];

    /// <summary>
    /// An animation's name without what its folders already say (a tile shows a dozen letters):
    /// "Maverick_Closed_GLIDER" in .../ParaGlide/Maverick is "Closed_GLIDER", "Emote_Prance_CLF" in
    /// .../Emotes/Prance "CLF". The whole name stays the object's, which the search also finds.
    /// </summary>
    private static string Short(string name, string folder)
    {
        var shorter = name;
        foreach (var prefix in Prefixes)
            if (shorter.StartsWith(prefix, StringComparison.OrdinalIgnoreCase) && shorter.Length > prefix.Length)
                shorter = shorter[prefix.Length..];
        var names = folder.Split('/', StringSplitOptions.RemoveEmptyEntries).Reverse().Take(4);
        foreach (var part in names)
            if (shorter.StartsWith(part + "_", StringComparison.OrdinalIgnoreCase) && shorter.Length > part.Length + 1)
            {
                shorter = shorter[(part.Length + 1)..];
                break;
            }
        return shorter;
    }

    public static string? IconPath(UObject animation) => (Unloaded.Detail(animation) as Outline)?.Owner?.Icon;

    public static bool Owned(UObject animation) => (Unloaded.Detail(animation) as Outline)?.Owner is not null;

    /// <summary>The packages that may be animations, by path: (package path as the game mounts it, object name).</summary>
    public static List<(string Package, string Name)> Candidates(IFileProvider provider, IEnumerable<(string Package, string Name)> registry)
    {
        var found = new Dictionary<string, (string, string)>(StringComparer.OrdinalIgnoreCase);
        foreach (var (package, name) in registry) found.TryAdd(package, (package, name));
        foreach (var path in provider.Files.Keys)
        {
            if (!path.EndsWith(".uasset", StringComparison.OrdinalIgnoreCase) || path.EndsWith(".o.uasset", StringComparison.OrdinalIgnoreCase)
                || !Candidate().IsMatch(path)) continue;
            var package = Effects.MountPath(path[..^".uasset".Length]);
            found.TryAdd(package, (package, package[(package.LastIndexOf('/') + 1)..]));
        }
        return found.Values.ToList();
    }

    // the outlines of an earlier listing of the same files (reading 130,000 packages' maps takes over a minute)
    private static ConcurrentDictionary<string, Outline> _known = new(StringComparer.OrdinalIgnoreCase);
    private static string? _knownFile, _knownKey;

    /// <summary>The outlines kept in the file from a listing of the same files (key: what says they are the same).</summary>
    public static void Recall(string file, string key)
    {
        _knownFile = file;
        _knownKey = key;
        _known = new ConcurrentDictionary<string, Outline>(StringComparer.OrdinalIgnoreCase);
        try
        {
            if (!File.Exists(file)) return;
            using var reader = new StreamReader(file);
            if (reader.ReadLine() != key) return;
            while (reader.ReadLine() is { } line)
            {
                var f = line.Split('\t');
                if (f.Length == 4) _known[f[0]] = new Outline(f[0], f[1].Length > 0 ? f[1] : null, f[2].Length > 0 ? f[2] : null, KindOf(f[0], f[2]));
            }
        }
        catch (Exception e)
        {
            Serilog.Log.Warning("[Material Porter] animation listing cache unreadable, read again: {Error}", e.Message);
            _known.Clear();
        }
    }

    /// <summary>Keeps this listing's outlines for the next.</summary>
    public static void Remember()
    {
        if (_knownFile is null || _knownKey is null) return;
        try
        {
            using var writer = new StreamWriter(_knownFile);
            writer.WriteLine(_knownKey);
            foreach (var (package, o) in _known)
                writer.WriteLine($"{package}\t{o.Class}\t{o.Skeleton}\t{o.Kind}");
        }
        catch (Exception e)
        {
            Serilog.Log.Warning("[Material Porter] animation listing cache not saved: {Error}", e.Message);
            // (only the next listing is slower)
        }
    }

    /// <summary>An animation's outline from its package's maps: its class, its skeleton (the import of that class), what it is for.</summary>
    public static async Task<Outline?> ReadOutline(IFileProvider provider, string package, string name)
    {
        if (_known.TryGetValue(package, out var known)) return known;
        var outline = await ReadMaps(provider, package, name);
        if (outline is not null) _known[package] = outline;
        return outline;
    }

    private static async Task<Outline?> ReadMaps(IFileProvider provider, string package, string name)
    {
        try
        {
            var loaded = await provider.LoadPackageAsync(package);
            var index = loaded.GetExportIndex(name, StringComparison.OrdinalIgnoreCase);
            var type = index < 0 ? null : loaded.ResolvePackageIndex(new FPackageIndex(loaded, index + 1))?.Class?.Name.Text;
            if (type is null || !Classes.Contains(type)) return new Outline(package, type, null, "");
            string? skeleton = null;
            for (var i = 0; i < loaded.ImportMapLength && skeleton is null; i++)
                if (loaded.ResolvePackageIndex(new FPackageIndex(loaded, -(i + 1))) is { } import && import.Class?.Name.Text == "Skeleton")
                    skeleton = import.Name.Text;
            return new Outline(package, type, skeleton, KindOf(package, skeleton));
        }
        catch (Exception e)
        {
            Failures.Note("animation listing", package, e);
            return null;
        }
    }

    /// <summary>The skeleton a package's mesh or animation imports (its package's import map: nothing is read).</summary>
    public static async Task<string?> SkeletonOf(IFileProvider provider, string package)
    {
        try
        {
            var loaded = await provider.LoadPackageAsync(package);
            for (var i = 0; i < loaded.ImportMapLength; i++)
                if (loaded.ResolvePackageIndex(new FPackageIndex(loaded, -(i + 1))) is { } import && import.Class?.Name.Text == "Skeleton")
                    return import.Name.Text;
        }
        catch (Exception e)
        {
            Failures.Note("animation skeletons", package, e);
            // (none found)
        }
        return null;
    }

    /// <summary>What an animation is for, from its path and its skeleton's name.</summary>
    public static string KindOf(string package, string? skeleton)
    {
        var about = $"{package} {skeleton}";
        return Kinds.FirstOrDefault(k => k.Match.IsMatch(about)).Kind ?? "Other";
    }

    /// <summary>What the tab says of an animation listed unread.</summary>
    public static string Describe(UObject animation)
    {
        var path = animation.GetPathName();
        var folder = path[..Math.Max(0, path.LastIndexOf('/'))];
        return Unloaded.Detail(animation) is Outline outline ? outline.Describe(folder) : folder;
    }

    public static bool Is(UObject animation, string kind) => (Unloaded.Detail(animation) as Outline)?.Kind == kind;
}
