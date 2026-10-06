using System;
using System.Collections.Generic;
using System.Linq;
using CUE4Parse.FileProvider;
using CUE4Parse.UE4.Assets.Exports;
using CUE4Parse.UE4.Assets.Exports.Animation;
using CUE4Parse.UE4.Assets.Exports.Engine;
using CUE4Parse.UE4.Assets.Exports.SkeletalMesh;
using CUE4Parse.UE4.Assets.Objects;
using CUE4Parse.UE4.Objects.Core.i18N;
using CUE4Parse.UE4.Objects.Core.Math;
using CUE4Parse.UE4.Objects.GameplayTags;
using CUE4Parse.UE4.Objects.UObject;

namespace FortnitePorting.Exporting.MaterialPorter;

/// <summary>A weapon mod item (FortWeaponModItemDefinition*): its slot, tag, default mesh and the weapons it allows.</summary>
public sealed class WeaponMod
{
    public UObject Item = null!;
    public string Path = "";
    public string Name = "";
    public string Tag = "";
    /// <summary>Optic, Magazine, Barrel or Underbarrel (its tag's second word).</summary>
    public string Slot = "";
    public FStructFallback? MeshData;
    public string[] AllowedTags = [];
    public byte[] AllowedStream = [];
}

/// <summary>A mod slot of a weapon as its page shows it: the weapon's own mod there, and the mods it allows.</summary>
public sealed class WeaponModSlotPlan
{
    public string Slot = "";
    public WeaponMod? Default;
    public List<WeaponMod> Options = [];
}

/// <summary>Where a mod goes on a weapon: its mesh, the bone it follows, and its place in the weapon's space.</summary>
public sealed record WeaponModPlacement(UObject Mesh, string Bone, FTransform Transform);

/// <summary>
/// Material Porter fork: weapon mods. A weapon item lists the mods it comes with (its DataList's
/// WeaponModSlots: a magazine, a foregrip, its sights), which the game attaches to the weapon's
/// mesh; FP exports the mesh alone. A mod's mesh, attach point and offset on a given weapon are a
/// row of the WeaponModOverrideData tables (the weapon's tag and the mod's tag), else the mod's own
/// DefaultModData. The attach point ("attach_mag", "attach_optic"...) is a bone of the weapon's
/// skeleton, or a socket on one.
/// </summary>
public static class WeaponMods
{
    public static readonly string[] Slots = ["Optic", "Magazine", "Barrel", "Underbarrel"];

    private static readonly object Lock = new();
    private static IFileProvider? _for;
    private static List<WeaponMod> _mods = [];
    private static List<(string WeaponTag, string ModTag, FStructFallback MeshData)> _rows = [];

    private static string SlotOf(string tag)
    {
        var words = tag.Split('.');
        var word = words.Length > 1 ? words[1] : tag;
        return word.ToLowerInvariant() switch
        {
            "mag" or "magazine" => "Magazine",
            "underbarrel" => "Underbarrel",
            "barrel" => "Barrel",
            "optic" => "Optic",
            _ => word
        };
    }

    /// <summary>Every mod item and override row of the game, read once.</summary>
    private static void Ensure(IFileProvider provider)
    {
        lock (Lock)
        {
            if (ReferenceEquals(_for, provider)) return;
            var mods = new List<WeaponMod>();
            var rows = new List<(string, string, FStructFallback)>();
            foreach (var key in provider.Files.Keys.ToArray())
            {
                if (!key.EndsWith(".uasset", StringComparison.OrdinalIgnoreCase)) continue;
                var file = key[(key.LastIndexOf('/') + 1)..^7];
                var path = key[..^7] + "." + file;
                try
                {
                    if (file.StartsWith("WMID_", StringComparison.OrdinalIgnoreCase) || file.StartsWith("WMOID_", StringComparison.OrdinalIgnoreCase))
                    {
                        if (!provider.TryLoadPackageObject(path, out UObject? item) || !item.ExportType.StartsWith("FortWeaponModItemDefinition")) continue;
                        var tag = item.GetOrDefault<FGameplayTag>("WeaponModTag").ToString();
                        var mod = new WeaponMod
                        {
                            Item = item, Path = item.GetPathName(), Tag = tag, Slot = SlotOf(tag),
                            // ("Iron Sight (Default)", "Magazine (Default)": the lists say which is the weapon's own)
                            Name = item.GetOrDefault<FText?>("ItemName")?.Text is { Length: > 0 } name ? name.Replace(" (Default)", "") : item.Name,
                            MeshData = item.GetOrDefault<FStructFallback?>("DefaultModData")?.GetOrDefault<FStructFallback?>("MeshData"),
                        };
                        if (item.Properties.FirstOrDefault(p => p.Name.Text == "AllowedWeaponTagQuery")?.Tag?.GenericValue is FScriptStruct query)
                        {
                            if (query.StructType is FGameplayTagQuery typed)
                            {
                                mod.AllowedTags = (typed.TagDictionary ?? []).Select(t => t.ToString()).ToArray();
                                mod.AllowedStream = typed.QueryTokenStream ?? [];
                            }
                            else if (query.StructType is FStructFallback fields)
                            {
                                mod.AllowedTags = fields.GetOrDefault("TagDictionary", Array.Empty<FGameplayTag>()).Select(t => t.ToString()).ToArray();
                                mod.AllowedStream = fields.GetOrDefault("QueryTokenStream", Array.Empty<byte>());
                            }
                        }
                        mods.Add(mod);
                    }
                    else if (file.Contains("WeaponModOverrideData", StringComparison.OrdinalIgnoreCase) && !file.Contains("Registry", StringComparison.OrdinalIgnoreCase))
                    {
                        if (!provider.TryLoadPackageObject(path, out UDataTable? table)) continue;
                        foreach (var row in table.RowMap.Values)
                        {
                            if (row.GetOrDefault<FStructFallback?>("ModMeshData") is not { } meshData) continue;
                            rows.Add((row.GetOrDefault<FGameplayTag>("WeaponTag").ToString(), row.GetOrDefault<FGameplayTag>("ModTag").ToString(), meshData));
                        }
                    }
                }
                catch { /* one that can't be read: the others still */ }
            }
            _mods = mods.OrderBy(m => m.Name, StringComparer.OrdinalIgnoreCase).ToList();
            _rows = rows;
            _for = provider;
        }
    }

    public static IReadOnlyList<WeaponMod> All(IFileProvider provider)
    {
        Ensure(provider);
        return _mods;
    }

    public static WeaponMod? Find(IFileProvider provider, string path)
    {
        Ensure(provider);
        return _mods.FirstOrDefault(m => m.Path.Equals(path, StringComparison.OrdinalIgnoreCase));
    }

    /// <summary>A weapon's gameplay tags (its DataList's Tags).</summary>
    public static HashSet<string> Tags(UObject weapon)
    {
        var tags = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        if (!weapon.TryGetValue(out FInstancedStruct[] dataList, "DataList")) return tags;
        foreach (var entry in dataList)
            if (entry.NonConstStruct is { } fields && fields.TryGetValue(out FGameplayTagContainer container, "Tags"))
                foreach (var tag in container.GameplayTags)
                    tags.Add(tag.ToString());
        return tags;
    }

    /// <summary>Whether the weapon has the tag, or one under it (UE's HasTag).</summary>
    private static bool Has(HashSet<string> tags, string tag) =>
        tags.Contains(tag) || tags.Any(t => t.StartsWith(tag + ".", StringComparison.OrdinalIgnoreCase));

    /// <summary>A gameplay tag query (FGameplayTagQuery's token stream) against a weapon's tags; an empty one allows all.</summary>
    public static bool Allows(WeaponMod mod, HashSet<string> tags)
    {
        var stream = mod.AllowedStream;
        if (stream.Length < 3 || stream[1] == 0) return true;
        var at = 2;
        try { return Evaluate(stream, mod.AllowedTags, tags, ref at); }
        catch { return true; }
    }

    private static bool Evaluate(byte[] stream, string[] dictionary, HashSet<string> tags, ref int at)
    {
        var type = stream[at++];
        var count = stream[at++];
        switch (type)
        {
            case 1 or 2 or 3 or 7 or 8:     // any / all / no tags match; any / all tags match exactly
            {
                var hits = 0;
                for (var i = 0; i < count; i++)
                {
                    var tag = dictionary[stream[at++]];
                    if (type is 7 or 8 ? tags.Contains(tag) : Has(tags, tag)) hits++;
                }
                return type switch { 1 or 7 => hits > 0, 2 or 8 => hits == count, _ => hits == 0 };
            }
            case 4 or 5 or 6:               // any / all / no expressions match
            {
                var hits = 0;
                for (var i = 0; i < count; i++)
                    if (Evaluate(stream, dictionary, tags, ref at)) hits++;
                return type switch { 4 => hits > 0, 5 => hits == count, _ => hits == 0 };
            }
            default:
                throw new NotSupportedException("gameplay tag query expression " + type);
        }
    }

    /// <summary>The mods a weapon item comes with, by slot (its DataList's WeaponModSlots); null when it takes none.</summary>
    public static Dictionary<string, WeaponMod>? Defaults(IFileProvider provider, UObject weapon)
    {
        Ensure(provider);
        if (!weapon.TryGetValue(out FInstancedStruct[] dataList, "DataList")) return null;
        foreach (var entry in dataList)
        {
            if (entry.NonConstStruct is not { } fields || !fields.TryGetValue(out FStructFallback[] slots, "WeaponModSlots")) continue;
            var defaults = new Dictionary<string, WeaponMod>();
            foreach (var slot in slots)
            {
                if (slot.GetOrDefault<UObject?>("WeaponMod") is not { } item) continue;
                if (Find(provider, item.GetPathName()) is { } mod) defaults.TryAdd(mod.Slot, mod);
            }
            return defaults;
        }
        return null;
    }

    /// <summary>A weapon's mod slots for its page: each slot's own mod and the mods that allow the weapon. Empty when it takes none.</summary>
    public static List<WeaponModSlotPlan> Plan(IFileProvider provider, UObject weapon)
    {
        var plans = new List<WeaponModSlotPlan>();
        if (Defaults(provider, weapon) is not { } defaults) return plans;
        var tags = Tags(weapon);
        foreach (var slot in Slots)
        {
            defaults.TryGetValue(slot, out var own);
            // the weapon's own, and the mods that allow the weapon and have a mesh for it; another
            // weapon's sights are that weapon's shape, so only its own
            var options = _mods.Where(m => m.Slot == slot && (ReferenceEquals(m, own)
                || Allows(m, tags) && HasMesh(m, tags) && !IsSights(m))).ToList();
            if (own is null && options.Count == 0) continue;
            plans.Add(new WeaponModSlotPlan { Slot = slot, Default = own, Options = options });
        }
        return plans;
    }

    private static bool IsSights(WeaponMod mod) =>
        mod.Item.Name.Contains("IronSight", StringComparison.OrdinalIgnoreCase) || mod.Item.Name.Contains("EmptyOptic", StringComparison.OrdinalIgnoreCase);

    /// <summary>Whether a mod has a mesh on a weapon: its own default one, or an override row's for the weapon's tag.</summary>
    private static bool HasMesh(WeaponMod mod, HashSet<string> weaponTags) =>
        mod.MeshData?.GetOrDefault<FSoftObjectPath>("ModMesh").AssetPathName.IsNone == false
        || _rows.Any(r => r.ModTag.Equals(mod.Tag, StringComparison.OrdinalIgnoreCase) && Has(weaponTags, r.WeaponTag));

    /// <summary>
    /// The options' names for a list: a mod's name, and where several share it (the same scope
    /// made for two weapon families), what its asset's name adds (WMOID_FeralCorgi_Optic_Holo: "FeralCorgi").
    /// </summary>
    public static Dictionary<WeaponMod, string> Labels(IEnumerable<WeaponMod> options)
    {
        var labels = new Dictionary<WeaponMod, string>();
        foreach (var group in options.GroupBy(m => m.Name, StringComparer.OrdinalIgnoreCase))
        {
            foreach (var mod in group)
            {
                if (group.Count() == 1) { labels[mod] = mod.Name; continue; }
                var words = mod.Item.Name.Split('_').Skip(1)
                    .Where(w => !w.Equals(mod.Slot, StringComparison.OrdinalIgnoreCase) && w is not ("Optic" or "Mag" or "Default")).ToList();
                // (the plain one of the set says nothing more: WMOID_Optic_Holo, "Holo-13 Optic")
                var plain = words.Count == 0 || words.Count == 1
                    && new string(mod.Name.Where(char.IsLetterOrDigit).ToArray()).Contains(words[0], StringComparison.OrdinalIgnoreCase);
                labels[mod] = plain ? mod.Name : $"{mod.Name} ({string.Join(" ", words)})";
            }
        }
        return labels;
    }

    /// <summary>
    /// A mod on a weapon: the override row for the weapon's tag and the mod's (the most specific
    /// weapon tag wins) over the mod's own mesh data, and the attach point's place on the weapon's mesh.
    /// Null when the mod has no mesh or the weapon no such attach point.
    /// </summary>
    public static WeaponModPlacement? Place(IFileProvider provider, WeaponMod mod, HashSet<string> weaponTags, USkeletalMesh weaponMesh)
    {
        Ensure(provider);
        var data = mod.MeshData;
        var best = -1;
        foreach (var (weaponTag, modTag, meshData) in _rows)
        {
            if (!modTag.Equals(mod.Tag, StringComparison.OrdinalIgnoreCase) || !Has(weaponTags, weaponTag) || weaponTag.Length <= best) continue;
            best = weaponTag.Length;
            data = meshData;
        }
        if (data is null) return null;
        var mesh = data.GetOrDefault<FSoftObjectPath>("ModMesh").TryLoad(provider, out UObject? loaded) ? loaded : null;
        // (a row that only moves the mod keeps the mod's own mesh)
        if (mesh is null && mod.MeshData?.GetOrDefault<FSoftObjectPath>("ModMesh").TryLoad(provider, out UObject? own) == true) mesh = own;
        if (mesh is null) return null;
        var socket = data.GetOrDefault<FName>("SocketName").Text;
        if (AttachPoint(weaponMesh, socket, out var bone) is not { } point) return null;
        var relative = data.GetOrDefault("RelativeTransform", FTransform.Identity);
        return new WeaponModPlacement(mesh, bone, relative * point);
    }

    /// <summary>An attach point in the mesh's space: a socket of the mesh or its skeleton, else the bone of that name.</summary>
    public static FTransform? AttachPoint(USkeletalMesh mesh, string name, out string bone)
    {
        bone = name;
        var local = FTransform.Identity;
        var sockets = mesh.Sockets.Concat(mesh.Skeleton?.Load<USkeleton>()?.Sockets ?? []);
        foreach (var index in sockets)
        {
            if (index?.Load<USkeletalMeshSocket>() is not { } socket || !socket.SocketName.Text.Equals(name, StringComparison.OrdinalIgnoreCase)) continue;
            bone = socket.BoneName.Text;
            local = new FTransform(socket.RelativeRotation, socket.RelativeLocation, socket.RelativeScale);
            break;
        }
        var skeleton = mesh.ReferenceSkeleton;
        var wanted = bone;
        var at = Array.FindIndex(skeleton.FinalRefBoneInfo, b => b.Name.Text.Equals(wanted, StringComparison.OrdinalIgnoreCase));
        if (at < 0) return null;
        bone = skeleton.FinalRefBoneInfo[at].Name.Text;
        var transform = local;
        for (var guard = 0; at >= 0 && guard < 256; guard++)
        {
            transform *= skeleton.FinalRefBonePose[at];
            at = skeleton.FinalRefBoneInfo[at].ParentIndex;
        }
        return transform;
    }
}
