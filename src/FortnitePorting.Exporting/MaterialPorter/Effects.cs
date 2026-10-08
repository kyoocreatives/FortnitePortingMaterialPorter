using System;
using System.Collections.Generic;
using System.Linq;
using System.Runtime.CompilerServices;
using CUE4Parse.UE4.Assets.Exports;
using CUE4Parse.UE4.Assets.Objects;
using CUE4Parse.UE4.Objects.Core.i18N;
using CUE4Parse.UE4.Objects.Core.Math;
using CUE4Parse.UE4.Objects.Core.Misc;
using CUE4Parse.UE4.Objects.Engine.VectorField;
using CUE4Parse.UE4.Objects.UObject;
using FortnitePorting.CUE4Parse.Extensions;
using CUE4Parse.UE4.Objects.Engine;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace FortnitePorting.Exporting.MaterialPorter;

/// <summary>
/// A Niagara system's emitters as the cooked asset keeps them. A system lists emitter handles; a handle is a standard emitter
/// (one of its versions: CPU or GPU simulated, with renderers) or a stateless one (UE 5.4's lightweight emitters: plain settings).
/// </summary>
public static class Effects
{
    public const string ContrailClass = "AthenaSkyDiveContrailItemDefinition";
    /// <summary>A contrail item's effect (older ones name only a Cascade effect: nothing to replay).</summary>
    public const string ContrailEffect = "NiagaraContrailEffect";

    /// <summary>
    /// A pickaxe's own effects as its weapon definition names them: the property, its label, and the property naming the socket it
    /// sits on (the trail runs between two sockets instead).
    /// </summary>
    public static readonly (string Property, string Name, string? Socket)[] PickaxeEffects =
    [
        ("AnimTrailsNiagara", "trail", null),
        ("SwingEffectNiagara", "swing", "SwingFXSocketName"),
        ("IdleEffectNiagara", "idle", "IdleFXSocketName"),
    ];
    public const string TrailFirstSocket = "AnimTrailsFirstSocketName", TrailSecondSocket = "AnimTrailsSecondSocketName";

    /// <summary>Which own effects a pickaxe's weapon definition has: "trail", "swing", "idle".</summary>
    public static List<string> PickaxeEffectNames(UObject weaponDefinition) =>
        PickaxeEffects.Where(e => Named(weaponDefinition.GetDataListItem<FSoftObjectPath>(e.Property).AssetPathName)).Select(e => e.Name)
            .Concat(PickaxeImpacts(weaponDefinition).Count > 0 ? ["impact"] : []).ToList();

    /// <summary>A weapon definition's hit effects: each system with the surfaces it is for (most have one for all, Default).</summary>
    public static List<(FSoftObjectPath System, List<string> Surfaces)> PickaxeImpacts(UObject weaponDefinition)
    {
        var found = new List<(FSoftObjectPath, List<string>)>();
        if (weaponDefinition.GetDataListItem<UScriptMap>("ImpactNiagaraPhysicalSurfaceEffectsMap") is not { } map) return found;
        foreach (var (key, value) in map.Properties)
        {
            if (value?.GetValue(typeof(FSoftObjectPath)) is not FSoftObjectPath path || !Named(path.AssetPathName)) continue;
            var surface = key.GenericValue?.ToString() ?? "";
            surface = surface[(surface.LastIndexOf(':') + 1)..].Replace("SurfaceType_", "");
            var at = found.FindIndex(f => f.Item1.AssetPathName == path.AssetPathName);
            if (at < 0) found.Add((path, [surface]));
            else found[at].Item2.Add(surface);
        }
        return found;
    }

    public const string PartEffect = "IdleEffectNiagara", PartSocket = "IdleFXSocketName";

    /// <summary>
    /// The Niagara systems the Effects tab lists: the asset registry's (nearly all islands') and the game's own found by file name
    /// (NS_...), which the cooked registry mostly omits. (package path as mounted, object name), each package once. Editor data
    /// (.o.uasset) and the engine's templates and examples (/Niagara/, not cooked to play) are left out.
    /// </summary>
    public static List<(string Package, string Name)> ListedSystems(global::CUE4Parse.FileProvider.IFileProvider provider,
        IEnumerable<(string Package, string Name)> registry)
    {
        var found = new Dictionary<string, (string, string)>(StringComparer.OrdinalIgnoreCase);
        foreach (var (package, name) in registry)
            if (!package.StartsWith("/Niagara/", StringComparison.OrdinalIgnoreCase)) found.TryAdd(package, (package, name));
        foreach (var path in provider.Files.Keys)
        {
            if (!path.EndsWith(".uasset", StringComparison.OrdinalIgnoreCase) || path.EndsWith(".o.uasset", StringComparison.OrdinalIgnoreCase)) continue;
            var name = path[(path.LastIndexOf('/') + 1)..^".uasset".Length];
            if (!name.StartsWith("NS_", StringComparison.OrdinalIgnoreCase)) continue;
            var package = MountPath(path[..^".uasset".Length]);
            if (!package.StartsWith("/Niagara/", StringComparison.OrdinalIgnoreCase)) found.TryAdd(package, (package, name));
        }
        return found.Values.ToList();
    }

    /// <summary>A package's mounted path from its file's: FortniteGame/Content/X is /Game/X, a plugin's .../Name/Content/X is /Name/X.</summary>
    public static string MountPath(string file)
    {
        var at = file.IndexOf("/Content/", StringComparison.OrdinalIgnoreCase);
        if (at < 0) return "/" + file;
        var root = file[..at];
        var mount = root.Equals("FortniteGame", StringComparison.OrdinalIgnoreCase) ? "Game"
            : root.Equals("Engine", StringComparison.OrdinalIgnoreCase) ? "Engine" : root[(root.LastIndexOf('/') + 1)..];
        return "/" + mount + file[(at + "/Content".Length)..];
    }

    /// <summary>
    /// A system as its package's export map tells it, without reading it: the class of the object of its name, its emitters, how many
    /// run on the GPU (a GPUComputeScript: not replayed) and what its renderers draw.
    /// </summary>
    public sealed record Outline(string? Class, int Emitters, int Gpu, string[] Draws) : Unloaded.IOutline
    {
        public bool Plays => Emitters > Gpu;

        public string Describe(string folder)
        {
            var what = Emitters == 0 ? "No emitters" : $"{Emitters} emitter{(Emitters == 1 ? "" : "s")}"
                + (Draws.Length > 0 ? ": " + string.Join(", ", Draws) : "");
            var gpu = Gpu == 0 || Emitters == 0 ? "" : Gpu >= Emitters ? " · GPU only: its pieces come still, not played"
                : $" · {Gpu} on the GPU: still, not played";
            return $"{what}{gpu} · {folder}";
        }
    }

    public static async System.Threading.Tasks.Task<Outline?> ReadOutline(global::CUE4Parse.FileProvider.IFileProvider provider, string package, string name)
    {
        try
        {
            var loaded = await provider.LoadPackageAsync(package);
            string? found = null;
            int emitters = 0, gpu = 0;
            var draws = new SortedSet<string>();
            for (var i = 0; i < loaded.ExportMapLength; i++)
            {
                if (loaded.ResolvePackageIndex(new FPackageIndex(loaded, i + 1)) is not { } export) continue;
                var type = export.Class?.Name.Text ?? "";
                var exportName = export.Name.Text;
                if (found is null && exportName.Equals(name, StringComparison.OrdinalIgnoreCase)) found = type;
                if (type is "NiagaraEmitter" or "NiagaraStatelessEmitter") emitters++;
                else if (type == "NiagaraScript" && exportName.StartsWith("GPUComputeScript", StringComparison.OrdinalIgnoreCase)) gpu++;
                else if (type.StartsWith("Niagara") && type.EndsWith("RendererProperties")) draws.Add(DrawsOf(type));
            }
            return new Outline(found, emitters, gpu, draws.ToArray());
        }
        catch (Exception e)
        {
            Failures.Note("effect outlines", package, e);
            return null;
        }
    }

    /// <summary>Whether any of a system's emitters is replayed (not all on the GPU), for the tab's filter.</summary>
    public static bool Plays(UObject system)
    {
        if (Unloaded.Detail(system) is Outline outline) return outline.Plays;
        return _plays.GetValue(system, s => new StrongBox<bool>(Emitters(s).Any(e => e.Sim != "GPU"))).Value;
    }

    private static readonly ConditionalWeakTable<UObject, StrongBox<bool>> _plays = new();

    /// <summary>
    /// The system a soft path names, if it shows something: many parts name a blank system (NS_Blank_Body, NS_Empty: no emitter,
    /// or one without a renderer) to switch off their base part's effect.
    /// </summary>
    public static UObject? Shown(FSoftObjectPath path)
    {
        try
        {
            return Named(path.AssetPathName) && path.TryLoad(out UObject? system) && Emitters(system).Any(e => e.Renderers.Count > 0) ? system : null;
        }
        catch (Exception e)
        {
            Failures.Note("item effects", path.AssetPathName.Text, e);
            return null;
        }
    }

    /// <summary>
    /// The systems the item's styles swap its effects for, those that show something (not NS_Empty for an aura), with each
    /// style's name and whether it is its channel's first option (picked unless the user picks another).
    /// </summary>
    public static List<(string Style, UObject System, bool First)> StyleSystems(UObject item)
    {
        var systems = new List<(string, UObject, bool)>();
        try
        {
            foreach (var variant in item.GetOrDefault("ItemVariants", Array.Empty<UObject>()))
                foreach (var property in variant.Properties)
                    if (property.Tag?.GenericValue is UScriptArray { Properties: var options })
                        foreach (var (option, index) in options.Select(o => o.GetValue(typeof(FStructFallback))).OfType<FStructFallback>().Select((o, i) => (o, i)))
                        {
                            var style = option.GetOrDefault<FText?>("VariantName")?.Text ?? "";
                            systems.AddRange(option.GetOrDefault("VariantParticles", Array.Empty<FStructFallback>())
                                .Select(s => Shown(s.GetOrDefault<FSoftObjectPath>("OverrideParticleSystem"))).OfType<UObject>().Select(system => (style, system, index == 0)));
                            // a style's own parts (Blackheart's later stages: a body whose part has the aura)
                            foreach (var path in option.GetOrDefault("VariantParts", Array.Empty<FSoftObjectPath>()))
                                if (path.TryLoad(out UObject? part) && Shown(part.GetOrDefault<FSoftObjectPath>(PartEffect)) is { } system)
                                    systems.Add((style, system, index == 0));
                        }
        }
        catch (Exception e)
        {
            Failures.Note("item effect styles", item.Name, e);
            // an item whose styles don't read is treated as having none
        }
        return systems;
    }

    /// <summary>The parts a back bling or outfit is made of before any style.</summary>
    private static UObject[] BaseParts(UObject item, EExportType type) =>
        type is EExportType.Backpack ? item.GetOrDefault("CharacterParts", Array.Empty<UObject>()) : OutfitParts(item);

    /// <summary>An outfit's character parts: its own, else its hero definition's first specialization's.</summary>
    public static UObject[] OutfitParts(UObject outfit)
    {
        var parts = outfit.GetOrDefault("BaseCharacterParts", Array.Empty<UObject>());
        if (parts.Length == 0 && outfit.TryGetValue(out UObject hero, "HeroDefinition") && hero.TryGetValue(out UObject[] specializations, "Specializations"))
            parts = specializations.FirstOrDefault()?.GetOrDefault("CharacterParts", Array.Empty<UObject>()) ?? [];
        return parts;
    }

    /// <summary>A glider's trail effects: (system, socket, offset), from its trail definitions or older trail properties.</summary>
    public static List<(FSoftObjectPath System, FName Socket, FTransform? Offset)> GliderTrails(UObject glider)
    {
        var trails = new List<(FSoftObjectPath, FName, FTransform?)>();
        foreach (var trail in glider.GetOrDefault("TrailEffectDefinitions", Array.Empty<FStructFallback>()))
        {
            var system = trail.GetOrDefault<FSoftObjectPath>("NiagaraSystem");
            if (Named(system.AssetPathName)) trails.Add((system, trail.GetOrDefault<FName>("EffectSocket"), trail.GetOrDefault<FTransform?>("Offset")));
        }
        if (trails.Count == 0)
            foreach (var property in new[] { "TrailEffectNiagara", "TrailEffectNiagara2" })
            {
                var system = glider.GetOrDefault<FSoftObjectPath>(property);
                if (Named(system.AssetPathName) && trails.All(t => t.Item1.AssetPathName != system.AssetPathName)) trails.Add((system, default, null));
            }
        return trails;
    }

    /// <summary>
    /// A weapon actor class's Niagara components, its own and its parents': (component, socket on the weapon, whether it plays by
    /// itself rather than on a game event).
    /// </summary>
    public static List<(UObject Component, string? Socket, bool Auto)> WeaponComponents(UObject? actorClass)
    {
        var found = new List<(UObject, string?, bool)>();
        for (var guard = 0; actorClass is UBlueprintGeneratedClass && guard < 6; guard++)
        {
            var exports = actorClass.Owner?.GetExports().ToList() ?? [];
            foreach (var component in exports.Where(e => e.ExportType == "NiagaraComponent"))
            {
                if (!Named(component.GetOrDefault<FPackageIndex?>("Asset")?.Name is { } asset ? new FName(asset) : default)) continue;
                if (found.Any(f => f.Item1.Name == component.Name)) continue;
                // the construction script node that owns the component says what it is attached to
                var node = exports.FirstOrDefault(e => e.ExportType == "SCS_Node" && e.GetOrDefault<FPackageIndex?>("ComponentTemplate")?.Name == component.Name);
                var socket = node?.GetOrDefault<FName>("AttachToName") ?? default;
                found.Add((component, Named(socket) ? socket.Text : null, component.GetOrDefault("bAutoActivate", true)));
            }
            actorClass = (actorClass as UBlueprintGeneratedClass)?.SuperStruct?.Load<UObject>();
        }
        return found;
    }

    /// <summary>A sprite definition's DataList entry for its own effect (beside its SkeletalMesh and Material).</summary>
    public const string SpriteEffect = "NiagaraSystem";

    /// <summary>The bone a sprite's effect is on (BP_Weapon_Extractable attaches its ExtractableFX there).</summary>
    public const string SpriteEffectBone = "spine_4_bind";

    /// <summary>
    /// Which own effects an item of a tab can be exported with ("trail", "swing", "idle", "event effects"): a pickaxe's weapon
    /// definition's, a back bling's or outfit part's idle effects, a glider's trails, a weapon actor class's Niagara components.
    /// </summary>
    public static List<string> OwnEffectNames(UObject item, EExportType type)
    {
        switch (type)
        {
            case EExportType.Pickaxe:
                return item.GetOrDefault<UObject?>("WeaponDefinition") is { } weapon ? PickaxeEffectNames(weapon) : [];
            case EExportType.Backpack or EExportType.Outfit:
                return BaseParts(item, type).Any(p => Shown(p.GetOrDefault<FSoftObjectPath>(PartEffect)) is not null) || StyleSystems(item).Count > 0 ? ["idle"] : [];
            case EExportType.Glider:
                return GliderTrails(item).Any(t => Shown(t.System) is not null) ? ["trail"] : [];
            case EExportType.Sprite:
                return Shown(item.GetDataListItem<FSoftObjectPath>(SpriteEffect)) is not null ? ["idle"] : [];
            case EExportType.Item:
                var components = WeaponComponents(item.GetOrDefault<UObject?>("WeaponActorClass") ?? item.GetDataListItem<UObject?>("WeaponActorClass"));
                var names = new List<string>();
                if (components.Any(c => c.Auto)) names.Add("idle");
                if (components.Any(c => !c.Auto)) names.Add("event effects");
                return names;
            default:
                return [];
        }
    }

    /// <summary>The systems behind OwnEffectNames.</summary>
    public static List<UObject> OwnSystems(UObject item, EExportType type)
    {
        IEnumerable<UObject?> systems = type switch
        {
            EExportType.Pickaxe when item.GetOrDefault<UObject?>("WeaponDefinition") is { } weapon =>
                PickaxeEffects.Select(e => Shown(weapon.GetDataListItem<FSoftObjectPath>(e.Property)))
                    .Concat(PickaxeImpacts(weapon).Select(i => Shown(i.System))),
            EExportType.Backpack or EExportType.Outfit =>
                BaseParts(item, type).Select(p => Shown(p.GetOrDefault<FSoftObjectPath>(PartEffect))).Concat(StyleSystems(item).Select(s => s.System)),
            EExportType.Glider => GliderTrails(item).Select(t => Shown(t.System)),
            EExportType.Sprite => [Shown(item.GetDataListItem<FSoftObjectPath>(SpriteEffect))],
            EExportType.Item => WeaponComponents(item.GetOrDefault<UObject?>("WeaponActorClass") ?? item.GetDataListItem<UObject?>("WeaponActorClass"))
                .Select(c => c.Component.GetOrDefault<UObject?>("Asset")),
            _ => []
        };
        return systems.OfType<UObject>().DistinctBy(s => s.GetPathName()).ToList();
    }

    /// <summary>Which styles bring a back bling's or outfit's effects when its base parts have none (Haze's: Tough Luck Haze).</summary>
    public static string? StyleOnlyNote(UObject item, EExportType type)
    {
        if (type is not (EExportType.Backpack or EExportType.Outfit)) return null;
        if (BaseParts(item, type).Any(p => Shown(p.GetOrDefault<FSoftObjectPath>(PartEffect)) is not null)) return null;
        var found = StyleSystems(item);
        if (found.Any(s => s.First)) return null;
        var styles = found.Select(s => s.Style).Where(s => s.Length > 0).Distinct().ToList();
        return styles.Count == 0 ? null : $"Only with the {string.Join(", ", styles)} style{(styles.Count == 1 ? "" : "s")}.";
    }

    /// <summary>What the export can't replay: GPU emitters have no script, so their particles come out still (niagara_gpu).</summary>
    public static string? GpuNote(List<UObject> systems)
    {
        var emitters = systems.SelectMany(Emitters).ToList();
        var gpu = emitters.Count(e => e.Sim == "GPU");
        if (gpu == 0) return null;
        return gpu == emitters.Count
            ? "GPU-driven: particles come out still, without motion."
            : $"{gpu} of {emitters.Count} emitters GPU-driven: those come out still.";
    }

    /// <summary>Whether a name is set and isn't None.</summary>
    public static bool Named(FName name) => name.Text is { Length: > 0 } text && text != "None";

    /// <summary>An enabled emitter: its name, how it is simulated (CPU, GPU, Stateless) and its enabled renderers.</summary>
    public sealed record Emitter(string Name, string Sim, UObject Asset, FStructFallback? Version, List<UObject> Renderers);

    public static List<Emitter> Emitters(UObject system)
    {
        var emitters = new List<Emitter>();
        foreach (var handle in system.GetOrDefault("EmitterHandles", Array.Empty<FStructFallback>()))
        {
            if (!handle.GetOrDefault("bIsEnabled", true)) continue;
            var name = handle.GetOrDefault<FName>("Name").Text;
            if (handle.GetOrDefault<UObject?>("StatelessEmitter") is { } stateless)
            {
                emitters.Add(new Emitter(name, "Stateless", stateless, null, Enabled(stateless.GetOrDefault("RendererProperties", Array.Empty<UObject>()))));
                continue;
            }
            if (handle.GetOrDefault<FStructFallback?>("VersionedInstance") is not { } instance
                || instance.GetOrDefault<UObject?>("Emitter") is not { } emitter) continue;
            var versions = emitter.GetOrDefault("VersionData", Array.Empty<FStructFallback>());
            if (versions.Length == 0) continue;
            // the handle's version of the emitter, else its only one
            var wanted = instance.GetOrDefault<FGuid>("Version");
            var version = versions.FirstOrDefault(v => v.GetOrDefault<FStructFallback?>("Version")?.GetOrDefault<FGuid>("VersionGuid") == wanted) ?? versions[0];
            var sim = version.GetOrDefault<FName>("SimTarget").Text.Contains("GPU") ? "GPU" : "CPU";
            emitters.Add(new Emitter(name, sim, emitter, version, Enabled(version.GetOrDefault("RendererProperties", Array.Empty<UObject>()))));
        }
        return emitters;
    }

    /// <summary>
    /// The texture a system curve becomes where it is exposed to materials (a renderer binds a material texture parameter to it; e.g.
    /// Voyager Unleashed's head flames take their colours from the system's colour curve, not the material's ramp), by the parameter
    /// the system's scripts know the curve by (System.X, Emitter.X, User.X). The cooked texture is written to
    /// <see cref="FigureRecipe.GeneratedDir"/>, named for its content; returns its path, or null if the variable names no such curve.
    /// </summary>
    public static string? ExposedCurve(UObject system, string variable)
    {
        if (FigureRecipe.GeneratedDir is not { } dir) return null;
        var map = system.Properties.FirstOrDefault(p => p.Name.Text == "ScriptRuntimeCookedDataMap")?.Tag?.GenericValue;
        if (map is null) return null;
        var serializer = JsonSerializer.Create(new JsonSerializerSettings { ReferenceLoopHandling = ReferenceLoopHandling.Ignore });
        var reference = JToken.FromObject(map, serializer).SelectTokens("$..ResolvedDataInterfaces[*]")
            .Where(d => (string?)d["ResolvedVariable"]?["Name"] == variable || (string?)d["ParameterStoreVariable"]?["Name"] == variable)
            .Select(d => (string?)d["ResolvedDataInterface"]?["ObjectName"])
            .FirstOrDefault(n => n is not null && n.Contains('\''));
        if (reference is null) return null;
        // NiagaraDataInterfaceColorCurve'NS_X:SystemSpawnScript.NiagaraDataInterfaceColorCurve_0': its name and its outer's
        var inner = reference.Split('\'')[1].Split(':')[^1].Split('.');
        var curve = system.Owner!.GetExports().FirstOrDefault(e => e.Name == inner[^1] && (inner.Length < 2 || e.Outer?.Name.Text == inner[^2]));
        if (curve is null || !curve.GetOrDefault("bExposeCurve", false)
            || curve.GetOrDefault<global::CUE4Parse.UE4.Assets.Exports.Texture.UTexture?>("ExposedTexture") is not { } texture
            || global::CUE4Parse_Conversion.Textures.TextureDecoder.Decode(texture, global::CUE4Parse.UE4.Assets.Exports.Texture.ETexturePlatform.DesktopMobile) is not { } decoded)
            return null;
        var data = global::CUE4Parse_Conversion.Textures.TextureEncoder.Encode(decoded,
            global::CUE4Parse_Conversion.Options.ETextureFormat.Png, true, out var ext);
        // linear: the curve's own values (HDR where they exceed 1)
        var name = "Curve_" + Convert.ToHexString(System.Security.Cryptography.SHA1.HashData(data))[..12] + "_Lin";
        var file = System.IO.Path.Combine(dir, name + "." + ext.Trim('.').ToLowerInvariant());
        if (!System.IO.File.Exists(file)) System.IO.File.WriteAllBytes(file, data);
        return FigureRecipe.GeneratedRoot + name + "." + name;
    }

    /// <summary>
    /// A user parameter's object (User.X: texture, material, mesh) as the system's user store holds it, i.e. what the effect has
    /// until the game sets another (Renzo's hair layers are materials a mesh renderer takes from user parameters); null if none.
    /// </summary>
    public static UObject? UserObject(UObject system, string variable)
    {
        if (system.GetOrDefault<FStructFallback?>("ExposedParameters") is not { } store) return null;
        var serializer = JsonSerializer.Create(new JsonSerializerSettings { ReferenceLoopHandling = ReferenceLoopHandling.Ignore });
        var offsets = JToken.FromObject(store, serializer)["SortedParameterOffsets"] as JArray ?? [];
        var entry = offsets.FirstOrDefault(e => string.Equals((string?)e["Name"], variable, StringComparison.OrdinalIgnoreCase));
        var kind = (string?)entry?["TypeDef"]?["ClassStructOrEnum"]?["ObjectName"] ?? "";
        if (entry is null || (int?)entry["TypeDef"]?["UnderlyingType"] != 1 || kind.Contains("NiagaraDataInterface")) return null;
        var objects = store.GetOrDefault("UObjects", Array.Empty<FPackageIndex>());
        var at = (int?)entry["Offset"] ?? -1;
        return at >= 0 && at < objects.Length ? objects[at].Load() : null;
    }

    /// <summary>The user parameter a renderer's property binds (MaterialUserParamBinding, an override's UserParamBinding), or null.</summary>
    public static string? UserBinding(object? binding)
    {
        if (binding is null) return null;
        var serializer = JsonSerializer.Create(new JsonSerializerSettings { ReferenceLoopHandling = ReferenceLoopHandling.Ignore });
        var name = (string?)JToken.FromObject(binding, serializer)["Parameter"]?["Name"];
        return name is { Length: > 0 } && name != "None" ? name : null;
    }

    // compiled data only the engine's own VM and the editor read
    private static readonly HashSet<string> Unread =
        ["ExperimentalContextData", "StatScopes", "CompileTags", "ShaderScriptParametersMetadata", "SimulationStageMetaData"];

    /// <summary>
    /// What a replay of the system is made from: its package's exports (name, type, outer, properties) in package order, which holds
    /// each CPU emitter's compiled scripts, parameters and curves. The plugin runs them (material_porter/niagara).
    /// </summary>
    public static JArray Program(UObject system)
    {
        var serializer = JsonSerializer.Create(new JsonSerializerSettings { ReferenceLoopHandling = ReferenceLoopHandling.Ignore });
        var exports = new JArray();
        foreach (var export in system.Owner!.GetExports())
        {
            var props = new JObject();
            foreach (var p in export.Properties)
                if (p.Tag?.GenericValue is { } value && !props.ContainsKey(p.Name.Text))
                    props[p.Name.Text] = JToken.FromObject(value, serializer);
            Prune(props);
            if (export.ExportType == "NiagaraMeshRendererProperties" && MeshBounds(export) is { } bounds) props["MPBounds"] = bounds;
            exports.Add(new JObject { ["name"] = export.Name, ["type"] = export.ExportType, ["outer"] = export.Outer?.Name.Text, ["props"] = props });
        }
        // the parameter collections its scripts read (time of day, wind): each one's values from its default instance's store
        // (a script's cooked store only holds placeholders)
        var collections = new Dictionary<string, UObject>();
        foreach (var export in system.Owner!.GetExports())
            if (export.ExportType == "NiagaraScript")
                foreach (var collection in export.GetOrDefault("CachedParameterCollectionReferences", Array.Empty<UObject>()))
                    collections.TryAdd(collection.GetPathName(), collection);
        foreach (var collection in collections.Values)
        {
            try
            {
                if (collection.GetOrDefault<UObject?>("DefaultInstance") is not { } instance) continue;
                var props = new JObject();
                foreach (var p in instance.Properties)
                    if (p.Tag?.GenericValue is { } value && !props.ContainsKey(p.Name.Text))
                        props[p.Name.Text] = JToken.FromObject(value, serializer);
                exports.Add(new JObject { ["name"] = collection.Name, ["type"] = "MPCollection", ["outer"] = null, ["props"] = props });
            }
            catch (Exception e)
            {
                Serilog.Log.Warning("[Material Porter] {System}: the parameter collection {Collection} wasn't read ({Error})", system.Name, collection.Name, e.Message);
            }
        }
        // the user-defined structs its data sets hold (a Fortnite module's bone data): how many floats and ints each is laid out as,
        // which the asset itself doesn't say
        var structs = new Dictionary<string, FPackageIndex>();
        foreach (var export in system.Owner!.GetExports())
            foreach (var p in export.Properties)
                FindStructs(p.Tag?.GenericValue, structs, 0);
        foreach (var (name, index) in structs)
        {
            try
            {
                if (index.Load<UStruct>() is not { } type) continue;
                var (floats, ints) = Components(type, 0);
                exports.Add(new JObject { ["name"] = name, ["type"] = "MPStruct", ["outer"] = null, ["props"] = new JObject { ["Floats"] = floats, ["Ints"] = ints } });
            }
            catch (Exception e)
            {
                Serilog.Log.Warning("[Material Porter] {System}: the struct {Struct} wasn't read ({Error})", system.Name, name, e.Message);
            }
        }
        return exports;
    }

    /// <summary>
    /// A mesh renderer's mesh bounds, each with the renderer's scale (what a script's GetMeshLocalBounds reads): min then max, six
    /// numbers a mesh.
    /// </summary>
    private static JArray? MeshBounds(UObject renderer)
    {
        try
        {
            var all = new JArray();
            foreach (var entry in renderer.GetOrDefault("Meshes", Array.Empty<FStructFallback>()))
            {
                var scale = entry.GetOrDefault("Scale", FVector.OneVector);
                var box = entry.GetOrDefault<global::CUE4Parse.UE4.Assets.Exports.StaticMesh.UStaticMesh?>("Mesh")?.RenderData?.Bounds;
                var (min, max) = box is { } b ? (b.Origin - b.BoxExtent, b.Origin + b.BoxExtent) : (FVector.ZeroVector, FVector.ZeroVector);
                all.Add(new JArray(min.X * scale.X, min.Y * scale.Y, min.Z * scale.Z, max.X * scale.X, max.Y * scale.Y, max.Z * scale.Z));
            }
            return all.Count > 0 ? all : null;
        }
        catch (Exception e)
        {
            Failures.Note("effect mesh bounds", renderer.Name, e);
            return null;
        }
    }

    /// <summary>The user-defined structs named by type definitions (FNiagaraTypeDefinition.ClassStructOrEnum) under a value.</summary>
    private static void FindStructs(object? value, Dictionary<string, FPackageIndex> found, int depth)
    {
        if (depth > 12) return;
        switch (value)
        {
            case FStructFallback fallback:
                foreach (var p in fallback.Properties)
                {
                    if (p.Name.Text == "ClassStructOrEnum" && p.Tag?.GenericValue is FPackageIndex { IsNull: false } index)
                    {
                        if (index.ResolvedObject?.Class?.Name.Text == "UserDefinedStruct") found.TryAdd(index.Name, index);
                        continue;
                    }
                    FindStructs(p.Tag?.GenericValue, found, depth + 1);
                }
                break;
            case FScriptStruct { StructType: FStructFallback inner }:
                FindStructs(inner, found, depth + 1);
                break;
            case FScriptStruct { StructType: global::CUE4Parse.UE4.Objects.Niagara.FNiagaraVariableBase variable }:
                FindStructs(variable.TypeDef, found, depth + 1); // a data set's variable: name and type
                break;
            case UScriptArray array:
                // arrays of numbers (bytecode, parameter data) hold no type definition
                if (array.InnerType is "StructProperty")
                    foreach (var item in array.Properties)
                        FindStructs(item.GenericValue, found, depth + 1);
                break;
        }
    }

    /// <summary>A struct as a Niagara data set lays it out: its fields' floats, then ints (a vector is three floats, as Niagara narrows it).</summary>
    private static (int Floats, int Ints) Components(UStruct type, int depth)
    {
        int floats = 0, ints = 0;
        foreach (var field in type.ChildProperties ?? [])
        {
            switch (field)
            {
                case FStructProperty inner:
                    var known = inner.Struct.Name switch
                    {
                        "Vector" or "Vector3f" or "Vector3d" or "Rotator" => 3,
                        "Vector2D" or "Vector2f" => 2,
                        "Vector4" or "Vector4f" or "Quat" or "Quat4f" or "LinearColor" => 4,
                        _ => 0,
                    };
                    if (known > 0) floats += known;
                    else if (depth < 4 && inner.Struct.Load<UStruct>() is { } nested)
                    {
                        var (f, i) = Components(nested, depth + 1);
                        floats += f;
                        ints += i;
                    }
                    break;
                case FFloatProperty or FDoubleProperty:
                    floats++;
                    break;
                case FBoolProperty or FEnumProperty or FNumericProperty:
                    ints++;
                    break;
            }
        }
        return (floats, ints);
    }

    /// <summary>
    /// The vector fields the system's scripts sample (a vector field data interface's Field), by package: grid size, bounds and
    /// vectors (four half floats a cell, as the asset keeps them).
    /// </summary>
    public static JObject Fields(UObject system)
    {
        var fields = new JObject();
        foreach (var export in system.Owner!.GetExports())
        {
            if (export.ExportType != "NiagaraDataInterfaceVectorField") continue;
            try
            {
                if (export.GetOrDefault<UVectorFieldStatic?>("Field") is not { } field || field.Owner is not { } package) continue;
                if (fields.ContainsKey(package.Name) || field.SourceData?.Data is not { Length: > 0 } data) continue;
                var bounds = field.GetOrDefault<FBox>("Bounds");
                fields[package.Name] = new JObject
                {
                    ["Size"] = new JArray(field.GetOrDefault<int>("SizeX"), field.GetOrDefault<int>("SizeY"), field.GetOrDefault<int>("SizeZ")),
                    ["Min"] = new JArray(bounds.Min.X, bounds.Min.Y, bounds.Min.Z),
                    ["Max"] = new JArray(bounds.Max.X, bounds.Max.Y, bounds.Max.Z),
                    ["Data"] = Convert.ToBase64String(data),
                };
            }
            catch (Exception e)
            {
                Serilog.Log.Warning("[Material Porter] {System}: {Field}'s vector field wasn't read ({Error})", system.Name, export.Name, e.Message);
            }
        }
        return fields;
    }

    private static void Prune(JToken token)
    {
        if (token is JObject o)
        {
            foreach (var name in o.Properties().Select(p => p.Name).Where(Unread.Contains).ToList()) o.Remove(name);
            foreach (var p in o.Properties()) Prune(p.Value);
        }
        else if (token is JArray a && a.Count > 0 && a[0] is JContainer)
        {
            foreach (var item in a) Prune(item);
        }
    }

    private static List<UObject> Enabled(IEnumerable<UObject?> renderers) =>
        renderers.OfType<UObject>().Where(r => r.GetOrDefault("bIsEnabled", true)).ToList();

    private static string DrawsOf(string rendererClass) => rendererClass switch
    {
        "NiagaraMeshRendererProperties" => "mesh",
        "NiagaraSpriteRendererProperties" => "sprite",
        "NiagaraRibbonRendererProperties" => "ribbon",
        "NiagaraLightRendererProperties" => "light",
        var other => other.Replace("Niagara", "").Replace("RendererProperties", "").ToLowerInvariant()
    };

    /// <summary>A system in a line: "3 emitters: Rays (mesh, CPU), Flare (sprite, stateless), Sparks (sprite, GPU)".</summary>
    public static string Describe(UObject system)
    {
        // one listed unread: what its package's export map tells, and where it is
        if (Unloaded.Is(system))
        {
            var path = system.GetPathName();
            var folder = path[..Math.Max(0, path.LastIndexOf('/'))];
            return Unloaded.Detail(system) is Outline outline ? outline.Describe(folder) : folder;
        }
        try
        {
            var emitters = Emitters(system);
            if (emitters.Count == 0) return "No emitters.";
            var parts = emitters.Select(e =>
            {
                var draws = e.Renderers.Select(r => DrawsOf(r.ExportType)).Distinct().ToList();
                return $"{e.Name} ({(draws.Count == 0 ? "draws nothing" : string.Join(" + ", draws))}, {(e.Sim == "Stateless" ? "stateless" : e.Sim)})";
            });
            return $"{emitters.Count} emitter{(emitters.Count == 1 ? "" : "s")}: {string.Join(", ", parts)}";
        }
        catch (Exception e)
        {
            Failures.Note("effect descriptions", system.Name, e);
            return "";
        }
    }
}
