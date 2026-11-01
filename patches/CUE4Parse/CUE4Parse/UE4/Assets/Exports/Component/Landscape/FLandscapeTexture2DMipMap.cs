using CUE4Parse.UE4.Assets.Objects;
using CUE4Parse.UE4.Assets.Readers;
using CUE4Parse.UE4.Versions;

namespace CUE4Parse.UE4.Assets.Exports.Component.Landscape;

public class FLandscapeTexture2DMipMap
{
    public int SizeX;
    public int SizeY;
    public bool bCompressed;
    public readonly FByteBulkData BulkData;

    public FLandscapeTexture2DMipMap(FAssetArchive Ar)
    {
        SizeX = Ar.Read<int>();
        SizeY = Ar.Read<int>();
        bCompressed = Ar.ReadBoolean();
        if (Ar.Game >= EGame.GAME_UE6_0) Ar.Position += 1;      // 0 on the first mip, 1 on the inline ones

        BulkData = new FByteBulkData(Ar);
    }
}
