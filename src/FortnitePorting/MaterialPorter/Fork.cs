using System;

namespace FortnitePorting.MaterialPorter;

/// <summary>The fork's own names (settings, folders, pipe, mutex, ports) so it runs beside an installed FortnitePorting.</summary>
// FORTNITEPORTING_MP_PROFILE (e.g. "test") runs a separate instance with its own folders, lock and bridge port.
public static class Fork
{
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

    /// <summary>UEFN island export (downloaded islands, unlock by map code): owner builds only, set by the git-ignored Fork.local.props (MPIslands).</summary>
#if MP_ISLANDS
    public const bool Islands = true;
#else
    public const bool Islands = false;
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
