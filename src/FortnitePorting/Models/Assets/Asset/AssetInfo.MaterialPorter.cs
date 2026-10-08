using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading.Tasks;
using Avalonia.Threading;
using CUE4Parse.UE4.Assets.Exports.Texture;
using CUE4Parse.UE4.Assets.Objects;
using CUE4Parse.UE4.Objects.Core.i18N;
using CUE4Parse.UE4.Objects.UObject;
using CUE4Parse_Conversion.Textures;
using FortnitePorting.Application;
using FortnitePorting.Exporting.MaterialPorter;
using FortnitePorting.Extensions;
using FortnitePorting.MaterialPorter;
using Serilog;
using SkiaSharp;

namespace FortnitePorting.Models.Assets.Asset;

/// <summary>Style channels for cars and LEGO figure expressions, which the game doesn't list as item variants.</summary>
// A car's channels (Tier, Body Color, Painted, Decal, Decal Color, Wheels) come from Material Porter's car assembly
// (Cars.PlanAsync), built off the UI thread; option previews (tier and decal images, colour swatches, wheel icons)
// fill in after. A LEGO figure's expression is the face rig's pose of its mouth, eyes and brows.
public partial class AssetInfo
{
    private void AddCarStyles()
    {
        var path = Asset.CreationData.Object.GetPathName();
        _ = Task.Run(async () =>
        {
            CarPlan plan;
            try { plan = await MaterialPorterService.Instance.CarPlanAsync(path, null); }
            catch (Exception e)
            {
                Log.Warning(e, "[Material Porter] car styles for {Path}", path);
                return;
            }

            var infos = new List<AssetStyleInfo>();
            var previews = new List<(CarStyleData Data, CarOption Option)>();
            for (var c = 0; c < plan.Channels.Count; c++)
            {
                var channel = plan.Channels[c];
                var datas = new List<CarStyleData>();
                for (var o = 0; o < channel.Options.Count; o++)
                {
                    var data = new CarStyleData(channel.Options[o].Name, c, o);
                    datas.Add(data);
                    previews.Add((data, channel.Options[o]));
                }
                if (datas.Count == 0) continue;
                infos.Add(new AssetStyleInfo(channel.Name, datas)
                {
                    SelectedStyleIndex = Math.Clamp(channel.Default, 0, datas.Count - 1),
                    // a long list (the wheel sets): a searchable tile grid
                    IsPicker = datas.Count > 30,
                });
            }
            await Dispatcher.UIThread.InvokeAsync(() =>
            {
                foreach (var info in infos) StyleInfos.Add(info);
            });

            foreach (var (data, option) in previews)
            {
                try
                {
                    if (Preview(option) is { } bitmap)
                        await Dispatcher.UIThread.InvokeAsync(() => data.StyleDisplayImage = bitmap);
                }
                catch (Exception e) { Exporting.MaterialPorter.Failures.Note("car style previews", data.StyleName, e); }
            }
        });
    }

    // every wrap (name, item path, icon path), read once; icons kept as small tiles
    private static readonly System.Threading.SemaphoreSlim WrapLock = new(1, 1);
    private static List<(string Name, string Path, string? Icon)>? _wraps;
    private static readonly System.Collections.Concurrent.ConcurrentDictionary<string, Avalonia.Media.Imaging.WriteableBitmap?> WrapIcons = new();

    private static async Task<List<(string Name, string Path, string? Icon)>> WrapsAsync()
    {
        await WrapLock.WaitAsync();
        try
        {
            if (_wraps is not null) return _wraps;
            var provider = AppServices.UEParse.Provider!;
            var wraps = new List<(string Name, string Path, string? Icon)>();
            foreach (var data in AppServices.UEParse.AssetRegistry.Where(a => a.AssetClass.Text == "AthenaItemWrapDefinition").ToList())
            {
                try
                {
                    if (!provider.TryLoadPackageObject(data.ObjectPath, out var wrap)
                        || wrap.GetOrDefault<FSoftObjectPath>("ItemWrapMaterial").AssetPathName.IsNone) continue;
                    // an unreleased wrap is named "TBD": use its asset's name
                    var name = wrap.GetAnyOrDefault<FText?>("DisplayName", "ItemName")?.Text;
                    if (string.IsNullOrWhiteSpace(name) || name == "TBD") name = wrap.Name;
                    wraps.Add((name, wrap.GetPathName(), Loading.AssetLoader.GetLowResIcon(wrap)?.GetPathName()));
                }
                catch (Exception e) { Exporting.MaterialPorter.Failures.Note("wraps", data.AssetName.Text, e); }
            }
            return _wraps = wraps.OrderBy(w => w.Name, StringComparer.OrdinalIgnoreCase).ToList();
        }
        finally
        {
            WrapLock.Release();
        }
    }

    // An icon as a small tile (a list of 1,200 wraps keeps them all).
    private static Avalonia.Media.Imaging.WriteableBitmap? Tile(string? iconPath)
    {
        if (iconPath is null) return null;
        return WrapIcons.GetOrAdd(iconPath, path =>
        {
            try
            {
                if (!AppServices.UEParse.Provider!.TryLoadPackageObject<UTexture2D>(path, out var texture)) return null;
                using var full = texture.Decode()?.ToSkBitmap();
                if (full is null) return null;
                using var small = full.Resize(new SKImageInfo(96, 96), SKFilterQuality.Medium);
                return small?.ToWriteableBitmap();
            }
            catch (Exception e) { Exporting.MaterialPorter.Failures.Note("wrap tiles", iconPath ?? "", e); return null; }
        });
    }

    // An item's Effects switch: off as FP exports it, on with its own effects (Effects.OwnEffectNames), which the plugin
    // plays on the item's sockets. The note says which styles bring them and which are GPU-driven.
    private void AddEffectStyles()
    {
        var item = Asset.CreationData.Object;
        var type = Asset.CreationData.ExportType;
        if (item is null || type is not (EExportType.Pickaxe or EExportType.Backpack or EExportType.Outfit or EExportType.Glider or EExportType.Item or EExportType.Sprite)) return;
        _ = Task.Run(async () =>
        {
            try
            {
                var names = Effects.OwnEffectNames(item, type);
                if (names.Count == 0) return;
                var label = string.Join(", ", names);
                var options = new List<EffectsStyleData> { new("None", false), new(char.ToUpper(label[0]) + label[1..], true) };
                var info = new AssetStyleInfo("Effects", options)
                {
                    SelectedStyleIndex = 0,
                    IsSwitch = true,
                    SwitchNote = string.Join(" ", new[] { Effects.StyleOnlyNote(item, type), Effects.GpuNote(Effects.OwnSystems(item, type)) }.OfType<string>())
                };
                await Dispatcher.UIThread.InvokeAsync(() => StyleInfos.Add(info));
            }
            catch (Exception e)
            {
                Log.Warning(e, "[Material Porter] effect styles for {Name}", item.Name);
            }
        });
    }

    // A weapon's mod slots (Optic, Magazine, Barrel, Underbarrel: its own mod, none, or a mod that allows it) and the wrap
    // over a weapon or vehicle (as it is, none, or any wrap). The export puts the mods on the weapon's attach bones and
    // lays the wrap over all of it.
    private void AddWeaponStyles()
    {
        AddEffectStyles();
        var item = Asset.CreationData.Object;
        if (item is null) return;
        var weapon = Asset.CreationData.ExportType is EExportType.Item && item.ExportType.Contains("Weapon", StringComparison.OrdinalIgnoreCase);
        if (!weapon && Asset.CreationData.ExportType is not EExportType.Vehicle) return;

        _ = Task.Run(async () =>
        {
            try
            {
                var provider = AppServices.UEParse.Provider!;
                if (weapon)
                {
                    var infos = new List<AssetStyleInfo>();
                    var icons = new List<(WeaponModStyleData Data, WeaponMod Mod)>();
                    foreach (var slot in WeaponMods.Plan(provider, item))
                    {
                        var labels = WeaponMods.Labels(slot.Options);
                        var datas = new List<WeaponModStyleData> { new(slot.Default is { } own ? $"Default ({own.Name})" : "Default (none)", slot.Slot, null) };
                        if (slot.Default is not null) datas.Add(new WeaponModStyleData("None", slot.Slot, ""));
                        foreach (var mod in slot.Options)
                        {
                            if (ReferenceEquals(mod, slot.Default)) continue;
                            var data = new WeaponModStyleData(labels[mod], slot.Slot, mod.Path);
                            datas.Add(data);
                            icons.Add((data, mod));
                        }
                        infos.Add(new AssetStyleInfo(slot.Slot, datas) { SelectedStyleIndex = 0, IsPicker = datas.Count > 30 });
                    }
                    await Dispatcher.UIThread.InvokeAsync(() =>
                    {
                        foreach (var info in infos) StyleInfos.Add(info);
                    });
                    foreach (var (data, mod) in icons)
                    {
                        if (Tile(Loading.AssetLoader.GetIcon(mod.Item)?.GetPathName()) is { } bitmap)
                            await Dispatcher.UIThread.InvokeAsync(() => data.StyleDisplayImage = bitmap);
                    }
                }

                // the wrap: on what takes one (a material with the customization mask)
                if (!Wraps.Supports(provider, item, Asset.CreationData.ExportType)) return;
                var wraps = await WrapsAsync();
                var wrapDatas = new List<WrapStyleData> { new("Default", null) };
                // an exotic wears a wrap of its own: "None" takes it off
                if (!item.GetOrDefault<FSoftObjectPath>("IntrinsicOverrideWrap").AssetPathName.IsNone)
                    wrapDatas.Add(new WrapStyleData("None", ""));
                var tiles = new List<(WrapStyleData Data, string? Icon)>();
                foreach (var (name, path, icon) in wraps)
                {
                    var data = new WrapStyleData(name, path);
                    wrapDatas.Add(data);
                    tiles.Add((data, icon));
                }
                var wrapInfo = new AssetStyleInfo("Wrap", wrapDatas) { SelectedStyleIndex = 0, IsPicker = true };
                await Dispatcher.UIThread.InvokeAsync(() => StyleInfos.Add(wrapInfo));
                foreach (var (data, icon) in tiles)
                {
                    if (Tile(icon) is { } bitmap)
                        await Dispatcher.UIThread.InvokeAsync(() => data.StyleDisplayImage = bitmap);
                }
            }
            catch (Exception e)
            {
                Log.Warning(e, "[Material Porter] weapon styles for {Name}", item.Name);
            }
        });
    }

    const string FaceSettings = "/FigureCharacter/Figure_Core/Rig/DA_Figure_Face_Settings.DA_Figure_Face_Settings";
    const string MouthAtlas = "/FigureCharacter/Figure_Core/Texture/Face/Mouth/T_Atlas_Figure_Mouth_Thin.T_Atlas_Figure_Mouth_Thin";
    const string BrowAtlas = "/FigureCharacter/Figure_Core/Texture/Face/Brow/T_Atlas_Figure_Brow_Thin01.T_Atlas_Figure_Brow_Thin01";

    // A LEGO figure's expression channels: Mouth, Eyes, Brows, each the figure's own or one of the face rig's poses
    // (DA_Figure_Face_Settings has a row per pose: 46 mouths, 12 brows, 6 eyes). Mouth and brow previews are their cells
    // of the default atlases (7 x 7 mouths, 4 x 4 brows), drawn as the face prints them.
    private void AddFigureFaceStyles()
    {
        _ = Task.Run(async () =>
        {
            var provider = AppServices.UEParse.Provider!;
            int mouths = 46, brows = 12, eyes = 6;
            if (provider.TryLoadPackageObject(FaceSettings, out var settings))
            {
                if (settings.GetOrDefault("Mouth Pose Matrix", Array.Empty<FStructFallback>()).Length is > 0 and var m) mouths = m;
                if (settings.GetOrDefault("Brow Pose Matrix", Array.Empty<FStructFallback>()).Length is > 0 and var b) brows = b;
                if (settings.GetOrDefault("Show Eyelash", Array.Empty<bool>()).Length is > 0 and var e) eyes = e;
            }
            var channels = new (string Feature, int Count, string? Atlas, int Grid)[] { ("Mouth", mouths, MouthAtlas, 7), ("Eyes", eyes, null, 0), ("Brows", brows, BrowAtlas, 4) };
            var infos = new List<(AssetStyleInfo Info, List<FigureFaceStyleData> Datas, string? Atlas, int Grid)>();
            foreach (var (feature, count, atlas, grid) in channels)
            {
                var datas = new List<FigureFaceStyleData> { new("Figure's own", feature, -1) };
                for (var pose = 0; pose < count; pose++) datas.Add(new FigureFaceStyleData($"{feature} {pose}", feature, pose));
                infos.Add((new AssetStyleInfo(feature, datas) { IsPicker = datas.Count > 30 }, datas, atlas, grid));
            }
            await Dispatcher.UIThread.InvokeAsync(() =>
            {
                foreach (var (info, _, _, _) in infos) StyleInfos.Add(info);
            });

            foreach (var (_, datas, atlas, grid) in infos)
            {
                if (atlas is null) continue;
                try
                {
                    if (!provider.TryLoadPackageObject<UTexture2D>(atlas, out var texture) || texture.Decode()?.ToSkBitmap() is not { } sheet) continue;
                    using (sheet)
                        foreach (var data in datas.Where(d => d.Pose >= 0))
                        {
                            var bitmap = FaceCell(sheet, grid, data.Pose, data.Feature == "Brows");
                            await Dispatcher.UIThread.InvokeAsync(() => data.StyleDisplayImage = bitmap);
                        }
                }
                catch (Exception e)
                {
                    Log.Warning(e, "[Material Porter] figure expression previews");
                }
            }
        });
    }

    // A face atlas cell drawn on LEGO yellow: a mouth's outline (B only) black, lips (R only) red, inside (G and B) dark,
    // teeth (all three) white; a brow (any channel) black.
    private static Avalonia.Media.Imaging.WriteableBitmap FaceCell(SKBitmap sheet, int grid, int index, bool brow)
    {
        const int size = 96;
        var cell = sheet.Width / grid;
        var x0 = index % grid * cell;
        var y0 = index / grid * cell;
        using var icon = new SKBitmap(size, size, SKColorType.Rgba8888, SKAlphaType.Unpremul);
        var colour = new float[3];
        void Over(float amount, float r, float g, float b)
        {
            colour[0] += (r - colour[0]) * amount;
            colour[1] += (g - colour[1]) * amount;
            colour[2] += (b - colour[2]) * amount;
        }
        for (var y = 0; y < size; y++)
        for (var x = 0; x < size; x++)
        {
            var c = sheet.GetPixel(x0 + x * cell / size, y0 + y * cell / size);
            float r = c.Red / 255f, g = c.Green / 255f, b = c.Blue / 255f;
            colour[0] = 0.96f; colour[1] = 0.80f; colour[2] = 0.22f;
            if (brow) Over(Math.Max(r, Math.Max(g, b)), 0.05f, 0.04f, 0.03f);
            else
            {
                Over(b, 0.05f, 0.04f, 0.03f);
                Over(r * (1 - g), 0.72f, 0.13f, 0.12f);
                Over(g * (1 - r), 0.22f, 0.03f, 0.03f);
                Over(Math.Min(r, g), 0.97f, 0.97f, 0.95f);
            }
            icon.SetPixel(x, y, new SKColor((byte)(colour[0] * 255), (byte)(colour[1] * 255), (byte)(colour[2] * 255)));
        }
        return icon.ToWriteableBitmap();
    }

    // An option's preview: its image, a swatch of its colour, or its item's icon.
    private static Avalonia.Media.Imaging.WriteableBitmap? Preview(CarOption option)
    {
        var provider = AppServices.UEParse.Provider!;
        if (option.Swatch is { Length: >= 6 } hex && SKColor.TryParse("#" + hex[..6], out var color))
        {
            using var swatch = new SKBitmap(64, 64, SKColorType.Rgba8888, SKAlphaType.Unpremul);
            swatch.Erase(color);
            return swatch.ToWriteableBitmap();
        }
        UTexture2D? texture = null;
        if (option.Icon is { } icon) provider.TryLoadPackageObject(icon, out texture);
        if (texture is null && option.IconItem is { } item && provider.TryLoadPackageObject(item.ObjectPath, out var itemObject))
            texture = itemObject.GetDataListItem<UTexture2D>("Icon", "LargeIcon")
                      ?? itemObject.GetAnyOrDefault<UTexture2D?>("SmallPreviewImage", "LargePreviewImage", "Icon", "LargeIcon");
        return texture?.Decode()?.ToWriteableBitmap();
    }
}
