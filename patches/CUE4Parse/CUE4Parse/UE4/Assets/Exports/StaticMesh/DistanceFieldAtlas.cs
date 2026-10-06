using System;
using System.Linq;
using CUE4Parse.UE4.Assets.Objects;
using CUE4Parse.UE4.Assets.Readers;
using CUE4Parse.UE4.Objects.Core.Math;
using CUE4Parse.UE4.Readers;
using CUE4Parse.UE4.Versions;

namespace CUE4Parse.UE4.Assets.Exports.StaticMesh
{
    public static class DistanceField
    {
        public const int NumMips = 3;
    }

    public class FSparseDistanceFieldMip
    {
        public FIntVector IndirectionDimensions;
        public int NumDistanceFieldBricks;
        public FVector VolumeToVirtualUVScale;
        public FVector VolumeToVirtualUVAdd;
        public FVector2D DistanceFieldToVolumeScaleBias;
        public uint BulkOffset;
        public uint BulkSize;

        public FSparseDistanceFieldMip(FArchive Ar) : this(Ar, Ar.Game >= GAME_UE5_4) { }

        // Material Porter fork: in the layout given (single-precision vectors: UE 5.4's)
        public FSparseDistanceFieldMip(FArchive Ar, bool singlePrecision)
        {
            IndirectionDimensions = Ar.Read<FIntVector>();
            NumDistanceFieldBricks = Ar.Read<int>();

            if (singlePrecision)
            {
                VolumeToVirtualUVScale = Ar.Read<FVector>();
                VolumeToVirtualUVAdd = Ar.Read<FVector>();
                DistanceFieldToVolumeScaleBias = Ar.Read<FVector2D>();
            }
            else
            {
                VolumeToVirtualUVScale = new FVector(Ar);
                VolumeToVirtualUVAdd = new FVector(Ar);
                DistanceFieldToVolumeScaleBias = new FVector2D(Ar);
            }

            BulkOffset = Ar.Read<uint>();
            BulkSize = Ar.Read<uint>();
        }

        // Material Porter fork: what a mip read in its own layout looks like
        public bool IsPlausible()
        {
            static bool Small(double d) => double.IsFinite(d) && Math.Abs(d) < 1e6;
            return IndirectionDimensions is { X: >= 0 and <= 4096, Y: >= 0 and <= 4096, Z: >= 0 and <= 4096 }
                   && NumDistanceFieldBricks >= 0
                   && NumDistanceFieldBricks <= (long) IndirectionDimensions.X * IndirectionDimensions.Y * IndirectionDimensions.Z
                   && Small(VolumeToVirtualUVScale.X) && Small(VolumeToVirtualUVScale.Y) && Small(VolumeToVirtualUVScale.Z)
                   && Small(VolumeToVirtualUVAdd.X) && Small(VolumeToVirtualUVAdd.Y) && Small(VolumeToVirtualUVAdd.Z)
                   && Small(DistanceFieldToVolumeScaleBias.X) && Small(DistanceFieldToVolumeScaleBias.Y)
                   && BulkSize < 1u << 30 && BulkOffset < 1u << 30;
        }
    }

    public class FDistanceFieldVolumeData
    {
        public ushort[] DistanceFieldVolume; // LegacyIndices
        public FIntVector Size;
        public FBox LocalBoundingBox;
        public bool bMeshWasClosed;
        public bool bBuiltAsIfTwoSided;
        public bool bMeshWasPlane;

        public byte[] CompressedDistanceFieldVolume;
        public FVector2D DistanceMinMax;

        public FDistanceFieldVolumeData(FArchive Ar)
        {
            if (Ar.Game >= GAME_UE4_16)
            {
                CompressedDistanceFieldVolume = Ar.ReadArray<byte>();
                Size = Ar.Read<FIntVector>();
                LocalBoundingBox = Ar.Read<FBox>();
                DistanceMinMax = Ar.Read<FVector2D>();
                bMeshWasClosed = Ar.ReadBoolean();
                bBuiltAsIfTwoSided = Ar.ReadBoolean();
                bMeshWasPlane = Ar.Game != GAME_FragPunk && Ar.ReadBoolean();
                DistanceFieldVolume = [];
                if (Ar.Game == GAME_Psychonauts2) Ar.Position += 22;
            }
            else
            {
                DistanceFieldVolume = Ar.ReadArray<ushort>();
                Size = Ar.Read<FIntVector>();
                LocalBoundingBox = Ar.Read<FBox>();
                bMeshWasClosed = Ar.ReadBoolean();
                bBuiltAsIfTwoSided = Ar.Ver >= EUnrealEngineObjectUE4Version.RENAME_CROUCHMOVESCHARACTERDOWN && Ar.ReadBoolean();
                bMeshWasPlane = Ar.Ver >= EUnrealEngineObjectUE4Version.DEPRECATE_UMG_STYLE_ASSETS && Ar.ReadBoolean();
                CompressedDistanceFieldVolume = [];
                DistanceMinMax = new FVector2D(0f, 0f);
            }
        }
    }

    public class FDistanceFieldVolumeData5
    {
        /** Local space bounding box of the distance field volume. */
        public FBox LocalSpaceMeshBounds;

        /** Whether most of the triangles in the mesh used a two-sided material. */
        public bool bMostlyTwoSided;

        public FSparseDistanceFieldMip[] Mips;

        // Lowest resolution mip is always loaded so we always have something
        public byte[] AlwaysLoadedMip;

        // Remaining mips are streamed
        public FByteBulkData StreamableMips;

        public FDistanceFieldVolumeData5(FAssetArchive Ar)
        {
            // Material Porter fork: a build between engine releases mixes their layouts (Fortnite 28.00: 5.3's
            // double-precision bounds, 5.4's single-precision mips). Each part is read in its engine version's
            // layout and, when that doesn't look right, in the other one; one neither fits is read as before.
            var singleBounds = Ar.Game >= GAME_UE5_4 || Ar.Game is GAME_Highguard;
            var start = Ar.Position;
            LocalSpaceMeshBounds = ReadBounds(Ar, singleBounds);
            if (!IsBool(Ar))
            {
                Ar.Position = start;
                LocalSpaceMeshBounds = ReadBounds(Ar, !singleBounds);
                if (!IsBool(Ar))
                {
                    Ar.Position = start;
                    LocalSpaceMeshBounds = ReadBounds(Ar, singleBounds);
                }
            }
            bMostlyTwoSided = Ar.ReadBoolean();
            var mips = Ar.Game switch
            {
                GAME_TheFinals or GAME_ArcRaiders => 2,
                _ => DistanceField.NumMips
            };
            var singleMips = Ar.Game >= GAME_UE5_4;
            start = Ar.Position;
            Mips = Ar.ReadArray(mips, () => new FSparseDistanceFieldMip(Ar, singleMips));
            if (!Mips.All(m => m.IsPlausible()))
            {
                var end = Ar.Position;
                Ar.Position = start;
                var other = Ar.ReadArray(mips, () => new FSparseDistanceFieldMip(Ar, !singleMips));
                if (other.All(m => m.IsPlausible())) Mips = other;
                else Ar.Position = end;
            }
            AlwaysLoadedMip = Ar.ReadArray<byte>();
            if (Ar.Game is GAME_TheFinals or GAME_ArcRaiders)
                Ar.Position += 6;
            StreamableMips = new FByteBulkData(Ar);
        }

        private static FBox ReadBounds(FAssetArchive Ar, bool singlePrecision) => singlePrecision ? Ar.Read<FBox>() : new FBox(Ar);

        // the bool that follows the bounds: 0 or 1 when they were read in the right layout
        private static bool IsBool(FAssetArchive Ar)
        {
            if (Ar.Position + 4 > Ar.Length) return false;
            var value = Ar.Read<int>();
            Ar.Position -= 4;
            return value is 0 or 1;
        }
    }
}
