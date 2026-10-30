using System;
using System.IO;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using FortnitePorting.MaterialPorter;
using FortnitePorting.Models.Map;
using Serilog;

namespace FortnitePorting.ViewModels;

// Local UEFN projects on the Map page: each project's World Partition maps (an asset pack's levels aren't maps to
// export), the one named after the project first.
public partial class MapViewModel
{
    partial void AddProjectMaps()
    {
        if (!Fork.Islands) return;
        foreach (var project in ProjectFiles.Mounted)
            foreach (var map in project.Maps)
            {
                var rel = map[(project.Mount.Length + 2)..].Replace('/', Path.DirectorySeparatorChar);
                if (!Directory.Exists(Path.Combine(project.ContentDir, "__ExternalActors__", rel))) continue;
                Maps.Add(new ProjectMap(project, map, map.Equals("/" + project.Mount + "/" + project.Mount, StringComparison.OrdinalIgnoreCase)));
            }
    }

    partial void RefreshProjectList() => _ = RefreshProjectsAsync();

    /// <summary>The map list read again, without Reload's confirmation.</summary>
    public Task LoadMapsQuietlyAsync() => LoadMapsAsync();

    static int _refreshing;

    /// <summary>Re-reads the projects (new and changed files), then the file list and the maps; once at a time.</summary>
    public async Task RefreshProjectsAsync()
    {
        if (!Fork.Islands || Interlocked.Exchange(ref _refreshing, 1) == 1) return;
        try
        {
            await Task.Run(() => ProjectFiles.Register(UEParse.Provider));
            await FortnitePorting.Application.AppServices.Files.Initialize();
            await LoadMapsAsync();
        }
        catch (Exception e)
        {
            Log.Error(e, "[Material Porter] UEFN projects couldn't be refreshed");
        }
        finally
        {
            Interlocked.Exchange(ref _refreshing, 0);
        }
    }
}
