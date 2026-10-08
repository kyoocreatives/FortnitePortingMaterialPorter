using System;
using System.Collections.Generic;
using System.Linq;
using CUE4Parse.GameTypes.FN.Assets.Exports;
using CUE4Parse.GameTypes.FN.Assets.Exports.DataAssets;
using CUE4Parse.UE4.Assets.Exports;
using CUE4Parse.UE4.Assets.Exports.Material;
using CUE4Parse.UE4.Assets.Exports.SkeletalMesh;
using CUE4Parse.UE4.Assets.Exports.StaticMesh;
using CUE4Parse.UE4.Assets.Exports.Texture;
using CUE4Parse.UE4.Assets.Objects;
using CUE4Parse.UE4.Assets.Objects.Properties;
using CUE4Parse.UE4.Objects.Core.Math;
using CUE4Parse.UE4.Objects.Engine;
using CUE4Parse.UE4.Objects.Engine.Animation;
using CUE4Parse.UE4.Objects.UObject;
using FortnitePorting.CUE4Parse.Extensions;
using FortnitePorting.CUE4Parse.Models.Fortnite;
using FortnitePorting.CUE4Parse.Models.Fortnite.Enums;
using FortnitePorting.CUE4Parse.Models.Fortnite.Instructions;
using FortnitePorting.Exporting.Models;
using FortnitePorting.Shared.Extensions;

namespace FortnitePorting.Exporting.Context;

// Prefab reading: template keys, per-slot texture data, older records.
public partial class ExportContext
{
    public List<ExportObject> LevelSaveRecord(ULevelSaveRecord levelSaveRecord)
    {
        var objects = new List<ExportObject>();
        foreach (var (_, templateRecord) in levelSaveRecord.TemplateRecords)
        {
            if (templateRecord is null) continue;
            var actorBlueprint = templateRecord.ActorClass.Load<UBlueprintGeneratedClass>();
            if (actorBlueprint is null) continue;

            // The record's ActorData list skips null templates, so its index isn't the template key.
            var templateObjects = Blueprint(actorBlueprint);
            if (templateObjects.Count == 0) continue;
            objects.AddRange(templateObjects);

            // texture data by slot; null clears the slot (mesh's own material)
            var textureDatas = new Dictionary<int, UBuildingTextureData?>();
            if (templateRecord.bUsingRecordDataReferenceTable)
            {
                var table = templateRecord.ActorDataReferenceTable;
                foreach (var property in ActorDataReader.Read(templateRecord.ActorData) ?? [])
                {
                    if (property.Name != "TextureData" || property.ReferenceIndex < 0) continue;
                    if (table is null || property.ReferenceIndex >= table.Length) continue;

                    var textureDataPath = table[property.ReferenceIndex];
                    UBuildingTextureData? textureData = null;
                    if (!textureDataPath.AssetPathName.IsNone && !string.IsNullOrEmpty(textureDataPath.AssetPathName.Text))
                        textureDataPath.TryLoad(out textureData);
                    textureDatas[property.ArrayIndex] = textureData;
                }
            }
            else
            {
                foreach (var property in LegacyActorData(levelSaveRecord, templateRecord).Properties)
                {
                    if (!property.Name.Text.Equals("TextureData"))
                        continue;

                    var textureData = property.Tag?.GetValue<FPackageIndex>()?.Load<UBuildingTextureData>();
                    if (property.Tag?.GetValue<string>() is { } textureDataAssetPath)
                        textureData ??= Meta.Provider.Provider!.SafeLoadPackageObject<UBuildingTextureData>(textureDataAssetPath);

                    if (textureData is not null)
                        textureDatas[property.ArrayIndex] = textureData;
                }
            }
            if (textureDatas.Count == 0) continue;

            // apply onto the actor's own mesh (the class default's), replacing that slot's default
            var meshes = templateObjects.OfType<ExportMesh>().ToList();
            var defaultName = actorBlueprint.ClassDefaultObject?.Name;
            var targetMesh = meshes.FirstOrDefault(mesh => mesh.TextureData.Count > 0)
                             ?? meshes.FirstOrDefault(mesh => mesh.Name == defaultName)
                             ?? meshes.FirstOrDefault();
            if (targetMesh is null) continue;
            foreach (var (textureDataIndex, textureData) in textureDatas)
            {
                targetMesh.TextureData.RemoveAll(data => data.Index == textureDataIndex);
                targetMesh.TextureData.AddIfNotNull(TextureData(textureData, textureDataIndex));
            }
        }

        return objects;
    }

    // CUE4Parse clears the package's unversioned flag while reading and leaves it cleared if the read throws; restore it.
    static FStructFallback LegacyActorData(ULevelSaveRecord levelSaveRecord, FActorTemplateRecord templateRecord)
    {
        var owner = levelSaveRecord.Owner;
        if (owner is null) return new FStructFallback();
        var flags = owner.Summary.PackageFlags;
        try
        {
            return templateRecord.ReadActorData(owner, levelSaveRecord.SaveVersion);
        }
        finally
        {
            owner.Summary.PackageFlags = flags;
        }
    }
}
