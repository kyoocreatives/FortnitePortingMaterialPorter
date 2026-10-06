using System;
using System.Collections.Generic;
using System.Linq;
using System.Numerics;
using CUE4Parse.FileProvider;
using CUE4Parse.UE4.Assets.Exports;
using CUE4Parse.UE4.Assets.Exports.StaticMesh;
using CUE4Parse.UE4.Assets.Objects;
using CUE4Parse.UE4.Objects.Core.Math;
using CUE4Parse.UE4.Objects.Engine;
using CUE4Parse.UE4.Objects.GameplayTags;
using CUE4Parse.UE4.Objects.UObject;

namespace FortnitePorting.Exporting.MaterialPorter;

/// <summary>
/// Material Porter fork: Rocket Racing (DelMar) tracks. A track actor (DelMarTrack_BP, the UEFN
/// RocketRacingTrack / BP_FortDelMarTrack) holds its road as a spline (MainSpline), a style per
/// spline point (TrackSplinePointData: a gameplay tag) and a palette (TrackPalette_V2: style tag ->
/// segment actor classes, each one road piece: a spline mesh about 2048 cm long). The game lays the
/// pieces along the spline when it builds the track. A level that saved them places them itself
/// (the map reader's spline meshes); for a track whose level holds none, they are laid here as the
/// track lays them: each span between two points cut into pieces of about the piece's length, each
/// piece a spline mesh over its stretch of the curve, turned to the spline's up (its rotation
/// channel; the rotation-minimal frames where a point asks for stable roll) and widened by its scale.
/// Not laid: the transition pieces between styles, end caps, the out-of-bounds tube; nor the per-piece
/// custom primitive data the track's Blueprint sets (its road UVs).
/// </summary>
public static class DelMarTracks
{
    /// <summary>A track actor's property naming its per-point styles (DelMarTrackBase, FortDelMarTrackBase).</summary>
    private const string PointDataProperty = "TrackSplinePointData";

    /// <summary>Tests: a track to lay from an object path (a class default, say) along these points (UE cm), or null.</summary>
    public static (string Actor, Vector3[] Points)? TestTrack;

    /// <summary>
    /// The road pieces of the tracks a level holds whose pieces it didn't save (none when it did, or holds no track).
    /// </summary>
    public static List<MapMesh> Place(IFileProvider provider, string levelPackage, Action<string, int> skip)
    {
        var placed = new List<MapMesh>();
        List<UObject> actors;
        try
        {
            var package = provider.LoadPackage(levelPackage);
            // a track's point data is its own subobject: its name is in the level's name table (no exports read otherwise)
            if (!package.NameMap.Any(n => n.Name?.Contains("TrackPointData", StringComparison.Ordinal) == true)) return placed;
            var exports = package.GetExports().ToList();
            var level = exports.FirstOrDefault(e => e.ExportType == "Level");
            if (level == null) return placed;
            actors = exports.Where(e => e.Outer?.Name.Text == level.Name).ToList();
        }
        catch (Exception e)
        {
            Failures.Note("Rocket Racing track levels", levelPackage, e);
            return placed;
        }
        // the level saved its tracks' pieces (segment actors, DelMarTrackSegmentBase): the map reader placed them
        if (actors.Any(a => Chain(a).Any(x => x.Properties.Any(p => p.Name.Text == "MaterialLayerComponent"))))
        {
            skip("Rocket Racing tracks (their road pieces are in the level)", 1);
            return placed;
        }
        foreach (var actor in actors.Where(a => Chain(a).Any(x => x.Properties.Any(p => p.Name.Text == PointDataProperty))))
        {
            try
            {
                var pieces = Lay(provider, actor, levelPackage, null);
                placed.AddRange(pieces);
                if (pieces.Count == 0) skip("Rocket Racing tracks with no road pieces", 1);
            }
            catch
            {
                skip("unreadable Rocket Racing tracks", 1);
            }
        }
        return placed;
    }

    /// <summary>One track's road pieces; `points` replaces its spline (tests).</summary>
    public static List<MapMesh> Lay(IFileProvider provider, UObject actor, string level, Vector3[]? points)
    {
        var pieces = new List<MapMesh>();
        var spline = Ref(actor, "Spline") ?? Ref(actor, "RootComponent");
        if (spline == null) return pieces;
        var curve = points is { Length: >= 2 } ? TrackCurve.Through(points) : TrackCurve.Read(spline);
        if (curve == null || curve.Spans == 0) return pieces;
        var world = Local(spline);

        var styles = (Ref(actor, PointDataProperty)?.GetOrDefault("MetaData", Array.Empty<FStructFallback>()) ?? []).ToList();
        var palette = Palette(Ref(actor, "TrackPalette_V2") ?? Ref(actor, "TrackPalette"));
        var segments = new Dictionary<string, Segment?>(StringComparer.OrdinalIgnoreCase);
        for (var span = 0; span < curve.Spans; span++)
        {
            // a span wears its first point's style (the last point's, past the styles listed)
            var style = styles.Count == 0 ? null : styles[Math.Min(span, styles.Count - 1)];
            var tag = style?.GetOrDefault<FGameplayTag>("TrackTypeTag").TagName.Text ?? "";
            var classPath = style?.GetOrDefault<FSoftObjectPath>("SegmentClass").AssetPathName.Text is { Length: > 0 } own && own != "None"
                ? own
                : palette.TryGetValue(tag, out var c) ? c : palette.Values.FirstOrDefault();
            if (classPath == null) continue;
            if (!segments.TryGetValue(classPath, out var segment)) segments[classPath] = segment = Segment.Read(provider, classPath);
            if (segment == null) continue;
            var stable = style?.GetOrDefault("bUseStableRoll", false) ?? false;

            var length = curve.Length(span);
            var count = Math.Max(1, (int) Math.Round(length / segment.Length));
            var at = 0f;
            for (var k = 1; k <= count; k++)
            {
                var next = curve.AtDistance(span, length * k / count);
                pieces.Add(new MapMesh
                {
                    Mesh = segment.Mesh, World = world, Overrides = segment.Overrides, Actor = actor.Name, Level = level,
                    Spline = curve.Piece(span, at, next, stable),
                });
                at = next;
            }
        }
        return pieces;
    }

    /// <summary>A palette's style tag -> segment class path (a theme's track types, or the older flat palette).</summary>
    private static Dictionary<string, string> Palette(UObject? palette)
    {
        var map = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        if (palette == null) return map;
        foreach (var trackType in palette.GetOrDefault("Tracks", Array.Empty<FPackageIndex>()))
        {
            UObject? type;
            try { type = trackType.Load(); }
            catch (Exception e) { Failures.Note("Rocket Racing track types", trackType.ToString(), e); continue; }
            foreach (var entry in type?.GetOrDefault("StyleArray", Array.Empty<FStructFallback>()) ?? [])
            {
                var first = entry.GetOrDefault("SegmentActors", Array.Empty<FPackageIndex>()).FirstOrDefault(s => s is { IsNull: false });
                if (first?.ResolvedObject?.GetPathName() is { } path) map.TryAdd(entry.GetOrDefault<FGameplayTag>("StyleTag").TagName.Text, path);
            }
        }
        foreach (var entry in palette.GetOrDefault("Palette", Array.Empty<FStructFallback>()))
            if (entry.GetOrDefault<FSoftObjectPath>("TrackSegmentClass").AssetPathName.Text is { Length: > 0 } path && path != "None")
                map.TryAdd(entry.GetOrDefault<FGameplayTag>("TrackTypeTag").TagName.Text, path);
        return map;
    }

    /// <summary>A road piece: its spline mesh's mesh, materials and length (its class default's).</summary>
    private sealed class Segment
    {
        public string Mesh = "";
        public Dictionary<int, string> Overrides = new();
        public float Length = 2048;

        public static Segment? Read(IFileProvider provider, string classPath)
        {
            if (!provider.TryLoadPackageObject(classPath, out var cls) || cls is not UClass uclass) return null;
            var defaults = uclass.ClassDefaultObject.Load();
            if (defaults == null) return null;
            var component = Ref(defaults, "SplineMesh") ?? Ref(defaults, "RootComponent");
            if (component == null || Ref(component, "StaticMesh") is not { } mesh || mesh.GetPathName() is not { } meshPath) return null;
            var segment = new Segment { Mesh = meshPath };
            foreach (var x in Chain(component))
                if (x.TryGetValue(out FPackageIndex[] materials, "OverrideMaterials"))
                    for (var i = 0; i < materials.Length; i++)
                        if (materials[i] is { IsNull: false } m && m.ResolvedObject?.GetPathName() is { } path) segment.Overrides.TryAdd(i, path);
            // its authored length: the template's spline (the piece as modelled), else the mesh's
            var authored = Chain(component).Select(x => x.TryGetValue(out FStructFallback s, "SplineParams") ? s : null).FirstOrDefault(s => s != null);
            var end = authored?.GetOrDefault("EndPos", new FVector(0, 0, 0)) ?? new FVector(0, 0, 0);
            var start = authored?.GetOrDefault("StartPos", new FVector(0, 0, 0)) ?? new FVector(0, 0, 0);
            var length = (float) (end - start).Size();
            if (length < 1 && mesh is UStaticMesh sm && sm.RenderData?.Bounds is { } b) length = (float) b.BoxExtent.X * 2;
            var scaler = Chain(defaults).Select(x => x.TryGetValue(out float f, "SegmentLengthScaler") ? f : 0).FirstOrDefault(f => f > 0);
            segment.Length = Math.Max(100, (length > 1 ? length : 2048) * (scaler > 0 ? scaler : 1));
            return segment;
        }
    }

    // ------------------------------------------------------------ properties through the template chain

    private static IEnumerable<UObject> Chain(UObject? o)
    {
        for (var i = 0; o != null && i < 12; i++)
        {
            yield return o;
            try { o = o.Template?.Load(); }
            catch (Exception e) { Failures.Note("Rocket Racing templates", o.Name, e); o = null; }
        }
    }

    private static UObject? Ref(UObject o, string name)
    {
        foreach (var x in Chain(o))
            if (x.TryGetValue(out FPackageIndex i, name) && !i.IsNull)
            {
                try { return i.Load(); }
                catch (Exception e) { Failures.Note("Rocket Racing references", name, e); return null; }
            }
        return null;
    }

    private static T Prop<T>(UObject o, string name, T fallback)
    {
        foreach (var x in Chain(o))
            if (x.TryGetValue(out T v, name)) return v;
        return fallback;
    }

    /// <summary>A root component's place in its level (UE, row vectors).</summary>
    private static Matrix4x4 Local(UObject c)
    {
        var l = Prop(c, "RelativeLocation", new FVector(0, 0, 0));
        var r = Prop(c, "RelativeRotation", new FRotator(0, 0, 0)).Quaternion();
        var s = Prop(c, "RelativeScale3D", new FVector(1, 1, 1));
        return Matrix4x4.CreateScale((float) s.X, (float) s.Y, (float) s.Z)
               * Matrix4x4.CreateFromQuaternion(Quaternion.Normalize(new Quaternion((float) r.X, (float) r.Y, (float) r.Z, (float) r.W)))
               * Matrix4x4.CreateTranslation((float) l.X, (float) l.Y, (float) l.Z);
    }

    /// <summary>
    /// A track spline in its component's space: its points' positions and Hermite tangents, rotations and scales
    /// (UE's SplineCurves, else the 5.6 spline's Bezier handles), and the rotation-minimal frames' normals.
    /// </summary>
    public sealed class TrackCurve
    {
        public readonly List<Vector3> Points = [], Arrive = [], Leave = [], Scales = [];
        public readonly List<Quaternion> Rotations = [];
        public readonly List<(float Key, Vector3 Normal)> StableNormals = [];
        public Vector3 DefaultUp = Vector3.UnitZ;
        public bool Closed;
        private readonly Dictionary<int, float[]> lengths = new();

        public int Spans => Points.Count < 2 ? 0 : Closed ? Points.Count : Points.Count - 1;
        private int Next(int span) => (span + 1) % Points.Count;

        public static TrackCurve? Read(UObject spline)
        {
            var curve = new TrackCurve
            {
                Closed = Prop(spline, "bClosedLoop", false),
                DefaultUp = ToVector(Prop(spline, "DefaultUpVector", new FVector(0, 0, 1))),
            };
            var curves = Prop<FStructFallback?>(spline, "SplineCurves", null);
            foreach (var p in curves?.GetOrDefault<FStructFallback?>("Position")?.GetOrDefault("Points", Array.Empty<FStructFallback>()) ?? [])
            {
                curve.Points.Add(ToVector(p.GetOrDefault("OutVal", new FVector(0, 0, 0))));
                curve.Arrive.Add(ToVector(p.GetOrDefault("ArriveTangent", new FVector(0, 0, 0))));
                curve.Leave.Add(ToVector(p.GetOrDefault("LeaveTangent", new FVector(0, 0, 0))));
            }
            foreach (var p in curves?.GetOrDefault<FStructFallback?>("Rotation")?.GetOrDefault("Points", Array.Empty<FStructFallback>()) ?? [])
                curve.Rotations.Add(ToQuat(p.GetOrDefault("OutVal", new FQuat(0, 0, 0, 1))));
            foreach (var p in curves?.GetOrDefault<FStructFallback?>("Scale")?.GetOrDefault("Points", Array.Empty<FStructFallback>()) ?? [])
                curve.Scales.Add(ToVector(p.GetOrDefault("OutVal", new FVector(1, 1, 1))));
            // UE 5.6's spline: per point its arrive handle, value and leave handle (a cubic Bezier's: a third of the tangent)
            var spline56 = Chain(spline).Select(x => x.TryGetValue(out FSpline s, "Spline") ? s : default).FirstOrDefault(s => s.Position != null);
            if (curve.Points.Count < 2 && spline56.Position is { PointCount: >= 2 } position)
            {
                curve.Points.Clear(); curve.Arrive.Clear(); curve.Leave.Clear(); curve.Rotations.Clear(); curve.Scales.Clear();
                for (var i = 0; i < position.PointCount; i++)
                {
                    var p = ToVector((FVector) position.Values[i * 3 + 1]);
                    curve.Points.Add(p);
                    curve.Arrive.Add(3 * (p - ToVector((FVector) position.Values[i * 3])));
                    curve.Leave.Add(3 * (ToVector((FVector) position.Values[i * 3 + 2]) - p));
                }
                if (spline56.Attributes?.GetValueOrDefault("Rotation") is { } rotation && rotation.PointCount == position.PointCount)
                    for (var i = 0; i < rotation.PointCount; i++) curve.Rotations.Add(ToQuat(rotation.Point<FQuat>(i)));
                if (spline56.Attributes?.GetValueOrDefault("Scale") is { } scale && scale.PointCount == position.PointCount)
                    for (var i = 0; i < scale.PointCount; i++) curve.Scales.Add(ToVector(scale.Point<FVector>(i)));
            }
            foreach (var f in Prop(spline, "RotationalMinimalFrameNormals", Array.Empty<FStructFallback>()))
            {
                var normal = ToVector(f.GetOrDefault("Normal", new FVector(0, 0, 0)));
                if (normal.LengthSquared() > 0.5f) curve.StableNormals.Add((f.GetOrDefault("InputKey", 0f), Vector3.Normalize(normal)));
            }
            return curve.Points.Count >= 2 ? curve : null;
        }

        /// <summary>A curve through these points (tests): Catmull-Rom tangents, no roll, unit scale.</summary>
        public static TrackCurve Through(IReadOnlyList<Vector3> points)
        {
            var curve = new TrackCurve();
            for (var i = 0; i < points.Count; i++)
            {
                var t = (points[Math.Min(i + 1, points.Count - 1)] - points[Math.Max(i - 1, 0)]) * (i == 0 || i == points.Count - 1 ? 1f : 0.5f);
                curve.Points.Add(points[i]);
                curve.Arrive.Add(t);
                curve.Leave.Add(t);
            }
            return curve;
        }

        public Vector3 Position(int span, float t)
        {
            var (p0, p1, t0, t1) = (Points[span], Points[Next(span)], Leave[span], Arrive[Next(span)]);
            float t2 = t * t, t3 = t2 * t;
            return (2 * t3 - 3 * t2 + 1) * p0 + (t3 - 2 * t2 + t) * t0 + (-2 * t3 + 3 * t2) * p1 + (t3 - t2) * t1;
        }

        public Vector3 Derivative(int span, float t)
        {
            var (p0, p1, t0, t1) = (Points[span], Points[Next(span)], Leave[span], Arrive[Next(span)]);
            var t2 = t * t;
            return (6 * t2 - 6 * t) * p0 + (3 * t2 - 4 * t + 1) * t0 + (-6 * t2 + 6 * t) * p1 + (3 * t2 - 2 * t) * t1;
        }

        /// <summary>The spline's up at a place: its rotation channel's (UE turns DefaultUpVector by it), or the stable frame's normal.</summary>
        public Vector3 Up(int span, float t, bool stable)
        {
            var key = span + t;
            if (stable && StableNormals.Count > 1)
            {
                var after = StableNormals.FindIndex(f => f.Key >= key);
                if (after <= 0) return StableNormals[after == 0 ? 0 : StableNormals.Count - 1].Normal;
                var (k0, n0) = StableNormals[after - 1];
                var (k1, n1) = StableNormals[after];
                return Vector3.Normalize(Vector3.Lerp(n0, n1, (key - k0) / MathF.Max(k1 - k0, 1e-6f)));
            }
            if (Rotations.Count != Points.Count) return DefaultUp;
            var q = Quaternion.Slerp(Rotations[span], Rotations[Next(span)], t);
            return Vector3.Transform(DefaultUp, q);
        }

        private Vector2 Scale(int span, float t)
        {
            if (Scales.Count != Points.Count) return Vector2.One;
            var s = Vector3.Lerp(Scales[span], Scales[Next(span)], t);
            return new Vector2(s.Y, s.Z);
        }

        /// <summary>A span's arc length table (cm at 64 even steps of its parameter).</summary>
        private float[] Table(int span)
        {
            if (lengths.TryGetValue(span, out var table)) return table;
            table = new float[65];
            var last = Position(span, 0);
            for (var i = 1; i <= 64; i++)
            {
                var p = Position(span, i / 64f);
                table[i] = table[i - 1] + Vector3.Distance(last, p);
                last = p;
            }
            return lengths[span] = table;
        }

        public float Length(int span) => Table(span)[64];

        /// <summary>The span's parameter at a distance along it.</summary>
        public float AtDistance(int span, float distance)
        {
            var table = Table(span);
            if (distance >= table[64]) return 1;
            var i = Array.FindIndex(table, d => d >= distance);
            if (i <= 0) return 0;
            return (i - 1 + (distance - table[i - 1]) / MathF.Max(table[i] - table[i - 1], 1e-6f)) / 64f;
        }

        /// <summary>
        /// A piece over the span's stretch [a, b] as the map reader's spline mesh bend: the stretch's Hermite
        /// (positions and tangents scaled to it), its up as the spline mesh's up direction, the end's roll
        /// to the spline's up there, the spline's scale as the piece's.
        /// </summary>
        public Dictionary<string, object> Piece(int span, float a, float b, bool stable)
        {
            var p0 = Position(span, a);
            var p1 = Position(span, b);
            var t0 = Derivative(span, a) * (b - a);
            var t1 = Derivative(span, b) * (b - a);
            var up = Up(span, a, stable);
            // the up its start has (no roll there), the roll its end needs to reach the spline's up
            var fwd = t1.LengthSquared() > 1e-6f ? Vector3.Normalize(t1) : Vector3.Normalize(p1 - p0);
            var bx = Vector3.Cross(up, fwd);
            var roll = 0f;
            if (bx.LengthSquared() > 1e-8f)
            {
                bx = Vector3.Normalize(bx);
                var by = Vector3.Cross(fwd, bx);
                var end = Up(span, b, stable);
                roll = MathF.Atan2(Vector3.Dot(end, bx), Vector3.Dot(end, by));
            }
            var s0 = Scale(span, a);
            var s1 = Scale(span, b);
            static double[] V3(Vector3 v) => [v.X, v.Y, v.Z];
            return new Dictionary<string, object>
            {
                ["axis"] = 0, ["up"] = V3(up), ["min"] = 0f, ["max"] = 0f, ["smooth"] = false,
                ["p0"] = V3(p0), ["t0"] = V3(t0), ["p1"] = V3(p1), ["t1"] = V3(t1),
                ["s0"] = new double[] { s0.X, s0.Y }, ["s1"] = new double[] { s1.X, s1.Y },
                ["r0"] = 0f, ["r1"] = roll,
                ["o0"] = new double[] { 0, 0 }, ["o1"] = new double[] { 0, 0 },
            };
        }

        private static Vector3 ToVector(FVector v) => new((float) v.X, (float) v.Y, (float) v.Z);

        private static Quaternion ToQuat(FQuat q)
        {
            var quat = new Quaternion((float) q.X, (float) q.Y, (float) q.Z, (float) q.W);
            return quat.LengthSquared() > 1e-8f ? Quaternion.Normalize(quat) : Quaternion.Identity;
        }
    }
}
