using System.Collections.Generic;
using FortnitePorting.Exporting.Models;

namespace FortnitePorting.Exporting.MaterialPorter;

/// <summary>A character part with the fork's extras for its Blender plugin (FP's record plus these fields).</summary>
public record MaterialPorterPart : ExportPart
{
    public MaterialPorterPart(ExportPart part) : base(part) { }
    /// <summary>The part's shell fur (ExportContext.ShellFur), or null.</summary>
    public MaterialPorterShells? MPShells;
}

/// <summary>
/// Shell fur (UE's ShellMesh plugin): the mesh drawn Count more times, copy n (1 to Count) pushed
/// out along its normals by Depth * n / Count (cm), with its slot's shell material; the base layer
/// (the mesh itself) with its slot's base material where one is given.
/// </summary>
public record MaterialPorterShells
{
    public float Depth;
    public int Count;
    public bool CastShadows;
    /// <summary>Each slot's shell material (Slot: the mesh's material slot).</summary>
    public readonly List<ExportMaterial> Materials = [];
    /// <summary>Each slot's material for the base layer, in place of the slot's own.</summary>
    public readonly List<ExportMaterial> BaseMaterials = [];
}
