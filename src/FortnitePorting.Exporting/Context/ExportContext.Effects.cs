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
/// Material Porter fork: a particle effect (a Niagara system) as what it is made of. Each enabled
/// emitter becomes an empty, and under it what its renderers draw: a mesh renderer's meshes with
/// the materials it puts on them, a sprite or ribbon renderer's material on a plane the plugin
/// makes. A renderer's own material parameters ride on the material (MPValues). A CPU emitter
/// keeps its compiled scripts, which the plugin runs to play its particles (the system's node
/// carries what that takes: Effects.Program); a GPU emitter keeps only a compiled shader, and
/// stays the pieces it draws.
/// </summary>
public partial class ExportContext
{
    /// <summary>Whether an item's export takes its own effects along (the Effects pick of its page).</summary>
    public bool EffectsPick;

    /// <summary>
    /// What the picked styles do to the item's effects: a system a style swaps for another (a part's
    /// NS_Empty for the style's own effect: VariantParticles) and the user parameters a style sets on a
    /// system (its colours and floats: VariantParticleParams), by system path.
    /// </summary>
    public readonly Dictionary<string, FSoftObjectPath> EffectSwaps = new(StringComparer.OrdinalIgnoreCase);
    public readonly Dictionary<string, Dictionary<string, object>> EffectUser = new(StringComparer.OrdinalIgnoreCase);

    /// <summary>A picked style's effect swaps and user parameters (EffectSwaps, EffectUser).</summary>
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
                    // (the values are engine structs: read as JSON)
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

    /// <summary>A system path as the picked styles swap it.</summary>
    private FSoftObjectPath Swapped(FSoftObjectPath path) =>
        EffectSwaps.TryGetValue(path.AssetPathName.Text, out var swapped) ? swapped : path;

    private readonly System.Runtime.CompilerServices.ConditionalWeakTable<ExportMesh, Dictionary<string, ExportSocket>> _meshSockets = new();

    /// <summary>
    /// A mesh's sockets, kept for the effects put on it: a skeletal mesh's own and its skeleton's, each
    /// on its bone; a static mesh's, on the mesh itself (no bone).
    /// </summary>
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

    /// <summary>
    /// One of an item's own effects, under its mesh: the effect (Effect) with what it is ("trail",
    /// "swing", "idle", "event": Role), the socket it sits on (MPParentBone) and where it sits there
    /// (Place). In Blender it is on the item's armature and reads its bones and sockets.
    /// </summary>
    private MaterialPorterMesh? OwnEffect(ExportMesh mesh, UObject? system, string role, string? socket, FTransform? place = null)
    {
        if (system is null || Effect(system) is not MaterialPorterMesh effect) return null;
        var node = effect.MPEffect ??= new Dictionary<string, object> { ["Kind"] = "System" };
        node["Role"] = role;
        // the user parameters the picked styles set on it (its colours): the replay's, over the asset's own
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
    /// A pickaxe's own effects (its weapon definition's trail, swing and idle effects): the swing's and
    /// the idle's on their sockets, the trail told the two sockets it runs between. Its hit effects
    /// ("impact": what a swing leaves where it hits, a system a surface) sit at its trail's far socket
    /// (the head, where it hits), unplayed until a swing's hits or Replay Effect.
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

    /// <summary>A character part's idle effect (a back bling's glow, an outfit's aura), on its socket of the part's mesh.</summary>
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

    /// <summary>
    /// A sprite's own effect (its definition's NiagaraSystem: a fire sprite's flames), playing on the bone
    /// the game's held sprite puts it on (BP_Weapon_Extractable's ExtractableFX: spine_4_bind).
    /// </summary>
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

    /// <summary>
    /// A glider's trails, each on its socket, played as the locker shows them once the glider is out
    /// (the glider needn't fly): front end, fully deployed (a speed line's opacity waits for it).
    /// </summary>
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

    /// <summary>
    /// A weapon's own effects: its actor class's Niagara components, each on the socket it is attached
    /// to. One that plays by itself is "idle"; one the game plays on an event (a reload, a level up)
    /// is "event": it comes along unplayed, for Replay Effect.
    /// </summary>
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
            // laid out in a row, 2 m apart: a palette to pick from (in the game they all sit at the system's origin)
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

    /// <summary>A renderer's material parameters (scalars, vectors, textures it sets on its materials), or null.</summary>
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
        // a texture parameter bound to a curve of the system's, exposed as a texture (a colour ramp), or
        // to a user parameter's texture (its own, until the game sets another); a value bound to the
        // system's variables is the plugin's (the replay has them: effect_replay.bindings)
        foreach (var p in parameters.GetOrDefault("AttributeBindings", Array.Empty<FStructFallback>()))
        {
            // (the variables are Niagara structs: read as JSON)
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

    /// <summary>A sprite's, ribbon's or decal's material: the user parameter's it binds (its own, until the game sets another), else its own.</summary>
    private static UMaterialInterface? RendererMaterial(UObject system, UObject renderer)
    {
        var bound = renderer.Properties.FirstOrDefault(p => p.Name.Text == "MaterialUserParamBinding")?.Tag?.GenericValue;
        if (Effects.UserBinding(bound) is { } user && Effects.UserObject(system, user) is UMaterialInterface own) return own;
        return renderer.GetOrDefault<UMaterialInterface?>("Material");
    }

    private ExportMaterial? EffectMaterial(UMaterialInterface? material, int slot, ParamSet? values)
    {
        // an instance the system keeps inside itself (a renderer's own, with its parameters): the asset it
        // is an instance of, with the instance's values over it
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
                    // (a mesh whose own slot holds no material still has the slot: the override's)
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
                // a flipbook: each particle shows one sub-image, which the material picks (the plugin's env.uv)
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
                // a point light a particle: the plugin makes the lights (no material)
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
                // a decal a particle: the plugin draws it as a quad across its projection (a decal lies on
                // the scene it projects onto, which isn't here: a ground decal lies flat)
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
