using System;
using System.Collections.Generic;
using System.Collections.Specialized;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using CUE4Parse.UE4.Assets.Exports;
using CUE4Parse.UE4.Assets.Exports.Paper2D;
using CUE4Parse.UE4.Assets.Exports.Texture;
using CUE4Parse.UE4.Objects.Engine;
using CUE4Parse.Utils;
using FortnitePorting.Application;
using FortnitePorting.CUE4Parse.Models.Unreal;
using FortnitePorting.CUE4Parse.Models.Unreal.VirtualTexture;
using FortnitePorting.Exporting;
using FortnitePorting.Exporting.Models;
using FortnitePorting.Models.Files;
using FortnitePorting.Shared.Extensions;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace FortnitePorting.MaterialPorter;

// The Files page as data, for the Fortnite editor's Files tab:
//   files-search?q=<words>&type=<kind>&limit=600   {total, items:[{path, name, folder, container, kind, kind_exact}]}
//   files-folder?path=<folder>                     {path, folders:[{name, path, files}], files:[items]} (no package loads)
//   files-info?path=                               {path, kind, class, display, kind_exact: true} (loads the package)
//   files-export?paths=a;b&settings=Name:Value;    the export as the plugin receives it, plus skipped:[names]
//   files-properties?path=                         {json} as the Files page's Properties export writes it
// While the app still builds its file list, search and folder answer {loading: true}.
public partial class MaterialPorterService
{
    private static readonly SemaphoreSlim WorldGate = new(1, 1);   // one world export at a time (they are heavy)
    private static Dictionary<string, string>? _registryClasses;

    private static Task<object?>? FilesRoute(string route, NameValueCollection query) => route switch
    {
        "files-search" => Task.FromResult<object?>(FilesSearch(query)),
        "files-folder" => Task.FromResult<object?>(FilesFolder(query["path"] ?? "")),
        "files-info" => FilesInfoAsync(query["path"] ?? throw new ArgumentException("path missing")),
        "files-export" => FilesExportAsync((query["paths"] ?? throw new ArgumentException("paths missing")).Split(';', StringSplitOptions.RemoveEmptyEntries), query["settings"]),
        "files-properties" => Task.FromResult<object?>(FilesProperties(query["path"] ?? throw new ArgumentException("path missing"))),
        _ => null,
    };

    private static object FilesSearch(NameValueCollection query)
    {
        var words = (query["q"] ?? "").Split(' ', StringSplitOptions.RemoveEmptyEntries);
        if (words.Length == 0) throw new ArgumentException("q missing");
        if (AppServices.Files.IsLoading) return new { loading = true };
        var kind = query["type"];
        var limit = int.TryParse(query["limit"], out var most) && most > 0 ? most : 600;
        var matches = AppServices.Files.ListedFiles()
            .Where(f => words.All(w => f.Path.Contains(w, StringComparison.OrdinalIgnoreCase)))
            .Select(f => Item(f.Path, f.VfsName))
            .Where(i => string.IsNullOrEmpty(kind) || i.kind == kind)
            .ToList();
        return new { total = matches.Count, items = matches.OrderBy(i => i.name, StringComparer.OrdinalIgnoreCase).Take(limit) };
    }

    private static object FilesFolder(string path)
    {
        if (AppServices.Files.IsLoading) return new { loading = true };
        var node = AppServices.Files.RootFileNode;
        foreach (var part in path.Split('/', StringSplitOptions.RemoveEmptyEntries))
            if (!node.TryGetChild(part, out node)) throw new ArgumentException("no folder " + path);
        var children = node.Children.Values.OrderBy(c => c.Name, StringComparer.OrdinalIgnoreCase).ToList();
        return new
        {
            path,
            folders = children.Where(c => c.Type == ENodeType.Folder).Select(c => new { name = c.Name, path = path.Length == 0 ? c.Name : path.TrimEnd('/') + "/" + c.Name, files = c.FileChildCount }),
            files = children.Where(c => c.Type == ENodeType.File).Select(c => Item(c.Path, c.VfsName)),
        };
    }

    private record FileItem(string path, string name, string folder, string container, string kind, bool kind_exact);

    private static FileItem Item(string path, string container)
    {
        var name = path.SubstringAfterLast("/").SubstringBeforeLast(".");
        var registry = RegistryClass(path);
        return new FileItem(path, name, path.SubstringBeforeLast("/"), container,
            registry is not null ? KindOfClass(registry) : GuessKind(path, name), registry is not null);
    }

    private static string? RegistryClass(string path)
    {
        _registryClasses ??= AppServices.UEParse.AssetRegistry
            .GroupBy(a => a.PackageName.Text, StringComparer.OrdinalIgnoreCase)
            .ToDictionary(g => g.Key, g => g.First().AssetClass.Text, StringComparer.OrdinalIgnoreCase);
        return _registryClasses.GetValueOrDefault(PackageName(path));
    }

    /// <summary>A game file's package name as the registry lists it: /Game/..., /Engine/... or /&lt;Plugin&gt;/...</summary>
    private static string PackageName(string path)
    {
        var p = path.SubstringBeforeLast(".");
        if (p.StartsWith("FortniteGame/Content/")) return "/Game/" + p["FortniteGame/Content/".Length..];
        if (p.StartsWith("Engine/Content/")) return "/Engine/" + p["Engine/Content/".Length..];
        var content = p.IndexOf("/Content/", StringComparison.Ordinal);
        return content < 0 ? p : "/" + p[..content].SubstringAfterLast("/") + "/" + p[(content + "/Content/".Length)..];
    }

    private static string KindOfClass(string cls) => cls switch
    {
        "StaticMesh" or "SkeletalMesh" or "Skeleton" => "Mesh",
        "World" => "Map",
        "SoundWave" or "SoundCue" or "MetaSoundSource" => "Sound",
        "AnimSequence" or "AnimMontage" or "AnimComposite" or "PoseAsset" => "Animation",
        "BlueprintGeneratedClass" or "Blueprint" or "WidgetBlueprintGeneratedClass" => "Blueprint",
        _ when cls.Contains("Texture") => "Texture",
        _ when cls.StartsWith("Material") => "Material",
        _ => "Other",
    };

    /// <summary>The kind from the file name alone (Fortnite's naming prefixes): the registry lists few meshes and materials.</summary>
    private static string GuessKind(string path, string name)
    {
        if (path.EndsWith(".umap")) return "Map";
        var prefix = name.SubstringBefore("_") + "_";
        return prefix.ToUpperInvariant() switch
        {
            "T_" or "TX_" => "Texture",
            "SM_" or "S_" or "SK_" or "SKM_" => "Mesh",
            "M_" or "MI_" or "MM_" or "MF_" or "MAT_" => "Material",
            "A_" or "AS_" or "AM_" or "ANIM_" or "EMOTE_" => "Animation",
            "SW_" or "SC_" or "SFX_" or "MS_" => "Sound",
            "BP_" or "B_" or "BGA_" or "PBWA_" => "Blueprint",
            _ => "Other",
        };
    }

    /// <summary>A file loaded as the Files page loads it for export (.umap: its world; blueprints: their class).</summary>
    private static async Task<UObject?> LoadForExport(string path)
    {
        var provider = AppServices.UEParse.Provider!;
        var basePath = ExportSession.FixPath(path);
        UObject? asset;
        if (path.EndsWith(".umap"))
        {
            asset = await provider.SafeLoadPackageObjectAsync(basePath);
            if (asset is not UWorld)
                asset = (await provider.LoadPackageAsync(basePath)).GetExports().OfType<UWorld>().FirstOrDefault();
        }
        else
        {
            asset = await provider.SafeLoadPackageObjectAsync(basePath)
                    ?? await provider.SafeLoadPackageObjectAsync($"{basePath}.{basePath.SubstringAfterLast("/")}_C");
        }
        return asset switch
        {
            UVirtualTextureBuilder vtb => vtb.Texture.Load<UVirtualTexture2D>(),
            UPaperSprite sprite => sprite.BakedSourceTexture.Load<UTexture2D>(),
            _ => asset,
        };
    }

    private static async Task<object?> FilesInfoAsync(string path)
    {
        var asset = await LoadForExport(path) ?? throw new ArgumentException("can't load " + path);
        return new { path, kind = KindOfClass(asset.ExportType), @class = asset.ExportType, display = asset.Name, kind_exact = true };
    }

    private static async Task<object?> FilesExportAsync(string[] paths, string? overrides)
    {
        using var status = StatusLog.Instance.Exporting(paths.Length == 1 ? $"Exporting {paths[0].SubstringAfterLast("/")}" : $"Exporting {paths.Length} files");
        var entries = new List<(UObject Asset, EExportType Type)>();
        var skipped = new List<string>();
        foreach (var path in paths)
        {
            var asset = await LoadForExport(path);
            var type = asset is null ? EExportType.None : AppServices.Exporter.DetermineExportType(asset);
            if (type is EExportType.None) skipped.Add(path.SubstringAfterLast("/"));
            else entries.Add((asset!, type));
        }
        if (entries.Count == 0) return new JObject { ["Exports"] = new JArray(), ["skipped"] = JArray.FromObject(skipped) };
        var (exportMeta, settings) = EditorExportMeta(overrides);
        using var meta = exportMeta;
        meta.WorldFlags = EWorldFlags.Actors | EWorldFlags.Landscape | EWorldFlags.WorldPartitionGrids | EWorldFlags.HLODs
                          | EWorldFlags.Lights | EWorldFlags.Decals | EWorldFlags.Effects;
        if (meta.Settings.ImportInstancedFoliage) meta.WorldFlags |= EWorldFlags.InstancedFoliage;
        var hasWorld = entries.Any(e => e.Type is EExportType.World);
        if (hasWorld) await WorldGate.WaitAsync();
        try
        {
            var session = new ExportSession(meta);
            var data = await Task.Run(() => session.RunAsync(() => entries.Select(e => session.CreateExport(e.Asset.Name, e.Asset, e.Type, []))));
            var payload = JObject.Parse(ExportPayload(data, settings).ToString());
            payload["skipped"] = JArray.FromObject(skipped);
            return payload;
        }
        finally
        {
            if (hasWorld) WorldGate.Release();
        }
    }

    private static object FilesProperties(string path)
    {
        if (!AppServices.UEParse.Provider!.TryLoadObjectExports(path, out var exports))
            throw new ArgumentException("can't read " + path);
        return new { json = JsonConvert.SerializeObject(exports, Formatting.Indented) };
    }
}
