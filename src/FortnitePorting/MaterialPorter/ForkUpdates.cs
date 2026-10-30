using System;
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
            var release = JObject.Parse(await client.GetStringAsync($"https://api.github.com/repos/{Repository}/releases/latest"));
            if (release.Value<string>("tag_name") is not { } tag || release.Value<string>("html_url") is not { } page) return;
            // an old mp.N tag has no Material Porter version: older than any build that can read this
            if (ForkVersion.MaterialPorter(tag) is not { } latest || latest <= current) return;
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
