using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using CUE4Parse.FileProvider.Objects;
using CUE4Parse.UE4.Assets.Exports.Material;
using CUE4Parse.UE4.Assets.Exports.SkeletalMesh;
using CUE4Parse.UE4.Assets.Exports.StaticMesh;
using CUE4Parse.UE4.Objects.Core.Math;
using FortnitePorting.Exporting.MaterialPorter;
using FortnitePorting.Exporting.Models;

namespace FortnitePorting.Exporting.Context;

public partial class ExportContext
{
    // A UEFN project's own static mesh: written once from its mesh description (again when its file is newer).
    partial void UefnStaticMesh<T>(UStaticMesh mesh, ref T? export) where T : ExportMesh, new()
    {
        if (mesh.RenderData is { LODs.Length: > 0 }) return;
        var path = mesh.GetPathName();
        if (!ProjectPaths.IsProject(path)) return;
        try
        {
            export = ProjectStaticMesh<T>(mesh, path);
        }
        catch (Exception e)
        {
            Failures.Note("mesh exports", path, e);
        }
    }

    T? ProjectStaticMesh<T>(UStaticMesh mesh, string path) where T : ExportMesh, new()
    {
        var slots = MeshDescriptions.Slots(mesh);
        var file = BuildExportPath(path, "uemodel");
        var source = mesh.Owner?.Provider?.Files.TryGetValue(mesh.Owner.Name + ".uasset", out var gameFile) == true && gameFile is OsGameFile os
            ? os.ActualFile.FullName : null;
        if (!File.Exists(file) || source != null && File.GetLastWriteTimeUtc(source) > File.GetLastWriteTimeUtc(file))
        {
            if (MeshDescriptions.Read(mesh) is not { } raw) return null;
            Directory.CreateDirectory(Path.GetDirectoryName(file)!);
            UEModelWriter.Write(file, mesh.Name, raw, null, slots);
        }
        return WithSlots<T>(mesh.Name, path, slots);
    }

    // A UEFN project's own skeletal mesh: written once from its mesh description, on the description's own bones.
    partial void UefnSkeletalMesh<T>(USkeletalMesh mesh, ref T? export) where T : ExportMesh, new()
    {
        var path = mesh.GetPathName();
        if (!ProjectPaths.IsProject(path)) return;
        try
        {
            export = ProjectSkeletalMesh<T>(mesh, path);
        }
        catch (Exception e)
        {
            Failures.Note("mesh exports", path, e);
        }
    }

    T? ProjectSkeletalMesh<T>(USkeletalMesh mesh, string path) where T : ExportMesh, new()
    {
        var slots = MeshDescriptions.Slots(mesh);
        var file = BuildExportPath(path, "uemodel");
        var source = mesh.Owner?.Provider?.Files.TryGetValue(mesh.Owner.Name + ".uasset", out var gameFile) == true && gameFile is OsGameFile os
            ? os.ActualFile.FullName : null;
        if (!File.Exists(file) || source != null && File.GetLastWriteTimeUtc(source) > File.GetLastWriteTimeUtc(file))
        {
            if (MeshDescriptions.Read(mesh) is not { } read) return null;
            var bones = read.Bones.Select(b => (b.Name, b.Parent, new FVector(b.Translation.X, b.Translation.Y, b.Translation.Z),
                new FQuat(b.Rotation.X, b.Rotation.Y, b.Rotation.Z, b.Rotation.W))).ToList();
            Directory.CreateDirectory(Path.GetDirectoryName(file)!);
            UEModelWriter.Write(file, mesh.Name, read.Mesh, mesh.Skeleton?.ResolvedObject?.GetPathName(), bones, slots);
        }
        return WithSlots<T>(mesh.Name, path, slots);
    }

    T WithSlots<T>(string name, string path, IList<(string Name, string Path)> slots) where T : ExportMesh, new()
    {
        var result = new T { Name = name, Path = path, NumLods = 1 };
        for (var slot = 0; slot < slots.Count; slot++)
            if (slots[slot].Path.Length > 0 && LoadMaterialPorterObject(slots[slot].Path) is UMaterialInterface mi && Material(mi, slot) is { } material)
                result.Materials.Add(material with { Slot = slot });
        return result;
    }

    // A UEFN project's own mesh (no render data until its mesh description is read): an empty where it's placed.
    partial void MissingMesh(MapMesh m, ref ExportMesh? empty)
    {
        if (!ProjectPaths.IsProject(m.Mesh)) return;
        var name = m.Mesh[(m.Mesh.LastIndexOf('/') + 1)..];
        var export = new MaterialPorterMesh { Name = name.Contains('.') ? name[..name.IndexOf('.')] : name, IsEmpty = true, MPActor = m.Actor, MPHidden = m.Hidden ? true : null, MPLayer = m.Layer };
        SetMaterialPorterTransform(export, m.World);
        empty = export;
    }
}
