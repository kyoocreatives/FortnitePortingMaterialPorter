using System.Collections.Generic;

namespace FortnitePorting.Exporting.Models;

// Spot and rect lights (FP exports point lights only), and how the engine reads a light.
public partial class ExportLightCollection
{
    public List<ExportSpotLight> SpotLights = [];
    public List<ExportRectLight> RectLights = [];

    private void AddForkLight(ExportLight exportLight)
    {
        if (exportLight is ExportSpotLight spotLight) SpotLights.Add(spotLight);
        else if (exportLight is ExportRectLight rectLight) RectLights.Add(rectLight);
    }
}

public partial record ExportLight
{
    // source level actor: its own meshes don't shadow it in Blender; null on a prop's
    public string? Actor;
    // UE's ELightUnits name (Candelas, Lumens, Unitless, EV, Nits); FP's own point lights are candelas
    public string IntensityUnits = "Candelas";
    // false: UE's older falloff, (1 - (d / AttenuationRadius)^2)^FalloffExponent, whatever the units
    public bool InverseSquaredFalloff = true;
    public float FalloffExponent = 8.0f;
    // a colour temperature (K) tinting Color when UseTemperature (6500 is white)
    public bool UseTemperature;
    public float Temperature = 6500.0f;
}

// shines along its X axis; cone angles in degrees from it
public record ExportSpotLight : ExportLight
{
    public float InnerConeAngle;
    public float OuterConeAngle = 44.0f;
}

// shines along its X axis; SourceWidth along Y, SourceHeight along Z (cm)
public record ExportRectLight : ExportLight
{
    public float SourceWidth = 64.0f;
    public float SourceHeight = 64.0f;
    public float BarnDoorAngle = 88.0f;
}
