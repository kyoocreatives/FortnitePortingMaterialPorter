using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;
using System.Threading.Tasks;
using CUE4Parse.UE4.Assets.Exports;
using CUE4Parse.UE4.Objects.Core.i18N;
using CUE4Parse.UE4.Objects.UObject;
using FortnitePorting.Application;
using FortnitePorting.Exporting.MaterialPorter;

namespace FortnitePorting.MaterialPorter;

/// <summary>Owner item (name, icon, kind) of each Effects-tab system, and what a system is for: the tab's filters.</summary>
// An item's effects are found as its page's Effects switch finds them (Effects.OwnSystems), a contrail's by its item property.
// Cached in a file: the first run reads every cosmetic and weapon.
public static class EffectOwners
{
    public sealed record Owner(string Name, string? Icon, string Kind);

    private static readonly (string[] Classes, EExportType Type, string Kind)[] Sources =
    [
        (["AthenaCharacterItemDefinition"], EExportType.Outfit, "Outfits"),
        (["AthenaBackpackItemDefinition"], EExportType.Backpack, "Back Blings"),
        (["AthenaPickaxeItemDefinition"], EExportType.Pickaxe, "Pickaxes"),
        (["AthenaGliderItemDefinition"], EExportType.Glider, "Gliders"),
        ([Effects.ContrailClass], EExportType.Contrail, "Contrails"),
        (["ExtractableItemDefinition"], EExportType.Sprite, "Sprites"),
        (["AthenaGadgetItemDefinition", "FortWeaponRangedItemDefinition", "FortWeaponMeleeItemDefinition", "FortCreativeWeaponMeleeItemDefinition",
          "FortCreativeWeaponRangedItemDefinition", "FortWeaponMeleeDualWieldItemDefinition"], EExportType.Item, "Weapons"),
    ];

    /// <summary>What an unowned system is for, from its path, first match wins (the owned ones take their item's kind).</summary>
    private static readonly (string Kind, Regex Match)[] PathKinds =
    [
        ("Gliders", new Regex("glider", RegexOptions.IgnoreCase)),
        ("Back Blings", new Regex("backpack|backbling|/capes?/", RegexOptions.IgnoreCase)),
        ("Pickaxes", new Regex("pickaxe|/melee/", RegexOptions.IgnoreCase)),
        ("Contrails", new Regex("contrail|skydive", RegexOptions.IgnoreCase)),
        ("Emotes", new Regex("/emotes?/|/dances?/|emote_", RegexOptions.IgnoreCase)),
        ("Weapons", new Regex("/weapons?/|weapon|/gadgets?/", RegexOptions.IgnoreCase)),
        ("Vehicles", new Regex("vehicle|/cars?/|valet", RegexOptions.IgnoreCase)),
        ("Creatures", new Regex("wildlife|creature|/animals?/|/npcs?/|/ai/", RegexOptions.IgnoreCase)),
        ("LEGO", new Regex("^/juno|figurecosmetics|lego", RegexOptions.IgnoreCase)),
        ("Outfits", new Regex("/characters?/|/heroes/|/bodies/|/heads/", RegexOptions.IgnoreCase)),
        ("Environment", new Regex("/environments?/|/props?/|/world/|/building|foliage|weather|/maps?/", RegexOptions.IgnoreCase)),
    ];

    /// <summary>The tab's kind filters, in order.</summary>
    public static readonly string[] Kinds = PathKinds.Select(k => k.Kind).Append("Sprites").Distinct().Append("Other").ToArray();

    private const int SharedMost = 3; // more items than this on one system: it is no item's (a weapon family's shared muzzle flash)

    // package (as mounted) -> its items
    private static readonly ConcurrentDictionary<string, ConcurrentDictionary<Owner, byte>> _byPackage = new(StringComparer.OrdinalIgnoreCase);

    /// <summary>The items' index, kept in a file per build: a later listing reads only the items added since
    /// (on-demand content and hotfixes add some), not every cosmetic and weapon again.</summary>
    public static async Task Build(string file, string build)
    {
        _byPackage.Clear();
        var items = UEParse.AssetRegistry.Select(a => (Data: a, Source: Sources.FirstOrDefault(s => s.Classes.Contains(a.AssetClass.Text))))
            .Where(x => x.Source.Classes is not null).ToList();
        var key = $"4 {build}";
        var known = Recall(file, key);
        var fresh = known is null ? items : items.Where(x => !known.Contains(x.Data.ObjectPath)).ToList();
        if (fresh.Count == 0) return;
        var found = new ConcurrentBag<(string Package, Owner Owner)>();
        await Parallel.ForEachAsync(fresh, new ParallelOptions { MaxDegreeOfParallelism = Math.Max(2, Environment.ProcessorCount / 2) }, async (x, _) =>
        {
            try
            {
                if (await UEParse.Provider.SafeLoadPackageObjectAsync(x.Data.ObjectPath) is not { } item) return;
                IEnumerable<UObject?> systems = x.Source.Type is EExportType.Contrail
                    ? [Effects.Shown(item.GetOrDefault<FSoftObjectPath>(Effects.ContrailEffect))]
                    : Effects.OwnSystems(item, x.Source.Type);
                var name = item.GetAnyOrDefault<FText?>("DisplayName", "ItemName")?.Text is { Length: > 0 } shown ? shown : item.Name;
                // the icon its own tab shows (a pickaxe's can be its weapon's, an outfit's its hero's)
                var icon = AppServices.AssetLoading.Get(x.Source.Type).LowResIconHandler(item);
                var owner = new Owner(name, icon?.GetPathName(), x.Source.Kind);
                foreach (var system in systems.OfType<UObject>())
                    found.Add((Package(system.GetPathName()), owner));
            }
            catch (Exception e)
            {
                Exporting.MaterialPorter.Failures.Note("effect owners", x.Data.ObjectPath, e);
            }
        });
        foreach (var (package, owner) in found) Own(package, owner);
        Serilog.Log.Information("[Material Porter] effect owners: {Count} item(s) read", fresh.Count);
        Save(file, key, fresh.Select(x => x.Data.ObjectPath), found, append: known is not null);
    }

    // the file: the key, then "item<TAB>path" for each item read (failed ones too: not retried) and
    // "package<TAB>name<TAB>icon<TAB>kind" for each system an item owns
    private static void Save(string file, string key, IEnumerable<string> items, IEnumerable<(string Package, Owner Owner)> found, bool append)
    {
        try
        {
            using var writer = new StreamWriter(file, append);
            if (!append) writer.WriteLine(key);
            foreach (var item in items) writer.WriteLine($"item\t{item}");
            foreach (var (package, owner) in found)
                writer.WriteLine($"{package}\t{owner.Name}\t{owner.Icon}\t{owner.Kind}");
        }
        catch (Exception e)
        {
            Serilog.Log.Warning("[Material Porter] effect owners cache not saved: {Error}", e.Message);
        }
    }

    /// <summary>The index kept for this build; returns the items it has read (null: none kept, read them all).</summary>
    private static HashSet<string>? Recall(string file, string key)
    {
        try
        {
            if (!File.Exists(file)) return null;
            using var reader = new StreamReader(file);
            if (reader.ReadLine() != key) return null;
            var known = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            var owners = new Dictionary<(string, string, string), Owner>();
            while (reader.ReadLine() is { } line)
            {
                var f = line.Split('\t');
                if (f.Length == 2 && f[0] == "item") known.Add(f[1]);
                if (f.Length != 4) continue;
                // share one owner object per item
                var owner = owners.TryGetValue((f[1], f[2], f[3]), out var had) ? had : owners[(f[1], f[2], f[3])] = new Owner(f[1], f[2].Length > 0 ? f[2] : null, f[3]);
                Own(f[0], owner);
            }
            return known;
        }
        catch (Exception e)
        {
            Serilog.Log.Warning("[Material Porter] effect owners cache unreadable, read again: {Error}", e.Message);
            _byPackage.Clear();
            return null;
        }
    }

    private static void Own(string package, Owner owner) => _byPackage.GetOrAdd(package, _ => new ConcurrentDictionary<Owner, byte>())[owner] = 0;

    /// <summary>A system's package as the tab lists it: /Game/... or /Plugin/..., without the object name.</summary>
    private static string Package(string path)
    {
        var package = path.Split('.')[0];
        return package.StartsWith('/') ? package : Effects.MountPath(package);
    }

    /// <summary>A system's item: one, or a few that share it (names joined, first icon); none when many share it.</summary>
    private static Owner? Of(UObject system)
    {
        if (!_byPackage.TryGetValue(Package(system.GetPathName()), out var owners) || owners.IsEmpty || owners.Count > SharedMost) return null;
        var sorted = owners.Keys.OrderBy(o => o.Name, StringComparer.OrdinalIgnoreCase).ToList();
        return sorted.Count == 1 ? sorted[0]
            : new Owner(string.Join(" / ", sorted.Select(o => o.Name).Distinct()), sorted.FirstOrDefault(o => o.Icon is not null)?.Icon, sorted[0].Kind);
    }

    public static bool Owned(UObject system) => Of(system) is not null;

    public static string KindOf(UObject system)
    {
        if (Of(system) is { } owner) return owner.Kind;
        var path = Package(system.GetPathName());
        return PathKinds.FirstOrDefault(k => k.Match.IsMatch(path)).Kind ?? "Other";
    }

    public static bool Is(UObject system, string kind) => KindOf(system) == kind;

    /// <summary>A system's name in the tab, then its item's for search.</summary>
    public static string DisplayName(UObject system) => Of(system) is { } owner ? $"{system.Name} · {owner.Name}" : system.Name;

    public static string? IconPath(UObject system) => Of(system)?.Icon;
}
