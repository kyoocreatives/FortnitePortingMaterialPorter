using System;
using System.Collections.ObjectModel;
using CommunityToolkit.Mvvm.ComponentModel;

namespace FortnitePorting.Models.Assets;

/// <summary>A channel with many options (a car's wheel sets) picked from a searchable tile grid in its pop-up instead of a list.</summary>
public partial class AssetStyleInfo
{
    [ObservableProperty] private bool _isPicker;
    [ObservableProperty] private string _pickerSearch = string.Empty;

    /// <summary>The options the search keeps (all of them without a search).</summary>
    public ObservableCollection<BaseStyleData> PickerItems { get; } = [];

    /// <summary>The picked option as an item (the grid's items are filtered, so not by index).</summary>
    public BaseStyleData? PickerSelection
    {
        get => SelectedStyleIndex >= 0 && SelectedStyleIndex < StyleDatas.Count ? StyleDatas[SelectedStyleIndex] : null;
        set
        {
            if (value is null) return;
            var index = StyleDatas.IndexOf(value);
            if (index >= 0) SelectedStyleIndex = index;
        }
    }

    partial void OnIsPickerChanged(bool value) => RefreshPicker();
    partial void OnPickerSearchChanged(string value) => RefreshPicker();
    partial void OnSelectedStyleIndexChanged(int value) => OnPropertyChanged(nameof(PickerSelection));

    // a list clearing its selection (its items or pop-up going away) must not unpick a channel that needs a pick:
    // the export would read StyleDatas[-1]
    partial void OnSelectedStyleIndexChanged(int oldValue, int newValue)
    {
        if (RequiredSelection && !MultiSelect && (newValue < 0 || newValue >= StyleDatas.Count)
            && oldValue >= 0 && oldValue < StyleDatas.Count)
            SelectedStyleIndex = oldValue;
    }

    private void RefreshPicker()
    {
        PickerItems.Clear();
        if (!IsPicker) return;
        var search = PickerSearch.Trim();
        foreach (var style in StyleDatas)
            if (search.Length == 0 || style.StyleName.Contains(search, StringComparison.OrdinalIgnoreCase))
                PickerItems.Add(style);
        OnPropertyChanged(nameof(PickerSelection));
    }
}
