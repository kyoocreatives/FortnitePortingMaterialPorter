using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using Avalonia.Controls;
using Avalonia.Threading;
using FortnitePorting.Application;
using Newtonsoft.Json;

namespace FortnitePorting.MaterialPorter;

/// <summary>The app as the Fortnite editor's engine: <c>--background</c> keeps the window hidden (no taskbar entry),
/// <c>--parent-pid</c> / engine-attach name the Blenders using it; it quits once none of them lives, unless shown.</summary>
// engine.json (exe, port, live status) is how a Blender finds and launches the engine, and how it reads the loading
// state before the bridge listens (the bridge starts once the game is loaded).
public static partial class Engine
{
    public static bool Background { get; private set; }
    public static bool Shown { get; private set; }

    private static readonly HashSet<int> Parents = [];
    private static Window? _window;
    private static string _written = "";

    private static string InfoPath => Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), Fork.AppFolder, "engine.json");

    public static void Start(Window window, string[] args)
    {
        _window = window;
        Background = args.Contains("--background");
        var parent = Array.IndexOf(args, "--parent-pid");
        if (parent >= 0 && parent + 1 < args.Length && int.TryParse(args[parent + 1], out var pid)) Parents.Add(pid);
        if (Background)
        {
            // Cloaked, not parked off-screen: at -32000 Avalonia took the nearest monitor's DPI and Windows pulled the
            // hidden resize back to the work area's corner, so a later Show had a mis-sized window with clipped controls.
            window.ShowInTaskbar = false;
            window.ShowActivated = false;
            Cloak(window, true);
            window.Opened += (_, _) => { if (!Shown) window.Hide(); };
        }
        var timer = new DispatcherTimer { Interval = TimeSpan.FromSeconds(1) };
        timer.Tick += (_, _) => Tick();
        timer.Start();
        Tick();
    }

    private static void Tick()
    {
        WriteInfo();
        if (!Background || Shown) return;
        lock (Parents)
            if (Parents.Count == 0 || Parents.Any(Alive)) return;
        AppServices.App.Lifetime.Shutdown();
    }

    private static bool Alive(int pid)
    {
        try { using var p = Process.GetProcessById(pid); return !p.HasExited; }
        catch (ArgumentException) { return false; }
    }

    private static void WriteInfo()
    {
        var text = JsonConvert.SerializeObject(new { exe = Environment.ProcessPath, port = Fork.BridgePort, pid = Environment.ProcessId, status = Status() });
        if (text == _written) return;
        try
        {
            Directory.CreateDirectory(Path.GetDirectoryName(InfoPath)!);
            File.WriteAllText(InfoPath, text);
            _written = text;
        }
        catch (IOException) { }     // a reader holds it: next tick
    }

    public static object Status()
    {
        var parse = AppServices.UEParse;
        return new
        {
            version = Globals.VersionString,
            setup = AppServices.AppSettings.Installation.FinishedSetup,
            loaded = parse.FinishedLoading,
            status = parse.Status,
            window = Shown || !Background,
            background = Background,
        };
    }

    public static object Attach(int pid)
    {
        lock (Parents)
        {
            Parents.Add(pid);
            return new { attached = Parents.ToArray() };
        }
    }

    /// <summary>The editor's Stop: this Blender lets go; the app quits unless shown or another Blender uses it.</summary>
    public static object Stop(int pid)
    {
        bool quit, others;
        lock (Parents)
        {
            Parents.Remove(pid);
            others = Parents.Any(Alive);
            quit = Background && !Shown && !others;
        }
        if (quit) Dispatcher.UIThread.Post(() => AppServices.App.Lifetime.Shutdown());
        return new { quitting = quit, shown = Shown || !Background, others };
    }

    public static void Show() => Dispatcher.UIThread.Post(() =>
    {
        if (_window is null) return;
        Shown = true;
        _window.ShowInTaskbar = true;
        _window.ShowActivated = true;
        Cloak(_window, false);
        _window.Show();
        if (_window.WindowState == WindowState.Minimized) _window.WindowState = WindowState.Normal;
        _window.Activate();
    });

    private static void Cloak(Window window, bool cloak)
    {
        if (!OperatingSystem.IsWindows() || window.TryGetPlatformHandle()?.Handle is not { } hwnd) return;
        var value = cloak ? 1 : 0;
        DwmSetWindowAttribute(hwnd, DWMWA_CLOAK, ref value, sizeof(int));
    }

    private const int DWMWA_CLOAK = 13;

    [LibraryImport("dwmapi.dll")]
    private static partial int DwmSetWindowAttribute(IntPtr hwnd, int attribute, ref int value, int size);
}
