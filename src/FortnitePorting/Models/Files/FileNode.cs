using System.Collections.Concurrent;
using System.Collections.Generic;

namespace FortnitePorting.Models.Files;

public class FileNode
{
    // MP: files share their (empty) children and their one-container set: 2 M files each held their own (~500 MB)
    private static readonly Dictionary<string, FileNode> NoChildren = new();
    private static readonly ConcurrentDictionary<string, HashSet<string>> FileVfsNames = new();
    private Dictionary<string, FileNode>? _children;
    private string? _lastVfsName;

    public string Name { get; }
    public string Path { get; }
    public string VfsName { get; }
    public HashSet<string> VfsNames { get; private set; }
    public ENodeType Type { get; }
    public int FileChildCount { get; set; }
    public int FolderChildCount { get; set; }

    public Dictionary<string, FileNode> Children => _children ?? NoChildren;

    public FileNode(string name, string path, ENodeType type, string vfsName = "")
    {
        Name = name;
        Path = path;
        Type = type;
        VfsName = vfsName;
        VfsNames = type is ENodeType.File
            ? FileVfsNames.GetOrAdd(vfsName, v => string.IsNullOrEmpty(v) ? [] : [v])
            : string.IsNullOrEmpty(vfsName) ? [] : [vfsName];
    }

    public void AddVfsName(string vfsName)
    {
        if (ReferenceEquals(vfsName, _lastVfsName) || string.IsNullOrEmpty(vfsName)) return;     // a folder's files mostly share one container
        _lastVfsName = vfsName;
        if (VfsNames.Contains(vfsName)) return;
        if (Type is ENodeType.File) VfsNames = new HashSet<string>(VfsNames);     // a shared set is never changed
        VfsNames.Add(vfsName);
    }

    public void AddChild(string name, FileNode child)
    {
        (_children ??= new())[name] = child;

        if (child.Type is ENodeType.Folder)
            FolderChildCount++;
        else
            FileChildCount++;
    }

    public bool TryGetChild(string name, out FileNode child)
        => Children.TryGetValue(name, out child!);

    // MP: by a path's segment, without making its string
    public bool TryGetChild(System.ReadOnlySpan<char> name, out FileNode child)
    {
        child = null!;
        return _children is not null && _children.GetAlternateLookup<System.ReadOnlySpan<char>>().TryGetValue(name, out child!);
    }

    public void Clear()
    {
        _children?.Clear();
        VfsNames = [];
        _lastVfsName = null;
        FileChildCount = 0;
        FolderChildCount = 0;
    }
}
