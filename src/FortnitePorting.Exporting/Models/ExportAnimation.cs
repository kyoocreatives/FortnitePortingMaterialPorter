using System.Collections.Generic;
using CUE4Parse.UE4.Assets.Exports.Animation;
using CUE4Parse.UE4.Objects.Core.Math;
using Newtonsoft.Json;

namespace FortnitePorting.Exporting.Models;

public class ExportAnimSection
{
    public string Path;
    public string Name;
    public float Time;
    public float Length;
    public float LinkValue;
    public bool Loop;
    // MP: an emote section's curve key interpolation, a letter per key (C constant, L linear, Q cubic),
    // one letter when all keys agree
    public Dictionary<string, string>? MPCurveModes;

    [JsonIgnore] public UAnimSequence AssetRef;
}

public class ExportProp
{
    public ExportMesh Mesh;
    public List<ExportAnimSection> AnimSections;
    public string SocketName;
    public FVector LocationOffset;
    public FRotator RotationOffset;
    public FVector Scale;
}