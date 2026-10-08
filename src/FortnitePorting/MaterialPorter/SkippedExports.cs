using CUE4Parse.UE4.Assets;
using CUE4Parse.UE4.Assets.Exports.Component;
using CUE4Parse.UE4.Assets.Readers;

namespace FortnitePorting.MaterialPorter;

/// <summary>Exports nothing here reads, left unread.</summary>
// A UEFN island cooked for an earlier game version carries landscape collision components whose unversioned
// properties no longer match the current mappings: they mis-parse into garbage array counts that allocate
// gigabytes and take minutes per level (Cristaline: 49 GB in a minute, vs 28 s for its export). The world export
// only reads the landscape's height and weight maps, never its collision.
public static class SkippedExports
{
    public static void Register()
    {
        ObjectTypeRegistry.RegisterClass("LandscapeHeightfieldCollisionComponent", typeof(USkippedSceneComponent));
        ObjectTypeRegistry.RegisterClass("LandscapeMeshCollisionComponent", typeof(USkippedSceneComponent));
    }
}

/// <summary>A scene component whose serialized data is skipped whole: it stays a USceneComponent for code that walks an actor's components, with no properties.</summary>
public class USkippedSceneComponent : USceneComponent
{
    public override void Deserialize(FAssetArchive Ar, long validPos)
    {
        Ar.Position = validPos;
    }
}
