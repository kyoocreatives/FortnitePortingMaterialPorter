using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Numerics;
using System.Threading;
using System.Threading.Tasks;
using CUE4Parse.FileProvider;
using CUE4Parse.UE4.Assets.Exports;
using CUE4Parse.UE4.Assets.Exports.Component.Landscape;
using CUE4Parse.UE4.Assets.Exports.StaticMesh;
using CUE4Parse.UE4.Assets.Exports.Texture;
using CUE4Parse.UE4.Objects.UObject;
using FortnitePorting.Exporting.MaterialPorter;
using Newtonsoft.Json;
using Serilog;
using SixLabors.ImageSharp;

namespace FortnitePorting.MaterialPorter;

/// <summary>Top-down picture of a map without its own minimap (a UEFN island, or a mode whose minimap is another map's).</summary>
// Landscape shaded by height, then each placed mesh's bounds seen from above at its top height, lit from the
// north-west. Image x runs along +X and y along +Y, like the game's own map pictures. Drawn from the map's levels
// (World Partition cells, an island's cells beside it), refined as they're read, and cached on disk.
public static class MapPreview
{
    public const int Size = 2048;
    private const int Version = 1;

    public sealed record Area(double MinX, double MinY, double Span)
    {
        public double K => Size / Span;                    // pixels per cm
        public double CenterX => MinX + Span / 2;
        public double CenterY => MinY + Span / 2;
    }

    public sealed record Picture(byte[] Rgba, Area Area);

    // ------------------------------------------------------------ minimaps that are another map's

    private static Dictionary<string, string>? _owners;
    private static readonly SemaphoreSlim OwnersLock = new(1, 1);

    /// <summary>Whether a minimap texture belongs to a map other than this one.</summary>
    // The game's map UI data names each picture's map; Battle Royale's picture (Apollo_Terrain_Minimap)
    // is the current island's, whichever map lists it.
    public static async Task<bool> IsAnotherMapsAsync(IFileProvider provider, string? minimapPath, string mapPath)
    {
        if (string.IsNullOrEmpty(minimapPath)) return false;
        var owners = await OwnersAsync(provider);
        return owners.TryGetValue(Name(minimapPath), out var owner) && !owner.Equals(Name(mapPath), StringComparison.OrdinalIgnoreCase);
    }

    private static async Task<Dictionary<string, string>> OwnersAsync(IFileProvider provider)
    {
        if (_owners is not null) return _owners;
        await OwnersLock.WaitAsync();
        try
        {
            if (_owners is not null) return _owners;
            var owners = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            var settings = new JsonSerializerSettings { ReferenceLoopHandling = ReferenceLoopHandling.Ignore };
            foreach (var file in provider.Files.Keys.Where(f => f.EndsWith("MapUIData.uasset", StringComparison.OrdinalIgnoreCase)).ToList())
            {
                try
                {
                    var package = await provider.LoadPackageAsync(file);
                    var data = package.GetExports().FirstOrDefault(e => e.ExportType == "FortMapUIData");
                    if (data is null || !data.TryGetValue(out string map, "MapPath")
                                     || !data.TryGetValue(out FSoftObjectPath material, "MapMaterial")) continue;
                    var materialObject = await material.LoadAsync();
                    // textures sampled by its map material
                    var json = JsonConvert.SerializeObject(materialObject, settings);
                    foreach (System.Text.RegularExpressions.Match m in System.Text.RegularExpressions.Regex.Matches(json, "Texture2D'([^']+)'"))
                        owners.TryAdd(Name(m.Groups[1].Value), Name(map));
                }
                catch (Exception e)
                {
                    Log.Debug("[Material Porter] map UI data {File}: {Error}", file, e.Message);
                }
            }
            return _owners = owners;
        }
        finally
        {
            OwnersLock.Release();
        }
    }

    private static string Name(string path)
    {
        var name = path[(path.LastIndexOf('/') + 1)..];
        var dot = name.IndexOf('.');
        return dot >= 0 ? name[..dot] : name;
    }

    // ------------------------------------------------------------ the cache

    private static string CachePath(string folder) => Path.Combine(folder, "Preview.png");

    private sealed record CacheMeta(int Version, string Key, double MinX, double MinY, double Span);

    public static string Key(IFileProvider provider, string mapPath, int levels)
    {
        var file = provider.Files.TryGetValue(provider.FixPath(mapPath + ".umap"), out var f) ? f.Size : 0;
        return $"{mapPath}|{file}|{levels}";
    }

    public static Picture? Cached(string folder, string key)
    {
        try
        {
            var meta = JsonConvert.DeserializeObject<CacheMeta>(File.ReadAllText(Path.Combine(folder, "Preview.json")));
            if (meta is null || meta.Version != Version || meta.Key != key || !File.Exists(CachePath(folder))) return null;
            using var image = SixLabors.ImageSharp.Image.Load<SixLabors.ImageSharp.PixelFormats.Rgba32>(CachePath(folder));
            if (image.Width != Size || image.Height != Size) return null;
            var rgba = new byte[Size * Size * 4];
            image.CopyPixelDataTo(rgba);
            return new Picture(rgba, new Area(meta.MinX, meta.MinY, meta.Span));
        }
        catch
        {
            return null;
        }
    }

    private static void Save(string folder, string key, Picture picture)
    {
        try
        {
            Directory.CreateDirectory(folder);
            using var image = SixLabors.ImageSharp.Image.LoadPixelData<SixLabors.ImageSharp.PixelFormats.Rgba32>(picture.Rgba, Size, Size);
            image.SaveAsPng(CachePath(folder));
            File.WriteAllText(Path.Combine(folder, "Preview.json"),
                JsonConvert.SerializeObject(new CacheMeta(Version, key, picture.Area.MinX, picture.Area.MinY, picture.Area.Span)));
        }
        catch (Exception e)
        {
            Log.Warning("[Material Porter] map preview not kept: {Error}", e.Message);
        }
    }

    // ------------------------------------------------------------ drawing

    /// <summary>Reads the levels (the map's first) and draws them, reporting each stage through `show`.</summary>
    // The landscape is drawn once the first level is read (its extent, if it has one, is the picture's),
    // then what stands on it every few seconds.
    public static async Task<Picture?> DrawAsync(IFileProvider provider, string mapPath, IReadOnlyList<string> levels, string folder, string key,
        Action<Picture?, string> show, CancellationToken ct)
    {
        var scan = new MapScan { Name = mapPath, Key = mapPath };
        var reader = new MapReader(new MapGame { Provider = provider }, new MapOptions { Instances = true, Landscape = true }, scan);
        var canvas = new Canvas();
        var bounds = new ConcurrentDictionary<string, Task<(Vector3 Origin, Vector3 Extent)?>>(StringComparer.OrdinalIgnoreCase);

        await reader.LevelAsync(levels[0], Matrix4x4.Identity, 0, ct);
        foreach (var landscape in reader.Landscapes) canvas.AddLandscape(landscape);
        Area? area = canvas.TerrainArea();
        if (area is not null)
        {
            canvas.Begin(area);
            show(canvas.Render(), "Drawing what stands on it...");
        }

        var done = 1;
        if (levels.Count > 1) show(null, $"Reading the map... 1/{levels.Count} levels");
        await Parallel.ForEachAsync(levels.Skip(1), new ParallelOptions { MaxDegreeOfParallelism = Math.Max(2, Environment.ProcessorCount / 2), CancellationToken = ct },
            async (level, token) =>
            {
                try { await reader.LevelAsync(level, Matrix4x4.Identity, 0, token); }
                catch (OperationCanceledException) { throw; }
                catch (Exception e) { Log.Debug("[Material Porter] map preview {Level}: {Error}", level, e.Message); }
                if (Interlocked.Increment(ref done) % 20 == 0) show(null, $"Reading the map... {done}/{levels.Count} levels");
            });
        ct.ThrowIfCancellationRequested();
        foreach (var landscape in reader.Landscapes.Where(l => !canvas.Has(l))) canvas.AddLandscape(landscape);

        var placed = reader.Placed.ToList();
        if (area is null)
        {
            area = canvas.TerrainArea() ?? PlacementArea(placed);
            if (area is null) return null;
            canvas.Begin(area);
        }
        else canvas.Begin(area);     // landscapes found in the cells

        // each mesh's bounds are read once; drawn in batches so the picture fills in
        var meshes = placed.Select(p => p.Mesh).Where(m => !string.IsNullOrEmpty(m)).Distinct(StringComparer.OrdinalIgnoreCase).ToList();
        var read = 0;
        var lastShown = DateTime.MinValue;
        foreach (var batch in meshes.Chunk(400))
        {
            await Parallel.ForEachAsync(batch, new ParallelOptions { MaxDegreeOfParallelism = Math.Max(2, Environment.ProcessorCount / 2), CancellationToken = ct },
                async (mesh, _) => await bounds.GetOrAdd(mesh, m => MeshBoundsAsync(provider, m)));
            read += batch.Length;
            if (DateTime.UtcNow - lastShown > TimeSpan.FromSeconds(3))
            {
                lastShown = DateTime.UtcNow;
                Draw(canvas, placed, bounds);
                show(canvas.Render(), $"Drawing what stands on it... {read * 100 / Math.Max(meshes.Count, 1)}%");
            }
        }
        Draw(canvas, placed, bounds);
        var picture = canvas.Render();
        Save(folder, key, picture);
        Log.Information("[Material Porter] {Map}: preview drawn from {Levels} levels, {Meshes} placements of {Unique} meshes, {Landscapes} landscape pieces",
            mapPath, levels.Count, placed.Count, meshes.Count, reader.Landscapes.Count);
        return picture;
    }

    private static void Draw(Canvas canvas, List<MapMesh> placed, ConcurrentDictionary<string, Task<(Vector3 Origin, Vector3 Extent)?>> bounds)
    {
        canvas.ClearMeshes();
        foreach (var p in placed)
        {
            if (string.IsNullOrEmpty(p.Mesh) || !bounds.TryGetValue(p.Mesh, out var task) || !task.IsCompletedSuccessfully || task.Result is not { } b) continue;
            if (Kind(p.Mesh) < 0) continue;
            if (canvas.AddMesh(p.World, b.Origin, b.Extent, Kind(p.Mesh), p.Mesh.StartsWith("/Engine/", StringComparison.OrdinalIgnoreCase)) is > 0.04f and var share && DebugBig)
                Log.Information("[map preview] big {Mesh} {Share:P0} of the side, actor {Actor}", p.Mesh, share, p.Actor);
        }
    }

    public static bool DebugBig = Environment.GetEnvironmentVariable("MP_PREVIEW_DEBUG") == "1";

    private static async Task<(Vector3, Vector3)?> MeshBoundsAsync(IFileProvider provider, string path)
    {
        try
        {
            var mesh = await provider.LoadPackageObjectAsync<UStaticMesh>(path);
            if (mesh.RenderData?.Bounds is not { } b) return null;
            return (new Vector3((float) b.Origin.X, (float) b.Origin.Y, (float) b.Origin.Z),
                    new Vector3((float) b.BoxExtent.X, (float) b.BoxExtent.Y, (float) b.BoxExtent.Z));
        }
        catch
        {
            return null;
        }
    }

    private static readonly string[] FoliageWords = ["Tree", "Bush", "Foliage", "Plant", "Grass", "Flower", "Leaf", "Leaves", "Palm", "Shrub", "Fern", "Hedge", "Pine", "Ivy", "Weed", "Reed"];

    // not ground seen from above: sky and backdrop cards, the ocean floor, effect volumes
    private static readonly string[] Backdrop = ["Cloud", "Sky", "BGMountain", "Backdrop", "Background", "Vista", "OceanFloor", "Dust", "Inverted", "FakeLight", "Fog", "Smoke", "BoxWall", "Blocker", "Collision"];
    private static readonly string[] RockWords = ["Cliff", "Rock", "Boulder", "Stone", "Blob", "Mountain", "Hill", "Mesa", "Ridge", "Canyon"];

    private static int Kind(string mesh)
    {
        var name = mesh[(mesh.LastIndexOf('/') + 1)..];
        if (Backdrop.Any(w => name.Contains(w, StringComparison.OrdinalIgnoreCase))) return -1;
        if (name.Contains("Water", StringComparison.OrdinalIgnoreCase)) return 2;
        if (RockWords.Any(w => name.Contains(w, StringComparison.OrdinalIgnoreCase))) return 3;
        return FoliageWords.Any(w => name.Contains(w, StringComparison.OrdinalIgnoreCase)) ? 1 : 0;
    }

    // Where most placements are; a few far off (sky box, lobby) would squeeze the rest into a corner.
    private static Area? PlacementArea(List<MapMesh> placed)
    {
        if (placed.Count == 0) return null;
        var xs = placed.Select(p => (double) p.World.M41).OrderBy(v => v).ToList();
        var ys = placed.Select(p => (double) p.World.M42).OrderBy(v => v).ToList();
        double Pct(List<double> v, double q) => v[(int) Math.Clamp(Math.Round(q * (v.Count - 1)), 0, v.Count - 1)];
        return Square(Pct(xs, 0.005), Pct(ys, 0.005), Pct(xs, 0.995), Pct(ys, 0.995));
    }

    internal static Area Square(double minX, double minY, double maxX, double maxY)
    {
        var span = Math.Max(Math.Max(maxX - minX, maxY - minY) * 1.06, 2000);
        return new Area((minX + maxX - span) / 2, (minY + maxY - span) / 2, span);
    }

    // ------------------------------------------------------------ the raster

    private sealed class Canvas
    {
        private readonly List<(MapLandscape Landscape, ULandscapeComponent Component)> _components = [];
        private readonly HashSet<string> _proxies = [];
        private readonly Dictionary<string, byte[]?> _heightTextures = new(StringComparer.OrdinalIgnoreCase);
        private Area _area = null!;
        private float[] _terrain = [];
        private float[] _top = [];
        private byte[] _kind = [];
        private float _groundTop = float.PositiveInfinity;

        public bool Has(MapLandscape l) => _proxies.Contains(l.Level + "|" + l.Name);

        public void AddLandscape(MapLandscape landscape)
        {
            if (!_proxies.Add(landscape.Level + "|" + landscape.Name)) return;
            foreach (var index in landscape.Proxy.LandscapeComponents)
            {
                try
                {
                    if (index.Load() is ULandscapeComponent component) _components.Add((landscape, component));
                }
                catch
                {
                    // unreadable component: a hole in the picture
                }
            }
        }

        public Area? TerrainArea()
        {
            if (_components.Count == 0) return null;
            double minX = double.MaxValue, minY = double.MaxValue, maxX = double.MinValue, maxY = double.MinValue;
            foreach (var (l, c) in _components)
            foreach (var (lx, ly) in new[] { (0, 0), (1, 0), (0, 1), (1, 1) })
            {
                var w = ToWorld(l, c, lx * c.ComponentSizeQuads, ly * c.ComponentSizeQuads, 0);
                minX = Math.Min(minX, w.X); maxX = Math.Max(maxX, w.X);
                minY = Math.Min(minY, w.Y); maxY = Math.Max(maxY, w.Y);
            }
            return Square(minX, minY, maxX, maxY);
        }

        private static Vector3 ToWorld(MapLandscape l, ULandscapeComponent c, float x, float y, float z)
        {
            var rel = c.GetRelativeLocation();
            var local = new Vector3((x + (float) rel.X) * l.Scale.X, (y + (float) rel.Y) * l.Scale.Y, (z + (float) rel.Z) * l.Scale.Z);
            return Vector3.Transform(local, l.World);
        }

        public void Begin(Area area)
        {
            _area = area;
            _terrain = new float[Size * Size];
            Array.Fill(_terrain, float.NaN);
            foreach (var (l, c) in _components) DrawComponent(l, c);
            var ground = _terrain.Where(float.IsFinite).ToArray();
            if (ground.Length > 0)
            {
                Array.Sort(ground);
                _groundTop = ground[(int) ((ground.Length - 1) * 0.99)];
            }
            ClearMeshes();
        }

        public void ClearMeshes()
        {
            _top = new float[Size * Size];
            Array.Fill(_top, float.NaN);
            _kind = new byte[Size * Size];
        }

        private byte[]? Heights(UTexture2D texture)
        {
            var path = texture.GetPathName();
            if (_heightTextures.TryGetValue(path, out var data)) return data;
            try
            {
                var mip = texture.GetMip(0);
                data = mip?.BulkData?.Data;
            }
            catch
            {
                data = null;
            }
            return _heightTextures[path] = data;
        }

        private void DrawComponent(MapLandscape l, ULandscapeComponent c)
        {
            if (c.GetHeightmap() is not { } texture || Heights(texture) is not { } data) return;
            var texW = texture.PlatformData.SizeX;
            var texH = texture.PlatformData.SizeY;
            if (data.Length < texW * texH * 4) return;
            var offX = (int) (texW * c.HeightmapScaleBias.Z);
            var offY = (int) (texH * c.HeightmapScaleBias.W);
            var quads = c.ComponentSizeQuads;
            var sub = Math.Max(c.SubsectionSizeQuads, 1);
            var subs = Math.Max(c.NumSubsections, 1);

            int Tex(int v)
            {
                var s = Math.Min(v / sub, subs - 1);
                return s * (sub + 1) + (v - s * sub);
            }
            float Height(int x, int y)
            {
                var i = ((offY + Tex(y)) * texW + offX + Tex(x)) * 4;     // BGRA; height is R << 8 | G
                return ((data[i + 2] << 8 | data[i + 1]) - 32768) / 128f;
            }

            // the component's corners in pixels, then each pixel back to its quads (affine map)
            var o = ToWorld(l, c, 0, 0, 0);
            var ax = ToWorld(l, c, quads, 0, 0) - o;
            var ay = ToWorld(l, c, 0, quads, 0) - o;
            var det = ax.X * ay.Y - ax.Y * ay.X;
            if (Math.Abs(det) < 1e-6) return;
            var k = _area.K;
            double minPx = double.MaxValue, minPy = double.MaxValue, maxPx = double.MinValue, maxPy = double.MinValue;
            foreach (var p in new[] { o, o + ax, o + ay, o + ax + ay })
            {
                minPx = Math.Min(minPx, (p.X - _area.MinX) * k); maxPx = Math.Max(maxPx, (p.X - _area.MinX) * k);
                minPy = Math.Min(minPy, (p.Y - _area.MinY) * k); maxPy = Math.Max(maxPy, (p.Y - _area.MinY) * k);
            }
            var zScale = l.Scale.Z;
            var zBase = ToWorld(l, c, 0, 0, 0).Z;
            for (var py = Math.Max(0, (int) minPy); py <= Math.Min(Size - 1, (int) maxPy); py++)
            for (var px = Math.Max(0, (int) minPx); px <= Math.Min(Size - 1, (int) maxPx); px++)
            {
                var wx = _area.MinX + (px + 0.5) / k - o.X;
                var wy = _area.MinY + (py + 0.5) / k - o.Y;
                var u = (wx * ay.Y - wy * ay.X) / det * quads;
                var v = (wy * ax.X - wx * ax.Y) / det * quads;
                if (u < 0 || v < 0 || u > quads || v > quads) continue;
                int x0 = Math.Min((int) u, quads - 1), y0 = Math.Min((int) v, quads - 1);
                float fx = (float) (u - x0), fy = (float) (v - y0);
                var h = (Height(x0, y0) * (1 - fx) + Height(x0 + 1, y0) * fx) * (1 - fy)
                        + (Height(x0, y0 + 1) * (1 - fx) + Height(x0 + 1, y0 + 1) * fx) * fy;
                _terrain[py * Size + px] = zBase + h * zScale;
            }
        }

        private static readonly (int, int)[] Corners = [(-1, -1), (1, -1), (-1, 1), (1, 1)];

        public float AddMesh(Matrix4x4 world, Vector3 origin, Vector3 extent, int kind, bool engine = false)
        {
            Span<Vector2> pts = stackalloc Vector2[8];
            var top = float.MinValue;
            var bottom = float.MaxValue;
            var n = 0;
            for (var z = -1; z <= 1; z += 2)
            foreach (var (x, y) in Corners)
            {
                var w = Vector3.Transform(origin + extent * new Vector3(x, y, z), world);
                top = Math.Max(top, w.Z);
                bottom = Math.Min(bottom, w.Z);
                pts[n++] = new Vector2((float) ((w.X - _area.MinX) * _area.K), (float) ((w.Y - _area.MinY) * _area.K));
            }
            float minX = float.MaxValue, minY = float.MaxValue, maxX = float.MinValue, maxY = float.MinValue;
            foreach (var p in pts)
            {
                minX = Math.Min(minX, p.X); maxX = Math.Max(maxX, p.X);
                minY = Math.Min(minY, p.Y); maxY = Math.Max(maxY, p.Y);
            }
            // off the picture, or covering most of it (sky sphere, ocean plane)
            var share = Math.Max(maxX - minX, maxY - minY) / Size;
            if (maxX < 0 || maxY < 0 || minX >= Size || minY >= Size) return share;
            // a volume or backdrop several blocks wide, or floating well above the ground (cloud)
            if (share > 0.15f || bottom > _groundTop + 20000) return share;
            // engine cube/sphere stretched over a block: a blocking volume, not something built
            if (engine && share > 0.02f) return share;
            if (maxX - minX < 1.5f && maxY - minY < 1.5f)
            {
                Plot((int) ((minX + maxX) / 2), (int) ((minY + maxY) / 2), top, kind);
                return share;
            }
            if (kind is 1 or 3)
            {
                // a tree crown or rock: a dome inside its bounds (round for a crown), so woods read as trees and cliffs as rock
                float cx = (minX + maxX) / 2, cy = (minY + maxY) / 2;
                float rx = (maxX - minX) * 0.45f, ry = (maxY - minY) * 0.45f;
                if (kind == 1) rx = ry = (rx + ry) / 2;
                var rise = (float) (Math.Max(rx, ry) / _area.K) * (kind == 1 ? 0.6f : 0.35f);
                for (var py = Math.Max(0, (int) (cy - ry)); py <= Math.Min(Size - 1, (int) (cy + ry)); py++)
                for (var px = Math.Max(0, (int) (cx - rx)); px <= Math.Min(Size - 1, (int) (cx + rx)); px++)
                {
                    float ux = (px + 0.5f - cx) / Math.Max(rx, 0.5f), uy = (py + 0.5f - cy) / Math.Max(ry, 0.5f);
                    var d = ux * ux + uy * uy;
                    if (d <= 1) Plot(px, py, top - d * rise, kind);
                }
                return share;
            }
            var hull = Hull(pts);
            for (var py = Math.Max(0, (int) Math.Floor(minY)); py <= Math.Min(Size - 1, (int) Math.Ceiling(maxY)); py++)
            {
                var yc = py + 0.5f;
                float lo = float.MaxValue, hi = float.MinValue;
                for (var i = 0; i < hull.Count; i++)
                {
                    var a = hull[i];
                    var b = hull[(i + 1) % hull.Count];
                    if ((a.Y <= yc && b.Y > yc) || (b.Y <= yc && a.Y > yc))
                    {
                        var x = a.X + (yc - a.Y) / (b.Y - a.Y) * (b.X - a.X);
                        lo = Math.Min(lo, x);
                        hi = Math.Max(hi, x);
                    }
                }
                if (lo > hi) continue;
                for (var px = Math.Max(0, (int) Math.Round(lo)); px <= Math.Min(Size - 1, (int) Math.Round(hi) - 1); px++)
                    Plot(px, py, top, kind);
            }
            return share;
        }

        private void Plot(int x, int y, float top, int kind)
        {
            if ((uint) x >= Size || (uint) y >= Size) return;
            var i = y * Size + x;
            if (!(top <= _top[i]))      // NaN: nothing yet
            {
                _top[i] = top;
                _kind[i] = (byte) (kind + 1);
            }
        }

        private static List<Vector2> Hull(Span<Vector2> points)
        {
            var p = points.ToArray().OrderBy(v => v.X).ThenBy(v => v.Y).ToArray();
            static float Cross(Vector2 o, Vector2 a, Vector2 b) => (a.X - o.X) * (b.Y - o.Y) - (a.Y - o.Y) * (b.X - o.X);
            var hull = new Vector2[p.Length * 2];
            var k = 0;
            foreach (var v in p)
            {
                while (k >= 2 && Cross(hull[k - 2], hull[k - 1], v) <= 0) k--;
                hull[k++] = v;
            }
            for (int i = p.Length - 2, t = k + 1; i >= 0; i--)
            {
                while (k >= t && Cross(hull[k - 2], hull[k - 1], p[i]) <= 0) k--;
                hull[k++] = p[i];
            }
            return hull.Take(Math.Max(k - 1, 1)).ToList();
        }

        // ground by height (low green to high rock); what stands on it is neutral (foliage green, water blue);
        // all lit from the north-west by the combined height's slope
        private static readonly (float T, Vector3 C)[] Ground =
        [
            (0f, new Vector3(52, 84, 58)), (0.35f, new Vector3(92, 122, 72)), (0.65f, new Vector3(138, 130, 96)), (1f, new Vector3(205, 200, 190)),
        ];

        public Picture Render()
        {
            var terrain = _terrain.Where(float.IsFinite).ToArray();
            float lo = 0, hi = 1;
            if (terrain.Length > 0)
            {
                Array.Sort(terrain);
                lo = terrain[(int) (terrain.Length * 0.01)];
                hi = Math.Max(terrain[(int) ((terrain.Length - 1) * 0.99)], lo + 1);
            }
            var tops = _top.Where(float.IsFinite).ToArray();
            float tlo = lo, thi = hi;
            if (tops.Length > 0)
            {
                Array.Sort(tops);
                tlo = tops[(int) (tops.Length * 0.02)];
                thi = Math.Max(tops[(int) ((tops.Length - 1) * 0.98)], tlo + 1);
            }

            var height = new float[Size * Size];
            for (var i = 0; i < height.Length; i++)
            {
                var t = _terrain[i];
                var m = _top[i];
                height[i] = float.IsFinite(m) && (!float.IsFinite(t) || m > t) ? m : float.IsFinite(t) ? t : float.NaN;
            }
            var cm = (float) (1 / _area.K);
            var light = Vector3.Normalize(new Vector3(-1, -1, 1.4f));
            var rgba = new byte[Size * Size * 4];
            Parallel.For(0, Size, y =>
            {
                for (var x = 0; x < Size; x++)
                {
                    var i = y * Size + x;
                    Vector3 color;
                    if (_kind[i] != 0 && float.IsFinite(_top[i]))
                    {
                        var t = Math.Clamp((_top[i] - tlo) / (thi - tlo), 0, 1);
                        color = _kind[i] switch
                        {
                            2 => Vector3.Lerp(new Vector3(48, 86, 46), new Vector3(86, 128, 70), t),
                            3 => new Vector3(52, 98, 138),
                            4 => Vector3.Lerp(new Vector3(112, 104, 90), new Vector3(178, 168, 148), t),
                            _ => Vector3.Lerp(new Vector3(118, 116, 114), new Vector3(226, 222, 214), t),
                        };
                    }
                    else if (float.IsFinite(_terrain[i]))
                        color = Ramp(Math.Clamp((_terrain[i] - lo) / (hi - lo), 0, 1));
                    else
                    {
                        Put(rgba, i, new Vector3(22, 24, 29));
                        continue;
                    }
                    // slope from the neighbours' heights (missing ones read as this one)
                    float H(int xx, int yy)
                    {
                        if ((uint) xx >= Size || (uint) yy >= Size) return height[i];
                        var v = height[yy * Size + xx];
                        return float.IsFinite(v) ? v : height[i];
                    }
                    var dx = (H(x + 1, y) - H(x - 1, y)) / (2 * cm);
                    var dy = (H(x, y + 1) - H(x, y - 1)) / (2 * cm);
                    var normal = Vector3.Normalize(new Vector3(-dx, -dy, 1));
                    var shade = 0.45f + 0.75f * Math.Max(0, Vector3.Dot(normal, light));
                    Put(rgba, i, color * Math.Min(shade, 1.25f));
                }
            });
            return new Picture(rgba, _area);
        }

        private static Vector3 Ramp(float t)
        {
            for (var i = 1; i < Ground.Length; i++)
                if (t <= Ground[i].T)
                    return Vector3.Lerp(Ground[i - 1].C, Ground[i].C, (t - Ground[i - 1].T) / (Ground[i].T - Ground[i - 1].T));
            return Ground[^1].C;
        }

        private static void Put(byte[] rgba, int i, Vector3 c)
        {
            rgba[i * 4] = (byte) Math.Clamp(c.X, 0, 255);
            rgba[i * 4 + 1] = (byte) Math.Clamp(c.Y, 0, 255);
            rgba[i * 4 + 2] = (byte) Math.Clamp(c.Z, 0, 255);
            rgba[i * 4 + 3] = 255;
        }
    }
}
