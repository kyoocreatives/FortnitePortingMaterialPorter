using System;
using System.Net.Http;
using System.Threading.Tasks;
using FortnitePorting.Models;
using FortnitePorting.Models.Information;
using FortnitePorting.Services;
using Newtonsoft.Json.Linq;
using Serilog;

namespace FortnitePorting.MaterialPorter;

/// <summary>Checks the fork's GitHub releases (tagged v4.0.0-mp.N).</summary>
// FP's updater would install upstream over the fork; "-mp.N" makes a release a dev build to FP, so FP never asks.
public static class ForkUpdates
{
    public const string Repository = "kyoocreatives/FortnitePortingMaterialPorter";

    public static async Task CheckAsync(InfoService info, AppService app)
    {
        // non-release builds (-dev, commit builds) don't check
        if (Release(Globals.Version) is not (var currentBase, var currentBuild)) return;
        try
        {
            using var client = new HttpClient();
            client.DefaultRequestHeaders.UserAgent.ParseAdd("FortnitePortingMP");
            var release = JObject.Parse(await client.GetStringAsync($"https://api.github.com/repos/{Repository}/releases/latest"));
            if (release.Value<string>("tag_name") is not { } tag || release.Value<string>("html_url") is not { } page) return;
            if (Release(new FPVersion(tag)) is not (var latestBase, var latestBuild)
                || latestBase < currentBase || latestBase == currentBase && latestBuild <= currentBuild) return;
            info.Dialog($"Update {tag}", "A new version of FortnitePorting MP is out.", buttons: [
                new DialogButton
                {
                    Text = "Download",
                    IsPrimary = true,
                    Action = () => app.Launch(page)
                },
                new DialogButton
                {
                    Text = "Cancel"
                }
            ]);
        }
        catch (Exception e)
        {
            Log.Warning("[Material Porter] update check failed: {Error}", e.Message);
        }
    }

    /// <summary>A release's FP version and build (N of "mp.N"), compared numerically since mp.10 sorts before mp.9 as text.</summary>
    private static (FPVersion Base, int Build)? Release(FPVersion version) =>
        version.Identifier.StartsWith("mp.") && int.TryParse(version.Identifier[3..], out var build)
            ? (new FPVersion(version.Release, version.Major, version.Minor, version.Patch), build)
            : null;
}
