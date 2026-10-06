using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Net.Http;
using System.Text.RegularExpressions;
using System.Threading.Tasks;
using EpicManifestParser;
using EpicManifestParser.UE;
using Newtonsoft.Json.Linq;
using Serilog;

namespace FortnitePorting.MaterialPorter;

/// <summary>A Fortnite build a Custom profile can download: its version, its manifest (a file or a link), its UEFN one when known.</summary>
public record OnDemandBuild(string Version, string Build, string Manifest, string? StudioManifest, string Source, string? StudioBuild = null)
{
    // (a UEFN of the same version but another changelist - a hotfix's - says which)
    public override string ToString() => $"{Version}   {Build[(Version.Length + 1)..]}   {Source}"
        + (StudioManifest is null ? "" : StudioBuild is null || StudioBuild == Build ? " + UEFN" : $" + UEFN {StudioBuild[(Version.Length + 1)..]}");
}

/// <summary>
/// Material Porter fork: the builds a Custom profile can download (Download Build) - Epic's API lists only the
/// live one. The launcher keeps the manifests of the builds it installed in the install's .egstore, the UEFN
/// (Studio) one of the same build beside it: its editor data gives exact materials. The fn-releases archive
/// keeps every public Windows build's game manifest, the UEFN-releases one every UEFN build's (24.01 on; Epic's
/// CDN no longer has the oldest ones' chunks).
/// </summary>
public static partial class OnDemandBuilds
{
    const string ArchiveReadme = "https://raw.githubusercontent.com/polynite/fn-releases/master/README.md";
    const string ArchiveManifest = "https://raw.githubusercontent.com/polynite/fn-releases/master/manifests/{0}.manifest";
    const string GameApp = "FortniteReleaseBuilds";
    const string StudioApp = "FortniteReleaseBuilds_Studio";
    const string StudioReadme = "https://raw.githubusercontent.com/Mast3rGamers/UEFN-releases/main/README.md";
    const string StudioManifestUrl = "https://raw.githubusercontent.com/Mast3rGamers/UEFN-releases/main/archive/{0}.manifest";
    const string StudioListing = "https://api.github.com/repos/Mast3rGamers/UEFN-releases/contents/archive";
    const string GameToc = "FortniteGame/Content/Paks/global.utoc";
    const string StudioToc = "FortniteGame/Content/Paks/UEFNFortniteGame-WindowsUEFN.utoc";

    [GeneratedRegex(@"(\d+\.\d+)-CL-(\d+)")]
    private static partial Regex BuildRx();

    /// <summary>This PC's builds first (the launcher's, with UEFN when it kept that), then the archive's; newest first.</summary>
    public static async Task<List<OnDemandBuild>> FindAsync(IEnumerable<string> archiveDirectories)
    {
        using var client = new HttpClient();
        client.DefaultRequestHeaders.UserAgent.ParseAdd("FortnitePorting");
        var installed = await Task.Run(() => Installed(archiveDirectories));
        var archived = await ArchivedAsync(client);
        var studios = await ArchivedStudiosAsync(client);
        return installed.Concat(archived.Where(a => installed.All(i => i.Build != a.Build)))
            .Select(b => b.StudioManifest is null ? WithStudio(b, studios) : b)
            .OrderByDescending(b => VersionKey(b.Version)).ThenByDescending(b => b.Build, StringComparer.Ordinal).ToList();
    }

    // the archive's UEFN of the same changelist, else of the same version (the nearest changelist)
    private static OnDemandBuild WithStudio(OnDemandBuild build, List<(string Version, string Build, string Manifest)> studios)
    {
        var studio = studios.FirstOrDefault(s => s.Build == build.Build) is { Manifest: not null } same ? same
            : studios.Where(s => s.Version == build.Version).OrderBy(s => Math.Abs(Changelist(s.Build) - Changelist(build.Build)))
                .FirstOrDefault();
        return studio.Manifest is null ? build : build with { StudioManifest = studio.Manifest, StudioBuild = studio.Build };
    }

    private static List<OnDemandBuild> Installed(IEnumerable<string> archiveDirectories)
    {
        var games = new Dictionary<string, (string Version, string Path)>();
        var studios = new Dictionary<string, string>();
        foreach (var folder in StoreFolders(archiveDirectories))
        foreach (var file in Directory.EnumerateFiles(folder, "*.manifest"))
        {
            try
            {
                var manifest = FBuildPatchAppManifest.Deserialize(File.ReadAllBytes(file), new ManifestParseOptions());
                var meta = manifest.Meta;
                if (BuildRx().Match(meta.BuildVersion) is not { Success: true } match) continue;
                var build = match.Value;
                // (newer launchers name the app by an id: what it installs tells)
                if (meta.AppName == GameApp || manifest.Files.Any(f => f.FileName.Equals(GameToc, StringComparison.OrdinalIgnoreCase)))
                    games.TryAdd(build, (match.Groups[1].Value, file));
                else if (meta.AppName == StudioApp || manifest.Files.Any(f => f.FileName.Equals(StudioToc, StringComparison.OrdinalIgnoreCase)))
                    studios.TryAdd(build, file);
            }
            catch (Exception e)
            {
                Exporting.MaterialPorter.Failures.Note("installed build manifests", file, e);
            }
        }
        return games.Select(g => new OnDemandBuild(g.Value.Version, g.Key, g.Value.Path, studios.GetValueOrDefault(g.Key), "this PC")).ToList();
    }

    // the .egstore folders of the Fortnite installs the launcher knows, and of the profiles' archive directories
    private static IEnumerable<string> StoreFolders(IEnumerable<string> archiveDirectories)
    {
        var roots = new List<string>();
        var launcher = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData), "Epic", "EpicGamesLauncher", "Data", "Manifests");
        if (Directory.Exists(launcher))
        {
            foreach (var item in Directory.EnumerateFiles(launcher, "*.item"))
            {
                try
                {
                    var json = JObject.Parse(File.ReadAllText(item));
                    if ((string?) json["AppName"] == "Fortnite" && (string?) json["InstallLocation"] is { Length: > 0 } location) roots.Add(location);
                }
                catch (Exception e)
                {
                    Exporting.MaterialPorter.Failures.Note("Epic launcher items", item, e);
                }
            }
        }
        // (an archive directory is the install's FortniteGame/Content/Paks)
        roots.AddRange(archiveDirectories.Where(d => !string.IsNullOrWhiteSpace(d)).Select(d => Path.Combine(d, "..", "..", "..")));
        return roots.Select(r => Path.GetFullPath(Path.Combine(r, ".egstore"))).Distinct(StringComparer.OrdinalIgnoreCase).Where(Directory.Exists);
    }

    private static async Task<List<OnDemandBuild>> ArchivedAsync(HttpClient client)
    {
        try
        {
            var readme = await client.GetStringAsync(ArchiveReadme);
            var builds = new List<OnDemandBuild>();
            // | Build version | Engine version | Net CL | Build date | Manifest | Notes |
            foreach (var line in readme.Split('\n'))
            {
                var cells = line.Trim().Trim('|').Split('|').Select(c => c.Trim()).ToArray();
                if (cells.Length < 5 || cells[4].Length == 0 || BuildRx().Match(cells[0]) is not { Success: true } match) continue;
                builds.Add(new OnDemandBuild(match.Groups[1].Value, match.Value, string.Format(ArchiveManifest, cells[4]), null, "archive"));
            }
            return builds;
        }
        catch (Exception e)
        {
            Log.Warning("[Material Porter] the fn-releases archive's builds not listed: {Error}", e.Message);
            return [];
        }
    }

    // | UEFN 33.11 CL-38773622 | <manifest id> | - the ones whose manifest the archive has
    private static async Task<List<(string Version, string Build, string Manifest)>> ArchivedStudiosAsync(HttpClient client)
    {
        try
        {
            var readme = await client.GetStringAsync(StudioReadme);
            HashSet<string>? files = null;
            try
            {
                files = JArray.Parse(await client.GetStringAsync(StudioListing)).Select(f => (string?) f["name"] ?? "").ToHashSet();
            }
            catch (Exception e)
            {
                // (GitHub's API allows 60 calls an hour: every listed one, then)
                Log.Warning("[Material Porter] the UEFN archive's file list not read, its manifests not checked: {Error}", e.Message);
            }
            var studios = new List<(string, string, string)>();
            foreach (var line in readme.Split('\n'))
            {
                var cells = line.Trim().Trim('|').Split('|').Select(c => c.Trim()).ToArray();
                // (its build written "33.11 CL-38773622")
                if (cells.Length < 2 || !cells[0].StartsWith("UEFN ") || BuildRx().Match(cells[0].Replace(" CL-", "-CL-")) is not { Success: true } match) continue;
                if (cells[1].Length == 0 || files is not null && !files.Contains(cells[1] + ".manifest")) continue;
                studios.Add((match.Groups[1].Value, match.Value, string.Format(StudioManifestUrl, cells[1])));
            }
            return studios;
        }
        catch (Exception e)
        {
            Log.Warning("[Material Porter] the UEFN archive's builds not listed: {Error}", e.Message);
            return [];
        }
    }

    // CUE4Parse's ini-read options (PostMount: DefaultEngine.ini's ConsoleVariables), which a version change resets to
    // their defaults - a build with r.SkeletalMesh.KeepMobileMinLODSettingOnDesktop=1 (36.10) then reads its skeletal
    // meshes' MinMobileLOD as their LOD count: 0 LODs, nothing exported
    static readonly string[] IniOptions = ["StripAdditiveRefPose", "SkeletalMesh.KeepMobileMinLODSettingOnDesktop", "StaticMesh.KeepMobileMinLODSettingOnDesktop"];

    /// <summary>Reads with another Unreal version, keeping the options the build's config set.</summary>
    public static void SetGame(global::CUE4Parse.UE4.Versions.VersionContainer versions, global::CUE4Parse.UE4.Versions.EGame game)
    {
        var kept = IniOptions.Where(versions.Options.ContainsKey).ToDictionary(key => key, key => versions.Options[key]);
        versions.Game = game;
        foreach (var (key, value) in kept) versions.Options[key] = value;
    }

    private static long Changelist(string build) => long.TryParse(build[(build.IndexOf("-CL-", StringComparison.Ordinal) + 4)..], out var cl) ? cl : 0;

    private static (int, int) VersionKey(string version)
    {
        var parts = version.Split('.');
        return (int.TryParse(parts[0], out var major) ? major : 0, parts.Length > 1 && int.TryParse(parts[1], out var minor) ? minor : 0);
    }
}
