namespace FortnitePorting.ViewModels.Settings;

public partial class BlenderSettingsViewModel
{
    /// <summary>What the Assets page's rig button applies to the selected armature: the rig chosen here.</summary>
    [Newtonsoft.Json.JsonIgnore] public string RigApplyTip => IsOfficialRig ? "Apply Official Rig" : "Apply Tasty Rig";

    partial void OnRigTypeChanged(ERigType value) => OnPropertyChanged(nameof(RigApplyTip));
}
