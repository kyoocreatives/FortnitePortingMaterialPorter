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

        public FDistanceFieldVolumeData5(FAssetArchive Ar) : this(Ar, null) { }

        // Material Porter fork: a build between engine releases mixes their layouts (Fortnite 28.00: 5.3's
        // double-precision bounds, 5.4's single-precision mips). Each combination is read, the engine version's
        // first, and kept when the whole block reads plausibly and what follows it does too: the LODs after it
        // (lodsAfter) - their flags, and when none has a distance field, the render data's bounds, a real sphere (a
        // block of zeros reads the same in every layout: the bounds after it tell). When none does, the engine
        // version's is read as before.
        public FDistanceFieldVolumeData5(FAssetArchive Ar, int? lodsAfter)
        {
            var singleBounds = Ar.Game >= GAME_UE5_4 || Ar.Game is GAME_Highguard;
            var singleMips = Ar.Game >= GAME_UE5_4;
            var start = Ar.Position;
            if (lodsAfter is { } after && Ar.Game is not (GAME_TheFinals or GAME_ArcRaiders))
            {
                foreach (var (bounds, mips) in new[] { (singleBounds, singleMips), (!singleBounds, singleMips), (singleBounds, !singleMips), (!singleBounds, !singleMips) })
                {
                    Ar.Position = start;
                    try
                    {
                        if (Read(Ar, bounds, mips, strict: true) && FollowedRight(Ar, after))
                            return;
                    }
                    catch
                    {
                        // (not this layout)
                    }
                }
                Ar.Position = start;
            }
            Read(Ar, singleBounds, singleMips, strict: false);
        }

        private bool Read(FAssetArchive Ar, bool singleBounds, bool singleMips, bool strict)
        {
            LocalSpaceMeshBounds = ReadBounds(Ar, singleBounds);
            if (strict && !IsPlausible(LocalSpaceMeshBounds)) return false;
            if (strict && !IsBool(Ar)) return false;
            bMostlyTwoSided = Ar.ReadBoolean();
            var mips = Ar.Game switch
            {
                GAME_TheFinals or GAME_ArcRaiders => 2,
                _ => DistanceField.NumMips
            };
            Mips = Ar.ReadArray(mips, () => new FSparseDistanceFieldMip(Ar, singleMips));
            if (strict && !Mips.All(m => m.IsPlausible())) return false;
            if (strict && (Ar.Position + 4 > Ar.Length || PeekInt(Ar) is var count && (count < 0 || Ar.Position + 4 + count > Ar.Length))) return false;
            AlwaysLoadedMip = Ar.ReadArray<byte>();
            if (Ar.Game is GAME_TheFinals or GAME_ArcRaiders)
                Ar.Position += 6;
            StreamableMips = new FByteBulkData(Ar);
            return true;
        }

        private static int PeekInt(FAssetArchive Ar)
        {
            var value = Ar.Read<int>();
            Ar.Position -= 4;
            return value;
        }

        private static bool IsPlausible(FBox box)
        {
            static bool Ok(double d) => double.IsFinite(d) && Math.Abs(d) < 1e8;
            return box.IsValid is 0 or 1 && Ok(box.Min.X) && Ok(box.Min.Y) && Ok(box.Min.Z) && Ok(box.Max.X) && Ok(box.Max.Y) && Ok(box.Max.Z)
                   && box.Min.X <= box.Max.X && box.Min.Y <= box.Max.Y && box.Min.Z <= box.Max.Z;
        }

        // the LODs after this one: each one's flag; when none has a distance field, the render data's bounds next
        private static bool FollowedRight(FAssetArchive Ar, int lodsAfter)
        {
            var saved = Ar.Position;
            try
            {
                for (var i = 0; i < lodsAfter; i++)
                {
                    if (Ar.Position + 4 > Ar.Length) return false;
                    var flag = Ar.Read<int>();
                    if (flag is not (0 or 1)) return false;
                    if (flag == 1) return true;     // (its own distance field follows: not walked)
                }
                return IsBoundsSphere(Ar);
            }
            finally
            {
                Ar.Position = saved;
            }
        }

        // the render data's bounds that follow the last LOD's distance field: a real mesh's (not zeros), its sphere
        // around its box
        private static bool IsBoundsSphere(FAssetArchive Ar)
        {
            var saved = Ar.Position;
            try
            {
                var sphere = new FBoxSphereBounds(Ar);
                static bool Ok(double d) => double.IsFinite(d) && Math.Abs(d) < 1e8;
                return Ok(sphere.Origin.X) && Ok(sphere.Origin.Y) && Ok(sphere.Origin.Z)
                       && Ok(sphere.BoxExtent.X) && sphere.BoxExtent.X >= 0 && Ok(sphere.BoxExtent.Y) && sphere.BoxExtent.Y >= 0
                       && Ok(sphere.BoxExtent.Z) && sphere.BoxExtent.Z >= 0 && Ok(sphere.SphereRadius) && sphere.SphereRadius > 0
                       && sphere.SphereRadius >= Math.Max(sphere.BoxExtent.X, Math.Max(sphere.BoxExtent.Y, sphere.BoxExtent.Z)) * 0.99
                       && sphere.SphereRadius <= sphere.BoxExtent.Size() * 1.01 + 1e-3;
            }
            catch
            {
                return false;
            }
            finally
            {
                Ar.Position = saved;
            }
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
