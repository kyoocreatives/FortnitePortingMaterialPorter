using System.IO;
using CUE4Parse.FileProvider.Objects;
using CUE4Parse.UE4.Assets.Objects;
using CUE4Parse.UE4.Versions;

namespace FortnitePorting.Exporting.MaterialPorter;

/// <summary>A UEFN project's file, read sharing write and delete: UEFN may be saving it, and its save mustn't fail
/// because the app has it open.</summary>
public sealed class ProjectGameFile(DirectoryInfo baseDir, FileInfo info, string mountPoint, VersionContainer versions)
    : OsGameFile(baseDir, info, mountPoint, versions)
{
    public override byte[] Read(FByteBulkDataHeader? header = null)
    {
        if (header == null) return ReadAll(ActualFile.FullName);
        using var stream = Open(ActualFile.FullName);
        stream.Seek(header.Value.OffsetInFile, SeekOrigin.Begin);
        var buffer = new byte[header.Value.SizeOnDisk];
        stream.ReadExactly(buffer);
        return buffer;
    }

    public static byte[] ReadAll(string path)
    {
        using var stream = Open(path);
        var buffer = new byte[stream.Length];
        stream.ReadExactly(buffer);
        return buffer;
    }

    static FileStream Open(string path) => new(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
}
