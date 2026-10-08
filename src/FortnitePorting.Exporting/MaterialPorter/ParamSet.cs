#nullable disable
using System.Globalization;
using System.Collections.Concurrent;
using System.Numerics;
using global::CUE4Parse.UE4.Assets.Exports;
using global::CUE4Parse.UE4.Assets.Exports.Component.StaticMesh;
using global::CUE4Parse.UE4.Objects.Core.Math;
using global::CUE4Parse.UE4.Objects.Core.Misc;
using global::CUE4Parse.UE4.Assets.Exports.Actor;
using global::CUE4Parse.UE4.Assets.Objects;
using global::CUE4Parse.UE4.Objects.Engine;
using global::CUE4Parse.UE4.Objects.UObject;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace FortnitePorting.Exporting.MaterialPorter;

public sealed class ParamSet
{
    public List<string> Materials { get; set; } = new();
    public Dictionary<string, double> Scalars { get; set; } = new();
    public Dictionary<string, double[]> Vectors { get; set; } = new();
    public Dictionary<string, string> Textures { get; set; } = new();
    /// <summary>Static switches (a weapon wrap's customization effects).</summary>
    public Dictionary<string, bool> Switches { get; set; } = new();
    /// <summary>The option that set them, for the built material's name.</summary>
    public string Label { get; set; }
    readonly List<string> labels = new();

    public void MergeFrom(ParamSet o)
    {
        foreach (var kv in o.Scalars) Scalars[kv.Key] = kv.Value;
        foreach (var kv in o.Vectors) Vectors[kv.Key] = kv.Value;
        foreach (var kv in o.Textures) Textures[kv.Key] = kv.Value;
        foreach (var kv in o.Switches) Switches[kv.Key] = kv.Value;
        // "Default" and unnamed options say nothing in a name
        if (o.Label is { } l && l != "Default" && !l.StartsWith("Option ") && !labels.Contains(l)) labels.Add(l);
        Label = labels.Count == 0 ? "style" : string.Join(", ", labels.Take(3)) + (labels.Count > 3 ? $" +{labels.Count - 3}" : "");
    }

    /// <summary>A short stable key for the values, so a styled material builds once.</summary>
    public string Key()
    {
        var s = string.Join(";", Scalars.OrderBy(k => k.Key).Select(k => k.Key + "=" + k.Value.ToString("R", CultureInfo.InvariantCulture)))
              + "|" + string.Join(";", Vectors.OrderBy(k => k.Key).Select(k => k.Key + "=" + string.Join(",", k.Value.Select(x => x.ToString("R", CultureInfo.InvariantCulture)))))
              + "|" + string.Join(";", Textures.OrderBy(k => k.Key).Select(k => k.Key + "=" + k.Value));
        // only when present, so keys of sets without switches are unchanged
        if (Switches.Count > 0) s += "|" + string.Join(";", Switches.OrderBy(k => k.Key).Select(k => k.Key + "=" + (k.Value ? "1" : "0")));
        return Convert.ToHexString(System.Security.Cryptography.SHA1.HashData(System.Text.Encoding.UTF8.GetBytes(s)))[..8].ToLowerInvariant();
    }
}
