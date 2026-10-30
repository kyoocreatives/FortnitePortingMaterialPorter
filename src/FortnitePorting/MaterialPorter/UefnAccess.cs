using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using FortnitePorting.Application;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using Serilog;

namespace FortnitePorting.MaterialPorter;

/// <summary>UEFN island and project export for people with a role on the owner's Discord server: they sign in with Discord
/// (no client secret: PKCE), and the role is read back from Discord at each start, so access needs internet at start.
/// Taking the role away ends it at their next start. Tokens are never logged.</summary>
public static class UefnAccess
{
    private const string ClientId = "1565762496038568047";
    private const string ServerId = "1565762720371056802";
    private const string RoleId = "1565762754143457452";
    private const string Redirect = "http://localhost:53682/signin/";
    private const string Api = "https://discord.com/api/v10";
    private static readonly TimeSpan StartWait = TimeSpan.FromSeconds(10);
    private static readonly HttpClient Http = new() { Timeout = StartWait };

    private sealed record Saved(string Refresh, string User);

    private static string StateFile => Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), Fork.AppFolder, "uefn-access.json");

    /// <summary>Who signed in, null if nobody.</summary>
    public static string? User { get; private set; }

    /// <summary>Discord couldn't be asked at this start: no access this run, the sign-in kept.</summary>
    public static bool Unreachable { get; private set; }

    /// <summary>Read once, at the first ask: the game's mounts and the map list depend on it for this run.</summary>
    public static bool Granted => LazyGranted.Value;

    private static readonly Lazy<bool> LazyGranted = new(() =>
    {
        // off the caller's thread: Avalonia's context would deadlock the wait
        var check = Task.Run(CheckAtStartAsync);
        if (check.Wait(StartWait)) return check.Result;
        Unreachable = true;
        return false;
    });

    private static async Task<bool> CheckAtStartAsync()
    {
        var saved = Load();
        if (saved is null) return false;
        User = saved.User;
        try
        {
#if DEBUG
            if (saved.Refresh == TestPrefix + "offline") throw new HttpRequestException("test");
            if (saved.Refresh.StartsWith(TestPrefix)) return saved.Refresh == TestPrefix + "1";
#endif
            return await RefreshAsync(saved);
        }
        catch (Exception e) when (e is HttpRequestException or TaskCanceledException or JsonException or IOException)
        {
            Log.Warning("[Material Porter] UEFN access: Discord didn't answer ({Error}), no access this run", e.GetType().Name);
            Unreachable = true;
            return false;
        }
    }

    /// <summary>Whether a Discord member answer holds the role.</summary>
    public static bool HasRole(JObject member) => member["roles"]?.Values<string>().Contains(RoleId) == true;

    private static async Task<bool> RefreshAsync(Saved saved)
    {
        var tokens = await TokenAsync(new() { ["grant_type"] = "refresh_token", ["refresh_token"] = saved.Refresh });
        if (tokens is null)
        {
            SignOut();      // the sign-in was revoked on Discord's side
            return false;
        }
        return await ReadRoleAsync(tokens);
    }

    /// <summary>Opens Discord's sign-in page and waits for its answer. Null when it worked (the app needs a restart to
    /// use it), else what to tell the user.</summary>
    public static async Task<string?> SignInAsync()
    {
        var verifier = Url(RandomNumberGenerator.GetBytes(48));
        var state = Url(RandomNumberGenerator.GetBytes(16));
        var challenge = Url(SHA256.HashData(Encoding.ASCII.GetBytes(verifier)));
        using var listener = new HttpListener();
        listener.Prefixes.Add(Redirect);
        try { listener.Start(); }
        catch (HttpListenerException) { return "Another sign-in is already waiting"; }
        AppServices.App.Launch($"https://discord.com/oauth2/authorize?response_type=code&client_id={ClientId}" +
                               $"&scope={Uri.EscapeDataString("identify guilds.members.read")}&redirect_uri={Uri.EscapeDataString(Redirect)}" +
                               $"&state={state}&code_challenge={challenge}&code_challenge_method=S256&prompt=none");
        using var waited = new CancellationTokenSource(TimeSpan.FromMinutes(5));
        HttpListenerContext context;
        try { context = await listener.GetContextAsync().WaitAsync(waited.Token); }
        catch (OperationCanceledException) { return "Sign-in timed out"; }
        var code = context.Request.QueryString["code"];
        var valid = code is not null && context.Request.QueryString["state"] == state;
        await using (var page = context.Response.OutputStream)
            await page.WriteAsync(Encoding.UTF8.GetBytes(valid ? "Signed in. You can close this tab." : "Sign-in was cancelled."));
        if (!valid) return "Sign-in was cancelled";
        try
        {
            var tokens = await TokenAsync(new()
            {
                ["grant_type"] = "authorization_code", ["code"] = code!, ["redirect_uri"] = Redirect, ["code_verifier"] = verifier,
            });
            if (tokens is null) return "Discord refused the sign-in";
            await ReadRoleAsync(tokens);
            return null;
        }
        catch (Exception e) when (e is HttpRequestException or TaskCanceledException)
        {
            return "Discord didn't answer";
        }
    }

    public static void SignOut()
    {
        User = null;
        File.Delete(StateFile);
    }

    private static async Task<JObject?> TokenAsync(Dictionary<string, string> form)
    {
        form["client_id"] = ClientId;
        using var answer = await Http.PostAsync(Api + "/oauth2/token", new FormUrlEncodedContent(form));
        if (answer.StatusCode is HttpStatusCode.BadRequest or HttpStatusCode.Unauthorized) return null;
        answer.EnsureSuccessStatusCode();
        return JObject.Parse(await answer.Content.ReadAsStringAsync());
    }

    // Discord replaces the refresh token each time: kept before anything else can fail.
    private static async Task<bool> ReadRoleAsync(JObject tokens)
    {
        var refresh = tokens["refresh_token"]!.ToString();
        Save(new Saved(refresh, User ?? "This Discord account"));
        using var request = new HttpRequestMessage(HttpMethod.Get, $"{Api}/users/@me/guilds/{ServerId}/member");
        request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", tokens["access_token"]!.ToString());
        using var answer = await Http.SendAsync(request);
        var allowed = false;
        var user = "This Discord account";
        if (answer.IsSuccessStatusCode)
        {
            var member = JObject.Parse(await answer.Content.ReadAsStringAsync());
            allowed = HasRole(member);
            user = member["user"]?["username"]?.ToString() ?? user;
        }
        else if (answer.StatusCode != HttpStatusCode.NotFound)      // 404: not on the server
            answer.EnsureSuccessStatusCode();
        User = user;
        Save(new Saved(refresh, user));
        return allowed;
    }

    private static Saved? Load()
    {
        try
        {
            return File.Exists(StateFile) ? JsonConvert.DeserializeObject<Saved>(File.ReadAllText(StateFile)) : null;
        }
        catch (Exception e) when (e is IOException or JsonException)
        {
            Log.Warning("[Material Porter] UEFN access: saved sign-in unreadable ({Error})", e.GetType().Name);
            return null;
        }
    }

    private static void Save(Saved saved)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(StateFile)!);
        File.WriteAllText(StateFile, JsonConvert.SerializeObject(saved));
    }

    private static string Url(byte[] data) => Convert.ToBase64String(data).TrimEnd('=').Replace('+', '-').Replace('/', '_');

#if DEBUG
    private const string TestPrefix = "test:";

    /// <summary>Test instance: what Discord answers at the next start: "1" the role, "0" no role, "offline" nothing.</summary>
    public static void SaveForTest(string answer) => Save(new Saved(TestPrefix + answer, "test user"));
#endif
}
