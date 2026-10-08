using System.Collections.Generic;
using FortnitePorting.Exporting.Models;

namespace FortnitePorting.Exporting.MaterialPorter;

/// <summary>A character part with the fork's extras for its Blender plugin.</summary>
public record MaterialPorterPart : ExportPart
{
    public MaterialPorterPart(ExportPart part) : base(part) { }
    /// <summary>The part's shell fur (ExportContext.ShellFur), or null.</summary>
    public MaterialPorterShells? MPShells;
}

/// <summary>
/// Shell fur (UE's ShellMesh plugin): the mesh drawn Count more times, copy n (1..Count) pushed out along
/// its normals by Depth * n / Count (cm), using the slot's shell material; plus an optional base layer.
/// </summary>
public record MaterialPorterShells
{
    public float Depth;
    public int Count;
    public bool CastShadows;
    /// <summary>Each slot's shell material (Slot: the mesh's material slot).</summary>
    public readonly List<ExportMaterial> Materials = [];
    /// <summary>Each slot's base-layer material, replacing the slot's own.</summary>
    public readonly List<ExportMaterial> BaseMaterials = [];
}
