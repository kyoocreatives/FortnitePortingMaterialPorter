using System;
using System.IO;
using System.Linq;
using CUE4Parse.UE4.Assets.Exports;
using CUE4Parse.UE4.Assets.Exports.SkeletalMesh;
using CUE4Parse.UE4.Assets.Objects;
using CUE4Parse.UE4.Objects.UObject;
using Newtonsoft.Json;

namespace FortnitePorting.Exporting.Context;

public partial class ExportContext
{
    /// <summary>The part's anim blueprint dumped once next to it when it runs dynamic bones (AnimDynamics or
    /// RigidBody nodes), for the plugin's dynamics bake; null otherwise.</summary>
    public string? AnimBlueprint(UObject additionalData)
    {
        if (DynamicsDefaults(additionalData) is not { } found) return null;
        return DumpPackage(found.Blueprint, "animbp.json");
    }

    /// <summary>The physics asset the part's RigidBody node simulates (its override, else the mesh's), dumped once next
    /// to it; null when the part runs no RigidBody node.</summary>
    public string? PhysicsAsset(UObject additionalData, USkeletalMesh mesh)
    {
        if (DynamicsDefaults(additionalData) is not { } found) return null;
        var node = found.Defaults.Properties
            .Where(p => p.Name.Text.StartsWith("AnimGraphNode_RigidBody"))
            .Select(p => (p.Tag?.GenericValue as FScriptStruct)?.StructType as FStructFallback)
            .FirstOrDefault(s => s is not null);
        if (node is null) return null;
        if (!node.TryGetValue(out UObject? asset, "OverridePhysicsAsset") || asset is null)
            mesh.PhysicsAsset.TryLoad(out asset);
        return asset is null ? null : DumpPackage(asset, "physics.json");
    }

    private (UClass Blueprint, UObject Defaults)? DynamicsDefaults(UObject additionalData)
    {
        if (!additionalData.TryGetValue(out FSoftObjectPath animClass, "AnimClass")
            || !animClass.TryLoad(out UClass animBlueprint)
            || animBlueprint.ClassDefaultObject is not { } defaults
            || !defaults.TryLoad(out var animDefaults)
            || !animDefaults.Properties.Any(p => p.Name.Text.StartsWith("AnimGraphNode_AnimDynamics")
                                                 || p.Name.Text.StartsWith("AnimGraphNode_RigidBody")))
            return null;
        return (animBlueprint, animDefaults);
    }

    private string? DumpPackage(UObject asset, string suffix)
    {
        if (asset.Owner is not { } package) return null;
        var path = GetExportPath(asset, suffix);
        try
        {
            if (!File.Exists(path))
                File.WriteAllText(path, JsonConvert.SerializeObject(package.GetExports(),
                    new JsonSerializerSettings { ReferenceLoopHandling = ReferenceLoopHandling.Ignore }));
            return path;
        }
        catch (Exception e)     // a package that won't dump costs the dynamics, never the export
        {
            Serilog.Log.Warning("[Material Porter] {Asset}: not dumped for the dynamics: {Error}", asset.Name, e.Message);
            return null;
        }
    }
}
