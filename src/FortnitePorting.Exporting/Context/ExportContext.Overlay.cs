using CUE4Parse.UE4.Assets.Exports;
using CUE4Parse.UE4.Assets.Exports.Material;
using CUE4Parse.UE4.Assets.Exports.SkeletalMesh;
using CUE4Parse.UE4.Assets.Objects;
using CUE4Parse.UE4.Objects.UObject;
using FortnitePorting.Exporting.Models;

namespace FortnitePorting.Exporting.Context;

public partial class ExportContext
{
    /// <summary>
    /// A part's overlay material: the game draws it over the whole mesh as a second pass. The anime skins' ink outlines
    /// are one (an inverted hull). From the part's OverlayMaterialData, else its mesh's own OverlayMaterial; null without.
    /// </summary>
    public ExportMaterial? Overlay(UObject part, USkeletalMesh mesh)
    {
        UMaterialInterface? material = null;
        if (part.TryGetValue(out FStructFallback data, "OverlayMaterialData") &&
            data.TryGetValue(out FSoftObjectPath path, "HighQualityMaterial"))
            path.TryLoad(out material);
        if (material is null && mesh.TryGetValue(out FPackageIndex index, "OverlayMaterial"))
            index.TryLoad(out material);
        return material is null ? null : Material(material, 0);
    }
}
