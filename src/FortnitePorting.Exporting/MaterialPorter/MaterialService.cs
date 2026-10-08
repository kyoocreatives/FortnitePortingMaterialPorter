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


static class MaterialService
{
    /// <summary>"/Game/X/T_A.0" (CUE4Parse's export index) -> "/Game/X/T_A.T_A".</summary>
    public static string ObjectPath(JToken reference)
    {
        if (reference == null || reference.Type != JTokenType.Object) return null;
        var path = (string)reference["ObjectPath"];
        var name = (string)reference["ObjectName"] ?? "";
        if (string.IsNullOrEmpty(path)) return null;
        var q = name.IndexOf('\'');
        if (q >= 0) name = name[(q + 1)..].TrimEnd('\'');
        name = name.Split(':').Last().Split('.').Last();
        var dot = path.LastIndexOf('.');
        return dot > 0 && path[(dot + 1)..].All(char.IsDigit) ? path[..dot] + "." + name : path;
    }
}
