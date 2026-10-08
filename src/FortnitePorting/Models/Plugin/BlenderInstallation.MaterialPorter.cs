using System;
using System.Diagnostics;
using System.IO;
using System.Threading.Tasks;
using Avalonia.Media;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using FortnitePorting.Extensions;
using FortnitePorting.Shared.Extensions;
using FortnitePorting.ViewModels;
using Newtonsoft.Json;

namespace FortnitePorting.Models.Plugin;

public partial class BlenderInstallation
{
    // whether Blender's copy of the plugin differs from this build's (a file missing or changed)
    public bool PluginDiffers()
    {
        if (StartupPath is null) return false;
        var source = new DirectoryInfo(Path.Combine(PluginWorkingDirectory.FullName, "fortnite_porting"));
        var target = Path.Combine(StartupPath, MaterialPorter.Fork.PluginFolder);
        if (!source.Exists) return false;
        foreach (var file in source.EnumerateFiles("*", SearchOption.AllDirectories))
        {
            if (file.FullName.Contains("__pycache__")) continue;
            var other = new FileInfo(Path.Combine(target, Path.GetRelativePath(source.FullName, file.FullName)));
            if (!other.Exists || other.Length != file.Length || !File.ReadAllBytes(other.FullName).AsSpan().SequenceEqual(File.ReadAllBytes(file.FullName)))
                return true;
        }
        return false;
    }
}
