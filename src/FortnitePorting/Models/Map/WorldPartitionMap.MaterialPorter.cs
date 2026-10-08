using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using Avalonia;
using Avalonia.Media.Imaging;
using Avalonia.Platform;
using Avalonia.Threading;
using CommunityToolkit.Mvvm.ComponentModel;
using CUE4Parse.UE4.Objects.Core.Math;
using CUE4Parse.Utils;
using FortnitePorting.MaterialPorter;
using Serilog;

namespace FortnitePorting.Models.Map;

/// <summary>A map without a minimap of its own (a UEFN island, or a mode whose listed minimap is another map's) shows a picture drawn from its levels.</summary>
// The picture (MapPreview) has the map's cells laid over it where they are. Kept on disk; drawn the first time the map is opened.
public partial class WorldPartitionMap
{
    [ObservableProperty, NotifyPropertyChangedFor(nameof(NoPreview))] private string _previewStatus = string.Empty;
    [ObservableProperty, NotifyPropertyChangedFor(nameof(NoPreview))] private bool _isDrawingPreview;

    public bool NoPreview => MapBitmap is null && !IsDrawingPreview && !_drawnPreview;

    private bool _drawnPreview;
    private int _previewLevels;
    private CancellationTokenSource? _previewCancel;
    private static readonly SemaphoreSlim Drawing = new(1, 1);     // one map drawn at a time; the others wait

    partial void OnMapBitmapChanged(Bitmap? value) => OnPropertyChanged(nameof(NoPreview));

    private string PreviewFolder => Path.Combine(MapsFolder.FullName, MapInfo.MapPath.SubstringAfterLast("/"));

    // Whether this map's picture is drawn (none, or another map's): before its cells are placed.
    private async Task<bool> NeedsDrawnPreviewAsync()
    {
        if (MapBitmap is not null && !await MapPreview.IsAnotherMapsAsync(UEParse.Provider, MapInfo.MinimapPath, MapInfo.MapPath))
            return false;
        if (MapBitmap is not null)
            Log.Information("[Material Porter] {Map}: its minimap {Minimap} is another map's: drawing one", MapInfo.Name, MapInfo.MinimapPath);
        MapBitmap = null;
        _drawnPreview = true;
        return true;
    }

    // A cell's corner, on the cell size's grid (FP's snapping sends a negative one to the next).
    private FVector SnapCell(FVector center)
    {
        double size = MapInfo.MinGridDistance;
        return new FVector((float) (Math.Floor(center.X / size) * size), (float) (Math.Floor(center.Y / size) * size), 0);
    }

    // The levels to read: the map, its streamed cells (an island's beside it).
    private List<string> PreviewLevels()
    {
        var levels = new List<string> { MapInfo.MapPath };
        var cells = Grids.SelectMany(g => g.Maps).Select(m => m.Path).Distinct(StringComparer.OrdinalIgnoreCase).ToList();
        if (cells.Count == 0)
        {
            var prefix = UEParse.Provider.FixPath(MapInfo.MapPath + ".umap");
            prefix = prefix[..prefix.LastIndexOf('.')] + "/_Generated_/";
            cells = UEParse.Provider.Files.Keys.Where(k => k.StartsWith(prefix, StringComparison.OrdinalIgnoreCase)
                                                           && k.EndsWith(".umap", StringComparison.OrdinalIgnoreCase)).ToList();
        }
        levels.AddRange(cells.Where(c => !c.Contains("HLOD", StringComparison.OrdinalIgnoreCase)).OrderBy(c => c, StringComparer.OrdinalIgnoreCase));
        return levels;
    }

    // After Load: the kept picture, if it's this build's.
    private void ShowKeptPreview()
    {
        _previewLevels = PreviewLevels().Count;
        if (MapPreview.Cached(PreviewFolder, MapPreview.Key(UEParse.Provider, MapInfo.MapPath, _previewLevels)) is { } kept)
            Show(kept);
    }

    /// <summary>Called when the map is opened: draws its picture if it has none yet (one map at a time, in opening order).</summary>
    public void EnsurePreview()
    {
        if (!_drawnPreview || MapBitmap is not null || IsDrawingPreview) return;
        var cancel = _previewCancel = new CancellationTokenSource();
        IsDrawingPreview = true;
        PreviewStatus = "Waiting for another map's drawing...";
        Log.Information("[Material Porter] {Map}: drawing its map", MapInfo.Name);
        var levels = PreviewLevels();
        var key = MapPreview.Key(UEParse.Provider, MapInfo.MapPath, levels.Count);
        _ = Task.Run(async () =>
        {
            var entered = false;
            try
            {
                await Drawing.WaitAsync(cancel.Token);
                entered = true;
                Dispatcher.UIThread.Post(() => PreviewStatus = "Drawing a map...");
                var picture = await MapPreview.DrawAsync(UEParse.Provider, MapInfo.MapPath, levels, PreviewFolder, key,
                    (shown, status) => Dispatcher.UIThread.Post(() =>
                    {
                        if (cancel.IsCancellationRequested) return;
                        if (shown is not null) Show(shown);
                        PreviewStatus = status;
                    }), cancel.Token);
                Dispatcher.UIThread.Post(() =>
                {
                    if (picture is not null) Show(picture);
                    PreviewStatus = picture is null ? "Nothing to draw a map from" : string.Empty;
                });
            }
            catch (OperationCanceledException)
            {
                Dispatcher.UIThread.Post(() => PreviewStatus = string.Empty);
            }
            catch (Exception e)
            {
                Log.Error(e, "[Material Porter] {Map}: map preview failed", MapInfo.Name);
                Dispatcher.UIThread.Post(() => PreviewStatus = "The map couldn't be drawn");
            }
            finally
            {
                if (entered) Drawing.Release();
                Dispatcher.UIThread.Post(() => IsDrawingPreview = false);
            }
        });
    }

    private void CancelPreview()
    {
        if (IsDrawingPreview) _previewCancel?.Cancel();
    }

    // Shows a drawn picture with its cells placed over it: no rotation, with the scale and offsets that put a cell's square
    // on the ground it covers (FP centres each square at half its margin, so everything is at half scale).
    private void Show(MapPreview.Picture picture)
    {
        var bitmap = new WriteableBitmap(new PixelSize(MapPreview.Size, MapPreview.Size), new Vector(96, 96), PixelFormat.Rgba8888, AlphaFormat.Unpremul);
        using (var buffer = bitmap.Lock())
            System.Runtime.InteropServices.Marshal.Copy(picture.Rgba, 0, buffer.Address, picture.Rgba.Length);
        MaskBitmap ??= White();
        var area = picture.Area;
        var s = 2 * area.K;
        var g = (double) MapInfo.MinGridDistance;
        MapInfo.RotateGrid = false;
        MapInfo.Scale = (float) s;
        MapInfo.XOffset = (int) Math.Round(s * (g / 2 - area.CenterX));
        MapInfo.YOffset = (int) Math.Round(s * (g / 2 - area.CenterY));
        foreach (var grid in Grids) grid.Place(grid.OriginalPosition, (int) Math.Round(g * area.K));
        var old = MapBitmap;
        MapBitmap = bitmap;
        if (!ReferenceEquals(old, bitmap)) old?.Dispose();
    }

    private static Bitmap White()
    {
        var white = new WriteableBitmap(new PixelSize(1, 1), new Vector(96, 96), PixelFormat.Rgba8888, AlphaFormat.Unpremul);
        using var buffer = white.Lock();
        System.Runtime.InteropServices.Marshal.Copy(new byte[] { 255, 255, 255, 255 }, 0, buffer.Address, 4);
        return white;
    }
}

public partial class WorldPartitionGrid
{
    /// <summary>Places the cells again over a drawn map (its scale and offsets just changed).</summary>
    public void Place(FVector position, int cellSize)
    {
        Position = position;
        CellSize = cellSize;
        OnPropertyChanged(nameof(OffsetMargin));
    }
}
