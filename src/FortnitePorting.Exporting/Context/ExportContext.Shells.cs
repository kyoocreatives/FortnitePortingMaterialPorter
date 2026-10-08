using System;
using System.Linq;
using CUE4Parse.UE4.Assets.Exports;
using CUE4Parse.UE4.Assets.Exports.Material;
using CUE4Parse.UE4.Assets.Objects;
using CUE4Parse.UE4.Objects.Engine;
using CUE4Parse.UE4.Objects.UObject;
using FortnitePorting.CUE4Parse.Extensions;
using FortnitePorting.Exporting.MaterialPorter;
using FortnitePorting.Exporting.Models;
using FortnitePorting.Shared.Extensions;

namespace FortnitePorting.Exporting.Context;

public partial class ExportContext
{
    /// <summary>
    /// A character part's shell fur. UE's ShellMesh plugin draws the mesh ShellCount times, each copy pushed
    /// further along the normals up to TotalShellDepth, with the shell material (per slot; its fur mask thins
    /// root to tip by layer) and the base layer material in place of the slot's. The part names its
    /// ShellMeshAsset in CosmeticPartDataList. Returns the part with MPShells, else unchanged.
    /// </summary>
    public ExportPart ShellFur(UObject part, ExportPart exportPart)
    {
        if (!part.TryGetValue(out FInstancedStruct[] dataList, "CosmeticPartDataList")) return exportPart;
        var path = dataList.GetItemOrDefault<FSoftObjectPath>("ShellMeshAsset");
        if (path.AssetPathName.IsNone || path.LoadOrDefault<UObject>() is not { } shell) return exportPart;

        // nearest LOD's count (further LODs only thin it out)
        var lods = shell.GetOrDefault("ShellLods", Array.Empty<FStructFallback>());
        var count = lods.FirstOrDefault()?.GetOrDefault<FPerQualityLevelInt>("ShellCount")?.Default ?? 0;
        var resources = shell.GetOrDefault<FStructFallback>("RuntimeResources");
        if (count <= 0 || resources is null) return exportPart;

        var shells = new MaterialPorterShells
        {
            Depth = shell.GetOrDefault("TotalShellDepth", 1f),
            Count = count,
            CastShadows = shell.GetOrDefault("bCastShadows", false)
        };
        var materials = resources.GetOrDefault("ShellMaterials", Array.Empty<UMaterialInterface>());
        for (var slot = 0; slot < materials.Length; slot++)
            shells.Materials.AddIfNotNull(Material(materials[slot], slot));
        var bases = resources.GetOrDefault("BaseLayerMaterialOverrides", Array.Empty<UMaterialInterface>());
        for (var slot = 0; slot < bases.Length; slot++)
            shells.BaseMaterials.AddIfNotNull(Material(bases[slot], slot));

        return shells.Materials.Count == 0 ? exportPart : new MaterialPorterPart(exportPart) { MPShells = shells };
    }
}
