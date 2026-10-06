using System;
using System.Collections.Generic;
using System.Linq;
using CUE4Parse.FileProvider;
using CUE4Parse.UE4.Assets.Exports;
using CUE4Parse.UE4.Assets.Exports.Component.SkeletalMesh;
using CUE4Parse.UE4.Assets.Exports.Component.StaticMesh;
using CUE4Parse.UE4.Assets.Exports.Material;
using CUE4Parse.UE4.Assets.Exports.SkeletalMesh;
using CUE4Parse.UE4.Assets.Exports.StaticMesh;
using CUE4Parse.UE4.Assets.Objects;
using CUE4Parse.UE4.Objects.Engine;
using CUE4Parse.UE4.Objects.UObject;

namespace FortnitePorting.Exporting.MaterialPorter;

/// <summary>
/// Material Porter fork: which assets take a wrap. A wrap shows through a material's customization
/// mask ("CustomizationMask (_CM)": where the wrap goes, and its accent areas), so an asset takes
/// wraps when one of its materials sets that mask: weapons and the vehicles the game wraps do, a
/// potion or the battle bus don't.
/// </summary>
public static class Wraps
{
    public const string Mask = "CustomizationMask (_CM)";

    public static bool HasMask(UObject? material)
    {
        for (var current = material as UUnrealMaterial; current is UMaterialInstanceConstant instance; current = instance.Parent)
            if (instance.TextureParameterValues.Any(t => t.Name == Mask))
                return true;
        return false;
    }

    private static IEnumerable<UObject?> MaterialsOf(UObject? mesh) => mesh switch
    {
        USkeletalMesh skeletal => skeletal.Materials.Select(m => m?.Load()),
        UStaticMesh stat => stat.Materials.Select(m => m?.Load()),
        _ => []
    };

    /// <summary>Whether a weapon item or a vehicle takes wraps: a material of its meshes, or one its actor class puts on them, has the mask.</summary>
    public static bool Supports(IFileProvider provider, UObject item, EExportType type)
    {
        try
        {
            var materials = new List<UObject?>();
            if (type is not EExportType.Vehicle)
            {
                foreach (var name in (string[]) ["WeaponMeshOverride", "PickupSkeletalMesh", "PickupStaticMesh"])
                {
                    materials.AddRange(MaterialsOf(item.GetOrDefault<UObject?>(name)));
                    if (!item.TryGetValue(out FInstancedStruct[] dataList, "DataList")) continue;
                    foreach (var entry in dataList)
                        if (entry.NonConstStruct is { } fields && fields.TryGetValue(out UObject mesh, name))
                            materials.AddRange(MaterialsOf(mesh));
                }
            }
            // the actor class's mesh components: their meshes' materials, and the ones they override
            if (item.TryGetValue(out UBlueprintGeneratedClass actor, type is EExportType.Vehicle ? "VehicleActorClass" : "WeaponActorClass")
                && actor.Owner is { } package)
            {
                foreach (var export in package.GetExports())
                {
                    switch (export)
                    {
                        case USkinnedMeshComponent skinned:
                            materials.AddRange(MaterialsOf(skinned.GetSkeletalMesh().Load()));
                            break;
                        case UStaticMeshComponent stat:
                            materials.AddRange(MaterialsOf(stat.GetStaticMesh().Load()));
                            break;
                        default:
                            continue;
                    }
                    materials.AddRange(export.GetOrDefault("OverrideMaterials", Array.Empty<UMaterialInterface?>()));
                }
            }
            return materials.Any(HasMask);
        }
        catch (Exception e)
        {
            Failures.Note("wraps", item.Name, e);
            return false;
        }
    }
}
