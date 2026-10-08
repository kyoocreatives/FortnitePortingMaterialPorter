using System;
using System.Collections.Generic;
using System.Linq;
using CUE4Parse_Conversion.Dto;
using CUE4Parse_Conversion.Options;
using CUE4Parse.GameTypes.FN.Assets.Exports.DataAssets;
using CUE4Parse.UE4.Assets.Exports;
using CUE4Parse.UE4.Assets.Exports.Actor;
using CUE4Parse.UE4.Assets.Exports.Component;
using CUE4Parse.UE4.Assets.Exports.Component.SkeletalMesh;
using CUE4Parse.UE4.Assets.Exports.Component.StaticMesh;
using CUE4Parse.UE4.Assets.Objects;
using CUE4Parse.UE4.Objects.Core.Math;
using CUE4Parse.UE4.Objects.Engine;
using CUE4Parse.UE4.Objects.UObject;
using CUE4Parse.Utils;
using FortnitePorting.CUE4Parse.Extensions;
using FortnitePorting.CUE4Parse.Models.Unreal.Lights;
using FortnitePorting.Exporting.Models;
using FortnitePorting.Shared.Extensions;
using Serilog;

namespace FortnitePorting.Exporting.Context;

// Material Porter fork: an asset's lights, as the engine reads them.
public partial class ExportContext
{
    // A point, spot or rect light (FP mapped point lights only), as the levels' lights are: the component's values
    // over its templates', else the engine's defaults (white, 6500 K, 1000 cm reach, 44° spot, 64 cm rect, 8 cd).
    public ExportLight? LightComponent(ULightComponentBase lightComponent)
    {
        lightComponent.GatherTemplateProperties();
        var kind = lightComponent switch
        {
            USpotLightComponent => "Spot",
            URectLightComponent => "Rect",
            UPointLightComponent => "Point",
            _ => null
        };
        if (kind is null) return null;

        var intensity = lightComponent.GetOrDefault("Intensity", lightComponent.GetOrDefault("Brightness", 8f));
        if (intensity < 0 || !float.IsFinite(intensity)) return null;

        ExportLight export = kind switch
        {
            "Spot" => new ExportSpotLight
            {
                InnerConeAngle = lightComponent.GetOrDefault("InnerConeAngle", 0f),
                OuterConeAngle = lightComponent.GetOrDefault("OuterConeAngle", 44f),
            },
            "Rect" => new ExportRectLight
            {
                SourceWidth = lightComponent.GetOrDefault("SourceWidth", 64f),
                SourceHeight = lightComponent.GetOrDefault("SourceHeight", 64f),
                BarnDoorAngle = lightComponent.GetOrDefault("BarnDoorAngle", 88f),
            },
            _ => new ExportPointLight(),
        };
        return export with
        {
            Name = lightComponent.Name,
            Location = lightComponent.RelativeLocation,
            Rotation = lightComponent.RelativeRotation,
            Scale = lightComponent.RelativeScale3D,
            Intensity = intensity,
            IntensityUnits = MaterialPorterLightUnits(lightComponent.GetOrDefault<FName>("IntensityUnits").Text),
            InverseSquaredFalloff = kind == "Rect" || lightComponent.GetOrDefault("bUseInverseSquaredFalloff", lightComponent.GetOrDefault("InverseSquaredFalloff", true)),
            FalloffExponent = lightComponent.GetOrDefault("LightFalloffExponent", 8f),
            Color = lightComponent.GetOrDefault("LightColor", new FColor(255, 255, 255, 255)).ToLinearColor(),
            UseTemperature = lightComponent.GetOrDefault("bUseTemperature", false),
            Temperature = lightComponent.GetOrDefault("Temperature", 6500f),
            AttenuationRadius = lightComponent.GetOrDefault("AttenuationRadius", 1000f),
            Radius = kind == "Rect" ? 0f : lightComponent.GetOrDefault("SourceRadius", 0f),
            CastShadows = lightComponent.GetOrDefault("CastShadows", true)
                          && (lightComponent.GetOrDefault("CastDynamicShadows", true) || lightComponent.GetOrDefault("CastStaticShadows", true)),
        };
    }
}
