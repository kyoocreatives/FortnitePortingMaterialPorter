using System.Collections.Generic;
using CUE4Parse.FileProvider;
using FortnitePorting.Exporting.Models;

namespace FortnitePorting.Exporting.MaterialPorter;

/// <summary>What the map reader reads of the game.</summary>
public sealed class MapGame
{
    public IFileProvider Provider { get; init; } = null!;
}

/// <summary>A placement's extras for the fork's Blender plugin.</summary>
public partial record MaterialPorterMesh : ExportMesh
{
    public MaterialPorterMesh() { }
    /// <summary>FP's record plus the fork's fields.</summary>
    public MaterialPorterMesh(ExportMesh mesh) : base(mesh) { }
    /// <summary>UE's Custom Primitive Data (texture data tints at 30/31/35), or null.</summary>
    public float[]? MPPrimitiveData;
    /// <summary>An instance's custom data floats (PerInstanceCustomData), or null.</summary>
    public float[]? MPInstanceData;
    /// <summary>A spline mesh's bend, applied in Blender (meshes.spline_bend), or null.</summary>
    public Dictionary<string, object>? MPSpline;
    /// <summary>A landscape's weight layers: LayerInfo asset name -> LayerName.</summary>
    public Dictionary<string, string>? MPLayerNames;
    /// <summary>False when the component casts no shadow in the game; omitted when it does.</summary>
    [Newtonsoft.Json.JsonProperty(NullValueHandling = Newtonsoft.Json.NullValueHandling.Ignore)]
    public bool? MPCastShadow;
    /// <summary>True when only its shadow shows (a tree's shadow proxy); omitted otherwise.</summary>
    [Newtonsoft.Json.JsonProperty(NullValueHandling = Newtonsoft.Json.NullValueHandling.Ignore)]
    public bool? MPShadowOnly;
    /// <summary>The actor the mesh came from; its lights don't shadow it (shadow linking).</summary>
    [Newtonsoft.Json.JsonProperty(NullValueHandling = Newtonsoft.Json.NullValueHandling.Ignore)]
    public string? MPActor;
    /// <summary>The parent's bone the mesh follows, or null for the parent itself.</summary>
    public string? MPParentBone;
    /// <summary>
    /// A particle effect node (ExportContext.Effects). Kind: "System" (Exports and Fields for CPU emitter replay, User parameters,
    /// Attach to the selected armature, Role/Place/Table/Sockets for an item's own effect), "Emitter" (Sim: CPU, GPU or Stateless),
    /// "Mesh" (Index: which of the renderer's meshes) or "Sprite"/"Ribbon" (no mesh; SubImages, Facing). A renderer's node names it (Renderer).
    /// </summary>
    public Dictionary<string, object>? MPEffect;
}

/// <summary>A material with values over it (a dynamic instance's, a building's texture data).</summary>
public record MaterialPorterMaterial : ExportMaterial
{
    public MaterialPorterMaterial(ExportMaterial material) : base(material) { }
    public ParamSet? MPValues;
    /// <summary>A wrap over the material (ExportContext.Weapons): values laid over the material's own and MPValues, except for textures those set.</summary>
    public ParamSet? MPWrap;
    /// <summary>A LEGO figure's face: where the rig puts the character accents for each mouth pose.</summary>
    public string? MPFaceRig;
    /// <summary>A sprite's flipbook: sub-images across and down, or null.</summary>
    public float[]? MPSprite;
    /// <summary>A ribbon's material: particle values run along the ribbon.</summary>
    public bool MPRibbon;
}
