using System;
using System.Collections.Generic;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using Newtonsoft.Json.Linq;
using Serilog;

namespace FortnitePorting.MaterialPorter;

/// <summary>UEFN island export for people the owner gave a key: a signed file kept in the settings folder, which updates
/// don't replace. Checked once at start; there is no button or menu for it.</summary>
public static class UefnAccess
{
    private const string PublicKey = """
        -----BEGIN PUBLIC KEY-----
        MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEBzxST5BPqQbRSIoUDE+/Hpjg3ZKQ
        oGDyhQSX76vV6hBIF30xO4Rm7L4hvYqZMNdtUDwRle+Zfu2F/iyLFZ043w==
        -----END PUBLIC KEY-----
        """;

    // ids of keys taken back
    private static readonly HashSet<string> Revoked = [];

    private static string KeyFile => Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), Fork.AppFolder, "uefn-access.key");

    public static bool Granted { get; } = ReadKey();

    private static bool ReadKey()
    {
        try
        {
            if (!File.Exists(KeyFile)) return false;
            var valid = Check(File.ReadAllText(KeyFile));
            if (!valid) Log.Warning("[Material Porter] uefn-access.key isn't valid");
            return valid;
        }
        catch (IOException e)
        {
            Log.Warning("[Material Porter] uefn-access.key unreadable: {Error}", e.GetType().Name);
            return false;
        }
    }

    /// <summary>Whether this text is "payload.signature", signed by the owner, and not taken back.</summary>
    public static bool Check(string text)
    {
        var parts = text.Trim().Split('.');
        if (parts.Length != 2) return false;
        try
        {
            using var key = ECDsa.Create();
            key.ImportFromPem(PublicKey);
            if (!key.VerifyData(Encoding.ASCII.GetBytes(parts[0]), Unpad(parts[1]), HashAlgorithmName.SHA256, DSASignatureFormat.Rfc3279DerSequence))
                return false;
            var id = JObject.Parse(Encoding.UTF8.GetString(Unpad(parts[0])))["id"]?.ToString();
            return !string.IsNullOrEmpty(id) && !Revoked.Contains(id);
        }
        catch (Exception e) when (e is FormatException or CryptographicException or Newtonsoft.Json.JsonException)
        {
            return false;
        }
    }

    private static byte[] Unpad(string text)
    {
        var b64 = text.Replace('-', '+').Replace('_', '/');
        return Convert.FromBase64String(b64.PadRight(b64.Length + (4 - b64.Length % 4) % 4, '='));
    }
}
