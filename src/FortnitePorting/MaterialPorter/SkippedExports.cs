using CUE4Parse.UE4.Assets;
using CUE4Parse.UE4.Assets.Exports.Component;
using CUE4Parse.UE4.Assets.Readers;

namespace FortnitePorting.MaterialPorter;

/// <summary>
/// Exports nothing here reads, left unread. A UEFN island cooked for an earlier game version
/// carries landscape collision components whose unversioned properties no longer match the
/// current mappings: each one mis-parsed into garbage array counts, which allocated gigabytes
/// and took minutes per level (Cristaline: 49 GB in a minute, where its export took 28 s).
/// The world export only ever reads the landscape's height and weight maps, never its collision.
/// </summary>
public static class SkippedExports
{
    public static void Register()
    {
        ObjectTypeRegistry.RegisterClass("LandscapeHeightfieldCollisionComponent", typeof(USkippedSceneComponent));
        ObjectTypeRegistry.RegisterClass("LandscapeMeshCollisionComponent", typeof(USkippedSceneComponent));
    }
}

/// <summary>A scene component whose serialized data is skipped whole: it stays a USceneComponent for any
/// code that walks an actor's components, with no properties.</summary>
public class USkippedSceneComponent : USceneComponent
{
    public override void Deserialize(FAssetArchive Ar, long validPos)
    {
        Ar.Position = validPos;
    }
}
