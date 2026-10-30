using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using CUE4Parse.FileProvider.Objects;
using CUE4Parse.FileProvider.Vfs;
using FortnitePorting.Exporting.MaterialPorter;
using Serilog;

namespace FortnitePorting.MaterialPorter;

/// <summary>A UEFN project mounted into the game's provider; Maps are package paths ("/Mount/Rel"), its main map first.</summary>
public sealed record MountedProject(string Name, string Mount, string Folder, string ContentDir, int Files, IReadOnlyList<string> Maps);

/// <summary>Local UEFN projects (IslandProjects' roots) added to the game's provider as loose files, so the Files
/// tab, the exporter and Material Porter read them like the game's own. Read-only: files are only opened to read.</summary>
public static class ProjectFiles
{
    static readonly string[] Extensions = [".uasset", ".umap", ".uexp", ".ubulk"];

    public static IReadOnlyList<MountedProject> Mounted { get; private set; } = [];
    public static IReadOnlyList<string> Skipped { get; private set; } = [];

    /// <summary>Where project map indexes are kept (ProjectMapIndex).</summary>
    public static string MapCacheDir => Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "MaterialPorter", "cache", "project-maps");

    /// <summary>Adds every project's files as one layer above the game's; returns how many files were added.</summary>
    public static int Register(AbstractVfsFileProvider provider)
    {
        if (!Fork.Islands || provider.Files is not FileProviderDictionary files) return 0;
        MaterialPorterService.ApplyProjectFolders();
        var added = new Dictionary<string, GameFile>(StringComparer.OrdinalIgnoreCase);
        var mounted = new List<MountedProject>();
        var skipped = new List<string>();
        // each project's files listed in parallel (the disk is the cost), then kept in discovery order
        var projects = IslandProjects.All;
        var game = GameMounts(provider);
        var listed = new (MountedProject? Project, Dictionary<string, GameFile>? Files, string? Error)[projects.Count];
        System.Threading.Tasks.Parallel.For(0, projects.Count, i => listed[i] = List(projects[i], provider));
        for (var i = 0; i < projects.Count; i++)
        {
            var (project, projectFiles, error) = listed[i];
            if (error != null) skipped.Add($"{projects[i].Name}: {error}");
            if (project == null) continue;     // nothing synced, or unreadable
            if (game.Contains(project.Mount))
            {
                // a published project's mount is its island's: the unlocked island's cooked files keep it
                skipped.Add($"{project.Name}: mount {project.Mount} is the game's or a mounted island's (export the island from Maps)");
                continue;
            }
            if (mounted.FirstOrDefault(m => m.Mount.Equals(project.Mount, StringComparison.OrdinalIgnoreCase)) is { } first)
            {
                skipped.Add($"{project.Name}: mount {project.Mount} already used by {first.Name}");
                continue;
            }
            foreach (var (key, file) in projectFiles!) added[key] = file;
            mounted.Add(project);
        }
        // only files the provider hasn't got yet: another layer of the same keys would list them twice (a changed file is
        // read from disk anyway; a deleted one stays until restart)
        var fresh = added.Where(kv => !files.ContainsKey(kv.Key)).ToDictionary(kv => kv.Key, kv => kv.Value, StringComparer.OrdinalIgnoreCase);
        if (fresh.Count > 0) files.AddFiles(fresh, long.MaxValue - 1);
        foreach (var project in mounted)
            provider.VirtualPaths.TryAdd(project.Mount, ProjectPaths.Prefix(project.Mount).TrimEnd('/'));
        ProjectPaths.Mounts = mounted.Select(m => m.Mount).ToHashSet(StringComparer.OrdinalIgnoreCase);
        Mounted = mounted;
        Skipped = skipped;
        Log.Information("[Material Porter] UEFN projects: {Count} mounted ({Files} files), {Skipped} skipped",
            mounted.Count, added.Count, skipped.Count);
        return added.Count;
    }

    // One project's files as provider entries, with its maps (the one named after the project first).
    static (MountedProject?, Dictionary<string, GameFile>?, string?) List(IslandProjects.Project project, AbstractVfsFileProvider provider)
    {
        try
        {
            var content = ContentDir(project);
            if (content == null) return (null, null, null);     // only revision-control data, nothing synced
            var baseDir = Directory.GetParent(content)!;
            var prefix = ProjectPaths.Prefix(project.Mount);
            var files = new Dictionary<string, GameFile>(StringComparer.OrdinalIgnoreCase);
            var maps = new List<string>();
            foreach (var file in baseDir.EnumerateFiles("*.uplugin").Concat(new DirectoryInfo(content).EnumerateFiles("*", SearchOption.AllDirectories)))
            {
                var ext = file.Extension.ToLowerInvariant();
                if (ext != ".uplugin" && !Extensions.Contains(ext)) continue;
                var gameFile = new ProjectGameFile(baseDir, file, prefix, provider.Versions);
                files[gameFile.Path] = gameFile;
                if (ext == ".umap")
                    maps.Add("/" + project.Mount + "/" + Path.GetRelativePath(content, file.FullName).Replace('\\', '/')[..^5]);
            }
            var main = "/" + project.Mount + "/" + project.Mount;
            maps = maps.OrderBy(m => !m.Equals(main, StringComparison.OrdinalIgnoreCase)).ThenBy(m => m, StringComparer.OrdinalIgnoreCase).ToList();
            return (new MountedProject(project.Name, project.Mount, project.Folder, content, files.Count, maps), files, null);
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException)
        {
            return (null, null, e.Message);
        }
    }

    /// <summary>Game feature plugins the game's own containers hold (islands included), by folder name.</summary>
    static HashSet<string> GameMounts(AbstractVfsFileProvider provider)
    {
        var mounts = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (var vfs in provider.MountedVfs)
            foreach (var key in vfs.Files.Keys)
                if (key.StartsWith(ProjectPaths.GameFeatures, StringComparison.OrdinalIgnoreCase))
                {
                    var end = key.IndexOf('/', ProjectPaths.GameFeatures.Length);
                    if (end > 0) mounts.Add(key[ProjectPaths.GameFeatures.Length..end]);
                }
        return mounts;
    }

    /// <summary>Mounts of the game's decrypted islands ("FortniteGame/Plugins/GameFeatures/<guid>/").</summary>
    public static HashSet<string> IslandMounts(AbstractVfsFileProvider provider)
    {
        const string marker = "/Plugins/GameFeatures/";
        var mounts = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (var vfs in provider.MountedVfs)
        {
            if (!vfs.Name.Equals("plugin.utoc", StringComparison.OrdinalIgnoreCase)) continue;
            var at = vfs.MountPoint.IndexOf(marker, StringComparison.OrdinalIgnoreCase);
            if (at >= 0) mounts.Add(vfs.MountPoint[(at + marker.Length)..].Split('/')[0]);
        }
        return mounts;
    }

    /// <summary>The project's Content folder (at its root, or in Plugins/Mount), or null when it holds no package.</summary>
    public static string? ContentDir(IslandProjects.Project project)
    {
        foreach (var dir in new[] { Path.Combine(project.Folder, "Content"), Path.Combine(project.Folder, "Plugins", project.Mount, "Content") })
            if (Directory.Exists(dir) && Directory.EnumerateFiles(dir, "*.u*", SearchOption.AllDirectories)
                    .Any(f => f.EndsWith(".uasset", StringComparison.OrdinalIgnoreCase) || f.EndsWith(".umap", StringComparison.OrdinalIgnoreCase)))
                return dir;
        return null;
    }
}
