using System.Collections.Generic;
using CUE4Parse.FileProvider;
using FortnitePorting.Exporting.Models;

namespace FortnitePorting.Exporting.MaterialPorter;

/// <summary>What Material Porter's map reader reads of the game.</summary>
public sealed class MapGame
{
    public IFileProvider Provider { get; init; } = null!;
}

/// <summary>A placement's extras for the fork's Blender plugin (FP's record plus these fields).</summary>
public partial record MaterialPorterMesh : ExportMesh
{
    public MaterialPorterMesh() { }
    /// <summary>FP's record with the fork's fields added (a weapon's mesh with its component's custom data).</summary>
    public MaterialPorterMesh(ExportMesh mesh) : base(mesh) { }
    /// <summary>UE's Custom Primitive Data (texture data tints at 30/31/35), or null.</summary>
    public float[]? MPPrimitiveData;
    /// <summary>An instance's own custom data floats (the materials' PerInstanceCustomData), or null.</summary>
    public float[]? MPInstanceData;
    /// <summary>A spline mesh's bend, bent in Blender (Material Porter's meshes.spline_bend), or null.</summary>
    public Dictionary<string, object>? MPSpline;
    /// <summary>A landscape's weight layers: LayerInfo asset name (FP's colour layer name) -> LayerName.</summary>
    public Dictionary<string, string>? MPLayerNames;
    /// <summary>False when the component casts no shadow in the game (a lamp's housing around its light); omitted when it casts.</summary>
    [Newtonsoft.Json.JsonProperty(NullValueHandling = Newtonsoft.Json.NullValueHandling.Ignore)]
    public bool? MPCastShadow;
    /// <summary>True when only its shadow shows (a tree's shadow proxy); omitted otherwise.</summary>
    [Newtonsoft.Json.JsonProperty(NullValueHandling = Newtonsoft.Json.NullValueHandling.Ignore)]
    public bool? MPShadowOnly;
    /// <summary>The actor the mesh came from: a light of the same actor (a lamp in its housing) isn't shadowed by it (shadow linking).</summary>
    [Newtonsoft.Json.JsonProperty(NullValueHandling = Newtonsoft.Json.NullValueHandling.Ignore)]
    public string? MPActor;
    /// <summary>The parent's bone the mesh follows (a weapon mod on its attach bone), or null: the parent itself.</summary>
    public string? MPParentBone;
    /// <summary>
    /// A particle effect's node (ExportContext.Effects): Kind "System" (Exports, Fields: what the
    /// plugin replays its CPU emitters from; User: user parameters to replay it with; Attach: put it
    /// on the armature selected in Blender; Role, Place, Table and Sockets: an item's own effect, where it
    /// sits on its socket, its mesh's sockets, and the two sockets a pickaxe's trail runs between; the plugin adds Bone, Offset, Start and End to an animation's), "Emitter" (Sim: CPU, GPU or Stateless), "Mesh" (a mesh
    /// renderer's mesh; Index: which of its meshes), or "Sprite" / "Ribbon" (no mesh: the plugin makes
    /// a plane for its Material; SubImages, Facing). A renderer's node names it (Renderer).
    /// </summary>
    public Dictionary<string, object>? MPEffect;
}

/// <summary>A material with values over it: a dynamic instance's, a building's texture data.</summary>
public record MaterialPorterMaterial : ExportMaterial
{
    public MaterialPorterMaterial(ExportMaterial material) : base(material) { }
    public ParamSet? MPValues;
    /// <summary>
    /// A wrap over the material (ExportContext.Weapons): its values, which the plugin lays over the
    /// material's own and MPValues, but for the textures those set.
    /// </summary>
    public ParamSet? MPWrap;
    /// <summary>A LEGO figure's face: where its rig puts the character accents for each mouth pose (FigureRecipe.AccentRigAsync).</summary>
    public string? MPFaceRig;
    /// <summary>A sprite's flipbook: how many sub-images across and down (each particle shows one), or null.</summary>
    public float[]? MPSprite;
    /// <summary>A ribbon's material: its particles' values run along the ribbon (the plugin reads them off the ribbon's mesh).</summary>
    public bool MPRibbon;
}
