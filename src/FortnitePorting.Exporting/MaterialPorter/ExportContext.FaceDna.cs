using System.IO;
using CUE4Parse.UE4.Assets.Exports.SkeletalMesh;
using FortnitePorting.Exporting.MaterialPorter;

namespace FortnitePorting.Exporting.Context;

public partial class ExportContext
{
    /// <summary>The head's RigLogic data file (written once next to the mesh), or null for a head without one.</summary>
    public string? FaceDna(USkeletalMesh mesh)
    {
        foreach (var data in mesh.AssetUserData ?? [])
        {
            if (!data.TryLoad(out var userData) || userData.ExportType != "DNAAssetUserData") continue;
            if (!userData.TryGetValue(out UFortFaceDna dna, "DNAAsset")) continue;
            var path = GetExportPath(dna, "rigdna");
            if (!File.Exists(path)) File.WriteAllBytes(path, dna.RawData);
            return path;
        }
        return null;
    }
}
