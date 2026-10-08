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
public partial class MaterialPorterService : IService
{
    public static int Port => Fork.BridgePort;

    public static MaterialPorterService Instance => AppServices.Services.GetRequiredService<MaterialPorterService>();

    public GameContext Game { get; } = new();
    public MaterialService? Materials { get; private set; }
    private Bridge? _bridge;

    const string NoGraphsMessage = "material-porter-no-graphs";

    /// <summary>A load starting: the last one's warnings no longer hold.</summary>
    public void OnGameLoading() => Avalonia.Threading.Dispatcher.UIThread.Post(() => AppServices.Info.CloseMessage(NoGraphsMessage));

    /// <summary>Once the game's files are mounted: the bridge starts listening (again after a reload).</summary>
    public void OnGameLoaded(IFileProvider provider, string? buildVersion)
    {
        Game.Provider = provider;
        Game.BuildVersion = string.IsNullOrWhiteSpace(buildVersion) ? "unknown" : buildVersion;
        Materials = new MaterialService(Game);
        IslandProjects.Log = message => Log.Information("[Material Porter] {Message}", message);
        IslandProjects.Debug = message => Log.Debug("[Material Porter] {Message}", message);
        ApplyProjectFolders();
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
                // (a build downloaded from its manifest gets them from its UEFN manifest instead)
                var downloaded = AppServices.AppSettings.Installation.CurrentProfile.IsCustomOnDemand;
                Avalonia.Threading.Dispatcher.UIThread.Post(() => AppServices.Info.Message("Exact materials need UEFN",
                    downloaded
                        ? "This build was loaded without UEFN data, so materials are approximated. In Installation, pick a build " +
                          "marked \"+ UEFN\" (or set its UEFN Manifest), then reload."
                        : "This Fortnite install has no editor data, so materials are approximated. Install Unreal Editor for Fortnite " +
                          "from the Epic Games Launcher (it adds its editor data to the game's folder), then restart the app.",
                    FluentAvalonia.UI.Controls.InfoBarSeverity.Warning, autoClose: false, id: NoGraphsMessage));
            });
        }
        catch (Exception e)
        {
            _bridge = null;
            Log.Error(e, "[Material Porter] could not listen on localhost:{Port}; Blender falls back to FP's materials", Port);
        }
    }

    // ------------------------------------------------------------ islands
    /// <summary>
    /// The folders of the user's UEFN projects (Settings > Application) to Material Porter's core, which reads an
    /// island's master graphs from the matching project. Owner builds only, as islands are; the core also
    /// takes MATERIAL_PORTER_PROJECTS whatever the settings say.
    /// </summary>
    public static void ApplyProjectFolders() =>
        IslandProjects.Roots = Fork.Islands
            ? AppSettings.Application.UefnProjectFolders.Concat(DetectedProjectRoots()).Distinct(StringComparer.OrdinalIgnoreCase).ToArray()
            : [];

    /// <summary>
    /// Where UEFN itself keeps the user's projects: the default Fortnite Projects folder, and the folders of the
    /// projects it lists outside it (its EditorPerProjectUserSettings.ini: AdditionalProjectFiles=X:/.../P/P.uefnproject,
    /// each one's parent, so the projects beside it count too). Found by themselves; the settings add to them.
    /// </summary>
    static IEnumerable<string> DetectedProjectRoots()
    {
        var defaultRoot = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments), "Fortnite Projects");
        if (Directory.Exists(defaultRoot)) yield return defaultRoot;
        var ini = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                               "UnrealEditorFortnite", "Saved", "Config", "WindowsEditor", "EditorPerProjectUserSettings.ini");
        if (!File.Exists(ini)) yield break;
        string[] lines;
        try { lines = File.ReadAllLines(ini); }
        catch (IOException) { yield break; }
        foreach (var line in lines)
        {
            if (!line.StartsWith("AdditionalProjectFiles=", StringComparison.Ordinal)) continue;
            var file = line["AdditionalProjectFiles=".Length..].Trim().Trim('"').Replace('/', Path.DirectorySeparatorChar);
            var parent = Path.GetDirectoryName(Path.GetDirectoryName(file));
            if (parent != null && Directory.Exists(parent)) yield return parent;
        }
    }

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
        catch (Exception e) { Log.Warning("[Material Porter] island keys file unreadable ({Error}): no island keys", e.GetType().Name); return []; }
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
            catch (Exception e) { Log.Warning("[Material Porter] Material Porter settings unreadable ({Error}): the default island key tool folder", e.GetType().Name); }
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
        try { provider.LoadVirtualPaths(); } catch (Exception e) { Log.Warning("[Material Porter] island {Code}: virtual paths not loaded, its files resolve by path still: {Error}", code, e.Message); }
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
                        catch (Exception e) { Exporting.MaterialPorter.Failures.Note("car names", a.AssetName.Text, e); /* its asset name, then */ }
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
        var hasObjectName = dot > bodyObjectPath.LastIndexOf('/');
        var package = hasObjectName ? bodyObjectPath[..dot] : bodyObjectPath;
        var name = hasObjectName ? bodyObjectPath[(dot + 1)..] : bodyObjectPath.Split('/').Last();
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
        if (route == "log")
        {
            // the Blender plugin telling what it does while it imports (status.py): a JSON list of lines, begin/end
            var lines = JsonConvert.DeserializeObject<string[]>(query["lines"] ?? "[]") ?? [];
            StatusLog.Instance.FromBlender(query["state"], lines);
            return "ok";
        }
#if DEBUG
        return await TestRouteAsync(route, query);
#else
        return null;
#endif
    }
}
