#nullable disable
using System.Reflection;
using global::CUE4Parse.FileProvider;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace FortnitePorting.Exporting.MaterialPorter;

/// <summary>
/// A cooked Mutable program (a CustomizableObject's Model.Program), run far enough to say what each surface's material gets: the
/// instance tree walked under a set of parameter values, each surface's material op resolved, its colour and scalar parameters
/// evaluated; and which constant meshes a component is built of (MeshSurfaces; MutableMeshes decodes them). Images aren't built
/// (textures stay the materials' own).
///
/// The format, as read off Rocket Racing's CO_VehicleCosmeticsRoot (CUE4Parse keeps the bytecode but not the op table): a reference
/// is (data type &lt;&lt; 24) | byte offset; an op is its type byte, then its arguments. A switch is (variable, default, count,
/// (value, ref)*); a conditional (bool, yes, no).
///   bool   3 int == const   4 and   5 or   6 not
///   int    8 parameter
///   scalar 11 constant   12 parameter   13 conditional   14 switch
///   colour 19 constant (RGBA; NaN = leave the material's own)   20 parameter   21 conditional   22 switch   26 from scalars (r, g, b, a)
///   mesh   70 constant (constant, skeleton, physics)   73 conditional   74 switch   94 (_, mesh)   95 (mesh, surface id)
///          102 a LOD's surface meshes (list)   103 conditional   104 switch   107 merge   115 (mesh)
///          105 surfaces (lists: materials, name ids, surface ids, a LOD op per LOD)
///   instance 116 conditional   117 switch   118 add (base, child, id)   119 component mesh (_, mesh)
///            121 surface (previous, material, name id)   122 surface (previous, material, string index)
///   material 130 constant (_, passthrough id)   131 parameter   132 conditional   133 switch
///            134/135 a mesh's own slot (name id, mesh)
///            137 parameters (base, count, (name id, flag, ref [+4 for images])*)
/// </summary>
public sealed class MutableProgram
{
    public sealed class Param
    {
        public string Name { get; init; }
        public string Type { get; init; }
        public JToken Default { get; init; }
        public Dictionary<string, int> Values { get; } = new(StringComparer.OrdinalIgnoreCase);
    }

    /// <summary>What a surface's material came to.</summary>
    public sealed class Surface
    {
        public string Name { get; init; }
        /// <summary>The material: a mesh's own slot (by name), a material parameter, or an asset.</summary>
        public string Slot { get; set; }
        public string MaterialParam { get; set; }
        public string Asset { get; set; }
        public Dictionary<string, double[]> Vectors { get; } = new(StringComparer.OrdinalIgnoreCase);
        public Dictionary<string, double> Scalars { get; } = new(StringComparer.OrdinalIgnoreCase);
    }

    byte[] code;
    uint root;
    readonly List<Param> parameters = new();
    readonly Dictionary<string, int> index = new(StringComparer.OrdinalIgnoreCase);
    readonly Dictionary<uint, string> names = new();
    string[] strings = Array.Empty<string>();
    uint[][] lists = Array.Empty<uint[]>();
    string[] componentNames = Array.Empty<string>();
    readonly Dictionary<uint, string> passthrough = new();

    public IReadOnlyList<Param> Parameters => parameters;
    public bool Has(string name) => index.ContainsKey(name);
    public Param Get(string name) => index.TryGetValue(name, out var i) ? parameters[i] : null;

    static readonly JsonSerializerSettings Ser = new() { ReferenceLoopHandling = ReferenceLoopHandling.Ignore };
    static JToken Json(object o) => JToken.Parse(JsonConvert.SerializeObject(o, Ser));

    static object Member(object o, string name)
    {
        const BindingFlags F = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance;
        var t = o.GetType();
        return t.GetField(name, F)?.GetValue(o) ?? t.GetProperty(name, F)?.GetValue(o);
    }

    public static async Task<MutableProgram> LoadAsync(IFileProvider provider, string coPackage)
    {
        var pkg = await provider.LoadPackageAsync(coPackage);
        var exports = pkg.GetExports().ToList();
        var co = exports.First(e => e.ExportType == "CustomizableObject");
        var program = Member(Member(co, "Model"), "Program");
        var p = new MutableProgram
        {
            code = (byte[])Member(program, "ByteCode"),
            strings = (string[])Member(program, "ConstantStrings") ?? Array.Empty<string>(),
            lists = (uint[][])Member(program, "ConstantUInt32Lists") ?? Array.Empty<uint[]>(),
        };
        var states = Json(Member(program, "States"));
        p.root = (uint)states[0]!["Root"]!;
        foreach (var d in Json(Member(program, "Parameters")))
        {
            var par = new Param { Name = (string)d["Name"], Type = (string)d["Type"], Default = d["DefaultValue"] };
            foreach (var v in d["PossibleValues"] ?? new JArray())
                par.Values[(string)v["Name"] ?? ""] = (int)v["Value"];
            p.index[par.Name] = p.parameters.Count;
            p.parameters.Add(par);
        }
        foreach (var kv in (JObject)Json(Member(program, "ConstantNames")))
            p.names[uint.Parse(kv.Key)] = (string)kv.Value;
        var res = exports.FirstOrDefault(e => e.ExportType == "ModelResources" && e.Name.EndsWith("WindowsClient"))
                  ?? exports.FirstOrDefault(e => e.ExportType == "ModelResources");
        if (res != null)
        {
            var props = Json(res)["Properties"];
            foreach (var x in props?["PassthroughObjects"] ?? new JArray())
                if (uint.TryParse((string)x["Key"], out var k)) p.passthrough[k] = (string)x["Value"]?["AssetPathName"];
            p.componentNames = (props?["ComponentNamesPerObjectComponent"] ?? new JArray()).Select(x => (string)x).ToArray();
        }
        return p;
    }

    public int? EnumValue(string param, string name) =>
        Get(param) is { } par && name != null && par.Values.TryGetValue(name, out var v) ? v : null;

    // ------------------------------------------------------------ evaluation
    uint U(int o) => BitConverter.ToUInt32(code, o);
    int I(int o) => BitConverter.ToInt32(code, o);
    float F(int o) => BitConverter.ToSingle(code, o);
    static int Off(uint r) => (int)(r & 0xFFFFFF);

    /// <summary>
    /// Every surface the instance gets under these values: int (enum index), double[4] (colour), double (scalar) or string
    /// (material path), by name; unset parameters take their defaults.
    /// </summary>
    public List<Surface> Evaluate(IReadOnlyDictionary<string, object> values)
    {
        var run = new Run(this, values);
        var list = new List<Surface>();
        run.Instance(root, list, 0);
        return list;
    }

    /// <summary>
    /// A surface of a component's mesh: the constant mesh it's built from, its name (the slot it fills) and its material (null if the
    /// program's op isn't one this reads).
    /// </summary>
    public sealed record MeshSurface(string Component, int Constant, string Name, Surface Material);

    /// <summary>
    /// The surfaces each component the instance gets under these values is built of, at a LOD (0 the most detailed). A component's
    /// mesh (119) leads, through conditionals (103), switches (104), merges (107) and 115, to a surfaces op (105): the ids of four
    /// constant lists (surfaces' materials, names and ids) and a ref per LOD to an op (102) naming the list of that LOD's surface
    /// meshes. A surface mesh is a constant mesh (70: constant, skeleton, physics) under conditionals (73), switches (74), 94 (_, mesh)
    /// and 95 (mesh, surface id); the id picks its material and name. Components are named as the model's resources have them
    /// (ComponentNamesPerObjectComponent, by the add op's id). What this can't follow goes to `problems`.
    /// </summary>
    public List<MeshSurface> MeshSurfaces(IReadOnlyDictionary<string, object> values, int lod = 0, List<string> problems = null)
    {
        var run = new Run(this, values);
        run.Instance(root, new List<Surface>(), 0);
        var result = new List<MeshSurface>();
        foreach (var (mesh, id) in run.Components)
        {
            var component = id < componentNames.Length ? componentNames[id] : id.ToString();
            var ops = new List<int>();
            run.SurfaceOps(U(mesh + 5), ops, 0);
            foreach (var o in ops)
            {
                var materials = List(U(o + 1));
                var nameIds = List(U(o + 5));
                var ids = List(U(o + 9));
                var lods = List(U(o + 13));
                if (lods.Length == 0) continue;
                var at = Off(lods[Math.Min(lod, lods.Length - 1)]);
                if (code[at] != 102) continue;
                foreach (var r in List(U(at + 1)))
                {
                    if (run.SurfaceMesh(r, 0, 0) is not { } s) continue;
                    var k = Array.IndexOf(ids, s.Id);
                    var name = k >= 0 && k < nameIds.Length ? names.GetValueOrDefault(nameIds[k]) : null;
                    Surface material = null;
                    try { if (k >= 0 && k < materials.Length) material = run.Material(materials[k], name); }
                    catch (NotSupportedException e) { problems?.Add($"{component} {name}: {e.Message}"); }
                    if (material == null) problems?.Add($"{component} {name ?? "surface " + s.Id}: no material" + (k < 0 ? " (its id isn't the surfaces op's)" : ""));
                    result.Add(new MeshSurface(component, s.Constant, name ?? "Surface_" + result.Count, material));
                }
            }
        }
        return result;
    }

    uint[] List(uint index) => index < lists.Length ? lists[index] : Array.Empty<uint>();

    sealed class Run
    {
        readonly MutableProgram p;
        readonly IReadOnlyDictionary<string, object> values;
        public Run(MutableProgram p, IReadOnlyDictionary<string, object> values) { this.p = p; this.values = values; }

        /// <summary>The component meshes (119) the instance walk reached: offset and component id.</summary>
        public readonly List<(int Mesh, uint Id)> Components = new();
        uint component = uint.MaxValue;

        object Param(uint idx)
        {
            var par = p.parameters[(int)idx];
            if (values.TryGetValue(par.Name, out var v)) return v;
            var d = par.Default;
            if (d is JObject o) return new[] { (double?)o["X"] ?? 0, (double?)o["Y"] ?? 0, (double?)o["Z"] ?? 0, (double?)o["W"] ?? 1 };
            if (d == null || d.Type == JTokenType.Null) return null;
            return d.Type is JTokenType.Integer ? (object)(int)d : d.Type is JTokenType.Float ? (double)d : d.Type is JTokenType.Boolean ? ((bool)d ? 1 : 0) : (string)d;
        }

        T Switch<T>(int o, Func<uint, T> ev)
        {
            var x = Num(p.U(o + 1));
            var n = p.U(o + 9);
            for (var k = 0; k < n; k++)
                if (p.I(o + 13 + 8 * k) == x) return ev(p.U(o + 17 + 8 * k));
            var dflt = p.U(o + 5);
            return dflt != 0 ? ev(dflt) : default;
        }

        /// <summary>The surfaces ops (105) a component's mesh leads to; ops this doesn't know lead nowhere.</summary>
        public void SurfaceOps(uint r, List<int> ops, int depth)
        {
            if (r == 0 || depth > 64) return;
            var o = Off(r);
            switch (p.code[o])
            {
                case 103: SurfaceOps(Num(p.U(o + 1)) != 0 ? p.U(o + 5) : p.U(o + 9), ops, depth + 1); return;
                case 104: Switch<object>(o, x => { SurfaceOps(x, ops, depth + 1); return null; }); return;
                case 105: ops.Add(o); return;
                case 107: SurfaceOps(p.U(o + 1), ops, depth + 1); SurfaceOps(p.U(o + 5), ops, depth + 1); return;
                case 115: SurfaceOps(p.U(o + 1), ops, depth + 1); return;
            }
        }

        /// <summary>A surface's constant mesh and surface id, or null (a conditional's other branch, an op this doesn't know).</summary>
        public (int Constant, uint Id)? SurfaceMesh(uint r, uint id, int depth)
        {
            if (r == 0 || depth > 64) return null;
            var o = Off(r);
            return p.code[o] switch
            {
                70 => ((int)p.U(o + 1), id),
                73 => SurfaceMesh(Num(p.U(o + 1)) != 0 ? p.U(o + 5) : p.U(o + 9), id, depth + 1),
                74 => Switch(o, x => SurfaceMesh(x, id, depth + 1)),
                94 => SurfaceMesh(p.U(o + 5), id, depth + 1),
                95 => SurfaceMesh(p.U(o + 1), p.U(o + 5), depth + 1),
                _ => null,
            };
        }

        public int Num(uint r)
        {
            var o = Off(r);
            switch (p.code[o])
            {
                case 8: return Param(p.U(o + 1)) switch { int i => i, double d => (int)d, _ => 0 };
                case 3: return Num(p.U(o + 1)) == p.I(o + 5) ? 1 : 0;
                case 4: return Num(p.U(o + 1)) != 0 && Num(p.U(o + 5)) != 0 ? 1 : 0;
                case 5: return Num(p.U(o + 1)) != 0 || Num(p.U(o + 5)) != 0 ? 1 : 0;
                case 6: return Num(p.U(o + 1)) != 0 ? 0 : 1;
                default: throw new NotSupportedException($"Mutable int op {p.code[o]} at {o}");
            }
        }

        public double? Scalar(uint r)
        {
            if (r == 0) return null;
            var o = Off(r);
            switch (p.code[o])
            {
                case 11: return p.F(o + 1);
                case 12: return Param(p.U(o + 1)) switch { double d => d, int i => i, _ => null };
                case 13: return Scalar(Num(p.U(o + 1)) != 0 ? p.U(o + 5) : p.U(o + 9));
                case 14: return Switch(o, Scalar);
                default: throw new NotSupportedException($"Mutable scalar op {p.code[o]} at {o}");
            }
        }

        public double[] Colour(uint r)
        {
            if (r == 0) return null;
            var o = Off(r);
            switch (p.code[o])
            {
                case 19: return new double[] { p.F(o + 1), p.F(o + 5), p.F(o + 9), p.F(o + 13) };
                case 20: return Param(p.U(o + 1)) as double[];
                case 21: return Colour(Num(p.U(o + 1)) != 0 ? p.U(o + 5) : p.U(o + 9));
                case 22: return Switch(o, Colour);
                case 26: return new[] { Scalar(p.U(o + 1)) ?? 0, Scalar(p.U(o + 5)) ?? 0, Scalar(p.U(o + 9)) ?? 0, Scalar(p.U(o + 13)) ?? 1 };
                default: throw new NotSupportedException($"Mutable colour op {p.code[o]} at {o}");
            }
        }

        public Surface Material(uint r, string surface)
        {
            if (r == 0) return null;
            var o = Off(r);
            switch (p.code[o])
            {
                case 130:
                    return new Surface { Name = surface, Asset = p.passthrough.GetValueOrDefault(p.U(o + 5)) };
                case 131:
                {
                    var par = p.parameters[(int)p.U(o + 1)].Name;
                    return new Surface { Name = surface, MaterialParam = par, Asset = values.GetValueOrDefault(par) as string };
                }
                case 132: return Material(Num(p.U(o + 1)) != 0 ? p.U(o + 5) : p.U(o + 9), surface);
                case 133: return Switch(o, x => Material(x, surface));
                case 134:
                case 135:
                    return new Surface { Name = surface, Slot = p.names.GetValueOrDefault(p.U(o + 1)) };
                case 137:
                {
                    var s = Material(p.U(o + 1), surface);
                    if (s == null) return null;
                    var n = p.U(o + 5);
                    var q = o + 9;
                    for (var k = 0; k < n; k++)
                    {
                        var name = p.names.GetValueOrDefault(p.U(q));
                        var vr = p.U(q + 5);
                        q += 9;
                        var dt = vr >> 24;
                        if (dt == 0x14) { q += 4; continue; } // an image (its layout index follows): the material's own
                        if (name == null) continue;
                        try
                        {
                            if (dt == 0x10 && Colour(vr) is { } c && !c.Any(double.IsNaN)) s.Vectors[name] = c;
                            else if (dt == 0x0c && Scalar(vr) is { } x && !double.IsNaN(x)) s.Scalars[name] = x;
                        }
                        catch (NotSupportedException) { /* a value this reader can't follow: the material keeps its own */ }
                    }
                    return s;
                }
                default: throw new NotSupportedException($"Mutable material op {p.code[o]} at {o}");
            }
        }

        public void Instance(uint r, List<Surface> list, int depth)
        {
            if (r == 0 || depth > 200) return;
            var o = Off(r);
            switch (p.code[o])
            {
                case 116: Instance(Num(p.U(o + 1)) != 0 ? p.U(o + 5) : p.U(o + 9), list, depth + 1); return;
                case 117: Switch<object>(o, x => { Instance(x, list, depth + 1); return null; }); return;
                case 118:
                {
                    Instance(p.U(o + 1), list, depth + 1);
                    var outer = component;
                    component = p.U(o + 9);
                    Instance(p.U(o + 5), list, depth + 1);
                    component = outer;
                    return;
                }
                case 119: Components.Add((o, component)); return; // a component's mesh
                case 121:
                case 122:
                {
                    Instance(p.U(o + 1), list, depth + 1);
                    var k = p.U(o + 9);
                    var name = p.code[o] == 121 ? p.names.GetValueOrDefault(k) : k < p.strings.Length ? p.strings[k] : null;
                    try
                    {
                        if (Material(p.U(o + 5), name) is { } s) list.Add(s);
                    }
                    catch (NotSupportedException) { /* a surface this reader can't follow keeps its material as is */ }
                    return;
                }
                default: throw new NotSupportedException($"Mutable instance op {p.code[o]} at {o}");
            }
        }
    }
}
