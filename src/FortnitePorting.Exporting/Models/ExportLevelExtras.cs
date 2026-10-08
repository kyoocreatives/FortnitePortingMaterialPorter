using System.Collections.Generic;
using CUE4Parse.UE4.Objects.Core.Math;

namespace FortnitePorting.Exporting.Models;

/// <summary>A decal a level places (DecalComponent). Transform is the world transform like meshes'; projects along X over a box of DecalSize (half extents, cm) times Scale.</summary>
public record ExportDecal : ExportObject
{
    /// <summary>cm, before Scale: X the depth along the projection, Y and Z the footprint (UE's default is 128, 256, 256).</summary>
    public FVector DecalSize = new(128, 256, 256);
    public int SortOrder;
    public float FadeScreenSize = 0.01f;
    /// <summary>The decal material as a mesh slot entry (dynamic instance values as MPValues).</summary>
    public ExportMaterial? Material;
}

/// <summary>A particle system a level places (NiagaraComponent). Transform is the world transform like meshes'.</summary>
public record ExportEffect : ExportObject
{
    /// <summary>The NiagaraSystem's object path, e.g. "/Game/.../NS_X.NS_X".</summary>
    public string System = string.Empty;
    /// <summary>User parameter overrides by name ("x", not "User.x"): float, int, bool, or floats (vector, colour); may be empty.</summary>
    public Dictionary<string, object> Parameters = [];
}
