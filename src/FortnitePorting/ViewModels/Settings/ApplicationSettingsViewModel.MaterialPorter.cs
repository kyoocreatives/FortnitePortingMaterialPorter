using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.ComponentModel;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using Avalonia.Controls;
using Avalonia.Markup.Xaml;
using Avalonia.Media;
using Avalonia.Styling;
using CommunityToolkit.Mvvm.ComponentModel;
using FluentAvalonia.Styling;
using FluentAvalonia.UI.Media.Animation;
using FortnitePorting.Controls;
using FortnitePorting.Extensions;
using FortnitePorting.Framework;
using FortnitePorting.Models;
using FortnitePorting.Models.API.Responses;
using FortnitePorting.Models.Assets.Base;
using FortnitePorting.Models.Map;
using FortnitePorting.Models.Radio;
using FortnitePorting.Shared.Extensions;
using FortnitePorting.Validators;
using Material.Icons;
using NAudio.Wave;
using Newtonsoft.Json;

namespace FortnitePorting.ViewModels.Settings;

// UEFN project folders.
public partial class ApplicationSettingsViewModel
{
    // folders holding the user's UEFN projects (.uefnproject): their islands import with exact materials from the
    // projects' own graphs (owner builds only)
    [ObservableProperty] private ObservableCollection<string> _uefnProjectFolders = [];

    [JsonIgnore] public bool ShowIslandSettings => MaterialPorter.Fork.Islands;

    // the projects found under those folders and where UEFN keeps them
    [JsonIgnore] public string FoundUefnProjects
    {
        get
        {
            var names = MaterialPorter.IslandProjects.All.Select(p => p.Name).OrderBy(n => n, StringComparer.OrdinalIgnoreCase).ToArray();
            return names.Length == 0 ? "No UEFN project found." : $"{names.Length} found: {string.Join(", ", names)}";
        }
    }

    public async Task AddUefnProjectFolder()
    {
        if (await App.BrowseFolderDialog() is not { } path) return;
        if (!UefnProjectFolders.Any(folder => string.Equals(folder, path, StringComparison.OrdinalIgnoreCase))) UefnProjectFolders.Add(path);
        MaterialPorter.MaterialPorterService.ApplyProjectFolders();
        OnPropertyChanged(nameof(FoundUefnProjects));
    }

    public void RemoveUefnProjectFolder(string? path)
    {
        if (path is null || !UefnProjectFolders.Remove(path)) return;
        MaterialPorter.MaterialPorterService.ApplyProjectFolders();
        OnPropertyChanged(nameof(FoundUefnProjects));
    }
}
