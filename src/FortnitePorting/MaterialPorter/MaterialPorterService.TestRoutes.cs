#if DEBUG
using System;
using System.Collections.Generic;
using System.Collections.Specialized;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using CUE4Parse.Encryption.Aes;
using CUE4Parse.FileProvider;
using CUE4Parse.FileProvider.Vfs;
using CUE4Parse.UE4.Assets.Exports;
using CUE4Parse.UE4.Assets.Objects;
using CUE4Parse.UE4.Objects.Core.i18N;
using CUE4Parse.UE4.Objects.Core.Misc;
using CUE4Parse.UE4.Objects.Engine;
using FortnitePorting.Application;
using FortnitePorting.Exporting;
using FortnitePorting.Exporting.MaterialPorter;
using FortnitePorting.Services;
using Microsoft.Extensions.DependencyInjection;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using Serilog;

namespace FortnitePorting.MaterialPorter;

// Bridge routes only the dev tools use (testapp.sh, health.py, baseline.py); Debug builds only.
public partial class MaterialPorterService
{
    // An item's styles by name (styles=Violet Board Jules;...): each name's option from the item's ItemVariants
    // (the one named so, else the first whose name contains it), as the asset page passes it.
    private static IEnumerable<Exporting.Styles.ExportStyleBase> PickedStyles(UObject asset, string? names)
    {
        if (string.IsNullOrWhiteSpace(names)) yield break;
        // each option carries its channel name: "jacket:On" picks one where several channels have an On
        var options = new List<(string Channel, FStructFallback Option)>();
        foreach (var variant in asset.GetOrDefault("ItemVariants", Array.Empty<UObject>()))
        {
            var channel = variant.GetOrDefault<FText?>("VariantChannelName")?.Text ?? "";
            foreach (var key in new[] { "PartOptions", "MaterialOptions", "ParticleOptions", "MeshOptions", "GenericTagOptions", "MorphTargetOptions", "Variants" })
                if (variant.TryGetValue(out FStructFallback[] list, key)) options.AddRange(list.Select(o => (channel, o)));
        }
        string Name(FStructFallback o) => o.GetOrDefault<FText?>("VariantName")?.Text ?? "";
        foreach (var pick in names.Split(';', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries))
        {
            var parts = pick.Split(':', 2);
            var (channel, wanted) = parts.Length == 2 ? (parts[0], parts[1]) : ("", pick);
            var mine = options.Where(c => channel.Length == 0 || c.Channel.Equals(channel, StringComparison.OrdinalIgnoreCase)).Select(c => c.Option).ToList();
            var option = mine.FirstOrDefault(o => Name(o).Equals(wanted, StringComparison.OrdinalIgnoreCase))
                         ?? mine.FirstOrDefault(o => Name(o).Contains(wanted, StringComparison.OrdinalIgnoreCase))
                         ?? throw new ArgumentException($"no style named {pick} (styles: {string.Join(", ", options.Select(c => c.Channel + ":" + Name(c.Option)))})");
            yield return new Exporting.Styles.ExportStructStyle { StyleData = option };
        }
    }


    // Renders the window as shown to a PNG (on the UI thread).
    private static Avalonia.PixelSize SaveShot(Avalonia.Controls.Window window, string file)
    {
        var size = new Avalonia.PixelSize((int) window.Bounds.Width, (int) window.Bounds.Height);
        using var shot = new Avalonia.Media.Imaging.RenderTargetBitmap(size);
        shot.Render(window);
        shot.Save(file);
        return size;
    }

    private async Task<object?> TestRouteAsync(string route, NameValueCollection query)
    {
        if (route == "fork-projects")
        {
            // UEFN projects found, which island is which, and what graphs came from where so far
            var (ownAssets, otherAssets, others, pairs) = IslandProjects.Counts();
            return new
            {
                roots = IslandProjects.Roots,
                projects = IslandProjects.All.Select(p => new { p.Name, p.Folder, p.Mount }),
                pairs, ownAssets, otherAssets, others = others.Take(50),
            };
        }
        if (route == "fork-car")
        {
            // a car body's channels and what the picks ("0:1,5:3") give
            var picks = (query["picks"] ?? "").Split(',', StringSplitOptions.RemoveEmptyEntries)
                .Select(x => x.Split(':')).Where(x => x.Length == 2).ToDictionary(x => int.Parse(x[0]), x => int.Parse(x[1]));
            var plan = await CarPlanAsync(query["path"] ?? throw new ArgumentException("path missing"), picks);
            return new
            {
                // all=1: every option with its item's package, to find one by its asset
                channels = plan.Channels.Select(c => new { c.Name, c.Default, options = c.Options.Count,
                    first = query["all"] == "1" ? c.Options.Select(o => o.Name + " | " + (o.IconItem?.Package ?? o.Icon)) : c.Options.Take(4).Select(o => o.Name) }),
                plan.Styles, plan.BodyMesh, plan.BodyOverrides, plan.WheelMesh,
                wheels = plan.Wheels.Select(w => new { w.Label, at = new[] { w.Transform.M41, w.Transform.M42, w.Transform.M43 } }),
                @params = plan.Params.ToDictionary(kv => kv.Key, kv => new { kv.Value.Vectors, kv.Value.Scalars }),
            };
        }
        if (route == "fork-assets-page")
        {
            // Opens the Assets page as a click does (its view model builds the tabs' sidebar) and reports the
            // sidebar entries and each tab's type, with whether it has its own icon.
            await Avalonia.Threading.Dispatcher.UIThread.InvokeAsync(() => AppServices.Navigation.App.Open<Views.AssetsView>());
            await Task.Delay(TimeSpan.FromSeconds(double.TryParse(query["wait"], System.Globalization.CultureInfo.InvariantCulture, out var pause) ? pause : 6));
            return await Avalonia.Threading.Dispatcher.UIThread.InvokeAsync(() =>
            {
                var model = AppServices.Services.GetRequiredService<ViewModels.AssetsViewModel>();
                var tabs = AppServices.AssetLoading.Categories.SelectMany(c => c.Loaders).Select(l => new
                {
                    Type = l.Type.ToString(),
                    OwnIcon = Avalonia.Platform.AssetLoader.Exists(new Uri($"avares://FortnitePorting/Assets/FN/{l.Type}.png")),
                    Icon = TabIcons.Of(l.Type).PixelSize.Width,
                }).ToList();
                return new { model.IsInitialized, Sidebar = model.SidebarItems.Count, Tabs = tabs.Count, WithoutOwnIcon = tabs.Where(t => !t.OwnIcon).Select(t => t.Type), tabs };
            });
        }
        if (route == "fork-screenshot")
        {
            // The window showing an asset tab (type=; search=, filters=Title,Title, select=<display name> opens its info),
            // rendered to a PNG (path=) after wait= seconds (icons load).
            var window = AppServices.App.Lifetime.MainWindow!;
            await Avalonia.Threading.Dispatcher.UIThread.InvokeAsync(() =>
            {
                window.WindowState = Avalonia.Controls.WindowState.Normal;
                window.Width = double.TryParse(query["width"], out var w) ? w : 1600;
                window.Height = double.TryParse(query["height"], out var h) ? h : 950;
                AppServices.Navigation.App.Open<Views.AssetsView>();
            });
            var assetsModel = AppServices.Services.GetRequiredService<ViewModels.AssetsViewModel>();
            for (var i = 0; i < 60 && !assetsModel.IsInitialized; i++) await Task.Delay(500);
            if (query["type"] is { } shown)
            {
                // as a click on the tab does, then once it has listed
                await Avalonia.Threading.Dispatcher.UIThread.InvokeAsync(() => AppServices.Navigation.Assets.Open(Enum.Parse<EExportType>(shown)));
                var listing = AppServices.AssetLoading.Get(Enum.Parse<EExportType>(shown));
                for (var i = 0; i < 600 && !listing.FinishedLoading; i++) await Task.Delay(500);
            }
            await Avalonia.Threading.Dispatcher.UIThread.InvokeAsync(() =>
            {
                if (query["type"] is not null)
                {
                    var tab = AppServices.AssetLoading.ActiveLoader!;
                    foreach (var active in tab.ActiveFilters.ToList()) tab.UpdateFilters(active, false);
                    tab.SelectedAssetInfos = [];
                    tab.SearchFilter = query["search"] ?? "";
                    foreach (var title in (query["filters"] ?? "").Split(',', StringSplitOptions.RemoveEmptyEntries))
                        if (tab.FilterCategories.SelectMany(c => c.Filters).FirstOrDefault(f => f.Title == title) is { } filter)
                            tab.UpdateFilters(filter, true);
                    if (query["select"] is { } pick && tab.Source.Items.OfType<Models.Assets.Asset.AssetItem>()
                            .FirstOrDefault(a => a.CreationData.DisplayName.Equals(pick, StringComparison.OrdinalIgnoreCase)) is { } item)
                        tab.SelectedAssetInfos = [new Models.Assets.Asset.AssetInfo(item)];
                }
            });
            await Task.Delay(TimeSpan.FromSeconds(double.TryParse(query["wait"], System.Globalization.CultureInfo.InvariantCulture, out var settle) ? settle : 8));
            return await Avalonia.Threading.Dispatcher.UIThread.InvokeAsync(() =>
            {
                var file = query["path"] ?? throw new ArgumentException("path missing");
                var size = SaveShot(window, file);
                return new { file, size.Width, size.Height, AppServices.AssetLoading.ActiveLoader?.Filtered.Count };
            });
        }
        if (route == "fork-settings-shot" && query["page"] == "installation")
        {
            // The Installation settings page rendered to a PNG (path=, height=); custom=1 shows the profile as a
            // Custom one downloading its build (put back after).
            var window = AppServices.App.Lifetime.MainWindow!;
            var installation = AppServices.AppSettings.Installation;
            (EFortniteVersion Version, bool Download)? was = null;
            await Avalonia.Threading.Dispatcher.UIThread.InvokeAsync(() =>
            {
                window.WindowState = Avalonia.Controls.WindowState.Normal;
                window.Width = 1600;
                window.Height = int.TryParse(query["height"], out var tall) ? tall : 1100;
                AppServices.Navigation.App.Open<Views.SettingsView>();
            });
            // the settings pane's own frame exists once its view has loaded
            await Task.Delay(TimeSpan.FromSeconds(1));
            await Avalonia.Threading.Dispatcher.UIThread.InvokeAsync(() =>
            {
                AppServices.Navigation.Settings.Open<Views.Settings.InstallationSettingsView>();
                // profile=<name start>: that profile's page
                if (query["profile"] is { } named && installation.Profiles.FirstOrDefault(p => p.ProfileName.StartsWith(named, StringComparison.OrdinalIgnoreCase)) is { } picked)
                    installation.SelectedEditProfile = picked;
                installation.SelectedEditProfile ??= installation.Profiles.FirstOrDefault(p => p.IsSelected) ?? installation.Profiles.FirstOrDefault();
                if (query["custom"] == "1" && installation.SelectedEditProfile is { } shown)
                {
                    was = (shown.FortniteVersion, shown.DownloadFromManifest);
                    shown.FortniteVersion = EFortniteVersion.Custom;
                    shown.DownloadFromManifest = true;
                }
            });
            await Task.Delay(TimeSpan.FromSeconds(3));
            return await Avalonia.Threading.Dispatcher.UIThread.InvokeAsync(() =>
            {
                var file = query["path"] ?? throw new ArgumentException("path missing");
                var size = SaveShot(window, file);
                var profile = installation.SelectedEditProfile;
                var custom = profile?.IsCustomOnDemand;
                if (was is { } before && profile is not null)
                {
                    profile.FortniteVersion = before.Version;
                    profile.DownloadFromManifest = before.Download;
                }
                return new { file, size.Width, size.Height, CustomOnDemand = custom };
            });
        }
        if (route == "fork-settings-shot" && query["page"] == "application")
        {
            // The Application settings page rendered to a PNG (path=); folders=A;B lists those UEFN project
            // folders for the picture only (taken out again).
            var window = AppServices.App.Lifetime.MainWindow!;
            var shown = (query["folders"] ?? "").Split(';', StringSplitOptions.RemoveEmptyEntries).ToList();
            var settings = AppServices.AppSettings.Application;
            await Avalonia.Threading.Dispatcher.UIThread.InvokeAsync(() =>
            {
                window.WindowState = Avalonia.Controls.WindowState.Normal;
                window.Width = 1600;
                window.Height = 1100;
                foreach (var folder in shown) settings.UefnProjectFolders.Add(folder);
                AppServices.Navigation.App.Open<Views.SettingsView>();
                AppServices.Navigation.Settings.Open<Views.Settings.ApplicationSettingsView>();
            });
            await Task.Delay(TimeSpan.FromSeconds(3));
            return await Avalonia.Threading.Dispatcher.UIThread.InvokeAsync(() =>
            {
                var file = query["path"] ?? throw new ArgumentException("path missing");
                var size = SaveShot(window, file);
                foreach (var folder in shown) settings.UefnProjectFolders.Remove(folder);
                return new { file, size.Width, size.Height, settings.ShowIslandSettings };
            });
        }
        if (route == "fork-settings-shot")
        {
            // The Blender export settings page showing a section (section= its sidebar button's text), rendered to a PNG (path=).
            var window = AppServices.App.Lifetime.MainWindow!;
            await Avalonia.Threading.Dispatcher.UIThread.InvokeAsync(() =>
            {
                window.WindowState = Avalonia.Controls.WindowState.Normal;
                window.Width = 1600;
                window.Height = 1100;
                AppServices.Navigation.App.Open<Views.ExportSettingsView>();
            });
            await Task.Delay(TimeSpan.FromSeconds(3));
            await Avalonia.Threading.Dispatcher.UIThread.InvokeAsync(() =>
            {
                var view = Avalonia.VisualTree.VisualExtensions.GetVisualDescendants(window).OfType<Views.Settings.BlenderSettingsView>().First();
                var bar = Avalonia.Controls.ControlExtensions.FindControl<Controls.Navigation.Sidebar.Sidebar>(view, "SettingsSidebar")!;
                var button = Avalonia.VisualTree.VisualExtensions.GetVisualDescendants(bar).OfType<Controls.Navigation.Sidebar.SidebarItemButton>()
                    .First(b => b.Text == (query["section"] ?? "Material"));
                bar.SelectedItem = button.Tag;
            });
            await Task.Delay(TimeSpan.FromSeconds(2));
            return await Avalonia.Threading.Dispatcher.UIThread.InvokeAsync(() =>
            {
                var file = query["path"] ?? throw new ArgumentException("path missing");
                var size = SaveShot(window, file);
                var blender = AppSettings.ExportSettings.Blender;
                return new { file, blender.SubsurfaceIntensity, blender.BaseSubsurface, blender.SubsurfaceScale, blender.ProfileColour, blender.FurSubsurfaceIntensity, blender.FurSubsurfaceScale };
            });
        }
        if (route == "fork-map-shot")
        {
            // The Map page with its Flags menu open (a throwaway map stands in if the list has none), rendered to a PNG (path=)
            // and the menu's popup to path+".menu.png"; flip=Header unchecks that item first. Answers each item's header,
            // checked state and the map's world flags.
            var window = AppServices.App.Lifetime.MainWindow!;
            await Avalonia.Threading.Dispatcher.UIThread.InvokeAsync(() =>
            {
                window.WindowState = Avalonia.Controls.WindowState.Normal;
                window.Width = 1600;
                window.Height = 950;
                AppServices.Navigation.App.Open<Views.MapView>();
            });
            var maps = AppServices.MapVM;
            for (var i = 0; i < 240 && (maps.IsLoading || !maps.IsInitialized); i++) await Task.Delay(500);
            await Avalonia.Threading.Dispatcher.UIThread.InvokeAsync(() =>
            {
                if (maps.SelectedMap is null)
                {
                    var stand = new Models.Map.WorldPartitionMap(Models.Map.MapInfo.CreateNonDisplay("Test", "FortniteGame/Content/Test"));
                    maps.Maps.Add(stand);
                    maps.SelectedMap = stand;
                }
            });
            await Task.Delay(TimeSpan.FromSeconds(2));
            var items = new List<object>();
            await Avalonia.Threading.Dispatcher.UIThread.InvokeAsync(() =>
            {
                var flags = Avalonia.VisualTree.VisualExtensions.GetVisualDescendants(window).OfType<Avalonia.Controls.DropDownButton>()
                    .First(b => b.Flyout is Avalonia.Controls.MenuFlyout);
                flags.Flyout!.ShowAt(flags);
            });
            await Task.Delay(TimeSpan.FromSeconds(1.5));
            return await Avalonia.Threading.Dispatcher.UIThread.InvokeAsync(() =>
            {
                var flags = Avalonia.VisualTree.VisualExtensions.GetVisualDescendants(window).OfType<Avalonia.Controls.DropDownButton>()
                    .First(b => b.Flyout is Avalonia.Controls.MenuFlyout);
                var menu = (Avalonia.Controls.MenuFlyout) flags.Flyout!;
                if (query["flip"] is { } flip && menu.Items.OfType<Avalonia.Controls.MenuItem>().FirstOrDefault(m => m.Header as string == flip) is { } flipped)
                    flipped.IsChecked = !flipped.IsChecked;
                foreach (var item in menu.Items.OfType<Avalonia.Controls.MenuItem>()) items.Add(new { item.Header, item.IsChecked, item.IsEnabled });
                var file = query["path"] ?? throw new ArgumentException("path missing");
                SaveShot(window, file);
                // Avalonia keeps the flyout's popup in a private member of PopupFlyoutBase
                Avalonia.Controls.Primitives.Popup? popup = null;
                for (var type = menu.GetType(); type is not null && popup is null; type = type.BaseType)
                {
                    const System.Reflection.BindingFlags all = System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.DeclaredOnly;
                    popup = type.GetProperties(all).Where(p => p.PropertyType == typeof(Avalonia.Controls.Primitives.Popup)).Select(p => p.GetValue(menu)).OfType<Avalonia.Controls.Primitives.Popup>().FirstOrDefault()
                            ?? type.GetFields(all).Select(f => f.GetValue(menu)).OfType<Avalonia.Controls.Primitives.Popup>().FirstOrDefault()
                            ?? type.GetFields(all).Select(f => f.GetValue(menu)).OfType<Lazy<Avalonia.Controls.Primitives.Popup>>().Select(l => l.Value).FirstOrDefault();
                }
                items.Add(new { menu.IsOpen, PopupOpen = popup?.IsOpen, Child = popup?.Child?.GetType().Name, Host = popup?.Host?.GetType().Name });
                if (popup?.Host is Avalonia.Visual { Bounds.Width: > 0 } host)
                {
                    using var shot = new Avalonia.Media.Imaging.RenderTargetBitmap(new Avalonia.PixelSize((int) host.Bounds.Width, (int) host.Bounds.Height));
                    shot.Render(host);
                    shot.Save(file + ".menu.png");
                }
                var map = maps.SelectedMap;
                return new { file, Popup = popup?.Child is not null, items, map.WorldFlagsActors, map.WorldFlagsInstancedFoliage, map.WorldFlagsLandscape, map.WorldFlagsHLODs, map.WorldFlagsLights, map.WorldFlagsDecals, map.WorldFlagsEffects };
            });
        }
        if (route == "fork-map-preview")
        {
            // The Map page on a map (name=; none: the list), its drawn preview awaited (wait= seconds, 600), rendered to path=.
            var window = AppServices.App.Lifetime.MainWindow!;
            await Avalonia.Threading.Dispatcher.UIThread.InvokeAsync(() =>
            {
                window.WindowState = Avalonia.Controls.WindowState.Normal;
                window.Width = 1600;
                window.Height = 950;
                AppServices.Navigation.App.Open<Views.MapView>();
            });
            var maps = AppServices.MapVM;
            for (var i = 0; i < 600 && (maps.IsLoading || !maps.IsInitialized); i++) await Task.Delay(500);
            var names = await Avalonia.Threading.Dispatcher.UIThread.InvokeAsync(() => maps.Maps.Select(m => new { m.MapInfo.Name, m.MapInfo.MapPath, m.MapInfo.MinimapPath, m.MapInfo.IsNonDisplay, Grids = m.Grids.Count }).ToList());
            if (query["name"] is not { } name) return new { names };
            var map = await Avalonia.Threading.Dispatcher.UIThread.InvokeAsync(() =>
            {
                var found = maps.Maps.FirstOrDefault(m => m.MapInfo.Name.Equals(name, StringComparison.OrdinalIgnoreCase));
                if (found is not null) maps.SelectedMap = found;
                return found;
            });
            if (map is null) return new { error = "no such map", names };
            var sw = System.Diagnostics.Stopwatch.StartNew();
            var wait = int.TryParse(query["wait"], out var w) ? w : 600;
            await Task.Delay(1000);
            while (sw.Elapsed.TotalSeconds < wait && map.IsDrawingPreview) await Task.Delay(1000);
            await Task.Delay(1000);
            return await Avalonia.Threading.Dispatcher.UIThread.InvokeAsync(() =>
            {
                if (query["path"] is { } file) SaveShot(window, file);
                if (query["image"] is { } image && map.MapBitmap is { } bitmap) bitmap.Save(image);
                return (object) new
                {
                    map.MapInfo.Name, seconds = sw.Elapsed.TotalSeconds, map.IsDrawingPreview, map.PreviewStatus, map.NoPreview,
                    bitmap = map.MapBitmap?.PixelSize.ToString(), grids = map.Grids.Count, map.MapInfo.Scale, map.MapInfo.XOffset, map.MapInfo.YOffset,
                };
            });
        }
        if (route == "fork-reload")
        {
            // Reloads the installation instead of restarting the app; version=<EGame> and auto=0|1 first set the profile's
            // Unreal version and detection. Answers at once (the reload restarts this bridge): the log's next "exact materials served" means done.
            var profile = AppServices.AppSettings.Installation.CurrentProfile;
            if (query["version"] is { } version) profile.UnrealVersion = Enum.Parse<global::CUE4Parse.UE4.Versions.EGame>(version);
            if (query["auto"] is { } auto) profile.AutoUnrealVersion = auto == "1";
            _ = Task.Run(async () =>
            {
                await Task.Delay(200);
                await AppServices.App.ReloadInstallationAsync();
            });
            return new { reloading = true };
        }
        if (route == "fork-status")
        {
            // the status line and the newest log lines, as the window shows them
            return await Avalonia.Threading.Dispatcher.UIThread.InvokeAsync(() => new
            {
                StatusLog.Instance.LastLine, StatusLog.Instance.IsBusy, StatusLog.Instance.BusyWhat,
                Lines = StatusLog.Instance.Lines.Take(int.TryParse(query["count"], out var n) ? n : 60).ToArray(),
            });
        }
        if (route == "fork-find-builds")
        {
            // the builds the Installation page's Build picker lists (Find)
            var builds = await OnDemandBuilds.FindAsync(AppServices.AppSettings.Installation.Profiles.Select(p => p.ArchiveDirectory));
            return new JArray(builds.Select(b => $"{b.Build} | {b.Source} | {(b.StudioManifest is null ? "-" : "UEFN")} | {Path.GetFileName(b.Manifest)}"));
        }
        if (route == "fork-find-assets")
        {
            // asset registry entries whose package name holds ?path= (and whose class is ?class=, if given)
            var needle = query["path"] ?? throw new ArgumentException("path missing");
            var wanted = query["class"];
            return new JArray(AppServices.UEParse.AssetRegistry
                .Where(a => a.PackageName.Text.Contains(needle, StringComparison.OrdinalIgnoreCase)
                            && (wanted is null || a.AssetClass.Text.Equals(wanted, StringComparison.OrdinalIgnoreCase)))
                .Take(int.TryParse(query["count"], out var max) ? max : 400)
                .Select(a => $"{a.AssetClass.Text} {a.PackageName.Text}"));
        }
        if (route == "fork-asset-page")
        {
            // An asset's page as the Assets view builds it (type=, name=<display name>): its style channels once filled in
            // (wait= seconds); pick=Channel:Option;... picks options, export=1 exports with the picks as the Export button
            // does (styles through ExportService).
            var loader = AppServices.AssetLoading.Get(Enum.Parse<EExportType>(query["type"] ?? "Item"));
            await loader.Load();
            var wanted = query["name"] ?? throw new ArgumentException("name missing");
            var item = loader.Source.Items.OfType<Models.Assets.Asset.AssetItem>()
                           .FirstOrDefault(a => a.CreationData.DisplayName.Equals(wanted, StringComparison.OrdinalIgnoreCase))
                       ?? throw new FileNotFoundException("no asset named " + wanted);
            var stylePaths = loader.StyleDictionary.GetValueOrDefault(item.CreationData.DisplayName) ?? loader.StyleDictionary.GetValueOrDefault(item.CreationData.ID);
            var info = await Avalonia.Threading.Dispatcher.UIThread.InvokeAsync(() => stylePaths is not null
                ? new Models.Assets.Asset.AssetInfo(item, stylePaths.OrderBy(x => x.EndsWith(item.CreationData.ID, StringComparison.OrdinalIgnoreCase) ? 0 : 1))
                : new Models.Assets.Asset.AssetInfo(item));
            await Task.Delay(TimeSpan.FromSeconds(double.TryParse(query["wait"], System.Globalization.CultureInfo.InvariantCulture, out var seconds) ? seconds : 8));
            return await Avalonia.Threading.Dispatcher.UIThread.InvokeAsync<object>(async () =>
            {
                foreach (var pick in (query["pick"] ?? "").Split(';', StringSplitOptions.RemoveEmptyEntries).Select(x => x.Split(':', 2)).Where(x => x.Length == 2))
                {
                    var channel = info.StyleInfos.First(c => c.ChannelName == pick[0]);
                    channel.SelectedStyleIndex = channel.StyleDatas.ToList().FindIndex(d => d.StyleName == pick[1]);
                }
                var page = info.StyleInfos.Select(c => new
                {
                    c.ChannelName, options = c.StyleDatas.Count, c.IsPicker, c.IsSwitch, c.SwitchNote, selected = c.SelectedStyle.StyleName,
                    images = c.StyleDatas.Count(d => d.StyleDisplayImage is not null), first = c.StyleDatas.Take(6).Select(d => d.StyleName),
                }).ToList();
                if (query["export"] != "1") return JToken.FromObject(new { path = item.CreationData.Object!.GetPathName(), page });
                var convert = typeof(ExportService).GetMethod("ConvertStyles", System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Static)!;
                var styles = (Exporting.Styles.ExportStyleBase[]) convert.Invoke(null, [info.GetSelectedStyles()])!;
                using var pageMeta = AppServices.AppSettings.ExportSettings.CreateExportMeta(EExportLocation.Blender);
                var pageSession = new ExportSession(pageMeta);
                var pageData = await Task.Run(() => pageSession.RunAsync(() => [pageSession.CreateExport(item.CreationData.Object!.Name, item.CreationData.Object, item.CreationData.ExportType, styles)]));
                return new JRaw(JsonConvert.SerializeObject(new { page, pageData.Exports }));
            });
        }
        if (route == "fork-weapon-mods")
        {
            // a weapon item's mod slots as its page lists them (its own mod, the mods that allow it)
            var weapon = await Game.Provider.LoadPackageObjectAsync(query["path"] ?? throw new ArgumentException("path missing"));
            return JToken.FromObject(new
            {
                tags = WeaponMods.Tags(weapon).OrderBy(t => t),
                slots = WeaponMods.Plan(Game.Provider, weapon).Select(p => new
                {
                    p.Slot, own = p.Default?.Name, options = p.Options.Select(o => new { Name = WeaponMods.Labels(p.Options)[o], o.Path, o.Tag }),
                }),
                all = WeaponMods.All(Game.Provider).Count,
            });
        }
        if (route == "fork-find-files")
        {
            // game files whose path holds every word of ?path= (space-separated), of any type, with the container each is in
            var words = (query["path"] ?? throw new ArgumentException("path missing")).Split(' ', StringSplitOptions.RemoveEmptyEntries);
            return new JArray(Game.Provider.Files
                .Where(f => words.All(w => f.Key.Contains(w, StringComparison.OrdinalIgnoreCase)))
                .Take(int.TryParse(query["count"], out var most) ? most : 400)
                .Select(f => $"{f.Key} [{f.Value.GetType().Name} {(f.Value as global::CUE4Parse.UE4.VirtualFileSystem.VfsEntry)?.Vfs.Name}]"));
        }
        if (route == "fork-effect-program")
        {
            // what an effect's export carries for its replay (Effects.Program and Fields)
            var effect = await Game.Provider.LoadPackageAsync(query["path"] ?? throw new ArgumentException("path missing"));
            var niagara = effect.GetExports().FirstOrDefault(e => e.ExportType == "NiagaraSystem") ?? throw new ArgumentException("no Niagara system in the package");
            return new JObject { ["Exports"] = Exporting.MaterialPorter.Effects.Program(niagara), ["Fields"] = Exporting.MaterialPorter.Effects.Fields(niagara) };
        }
        if (route == "fork-dump")
        {
            // A package's exports (name, type, outer, properties) as CUE4Parse reads them; full=1: each export as CUE4Parse
            // writes it (including what it reads outside the properties, e.g. a material's cached data);
            // game=<EGame> reads with that Unreal version instead (put back after).
            var versions = ((global::CUE4Parse.FileProvider.AbstractFileProvider) Game.Provider).Versions;
            var was = versions.Game;
            if (query["game"] is { } game) OnDemandBuilds.SetGame(versions, Enum.Parse<global::CUE4Parse.UE4.Versions.EGame>(game));
            global::CUE4Parse.UE4.Assets.IPackage dumped;
            try
            {
                dumped = await Game.Provider.LoadPackageAsync(query["path"] ?? throw new ArgumentException("path missing"));
                if (query["game"] is not null) _ = dumped.GetExports().ToList();
            }
            finally
            {
                // only then: setting it rebuilds the options other reads are using
                if (query["game"] is not null) OnDemandBuilds.SetGame(versions, was);
            }
            var settings = new JsonSerializerSettings { ReferenceLoopHandling = ReferenceLoopHandling.Ignore };
            // count=1: exports per class (a level's lights, decals, effects; a 117 MB level's full dump doesn't serialize)
            if (query["count"] == "1")
                return JObject.FromObject(dumped.GetExports().GroupBy(e => e.ExportType).OrderByDescending(g => g.Count())
                    .ToDictionary(g => g.Key, g => g.Count()));
            // of=<class>: whose exports those are (their outers' classes, with a name each)
            if (query["of"] is { } ofClass)
                return JObject.FromObject(dumped.GetExports().Where(e => e.ExportType == ofClass)
                    .GroupBy(e => e.Outer?.Class?.Name.Text ?? "(none)").OrderByDescending(g => g.Count())
                    .ToDictionary(g => g.Key, g => new { count = g.Count(), example = g.First().Outer?.Name }));
            if (query["full"] == "1")
                return new JRaw(JsonConvert.SerializeObject(dumped.GetExports(), settings));
            // type=<class>, outer=<part of the owner's name>, limit=<n>: only those exports (a big level's dump doesn't serialize whole)
            var listed = dumped.GetExports().Where(e => (query["type"] is not { } wantType || e.ExportType == wantType)
                                                          && (query["outer"] is not { } wantOuter || e.Outer?.Name.Text.Contains(wantOuter, StringComparison.OrdinalIgnoreCase) == true)
                                                          && (query["name"] is not { } wantName || e.Name.Contains(wantName, StringComparison.OrdinalIgnoreCase)));
            if (int.TryParse(query["limit"], out var listLimit)) listed = listed.Take(listLimit);
            return new JArray(listed.Select(e => new JObject
            {
                ["name"] = e.Name, ["type"] = e.ExportType, ["outer"] = e.Outer?.Name.Text,
                ["props"] = JToken.Parse(JsonConvert.SerializeObject(e.Properties.ToDictionary(p => p.Name.Text, p => p.Tag?.GenericValue), settings)),
            }));
        }
        if (route == "fork-emote-census")
        {
            // How emotes play effects: each notify kind their montages (and sections' sequences) carry, with a
            // Template's class (Niagara, Cascade), in how many emotes and which.
            var listing = AppServices.AssetLoading.Get(Enum.Parse<EExportType>(query["type"] ?? "Emote"));
            await listing.Load();
            var kinds = new Dictionary<string, (int Count, List<string> Where)>();
            var emotes = 0;
            foreach (var item in listing.Source.Items.Select(a => a.CreationData).OfType<Models.Assets.Asset.AssetItemCreationArgs>())
            {
                try
                {
                    var montage = item.Object.GetOrDefault<global::CUE4Parse.UE4.Assets.Exports.Animation.UAnimMontage?>("Animation")
                                  ?? item.Object.GetOrDefault<global::CUE4Parse.UE4.Assets.Exports.Animation.UAnimMontage?>("FrontEndAnimation");
                    if (montage is null) continue;
                    emotes++;
                    var notifies = new List<global::CUE4Parse.UE4.Assets.Exports.Animation.FAnimNotifyEvent>(montage.Notifies ?? []);
                    foreach (var section in montage.CompositeSections)
                        if (section.LinkedSequence.Load<global::CUE4Parse.UE4.Assets.Exports.Animation.UAnimSequenceBase>() is { } sequence)
                            notifies.AddRange(sequence.Notifies ?? []);
                    var seen = new HashSet<string>();
                    foreach (var notify in notifies)
                    {
                        var played = notify.NotifyStateClass?.Load<UObject>() ?? notify.Notify?.Load<UObject>();
                        if (played is null) continue;
                        var kind = played.ExportType;
                        if (played.GetOrDefault<UObject?>("Template") is { } template) kind += " [" + template.ExportType + "]";
                        if (!seen.Add(kind)) continue;
                        var k = kinds.TryGetValue(kind, out var got) ? got : (0, []);
                        if (k.Where.Count < 6) k.Where.Add(item.DisplayName);
                        kinds[kind] = (k.Count + 1, k.Where);
                    }
                }
                catch (Exception e) { Log.Warning("emote census: {Item}: {Error}", item.Object.Name, e.Message); }
            }
            return JToken.FromObject(new { emotes, kinds = kinds.OrderByDescending(k => k.Value.Count).ToDictionary(k => k.Key, k => new { k.Value.Count, k.Value.Where }) });
        }
        if (route == "fork-duplicates")
        {
            // paths more than one container holds (?container= one of them, by name): how many per extension, which
            // container answers for them now, and a few examples
            var files = (global::CUE4Parse.FileProvider.Vfs.FileProviderDictionary) Game.Provider.Files;
            var wanted = query["container"] ?? "WindowsUEFN";
            var shared = new List<(string Path, string Answers)>();
            foreach (var key in files.Keys.Distinct(StringComparer.OrdinalIgnoreCase))
            {
                if (!files.TryGetValues(key, out var all) || all.Count < 2) continue;
                if (!all.Any(f => (f as global::CUE4Parse.UE4.VirtualFileSystem.VfsEntry)?.Vfs.Name.Contains(wanted) == true)) continue;
                shared.Add((key, (files[key] as global::CUE4Parse.UE4.VirtualFileSystem.VfsEntry)?.Vfs.Name ?? ""));
            }
            return JToken.FromObject(new
            {
                total = shared.Count,
                byExtension = shared.GroupBy(s => System.IO.Path.GetExtension(s.Path)).ToDictionary(g => g.Key, g => g.Count()),
                answeredBy = shared.GroupBy(s => s.Answers).ToDictionary(g => g.Key, g => g.Count()),
                sample = shared.Take(20).Select(s => $"{s.Path} <- {s.Answers}"),
            });
        }
        if (route == "fork-effect-items")
        {
            // a tab's items (type=Outfit) that have their own effects, as the Effects switch finds them
            var type = Enum.Parse<EExportType>(query["type"] ?? "Outfit");
            var listing = AppServices.AssetLoading.Get(type);
            await listing.Load();
            var found = new List<string>();
            foreach (var item in listing.Source.Items.Select(a => a.CreationData).OfType<Models.Assets.Asset.AssetItemCreationArgs>())
            {
                try
                {
                    if (Effects.OwnEffectNames(item.Object, type).Count > 0) found.Add($"{item.DisplayName} = {item.Object.GetPathName()}");
                }
                catch (Exception e)
                {
                    Log.Warning("effect items: {Item}: {Error}", item.Object.Name, e.Message);
                }
            }
            return JToken.FromObject(found);
        }
        if (route == "fork-effect-census")
        {
            // How the idle effects of a tab's items (?types=Outfit,Backpack: their parts') feed their materials (renderer
            // parameters, bindings to the system's variables, materials from user parameters) and what their emitters
            // run on / call: per kind, how many and where.
            var serializer = JsonSerializer.Create(new JsonSerializerSettings { ReferenceLoopHandling = ReferenceLoopHandling.Ignore });
            var owners = new Dictionary<string, (UObject System, List<string> Items)>(StringComparer.OrdinalIgnoreCase);
            var variantKinds = new List<(string Kind, string Where)>();
            foreach (var type in (query["types"] ?? "Outfit,Backpack").Split(','))
            {
                var exportType = Enum.Parse<EExportType>(type);
                var listing = AppServices.AssetLoading.Get(exportType);
                await listing.Load();
                foreach (var item in listing.Source.Items.Select(a => a.CreationData).OfType<Models.Assets.Asset.AssetItemCreationArgs>())
                {
                    try
                    {
                        // its styles that set its effects' parameters or swap its effects
                        if (query["variants"] == "1")
                            foreach (var variant in item.Object.Owner!.GetExports().Where(e => e.ExportType.Contains("Variant")))
                            {
                                var props = Props(variant);
                                foreach (var key in new[] { "VariantParticleParams", "VariantParticles", "InitalParticleSystemData" })
                                    foreach (var list in props.SelectTokens("$.." + key).OfType<JArray>().Where(a => a.Count > 0))
                                        variantKinds.Add((key, $"{item.DisplayName}: {list.ToString(Formatting.None)[..Math.Min(260, list.ToString(Formatting.None).Length)]}"));
                            }
                        var parts = exportType == EExportType.Backpack ? item.Object.GetOrDefault("CharacterParts", Array.Empty<UObject>()) : Effects.OutfitParts(item.Object);
                        foreach (var part in parts)
                        {
                            if (Effects.Shown(part.GetOrDefault<global::CUE4Parse.UE4.Objects.UObject.FSoftObjectPath>(Effects.PartEffect)) is not { } system) continue;
                            if (!owners.TryGetValue(system.GetPathName(), out var entry)) owners[system.GetPathName()] = entry = (system, []);
                            entry.Items.Add($"{item.DisplayName} = {item.Object.GetPathName()}");
                        }
                    }
                    catch (Exception e) { Log.Warning("census: {Item}: {Error}", item.Object.Name, e.Message); }
                }
            }
            var kinds = new Dictionary<string, (int Count, List<string> Where)>();
            void Count(string kind, string where)
            {
                var k = kinds.TryGetValue(kind, out var got) ? got : (0, []);
                if (k.Where.Count < 8 && !k.Where.Contains(where)) k.Where.Add(where);
                kinds[kind] = (k.Count + 1, k.Where);
            }
            JObject Props(UObject o)
            {
                var j = new JObject();
                foreach (var p in o.Properties)
                    if (p.Tag?.GenericValue is { } v && !j.ContainsKey(p.Name.Text)) j[p.Name.Text] = JToken.FromObject(v, serializer);
                return j;
            }
            foreach (var (kind, where) in variantKinds)
                Count("style: " + kind, where);
            var heavy = new List<(int Cpu, string Label, string Item)>();
            foreach (var (_, (system, items)) in owners)
            {
                var label = system.Name + " <- " + items[0].Split(" = ")[0] + (items.Count > 1 ? $" +{items.Count - 1}" : "");
                try
                {
                    var emitters = Effects.Emitters(system);
                    Count("systems", label);
                    if (emitters.All(e => e.Sim == "GPU")) Count("system: GPU only (not played)", label);
                    else if (emitters.Any(e => e.Sim == "GPU")) Count("system: some GPU emitters (those not played)", label);
                    heavy.Add((emitters.Count(e => e.Sim != "GPU"), label, items[0]));
                    foreach (var emitter in emitters)
                    {
                        Count("emitter: " + emitter.Sim, label);
                        foreach (var renderer in emitter.Renderers)
                        {
                            var r = Props(renderer);
                            Count("renderer: " + renderer.ExportType, label);
                            foreach (var b in r["MaterialParameters"]?["AttributeBindings"] as JArray ?? [])
                            {
                                var variable = (string?)b["ResolvedNiagaraVariable"]?["Name"] ?? (string?)b["NiagaraVariable"]?["Name"] ?? "";
                                var typeDef = b["ResolvedNiagaraVariable"]?["TypeDef"] ?? b["NiagaraVariable"]?["TypeDef"];
                                var cls = ((string?)typeDef?["ClassStructOrEnum"]?["ObjectName"] ?? "?").Split('\'').ElementAtOrDefault(1) ?? "?";
                                var space = variable.Contains('.') ? variable[..variable.IndexOf('.')] : "?";
                                var resolved = cls.Contains("Curve") && Effects.ExposedCurve(system, variable) is not null;
                                Count($"binding: {space}.* {cls}{(resolved ? " (resolved)" : "")}", $"{label} [{(string?)b["MaterialParameterName"]} <- {variable}]");
                            }
                            foreach (var section in new[] { "ScalarParameters", "VectorParameters", "TextureParameters" })
                                if ((r["MaterialParameters"]?[section] as JArray)?.Count > 0) Count("renderer values: " + section, label);
                            foreach (var (key, value) in r)
                                if (key.EndsWith("UserParamBinding") && (string?)value?["Parameter"]?["Name"] is { Length: > 0 } user && user != "None")
                                    Count("user binding: " + key, $"{label} [{user}]");
                            foreach (var o in r["OverrideMaterials"] as JArray ?? [])
                                if ((string?)o["UserParamBinding"]?["Parameter"]?["Name"] is { Length: > 0 } user && user != "None")
                                    Count("user binding: OverrideMaterials", $"{label} [{user}]");
                        }
                    }
                    // the data interfaces the system's scripts call
                    if (system.Properties.FirstOrDefault(p => p.Name.Text == "ScriptRuntimeCookedDataMap")?.Tag?.GenericValue is { } map)
                        foreach (var cls in JToken.FromObject(map, serializer).SelectTokens("$..ResolvedDataInterfaces[*].ResolvedVariable.TypeDef.ClassStructOrEnum.ObjectName")
                                     .Select(t => ((string?)t ?? "").Split('\'').ElementAtOrDefault(1) ?? "?").Distinct())
                            Count("data interface: " + cls, label);
                }
                catch (Exception e) { Count("error: " + e.Message, label); }
            }
            return JToken.FromObject(new
            {
                systems = owners.Count,
                kinds = kinds.OrderBy(k => k.Key).ToDictionary(k => k.Key, k => new { k.Value.Count, k.Value.Where }),
                heavy = heavy.OrderByDescending(h => h.Cpu).Take(int.TryParse(query["heavy"], out var top) ? top : 60).Select(h => $"{h.Cpu} {h.Label} = {h.Item.Split(" = ").Last()}"),
            });
        }
        if (route == "fork-loader")
        {
            // Runs one asset tab's loader as the Assets page does and says what it lists (icons=1: with each one's icon).
            var loader = AppServices.AssetLoading.Get(Enum.Parse<EExportType>(query["type"] ?? "LegoOutfit"));
            var clock = Stopwatch.StartNew();
            await loader.Load();
            var names = loader.Source.Items.Select(a => a.CreationData is Models.Assets.Asset.AssetItemCreationArgs args
                    ? $"{args.DisplayName} = {args.Object.GetPathName()}" + (query["icons"] == "1" ? $" [{args.IconPath?.Split('/').Last()}]" : "")
                      + (query["described"] == "1" ? $" | {args.Description}" : "")
                    : a.CreationData.DisplayName)
                .OrderBy(n => n).ToList();
            var filter = query["filter"] ?? "";
            // check=1 (LEGO): every listed figure resolved to a cooked mesh or a recipe; the ones with neither are named
            var cooked = 0;
            var failed = query["check"] != "1" ? [] : loader.Source.Items
                .Select(a => (a.CreationData as Models.Assets.Asset.AssetItemCreationArgs)?.Object)
                .Where(o =>
                {
                    if (o is null) return false;
                    if (Figures.BakedMeshes(Game.Provider, o).Count > 0) { cooked++; return false; }
                    return Figures.RecipeInstance(Game.Provider, o) is null;
                })
                .Select(o => o!.Name).ToList();
            return JToken.FromObject(new
            {
                total = loader.TotalAssets, listed = names.Count, seconds = clock.Elapsed.TotalSeconds,
                managedMB = GC.GetTotalMemory(false) / 1_000_000, workingSetMB = Environment.WorkingSet / 1_000_000,
                // effects: how many play in Blender (the tab's filter)
                plays = loader.Type == EExportType.Effect ? loader.Source.Items.OfType<Models.Assets.Asset.AssetItem>().Count(a => Effects.Plays(a.CreationData.Object)) : -1,
                owned = loader.Type == EExportType.Animation ? loader.Source.Items.OfType<Models.Assets.Asset.AssetItem>().Count(a => Animations.Owned(a.CreationData.Object)) : -1,
                // effects: how many belong to an item, and how many of each kind (the tab's filters)
                effectOwned = loader.Type == EExportType.Effect ? loader.Source.Items.OfType<Models.Assets.Asset.AssetItem>().Count(a => EffectOwners.Owned(a.CreationData.Object)) : -1,
                effectKinds = loader.Type == EExportType.Effect ? loader.Source.Items.OfType<Models.Assets.Asset.AssetItem>()
                    .GroupBy(a => EffectOwners.KindOf(a.CreationData.Object)).ToDictionary(g => g.Key, g => g.Count()) : null,
                sample = names.Where(n => n.Contains(filter, StringComparison.OrdinalIgnoreCase)).Take(12),
                cooked, failed = failed.Count, failedSample = failed.Take(12)
            });
        }
        if (route == "fork-export-asset")
        {
            // FP's export of one asset (type=Car|Outfit|...; picks for a car, face=Mouth:12,Eyes:2 for a LEGO figure)
            // as the plugin receives it.
            var type = Enum.Parse<EExportType>(query["type"] ?? "Car");
            if (type == EExportType.TimeOfDay && !Fork.TimeOfDayExport)
                throw new InvalidOperationException("Time of Day export isn't in this build");
            // listed=1: the asset as its tab lists it (an effect found by file name is listed unread)
            UObject asset;
            if (query["listed"] == "1")
            {
                var listing = AppServices.AssetLoading.Get(type);
                await listing.Load();
                var wantedPath = query["path"] ?? throw new ArgumentException("path missing");
                asset = listing.Source.Items.OfType<Models.Assets.Asset.AssetItem>().Select(a => a.CreationData.Object)
                            .FirstOrDefault(o => o.GetPathName().Equals(wantedPath, StringComparison.OrdinalIgnoreCase))
                        ?? throw new FileNotFoundException("not listed: " + wantedPath);
            }
            else asset = await Game.Provider.LoadPackageObjectAsync(query["path"] ?? throw new ArgumentException("path missing"));
            var styles = (query["picks"] ?? "").Split(',', StringSplitOptions.RemoveEmptyEntries).Select(x => x.Split(':'))
                .Where(x => x.Length == 2).Select(x => (Exporting.Styles.ExportStyleBase) new ExportCarStyle { Channel = int.Parse(x[0]), Option = int.Parse(x[1]) })
                .Concat((query["face"] ?? "").Split(',', StringSplitOptions.RemoveEmptyEntries).Select(x => x.Split(':'))
                    .Where(x => x.Length == 2).Select(x => (Exporting.Styles.ExportStyleBase) new ExportFigureFaceStyle { Feature = x[0], Pose = int.Parse(x[1]) }))
                // wrap=<wrap item path, or empty for none>; mods=Optic:<mod item path>;Magazine: (empty: none); effects=1
                .Concat(query["wrap"] is { } wrapPick ? [new ExportWrapStyle { Path = wrapPick }] : Array.Empty<Exporting.Styles.ExportStyleBase>())
                // effects=1: a pickaxe with its own effects
                .Concat(query["effects"] == "1" ? [new ExportEffectsStyle { On = true }] : Array.Empty<Exporting.Styles.ExportStyleBase>())
                .Concat((query["mods"] ?? "").Split(';', StringSplitOptions.RemoveEmptyEntries).Select(x => x.Split(':', 2))
                    .Where(x => x.Length == 2).Select(x => (Exporting.Styles.ExportStyleBase) new ExportWeaponModStyle { Slot = x[0], Path = x[1] }))
                // styles=<style name>;<style name>: an item's styles picked by their names (as its page shows them)
                .Concat(PickedStyles(asset, query["styles"]))
                .ToArray();
            using var assetMeta = AppServices.AppSettings.ExportSettings.CreateExportMeta(EExportLocation.Blender);
            var assetSession = new ExportSession(assetMeta);
            var assetData = await assetSession.RunAsync(() => [assetSession.CreateExport(asset.Name, asset, type, styles)]);
            return new JRaw(JsonConvert.SerializeObject(new
            {
                MetaData = new
                {
                    assetData.MetaData.Version,
                    assetData.MetaData.AssetsRoot,
                    Settings = AppServices.AppSettings.ExportSettings.GetSettingsViewModel(EExportLocation.Blender),
                },
                assetData.Exports,
            }));
        }
        // /fork-export-world?path=<level package>[&landscape=1][&actors=0][&lights=0][&decals=0][&effects=0][&actor=name part]:
        // FP's world export of that level (actors and instances, as the Map page sends it to Blender) as the plugin receives it.
        // track=<Rocket Racing track object path>&points=x,y,z;x,y,z...: also lays that track's road along those points.
        if (route != "fork-export-world") return null;
        var path = query["path"] ?? throw new ArgumentException("path missing");
        // a World Partition cell's world is named after its map, not its file
        var package = await Game.Provider.LoadPackageAsync(path);
        var world = package.GetExports().OfType<UWorld>().FirstOrDefault()
                    ?? throw new FileNotFoundException("no world in " + path);
        using var meta = AppServices.AppSettings.ExportSettings.CreateExportMeta(EExportLocation.Blender);
        meta.WorldFlags = (query["actors"] == "0" ? 0 : EWorldFlags.Actors | EWorldFlags.InstancedFoliage) | (query["landscape"] == "1" ? EWorldFlags.Landscape : 0)
                         | (query["lights"] == "0" ? 0 : EWorldFlags.Lights) | (query["decals"] == "0" ? 0 : EWorldFlags.Decals) | (query["effects"] == "0" ? 0 : EWorldFlags.Effects);
        var session = new ExportSession(meta);
        Exporting.Context.ExportContext.MaterialPorterActorFilter = query["actor"];
        if (query["track"] is { } track)
            Exporting.MaterialPorter.DelMarTracks.TestTrack = (track, (query["points"] ?? "").Split(';', StringSplitOptions.RemoveEmptyEntries)
                .Select(p => p.Split(',').Select(x => float.Parse(x, System.Globalization.CultureInfo.InvariantCulture)).ToArray())
                .Select(v => new System.Numerics.Vector3(v[0], v[1], v[2])).ToArray());
        Exporting.Models.ExportData data;
        try { data = await session.RunAsync(() => [session.CreateExport(world.Name, world, EExportType.World, [])]); }
        finally
        {
            Exporting.Context.ExportContext.MaterialPorterActorFilter = null;
            Exporting.MaterialPorter.DelMarTracks.TestTrack = null;
        }
        var payload = new
        {
            MetaData = new
            {
                data.MetaData.Version,
                data.MetaData.AssetsRoot,
                Settings = AppServices.AppSettings.ExportSettings.GetSettingsViewModel(EExportLocation.Blender),
            },
            data.Exports,
        };
        return new JRaw(JsonConvert.SerializeObject(payload));
    }
}
#endif
