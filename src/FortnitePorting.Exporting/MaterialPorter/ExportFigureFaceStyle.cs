using FortnitePorting.Exporting.Styles;

namespace FortnitePorting.Exporting.MaterialPorter;

/// <summary>A LEGO figure's expression pick: a face feature ("Mouth", "Eyes", "Brows") and its rig pose.</summary>
public class ExportFigureFaceStyle : ExportStyleBase
{
    public string Feature = "";
    public int Pose;
}
