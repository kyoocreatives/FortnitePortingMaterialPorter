using System;
using System.IO;
using System.Linq;
using CUE4Parse.UE4.Assets.Exports;
using CUE4Parse.UE4.Assets.Objects;
using CUE4Parse.UE4.Objects.UObject;
using Newtonsoft.Json;

namespace FortnitePorting.Exporting.Context;

public partial class ExportContext
{
    /// <summary>The character's deform Control Rig (the one its part's anim blueprint runs: Fortnite's helper-bone
    /// correctives) dumped once next to it, for the plugin's deform_rig; null when the part runs none.</summary>
    public string? DeformRig(UObject additionalData)
    {
        if (!additionalData.TryGetValue(out FSoftObjectPath animClass, "AnimClass")
            || !animClass.TryLoad(out UClass animBlueprint)
            || animBlueprint.ClassDefaultObject is not { } defaults
            || !defaults.TryLoad(out var animDefaults))
            return null;
        var node = animDefaults.Properties
            .Where(p => p.Name.Text.StartsWith("AnimGraphNode_ControlRig"))
            .Select(p => p.Tag?.GenericValue as FScriptStruct)
            .Select(s => s?.StructType as FStructFallback)
            .FirstOrDefault(s => s is not null);
        if (node is null) return null;
        // the rig: a blueprint class under ControlRigAssetReference in current builds (ControlRigClass in older ones),
        // or a cooked runtime asset (ControlRigRuntimeAsset) there instead
        UObject? rigClass = null;
        if (node.TryGetValue(out FStructFallback reference, "ControlRigAssetReference")
            && !reference.TryGetValue(out rigClass, "BlueprintRigClass"))
            reference.TryGetValue(out rigClass, "ControlRigAsset");
        if (rigClass is null && node.TryGetValue(out UClass oldClass, "ControlRigClass")) rigClass = oldClass;
        if (rigClass?.Owner is not { } package) return null;
        var path = GetExportPath(rigClass, "deformrig.json");
        try
        {
            if (!File.Exists(path))
                File.WriteAllText(path, JsonConvert.SerializeObject(package.GetExports(),
                    new JsonSerializerSettings { ReferenceLoopHandling = ReferenceLoopHandling.Ignore }));
            return path;
        }
        catch (Exception e)     // a package that won't dump costs the correctives' tuning, never the export
        {
            Serilog.Log.Warning("[Material Porter] {Rig}: deform rig not dumped: {Error}", rigClass.Name, e.Message);
            return null;
        }
    }
}
