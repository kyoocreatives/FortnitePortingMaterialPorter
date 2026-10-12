using System.Linq;
using System.Text.RegularExpressions;
using CUE4Parse.UE4.Assets.Exports;

namespace FortnitePorting.Exporting.MaterialPorter;

/// <summary>
/// The Animations tab's other filters, from the package path: the character's gender and body type (the player's animations sit in
/// .../Medium/Female/... folders, emotes end in _CMF/_CMM: Character Medium Female/Male) and what the animation is for. Each group
/// takes its first match. Tall has no animations of its own (none in the files), so it isn't offered.
/// </summary>
public static partial class Animations
{
    private const RegexOptions Options = RegexOptions.IgnoreCase | RegexOptions.Compiled;

    public static readonly (string Group, (string Name, Regex Match)[] Items)[] Groups =
    [
        ("GENDER",
        [
            ("Female", new Regex(@"[/_]female(?=[/_]|$)|_CMF(?=_|$)|/F_(SML|MED|LRG)_", Options)),
            ("Male", new Regex(@"[/_]male(?=[/_]|$)|_CMM(?=_|$)|/M_(SML|MED|LRG)_", Options)),
        ]),
        ("BODY TYPE",
        [
            ("Small", new Regex(@"/small/|_SML_|smallbodytype", Options)),
            ("Medium", new Regex(@"/medium/|_MED_|mediumbodytype|_CM[FM](?=_|$)", Options)),
            ("Large", new Regex(@"/large/|_LRG_|largebodytype", Options)),
        ]),
        ("USE",
        [
            ("Poses", new Regex(@"/poses?/", Options)),      // first: a pose folder holds sprint and aim poses
            ("Locomotion", new Regex(@"/locomotion|locomotion_|/jog/|/walk/|/sprint|tacsprint|/crouch/|/run/|motionmatching", Options)),
            ("Swimming", new Regex(@"swim", Options)),
            ("Skydiving", new Regex(@"skydive|freefall|paraglide|wingsuit", Options)),
            ("Combat", new Regex(@"/combat/|/guns?/|/melee/|/gadgets/|reload|_fire|/weapons?/|harvest", Options)),
            ("Traversal", new Regex(@"mantle|/climb|/ledge|/slide/|/vault|grindrail|grapple", Options)),
            ("Riding", new Regex(@"ridingcontent|hardsaddle|/riding|/mount", Options)),
            ("Vehicles", new Regex(@"/vehicles?/|/driver/|motorcycle", Options)),
            ("Items", new Regex(@"/items?/|gameplay/", Options)),
            ("Festival", new Regex(@"/bandplay/|sparkscharacter|/jam/", Options)),
            ("Lobby", new Regex(@"/menu/|charintro|/lobby", Options)),
            ("Facial", new Regex(@"/facial/|/face/", Options)),
        ]),
    ];

    /// <summary>The item of a group an animation path falls in (first match), or null.</summary>
    public static string? GroupOf(string package, int group) => Groups[group].Items.FirstOrDefault(i => i.Match.IsMatch(package)).Name;

    public static bool InGroup(UObject animation, int group, string name) =>
        (Unloaded.Detail(animation) is Outline outline ? outline.Groups[group] : GroupOf(animation.GetPathName(), group)) == name;
}
