#nullable disable
using global::CUE4Parse.UE4.Assets.Exports.Material;
using global::CUE4Parse.UE4.Objects.UObject;
using Newtonsoft.Json.Linq;

namespace FortnitePorting.MaterialPorter;

/// <summary>A texture the compiled shader binds: a parameter's (Param) or a hard-coded one (Param null).</summary>
public sealed class ShaderTexture
{
    public string Param { get; set; }
    public string Path { get; set; }
    /// <summary>"2D", "Cube", "Array", "Volume" or "Virtual".</summary>
    public string Kind { get; set; }
}

/// <summary>
/// What a cooked material's compiled shader uses, read from its inline shader map
/// (FMaterialShaderMap: the uniform expression set the compiler left once the
/// graph's dead branches were gone). An island's master has no editor graph, but
/// its shader map says which textures the pixel shader really samples, which
/// scalar and vector parameters it reads, and (when decodable) the preshaders
/// that combine them.
/// </summary>
public sealed class ShaderHints
{
    public List<ShaderTexture> Textures { get; set; } = new();
    public List<string> UsedScalars { get; set; } = new();
    public List<string> UsedVectors { get; set; } = new();
    /// <summary>How many preshaders (the compiled value expressions) the uniform set has.</summary>
    public int Preshaders { get; set; }
    /// <summary>Each preshader's parameters and final value under the instance chain's values (empty when they can't be decoded).</summary>
    public List<ShaderPreshader> PreshaderValues { get; set; } = new();
    /// <summary>Why something is missing (an undecodable preshader opcode...).</summary>
    public string Note { get; set; }
    /// <summary>Raw structures for exploration (env MATERIAL_PORTER_SHADER_DEBUG=1).</summary>
    public JObject Debug { get; set; }

    /// <summary>The material's compiled programs and numeric parameters, to evaluate for each instance chain's values (not serialized).</summary>
    [Newtonsoft.Json.JsonIgnore] public List<PreshaderProgram> Programs { get; set; } = new();
    [Newtonsoft.Json.JsonIgnore] public List<NumericParam> Numeric { get; set; } = new();
}

public static class ShaderMapHints
{
    public static bool DebugDump => Environment.GetEnvironmentVariable("MATERIAL_PORTER_SHADER_DEBUG") == "1";

    static readonly System.Collections.Concurrent.ConcurrentDictionary<string, ShaderHints> Cache = new();

    /// <summary>
    /// The hints of a cooked master's inline shader map; null when it carries no readable one.
    /// Never throws. Remembered per master.
    /// </summary>
    /// <param name="master">The master's object path ("/Game/X/M.M"), a package the provider can save.</param>
    /// <param name="m">The master, loaded the usual way (no shader maps asked of the provider).</param>
    /// <param name="imported">Its package's imported textures, in import order.</param>
    public static ShaderHints From(global::CUE4Parse.FileProvider.IFileProvider provider, string master, UMaterial m, IReadOnlyList<string> imported)
    {
        if (Cache.TryGetValue(master, out var known) && !DebugDump) return known;
        ShaderHints hints = null;
        try { hints = Read(provider, master, m, imported ?? Array.Empty<string>()); }
        catch (Exception e) { Timing.Log("shader map of " + master + ": " + e.GetType().Name + " " + e.Message, new System.Diagnostics.Stopwatch()); }
        Cache[master] = hints;
        return hints;
    }

    /// <summary>
    /// The material's shader maps, read from its package's bytes. CUE4Parse reads them with the
    /// material (ReadShaderMaps) but expects a FMaterialShaderMapId Fortnite's cooked maps no longer
    /// carry, so the read goes in below it: FShaderMapBase.Deserialize straight after the two flags.
    /// The maps sit after the name table that starts with the type layout names, which the package's
    /// own bytes give away.
    /// </summary>
    static List<FMaterialShaderMap> ReadMaps(global::CUE4Parse.FileProvider.IFileProvider provider, string master, JObject debug)
    {
        var maps = new List<FMaterialShaderMap>();
        var path = master.LastIndexOf('.') > master.LastIndexOf('/') ? master[..master.LastIndexOf('.')] : master;
        if (!provider.TrySavePackage(path, out var files)) return maps;
        // a package is one file (zen) or a header and its exports (legacy): the shader maps are in the exports
        var data = files.OrderBy(f => f.Key.EndsWith(".uasset", StringComparison.OrdinalIgnoreCase) ? 0 : 1).SelectMany(f => f.Value).ToArray();
        var marker = System.Text.Encoding.ASCII.GetBytes("FMaterialShaderMapContent\0");
        var at = data.AsSpan().IndexOf(marker);
        if (at < 12) return maps;
        int lenPos = at - 4, countPos = lenPos - 4, numPos = countPos - 4;
        var num = BitConverter.ToInt32(data, numPos);
        if (BitConverter.ToInt32(data, lenPos) != marker.Length || num < 1 || num > 16) return maps;
        debug?.Add("shader_map_offset", countPos);
        var ar = new global::CUE4Parse.UE4.Readers.FByteArchive("shader maps", data, provider.Versions) { Position = countPos };
        var proxy = new NumberedProxyReader(ar);
        for (var i = 0; i < num; i++)
        {
            try
            {
                if (!proxy.ReadBoolean() || !proxy.ReadBoolean()) continue;      // bCooked, bValid
                var map = new FMaterialShaderMap();
                ((FShaderMapBase)map).Deserialize(proxy);                         // not FMaterialShaderMap's own: that reads a ShaderMapId first
                maps.Add(map);
            }
            catch (Exception e)
            {
                debug?.Add("shader_map_error_" + i, e.GetType().Name + ": " + e.Message);
                break;
            }
        }
        return maps;
    }

    static ShaderHints Read(global::CUE4Parse.FileProvider.IFileProvider provider, string master, UMaterial m, IReadOnlyList<string> imported)
    {
        var debug = DebugDump ? new JObject() : null;
        var maps = ReadMaps(provider, master, debug);
        FUniformExpressionSet set = null;
        var resources = new JArray();
        foreach (var map in maps)
        {
            var ues = (map.Content as FMaterialShaderMapContent)?.MaterialCompilationOutput?.UniformExpressionSet;
            resources.Add(new JObject { ["platform"] = map.ShaderPlatform.ToString(), ["uniform_set"] = ues != null });
            set ??= ues;
        }
        if (debug != null) debug["resources"] = resources;
        if (set == null) return DebugDump ? new ShaderHints { Note = "no readable shader map", Debug = debug } : null;

        var hints = new ShaderHints();

        // the texture list TextureIndex points into: the material's cached referenced textures,
        // which a cooked UE 5 master keeps in ReferencedDefaultTextures (its own order)
        var defaults = new List<string>();
        if (m.TryGetValue(out FPackageIndex[] def, "ReferencedDefaultTextures"))
            foreach (var d in def) defaults.Add(PathOf(d));
        var cue = m.ReferencedTextures.Select(t => t.GetPathName()).ToList();
        var maxIndex = set.UniformTextureParameters.Where(a => a != null).SelectMany(a => a).Select(p => p.TextureIndex).DefaultIfEmpty(-1).Max();
        var lists = new (string name, IReadOnlyList<string> items)[] { ("defaults", defaults), ("referenced", cue), ("imports", imported) };
        var chosen = lists.FirstOrDefault(l => l.items.Count > maxIndex);
        if (debug != null)
        {
            debug["max_texture_index"] = maxIndex;
            debug["index_lists"] = new JObject(lists.Select(l => new JProperty(l.name, new JArray(l.items))));
            debug["index_list_used"] = chosen.name;
        }

        for (var type = 0; type < set.UniformTextureParameters.Length; type++)
        {
            foreach (var p in set.UniformTextureParameters[type] ?? Array.Empty<FMaterialTextureParameterInfo>())
            {
                var name = NameOf(p);
                var path = chosen.items != null && p.TextureIndex >= 0 && p.TextureIndex < chosen.items.Count ? chosen.items[p.TextureIndex] : null;
                hints.Textures.Add(new ShaderTexture { Param = name, Path = path, Kind = KindOf(type, set.UniformTextureParameters.Length) });
            }
        }
        if (debug != null)
            debug["textures"] = new JArray(set.UniformTextureParameters.SelectMany((a, type) => (a ?? Array.Empty<FMaterialTextureParameterInfo>())
                .Select(p => new JObject { ["type"] = type, ["param"] = NameOf(p), ["index"] = p.TextureIndex, ["sampler"] = p.SamplerSource.ToString() })));

        // UE 5: one numeric parameter list (scalar, vector...); UE 4: a list each
        foreach (var p in set.UniformNumericParameters ?? Array.Empty<FMaterialNumericParameterInfo>())
        {
            var name = p.ParameterInfo?.Name.Text;
            if (string.IsNullOrEmpty(name)) continue;
            if (p.ParameterType == EMaterialParameterType.Scalar) hints.UsedScalars.Add(name);
            else if (p.ParameterType is EMaterialParameterType.Vector or EMaterialParameterType.DoubleVector) hints.UsedVectors.Add(name);
        }
        foreach (var p in set.UniformScalarParameters ?? Array.Empty<FMaterialScalarParameterInfo>())
            if (NameOf(p) is { Length: > 0 } n) hints.UsedScalars.Add(n);
        foreach (var p in set.UniformVectorParameters ?? Array.Empty<FMaterialVectorParameterInfo>())
            if (NameOf(p) is { Length: > 0 } n) hints.UsedVectors.Add(n);
        hints.UsedScalars = hints.UsedScalars.Distinct().ToList();
        // SelectionColor is the editor's highlight, compiled into every material
        hints.UsedVectors = hints.UsedVectors.Distinct().Where(n => n != "SelectionColor").ToList();
        hints.Preshaders = (set.UniformPreshaders?.Length ?? 0) + (set.UniformVectorPreshaders?.Length ?? 0) + (set.UniformScalarPreshaders?.Length ?? 0);
        LoadPrograms(set, hints);

        if (debug != null)
        {
            DumpPreshaders(set, debug);
            hints.Debug = debug;
        }
        return hints;
    }

    static void DumpPreshaders(FUniformExpressionSet set, JObject debug)
    {
        var data = set.UniformPreshaderData?.Data ?? Array.Empty<byte>();
        debug["preshader_data"] = new JObject
        {
            ["length"] = data.Length, ["hex"] = Convert.ToHexString(data),
            ["names"] = new JArray((set.UniformPreshaderData?.Names ?? Array.Empty<global::CUE4Parse.UE4.Objects.UObject.FName>()).Select(n => n.Text)),
            ["buffer_size"] = set.UniformPreshaderBufferSize,
            ["preshader2"] = set.UniformPreshaderData?.bPreshader2,
            ["struct_types"] = set.UniformPreshaderData?.StructTypes?.Length,
        };
        debug["preshader_headers"] = new JArray((set.UniformPreshaders ?? Array.Empty<FMaterialUniformPreshaderHeader>()).Select(h =>
        {
            var o = new JObject { ["kind"] = h.GetType().Name, ["offset"] = h.OpcodeOffset, ["size"] = h.OpcodeSize };
            if (h is FMaterialUniformPreshaderHeader_5_8 h8) { o["type"] = h8.Type.ToString(); o["buffer_offset"] = h8.BufferOffset; }
            if (h is FMaterialUniformPreshaderHeader_5_1 h1) { o["field_index"] = h1.FieldIndex; o["num_fields"] = h1.NumFields; }
            if (h.OpcodeOffset + h.OpcodeSize <= data.Length) o["ops"] = Convert.ToHexString(data, (int)h.OpcodeOffset, (int)h.OpcodeSize);
            return o;
        }));
        debug["numeric_parameters"] = new JArray((set.UniformNumericParameters ?? Array.Empty<FMaterialNumericParameterInfo>()).Select(p => new JObject
        {
            ["name"] = p.ParameterInfo?.Name.Text, ["type"] = p.ParameterType.ToString(), ["default_offset"] = p.DefaultValueOffset, ["value"] = ValueOf(p.Value) is { } dv ? new JArray(dv) : null,
        }));
        debug["default_values_hex"] = Convert.ToHexString(set.DefaultValues ?? Array.Empty<byte>());
        debug["fields"] = new JArray((set.UniformPreshaderFields ?? Array.Empty<FMaterialUniformPreshaderField>()).Select(f => new JObject
        {
            ["buffer_offset"] = f.BufferOffset, ["component"] = f.ComponentIndex, ["type"] = f.Type.ToString(),
        }));
    }

    // ------------------------------------------------------------ preshaders
    // UE's preshader bytecode (Shader/Preshader.h), as this Fortnite build writes it: an opcode byte, then
    //   03 u16        Parameter: the numeric parameter list's index (its value, a vector as 4 floats)
    //   02 t floats   Constant: t = Float1..Float4, then that many floats
    //   25 n r g b a  ComponentSwizzle: n components, each indexing the value (FF unused)
    //   26            AppendVector: a, b -> (a, b)
    //   04 / 05 / 06  Add, Sub, Mul on the top two values (a scalar broadcasts over a vector)
    //   17 / 1A / 1B  Rcp, Saturate, Abs (inferred, not read from a table: 17 is 1 / (DirtGradientMax - DirtGradientMin) and
    //                 1 / GradientFalloff in the Cristaline masters, 1A and 1B follow it in UE's order: Rcp, Length, Normalize,
    //                 Saturate, Abs)
    // Every program of the 161 masters of the Cristaline island parses to the byte with these. Any other opcode
    // leaves its program with the parameters it reads but no value.
    static void LoadPrograms(FUniformExpressionSet set, ShaderHints hints)
    {
        foreach (var p in set.UniformNumericParameters ?? Array.Empty<FMaterialNumericParameterInfo>())
        {
            var vector = p.ParameterType is EMaterialParameterType.Vector or EMaterialParameterType.DoubleVector;
            hints.Numeric.Add(new NumericParam { Name = p.ParameterInfo?.Name.Text, Vector = vector, Default = ValueOf(p.Value) });
        }
        var data = set.UniformPreshaderData?.Data;
        if (data == null) return;
        foreach (var h in set.UniformPreshaders ?? Array.Empty<FMaterialUniformPreshaderHeader>())
        {
            if ((long)h.OpcodeOffset + h.OpcodeSize > data.Length) continue;
            hints.Programs.Add(new PreshaderProgram
            {
                Type = (h as FMaterialUniformPreshaderHeader_5_8)?.Type.ToString(),
                Ops = data.AsSpan((int)h.OpcodeOffset, (int)h.OpcodeSize).ToArray(),
            });
        }
    }

    static double[] ValueOf(object v) => v switch
    {
        float f => new double[] { f },
        global::CUE4Parse.UE4.Objects.Core.Math.FLinearColor c => new double[] { c.R, c.G, c.B, c.A },
        global::CUE4Parse.UE4.Objects.Core.Math.FVector4 q => new double[] { q.X, q.Y, q.Z, q.W },
        bool b => new double[] { b ? 1 : 0 },
        _ => null,
    };

    /// <summary>
    /// The hints for one instance chain: each folded preshader (one that does arithmetic on parameters, a
    /// bare parameter being just the chain's own value) with the parameters it reads and its value under
    /// the chain's scalars and vectors (the master's defaults where the chain sets none).
    /// </summary>
    public static ShaderHints For(ShaderHints h, IReadOnlyDictionary<string, double> scalars, IReadOnlyDictionary<string, double[]> vectors)
    {
        if (h == null) return null;
        var copy = new ShaderHints
        {
            Textures = h.Textures, UsedScalars = h.UsedScalars, UsedVectors = h.UsedVectors, Preshaders = h.Preshaders,
            Note = h.Note, Debug = h.Debug, Programs = h.Programs, Numeric = h.Numeric,
        };
        var undecoded = new SortedSet<string>();
        foreach (var program in h.Programs)
        {
            var r = Run(program, h.Numeric, scalars, vectors, undecoded);
            if (r != null) copy.PreshaderValues.Add(r);
        }
        if (undecoded.Count > 0)
            copy.Note = (copy.Note == null ? "" : copy.Note + "; ") + "preshader opcodes not decoded: " + string.Join(",", undecoded);
        return copy;
    }

    static ShaderPreshader Run(PreshaderProgram program, List<NumericParam> numeric, IReadOnlyDictionary<string, double> scalars,
                               IReadOnlyDictionary<string, double[]> vectors, SortedSet<string> undecoded)
    {
        var ops = program.Ops;
        var stack = new Stack<double[]>();
        var names = new List<string>();
        bool ok = true, arithmetic = false, product = true;
        for (var i = 0; i < ops.Length;)
        {
            var op = ops[i++];
            try
            {
                switch (op)
                {
                    case 3:   // Parameter
                    {
                        var index = ops[i] | ops[i + 1] << 8;
                        i += 2;
                        var param = numeric[index];
                        names.Add(param.Name);
                        double[] value = null;
                        if (param.Vector && vectors != null && vectors.TryGetValue(param.Name, out var vec)) value = vec;
                        else if (!param.Vector && scalars != null && scalars.TryGetValue(param.Name, out var sc)) value = new[] { sc };
                        stack.Push(value ?? param.Default ?? (param.Vector ? new double[4] : new double[1]));
                        break;
                    }
                    case 2:   // Constant
                    {
                        var n = ops[i++];
                        if (n < 1 || n > 4) throw new FormatException("constant type " + n);
                        var c = new double[n];
                        for (var k = 0; k < n; k++, i += 4) c[k] = Math.Round(BitConverter.ToSingle(ops, i), 6);
                        stack.Push(c);
                        break;
                    }
                    case 0x25:   // ComponentSwizzle
                    {
                        var n = ops[i];
                        var v = stack.Pop();
                        var r = new double[n];
                        for (var k = 0; k < n; k++) r[k] = v[ops[i + 1 + k]];
                        i += 5;
                        stack.Push(r);
                        break;
                    }
                    case 0x26:   // AppendVector
                    {
                        var b = stack.Pop();
                        stack.Push(stack.Pop().Concat(b).ToArray());
                        break;
                    }
                    case 0x17 or 0x1A or 0x1B:   // Rcp, Saturate, Abs
                    {
                        var v = stack.Pop();
                        var r = new double[v.Length];
                        for (var k = 0; k < v.Length; k++)
                        {
                            if (op == 0x17 && Math.Abs(v[k]) < 1e-12) throw new DivideByZeroException();
                            r[k] = Math.Round(op == 0x17 ? 1 / v[k] : op == 0x1A ? Math.Clamp(v[k], 0, 1) : Math.Abs(v[k]), 6);
                        }
                        arithmetic = true;
                        product = false;
                        stack.Push(r);
                        break;
                    }
                    case 4 or 5 or 6:
                    {
                        var b = stack.Pop();
                        var a = stack.Pop();
                        if (a.Length != b.Length && a.Length != 1 && b.Length != 1) throw new FormatException("operand sizes");
                        var n = Math.Max(a.Length, b.Length);
                        var r = new double[n];
                        for (var k = 0; k < n; k++)
                        {
                            double x = a[a.Length == 1 ? 0 : k], y = b[b.Length == 1 ? 0 : k];
                            r[k] = Math.Round(op == 4 ? x + y : op == 5 ? x - y : x * y, 6);
                        }
                        arithmetic = true;
                        product &= op == 6;
                        stack.Push(r);
                        break;
                    }
                    default:
                        undecoded.Add(op.ToString("X2"));
                        ok = false;
                        arithmetic = true;
                        product = false;
                        break;
                }
            }
            catch (Exception) { ok = false; break; }
        }
        // a bare parameter is the chain's own value; nothing to say about it
        var distinct = names.Distinct().Where(n => n != "SelectionColor").ToList();
        if (distinct.Count == 0 || !arithmetic && distinct.Count < 2) return null;
        double[] result = ok && stack.Count == 1 ? stack.Peek() : null;
        var want = program.Type is { Length: 6 } t && t.StartsWith("Float") ? t[5] - '0' : 0;
        if (result != null && want > 0 && result.Length != want) result = null;
        return new ShaderPreshader { Params = distinct, Value = result, Product = ok && product && arithmetic, Type = program.Type };
    }

    static string PathOf(FPackageIndex i)
    {
        try { return i?.ResolvedObject?.GetPathName(); }
        catch { return null; }
    }

    static string NameOf(FMaterialBaseParameterInfo p)
    {
        var n = p.ParameterInfo?.Name.Text ?? p.ParameterName;
        return string.IsNullOrEmpty(n) || n == "None" ? null : n;
    }

    /// <summary>UniformTextureParameters' slot -> the kind of texture (UE 5: 2D, cube, 2D array, cube array, volume, virtual, sparse volume; UE 4 has no cube array).</summary>
    static string KindOf(int type, int count)
    {
        var kinds = count <= 5 ? new[] { "2D", "Cube", "Array", "Volume", "Virtual" }
                               : new[] { "2D", "Cube", "Array", "Array", "Volume", "Virtual", "Volume" };
        return type < kinds.Length ? kinds[type] : "2D";
    }
}

/// <summary>
/// The shader maps' reader with names' numbers kept: a frozen name is written to the map's name table as
/// an index and a number, and CUE4Parse reads it as its plain text, so a material's parameters "Param",
/// "Param_1" and "Param_2" (what the editor makes of three nodes called Param) all read "Param".
/// </summary>
sealed class NumberedProxyReader : FMaterialResourceProxyReader
{
    public NumberedProxyReader(global::CUE4Parse.UE4.Readers.FArchive inner) : base(inner) { }

    public override string ReadFString() => ReadFName().Text;
}

/// <summary>One compiled value expression: the parameters it reads and what it comes to.</summary>
public sealed class ShaderPreshader
{
    public List<string> Params { get; set; } = new();
    /// <summary>Its value under the instance chain's parameters; null when an opcode in it isn't decoded.</summary>
    public double[] Value { get; set; }
    /// <summary>Only multiplications of parameters and constants: a tint or a brightness folded.</summary>
    public bool Product { get; set; }
    public string Type { get; set; }
}

/// <summary>A compiled value expression's opcodes (see ShaderMapHints.LoadPrograms).</summary>
public sealed class PreshaderProgram
{
    public string Type { get; set; }
    public byte[] Ops { get; set; }
}

/// <summary>A scalar or vector parameter the shader reads, with the master's default.</summary>
public sealed class NumericParam
{
    public string Name { get; set; }
    public bool Vector { get; set; }
    public double[] Default { get; set; }
}
