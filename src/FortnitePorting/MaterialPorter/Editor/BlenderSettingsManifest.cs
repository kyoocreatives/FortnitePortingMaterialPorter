using System;
using System.Linq;
using System.Reflection;
using Avalonia.Threading;
using FortnitePorting.Application;
using FortnitePorting.Exporting;
using FortnitePorting.Extensions;
using FortnitePorting.ViewModels;

namespace FortnitePorting.MaterialPorter;

/// <summary>The Blender export settings as the editor's Settings tab shows them: sections, labels and ranges copied
/// from Views/Settings/BlenderSettingsView.axaml and the fork's rows in it (Views/MaterialPorter), values by reflection.
/// devtools/tests/editor_routes_check.py fails when a setting the view binds is missing here.</summary>
public static class BlenderSettingsManifest
{
    private record Row(string Section, string Property, string Label, string Description, double? Min = null, double? Max = null);

    private static readonly Row[] Rows =
    [
        new("General", "ScaleDown", "Scale Down", "Scales the model down to centimeters. Recommended for Blender."),
        new("General", "ImportIntoCollection", "Import Into Collection", "Creates a collection in which all imported assets are put into."),
        new("General", "ImportAt3DCursor", "Import At 3D Cursor", "Creates all imported assets at the 3D Cursor."),
        new("General", "CompressionFormat", "Compression", "The type of compression that should be used on 'UE Format' assets."),
        new("Armature", "RigType", "Rig", "The type of rig that should be applied to the imported object. (Outfit Only)"),
        new("Armature", "MergeArmatures", "Merge Armatures", "All imported parts are merged together and properly parented. (Outfit Only)"),
        new("Armature", "ReorientBones", "Reorient Bones", "Reorients bones to point to their average child position."),
        new("Armature", "UseDynamicBoneShape", "Dynamic Bone Shape", "Dynamic bones, usually used for physics, will have a rectangular bone shape."),
        new("Armature", "ImportSockets", "Import Sockets", "Whether or not to import bone sockets."),
        new("Armature", "ImportVirtualBones", "Import Virtual Bones", "Whether or not to import virtual bones."),
        new("Armature", "BoneLength", "Bone Length", "The length of all imported bones.", 1, 5),
        new("Armature", "OfficialRigHandControls", "Extra Hand Controls", "Adds hand controls on top of Epic's finger controls: curl and spread every finger, cup the palm, curl one finger. Not part of Epic's rig. (Official rig)"),
        new("Armature", "FaceBoard", "Face Board", "Puts a face board (MetaHuman controls) on regular characters. Off keeps their face bones as they are. LEGO figures and creatures keep theirs."),
        new("Mesh", "TargetLOD", "Level of Detail", "Level of Detail that should be imported for each mesh. The highest quality starts at zero and reduces as the LOD increases.", 0, 7),
        new("Mesh", "ExportNanite", "Export Nanite", "Determines whether nanite meshes should be exported if available."),
        new("Mesh", "PolygonType", "Polygon Type", "The type of polygon that imported meshes should use."),
        new("Mesh", "ImportCollision", "Import Collision Geometry", "Imports the convex collision geometry if available."),
        new("Animation", "LoopAnimation", "Loop Animation", "Whether or not to loop imported animations."),
        new("Animation", "UpdateTimelineLength", "Update Timeline Length", "Whether or not to update the timeline length to the duration of the animation."),
        new("Animation", "ImportLobbyPoses", "Import Lobby Animations", "Whether or not to import lobby animations. (Outfit Only)"),
        new("Material", "ExportMaterials", "Export", "Whether or not to export materials."),
        new("Material", "AmbientOcclusion", "Ambient Occlusion", "Adds darker details in the crevices of materials.", 0, 1),
        new("Material", "Cavity", "Cavity", "Adds contrast to parts of the material using 'Soft Light' blending.", 0, 1),
        new("Material", "Subsurface", "Subsurface Scattering", "The scattering of light through skin or other surfaces (FP's shaders).", 0, 0.5),
        new("Material", "ToonShadingBrightness", "Toon Brightness", "The brightness of the output of the toon material.", 0, 1),
        new("Material", "MaterialImportMethod", "Method", "The way standalone materials should be imported."),
        new("Material", "PreferFPShaders", "Prefer FP Shaders for Characters", "Outfits, back blings, pickaxes, gliders, pets, kicks and sidekicks use FP's prebuilt shaders where FP has one for a material; the others keep the exact material rebuilt from its Unreal graph."),
        new("Material", "RimLight", "Rim Light", "Keeps the rim light Fortnite's character materials add at the edges of a silhouette (their baseBrightness). Off: baseBrightness is 0 on exact materials."),
        new("Material", "SubsurfaceIntensity", "Skin Subsurface", "How much light scatters under skin, where the game's material scatters (x its amount).", 0, 1),
        new("Material", "BaseSubsurface", "Base Subsurface", "Light scattering over the whole of a cosmetic, clothes included, for a softer look. 0: only where the game scatters.", 0, 1),
        new("Material", "SubsurfaceScale", "Scatter Distance", "How far light scatters, x the game's distance (1: the game's).", 0, 10),
        new("Material", "ProfileColour", "Profile Colour", "How much of the skin profile's colour the scattering keeps. 1: the game's; lower softens coloured profiles that darken and saturate skin.", 0, 1),
        new("Material", "FurSubsurfaceIntensity", "Fur Subsurface Intensity", "How much light scatters under shell fur (all of it) and the skin under it (where the game's scatters any). Higher: softer fur that blends into the body.", 0, 1),
        new("Material", "FurSubsurfaceScale", "Fur Subsurface Scale", "How far light scatters under shell fur and the skin under it, times the game's distance (1: the game's).", 0, 20),
        new("Texture", "ImageFormat", "Image Format", "The file type that textures should be exported as."),
        new("Texture", "MaxTextureSize", "Max Texture Size", "The largest textures are imported at, from their own smaller mips (no resampling)."),
        new("Texture", "MapTextureSize", "Map Texture Size", "The same for maps and prefabs: hundreds of textures fit in far less memory at 1024."),
        new("Texture", "TextureImportMethod", "Method", "The way textures should be imported."),
        new("Sound", "ImportSounds", "Import Sounds", "Whether or not to import sounds collected from animation notifies."),
        new("Sound", "SoundFormat", "Sound Format", "The file type that sounds should be exported as."),
        new("World", "ImportInstancedFoliage", "Import Instanced Foliage", "Whether or not to import instanced foliage meshes."),
        new("World", "LandscapeDetail", "Landscape Detail", "How much of a landscape's grid is imported. Half and Quarter keep every 2nd or 4th line (4x or 16x fewer polygons) with exact UVs and paint layers."),
    ];

    private static object Model => AppServices.AppSettings.ExportSettings.GetSettingsViewModel(EExportLocation.Blender);

    public static object List() => Dispatcher.UIThread.Invoke(() => Rows.Select(Field).Where(f => f is not null).ToList());

    public static object? Set(string name, string value) => Dispatcher.UIThread.Invoke(() =>
    {
        var row = Rows.FirstOrDefault(r => r.Property == name) ?? throw new ArgumentException("no setting " + name);
        Assign(Model, name, value);
        AppServices.AppSettings.Save();
        return Field(row);
    });

    /// <summary>The Blender export settings for one export: a copy with "Name:Value;Name:Value" over it (the editor's
    /// per-import options). The app's own settings stay as they are.</summary>
    public static BaseExportSettings Overridden(string? overrides) => Dispatcher.UIThread.Invoke(() =>
    {
        var model = (BaseExportSettings) Model;
        var copy = (BaseExportSettings) Activator.CreateInstance(model.GetType())!;
        foreach (var p in model.GetType().GetProperties().Where(p => p is { CanRead: true, CanWrite: true } && p.GetIndexParameters().Length == 0))
            p.SetValue(copy, p.GetValue(model));
        foreach (var pair in (overrides ?? "").Split(';', StringSplitOptions.RemoveEmptyEntries).Select(x => x.Split(':', 2)))
        {
            if (pair.Length != 2 || Rows.All(r => r.Property != pair[0])) throw new ArgumentException("no setting " + pair[0]);
            Assign(copy, pair[0], pair[1]);
        }
        return copy;
    });

    private static void Assign(object model, string name, string value)
    {
        var property = model.GetType().GetProperty(name) ?? throw new ArgumentException("no setting " + name);
        var type = property.PropertyType;
        object parsed = type == typeof(bool) ? bool.Parse(value)
            : type.IsEnum ? Enum.Parse(type, value)
            : Convert.ChangeType(value, type, System.Globalization.CultureInfo.InvariantCulture);
        property.SetValue(model, parsed);
    }

    private static object? Field(Row row)
    {
        if (Model.GetType().GetProperty(row.Property) is not { } property) return null;     // a setting this build doesn't have
        var type = property.PropertyType;
        var value = property.GetValue(Model);
        var kind = type == typeof(bool) ? "bool" : type.IsEnum ? "enum" : type == typeof(int) ? "int"
            : type == typeof(float) || type == typeof(double) ? "float" : "string";
        return new
        {
            name = row.Property, label = row.Label, section = row.Section, description = row.Description, type = kind,
            value = type.IsEnum ? value?.ToString() : value,
            choices = type.IsEnum ? Enum.GetNames(type) : null,
            labels = type.IsEnum ? Enum.GetValues(type).Cast<Enum>().Select(e => e.Description).ToArray() : null,
            min = row.Min, max = row.Max,
        };
    }
}
