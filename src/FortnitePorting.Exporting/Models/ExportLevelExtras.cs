using System.Collections.Generic;
using CUE4Parse.UE4.Objects.Core.Math;

namespace FortnitePorting.Exporting.Models;

/// <summary>
/// Material Porter fork: a decal a level places (a DecalComponent: a DecalActor's, a Blueprint's). Location, Rotation and
/// Scale are the component's world transform as the meshes' are; it projects along its X axis, over a box of DecalSize
/// (half extents, cm) times Scale.
/// </summary>
public record ExportDecal : ExportObject
{
    /// <summary>cm, before Scale: X the depth along the projection, Y and Z the footprint (UE's default is 128, 256, 256).</summary>
    public FVector DecalSize = new(128, 256, 256);
    public int SortOrder;
    public float FadeScreenSize = 0.01f;
    /// <summary>The decal material, as a mesh slot's material entry (a dynamic instance's values over it as MPValues).</summary>
    public ExportMaterial? Material;
}

/// <summary>
/// Material Porter fork: a particle system a level places (a NiagaraComponent: a NiagaraActor's, a Blueprint's). Location,
/// Rotation and Scale are the component's world transform as the meshes' are.
/// </summary>
public record ExportEffect : ExportObject
{
    /// <summary>The NiagaraSystem's object path ("/Game/.../NS_X.NS_X", or an island's "/guid/.../NS_X.NS_X").</summary>
    public string System = string.Empty;
    /// <summary>The user parameters the component overrides, by name (as the system names them: "x", not "User.x"): a float, an int, a bool, or floats (a vector, a colour); may be empty.</summary>
    public Dictionary<string, object> Parameters = [];
}
