using CUE4Parse.UE4.Assets.Exports;

namespace FortnitePorting.CUE4Parse.Models.Unreal.Lights;

/// <summary>Rect light: shines along X; SourceWidth runs along Y, SourceHeight along Z (cm).</summary>
public class URectLightComponent : ULocalLightComponent
{
    [UProperty] public float SourceWidth = 64.0f;
    [UProperty] public float SourceHeight = 64.0f;
    [UProperty] public float BarnDoorAngle = 88.0f;
    [UProperty] public float BarnDoorLength = 20.0f;
}
