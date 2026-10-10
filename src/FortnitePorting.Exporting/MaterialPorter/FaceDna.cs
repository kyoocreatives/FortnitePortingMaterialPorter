using CUE4Parse.UE4.Assets;
using CUE4Parse.UE4.Assets.Exports;
using CUE4Parse.UE4.Assets.Readers;

namespace FortnitePorting.Exporting.MaterialPorter;

/// <summary>A 3L head's DNA asset (Fortnite's export class "DNA") kept as bytes: the plugin reads its RigLogic data.</summary>
// Fortnite cooks a 4-byte prefix before the DNA and RigLogic's runtime dump after it, which CUE4Parse's UDNAAsset
// doesn't read; the plugin's riglogic_read finds both.
public class UFortFaceDna : UObject
{
    public byte[] RawData = [];

    public override void Deserialize(FAssetArchive Ar, long validPos)
    {
        base.Deserialize(Ar, validPos);
        RawData = Ar.ReadBytes((int) (validPos - Ar.Position));
    }

    public static void Register() => ObjectTypeRegistry.RegisterClass("DNA", typeof(UFortFaceDna));
}
