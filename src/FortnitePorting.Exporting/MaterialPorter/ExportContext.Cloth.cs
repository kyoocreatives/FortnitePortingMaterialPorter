using System;
using System.IO;
using System.Linq;
using CUE4Parse.UE4.Assets.Exports;
using CUE4Parse.UE4.Assets.Exports.SkeletalMesh;
using CUE4Parse.UE4.Objects.UObject;
using Newtonsoft.Json;

namespace FortnitePorting.Exporting.Context;

public partial class ExportContext
{
    /// <summary>The mesh's clothing assets (with their configs and collision physics asset) and each LOD's render
    /// mapping onto them, written once next to it for the plugin's cloth replay; null when the mesh has no cloth.</summary>
    public string? Cloth(USkeletalMesh mesh)
    {
        if (!mesh.TryGetValue(out FPackageIndex[] clothing, "MeshClothingAssets") || clothing.Length == 0
            || mesh.Owner is not { } package || mesh.LODModels is not { } lods)
            return null;
        var path = GetExportPath(mesh, "cloth.json");
        try
        {
            if (!File.Exists(path))
            {
                var assets = clothing.Select(index => index.Load()).ToArray();
                var data = new
                {
                    Assets = assets.Select(asset => asset?.Name),
                    // the assets and their ClothConfigs live in the mesh's package
                    Exports = package.GetExports().Where(e => e.ExportType.Contains("Cloth")),
                    PhysicsAssets = assets.Select(asset => asset is not null && asset.TryGetValue(out UObject physics, "PhysicsAsset")
                        ? DumpPackage(physics, "physics.json") : null),
                    Lods = lods.Select(lod => new
                    {
                        lod.NumVertices,
                        Sections = lod.Sections.Where(s => s.HasClothData).Select(s => new
                        {
                            s.BaseVertexIndex, s.NumVertices, Asset = s.CorrespondClothAssetIndex, AssetLod = s.ClothingData.AssetLodIndex,
                            // per entry: position, normal and tangent (barycentrics + offset), four simulation vertices, weight
                            Mapping = s.ClothMappingDataLODs.Length > 0 ? s.ClothMappingDataLODs[0].Select(m => new double[]
                            {
                                m.PositionBaryCoordsAndDist.X, m.PositionBaryCoordsAndDist.Y, m.PositionBaryCoordsAndDist.Z, m.PositionBaryCoordsAndDist.W,
                                m.NormalBaryCoordsAndDist.X, m.NormalBaryCoordsAndDist.Y, m.NormalBaryCoordsAndDist.Z, m.NormalBaryCoordsAndDist.W,
                                m.TangentBaryCoordsAndDist.X, m.TangentBaryCoordsAndDist.Y, m.TangentBaryCoordsAndDist.Z, m.TangentBaryCoordsAndDist.W,
                                (ushort) m.SourceMeshVertIndices[0], (ushort) m.SourceMeshVertIndices[1],
                                (ushort) m.SourceMeshVertIndices[2], (ushort) m.SourceMeshVertIndices[3], m.Weight
                            }) : []
                        })
                    })
                };
                File.WriteAllText(path, JsonConvert.SerializeObject(data,
                    new JsonSerializerSettings { ReferenceLoopHandling = ReferenceLoopHandling.Ignore }));
            }
            return path;
        }
        catch (Exception e)     // a mesh whose cloth won't write costs the cloth, never the export
        {
            Serilog.Log.Warning("[Material Porter] {Mesh}: cloth not written: {Error}", mesh.Name, e.Message);
            return null;
        }
    }
}
