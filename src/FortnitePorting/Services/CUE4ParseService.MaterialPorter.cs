using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Net.Http.Headers;
using System.Threading.Tasks;
using CUE4Parse.Compression;
using CUE4Parse.UE4.Versions;
using EpicManifestParser;
using EpicManifestParser.UE;
using FluentAvalonia.UI.Controls;
using FortnitePorting.Models.Installation;
using Serilog;

namespace FortnitePorting.Services;

// Material Porter fork: exact materials and builds downloaded from their manifest.
public partial class CUE4ParseService
{
    private InstallationProfile Profile => AppSettings.Installation.CurrentProfile;

    private void OnLoadingStarted()
    {
        MaterialPorter.MaterialPorterService.Instance.OnGameLoading();
        // downloaded files are prefetched beside an export; a local install's are already at hand
        Exporting.MaterialPorter.Prefetch.Enabled = Profile.IsCustomOnDemand || Profile.FortniteVersion is EFortniteVersion.LatestOnDemand;
    }

    // a downloaded build caches its material graphs under its own version
    private void OnLoadingFinished() =>
        MaterialPorter.MaterialPorterService.Instance.OnGameLoaded(Provider!, _resolvedVersion?.Version ?? LiveManifest?.Meta.BuildVersion);

    // a Custom install that streams needs Epic's token too
    private bool NeedsEpicAuth => Profile.IsCustomOnDemand || Provider.LoadOnDemandTocs;

    // the on-demand options were made at setup: give them the token just checked, not an expired one
    private void RefreshOnDemandAuth()
    {
        if (Provider.OnDemandOptions is { } onDemand)
            onDemand.Authorization = new AuthenticationHeaderValue("Bearer", AppSettings.Application.EpicAuth?.Token);
    }

    private ManifestParseOptions OnDemandManifestOptions() => new()
    {
        ChunkBaseUrl = "https://egdownload.fastly-edge.com/Builds/Fortnite/CloudDir/",
        // FORTNITEPORTING_MP_CHUNK_CACHE shares one chunk cache between test instances (chunks are named by hash)
        ChunkCacheDirectory = Environment.GetEnvironmentVariable("FORTNITEPORTING_MP_CHUNK_CACHE") is { Length: > 0 } shared
            ? Directory.CreateDirectory(shared).FullName : CacheFolder.FullName,
        ManifestCacheDirectory = CacheFolder.FullName,
        Decompressor = Compression.Decompressor,
        // cached as downloaded, every 64 KB read inflated its whole 1 MB chunk again
        CacheChunksAsIs = false
    };

    private async Task LoadDownloadedBuildAsync()
    {
        var manifest = await CustomManifestAsync(Profile.ManifestPath, "no game files are loaded");
        if (manifest is null) return;

        Log.Information("On-Demand Build: {Build}", manifest.Meta.BuildVersion);
        // Epic drops its oldest builds' files: say so once instead of a 404 per container
        if (await MaterialPorter.OnDemandBuilds.AvailableAsync(manifest) == false)
        {
            Log.Error("[Material Porter] {Build}: Epic's servers no longer have its files", manifest.Meta.BuildVersion);
            Info.Message("On-Demand Build", $"Epic's servers no longer have {manifest.Meta.BuildVersion}'s files (they answer \"not found\"): nothing can be loaded. Pick a newer build.",
                InfoBarSeverity.Error, autoClose: false);
            return;
        }
        LiveManifest = manifest;
        await Provider.RegisterFiles(manifest);

        // the same build's UEFN carries the materials' graphs
        if (string.IsNullOrWhiteSpace(Profile.StudioManifestPath)
            || await CustomManifestAsync(Profile.StudioManifestPath, "materials are approximated") is not { } studio)
            return;

        Log.Information("On-Demand UEFN Build: {Build}", studio.Meta.BuildVersion);
        if (await MaterialPorter.OnDemandBuilds.AvailableAsync(studio) == false)
        {
            Log.Warning("[Material Porter] {Build}: Epic's servers no longer have its UEFN files", studio.Meta.BuildVersion);
            Info.Message("On-Demand Build", "Epic's servers no longer have this build's UEFN files: materials are approximated.",
                InfoBarSeverity.Warning, autoClose: false);
            return;
        }
        _studioEngine = await StudioEngineAsync(studio);
        try
        {
            await Provider.RegisterFiles(studio);
        }
        catch (Exception e)
        {
            Log.Error("[Material Porter] UEFN build {Build} not downloaded: {Error}", studio.Meta.BuildVersion, e.Message);
            Info.Message("On-Demand Build", $"This build's UEFN data couldn't be downloaded ({e.Message}): materials are approximated.",
                InfoBarSeverity.Warning, autoClose: false);
        }
    }

    // a .manifest file or a link to one; null (and said) when it can't be read
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
                InfoBarSeverity.Error, autoClose: false);
            return null;
        }
    }

    // the engine version the build's UEFN names (Engine/Build/Build.version, Studio builds only)
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

    // A downloaded build's Unreal version (the archive leaves most builds' engine out): the one that reads the most
    // of a few packages every build has, with serialization errors made fatal so a wrong version fails outright.
    // Tried in order: the chosen version, the one its UEFN names, then nearby versions newest first.
    private void DetectUnrealVersion()
    {
        var profile = Profile;
        if (!profile.IsCustomOnDemand || !profile.AutoUnrealVersion) return;

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

        // only 3 versions either way of the anchors: a much older one misreads the package header and
        // CUE4Parse's name reading then corrupts memory instead of throwing
        var chosen = Provider.Versions.Game;
        var engines = Enum.GetValues<EGame>()
            .Where(g => g is >= EGame.GAME_UE4_16 and <= EGame.GAME_UE6_0 && ((int) g & 0xFFFF) == 0).Distinct().Order().ToList();
        var anchors = new[] { chosen }.Concat(_studioEngine is { } hint ? [hint] : []).ToList();
        var candidates = anchors
            .Concat(engines.Where(g => anchors.Any(a => engines.IndexOf(a) is >= 0 and var at && Math.Abs(engines.IndexOf(g) - at) <= 3))
                .OrderByDescending(g => g))
            .Distinct();
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
}
