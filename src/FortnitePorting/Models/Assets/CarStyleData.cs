namespace FortnitePorting.Models.Assets;

/// <summary>A car channel's option: which channel, which option.</summary>
public partial class CarStyleData : BaseStyleData
{
    public int Channel { get; }
    public int Option { get; }

    public CarStyleData(string name, int channel, int option)
    {
        StyleName = name;
        Channel = channel;
        Option = option;
        ShowName = true;
    }
}
