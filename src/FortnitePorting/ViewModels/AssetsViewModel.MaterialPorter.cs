using System.Linq;
using System.Threading.Tasks;
using CommunityToolkit.Mvvm.Input;
using FortnitePorting.Exporting.MaterialPorter;
using FortnitePorting.Models.Assets.Asset;
using FortnitePorting.ViewModels.Settings;

namespace FortnitePorting.ViewModels;

public partial class AssetsViewModel
{
    /// <summary>The Blender settings, for the rig button: it applies the rig chosen there (official or Tasty's).</summary>
    public BlenderSettingsViewModel BlenderRig => _settings.ExportSettings.Blender;

    /// <summary>A face board on the armature selected in Blender, with the selected outfit's face data when there is one.</summary>
    [RelayCommand]
    public async Task ExportFaceBoard()
    {
        var outfit = _assetLoader.ActiveLoader?.SelectedAssetInfos.OfType<AssetInfo>().FirstOrDefault()?.Asset.CreationData.Object;
        await _exporter.ExportTastyRig(_settings.ExportSettings.CreateExportMeta(ExportLocation), meta => new FaceBoardExport(meta, outfit));
    }
}
