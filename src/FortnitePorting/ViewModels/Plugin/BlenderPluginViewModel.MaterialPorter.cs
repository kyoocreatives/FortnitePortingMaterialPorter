using System.Diagnostics;
using System.Diagnostics.CodeAnalysis;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using CommunityToolkit.Mvvm.ComponentModel;
using FluentAvalonia.UI.Controls;
using FortnitePorting.Models.Information;
using FortnitePorting.Models.Plugin;
using FortnitePorting.Services;
using Serilog;

namespace FortnitePorting.ViewModels.Plugin;

// Material Porter fork.
public partial class BlenderPluginViewModel
{
    // a process whose modules can't be read (Win32 error 299: exiting, or another session's): skipped
    // instead of failing the whole plugin sync
    private static string? ExecutablePath(Process process)
    {
        try
        {
            return process.MainModule?.FileName;
        }
        catch (Exception)
        {
            return null;
        }
    }
}
