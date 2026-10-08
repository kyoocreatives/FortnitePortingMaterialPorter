#nullable disable
using System.Security.Cryptography;
using global::CUE4Parse.FileProvider;
using global::CUE4Parse.UE4.Assets.Exports.Animation;
using global::CUE4Parse.UE4.Assets.Exports.Material;
using global::CUE4Parse.UE4.Assets.Exports.Texture;
using global::CUE4Parse_Conversion.Textures;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace FortnitePorting.Exporting.MaterialPorter;

/// <summary>
/// A LEGO figure that exists only as a recipe: its CustomizableObjectInstance ("COI_Figure_X_Dataless") on the shared Mutable
/// object CO_Figure_Recipe_Dataless. The program builds only the body geometry; the game dresses it from the recipe's values,
/// and so does this:
///  - body: the program's standard body mesh (a streamed constant, stomach panel merged in) with MI_Figure_DecoratedPlastic_RecipeCOv2
///    (or the variant a "Body Material Type" names). "Tex Color D" is a 32x32 grid of the body's layout blocks (layout 11), each the
///    LUT colour (T_LUT_Default, 512 x 1: pixel N is colour id N) of its part's "&lt;part&gt; Color" id; "Tex Color M" is the same
///    grid of block surfaces (metal, glow, see-through); deco and normal come from the recipe.
///  - head: the recipe's head mesh with its "Head Material"; base colour grid ("Tex Color-D") the LUT colour of the face material's
///    "Color Head ID"; character accents placed as the face rig places them (the schema's accent registrations).
///  - each accessory or replacement part ("&lt;X&gt; SKM"): its mesh with the recipe material, a 2x2 colour grid ("LU/RU/LL/RL Color
///    &lt;X&gt;": top left, top right, bottom left, bottom right), its surface grid, and its deco, mask and normal.
/// A hand or leg replacement removes the body's own part (the program's remove masks). The skeleton body ("Body Selector" Skeleton)
/// has its own layout (layout 10, 7 blocks) that its mesh doesn't tie to parts (rigid on 5 bones, UVs across the blocks); every
/// skeleton recipe gives all blocks the same values, which fill its whole grid. All parts are skinned to SK_Figure. Not done: cloth.
/// </summary>
public sealed class FigureRecipe
{
    public const string LutPath = "/JunoBase/EditorUtility/LUT_Generator/Variants/T_LUT_Default.T_LUT_Default";
    public const string SkeletonPath = "/FigureCharacter/Figure_Core/SkeletalMesh/SK_Figure.SK_Figure";
    const string MaterialFolder = "/FigureCharacter/Figure_Core/Material/MaterialInstance/";
    const string RecipeMaterial = MaterialFolder + "MI_Figure_DecoratedPlastic_RecipeCOv2.MI_Figure_DecoratedPlastic_RecipeCOv2";
    /// <summary>The program's bodies at LOD 0 by "Body Selector": the standard one, the skeleton.</summary>
    const int BodyConstant = 4, SkeletonBodyConstant = 13;
    /// <summary>The standard body's stomach panel: the body is open there, the program merges it in (torso block, body deco).</summary>
    const int StomachConstant = 37;
    /// <summary>The standard body's remove masks by replaced part (the program's constants 0..3).</summary>
    static readonly (string Part, int Constant)[] BodyMasks = [("Leg L", 0), ("Leg R", 1), ("Hand L", 2), ("Hand R", 3)];
    /// <summary>Bumped when the bodies built here change; their shared files are named by it.</summary>
    const int BodyRevision = 2;

    /// <summary>Generated textures' paths and folder (the app's MaterialService.GeneratedDir, which serves them).</summary>
    public const string GeneratedRoot = "/MaterialPorter/Generated/";
    public static string? GeneratedDir { get; set; }

    /// <summary>The body's colour layout: each part's block on the 32x32 grid (layout 11 of the program, matched to parts by bone).</summary>
    public static readonly (string Part, int X, int Y, int W, int H)[] BodyBlocks =
    [
        ("leg_lu", 20, 21, 12, 7), ("leg_ru", 20, 14, 12, 7), ("hand_l", 0, 18, 10, 4), ("torso", 20, 0, 12, 14),
        ("hand_r", 0, 28, 10, 4), ("hips", 0, 0, 10, 12), ("leg_rl", 10, 10, 10, 10), ("leg_ll", 10, 20, 10, 10),
        ("arm_ru", 0, 12, 4, 6), ("arm_lu", 0, 22, 4, 6), ("arm_rl", 4, 12, 5, 6), ("arm_ll", 4, 22, 5, 6),
    ];

    public string Instance { get; private init; } = "";
    public string CustomizableObject { get; private init; } = "";
    public Dictionary<string, string> Ints { get; } = new(StringComparer.OrdinalIgnoreCase);
    public Dictionary<string, double> Floats { get; } = new(StringComparer.OrdinalIgnoreCase);
    public Dictionary<string, string> Textures { get; } = new(StringComparer.OrdinalIgnoreCase);
    public Dictionary<string, string> Meshes { get; } = new(StringComparer.OrdinalIgnoreCase);
    public Dictionary<string, string> Materials { get; } = new(StringComparer.OrdinalIgnoreCase);

    /// <summary>A part of the figure to export.</summary>
    public sealed class Part
    {
        public string Name { get; init; } = "";
        /// <summary>A cooked skeletal mesh's path; null for the body (<see cref="Raw"/>).</summary>
        public string? Mesh { get; init; }
        public RawSkinnedMesh? Raw { get; init; }
        /// <summary>The body's name, the same for every figure with that body (its file is shared).</summary>
        public string RawName { get; init; } = "";
        /// <summary>The material for all its slots, or null: the mesh's own.</summary>
        public string? Material { get; init; }
        public Dictionary<string, string> Textures { get; } = new(StringComparer.OrdinalIgnoreCase);
        public Dictionary<string, double> Scalars { get; } = new(StringComparer.OrdinalIgnoreCase);
        /// <summary>The head's character-accent placement for every mouth pose (<see cref="AccentRigAsync"/>), or null.</summary>
        public string? FaceRig { get; set; }
    }

    static readonly JsonSerializerSettings Ser = new() { ReferenceLoopHandling = ReferenceLoopHandling.Ignore };

    /// <summary>A recipe from its instance's path (a CustomizableObjectInstance), or null.</summary>
    public static async Task<FigureRecipe?> LoadAsync(IFileProvider provider, string instancePath)
    {
        var package = instancePath.Contains('.') ? instancePath[..instancePath.LastIndexOf('.')] : instancePath;
        var pkg = await provider.LoadPackageAsync(package);
        var coi = pkg.GetExports().FirstOrDefault(e => e.ExportType == "CustomizableObjectInstance");
        if (coi is null) return null;
        var desc = JObject.Parse(JsonConvert.SerializeObject(coi, Ser))["Properties"]?["Descriptor"];
        if (desc is null) return null;
        var recipe = new FigureRecipe { Instance = instancePath, CustomizableObject = ObjectPath(desc["CustomizableObject"]) ?? "" };
        foreach (var x in desc["IntParameters"] ?? new JArray())
            if ((string?)x["ParameterName"] is { } n && (string?)x["ParameterValueName"] is { } v) recipe.Ints[n] = v;
        foreach (var x in desc["FloatParameters"] ?? new JArray())
            if ((string?)x["ParameterName"] is { } n && x["ParameterValue"] is { Type: JTokenType.Float or JTokenType.Integer } v) recipe.Floats[n] = (double)v;
        void Objects(string key, Dictionary<string, string> into)
        {
            foreach (var x in desc[key] ?? new JArray())
                if ((string?)x["ParameterName"] is { } n && ObjectPath(x["ParameterValue"]) is { } p) into[n] = p;
        }
        Objects("TextureParameters", recipe.Textures);
        Objects("SkeletalMeshParameters", recipe.Meshes);
        Objects("MaterialParameters", recipe.Materials);
        return recipe;
    }

    /// <summary>A serialized object reference ({ObjectName: "Class'Name'", ObjectPath: "/Pkg/Name.0"}) as "/Pkg/Name.Name".</summary>
    static string? ObjectPath(JToken? token)
    {
        if (token is not JObject o || (string?)o["ObjectPath"] is not { Length: > 0 } path || (string?)o["ObjectName"] is not { } name) return null;
        var q = name.IndexOf('\'');
        if (q >= 0) name = name[(q + 1)..].TrimEnd('\'');
        var dot = path.LastIndexOf('.');
        return (dot > path.LastIndexOf('/') ? path[..dot] : path) + "." + name.Split(':')[^1];
    }

    // ------------------------------------------------------------ shared data (once per game)

    static readonly SemaphoreSlim Gate = new(1, 1);
    static IFileProvider? _provider;
    static byte[]? _lut;
    static readonly Dictionary<int, RawSkinnedMesh> _bodies = new();
    static readonly Dictionary<int, HashSet<int>> _masks = new();
    static USkeleton? _skeleton;

    public static async Task<USkeleton> SkeletonAsync(IFileProvider provider)
    {
        await Shared(provider, null);
        return _skeleton!;
    }

    static async Task Shared(IFileProvider provider, string? customizableObject, int body = BodyConstant)
    {
        await Gate.WaitAsync();
        try
        {
            if (!ReferenceEquals(_provider, provider)) { _provider = provider; _lut = null; _bodies.Clear(); _masks.Clear(); _skeleton = null; }
            _skeleton ??= await provider.LoadPackageObjectAsync<USkeleton>(SkeletonPath);
            if (_lut is null)
            {
                var lut = (await provider.LoadPackageObjectAsync<UTexture2D>(LutPath)).Decode(ETexturePlatform.DesktopMobile)
                          ?? throw new InvalidDataException("the LEGO colour LUT doesn't decode");
                var rgba = new byte[lut.Width * 4];
                var bgra = lut.PixelFormat == global::CUE4Parse.UE4.Assets.Exports.Texture.EPixelFormat.PF_B8G8R8A8;
                for (var x = 0; x < lut.Width; x++)
                {
                    rgba[x * 4] = lut.Data[x * 4 + (bgra ? 2 : 0)];
                    rgba[x * 4 + 1] = lut.Data[x * 4 + 1];
                    rgba[x * 4 + 2] = lut.Data[x * 4 + (bgra ? 0 : 2)];
                    rgba[x * 4 + 3] = lut.Data[x * 4 + 3];
                }
                _lut = rgba;
            }
            if (customizableObject is not null && !_bodies.ContainsKey(body))
            {
                var program = await MutableMeshes.LoadAsync(provider, customizableObject.Split('.')[0]);
                var bones = _skeleton.ReferenceSkeleton.FinalRefBoneInfo.Select(b => b.Name.Text).ToList();
                var mesh = program.Skinned(body, bones) ?? throw new InvalidDataException("the figure body didn't decode");
                if (body == BodyConstant && program.Skinned(StomachConstant, bones) is { } stomach) mesh = mesh.Merged(stomach);
                _bodies[body] = mesh;
                if (body == BodyConstant)
                    foreach (var (_, constant) in BodyMasks) _masks[constant] = program.MaskVertices(constant);
            }
        }
        finally { Gate.Release(); }
    }

    /// <summary>A LEGO colour id's sRGB colour (the LUT's pixel), opaque.</summary>
    static byte[] Colour(double id)
    {
        var i = Math.Clamp((int)Math.Round(id), 0, _lut!.Length / 4 - 1);
        return [_lut[i * 4], _lut[i * 4 + 1], _lut[i * 4 + 2], 255];
    }

    /// <summary>
    /// A block's surface as the material reads "Tex Color M" (R metallic, G ink, B surface type, A emissive): R is the colour's
    /// metallic (the LUT's alpha: 255 for metallic colours) unless "Metal Enum" overrides it with "Metal Value"; B is "Surface"
    /// (the see-through share in a transparent material); A is "Glow". Values are named "&lt;prefix&gt; Glow" (a body block) or
    /// "&lt;prefix&gt; Glow &lt;part&gt;" (a quadrant).
    /// </summary>
    byte[] Surface(string prefix, string? part, double colour)
    {
        string Name(string what) => part is null ? $"{prefix} {what}" : $"{prefix} {what} {part}";
        static byte Byte(double v) => (byte)Math.Clamp((int)Math.Round(v * 255), 0, 255);
        var i = Math.Clamp((int)Math.Round(colour), 0, _lut!.Length / 4 - 1);
        var metal = (Ints.GetValueOrDefault(Name("Metal Enum")) ?? "").Equals("OverrideMetalValue", StringComparison.OrdinalIgnoreCase)
            ? Byte(Floats.GetValueOrDefault(Name("Metal Value"))) : _lut[i * 4 + 3];
        return [metal, 0, Byte(Floats.GetValueOrDefault(Name("Surface"))), Byte(Floats.GetValueOrDefault(Name("Glow")))];
    }

    // ------------------------------------------------------------ the figure

    /// <summary>The figure's parts, the body first. Writes its colour grids to <see cref="GeneratedDir"/>.</summary>
    public async Task<List<Part>> PartsAsync(IFileProvider provider)
    {
        if (!CustomizableObject.Contains("/RecipeSystem/", StringComparison.OrdinalIgnoreCase))
            throw new InvalidDataException("not a figure recipe (its object is " + CustomizableObject + ")");
        var bodyConstant = Ints.GetValueOrDefault("Body Selector") is "Skeleton" ? SkeletonBodyConstant : BodyConstant;
        await Shared(provider, CustomizableObject, bodyConstant);
        var parts = new List<Part>();

        // the body: layout blocks in their parts' colours; a hand or leg replacement removes the body's own part
        var raw = _bodies[bodyConstant];
        var rawName = (bodyConstant == BodyConstant ? "FigureBody" : "FigureBodySkeleton") + "_r" + BodyRevision;
        if (bodyConstant == BodyConstant)
            foreach (var (replaced, constant) in BodyMasks)
                if (Meshes.ContainsKey(replaced + " SKM") && !(Ints.GetValueOrDefault(replaced + " Replacement") ?? "None").Equals("None", StringComparison.OrdinalIgnoreCase)
                    && _masks.TryGetValue(constant, out var mask) && mask.Count > 0)
                {
                    raw = raw.Without(mask);
                    rawName += "_No" + replaced.Replace(" ", "");
                }
        var body = new Part { Name = "Body", Raw = raw, RawName = rawName, Material = MaterialFor(provider, Ints.GetValueOrDefault("Body Material Type")) };
        // the skeleton body: its torso's values over the whole grid (all its blocks have the same)
        var layout = bodyConstant == BodyConstant ? BodyBlocks : [("torso", 0, 0, 32, 32)];
        body.Textures["Tex Color D"] = Grid("FigureBody", layout.Select(b => (b.X, b.Y, b.W, b.H, Colour(Floats.GetValueOrDefault(b.Part + " Color", 1)))));
        // the recipe's "Body Color M" is a placeholder the game fills, as it fills "Body Color D"
        body.Textures["Tex Color M"] = Grid("FigureBodyM", layout.Select(b => (b.X, b.Y, b.W, b.H, Surface(b.Part, null, Floats.GetValueOrDefault(b.Part + " Color", 1)))), linear: true);
        Copy(body, "Body Deco D", "Tex Deco D");
        Copy(body, "Body Deco M", "Tex Deco M");
        Copy(body, "Body Normal", "Tex Normal");
        if (Floats.TryGetValue("Body Metallic Power Deco", out var metal)) body.Scalars["Metallic Power Deco"] = metal;
        if (Floats.TryGetValue("Body Emissive Mult", out var em)) body.Scalars["Emissive Mult"] = em;
        if (Floats.TryGetValue("Body Element Emissive Mult", out var eem)) body.Scalars["Element Emissive Mult"] = eem;
        parts.Add(body);

        // the head: its face material's "Color Head ID" gives the base colour id (a grid the game fills), else the recipe's
        // "Head Standard Color" (ColorID_24_...)
        if (Meshes.TryGetValue("Head SKM", out var head))
        {
            var face = Materials.GetValueOrDefault("Head Material");
            var headPart = new Part { Name = "Head", Mesh = head, Material = face };
            var id = (face is null ? null : await ScalarAsync(provider, face, "Color Head ID"))
                     ?? (Ints.GetValueOrDefault("Head Standard Color") is { } std && std.Split('_') is { Length: > 1 } bits && int.TryParse(bits[1], out var n) ? n : 24);
            headPart.Textures["Tex Color-D"] = Grid("FigureHead", [(0, 0, 32, 32, Colour(id))]);
            // an expression picked (the face rig's poses), and the character accents (mustache, beard) where the face rig
            // places them for its mouth pose
            foreach (var (k, v) in FaceScalars(FacePoses)) headPart.Scalars[k] = v;
            if (face is not null)
                foreach (var (k, v) in await AccentScalarsAsync(provider, SchemaPath ?? ConventionSchema(), face, FacePoses.TryGetValue("Mouth", out var mouth) ? mouth : null))
                    headPart.Scalars[k] = v;
            // and for every mouth pose, for a face an emote animates
            headPart.FaceRig = await AccentRigAsync(provider, SchemaPath ?? ConventionSchema());
            parts.Add(headPart);
        }

        // accessories and replacement parts, each where its mode switch has it (Extend: the recipe's material and colours;
        // Override: its own override material)
        foreach (var (param, mesh) in Meshes)
        {
            if (!param.EndsWith(" SKM", StringComparison.OrdinalIgnoreCase) || param.Equals("Head SKM", StringComparison.OrdinalIgnoreCase)) continue;
            var x = param[..^4];
            var mode = Ints.GetValueOrDefault(ModeParameter(x)) ?? "None";
            if (mode.Equals("None", StringComparison.OrdinalIgnoreCase)) continue;
            if (x.Equals("Cape", StringComparison.OrdinalIgnoreCase) || x.Equals("NeckCloth", StringComparison.OrdinalIgnoreCase))
            {
                parts.Add(new Part { Name = x, Mesh = mesh, Material = Materials.GetValueOrDefault(x + " Material") });
                continue;
            }
            var material = mode.EndsWith("Override", StringComparison.OrdinalIgnoreCase) && Materials.TryGetValue(x + " Override Material", out var own)
                ? own : MaterialFor(provider, Ints.GetValueOrDefault(x + " Material"));
            var part = new Part { Name = x, Mesh = mesh, Material = material };
            // quadrants: top left, top right, bottom left, bottom right
            var quads = new[] { ("LU", 0, 0), ("RU", 16, 0), ("LL", 0, 16), ("RL", 16, 16) };
            part.Textures["Tex Color D"] = Grid("FigurePart", quads.Select(q => (q.Item2, q.Item3, 16, 16, Colour(Floats.GetValueOrDefault($"{q.Item1} Color {x}", 1)))));
            part.Textures["Tex Color M"] = Grid("FigurePartM", quads.Select(q => (q.Item2, q.Item3, 16, 16, Surface(q.Item1, x, Floats.GetValueOrDefault($"{q.Item1} Color {x}", 1)))), linear: true);
            Copy(part, x + " Deco D", "Tex Deco D");
            Copy(part, x + " Deco Mask", "Tex Deco M");
            Copy(part, x + " Normal", "Tex Normal");
            if (Floats.TryGetValue(x + " EmissiveMult", out var pem)) part.Scalars["Emissive Mult"] = pem;
            if (Floats.TryGetValue(x + " ElementEmissiveMult", out var peem)) part.Scalars["Element Emissive Mult"] = peem;
            parts.Add(part);
        }
        return parts;
    }

    /// <summary>A material instance's scalar parameter, from it or the nearest parent instance that sets it.</summary>
    static async Task<double?> ScalarAsync(IFileProvider provider, string material, string name)
    {
        try
        {
            var mi = await provider.LoadPackageObjectAsync<UMaterialInstanceConstant>(material);
            for (var depth = 0; mi is not null && depth < 16; depth++)
            {
                foreach (var p in mi.ScalarParameterValues)
                    if (p.ParameterInfo.Name.Text.Equals(name, StringComparison.OrdinalIgnoreCase)) return p.ParameterValue;
                mi = mi.Parent as UMaterialInstanceConstant;
            }
        }
        catch
        {
            // a material that doesn't load: use the fallback
        }
        return null;
    }

    /// <summary>The figure's AssembledMeshSchema (its data assets for the face rig): set by the caller, else /FigureCosmetics/AMS/AMS_&lt;figure folder&gt;.</summary>
    public string? SchemaPath { get; set; }

    string? ConventionSchema() => Instance.Split('/').FirstOrDefault(x => x.StartsWith("Figure_", StringComparison.OrdinalIgnoreCase)) is { } folder
        ? $"/FigureCosmetics/AMS/AMS_{folder}.AMS_{folder}" : null;

    /// <summary>An expression: the face rig's pose of each feature ("Mouth", "Eyes", "Brows"), over the face material's own. Poses are atlas cells (DA_Figure_Face_Settings: 46 mouths, 6 eyes, 12 brows).</summary>
    public Dictionary<string, int> FacePoses { get; } = new(StringComparer.OrdinalIgnoreCase);

    /// <summary>The face material's pose parameters for an expression's picks.</summary>
    public static Dictionary<string, double> FaceScalars(IReadOnlyDictionary<string, int> poses)
    {
        var scalars = new Dictionary<string, double>();
        foreach (var (feature, pose) in poses)
        {
            string[] names = feature.ToLowerInvariant() switch
            {
                "mouth" => ["MouthPose"],
                "eyes" => ["EyeLeftPose", "EyeRightPose"],
                "brows" => ["BrowLeftPose", "BrowRightPose"],
                _ => [],
            };
            foreach (var name in names) scalars[name] = pose;
        }
        return scalars;
    }

    /// <summary>Rig units (a registration's translation) to face UV units, fitted on the cooked figures' baked values.</summary>
    const double RigToFaceUv = 0.0413;

    /// <summary>
    /// What the face rig does with a figure's character accents: the schema's CharacterAcc data names each accent's registration
    /// ("beard_bean", "beard" or None), its BeardRegistration data gives that registration's transform and pose per mouth pose.
    /// The accent sits at the mouth plus the transform's x/z in face UV (V = MouthV + z * 0.0413 matches the baked figures:
    /// -0.118 for a mustache's 1.993, -0.191 for a goatee's 0.215), unscaled. The mouth pose is the face material's, or the one
    /// given (a picked expression). Also used for a cooked figure's face, whose baked values hold its own mouth pose's.
    /// </summary>
    public static async Task<Dictionary<string, double>> AccentScalarsAsync(IFileProvider provider, string? schema, string face, int? mouthPose = null)
    {
        var scalars = new Dictionary<string, double>();
        var accents = await AccentRegistrationsAsync(provider, schema);
        if (accents.Count == 0) return scalars;
        var mouthU = await ScalarAsync(provider, face, "MouthU") ?? 0;
        var mouthV = await ScalarAsync(provider, face, "MouthV") ?? 0;
        var pose = mouthPose ?? (int)Math.Round(await ScalarAsync(provider, face, "MouthPose") ?? 0);
        foreach (var (a, byPose) in accents)
        {
            var (x, z, accentPose) = byPose[Math.Clamp(pose, 0, byPose.Count - 1)];
            scalars[$"CharacterAccent{a}U"] = mouthU + x * RigToFaceUv;
            scalars[$"CharacterAccent{a}V"] = mouthV + z * RigToFaceUv;
            scalars[$"CharacterAccent{a}Pose"] = accentPose;
        }
        return scalars;
    }

    /// <summary>
    /// The face rig's character-accent placement for every mouth pose, for a face animated later (an emote's mouth curves move the
    /// accents with the mouth): JSON {"k": rig units to face UV, "accents": {"1": [[x, z, accent pose] per mouth pose], ...}},
    /// the accent at the mouth's U/V + (x, z) * k; null when the figure has no registered accent.
    /// </summary>
    public static async Task<string?> AccentRigAsync(IFileProvider provider, string? schema)
    {
        var accents = await AccentRegistrationsAsync(provider, schema);
        if (accents.Count == 0) return null;
        var table = new JObject();
        foreach (var (a, byPose) in accents)
            table[a.ToString()] = new JArray(byPose.Select(e => new JArray(e.X, e.Z, e.Pose)));
        return new JObject { ["k"] = RigToFaceUv, ["accents"] = table }.ToString(Formatting.None);
    }

    /// <summary>Each registered character accent (1..4): its offset from the mouth (x, z) and pose, by mouth pose.</summary>
    static async Task<List<(int Accent, List<(double X, double Z, double Pose)> ByPose)>> AccentRegistrationsAsync(IFileProvider provider, string? schema)
    {
        var accents = new List<(int, List<(double, double, double)>)>();
        var props = schema is null ? null : await PropertiesAsync(provider, schema);
        var maps = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        foreach (var data in props?["AdditionalData"] ?? new JArray())
        foreach (var kv in data["DataAssetMap"] ?? new JArray())
            if ((string?)kv["Key"] is { } key && ObjectPath(kv["Value"]) is { } path) maps[key] = path;
        if (!maps.TryGetValue("CharacterAcc", out var accPath) || await PropertiesAsync(provider, accPath) is not { } acc) return accents;
        var registrations = (acc["Character Accent Registration"] as JArray)?.Select(x => (string?)x ?? "None").ToList() ?? [];
        var beard = maps.TryGetValue("BeardRegistration", out var beardPath) ? await PropertiesAsync(provider, beardPath) : null;
        for (var i = 0; i < Math.Min(4, registrations.Count); i++)
        {
            var (transforms, poses) = registrations[i].ToLowerInvariant() switch
            {
                "beard_bean" => ("BeardBean", "BeardBeanPoses"),
                "beard" => ("Beard", "BeardPoses"),
                _ => (null, null),
            };
            if (transforms is null || beard?[transforms] is not JArray list || list.Count == 0) continue;
            var p = beard[poses] as JArray;
            accents.Add((i + 1, list.Select((t, n) => (
                (double?)t["Translation"]?["X"] ?? 0,
                (double?)t["Translation"]?["Z"] ?? 0,
                p is { Count: > 0 } ? (double?)p[Math.Min(n, p.Count - 1)] ?? 0 : 0)).ToList()));
        }
        return accents;
    }

    /// <summary>An object's tagged properties as JSON, or null.</summary>
    static async Task<JToken?> PropertiesAsync(IFileProvider provider, string objectPath)
    {
        try
        {
            var package = objectPath.Contains('.') ? objectPath[..objectPath.LastIndexOf('.')] : objectPath;
            var name = objectPath[(objectPath.LastIndexOf('.') + 1)..];
            var exports = (await provider.LoadPackageAsync(package)).GetExports().ToList();
            var obj = exports.FirstOrDefault(e => e.Name == name) ?? exports.FirstOrDefault();
            return obj is null ? null : JObject.Parse(JsonConvert.SerializeObject(obj, Ser))["Properties"];
        }
        catch
        {
            return null;
        }
    }

    /// <summary>The int parameter that turns a part on ("None", "... Extend", "... Override", "... ON").</summary>
    static string ModeParameter(string part) => part switch
    {
        "Head Acc" => "Head Accessory",
        "Head Acc Extra" => "Head Accessory Extra",
        "Neck Acc" => "Neck Accessory",
        "Neck Acc Extra" => "Neck Accessory Extra",
        "Hip Acc" => "Hip Accessory",
        "Cape" => "Cape",
        "NeckCloth" => "NeckCloth",
        _ => part + " Replacement",     // Hand L, Hand R, Leg L, Leg R
    };

    void Copy(Part part, string from, string to)
    {
        if (Textures.TryGetValue(from, out var path)) part.Textures[to] = path;
    }

    /// <summary>The plastic material a "... Material Type" value names: Default is the recipe's own.</summary>
    static string MaterialFor(IFileProvider provider, string? type)
    {
        if (string.IsNullOrEmpty(type) || type.Equals("Default", StringComparison.OrdinalIgnoreCase)) return RecipeMaterial;
        var name = "MI_Figure_DecoratedPlastic_" + type.Replace(" ", "").Replace("_", "");
        var path = MaterialFolder + name;
        return provider.TryGetGameFile(path + ".uasset", out _) ? path + "." + name : RecipeMaterial;
    }

    /// <summary>
    /// A 32x32 colour grid of blocks (x, y, w, h in pixels), written as a PNG 8 times the size (the game samples it unfiltered, so
    /// blocks stay sharp when Blender filters it). Returns its generated texture path (named _Lin when it holds data, not colour).
    /// </summary>
    static string Grid(string kind, IEnumerable<(int X, int Y, int W, int H, byte[] Rgba)> blocks, bool linear = false)
    {
        const int size = 32, scale = 8;
        var px = new byte[size * size * 4];
        foreach (var (x0, y0, w, h, c) in blocks)
            for (var y = y0; y < Math.Min(size, y0 + h); y++)
            for (var x = x0; x < Math.Min(size, x0 + w); x++)
                Array.Copy(c, 0, px, (y * size + x) * 4, 4);
        var name = kind + "_" + Convert.ToHexString(SHA1.HashData(px))[..12] + (linear ? "_Lin" : "");
        var dir = GeneratedDir ?? throw new InvalidOperationException("FigureRecipe.GeneratedDir isn't set");
        var file = Path.Combine(dir, name + ".png");
        if (!File.Exists(file))
        {
            using var bitmap = new SkiaSharp.SKBitmap(size * scale, size * scale, SkiaSharp.SKColorType.Rgba8888, SkiaSharp.SKAlphaType.Unpremul);
            for (var y = 0; y < size * scale; y++)
            for (var x = 0; x < size * scale; x++)
            {
                var o = ((y / scale) * size + x / scale) * 4;
                bitmap.SetPixel(x, y, new SkiaSharp.SKColor(px[o], px[o + 1], px[o + 2], px[o + 3]));
            }
            using var png = bitmap.Encode(SkiaSharp.SKEncodedImageFormat.Png, 100);
            File.WriteAllBytes(file, png.ToArray());
        }
        return GeneratedRoot + name + "." + name;
    }
}
