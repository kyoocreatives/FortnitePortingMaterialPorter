using System;
using System.Collections.Generic;
using System.Linq;
using CUE4Parse.UE4.Assets.Exports;
using CUE4Parse.UE4.Assets.Exports.Material;
using CUE4Parse.UE4.Assets.Objects;
using CUE4Parse.UE4.Objects.Core.Math;
using CUE4Parse.UE4.Objects.Engine;
using CUE4Parse.UE4.Objects.UObject;
using FortnitePorting.CUE4Parse.Extensions;
using FortnitePorting.Exporting.Models;
using FortnitePorting.Exporting.Styles;
using FortnitePorting.Shared.Extensions;

namespace FortnitePorting.Exporting.Types;

// The fork's export types and what its asset pages pick.
public partial class MeshExport
{
    // a level's decals and particle systems (world exports only; omitted from the JSON otherwise)
    [Newtonsoft.Json.JsonProperty(NullValueHandling = Newtonsoft.Json.NullValueHandling.Ignore)]
    public List<ExportDecal>? Decals;
    [Newtonsoft.Json.JsonProperty(NullValueHandling = Newtonsoft.Json.NullValueHandling.Ignore)]
    public List<ExportEffect>? Effects;

    [Newtonsoft.Json.JsonIgnore] public Dictionary<int, int> CarPicks = [];
    [Newtonsoft.Json.JsonIgnore] public Dictionary<string, int> FacePicks = [];

    // private overlay exports, imported by FortnitePorting.Exporting.csproj when present
    partial void ExportOwner(UObject asset, EExportType exportType);

    // wrap, effects and weapon mods picked on the asset page; also prefetches a downloaded build's files
    private void ReadPagePicks(UObject asset, ExportStyleBase[] styles)
    {
        Context.StartPrefetch(asset);
        Context.WrapPick = styles.OfType<MaterialPorter.ExportWrapStyle>().FirstOrDefault()?.Path;
        Context.EffectsPick = styles.OfType<MaterialPorter.ExportEffectsStyle>().Any(s => s.On);
        Context.WeaponModPicks = styles.OfType<MaterialPorter.ExportWeaponModStyle>().Where(s => s.Path is not null)
            .GroupBy(s => s.Slot).ToDictionary(g => g.Key, g => g.Last().Path!);
    }

    // car channel picks, LEGO figure expression, and picked styles' effect changes (needed before part effects are exported)
    private void ReadStylePicks(ExportStyleBase[] styles)
    {
        CarPicks = styles.OfType<MaterialPorter.ExportCarStyle>().ToDictionary(s => s.Channel, s => s.Option);
        FacePicks = styles.OfType<MaterialPorter.ExportFigureFaceStyle>().Where(s => s.Pose >= 0).ToDictionary(s => s.Feature, s => s.Pose);
        foreach (var style in styles.OfType<ExportStructStyle>()) Context.AddEffectStyle(style.StyleData);
    }

    // applies the picked wrap to every mesh of the export
    private void ApplyWrapPick()
    {
        if (Context.WrapPick is not { Length: > 0 } path) return;
        try
        {
            if (Context.WrapValuesAt(path) is not { } values) return;
            foreach (var mesh in Meshes.Concat(OverrideMeshes)) Context.ApplyWrapDeep(mesh, values);
        }
        catch (Exception e)
        {
            Serilog.Log.Warning("[Material Porter] {Name}: the wrap wasn't laid over it ({Error})", Name, e.Message);
        }
    }

    // A prop whose parts sit off its pivot (e.g. a billboard) goes under an empty at the item's transform;
    // applying it to each part would move their offsets without rotating or scaling them.
    private static bool GroupOffPivot(FStructFallback prop, FTransform transform, ref List<ExportObject> objects)
    {
        if (!objects.Any(o => o.Location != FVector.ZeroVector || o.Rotation.Pitch != 0 || o.Rotation.Yaw != 0 || o.Rotation.Roll != 0))
            return false;
        var item = new ExportMesh
        {
            Name = prop.GetOrDefault<FName>("RecordUniqueName") is { IsNone: false } record ? record.Text : objects[0].Name,
            IsEmpty = true,
            Location = transform.Translation,
            Rotation = transform.Rotator(),
            Scale = transform.Scale3D
        };
        item.AddChildren(objects);
        objects = [item];
        return true;
    }

    // the level's lights, decals and particle systems
    private void AddLevelExtras()
    {
        Lights.AddRange(Context.MaterialPorterLights);
        Decals = [.. Context.MaterialPorterDecals];
        Effects = [.. Context.MaterialPorterEffects];
    }

    // a sprite's second material slot and its effects
    private void AddSpriteExtras(UObject asset, ExportMesh? exportMesh)
    {
        if (asset.GetDataListItem<UMaterialInterface>("MaterialSlot2") is { } second)
            exportMesh?.OverrideMaterials.AddIfNotNull(Context.Material(second, 1));
        if (Context.EffectsPick && exportMesh is not null) Context.SpriteEffects(asset, exportMesh);
    }

    // the fork's own export types; false for others
    private bool ExportForkType(UObject asset, EExportType exportType)
    {
        switch (exportType)
        {
            case EExportType.Car:
                Meshes.AddRange(Context.MaterialPorterCar(asset, CarPicks));
                Type = EExportType.Vehicle;     // the plugins import it as a vehicle
                return true;
            case EExportType.Effect:
                // an effect the tab lists unread is read now
                Meshes.Add(Context.Effect(MaterialPorter.Unloaded.Read(asset, Context.Meta.Provider.Provider)));
                return true;
            case EExportType.TimeOfDay:
                ExportOwner(asset, exportType);
                return true;
            case EExportType.Contrail:
            {
                if (asset.GetOrDefault<FSoftObjectPath>(MaterialPorter.Effects.ContrailEffect).TryLoad(out UObject? contrail))
                {
                    var effect = Context.Effect(contrail);
                    // as the locker shows it, attached to the selected armature in Blender
                    if (effect is MaterialPorter.MaterialPorterMesh { MPEffect: { } node })
                    {
                        node["User"] = new Dictionary<string, object> { ["User.bIsFrontEnd"] = true, ["User.bIsFrontEndPreview"] = true };
                        node["Attach"] = true;
                    }
                    Meshes.Add(effect);
                }
                Type = EExportType.Effect;      // the plugins import it as an effect
                return true;
            }
            case EExportType.LegoWildlife:
                Meshes.AddRange(Context.MaterialPorterCreature(asset));
                return true;
            case EExportType.LegoProp:
                // building prop or set: the actor its item previews; cave room: its level
                if (MaterialPorter.Figures.PropActor(asset) is { } actorClass)
                {
                    AddObjects(Context.Blueprint(actorClass));
                    AddObjects(Context.NativeGeometryCollections(actorClass));
                }
                else if (asset.GetOrDefault<FSoftObjectPath>("World").TryLoad<UWorld>(out var caveWorld))
                {
                    Meshes.AddRange(Context.World(caveWorld));
                    AddLevelExtras();
                }
                return true;
            case EExportType.LegoOutfit:
                // cooked (Bake folder meshes) or built from its recipe
                Meshes.AddRange(Context.MaterialPorterFigure(asset, FacePicks));
                return true;
            default:
                return false;
        }
    }
}
