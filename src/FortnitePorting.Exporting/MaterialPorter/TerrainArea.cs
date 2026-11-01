namespace FortnitePorting.Exporting.MaterialPorter;

/// <summary>A square of ground in world units (a picked map cell, a landscape piece).</summary>
public readonly record struct TerrainArea(double MinX, double MinY, double MaxX, double MaxY)
{
    public bool Overlaps(TerrainArea other) => MinX < other.MaxX && other.MinX < MaxX && MinY < other.MaxY && other.MinY < MaxY;
}
