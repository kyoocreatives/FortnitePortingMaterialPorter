using System.Linq;
using CUE4Parse_Conversion.Dto;
using CUE4Parse.UE4.Assets.Exports.SkeletalMesh;
using FortnitePorting.Exporting.Models;

namespace FortnitePorting.Exporting.Context;

public partial class ExportContext
{
    /// <summary>Marks the materials of the sections the game doesn't draw (FSkelMeshSection.bDisabled, e.g. Sung Jinwoo's
    /// DEMO_Master placeholder): the plugin hides their faces.</summary>
    private static void HideDisabledSections<TVertex>(ExportMesh exportPart, USkeletalMesh mesh, MeshDto<TVertex> dto)
        where TVertex : struct, IMeshVertex
    {
        var lod = dto.LODs.FirstOrDefault(l => !l.IsNanite);
        if (lod is null || mesh.LODModels is not { } models || lod.SourceLodIndex >= models.Length) return;
        var sections = models[lod.SourceLodIndex].Sections;
        foreach (var material in exportPart.Materials)
            if (material.Slot < sections.Length && sections[material.Slot].bDisabled)
                material.MPHidden = true;
    }
}
