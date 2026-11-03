using CUE4Parse.UE4.Assets.Exports.Material;
using CUE4Parse.UE4.Assets.Objects;
using CUE4Parse.UE4.Objects.Core.Math;
using CUE4Parse.UE4.Objects.UObject;
using FortnitePorting.Exporting.Models;
using FortnitePorting.Shared.Extensions;

namespace FortnitePorting.Exporting.Context;

public partial class ExportContext
{
    /// <summary>
    /// A style option's CosmeticProperties parameters (newer styles set them there, not in VariantMaterialParams), on
    /// every material: TacticalRecon's default "Cel" sets IsToon? = 1, without which its anime shading and outlines
    /// stay in their PBR mode.
    /// </summary>
    public ExportOverrideParameters? CosmeticPropertyParameters(FStructFallback style)
    {
        var parameters = new ExportOverrideParameters { MaterialNameToAlter = "Global" };
        // instanced structs (the property's kind is its PropertyTag)
        foreach (var instanced in style.GetOrDefault("CosmeticProperties", Array.Empty<FInstancedStruct>()) ?? [])
        {
            if (instanced?.NonConstStruct is not { } property) continue;
            foreach (var scalar in property.GetOrDefault("AdditionalScalarParameters", Array.Empty<FStructFallback>()) ?? [])
                if (ParameterName(scalar) is { } name)
                    parameters.Scalars.AddUnique(new ScalarParameter(name, scalar.GetOrDefault<float>("ParameterValue")));
            foreach (var vector in property.GetOrDefault("AdditionalVectorParameters", Array.Empty<FStructFallback>()) ?? [])
                if (ParameterName(vector) is { } name && vector.TryGetValue(out FLinearColor colour, "ParameterValue"))
                    parameters.Vectors.AddUnique(new VectorParameter(name, colour));
        }
        if (parameters.Scalars.Count == 0 && parameters.Vectors.Count == 0) return null;
        parameters.Hash = parameters.GetHashCode();
        return parameters;
    }

    // ParameterInfo reads as CUE4Parse's FMaterialParameterInfo or as a plain struct
    private static string? ParameterName(FStructFallback? parameter)
    {
        if (parameter is null) return null;
        if (parameter.TryGetValue(out FMaterialParameterInfo info, "ParameterInfo") && info is not null) return info.Name.Text;
        return parameter.TryGetValue(out FStructFallback plain, "ParameterInfo") && plain.TryGetValue(out FName name, "Name") ? name.Text : null;
    }
}
