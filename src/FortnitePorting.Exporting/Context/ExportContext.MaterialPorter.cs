using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Numerics;
using CUE4Parse.FileProvider;
using CUE4Parse.UE4.Assets.Exports;
using CUE4Parse.UE4.Assets.Exports.Actor;
using CUE4Parse.UE4.Assets.Exports.Component.Landscape;
using CUE4Parse.UE4.Assets.Exports.Material;
using CUE4Parse.UE4.Assets.Exports.SkeletalMesh;
using CUE4Parse.UE4.Assets.Exports.StaticMesh;
using CUE4Parse.UE4.Objects.Core.Math;
using CUE4Parse.UE4.Objects.Engine;
using CUE4Parse.UE4.Assets.Objects;
using CUE4Parse.UE4.Objects.UObject;
using FortnitePorting.CUE4Parse.Extensions;
using FortnitePorting.CUE4Parse.Models.Fortnite.Enums;
using FortnitePorting.Exporting.MaterialPorter;
using FortnitePorting.Exporting.Models;
using FortnitePorting.Shared.Extensions;
using Serilog;

namespace FortnitePorting.Exporting.Context;

/// <summary>
/// Material Porter fork: a level read by Material Porter's map reader. Each
/// actor's components come from the level's own exports over their class's
/// templates: attachments, overrides, dynamic material instances, texture
/// data (textures and tints), custom primitive and per-instance data,
/// level instances, spline meshes (bent in Blender), water bodies; HLODs,
/// devices, hidden actors, ziplines and meshes drawn only into the terrain's
/// virtual texture are left out. Landscapes and FP's HLOD export still go
/// through FP's own actor export. A Rocket Racing track whose level didn't
/// save its road pieces gets them laid along its spline (DelMarTracks).
/// </summary>
public partial class ExportContext
{
    /// <summary>Tests: only actors whose name holds this (null: all).</summary>
    public static string? MaterialPorterActorFilter;

    private readonly Dictionary<string, ExportMesh?> _mpMeshes = new(StringComparer.OrdinalIgnoreCase);

    /// <summary>The point, spot and rect lights the levels read so far: a world export puts them in its Lights (MeshExport).</summary>
    public readonly List<ExportLight> MaterialPorterLights = [];

    /// <summary>The decals the levels read so far: a world export puts them in its Decals (MeshExport).</summary>
    public readonly List<ExportDecal> MaterialPorterDecals = [];

    /// <summary>The particle systems the levels read so far: a world export puts them in its Effects (MeshExport).</summary>
    public readonly List<ExportEffect> MaterialPorterEffects = [];

    public List<ExportMesh>? MaterialPorterLevel(ULevel level)
    {
        if (level.Owner?.Name is not { } package) return null;

        var meshes = new List<ExportMesh>();
        // the Map page's Actors, Lights, Decals and Effects toggles: the level is read when any is on, and each
        // kind is converted (and so exported) only when its own flag is
        var actors = Meta.WorldFlags.HasFlag(EWorldFlags.Actors);
        var withLights = Meta.WorldFlags.HasFlag(EWorldFlags.Lights);
        var withDecals = Meta.WorldFlags.HasFlag(EWorldFlags.Decals);
        var withEffects = Meta.WorldFlags.HasFlag(EWorldFlags.Effects);
        if (actors || withLights || withDecals || withEffects)
        {
            var options = new MapOptions
            {
                Instances = actors && Meta.WorldFlags.HasFlag(EWorldFlags.InstancedFoliage),
                Landscape = false,
            };
            var scan = new MapScan { Name = package, Key = package };
            var reader = new MapReader(new MapGame { Provider = FileProvider }, options, scan);
            // a UEFN island's cells aren't in its runtime hash (FP never visits them): read them with it
            var levels = new List<string> { package };
            levels.AddRange(IslandCells(level, package));
            try
            {
                foreach (var read in levels)
                {
                    CancellationToken.ThrowIfCancellationRequested();
                    reader.LevelAsync(read, Matrix4x4.Identity, 0, CancellationToken).GetAwaiter().GetResult();
                }
            }
            catch (OperationCanceledException)
            {
                return meshes;
            }

            // Rocket Racing tracks: the road pieces of the tracks whose level didn't save them (DelMarTracks)
            var tracks = new List<MapMesh>();
            foreach (var read in levels)
            {
                if (!actors || CancellationToken.IsCancellationRequested) break;
                tracks.AddRange(DelMarTracks.Place(FileProvider, read, (what, n) => scan.Skip(what, n)));
            }
            if (actors && DelMarTracks.TestTrack is { } test)
            {
                // (once: a world's streamed levels come through here too)
                DelMarTracks.TestTrack = null;
                if (LoadMaterialPorterObject(test.Actor) is { } testActor) tracks.AddRange(DelMarTracks.Lay(FileProvider, testActor, package, test.Points));
            }
            if (tracks.Count > 0)
                Log.Information("[Material Porter] {Level}: {Pieces} Rocket Racing road pieces laid along {Tracks} tracks", package, tracks.Count,
                    tracks.Select(t => t.Actor).Distinct().Count());

            var placed = (actors ? reader.Placed.Concat(tracks) : [])
                .Where(m => MaterialPorterActorFilter is null || m.Actor.Contains(MaterialPorterActorFilter, StringComparison.OrdinalIgnoreCase))
                .OrderBy(m => m.Actor, StringComparer.Ordinal).ThenBy(m => m.Mesh, StringComparer.Ordinal).ToList();
            var done = 0;
            foreach (var m in placed)
            {
                if (CancellationToken.IsCancellationRequested) break;
                if (++done % 200 == 0) Meta.OnUpdateProgress(m.Actor, done, placed.Count);
                meshes.AddIfNotNull(Placement(m));
            }
            var lights = reader.Lights.Where(l => MaterialPorterActorFilter is null || l.Actor.Contains(MaterialPorterActorFilter, StringComparison.OrdinalIgnoreCase))
                .OrderBy(l => l.Actor, StringComparer.Ordinal).ThenBy(l => l.Name, StringComparer.Ordinal)
                .ThenBy(l => l.World.M41).ThenBy(l => l.World.M42).ThenBy(l => l.World.M43);
            if (withLights)
                foreach (var l in lights) MaterialPorterLights.Add(Light(l));

            var decals = reader.Decals.Where(d => MaterialPorterActorFilter is null || d.Actor.Contains(MaterialPorterActorFilter, StringComparison.OrdinalIgnoreCase))
                .OrderBy(d => d.Actor, StringComparer.Ordinal).ThenBy(d => d.Name, StringComparer.Ordinal)
                .ThenBy(d => d.World.M41).ThenBy(d => d.World.M42).ThenBy(d => d.World.M43);
            if (withDecals)
                foreach (var d in decals)
                {
                    if (CancellationToken.IsCancellationRequested) break;
                    MaterialPorterDecals.AddIfNotNull(Decal(d));
                }
            var effects = reader.Effects.Where(e => MaterialPorterActorFilter is null || e.Actor.Contains(MaterialPorterActorFilter, StringComparison.OrdinalIgnoreCase))
                .OrderBy(e => e.Actor, StringComparer.Ordinal).ThenBy(e => e.Name, StringComparer.Ordinal)
                .ThenBy(e => e.World.M41).ThenBy(e => e.World.M42).ThenBy(e => e.World.M43);
            if (withEffects)
                foreach (var e in effects) MaterialPorterEffects.Add(Effect(e));
            Log.Information("[Material Porter] {Level}: {Count} placements ({Unshadowed} casting no shadow), {Lights} lights, {Decals} decals, {Effects} effects, skipped {Skipped}",
                package, meshes.Count, meshes.Count(m => m is MaterialPorterMesh { MPCastShadow: false }),
                withLights ? reader.Lights.Count : 0, withDecals ? reader.Decals.Count : 0, withEffects ? reader.Effects.Count : 0,
                string.Join(", ", scan.Skipped.Select(kv => $"{kv.Value} {kv.Key}")));
        }

        // the terrain and FP's HLODs, as FP exports them
        foreach (var actorLazy in level.Actors)
        {
            if (CancellationToken.IsCancellationRequested) break;
            if (actorLazy is null || actorLazy.IsNull) continue;
            var type = actorLazy.ResolvedObject?.Class?.Name.Text ?? string.Empty;
            if (!type.Contains("Landscape", StringComparison.Ordinal) && type != "FortMainHLOD") continue;
            if (actorLazy.Load() is not { } actor) continue;
            if (MaterialPorterActorFilter is { } only && !actor.Name.Contains(only, StringComparison.OrdinalIgnoreCase)) continue;
            if (actor is ALandscapeProxy proxy)
            {
                // FP names a weight layer after its LayerInfo asset; the exact materials ask by LayerName
                var names = LandscapeLayerNames(proxy);
                foreach (var landscape in Actor(actor, loadTemplate: false).Where(x => x is not null))
                {
                    var export = new MaterialPorterMesh
                    {
                        Name = landscape.Name, Path = landscape.Path, NumLods = landscape.NumLods, IsEmpty = landscape.IsEmpty,
                        Location = landscape.Location, Rotation = landscape.Rotation, Scale = landscape.Scale,
                        MPLayerNames = names,
                    };
                    export.Materials.AddRange(landscape.Materials);
                    export.OverrideMaterials.AddRange(landscape.OverrideMaterials);
                    meshes.Add(export);
                }
            }
            else if (actor.ExportType == "FortMainHLOD")
                meshes.AddRange(Actor(actor, loadTemplate: false).Where(x => x is not null));
        }

        return meshes;
    }

    /// <summary>
    /// The _Generated_ cell levels beside a World Partition map whose runtime hash
    /// lists none (UEFN islands); none for a cell, a classic map, or a map FP walks.
    /// </summary>
    private List<string> IslandCells(ULevel level, string package)
    {
        if (package.Contains("/_Generated_/", StringComparison.OrdinalIgnoreCase)) return [];
        if (level.GetOrDefault<UObject>("WorldSettings") is not { } settings
            || settings.GetOrDefault<UObject>("WorldPartition") is not { } partition
            || partition.GetOrDefault<UObject>("RuntimeHash") is not { } hash) return [];
        foreach (var data in hash.GetOrDefault("RuntimeStreamingData", Array.Empty<FStructFallback>()))
            if (data.GetOrDefault("SpatiallyLoadedCells", Array.Empty<FPackageIndex>()).Length
                + data.GetOrDefault("NonSpatiallyLoadedCells", Array.Empty<FPackageIndex>()).Length > 0) return [];
        foreach (var grid in hash.GetOrDefault("StreamingGrids", Array.Empty<FStructFallback>()))
        foreach (var gridLevel in grid.GetOrDefault("GridLevels", Array.Empty<FStructFallback>()))
        foreach (var layerCell in gridLevel.GetOrDefault("LayerCells", Array.Empty<FStructFallback>()))
            if (layerCell.GetOrDefault("GridCells", Array.Empty<UObject>()).Length > 0) return [];

        string key;
        try { key = FileProvider.FixPath(package); }
        catch (Exception e) { Log.Warning("[Material Porter] {Package}: island cells not read: {Error}", package, e.Message); return []; }
        var dot = key.LastIndexOf('.');
        if (dot > key.LastIndexOf('/')) key = key[..dot];
        var prefix = key + "/_Generated_/";
        var cells = FileProvider.Files.Keys
            .Where(k => k.StartsWith(prefix, StringComparison.OrdinalIgnoreCase) && k.EndsWith(".umap", StringComparison.OrdinalIgnoreCase))
            .OrderBy(k => k, StringComparer.OrdinalIgnoreCase).ToList();
        Log.Information("[Material Porter] {Map}: {Count} island cells beside it", package, cells.Count);
        return cells;
    }

    /// <summary>A landscape's weight layers: LayerInfo asset name -> the LayerName materials sample.</summary>
    private static Dictionary<string, string> LandscapeLayerNames(ALandscapeProxy proxy)
    {
        var names = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        foreach (var componentLazy in proxy.LandscapeComponents ?? [])
        {
            ULandscapeComponent? component;
            try { component = componentLazy.Load<ULandscapeComponent>(); }
            catch (Exception e) { MaterialPorter.Failures.Note("landscape components", componentLazy.Name, e); continue; }
            if (component is null) continue;
            foreach (var allocation in component.GetWeightmapLayerAllocations() ?? [])
            {
                var asset = allocation.GetLayerName();
                if (asset is null || names.ContainsKey(asset)) continue;
                try
                {
                    // the property, read as tagged (this CUE4Parse's LayerName field stays None)
                    var layer = allocation.LayerInfo.Load()?.GetOrDefault<FName>("LayerName") ?? default;
                    if (!layer.IsNone && !string.IsNullOrEmpty(layer.Text)) names[asset] = layer.Text;
                }
                catch (Exception e)
                {
                    MaterialPorter.Failures.Note("landscape layer infos", asset, e);
                    // an unreadable layer info keeps FP's name
                }
            }
        }
        return names;
    }

    /// <summary>
    /// One light as FP's light record: where it stands (as the meshes are), its colour (as FP reads a light's: LightColor
    /// as linear), and the values the engine read, in the units it read them in (the plugin converts).
    /// </summary>
    private static ExportLight Light(MapLight l)
    {
        ExportLight export = l.Kind switch
        {
            "Spot" => new ExportSpotLight { InnerConeAngle = l.InnerConeAngle, OuterConeAngle = l.OuterConeAngle },
            "Rect" => new ExportRectLight { SourceWidth = l.SourceWidth, SourceHeight = l.SourceHeight, BarnDoorAngle = l.BarnDoorAngle },
            _ => new ExportPointLight(),
        };
        export = export with
        {
            Name = $"{l.Name}.{l.Actor}",
            Actor = l.Actor,
            Color = l.Color.ToLinearColor(),
            Intensity = l.Intensity,
            IntensityUnits = l.Units,
            InverseSquaredFalloff = l.InverseSquared,
            FalloffExponent = l.FalloffExponent,
            UseTemperature = l.UseTemperature,
            Temperature = l.Temperature,
            AttenuationRadius = l.AttenuationRadius,
            Radius = l.SourceRadius,
            CastShadows = l.CastShadows,
        };
        SetMaterialPorterTransform(export, l.World);
        return export;
    }

    /// <summary>A light's units from the name of UE's ELightUnits value (as the map reader names them); unset is candelas.</summary>
    private static string MaterialPorterLightUnits(string? name) =>
        name is null ? "Candelas"
        : name.Contains("Lumens", StringComparison.Ordinal) ? "Lumens"
        : name.Contains("Unitless", StringComparison.Ordinal) ? "Unitless"
        : name.EndsWith("EV", StringComparison.Ordinal) ? "EV"
        : name.Contains("Nits", StringComparison.Ordinal) ? "Nits"
        : "Candelas";

    /// <summary>
    /// One decal as the world export's record: the component's world transform (as the meshes'), the projection box, and its
    /// material as a mesh slot's entry (Material over the asset, a dynamic instance's values as MPValues: SlotMaterial, as Placement
    /// gives each slot). Null when the material can't be exported.
    /// </summary>
    private ExportDecal? Decal(MapDecal d)
    {
        if (LoadMaterialPorterObject(d.Material) is not UMaterialInterface mi || Material(mi, 0) is not { } material) return null;
        var export = new ExportDecal
        {
            Name = d.Name,
            DecalSize = new FVector(d.DecalSize.X, d.DecalSize.Y, d.DecalSize.Z),
            SortOrder = d.SortOrder,
            FadeScreenSize = d.FadeScreenSize,
            Material = SlotMaterial(material, 0, d.Params ?? new ParamSet()),
        };
        SetMaterialPorterTransform(export, d.World);
        return export;
    }

    /// <summary>One particle system as the world export's record: the component's world transform and its system's path and user parameters.</summary>
    private static ExportEffect Effect(MapEffect e)
    {
        var export = new ExportEffect { Name = e.Name, System = e.System, Parameters = e.Parameters };
        SetMaterialPorterTransform(export, e.World);
        return export;
    }

    /// <summary>A slot's material as the plugin reads it: the material in its slot, a dynamic instance's or a building's values (MPValues) over it.</summary>
    private static ExportMaterial SlotMaterial(ExportMaterial material, int slot, ParamSet values)
    {
        var hasValues = values.Scalars.Count + values.Vectors.Count + values.Textures.Count > 0;
        if (!hasValues) return material with { Slot = slot };
        return new MaterialPorterMaterial(material with { Slot = slot })
        {
            MPValues = values,
            Hash = HashCode.Combine(material.Hash, values.Key()),
        };
    }

    /// <summary>One placement as FP's mesh record: the mesh (exported once), where it stands, what its slots wear.</summary>
    private ExportMesh? Placement(MapMesh m)
    {
        if (!_mpMeshes.TryGetValue(m.Mesh, out var template))
        {
            template = LoadMaterialPorterObject(m.Mesh) switch
            {
                UStaticMesh staticMesh => Mesh(staticMesh),
                USkeletalMesh skeletalMesh => Mesh(skeletalMesh),
                _ => null
            };
            _mpMeshes[m.Mesh] = template;
        }
        if (template is null) return null;

        var export = new MaterialPorterMesh
        {
            Name = template.Name,
            Path = template.Path,
            NumLods = template.NumLods,
            MPPrimitiveData = m.PrimitiveData,
            MPInstanceData = m.InstanceData,
            MPSpline = m.Spline,
            MPCastShadow = m.CastShadow ? null : false,
            MPShadowOnly = m.ShadowOnly ? true : null,
            MPActor = m.Actor,
        };
        export.Materials.AddRange(template.Materials);
        SetMaterialPorterTransform(export, m.World);

        // each slot's material: a component's override, else the mesh's own; a dynamic instance's
        // values and a building's texture data over it
        var slots = template.Materials.Select(x => x.Slot).Concat(m.Overrides.Keys).Distinct();
        foreach (var slot in slots)
        {
            ExportMaterial? material = null;
            if (m.Overrides.TryGetValue(slot, out var path) && LoadMaterialPorterObject(path) is UMaterialInterface mi)
                material = Material(mi, slot);
            var values = new ParamSet();
            if (m.Params.TryGetValue(slot, out var mine)) values.MergeFrom(mine);
            if (m.AllSlots is { } skin) values.MergeFrom(skin);
            var hasValues = values.Scalars.Count + values.Vectors.Count + values.Textures.Count > 0;
            if (material is null && !hasValues) continue;

            material ??= template.Materials.FirstOrDefault(x => x.Slot == slot);
            if (material is null) continue;
            export.OverrideMaterials.Add(SlotMaterial(material, slot, values));
        }
        return export;
    }

    /// <summary>
    /// A Rocket Racing car (Material Porter fork): its body with the tier's (or a decal's)
    /// material, the picked wheels as children on the body's wheel sockets, and the values
    /// the car's Mutable program gives each material. Picks: channel index -> option index.
    /// </summary>
    public List<ExportMesh> MaterialPorterCar(UObject body, IReadOnlyDictionary<int, int> picks)
    {
        if (Cars.Items is null || body.Owner?.Name is not { } package) return [];
        var (skins, wheels) = Cars.Items().GetAwaiter().GetResult();
        var plan = new Cars(FileProvider).PlanAsync(package, body.Name, skins, wheels, picks).GetAwaiter().GetResult();
        Log.Information("[Material Porter] car {Body}: {Styles} (options: {Channels})", body.Name, string.Join(", ", plan.Styles),
            string.Join(", ", plan.Channels.Select(c => $"{c.Name} {c.Options.Count}")));
        if (plan.BuildNotes.Count > 0) Log.Warning("[Material Porter] car {Body}: building it, Mutable couldn't follow: {Notes}", body.Name, string.Join("; ", plan.BuildNotes));
        var export = plan.BodyMesh is not null
            ? LoadMaterialPorterObject(plan.BodyMesh) is USkeletalMesh bodyMesh ? Mesh(bodyMesh) : null
            : plan.BuiltBody is { } builtBody ? BuiltCarMesh(builtBody) : null;
        if (export is null) return [];
        export.Name = body.Name;
        CarMaterials(export, plan, plan.BodyOverrides);

        var wheel = plan.WheelMesh is null ? plan.BuiltWheel is { } builtWheel ? BuiltCarMesh(builtWheel) : null : LoadMaterialPorterObject(plan.WheelMesh) switch
        {
            USkeletalMesh sk => Mesh(sk),
            UStaticMesh sm => Mesh(sm),
            _ => null
        };
        if (wheel is not null)
            foreach (var (label, transform) in plan.Wheels)
            {
                var child = new ExportMesh { Name = label, Path = wheel.Path, NumLods = wheel.NumLods };
                child.Materials.AddRange(wheel.Materials);
                CarMaterials(child, plan, null);
                SetMaterialPorterTransform(child, transform);
                export.Children.Add(child);
            }
        return [export];
    }

    /// <summary>A car part Mutable builds, written once as a UEFormat model on its skeleton, with its slots' materials.</summary>
    private ExportMesh? BuiltCarMesh(BuiltCarMesh built)
    {
        if (LoadMaterialPorterObject(built.Skeleton) is not global::CUE4Parse.UE4.Assets.Exports.Animation.USkeleton skeleton) return null;
        var path = $"/MaterialPorter/Cars/{built.Name}.{built.Name}";
        var file = BuildExportPath(path, "uemodel");
        if (!File.Exists(file)) UEModelWriter.Write(file, built.Name, built.Mesh, skeleton, built.Slots);
        var export = new ExportMesh { Name = built.Name, Path = path, NumLods = 1 };
        for (var slot = 0; slot < built.Slots.Count; slot++)
            if (built.Slots[slot].Path.Length > 0 && LoadMaterialPorterObject(built.Slots[slot].Path) is UMaterialInterface mi
                && Material(mi, slot) is { } material)
                export.Materials.Add(material with { Slot = slot });
        return export;
    }

    /// <summary>
    /// A LEGO figure: its cooked skeletal meshes (the schema's, or its Bake folder's) with
    /// their baked materials; else its recipe (FigureRecipe): the shared Mutable object's
    /// body and the recipe's cooked parts, dressed with its materials, colours and decos.
    /// </summary>
    public List<ExportMesh> MaterialPorterFigure(UObject item, IReadOnlyDictionary<string, int> face)
    {
        var meshes = Figures.BakedMeshes(FileProvider, item);
        if (meshes.Count > 0)
        {
            Log.Information("[Material Porter] figure {Item}: {Meshes}", item.Name, string.Join(", ", meshes.Select(m => m.Name)));
            // parts, the first one the body: the plugin merges them onto its armature
            var cooked = meshes.Select(m => Mesh<ExportPart>(m)).OfType<ExportPart>().ToList();
            for (var i = 0; i < cooked.Count; i++) cooked[i].Type = i == 0 ? EFortCustomPartType.Body : EFortCustomPartType.MiscOrTail;
            FigureFace(item, cooked, face);
            return cooked.Cast<ExportMesh>().ToList();
        }
        if (Figures.RecipeInstance(FileProvider, item) is not { } instance)
        {
            Log.Warning("[Material Porter] figure {Item}: no cooked mesh and no recipe: {Trace}", item.Name, Figures.Trace(FileProvider, item));
            return [];
        }
        var recipe = FigureRecipe.LoadAsync(FileProvider, instance).GetAwaiter().GetResult();
        if (recipe is null) return [];
        foreach (var (feature, pose) in face) recipe.FacePoses[feature] = pose;
        var parts = recipe.PartsAsync(FileProvider).GetAwaiter().GetResult();
        Log.Information("[Material Porter] figure {Item}: recipe {Instance}, parts {Parts}", item.Name, instance, string.Join(", ", parts.Select(p => p.Name)));

        var exports = new List<ExportMesh>();
        foreach (var part in parts)
        {
            ExportMesh? export;
            var slots = 1;
            if (part.Raw is { } raw)
            {
                // a body's geometry is the same for every recipe figure with it: one file each
                var skeleton = FigureRecipe.SkeletonAsync(FileProvider).GetAwaiter().GetResult();
                var bodyPath = $"/MaterialPorter/Figures/{part.RawName}.{part.RawName}";
                var file = BuildExportPath(bodyPath, "uemodel");
                if (!File.Exists(file)) UEModelWriter.Write(file, part.RawName, raw, skeleton, [("Body", part.Material ?? "")]);
                export = new ExportPart { Name = part.RawName, Path = bodyPath, NumLods = 1, Type = EFortCustomPartType.Body };
            }
            else
            {
                if (part.Mesh is null || LoadMaterialPorterObject(part.Mesh) is not USkeletalMesh sk || Mesh<ExportPart>(sk) is not { } cooked) continue;
                // parts: the plugin merges them onto the body's armature (the head as FP's heads)
                cooked.Type = part.Name == "Head" ? EFortCustomPartType.Head : EFortCustomPartType.MiscOrTail;
                export = cooked;
                slots = Math.Max(1, sk.Materials?.Length ?? 1);
            }
            if (part.Material is not null && LoadMaterialPorterObject(part.Material) is UMaterialInterface mi)
            {
                var values = new ParamSet { Label = part.Name };
                foreach (var (k, v) in part.Textures) values.Textures[k] = v;
                foreach (var (k, v) in part.Scalars) values.Scalars[k] = v;
                export.Materials.Clear();
                for (var slot = 0; slot < slots; slot++)
                {
                    if (Material(mi, slot) is not { } material) continue;
                    var hasValues = values.Textures.Count + values.Scalars.Count > 0;
                    export.Materials.Add(!hasValues && part.FaceRig is null
                        ? material with { Slot = slot }
                        : new MaterialPorterMaterial(material with { Slot = slot })
                        {
                            MPValues = hasValues ? values : null, MPFaceRig = part.FaceRig,
                            Hash = hasValues ? HashCode.Combine(material.Hash, values.Key()) : material.Hash,
                        });
                }
            }
            exports.Add(export);
        }
        return exports;
    }

    /// <summary>
    /// A LEGO creature: its schema's skeletal meshes as parts (the first the body, whose armature takes
    /// the others), each material with the textures the schema puts on it (its colour LUT); or its one
    /// skeletal mesh with its override materials.
    /// </summary>
    public List<ExportMesh> MaterialPorterCreature(UObject item)
    {
        var meshes = Figures.BakedMeshes(FileProvider, item);
        var single = false;
        if (meshes.Count == 0 && item.GetOrDefault<USkeletalMesh?>("SkeletalMesh") is { } mesh)
        {
            meshes = [mesh];
            single = true;
        }
        var parts = meshes.Select(m => Mesh<ExportPart>(m)).OfType<ExportPart>().ToList();
        for (var i = 0; i < parts.Count; i++) parts[i].Type = i == 0 ? EFortCustomPartType.Body : EFortCustomPartType.MiscOrTail;
        if (single && parts.Count == 1)
        {
            var overrides = item.GetOrDefault("OverrideMaterials", Array.Empty<FStructFallback>());
            for (var slot = 0; slot < overrides.Length; slot++)
                if (overrides[slot].GetOrDefault<UMaterialInterface?>("Material") is { } mi && Material(mi, slot) is { } material)
                    parts[0].OverrideMaterials.Add(material with { Slot = slot });
        }
        var textures = Figures.SchemaTextures(FileProvider, item);
        Log.Information("[Material Porter] creature {Item}: {Meshes}, textures {Textures}", item.Name,
            string.Join(", ", parts.Select(p => p.Name)), string.Join(", ", textures.Select(kv => $"{kv.Key}={kv.Value}")));
        if (textures.Count == 0) return parts.Cast<ExportMesh>().ToList();
        var values = new ParamSet { Label = item.Name };
        foreach (var (param, texture) in textures) values.Textures[param] = texture;
        foreach (var part in parts)
            for (var i = 0; i < part.Materials.Count; i++)
                part.Materials[i] = new MaterialPorterMaterial(part.Materials[i])
                {
                    MPValues = values, Hash = HashCode.Combine(part.Materials[i].Hash, values.Key()),
                };
        return parts.Cast<ExportMesh>().ToList();
    }

    /// <summary>
    /// A cooked figure's face (its instance of M_Figure_RigDrivenFace): the expression picked (its poses,
    /// its character accents moved where the face rig puts them for the mouth picked), and where the
    /// rig puts those accents for every mouth pose, for an emote that animates the face.
    /// </summary>
    private void FigureFace(UObject item, List<ExportPart> parts, IReadOnlyDictionary<string, int> face)
    {
        var schema = Figures.Schema(item, FileProvider)?.GetPathName();
        string? rig = null;
        var rigRead = false;
        foreach (var part in parts)
            for (var i = 0; i < part.Materials.Count; i++)
            {
                var material = part.Materials[i];
                if (!material.BaseMaterialPath.Contains("RigDrivenFace", StringComparison.OrdinalIgnoreCase)) continue;
                if (!rigRead)
                {
                    rig = FigureRecipe.AccentRigAsync(FileProvider, schema).GetAwaiter().GetResult();
                    rigRead = true;
                }
                ParamSet? values = null;
                if (face.Count > 0)
                {
                    values = new ParamSet { Label = "expression" };
                    foreach (var (k, v) in FigureRecipe.FaceScalars(face)) values.Scalars[k] = v;
                    if (face.TryGetValue("Mouth", out var mouth))
                        foreach (var (k, v) in FigureRecipe.AccentScalarsAsync(FileProvider, schema, material.Path, mouth).GetAwaiter().GetResult())
                            values.Scalars[k] = v;
                }
                if (values is null && rig is null) continue;
                part.Materials[i] = new MaterialPorterMaterial(material)
                {
                    MPValues = values, MPFaceRig = rig,
                    Hash = values is null ? material.Hash : HashCode.Combine(material.Hash, values.Key()),
                };
            }
    }

    /// <summary>A car part's slots: a decal's material where it goes, Mutable's values over each material they touch.</summary>
    private void CarMaterials(ExportMesh mesh, CarPlan plan, Dictionary<int, string>? overrides)
    {
        var slots = mesh.Materials.GroupBy(m => m.Slot).ToDictionary(g => g.Key, g => g.First());
        var changed = new HashSet<int>();
        foreach (var (slot, path) in overrides ?? [])
            if (LoadMaterialPorterObject(path) is UMaterialInterface mi && Material(mi, slot) is { } decal)
            {
                slots[slot] = decal;
                changed.Add(slot);
            }
        foreach (var (slot, material) in slots)
        {
            var tail = material.Path[(material.Path.LastIndexOf('/') + 1)..];
            if (plan.Params.TryGetValue(tail, out var values))
            {
                mesh.OverrideMaterials.Add(new MaterialPorterMaterial(material with { Slot = slot })
                {
                    MPValues = values,
                    Hash = HashCode.Combine(material.Hash, values.Key()),
                });
            }
            else if (changed.Contains(slot)) mesh.OverrideMaterials.Add(material with { Slot = slot });
        }
    }

    /// <summary>A UE world matrix (row vectors) as FP's location, rotation and scale; a mirror goes into X's scale.</summary>
    private static void SetMaterialPorterTransform(ExportObject export, Matrix4x4 world)
    {
        var mirrored = world.GetDeterminant() < 0;
        if (mirrored) world = Matrix4x4.CreateScale(-1, 1, 1) * world;
        if (!Matrix4x4.Decompose(world, out var scale, out var rotation, out var translation))
        {
            scale = Vector3.One;
            rotation = Quaternion.Identity;
            translation = world.Translation;
        }
        if (mirrored) scale.X = -scale.X;
        export.Location = new FVector(translation.X, translation.Y, translation.Z);
        export.Rotation = new FQuat(rotation.X, rotation.Y, rotation.Z, rotation.W).Rotator();
        export.Scale = new FVector(scale.X, scale.Y, scale.Z);
    }

    /// <summary>An object by its path, also one nested in a level ("/Map.Map:PersistentLevel.Actor.Component.WaterInfoMesh_0").</summary>
    private UObject? LoadMaterialPorterObject(string path)
    {
        try
        {
            if (!path.Contains(':')) return FileProvider.TryLoadPackageObject(path, out var obj) ? obj : null;
            var slash = path.LastIndexOf('/');
            var package = FileProvider.LoadPackage(path[..path.IndexOf('.', slash)]);
            return package.GetExports().FirstOrDefault(e => e.GetPathName() == path);
        }
        catch (Exception e)
        {
            Log.Warning("[Material Porter] {Path}: {Message}", path, e.Message);
            return null;
        }
    }
}
