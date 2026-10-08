using System;
using System.Collections.Generic;
using System.Linq;
using CUE4Parse.UE4.Assets.Exports;
using CUE4Parse.UE4.Assets.Exports.Material;
using CUE4Parse.UE4.Assets.Exports.SkeletalMesh;
using CUE4Parse.UE4.Assets.Exports.Texture;
using CUE4Parse.UE4.Assets.Objects;
using CUE4Parse.UE4.Objects.Engine;
using CUE4Parse.UE4.Objects.UObject;
using FortnitePorting.Exporting.MaterialPorter;
using FortnitePorting.Exporting.Models;
using FortnitePorting.Shared.Extensions;

namespace FortnitePorting.Exporting.Context;

/// <summary>
/// A weapon as the game draws it. Many weapons get their look beyond the mesh's own materials:
/// - the actor class's weapon mesh component: material overrides and custom primitive data;
/// - a wrap, e.g. the item's own IntrinsicOverrideWrap.
/// A wrap is a material instance of M_FN_Customization_MASTER, which only carries the customization layer
/// (MF_Base_Customization) that every weapon master also runs. The game lays the wrap's values over the
/// weapon's own material, which keeps its textures; the exact materials do the same.
/// </summary>
public partial class ExportContext
{
    /// <summary>An object, then the archetypes it inherits from (child defaults first).</summary>
    private static IEnumerable<UObject> Archetypes(UObject? o)
    {
        for (var guard = 0; o != null && guard < 8; guard++)
        {
            yield return o;
            o = o.Template?.Load();
        }
    }

    /// <summary>The picked wrap: null for the item's own, "" for none, else a wrap item's path.</summary>
    public string? WrapPick;
    /// <summary>Picked mods by slot: a mod item's path, "" for none; an absent slot keeps the weapon's own.</summary>
    public Dictionary<string, string> WeaponModPicks = [];

    /// <summary>The weapon's meshes as the game shows them: actor class look, mods on their attach points, own wrap (a picked wrap goes on later in MeshExport).</summary>
    public void WeaponLook(UObject weaponDefinition, List<ExportMesh> meshes)
    {
        if (meshes.Count == 0) return;
        try
        {
            if (weaponDefinition.TryGetValue(out UBlueprintGeneratedClass actorClass, "WeaponActorClass")
                && actorClass.ClassDefaultObject?.Load() is { } defaults)
            {
                WeaponComponentLook(defaults, "WeaponMesh", meshes, 0);
                WeaponComponentLook(defaults, "LeftHandWeaponMesh", meshes, 1);
                if (EffectsPick) WeaponEffects(actorClass, meshes[0]);
            }

            WeaponModMeshes(weaponDefinition, meshes[0]);

            if (WrapPick is null && weaponDefinition.GetOrDefault<FSoftObjectPath>("IntrinsicOverrideWrap").TryLoad(out UObject? wrap)
                && WrapValues(wrap) is { } values)
            {
                foreach (var mesh in meshes) ApplyWrapDeep(mesh, values);
            }
        }
        catch (Exception e)
        {
            // fall back to the plain weapon
            Serilog.Log.Warning("[Material Porter] {Weapon}: its look wasn't read ({Error})", weaponDefinition.Name, e.Message);
        }
    }

    /// <summary>The weapon's mods as children of its mesh, each following its attach bone: its own WeaponModSlots (unless hidden) or the picked ones.</summary>
    private void WeaponModMeshes(UObject weaponDefinition, ExportMesh weapon)
    {
        var provider = FileProvider;
        var own = WeaponMods.Defaults(provider, weaponDefinition);
        if (own is null && WeaponModPicks.Count == 0) return;
        if (WeaponDefinitionMeshes(weaponDefinition).FirstOrDefault() is not USkeletalMesh weaponMesh) return;
        var hidden = weaponDefinition.GetOrDefault("bModsHidden", false);
        var tags = WeaponMods.Tags(weaponDefinition);
        foreach (var slot in WeaponMods.Slots)
        {
            WeaponMod? mod = null;
            if (WeaponModPicks.TryGetValue(slot, out var picked))
            {
                if (picked.Length > 0) mod = WeaponMods.Find(provider, picked);
            }
            else if (!hidden) own?.TryGetValue(slot, out mod);
            if (mod is null) continue;

            if (WeaponMods.Place(provider, mod, tags, weaponMesh) is not { } placement || Mesh(placement.Mesh) is not { } mesh)
            {
                Serilog.Log.Information("[Material Porter] {Weapon}: {Mod} has no place on it", weaponDefinition.Name, mod.Name);
                continue;
            }
            weapon.Children.Add(new MaterialPorterMesh(mesh)
            {
                Name = mod.Item.Name,
                Location = placement.Transform.Translation,
                Rotation = placement.Transform.Rotator(),
                Scale = placement.Transform.Scale3D,
                MPParentBone = placement.Bone,
            });
        }
    }

    /// <summary>Applies a wrap to a mesh and its child meshes (weapon mods).</summary>
    public void ApplyWrapDeep(ExportMesh mesh, ParamSet wrap)
    {
        ApplyWrap(mesh, wrap);
        foreach (var child in mesh.Children) ApplyWrapDeep(child, wrap);
    }

    /// <summary>Applies a weapon mesh component's material overrides and custom primitive data to the mesh it shows.</summary>
    private void WeaponComponentLook(UObject defaults, string property, List<ExportMesh> meshes, int index)
    {
        if (index >= meshes.Count) return;
        UObject? component = null;
        foreach (var d in Archetypes(defaults))
        {
            if (!d.TryGetValue(out UObject found, property)) continue;
            component = found;
            break;
        }
        if (component is null) return;

        var mesh = meshes[index];
        float[]? primitiveData = null;
        foreach (var c in Archetypes(component))
        {
            var overrides = c.GetOrDefault("OverrideMaterials", Array.Empty<UMaterialInterface?>());
            for (var slot = 0; slot < overrides.Length; slot++)
            {
                // nearest class wins; the item's own WeaponMaterialOverrides are already there
                if (overrides[slot] is not { } material || mesh.OverrideMaterials.Any(m => m.Slot == slot)) continue;
                mesh.OverrideMaterials.AddIfNotNull(Material(material, slot));
            }
            if (primitiveData is null && c.TryGetValue(out FStructFallback custom, "CustomPrimitiveData")
                && custom.GetOrDefault<float[]>("Data") is { Length: > 0 } floats)
                primitiveData = floats;
        }
        if (primitiveData is not null)
            meshes[index] = new MaterialPorterMesh(mesh) { MPPrimitiveData = primitiveData };
    }

    /// <summary>A wrap item's values by path; null when there's no such wrap.</summary>
    public ParamSet? WrapValuesAt(string path) =>
        FileProvider.TryLoadPackageObject(path, out UObject? wrap) ? WrapValues(wrap) : null;

    /// <summary>A wrap's values: everything its material instance chain sets (child wins).</summary>
    public ParamSet? WrapValues(UObject wrap)
    {
        if (!wrap.GetOrDefault<FSoftObjectPath>("ItemWrapMaterial").TryLoad(out UMaterialInstanceConstant? material))
            return null;
        var values = new ParamSet { Label = wrap.Name };
        for (UUnrealMaterial? current = material; current is UMaterialInstanceConstant instance; current = instance.Parent)
        {
            foreach (var p in instance.ScalarParameterValues) values.Scalars.TryAdd(p.Name, p.ParameterValue);
            foreach (var p in instance.VectorParameterValues)
                if (p.ParameterValue is { } c) values.Vectors.TryAdd(p.Name, [c.R, c.G, c.B, c.A]);
            foreach (var p in instance.TextureParameterValues)
                if (p.ParameterValue.Load<UTexture>() is { } texture) values.Textures.TryAdd(p.Name, texture.GetPathName());
            foreach (var p in instance.StaticParameters?.StaticSwitchParameters ?? [])
                values.Switches.TryAdd(p.Name, p.Value);
        }
        return values;
    }

    /// <summary>Applies a wrap to each of a mesh's materials. The plugin lays its values over the material's own, except textures the material sets itself.</summary>
    public void ApplyWrap(ExportMesh mesh, ParamSet wrap)
    {
        var slots = mesh.Materials.Select(m => m.Slot).Concat(mesh.OverrideMaterials.Select(m => m.Slot)).Distinct().ToList();
        foreach (var slot in slots)
        {
            var at = mesh.OverrideMaterials.FindIndex(m => m.Slot == slot);
            var material = at >= 0 ? mesh.OverrideMaterials[at] : mesh.Materials.First(m => m.Slot == slot);
            var wrapped = new MaterialPorterMaterial(material with { Slot = slot })
            {
                MPValues = (material as MaterialPorterMaterial)?.MPValues,
                MPWrap = wrap,
                Hash = HashCode.Combine(material.Hash, wrap.Key()),
            };
            if (at >= 0) mesh.OverrideMaterials[at] = wrapped;
            else mesh.OverrideMaterials.Add(wrapped);
        }
    }
}
