using System;
using System.Linq;
using System.Reflection;
using System.Runtime.CompilerServices;
using CUE4Parse.UE4.Assets;
using CUE4Parse.UE4.Assets.Exports.Texture;
using CUE4Parse.UE4.Assets.Objects;
using CUE4Parse.UE4.Assets.Readers;
using CUE4Parse.UE4.Objects.UObject;
using Serilog;

namespace FortnitePorting.Exporting.MaterialPorter;

/// <summary>Texture2D as CUE4Parse reads it, plus: an uncooked one (a UEFN project's, which has no platform data) gets its
/// editor source pixels as its mips, decoded when first read, so every texture path (exports, materials, landscapes) reads
/// it like a cooked one.</summary>
public class EditorTexture2D : UTexture2D
{
    [ModuleInitializer]
    internal static void Register() => ObjectTypeRegistry.RegisterClass("Texture2D", typeof(EditorTexture2D));

    static readonly PropertyInfo PlatformDataProperty = typeof(UTexture).GetProperty(nameof(PlatformData))!;

    public override void Deserialize(FAssetArchive Ar, long validPos)
    {
        base.Deserialize(Ar, validPos);
        if (PlatformData.Mips.Length > 0 || Ar.Owner?.Summary.PackageFlags.HasFlag(EPackageFlags.PKG_FilterEditorOnly) != false) return;
        try
        {
            AttachSource();
        }
        catch (Exception e)
        {
            Log.Warning("[Material Porter] {Texture}: its source couldn't be read ({Message})", GetPathName(), e.Message);
        }
    }

    void AttachSource()
    {
        if (TextureSources.SourceOf(this) is not { Slices: 1 } source) return;
        var format = source.Format switch
        {
            "TSF_BGRA8" or "TSF_BGRE8" => EPixelFormat.PF_B8G8R8A8,
            "TSF_G8" => EPixelFormat.PF_G8,
            "TSF_G16" => EPixelFormat.PF_G16,
            "TSF_R16F" => EPixelFormat.PF_R16F,
            "TSF_RGBA16F" => EPixelFormat.PF_FloatRGBA,
            "TSF_RGBA32F" => EPixelFormat.PF_A32B32G32R32F,
            "TSF_R32F" => EPixelFormat.PF_R32_FLOAT,
            _ => EPixelFormat.PF_Unknown,
        };
        if (format == EPixelFormat.PF_Unknown) return;
        // the source decoded once, into its mips (not kept beside them)
        var split = new Lazy<byte[]?[]>(() =>
        {
            var all = TextureSources.Pixels(this, source);
            if (all == null || source.Mips == 1) return [all];
            var parts = new byte[]?[source.Mips];
            long at = 0;
            for (var m = 0; m < source.Mips; m++)
            {
                var size = (int) source.MipBytes(m);
                parts[m] = all.AsSpan((int) at, size).ToArray();
                at += size;
            }
            return parts;
        });
        var mips = new FTexture2DMipMap[source.Mips];
        for (var m = 0; m < source.Mips; m++)
        {
            var mip = m;
            var size = (int) source.MipBytes(m);
            var data = new Lazy<byte[]?>(() => split.Value.ElementAtOrDefault(mip));
            mips[m] = new FTexture2DMipMap(new SourceBulkData(data) { Header = new FByteBulkDataHeader(0, size, (uint) size, 0, default) },
                Math.Max(1, source.SizeX >> m), Math.Max(1, source.SizeY >> m), 1);
        }
        Format = format;
        PlatformDataProperty.SetValue(this, PlatformDataOf(source.SizeX, source.SizeY, format, mips));
    }

    // FTexturePlatformData keeps its sizes and format read-only (it's only ever read from a cooked package)
    static FTexturePlatformData PlatformDataOf(int sizeX, int sizeY, EPixelFormat format, FTexture2DMipMap[] mips)
    {
        var data = (FTexturePlatformData) RuntimeHelpers.GetUninitializedObject(typeof(FTexturePlatformData));
        void Set(string field, object? value) => typeof(FTexturePlatformData).GetField(field)!.SetValue(data, value);
        Set(nameof(FTexturePlatformData.SizeX), sizeX);
        Set(nameof(FTexturePlatformData.SizeY), sizeY);
        Set(nameof(FTexturePlatformData.PackedData), 1u);
        Set(nameof(FTexturePlatformData.PixelFormat), format.ToString());
        Set(nameof(FTexturePlatformData.FirstMipToSerialize), 0);
        data.Mips = mips;
        return data;
    }

    sealed class SourceBulkData(Lazy<byte[]?> data) : TBulkData<byte>(data);
}
