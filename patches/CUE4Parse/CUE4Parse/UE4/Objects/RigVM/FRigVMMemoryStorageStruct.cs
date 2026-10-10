using CUE4Parse.UE4.Assets.Objects;
using CUE4Parse.UE4.Assets.Exports;
using CUE4Parse.UE4.Assets.Objects.Properties;
using System.Collections.Generic;
using CUE4Parse.UE4.Assets.Objects.Unversioned;
using CUE4Parse.UE4.Assets.Readers;
using CUE4Parse.UE4.Objects.Core.i18N;
using CUE4Parse.UE4.Objects.StructUtils;
using CUE4Parse.UE4.Objects.UObject;

namespace CUE4Parse.UE4.Objects.RigVM;

public struct FRigVMPropertyPathDescription(FAssetArchive Ar)
{
    public int PropertyIndex = Ar.Read<int>();
    public string HeadCPPType = Ar.ReadFString();
    public string SegmentPath = Ar.ReadFString();
}

public class FRigVMMemoryStorageStruct : FInstancedPropertyBag
{
    public ERigVMMemoryType MemoryType;
    public FRigVMPropertyPathDescription[] PropertyPathDescriptions;
    // MP: the registers' default values (literals: the rig's constants), by register name
    public Dictionary<string, object?>? Values;

    public FRigVMMemoryStorageStruct(FAssetArchive Ar) : base(Ar)
    {
        if (SerialSize > 0)
        {
            var end = Ar.Position;
            Ar.Position = end - SerialSize;
            try
            {
                Values = Ar.HasUnversionedProperties ? ReadValues(Ar) : ReadTagged(Ar);
            }
            catch (Exception e)
            {
                Log.Warning("[Material Porter] RigVM register values not read ({Error})", e.Message);
            }
            Ar.Position = end;
        }
        MemoryType = Ar.Read<ERigVMMemoryType>();
        PropertyPathDescriptions = Ar.ReadArray(() => new FRigVMPropertyPathDescription(Ar));
    }

    // an editor (versioned) package's bag: tagged properties, each naming itself
    private static Dictionary<string, object?> ReadTagged(FAssetArchive Ar)
    {
        var values = new Dictionary<string, object?>();
        foreach (var property in new FStructFallback(Ar).Properties)
            values[property.Name.Text] = property.Tag?.GenericValue;
        return values;
    }

    // the bag's struct in unversioned form: a header of present/zero properties, then each present value
    private Dictionary<string, object?> ReadValues(FAssetArchive Ar)
    {
        var values = new Dictionary<string, object?>();
        var header = new FUnversionedHeader(Ar);
        var index = 0;
        var zeroBit = 0;
        foreach (var fragment in header.Fragments)
        {
            index += fragment.SkipNum;
            for (var i = 0; i < fragment.ValueNum; i++, index++)
            {
                var desc = PropertyDescs[index];
                var zero = fragment.HasAnyZeroes && header.ZeroMask[zeroBit++];
                if (zero)
                {
                    values[desc.Name.Text] = null;
                    continue;
                }
                try
                {
                    values[desc.Name.Text] = ReadValue(Ar, desc);
                }
                catch (NotSupportedException)
                {
                    return values;      // a type not read here: the values before it stand
                }
            }
        }
        return values;
    }

    private static object? ReadValue(FAssetArchive Ar, FPropertyBagPropertyDesc desc)
    {
        var isArray = desc.ContainerTypes.Types is [EPropertyBagContainerType.Array, ..];
        if (!isArray) return ReadItem(Ar, desc);
        var items = new List<object?>();
        var count = Ar.Read<int>();
        for (var i = 0; i < count; i++) items.Add(ReadItem(Ar, desc));
        return items;
    }

    private static object? ReadItem(FAssetArchive Ar, FPropertyBagPropertyDesc desc) => desc.ValueType switch
    {
        EPropertyBagPropertyType.Bool => Ar.ReadByte() != 0,
        EPropertyBagPropertyType.Byte => Ar.ReadByte(),
        EPropertyBagPropertyType.Int32 => Ar.Read<int>(),
        EPropertyBagPropertyType.Int64 => Ar.Read<long>(),
        EPropertyBagPropertyType.Float => Ar.Read<float>(),
        EPropertyBagPropertyType.Double => Ar.Read<double>(),
        EPropertyBagPropertyType.Name => Ar.ReadFName().Text,
        EPropertyBagPropertyType.UInt32 => Ar.Read<uint>(),
        EPropertyBagPropertyType.UInt64 => Ar.Read<ulong>(),
        EPropertyBagPropertyType.String => Ar.ReadFString(),
        EPropertyBagPropertyType.Text => new FText(Ar),
        EPropertyBagPropertyType.Enum => Ar.ReadByte(),     // the bag's enums are uint8-backed: the value's index
        EPropertyBagPropertyType.Struct when desc.ValueTypeObject?.Name == "RigElementKey" => ReadElementKey(Ar),
        // any other struct as CUE4Parse reads it: native ones by type (Vector, Quat, Transform...), the rest by mappings
        EPropertyBagPropertyType.Struct when desc.ValueTypeObject?.Name is { } name =>
            new FScriptStruct(Ar, name, StructOf(desc), ReadType.NORMAL).StructType,
        EPropertyBagPropertyType.Object or EPropertyBagPropertyType.Class => new FPackageIndex(Ar),
        EPropertyBagPropertyType.SoftObject or EPropertyBagPropertyType.SoftClass => new FSoftObjectPath(Ar),
        _ => throw new NotSupportedException($"{desc.Name.Text}: {desc.ValueType} {desc.ValueTypeObject?.Name}"),
    };

    // a Blueprint struct (the deform rig's part values) has no mappings: its own asset gives the layout
    private static UStruct? StructOf(FPropertyBagPropertyDesc desc) =>
        desc.ValueTypeObject is { IsNull: false } index && index.TryLoad(out UStruct structure) ? structure : null;

    // FRigElementKey, an unversioned struct: Type (ERigElementType, a byte), Name
    private static string ReadElementKey(FAssetArchive Ar)
    {
        var header = new FUnversionedHeader(Ar);
        var parts = new string[2];
        var index = 0;
        var zeroBit = 0;
        foreach (var fragment in header.Fragments)
        {
            index += fragment.SkipNum;
            for (var i = 0; i < fragment.ValueNum; i++, index++)
            {
                if (fragment.HasAnyZeroes && header.ZeroMask[zeroBit++]) continue;
                parts[index] = index == 0 ? ((ERigElementTypeFlags) Ar.ReadByte()).ToString() : Ar.ReadFName().Text;
            }
        }
        return $"{parts[0] ?? "None"}/{parts[1] ?? "None"}";
    }

    [Flags]
    private enum ERigElementTypeFlags : byte
    {
        Bone = 1, Null = 2, Control = 4, Curve = 8, Physics = 16, Reference = 32, Connector = 64, Socket = 128,
    }
}
