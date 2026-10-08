using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using CUE4Parse.UE4.Assets.Exports;
using CUE4Parse.UE4.Objects.Core.i18N;
using CUE4Parse.UE4.Objects.UObject;
using FortnitePorting.Application;
using FortnitePorting.Exporting.MaterialPorter;

namespace FortnitePorting.MaterialPorter;

/// <summary>Owner item (name, icon) of each Animations-tab animation.</summary>
// Gliders, back blings and pickaxes are matched by their mesh's skeleton (from the package imports),
// emotes by their montage's folder. Cached in a file: the first run reads ~9,000 items.
public static class AnimationOwners
{
    private static readonly (string Class, EExportType Type, bool ByFolder)[] Sources =
    [
        ("AthenaGliderItemDefinition", EExportType.Glider, false),
        ("AthenaBackpackItemDefinition", EExportType.Backpack, false),
        ("AthenaPickaxeItemDefinition", EExportType.Pickaxe, false),
        ("AthenaDanceItemDefinition", EExportType.Emote, true),
    ];

    public static async Task Build(string file, string key)
    {
        Animations.ClearOwners();
        if (Recall(file, key)) return;
        var found = new ConcurrentBag<(bool Folder, string Key, Animations.Owner Owner)>();
        var items = UEParse.AssetRegistry.Where(a => Sources.Any(s => s.Class == a.AssetClass.Text)).ToList();
        await Parallel.ForEachAsync(items, new ParallelOptions { MaxDegreeOfParallelism = Math.Max(2, Environment.ProcessorCount / 2) }, async (data, _) =>
        {
            try
            {
                if (await UEParse.Provider.SafeLoadPackageObjectAsync(data.ObjectPath) is not { } item) return;
                var name = item.GetAnyOrDefault<FText?>("DisplayName", "ItemName")?.Text is { Length: > 0 } shown ? shown : item.Name;
                var source = Sources.First(s => s.Class == data.AssetClass.Text);
                // the icon its own tab shows (a pickaxe's can be its weapon's)
                var owner = new Animations.Owner(name, AppServices.AssetLoading.Get(source.Type).LowResIconHandler(item)?.GetPathName());
                if (source.ByFolder)
                {
                    foreach (var montage in new[] { PathOf(item, "Animation"), PathOf(item, "AnimationFemaleOverride") })
                        if (montage is not null) found.Add((true, montage[..montage.LastIndexOf('/')], owner));
                    return;
                }
                foreach (var mesh in Meshes(item))
                    if (await Animations.SkeletonOf(UEParse.Provider, mesh.Split('.')[0]) is { } skeleton)
                        found.Add((false, skeleton, owner));
            }
            catch (Exception e)
            {
                Exporting.MaterialPorter.Failures.Note("animation owners", data.ObjectPath, e);
            }
        });
        foreach (var (folder, at, owner) in found)
            if (folder) Animations.OwnFolder(at, owner); else Animations.OwnSkeleton(at, owner);
        try
        {
            using var writer = new StreamWriter(file);
            writer.WriteLine(key);
            foreach (var (folder, at, owner) in found)
                writer.WriteLine($"{(folder ? "F" : "S")}\t{at}\t{owner.Name}\t{owner.Icon}");
        }
        catch (Exception e)
        {
            Serilog.Log.Warning("[Material Porter] animation owners cache not saved: {Error}", e.Message);
        }
    }

    private static bool Recall(string file, string key)
    {
        try
        {
            if (!File.Exists(file)) return false;
            using var reader = new StreamReader(file);
            if (reader.ReadLine() != key) return false;
            var owners = new Dictionary<(string, string), Animations.Owner>();
            while (reader.ReadLine() is { } line)
            {
                var f = line.Split('\t');
                if (f.Length != 4) continue;
                // share one owner object per item
                var owner = owners.TryGetValue((f[2], f[3]), out var had) ? had : owners[(f[2], f[3])] = new Animations.Owner(f[2], f[3].Length > 0 ? f[3] : null);
                if (f[0] == "F") Animations.OwnFolder(f[1], owner); else Animations.OwnSkeleton(f[1], owner);
            }
            return true;
        }
        catch (Exception e)
        {
            Serilog.Log.Warning("[Material Porter] animation owners cache unreadable, read again: {Error}", e.Message);
            Animations.ClearOwners();
            return false;
        }
    }

    /// <summary>The meshes an item shows (glider, back bling parts, pickaxe weapon).</summary>
    private static IEnumerable<string> Meshes(UObject item)
    {
        if (PathOf(item, "SkeletalMesh") is { } glider) yield return glider;
        foreach (var part in item.GetOrDefault("CharacterParts", Array.Empty<UObject>()))
            if (PathOf(part, "SkeletalMesh") is { } mesh) yield return mesh;
        if (item.GetOrDefault<UObject?>("WeaponDefinition") is { } weapon
            && (PathOf(weapon, "WeaponMeshOverride") ?? weapon.GetDataListItem<FSoftObjectPath>("WeaponMeshOverride").AssetPathName.Text) is { Length: > 0 } axe && axe != "None")
            yield return axe;
    }

    /// <summary>The path a property names, soft or hard, without loading it.</summary>
    private static string? PathOf(UObject owner, string property)
    {
        if (owner.GetOrDefault<FSoftObjectPath>(property) is { AssetPathName.IsNone: false } soft) return soft.AssetPathName.Text;
        if (owner.GetOrDefault<FPackageIndex?>(property) is { IsNull: false } hard && hard.ResolvedObject is { } resolved) return resolved.GetPathName();
        return null;
    }
}
