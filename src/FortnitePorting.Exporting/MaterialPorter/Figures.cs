using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;
using CUE4Parse.FileProvider;
using CUE4Parse.UE4.Assets.Exports;
using CUE4Parse.UE4.Assets.Exports.SkeletalMesh;
using CUE4Parse.UE4.Assets.Exports.Texture;
using CUE4Parse.UE4.Objects.Engine;
using CUE4Parse.UE4.Assets.Objects;
using CUE4Parse.UE4.Objects.UObject;
using FortnitePorting.CUE4Parse.Extensions;

namespace FortnitePorting.Exporting.MaterialPorter;

/// <summary>
/// LEGO figures (JunoAthenaCharacterItemOverrideDefinition). A figure's AssembledMeshSchema is either baked (SkeletalMeshes lists its
/// cooked meshes) or a Mutable object (CustomizableObjectInstance), which may still have a bake in its Bake folder beside the
/// Mutable one (/FigureCosmetics/Figure/Figure_X/Mutable/... and .../Figure_X/Bake/).
/// </summary>
public static class Figures
{
    public const string ItemClass = "JunoAthenaCharacterItemOverrideDefinition";
    /// <summary>A LEGO emote: its Battle Royale emote and the figure's montage ("Animation").</summary>
    public const string EmoteClass = "JunoAthenaDanceItemOverrideDefinition";
    /// <summary>
    /// A LEGO creature's look (a pawn customization under /JunoCreature_*): an AssembledMeshSchema listing its skeletal meshes (body,
    /// head) and the textures it puts on their materials (its colour LUT), or one SkeletalMesh with OverrideMaterials by slot.
    /// </summary>
    public const string CreatureClass = "FortAIPawnCustomizationDefinition";

    public static bool IsCreature(UObject item) => item.GetPathName().StartsWith("/JunoCreature", StringComparison.OrdinalIgnoreCase);

    /// <summary>Whether a creature's meshes are installed (much of LEGO Fortnite is an optional download).</summary>
    public static bool HasCreatureMesh(IFileProvider provider, UObject item) =>
        HasBake(provider, item)
        || item.TryGetValue(out FSoftObjectPath mesh, "SkeletalMesh") && mesh.AssetPathName.Text is { Length: > 0 } path && path != "None"
           && provider.TryGetGameFile(path[..path.LastIndexOf('.')] + ".uasset", out _);

    /// <summary>
    /// A creature look's icon (large: the _L one if there is one). The game's creature icons (T_UI_Juno_Icon_[Creature_]...) aren't
    /// tied to looks by any asset: an icon named with the look's words (Juno_Cow_Default_Holstein_A -> ..._Cow_HolsteinA) is its own;
    /// else its species' icon, the one its pawn blueprint shows on the map (MarkerDisplay.Icon), from its own folder, or, if that
    /// folder's pawns show none (Juno_PigCowboy), the species its name starts with.
    /// </summary>
    public static UTexture2D? CreatureIcon(IFileProvider provider, UObject item, bool large)
    {
        var (icons, pawns) = CreatureIndex(provider);
        // its words, or its words minus the species' first (Juno_Cow_Default_Simmental_A -> ..._SimmentalA)
        var look = item.Name.StartsWith("Juno_", StringComparison.OrdinalIgnoreCase) ? item.Name[5..] : item.Name;
        var species = Regex.Match(look, "^[A-Z][a-z]+").Value;
        foreach (var key in (string[]) [IconKey(look), IconKey(look[species.Length..])])
            if (icons.TryGetValue(key, out var match) && Texture(provider, large && match.Large is { } l ? l : match.Small) is { } texture)
                return texture;
        string file;
        try { file = provider.FixPath(item.GetPathName().Split('.')[0]); }
        catch (Exception e) { Failures.Note("LEGO creature icons", item.Name, e); return null; }
        var content = file.IndexOf("/Content/", StringComparison.OrdinalIgnoreCase);
        if (content >= 0 && pawns.TryGetValue(file[..(content + "/Content/".Length)], out var own) && MarkerIcon(provider, own) is { } icon)
            return icon;
        var speciesRoot = $"/JunoCreature_{species}/Content/";
        return species.Length > 0 && pawns.FirstOrDefault(kv => kv.Key.EndsWith(speciesRoot, StringComparison.OrdinalIgnoreCase)).Value is { } theirs
            ? MarkerIcon(provider, theirs) : null;
    }

    /// <summary>The first of these pawn blueprints that shows an icon on the map.</summary>
    private static UTexture2D? MarkerIcon(IFileProvider provider, List<string> pawns)
    {
        foreach (var pawn in pawns)
            if (provider.TryLoadPackageObject<UBlueprintGeneratedClass>($"{pawn}.{Path.GetFileName(pawn)}_C", out var cls)
                && cls.ClassDefaultObject.Load() is { } defaults
                && defaults.GetOrDefault<FStructFallback?>("MarkerDisplay")?.GetOrDefault<UTexture2D?>("Icon") is { } icon)
                return icon;
        return null;
    }

    private static UTexture2D? Texture(IFileProvider provider, string key) =>
        provider.TryLoadPackageObject<UTexture2D>($"{key}.{Path.GetFileName(key)}", out var texture) ? texture : null;

    /// <summary>A look's or icon's name as the words that tell it, in any order (Juno_, Creature_, Default, Customization, T_UI_Juno_Icon_ dropped).</summary>
    private static string IconKey(string name)
    {
        var words = Regex.Replace(name, "([a-z])([A-Z])", "$1_$2").Split('_', StringSplitOptions.RemoveEmptyEntries);
        return string.Join(" ", words.Select(w => w.ToLowerInvariant())
            .Where(w => w is not ("t" or "ui" or "juno" or "icon" or "creature" or "default" or "customization")).Order());
    }

    private static IFileProvider? _creaturesIndexed;
    private static Dictionary<string, (string Small, string? Large)> _creatureIcons = new();
    private static Dictionary<string, List<string>> _creaturePawns = new(StringComparer.OrdinalIgnoreCase);

    /// <summary>The creature icons by key and each plugin's pawn blueprints (shortest name first: the species' own), made once per provider.</summary>
    private static (Dictionary<string, (string Small, string? Large)> Icons, Dictionary<string, List<string>> Pawns) CreatureIndex(IFileProvider provider)
    {
        lock (Lock)
        {
            if (ReferenceEquals(_creaturesIndexed, provider)) return (_creatureIcons, _creaturePawns);
            var icons = new Dictionary<string, (string Small, string? Large)>();
            var pawns = new Dictionary<string, List<string>>(StringComparer.OrdinalIgnoreCase);
            foreach (var file in provider.Files.Keys)
            {
                if (!file.EndsWith(".uasset", StringComparison.OrdinalIgnoreCase) || file.EndsWith(".o.uasset", StringComparison.OrdinalIgnoreCase)) continue;
                var key = file[..^".uasset".Length];
                var name = Path.GetFileName(key);
                if (name.StartsWith("T_UI_Juno_Icon_", StringComparison.OrdinalIgnoreCase) && !name.Contains("Portrait", StringComparison.OrdinalIgnoreCase)
                    && !name.StartsWith("T_UI_Juno_Icon_Minifig", StringComparison.OrdinalIgnoreCase) && !name.EndsWith("_S", StringComparison.OrdinalIgnoreCase))
                {
                    var large = name.EndsWith("_L", StringComparison.OrdinalIgnoreCase);
                    var iconKey = IconKey(large ? name[..^2] : name);
                    icons.TryGetValue(iconKey, out var entry);
                    icons[iconKey] = large ? (entry.Small ?? key, key) : (key, entry.Large);
                }
                var pawnsAt = key.IndexOf("/Content/Pawns/BP_", StringComparison.OrdinalIgnoreCase);
                if (pawnsAt > 0 && key.Contains("/JunoCreature", StringComparison.OrdinalIgnoreCase))
                {
                    var root = key[..(pawnsAt + "/Content/".Length)];
                    if (!pawns.TryGetValue(root, out var list)) pawns[root] = list = [];
                    list.Add(key);
                }
            }
            foreach (var list in pawns.Values) list.Sort((a, b) => a.Length.CompareTo(b.Length));
            _creatureIcons = icons;
            _creaturePawns = pawns;
            _creaturesIndexed = provider;
            return (icons, pawns);
        }
    }

    /// <summary>The textures a figure's or creature's schema puts on its meshes' materials (parameter -> texture path).</summary>
    public static Dictionary<string, string> SchemaTextures(IFileProvider provider, UObject item)
    {
        var textures = new Dictionary<string, string>();
        foreach (var entry in Schema(item, provider)?.GetOrDefault("OverrideTextures", Array.Empty<FStructFallback>()) ?? [])
            if (entry.GetOrDefault<FName>("ParamName") is { IsNone: false } param
                && entry.GetOrDefault<FSoftObjectPath>("Texture").AssetPathName.Text is { Length: > 0 } texture && texture != "None")
                textures[param.Text] = texture;
        return textures;
    }

    /// <summary>LEGO building props and building sets: an actor class of meshes ("BuildingActorClassToPreview").</summary>
    public static readonly string[] PropClasses = ["JunoBuildingPropAccountItemDefinition", "JunoBuildingSetAccountItemDefinition"];

    /// <summary>What LEGO Fortnite builds: walls, floors, roofs, doors, furniture (JBID_), crafting stations, chests (PBID_)... Its actor class is its DataList's "ActorClass".</summary>
    public const string BuildClass = "JunoBuildInstructionsItemDefinition";

    /// <summary>A LEGO Fortnite cave room (entrances, dens, treasure rooms...): a level ("World").</summary>
    public const string CaveClass = "PDA_Juno_ProcCave_ShellData_C";

    /// <summary>A cave room's level path, or null.</summary>
    public static string? CaveWorldPath(UObject item) =>
        item.GetOrDefault<FSoftObjectPath>("World").AssetPathName.Text is { Length: > 0 } path && path != "None" ? path : null;

    /// <summary>A LEGO prop's or build's actor class path: the preview actor, else its DataList's.</summary>
    public static string? PropActorPath(UObject item)
    {
        if (item.GetOrDefault<FSoftObjectPath>("BuildingActorClassToPreview").AssetPathName.Text is { Length: > 0 } preview && preview != "None")
            return preview;
        if (!item.TryGetValue(out FInstancedStruct[] dataList, "DataList")) return null;
        foreach (var entry in dataList)
            if (entry.NonConstStruct is FStructFallback fields && fields.TryGetValue(out FSoftObjectPath actor, "ActorClass")
                && actor.AssetPathName.Text is { Length: > 0 } path && path != "None")
                return path;
        return null;
    }

    /// <summary>A LEGO prop's or build's actor class, loaded.</summary>
    public static UBlueprintGeneratedClass? PropActor(UObject item)
    {
        if (item.GetOrDefault<UBlueprintGeneratedClass?>("BuildingActorClassToPreview") is { } preview) return preview;
        if (!item.TryGetValue(out FInstancedStruct[] dataList, "DataList")) return null;
        foreach (var entry in dataList)
            if (entry.NonConstStruct is { } fields && fields.TryGetValue(out UBlueprintGeneratedClass actor, "ActorClass"))
                return actor;
        return null;
    }

    /// <summary>Whether a LEGO prop's actor class can be read: much of LEGO Fortnite's gameplay content is an optional download (install tag GFP_JunoRoot) that may be absent.</summary>
    public static bool HasPropActor(IFileProvider provider, UObject item) =>
        PropActorPath(item) is { } path && provider.TryGetGameFile(path[..path.LastIndexOf('.')] + ".uasset", out _)
        || CaveWorldPath(item) is { } world && provider.TryGetGameFile(world[..world.LastIndexOf('.')] + ".umap", out _);

    private static readonly object Lock = new();
    private static IFileProvider? _indexed;
    private static Dictionary<string, List<string>> _bakes = new(StringComparer.OrdinalIgnoreCase);

    /// <summary>The figure's AssembledMeshSchema (a soft reference on the item), loaded.</summary>
    public static UObject? Schema(UObject item, IFileProvider? provider = null) => Schemas(item, provider).FirstOrDefault();

    /// <summary>The figure's schemas, full detail first, then low detail.</summary>
    private static IEnumerable<UObject> Schemas(UObject item, IFileProvider? provider)
    {
        foreach (var name in (string[]) ["AssembledMeshSchema", "LowDetailsAssembledMeshSchema"])
        {
            if (!item.TryGetValue(out FSoftObjectPath path, name) || path.AssetPathName.IsNone || path.AssetPathName.Text.Length == 0) continue;
            if (provider is null ? path.TryLoad(out UObject? schema) : path.TryLoad(provider, out schema)) yield return schema;
        }
    }

    /// <summary>The references a baked schema lists (SkeletalMeshes: soft or hard, or in structs), unloaded.</summary>
    private static IEnumerable<object> SchemaMeshRefs(UObject schema)
    {
        if (schema.Properties.FirstOrDefault(p => p.Name.Text == "SkeletalMeshes")?.Tag?.GenericValue is not UScriptArray meshes)
            yield break;
        foreach (var value in meshes.Properties.Select(p => p.GenericValue))
        {
            if (value is FScriptStruct { StructType: FStructFallback fields })
            {
                foreach (var field in fields.Properties.Select(p => p.Tag?.GenericValue))
                    if (field is FSoftObjectPath or FPackageIndex) yield return field;
            }
            else if (value is FSoftObjectPath or FPackageIndex)
                yield return value;
        }
    }

    private static USkeletalMesh? LoadMesh(IFileProvider provider, object reference)
    {
        try
        {
            return reference switch
            {
                FSoftObjectPath soft when !soft.AssetPathName.IsNone => soft.TryLoad(provider, out UObject? o) ? o as USkeletalMesh : null,
                FPackageIndex index when !index.IsNull => index.Load() as USkeletalMesh,
                _ => null
            };
        }
        catch (Exception e)
        {
            Failures.Note("LEGO meshes", $"{reference}", e);
            return null;
        }
    }

    /// <summary>The figure's Bake folder (a file key, no trailing slash) beside its Mutable object, or null.</summary>
    public static string? BakeFolder(IFileProvider provider, UObject? schema)
    {
        if (schema is null || !schema.TryGetValue(out FSoftObjectPath instance, "CustomizableObjectInstance")) return null;
        var path = instance.AssetPathName.Text;
        if (string.IsNullOrEmpty(path) || path == "None") return null;
        var dot = path.LastIndexOf('.');
        if (dot > path.LastIndexOf('/')) path = path[..dot];
        string key;
        try { key = provider.FixPath(path); }
        catch (Exception e) { Failures.Note("LEGO bake folders", path, e); return null; }
        var mutable = key.IndexOf("/Mutable/", StringComparison.OrdinalIgnoreCase);
        return mutable < 0 ? null : key[..mutable] + "/Bake";
    }

    /// <summary>The packages in a figure's Bake folder that may be its mesh (not its materials or textures).</summary>
    private static IReadOnlyList<string> BakeCandidates(IFileProvider provider, UObject? schema)
    {
        if (BakeFolder(provider, schema) is not { } folder) return [];
        return Index(provider).TryGetValue(folder, out var found) ? found : [];
    }

    /// <summary>
    /// The figure's recipe: its schema's CustomizableObjectInstance (Figure_X/Mutable/Dataless/COI_..., or Figure_X/Mutable/COI_... for
    /// newer ones), all on the shared recipe object FigureRecipe builds; else null.
    /// </summary>
    public static string? RecipeInstance(IFileProvider provider, UObject item)
    {
        foreach (var schema in Schemas(item, provider))
        {
            if (!schema.TryGetValue(out FSoftObjectPath instance, "CustomizableObjectInstance")) continue;
            var path = instance.AssetPathName.Text;
            if (!string.IsNullOrEmpty(path) && path != "None" && path.Contains("/Mutable/", StringComparison.OrdinalIgnoreCase))
                return path;
        }
        return null;
    }

    /// <summary>Whether the figure can be exported: cooked, or a recipe.</summary>
    public static bool HasFigure(IFileProvider provider, UObject item) => HasBake(provider, item) || RecipeInstance(provider, item) is not null;

    /// <summary>Whether the figure has cooked meshes (listed by its schema, or in its Bake folder), without loading them.</summary>
    public static bool HasBake(IFileProvider provider, UObject item) =>
        Schemas(item, provider).Any(schema => SchemaMeshRefs(schema).Any() || BakeCandidates(provider, schema).Count > 0);

    /// <summary>The figure's cooked skeletal meshes: its schema's, or else its Bake folder's.</summary>
    public static List<USkeletalMesh> BakedMeshes(IFileProvider provider, UObject item)
    {
        foreach (var schema in Schemas(item, provider))
        {
            var meshes = SchemaMeshRefs(schema).Select(r => LoadMesh(provider, r)).OfType<USkeletalMesh>()
                .DistinctBy(m => m.GetPathName()).ToList();
            if (meshes.Count > 0) return meshes;
            foreach (var key in BakeCandidates(provider, schema))
            {
                try
                {
                    if (provider.LoadPackage(key).GetExports().OfType<USkeletalMesh>().FirstOrDefault() is { } mesh) return [mesh];
                }
                catch
                {
                    // not a package that loads: try the next
                }
            }
        }
        return [];
    }

    /// <summary>Why a figure has no cooked mesh: its schema's properties and Bake folder, for the log.</summary>
    public static string Trace(IFileProvider provider, UObject item)
    {
        var schema = Schema(item, provider);
        if (schema is null) return "no schema";
        var props = string.Join(" ", schema.Properties.Select(p => $"{p.Name.Text}:{p.Tag?.GenericValue?.GetType().Name}"));
        var refs = string.Join(" ", SchemaMeshRefs(schema).Select(r => r.ToString()));
        return $"schema {schema.GetPathName()} ({props}), meshes [{refs}], bake folder {BakeFolder(provider, schema) ?? "none"}";
    }

    /// <summary>Bake folder -> its packages named like a mesh (FigureBake_X, SK_FigureBake_X_...), made once per provider.</summary>
    private static Dictionary<string, List<string>> Index(IFileProvider provider)
    {
        lock (Lock)
        {
            if (ReferenceEquals(_indexed, provider)) return _bakes;
            var bakes = new Dictionary<string, List<string>>(StringComparer.OrdinalIgnoreCase);
            foreach (var key in provider.Files.Keys)
            {
                if (!key.EndsWith(".uasset", StringComparison.OrdinalIgnoreCase)) continue;
                var slash = key.LastIndexOf('/');
                if (slash < 5 || !key.AsSpan(0, slash).EndsWith("/Bake", StringComparison.OrdinalIgnoreCase)) continue;
                var name = Path.GetFileNameWithoutExtension(key);
                // the bake's materials and textures are named after the mesh: FigureBake_X_M_Figure_..., FigureBake_X_MI_...
                // (some Bake folders hold only a material)
                if (!name.Contains("FigureBake_", StringComparison.OrdinalIgnoreCase) || name.Contains("_M_", StringComparison.OrdinalIgnoreCase)
                    || name.Contains("_MI_", StringComparison.OrdinalIgnoreCase)) continue;
                var folder = key[..slash];
                if (!bakes.TryGetValue(folder, out var list)) bakes[folder] = list = [];
                list.Add(key[..^".uasset".Length]);
            }
            foreach (var list in bakes.Values)
                list.Sort((a, b) => a.Length.CompareTo(b.Length)); // FigureBake_X before its variants
            _bakes = bakes;
            _indexed = provider;
            return bakes;
        }
    }
}
