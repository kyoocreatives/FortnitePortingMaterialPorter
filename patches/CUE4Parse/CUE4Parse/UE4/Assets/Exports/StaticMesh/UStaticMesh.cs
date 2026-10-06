using CUE4Parse.UE4.Assets.Readers;
using CUE4Parse.UE4.Objects.Core.Math;
using CUE4Parse.UE4.Objects.Core.Misc;
using CUE4Parse.UE4.Objects.Engine;
using CUE4Parse.UE4.Objects.UObject;
using CUE4Parse.UE4.Versions;
using Newtonsoft.Json;

namespace CUE4Parse.UE4.Assets.Exports.StaticMesh;

public class UStaticMesh : UObject
{
    public bool bCooked { get; private set; }
    public bool HasTangents { get; private set; }
    public FPackageIndex BodySetup { get; private set; }
    public FPackageIndex NavCollision { get; private set; }
    public FGuid LightingGuid { get; private set; }
    public FPackageIndex[] Sockets { get; private set; } // UStaticMeshSocket[]
    public FStaticMeshRenderData? RenderData { get; private set; }
    public FPackageIndex?[] Materials { get; private set; } = []; // UMaterialInterface[]
    public FStaticMaterial[] StaticMaterials { get; private set; } = [];
    public int LODForCollision { get; private set; }

    public override void Deserialize(FAssetArchive Ar, long validPos)
    {
        if(Ar.Game == GAME_WorldofJadeDynasty) Ar.Position += 12;
        base.Deserialize(Ar, validPos);
        LODForCollision = GetOrDefault(nameof(LODForCollision), 0);

        var stripDataFlags = new FStripDataFlags(Ar);
        bCooked = Ar.Ver >= EUnrealEngineObjectUE4Version.STATIC_MESH_REFACTOR && Ar.ReadBoolean();
        HasTangents = Ar.Ver >= EUnrealEngineObjectUE3Version.STATICMESH_VERTEXBUFFER_MERGE;

        if (Ar.Game == GAME_WutheringWaves && GetOrDefault<bool>("bUseStandaloneBodySetup"))
            BodySetup = GetOrDefault<FPackageIndex>("StandaloneBodySetup");
        else
            BodySetup = new FPackageIndex(Ar);

        if (Ar.Versions["StaticMesh.HasNavCollision"])
            NavCollision = new FPackageIndex(Ar);

        if (!stripDataFlags.IsEditorDataStripped())
        {
            if (Ar.Ver < EUnrealEngineObjectUE4Version.DEPRECATED_STATIC_MESH_THUMBNAIL_PROPERTIES_REMOVED)
            {
                 var dummyThumbnailAngle = new FRotator(Ar);
                 if (Ar.Ver >= EUnrealEngineObjectUE3Version.STATICMESH_THUMBNAIL_DISTANCE)
                 {
                     var dummyThumbnailDistance = Ar.Read<float>();
                 }
            }

            if (FRenderingObjectVersion.Get(Ar) < FRenderingObjectVersion.Type.DeprecatedHighResSourceMesh)
            {
                var Deprecated_HighResSourceMeshName = Ar.ReadFString();
                var Deprecated_HighResSourceMeshCRC = Ar.Read<uint>();
            }
        }

        if (Ar.Ver >= EUnrealEngineObjectUE3Version.INTEGRATED_LIGHTMASS)
        {
            LightingGuid = Ar.Read<FGuid>(); // LocalLightingGuid
        }
        else
        {
            LightingGuid = FGuid.Random();
        }

        if (Ar.Ver > EUnrealEngineObjectUE4Version.STATIC_MESH_SOCKETS)
        {
            Sockets = Ar.ReadArray(() => new FPackageIndex(Ar));
        }

        if (!Ar.IsFilterEditorOnly)
        {
            return; // so it doesn't throw
        }

        // https://github.com/EpicGames/UnrealEngine/blob/ue5-main/Engine/Source/Runtime/Engine/Private/StaticMesh.cpp#L6701
        if (bCooked)
        {
            RenderData = Ar.Game switch
            {
                GAME_GameForPeace => new GFPStaticMeshRenderData(Ar, GetOrDefault<bool>("bIsStreamable")),
                GAME_WeHappyFew => new GFPStaticMeshRenderData(Ar, true),
                _ => RenderData = new FStaticMeshRenderData(Ar)
            };
        }

        if (Ar.Game == GAME_WutheringWaves && GetOrDefault<bool>("bUseKuroLODDistance") && Ar.ReadBoolean())
        {
            Ar.Position += 64; // 8 per-platform floats
        }

        if (Ar.Game is GAME_RocoKingdomWorld or GAME_SilverPalace) Ar.Position += 4;

        if (bCooked && Ar.Game is >= GAME_UE4_20 and < GAME_UE5_0 && Ar.Game != GAME_DreamStar) // DS removed this for some reason
        {
            var bHasOccluderData = Ar.ReadBoolean();
            if (bHasOccluderData)
            {
                switch (Ar.Game)
                {
                    case GAME_CrystalOfAtlan:
                    case GAME_FragPunk:
                    case GAME_RocoKingdomWorld:
                        if (Ar.Game is GAME_FragPunk && !Ar.ReadBoolean()) break;
                        Ar.SkipMultipleBulkArrayData(3);
                        break;
                    case GAME_Farlight84:
                    {
                        Ar.SkipMultipleBulkArrayData(2);
                        var count = Ar.Read<int>();
                        for (var i = 0; i < count; i++)
                            Ar.SkipMultipleBulkArrayData(2);
                        break;
                    }
                    case GAME_NeedForSpeedMobile:
                        Ar.SkipMultipleBulkArrayData(3);
                        Ar.Position += 4;
                        var count1 = Ar.Read<int>();
                        for (var i = 0; i < count1; i++)
                        {
                            Ar.Position += 4;
                            Ar.SkipMultipleFixedArrays(2, 4);
                        }
                        break;
                    case GAME_HonorofKingsWorld:
                        Ar.SkipBulkArrayData();
                        break;
                    case GAME_ArenaBreakoutMobile:
                    case GAME_ValorantSource:
                        Ar.SkipMultipleBulkArrayData(2);
                        break;
                    default:
                        Ar.SkipFixedArray(12); // Vertices
                        Ar.SkipFixedArray(2); // Indices
                        break;
                }
            }
        }

        switch (Ar.Game)
        {
            case GAME_FateTrigger or GAME_GhostsofTabor or GAME_Aion2:
                Ar.Position += 4;
                break;
            case GAME_TheFinals or GAME_ArcRaiders when Ar.ReadBoolean():
                Ar.SkipMultipleBulkArrayData(5);
                break;
            case GAME_ValorantSource when Ar.ReadBoolean():
                var count = Ar.Read<int>();
                for (var i = 0; i < count; i++)
                {
                    Ar.Position += 64;
                    Ar.SkipFixedArray(16);
                }
                Ar.SkipFixedArray(12);
                break;
            case GAME_PUBGLite when Ar.ReadBoolean():
                Ar.SkipMultipleBulkArrayData(2);
                break;
        }

        // (Ar.Ver >= EUnrealEngineObjectUE4Version.SPEEDTREE_STATICMESH), but we check UE version for Materials
        if (Ar.Game >= GAME_UE4_14)
        {
            // Material Porter fork: a build between engine releases (Fortnite 28.00) ends its render data a few bytes
            // off what its engine version reads: the flag, then the materials, are found where they parse
            if (Ar.Game >= GAME_UE5_0 && !IsBoolAt(Ar, Ar.Position) && FindMaterials(Ar, validPos) is { } found)
                Ar.Position = found;
            var bHasSpeedTreeWind = Ar.ReadBoolean();
            if (bHasSpeedTreeWind)
            {
                Ar.Position = validPos;
                // return;
            }

            if (FEditorObjectVersion.Get(Ar) >= FEditorObjectVersion.Type.RefactorMeshEditorMaterials)
            {
                // UE4.14+ - "Materials" are deprecated, added StaticMaterials
                StaticMaterials = bHasSpeedTreeWind ? GetOrDefault("StaticMaterials", Array.Empty<FStaticMaterial>()) : Ar.ReadArray(() => new FStaticMaterial(Ar));
            }
        }
        else if (TryGetValue(out FPackageIndex[] materials, "Materials"))
        {
            StaticMaterials = new FStaticMaterial[materials.Length];
            for (var i = 0; i < materials.Length; i++)
            {
                StaticMaterials[i] = new FStaticMaterial(materials[i]);
            }
        }

        if (Ar.Game is GAME_TamasShadowveil)
            StaticMaterials = Ar.ReadArray(() => new FStaticMaterial(Ar));

        Materials = new FPackageIndex?[StaticMaterials.Length];
        for (var i = 0; i < Materials.Length; i++)
        {
            Materials[i] = StaticMaterials[i].MaterialInterface;
        }

        Ar.Position += Ar.Game switch
        {
            GAME_OutlastTrials => 1,
            GAME_Farlight84 or GAME_DuneAwakening => 4,
            GAME_DaysGone => Ar.Read<int>() * 4 + 4,
            _ => 0
        };
    }

    private static bool IsBoolAt(FAssetArchive Ar, long position)
    {
        if (position < 0 || position + 4 > Ar.Length) return false;
        var saved = Ar.Position;
        Ar.Position = position;
        var value = Ar.Read<int>();
        Ar.Position = saved;
        return value is 0 or 1;
    }

    // Material Porter fork: within 8 bytes either way, the spot where a false SpeedTree flag is followed by a material
    // array that reads to plausible package indices and ends inside the export
    private static long? FindMaterials(FAssetArchive Ar, long validPos)
    {
        var start = Ar.Position;
        var owner = Ar.Owner;
        long? found = null;
        foreach (var delta in new[] { -2, 2, -1, 1, -3, 3, -4, 4, -5, 5, -6, 6, -7, 7, -8, 8 })
        {
            var at = start + delta;
            if (!IsBoolAt(Ar, at)) continue;
            try
            {
                Ar.Position = at;
                if (Ar.Read<int>() != 0) continue;     // (a SpeedTree mesh's materials aren't read from here)
                var count = Ar.Read<int>();
                if (count is < 1 or > 256) continue;
                var ok = true;
                for (var i = 0; i < count && ok; i++)
                {
                    var material = new FStaticMaterial(Ar);
                    var index = material.MaterialInterface?.Index ?? 0;
                    ok = owner is null || index == 0 || (index < 0 ? -index <= owner.ImportMapLength : index <= owner.ExportMapLength);
                }
                if (ok && Ar.Position <= validPos)
                {
                    found = at;
                    break;
                }
            }
            catch
            {
                // (not here)
            }
        }
        Ar.Position = start;
        return found;
    }

    protected internal override void WriteJson(JsonWriter writer, JsonSerializer serializer)
    {
        base.WriteJson(writer, serializer);

        writer.WritePropertyName("BodySetup");
        serializer.Serialize(writer, BodySetup);

        writer.WritePropertyName("NavCollision");
        serializer.Serialize(writer, NavCollision);

        writer.WritePropertyName("LightingGuid");
        serializer.Serialize(writer, LightingGuid);

        writer.WritePropertyName("RenderData");
        serializer.Serialize(writer, RenderData);
    }
}
