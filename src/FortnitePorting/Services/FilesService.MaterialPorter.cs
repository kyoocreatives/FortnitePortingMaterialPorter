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

    // A file's place in the tree, as FP's split-per-file walk made it, but a segment's string is only made for a new node
    // (17 M segment strings over 2 M files took seconds).
    private void AddToTree(string path, string vfsName)
    {
        var parent = RootFileNode;
        RootFileNode.AddVfsName(vfsName);
        var start = 0;
        while (start < path.Length)
        {
            var end = path.IndexOf('/', start);
            if (end < 0) end = path.Length;
            if (end == start) { start++; continue; }
            var segment = path.AsSpan(start, end - start);
            var isFile = path.AsSpan(end).TrimStart('/').IsEmpty;
            if (!parent.TryGetChild(segment, out var child))
            {
                var part = segment.ToString();
                var nodePath = isFile ? path : string.Concat(path.AsSpan(0, path.IndexOf(part, StringComparison.Ordinal)), part);
                child = new FileNode(part, nodePath, isFile ? ENodeType.File : ENodeType.Folder, vfsName);
                parent.AddChild(part, child);
            }
            else child.AddVfsName(vfsName);
            parent = child;
            start = end + 1;
        }
    }

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
