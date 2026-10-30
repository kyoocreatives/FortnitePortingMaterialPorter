using System;

namespace FortnitePorting.MaterialPorter;

/// <summary>The fork's own names (settings, folders, pipe, mutex, ports) so it runs beside an installed FortnitePorting.</summary>
// FORTNITEPORTING_MP_PROFILE (e.g. "test") runs a separate instance with its own folders, lock and bridge port.
public static partial class Fork
{
    /// <summary>Sent on the single-instance pipe by a plain second launch: the running app comes to the front.</summary>
    public const string ShowRequest = "fp-engine://show";

    // owner builds (App.props) add to these; without it they compile to nothing
    static partial void OwnerStart(Avalonia.Controls.Window window);
    static partial void OwnerComposition(ref Avalonia.Win32CompositionMode[] modes);

    public static void Start(Avalonia.Controls.Window window, string[] args)
    {
        Engine.Start(window, args);
        OwnerStart(window);
    }

    /// <summary>Starts this exe again; the new one waits for this one to quit instead of handing over to it and leaving.</summary>
    public static void Restart() => System.Diagnostics.Process.Start(Environment.ProcessPath!, RestartArg);

    private const string RestartArg = "--restart";

    public static bool WaitAfterRestart(System.Threading.Mutex instance, string[] args)
    {
        if (Array.IndexOf(args, RestartArg) < 0) return false;
        try { return instance.WaitOne(TimeSpan.FromSeconds(30)); }
        catch (System.Threading.AbandonedMutexException) { return true; }     // it was killed: the mutex is ours anyway
    }

    public static bool HandlePipe(string message)
    {
        if (message != ShowRequest) return false;
        Engine.Show();
        return true;
    }

    /// <summary>What a second launch sends the running app. Only the launched process may take the foreground,
    /// so it hands that right on; otherwise the shown window opens behind the one in front.</summary>
    public static string PipeMessage(string[] args)
    {
        if (OperatingSystem.IsWindows()) AllowSetForegroundWindow(-1);     // ASFW_ANY
        return args.Length > 0 ? args[0] : ShowRequest;
    }

    [System.Runtime.InteropServices.LibraryImport("user32.dll")]
    [return: System.Runtime.InteropServices.MarshalAs(System.Runtime.InteropServices.UnmanagedType.Bool)]
    private static partial bool AllowSetForegroundWindow(int processId);

    public static Avalonia.Win32CompositionMode[] CompositionModes
    {
        get
        {
            Avalonia.Win32CompositionMode[] modes = [Avalonia.Win32CompositionMode.WinUIComposition];
            OwnerComposition(ref modes);
            return modes;
        }
    }

    public static readonly string Profile = Environment.GetEnvironmentVariable("FORTNITEPORTING_MP_PROFILE")?.Trim() ?? "";
    public static string AppFolder => Profile.Length == 0 ? "FortnitePorting MP" : $"FortnitePorting MP {Profile}";
    public static string InstancePipe => "FortnitePortingMP" + Profile;
    public static string InstanceMutex => "FortnitePortingMPMutex" + Profile;
    /// <summary>Port where Blender asks for exact materials (Material Porter's own app uses 24300). Override: FORTNITEPORTING_MP_BRIDGE_PORT.</summary>
    public static int BridgePort => int.TryParse(Environment.GetEnvironmentVariable("FORTNITEPORTING_MP_BRIDGE_PORT"), out var port) && port > 0
        ? port
        : Profile.Length == 0 ? 24320 : 24322;
    /// <summary>The Blender plugin's folder in scripts/startup (its Python package name).</summary>
    public const string PluginFolder = "fortnite_porting_mp";
    /// <summary>Upstream's Blender plugin listens on 40000.</summary>
    public const int BlenderPort = 40010;

    /// <summary>FORTNITEPORTING_MP_QUIET=1 (Debug test instances): the window opens minimized and never takes the focus,
    /// so a test run doesn't interrupt whatever is in front.</summary>
    public static void Quiet(Avalonia.Controls.Window window)
    {
#if DEBUG
        if (Environment.GetEnvironmentVariable("FORTNITEPORTING_MP_QUIET") != "1") return;
        window.ShowActivated = false;
        window.WindowState = Avalonia.Controls.WindowState.Minimized;
#endif
    }

    /// <summary>UEFN island export (downloaded islands, unlock by map code, local projects): with the owner's Discord role
    /// (UefnAccess), or always where the git-ignored Fork.local.props sets MPIslands.</summary>
#if MP_ISLANDS
    public const bool IslandsAlwaysOn = true;
    public static bool Islands => true;
#else
    public const bool IslandsAlwaysOn = false;
    public static bool Islands => UefnAccess.Granted;
#endif

    /// <summary>Older-build download from a manifest (build picker, UEFN manifest, Unreal version detection): owner builds only, set by Fork.local.props (MPOlderBuilds).</summary>
#if MP_OLDER_BUILDS
    public const bool OlderBuilds = true;
#else
    public const bool OlderBuilds = false;
#endif

    /// <summary>Time of Day export: only with the owner's private overlay (fpfork-private) built in; otherwise the tab is hidden and export refused.</summary>
#if MP_TIME_OF_DAY
    public const bool TimeOfDayExport = true;
#else
    public const bool TimeOfDayExport = false;
#endif
}
