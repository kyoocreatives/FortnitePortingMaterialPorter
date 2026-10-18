using System;
using System.Collections.Generic;
using CUE4Parse.UE4.Assets.Exports;

namespace FortnitePorting.Exporting.MaterialPorter;

/// <summary>What a cook leaves out of a level: editor-only components, which an uncooked package (an editor map's actor) still holds.</summary>
public static class EditorOnly
{
    static readonly HashSet<string> Types = new(StringComparer.Ordinal)
        { "BillboardComponent", "ArrowComponent", "NavLinkRenderingComponent", "DrawFrustumComponent" };

    /// <summary>flag(object, name): a bool property through the object's template chain.</summary>
    public static bool Component(UObject c, Func<UObject, string, bool> flag) =>
        Types.Contains(c.ExportType)
        || c.ExportType.Contains("EditOnly", StringComparison.Ordinal)     // UEFN's CreativeEditOnly* (device visuals)
        || c.Name.Contains("EditorOnly", StringComparison.Ordinal)
        || c.Name.EndsWith("_EDITOR", StringComparison.Ordinal)
        || flag(c, "bIsEditorOnly");
}
