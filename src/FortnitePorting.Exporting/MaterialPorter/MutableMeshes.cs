#nullable disable
using global::CUE4Parse.FileProvider;
using global::CUE4Parse.UE4.Assets.Exports.CustomizableObject;
using global::CUE4Parse.UE4.Assets.Exports.CustomizableObject.Mutable;
using global::CUE4Parse.UE4.Assets.Exports.CustomizableObject.Mutable.Mesh;
using global::CUE4Parse.UE4.Assets.Exports.CustomizableObject.Mutable.Mesh.Buffers;
using System.Runtime.InteropServices;
using global::CUE4Parse.FileProvider.Vfs;
using global::CUE4Parse.UE4.Assets.Readers;
using global::CUE4Parse.UE4.IO;
using global::CUE4Parse.UE4.IO.Objects;
using global::CUE4Parse.UE4.IO.Objects.OnDemand;
using global::CUE4Parse.UE4.Readers;

namespace FortnitePorting.Exporting.MaterialPorter;

/// <summary>
/// A cooked Mutable program's constant meshes (the parts its mesh ops start from), read as stored: each constant is a range of
/// content parts (geometry, pose, physics, metadata), each either kept in the program (ConstantMeshesPermanent) or streamed as a
/// ROM, whose bytes are a block of the model's streamable bulk data (UModelStreamableData: ROM id -> file, offset; the ROM table
/// gives its size).
/// </summary>
public sealed class MutableMeshes
{
    public FProgram Program { get; }
    public FModelStreamableBulkData? Streaming { get; }
    readonly IFileProvider provider;
    readonly ulong packageId;
    readonly Dictionary<uint, FArchive?> files = new();

    MutableMeshes(IFileProvider provider, ulong packageId, FProgram program, FModelStreamableBulkData? streaming)
    {
        this.provider = provider;
        this.packageId = packageId;
        Program = program;
        Streaming = streaming;
    }

    public int Count => Program.ConstantMeshes?.Length ?? 0;

    public static async Task<MutableMeshes> LoadAsync(IFileProvider provider, string coPackage)
    {
        var pkg = await provider.LoadPackageAsync(coPackage);
        var exports = pkg.GetExports().ToList();
        var co = exports.OfType<UCustomizableObject>().First();
        var streaming = exports.OfType<UModelStreamableData>().FirstOrDefault()?.StreamingData;
        var id = provider.TryGetGameFile(coPackage + ".uasset", out var file) && file is FIoStoreEntry entry ? entry.ChunkId.ChunkId : 0;
        return new MutableMeshes(provider, id, co.Model?.Program ?? throw new InvalidDataException("no Mutable model in " + coPackage), streaming);
    }

    /// <summary>A constant mesh's content parts, in flag order (geometry, pose, physics, metadata).</summary>
    public List<(EMeshContentFlags Content, FMesh? Mesh, bool Streamed)> Contents(int constant)
    {
        var range = Program.ConstantMeshes![constant];
        var list = new List<(EMeshContentFlags, FMesh?, bool)>();
        var k = 0;
        for (var bit = EMeshContentFlags.GeometryData; bit <= EMeshContentFlags.LastFlag; bit = (EMeshContentFlags)((int)bit << 1))
        {
            if (!range.ContentFlags.HasFlag(bit)) continue;
            var index = Program.ConstantMeshContentIndices![range.FirstIndex + k++];
            list.Add(index.Streamable ? (bit, Rom(index.Index), true) : (bit, Program.ConstantMeshesPermanent![index.Index], false));
        }
        return list;
    }

    /// <summary>A streamed mesh ROM, read from its block of the model's bulk data.</summary>
    public FMesh? Rom(uint id)
    {
        if (Streaming?.ModelStreamables is null || !Streaming.ModelStreamables.TryGetValue(id, out var block)) return null;
        if (!files.TryGetValue(block.FileId, out var file))
            files[block.FileId] = file = OpenFile(block.FileId);
        if (file is null) return null;
        file.Position = (long)block.Offset;
        return new FMesh(new FMutableArchive(file));
    }

    /// <summary>
    /// One of the model's streamable bulk files: the package's bulk data at different cooked indices, as IoStore chunks of the
    /// package's id, type BulkData, with the cooked index in the id's padding byte (CUE4Parse's reader maps the package's .ubulk
    /// to only one of them).
    /// </summary>
    public FArchive? OpenFile(uint fileId)
    {
        var bulk = Streaming!.StreamableBulkData[fileId];
        // 8 bytes package id, 2 bytes chunk index (0), the cooked index, the chunk type
        var raw = new byte[12];
        BitConverter.TryWriteBytes(raw.AsSpan(0, 8), packageId);
        raw[10] = bulk.Header.CookedIndex.Value;
        raw[11] = (byte)EIoChunkType5.BulkData;
        var chunk = MemoryMarshal.Read<FIoChunkId>(raw);
        if (packageId != 0 && provider is AbstractVfsFileProvider vfs)
            foreach (var reader in vfs.MountedVfs.OfType<IoStoreReader>())
            {
                if (!reader.TryResolve(chunk, out _)) continue;
                var data = reader is IoStoreOnDemandReader onDemand ? ReadOnDemand(onDemand, chunk) : reader.Read(chunk);
                if (data is null) continue;
                if (bulk.Header.OffsetInFile > 0 || data.Length != bulk.Header.SizeOnDisk)
                    data = data.AsSpan((int)bulk.Header.OffsetInFile, (int)Math.Min(bulk.Header.SizeOnDisk, data.Length - bulk.Header.OffsetInFile)).ToArray();
                return new FByteArchive("mutable rom file " + fileId, data, reader.Versions);
            }
        return bulk.TryCreateReader("mutable rom file " + fileId, out var own) ? own : null;
    }

    /// <summary>
    /// A chunk of a CDN-streamed container, matched on all of its id: CUE4Parse's lookup compares ids without the index and padding
    /// bytes, so it returns the package's first bulk chunk for every cooked index.
    /// </summary>
    static byte[]? ReadOnDemand(IoStoreOnDemandReader reader, FIoChunkId chunk)
    {
        if (!reader.TryResolve(chunk, out var place)) return null;
        FOnDemandFileEntry? entry = null;
        switch (reader.Container)
        {
            case global::CUE4Parse.UE4.IO.Objects.OnDemand.V2.FOnDemandContainerEntry v2 when v2.ChunkIds is { } ids && v2.ChunkEntries is { } chunks:
                for (var i = 0; i < ids.Length; i++)
                {
                    if (!Same(ids[i], chunk)) continue;
                    var part = chunks[i].PartitionIndex;
                    entry = v2.PartitionEntries is { } parts && parts.Length > part
                        ? new FOnDemandFileEntry(parts[part].Hash, chunks[i].PartitionOffset, "iopart")
                        : new FOnDemandFileEntry(chunks[i]);
                    break;
                }
                break;
            case global::CUE4Parse.UE4.IO.Objects.OnDemand.V1.FOnDemandContainerEntry v1:
                foreach (var e in v1.Entries)
                    if (Same(e.ChunkId, chunk)) { entry = new FOnDemandFileEntry(e.Hash); break; }
                break;
        }
        if (entry is null) return null;
        var read = typeof(IoStoreOnDemandReader).GetMethod("Read", System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance,
            [typeof(FOnDemandFileEntry), typeof(long), typeof(long)]);
        return read?.Invoke(reader, [entry.Value, (long)place.Offset, (long)place.Length]) as byte[];
    }

    static bool Same(FIoChunkId a, FIoChunkId b) =>
        MemoryMarshal.AsBytes(new ReadOnlySpan<FIoChunkId>(in a)).SequenceEqual(MemoryMarshal.AsBytes(new ReadOnlySpan<FIoChunkId>(in b)));

    /// <summary>Every IoStore chunk of the program's package, for exploration: container, type, chunk index, size.</summary>
    public IEnumerable<string> PackageChunks()
    {
        if (provider is not AbstractVfsFileProvider vfs) yield break;
        foreach (var reader in vfs.MountedVfs.OfType<IoStoreReader>())
        {
            var ids = reader.TocResource.ChunkIds;
            for (var i = 0; i < ids.Length; i++)
                if (ids[i].ChunkId == packageId)
                {
                    reader.TryResolve(ids[i], out var ol);
                    yield return $"{reader.Name}: type {ids[i].ChunkType} index {ids[i]._chunkIndex:X4} pad {ids[i]._padding} length {ol.Length}";
                }
        }
    }

    public uint RomSize(uint id) => Program.Roms is { } roms && id < roms.Length ? roms[id].Size : 0;

    /// <summary>Where a constant's content slots point, for exploration: index, streamed, ROM block and size.</summary>
    public string Slots(int constant)
    {
        var range = Program.ConstantMeshes![constant];
        var parts = new List<string> { $"flags {range.ContentFlags} first {range.FirstIndex}" };
        for (var k = 0; k < 4; k++)
        {
            var index = Program.ConstantMeshContentIndices![range.FirstIndex + k];
            var block = Streaming?.ModelStreamables is { } ms && ms.TryGetValue(index.Index, out var b) ? $"file {b.FileId} @{b.Offset} flags {b.Flags}" : "no block";
            parts.Add($"{index.Index}{(index.Streamable ? "s" : "p")} {block} size {RomSize(index.Index)}");
        }
        return string.Join("; ", parts);
    }

    /// <summary>
    /// A constant as a skinned mesh: its geometry's vertices, its surfaces as sections, and each vertex's influences on the
    /// skeleton's bones (the pose part's bone map, ids matched to the skeleton's names by <see cref="CityHash32.BoneId"/>).
    /// </summary>
    public RawSkinnedMesh? Skinned(int constant, IReadOnlyList<string> skeletonBones)
    {
        var parts = Contents(constant);
        var geo = parts.FirstOrDefault(x => x.Content == EMeshContentFlags.GeometryData).Mesh;
        var pose = parts.FirstOrDefault(x => x.Content == EMeshContentFlags.PoseData).Mesh;
        if (geo is null || geo.VertexBuffers.ElementCount == 0) return null;
        var vb = geo.VertexBuffers;
        var n = (int)vb.ElementCount;
        var mesh = new RawSkinnedMesh { VertexCount = n };
        mesh.Positions = Channel(vb, EMeshBufferSemantic.Position, 0, out var pc) is { } pos ? Take(pos, pc, 3) : throw new InvalidDataException("no positions");
        mesh.Normals = Channel(vb, EMeshBufferSemantic.Normal, 0, out var nc) is { } nrm ? Take(nrm, nc, 4, 1f) : new float[n * 4];
        mesh.Tangents = Channel(vb, EMeshBufferSemantic.Tangent, 0, out var tc) is { } tan ? Take(tan, tc, 3) : new float[n * 3];
        for (var k = 0; k < 8; k++)
            if (Channel(vb, EMeshBufferSemantic.TexCoords, k, out var uc) is { } uv) mesh.Uvs.Add(Take(uv, uc, 2));
            else break;
        if (Channel(vb, EMeshBufferSemantic.Color, 0, out var cc) is { } col)
            mesh.Colors = Rgba(Take(col, cc, 4, 1f), ColorIsBgra(vb)).Select(x => (byte)Math.Clamp(MathF.Round(x * 255f), 0, 255)).ToArray();
        mesh.Indices = Indices(geo.IndexBuffers) ?? throw new InvalidDataException("no indices");

        var byId = new Dictionary<uint, short>();
        for (var b = 0; b < skeletonBones.Count; b++) byId.TryAdd(CityHash32.BoneId(skeletonBones[b]), (short)b);
        var boneMap = pose?.BoneMap ?? geo.BoneMap ?? [];
        var surfaces = geo.Surfaces ?? [];
        var surfaceOf = new int[n];
        for (var s = 0; s < surfaces.Length; s++)
        {
            foreach (var sub in surfaces[s].SubMeshes)
            {
                for (var v = sub.VertexBegin; v < sub.VertexEnd && v < n; v++) surfaceOf[v] = s;
                if (sub.IndexEnd > sub.IndexBegin)
                    mesh.Sections.Add((s, sub.IndexBegin, (sub.IndexEnd - sub.IndexBegin) / 3));
            }
        }
        if (mesh.Sections.Count == 0) mesh.Sections.Add((0, 0, mesh.Indices.Length / 3));
        for (var set = 0; set < 4; set++)
        {
            var bi = Channel(vb, EMeshBufferSemantic.BoneIndices, set, out var bic);
            var bw = Channel(vb, EMeshBufferSemantic.BoneWeights, set, out var bwc);
            if (bi is null || bw is null) break;
            for (var v = 0; v < n; v++)
            {
                var start = surfaces.Length > 0 ? (int)surfaces[surfaceOf[v]].BoneMapIndex : 0;
                for (var c = 0; c < Math.Min(bic, bwc); c++)
                {
                    var w = bw[v * bwc + c];
                    if (w <= 0) continue;
                    var at = start + (int)bi[v * bic + c];
                    if (at < boneMap.Length && byId.TryGetValue(boneMap[at].Id, out var bone))
                        mesh.Weights.Add((bone, v, w));
                    else mesh.UnknownBones++;
                }
            }
        }
        return mesh;
    }

    /// <summary>A mask constant's vertices (a remove op's: indices of the mesh it removes from).</summary>
    public HashSet<int> MaskVertices(int constant)
    {
        var geo = Contents(constant).FirstOrDefault(x => x.Content == EMeshContentFlags.GeometryData).Mesh;
        var ids = geo is null ? null : Channel(geo.VertexBuffers, EMeshBufferSemantic.VertexIndex, 0, out _);
        return ids is null ? new HashSet<int>() : ids.Select(x => (int)x).ToHashSet();
    }

    /// <summary>n x `from` components as n x `to` (missing ones `fill`).</summary>
    /// <summary>
    /// Whether a buffer set's colour channel holds 8-bit colours, which are UE's FColor bytes, B G R A (a Mutable-built wheel's
    /// tire mask, R in the cooked mesh, came out in B, so the tire drew the rim's masks).
    /// </summary>
    static bool ColorIsBgra(FMeshBufferSet set) =>
        set.Buffers.SelectMany(b => b.Channels).FirstOrDefault(c => c.Semantic == EMeshBufferSemantic.Color && c.SemanticIndex == 0) is { } ch
        && ch.Format is EMeshBufferFormat.NUInt8 or EMeshBufferFormat.UInt8 && ch.ComponentCount >= 3;

    /// <summary>Colours (n x 4) as R G B A, from B G R A when asked.</summary>
    static float[] Rgba(float[] colours, bool bgra)
    {
        if (!bgra) return colours;
        for (var i = 0; i + 2 < colours.Length; i += 4)
            (colours[i], colours[i + 2]) = (colours[i + 2], colours[i]);
        return colours;
    }

    static float[] Take(float[] src, int from, int to, float fill = 0f)
    {
        var n = src.Length / Math.Max(1, from);
        var dst = new float[n * to];
        for (var v = 0; v < n; v++)
        for (var c = 0; c < to; c++)
            dst[v * to + c] = c < from ? src[v * from + c] : fill;
        return dst;
    }

    // ------------------------------------------------------------ buffers

    /// <summary>One vertex channel (semantic, index) of a buffer set as floats, n x components; null if absent.</summary>
    public static float[]? Channel(FMeshBufferSet set, EMeshBufferSemantic semantic, int semanticIndex, out int components)
    {
        components = 0;
        foreach (var buffer in set.Buffers)
        foreach (var ch in buffer.Channels)
        {
            if (ch.Semantic != semantic || ch.SemanticIndex != semanticIndex) continue;
            components = ch.ComponentCount;
            var n = (int)set.ElementCount;
            var result = new float[n * components];
            for (var v = 0; v < n; v++)
            for (var c = 0; c < components; c++)
                result[v * components + c] = Read(buffer.Data, (int)(v * buffer.ElementSize) + ch.Offset, ch.Format, c);
            return result;
        }
        return null;
    }

    public static int[]? Indices(FMeshBufferSet set)
    {
        var f = Channel(set, EMeshBufferSemantic.VertexIndex, 0, out _);
        return f?.Select(x => (int)x).ToArray();
    }

    public static string Describe(FMeshBufferSet set) => string.Join(" | ", set.Buffers.Select(b =>
        string.Join(",", b.Channels.Select(c => $"{c.Semantic}{c.SemanticIndex}:{c.Format}x{c.ComponentCount}")) + $" ({b.ElementSize} B)"));

    static float Read(byte[] data, int offset, EMeshBufferFormat format, int component)
    {
        switch (format)
        {
            case EMeshBufferFormat.Float32: return BitConverter.ToSingle(data, offset + component * 4);
            case EMeshBufferFormat.Float16: return (float)BitConverter.ToHalf(data, offset + component * 2);
            case EMeshBufferFormat.Float64: return (float)BitConverter.ToDouble(data, offset + component * 8);
            case EMeshBufferFormat.UInt8: return data[offset + component];
            case EMeshBufferFormat.Int8: return (sbyte)data[offset + component];
            case EMeshBufferFormat.UInt16: return BitConverter.ToUInt16(data, offset + component * 2);
            case EMeshBufferFormat.Int16: return BitConverter.ToInt16(data, offset + component * 2);
            case EMeshBufferFormat.UInt32: return BitConverter.ToUInt32(data, offset + component * 4);
            case EMeshBufferFormat.Int32: return BitConverter.ToInt32(data, offset + component * 4);
            case EMeshBufferFormat.NUInt8: return data[offset + component] / 255f;
            case EMeshBufferFormat.NUInt16: return BitConverter.ToUInt16(data, offset + component * 2) / 65535f;
            case EMeshBufferFormat.NUInt32: return (float)(BitConverter.ToUInt32(data, offset + component * 4) / (double)uint.MaxValue);
            case EMeshBufferFormat.NInt8: return Math.Max(-1f, (sbyte)data[offset + component] / 127f);
            case EMeshBufferFormat.NInt16: return Math.Max(-1f, BitConverter.ToInt16(data, offset + component * 2) / 32767f);
            case EMeshBufferFormat.PackedDir8:
            case EMeshBufferFormat.PackedDir8_W_TangentSign:
                return data[offset + component] / 127.5f - 1f;
            case EMeshBufferFormat.PackedDirS8:
            case EMeshBufferFormat.PackedDirS8_W_TangentSign:
                return (sbyte)data[offset + component] / 127f;
            default: throw new NotSupportedException("Mutable buffer format " + format);
        }
    }
}

/// <summary>A skinned mesh as arrays, in UE space (cm, UE's axes and UVs).</summary>
public sealed class RawSkinnedMesh
{
    public int VertexCount;
    public float[] Positions = [];                  // xyz
    public float[] Normals = [];                    // xyz + the binormal sign
    public float[] Tangents = [];                   // xyz
    public List<float[]> Uvs = new();               // uv per channel
    public byte[]? Colors;                          // rgba
    public int[] Indices = [];
    /// <summary>(surface, first index, triangles).</summary>
    public List<(int Surface, int FirstIndex, int Faces)> Sections = new();
    public List<(short Bone, int Vertex, float Weight)> Weights = new();
    /// <summary>Influences on bones the skeleton doesn't have (left out).</summary>
    public int UnknownBones;

    /// <summary>
    /// This mesh without the faces whose vertices are all in a mask (a remove-mask op: the body part a replacement takes the place
    /// of). The vertices stay, unused.
    /// </summary>
    public RawSkinnedMesh Without(IReadOnlySet<int> mask)
    {
        var kept = new List<int>(Indices.Length);
        for (var t = 0; t + 2 < Indices.Length; t += 3)
            if (!(mask.Contains(Indices[t]) && mask.Contains(Indices[t + 1]) && mask.Contains(Indices[t + 2])))
                kept.AddRange([Indices[t], Indices[t + 1], Indices[t + 2]]);
        var cut = new RawSkinnedMesh
        {
            VertexCount = VertexCount, Positions = Positions, Normals = Normals, Tangents = Tangents,
            Colors = Colors, Indices = kept.ToArray(), UnknownBones = UnknownBones,
        };
        cut.Uvs.AddRange(Uvs);
        cut.Weights.AddRange(Weights);
        cut.Sections.Add((0, 0, cut.Indices.Length / 3));
        return cut;
    }

    /// <summary>
    /// This mesh with another's geometry added (a program merging two constants: the figure body and the stomach panel that closes
    /// it), all in one section.
    /// </summary>
    public RawSkinnedMesh Merged(RawSkinnedMesh other)
    {
        var n = VertexCount;
        var merged = new RawSkinnedMesh
        {
            VertexCount = n + other.VertexCount,
            Positions = [.. Positions, .. other.Positions],
            Normals = [.. Normals, .. other.Normals],
            Tangents = [.. Tangents, .. other.Tangents],
            Indices = [.. Indices, .. other.Indices.Select(i => i + n)],
            UnknownBones = UnknownBones + other.UnknownBones,
        };
        for (var k = 0; k < Math.Min(Uvs.Count, other.Uvs.Count); k++) merged.Uvs.Add([.. Uvs[k], .. other.Uvs[k]]);
        if (Colors is not null)
            merged.Colors = [.. Colors, .. other.Colors ?? Enumerable.Repeat((byte)255, other.VertexCount * 4)];
        merged.Weights.AddRange(Weights);
        merged.Weights.AddRange(other.Weights.Select(w => (w.Bone, w.Vertex + n, w.Weight)));
        merged.Sections.Add((0, 0, merged.Indices.Length / 3));
        return merged;
    }

    /// <summary>
    /// Meshes as one, each part's faces a section of its slot (a car body: a constant per surface). UV channels a part lacks are
    /// zeros; colours a part lacks are white.
    /// </summary>
    public static RawSkinnedMesh Combine(IReadOnlyList<(RawSkinnedMesh Mesh, int Slot)> parts)
    {
        var uvs = parts.Count == 0 ? 0 : parts.Max(x => x.Mesh.Uvs.Count);
        var colors = parts.Any(x => x.Mesh.Colors is not null);
        var combined = new RawSkinnedMesh
        {
            VertexCount = parts.Sum(x => x.Mesh.VertexCount),
            Positions = parts.SelectMany(x => x.Mesh.Positions).ToArray(),
            Normals = parts.SelectMany(x => x.Mesh.Normals).ToArray(),
            Tangents = parts.SelectMany(x => x.Mesh.Tangents).ToArray(),
            Colors = colors ? parts.SelectMany(x => x.Mesh.Colors ?? Enumerable.Repeat((byte)255, x.Mesh.VertexCount * 4)).ToArray() : null,
            UnknownBones = parts.Sum(x => x.Mesh.UnknownBones),
        };
        for (var k = 0; k < uvs; k++)
            combined.Uvs.Add(parts.SelectMany(x => k < x.Mesh.Uvs.Count ? x.Mesh.Uvs[k] : new float[x.Mesh.VertexCount * 2]).ToArray());
        var indices = new List<int>();
        var vertex = 0;
        foreach (var (mesh, slot) in parts)
        {
            var first = indices.Count;
            indices.AddRange(mesh.Indices.Select(i => i + vertex));
            foreach (var (_, start, faces) in mesh.Sections) combined.Sections.Add((slot, first + start, faces));
            combined.Weights.AddRange(mesh.Weights.Select(w => (w.Bone, w.Vertex + vertex, w.Weight)));
            vertex += mesh.VertexCount;
        }
        combined.Indices = indices.ToArray();
        return combined;
    }
}

/// <summary>Google's CityHash32: Mutable's bone ids are CityHash32 of the bone name's UTF-16 bytes.</summary>
public static class CityHash32
{
    const uint C1 = 0xcc9e2d51, C2 = 0x1b873593;

    public static uint BoneId(string name) => Hash(System.Text.Encoding.Unicode.GetBytes(name.ToLowerInvariant()));

    static uint Rot(uint v, int s) => s == 0 ? v : (v >> s) | (v << (32 - s));
    static uint Fetch(byte[] b, int i) => BitConverter.ToUInt32(b, i);
    static uint BSwap(uint v) => System.Buffers.Binary.BinaryPrimitives.ReverseEndianness(v);
    static uint Fmix(uint h) { h ^= h >> 16; h *= 0x85ebca6b; h ^= h >> 13; h *= 0xc2b2ae35; h ^= h >> 16; return h; }
    static uint Mur(uint a, uint h) { a *= C1; a = Rot(a, 17); a *= C2; h ^= a; h = Rot(h, 19); return h * 5 + 0xe6546b64; }

    public static uint Hash(byte[] s)
    {
        var n = s.Length;
        if (n <= 4)
        {
            uint b = 0, c = 9;
            foreach (var x in s) { b = b * C1 + (uint)(sbyte)x; c ^= b; }
            return Fmix(Mur(b, Mur((uint)n, c)));
        }
        if (n <= 12)
        {
            uint a = (uint)n, b = (uint)n * 5, c = 9, d = b;
            a += Fetch(s, 0); b += Fetch(s, n - 4); c += Fetch(s, (n >> 1) & 4);
            return Fmix(Mur(c, Mur(b, Mur(a, d))));
        }
        if (n <= 24)
        {
            uint a = Fetch(s, (n >> 1) - 4), b = Fetch(s, 4), c = Fetch(s, n - 8), d = Fetch(s, n >> 1), e = Fetch(s, 0), f = Fetch(s, n - 4), h = (uint)n;
            return Fmix(Mur(f, Mur(e, Mur(d, Mur(c, Mur(b, Mur(a, h)))))));
        }
        {
            uint h = (uint)n, g = C1 * (uint)n, f = g;
            uint a0 = Rot(Fetch(s, n - 4) * C1, 17) * C2, a1 = Rot(Fetch(s, n - 8) * C1, 17) * C2, a2 = Rot(Fetch(s, n - 16) * C1, 17) * C2;
            uint a3 = Rot(Fetch(s, n - 12) * C1, 17) * C2, a4 = Rot(Fetch(s, n - 20) * C1, 17) * C2;
            h ^= a0; h = Rot(h, 19); h = h * 5 + 0xe6546b64;
            h ^= a2; h = Rot(h, 19); h = h * 5 + 0xe6546b64;
            g ^= a1; g = Rot(g, 19); g = g * 5 + 0xe6546b64;
            g ^= a3; g = Rot(g, 19); g = g * 5 + 0xe6546b64;
            f += a4; f = Rot(f, 19); f = f * 5 + 0xe6546b64;
            var iters = (n - 1) / 20;
            var i = 0;
            do
            {
                uint b0 = Rot(Fetch(s, i) * C1, 17) * C2, b1 = Fetch(s, i + 4), b2 = Rot(Fetch(s, i + 8) * C1, 17) * C2;
                uint b3 = Rot(Fetch(s, i + 12) * C1, 17) * C2, b4 = Fetch(s, i + 16);
                h ^= b0; h = Rot(h, 18); h = h * 5 + 0xe6546b64;
                f += b1; f = Rot(f, 19); f *= C1;
                g += b2; g = Rot(g, 18); g = g * 5 + 0xe6546b64;
                h ^= b3 + b1; h = Rot(h, 19); h = h * 5 + 0xe6546b64;
                g ^= b4; g = BSwap(g) * 5;
                h += b4 * 5; h = BSwap(h);
                f += b0;
                (f, h, g) = (g, f, h);
                i += 20;
            } while (--iters != 0);
            g = Rot(g, 11) * C1; g = Rot(g, 17) * C1;
            f = Rot(f, 11) * C1; f = Rot(f, 17) * C1;
            h = Rot(h + g, 19); h = h * 5 + 0xe6546b64; h = Rot(h, 17) * C1;
            h = Rot(h + f, 19); h = h * 5 + 0xe6546b64; h = Rot(h, 17) * C1;
            return h;
        }
    }
}
