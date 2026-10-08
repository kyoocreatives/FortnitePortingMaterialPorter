using System.Collections.Generic;
using CUE4Parse.UE4.Objects.Core.Math;
using FortnitePorting.Shared.Extensions;

namespace FortnitePorting.Exporting.Models;

public partial class ExportLightCollection
{
    public List<ExportPointLight> PointLights = [];

    public void Add(ExportLight exportLight)
    {
        if (exportLight is ExportPointLight pointLight)
        {
            PointLights.Add(pointLight);
        }
        else AddForkLight(exportLight);     // MP
    }
    
    public void AddRange(IEnumerable<ExportLight> exportLights)
    {
        exportLights.ForEach(Add);
    }
}

public partial record ExportLight : ExportObject
{
    public FLinearColor Color;
    public float Intensity = 1.0f;
    public float AttenuationRadius = 1000;
    public float Radius = 0.0f;
    public bool CastShadows;
}

public record ExportPointLight : ExportLight
{
    
}