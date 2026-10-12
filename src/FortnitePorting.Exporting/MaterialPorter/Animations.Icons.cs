using System.Collections.Generic;
using CUE4Parse.UE4.Assets.Exports;

namespace FortnitePorting.Exporting.MaterialPorter;

/// <summary>
/// The tile icon of an animation without an item: its use's, else its kind's, else a clapperboard, from the game's own Creative icon
/// library (one white glyph set; most animations have no item, so the tab was a wall of placeholders).
/// </summary>
public static partial class Animations
{
    private const string Library = "FortniteGame/Content/Athena/UI/InGame/Creative/PropertyEditor/Icon_Picker/";

    private static readonly Dictionary<string, string> UseIcons = new()
    {
        ["Poses"] = "Textures_V2/128x128/T_UI_IconLibrary_Camera_128",
        ["Locomotion"] = "Textures/128x128/T_UI_IconLibrary_running_128",
        ["Swimming"] = "Textures_V2/128x128/T_UI_IconLibrary_Swimming_128",
        ["Skydiving"] = "Textures_V2/128x128/T_UI_IconLibrary_Parachute_128",
        ["Combat"] = "MilitaryTextures/T_128x128/T_UI_IconLibrary_assaultrifle_128",
        ["Traversal"] = "Textures_V2/128x128/T_UI_IconLibrary_Mountains_128",
        ["Riding"] = "Textures/128x128/T_UI_IconLibrary_boar_128",
        ["Vehicles"] = "Textures/128x128/T_UI_IconLibrary_car_128",
        ["Items"] = "Textures/128x128/T_UI_IconLibrary_package_128",
        ["Festival"] = "Textures_V2/128x128/T_UI_IconLibrary_MusicNote_128",
        ["Lobby"] = "Textures/128x128/T_UI_IconLibrary_house_128",
        ["Facial"] = "Textures/128x128/T_UI_IconLibrary_happy_128",
    };

    private static readonly Dictionary<string, string> KindIcons = new()
    {
        ["Emotes"] = "Textures_V2/128x128/T_UI_IconLibrary_Drama_128",
        ["Gliders"] = "Textures_V2/128x128/T_UI_IconLibrary_Parachute_128",
        ["Back Blings"] = "Textures/128x128/T_UI_IconLibrary_package_128",
        ["Pickaxes"] = "Textures_V2/128x128/T_UI_IconLibrary_Axe_128",
        ["Creatures"] = "Textures/128x128/T_UI_IconLibrary_wolf_128",
        ["Pets"] = "Textures_V2/128x128/T_UI_IconLibrary_Pawprint_128",
        ["Vehicles"] = "Textures/128x128/T_UI_IconLibrary_car_128",
        ["Weapons"] = "MilitaryTextures/T_128x128/T_UI_IconLibrary_assaultrifle_128",
        ["LEGO"] = "Textures_V2/128x128/T_UI_IconLibrary_Cube_128",
    };

    private const string AnyIcon = "Textures_V2/128x128/T_UI_IconLibrary_Clapperboard_128";
    private const int UseGroup = 2; // Groups: gender, body type, use

    /// <summary>An animation's tile icon: its item's, else its use's or kind's.</summary>
    public static string IconPath(UObject animation)
    {
        if (Unloaded.Detail(animation) is not Outline outline) return Library + AnyIcon;
        if (outline.Owner?.Icon is { } own && !own.Contains("Placeholder", System.StringComparison.OrdinalIgnoreCase)) return own;
        return Library + (outline.Groups[UseGroup] is { } use && UseIcons.TryGetValue(use, out var byUse) ? byUse
            : KindIcons.TryGetValue(outline.Kind, out var byKind) ? byKind : AnyIcon);
    }
}
