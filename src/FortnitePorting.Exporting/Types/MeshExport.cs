using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using CUE4Parse.GameTypes.FN.Assets.Exports;
using CUE4Parse.UE4.Assets.Exports;
using CUE4Parse.UE4.Assets.Exports.Animation;
using CUE4Parse.UE4.Assets.Exports.Component.SkeletalMesh;
using CUE4Parse.UE4.Assets.Exports.Component.StaticMesh;
using CUE4Parse.UE4.Assets.Exports.Material;
using CUE4Parse.UE4.Assets.Exports.SkeletalMesh;
using CUE4Parse.UE4.Assets.Exports.Texture;
using CUE4Parse.UE4.Assets.Objects;
using CUE4Parse.UE4.Objects.Core.Math;
using CUE4Parse.UE4.Objects.Engine;
using CUE4Parse.UE4.Objects.Engine.Animation;
using CUE4Parse.UE4.Objects.GameplayTags;
using CUE4Parse.UE4.Objects.UObject;
using CUE4Parse.Utils;
using FortnitePorting.CUE4Parse.Extensions;
using FortnitePorting.CUE4Parse.Models.Fortnite;
using FortnitePorting.CUE4Parse.Models.Fortnite.Enums;
using FortnitePorting.CUE4Parse.Models.Fortnite.Instructions;
using FortnitePorting.Exporting.Custom;
using FortnitePorting.Exporting.Models;
using FortnitePorting.Exporting.Models.Files.Meta;
using FortnitePorting.Exporting.Styles;
using FortnitePorting.Shared.Extensions;
using Path = System.IO.Path;

namespace FortnitePorting.Exporting.Types;

public partial class MeshExport : BaseExport
{
    public readonly List<ExportMesh> Meshes = [];
    public readonly List<ExportMesh> OverrideMeshes = [];
    public readonly List<ExportOverrideMaterial> OverrideMaterials = [];
    public readonly List<ExportOverrideParameters> OverrideParameters = [];
    public readonly List<ExportOverrideMorphTargets> OverrideMorphTargets = [];
    public ExportLightCollection Lights = new();
    /// <summary>Material Porter fork: a level's decals (a world export's; omitted from any other).</summary>
    [Newtonsoft.Json.JsonProperty(NullValueHandling = Newtonsoft.Json.NullValueHandling.Ignore)]
    public List<ExportDecal>? Decals;
    /// <summary>Material Porter fork: a level's particle systems (a world export's; omitted from any other).</summary>
    [Newtonsoft.Json.JsonProperty(NullValueHandling = Newtonsoft.Json.NullValueHandling.Ignore)]
    public List<ExportEffect>? Effects;
    public AnimExport? Animation;
    [Newtonsoft.Json.JsonIgnore] public Dictionary<int, int> CarPicks = [];
    [Newtonsoft.Json.JsonIgnore] public Dictionary<string, int> FacePicks = [];
    
    public MeshExport(string name, UObject asset, ExportStyleBase[] styles, EExportType exportType, ExportDataMeta metaData, IExportFileMeta? fileMeta) : base(name, exportType, metaData)
    {
        // Material Porter fork: the wrap and the weapon mods picked on the asset's page
        Context.WrapPick = styles.OfType<MaterialPorter.ExportWrapStyle>().FirstOrDefault()?.Path;
        Context.EffectsPick = styles.OfType<MaterialPorter.ExportEffectsStyle>().Any(s => s.On);
        Context.WeaponModPicks = styles.OfType<MaterialPorter.ExportWeaponModStyle>().Where(s => s.Path is not null)
            .GroupBy(s => s.Slot).ToDictionary(g => g.Key, g => g.Last().Path!);

        var objectStyles = styles.OfType<ExportObjectStyle>().ToArray();
        if (objectStyles.Length > 0)
        {
            foreach (var objectStyle in objectStyles)
            {
                Export(objectStyle.StyleData, objectStyle.AssociatedExportType is not EExportType.None ? objectStyle.AssociatedExportType : exportType);
            }

            ApplyWrapPick();
            return;
        }

        // Material Porter fork: a car's style picks (channel -> option)
        CarPicks = styles.OfType<MaterialPorter.ExportCarStyle>().ToDictionary(s => s.Channel, s => s.Option);
        // a LEGO figure's expression (feature -> rig pose)
        FacePicks = styles.OfType<MaterialPorter.ExportFigureFaceStyle>().Where(s => s.Pose >= 0).ToDictionary(s => s.Feature, s => s.Pose);
        // what the picked styles do to the item's effects, known before its parts' effects are exported
        foreach (var style in styles.OfType<ExportStructStyle>()) Context.AddEffectStyle(style.StyleData);
        Export(asset, exportType);

        var assetStyles = styles.OfType<ExportStructStyle>();
        ExportStyles(asset, assetStyles);
        ApplyWrapPick();
    }

    /// <summary>Material Porter fork: the wrap picked on the asset's page, over every mesh of the export.</summary>
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

    public MeshExport(string name, MeshDefinition mesh, Func<string, Stream> openCustomAssetResource, EExportType exportType, ExportDataMeta metaData) : base(name, exportType, metaData)
    {
        string ExportCustom(string path)
        {
            using var stream = openCustomAssetResource(path);

            var outPathPortion = path.SubstringAfter("Assets/");
            var outPath = Path.Combine(metaData.AssetsRoot, outPathPortion);
            Directory.CreateDirectory(outPath.SubstringBeforeLast("/"));
            File.WriteAllBytes(outPath, stream.ReadToEnd());

            return outPathPortion;
        }
        
        var exportMesh = new ExportMesh();
        exportMesh.Name = name;
        exportMesh.Path = ExportCustom(mesh.Path);

        for (var matIndex = 0; matIndex < mesh.Materials.Length; matIndex++)
        {
            var material = mesh.Materials[matIndex];
            
            var exportMaterial = new ExportMaterial();
            exportMaterial.Name = material.Name;
            exportMaterial.Slot = matIndex;
            exportMaterial.Hash = material.Name.GetHashCode();

            foreach (var texture in material.Textures)
            {
                exportMaterial.Textures.Add(new TextureParameter(texture.Slot, new ExportTexture(ExportCustom(texture.Path), true,
                    TextureCompressionSettings.TC_Default)));
            }
            
            exportMesh.Materials.Add(exportMaterial);
        }
        
        Meshes.Add(exportMesh);
        
    }

    /// <summary>The owner's private overlay's exports (FortnitePorting.Exporting.csproj imports it when it's there).</summary>
    partial void ExportOwner(UObject asset, EExportType exportType);

    public void Export(UObject asset, EExportType exportType)
    {
        switch (exportType)
        {
            case EExportType.Outfit:
            {
                UAnimMontage? montage = asset.GetOrDefault<UAnimMontage?>("FrontendAnimMontageIdleOverride");
                var parts = asset.GetOrDefault("BaseCharacterParts", Array.Empty<UObject>());
                if (asset.TryGetValue(out UObject heroDefinition, "HeroDefinition"))
                {
                    if (parts.Length == 0 && heroDefinition.TryGetValue(out UObject[] specializations, "Specializations"))
                    {
                        parts = specializations.First().GetOrDefault("CharacterParts", Array.Empty<UObject>());
                    }

                    montage ??= heroDefinition.GetOrDefault<UAnimMontage?>("FrontendAnimMontageIdleOverride");
                }
                
                foreach (var part in parts)
                {
                    Meshes.AddIfNotNull(Context.CharacterPart(part));
                    
                    montage ??= part.GetOrDefault<UAnimMontage?>("FrontendAnimMontageIdleOverride");
                }

                if (Meshes.FirstOrDefault(mesh => mesh is ExportPart { Type: EFortCustomPartType.Body }) is ExportPart bodyPart)
                {
                    montage ??= bodyPart.GenderPermitted switch
                    {
                        EFortCustomGender.Female => Context.Meta.Provider.FemaleLobbyMontages.Random()!,
                        _ => Context.Meta.Provider.MaleLobbyMontages.Random()!
                    };
                }
                
                if (Context.Meta.Settings.ImportLobbyPoses && montage is not null)
                {
                    Animation = new AnimExport(montage.Name, montage, [], EExportType.Animation, Context.Meta, null);
                }
                
                break;
            }
            case EExportType.CharacterPart:
            {
                Meshes.AddIfNotNull(Context.CharacterPart(asset));
                
                break;
            }
            case EExportType.Backpack:
            {
                var parts = asset.GetOrDefault("CharacterParts", Array.Empty<UObject>());
                foreach (var part in parts)
                {
                    Meshes.AddIfNotNull(Context.CharacterPart(part));
                }
                
                break;
            }
            case EExportType.Pickaxe:
            {
                var weapon = asset.GetOrDefault<UObject?>("WeaponDefinition");
                if (weapon is null) break;

                Meshes.AddRange(Context.WeaponDefinition(weapon));
                // Material Porter fork: its trail, swing and idle effects, when its page says so
                if (Context.EffectsPick) Context.PickaxeEffects(weapon, Meshes);
                break;
            }
            case EExportType.Glider:
            {
                var mesh = asset.GetOrDefault<USkeletalMesh?>("SkeletalMesh");
                if (mesh is null) break;

                var part = Context.Mesh(mesh);
                if (part is null) break;

                var overrideMaterials = asset.GetOrDefault("MaterialOverrides", Array.Empty<FStructFallback>());
                foreach (var overrideMaterial in overrideMaterials)
                {
                    part.OverrideMaterials.AddIfNotNull(Context.OverrideMaterial(overrideMaterial));
                }

                // Material Porter fork: its trails, when its page says so
                if (Context.EffectsPick) Context.GliderEffects(asset, part);
                Meshes.Add(part);
                break;
            }
            case EExportType.Pet:
            {
                // backpack meshes
                var parts = asset.GetOrDefault("CharacterParts", Array.Empty<UObject>());
                foreach (var part in parts) Meshes.AddIfNotNull(Context.CharacterPart(part));

                // pet mesh
                var petAsset = asset.Get<UObject>("DefaultPet");
                var prefabClassPath = petAsset.Get<FSoftObjectPath>("PetPrefabClass");
                var prefabExports = Context.Meta.Provider.Provider.LoadAllObjects(prefabClassPath.AssetPathName.Text.SubstringBeforeLast("."));
                if (prefabExports.FirstOrDefault(export => export.Name.Equals("PetMesh0")) is not USkeletalMeshComponentBudgeted meshComponent) break;
                
                var mesh = meshComponent.GetSkeletalMesh().Load<USkeletalMesh>();
                if (mesh is null) break;

                var exportMesh = Context.Mesh<ExportPart>(mesh);
                if (exportMesh is null) break;
                
                var meta = new ExportPoseAssetMeta();
                if (meshComponent.TryGetValue(out UAnimBlueprintGeneratedClass animBlueprint, "AnimClass"))
                {
                    var animBlueprintData = animBlueprint.ClassDefaultObject.Load()!;
                    if (animBlueprintData.TryGetValue(out UPoseAsset poseAsset, "FacePoseAsset"))
                    {
                        meta.PoseAsset = Context.Export(poseAsset); // most pets have empty pose assets now but whatever
                    }
                }

                exportMesh.Meta = meta;
                
                Meshes.AddIfNotNull(exportMesh);

                break;
            }
            case EExportType.Toy:
            {
                var actor = asset.Get<UBlueprintGeneratedClass>("ToyActorClass");

                var exportComponent = GetComponent(actor);
                exportComponent ??= GetComponent(actor.SuperStruct.Load<UBlueprintGeneratedClass>());
                if (exportComponent is null) break;

                Meshes.AddIfNotNull(Context.MeshComponent(exportComponent));
                break;

                UStaticMeshComponent? GetComponent(UBlueprintGeneratedClass? blueprint)
                {
                    if (blueprint is null) return null;
                    if (!blueprint.TryGetValue(out UObject internalComponentHandler, "InheritableComponentHandler")) return null;

                    var records = internalComponentHandler.GetOrDefault("Records", Array.Empty<FStructFallback>());
                    foreach (var record in records)
                    {
                        var component = record.Get<UObject>("ComponentTemplate");
                        if (component is not UStaticMeshComponent staticMeshComponent) continue;

                        return staticMeshComponent;
                    }

                    return null;
                }
            }
            case EExportType.Prop:
            {
                var levelSaveRecord = asset.Get<ULevelSaveRecord>("ActorSaveRecord");
                AddObjects(Context.LevelSaveRecord(levelSaveRecord));
                break;
            }
            case EExportType.Prefab:
            {
                if (asset.TryGetValue(out ULevelSaveRecord baseSaveRecord, "LevelSaveRecord"))
                {
                    throw new NotSupportedException("Legacy level save record prefabs are not supported.");
                }
                
                var recordCollectionLazy = asset.GetOrDefault<FPackageIndex?>("PlaysetPropLevelSaveRecordCollection");
                if (recordCollectionLazy is null || recordCollectionLazy.IsNull || !recordCollectionLazy.TryLoad(out var recordCollection) || recordCollection is null) break;

                var props = recordCollection.GetOrDefault<FStructFallback[]>("Items");
                var totalProps = props.Length;
                var currentProp = 0;
                foreach (var prop in props)
                {
                    if (Context.CancellationToken.IsCancellationRequested) break;
                    
                    currentProp++;
                    
                    var levelSaveRecord = prop.GetOrDefault<UObject?>("LevelSaveRecord");
                    if (levelSaveRecord is null) continue;

                    var targetSaveRecord = levelSaveRecord.GetOrDefault<ULevelSaveRecord?>("ActorSaveRecord") ?? prop.GetOrDefault<ULevelSaveRecord?>("LevelSaveRecord");
                    if (targetSaveRecord is null) continue;
                    
                    var transform = prop.GetOrDefault<FTransform>("Transform");
                    var objects = Context.LevelSaveRecord(targetSaveRecord);
                    // Material Porter fork: a prop whose parts sit off its pivot (a billboard's screen and
                    // frame) under an empty at the item's transform - added to each part, the transform
                    // moved their offsets without turning or scaling them with the item
                    if (objects.Any(o => o.Location.X != 0 || o.Location.Y != 0 || o.Location.Z != 0
                                         || o.Rotation.Pitch != 0 || o.Rotation.Yaw != 0 || o.Rotation.Roll != 0))
                    {
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
                    }
                    else foreach (var mesh in objects)
                    {
                        mesh.Location += transform.Translation;
                        mesh.Rotation += transform.Rotator();
                        mesh.Scale *= transform.Scale3D;
                    }
                    
                    Context.Meta.OnUpdateProgress(objects.FirstOrDefault()?.Name ?? "Prop", currentProp, totalProps);

                    AddObjects(objects);
                }

                break;
            }
            case EExportType.Mesh:
            case EExportType.WeaponMod:
            {
                if (asset is UBlueprintGeneratedClass blueprintGeneratedClass)
                {
                    AddObjects(Context.Blueprint(blueprintGeneratedClass));
                }
                else if (asset is UFortBuildingInstructions buildingInstructions)
                {
                    Meshes.AddIfNotNull(Context.FortBuildingInstructions(buildingInstructions));
                }
                else
                {
                    Meshes.AddIfNotNull(Context.Mesh(asset));
                }
                break;
            }
            case EExportType.World:
            {
                if (asset is not UWorld world) break;

                Name = world.Owner?.Name.SubstringAfterLast("/") ?? world.Name;
                Meshes.AddRange(Context.World(world));
                // Material Porter fork: the level's point, spot and rect lights, decals and particle systems
                Lights.AddRange(Context.MaterialPorterLights);
                Decals = [.. Context.MaterialPorterDecals];
                Effects = [.. Context.MaterialPorterEffects];
                break;
            }
            case EExportType.Item:
            {
                Meshes.AddRange(Context.WeaponDefinition(asset));
                break;
            }
            case EExportType.Resource:
            {
                Meshes.AddRange(Context.WeaponDefinition(asset));
                break;
            }
            case EExportType.Trap:
            {
                var actor = asset.Get<UBlueprintGeneratedClass>("BlueprintClass").ClassDefaultObject.Load();
                if (actor is null) break;

                var staticMesh = actor.GetOrDefault<UBaseBuildingStaticMeshComponent?>("StaticMeshComponent");
                if (staticMesh is not null)
                {
                    Meshes.AddIfNotNull(Context.MeshComponent(staticMesh));
                }

                var components = Context.Meta.Provider.Provider.LoadAllObjects(actor.GetPathName().SubstringBeforeLast("."));
                foreach (var component in components)
                {
                    if (component.Name.Equals(staticMesh?.Name)) continue;
                    Meshes.AddIfNotNull(Context.MeshComponent(component));
                }

                break;
            }
            case EExportType.Sprite:
            {
                var mesh = asset.GetDataListItem<USkeletalMesh>("SkeletalMesh");
                var exportMesh = Context.Mesh(mesh);

                var material = asset.GetDataListItem<UMaterialInterface>("Material");
                var exportMaterial = Context.Material(material, 0);
                exportMesh?.OverrideMaterials.AddIfNotNull(exportMaterial);
                // Material Porter fork: a sprite's second material slot, its own effect (a fire sprite's flames)
                if (asset.GetDataListItem<UMaterialInterface>("MaterialSlot2") is { } second)
                    exportMesh?.OverrideMaterials.AddIfNotNull(Context.Material(second, 1));
                if (Context.EffectsPick && exportMesh is not null) Context.SpriteEffects(asset, exportMesh);

                Meshes.AddIfNotNull(exportMesh);

                break;
            }
            case EExportType.FallGuysOutfit:
            {
                var parts = asset.GetOrDefault("BaseCharacterParts", Array.Empty<UObject>());
                foreach (var part in parts)
                {
                    Meshes.AddIfNotNull(Context.CharacterPart(part));
                }

                var bodyParams = new ExportOverrideParameters {MaterialNameToAlter = "MI_BeanChar_Body_Base"};
                ExportOverrideParameters costumeParams = null;
                ExportOverrideParameters headCostumeParams = null;
                
                // Costume material is always in slot 1 of the last mesh
                if (Meshes.Last().Materials.Count > 1)
                    costumeParams = new ExportOverrideParameters {MaterialNameToAlter = Meshes.Last().Materials[1].Name};
                
                // Head costume is a separate mesh, so we know one is used when there are 2+ meshes
                if (Meshes.Count > 1)
                    headCostumeParams = new ExportOverrideParameters {MaterialNameToAlter = Meshes[0].Materials[0].Name};
                

                var additionalFields = asset.GetOrDefault("AdditionalDataFields", Array.Empty<FPackageIndex>());
                foreach (var additionalField in additionalFields)
                {
                    var field = additionalField.Load();
                    if (field is null) continue;
                    if (!field.ExportType.Equals("BeanCosmeticItemDefinitionBase")) continue;

                    // Eye
                    ColorIndex("BodyEyesColorIndex", "Body_EyesColor", bodyParams);
                    MaterialTypeIndex("BodyEyesMaterialTypeIndex", "Body_Eyes_MaterialProps", bodyParams);
                        
                    // Main
                    ColorIndex("BodyMainColorIndex", "Body_MainColor", bodyParams);
                    MaterialTypeIndex("BodyMainMaterialTypeIndex", "Body_MaterialProps", bodyParams);
                    
                    // Pattern
                    Texture("Body_Pattern", "Body_Pattern", bodyParams);
                    ColorIndex("BodySecondaryColorIndex", "Body_SecondaryColor", bodyParams);
                    MaterialTypeIndex("BodySecondaryMaterialTypeIndex", "Body_Secondary_MaterialProps", bodyParams);
                    
                    // Face Plate
                    ColorIndex("BodyFaceplateColorIndex", "Body_FacePlateColor", bodyParams);
                    MaterialTypeIndex("BodyFaceplateMaterialTypeIndex", "Body_Faceplate_MaterialProps", bodyParams);
                    
                    // Face Items
                    ColorIndex("EyelashesColorIndex", "Eyelashes_Color", bodyParams);
                    MaterialTypeIndex("EyelashesMaterialTypeIndex", "Eyelashes_MaterialProps", bodyParams);
                    ColorIndex("GlassesFrameColorIndex", "Glasses_Frame_Color", bodyParams);
                    MaterialTypeIndex("GlassesFrameMaterialTypeIndex", "Glasses_Frame_MaterialProps", bodyParams);
                    ColorIndex("GlassesLensesColorIndex", "Glasses_Lense_Color", bodyParams);
                    MaterialTypeIndex("GlassesLensesMaterialTypeIndex", "Glasses_Lense_MaterialProps", bodyParams);

                    bodyParams.Vectors.Add(new VectorParameter("Body_GlassesEyeLashes", new FLinearColor
                    {
                        R = field.GetOrDefault<bool>("bGlasses") ? 1 : 0,
                        G = field.GetOrDefault<bool>("bGlassesLenses") ? 1 : 0,
                        B = field.GetOrDefault<bool>("bEyelashes") ? 1 : 0
                    }));

                    if (costumeParams != null)
                    {
                        // Costume
                        ColorIndex("CostumeMainColorIndex", "Costume_MainColor", costumeParams);
                        MaterialTypeIndex("CostumeMainMaterialTypeIndex", "Costume_MainMaterialProps", costumeParams);
                        ColorIndex("CostumeSecondaryColorIndex", "Costume_Secondary_Color", costumeParams);
                        MaterialTypeIndex("CostumeSecondaryMaterialTypeIndex", "Costume_SecondaryMaterialProps", costumeParams);
                        ColorIndex("CostumeAccentColorIndex", "Costume_AccentColor", costumeParams);
                        MaterialTypeIndex("CostumeAccentMaterialTypeIndex", "Costume_AccentMaterialProps", costumeParams);
                        AtlasTextureSlotIndex("CostumePatternAtlasTextureSlot", "Costume_UVPatternPosition", costumeParams);
                    }
                    
                    if (headCostumeParams != null)
                    {
                        // Head Costume
                        ColorIndex("HeadCostumeMainColorIndex", "Head_Costume_MainColor", headCostumeParams);
                        MaterialTypeIndex("HeadCostumeMainMaterialTypeIndex", "Head_Costume_MainMaterialProps", headCostumeParams);
                        ColorIndex("HeadCostumeSecondaryColorIndex", "Head_Costume_Secondary_Color", headCostumeParams);
                        MaterialTypeIndex("HeadCostumeSecondaryMaterialTypeIndex", "Head_Costume_SecondaryMaterialProps", headCostumeParams);
                        ColorIndex("HeadCostumeAccentColorIndex", "Head_Costume_AccentColor", headCostumeParams);
                        MaterialTypeIndex("HeadCostumeAccentMaterialTypeIndex", "Head_Costume_AccentMaterialProps", headCostumeParams);
                        AtlasTextureSlotIndex("HeadCostumePatternAtlasTextureSlot", "Head_Costume_UVPatternPosition", headCostumeParams);
                    }
                    
                    continue;
                    
                    void AtlasTextureSlotIndex(string propertyName, string shaderName, ExportOverrideParameters parameterSet)
                    {
                        if (!field.TryGetValue(out int index, propertyName))
                        {
                            parameterSet.Vectors.Add(new VectorParameter(shaderName, new FLinearColor(0, 0.5f, 0, 0)));
                            return;
                        }

                        // do we know why this is inverted? no
                        // do we care? probably not
                        index = index switch
                        {
                            1 => 4,
                            2 => 3,
                            3 => 2,
                            4 => 1
                        };

                        var offset = Context.Meta.Provider.BeanstalkAtlasTextureUVs[index];
                        parameterSet.Vectors.Add(new VectorParameter(shaderName, new FLinearColor(offset.X, offset.Y, offset.Z, 0)));
                    }

                    void ColorIndex(string propertyName, string shaderName, ExportOverrideParameters parameterSet)
                    {
                        if (!field.TryGetValue(out int index, propertyName)) return;
                    
                        var color = Context.Meta.Provider.BeanstalkColors[index];
                        parameterSet.Vectors.Add(new VectorParameter(shaderName, color.ToLinearColor()));
                    }
                    
                    void MaterialTypeIndex(string propertyName, string shaderName, ExportOverrideParameters parameterSet)
                    {
                        if (!field.TryGetValue(out int index, propertyName)) return;
                    
                        var color = Context.Meta.Provider.BeanstalkMaterialProps[index];
                        parameterSet.Vectors.Add(new VectorParameter(shaderName, color));
                    }
                    
                    void Texture(string propertyName, string shaderName, ExportOverrideParameters parameterSet)
                    {
                        if (!field.TryGetValue(out UTexture2D texture, propertyName)) return;
                    
                        parameterSet.Textures.AddUnique(new TextureParameter(shaderName, 
                            new ExportTexture(Context.Export(texture), texture.SRGB, texture.CompressionSettings)));
                    }
                }
                    
                bodyParams.Hash = bodyParams.GetHashCode();
                OverrideParameters.Add(bodyParams);

                if (costumeParams != null)
                {
                    costumeParams.Hash = costumeParams.GetHashCode();
                    OverrideParameters.Add(costumeParams);
                }

                if (headCostumeParams != null)
                {
                    headCostumeParams.Hash = headCostumeParams.GetHashCode();
                    OverrideParameters.Add(headCostumeParams);
                }
                
                break;
            }
            case EExportType.FestivalBass:
            case EExportType.FestivalDrum:
            case EExportType.FestivalGuitar:
            case EExportType.FestivalKeytar:
            case EExportType.FestivalMic:
            {
                if (asset.TryGetValue(out USkeletalMesh mesh, "Mesh"))
                {
                    var exportMesh = Context.Mesh(mesh);

                    var material = asset.GetOrDefault<UMaterialInterface>("Material");
                    exportMesh?.Materials.AddIfNotNull(Context.Material(material, 0));

                    Meshes.AddIfNotNull(exportMesh);
                }

                if (asset.TryGetValue(out USkeletalMesh leftHandMesh, "LeftHandMesh"))
                {
                    var exportMesh = Context.Mesh(leftHandMesh);

                    var material = asset.GetOrDefault<UMaterialInterface>("LeftHandMaterial");
                    exportMesh?.Materials.AddIfNotNull(Context.Material(material, 0));

                    Meshes.AddIfNotNull(exportMesh);
                }

                if (asset.TryGetValue(out USkeletalMesh auxiliaryMesh, "AuxiliaryMesh"))
                {
                    var exportMesh = Context.Mesh(auxiliaryMesh);
                    if (exportMesh is null) break;

                    if (asset.TryGetValue(out UMaterialInterface auxMaterial, "AuxiliaryMaterial"))
                        exportMesh?.Materials.AddIfNotNull(Context.Material(auxMaterial, 0));

                    if (asset.TryGetValue(out UMaterialInterface auxMaterial2, "AuxiliaryMaterial2"))
                        exportMesh?.Materials.AddIfNotNull(Context.Material(auxMaterial2, 1));

                    Meshes.AddIfNotNull(exportMesh);
                }


                break;
            }
            case EExportType.Wildlife:
            {
                Meshes.AddIfNotNull(Context.Mesh(asset));
                break;
            }
            case EExportType.Car:
            {
                // Material Porter fork: the body, its wheels on their sockets, Mutable's colours
                Meshes.AddRange(Context.MaterialPorterCar(asset, CarPicks));
                Type = EExportType.Vehicle;     // the plugins import it as a vehicle
                break;
            }
            case EExportType.Effect:
            {
                // Material Porter fork: a particle effect's emitters, and what each draws (one the tab
                // lists unread is read now)
                Meshes.Add(Context.Effect(MaterialPorter.Unloaded.Read(asset, Context.Meta.Provider.Provider)));
                break;
            }
            case EExportType.TimeOfDay:
                // Material Porter fork: the owner's private overlay exports it (nothing without it)
                ExportOwner(asset, exportType);
                break;
            case EExportType.Contrail:
            {
                // Material Porter fork: a contrail is its item's effect (put on a character in Blender: Replay Effect)
                if (asset.GetOrDefault<FSoftObjectPath>(MaterialPorter.Effects.ContrailEffect).TryLoad(out UObject? contrail))
                {
                    var effect = Context.Effect(contrail);
                    // as the locker shows it (the character needn't fall), and on the armature selected in Blender
                    if (effect is MaterialPorter.MaterialPorterMesh { MPEffect: { } node })
                    {
                        node["User"] = new Dictionary<string, object> { ["User.bIsFrontEnd"] = true, ["User.bIsFrontEndPreview"] = true };
                        node["Attach"] = true;
                    }
                    Meshes.Add(effect);
                }
                Type = EExportType.Effect;      // the plugins import it as an effect
                break;
            }
            case EExportType.LegoWildlife:
            {
                // Material Porter fork: a LEGO creature's meshes, their materials in its colours
                Meshes.AddRange(Context.MaterialPorterCreature(asset));
                break;
            }
            case EExportType.LegoProp:
            {
                // Material Porter fork: a LEGO building prop or set, the meshes of the actor its item previews;
                // a LEGO build (wall, station...), its DataList's actor class
                if (MaterialPorter.Figures.PropActor(asset) is { } actorClass)
                {
                    AddObjects(Context.Blueprint(actorClass));
                    AddObjects(Context.NativeGeometryCollections(actorClass));
                }
                // a LEGO cave room: its level
                else if (asset.GetOrDefault<FSoftObjectPath>("World").TryLoad<UWorld>(out var caveWorld))
                {
                    Meshes.AddRange(Context.World(caveWorld));
                    Lights.AddRange(Context.MaterialPorterLights);
                    Decals = [.. Context.MaterialPorterDecals];
                    Effects = [.. Context.MaterialPorterEffects];
                }
                break;
            }
            case EExportType.LegoOutfit:
            {
                // Material Porter fork: a LEGO figure, cooked (its Bake folder's meshes) or from its recipe
                Meshes.AddRange(Context.MaterialPorterFigure(asset, FacePicks));
                break;
            }
            case EExportType.Vehicle:
            {
                var blueprint = asset.Get<UBlueprintGeneratedClass>("VehicleActorClass");
                AddObjects(Context.Blueprint(blueprint));
                
                break;
            }
            case EExportType.SideKick:
            {
                var parts = asset.GetOrDefault<UObject[]>("CharacterParts", []);
                foreach (var part in parts)
                {
                    Meshes.AddIfNotNull(Context.CharacterPart(part));
                }
                
                break;
            }
            case EExportType.Kicks:
            {
                var parts = asset.GetOrDefault("CharacterParts", Array.Empty<UObject>());
                foreach (var part in parts)
                {
                    if (!part.TryGetValue(out FInstancedStruct[] dataList, "CosmeticPartDataList"))
                        continue;
                    
                    var skeletalMesh = dataList
                        .GetItemOrDefault<FStructFallback[]>("SkeletalMeshParameters")?
                        .FirstOrDefault(p => p.Get<string>("ParameterName").Equals("ShoeMesh"))?
                        .Get<FSoftObjectPath>("ParameterValue")
                        .LoadOrDefault<USkeletalMesh>();
                    
                    var material = dataList
                        .GetItemOrDefault<FStructFallback[]>("MaterialParameters")?
                        .FirstOrDefault(p => p.Get<string>("ParameterName").Equals("ShoeMaterial"))?
                        .Get<FSoftObjectPath>("ParameterValue")
                        .LoadOrDefault<UMaterialInterface>();

                    var exportMesh = Context.Mesh(skeletalMesh);
                    exportMesh?.Materials.AddIfNotNull(Context.Material(material, 0));
                    Meshes.AddIfNotNull(exportMesh);
                }
                
                break;
            }
        }
    }

    private void AddObjects(IEnumerable<ExportObject> objects)
    {
        foreach (var obj in objects)
        {
            if (obj is ExportMesh exportMesh)
            {
                Meshes.Add(exportMesh);
            }
            else if (obj is ExportLight exportLight)
            {
                Lights.Add(exportLight);
            }
        }
    }
    
    private void ExportStyles(UObject asset, IEnumerable<ExportStructStyle> styles)
    {
        var metaTagsToApply = new List<FGameplayTag>();
        var metaTagsToRemove = new List<FGameplayTag>();

        var styleDatas = styles.Select(data => data.StyleData).ToArray();
        foreach (var style in styleDatas)
        {
            var tags = style.GetOrDefault<FStructFallback?>("MetaTags");
            if (tags == null) continue;

            if (tags.TryGetValue<FGameplayTagContainer>(out var tagsToApply, "MetaTagsToApply"))
                metaTagsToApply.AddRange(tagsToApply.GameplayTags);

            if (tags.TryGetValue<FGameplayTagContainer>(out var tagsToRemove, "MetaTagsToRemove"))
                metaTagsToRemove.AddRange(tagsToRemove.GameplayTags);
        }

        var metaTags = new FGameplayTagContainer(metaTagsToApply.Where(tag => !metaTagsToRemove.Contains(tag)).ToArray());
        var itemStyles = asset.GetOrDefault("ItemVariants", Array.Empty<UObject>());
        var tagDrivenStyles = itemStyles.Where(style => style.ExportType.Equals("FortCosmeticLoadoutTagDrivenVariant"));
        foreach (var tagDrivenStyle in tagDrivenStyles)
        {
            var options = tagDrivenStyle.Get<FStructFallback[]>("Variants");
            foreach (var option in options)
            {
                var requiredConditions = option.Get<FStructFallback[]>("RequiredConditions");
                foreach (var condition in requiredConditions)
                {
                    var metaTagQuery = condition.Get<FGameplayTagQuery>("MetaTagQuery");
                    if (metaTags.MatchesQuery(metaTagQuery)) ExportStyleData(option);
                }
            }
        }

        foreach (var style in styleDatas) ExportStyleData(style);

        var colorStyles = styles.Where(style => style is ExportColorStyle)
                                                 .Select(style => (ExportColorStyle)style)
                                                 .ToArray();
        foreach (var colorStyle in colorStyles) OverrideParameters.AddRangeIfNotNull(Context.OverrideColors(colorStyle));
    }

    private void ExportStyleData(FStructFallback style)
    {
        var variantParts = style.GetOrDefault("VariantParts", Array.Empty<UObject>());
        foreach (var part in variantParts) OverrideMeshes.AddIfNotNull(Context.CharacterPart(part));

        var variantMaterials = style.GetOrDefault("VariantMaterials", Array.Empty<FStructFallback>());
        foreach (var material in variantMaterials) OverrideMaterials.AddIfNotNull(Context.OverrideMaterialSwap(material));

        var variantParameters = style.GetOrDefault("VariantMaterialParams", Array.Empty<FStructFallback>());
        foreach (var parameters in variantParameters) OverrideParameters.AddRangeIfNotNull(Context.OverrideParameters(parameters));
        
        var variantMeshes = style.GetOrDefault("VariantMeshes", Array.Empty<FStructFallback>());
        foreach (var mesh in variantMeshes)
        {
            var overrideMesh = mesh.GetOrDefault<USkeletalMesh>("OverrideMesh");
            OverrideMeshes.AddIfNotNull(Context.Mesh(overrideMesh));
        }

        var variantMorphTargets = style.GetOrDefault("MorphTargets", Array.Empty<FStructFallback>());
        foreach (var morphTarget in variantMorphTargets) OverrideMorphTargets.AddIfNotNull(Context.OverrideMorphTargets(morphTarget));
    }
}
