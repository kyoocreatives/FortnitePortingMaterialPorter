using System.Collections.Generic;
using System.Linq;
using System.Threading.Tasks;
using CUE4Parse.UE4.VirtualFileSystem;
using FortnitePorting.Models.Files;

namespace FortnitePorting.Services;

// The flat view's items (one per game file, ~0.7 GB with its cache) are made the first time it is shown, not at load:
// the Blender editor and most sessions never open it.
public partial class FilesService
{
    private readonly object _flatViewGate = new();

    /// <summary>The game's files the Files page lists: (path, container).</summary>
    public IEnumerable<(string Path, string VfsName)> ListedFiles() =>
        UEParse.Provider.Files.Values
            .Where(file => IsValidFilePath(file.Path))
            .Select(file => (file.Path, file is VfsEntry entry ? entry.Vfs.Name : string.Empty));

    /// <summary>Fills the flat view's items once (off the UI thread); again after a reload cleared them.</summary>
    public Task EnsureFlatView() => Task.Run(() =>
    {
        if (UEParse.Provider is null) return;
        lock (_flatViewGate)
        {
            if (FlatViewAssetCache.Count > 0) return;
            var items = ListedFiles().Select(f => new FlatItem(f.Path, f.VfsName)).ToList();
            FlatViewAssetCache.Edit(updater => updater.AddOrUpdate(items));
        }
    });
}
