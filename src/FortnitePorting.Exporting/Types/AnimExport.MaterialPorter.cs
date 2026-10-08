using System;
using System.Collections.Generic;
using System.Linq;
using CUE4Parse.UE4.Assets.Exports;
using CUE4Parse.UE4.Assets.Exports.Animation;
using CUE4Parse.UE4.Objects.Core.Math;
using CUE4Parse.UE4.Objects.Engine.Curves;
using CUE4Parse.UE4.Objects.UObject;
using FortnitePorting.CUE4Parse.Extensions;
using FortnitePorting.Exporting.Extensions;

namespace FortnitePorting.Exporting.Types;

// Effects played by animation notifies, skeleton sockets, LEGO face curves.
public partial class AnimExport
{
    public readonly List<MaterialPorter.ExportAnimEffect> MPEffects = new();
    // Blender's armature may lack the sockets effects sit on
    public readonly Dictionary<string, MaterialPorter.ExportSocket> MPSockets = new(StringComparer.OrdinalIgnoreCase);
    // pickaxe trail [on, off] times per swing
    public List<float[]> MPTrails = [];
    // swing hit times (for pickaxe hit effects)
    public List<float> MPHits = [];
    private readonly List<float> _trailsOn = [], _trailsOff = [];
    private readonly HashSet<FAnimNotifyEvent> _effectNotifies = [];
    private readonly Dictionary<string, MaterialPorter.ExportAnimEffect> _effects = [];

    // a LEGO emote montage names no skeleton; its sequences do
    private static USkeleton? MontageSkeleton(UAnimMontage montage) =>
        montage.Skeleton.Load<USkeleton>()
        ?? montage.CompositeSections.Select(section => section.LinkedSequence.Load<UAnimSequence>()?.Skeleton.Load<USkeleton>()).FirstOrDefault(s => s is not null);

    // a montage's effect notifies; a section's own notifies count from the section start
    private void ReadMontageEffects(UAnimMontage montage, USkeleton? skeleton)
    {
        SkeletonSockets(skeleton);
        foreach (var notify in montage.GetOrDefault("Notifies", Array.Empty<FAnimNotifyEvent>()))
            EffectNotify(notify, 0);
        foreach (var section in Sections)
            foreach (var notify in section.AssetRef?.Notifies ?? [])
                EffectNotify(notify, section.Time);
    }

    // a plain sequence's effect notifies (montages are read above)
    private void ReadSequenceEffects(UObject asset)
    {
        if (asset is not UAnimSequenceBase sequence || asset is UAnimMontage) return;
        SkeletonSockets(sequence.Skeleton.Load<USkeleton>());
        foreach (var notify in sequence.Notifies ?? [])
            EffectNotify(notify, 0);
    }

    // a LEGO emote animates the face material with curves; keep their key interpolation
    private void ReadFaceCurveModes()
    {
        foreach (var section in Sections)
            section.MPCurveModes = CurveModes(section.AssetRef);
    }

    // each "on" lasts until the next "off" (half a second without one)
    private void TrailWindows()
    {
        MPTrails = _trailsOn.Distinct().OrderBy(t => t)
            .Select(on => new[] { on, _trailsOff.Where(off => off > on).DefaultIfEmpty(on + 0.5f).Min() }).ToList();
    }

    private void SkeletonSockets(USkeleton? skeleton)
    {
        foreach (var index in skeleton?.Sockets ?? [])
        {
            if (index.Load<global::CUE4Parse.UE4.Assets.Exports.SkeletalMesh.USkeletalMeshSocket>() is not { } socket || socket.SocketName.Text is not { Length: > 0 } name) continue;
            MPSockets.TryAdd(name, new MaterialPorter.ExportSocket
            {
                Bone = socket.BoneName.Text, Location = socket.RelativeLocation, Rotation = socket.RelativeRotation, Scale = socket.RelativeScale,
            });
        }
    }

    // A notify playing a Niagara system becomes an effect on its socket at its time; swing hits and trail switches are recorded as times.
    private void EffectNotify(FAnimNotifyEvent notify, float sectionTime)
    {
        if (!_effectNotifies.Add(notify)) return;
        try
        {
            var timed = notify.NotifyStateClass?.Load<UObject>();
            var played = timed ?? notify.Notify?.Load<UObject>();
            if (played?.ExportType is "FortAnimNotify_TriggerGameplayAbility")
            {
                MPHits = MPHits.Append(sectionTime + notify.GetTime()).Distinct().OrderBy(t => t).ToList();
                return;
            }
            if (played?.ExportType is "FortAnimNotify_MeleeAnimTrails_On" or "FortAnimNotify_MeleeAnimTrails_Off")
            {
                (played.ExportType.EndsWith("_On") ? _trailsOn : _trailsOff).Add(sectionTime + notify.GetTime());
                TrailWindows();
                return;
            }
            if (played?.GetOrDefault<UObject?>("Template") is not { ExportType: "NiagaraSystem" } system) return;
            var socket = played.GetOrDefault<FName>("SocketName");
            var location = played.GetOrDefault("LocationOffset", FVector.ZeroVector);
            var rotation = played.GetOrDefault("RotationOffset", FRotator.ZeroRotator);
            var scale = played.GetOrDefault("Scale", FVector.OneVector);
            // one effect per system and place, played at each of its notifies' times
            var key = $"{system.GetPathName()}|{socket.Text}|{location}|{rotation}|{scale}";
            if (!_effects.TryGetValue(key, out var effect))
            {
                effect = _effects[key] = new MaterialPorter.ExportAnimEffect
                {
                    Effect = Context.Effect(system),
                    SocketName = MaterialPorter.Effects.Named(socket) ? socket.Text : null,
                    LocationOffset = location, RotationOffset = rotation, Scale = scale,
                };
                MPEffects.Add(effect);
            }
            effect.Times.Add(sectionTime + notify.GetTime());
            effect.Durations.Add(timed is not null ? notify.Duration : 0);
        }
        catch (Exception e)
        {
            Serilog.Log.Warning("[Material Porter] {Name}: an effect notify wasn't read ({Error})", Name, e.Message);
        }
    }

    // Float curve key interpolation, one letter per key (C constant, Q cubic, L linear), or one letter if all agree
    private static Dictionary<string, string>? CurveModes(UAnimSequence? sequence)
    {
        if (sequence?.CompressedCurveData?.FloatCurves is not { Length: > 0 } curves) return null;
        var modes = new Dictionary<string, string>();
        foreach (var curve in curves)
        {
            var letters = string.Concat(curve.FloatCurve.Keys.Select(key => key.InterpMode switch
            {
                ERichCurveInterpMode.RCIM_Constant => 'C',
                ERichCurveInterpMode.RCIM_Cubic => 'Q',
                _ => 'L',
            }));
            if (letters.Length == 0) continue;
            modes[curve.CurveName.Text] = letters.Distinct().Count() == 1 ? letters[..1] : letters;
        }
        return modes;
    }
}
