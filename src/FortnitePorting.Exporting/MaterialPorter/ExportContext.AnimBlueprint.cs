using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using CUE4Parse.UE4.Assets.Exports;
using CUE4Parse.UE4.Assets.Exports.SkeletalMesh;
using CUE4Parse.UE4.Assets.Objects;
using CUE4Parse.UE4.Kismet;
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

    /// <summary>Each RigidBody node's physics asset (its override, else the mesh's) by node, dumped once next to it;
    /// null when the part runs none.</summary>
    public Dictionary<string, string>? PhysicsAssets(UObject additionalData, USkeletalMesh mesh)
    {
        if (DynamicsDefaults(additionalData) is not { } found) return null;
        var out_ = new Dictionary<string, string>();
        foreach (var property in found.Defaults.Properties.Where(p => p.Name.Text.StartsWith("AnimGraphNode_RigidBody")))
        {
            if ((property.Tag?.GenericValue as FScriptStruct)?.StructType is not FStructFallback node) continue;
            if (!node.TryGetValue(out UObject? asset, "OverridePhysicsAsset") || asset is null)
                mesh.PhysicsAsset.TryLoad(out asset);
            if (asset is not null && DumpPackage(asset, "physics.json") is { } path)
                out_[property.Name.Text] = path;
        }
        return out_.Count > 0 ? out_ : null;
    }

    /// <summary>The character dynamics parameters (per-state gravity and RigidBody settings) the part's anim blueprint
    /// reads at runtime, found in its event graph's bytecode and dumped once next to it; null when it reads none.</summary>
    public string? DynamicsParameters(UObject additionalData)
    {
        if (ScriptPackage(additionalData) is not { } package) return null;
        foreach (var function in package.GetExports().OfType<UStruct>())
            foreach (var constant in Expressions(function.ScriptBytecode ?? []).OfType<EX_ObjectConst>())
                if (constant.Value.TryLoad(out UObject asset) && asset.ExportType == "FortCharacterDynamicsParameters")
                    return DumpPackage(asset, "dynparams.json");
        return null;
    }

    /// <summary>The part's anim blueprint with its functions' bytecode, dumped once next to it: the plugin runs its
    /// update to learn the values the game binds to the dynamics nodes; null when it runs none.</summary>
    public string? AnimScript(UObject additionalData)
    {
        return ScriptPackage(additionalData)?.GetExports().OfType<UClass>().FirstOrDefault() is { } cls
            ? DumpPackage(cls, "animscript.json") : null;
    }

    private global::CUE4Parse.UE4.Assets.IPackage? ScriptPackage(UObject additionalData)
    {
        if (DynamicsDefaults(additionalData) is not { } found || found.Blueprint.Owner is not { Provider: { } provider } owner)
            return null;
        var readScript = provider.ReadScriptData;
        try
        {
            provider.ReadScriptData = true;
            var package = provider.LoadPackage(owner.Name);
            _ = package.GetExports().ToList();      // read while the bytecode is on
            return package;
        }
        catch (Exception e)     // a graph that won't read costs the runtime values, never the export
        {
            Serilog.Log.Warning("[Material Porter] {Anim}: event graph not read: {Error}", found.Blueprint.Name, e.Message);
            return null;
        }
        finally
        {
            provider.ReadScriptData = readScript;
        }
    }

    private static IEnumerable<KismetExpression> Expressions(IEnumerable<KismetExpression> roots)
    {
        // every expression nested in fields or arrays of the statements
        var stack = new Stack<object>(roots);
        while (stack.Count > 0)
        {
            var item = stack.Pop();
            if (item is not KismetExpression expression) continue;
            yield return expression;
            foreach (var field in expression.GetType().GetFields(BindingFlags.Public | BindingFlags.Instance))
            {
                var value = field.GetValue(expression);
                if (value is KismetExpression child) stack.Push(child);
                else if (value is IEnumerable<KismetExpression> children)
                    foreach (var c in children) stack.Push(c);
            }
        }
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
