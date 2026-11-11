using System;
using System.Collections.Generic;
using System.Collections.Specialized;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Threading.Tasks;
using Avalonia.Threading;
using CUE4Parse.UE4.Objects.Core.Math;
using FortnitePorting.Application;
using FortnitePorting.Models.Assets;
using FortnitePorting.Models.Assets.Asset;
using FortnitePorting.Exporting;
using FortnitePorting.Exporting.Models;
using FortnitePorting.Extensions;
using FortnitePorting.Services;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace FortnitePorting.MaterialPorter;

// The Assets page as data, for the Fortnite editor:
//   browse-categories                      [{category, tabs: [type]}]
//   browse?type=Outfit                     {finished, loaded, total, filters: [{category, filters: [title]}],
//                                          items: [{name, path, icon, id, rarity, series, colors, set, description,
//                                                   season, favorite, hidden, filters: [title]}]}
//   browse-favorite?type=&path=            {favorite}: FP's favorite flag toggled (its Favorites filter)
//   browse-styles?type=&path=              [{channel, selected, options: [{name, icon}]}] (icons: PNG files the app wrote)
//   browse-export?type=&path=&styles=C:O;&settings=Name:Value;   the export as the plugin receives it (Blender imports
//                                          it itself); settings: Blender export settings for this export only
//   settings-blender, settings-blender-set?name=&value=  the Blender export settings (BlenderSettingsManifest)
//   status-log?since=N                     {seq, lines: [{seq, time, text}] (after N, oldest first, at most 500),
//                                          busy, what, last}: the app's status log (StatusLog); poll with since=seq
public partial class MaterialPorterService
{
    const int StatusLogPage = 500;

    private Task<object?>? EditorRoute(string route, NameValueCollection query)
    {
        if (route == "status-log") return Task.FromResult(StatusLogRoute(query["since"]));
        if (route.StartsWith("browse")) return BrowseRouteAsync(route, query);
        if (route.StartsWith("files-")) return FilesRoute(route, query);
        if (route.StartsWith("maps-")) return MapsRoute(route, query);
        if (route.StartsWith("foliage-")) return FoliageRoute(route, query);
        if (route.StartsWith("engine-")) return Task.FromResult(EngineRoute(route, query));
        if (route == "settings-blender") return Task.FromResult<object?>(BlenderSettingsManifest.List());
        if (route == "settings-blender-set")
            return Task.FromResult(BlenderSettingsManifest.Set(query["name"] ?? throw new ArgumentException("name missing"), query["value"] ?? ""));
        return null;
    }

    // engine-status | engine-show | engine-attach?pid=N | engine-stop?pid=N (Engine: the app as the editor's background engine)
    private static object? EngineRoute(string route, NameValueCollection query) => route switch
    {
        "engine-status" => Engine.Status(),
        "engine-show" => Show(),
        "engine-attach" => Engine.Attach(int.Parse(query["pid"] ?? throw new ArgumentException("pid missing"))),
        "engine-stop" => Engine.Stop(int.Parse(query["pid"] ?? throw new ArgumentException("pid missing"))),
        _ => null,
    };

    private static object? StatusLogRoute(string? since)
    {
        var log = StatusLog.Instance;
        var (seq, lines) = log.Since(long.TryParse(since, out var after) ? after : 0, StatusLogPage);
        return new
        {
            seq,
            lines = lines.Select(e => new { seq = e.Seq, time = e.Time.ToString("HH:mm:ss.f"), text = e.Text }),
            busy = log.IsBusy,
            what = log.BusyWhat,
            last = log.LastLine,
        };
    }

    private static object Show()
    {
        Engine.Show();
        return new { shown = true };
    }

    private async Task<object?> BrowseRouteAsync(string route, NameValueCollection query)
    {
        if (route == "browse-categories")
            return AppServices.AssetLoading.Categories.Select(c => new
            {
                category = c.Category.ToString(),
                tabs = c.Loaders.Select(l => l.Type.ToString()),
            });
        var type = Enum.Parse<EExportType>(query["type"] ?? throw new ArgumentException("type missing"));
        var loader = AppServices.AssetLoading.Get(type);
        if (route == "browse")
        {
            if (!loader.FinishedLoading)
            {
                _ = loader.Load();
                return new { finished = false, loaded = loader.LoadedAssets, total = loader.TotalAssets, items = Array.Empty<object>() };
            }
            // FP's filters for this tab (its own Favorites / Hidden Items come as flags on each item)
            var filters = loader.FilterCategories
                .Where(c => c.AllowedTypes.Count == 0 || c.AllowedTypes.Contains(type))
                .Select(c => (c.Title, Filters: c.Filters.Where(f => f.Title is not ("Favorites" or "Hidden Items")).ToList()))
                .Where(c => c.Filters.Count > 0).ToList();
            var items = loader.Source.Items.OfType<AssetItem>()
                .Select(a => new
                {
                    name = a.CreationData.DisplayName, path = a.CreationData.Object.GetPathName(), icon = a.CreationData.IconPath,
                    id = a.CreationData.ID, rarity = a.Rarity.Description, series = a.Series?.DisplayName?.Text,
                    colors = a.Series?.Colors is { } c ? new[] { Hex(c.Color1), Hex(c.Color2), Hex(c.Color3) } : null,
                    set = a.SetName, description = a.CreationData.Description,
                    season = a.Season == AssetItem.INVALID_SEASON ? (int?) null : a.Season,
                    favorite = a.IsFavorite, hidden = a.CreationData.IsHidden,
                    filters = filters.SelectMany(g => g.Filters).Where(f => f.Predicate(a)).Select(f => f.Title).ToList(),
                })
                .OrderBy(a => a.name, StringComparer.OrdinalIgnoreCase);
            return new
            {
                finished = true, loaded = loader.LoadedAssets, total = loader.TotalAssets, items,
                filters = filters.Select(g => new { category = g.Title, filters = g.Filters.Select(f => new { title = f.Title, any = f.MPAnyOf }) }),
            };
        }
        var wanted = query["path"] ?? throw new ArgumentException("path missing");
        if (route == "browse-favorite")
        {
            await loader.Load();
            var asset = loader.Source.Items.OfType<AssetItem>()
                .FirstOrDefault(a => a.CreationData.Object.GetPathName().Equals(wanted, StringComparison.OrdinalIgnoreCase))
                ?? throw new FileNotFoundException("not listed");
            return await Dispatcher.UIThread.InvokeAsync(() =>
            {
                asset.Favorite();
                return new { favorite = asset.IsFavorite };
            });
        }
        var (item, info) = await AssetInfoAsync(type, a => a.CreationData.Object.GetPathName().Equals(wanted, StringComparison.OrdinalIgnoreCase));
        if (route == "browse-styles")
            return await Dispatcher.UIThread.InvokeAsync(() => info.StyleInfos.Select(c => new
            {
                channel = c.ChannelName,
                // a channel that needs no pick starts with none (a prefab's props: index -1)
                selected = c.SelectedStyleIndex >= 0 && c.SelectedStyleIndex < c.StyleDatas.Count ? c.SelectedStyle.StyleName : null,
                options = c.StyleDatas.Select(d => new { name = d.StyleName, icon = StyleIcon(wanted, c.ChannelName, d) }).ToList(),
            }).ToList());
        if (route == "browse-export")
        {
            using var status = StatusLog.Instance.Exporting($"Exporting {item.CreationData.DisplayName}");
            var styles = await Dispatcher.UIThread.InvokeAsync(() =>
            {
                Pick(info, query["styles"]);
                return ConvertStyles(info.GetSelectedStyles());
            });
            var (exportMeta, settings) = EditorExportMeta(query["settings"]);
            using var meta = exportMeta;
            var session = new ExportSession(meta);
            var data = await Task.Run(() => session.RunAsync(() => [session.CreateExport(item.CreationData.DisplayName, item.CreationData.Object, type, styles)]));
            return ExportPayload(data, settings);
        }
        return null;
    }

    /// <summary>An asset's page as the Assets view builds it (its style channels filled in).</summary>
    internal static async Task<(AssetItem Item, AssetInfo Info)> AssetInfoAsync(EExportType type, Func<AssetItem, bool> match)
    {
        var loader = AppServices.AssetLoading.Get(type);
        await loader.Load();
        var item = loader.Source.Items.OfType<AssetItem>().FirstOrDefault(match) ?? throw new FileNotFoundException("not listed");
        var stylePaths = loader.StyleDictionary.GetValueOrDefault(item.CreationData.DisplayName) ?? loader.StyleDictionary.GetValueOrDefault(item.CreationData.ID);
        var info = await Dispatcher.UIThread.InvokeAsync(() => stylePaths is not null
            ? new AssetInfo(item, stylePaths.OrderBy(x => x.EndsWith(item.CreationData.ID, StringComparison.OrdinalIgnoreCase) ? 0 : 1))
            : new AssetInfo(item));
        // the page fills its channels as their icons load: wait until the count holds
        var last = -1;
        for (var stable = 0; stable < 5; )
        {
            await Task.Delay(200);
            var count = await Dispatcher.UIThread.InvokeAsync(() => info.StyleInfos.Sum(c => c.StyleDatas.Count));
            stable = count == last ? stable + 1 : 0;
            last = count;
        }
        return (item, info);
    }

    /// <summary>Picks "Channel:Option;Channel:Option" on the page (UI thread).</summary>
    internal static void Pick(AssetInfo info, string? picks)
    {
        var listed = new HashSet<AssetStyleInfo>();
        foreach (var pick in (picks ?? "").Split(';', StringSplitOptions.RemoveEmptyEntries).Select(x => x.Split(':', 2)).Where(x => x.Length == 2))
        {
            var channel = info.StyleInfos.FirstOrDefault(c => c.ChannelName == pick[0]);
            var index = channel?.StyleDatas.ToList().FindIndex(d => d.StyleName == pick[1]) ?? -1;
            if (channel is null || index < 0) continue;
            channel.SelectedStyleIndex = index;
            // a multi-select channel (an item's "Styles": its other item definitions) exports its list, not the index;
            // the page's ListBox fills that list, so without it the export took the base item
            if (!channel.MultiSelect) continue;
            if (listed.Add(channel)) channel.SelectedItems.Clear();
            channel.SelectedItems.Add(channel.StyleDatas[index]);
        }
    }

    internal static Exporting.Styles.ExportStyleBase[] ConvertStyles(BaseStyleData[] styles)
    {
        var convert = typeof(ExportService).GetMethod("ConvertStyles", System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Static)!;
        return (Exporting.Styles.ExportStyleBase[]) convert.Invoke(null, [styles])!;
    }

    /// <summary>A Blender export's meta with the editor's per-import options ("Name:Value;..." or none) over the
    /// Blender export settings, and those settings (the payload's, so Blender imports with them too).</summary>
    internal static (ExportDataMeta Meta, ViewModels.BaseExportSettings Settings) EditorExportMeta(string? overrides)
    {
        var settings = BlenderSettingsManifest.Overridden(overrides);
        var meta = AppServices.AppSettings.ExportSettings.CreateExportMeta(EExportLocation.Blender);
        meta.Settings = settings.ToExportSettings();
        return (meta, settings);
    }

    /// <summary>An export as the plugin receives it (what the Blender TCP client would send).</summary>
    internal static JRaw ExportPayload(ExportData data, ViewModels.BaseExportSettings? settings = null) => new(JsonConvert.SerializeObject(new
    {
        MetaData = new
        {
            data.MetaData.Version,
            data.MetaData.AssetsRoot,
            Settings = settings ?? AppServices.AppSettings.ExportSettings.GetSettingsViewModel(EExportLocation.Blender),
        },
        data.Exports,
    }));

    private static string Hex(FLinearColor color)
    {
        var c = color.ToFColor(true);
        return $"#{c.R:X2}{c.G:X2}{c.B:X2}";
    }

    /// <summary>A style option's picture as a PNG the app writes once (the page only holds it as a bitmap).</summary>
    private static string? StyleIcon(string assetPath, string channel, BaseStyleData style)
    {
        if (style.StyleDisplayImage is not { } image) return null;
        var key = Convert.ToHexString(SHA1.HashData(Encoding.UTF8.GetBytes($"{assetPath}|{channel}|{style.StyleName}")))[..16];
        var file = Path.Combine(AppServices.App.DataFolder.FullName, "editor-styles", key + ".png");
        if (!File.Exists(file))
        {
            Directory.CreateDirectory(Path.GetDirectoryName(file)!);
            image.Save(file);
        }
        return file;
    }
}
