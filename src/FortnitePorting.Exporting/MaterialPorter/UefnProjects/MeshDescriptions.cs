using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using CUE4Parse.UE4.Assets;
using CUE4Parse.UE4.Assets.Exports.SkeletalMesh;
using CUE4Parse.UE4.Assets.Exports.StaticMesh;
using CUE4Parse.UE4.Assets.Objects;
using CUE4Parse.UE4.Objects.Core.Misc;
using CUE4Parse.UE4.Objects.UObject;

namespace FortnitePorting.Exporting.MaterialPorter;

/// <summary>An uncooked mesh's geometry (static or skeletal): the FMeshDescription its source model keeps as an editor payload
/// (EditorPayloads), in the engine's new format (MeshDescription.cpp, MeshElementContainer.h, MeshAttributeArray.h):
/// element types, each a container per channel (allocated-ID bits, holes, attributes); attributes bulk-serialized by type;
/// names as strings, de-duplicated since FFortniteMainBranchObjectVersion 257.</summary>
public static class MeshDescriptions
{
    static readonly FGuid FortniteMain = new(0x601D1886, 0xAC644F84, 0xAA16D3DE, 0x0DEAC7D6);

    sealed class Attribute
    {
        public uint Type, Extent;
        public List<byte[]> Channels = [];
        public List<string[]> Names = [];
        public List<int[][]> Arrays = [];     // arrays per element (int32[] attributes, as skin weights)
    }

    sealed class Container
    {
        public int Slots;
        public uint[] Bits = [];
        public Dictionary<string, Attribute> Attributes = new(StringComparer.OrdinalIgnoreCase);
        public bool Has(int id) => id < Slots && (Bits[id >> 5] >> (id & 31) & 1) != 0;
    }

    /// <summary>The mesh's slots: (imported slot name, material path) in StaticMaterials order.</summary>
    public static List<(string Name, string Path)> Slots(UStaticMesh mesh) =>
        (mesh.GetOrDefault<FStructFallback[]>("StaticMaterials") ?? [])
        .Select(m => (m.GetOrDefault<FName>("ImportedMaterialSlotName").Text,
                      m.TryGetValue(out FPackageIndex material, "MaterialInterface") && material.ResolvedObject is { } o ? o.GetPathName() : ""))
        .ToList();

    /// <summary>LOD 0 of the mesh as a raw mesh (one vertex per vertex instance, triangles grouped by slot), or null when
    /// its mesh description can't be read.</summary>
    public static RawSkinnedMesh? Read(UStaticMesh mesh) =>
        Elements(mesh, "StaticMeshDescriptionBulkData") is { } elements ? Build(elements, Slots(mesh)) : null;

    /// <summary>A skeletal mesh's slots: (imported slot name, else slot name; material path).</summary>
    public static List<(string Name, string Path)> Slots(USkeletalMesh mesh) =>
        (mesh.SkeletalMaterials ?? []).Select(m => ((m.ImportedMaterialSlotName ?? m.MaterialSlotName).Text,
            m.MaterialInterface?.ResolvedObject is { } o ? o.GetPathName() : "")).ToList();

    /// <summary>A bone of a skeletal mesh description: its name, parent and pose (relative to its parent).</summary>
    public sealed record Bone(string Name, int Parent, System.Numerics.Vector3 Translation, System.Numerics.Quaternion Rotation);

    /// <summary>LOD 0 of a skeletal mesh as a raw mesh weighted to its own bones (the mesh description's), or null.</summary>
    public static (RawSkinnedMesh Mesh, List<Bone> Bones)? Read(USkeletalMesh mesh)
    {
        // the bones' element type is saved as "BonesElementName"
        if (Elements(mesh, "MeshDescriptionBulkData") is not { } elements
            || elements.FirstOrDefault(e => e.Key.StartsWith("Bones", StringComparison.OrdinalIgnoreCase)).Value is not { } boneTypes) return null;
        var raw = Build(elements, Slots(mesh));
        var bones = boneTypes[0];
        var names = bones.Attributes["Name"].Names[0];
        var parents = bones.Attributes["ParentIndex"].Channels[0];
        var poses = bones.Attributes["Pose"].Channels[0];
        var list = new List<Bone>();
        for (var b = 0; b < bones.Slots; b++)
        {
            double D(int i) => BitConverter.ToDouble(poses, b * 80 + i * 8);
            list.Add(new Bone(names[b], BitConverter.ToInt32(parents, b * 4), new((float) D(4), (float) D(5), (float) D(6)),
                new((float) D(0), (float) D(1), (float) D(2), (float) D(3))));
        }
        // a vertex instance's weights are its vertex's: bone index (high 16 bits) and weight (low 16 bits, of 65535)
        var weights = elements["Vertices"][0].Attributes["SkinWeights"].Arrays[0];
        var instances = elements["VertexInstances"][0];
        var vertexOf = instances.Attributes["VertexIndex"].Channels[0];
        var v = 0;
        for (var i = 0; i < instances.Slots; i++)
        {
            if (!instances.Has(i)) continue;
            foreach (var packed in weights[BitConverter.ToInt32(vertexOf, i * 4)])
                raw.Weights.Add(((short) ((uint) packed >> 16), v, (packed & 0xFFFF) / 65535f));
            v++;
        }
        return (raw, list);
    }

    /// <summary>What a mesh's description holds (element types and their attributes), for the owner's test routes.</summary>
    public static Dictionary<string, string[]>? Describe(global::CUE4Parse.UE4.Assets.Exports.UObject mesh, string bulkProperty) =>
        Elements(mesh, bulkProperty)?.ToDictionary(e => e.Key, e => e.Value[0].Attributes.Select(a => $"{a.Key}:{a.Value.Type}x{a.Value.Extent}").ToArray());

    // The mesh's LOD 0 mesh description: its source model's bulk data export names its payload (FEditorBulkData's
    // PayloadContentId) in the export's bytes.
    static Dictionary<string, List<Container>>? Elements(global::CUE4Parse.UE4.Assets.Exports.UObject mesh, string bulkProperty)
    {
        if (mesh.Owner is not Package package || EditorPayloads.FileOf(package) is not { } file) return null;
        if (mesh.GetOrDefault<FStructFallback[]>("SourceModels") is not [var lod0, ..]
            || !lod0.TryGetValue(out FPackageIndex bulk, bulkProperty) || !bulk.IsExport) return null;
        var export = package.ExportMap[bulk.Index - 1];
        var body = file[(int) export.SerialOffset..(int) (export.SerialOffset + export.SerialSize)];
        if (EditorPayloads.Of(file).FirstOrDefault(p => body.AsSpan().IndexOf(p.Id) >= 0) is not { } payload) return null;
        var newNames = package.Summary.CustomVersionContainer?.GetVersion(FortniteMain) is >= 257;
        return Parse(EditorPayloads.Decompress(file, payload.At), newNames);
    }

    sealed class Reader(byte[] d)
    {
        int p;
        public int I32() { var v = BitConverter.ToInt32(d, p); p += 4; return v; }
        public uint U32() { var v = BitConverter.ToUInt32(d, p); p += 4; return v; }
        public byte U8() => d[p++];
        public ushort U16() { var v = BitConverter.ToUInt16(d, p); p += 2; return v; }
        public byte[] Bytes(int n) { var v = d[p..(p + n)]; p += n; return v; }
        public string Text()
        {
            var n = I32();
            if (n == 0) return "";
            string s;
            if (n > 0) { s = Encoding.Latin1.GetString(d, p, n - 1); p += n; }
            else { s = Encoding.Unicode.GetString(d, p, (-n - 1) * 2); p += -n * 2; }
            return s;
        }
    }

    // sizes of the bulk types: FVector4f, FVector3f, FVector2f, float, int32, bool
    static readonly int[] Sizes = [16, 12, 8, 4, 4, 1];

    static Dictionary<string, List<Container>> Parse(byte[] payload, bool newNames)
    {
        var r = new Reader(payload);
        var types = new Dictionary<string, List<Container>>(StringComparer.OrdinalIgnoreCase);
        var count = r.I32();
        for (var t = 0; t < count; t++)
        {
            var name = r.Text();
            var channels = r.I32();
            types[name] = Enumerable.Range(0, channels).Select(_ => ContainerOf(r, newNames)).ToList();
        }
        return types;
    }

    static Container ContainerOf(Reader r, bool newNames)
    {
        var c = new Container { Slots = r.I32() };
        c.Bits = Enumerable.Range(0, (c.Slots + 31) / 32).Select(_ => r.U32()).ToArray();
        r.I32();     // holes
        r.I32();     // the attributes' element count
        var attributes = r.I32();
        for (var a = 0; a < attributes; a++)
        {
            var name = r.Text();
            var at = new Attribute { Type = r.U32(), Extent = r.U32() };
            if (at.Extent == 0)
            {
                Unbounded(r, at);
                c.Attributes[name] = at;
                continue;
            }
            r.I32();     // elements
            var channels = r.I32();
            for (var ch = 0; ch < channels; ch++)
            {
                r.U32();     // extent
                if (at.Type <= 5)
                {
                    var size = r.I32();
                    at.Channels.Add(r.Bytes(size * r.I32()));
                }
                else if (at.Type == 6) at.Names.Add(NamesOf(r, newNames));
                else at.Channels.Add(r.Bytes(r.I32() * 80));     // FTransforms: rotation, translation, scale as doubles
            }
            if (at.Type <= 4) r.Bytes(Sizes[at.Type]);     // default value
            else if (at.Type == 5) r.U32();
            else if (at.Type == 6) r.Text();
            else r.Bytes(80);
            r.U32();     // flags
            c.Attributes[name] = at;
        }
        return c;
    }

    // An attribute of arrays per element (TMeshUnboundedAttributeArraySet): per channel, chunks of 256 elements, each its
    // packed data then every element's start, count and capacity. Only int32 arrays are kept (skin weights).
    static void Unbounded(Reader r, Attribute at)
    {
        r.I32();     // elements
        var channels = r.I32();
        for (var ch = 0; ch < channels; ch++)
        {
            var elements = new List<int[]>();
            var chunks = r.I32();
            for (var k = 0; k < chunks; k++)
            {
                var count = r.I32();
                var data = Enumerable.Range(0, count).Select(_ => Value(r, at.Type)).ToArray();
                var n = r.I32();
                var start = Enumerable.Range(0, n).Select(_ => r.I32()).ToArray();
                var lengths = Enumerable.Range(0, n).Select(_ => r.I32()).ToArray();
                for (var i = 0; i < n; i++) r.I32();     // capacity
                for (var i = 0; i < n; i++)
                    elements.Add(at.Type == 4 ? data.Skip(start[i]).Take(lengths[i]).Select(o => (int) o!).ToArray() : []);
            }
            r.I32();     // elements
            Value(r, at.Type);     // default
            at.Arrays.Add(elements.ToArray());
        }
        Value(r, at.Type);     // default
        r.U32();     // flags
    }

    // one value as a plain archive writes it (bool as 4 bytes, names as strings)
    static object? Value(Reader r, uint type) => type switch
    {
        4 => r.I32(),
        5 => r.U32(),
        6 => r.Text(),
        7 => r.Bytes(80),
        _ => r.Bytes(Sizes[type]),
    };

    static string[] NamesOf(Reader r, bool deduplicated)
    {
        if (!deduplicated) return Enumerable.Range(0, r.I32()).Select(_ => r.Text()).ToArray();
        var count = (int) r.U32();
        if (count == 0) return [];
        var unique = Enumerable.Range(0, (int) r.U32()).Select(_ => r.Text()).ToArray();
        if (unique.Length == 1) return Enumerable.Repeat(unique[0], count).ToArray();
        if (unique.Length == count) return unique;
        var size = r.U8();
        return Enumerable.Range(0, count).Select(_ => unique[size == 0 ? r.U8() : size == 1 ? r.U16() : (int) r.U32()]).ToArray();
    }

    static RawSkinnedMesh Build(Dictionary<string, List<Container>> m, List<(string Name, string Path)> slots)
    {
        var vertices = m["Vertices"][0];
        var instances = m["VertexInstances"][0];
        var triangles = m["Triangles"][0];
        var groups = m["PolygonGroups"][0];
        var position = vertices.Attributes["Position "].Channels[0];
        var vertexOf = instances.Attributes["VertexIndex"].Channels[0];
        byte[]? Channel(Container c, string name, int i = 0) => c.Attributes.TryGetValue(name, out var a) && i < a.Channels.Count ? a.Channels[i] : null;
        var normal = Channel(instances, "Normal");
        var tangent = Channel(instances, "Tangent");
        var sign = Channel(instances, "BinormalSign");
        var color = Channel(instances, "Color");
        var uvChannels = instances.Attributes.TryGetValue("TextureCoordinate", out var uvs) ? uvs.Channels : [];
        var corners = triangles.Attributes["VertexInstanceIndex"].Channels[0];
        var groupOf = Channel(triangles, "PolygonGroupIndex");

        // a vertex per allocated instance
        var index = new int[instances.Slots];
        var n = 0;
        for (var i = 0; i < instances.Slots; i++) index[i] = instances.Has(i) ? n++ : -1;
        float F(byte[] b, int i) => BitConverter.ToSingle(b, i * 4);
        var raw = new RawSkinnedMesh
        {
            VertexCount = n,
            Positions = new float[n * 3], Normals = new float[n * 4], Tangents = new float[n * 3],
            Uvs = uvChannels.Select(_ => new float[n * 2]).ToList(),
            Colors = color == null ? null : new byte[n * 4],
        };
        for (var i = 0; i < instances.Slots; i++)
        {
            var v = index[i];
            if (v < 0) continue;
            var p = BitConverter.ToInt32(vertexOf, i * 4);
            for (var k = 0; k < 3; k++)
            {
                raw.Positions[v * 3 + k] = F(position, p * 3 + k);
                raw.Normals[v * 4 + k] = normal == null ? (k == 2 ? 1 : 0) : F(normal, i * 3 + k);
                raw.Tangents[v * 3 + k] = tangent == null ? (k == 0 ? 1 : 0) : F(tangent, i * 3 + k);
            }
            raw.Normals[v * 4 + 3] = sign == null ? 1 : F(sign, i);
            for (var c = 0; c < uvChannels.Count; c++)
            {
                raw.Uvs[c][v * 2] = F(uvChannels[c], i * 2);
                raw.Uvs[c][v * 2 + 1] = F(uvChannels[c], i * 2 + 1);
            }
            if (color != null)
                for (var k = 0; k < 4; k++)
                    raw.Colors![v * 4 + k] = (byte) Math.Clamp(MathF.Round((k < 3 ? Srgb(F(color, i * 4 + k)) : F(color, i * 4 + k)) * 255), 0, 255);
        }

        // triangles by slot (its group's imported slot name in StaticMaterials, else the group's order)
        var groupNames = groups.Attributes.TryGetValue("ImportedMaterialSlotName", out var names) && names.Names.Count > 0 ? names.Names[0] : [];
        int SlotOf(int group)
        {
            var name = group < groupNames.Length ? groupNames[group] : null;
            var at = name == null ? -1 : slots.FindIndex(s => s.Name.Equals(name, StringComparison.OrdinalIgnoreCase));
            return at >= 0 ? at : Math.Max(0, group);
        }
        var bySlot = new SortedDictionary<int, List<int>>();
        for (var t = 0; t < triangles.Slots; t++)
        {
            if (!triangles.Has(t)) continue;
            var slot = SlotOf(groupOf == null ? 0 : BitConverter.ToInt32(groupOf, t * 4));
            if (!bySlot.TryGetValue(slot, out var list)) bySlot[slot] = list = [];
            list.Add(t);
        }
        var indices = new List<int>();
        foreach (var (slot, list) in bySlot)
        {
            raw.Sections.Add((slot, indices.Count, list.Count));
            foreach (var t in list)
                for (var k = 0; k < 3; k++) indices.Add(index[BitConverter.ToInt32(corners, (t * 3 + k) * 4)]);
        }
        raw.Indices = indices.ToArray();
        if (normal == null || normal.All(b => b == 0)) ComputeNormals(raw, m["Edges"][0], triangles, corners, vertexOf, index);
        return raw;
    }

    // A mesh built with "recompute normals" keeps none: as the build does, each corner gets the angle-weighted normals of the
    // triangles around its vertex that it reaches without crossing a hard edge.
    static void ComputeNormals(RawSkinnedMesh raw, Container edges, Container triangles, byte[] corners, byte[] vertexOf, int[] index)
    {
        var hard = new HashSet<(int, int)>();
        if (edges.Attributes.TryGetValue("IsHard", out var isHard) && edges.Attributes.TryGetValue("VertexIndex", out var ends))
            for (var e = 0; e < edges.Slots; e++)
                if (edges.Has(e) && isHard.Channels[0][e] != 0)
                {
                    int a = BitConverter.ToInt32(ends.Channels[0], e * 8), b = BitConverter.ToInt32(ends.Channels[0], e * 8 + 4);
                    hard.Add((Math.Min(a, b), Math.Max(a, b)));
                }
        System.Numerics.Vector3 P(int instance) => new(raw.Positions[index[instance] * 3], raw.Positions[index[instance] * 3 + 1], raw.Positions[index[instance] * 3 + 2]);
        int VertexOf(int instance) => BitConverter.ToInt32(vertexOf, instance * 4);

        // per triangle: its corners' instances, positions' vertices, face normal and corner angles
        var faces = new List<(int[] Instances, int[] Vertices, System.Numerics.Vector3 Normal, float[] Angles)>();
        var around = new Dictionary<int, List<(int Face, int Corner)>>();
        for (var t = 0; t < triangles.Slots; t++)
        {
            if (!triangles.Has(t)) continue;
            var inst = Enumerable.Range(0, 3).Select(k => BitConverter.ToInt32(corners, (t * 3 + k) * 4)).ToArray();
            var verts = inst.Select(VertexOf).ToArray();
            var p = inst.Select(P).ToArray();
            var n = System.Numerics.Vector3.Cross(p[2] - p[0], p[1] - p[0]);
            if (n.LengthSquared() > 0) n = System.Numerics.Vector3.Normalize(n);
            var angles = new float[3];
            for (var k = 0; k < 3; k++)
            {
                var a = p[(k + 1) % 3] - p[k];
                var b = p[(k + 2) % 3] - p[k];
                angles[k] = a.LengthSquared() > 0 && b.LengthSquared() > 0
                    ? MathF.Acos(Math.Clamp(System.Numerics.Vector3.Dot(System.Numerics.Vector3.Normalize(a), System.Numerics.Vector3.Normalize(b)), -1, 1)) : 0;
            }
            var face = faces.Count;
            faces.Add((inst, verts, n, angles));
            for (var k = 0; k < 3; k++)
            {
                if (!around.TryGetValue(verts[k], out var list)) around[verts[k]] = list = [];
                list.Add((face, k));
            }
        }

        foreach (var (vertex, list) in around)
        {
            // the triangles around the vertex, joined where they share a soft edge through it
            var group = Enumerable.Range(0, list.Count).ToArray();
            int Find(int i) => group[i] == i ? i : group[i] = Find(group[i]);
            for (var i = 0; i < list.Count; i++)
                for (var j = i + 1; j < list.Count; j++)
                {
                    var a = faces[list[i].Face].Vertices;
                    var b = faces[list[j].Face].Vertices;
                    foreach (var other in a)
                        if (other != vertex && b.Contains(other) && !hard.Contains((Math.Min(vertex, other), Math.Max(vertex, other))))
                            group[Find(i)] = Find(j);
                }
            var sums = new Dictionary<int, System.Numerics.Vector3>();
            for (var i = 0; i < list.Count; i++)
            {
                var (face, corner) = list[i];
                sums[Find(i)] = sums.GetValueOrDefault(Find(i)) + faces[face].Normal * faces[face].Angles[corner];
            }
            for (var i = 0; i < list.Count; i++)
            {
                var (face, corner) = list[i];
                var n = sums[Find(i)];
                n = n.LengthSquared() > 0 ? System.Numerics.Vector3.Normalize(n) : faces[face].Normal;
                var v = index[faces[face].Instances[corner]];
                raw.Normals[v * 4] = n.X;
                raw.Normals[v * 4 + 1] = n.Y;
                raw.Normals[v * 4 + 2] = n.Z;
            }
        }
    }

    static float Srgb(float linear) => linear <= 0.0031308f ? linear * 12.92f : 1.055f * MathF.Pow(linear, 1 / 2.4f) - 0.055f;
}
