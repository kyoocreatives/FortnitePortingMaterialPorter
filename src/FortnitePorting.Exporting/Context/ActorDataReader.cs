using System;
using System.Collections.Generic;
using System.IO;
using System.Text;

namespace FortnitePorting.Exporting.Context;

/// <summary>
/// Material Porter fork: a level save record template's actor data in the reference-table
/// format (a header, then UE 5 tagged properties whose object values index the template's
/// ActorDataReferenceTable). CUE4Parse's reader stops one byte short of the properties (the
/// class serialization control byte) and drops them all - a prefab's walls, rugs and crates
/// lost their texture data (Rebel's Roost). Older records (UE5 file version before 1012:
/// Neon City's, saved by 26.00) have the old property tags.
/// </summary>
public static class ActorDataReader
{
    public const uint Magic = 0xA2189DE9;

    public readonly record struct Property(string Name, string Type, int ArrayIndex, byte Flags, byte[] Value)
    {
        /// <summary>An object property's index into the reference table (-1: not one).</summary>
        public int ReferenceIndex => Type == "ObjectProperty" && Value.Length == 4 ? BitConverter.ToInt32(Value, 0) : -1;
    }

    /// <summary>The properties, or null where the data isn't in this format (or is cut short).</summary>
    public static List<Property>? Read(byte[]? data)
    {
        if (data is null || data.Length < 4) return null;
        try
        {
            using var stream = new MemoryStream(data);
            using var reader = new BinaryReader(stream);
            if (reader.ReadUInt32() != Magic) return null;
            reader.ReadInt32();                         // FileVersionUE4 (522)
            var fileVersionUE5 = reader.ReadInt32();    // 1009 (26.00) .. 1013 (37.x)
            stream.Position += 2 + 2 + 2 + 4;   // engine version: major, minor, patch, changelist
            ReadString(reader);                 // and branch ("++Fortnite+Main")
            stream.Position += 4 + 2;       // 0xFFFFFFFF, 01 03
            var customVersions = reader.ReadInt32();
            stream.Position += customVersions * 20L;   // (guid, version)

            // UObject::SerializeScriptProperties: EClassSerializationControlExtension first
            // (PROPERTY_TAG_EXTENSION_AND_OVERRIDABLE_SERIALIZATION, UE5 1011)
            if (fileVersionUE5 >= UE5PropertyTagExtension)
            {
                var control = reader.ReadByte();
                if ((control & 0x02) != 0) reader.ReadByte();
            }

            var properties = new List<Property>();
            while (true)
            {
                var name = ReadString(reader);
                if (name.Length == 0 || name == "None") break;
                if (fileVersionUE5 < UE5CompleteTypeName)
                {
                    properties.Add(ReadOldTag(reader, name, fileVersionUE5));
                    continue;
                }
                var type = ReadTypeName(reader);
                var size = reader.ReadInt32();
                var flags = reader.ReadByte();
                var arrayIndex = (flags & 0x01) != 0 ? reader.ReadInt32() : 0;
                if ((flags & 0x02) != 0) stream.Position += 16;         // property guid
                if ((flags & 0x04) != 0)
                {
                    var extension = reader.ReadByte();
                    if ((extension & 0x02) != 0) stream.Position += 1 + 4;  // override operation, experimental logic (bool)
                }
                properties.Add(new Property(name, type, arrayIndex, flags, reader.ReadBytes(size)));
            }
            return properties;
        }
        catch (Exception e)
        {
            MaterialPorter.Failures.Note("prefab actor data", "unreadable", e);
            return null;
        }
    }

    const int UE5PropertyTagExtension = 1011;
    const int UE5CompleteTypeName = 1012;

    // A tag before PROPERTY_TAG_COMPLETE_TYPE_NAME (a prefab saved by 26.00: Neon City's): type,
    // size, array index, the type's own names, a guid flag
    static Property ReadOldTag(BinaryReader reader, string name, int fileVersionUE5)
    {
        var type = ReadString(reader);
        var size = reader.ReadInt32();
        var arrayIndex = reader.ReadInt32();
        byte flags = 0;
        switch (type)
        {
            case "StructProperty":
                type += "(" + ReadString(reader) + ")";
                reader.BaseStream.Position += 16;   // struct guid
                break;
            case "BoolProperty":
                if (reader.ReadByte() != 0) flags |= 0x10;
                break;
            case "ByteProperty" or "EnumProperty" or "ArrayProperty" or "SetProperty" or "OptionalProperty":
                type += "(" + ReadString(reader) + ")";
                break;
            case "MapProperty":
                type += "(" + ReadString(reader) + "," + ReadString(reader) + ")";
                break;
        }
        if (reader.ReadByte() != 0) reader.BaseStream.Position += 16;     // property guid
        if (fileVersionUE5 >= UE5PropertyTagExtension)
        {
            var extension = reader.ReadByte();
            if ((extension & 0x02) != 0) reader.BaseStream.Position += 1 + 4;
        }
        if (arrayIndex != 0) flags |= 0x01;
        return new Property(name, type, arrayIndex, flags, reader.ReadBytes(size));
    }

    static string ReadString(BinaryReader reader)
    {
        var length = reader.ReadInt32();
        if (length == 0) return string.Empty;
        if (length < 0)
            return Encoding.Unicode.GetString(reader.ReadBytes(-length * 2)).TrimEnd('\0');
        return Encoding.Latin1.GetString(reader.ReadBytes(length)).TrimEnd('\0');
    }

    // FPropertyTypeName: a name and its parameters, depth first ("StructProperty(LinearColor)")
    static string ReadTypeName(BinaryReader reader)
    {
        var name = ReadString(reader);
        var count = reader.ReadInt32();
        if (count == 0) return name;
        var parameters = new string[count];
        for (var i = 0; i < count; i++) parameters[i] = ReadTypeName(reader);
        return name + "(" + string.Join(",", parameters) + ")";
    }
}
