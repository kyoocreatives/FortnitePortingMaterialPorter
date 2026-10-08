#nullable disable
using System.Net;
using System.Text;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace FortnitePorting.MaterialPorter;

/// <summary>
/// The app's end of the link, on localhost: while Blender builds a material
/// it asks here for what the graph needs - each function's graph, each
/// texture, each parameter collection - so only what the material really
/// reads is exported.
///
///   GET /ping                        {"app", "build", "mounted"}
///   GET /graph?path=/Game/.../MF_X   {"file"}  a material or function graph
///   GET /texture?path=/Game/.../T_X  {"file", "srgb", "kind", "depth", ...}
///   GET /collection?path=...         {"scalars", "vectors"}
///   GET /material?path=...           the instance, described
///   GET /find-materials?path=A,B     {"A": [object path, ...]}  material assets by name
///   GET /file?path=C:\...            the bytes of a file the app exported
///
/// /file is for a Blender that can't see the app's files where the app sees
/// them (Windows redirects AppData writes of a packaged launcher's children
/// to a private folder). It serves only files inside the app's data folder.
/// </summary>
public sealed class Bridge : IDisposable
{
    readonly GameContext game;
    readonly MaterialService materials;
    HttpListener listener;
    public event Action<string> Log;
    public int Port { get; private set; }
    /// <summary>Routes a host adds (route, query) -> the answer; null: none.</summary>
    public Func<string, System.Collections.Specialized.NameValueCollection, Task<object>> Extra { get; set; }

    public Bridge(GameContext game, MaterialService materials)
    {
        this.game = game;
        this.materials = materials;
    }

    public void Start(int port)
    {
        Port = port;
        listener = new HttpListener();
        listener.Prefixes.Add($"http://localhost:{port}/");
        listener.Start();
        _ = Task.Run(Loop);
    }

    async Task Loop()
    {
        while (listener?.IsListening == true)
        {
            HttpListenerContext ctx;
            try { ctx = await listener.GetContextAsync(); }
            catch { return; }
            _ = Task.Run(() => Handle(ctx));
        }
    }

    async Task Handle(HttpListenerContext ctx)
    {
        var q = ctx.Request.QueryString;
        var route = ctx.Request.Url?.AbsolutePath.Trim('/') ?? "";
        var path = q["path"];
        if (route == "file")
        {
            await SendFile(ctx, path);
            return;
        }
        object result;
        var status = 200;
        var sw = System.Diagnostics.Stopwatch.StartNew();
        try
        {
            result = route switch
            {
                "ping" => new { app = "MaterialPorter", build = game.BuildVersion, mounted = game.Mounted },
                "graph" => new { file = await materials.GraphAsync(Need(path)) },
                // cap=<pixels>: the import's texture size cap (its own smaller mip)
                "texture" => await materials.BlenderTextureAsync(Need(path), int.TryParse(q["cap"], out var cap) && cap > 0 ? cap : null),
                "collection" => await materials.CollectionAsync(Need(path)),
                "material" => (await materials.DescribeAsync(Need(path))).ToJson(),
                "find-materials" => await materials.FindMaterialsAsync(Need(path).Split(',')),
                _ => Extra != null && await Extra(route, q) is { } extra ? extra : throw new KeyNotFoundException("no route " + route),
            };
            if (route is not ("ping" or "log")) Log?.Invoke($"Blender asked for {route} {ShortName(path)}");   // "log": Blender telling what it does
            Timing.Log($"bridge {route} {ShortName(path)}", sw);
        }
        catch (Exception e)
        {
            status = e is KeyNotFoundException or FileNotFoundException ? 404 : 500;
            result = new { error = e.Message };
            Log?.Invoke($"{route} {ShortName(path)}: {e.Message}");
        }
        var bytes = Encoding.UTF8.GetBytes(JsonConvert.SerializeObject(result));
        ctx.Response.StatusCode = status;
        ctx.Response.ContentType = "application/json";
        ctx.Response.ContentLength64 = bytes.Length;
        try
        {
            await ctx.Response.OutputStream.WriteAsync(bytes);
            ctx.Response.Close();
        }
        catch { /* Blender went away */ }
    }

    /// <summary>A file of the app's data folder, as bytes (nothing outside it).</summary>
    async Task SendFile(HttpListenerContext ctx, string path)
    {
        var root = Path.GetFullPath(GameContext.DataDir).TrimEnd('\\') + "\\";
        string full = null;
        try { full = string.IsNullOrEmpty(path) ? null : Path.GetFullPath(path); }
        catch { /* not a path */ }
        try
        {
            if (full == null || !full.StartsWith(root, StringComparison.OrdinalIgnoreCase) || !File.Exists(full))
            {
                ctx.Response.StatusCode = 404;
                ctx.Response.Close();
                return;
            }
            var bytes = await File.ReadAllBytesAsync(full);
            ctx.Response.StatusCode = 200;
            ctx.Response.ContentType = "application/octet-stream";
            ctx.Response.ContentLength64 = bytes.Length;
            await ctx.Response.OutputStream.WriteAsync(bytes);
            ctx.Response.Close();
            Log?.Invoke("Blender fetched " + Path.GetFileName(full));
        }
        catch { /* Blender went away */ }
    }

    static string Need(string path) => string.IsNullOrWhiteSpace(path) ? throw new ArgumentException("path missing") : path;

    public static string ShortName(string path) => path?.Split('/').Last().Split('.').First() ?? "";

    public void Dispose()
    {
        try { listener?.Stop(); listener?.Close(); } catch { /* already down */ }
        listener = null;
    }
}
