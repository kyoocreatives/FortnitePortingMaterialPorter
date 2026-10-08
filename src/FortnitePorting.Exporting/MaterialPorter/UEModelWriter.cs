#nullable disable
using System.Text;
using global::CUE4Parse.UE4.Assets.Exports.Animation;
using global::CUE4Parse.UE4.Assets.Exports.SkeletalMesh;
using global::CUE4Parse.UE4.Assets.Exports.StaticMesh;
using global::CUE4Parse_Conversion.Dto;
using global::CUE4Parse_Conversion.Options;

namespace FortnitePorting.Exporting.MaterialPorter;

/// <summary>
/// Writes a mesh as a UEFormat model, version 9 - the version the installed
/// io_scene_ueformat (FortnitePorting's importer) reads. CUE4Parse's own
/// writer has moved on to version 10, a different layout that importer
/// rejects; this follows the importer's reader field for field
/// (importer/classes.py, UEModel.from_archive), so armatures come out
/// exactly as an FP import makes them.
///
/// Values are written in UE space (cm, UE's axes): from version 8
/// (PreserveOriginalTransforms) the reader scales and mirrors them itself.
/// Only LOD 0 is written; the importer builds one LOD.
/// </summary>
public static class UEModelWriter
{
    const byte Version = 9;
    /// <summary>Bumped when what this writer puts in a file changes (cached exports are named by it). 2: a
    /// Mutable-built mesh's 8-bit vertex colours read as B G R A (MutableMeshes.ColorIsBgra).</summary>
    public const int Revision = 2;

    sealed class Out
    {
        readonly MemoryStream ms = new();
        public readonly BinaryWriter W;
        public Out() => W = new BinaryWriter(ms, Encoding.UTF8, leaveOpen: true);
        public byte[] Bytes() { W.Flush(); return ms.ToArray(); }

        public void FString(string s)
        {
            var b = Encoding.UTF8.GetBytes(s ?? "");
            W.Write(b.Length);
            W.Write(b);
        }

        /// <summary>A section: name, element count, byte size, then its bytes.</summary>
        public void Section(string name, int count, Action<Out> body)
        {
            var o = new Out();
            body(o);
            var data = o.Bytes();
            FString(name);
            W.Write(count);
            W.Write(data.Length);
            W.Write(data);
        }
    }

    public static void Write(string file, string objectName, USkeletalMesh mesh, IList<string> notes = null)
    {
        using var dto = new SkeletalMeshDto(mesh, EMeshQuality.Highest, ENaniteMeshFormat.NoNanite, false);
        var morphs = Morphs(mesh, notes);
        Save(file, objectName, o =>
        {
            Lods(o, dto.LODs, dto.Materials, (lod, x) =>
            {
                var weights = new List<(short Bone, int Vertex, float Weight)>();
                for (var i = 0; i < lod.Vertices.Length; i++)
                    foreach (var inf in lod.Vertices[i].Influences ?? Array.Empty<MeshBoneInfluenceDto>())
                        if (inf.Weight > 0) weights.Add(((short)inf.Bone, i, inf.Weight));
                x.Section("WEIGHTS", weights.Count, s =>
                {
                    foreach (var (bone, vertex, weight) in weights)
                    {
                        s.W.Write(bone);
                        s.W.Write(vertex);
                        s.W.Write(weight);
                    }
                });
                if (morphs.Count > 0)
                    x.Section("MORPHTARGETS", morphs.Count, s =>
                    {
                        foreach (var (name, deltas) in morphs)
                        {
                            s.FString(name);
                            s.W.Write(deltas.Length);
                            foreach (var d in deltas)
                            {
                                V3(s, d.PositionDelta.X, d.PositionDelta.Y, d.PositionDelta.Z);
                                V3(s, d.TangentZDelta.X, d.TangentZDelta.Y, d.TangentZDelta.Z);
                                s.W.Write((int)d.SourceIdx);
                            }
                        }
                    });
            });
            var skel = mesh.ReferenceSkeleton;
            o.Section("SKELETON", 1, s =>
            {
                s.Section("METADATA", 1, m => m.FString(mesh.Skeleton?.ResolvedObject?.GetPathName() ?? ""));
                s.Section("BONES", skel.FinalRefBoneInfo.Length, b =>
                {
                    for (var i = 0; i < skel.FinalRefBoneInfo.Length; i++)
                    {
                        var info = skel.FinalRefBoneInfo[i];
                        var pose = skel.FinalRefBonePose[i];
                        b.FString(info.Name.Text);
                        b.W.Write(info.ParentIndex);
                        V3(b, pose.Translation.X, pose.Translation.Y, pose.Translation.Z);
                        b.W.Write((float)pose.Rotation.X);
                        b.W.Write((float)pose.Rotation.Y);
                        b.W.Write((float)pose.Rotation.Z);
                        b.W.Write((float)pose.Rotation.W);
                    }
                });
            });
        });
    }

    /// <summary>
    /// A skinned mesh from arrays (a Mutable mesh, which has no USkeletalMesh), on a
    /// skeleton's bones; each section's material slot named (name, material path).
    /// </summary>
    public static void Write(string file, string objectName, RawSkinnedMesh mesh, USkeleton skeleton, IList<(string Name, string Path)> slots)
    {
        Save(file, objectName, o =>
        {
            o.Section("LODS", 1, l =>
            {
                var inner = new Out();
                var n = mesh.VertexCount;
                inner.Section("VERTICES", n, s =>
                {
                    for (var v = 0; v < n; v++) V3(s, mesh.Positions[v * 3], mesh.Positions[v * 3 + 1], mesh.Positions[v * 3 + 2]);
                });
                inner.Section("INDICES", mesh.Indices.Length, s =>
                {
                    foreach (var i in mesh.Indices) s.W.Write(i);
                });
                inner.Section("NORMALS", n, s =>
                {
                    for (var v = 0; v < n; v++)
                    {
                        s.W.Write(mesh.Normals[v * 4 + 3]);
                        V3(s, mesh.Normals[v * 4], mesh.Normals[v * 4 + 1], mesh.Normals[v * 4 + 2]);
                    }
                });
                inner.Section("TANGENTS", n, s =>
                {
                    for (var v = 0; v < n; v++) V3(s, mesh.Tangents[v * 3], mesh.Tangents[v * 3 + 1], mesh.Tangents[v * 3 + 2]);
                });
                if (mesh.Colors is { } colors)
                    inner.Section("VERTEXCOLORS", 1, s =>
                    {
                        s.FString("COL0");
                        s.W.Write(n);
                        s.W.Write(colors, 0, n * 4);
                    });
                inner.Section("TEXCOORDS", mesh.Uvs.Count, s =>
                {
                    foreach (var uv in mesh.Uvs)
                    {
                        s.W.Write(n);
                        for (var v = 0; v < n; v++)
                        {
                            s.W.Write(uv[v * 2]);
                            s.W.Write(uv[v * 2 + 1]);
                        }
                    }
                });
                inner.Section("MATERIALS", mesh.Sections.Count, s =>
                {
                    foreach (var (surface, first, faces) in mesh.Sections)
                    {
                        var (name, path) = surface < slots.Count ? slots[surface] : ("Material_" + surface, "");
                        s.FString(name);
                        s.FString(path);
                        s.W.Write(first);
                        s.W.Write(faces);
                    }
                });
                inner.Section("WEIGHTS", mesh.Weights.Count, s =>
                {
                    foreach (var (bone, vertex, weight) in mesh.Weights)
                    {
                        s.W.Write(bone);
                        s.W.Write(vertex);
                        s.W.Write(weight);
                    }
                });
                var data = inner.Bytes();
                l.FString("LOD0");
                l.W.Write(data.Length);
                l.W.Write(data);
            });
            var skel = skeleton.ReferenceSkeleton;
            o.Section("SKELETON", 1, s =>
            {
                s.Section("METADATA", 1, m => m.FString(skeleton.GetPathName()));
                s.Section("BONES", skel.FinalRefBoneInfo.Length, b =>
                {
                    for (var i = 0; i < skel.FinalRefBoneInfo.Length; i++)
                    {
                        var info = skel.FinalRefBoneInfo[i];
                        var pose = skel.FinalRefBonePose[i];
                        b.FString(info.Name.Text);
                        b.W.Write(info.ParentIndex);
                        V3(b, pose.Translation.X, pose.Translation.Y, pose.Translation.Z);
                        b.W.Write((float)pose.Rotation.X);
                        b.W.Write((float)pose.Rotation.Y);
                        b.W.Write((float)pose.Rotation.Z);
                        b.W.Write((float)pose.Rotation.W);
                    }
                });
            });
        });
    }

    public static void Write(string file, string objectName, UStaticMesh mesh)
    {
        using var dto = new StaticMeshDto(mesh, EMeshQuality.Highest, ENaniteMeshFormat.NoNanite, null);
        Save(file, objectName, o => Lods<MeshVertex>(o, dto.LODs, dto.Materials, null));
    }

    /// <summary>
    /// A landscape's mesh (LandscapeMeshDto): its vertices scaled by the proxy's
    /// scale, its weight layers renamed (asset name -> layer name).
    /// </summary>
    public static void Write(string file, string objectName, StaticMeshDto dto, System.Numerics.Vector3 scale, Func<string, string> colorName)
    {
        Save(file, objectName, o => Lods<MeshVertex>(o, dto.LODs, dto.Materials, null, scale, colorName));
    }

    static void Save(string file, string objectName, Action<Out> model)
    {
        var body = new Out();
        model(body);
        var head = new Out();
        head.W.Write(Encoding.ASCII.GetBytes("UEFORMAT"));
        head.FString("UEMODEL");
        head.W.Write(Version);
        head.FString(objectName);
        head.W.Write(false);            // not compressed
        using var f = File.Create(file);
        f.Write(head.Bytes());
        f.Write(body.Bytes());
    }

    static void V3(Out o, double x, double y, double z)
    {
        o.W.Write((float)x);
        o.W.Write((float)y);
        o.W.Write((float)z);
    }

    /// <summary>The LODS section with LOD 0: geometry, colours, UVs, material sections, then extras.</summary>
    static void Lods<TVertex>(Out o, IList<MeshLodDto<TVertex>> lods, MeshMaterialDto[] materials, Action<MeshLodDto<TVertex>, Out> extra,
                              System.Numerics.Vector3? scale = null, Func<string, string> colorName = null)
        where TVertex : struct, IMeshVertex
    {
        var k = scale ?? System.Numerics.Vector3.One;
        var lod = lods.FirstOrDefault() ?? throw new InvalidDataException("mesh has no LOD");
        o.Section("LODS", 1, l =>
        {
            var inner = new Out();
            var n = lod.Vertices.Length;
            inner.Section("VERTICES", n, s =>
            {
                foreach (var v in lod.Vertices) V3(s, v.Position.X * k.X, v.Position.Y * k.Y, v.Position.Z * k.Z);
            });
            inner.Section("INDICES", lod.Indices.Length, s =>
            {
                foreach (var i in lod.Indices) s.W.Write(i);
            });
            inner.Section("NORMALS", n, s =>
            {
                // W (the binormal sign) first, then XYZ
                foreach (var v in lod.Vertices)
                {
                    s.W.Write((float)v.Normal.W);
                    V3(s, v.Normal.X, v.Normal.Y, v.Normal.Z);
                }
            });
            inner.Section("TANGENTS", n, s =>
            {
                foreach (var v in lod.Vertices) V3(s, v.Tangent.X, v.Tangent.Y, v.Tangent.Z);
            });
            var colors = lod.VertexColors ?? Array.Empty<MeshVertexColorDto>();
            if (colors.Length > 0)
                inner.Section("VERTEXCOLORS", colors.Length, s =>
                {
                    foreach (var c in colors)
                    {
                        s.FString(colorName?.Invoke(c.Name) ?? c.Name);
                        s.W.Write(c.Colors.Length);
                        foreach (var col in c.Colors)
                        {
                            s.W.Write(col.R);
                            s.W.Write(col.G);
                            s.W.Write(col.B);
                            s.W.Write(col.A);
                        }
                    }
                });
            var extras = lod.ExtraUvs ?? Array.Empty<global::CUE4Parse.UE4.Objects.Meshes.FMeshUVFloat[]>();
            inner.Section("TEXCOORDS", 1 + extras.Length, s =>
            {
                s.W.Write(n);
                foreach (var v in lod.Vertices)
                {
                    s.W.Write(v.Uv.U);
                    s.W.Write(v.Uv.V);
                }
                foreach (var channel in extras)
                {
                    s.W.Write(channel.Length);
                    foreach (var uv in channel)
                    {
                        s.W.Write(uv.U);
                        s.W.Write(uv.V);
                    }
                }
            });
            var sections = lod.Sections.Where(x => x.IsValid).ToArray();
            inner.Section("MATERIALS", sections.Length, s =>
            {
                foreach (var sec in sections)
                {
                    var m = sec.MaterialIndex >= 0 && sec.MaterialIndex < materials.Length ? materials[sec.MaterialIndex].Material : null;
                    var name = m?.ResolvedObject?.Name.Text ?? (sec.MaterialIndex < materials.Length ? materials[sec.MaterialIndex].SlotName : null)
                               ?? "Material_" + sec.MaterialIndex;
                    s.FString(name);
                    s.FString(m?.ResolvedObject?.GetPathName() ?? "");
                    s.W.Write(sec.FirstIndex);
                    s.W.Write(sec.NumFaces);
                }
            });
            extra?.Invoke(lod, inner);
            var data = inner.Bytes();
            l.FString("LOD0");
            l.W.Write(data.Length);
            l.W.Write(data);
        });
    }

    /// <summary>LOD 0 of each morph target the mesh has uncompressed.</summary>
    static List<(string Name, FMorphTargetDelta[] Deltas)> Morphs(USkeletalMesh mesh, IList<string> notes)
    {
        var list = new List<(string, FMorphTargetDelta[])>();
        var skipped = 0;
        foreach (var idx in mesh.MorphTargets ?? Array.Empty<global::CUE4Parse.UE4.Objects.UObject.FPackageIndex>())
        {
            try
            {
                var morph = idx.Load<UMorphTarget>();
                var deltas = morph?.MorphLODModels?.FirstOrDefault()?.Vertices;
                if (deltas is { Length: > 0 }) list.Add((morph.Name, deltas));
                else skipped++;
            }
            catch { skipped++; }
        }
        if (skipped > 0) notes?.Add($"{skipped} morph targets without uncompressed data were left out");
        return list;
    }
}
