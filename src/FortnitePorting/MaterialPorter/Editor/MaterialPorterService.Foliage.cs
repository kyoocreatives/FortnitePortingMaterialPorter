using System;
using System.Collections.Generic;
using System.Collections.Specialized;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using CUE4Parse.UE4.Assets;
using CUE4Parse.UE4.Assets.Exports;
using CUE4Parse.UE4.Assets.Exports.Material;
using CUE4Parse.UE4.Assets.Exports.StaticMesh;
using CUE4Parse.UE4.Assets.Objects;
using CUE4Parse.Utils;
using FortnitePorting.Application;
using FortnitePorting.Exporting;
using FortnitePorting.Exporting.MaterialPorter;
using FortnitePorting.Exporting.Models;
using FortnitePorting.Exporting.Types;
using Newtonsoft.Json.Linq;

namespace FortnitePorting.MaterialPorter;

// Grass types for the Fortnite editor's Scatter mode (schema: GrassTypes.cs in the fork):
//   foliage-grass-types?query=<words>              [{name, path, varieties}] (every LandscapeGrassType in the game files)
//   foliage-grass-type?path=                       {name, path, varieties: [schema]}
//   foliage-grass-mesh?mesh=&materials=a;;b&settings=Name:Value;   a static mesh's export payload with a grass variety's
//                                                  OverrideMaterials over its slots (empty entry: the slot keeps its own)
//   foliage-material-grass?material=               MPGrass of a material's master (no grass output: outputs []), for a
//                                                  terrain no map placement gave one
// Paths are package paths (/Game/..., /<Plugin>/...) or file keys.
public partial class MaterialPorterService
{
    private static readonly SemaphoreSlim GrassGate = new(1, 1);
    private static (object Provider, List<GrassTypeEntry> List)? _grassTypes;

    private record GrassTypeEntry(string name, string path, int varieties, string key);

    private static Task<object?>? FoliageRoute(string route, NameValueCollection query) => route switch
    {
        "foliage-grass-types" => GrassTypesAsync(query["query"] ?? ""),
        "foliage-grass-type" => GrassTypeAsync(query["path"] ?? throw new ArgumentException("path missing")),
        "foliage-grass-mesh" => GrassMeshAsync(query["mesh"] ?? throw new ArgumentException("mesh missing"), query["materials"], query["settings"]),
        "foliage-material-grass" => MaterialGrassAsync(query["material"] ?? throw new ArgumentException("material missing")),
        _ => null,
    };

    private static Task<object?> MaterialGrassAsync(string path) => Task.Run<object?>(() =>
    {
        var material = LoadMain(path) as UUnrealMaterial ?? throw new ArgumentException("no material " + path);
        while (material is UMaterialInstanceConstant instance)
            material = instance.Parent?.LoadOrDefault<UUnrealMaterial>() ?? throw new ArgumentException("no master for " + path);
        var master = material as UMaterial ?? throw new ArgumentException("no master for " + path);
        return GrassTypes.Landscape(AppServices.UEParse.Provider!, [master])
               ?? new Dictionary<string, object> { ["material"] = master.GetPathName().SubstringBeforeLast("."), ["outputs"] = new List<object>() };
    });

    private static async Task<object?> GrassTypesAsync(string query)
    {
        var words = query.Split(' ', StringSplitOptions.RemoveEmptyEntries);
        return (await GrassTypeListAsync())
            .Where(t => words.All(w => t.path.Contains(w, StringComparison.OrdinalIgnoreCase)))
            .Select(t => new { t.name, t.path, t.varieties }).ToList();
    }

    private static async Task<object?> GrassTypeAsync(string path)
    {
        var entry = (await GrassTypeListAsync()).FirstOrDefault(t => SamePackage(t, path)) ?? throw new ArgumentException("no grass type " + path);
        var asset = await Task.Run(() => GrassTypeIn(AppServices.UEParse.Provider!.LoadPackage(entry.key)))
                    ?? throw new ArgumentException(path + " holds no LandscapeGrassType");
        return new { entry.name, entry.path, varieties = GrassTypes.Read(asset) };
    }

    private static bool SamePackage(GrassTypeEntry t, string path)
    {
        var p = path.SubstringBeforeLast(".uasset");
        if (p.LastIndexOf('.') > p.LastIndexOf('/')) p = p.SubstringBeforeLast(".");
        return t.path.Equals(p, StringComparison.OrdinalIgnoreCase) || t.key.SubstringBeforeLast(".").Equals(p, StringComparison.OrdinalIgnoreCase);
    }

    /// <summary>Every LandscapeGrassType package (named LGT_* or *GrassType*; the registry misses plugin content), read once
    /// per mounted game.</summary>
    private static async Task<List<GrassTypeEntry>> GrassTypeListAsync()
    {
        var provider = AppServices.UEParse.Provider!;
        if (_grassTypes is { } known && ReferenceEquals(known.Provider, provider)) return known.List;
        await GrassGate.WaitAsync();
        try
        {
            if (_grassTypes is { } built && ReferenceEquals(built.Provider, provider)) return built.List;
            var list = await Task.Run(() =>
            {
                var found = new List<GrassTypeEntry>();
                var keys = provider.Files.Keys.Where(k => k.EndsWith(".uasset", StringComparison.OrdinalIgnoreCase)
                                                          && !k.EndsWith(".o.uasset", StringComparison.OrdinalIgnoreCase)).ToList();
                foreach (var key in keys)
                {
                    var name = key.SubstringAfterLast("/").SubstringBeforeLast(".");
                    if (!name.StartsWith("LGT_", StringComparison.OrdinalIgnoreCase) && !name.Contains("GrassType", StringComparison.OrdinalIgnoreCase)) continue;
                    try
                    {
                        if (provider.TryLoadPackage(key, out var package) && GrassTypeIn(package) is { } asset)
                            found.Add(new GrassTypeEntry(asset.Name, PackageName(key), asset.GetOrDefault("GrassVarieties", Array.Empty<FStructFallback>()).Length, key));
                    }
                    catch (Exception e)
                    {
                        Failures.Note("grass types", key, e);
                    }
                }
                return found.OrderBy(t => t.name, StringComparer.OrdinalIgnoreCase).ToList();
            });
            _grassTypes = (provider, list);
            return list;
        }
        finally
        {
            GrassGate.Release();
        }
    }

    private static UObject? GrassTypeIn(IPackage package) => package.GetExports().FirstOrDefault(e => e.ExportType == "LandscapeGrassType");

    private static async Task<object?> GrassMeshAsync(string meshPath, string? materials, string? overrides)
    {
        var mesh = LoadMain(meshPath) as UStaticMesh ?? throw new ArgumentException("no static mesh " + meshPath);
        var slots = string.IsNullOrEmpty(materials) ? new List<UMaterialInterface?>() : materials.Split(';')
            .Select(p => p.Length == 0 ? null : LoadMain(p) as UMaterialInterface ?? throw new ArgumentException("no material " + p)).ToList();
        using var status = StatusLog.Instance.Exporting($"Exporting {mesh.Name}");
        var (exportMeta, settings) = EditorExportMeta(overrides);
        using var meta = exportMeta;
        var session = new ExportSession(meta);
        var data = await Task.Run(() => session.RunAsync(() =>
        {
            var export = (MeshExport) session.CreateExport(mesh.Name, mesh, EExportType.Mesh, []);
            export.OverrideSlots(slots);
            return new BaseExport[] { export };
        }));
        return JObject.Parse(ExportPayload(data, settings).ToString());
    }

    /// <summary>A package's main export by package path (/Game/.., /Plugin/.., "Pkg.Obj") or file key.</summary>
    private static UObject? LoadMain(string path)
    {
        var provider = AppServices.UEParse.Provider!;
        var p = path.SubstringBeforeLast(".uasset");
        if (p.LastIndexOf('.') > p.LastIndexOf('/')) p = p.SubstringBeforeLast(".");
        if (!provider.TryLoadPackage(p, out var package))
        {
            // a plugin mount CUE4Parse doesn't map: the file whose package name is p
            var key = provider.Files.Keys.FirstOrDefault(k => k.EndsWith(".uasset", StringComparison.OrdinalIgnoreCase)
                && !k.EndsWith(".o.uasset", StringComparison.OrdinalIgnoreCase) && PackageName(k).Equals(p, StringComparison.OrdinalIgnoreCase));
            if (key is null || !provider.TryLoadPackage(key, out package)) return null;
        }
        var name = p.SubstringAfterLast("/");
        var exports = package.GetExports().ToList();
        return exports.FirstOrDefault(e => e.Name.Equals(name, StringComparison.OrdinalIgnoreCase)) ?? exports.FirstOrDefault();
    }
}
