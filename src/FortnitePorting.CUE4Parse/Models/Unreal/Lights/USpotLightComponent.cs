using CUE4Parse.UE4.Assets.Exports;

namespace FortnitePorting.CUE4Parse.Models.Unreal.Lights;

/// <summary>Material Porter fork: a spot light (it shines along its X axis; the cone angles are degrees from that axis).</summary>
public class USpotLightComponent : UPointLightComponent
{
    [UProperty] public float InnerConeAngle;
    [UProperty] public float OuterConeAngle = 44.0f;
}
