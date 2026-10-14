using FortnitePorting.ViewModels.Settings;

namespace FortnitePorting.ViewModels;

public partial class AssetsViewModel
{
    /// <summary>The Blender settings, for the rig button: it applies the rig chosen there (official or Tasty's).</summary>
    public BlenderSettingsViewModel BlenderRig => _settings.ExportSettings.Blender;
}
