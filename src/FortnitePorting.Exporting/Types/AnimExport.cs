using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Linq;
using CUE4Parse.UE4.Assets.Exports;
using CUE4Parse.UE4.Assets.Exports.Animation;
using CUE4Parse.UE4.Assets.Exports.Animation.CurveExpression;
using CUE4Parse.UE4.Assets.Exports.MetaSound;
using CUE4Parse.UE4.Assets.Exports.Sound;
using CUE4Parse.UE4.Assets.Objects;
using CUE4Parse.UE4.Objects.Core.Math;
using CUE4Parse.UE4.Objects.Engine.Curves;
using CUE4Parse.UE4.Objects.UObject;
using FortnitePorting.CUE4Parse.Extensions;
using FortnitePorting.CUE4Parse.Models.Fortnite;
using FortnitePorting.CUE4Parse.Models.Fortnite.AnimNotifies;
using FortnitePorting.Exporting.Extensions;
using FortnitePorting.Exporting.Models;
using FortnitePorting.Exporting.Models.Files.Meta;
using FortnitePorting.Exporting.Styles;
using FortnitePorting.Shared.Extensions;

namespace FortnitePorting.Exporting.Types;

public class AnimExport : BaseExport
{
    public ExportMesh? Skeleton;
    public readonly List<ExportAnimSection> Sections = new();
    public readonly List<ExportSound> Sounds = new();
    public readonly List<ExportProp> Props = new();
    // Material Porter fork: the particle effects the animation's notifies play
    public readonly List<MaterialPorter.ExportAnimEffect> MPEffects = new();
    // and the skeleton's sockets (an effect sits on one, or reads them; an armature in Blender may have none of them)
    public readonly Dictionary<string, MaterialPorter.ExportSocket> MPSockets = new(StringComparer.OrdinalIgnoreCase);
    // and when a swing turns the held pickaxe's trails on and off: [on, off] times (its MeleeAnimTrails notifies)
    public List<float[]> MPTrails = [];
    // and when its swings hit (the melee ability each one triggers): times, for the pickaxe's hit effects
    public List<float> MPHits = [];
    private readonly List<float> _trailsOn = [], _trailsOff = [];
    private readonly HashSet<FAnimNotifyEvent> _effectNotifies = [];
    private readonly Dictionary<string, MaterialPorter.ExportAnimEffect> _effects = [];
    public List<ExportCurveMapping> LegacyToMetahumanMappings = [];
    public List<ExportCurveMapping> MetahumanToLegacyMappings = [];
    
    public AnimExport(string name, UObject asset, ExportStyleBase[] styles, EExportType exportType, ExportDataMeta metaData, IExportFileMeta? fileMeta) : base(name, exportType, metaData)
    {
        // Material Porter fork: one the Animations tab lists unread is read now
        asset = MaterialPorter.Unloaded.Read(asset, Context.Meta.Provider.Provider);
        switch (exportType)
        {
            case EExportType.Animation:
            {
                switch (asset)
                {
                    case UAnimMontage animMontage:
                    {
                        AnimMontage(animMontage);
                        break;
                    }
                    case UAnimSequenceBase animSequenceBase:
                    {
                        if (animSequenceBase.Skeleton.Load<USkeleton>() is { } skeleton)
                        {
                            Skeleton = Context.Skeleton(skeleton);
                        }
                        
                        if (fileMeta is ExportAdditiveAnimFileMeta additiveAnimFileMeta
                            && animSequenceBase is UAnimSequence animSequence)
                        {
                            Sections.AddIfNotNull(Context.AnimSequence(animSequence, additiveAnimFileMeta.BaseSequence));
                        }
                        else
                        {
                            Sections.AddIfNotNull(Context.AnimSequence(animSequenceBase));
                        }
                        break;
                    }
                }
                        
                break;
            }
            case EExportType.Emote:
            case EExportType.LegoEmote: // Material Porter fork: a LEGO emote's montage is its override's "Animation"
            {
                if (styles.Length > 0)
                {
                    foreach (var style in styles.OfType<ExportObjectStyle>())
                    {
                        if (style.StyleData is not UAnimMontage styleMontage) continue;
                        
                        AnimMontage(styleMontage);
                    }
                    
                    break;
                }
                
                var montage = asset.GetOrDefault<UAnimMontage?>("Animation");
                montage ??= asset.GetOrDefault<UAnimMontage?>("FrontEndAnimation");
                montage ??= DataListMontage(asset);
                if (montage is null) break;
                
                AnimMontage(montage);
                // Material Porter fork: a LEGO emote animates the figure's face with its curves
                // (the face material's parameters); their keys' interpolation goes along
                if (exportType is EExportType.LegoEmote)
                    foreach (var section in Sections)
                        section.MPCurveModes = CurveModes(section.AssetRef);
                break;
            }
        }

        // Material Porter fork: a plain sequence's effect notifies (a montage's are read with it)
        if (asset is UAnimSequenceBase sequence and not UAnimMontage)
        {
            SkeletonSockets(sequence.Skeleton.Load<USkeleton>());
            foreach (var notify in sequence.Notifies ?? [])
                EffectNotify(notify, 0);
        }

        if (Context.Meta.Provider.Provider.TryLoadPackageObject<UCurveExpressionsDataAsset>(
                "FortniteGame/Content/Characters/Player/Common/Fortnite_Base_Head/Facials/CurveMappings/FN_LegacyTo3L_Main_Mapping",
                out var legacyToMetahumanCurves))
        {
            LegacyToMetahumanMappings = CurveMappings(legacyToMetahumanCurves);
        }
        
        if (Context.Meta.Provider.Provider.TryLoadPackageObject<UCurveExpressionsDataAsset>(
                "FortniteGame/Content/Characters/Player/Common/Fortnite_Base_Head/Facials/CurveMappings/FN_3LToLegacy_Main_Mapping",
                out var metahumanToLegacyCurves))
        {
            MetahumanToLegacyMappings = CurveMappings(metahumanToLegacyCurves);
        }
    }
    
    private void AnimMontage(UAnimMontage montage)
    {
        // Material Porter fork: a LEGO emote's montage names no skeleton; its sequences do (the figure's)
        var skeleton = montage.Skeleton.Load<USkeleton>()
                       ?? montage.CompositeSections.Select(section => section.LinkedSequence.Load<UAnimSequence>()?.Skeleton.Load<USkeleton>()).FirstOrDefault(s => s is not null);
        Skeleton = Context.Skeleton(skeleton)!;
        HandleSectionTree(Sections, montage, montage.CompositeSections.First());

        var notifies = new List<FAnimNotifyEvent>();
        notifies.AddRange(montage.GetOrDefault("Notifies", Array.Empty<FAnimNotifyEvent>()));
        notifies.AddRange(Sections.SelectMany(section => section.AssetRef?.Notifies ?? []));
        foreach (var notify in notifies)
        {
            HandleNotify(notify);
        }

        // Material Porter fork: the Niagara effects its notifies play, each at its time (a section's own
        // notifies: from the section's start)
        SkeletonSockets(skeleton);
        foreach (var notify in montage.GetOrDefault("Notifies", Array.Empty<FAnimNotifyEvent>()))
            EffectNotify(notify, 0);
        foreach (var section in Sections)
            foreach (var notify in section.AssetRef?.Notifies ?? [])
                EffectNotify(notify, section.Time);
    }

    /// <summary>Material Porter fork: the trail switches as windows: each "on" until the next "off" (half a second without one).</summary>
    private void TrailWindows()
    {
        MPTrails = _trailsOn.Distinct().OrderBy(t => t)
            .Select(on => new[] { on, _trailsOff.Where(off => off > on).DefaultIfEmpty(on + 0.5f).Min() }).ToList();
    }

    /// <summary>Material Porter fork: a skeleton's sockets, by name.</summary>
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

    /// <summary>Material Porter fork: a notify that plays a Niagara system, as an effect on its socket from its time
    /// (a notify's own: a montage's is linked to one of its segments, absolutely or from the segment's start).</summary>
    private void EffectNotify(FAnimNotifyEvent notify, float sectionTime)
    {
        if (!_effectNotifies.Add(notify)) return;
        try
        {
            var timed = notify.NotifyStateClass?.Load<UObject>();
            var played = timed ?? notify.Notify?.Load<UObject>();
            // a swing's hit (the melee ability it triggers)
            if (played?.ExportType is "FortAnimNotify_TriggerGameplayAbility")
            {
                MPHits = MPHits.Append(sectionTime + notify.GetTime()).Distinct().OrderBy(t => t).ToList();
                return;
            }
            // a swing's trail switch
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

    private void HandleSectionTree(List<ExportAnimSection> sections, UAnimMontage montageRef, FCompositeSection currentSection, float time = 0.0f)
    {
        var baseSequence = currentSection.LinkedSequence.Load<UAnimSequence>();
        if (baseSequence is null) return;

        ExportAnimSection? anim = null;
        if (montageRef.SlotAnimTracks.FirstOrDefault(slot => slot.SlotName.Text.Equals("AdditiveCorrective")) is
            { } additiveSlot)
        {
            var additiveSection = additiveSlot.AnimTrack.AnimSegments.FirstOrDefault(x => Math.Abs(x.StartPos - currentSection.SegmentBeginTime) < 0.01);
            var additiveSequence = additiveSection?.AnimReference.Load<UAnimSequence>();
            anim = Context.AnimSequence(additiveSequence, baseSequence);
        }
        
        anim ??= Context.AnimSequence(baseSequence);
        
        if (anim is not null)
        {
            anim.Name = currentSection.SectionName.Text;
            anim.Time = currentSection.SegmentBeginTime + time;
            anim.LinkValue = currentSection.LinkValue;
            anim.Loop = currentSection.SectionName == currentSection.NextSectionName || currentSection.NextSectionName.IsNone;
            anim.AssetRef = baseSequence;
            
            sections.Add(anim);
        
            if (anim.Loop) return;
        }

        var nextSection = montageRef.CompositeSections.FirstOrDefault(sec => currentSection.NextSectionName == sec.SectionName);
        if (nextSection is null) return;
        
        if (Sections.Any(section => section.Name.Equals(nextSection.SectionName.Text, StringComparison.OrdinalIgnoreCase))) return;

        var isSequentiallyNext = Math.Abs(nextSection.SegmentBeginTime - currentSection.SegmentBeginTime) < 0.01f;
        HandleSectionTree(sections, montageRef, nextSection, isSequentiallyNext ? time + currentSection.SegmentLength : time);
    }

    private void HandleNotify(FAnimNotifyEvent notify)
    {
        switch (notify.NotifyStateClass.Load())
        {
            case FortAnimNotifyState_EmoteSound soundNotify:
            {
                var sounds = new List<Sound>();
                sounds.AddRangeIfNotNull(soundNotify.EmoteSound1P?.HandleSoundTree(notify.TriggerTimeOffset));
                sounds.AddRangeIfNotNull(HandleMetaSound(soundNotify.MetaEmoteSound1P, notify.TriggerTimeOffset));
                foreach (var sound in sounds)
                {
                    Sounds.Add(new ExportSound
                    {
                        Path = Context.Export(sound.SoundWave.Load<USoundWave>()),
                        // Material Porter fork: the notify's own time (one linked from its segment's start: LinkValue alone is early)
                        Time = sound.Time + notify.GetTime(),
                        Loop = sound.Loop
                    });
                }
                break;
            }
            case FortAnimNotifyState_SpawnProp propNotify:
            {
                var mesh = Context.Mesh(propNotify.StaticMeshProp) ?? Context.Mesh(propNotify.SkeletalMeshProp);
                if (mesh is null) break;
                
                var animSections = new List<ExportAnimSection>();
                
                if (propNotify.SkeletalMeshPropMontage is { } montage) 
                    HandleSectionTree(animSections, montage, montage.CompositeSections.First());
                
                if (animSections.Count == 0 && propNotify.SkeletalMeshPropAnimationMontage is { } secondMontage)
                    HandleSectionTree(animSections, secondMontage, secondMontage.CompositeSections.First());

                if (animSections.Count == 0 && propNotify.SkeletalMeshPropAnimation is { } animSequence &&
                    Context.AnimSequence(animSequence) is { } exportedSequenceSection)
                    animSections = [exportedSequenceSection];
                
                var prop = new ExportProp
                {
                    Mesh = mesh,
                    AnimSections = animSections,
                    SocketName = propNotify.SocketName.Text,
                    LocationOffset = propNotify.LocationOffset,
                    RotationOffset = propNotify.RotationOffset,
                    Scale = propNotify.Scale
                };

                Props.Add(prop);
                break;
            }
        }
    }

    /// <summary>Material Porter fork: a sequence's float curves' key interpolation (see ExportAnimSection.MPCurveModes).</summary>
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

    private List<ExportCurveMapping> CurveMappings(UCurveExpressionsDataAsset curveExpressions)
    {
        var mappings = new List<ExportCurveMapping>();
        if (curveExpressions?.ExpressionData?.ExpressionMap == null) return mappings;

        foreach (var (curveName, expr) in curveExpressions.ExpressionData.ExpressionMap)
        {
            var expressionStack = expr.Expression.Select(element => element switch
                {
                    OpElement<EOperator> op => new ExportCurveExpressionElement(OpElement.EOperator, (int)op.Value),
                    OpElement<FName> name => new ExportCurveExpressionElement(OpElement.FName, name.Value.Text),
                    OpElement<FFunctionRef> functionRef => new ExportCurveExpressionElement(OpElement.FFunctionRef, functionRef.Value.Index),
                    OpElement<float> single => new ExportCurveExpressionElement(OpElement.Float, single.Value),
                })
                .ToList();

            mappings.Add(new ExportCurveMapping
            {
                Name = curveName.Text,
                ExpressionStack = expressionStack
            });
        }
        
        return mappings;
    }

    private List<Sound> HandleMetaSound(UMetaSoundSource? metaSoundSource, float offsetTime = 0.0f)
    {
        if (metaSoundSource is null) return [];
        
        var rootMetasoundDocument = metaSoundSource.GetOrDefault<FStructFallback?>("RootMetaSoundDocument") 
                                    ?? metaSoundSource.GetOrDefault<FStructFallback?>("RootMetasoundDocument");
        if (rootMetasoundDocument is null) return [];

        var sounds = new List<Sound>();
        var rootGraph = rootMetasoundDocument.Get<FStructFallback>("RootGraph");
        var interFace = rootGraph.Get<FStructFallback>("Interface");
        var inputs = interFace.Get<FStructFallback[]>("Inputs");
        foreach (var input in inputs)
        {
            var typeName = input.Get<FName>("TypeName");
            if (!typeName.Text.Contains("WaveAsset")) continue;
                
            var name = input.Get<FName>("Name");
            if (!name.Text.Contains("Loop")) continue;
            
            var literal = input.GetOrDefault<FStructFallback?>("DefaultLiteral");
            if (literal is null && input.TryGetValue(out FStructFallback[] defaults, "Defaults"))
            {
                literal = defaults.FirstOrDefault()?.GetOrDefault<FStructFallback?>("Literal");
            }

            var soundWave = literal?.Get<FPackageIndex[]>("AsUObject").FirstOrDefault();
            if (soundWave is null) continue;
            
            sounds.Add(SoundExtensions.CreateSound(soundWave));
        }

        return sounds;
    }

    private UAnimMontage? DataListMontage(UObject asset)
    {
        var groups = asset.GetDataListItem<FStructFallback[]>("Groups") ?? [];

        foreach (var group in groups)
        {
            var entries = group.GetOrDefault<FInstancedStruct[]>("Entries");
            var targetMontage = entries.GetItemOrDefault<UAnimMontage?>("MaleMontage");
            targetMontage ??= entries.GetItemOrDefault<UAnimMontage?>("MaleMontage");
            targetMontage ??= entries.GetItemOrDefault<UAnimMontage?>("DefaultMontage");
            
            if (targetMontage is not null)
                return targetMontage;
        }

        return null;
    }
    
}
