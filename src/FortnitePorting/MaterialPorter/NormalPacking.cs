using System;
using CUE4Parse.UE4.Assets.Exports.Texture;
using CUE4Parse_Conversion.Textures;

namespace FortnitePorting.MaterialPorter;

/// <summary>
/// Normal maps for Blender with X in alpha and Y in green. Blender keeps DXT5 compressed on the GPU but decodes BC5 to
/// RGBA (4x the memory), so a BC5 map becomes DXT5: its X block is DXT5's alpha block as is (both are BC4 blocks), and
/// Y goes into the colour block's green (red holds a block's mean X, for code reading rg).
/// </summary>
public static class NormalPacking
{
    /// <summary>A BC5 mip as DXT5 (same size: 16 bytes per 4x4 block).</summary>
    public static byte[] Bc5ToDxt5(byte[] bc5)
    {
        var dxt5 = new byte[bc5.Length];
        Span<byte> x = stackalloc byte[16], y = stackalloc byte[16];
        for (var i = 0; i + 16 <= bc5.Length; i += 16)
        {
            Buffer.BlockCopy(bc5, i, dxt5, i, 8);
            DecodeBc4(bc5.AsSpan(i, 8), x);
            DecodeBc4(bc5.AsSpan(i + 8, 8), y);
            EncodeBc1(x, y, dxt5.AsSpan(i + 8, 8));
        }
        return dxt5;
    }

    /// <summary>A decoded normal map (RGBA8 or BGRA8) with its red copied into alpha.</summary>
    public static void RedToAlpha(CTexture ct)
    {
        var red = ct.PixelFormat switch { EPixelFormat.PF_R8G8B8A8 => 0, EPixelFormat.PF_B8G8R8A8 => 2, _ => -1 };
        if (red < 0 || ct.Data == null) return;
        for (var i = 0; i + 3 < ct.Data.Length; i += 4)
            ct.Data[i + 3] = ct.Data[i + red];
    }

    // BC4: two 8-bit endpoints, then 16 three-bit indices (texel 0 lowest)
    static void DecodeBc4(ReadOnlySpan<byte> block, Span<byte> out16)
    {
        int e0 = block[0], e1 = block[1];
        Span<int> pal = stackalloc int[8];
        pal[0] = e0;
        pal[1] = e1;
        if (e0 > e1)
            for (var k = 1; k < 7; k++) pal[k + 1] = ((7 - k) * e0 + k * e1) / 7;
        else
        {
            for (var k = 1; k < 5; k++) pal[k + 1] = ((5 - k) * e0 + k * e1) / 5;
            pal[6] = 0;
            pal[7] = 255;
        }
        ulong bits = 0;
        for (var k = 0; k < 6; k++) bits |= (ulong)block[2 + k] << (8 * k);
        for (var t = 0; t < 16; t++) out16[t] = (byte)pal[(int)((bits >> (3 * t)) & 7)];
    }

    // BC1 four-colour block of (x, y, 0) fitted to y alone (x is exact in alpha; red keeps the block's mean x):
    // endpoints from y's range, refined by least squares on the chosen weights, the best of the tries kept
    static void EncodeBc1(ReadOnlySpan<byte> x, ReadOnlySpan<byte> y, Span<byte> dst)
    {
        double mx = 0, lo = 255, hi = 0;
        for (var t = 0; t < 16; t++) { mx += x[t]; lo = Math.Min(lo, y[t]); hi = Math.Max(hi, y[t]); }
        mx /= 16;
        Span<int> idx = stackalloc int[16], best = stackalloc int[16];
        var bestError = long.MaxValue;
        ushort bestC0 = 0, bestC1 = 0;
        for (var pass = 0; pass < 4; pass++)
        {
            ushort c0 = Pack(mx, hi), c1 = Pack(mx, lo);
            if (c0 < c1) (c0, c1) = (c1, c0);
            var error = Fit(y, c0, c1, idx);
            if (error < bestError)
            {
                bestError = error; bestC0 = c0; bestC1 = c1;
                idx.CopyTo(best);
            }
            if (c0 == c1 || error == 0) break;
            // least squares: y ~ a * (1 - w) + b * w over the chosen weights w in {0, 1/3, 2/3, 1}
            double s00 = 0, s01 = 0, s11 = 0, r0 = 0, r1 = 0;
            for (var t = 0; t < 16; t++)
            {
                var w = idx[t] switch { 0 => 0.0, 1 => 1.0, 2 => 1.0 / 3, _ => 2.0 / 3 };
                double u = 1 - w;
                s00 += u * u; s01 += u * w; s11 += w * w; r0 += u * y[t]; r1 += w * y[t];
            }
            var det = s00 * s11 - s01 * s01;
            if (Math.Abs(det) < 1e-9) break;
            hi = Math.Clamp((r0 * s11 - r1 * s01) / det, 0, 255);
            lo = Math.Clamp((r1 * s00 - r0 * s01) / det, 0, 255);
            if (hi < lo) (hi, lo) = (lo, hi);
        }
        uint indices = 0;
        if (bestC0 != bestC1)
            for (var t = 0; t < 16; t++) indices |= (uint)best[t] << (2 * t);
        dst[0] = (byte)bestC0; dst[1] = (byte)(bestC0 >> 8);
        dst[2] = (byte)bestC1; dst[3] = (byte)(bestC1 >> 8);
        for (var k = 0; k < 4; k++) dst[4 + k] = (byte)(indices >> (8 * k));
    }

    // each texel's nearest of the block's four greens (index 0: c0, 1: c1, 2: 2/3 c0 + 1/3 c1, 3: 1/3 c0 + 2/3 c1);
    // the squared error
    static long Fit(ReadOnlySpan<byte> y, ushort c0, ushort c1, Span<int> idx)
    {
        int g0 = Unpack(c0).Item2, g1 = Unpack(c1).Item2;
        Span<int> pal = stackalloc int[] { g0, g1, (2 * g0 + g1) / 3, (g0 + 2 * g1) / 3 };
        long error = 0;
        for (var t = 0; t < 16; t++)
        {
            int k = 0, e = int.MaxValue;
            for (var j = 0; j < (c0 == c1 ? 1 : 4); j++)
            {
                var d = (y[t] - pal[j]) * (y[t] - pal[j]);
                if (d < e) { e = d; k = j; }
            }
            idx[t] = k;
            error += e;
        }
        return error;
    }

    static ushort Pack(double r, double g)
    {
        var r5 = (int)Math.Round(Math.Clamp(r, 0, 255) * 31 / 255);
        var g6 = (int)Math.Round(Math.Clamp(g, 0, 255) * 63 / 255);
        return (ushort)((r5 << 11) | (g6 << 5));
    }

    static (int, int) Unpack(ushort c)
    {
        int r5 = c >> 11, g6 = (c >> 5) & 63;
        return ((r5 << 3) | (r5 >> 2), (g6 << 2) | (g6 >> 4));
    }
}
