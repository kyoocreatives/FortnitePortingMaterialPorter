using System;
using System.Threading.Tasks;
using Avalonia.Threading;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using FortnitePorting.Application;
using FortnitePorting.MaterialPorter;

namespace FortnitePorting.ViewModels;

/// <summary>A UEFN island opened by its map code (owner builds only, Fork.Islands).</summary>
// The user's key tool gets the island's key from Epic (a sign-in page opens when it asks); the key is kept with
// Material Porter's (never shown or logged), the island Fortnite downloaded is mounted, and the map list reloads.
public partial class MapViewModel
{
    public bool CanUnlockIslands => Fork.Islands;

    [ObservableProperty] private string _islandCode = string.Empty;
    [ObservableProperty] private string _islandStatus = string.Empty;
    [ObservableProperty] private bool _isUnlockingIsland;

    [RelayCommand]
    public async Task UnlockIsland()
    {
        var code = IslandCode.Trim();
        if (!Fork.Islands || IsUnlockingIsland || code.Length == 0) return;
        IsUnlockingIsland = true;
        IslandStatus = "Asking for the island's key...";
        try
        {
            var opened = await Task.Run(() => MaterialPorterService.Instance.UnlockIslandAsync(code,
                status => Dispatcher.UIThread.Post(() => IslandStatus = status),
                url => AppServices.App.Launch(url)));
            if (opened)
            {
                IslandStatus = $"{code} unlocked";
                await LoadMapsAsync();
            }
            else IslandStatus = $"Key for {code} saved: play the island once in Fortnite, then restart";
        }
        catch (Exception e)
        {
            IslandStatus = e.Message;
        }
        finally
        {
            IsUnlockingIsland = false;
        }
    }
}
