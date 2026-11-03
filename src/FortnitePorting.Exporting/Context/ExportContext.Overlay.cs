using CUE4Parse.UE4.Assets.Exports;
using CUE4Parse.UE4.Assets.Exports.Material;
using CUE4Parse.UE4.Assets.Exports.SkeletalMesh;
using CUE4Parse.UE4.Assets.Objects;
using CUE4Parse.UE4.Objects.UObject;
using CUE4Parse.Utils;
using FortnitePorting.Exporting.Models;

namespace FortnitePorting.Exporting.Context;

public partial class ExportContext
{
    /// <summary>
    /// A part's overlay material: the game draws it over the whole mesh as a second pass. The anime skins' ink outlines
    /// are one (an inverted hull). From the part's OverlayMaterialData, else its mesh's own OverlayMaterial (whole mesh
    /// or first slot's), else the slot material's "_InvHull" sibling; null without.
    /// </summary>
    public ExportMaterial? Overlay(UObject part, USkeletalMesh mesh)
    {
        UMaterialInterface? material = null;
        if (part.TryGetValue(out FStructFallback data, "OverlayMaterialData") &&
            data.TryGetValue(out FSoftObjectPath path, "HighQualityMaterial"))
            path.TryLoad(out material);
        if (material is null && mesh.TryGetValue(out FPackageIndex index, "OverlayMaterial"))
            index.TryLoad(out material);
        var slot = mesh.SkeletalMaterials.FirstOrDefault();
        if (material is null && slot?.OverlayMaterialInterface is { IsNull: false } slotOverlay)
            slotOverlay.TryLoad(out material);
        if (material is null && slot?.MaterialInterface?.ResolvedObject?.GetPathName() is { } own)
            material = InvHullSibling(own);
        return material is null ? null : Material(material, 0);
    }

    // Some anime parts (TacticalRecon's body and face accessory) name no outline anywhere in their files: the game adds
    // MI_X_InvHull next to MI_X at run time (their style variants swap it), so the sibling stands in.
    private UMaterialInterface? InvHullSibling(string materialPath)
    {
        var package = materialPath.SubstringBeforeLast('.');
        var name = package.SubstringAfterLast('/') + "_InvHull";
        return FileProvider.TryLoadPackageObject<UMaterialInterface>($"{package}_InvHull.{name}", out var hull) ? hull : null;
    }
}
