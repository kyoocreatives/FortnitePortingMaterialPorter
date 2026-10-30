using System;
using System.Linq;
using System.Net.Http;
using System.Threading.Tasks;
using FortnitePorting.Models;
using FortnitePorting.Models.Information;
using FortnitePorting.Services;
using Newtonsoft.Json.Linq;
using Serilog;

namespace FortnitePorting.MaterialPorter;

/// <summary>Checks the fork's GitHub releases (tagged v4.3.4-mp.1.12.3).</summary>
// FP's updater would install upstream over the fork; "-mp." makes a release a dev build to FP, so FP never asks.
public static class ForkUpdates
{
    public const string Repository = "kyoocreatives/FortnitePortingMaterialPorter";

    public static async Task CheckAsync(InfoService info, AppService app)
    {
        // non-release builds (-dev, commit builds) don't check
        if (ForkVersion.MaterialPorter(Globals.VersionString) is not { } current) return;
        try
        {
            using var client = new HttpClient();
            client.DefaultRequestHeaders.UserAgent.ParseAdd("FortnitePortingMP");
            // the whole list, not releases/latest: GitHub's "latest" stays on the bridge release, for apps from before this numbering
            var releases = JArray.Parse(await client.GetStringAsync($"https://api.github.com/repos/{Repository}/releases?per_page=100"))
                .Where(release => !release.Value<bool>("draft") && !release.Value<bool>("prerelease")).ToList();
            // an old mp.N tag has no Material Porter version: older than any build that can read this
            if (ForkVersion.Newest(releases.Select(release => release.Value<string>("tag_name")!)) is not { } tag
                || ForkVersion.MaterialPorter(tag)! <= current
                || releases.First(release => release.Value<string>("tag_name") == tag).Value<string>("html_url") is not { } page) return;
            info.Dialog($"Update {ForkVersion.Display(tag)}", "A new version of FortnitePorting MP is out.", buttons: [
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
}
