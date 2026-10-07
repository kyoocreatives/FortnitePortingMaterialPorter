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

public partial class InstallationProfile : ObservableValidator
{
    [ObservableProperty] private string _profileName = "Unnammed";
    
    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(ArchiveDirectoryEnabled))]
    [NotifyPropertyChangedFor(nameof(UnrealVersionEnabled))]
    [NotifyPropertyChangedFor(nameof(EncryptionKeyEnabled))]
    [NotifyPropertyChangedFor(nameof(MappingsFileEnabled))]
    [NotifyPropertyChangedFor(nameof(TextureStreamingEnabled))]
    [NotifyPropertyChangedFor(nameof(LoadInstalledBundlesEnabled))]
    [NotifyPropertyChangedFor(nameof(IsCustom))]
    [NotifyPropertyChangedFor(nameof(IsCustomOnDemand))]
    private EFortniteVersion _fortniteVersion = EFortniteVersion.LatestInstalled;
    
    [NotifyDataErrorInfo]
    [ArchiveDirectory(canValidateProperty: nameof(ArchiveDirectoryEnabled))]
    [NotifyPropertyChangedFor(nameof(TextureStreamingEnabled))]
    [ObservableProperty] private string _archiveDirectory = string.Empty;

    // Material Porter fork: a Custom profile can download its build instead of reading an install - an older
    // one, from its manifest (a .manifest file or a link: Epic's API only lists the live build), its chunks
    // from Epic's CDN as the On-Demand mode's
    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(IsCustomOnDemand))]
    [NotifyPropertyChangedFor(nameof(ArchiveDirectoryEnabled))]
    [NotifyPropertyChangedFor(nameof(TextureStreamingEnabled))]
    private bool _downloadFromManifest;

    [ObservableProperty] private string _manifestPath = string.Empty;

    // the same build's UEFN (Studio) manifest: its editor data gives exact materials
    [ObservableProperty] private string _studioManifestPath = string.Empty;

    // the Unreal version found by reading the build (the archive leaves most builds' engine out)
    [ObservableProperty] private bool _autoUnrealVersion = true;

    [ObservableProperty] [property: JsonIgnore] private ObservableCollection<MaterialPorter.OnDemandBuild> _availableBuilds = [];
    [ObservableProperty] [property: JsonIgnore] private MaterialPorter.OnDemandBuild? _selectedBuild;
    [ObservableProperty] [property: JsonIgnore] private bool _isFindingBuilds;
    
    [ObservableProperty] private EGame _unrealVersion = EGame.GAME_UE6_0;
    
    [NotifyDataErrorInfo]
    [EncryptionKey(canValidateProperty: nameof(EncryptionKeyEnabled))]
    [ObservableProperty] 
    private FileEncryptionKey _mainKey = FileEncryptionKey.Empty;
    
    [ObservableProperty] private ObservableCollection<FileEncryptionKey> _extraKeys = [];

    [NotifyPropertyChangedFor(nameof(CanFetchVersion))]
    [ObservableProperty] [property: JsonIgnore]
    private string _fetchVersion = string.Empty;
    
    [ObservableProperty] 
    [NotifyPropertyChangedFor(nameof(MappingsFileEnabled))]
    private bool _useMappingsFile;
    
    [ObservableProperty] private string _mappingsFile = string.Empty;
    
    [ObservableProperty] private ELanguage _gameLanguage = ELanguage.English;
    [ObservableProperty] private bool _useTextureStreaming = true;
    [ObservableProperty] private bool _loadInstalledBundles = true;
    [ObservableProperty] private bool _loadNaniteData = true;

    [ObservableProperty] private bool _isSelected;

    [JsonIgnore] public bool IsCustom => FortniteVersion is EFortniteVersion.Custom;
    [JsonIgnore] public bool IsCustomOnDemand => IsCustom && DownloadFromManifest;
    [JsonIgnore] public bool ArchiveDirectoryEnabled => FortniteVersion is not EFortniteVersion.LatestOnDemand && !IsCustomOnDemand;
    [JsonIgnore] public bool UnrealVersionEnabled => IsCustom;
    [JsonIgnore] public bool EncryptionKeyEnabled => IsCustom;
    [JsonIgnore] public bool MappingsFileEnabled => IsCustom;
    // Material Porter fork: an older install (Custom) streams too when it has an on-demand TOC of its own -
    // the chunks its build lists (the latest build's TOC, the Latest modes', wouldn't match it)
    [JsonIgnore] public bool TextureStreamingEnabled => FortniteVersion is EFortniteVersion.LatestInstalled || IsCustom && (DownloadFromManifest || HasOnDemandToc);
    [JsonIgnore] public bool HasOnDemandToc => Directory.Exists(ArchiveDirectory) && Directory.EnumerateFiles(ArchiveDirectory, "*.uondemandtoc").Any();
    [JsonIgnore] public bool LoadInstalledBundlesEnabled => FortniteVersion is EFortniteVersion.LatestInstalled;
    [JsonIgnore] public bool CanFetchVersion => !string.IsNullOrWhiteSpace(FetchVersion);
    
    public async Task BrowseArchivePath()
    {
        if (await App.BrowseFolderDialog() is { } path)
        {
            ArchiveDirectory = path;
        }
    }
    
    public async Task BrowseManifestFile()
    {
        if (await App.BrowseFileDialog(fileTypes: new Avalonia.Platform.Storage.FilePickerFileType("Epic Build Manifest") { Patterns = ["*.manifest"] },
                suggestedFileName: ManifestPath) is { } path)
        {
            ManifestPath = path;
        }
    }

    public async Task BrowseMappingsFile()
    {
        if (await App.BrowseFileDialog(fileTypes: Globals.MappingsFileType, suggestedFileName: MappingsFile) is { } path)
        {
            MappingsFile = path;
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

    // Material Porter fork: the builds this profile can download - the launcher's on this PC, the archive's
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

    // Material Porter fork: whether Epic still has the picked build's files (its oldest builds' are gone)
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

    public async Task FetchVersionData()
    {
        if (!CanFetchVersion) return;

        var version = FetchVersion.Trim();
        var response = await Api.FortnitePorting.FortniteVersion(version);
        if (response is null)
        {
            Info.Message("Fetch Data", $"Failed to data for {version}", InfoBarSeverity.Error);
            return;
        }

        MainKey = new FileEncryptionKey(response.Keys.MainKey.Key);
        ExtraKeys.Clear();
        foreach (var extraKey in response.Keys.ExtraKeys)
        {
            ExtraKeys.Add(new FileEncryptionKey(extraKey.Key));
        }

        var mappingsFound = false;
        if (response.Mappings?.Url is not null)
        {
            var mappingsFilePath = Path.Combine(App.DataFolder.FullName, response.Mappings.Url.SubstringAfterLast("/"));
            if (!File.Exists(mappingsFilePath) ||
                !new FileInfo(mappingsFilePath).GetFileHashMD5().Equals(response.Mappings.Md5Hash))
            {
                var downloaded = await Api.DownloadFileAsync(response.Mappings.Url, mappingsFilePath);
                if (downloaded is not { Exists: true })
                {
                    Info.Message("Fetch Data", $"Failed to download mappings for {version}", InfoBarSeverity.Error);
                    return;
                }

                File.SetCreationTime(mappingsFilePath, DateTime.Now);
            }

            MappingsFile = mappingsFilePath;
            UseMappingsFile = true;
            mappingsFound = true;
        }
        else
        {
            UseMappingsFile = false;
            MappingsFile = string.Empty;
        }

        var keyCount = response.Keys.ExtraKeys.Count + 1;
        var mappingsMessage = mappingsFound
            ? "and downloaded mappings for this version."
            : "but mappings were not available for this version";
        
        Info.Message("Fetch Data", $"Successfully fetched {keyCount} keys for {response.Version} {mappingsMessage}",
            InfoBarSeverity.Success);
    }
    
    public async Task AddEncryptionKey()
    {
        ExtraKeys.Add(FileEncryptionKey.Empty);
    }
    
    public async Task RemoveEncryptionKey(FileEncryptionKey? key)
    {
        if (key is null) return;
        ExtraKeys.Remove(key);
    }

    public override string ToString()
    {
        return ProfileName;
    }

    protected override void OnPropertyChanged(PropertyChangedEventArgs e)
    {
        base.OnPropertyChanged(e);

        switch (e.PropertyName)
        {
            case nameof(FortniteVersion):
            case nameof(DownloadFromManifest):     // Material Porter fork: a downloaded build needs no folder
            {
                ValidateAllProperties();
                break;
            }
        }
    }
}
