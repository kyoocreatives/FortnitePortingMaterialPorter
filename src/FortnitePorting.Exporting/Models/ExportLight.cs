using System.Collections.Generic;
using CUE4Parse.UE4.Objects.Core.Math;
using FortnitePorting.Shared.Extensions;

namespace FortnitePorting.Exporting.Models;

public class ExportLightCollection
{
    public List<ExportPointLight> PointLights = [];
    // Material Porter fork: a level's spot and rect lights (FP's own export has point lights only)
    public List<ExportSpotLight> SpotLights = [];
    public List<ExportRectLight> RectLights = [];

    public void Add(ExportLight exportLight)
    {
        if (exportLight is ExportPointLight pointLight)
        {
            PointLights.Add(pointLight);
        }
        else if (exportLight is ExportSpotLight spotLight)
        {
            SpotLights.Add(spotLight);
        }
        else if (exportLight is ExportRectLight rectLight)
        {
            RectLights.Add(rectLight);
        }
    }

    public void AddRange(IEnumerable<ExportLight> exportLights)
    {
        exportLights.ForEach(Add);
    }
}

public record ExportLight : ExportObject
{
    public FLinearColor Color;
    public float Intensity = 1.0f;
    public float AttenuationRadius = 1000;
    public float Radius = 0.0f;
    public bool CastShadows;
    /// <summary>The actor the light came from (a level's lamp): its own meshes don't shadow it in Blender (shadow linking); null on a prop's.</summary>
    public string? Actor;
    /// <summary>
    /// Material Porter fork: what Intensity is in (UE's ELightUnits: Candelas, Lumens, Unitless, EV, Nits); FP's
    /// own point lights have always been read as candelas.
    /// </summary>
    public string IntensityUnits = "Candelas";
    /// <summary>
    /// Material Porter fork: false for UE's older falloff (Intensity is then a brightness that fades as
    /// (1 - (d / AttenuationRadius)^2)^FalloffExponent, whatever IntensityUnits say); FP's own point lights are inverse squared.
    /// </summary>
    public bool InverseSquaredFalloff = true;
    public float FalloffExponent = 8.0f;
    /// <summary>Material Porter fork: a colour temperature (K) that tints Color when UseTemperature (UE: 6500 is white).</summary>
    public bool UseTemperature;
    public float Temperature = 6500.0f;
}

public record ExportPointLight : ExportLight
{

}

/// <summary>Material Porter fork: a spot light; it shines along its X axis, the cone angles are degrees from that axis.</summary>
public record ExportSpotLight : ExportLight
{
    public float InnerConeAngle;
    public float OuterConeAngle = 44.0f;
}

/// <summary>Material Porter fork: a rect light; it shines along its X axis, SourceWidth along its Y and SourceHeight along its Z (cm).</summary>
public record ExportRectLight : ExportLight
{
    public float SourceWidth = 64.0f;
    public float SourceHeight = 64.0f;
    public float BarnDoorAngle = 88.0f;
}
