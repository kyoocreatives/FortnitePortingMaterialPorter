namespace FortnitePorting.Models.Assets;

/// <summary>A LEGO figure's expression option: a face feature's rig pose, -1 for the figure's own.</summary>
public partial class FigureFaceStyleData : BaseStyleData
{
    public string Feature { get; }
    public int Pose { get; }

    public FigureFaceStyleData(string name, string feature, int pose)
    {
        StyleName = name;
        Feature = feature;
        Pose = pose;
        ShowName = true;
    }
}
