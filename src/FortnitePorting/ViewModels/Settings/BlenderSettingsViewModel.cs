using System.ComponentModel;
using CommunityToolkit.Mvvm.ComponentModel;
using CUE4Parse_Conversion.Options;
using FortnitePorting.Exporting.Models;

namespace FortnitePorting.ViewModels.Settings;

public partial class BlenderSettingsViewModel : BaseExportSettings
{
    public bool IsTastyRig => RigType == ERigType.Tasty;
    
    // General
    [ObservableProperty] private bool _scaleDown = true;
    [ObservableProperty] private bool _importIntoCollection = true;
    [ObservableProperty] private bool _importAt3DCursor = false;
    
    // Armature
    [ObservableProperty, NotifyPropertyChangedFor(nameof(IsTastyRig))] private ERigType _rigType = ERigType.Default;
    [ObservableProperty] private bool _mergeArmatures = true;
    [ObservableProperty] private bool _reorientBones = false;
    [ObservableProperty] private bool _importSockets = true;
    [ObservableProperty] private bool _importVirtualBones = false;
    [ObservableProperty] private bool _useDynamicBoneShape = true;
    [ObservableProperty] private float _boneLength = 4.0f;
    
    // Mesh
    [ObservableProperty] private int _targetLOD = 0;
    [ObservableProperty] private EPolygonType _polygonType;
    [ObservableProperty] private bool _importCollision = false;
    
    // Material
    [ObservableProperty] private float _ambientOcclusion = 0.0f;
    [ObservableProperty] private float _cavity = 0.0f;
    [ObservableProperty] private float _subsurface = 0.0f;
    [ObservableProperty] private float _toonShadingBrightness = 0.5f;
    [ObservableProperty] private EMaterialImportMethod _materialImportMethod = EMaterialImportMethod.Data;
    // Material Porter fork: characters keep FP's shaders where FP has one for a material
    [ObservableProperty] private bool _preferFPShaders = false;
    // Material Porter fork: the character materials' rim light (baseBrightness), off by default
    [ObservableProperty] private bool _rimLight = false;
    // Material Porter fork: how much light scatters under exact materials where they scatter any (flat), and how far
    // (× the game's distance): skin; shell fur and the skin under it
    [ObservableProperty] private float _subsurfaceIntensity = 1.0f;
    [ObservableProperty] private float _subsurfaceScale = 1.0f;
    [ObservableProperty] private float _furSubsurfaceIntensity = 1.0f;
    [ObservableProperty] private float _furSubsurfaceScale = 1.0f;
    
    // Texture
    [ObservableProperty] private ETextureImportMethod _textureImportMethod = ETextureImportMethod.Data;
    
    // Animation
    [ObservableProperty] private bool _loopAnimation = false;
    [ObservableProperty] private bool _updateTimelineLength = false;
    [ObservableProperty] private bool _importSounds = false;

    public override ExportSettings ToExportSettings()
    {
        var settings = base.ToExportSettings();
        settings.MeshFormat = EMeshFormat.UEFormat;
        settings.MeshQuality = EMeshQuality.All;
        return settings;
    }
}
public enum ERigType
{
    [Description("Default Rig (FK)")]
    Default,

    [Description("Tasty Rig (IK)")]
    Tasty
}

public enum EPolygonType
{
    [Description("Triangles")]
    Tris,

    [Description("Quads")]
    Quads
}

public enum ETextureImportMethod
{
    [Description("As Texture Data")]
    Data,

    [Description("As Object")]
    Object
}

public enum EMaterialImportMethod
{
    [Description("As Material Data")]
    Data,

    [Description("As Object")]
    Object
}
