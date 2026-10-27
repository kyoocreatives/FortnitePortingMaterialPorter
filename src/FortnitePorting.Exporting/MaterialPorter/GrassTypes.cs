using System;
using System.Collections.Generic;
using System.Linq;
using CUE4Parse.FileProvider;
using CUE4Parse.UE4.Assets.Exports;
using CUE4Parse.UE4.Assets.Exports.Material;
using CUE4Parse.UE4.Assets.Objects;
using CUE4Parse.UE4.Objects.Engine;
using CUE4Parse.UE4.Objects.UObject;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace FortnitePorting.Exporting.MaterialPorter;

/// <summary>
/// Landscape grass for the plugin's scatter (material_porter/scatter.py, grass_masks.py). UE units: cm, degrees.
/// <para>One variety (Read): {"type": LGT package, "name", "variety": index, "mesh": package or null,
/// "materials": [package or null per slot], "density": instances per 10 m x 10 m, "use_grid", "jitter",
/// "scaling": "Uniform"|"Free"|"LockXY", "scale_x"/"scale_y"/"scale_z": [min, max], "random_rotation",
/// "align_to_surface", "align_to_triangle_normals", "cull": [start, end], "density_range": [min, max],
/// "weight_attenuates_max_scale", "max_scale_weight_attenuation", "cast_shadow"}.
/// density and cull: the cooked per-quality value at Epic (PerQuality "3", else its Default) when the asset has one,
/// else the plain value; fields the asset lacks take FGrassVariety's constructor defaults.</para>
/// <para>A landscape placement's MPGrass (Landscape): {"material": master package, "outputs": [{"name", "type":
/// [varieties], "input": {"expression", "output", "channels"?}}]}. The master's graph (the plugin's graph route,
/// ue_graph's input) holds the LandscapeGrassOutput and every input's subgraph, so no mask graph is sent: an output's
/// mask is the value of graph node "expression" (an export Name), output index "output"; "channels" [r, g, b, a]
/// (0/1) only when the input masks components. The same input is the node's Properties.GrassTypes[i].Input where
/// GrassTypes[i].Name equals the output's name.</para>
/// </summary>
public static class GrassTypes
{
    static readonly JsonSerializerSettings Ser = new() { ReferenceLoopHandling = ReferenceLoopHandling.Ignore };

    // FGrassVariety's constructor (UE 5.8 Runtime/Landscape/Private/LandscapeGrass.cpp, FGrassVariety::FGrassVariety;
    // bWeightAttenuatesMaxScale and MaxScaleWeightAttenuation are initialised in Classes/LandscapeGrassType.h)
    const float DefaultDensity = 400f, DefaultJitter = 1f, DefaultCull = 10000f, DefaultAttenuation = 0.5f;

    /// <summary>A LandscapeGrassType's varieties in the scatter schema.</summary>
    public static List<Dictionary<string, object?>> Read(UObject grassType)
    {
        var props = JObject.Parse(JsonConvert.SerializeObject(grassType, Ser))["Properties"] as JObject ?? new JObject();
        var type = PackagePath(grassType.GetPathName());
        var list = new List<Dictionary<string, object?>>();
        var index = 0;
        foreach (var v in props["GrassVarieties"] as JArray ?? [])
        {
            if (v is not JObject variety) continue;
            list.Add(new Dictionary<string, object?>
            {
                ["type"] = type,
                ["name"] = grassType.Name,
                ["variety"] = index++,
                ["mesh"] = Reference(variety["GrassMesh"]),
                ["materials"] = (variety["OverrideMaterials"] as JArray ?? []).Select(Reference).ToList(),
                ["density"] = Quality(variety, "GrassDensity", DefaultDensity),
                ["use_grid"] = Bool(variety, "bUseGrid", true),
                ["jitter"] = Number(variety["PlacementJitter"], DefaultJitter),
                ["scaling"] = ((string?)variety["Scaling"] ?? "Uniform").Split("::").Last(),
                ["scale_x"] = Interval(variety["ScaleX"], 1, 1),
                ["scale_y"] = Interval(variety["ScaleY"], 1, 1),
                ["scale_z"] = Interval(variety["ScaleZ"], 1, 1),
                ["random_rotation"] = Bool(variety, "RandomRotation", true),
                ["align_to_surface"] = Bool(variety, "AlignToSurface", true),
                ["align_to_triangle_normals"] = Bool(variety, "bAlignToTriangleNormals", true),
                ["cull"] = new[] { Quality(variety, "StartCullDistance", DefaultCull), Quality(variety, "EndCullDistance", DefaultCull) },
                ["density_range"] = Interval(variety["AllowedDensityRange"], 0, 1),
                ["weight_attenuates_max_scale"] = Bool(variety, "bWeightAttenuatesMaxScale", false),
                ["max_scale_weight_attenuation"] = Number(variety["MaxScaleWeightAttenuation"], DefaultAttenuation),
                ["cast_shadow"] = Bool(variety, "bCastDynamicShadow", true),
            });
        }
        return list;
    }

    /// <summary>MPGrass for a landscape drawn with these masters (null when none has a LandscapeGrassOutput).</summary>
    public static Dictionary<string, object>? Landscape(IFileProvider provider, IEnumerable<UMaterial> masters)
    {
        foreach (var master in masters)
        {
            if (Output(provider, master) is not { } output) continue;
            var outputs = new List<object>();
            foreach (var input in output.GetOrDefault("GrassTypes", Array.Empty<FStructFallback>()))
            {
                var name = input.GetOrDefault<FName>("Name").Text;
                var varieties = new List<Dictionary<string, object?>>();
                try
                {
                    if (input.GetOrDefault<FPackageIndex>("GrassType") is { IsNull: false } lgt && lgt.Load() is { } loaded) varieties = Read(loaded);
                }
                catch (Exception e)
                {
                    Failures.Note("landscape grass types", name, e);
                }
                var expression = input.GetOrDefault<FExpressionInput>("Input");
                var node = expression?.ExpressionName is { IsNone: false } named ? named.Text : expression?.Expression?.Name;
                var link = new Dictionary<string, object?> { ["expression"] = node, ["output"] = expression?.OutputIndex ?? 0 };
                if (expression is { Mask: not 0 })
                    link["channels"] = new[] { expression.MaskR, expression.MaskG, expression.MaskB, expression.MaskA };
                outputs.Add(new Dictionary<string, object?> { ["name"] = name, ["type"] = varieties, ["input"] = link });
            }
            return new Dictionary<string, object> { ["material"] = PackagePath(master.GetPathName()), ["outputs"] = outputs };
        }
        return null;
    }

    /// <summary>The master's LandscapeGrassOutput: in its editor exports (the cooked package with its optional segment
    /// merged, or the older separate .o.uasset).</summary>
    static UObject? Output(IFileProvider provider, UMaterial master)
    {
        static UObject? Find(IEnumerable<UObject> exports) =>
            exports.FirstOrDefault(e => e.ExportType == "MaterialExpressionLandscapeGrassOutput");
        if (master.Owner is { } owner && Find(owner.GetExports()) is { } merged) return merged;
        var package = PackagePath(master.GetPathName());
        return provider.TryLoadPackage(package + ".o.uasset", out var editor) ? Find(editor.GetExports()) : null;
    }

    /// <summary>"/Game/X/A.A" or "/Game/X/A.0" -> "/Game/X/A".</summary>
    static string PackagePath(string path)
    {
        var dot = path.LastIndexOf('.');
        return dot > path.LastIndexOf('/') ? path[..dot] : path;
    }

    static string? Reference(JToken? reference) =>
        reference is JObject o && (string?)o["ObjectPath"] is { Length: > 0 } path ? PackagePath(path) : null;

    // the cooked FPerQualityLevel* value at Epic (3) else its Default; without one, FPerPlatform* Default, else UE's default
    static float Quality(JObject variety, string field, float fallback) => variety[field + "Quality"] is JObject quality
        ? Number(quality["PerQuality"]?[EpicQuality], Number(quality["Default"], fallback))
        : Number(variety[field]?["Default"], fallback);

    const string EpicQuality = "3";

    static float Number(JToken? t, float fallback) => t is { Type: JTokenType.Float or JTokenType.Integer } ? (float)t : fallback;

    static bool Bool(JObject variety, string field, bool fallback) => variety[field] is { Type: JTokenType.Boolean } b ? (bool)b : fallback;

    static float[] Interval(JToken? t, float min, float max) => [Number(t?["Min"], min), Number(t?["Max"], max)];
}

public partial record MaterialPorterMesh
{
    /// <summary>A landscape's grass outputs (GrassTypes.Landscape), or null.</summary>
    [JsonProperty(NullValueHandling = NullValueHandling.Ignore)]
    public Dictionary<string, object>? MPGrass;
}
