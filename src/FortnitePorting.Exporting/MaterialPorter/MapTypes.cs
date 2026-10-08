#nullable disable
using System.Globalization;
using System.Collections.Concurrent;
using System.Numerics;
using global::CUE4Parse.UE4.Assets.Exports;
using global::CUE4Parse.UE4.Assets.Exports.Component.StaticMesh;
using global::CUE4Parse.UE4.Objects.Core.Math;
using global::CUE4Parse.UE4.Objects.Core.Misc;
using global::CUE4Parse.UE4.Assets.Exports.Actor;
using global::CUE4Parse.UE4.Assets.Objects;
using global::CUE4Parse.UE4.Objects.Engine;
using global::CUE4Parse.UE4.Objects.UObject;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace FortnitePorting.Exporting.MaterialPorter;


/// <summary>A mesh a map places: where (UE space, row vectors), and what its slots wear.</summary>
public sealed class MapMesh
{
    public string Mesh { get; init; }
    public Matrix4x4 World { get; init; }
    /// <summary>UE material index -> the material placed there instead (a component's override, a building's texture data).</summary>
    public Dictionary<int, string> Overrides { get; init; } = new();
    /// <summary>UE material index -> values over that slot's material (a dynamic instance's parameters).</summary>
    public Dictionary<int, ParamSet> Params { get; init; } = new();
    /// <summary>Values over every slot's material: a building's texture data (Diffuse, Normals, SpecularMasks by layer).</summary>
    public ParamSet AllSlots { get; init; }
    public string Actor { get; init; }
    /// <summary>UE's Custom Primitive Data: floats the component hands its materials (a tree's season tint); else null.</summary>
    public float[] PrimitiveData { get; init; }
    /// <summary>An instanced mesh's own custom data floats (PerInstanceSMCustomData; the materials' PerInstanceCustomData), or null.</summary>
    public float[] InstanceData { get; init; }
    /// <summary>The level file that places it.</summary>
    public string Level { get; init; }
    /// <summary>A spline mesh's bend (its mesh deformed along a Hermite segment), in the component's space; else null.</summary>
    public Dictionary<string, object> Spline { get; init; }
    /// <summary>What a big mesh covers (UE X/Y, world): an area keeps it when they overlap (water bodies); else null.</summary>
    public (float MinX, float MinY, float MaxX, float MaxY)? Area { get; init; }
    /// <summary>
    /// False when the component casts no shadow in the game (CastShadow, UE's master flag, off; or both bCastDynamicShadow and
    /// bCastStaticShadow off), e.g. a lamp's housing.
    /// </summary>
    public bool CastShadow { get; init; } = true;
    /// <summary>Seen only through its shadow: a shadow proxy (ShadowProxyMeshComponent: hidden, not in the main pass), e.g. the simple mesh a tree's shadow comes from.</summary>
    public bool ShadowOnly { get; init; }
    /// <summary>Where it stands, for an area's test: its origin, a spline mesh's segment middle.</summary>
    public Vector3 Anchor
    {
        get
        {
            if (Spline?["p0"] is double[] a && Spline["p1"] is double[] b)
                return Vector3.Transform(new Vector3((float)(a[0] + b[0]) / 2, (float)(a[1] + b[1]) / 2, (float)(a[2] + b[2]) / 2), World);
            return World.Translation;
        }
    }
}

/// <summary>
/// A light a map places (point, spot or rect light component): where (UE space, row vectors, cm) and what the engine reads of it, as
/// stored (the level's values over its templates'). The consumer converts units and colour (Unitless local lights are 1/625 candela
/// per unit; spots and rects shine along their X axis).
/// </summary>
public sealed class MapLight
{
    /// <summary>"Point", "Spot" or "Rect".</summary>
    public string Kind { get; init; }
    public string Name { get; init; }
    public string Actor { get; init; }
    public string Level { get; init; }
    public Matrix4x4 World { get; init; }
    public float Intensity { get; init; }
    /// <summary>"Candelas", "Lumens", "Unitless", "EV" or "Nits": what Intensity is in when the light falls off by the inverse square.</summary>
    public string Units { get; init; }
    /// <summary>
    /// False: the older falloff (Intensity is a brightness, times (1 - (d / AttenuationRadius)^2)^FalloffExponent out to the radius,
    /// whatever the units say); true: candelas and the like, falling off with the square of the distance.
    /// </summary>
    public bool InverseSquared { get; init; } = true;
    public float FalloffExponent { get; init; } = 8f;
    /// <summary>The sRGB colour (LightColor).</summary>
    public FColor Color { get; init; }
    public bool UseTemperature { get; init; }
    public float Temperature { get; init; }
    /// <summary>cm: where the light ends.</summary>
    public float AttenuationRadius { get; init; }
    /// <summary>cm: a point or spot light's source sphere.</summary>
    public float SourceRadius { get; init; }
    public bool CastShadows { get; init; }
    /// <summary>A spot's cone, in degrees from its axis: where the light is full, where it ends.</summary>
    public float InnerConeAngle { get; init; }
    public float OuterConeAngle { get; init; }
    /// <summary>cm: a rect light's source.</summary>
    public float SourceWidth { get; init; }
    public float SourceHeight { get; init; }
    public float BarnDoorAngle { get; init; }
}

/// <summary>
/// A decal a map places (a DecalComponent): its world matrix (UE space, row vectors, cm; it projects along its X axis) and what the
/// engine reads of it. Its half extents are DecalSize times the matrix's scale.
/// </summary>
public sealed class MapDecal
{
    public string Name { get; init; }
    public string Actor { get; init; }
    public string Level { get; init; }
    public Matrix4x4 World { get; init; }
    /// <summary>cm: the half extents before the component's scale (X the depth along the projection, Y and Z the footprint); UE's 128, 256, 256 unless stored.</summary>
    public Vector3 DecalSize { get; init; }
    public int SortOrder { get; init; }
    public float FadeScreenSize { get; init; }
    /// <summary>The decal material's asset path (or a dynamic instance's parent asset's).</summary>
    public string Material { get; init; }
    /// <summary>The values a dynamic instance sets over the material; else null.</summary>
    public ParamSet Params { get; init; }
}

/// <summary>A particle system a map places (a NiagaraComponent: a NiagaraActor's, a Blueprint's): where, and its user parameters' overrides.</summary>
public sealed class MapEffect
{
    public string Name { get; init; }
    public string Actor { get; init; }
    public string Level { get; init; }
    public Matrix4x4 World { get; init; }
    /// <summary>The Niagara system's asset path.</summary>
    public string System { get; init; }
    /// <summary>The component's user parameter overrides by name (as the system names them: "x", not "User.x"): a float, an int, a bool, or floats (a vector, a colour); may be empty.</summary>
    public Dictionary<string, object> Parameters { get; init; } = new();
}

public sealed class MapOptions
{
    /// <summary>Also the cells external data layers add (game modes' and events' content, shipped in other plugins); without them, the map as its own cells have it.</summary>
    public bool DataLayers { get; set; }
    /// <summary>Instanced meshes: foliage, prop groups, scatter.</summary>
    public bool Instances { get; set; } = true;
    /// <summary>The landscape (terrain) too.</summary>
    public bool Landscape { get; set; } = true;
    /// <summary>Water bodies: ocean, lakes, rivers (their surface meshes), pools and waterfalls.</summary>
    public bool Water { get; set; } = true;
    /// <summary>Textures at most this size (the game's own smaller mips); 0: full size.</summary>
    public int TextureSize { get; set; }
    /// <summary>Only this area (UE cm, X/Y): the cells that touch it, and what they place inside it. Null: all of it.</summary>
    public MapRegion? Region { get; set; }
}

/// <summary>An area of a map, in UE centimetres.</summary>
public readonly record struct MapRegion(double MinX, double MinY, double MaxX, double MaxY)
{
    public bool Contains(double x, double y) => x >= MinX && x <= MaxX && y >= MinY && y <= MaxY;
    public bool Overlaps((float MinX, float MinY, float MaxX, float MaxY) a) => a.MaxX > MinX && a.MinX < MaxX && a.MaxY > MinY && a.MinY < MaxY;
    public bool Touches(MapCell c) => c.MaxX > MinX && c.MinX < MaxX && c.MaxY > MinY && c.MinY < MaxY;
}

/// <summary>One World Partition cell: its level file, and the area it streams in for.</summary>
public sealed class MapCell
{
    public string Key { get; init; }
    public string Grid { get; init; }
    /// <summary>0 for the finest cells; higher levels hold what spans several.</summary>
    public int Level { get; init; }
    /// <summary>False: always loaded, whatever the player's position.</summary>
    public bool Spatial { get; init; }
    public double MinX { get; init; }
    public double MinY { get; init; }
    public double MaxX { get; init; }
    public double MaxY { get; init; }
    public long Size { get; init; }
    /// <summary>Added by an external data layer (a game mode's or an event's content).</summary>
    public bool DataLayer { get; init; }
}

/// <summary>How far a map's export is: its stage (1-4: reading, meshes, materials, Blender), done of total.</summary>
public readonly record struct MapProgress(int Stage, string What, int Done, int Total)
{
    public const int Stages = 4;
    // where each stage starts in the whole, by how long they usually take (Blender's placing is most of it)
    static readonly double[] Starts = { 0, 0.10, 0.25, 0.35, 1.0 };
    /// <summary>The whole export's share done, 0..1.</summary>
    public double Overall
    {
        get
        {
            var s = Math.Clamp(Stage, 1, Stages);
            var f = Total > 0 ? Math.Clamp((double)Done / Total, 0, 1) : 0;
            return Starts[s - 1] + (Starts[s] - Starts[s - 1]) * f;
        }
    }
}

/// <summary>What reading a map found.</summary>
public sealed class MapScan
{
    public string Name { get; init; }
    public string Key { get; init; }
    public List<MapMesh> Meshes { get; set; } = new();
    /// <summary>Its point, spot and rect lights (visible ones).</summary>
    public List<MapLight> Lights { get; set; } = new();
    /// <summary>Its decals (visible ones with a material).</summary>
    public List<MapDecal> Decals { get; set; } = new();
    /// <summary>Its particle systems (visible ones with a system, not a device's).</summary>
    public List<MapEffect> Effects { get; set; } = new();
    /// <summary>The landscape pieces its levels hold (exported when the bundle is made).</summary>
    public List<MapLandscape> Landscapes { get; set; } = new();
    /// <summary>The area asked for, if any.</summary>
    public MapRegion? Region { get; set; }
    public int Levels { get; set; }
    public int Actors;
    /// <summary>What was left out, by kind, with counts (landscape, splines, HLODs...).</summary>
    public ConcurrentDictionary<string, int> Skipped { get; } = new();
    public int UniqueMeshes => Meshes.Select(m => m.Mesh).Distinct(StringComparer.OrdinalIgnoreCase).Count();
    internal void Skip(string what, int n = 1) => Skipped.AddOrUpdate(what, n, (_, c) => c + n);
}

public sealed class MapLandscape
{
    public ALandscapeProxy Proxy { get; init; }
    public string Name { get; init; }
    public string Level { get; init; }
    /// <summary>Where it sits, rotation and translation only (UE, row vectors): its scale goes into the vertices.</summary>
    public Matrix4x4 World { get; init; }
    public Vector3 Scale { get; init; }
}
