using System;
using CUE4Parse.UE4.Assets.Exports;
using CUE4Parse.UE4.Assets.Exports.SkeletalMesh;
using FortnitePorting.Exporting.Models;
using FortnitePorting.Exporting.Types;

namespace FortnitePorting.Exporting.MaterialPorter;

/// <summary>
/// The Assets page's face board button: the plugin puts a face board on the armature selected in Blender (sent as the
/// rig button's kind, Apply telling them apart). FaceDNA: the selected outfit's head RigLogic data, so a MetaHuman-style
/// board drives the facial bones as the game does; null without an outfit or for a head without one.
/// </summary>
public class FaceBoardExport : BaseExport
{
    public string Apply = "FaceBoard";
    public string? FaceDNA;

    public FaceBoardExport(ExportDataMeta metaData, UObject? outfit) : base("Face Board", EExportType.TastyRig, metaData)
    {
        if (outfit is null) return;
        var parts = outfit.GetOrDefault("BaseCharacterParts", Array.Empty<UObject>());
        if (parts.Length == 0 && outfit.TryGetValue(out UObject hero, "HeroDefinition") && hero.TryGetValue(out UObject[] specializations, "Specializations"))
            parts = specializations[0].GetOrDefault("CharacterParts", Array.Empty<UObject>());
        foreach (var part in parts)
        {
            if (part.GetOrDefault<USkeletalMesh?>("SkeletalMesh") is not { } mesh || Context.FaceDna(mesh) is not { } dna) continue;
            FaceDNA = dna;
            break;
        }
    }
}
