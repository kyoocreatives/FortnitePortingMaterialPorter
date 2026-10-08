using System;
using System.Collections.Generic;
using System.Linq;
using CUE4Parse.UE4.Assets.Exports;
using CUE4Parse.UE4.Assets.Exports.Animation;
using CUE4Parse.UE4.Assets.Exports.Material;
using CUE4Parse.UE4.Assets.Exports.SkeletalMesh;
using CUE4Parse.UE4.Assets.Exports.StaticMesh;
using CUE4Parse.UE4.Assets.Exports.Texture;
using CUE4Parse.UE4.Assets.Objects;
using CUE4Parse.UE4.Objects.Core.Math;
using CUE4Parse.UE4.Objects.UObject;
using FortnitePorting.CUE4Parse.Extensions;
using FortnitePorting.Exporting.MaterialPorter;
using FortnitePorting.Exporting.Models;
using Newtonsoft.Json.Linq;

namespace FortnitePorting.Exporting.Context;

/// <summary>
/// Niagara systems as nodes: each enabled emitter becomes an empty holding what its renderers draw
/// (meshes with materials; sprite/ribbon materials on a plane the plugin makes). Renderer material
/// parameters ride on the material (MPValues). CPU emitters keep their compiled scripts for the
/// plugin to run (Effects.Program); GPU emitters only keep the pieces they draw.
/// </summary>
public partial class ExportContext
{
    /// <summary>Whether the item's export takes its own effects along (the Effects pick).</summary>
    public bool EffectsPick;

    /// <summary>Picked styles' effect changes by system path: swapped systems (VariantParticles) and user parameters (VariantParticleParams).</summary>
    public readonly Dictionary<string, FSoftObjectPath> EffectSwaps = new(StringComparer.OrdinalIgnoreCase);
    public readonly Dictionary<string, Dictionary<string, object>> EffectUser = new(StringComparer.OrdinalIgnoreCase);

    /// <summary>Adds a picked style's effect swaps and user parameters.</summary>
    public void AddEffectStyle(FStructFallback style)
    {
        foreach (var swap in style.GetOrDefault("VariantParticles", Array.Empty<FStructFallback>()))
        {
            var from = swap.GetOrDefault<FSoftObjectPath>("ParticleSystemToAlter").AssetPathName.Text;
            if (Effects.Named(new FName(from))) EffectSwaps[from] = swap.GetOrDefault<FSoftObjectPath>("OverrideParticleSystem");
        }
        foreach (var set in style.GetOrDefault("VariantParticleParams", Array.Empty<FStructFallback>()))
        {
            var system = set.GetOrDefault<FSoftObjectPath>("ParticleSystemToAlter").AssetPathName.Text;
            if (!Effects.Named(new FName(system))) continue;
            if (!EffectUser.TryGetValue(system, out var values)) EffectUser[system] = values = new Dictionary<string, object>();
            foreach (var kind in new[] { "ColorParams", "VectorParams", "FloatParams" })
                foreach (var p in set.GetOrDefault(kind, Array.Empty<FStructFallback>()))
                {
                    var name = p.GetOrDefault<FName>("ParamName").Text;
                    if (!Effects.Named(new FName(name))) continue;
                    if (!name.StartsWith("User.", StringComparison.OrdinalIgnoreCase)) name = "User." + name;
                    // engine structs: read as JSON
                    var value = JObject.FromObject(p)["Value"];
                    values[name] = value switch
                    {
                        JObject c when c["R"] is not null => new[] { (float) c["R"]!, (float) c["G"]!, (float) c["B"]!, (float?) c["A"] ?? 1f },
                        JObject v => new[] { "X", "Y", "Z", "W" }.Where(k => v[k] is not null).Select(k => (float) v[k]!).ToArray(),
                        _ => (object) (float) (value ?? 0f),
                    };
                }
        }
    }

    /// <summary>A system path after the picked styles' swaps.</summary>
    private FSoftObjectPath Swapped(FSoftObjectPath path) =>
        EffectSwaps.TryGetValue(path.AssetPathName.Text, out var swapped) ? swapped : path;

    private readonly System.Runtime.CompilerServices.ConditionalWeakTable<ExportMesh, Dictionary<string, ExportSocket>> _meshSockets = new();

    /// <summary>Keeps a mesh's sockets for its effects: skeletal (own and skeleton's, on bones) or static (no bone).</summary>
    private void MeshSockets(ExportMesh export, UObject mesh)
    {
        var table = new Dictionary<string, ExportSocket>();
        try
        {
            if (mesh is USkeletalMesh skeletal)
            {
                foreach (var index in skeletal.Sockets.Concat(skeletal.Skeleton.Load<USkeleton>()?.Sockets ?? []))
                    if (index.Load<USkeletalMeshSocket>() is { } socket && Effects.Named(socket.SocketName))
                        table.TryAdd(socket.SocketName.Text, new ExportSocket
                        {
                            Bone = socket.BoneName.Text, Location = socket.RelativeLocation, Rotation = socket.RelativeRotation, Scale = socket.RelativeScale,
                        });
            }
            else if (mesh is UStaticMesh fixedMesh)
            {
                foreach (var index in fixedMesh.Sockets ?? [])
                    if (index.Load<UStaticMeshSocket>() is { } socket && Effects.Named(socket.SocketName))
                        table.TryAdd(socket.SocketName.Text, new ExportSocket
                        {
                            Location = socket.RelativeLocation, Rotation = socket.RelativeRotation, Scale = socket.RelativeScale,
                        });
            }
        }
        catch (Exception e)
        {
            Serilog.Log.Warning("[Material Porter] {Mesh}: its sockets weren't read ({Error})", mesh.Name, e.Message);
        }
        if (table.Count > 0) _meshSockets.AddOrUpdate(export, table);
    }

    /// <summary>Adds one of an item's own effects under its mesh, with its role ("trail", "swing", "idle", "event"), socket (MPParentBone) and placement.</summary>
    private MaterialPorterMesh? OwnEffect(ExportMesh mesh, UObject? system, string role, string? socket, FTransform? place = null)
    {
        if (system is null || Effect(system) is not MaterialPorterMesh effect) return null;
        var node = effect.MPEffect ??= new Dictionary<string, object> { ["Kind"] = "System" };
        node["Role"] = role;
        // picked styles' user parameters override the asset's own
        if (EffectUser.TryGetValue(system.GetPathName(), out var user))
            node["User"] = node.TryGetValue("User", out var had) && had is Dictionary<string, object> own
                ? own.Concat(user).GroupBy(p => p.Key).ToDictionary(g => g.Key, g => g.Last().Value) : new Dictionary<string, object>(user);
        if (_meshSockets.TryGetValue(mesh, out var table)) node["Table"] = table;
        if (socket is not null) effect.MPParentBone = socket;
        if (place is { } at)
            node["Place"] = new Dictionary<string, object> { ["Location"] = at.Translation, ["Rotation"] = at.Rotation.Rotator(), ["Scale"] = at.Scale3D };
        mesh.Children.Add(effect);
        return effect;
    }

    /// <summary>
    /// A pickaxe's trail, swing and idle effects on their sockets (the trail gets the two sockets it runs between).
    /// Hit effects ("impact", per surface) sit at the trail's far socket (the head) and stay unplayed until a hit or Replay Effect.
    /// </summary>
    public void PickaxeEffects(UObject weaponDefinition, List<ExportMesh> meshes)
    {
        if (meshes.FirstOrDefault() is not { } mesh) return;
        foreach (var (property, name, socketProperty) in Effects.PickaxeEffects)
        {
            try
            {
                var path = weaponDefinition.GetDataListItem<FSoftObjectPath>(property);
                if (!Effects.Named(path.AssetPathName) || !path.TryLoad(out UObject? system)) continue;
                var socket = socketProperty is null ? default : weaponDefinition.GetDataListItem<FName>(socketProperty);
                if (OwnEffect(mesh, system, name, Effects.Named(socket) ? socket.Text : null) is not { MPEffect: { } node } || socketProperty is not null) continue;
                var sockets = new[] { Effects.TrailFirstSocket, Effects.TrailSecondSocket }
                    .Select(p => weaponDefinition.GetDataListItem<FName>(p)).Where(Effects.Named).Select(s => s.Text).ToArray();
                if (sockets.Length > 0) node["Sockets"] = sockets;
            }
            catch (Exception e)
            {
                Serilog.Log.Warning("[Material Porter] {Weapon}: its {Effect} effect wasn't read ({Error})", weaponDefinition.Name, name, e.Message);
            }
        }
        try
        {
            var head = new[] { Effects.TrailSecondSocket, Effects.TrailFirstSocket, "SwingFXSocketName" }
                .Select(p => weaponDefinition.GetDataListItem<FName>(p)).FirstOrDefault(Effects.Named);
            foreach (var (path, surfaces) in Effects.PickaxeImpacts(weaponDefinition))
            {
                if (!path.TryLoad(out UObject? system)) continue;
                if (OwnEffect(mesh, system, "impact", Effects.Named(head) ? head.Text : null) is { MPEffect: { } node })
                    node["Surfaces"] = surfaces;
            }
        }
        catch (Exception e)
        {
            Serilog.Log.Warning("[Material Porter] {Weapon}: its hit effects weren't read ({Error})", weaponDefinition.Name, e.Message);
        }
    }

    /// <summary>A character part's idle effect (e.g. back bling glow), on its socket of the part's mesh.</summary>
    public void PartEffects(UObject part, ExportMesh mesh)
    {
        try
        {
            if (Effects.Shown(Swapped(part.GetOrDefault<FSoftObjectPath>(Effects.PartEffect))) is not { } system) return;
            var socket = part.GetOrDefault<FName>(Effects.PartSocket);
            OwnEffect(mesh, system, "idle", Effects.Named(socket) ? socket.Text : null);
        }
        catch (Exception e)
        {
            Serilog.Log.Warning("[Material Porter] {Part}: its idle effect wasn't read ({Error})", part.Name, e.Message);
        }
    }

    /// <summary>A sprite's own NiagaraSystem, on the bone the game's held sprite uses (BP_Weapon_Extractable's ExtractableFX: spine_4_bind).</summary>
    public void SpriteEffects(UObject sprite, ExportMesh mesh)
    {
        try
        {
            if (Effects.Shown(sprite.GetDataListItem<FSoftObjectPath>(Effects.SpriteEffect)) is { } system)
                OwnEffect(mesh, system, "idle", Effects.SpriteEffectBone);
        }
        catch (Exception e)
        {
            Serilog.Log.Warning("[Material Porter] {Sprite}: its effect wasn't read ({Error})", sprite.Name, e.Message);
        }
    }

    /// <summary>A glider's trails on their sockets, set as the locker shows them: front end, fully deployed (speed line opacity waits for it).</summary>
    public void GliderEffects(UObject glider, ExportMesh mesh)
    {
        foreach (var (path, socket, offset) in Effects.GliderTrails(glider))
        {
            try
            {
                if (Effects.Shown(path) is not { } system) continue;
                if (OwnEffect(mesh, system, "trail", Effects.Named(socket) ? socket.Text : null, offset) is { MPEffect: { } node })
                    node["User"] = new Dictionary<string, object> { ["User.bIsFrontEnd"] = true, ["User.bIsFrontEndPreview"] = true, ["User.bIsFullyDeployed"] = true };
            }
            catch (Exception e)
            {
                Serilog.Log.Warning("[Material Porter] {Glider}: a trail wasn't read ({Error})", glider.Name, e.Message);
            }
        }
    }

    /// <summary>A weapon actor's Niagara components on their sockets: "idle" if autoplay, else "event" (unplayed, for Replay Effect).</summary>
    public void WeaponEffects(UObject actorClass, ExportMesh mesh)
    {
        foreach (var (component, socket, auto) in Effects.WeaponComponents(actorClass))
        {
            try
            {
                var place = new FTransform(component.GetOrDefault("RelativeRotation", FRotator.ZeroRotator).Quaternion(),
                    component.GetOrDefault("RelativeLocation", FVector.ZeroVector), component.GetOrDefault("RelativeScale3D", FVector.OneVector));
                OwnEffect(mesh, component.GetOrDefault<UObject?>("Asset"), auto ? "idle" : "event", socket, place);
            }
            catch (Exception e)
            {
                Serilog.Log.Warning("[Material Porter] {Weapon}: its {Component} wasn't read ({Error})", actorClass.Name, component.Name, e.Message);
            }
        }
    }

    public ExportMesh Effect(UObject system)
    {
        var emitters = Effects.Emitters(system);
        var root = new MaterialPorterMesh { Name = system.Name, IsEmpty = true };
        if (emitters.Any(e => e.Sim != "GPU"))
        {
            try
            {
                root.MPEffect = new Dictionary<string, object>
                {
                    ["Kind"] = "System", ["Exports"] = Effects.Program(system), ["Fields"] = Effects.Fields(system),
                };
            }
            catch (Exception e)
            {
                Serilog.Log.Warning("[Material Porter] {System}: not read for a replay ({Error})", system.Name, e.Message);
            }
        }
        var at = 0;
        foreach (var emitter in emitters)
        {
            // laid out in a row, 2 m apart, to pick from (in game they share the system origin)
            var node = new MaterialPorterMesh
            {
                Name = emitter.Name, IsEmpty = true,
                Location = new FVector(0, 200 * at++, 0),
                MPEffect = new Dictionary<string, object> { ["Kind"] = "Emitter", ["Sim"] = emitter.Sim },
            };
            foreach (var renderer in emitter.Renderers)
            {
                try
                {
                    node.Children.AddRange(EffectRenderer(system, emitter, renderer));
                }
                catch (Exception e)
                {
                    Serilog.Log.Warning("[Material Porter] {System}: {Emitter}'s {Renderer} wasn't read ({Error})",
                        system.Name, emitter.Name, renderer.ExportType, e.Message);
                }
            }
            root.Children.Add(node);
        }
        return root;
    }

    /// <summary>A renderer's material parameters (scalars, vectors, textures), or null.</summary>
    private static ParamSet? RendererValues(UObject system, UObject renderer)
    {
        if (!renderer.TryGetValue(out FStructFallback parameters, "MaterialParameters")) return null;
        var values = new ParamSet();
        foreach (var p in parameters.GetOrDefault("ScalarParameters", Array.Empty<FStructFallback>()))
            values.Scalars[p.GetOrDefault<FName>("MaterialParameterName").Text] = p.GetOrDefault<float>("Value");
        foreach (var p in parameters.GetOrDefault("VectorParameters", Array.Empty<FStructFallback>()))
        {
            var c = p.GetOrDefault<FLinearColor>("Value");
            values.Vectors[p.GetOrDefault<FName>("MaterialParameterName").Text] = [c.R, c.G, c.B, c.A];
        }
        foreach (var p in parameters.GetOrDefault("TextureParameters", Array.Empty<FStructFallback>()))
            if (p.GetOrDefault<UTexture?>("Texture") is { } texture)
                values.Textures[p.GetOrDefault<FName>("MaterialParameterName").Text] = texture.GetPathName();
        // A texture parameter bound to a system curve becomes a texture (colour ramp); one bound to a user
        // parameter takes its texture. Values bound to system variables are left to the plugin (effect_replay.bindings).
        foreach (var p in parameters.GetOrDefault("AttributeBindings", Array.Empty<FStructFallback>()))
        {
            // Niagara structs: read as JSON
            var binding = JObject.FromObject(p);
            var variable = (string?)binding["ResolvedNiagaraVariable"]?["Name"] ?? (string?)binding["NiagaraVariable"]?["Name"];
            var parameter = p.GetOrDefault<FName>("MaterialParameterName").Text;
            if (string.IsNullOrEmpty(variable) || !variable.Contains('.')) continue;
            if (Effects.ExposedCurve(system, variable) is { } curve) values.Textures[parameter] = curve;
            else if (variable.StartsWith("User.", StringComparison.OrdinalIgnoreCase) && Effects.UserObject(system, variable) is UTexture own)
                values.Textures[parameter] = own.GetPathName();
        }
        return values.Scalars.Count + values.Vectors.Count + values.Textures.Count > 0 ? values : null;
    }

    /// <summary>A sprite, ribbon or decal material: the bound user parameter's, else its own.</summary>
    private static UMaterialInterface? RendererMaterial(UObject system, UObject renderer)
    {
        var bound = renderer.Properties.FirstOrDefault(p => p.Name.Text == "MaterialUserParamBinding")?.Tag?.GenericValue;
        if (Effects.UserBinding(bound) is { } user && Effects.UserObject(system, user) is UMaterialInterface own) return own;
        return renderer.GetOrDefault<UMaterialInterface?>("Material");
    }

    private ExportMaterial? EffectMaterial(UMaterialInterface? material, int slot, ParamSet? values)
    {
        // an instance embedded in the system: use its parent asset with the instance's values on top
        while (material is UMaterialInstanceConstant { Parent: UMaterialInterface parent } inner && inner.GetPathName().Contains(':'))
        {
            values ??= new ParamSet();
            foreach (var p in inner.ScalarParameterValues) values.Scalars.TryAdd(p.Name, p.ParameterValue);
            foreach (var p in inner.VectorParameterValues)
                if (p.ParameterValue is { } c) values.Vectors.TryAdd(p.Name, [c.R, c.G, c.B, c.A]);
            foreach (var p in inner.TextureParameterValues)
                if (p.ParameterValue.Load<UTexture>() is { } texture) values.Textures.TryAdd(p.Name, texture.GetPathName());
            material = parent;
        }
        if (material is null || Material(material, slot) is not { } export) return null;
        if (values is null) return export;
        return new MaterialPorterMaterial(export) { MPValues = values, Hash = HashCode.Combine(export.Hash, values.Key()) };
    }

    private IEnumerable<ExportMesh> EffectRenderer(UObject system, Effects.Emitter emitter, UObject renderer)
    {
        var values = RendererValues(system, renderer);
        switch (renderer.ExportType)
        {
            case "NiagaraMeshRendererProperties":
            {
                var overrides = renderer.GetOrDefault("bOverrideMaterials", false)
                    ? renderer.GetOrDefault("OverrideMaterials", Array.Empty<FStructFallback>())
                    : [];
                var index = -1;
                foreach (var entry in renderer.GetOrDefault("Meshes", Array.Empty<FStructFallback>()))
                {
                    index++;
                    if (entry.GetOrDefault<UStaticMesh?>("Mesh") is not { } staticMesh || Mesh(staticMesh) is not { } mesh) continue;
                    var export = new MaterialPorterMesh(mesh)
                    {
                        Scale = entry.GetOrDefault("Scale", FVector.OneVector),
                        Rotation = entry.GetOrDefault("Rotation", FRotator.ZeroRotator),
                        MPEffect = new Dictionary<string, object> { ["Kind"] = "Mesh", ["Renderer"] = renderer.Name, ["Index"] = index },
                    };
                    // each slot: the renderer's override, else the mesh's own, with the renderer's parameters
                    // (slots with no mesh material still count if overridden)
                    foreach (var slot in mesh.Materials.Select(m => m.Slot).Concat(Enumerable.Range(0, overrides.Length)).Distinct())
                    {
                        var material = slot < overrides.Length
                            ? Effects.UserBinding(overrides[slot].Properties.FirstOrDefault(p => p.Name.Text == "UserParamBinding")?.Tag?.GenericValue) is { } user
                              && Effects.UserObject(system, user) is UMaterialInterface userMaterial ? userMaterial : overrides[slot].GetOrDefault<UMaterialInterface?>("ExplicitMat")
                            : null;
                        if (material is not null)
                        {
                            if (EffectMaterial(material, slot, values) is { } over) export.OverrideMaterials.Add(over);
                        }
                        else if (values is not null && mesh.Materials.FirstOrDefault(m => m.Slot == slot) is { } own)
                        {
                            export.OverrideMaterials.Add(new MaterialPorterMaterial(own) { MPValues = values, Hash = HashCode.Combine(own.Hash, values.Key()) });
                        }
                    }
                    yield return export;
                }
                break;
            }
            case "NiagaraSpriteRendererProperties" or "NiagaraRibbonRendererProperties":
            {
                if (EffectMaterial(RendererMaterial(system, renderer), 0, values) is not { } material) break;
                var sub = renderer.GetOrDefault("SubImageSize", new FVector2D(1, 1));
                var ribbon = renderer.ExportType.Contains("Ribbon");
                // flipbook: each particle shows one sub-image, picked by the material (the plugin's env.uv)
                if (ribbon)
                    material = new MaterialPorterMaterial(material)
                    {
                        MPValues = (material as MaterialPorterMaterial)?.MPValues,
                        MPRibbon = true,
                        Hash = HashCode.Combine(material.Hash, "ribbon"),
                    };
                else if (sub.X * sub.Y > 1)
                    material = new MaterialPorterMaterial(material)
                    {
                        MPValues = (material as MaterialPorterMaterial)?.MPValues,
                        MPSprite = [(float) sub.X, (float) sub.Y],
                        Hash = HashCode.Combine(material.Hash, sub.X, sub.Y),
                    };
                yield return new MaterialPorterMesh
                {
                    Name = $"{emitter.Name} {(ribbon ? "ribbon" : "sprite")}",
                    IsEmpty = true,
                    MPEffect = new Dictionary<string, object>
                    {
                        ["Kind"] = ribbon ? "Ribbon" : "Sprite",
                        ["Renderer"] = renderer.Name,
                        ["Material"] = material,
                        ["SubImages"] = new[] { sub.X, sub.Y },
                        ["Facing"] = renderer.GetOrDefault<FName>("FacingMode").Text.Split("::").Last(),
                    },
                };
                break;
            }
            case "NiagaraLightRendererProperties":
            {
                // light per particle; the plugin makes the lights (no material)
                yield return new MaterialPorterMesh
                {
                    Name = $"{emitter.Name} light",
                    IsEmpty = true,
                    MPEffect = new Dictionary<string, object> { ["Kind"] = "Light", ["Renderer"] = renderer.Name },
                };
                break;
            }
            case "NiagaraDecalRendererProperties":
            {
                // The plugin draws a decal as a quad across its projection; the scene it projects onto isn't
                // exported, so a ground decal lies flat.
                if (EffectMaterial(RendererMaterial(system, renderer), 0, values) is not { } material) break;
                yield return new MaterialPorterMesh
                {
                    Name = $"{emitter.Name} decal",
                    IsEmpty = true,
                    MPEffect = new Dictionary<string, object> { ["Kind"] = "Decal", ["Renderer"] = renderer.Name, ["Material"] = material },
                };
                break;
            }
        }
    }
}
