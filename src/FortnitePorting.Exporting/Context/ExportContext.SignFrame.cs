using System;
using System.Collections.Generic;
using System.Linq;
using CUE4Parse.UE4.Assets.Exports;
using CUE4Parse.UE4.Assets.Exports.Component.StaticMesh;
using CUE4Parse.UE4.Assets.Exports.Material;
using CUE4Parse.UE4.Assets.Exports.StaticMesh;
using CUE4Parse.UE4.Objects.Core.Math;
using CUE4Parse.UE4.Objects.Engine;
using CUE4Parse.UE4.Objects.UObject;
using FortnitePorting.Exporting.MaterialPorter;
using FortnitePorting.Exporting.Models;
using FortnitePorting.Shared.Extensions;

namespace FortnitePorting.Exporting.Context;

/// <summary>
/// Material Porter fork: Neon City's billboards (Asteria_NeonCity_SignFrame_E and its CP_ props). The
/// blueprint builds them in its construction script - a screen stretched between TopLeft and
/// BottomRight, a frame of corner and edge instances around it, struts behind - which FP doesn't run:
/// they came out as an empty component. This is that script, ported (UserConstructionScript:
/// InitializeVectorParams, AddInstances_Corner, AddInstances_Edges, ValidateSignPlaneTransform,
/// AddStruts, ValidateMaterial_Override/_TwoSided, SetupMaterialParamsOnSignMesh). The fake glow
/// card shows on Switch only, and is left out.
/// </summary>
public partial class ExportContext
{
    const string SignFrameClass = "Asteria_NeonCity_SignFrame_E_C";
    const string SignFrameMeshes = "/Game/Environments/Asteria/Props/NeonCity/NeonCity_Signage/Meshes/";

    /// <summary>The sign's parts (relative to the actor), or null when the class isn't a sign frame.</summary>
    public List<ExportObject>? SignFrame(UBlueprintGeneratedClass blueprint)
    {
        var chain = new List<UBlueprintGeneratedClass>();
        for (var c = blueprint; c is not null && chain.Count < 16; c = c.SuperStruct?.Load<UBlueprintGeneratedClass>())
            chain.Add(c);
        var root = chain.FirstOrDefault(c => c.Name == SignFrameClass);
        if (root is null) return null;

        // the class's values: the nearest class's default (a CP_ prop sets its size and ad)
        var defaults = chain.Select(c => c.ClassDefaultObject.TryLoad(out var cdo) ? cdo : null).Where(o => o is not null).ToList();
        T Value<T>(string name, T fallback)
        {
            foreach (var cdo in defaults)
                if (cdo!.TryGetValue(out T value, name)) return value;
            return fallback;
        }

        var templates = SignFrameTemplates(root);
        var topLeft = Value("TopLeft", FVector.ZeroVector);
        var bottomRight = Value("BottomRight", new FVector(512, 0, -512));
        // InitializeVectorParams: the corners on the sign's plane (Y 0)
        var tl = new FVector(topLeft.X, 0, topLeft.Z);
        var br = new FVector(bottomRight.X, 0, bottomRight.Z);
        var width = br.X - tl.X;
        var height = tl.Z - br.Z;

        var objects = new List<ExportObject>();

        // AddInstances_Corner
        if (Instanced(templates, "Frame_A_Corner_HISM") is { } corners)
        {
            corners.Instances.Add(Instance(new FVector(tl.X + 128, 0, tl.Z), 90, FVector.OneVector));
            corners.Instances.Add(Instance(new FVector(br.X - 128, 0, br.Z), -90, FVector.OneVector));
            corners.Instances.Add(Instance(new FVector(br.X, 0, tl.Z - 128), 0, FVector.OneVector));
            corners.Instances.Add(Instance(new FVector(tl.X, 0, br.Z + 128), 180, FVector.OneVector));
            objects.Add(corners);
        }

        // AddInstances_Edges: the 512-unit edge stretched between the corners (128 each)
        if (Instanced(templates, "Frame_B_Long_HISM") is { } edges)
        {
            var side = (float) ((height - 256) / 512);
            var span = (float) ((width - 256) / 512);
            edges.Instances.Add(Instance(new FVector(tl.X, 0, tl.Z - 128), 180, new FVector(1, 1, side)));
            edges.Instances.Add(Instance(new FVector(br.X, 0, br.Z + 128), 0, new FVector(1, 1, side)));
            edges.Instances.Add(Instance(new FVector(br.X - 128, 0, tl.Z - 0.005f), 90, new FVector(0.995f, 0.995f, span)));
            edges.Instances.Add(Instance(new FVector(tl.X + 128, 0, br.Z + 0.005f), -90, new FVector(0.995f, 0.995f, span)));
            objects.Add(edges);
        }

        // ValidateSignPlaneTransform: the screen in the frame's middle, 23 units in (its X mirrored)
        if (templates.TryGetValue("MainSignBox", out var boxTemplate) && boxTemplate is UStaticMeshComponent boxComponent
            && MeshComponent(boxComponent) is { } box && width != 0 && height != 0)
        {
            box.Name = "MainSignBox";
            box.Location = new FVector(tl.X + width * 0.5f, 0, tl.Z - height * 0.5f);
            box.Rotation = FRotator.ZeroRotator;
            box.Scale = new FVector(0.9975f * (23 - width) / 100, 1, 0.9975f * (height - 23) / 100);
            var values = new ParamSet { Label = "sign" };
            values.Scalars["LumenBoost"] = Value("LumenBoost", 2.0);
            values.Scalars["LumenUseColorOverride"] = Math.Clamp(Value("LumenUseColorOverride", 0.0), 0, 1);
            values.Scalars["LumenSaturation"] = Math.Min(1 - Value("LumenSaturation", 1.0), 1);
            var tint = Value("LumenColorOverride", new FLinearColor(1, 1, 1, 0));
            values.Vectors["LumenColorOverride"] = [tint.R, tint.G, tint.B, 1];
            objects.Add(SignScreen(box, Value<UMaterialInterface?>("Material", null), Value("TwoSided", false),
                Value<UMaterialInterface?>("DummyMaterial", null), values, (float) Value("PIxelCellSize", 0.01)));
        }

        // AddStruts
        if (Value("bAddStruts", false))
        {
            var count = Value("StrutCount", 2);
            var spread = Value("StrutSpreadFactor", 1.5);
            var holder = LoadMaterialPorterObject(SignFrameMeshes + (count > 1 ? "SM_NeonCity_SignFrame_Holder_B" : "SM_NeonCity_SignFrame_Holder_A")) as UStaticMesh;
            for (var i = 0; i < count && holder is not null; i++)
            {
                var m = ((double) (i + 1) / (count + 1) - 0.5) * 2;
                var f = (Math.Sign(m) * (1 - Math.Pow(1 - Math.Abs(m), spread)) + 1) / 2;
                if (Mesh(holder) is not { } strut) continue;
                strut.Location = new FVector(topLeft.X - 42, 0, (float) (topLeft.Z - f * (topLeft.Z - bottomRight.Z) + 39.5));
                strut.Rotation = new FRotator(180, 0, 0);
                objects.Add(strut);
            }
        }

        return objects;
    }

    // SetupMaterialParamsOnSignMesh / ValidateMaterial_*: the ad in slot 0 (and slot 1 when two-sided,
    // else the dummy back), the Lumen values on both, PIxelCellSize as custom primitive data 0
    ExportMesh SignScreen(ExportMesh box, UMaterialInterface? ad, bool twoSided, UMaterialInterface? back,
        ParamSet values, float pixelCellSize)
    {
        var front = ad is not null ? Material(ad, 0)
            : box.OverrideMaterials.FirstOrDefault(m => m.Slot == 0) ?? box.Materials.FirstOrDefault(m => m.Slot == 0);
        var rear = twoSided ? (ad is not null ? Material(ad, 1) : front) : back is not null ? Material(back, 1) : null;
        box.OverrideMaterials.Clear();
        if (front is not null) box.OverrideMaterials.Add(SlotMaterial(front, 0, values));
        if (rear is not null) box.OverrideMaterials.Add(SlotMaterial(rear, 1, values));
        return new MaterialPorterMesh(box) { MPPrimitiveData = [pixelCellSize] };
    }

    /// <summary>The sign frame class's own component templates, by variable name (its SCS).</summary>
    static Dictionary<string, UObject> SignFrameTemplates(UBlueprintGeneratedClass root)
    {
        var templates = new Dictionary<string, UObject>();
        if (!root.TryGetValue(out UObject scs, "SimpleConstructionScript")) return templates;
        foreach (var node in scs.GetOrDefault("AllNodes", Array.Empty<UObject>()))
        {
            var name = node.GetOrDefault<FName>("InternalVariableName").Text;
            if (node.GetOrDefault<UObject>("ComponentTemplate") is { } template && !string.IsNullOrEmpty(name))
                templates[name] = template;
        }
        return templates;
    }

    /// <summary>An instanced frame part's mesh, its instances to add.</summary>
    ExportMesh? Instanced(Dictionary<string, UObject> templates, string name)
    {
        if (!templates.TryGetValue(name, out var template) || template is not UStaticMeshComponent component) return null;
        var mesh = component.GetStaticMesh().Load<UStaticMesh>();
        if (Mesh(mesh) is not { } export) return null;
        export.Name = name;
        return export;
    }

    static ExportTransform Instance(FVector location, float pitch, FVector scale) =>
        new(new FTransform(new FRotator(pitch, 0, 0).Quaternion(), location, scale));
}
