using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Net.Http.Headers;
using System.Reflection;
using System.Text.RegularExpressions;
using System.Threading.Tasks;
using CommunityToolkit.Mvvm.ComponentModel;
using Avalonia.Media.Imaging;
using Avalonia.Platform;
using CUE4Parse_Conversion.Textures;
using CUE4Parse_Conversion.Textures.BC;
using CUE4Parse.Compression;
using CUE4Parse.Encryption.Aes;
using CUE4Parse.MappingsProvider;
using CUE4Parse.MappingsProvider.Usmap;
using CUE4Parse.UE4.AssetRegistry;
using CUE4Parse.UE4.AssetRegistry.Objects;
using CUE4Parse.UE4.Assets;
using CUE4Parse.UE4.Assets.Exports;
using CUE4Parse.UE4.Assets.Exports.Texture;
using CUE4Parse.UE4.Assets.Exports.Animation;
using CUE4Parse.UE4.Assets.Exports.Engine;
using CUE4Parse.UE4.Assets.Exports.SkeletalMesh;
using CUE4Parse.UE4.Assets.Exports.StaticMesh;
using CUE4Parse.UE4.IO;
using CUE4Parse.UE4.Objects.Core.i18N;
using CUE4Parse.UE4.Objects.Core.Math;
using CUE4Parse.UE4.Objects.Engine;
using CUE4Parse.UE4.Objects.UObject;
using CUE4Parse.UE4.Pak;
using CUE4Parse.UE4.Readers;
using CUE4Parse.UE4.Versions;
using CUE4Parse.UE4.VirtualFileSystem;
using CUE4Parse.Utils;
using EpicManifestParser;
using EpicManifestParser.UE;
using FortnitePorting.CUE4Parse.Models.Fortnite;
using FortnitePorting.CUE4Parse.Models.Fortnite.GameFeature;
using FortnitePorting.CUE4Parse.Models.Fortnite.Styles;
using FortnitePorting.Exporting;
using FortnitePorting.Extensions;
using FortnitePorting.Framework;
using FortnitePorting.Models.API.Responses;
using FortnitePorting.Models.CUE4Parse;
using FortnitePorting.Models.Information;
using FortnitePorting.Rendering.Preview;
using FortnitePorting.Shared.Extensions;
using FortnitePorting.Views;
using FortnitePorting.Views.Settings;
using Serilog;
using UE4Config.Parsing;
using FGuid = CUE4Parse.UE4.Objects.Core.Misc.FGuid;

namespace FortnitePorting.Services;

public partial class CUE4ParseService : ObservableObject, IService, IResettable
{
    [ObservableProperty] private string _status = "Loading Files";
    [ObservableProperty] private bool _finishedLoading;
    [ObservableProperty] private float _progress = 0.0f;
    [ObservableProperty] private bool _isLoading;
    public HybridFileProvider? Provider;

    public FBuildPatchAppManifest? LiveManifest;
    
    public readonly List<FPartialAssetData> AssetRegistry = [];
    public readonly List<FRarityCollection> RarityColors = [];
    public readonly Dictionary<int, FColor> BeanstalkColors = [];
    public readonly Dictionary<int, FLinearColor> BeanstalkMaterialProps = [];
    public readonly Dictionary<int, FVector> BeanstalkAtlasTextureUVs = [];
    public readonly List<UAnimMontage> MaleLobbyMontages = [];
    public readonly List<UAnimMontage> FemaleLobbyMontages = [];
    public readonly Dictionary<string, string> SetNames = [];
    
    private static readonly List<DirectoryInfo> ExtraDirectories = 
    [
        new(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "FortniteGame", "Saved", "PersistentDownloadDir", "GameCustom", "InstalledBundles"))
    ];
    
    private static readonly List<string> MaleLobbyMontagePaths = 
    [
        "FortniteGame/Content/Animation/Game/MainPlayer/Menu/BR/Male_Commando_Idle_01_M",
        "FortniteGame/Content/Animation/Game/MainPlayer/Menu/BR/Male_commando_Idle_2_M",
        "FortniteGame/Plugins/GameFeatures/BRCosmetics/Content/Animation/Game/MainPlayer/Menu/BR/Male_commando_Idle_01_M",
        "FortniteGame/Plugins/GameFeatures/BRCosmetics/Content/Animation/Game/MainPlayer/Menu/BR/Male_commando_Idle_2_M"
    ];
    
    private static readonly List<string> FemaleLobbyMontagePaths = 
    [
        "FortniteGame/Content/Animation/Game/MainPlayer/Menu/BR/Female_Commando_Idle_02_Rebirth_Montage",
        "FortniteGame/Content/Animation/Game/MainPlayer/Menu/BR/Female_Commando_Idle_03_Montage",
        "FortniteGame/Plugins/GameFeatures/BRCosmetics/Content/Animation/Game/MainPlayer/Menu/BR/Female_Commando_Idle_02_Rebirth_Montage"
    ];

    private const EGame LATEST_GAME_VERSION = EGame.GAME_UE6_0;

    private FortniteVersionResponse? _resolvedVersion;
    
    public DirectoryInfo CacheFolder => new(Path.Combine(App.ApplicationDataFolder.FullName, ".cache"));

    public CUE4ParseService()
    {
        CacheFolder.Create();
    }

    public async Task Initialize()
    {
        MaterialPorter.MaterialPorterService.Instance.OnGameLoading();     // Material Porter fork
        // Material Porter fork: downloaded files are fetched beside an export (a local install's are at hand)
        Exporting.MaterialPorter.Prefetch.Enabled = AppSettings.Installation.CurrentProfile.IsCustomOnDemand
            || AppSettings.Installation.CurrentProfile.FortniteVersion is EFortniteVersion.LatestOnDemand;
        if (!HasValidArchivePath())
        {
            Info.Dialog("Invalid Installation Settings", "The archive directory set in Installation Settings does not exist or is empty. Please set it to your Fortnite installation's archive directory (generally located at FortniteGame/Content/Paks).", buttons:
            [
                new DialogButton
                {
                    Text = "Open Installation Settings",
                    IsPrimary = true,
                    Action = () => TaskService.Run(async () =>
                    {
                        Navigation.App.Open<SettingsView>();
                        await Task.Delay(250);
                        Navigation.Settings.Open<InstallationSettingsView>();
                    })
                },
                new DialogButton
                {
                    Text = "Cancel"
                }
            ]);
            
            return;
        }

        _resolvedVersion = null;
        
        var stages = GetType()
            .GetMethods(BindingFlags.NonPublic | BindingFlags.Instance)
            .Select(m => (Method: m, Attr: m.GetCustomAttribute<LoadingStageAttribute>()))
            .Where(x => x.Attr is not null)
            .OrderBy(x => x.Attr!.Stage)
            .Select(x => new LoadingStage(x.Method, x.Attr!))
            .ToList();
        
        var totalWeight = stages.Sum(x => x.Attr.Weight);
        var completedWeight = 0.0f;

        foreach (var stage in stages)
        {
            UpdateStatus(stage.Attr.Name);
            
            completedWeight += stage.Attr.Weight;
            Progress = (completedWeight / totalWeight) * 100.0f;
            
            if (stage.Method.Invoke(this, null) is not Task stageTask)
                continue;

            await stageTask;
        }

        UpdateStatus(string.Empty);
        FinishedLoading = true;
        Progress = 0;

        // Material Porter fork: exact materials, served to Blender from these files
        // (a build downloaded from its manifest names itself: its graphs are cached apart from other builds')
        MaterialPorter.MaterialPorterService.Instance.OnGameLoaded(Provider!, _resolvedVersion?.Version ?? LiveManifest?.Meta.BuildVersion);
    }

    public void Reset()
    {
        FinishedLoading = false;
        Progress = 0;
        Status = "Loading Files";

        Provider?.Dispose();
        Provider = null;
        LiveManifest = null;

        AssetRegistry.Clear();
        RarityColors.Clear();
        BeanstalkColors.Clear();
        BeanstalkMaterialProps.Clear();
        BeanstalkAtlasTextureUVs.Clear();
        MaleLobbyMontages.Clear();
        FemaleLobbyMontages.Clear();
        SetNames.Clear();
        _resolvedVersion = null;
    }

    public async Task LoadCoreSessionAsync()
    {
        IsLoading = true;
        await Initialize();
        IsLoading = false;

        if (!FinishedLoading) return;

        if (AppSettings.Application.UseDefaultExportLoadType)
            await AssetLoading.Load(AppSettings.Application.DefaultExportLoadType);

        await Files.Initialize();
        await FilesVM.Initialize();

        App.TryFlushPendingUrlScheme();
    }

    public void UpdateStatus(string status)
    {
        Status = status;
        if (!string.IsNullOrEmpty(status))
            Log.Information("[STATUS] {status}", status);
    }

    private bool HasValidArchivePath()
    {
        return AppSettings.Installation.CurrentProfile.FortniteVersion switch
        {
            // Material Porter fork: a build downloaded from its manifest has no folder
            _ when AppSettings.Installation.CurrentProfile.IsCustomOnDemand => true,
            EFortniteVersion.LatestInstalled or EFortniteVersion.Custom => Directory.Exists(AppSettings.Installation.CurrentProfile.ArchiveDirectory),
            _ => true
        };
    }

    [LoadingStage("Initializing CUE4Parse", stage: 0, weight: 5)]
    private async Task InitializeProviderSetup()
    {
        Provider = AppSettings.Installation.CurrentProfile.FortniteVersion switch
        {
            EFortniteVersion.LatestOnDemand => new HybridFileProvider(new VersionContainer(LATEST_GAME_VERSION)),
            EFortniteVersion.LatestInstalled => new HybridFileProvider(AppSettings.Installation.CurrentProfile.ArchiveDirectory, ExtraDirectories, new VersionContainer(LATEST_GAME_VERSION)),
            // Material Porter fork: an older build downloaded from its manifest (InitializeProvider)
            _ when AppSettings.Installation.CurrentProfile.IsCustomOnDemand => new HybridFileProvider(new VersionContainer(AppSettings.Installation.CurrentProfile.UnrealVersion)),
            _ => new HybridFileProvider(AppSettings.Installation.CurrentProfile.ArchiveDirectory, [], new VersionContainer(AppSettings.Installation.CurrentProfile.UnrealVersion)),
        };

        if (AppSettings.Installation.CurrentProfile.FortniteVersion is EFortniteVersion.LatestInstalled or EFortniteVersion.LatestOnDemand)
        {
            _resolvedVersion = await Api.FortnitePorting.FortniteVersion();
            if (_resolvedVersion is not null)
                Log.Information("Resolved Fortnite Version: {Version}", _resolvedVersion.Version);
            else
                Log.Warning("Failed to resolve latest Fortnite version keys/mappings from API");
        }
        
        Log.Information("Installation Type: {Type}", AppSettings.Installation.CurrentProfile.FortniteVersion);
        Log.Information("Archive Path: {Path}", AppSettings.Installation.CurrentProfile.FortniteVersion is EFortniteVersion.LatestOnDemand ? "On-Demand"
            : AppSettings.Installation.CurrentProfile.IsCustomOnDemand ? "On-Demand, " + AppSettings.Installation.CurrentProfile.ManifestPath
            : AppSettings.Installation.CurrentProfile.ArchiveDirectory);
        Log.Information("Unreal Version: {Version}", Provider.Versions.Game.ToString());
        Log.Information("Texture Streaming: {UseTextureStreaming}", AppSettings.Installation.CurrentProfile.UseTextureStreaming);
        
        ObjectTypeRegistry.RegisterEngine(typeof(UFortGameFeatureData).Assembly);
        // Material Porter fork: island landscape collision components left unread (SkippedExports)
        MaterialPorter.SkippedExports.Register();

        Provider.LoadOnDemandTocs = AppSettings.Installation.CurrentProfile is { TextureStreamingEnabled: true, UseTextureStreaming: true };
        Provider.LoadExtraDirectories = AppSettings.Installation.CurrentProfile.LoadInstalledBundles;
        Provider.ReadNaniteData = AppSettings.Installation.CurrentProfile.LoadNaniteData;
        Provider.OnDemandOptions = new IoStoreOnDemandOptions
        {
            ChunkHostUri = new Uri("https://egdownload.fastly-edge.com/", UriKind.Absolute),
            ChunkCacheDirectory = CacheFolder,
            Authorization = new AuthenticationHeaderValue("Bearer", AppSettings.Application.EpicAuth?.Token),
            Timeout = TimeSpan.FromSeconds(AppSettings.Developer.RequestTimeoutSeconds)
        };

        Provider.VfsMounted += (sender, _) =>
        {
            if (sender is not IAesVfsReader reader) return;

            UpdateStatus(reader.Name.Equals("plugin.utoc")
                ? $"Loading GameFeature {reader.Path.SubstringBeforeLast("\\").SubstringAfterLast("\\")}"
                : $"Loading {reader.Name}");
        };
    }

    [LoadingStage("Checking for Valid Keys", stage: 1, weight: 1)]
    private async Task CheckBlackHole()
    {
        if (AppSettings.Installation.CurrentProfile.FortniteVersion is not EFortniteVersion.LatestInstalled) return;
        
        var mainKey = _resolvedVersion?.Keys?.MainKey;
        if (mainKey is null) return;
        
        var mainPakPath = Path.Combine(AppSettings.Installation.CurrentProfile.ArchiveDirectory,
            "pakchunk0-WindowsClient.pak");
        if (!File.Exists(mainPakPath)) return;

        var mainPakReader = new PakFileReader(mainPakPath);
        if (mainPakReader.TestAesKey(new FAesKey(mainKey.Key)))
        {
            Log.Information("Main key {Key} succeeded on pak {PakName}", mainKey.Key, mainPakPath);
            return;
        }
        
        BlackHole.Open(isMinigame: false);
    }
    
    [LoadingStage("Removing Outdated Cache Files", stage: 2, weight: 1)]
    private async Task CleanupCache()
    {
        var files = CacheFolder.GetFiles();

        var cutoffDate = DateTime.Now - TimeSpan.FromDays(AppSettings.Developer.ChunkCacheLifetime);
        foreach (var file in files)
        {
            if (file.LastWriteTime >= cutoffDate) continue;
            
            file.Delete();
        }
    }
    
    [LoadingStage("Loading Detex", stage: 3, weight: 1)]
    private async Task InitializeDetex()
    {
        var detexPath = Path.Combine(App.DataFolder.FullName, DetexHelper.DLL_NAME);
        if (!File.Exists(detexPath)) await DetexHelper.LoadDllAsync(detexPath);
        DetexHelper.Initialize(detexPath);
    }
    
    [LoadingStage("Initializing Provider", stage: 4, weight: 10)]
    private async Task InitializeProvider()
    {
        // Material Porter fork: a Custom install that streams needs Epic's token too, and the checked token goes
        // into the on-demand options (they were made at setup: an expired token stayed in them all session)
        if (AppSettings.Installation.CurrentProfile.FortniteVersion is EFortniteVersion.LatestInstalled or EFortniteVersion.LatestOnDemand
            || AppSettings.Installation.CurrentProfile.IsCustomOnDemand || Provider.LoadOnDemandTocs)
        {
            await Api.EpicGames.VerifyAuthAsync();
            if (Provider.OnDemandOptions is { } onDemand)
                onDemand.Authorization = new AuthenticationHeaderValue("Bearer", AppSettings.Application.EpicAuth?.Token);
        }
        
        switch (AppSettings.Installation.CurrentProfile.FortniteVersion)
        {
            case EFortniteVersion.LatestOnDemand:
            {
                var manifestInfo = await Api.EpicGames.GetManifestInfoAsync();
                if (manifestInfo is null) break;

                var options = OnDemandManifestOptions();
                
                var (manifest, element) = await manifestInfo.DownloadAndParseAsync(options);
                LiveManifest = manifest;
                await Provider.RegisterFiles(manifest);
                
                var manifests = await Api.Dilly.Manifests();
                if (manifests.FirstOrDefault(x => x.AppName == "Fortnite_Studio")?.DownloadUrl is { } studioDownloadUrl
                    && await Api.DownloadFileAsync(studioDownloadUrl, CacheFolder) is { } studioManifestFile)
                {
                    var studioManifestBytes = await File.ReadAllBytesAsync(studioManifestFile.FullName);
                    var studioManifest = FBuildPatchAppManifest.Deserialize(studioManifestBytes, options);
                    await Provider.RegisterFiles(studioManifest);
                }

                
                break;
            }
            // Material Porter fork: an older build, from its manifest (its keys and mappings the profile's own)
            case EFortniteVersion.Custom when AppSettings.Installation.CurrentProfile.IsCustomOnDemand:
            {
                var manifest = await CustomManifestAsync(AppSettings.Installation.CurrentProfile.ManifestPath, "no game files are loaded");
                if (manifest is null) break;

                Log.Information("On-Demand Build: {Build}", manifest.Meta.BuildVersion);
                // (Epic drops its oldest builds' files: said once, rather than a 404 per container)
                if (await MaterialPorter.OnDemandBuilds.AvailableAsync(manifest) == false)
                {
                    Log.Error("[Material Porter] {Build}: Epic's servers no longer have its files", manifest.Meta.BuildVersion);
                    Info.Message("On-Demand Build", $"Epic's servers no longer have {manifest.Meta.BuildVersion}'s files (they answer \"not found\"): nothing can be loaded. Pick a newer build.",
                        FluentAvalonia.UI.Controls.InfoBarSeverity.Error, autoClose: false);
                    break;
                }
                LiveManifest = manifest;
                await Provider.RegisterFiles(manifest);

                // the same build's UEFN: its editor data (the materials' graphs) beside the game's, as the live mode's
                if (!string.IsNullOrWhiteSpace(AppSettings.Installation.CurrentProfile.StudioManifestPath)
                    && await CustomManifestAsync(AppSettings.Installation.CurrentProfile.StudioManifestPath, "materials are approximated") is { } studio)
                {
                    Log.Information("On-Demand UEFN Build: {Build}", studio.Meta.BuildVersion);
                    if (await MaterialPorter.OnDemandBuilds.AvailableAsync(studio) == false)
                    {
                        Log.Warning("[Material Porter] {Build}: Epic's servers no longer have its UEFN files", studio.Meta.BuildVersion);
                        Info.Message("On-Demand Build", "Epic's servers no longer have this build's UEFN files: materials are approximated.",
                            FluentAvalonia.UI.Controls.InfoBarSeverity.Warning, autoClose: false);
                        break;
                    }
                    _studioEngine = await StudioEngineAsync(studio);
                    try
                    {
                        await Provider.RegisterFiles(studio);
                    }
                    catch (Exception e)
                    {
                        // (Epic's CDN no longer has the oldest UEFN builds' chunks: the game loads without them)
                        Log.Error("[Material Porter] UEFN build {Build} not downloaded: {Error}", studio.Meta.BuildVersion, e.Message);
                        Info.Message("On-Demand Build", $"This build's UEFN data couldn't be downloaded ({e.Message}): materials are approximated.",
                            FluentAvalonia.UI.Controls.InfoBarSeverity.Warning, autoClose: false);
                    }
                }
                break;
            }
            default:
            {
                await Provider.InitializeAsync();
                break;
            }
        }
    }

    // Material Porter fork: a downloaded Custom build's Unreal version (the archive leaves most builds' engine
    // out), found by reading packages every build has with each one: a balance table, some Blueprints and structs
    // (their properties' layout changes between versions), with serialization errors fatal so a wrong version fails
    // instead of reading half. The chosen version first, then the one the build's UEFN names, then newest first.
    private void DetectUnrealVersion()
    {
        var profile = AppSettings.Installation.CurrentProfile;
        if (!profile.IsCustomOnDemand || !profile.AutoUnrealVersion) return;

        // some of each: Blueprints and structs (by their usual names: S_, ...Struct...)
        List<global::CUE4Parse.FileProvider.Objects.GameFile> Sample(Func<string, bool> named, int count)
        {
            var files = Provider.Files.Values
                .Where(f => f.Extension == "uasset" && f.Path.StartsWith("FortniteGame/", StringComparison.OrdinalIgnoreCase) && named(f.Name))
                .OrderBy(f => f.Path, StringComparer.OrdinalIgnoreCase).ToList();
            return files.Where((_, i) => i % Math.Max(1, files.Count / count) == 0).Take(count).ToList();
        }
        var probes = Sample(name => name.StartsWith("BP_", StringComparison.OrdinalIgnoreCase), 6)
            .Concat(Sample(name => name.StartsWith("S_", StringComparison.OrdinalIgnoreCase)
                                   || name.Contains("Struct", StringComparison.OrdinalIgnoreCase), 6)).ToList();
        if (Provider.TryGetGameFile("FortniteGame/Content/Balance/RarityData.uasset", out var rarity)) probes.Insert(0, rarity);
        if (probes.Count == 0) return;

        // only near the chosen version and the UEFN's (3 either way): one much older reads the package header wrong,
        // and CUE4Parse's name reading then corrupts memory instead of throwing
        var chosen = Provider.Versions.Game;
        var engines = Enum.GetValues<EGame>()
            .Where(g => g is >= EGame.GAME_UE4_16 and <= EGame.GAME_UE6_0 && ((int) g & 0xFFFF) == 0).Distinct().Order().ToList();
        var anchors = new[] { chosen }.Concat(_studioEngine is { } hint ? [hint] : []).ToList();
        var candidates = anchors
            .Concat(engines.Where(g => anchors.Any(a => engines.IndexOf(a) is >= 0 and var at && Math.Abs(engines.IndexOf(g) - at) <= 3))
                .OrderByDescending(g => g))
            .Distinct();
        // the version reading the most (some packages fail with every one: not the version's doing), the first
        // in that order on a tie
        var fatal = global::CUE4Parse.Globals.FatalObjectSerializationErrors;
        global::CUE4Parse.Globals.FatalObjectSerializationErrors = true;
        var (best, bestRead) = (chosen, -1);
        try
        {
            foreach (var game in candidates)
            {
                MaterialPorter.OnDemandBuilds.SetGame(Provider.Versions, game);
                var read = probes.Count(file =>
                {
                    try
                    {
                        return Provider.LoadPackage(file).GetExports().ToList().Count > 0;
                    }
                    catch (Exception)
                    {
                        return false;
                    }
                });
                if (read > bestRead) (best, bestRead) = (game, read);
                if (read == probes.Count) break;
            }
        }
        finally
        {
            global::CUE4Parse.Globals.FatalObjectSerializationErrors = fatal;
        }

        MaterialPorter.OnDemandBuilds.SetGame(Provider.Versions, best);
        Log.Information("[Material Porter] Unreal version of this build: {Game}{Note} ({Read} of {Count} packages read)",
            best, best == chosen ? "" : " (found)", bestRead, probes.Count);
        if (best != chosen) Avalonia.Threading.Dispatcher.UIThread.Post(() => profile.UnrealVersion = best);
    }

    // the engine version the build's UEFN names (Engine/Build/Build.version, only Studio builds ship it)
    private EGame? _studioEngine;

    private static async Task<EGame?> StudioEngineAsync(FBuildPatchAppManifest studio)
    {
        try
        {
            if (studio.Files.FirstOrDefault(f => f.FileName.EndsWith("Engine/Build/Build.version", StringComparison.OrdinalIgnoreCase)) is not { } file)
                return null;
            using var reader = new StreamReader(file.GetStream());
            var json = Newtonsoft.Json.Linq.JObject.Parse(await reader.ReadToEndAsync());
            return Enum.TryParse<EGame>($"GAME_UE{(int?) json["MajorVersion"]}_{(int?) json["MinorVersion"]}", out var game) ? game : null;
        }
        catch (Exception e)
        {
            Log.Warning("[Material Porter] UEFN engine version not read: {Error}", e.Message);
            return null;
        }
    }

    private ManifestParseOptions OnDemandManifestOptions() => new()
    {
        ChunkBaseUrl = "https://egdownload.fastly-edge.com/Builds/Fortnite/CloudDir/",
        // Material Porter fork: FORTNITEPORTING_MP_CHUNK_CACHE shares one chunk cache between instances (test ones:
        // chunks are named by their hash, any build's are the same file)
        ChunkCacheDirectory = Environment.GetEnvironmentVariable("FORTNITEPORTING_MP_CHUNK_CACHE") is { Length: > 0 } shared
            ? Directory.CreateDirectory(shared).FullName : CacheFolder.FullName,
        ManifestCacheDirectory = CacheFolder.FullName,
        Decompressor = Compression.Decompressor,
        // Material Porter fork: chunks cached decompressed - cached as downloaded, every read (an IoStore block,
        // 64 KB) inflated its whole 1 MB chunk again: a map export spent most of its time there
        CacheChunksAsIs = false
    };

    // Material Porter fork: a Custom profile's build manifest - a .manifest file, or a link to one - parsed as
    // the live one is; null (and said) when it can't be had
    private async Task<FBuildPatchAppManifest?> CustomManifestAsync(string path, string without)
    {
        var source = path.Trim().Trim('"');
        try
        {
            FileInfo? file = null;
            if (Uri.TryCreate(source, UriKind.Absolute, out var uri) && uri.Scheme is "http" or "https")
                file = await Api.DownloadFileAsync(source, CacheFolder);
            else if (File.Exists(source))
                file = new FileInfo(source);
            if (file is not { Exists: true }) throw new FileNotFoundException("not found", source);

            return FBuildPatchAppManifest.Deserialize(await File.ReadAllBytesAsync(file.FullName), OnDemandManifestOptions());
        }
        catch (Exception e)
        {
            Log.Error("[Material Porter] build manifest {Source} not read: {Error}", source, e.Message);
            Info.Message("On-Demand Build", $"The build manifest \"{source}\" couldn't be read ({e.Message}): {without}.",
                FluentAvalonia.UI.Controls.InfoBarSeverity.Error, autoClose: false);
            return null;
        }
    }

    [LoadingStage("Loading Texture Streaming", stage: 5, weight: 5)]
    private async Task InitializeTextureStreaming()
    {
        // Material Porter fork: an older build downloaded from its manifest streams as the On-Demand mode does,
        // its TOC kept apart (TOC names repeat from build to build)
        var customOnDemand = AppSettings.Installation.CurrentProfile.IsCustomOnDemand;
        if (AppSettings.Installation.CurrentProfile.FortniteVersion is not (EFortniteVersion.LatestInstalled or EFortniteVersion.LatestOnDemand) && !customOnDemand) return;
        if ((AppSettings.Installation.CurrentProfile.FortniteVersion is EFortniteVersion.LatestInstalled || customOnDemand)
            && !AppSettings.Installation.CurrentProfile.UseTextureStreaming) return;

        try
        {
            var tocPath = await GetTocPath(customOnDemand ? EFortniteVersion.LatestOnDemand : AppSettings.Installation.CurrentProfile.FortniteVersion);
            if (string.IsNullOrEmpty(tocPath)) return;
            
            Log.Information("Found toc path: {tocPath}", tocPath);

            var tocName = tocPath.SubstringAfterLast("/");
            var tocFolder = customOnDemand && LiveManifest is not null
                ? Directory.CreateDirectory(Path.Combine(CacheFolder.FullName, "uondemandtoc", LiveManifest.Meta.BuildVersion)).FullName
                : CacheFolder.FullName;
            var onDemandFile = new FileInfo(Path.Combine(tocFolder, tocName));
            if (!onDemandFile.Exists || onDemandFile.Length == 0)
            {
                await Api.DownloadFileAsync($"https://download.epicgames.com/{tocPath}", onDemandFile.FullName);
            }
            
            await Provider.RegisterVfsAsync(new IoChunkToc(onDemandFile.FullName, Provider.Versions));
            await Provider.MountAsync();
        }
        catch (Exception e)
        {
            Info.Message("Failed to Initialize Texture Streaming", 
                $"Please enable the \"Pre-Download Streamed Assets\" option for Fortnite in the Epic Games Launcher and disable texture streaming in installation settings to remove this popup.");
        }
    }
    
    [LoadingStage("Submitting Keys", stage: 6, weight: 20)]
    private async Task LoadKeys()
    {
        switch (AppSettings.Installation.CurrentProfile.FortniteVersion)
        {
            case EFortniteVersion.LatestInstalled:
            case EFortniteVersion.LatestOnDemand:
            {
                var keys = _resolvedVersion?.Keys;
                if (keys?.MainKey is null)
                {
                    await LoadLocalKeys();
                    break;
                }

                Log.Information("Submitting Main Key {Key}", keys.MainKey.Key);
                await Provider.SubmitKeyAsync(Globals.ZERO_GUID, new FAesKey(keys.MainKey.Key));
                
                foreach (var key in keys.ExtraKeys)
                {
                    Log.Information("Submitting Dynamic Key {Key} with GUID {Guid}", key.Key, key.GUID);
                    await Provider.SubmitKeyAsync(new FGuid(key.GUID), new FAesKey(key.Key));
                }

                await LoadLocalExtraKeys();
                
                break;
            }
            default:
            {
                await LoadLocalKeys();
                break;
            }
        }

        // Material Porter fork: the islands unlocked by map code (their keys never logged)
        await MaterialPorter.MaterialPorterService.Instance.SubmitIslandKeysAsync(Provider!);
    }
    
    [LoadingStage("Loading Virtual Paths", stage: 7, weight: 15)]
    private async Task LoadVirtualPaths()
    {
        Provider.LoadVirtualPaths();
        Provider.PostMount();
        
        if (AppSettings.Installation.CurrentProfile.GameLanguage is not ELanguage.English 
            && !Provider.TryChangeCulture(Provider.GetLanguageCode(AppSettings.Installation.CurrentProfile.GameLanguage)))
        {
            Info.Message("Internationalization", $"Failed to load language \"{AppSettings.Installation.CurrentProfile.GameLanguage.Description}\"");
        }
    }

    [LoadingStage("Loading Mappings", stage: 8, weight: 1)]
    private async Task LoadMappings()
    {
        var mappingsPath = AppSettings.Installation.CurrentProfile.FortniteVersion switch
        {
            EFortniteVersion.LatestInstalled or EFortniteVersion.LatestOnDemand => await GetEndpointMappings() ?? GetLocalMappings(),
            _ when AppSettings.Installation.CurrentProfile.UseMappingsFile && File.Exists(AppSettings.Installation.CurrentProfile.MappingsFile) => AppSettings.Installation.CurrentProfile.MappingsFile,
            _ => string.Empty
        };

        if (string.IsNullOrEmpty(mappingsPath))
        {
            Log.Information("Failed to load mappings, path is empty");
            return;
        }
        
        Provider.MappingsContainer = new FileUsmapTypeMappingsProvider(mappingsPath, StringComparer.Ordinal);
        MaterialPorter.MappingsRepair.Repair(Provider.MappingsContainer, mappingsPath);     // Material Porter fork
        Log.Information("Loaded Mappings: {Path}", mappingsPath);
    }
    
    [LoadingStage("Loading Required Assets", stage: 9, weight: 5)]
    private async Task LoadApplicationAssets()
    {
        DetectUnrealVersion();     // Material Porter fork: before the first package is read
        if (await Provider.SafeLoadPackageObjectAsync("FortniteGame/Content/Balance/RarityData") is { } rarityData)
        {
            for (var i = 0; i < rarityData.Properties.Count; i++)
                RarityColors.Add(rarityData.GetByIndex<FRarityCollection>(i));
        }

        if (await Provider.SafeLoadPackageObjectAsync("/BeanstalkCosmetics/Cosmetics/DataTables/DT_BeanstalkCosmetics_Colors") is UDataTable beanstalkColorTable)
        {
            foreach (var (name, fallback) in beanstalkColorTable.RowMap)
            {
                var index = int.Parse(name.Text);
                BeanstalkColors[index] = fallback.GetOrDefault<FColor>("Color");
            }
        }
        
        if (await Provider.SafeLoadPackageObjectAsync("/BeanstalkCosmetics/Cosmetics/DataTables/DT_BeanstalkCosmetics_MaterialTypes") is UDataTable beanstalkMaterialTypesTable)
        {
            foreach (var (name, fallback) in beanstalkMaterialTypesTable.RowMap)
            {
                var index = int.Parse(name.Text);
                var color = new FLinearColor();
                foreach (var property in fallback.Properties)
                {
                    if (property.Tag is null) continue;
                    
                    var actualName = property.Name.Text.SubstringBefore("_");
                    switch (actualName)
                    {
                        case "Metallic":
                        {
                            color.R = (float) property.Tag.GetValue<double>();
                            break;
                        }
                        case "Roughness":
                        {
                            color.G = (float) property.Tag.GetValue<double>();
                            break;
                        }
                        case "Emissive":
                        {
                            color.B = (float) property.Tag.GetValue<double>();
                            break;
                        }
                    }
                }
                
                BeanstalkMaterialProps[index] = color;
            }
        }
        
        if (await Provider.SafeLoadPackageObjectAsync("/BeanstalkCosmetics/Cosmetics/DataTables/DT_PatternAtlasTextureSlots") is UDataTable beanstalkAtlasSlotsTable)
        {
            foreach (var (name, fallback) in beanstalkAtlasSlotsTable.RowMap)
            {
                var index = int.Parse(name.Text);
                foreach (var property in fallback.Properties)
                {
                    if (property.Tag is null) continue;
                    
                    var actualName = property.Name.Text.SubstringBefore("_");
                    if (!actualName.Equals("UV")) continue;
                    
                    BeanstalkAtlasTextureUVs[index] = property.Tag.GetValue<FVector>();
                }
            }
        }

        if (await Provider.SafeLoadPackageObjectAsync(
                "FortniteGame/Content/Athena/Items/Cosmetics/Metadata/CosmeticSets") is UDataTable cosmeticSetsTable)
        {
            foreach (var (tagName, data) in cosmeticSetsTable.RowMap)
            {
                if (data.GetOrDefault<FText?>("DisplayName") is not { } displayName) continue;
                SetNames[tagName.Text] = displayName.Text;
            }
        }
        
        foreach (var path in MaleLobbyMontagePaths)
        {
            MaleLobbyMontages.AddIfNotNull(await Provider.SafeLoadPackageObjectAsync<UAnimMontage>(path));
        }
        
        foreach (var path in FemaleLobbyMontagePaths)
        {
            FemaleLobbyMontages.AddIfNotNull(await Provider.SafeLoadPackageObjectAsync<UAnimMontage>(path));
        }
    }
    
    
    [LoadingStage("Loading Asset Registries", stage: 10, weight: 10)]
    private async Task LoadAssetRegistries()
    {
        var assetRegistries = Provider.Files
            .Where(x => x.Key.Contains("AssetRegistry", StringComparison.OrdinalIgnoreCase))
            .ToArray();
        
        foreach (var (path, file) in assetRegistries)
        {
            if (!path.EndsWith(".bin")) continue;
            if (path.Contains("Editor", StringComparison.OrdinalIgnoreCase)) continue;

            UpdateStatus($"Loading {file.Path}");
            var assetArchive = await file.SafeCreateReaderAsync();
            if (assetArchive is null) continue;

            try
            {
                var assetRegistry = new FPartialAssetRegistryState(assetArchive);
                AssetRegistry.AddRange(assetRegistry.PreallocatedAssetDataBuffers);
                Log.Information("Loaded Asset Registry: {FilePath}", file.Path);
            }
            catch (Exception e)
            {
                Log.Warning("Failed to load asset registry: {FilePath}", file.Path);
                Log.Error(e.ToString());
            }
        }
    }

    private async Task<string> GetTocPath(EFortniteVersion loadingType)
    {
        var onDemandText = string.Empty;
        switch (loadingType)
        {
            case EFortniteVersion.LatestInstalled:
            {
                var onDemandPath = Path.Combine(AppSettings.Installation.CurrentProfile.ArchiveDirectory, @"..\..\..\Cloud\IoStoreOnDemand.ini");
                if (File.Exists(onDemandPath)) onDemandText = await File.ReadAllTextAsync(onDemandPath);
                break;
            }
            case EFortniteVersion.LatestOnDemand:
            {
                var onDemandFile = LiveManifest?.Files.FirstOrDefault(x => x.FileName.Equals("Cloud/IoStoreOnDemand.ini", StringComparison.OrdinalIgnoreCase));
                if (onDemandFile is not null) onDemandText = onDemandFile.GetStream().ReadToEnd().BytesToString();
                break;
            }
        }

        if (string.IsNullOrEmpty(onDemandText)) return string.Empty;

        var onDemandIni = new ConfigIni();
        onDemandIni.Read(new StringReader(onDemandText));
        return onDemandIni
            .Sections.FirstOrDefault(section => section.Name?.Equals("Endpoint") ?? false)?
            .Tokens.OfType<InstructionToken>().FirstOrDefault(token => token.Key.Equals("TocPath"))?
            .Value.Replace("\"", string.Empty) ?? string.Empty;
    }
    
    private async Task LoadLocalKeys()
    {
        var mainKey = AppSettings.Installation.CurrentProfile.MainKey;
        if (mainKey.IsEmpty) mainKey = FileEncryptionKey.Empty;
                
        
        Log.Information("Submitting Local Main Key {Key}", mainKey.KeyString);
        await Provider.SubmitKeyAsync(Globals.ZERO_GUID, mainKey.EncryptionKey);

        await LoadLocalExtraKeys();
    }
    
    private async Task LoadLocalExtraKeys()
    {
        foreach (var vfs in Provider.UnloadedVfs.ToArray())
        {
            foreach (var extraKey in AppSettings.Installation.CurrentProfile.ExtraKeys)
            {
                if (extraKey.IsEmpty) continue;
                if (!vfs.TestAesKey(extraKey.EncryptionKey)) continue;
                        
                Log.Information("Submitting Local Extra Key {Key} with GUID {Guid} for {FileName}", extraKey.EncryptionKey, vfs.EncryptionKeyGuid, vfs.Name);
                await Provider.SubmitKeyAsync(vfs.EncryptionKeyGuid, extraKey.EncryptionKey);
            }
        }
    }
    
    private async Task<string?> GetEndpointMappings()
    {
        var mappings = _resolvedVersion?.Mappings;
        if (mappings?.Url is null) return null;

        var mappingsFilePath = Path.Combine(App.DataFolder.FullName, mappings.Url.SubstringAfterLast("/"));
        if (File.Exists(mappingsFilePath) && new FileInfo(mappingsFilePath).GetFileHashMD5().Equals(mappings.Md5Hash))
            return mappingsFilePath;
            
        var createdFile = await Api.DownloadFileAsync(mappings.Url, mappingsFilePath);
        if (createdFile is { Exists: false}) return null;
            
        File.SetCreationTime(mappingsFilePath, DateTime.Now);

        return mappingsFilePath;
    }

    private string? GetLocalMappings()
    {
        var usmapFiles = App.DataFolder.GetFiles("*.usmap");
        if (usmapFiles.Length <= 0) return null;

        var latestUsmap = usmapFiles.MaxBy(x => x.CreationTime);
        return latestUsmap?.FullName;
    }

    public async Task<(Bitmap Icon, string? DisplayName, string? ExportType)> ResolveGameFileAsync(string gameFilePath)
    {
        return await Task.Run(() =>
        {
            var fileName = gameFilePath.SubstringAfterLast("/").SubstringBefore(".");
            var fallbackIcon = ImageExtensions.AvaresBitmap("avares://FortnitePorting/Assets/Unreal/DataAsset_64x.png");

            if (!Provider.TryLoadPackage(Provider.FixPath(gameFilePath), out var package))
                return (fallbackIcon, fileName, null);

            var export = FindPrimaryExport(package, fileName);
            if (export is null)
                return (fallbackIcon, fileName, (string?) null);

            var (icon, displayName, exportType) = ResolveExportPreview(package, export);
            return (icon ?? fallbackIcon, displayName ?? fileName, exportType);
        });
    }

    private static ResolvedObject? FindPrimaryExport(IPackage package, string fileName)
    {
        ResolvedObject? namedExport = null;
        ResolvedObject? packageRootExport = null;

        for (var i = 0; i < package.ExportMapLength; i++)
        {
            var pointer = new FPackageIndex(package, i + 1).ResolvedObject;
            if (pointer?.Object is null) continue;

            var outer = pointer.Outer;
            var isPackageRoot = outer is null
                                || outer.ExportIndex < 0
                                || !ReferenceEquals(outer.Package, package);
            if (isPackageRoot)
                packageRootExport ??= pointer;

            var nameMatches = pointer.Name.Text.Equals(fileName, StringComparison.OrdinalIgnoreCase)
                              || pointer.Name.Text.Equals(fileName + "_C", StringComparison.OrdinalIgnoreCase);
            if (!nameMatches) continue;

            if (isPackageRoot)
                return pointer;

            namedExport ??= pointer;
        }

        return namedExport ?? packageRootExport;
    }

    private static (Bitmap? Icon, string? DisplayName, string? ExportType) ResolveExportPreview(
        IPackage package, ResolvedObject pointer)
    {
        var obj = ((AbstractUePackage) package).ConstructObject(pointer.Class, package);
        var exportType = obj.ExportType;
        string? displayName = null;
        Bitmap? icon = null;

        if (obj is UTexture && pointer.TryLoad(out var textureObj) &&
            textureObj is UTexture texture &&
            texture.Decode(maxMipSize: 128) is { } decodedTexture)
        {
            if (texture is UTextureCube)
                decodedTexture = decodedTexture.ToPanorama();

            return (decodedTexture.ToWriteableBitmap(), displayName, exportType);
        }

        if (obj.ExportType is "StaticMesh" or "SkeletalMesh"
            && pointer.TryLoad(out var meshObj)
            && MeshPreviewRenderer.TryRender(meshObj) is { } meshPreview)
        {
            return (meshPreview.ToWriteableBitmap(), displayName, exportType);
        }

        var assetLoader = AssetLoading.Categories
            .SelectMany(category => category.Loaders)
            .FirstOrDefault(loader => loader.ClassNames.Contains(obj.ExportType));
        if (assetLoader is not null && pointer.TryLoad(out var assetObj))
        {
            icon = (assetLoader.LowResIconHandler(assetObj) ?? assetLoader.HighResIconHandler(assetObj))
                ?.Decode(maxMipSize: 128)?.ToWriteableBitmap();
            displayName = assetLoader.DisplayNameHandler(assetObj);
            return (icon, displayName, exportType);
        }

        displayName = obj.GetAnyOrDefault<FText?>("DisplayName", "ItemName")?.Text;

        if (obj.GetEditorIconBitmap() is { } editorIcon)
            return (editorIcon, displayName, exportType);

        if (Exporter.DetermineExportType(obj) is var fnExportType and not EExportType.None
            && $"avares://FortnitePorting/Assets/FN/{fnExportType}.png" is { } exportIconPath
            && AssetLoader.Exists(new Uri(exportIconPath)))
        {
            return (ImageExtensions.AvaresBitmap(exportIconPath), displayName, exportType);
        }

        return (icon, displayName, exportType);
    }
}

public class LoadingStageAttribute : Attribute
{
    public string Name { get; }
    public int Stage { get; }
    public float Weight { get; }

    public LoadingStageAttribute(string name, int stage, float weight)
    {
        Name = name;
        Stage = stage;
        Weight = weight;
    }
}
record LoadingStage(MethodInfo Method, LoadingStageAttribute Attr);