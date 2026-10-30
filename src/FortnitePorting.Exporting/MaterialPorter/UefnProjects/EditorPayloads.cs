using System;
using System.Buffers.Binary;
using System.Collections.Generic;
using System.IO;
using CUE4Parse.Compression;
using CUE4Parse.FileProvider.Objects;
using CUE4Parse.UE4.Assets;

namespace FortnitePorting.Exporting.MaterialPorter;

/// <summary>An uncooked package's editor payloads (texture sources, mesh descriptions): kept in the package trailer
/// (FPackageTrailer) as compressed buffers (FCompressedBuffer). Formats from the engine's PackageTrailer.cpp and
/// CompressedBuffer.cpp.</summary>
public static class EditorPayloads
{
    /// <summary>A local payload: its id (the IoHash an FEditorBulkData names it by), where it starts and its raw size.</summary>
    public sealed record Payload(byte[] Id, long At, long Raw);

    /// <summary>The package's file bytes, when it's read from disk.</summary>
    public static byte[]? FileOf(IPackage? package)
    {
        if (package?.Provider is not { } provider) return null;
        if (!provider.Files.TryGetValue(package.Name + ".uasset", out var file) && !provider.Files.TryGetValue(package.Name + ".umap", out file)) return null;
        return file is OsGameFile os ? ProjectGameFile.ReadAll(os.ActualFile.FullName) : null;
    }

    /// <summary>The trailer's local payloads (none without a trailer): its footer (tag, length, package tag) points back to its header.</summary>
    public static List<Payload> Of(byte[] file)
    {
        var found = new List<Payload>();
        if (file.Length < 48) return found;
        var footer = file.Length - 20;
        if (BitConverter.ToUInt64(file, footer) != 0x29BFCA045138DE76UL) return found;
        var start = file.Length - (long) BitConverter.ToUInt64(file, footer + 8);
        if (start < 0 || BitConverter.ToUInt64(file, (int) start) != 0xD1C43B2E80A5F697UL) return found;
        var version = BitConverter.ToUInt32(file, (int) start + 8);
        var headerLength = BitConverter.ToUInt32(file, (int) start + 12);
        var p = (int) start + 24 + (version < 1 ? 1 : 0);
        var count = BitConverter.ToInt32(file, p);
        p += 4;
        for (var i = 0; i < count; i++)
        {
            var id = file[p..(p + 20)];
            var offset = BitConverter.ToInt64(file, p + 20);
            var raw = (long) BitConverter.ToUInt64(file, p + 36);
            p += 44 + (version >= 2 ? 4 : 0);                   // id, offset, compressed and raw sizes; flags, filter flags
            var access = version >= 1 ? file[p++] : (byte) 0;
            if (access == 0) found.Add(new Payload(id, start + headerLength + offset, raw));     // local (not virtualized)
        }
        return found;
    }

    /// <summary>A compressed buffer's bytes: a 64-byte big-endian header; method 0 one raw block, 3 (Oodle) / 4 (LZ4) the
    /// blocks' sizes then the blocks, a block kept raw when compressing didn't make it smaller.</summary>
    public static byte[] Decompress(byte[] file, long at)
    {
        var h = (int) at;
        if (BinaryPrimitives.ReadUInt32BigEndian(file.AsSpan(h)) != 0xb7756362) throw new InvalidDataException("not a compressed buffer");
        var method = file[h + 8];
        var blockSize = 1L << file[h + 11];
        var blocks = (int) BinaryPrimitives.ReadUInt32BigEndian(file.AsSpan(h + 12));
        var rawSize = (long) BinaryPrimitives.ReadUInt64BigEndian(file.AsSpan(h + 16));
        var raw = new byte[rawSize];
        if (method == 0)
        {
            Array.Copy(file, h + 64, raw, 0, rawSize);
            return raw;
        }
        var sizes = h + 64;
        var data = sizes + 4 * blocks;
        long rawAt = 0;
        for (var b = 0; b < blocks; b++)
        {
            var compressed = (int) BinaryPrimitives.ReadUInt32BigEndian(file.AsSpan(sizes + 4 * b));
            var size = (int) Math.Min(blockSize, rawSize - rawAt);
            if (compressed < size)
                Array.Copy(Compression.Decompress(file, data, compressed, size, method == 3 ? CompressionMethod.Oodle : CompressionMethod.LZ4), 0, raw, rawAt, size);
            else
                Array.Copy(file, data, raw, rawAt, size);
            data += compressed;
            rawAt += size;
        }
        return raw;
    }
}
