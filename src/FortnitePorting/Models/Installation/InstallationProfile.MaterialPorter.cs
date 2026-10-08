using System;
using System.Collections.ObjectModel;
using System.ComponentModel;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using CommunityToolkit.Mvvm.ComponentModel;
using CUE4Parse.UE4.Versions;
using CUE4Parse.Utils;
using FluentAvalonia.UI.Controls;
using FortnitePorting.Models.CUE4Parse;
using FortnitePorting.Services;
using FortnitePorting.Shared.Extensions;
using FortnitePorting.Validators;
using Newtonsoft.Json;

namespace FortnitePorting.Models.Installation;

// Builds downloaded from their manifest.
public partial class InstallationProfile
{
    // A Custom profile can download an older build from its manifest (a .manifest file or a link; Epic's API only lists
    // the live build), with its chunks from Epic's CDN.
    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(IsCustomOnDemand))]
    [NotifyPropertyChangedFor(nameof(ArchiveDirectoryEnabled))]
    [NotifyPropertyChangedFor(nameof(TextureStreamingEnabled))]
    private bool _downloadFromManifest;

    [ObservableProperty] private string _manifestPath = string.Empty;

    // the same build's UEFN (Studio) manifest: its editor data gives exact materials
    [ObservableProperty] private string _studioManifestPath = string.Empty;

    // the Unreal version found by reading the build (the archive leaves out most builds' engine)
    [ObservableProperty] private bool _autoUnrealVersion = true;

    [ObservableProperty] [property: JsonIgnore] private ObservableCollection<MaterialPorter.OnDemandBuild> _availableBuilds = [];
    [ObservableProperty] [property: JsonIgnore] private MaterialPorter.OnDemandBuild? _selectedBuild;
    [ObservableProperty] [property: JsonIgnore] private bool _isFindingBuilds;
    

    // owner builds only for now (Fork.OlderBuilds)
    [JsonIgnore] public bool CanDownloadBuild => IsCustom && MaterialPorter.Fork.OlderBuilds;
    [JsonIgnore] public bool IsCustomOnDemand => CanDownloadBuild && DownloadFromManifest;
    [JsonIgnore] public bool HasOnDemandToc => Directory.Exists(ArchiveDirectory) && Directory.EnumerateFiles(ArchiveDirectory, "*.uondemandtoc").Any();

    public async Task BrowseManifestFile()
    {
        if (await App.BrowseFileDialog(fileTypes: new Avalonia.Platform.Storage.FilePickerFileType("Epic Build Manifest") { Patterns = ["*.manifest"] },
                suggestedFileName: ManifestPath) is { } path)
        {
            ManifestPath = path;
        }
    }

    public async Task BrowseStudioManifestFile()
    {
        if (await App.BrowseFileDialog(fileTypes: new Avalonia.Platform.Storage.FilePickerFileType("Epic Build Manifest") { Patterns = ["*.manifest"] },
                suggestedFileName: StudioManifestPath) is { } path)
        {
            StudioManifestPath = path;
        }
    }

    // the builds this profile can download: the launcher's on this PC, plus the archive's
    public async Task FindBuilds()
    {
        IsFindingBuilds = true;
        try
        {
            var builds = await MaterialPorter.OnDemandBuilds.FindAsync(AppSettings.Installation.Profiles.Select(p => p.ArchiveDirectory));
            AvailableBuilds = new ObservableCollection<MaterialPorter.OnDemandBuild>(builds);
            Info.Message("Builds", $"{builds.Count} builds found ({builds.Count(b => b.Source == "this PC")} on this PC, {builds.Count(b => b.StudioManifest is not null)} with UEFN)");
        }
        finally
        {
            IsFindingBuilds = false;
        }
    }

    // a build picked: its manifests, then its keys and mappings (Fetch Data)
    partial void OnSelectedBuildChanged(MaterialPorter.OnDemandBuild? value)
    {
        if (value is null) return;
        ManifestPath = value.Manifest;
        StudioManifestPath = value.StudioManifest ?? string.Empty;
        FetchVersion = value.Version;
        _ = FetchVersionData();
        _ = CheckBuildAsync(value);
    }

    // whether Epic still has the picked build's files (the oldest builds' are gone)
    private async Task CheckBuildAsync(MaterialPorter.OnDemandBuild build)
    {
        if (await MaterialPorter.OnDemandBuilds.AvailableAsync(build.Manifest) == false)
        {
            Info.Message("Build", $"Epic's servers no longer have {build.Version}'s files (they answer \"not found\"): this build can't be loaded.",
                FluentAvalonia.UI.Controls.InfoBarSeverity.Error, autoClose: false);
            return;
        }
        if (build.StudioManifest is { } studio && await MaterialPorter.OnDemandBuilds.AvailableAsync(studio) == false)
        {
            if (SelectedBuild == build) StudioManifestPath = string.Empty;
            Info.Message("Build", $"Epic's servers no longer have {build.Version}'s UEFN files: it loads, with approximated materials.",
                FluentAvalonia.UI.Controls.InfoBarSeverity.Warning, autoClose: false);
        }
    }
}
