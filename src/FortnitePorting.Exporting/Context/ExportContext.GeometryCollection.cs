using System.Collections.Generic;
using CUE4Parse.UE4.Assets.Exports.Component;
using CUE4Parse.UE4.Assets.Exports.GeometryCollection;
using CUE4Parse.UE4.Assets.Exports.StaticMesh;
using CUE4Parse.UE4.Objects.Core.Math;
using CUE4Parse.UE4.Objects.Engine;
using CUE4Parse.UE4.Objects.UObject;
using FortnitePorting.Exporting.Models;

namespace FortnitePorting.Exporting.Context;

/// <summary>Geometry Collection components (Chaos destruction) drawn whole, as the game does until they break: the root proxy meshes.</summary>
public partial class ExportContext
{
    public List<ExportMesh> GeometryCollectionComponent(UGeometryCollectionComponent component)
    {
        var meshes = new List<ExportMesh>();
        if (component.RestCollection?.Load<UGeometryCollection>() is not { RootProxyData: { } proxy }) return meshes;
        for (var i = 0; i < proxy.ProxyMeshes.Length; i++)
        {
            if (proxy.ProxyMeshes[i].Load<UStaticMesh>() is not { } mesh || Mesh(mesh) is not { } export) continue;
            SetMeshComponentTransforms(export, component);
            if (proxy.MeshTransforms is { } transforms && i < transforms.Length)
            {
                // proxy transform is relative to the collection; apply the component's on top
                var placed = transforms[i] * new FTransform(export.Rotation, export.Location, export.Scale);
                export.Location = placed.Translation;
                export.Rotation = placed.Rotator();
                export.Scale = placed.Scale3D;
            }
            meshes.Add(export);
        }
        return meshes;
    }

    /// <summary>Native Geometry Collection components in a class's defaults, which the construction script walk misses.</summary>
    public List<ExportMesh> NativeGeometryCollections(UBlueprintGeneratedClass actorClass)
    {
        var meshes = new List<ExportMesh>();
        if (actorClass.ClassDefaultObject?.Load() is not { } defaults) return meshes;
        foreach (var property in defaults.Properties)
            if (property.Tag?.GenericValue is FPackageIndex { IsNull: false } index
                && index.ResolvedObject?.Class?.Name.Text.Contains("GeometryCollectionComponent") == true
                && index.Load() is UGeometryCollectionComponent component)
                meshes.AddRange(GeometryCollectionComponent(component));
        return meshes;
    }
}
