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

/// <summary>
/// What Material Porter's ported code (MaterialService, Bridge) reads of the
/// game: FP's provider, the build it mounted, where its cache lives.
/// </summary>
public class GameContext
{
    /// <summary>Material Porter's data folder: its cache (graphs, textures) is shared with the Material Porter app.</summary>
    public static string DataDir { get; set; } = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "MaterialPorter");

    public IFileProvider Provider { get; set; } = null!;
    public string BuildVersion { get; set; } = "unknown";
    public bool Mounted => Provider != null;
}

/// <summary>Material Porter's timing lines, into FP's log at debug level.</summary>
public static class Timing
{
    public static void Log(string what, Stopwatch sw) =>
        Serilog.Log.Debug("[Material Porter] {What}: {Ms:0} ms", what, sw.Elapsed.TotalMilliseconds);
}

/// <summary>
/// Exact materials: while FP's Blender plugin imports, it asks this app on
/// localhost (Material Porter's bridge) for each material's description, its
/// graph and its functions' graphs, textures and parameter collections, and
/// rebuilds the material from its UE graph instead of FP's presets.
/// </summary>
public class MaterialPorterService : IService
{
    public static int Port => Fork.BridgePort;

    public static MaterialPorterService Instance => AppServices.Services.GetRequiredService<MaterialPorterService>();

    public GameContext Game { get; } = new();
    public MaterialService? Materials { get; private set; }
    private Bridge? _bridge;

    /// <summary>Once the game's files are mounted: the bridge starts listening (again after a reload).</summary>
    public void OnGameLoaded(IFileProvider provider, string? buildVersion)
    {
        Game.Provider = provider;
        Game.BuildVersion = string.IsNullOrWhiteSpace(buildVersion) ? "unknown" : buildVersion;
        Materials = new MaterialService(Game);
        FigureRecipe.GeneratedDir = Materials.GeneratedDir;    // LEGO figures' colour grids, served by the bridge
        // cars: the registry's decals and wheel sets, read once in the background
        _carSkins = _carWheels = null;
        Cars.Items = CarItemsAsync;
        _ = Task.Run(CarItemsAsync);
        _bridge?.Dispose();
        try
        {
            _bridge = new Bridge(Game, Materials);
            _bridge.Log += message => Log.Information("[Material Porter] {Message}", message);
            _bridge.Extra = ExtraRouteAsync;
            _bridge.Start(Port);
            Log.Information("[Material Porter] exact materials served on localhost:{Port} ({Build})", Port, Game.BuildVersion);
            // exact materials are translated from the masters' editor graphs (<master>.o.uasset), which only an
            // install with Unreal Editor for Fortnite has (its editor data, about 5 GB in the game's Paks): say so
            // once, rather than every material coming out approximated
            _ = Task.Run(() =>
            {
                if (provider.Files.Keys.Any(k => k.EndsWith(".o.uasset", StringComparison.OrdinalIgnoreCase))) return;
                Log.Warning("[Material Porter] no editor graphs in this install: materials are approximated (install Unreal Editor for Fortnite)");
                Avalonia.Threading.Dispatcher.UIThread.Post(() => AppServices.Info.Message("Exact materials need UEFN",
                    "This Fortnite install has no editor data, so materials are approximated. Install Unreal Editor for Fortnite " +
                    "from the Epic Games Launcher (it adds its editor data to the game's folder), then restart the app.",
                    FluentAvalonia.UI.Controls.InfoBarSeverity.Warning, autoClose: false));
            });
        }
        catch (Exception e)
        {
            _bridge = null;
            Log.Error(e, "[Material Porter] could not listen on localhost:{Port}; Blender falls back to FP's materials", Port);
        }
    }

    // ------------------------------------------------------------ islands
    /// <summary>A key the user's key tool gave for an island (shared with the Material Porter app).</summary>
    private sealed class IslandKey
    {
        public string Code { get; set; } = "";
        public string Guid { get; set; } = "";
        public string Key { get; set; } = "";
    }

    /// <summary>Island keys, in Material Porter's data folder: unlocked in either app, open in both.</summary>
    public static string IslandKeysFile => Path.Combine(GameContext.DataDir, ".data", "islands.json");

    private static List<IslandKey> LoadIslandKeys()
    {
        try { return File.Exists(IslandKeysFile) ? JsonConvert.DeserializeObject<List<IslandKey>>(File.ReadAllText(IslandKeysFile)) ?? [] : []; }
        catch { return []; }
    }

    /// <summary>
    /// tests: an item's styles by name (styles=Violet Board Jules;...): each name's option from the item's
    /// ItemVariants (the one named so, else the first whose name contains it), as the asset page passes it.
    /// </summary>
    private static IEnumerable<Exporting.Styles.ExportStyleBase> PickedStyles(UObject asset, string? names)
    {
        if (string.IsNullOrWhiteSpace(names)) yield break;
        // (each option with its channel's name: "jacket:On" picks one where several channels have an On)
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

    private static string NormalGuid(string? g) => new string((g ?? "").Where(Uri.IsHexDigit).ToArray()).ToUpperInvariant();

    /// <summary>The downloaded islands (FP registers them, locked) this app has keys for, opened. Keys are never logged.</summary>
    public async Task SubmitIslandKeysAsync(AbstractVfsFileProvider provider)
    {
        if (!Fork.Islands) return;
        var opened = 0;
        var keys = LoadIslandKeys();
        foreach (var k in keys)
        {
            try { opened += await provider.SubmitKeyAsync(new FGuid(NormalGuid(k.Guid)), new FAesKey(k.Key)); }
            catch (Exception e) { Log.Warning("[Material Porter] island {Code}: {Message}", k.Code, e.Message); }
        }
        Log.Information("[Material Porter] islands: {Opened} archives opened with {Keys} saved keys", opened, keys.Count);
    }

    /// <summary>The user's island key tool (Material Porter's setting, else Documents\UEFN-AES-grabber-main).</summary>
    public static string IslandKeyTool
    {
        get
        {
            try
            {
                var settings = Path.Combine(GameContext.DataDir, "settings.json");
                if (File.Exists(settings) && JObject.Parse(File.ReadAllText(settings))["IslandKeyTool"]?.ToString() is { Length: > 0 } dir)
                    return dir;
            }
            catch { /* the default */ }
            return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments), "UEFN-AES-grabber-main");
        }
    }

    /// <summary>
    /// A map code's island key from the key tool, kept with Material Porter's keys, and the
    /// island mounted. False when Fortnite hasn't downloaded that island (the key waits for it).
    /// </summary>
    public async Task<bool> UnlockIslandAsync(string code, Action<string> status, Action<string> signIn)
    {
        if (!Fork.Islands) throw new InvalidOperationException("UEFN island export isn't in this build");
        var (guid, key) = await RunKeyToolAsync(IslandKeyTool, code, status, signIn);
        var keys = LoadIslandKeys();
        keys.RemoveAll(k => NormalGuid(k.Guid) == NormalGuid(guid));
        keys.Add(new IslandKey { Code = code, Guid = NormalGuid(guid), Key = key });
        Directory.CreateDirectory(Path.GetDirectoryName(IslandKeysFile)!);
        await File.WriteAllTextAsync(IslandKeysFile, JsonConvert.SerializeObject(keys, Formatting.Indented));
        if (Game.Provider is not AbstractVfsFileProvider provider) return false;
        var opened = await provider.SubmitKeyAsync(new FGuid(NormalGuid(guid)), new FAesKey(key));
        if (opened == 0) return false;
        try { provider.LoadVirtualPaths(); } catch { /* its files resolve by path still */ }
        Log.Information("[Material Porter] island {Code} unlocked ({Opened} archives)", code, opened);
        return true;
    }

    /// <summary>
    /// The key tool (Node.js, `node .` in its folder) run for one code: its sign-in link goes to
    /// signIn, its progress to status; its "AES Key:" and "GUID:" lines come back, never logged.
    /// </summary>
    private static async Task<(string Guid, string Key)> RunKeyToolAsync(string toolDir, string code, Action<string> status, Action<string> signIn)
    {
        if (string.IsNullOrWhiteSpace(toolDir) || !File.Exists(Path.Combine(toolDir, "index.js")))
            throw new FileNotFoundException("No island key tool at " + toolDir);
        var psi = new ProcessStartInfo("node", ".")
        {
            WorkingDirectory = toolDir, UseShellExecute = false, CreateNoWindow = true,
            RedirectStandardInput = true, RedirectStandardOutput = true, RedirectStandardError = true,
        };
        Process? started;
        try { started = Process.Start(psi); }
        catch (System.ComponentModel.Win32Exception) { throw new InvalidOperationException("Node.js isn't installed (the island key tool runs on it)"); }
        using var proc = started ?? throw new InvalidOperationException("the key tool didn't start");
        string? guid = null, key = null, error = null;
        void Line(string? l, bool err)
        {
            if (string.IsNullOrWhiteSpace(l)) return;
            var t = l.Trim().Replace("Enter the map code:", "").Trim();
            if (t.Length == 0) return;
            if (t.StartsWith("AES Key:", StringComparison.OrdinalIgnoreCase)) { key = t[8..].Trim(); return; }
            if (t.StartsWith("GUID:", StringComparison.OrdinalIgnoreCase)) { guid = t[5..].Trim(); return; }
            var at = t.IndexOf("https://", StringComparison.OrdinalIgnoreCase);
            if (t.StartsWith("Authorize here", StringComparison.OrdinalIgnoreCase) && at >= 0)
            {
                signIn(t[at..].Trim());
                status("Sign in to Epic in the page that opened");
                return;
            }
            if (System.Text.RegularExpressions.Regex.Match(t, @"errorMessage:\s*'(.*)'") is { Success: true } em)
            {
                error = "Epic: " + em.Groups[1].Value;
                return;
            }
            if (t.Contains("encrypt", StringComparison.OrdinalIgnoreCase) || t.Contains("can't be downloaded", StringComparison.OrdinalIgnoreCase)
                || (t.StartsWith("Error:", StringComparison.OrdinalIgnoreCase) && t.Length > 8 && !t.EndsWith("{")))
            {
                error = t;
                return;
            }
            if (!err) status(t);
        }
        var output = Task.Run(async () => { while (await proc.StandardOutput.ReadLineAsync() is { } l) Line(l, false); });
        var errors = Task.Run(async () => { while (await proc.StandardError.ReadLineAsync() is { } l) Line(l, true); });
        // the tool reads the code after signing in; the pipe holds it till then
        await proc.StandardInput.WriteLineAsync(code);
        proc.StandardInput.Close();
        using var timeout = new System.Threading.CancellationTokenSource(TimeSpan.FromMinutes(6));   // an unanswered sign-in expires
        try { await proc.WaitForExitAsync(timeout.Token); }
        catch (OperationCanceledException)
        {
            try { proc.Kill(true); } catch { /* gone */ }
            throw new TimeoutException("The key tool waited too long (sign-in not finished)");
        }
        await Task.WhenAll(output, errors);
        if (key is null || guid is null) throw new InvalidOperationException(error ?? "the key tool gave no key for " + code);
        return (guid, key);
    }

    // ------------------------------------------------------------ cars
    private List<CarItem>? _carSkins, _carWheels;
    private readonly System.Threading.SemaphoreSlim _carGate = new(1, 1);

    /// <summary>The registry's car decals and wheel sets, titled by their item names (read once).</summary>
    public async Task<(List<CarItem> Skins, List<CarItem> Wheels)> CarItemsAsync()
    {
        await _carGate.WaitAsync();
        try
        {
            if (_carSkins is null || _carWheels is null)
            {
                async Task<List<CarItem>> Of(string cls)
                {
                    var list = new List<CarItem>();
                    foreach (var a in AppServices.UEParse.AssetRegistry.Where(a => a.AssetClass.Text == cls))
                    {
                        string? name = null;
                        try
                        {
                            if (await Game.Provider.LoadPackageObjectAsync(a.ObjectPath) is { } item)
                                name = item.GetOrDefault<FText?>("ItemName")?.Text;
                        }
                        catch { /* its asset name, then */ }
                        var title = Cars.ItemTitle(name, a.AssetName.Text);
                        list.Add(new CarItem(a.AssetName.Text, title, a.PackageName.Text, a.ObjectPath));
                    }
                    return list;
                }
                _carSkins = await Of(Cars.SkinClass);
                _carWheels = await Of(Cars.WheelClass);
            }
            return (_carSkins, _carWheels);
        }
        finally { _carGate.Release(); }
    }

    /// <summary>A car body item's plan under these picks (channel index -> option index).</summary>
    public async Task<CarPlan> CarPlanAsync(string bodyObjectPath, IReadOnlyDictionary<int, int>? picks)
    {
        var (skins, wheels) = await CarItemsAsync();
        var dot = bodyObjectPath.LastIndexOf('.');
        var package = dot > bodyObjectPath.LastIndexOf('/') ? bodyObjectPath[..dot] : bodyObjectPath;
        var name = dot > bodyObjectPath.LastIndexOf('/') ? bodyObjectPath[(dot + 1)..] : bodyObjectPath.Split('/').Last();
        return await new Cars(Game.Provider).PlanAsync(package, name, skins, wheels, picks);
    }

    private async Task<object?> ExtraRouteAsync(string route, NameValueCollection query)
    {
        // what this build does: the Blender plugin's Material Fixer shows island recovery only where islands are
        if (route == "fork-caps") return new { islands = Fork.Islands };
        if (route.StartsWith("fork-island-"))
        {
            // the Material Fixer's island texture recovery (IslandMaterials): island content, the owner's builds only
            if (!Fork.Islands) throw new InvalidOperationException("island texture recovery is only in the owner's builds");
            var asked = query["path"] ?? throw new ArgumentException("path missing");
            return route switch
            {
                "fork-island-find" => IslandMaterials.Find(Game.Provider, asked.Split(',')),
                "fork-island-mesh" => await IslandMaterials.MeshSlotsAsync(Game.Provider, asked),
                "fork-island-material" => await IslandMaterials.MaterialAsync(Game.Provider, asked),
                _ => throw new KeyNotFoundException("no route " + route),
            };
        }
        if (route == "fork-car")
        {
            // tests: a car body's channels and what the picks ("0:1,5:3") give
            var picks = (query["picks"] ?? "").Split(',', StringSplitOptions.RemoveEmptyEntries)
                .Select(x => x.Split(':')).Where(x => x.Length == 2).ToDictionary(x => int.Parse(x[0]), x => int.Parse(x[1]));
            var plan = await CarPlanAsync(query["path"] ?? throw new ArgumentException("path missing"), picks);
            return new
            {
                // (all=1: every option with its item's package, to find one by its asset)
                channels = plan.Channels.Select(c => new { c.Name, c.Default, options = c.Options.Count,
                    first = query["all"] == "1" ? c.Options.Select(o => o.Name + " | " + (o.IconItem?.Package ?? o.Icon)) : c.Options.Take(4).Select(o => o.Name) }),
                plan.Styles, plan.BodyMesh, plan.BodyOverrides, plan.WheelMesh,
                wheels = plan.Wheels.Select(w => new { w.Label, at = new[] { w.Transform.M41, w.Transform.M42, w.Transform.M43 } }),
                @params = plan.Params.ToDictionary(kv => kv.Key, kv => new { kv.Value.Vectors, kv.Value.Scalars }),
            };
        }
        if (route == "log")
        {
            // the Blender plugin telling what it does while it imports (status.py): a JSON list of lines, begin/end
            var lines = JsonConvert.DeserializeObject<string[]>(query["lines"] ?? "[]") ?? [];
            StatusLog.Instance.FromBlender(query["state"], lines);
            return "ok";
        }
        if (route == "fork-assets-page")
        {
            // tests: open the Assets page as a click on it does (its view model builds the tabs' sidebar), and
            // say what it built: the sidebar's entries, and each tab's type with whether it has an icon of its own
            await Avalonia.Threading.Dispatcher.UIThread.InvokeAsync(() => AppServices.Navigation.App.Open<Views.AssetsView>());
            await Task.Delay(TimeSpan.FromSeconds(double.TryParse(query["wait"], System.Globalization.CultureInfo.InvariantCulture, out var pause) ? pause : 6));
            return await Avalonia.Threading.Dispatcher.UIThread.InvokeAsync(() =>
            {
                var model = Microsoft.Extensions.DependencyInjection.ServiceProviderServiceExtensions.GetRequiredService<ViewModels.AssetsViewModel>(AppServices.Services);
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
            // tests: the window as it shows an asset tab (type=; search=, filters=Title,Title, select= a display
            // name to open its info), rendered to a PNG (path=) after wait= seconds (icons load)
            var window = AppServices.App.Lifetime.MainWindow!;
            await Avalonia.Threading.Dispatcher.UIThread.InvokeAsync(() =>
            {
                window.WindowState = Avalonia.Controls.WindowState.Normal;
                window.Width = double.TryParse(query["width"], out var w) ? w : 1600;
                window.Height = double.TryParse(query["height"], out var h) ? h : 950;
                AppServices.Navigation.App.Open<Views.AssetsView>();
            });
            var assetsModel = Microsoft.Extensions.DependencyInjection.ServiceProviderServiceExtensions.GetRequiredService<ViewModels.AssetsViewModel>(AppServices.Services);
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
                var size = new Avalonia.PixelSize((int) window.Bounds.Width, (int) window.Bounds.Height);
                using var shot = new Avalonia.Media.Imaging.RenderTargetBitmap(size);
                shot.Render(window);
                var file = query["path"] ?? throw new ArgumentException("path missing");
                shot.Save(file);
                return new { file, size.Width, size.Height, AppServices.AssetLoading.ActiveLoader?.Filtered.Count };
            });
        }
        if (route == "fork-settings-shot")
        {
            // tests: the Blender export settings page as it shows a section (section= its sidebar button's text),
            // rendered to a PNG (path=)
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
                var size = new Avalonia.PixelSize((int) window.Bounds.Width, (int) window.Bounds.Height);
                using var shot = new Avalonia.Media.Imaging.RenderTargetBitmap(size);
                shot.Render(window);
                var file = query["path"] ?? throw new ArgumentException("path missing");
                shot.Save(file);
                var blender = AppSettings.ExportSettings.Blender;
                return new { file, blender.SubsurfaceIntensity, blender.SubsurfaceScale, blender.FurSubsurfaceIntensity, blender.FurSubsurfaceScale };
            });
        }
        if (route == "fork-status")
        {
            // tests: the status line and the newest log lines, as the window shows them
            return await Avalonia.Threading.Dispatcher.UIThread.InvokeAsync(() => new
            {
                StatusLog.Instance.LastLine, StatusLog.Instance.IsBusy, StatusLog.Instance.BusyWhat,
                Lines = StatusLog.Instance.Lines.Take(int.TryParse(query["count"], out var n) ? n : 60).ToArray(),
            });
        }
        if (route == "fork-find-assets")
        {
            // tests: the asset registry's entries whose package name holds ?path= (and whose class is ?class=, if given)
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
            // tests: an asset's page as the Assets view builds it (type=, name= its display name): its style
            // channels once they have filled in (wait= seconds); pick=Channel:Option;... picks options, and
            // export=1 exports with the picks as the Export button does (the styles through ExportService)
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
                    c.ChannelName, options = c.StyleDatas.Count, c.IsPicker, selected = c.SelectedStyle.StyleName,
                    images = c.StyleDatas.Count(d => d.StyleDisplayImage is not null), first = c.StyleDatas.Take(6).Select(d => d.StyleName),
                }).ToList();
                if (query["export"] != "1") return JToken.FromObject(new { page });
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
            // tests: a weapon item's mod slots as its page lists them (its own mod, the mods that allow it)
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
            // tests: the game files whose path holds every word of ?path= (space-separated), whatever their type
            var words = (query["path"] ?? throw new ArgumentException("path missing")).Split(' ', StringSplitOptions.RemoveEmptyEntries);
            return new JArray(Game.Provider.Files.Keys
                .Where(k => words.All(w => k.Contains(w, StringComparison.OrdinalIgnoreCase)))
                .Take(int.TryParse(query["count"], out var most) ? most : 400));
        }
        if (route == "fork-effect-program")
        {
            // tests: what an effect's export carries for its replay (Effects.Program and Fields)
            var effect = await Game.Provider.LoadPackageAsync(query["path"] ?? throw new ArgumentException("path missing"));
            var niagara = effect.GetExports().FirstOrDefault(e => e.ExportType == "NiagaraSystem") ?? throw new ArgumentException("no Niagara system in the package");
            return new JObject { ["Exports"] = Exporting.MaterialPorter.Effects.Program(niagara), ["Fields"] = Exporting.MaterialPorter.Effects.Fields(niagara) };
        }
        if (route == "fork-dump")
        {
            // tests: a package's exports (name, type, outer, properties) as CUE4Parse reads them; full=1: each
            // export as CUE4Parse writes it (what it reads outside the properties too: a material's cached data...)
            var dumped = await Game.Provider.LoadPackageAsync(query["path"] ?? throw new ArgumentException("path missing"));
            var settings = new JsonSerializerSettings { ReferenceLoopHandling = ReferenceLoopHandling.Ignore };
            // count=1: how many exports of each class (a level's lights, decals, effects - a 117 MB level's
            // full dump doesn't serialize)
            if (query["count"] == "1")
                return JObject.FromObject(dumped.GetExports().GroupBy(e => e.ExportType).OrderByDescending(g => g.Count())
                    .ToDictionary(g => g.Key, g => g.Count()));
            // of=<class>: whose exports those are (their outers' classes, with a name each)
            if (query["of"] is { } ofClass)
                return JObject.FromObject(dumped.GetExports().Where(e => e.ExportType == ofClass)
                    .GroupBy(e => e.Outer?.Class?.Name.Text ?? "(none)").OrderByDescending(g => g.Count())
                    .ToDictionary(g => g.Key, g => (object)new { count = g.Count(), example = g.First().Outer?.Name }));
            if (query["full"] == "1")
                return new JRaw(JsonConvert.SerializeObject(dumped.GetExports(), settings));
            return new JArray(dumped.GetExports().Select(e => new JObject
            {
                ["name"] = e.Name, ["type"] = e.ExportType, ["outer"] = e.Outer?.Name.Text,
                ["props"] = JToken.Parse(JsonConvert.SerializeObject(e.Properties.ToDictionary(p => p.Name.Text, p => p.Tag?.GenericValue), settings)),
            }));
        }
        if (route == "fork-emote-census")
        {
            // tests: how emotes play effects - each kind of notify their montages (and its sections'
            // sequences) carry, a Template's class with it (Niagara, Cascade): in how many emotes, which
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
        if (route == "fork-effect-census")
        {
            // tests: how the idle effects of a tab's items (?types=Outfit,Backpack: their parts') feed their
            // materials (renderer parameters, bindings to the system's variables, materials from user
            // parameters) and what their emitters run on / call: per kind, how many and where
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
                                var props = new JObject();
                                foreach (var p in variant.Properties)
                                    if (p.Tag?.GenericValue is { } v && !props.ContainsKey(p.Name.Text)) props[p.Name.Text] = JToken.FromObject(v, serializer);
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
            foreach (var group in variantKinds.GroupBy(v => v.Kind))
                foreach (var (kind, where) in group)
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
            // tests: run one asset tab's loader as the Assets page does, and say what it lists (icons=1: with each one's icon)
            var loader = AppServices.AssetLoading.Get(Enum.Parse<EExportType>(query["type"] ?? "LegoOutfit"));
            var clock = Stopwatch.StartNew();
            await loader.Load();
            var names = loader.Source.Items.Select(a => a.CreationData is Models.Assets.Asset.AssetItemCreationArgs args
                    ? $"{args.DisplayName} = {args.Object.GetPathName()}" + (query["icons"] == "1" ? $" [{args.IconPath?.Split('/').Last()}]" : "")
                      + (query["described"] == "1" ? $" | {args.Description}" : "")
                    : a.CreationData.DisplayName)
                .OrderBy(n => n).ToList();
            var filter = query["filter"] ?? "";
            // check=1 (LEGO): every listed figure resolved to a cooked mesh or a recipe; the ones with neither named
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
                sample = names.Where(n => n.Contains(filter, StringComparison.OrdinalIgnoreCase)).Take(12),
                cooked, failed = failed.Count, failedSample = failed.Take(12)
            });
        }
        if (route == "fork-export-asset")
        {
            // tests: FP's export of one asset (type=Car|Outfit|...; picks for a car, face=Mouth:12,Eyes:2 for a
            // LEGO figure) as the plugin receives it
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
            var carStyles = (query["picks"] ?? "").Split(',', StringSplitOptions.RemoveEmptyEntries).Select(x => x.Split(':'))
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
            var assetData = await assetSession.RunAsync(() => [assetSession.CreateExport(asset.Name, asset, type, carStyles)]);
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
        // tests: /fork-export-world?path=<level package>[&landscape=1][&actor=name part], FP's world export of that
        // one level (actors and instances, as the Map page sends it to Blender) as the plugin receives it;
        // track=<Rocket Racing track object path>&points=x,y,z;x,y,z...: also that track's road laid along those points
        if (route != "fork-export-world") return null;
        var path = query["path"] ?? throw new ArgumentException("path missing");
        // a World Partition cell's world is named after its map, not its file
        var package = await Game.Provider.LoadPackageAsync(path);
        var world = package.GetExports().OfType<UWorld>().FirstOrDefault()
                    ?? throw new FileNotFoundException("no world in " + path);
        using var meta = AppServices.AppSettings.ExportSettings.CreateExportMeta(EExportLocation.Blender);
        meta.WorldFlags = EWorldFlags.Actors | EWorldFlags.InstancedFoliage | (query["landscape"] == "1" ? EWorldFlags.Landscape : 0);
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
