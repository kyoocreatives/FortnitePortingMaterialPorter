using System;
using System.IO;
using System.Linq;
using CUE4Parse.UE4.Assets.Exports;
using CUE4Parse.UE4.Assets.Exports.Texture;
using CUE4Parse.UE4.Assets.Objects;
using CUE4Parse.UE4.Objects.Core.i18N;
using CUE4Parse.UE4.Objects.UObject;
using FortnitePorting.Exporting.MaterialPorter;
using FortnitePorting.MaterialPorter;
using FortnitePorting.Models.Assets.Filters;
using FortnitePorting.Models.Assets.Loading;

namespace FortnitePorting.Services;

/// <summary>Material Porter fork: Rocket Racing cars (their bodies), assembled with their wheels and paint;
/// LEGO figures (cooked ones and recipes), their emotes, building props and sets, creatures.</summary>
public partial class AssetLoaderService
{
    /// <summary>The owner's private overlay's tabs (FortnitePorting.csproj imports it when it's there).</summary>
    partial void AddOwnerLoaders();

    public AssetLoaderService()
    {
        // particle effects (Niagara systems): what each emitter draws, to place by hand; the description says
        // what the emitters are. The registry's (islands') and the game's own, found by file name.
        Categories.First(category => category.Category == EAssetCategory.Gameplay).Loaders.Add(new AssetLoader(EExportType.Effect)
        {
            ClassNames = ["NiagaraSystem"],
            HideRarity = true,
            DescriptionHandler = Effects.Describe,
            MPUnregistered = registry => UEParse.Provider is { } provider ? Effects.ListedSystems(provider, registry) : registry,
            // GPU emitters aren't replayed (an island's effects are mostly GPU): the ones that play apart
            FilterCategories =
            {
                new FilterCategory("EFFECT", [EExportType.Effect])
                {
                    Filters = [new FilterItem("Plays in Blender", asset => Effects.Plays(asset.CreationData.Object))]
                }
            },
        });

        // animations (sequences and montages) by what they are for: characters', gliders', back blings',
        // creatures'... found by path and listed unread (the registry keeps few of them); exported onto
        // the armature selected in Blender
        Categories.First(category => category.Category == EAssetCategory.Gameplay).Loaders.Add(new AssetLoader(EExportType.Animation)
        {
            ClassNames = Animations.Classes,
            HideRarity = true,
            // an animation of an item (a glider's, a back bling's, a pickaxe's, an emote's) shows the
            // item's icon and its name after its own: the search finds an item's animations by its name
            SortType = EAssetSortType.AZ,
            DisplayNameHandler = Animations.DisplayName,
            DescriptionHandler = Animations.Describe,
            MPIconPath = Animations.IconPath,
            MPUnregistered = registry => UEParse.Provider is { } provider ? Animations.Candidates(provider, registry) : registry,
            MPOutline = async (package, name) => await Animations.ReadOutline(UEParse.Provider, package, name),
            // the outlines and the items' index kept from the last listing of the same files: a few
            // seconds instead of over a minute
            MPBeforeListing = async () =>
            {
                var key = $"2 {UEParse.Provider.Files.Count} {UEParse.Provider.MountedVfs.Count}";
                var data = FortnitePorting.Application.AppServices.App.DataFolder.FullName;
                await AnimationOwners.Build(Path.Combine(data, "mp_animation_owners.tsv"), key);
                Animations.Recall(Path.Combine(data, "mp_animations.tsv"), key);
            },
            MPOutlined = Animations.Assign,
            MPAfterListing = Animations.Remember,
            FilterCategories =
            {
                new FilterCategory("ANIMATION", [EExportType.Animation])
                {
                    Filters = [new FilterItem("Of an Item", asset => Animations.Owned(asset.CreationData.Object)),
                        ..Animations.Kinds.Select(k => k.Kind).Append("Other")
                        .Select(kind => new FilterItem(kind, asset => Animations.Is(asset.CreationData.Object, kind)))]
                }
            },
        });

        // the owner's tabs, from the private overlay beside the repo (none without it)
        AddOwnerLoaders();

        // skydiving contrails: each item's effect (played on a character in Blender)
        Categories.First(category => category.Category == EAssetCategory.Cosmetics).Loaders.Add(new AssetLoader(EExportType.Contrail)
        {
            ClassNames = [Effects.ContrailClass],
            HideNames = ["Dev_", "TBD_"],
            HidePredicate = (_, asset, _) => asset.GetOrDefault<FSoftObjectPath>(Effects.ContrailEffect).AssetPathName.IsNone,
        });

        Categories.Add(new AssetLoaderCategory(EAssetCategory.RocketRacing)
        {
            Loaders =
            [
                new AssetLoader(EExportType.Car)
                {
                    ClassNames = [Cars.BodyClass],
                    HideRarity = true,
                    // the newest bodies are named "Blank": their asset's name instead
                    DisplayNameHandler = asset => Cars.ItemTitle(asset.GetOrDefault<FText?>("ItemName")?.Text, asset.Name),
                    DescriptionHandler = asset => asset.GetOrDefault<FText?>("ItemDescription")?.Text is { } description
                                                  && !Cars.IsPlaceholder(description) ? description.TrimEnd() : "",
                }
            ]
        });
        Categories.Add(new AssetLoaderCategory(EAssetCategory.Lego)
        {
            Loaders =
            [
                new AssetLoader(EExportType.LegoOutfit)
                {
                    ClassNames = [Figures.ItemClass],
                    HideRarity = true,
                    // cooked figures, and recipe figures (built from the shared Mutable object's body)
                    HidePredicate = (_, asset, _) => UEParse.Provider is not { } provider || !Figures.HasFigure(provider, asset),
                    LowResIconHandler = asset => AssetLoader.GetLowResIcon(asset) ?? FigurePreview(asset, "SmallPreviewImage", "LargePreviewImage"),
                    HighResIconHandler = asset => AssetLoader.GetHighResIcon(asset) ?? FigurePreview(asset, "LargePreviewImage", "SmallPreviewImage"),
                    DisplayNameHandler = asset => BaseCharacter(asset)?.GetAnyOrDefault<FText?>("DisplayName", "ItemName")?.Text ?? asset.Name,
                    DescriptionHandler = asset => BaseCharacter(asset)?.GetAnyOrDefault<FText?>("Description", "ItemDescription")?.Text.TrimEnd() ?? "",
                },
                // the figure's montage of a Battle Royale emote: imported onto the selected figure's armature
                new AssetLoader(EExportType.LegoEmote)
                {
                    ClassNames = [Figures.EmoteClass],
                    HideRarity = true,
                    LowResIconHandler = asset => BaseDance(asset) is { } dance ? AssetLoader.GetLowResIcon(dance) : null,
                    HighResIconHandler = asset => BaseDance(asset) is { } dance ? AssetLoader.GetHighResIcon(dance) : null,
                    DisplayNameHandler = asset => BaseDance(asset)?.GetAnyOrDefault<FText?>("DisplayName", "ItemName")?.Text ?? asset.Name,
                    DescriptionHandler = asset => BaseDance(asset)?.GetAnyOrDefault<FText?>("Description", "ItemDescription")?.Text.TrimEnd() ?? "",
                },
                // building props and sets, what LEGO Fortnite builds (walls, roofs, doors, furniture, crafting
                // stations, chests...): their actor's meshes (the ones whose actor can be read); cave rooms: their level
                new AssetLoader(EExportType.LegoProp)
                {
                    ClassNames = [..Figures.PropClasses, Figures.BuildClass, Figures.CaveClass],
                    HideRarity = true,
                    HidePredicate = (_, asset, _) => UEParse.Provider is not { } provider || !Figures.HasPropActor(provider, asset),
                    DisplayNameHandler = asset => asset.GetAnyOrDefault<FText?>("DisplayName", "ItemName")?.Text is { Length: > 0 } name
                        ? name : BuildName(asset.Name),
                    DescriptionHandler = BuildDescription,
                    FilterCategories =
                    {
                        new FilterCategory("LEGO", [EExportType.LegoProp])
                        {
                            Filters =
                            [
                                new FilterItem("Props & Sets", asset => Figures.PropClasses.Contains(asset.CreationData.Object.ExportType)),
                                new FilterItem("Building Pieces", asset => asset.CreationData.Object.ExportType == Figures.BuildClass
                                                                           && asset.CreationData.Object.Name.StartsWith("JBID_")),
                                new FilterItem("Stations & Placeables", asset => asset.CreationData.Object.ExportType == Figures.BuildClass
                                                                                 && !asset.CreationData.Object.Name.StartsWith("JBID_")),
                                new FilterItem("Caves", asset => asset.CreationData.Object.ExportType == Figures.CaveClass),
                            ]
                        }
                    }
                },
                // creatures: each look of each species (its meshes from the LEGO Fortnite install)
                new AssetLoader(EExportType.LegoWildlife)
                {
                    ClassNames = [Figures.CreatureClass],
                    HideRarity = true,
                    HidePredicate = (_, asset, _) => !Figures.IsCreature(asset) || UEParse.Provider is not { } provider || !Figures.HasCreatureMesh(provider, asset),
                    LowResIconHandler = asset => UEParse.Provider is { } provider ? Figures.CreatureIcon(provider, asset, large: false) : null,
                    HighResIconHandler = asset => UEParse.Provider is { } provider ? Figures.CreatureIcon(provider, asset, large: true) : null,
                    DisplayNameHandler = asset => CreatureName(asset.Name),
                    DescriptionHandler = _ => "",
                }
            ]
        });
    }

    /// <summary>A LEGO build's name from its asset's (JBID_BS_Wall_Door_02x16x12_01_A -> BS Wall Door 02x16x12 01 A).</summary>
    private static string BuildName(string asset)
    {
        foreach (var prefix in (string[]) ["JBID_", "PBID_", "PPID_"])
            if (asset.StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
                return asset[prefix.Length..].Replace('_', ' ');
        return asset.Replace('_', ' ');
    }

    /// <summary>A LEGO prop's description, else a build's theme (its plugin: JunoTheme_OsirisTown -> OsirisTown) and size.</summary>
    private static string BuildDescription(UObject asset)
    {
        if (asset.GetAnyOrDefault<FText?>("Description", "ItemDescription")?.Text is { Length: > 0 } description)
            return description.TrimEnd();
        var plugin = asset.GetPathName().TrimStart('/').Split('/')[0];
        foreach (var prefix in (string[]) ["JunoTheme_", "JunoTG_", "Juno"])
            if (plugin.StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
            {
                plugin = plugin[prefix.Length..];
                break;
            }
        var size = asset.GetOrDefault<FText?>("SizeDescription")?.Text;
        return string.Join(" · ", new[] { plugin, size }.Where(s => !string.IsNullOrEmpty(s)));
    }

    /// <summary>A creature look's name from its asset's (Juno_Cow_Highlands_LightBrown -> Cow Highlands LightBrown).</summary>
    private static string CreatureName(string asset)
    {
        var name = asset.StartsWith("Juno_", StringComparison.OrdinalIgnoreCase) ? asset[5..] : asset;
        if (name.EndsWith("_Customization", StringComparison.OrdinalIgnoreCase)) name = name[..^"_Customization".Length];
        return name.Replace('_', ' ');
    }

    /// <summary>The Battle Royale outfit a LEGO figure stands for.</summary>
    private static UObject? BaseCharacter(UObject figure) => figure.GetOrDefault<UObject?>("BaseAthenaCharacterItemDefinition");

    /// <summary>The Battle Royale emote a LEGO emote stands for.</summary>
    private static UObject? BaseDance(UObject emote) => emote.GetOrDefault<UObject?>("BaseAthenaDanceItemDefinition");

    /// <summary>A figure's preview from its schema's additional data.</summary>
    private static UTexture2D? FigurePreview(UObject figure, params string[] names)
    {
        foreach (var data in Figures.Schema(figure)?.GetOrDefault("AdditionalData", Array.Empty<FInstancedStruct>()) ?? [])
            if (data.NonConstStruct?.GetAnyOrDefault<UTexture2D?>(names) is { } image)
                return image;
        return null;
    }
}
