using System;
using Avalonia.Media.Imaging;
using Avalonia.Platform;
using FortnitePorting.Extensions;

namespace FortnitePorting.MaterialPorter;

/// <summary>An export type's icon (Assets/FN/&lt;type&gt;.png), or the plain icon for a type without one.</summary>
// A missing resource threw while the Assets page was being built, so the page never came up
// (the Effects and Contrails tabs had no icon).
public static class TabIcons
{
    private const string Plain = "avares://FortnitePorting/Assets/FN/Misc.png";

    public static Bitmap Of(EExportType type)
    {
        var own = $"avares://FortnitePorting/Assets/FN/{type}.png";
        return ImageExtensions.AvaresBitmap(AssetLoader.Exists(new Uri(own)) ? own : Plain);
    }
}
