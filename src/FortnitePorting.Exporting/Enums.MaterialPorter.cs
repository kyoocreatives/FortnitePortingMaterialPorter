using System.ComponentModel;

namespace FortnitePorting;

// Largest texture size exported, taken from the smaller mips (pixels)
public enum ETextureSizeCap
{
    [Description("Full Size")]
    Full = 0,

    [Description("4096")]
    Size4096 = 4096,

    [Description("2048")]
    Size2048 = 2048,

    [Description("1024")]
    Size1024 = 1024,

    [Description("512")]
    Size512 = 512,

    [Description("256")]
    Size256 = 256
}
