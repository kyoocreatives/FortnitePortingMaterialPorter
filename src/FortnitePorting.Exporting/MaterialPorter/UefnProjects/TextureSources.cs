using System;
using System.Buffers.Binary;
using System.Collections.Generic;
using System.Linq;
using CUE4Parse.UE4.Assets.Exports.Texture;
using CUE4Parse.UE4.Assets.Objects;
using CUE4Parse.UE4.Objects.Core.Misc;

namespace FortnitePorting.Exporting.MaterialPorter;

/// <summary>An uncooked texture's source pixels (what UEFN keeps in the editor): its package's editor payload
/// (EditorPayloads), undone from TSCF_UEDELTA when so stored (the engine's ImageCoreDelta.cpp).</summary>
public static class TextureSources
{
    /// <summary>A texture source's layout: mip 0's size, its mips and slices, its format and how it's stored.</summary>
    public sealed record Source(int SizeX, int SizeY, int Mips, int Slices, string Format, string Compression, FGuid Id, bool IdIsHash)
    {
        public int BytesPerPixel => Format switch
        {
            "TSF_G8" => 1, "TSF_G16" or "TSF_R16F" => 2, "TSF_RGBA16" or "TSF_RGBA16F" => 8, "TSF_RGBA32F" => 16, "TSF_R32F" => 4, _ => 4,
        };

        // the delta's sample size: 8-bit channels, 16-bit (bias 0x8080) or 32-bit (bias 0x80808080)
        public int SampleBytes => Format switch
        {
            "TSF_G16" or "TSF_R16F" or "TSF_RGBA16" or "TSF_RGBA16F" => 2, "TSF_RGBA32F" or "TSF_R32F" => 4, _ => 1,
        };

        public long MipBytes(int mip) => (long) Math.Max(1, SizeX >> mip) * Math.Max(1, SizeY >> mip) * BytesPerPixel * Slices;
        public long TotalBytes => Enumerable.Range(0, Mips).Sum(MipBytes);
    }

    public static Source? SourceOf(UTexture texture)
    {
        if (!texture.TryGetValue(out FStructFallback source, "Source")) return null;
        string Text(string name) => source.Properties.FirstOrDefault(p => p.Name.Text == name)?.Tag?.GenericValue?.ToString() ?? "";
        var sizeX = source.GetOrDefault<int>("SizeX");
        var sizeY = source.GetOrDefault<int>("SizeY");
        if (sizeX <= 0 || sizeY <= 0) return null;
        return new Source(sizeX, sizeY, Math.Max(1, source.GetOrDefault<int>("NumMips")), Math.Max(1, source.GetOrDefault<int>("NumSlices")),
            Text("Format").SubstringAfter("::"), Text("CompressionFormat").SubstringAfter("::"),
            source.GetOrDefault<FGuid>("Id"), source.GetOrDefault<bool>("bGuidIsHash"));
    }

    static string SubstringAfter(this string s, string what) => s.Contains(what) ? s[(s.IndexOf(what, StringComparison.Ordinal) + what.Length)..] : s;

    /// <summary>The texture's source pixels, every mip one after the other (null when it has none it can read): the
    /// trailer payload of the source's size, the one whose pixels hash to the source's Id when several have it.</summary>
    public static byte[]? Pixels(UTexture texture, Source source)
    {
        if (source.Compression is "TSCF_PNG" or "TSCF_JPEG") return null;      // older sources: an image file (step 2 fallback)
        if (EditorPayloads.FileOf(texture.Owner) is not { } file) return null;
        var candidates = EditorPayloads.Of(file).Where(p => p.Raw == source.TotalBytes).ToList();
        foreach (var candidate in candidates)
        {
            var pixels = EditorPayloads.Decompress(file, candidate.At);
            if (source.Compression == "TSCF_UEDELTA") UnDelta(pixels, source);
            if (candidates.Count == 1 || !source.IdIsHash || HashGuid(pixels) == source.Id) return pixels;
        }
        return null;
    }

    /// <summary>The source Id UE gives hashed pixels (IoHashToGuid of their BLAKE3 hash).</summary>
    public static FGuid HashGuid(byte[] pixels)
    {
        var hash = Blake3.Hasher.Hash(pixels).AsSpan();
        return new FGuid(BitConverter.ToUInt32(hash), BitConverter.ToUInt32(hash[4..]), BitConverter.ToUInt32(hash[8..]), BitConverter.ToUInt32(hash[12..]));
    }

    // ImageCoreDelta.cpp: each mip (and slice) is cut into tiles whose first row is kept and every other row is the
    // difference from the row above. The cutting is part of the format.
    sealed record Tile(long Offset, long Stride, long X, long Width, long Y, long Height);

    static long Cuts(long items)
    {
        if (items <= 32768) return 1;
        var n = items / 32768;
        while (n > 512) n >>= 1;
        return n;
    }

    static long CutsForRows(out long rowsPerCut, long sizeX, long sizeY)
    {
        var n = Cuts(sizeX * sizeY);
        rowsPerCut = (sizeY + n - 1) / n;
        return (sizeY + rowsPerCut - 1) / rowsPerCut;
    }

    static IEnumerable<Tile> Tiles(long offset, long sizeX, long sizeY, long bpp)
    {
        var stride = sizeX * bpp;
        if (sizeX * sizeY <= 136 * 136)
        {
            yield return new Tile(offset, stride, 0, sizeX, 0, sizeY);
            yield break;
        }
        if (stride <= 4096)
        {
            var cuts = CutsForRows(out var rows, sizeX, sizeY);
            for (var c = 0; c < cuts; c++) yield return new Tile(offset, stride, 0, sizeX, c * rows, Math.Min(rows, sizeY - c * rows));
            yield break;
        }
        var parts = (stride + 4095) / 4096;
        var partBytes = ((stride + parts / 2) / parts + 63) & ~63L;
        var partPixels = partBytes / bpp;
        parts = (sizeX + partPixels - 1) / partPixels;
        for (var part = 0; part < parts; part++)
        {
            var startX = part * partPixels;
            var width = Math.Min(partPixels, sizeX - startX);
            var cuts = CutsForRows(out var rows, width, sizeY);
            for (var c = 0; c < cuts; c++) yield return new Tile(offset, stride, startX, width, c * rows, Math.Min(rows, sizeY - c * rows));
        }
    }

    static void UnDelta(byte[] d, Source source)
    {
        long offset = 0;
        for (var mip = 0; mip < source.Mips; mip++)
        {
            long x = Math.Max(1, source.SizeX >> mip), y = Math.Max(1, source.SizeY >> mip);
            for (var slice = 0; slice < source.Slices; slice++)
            {
                foreach (var tile in Tiles(offset, x, y, source.BytesPerPixel)) Undo(d, tile, source.BytesPerPixel, source.SampleBytes);
                offset += x * y * source.BytesPerPixel;
            }
        }
    }

    static void Undo(byte[] d, Tile t, int bpp, int sample)
    {
        var rowBytes = t.Width * bpp;
        for (var y = t.Y + 1; y < t.Y + t.Height; y++)
        {
            var row = (int) (t.Offset + y * t.Stride + t.X * bpp);
            var up = row - (int) t.Stride;
            if (sample == 1)
                for (var i = 0; i < rowBytes; i++) d[row + i] = (byte) (d[row + i] + d[up + i]);
            else if (sample == 2)
                for (var i = 0; i < rowBytes; i += 2)
                    BinaryPrimitives.WriteUInt16LittleEndian(d.AsSpan(row + i),
                        (ushort) (BinaryPrimitives.ReadUInt16LittleEndian(d.AsSpan(row + i)) + BinaryPrimitives.ReadUInt16LittleEndian(d.AsSpan(up + i)) - 0x8080));
            else
                for (var i = 0; i < rowBytes; i += 4)
                    BinaryPrimitives.WriteUInt32LittleEndian(d.AsSpan(row + i),
                        BinaryPrimitives.ReadUInt32LittleEndian(d.AsSpan(row + i)) + BinaryPrimitives.ReadUInt32LittleEndian(d.AsSpan(up + i)) - 0x80808080U);
        }
    }
}
