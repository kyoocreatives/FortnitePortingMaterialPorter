using System.Collections.Generic;
using CUE4Parse.UE4.Objects.Core.Math;
using FortnitePorting.Exporting.Models;

namespace FortnitePorting.Exporting.MaterialPorter;

/// <summary>A skeleton's socket: the bone it is on, and where it sits on it.</summary>
public class ExportSocket
{
    public string Bone = "";
    public FVector Location;
    public FRotator Rotation;
    public FVector Scale = FVector.OneVector;
}

/// <summary>A particle effect an animation plays: its socket and offsets, start times, and per start how long a timed notify keeps it going (0: plays out by itself).</summary>
public class ExportAnimEffect
{
    public ExportMesh Effect = null!;
    public string? SocketName;
    public FVector LocationOffset;
    public FRotator RotationOffset;
    public FVector Scale = FVector.OneVector;
    public List<float> Times = [];
    public List<float> Durations = [];
}
