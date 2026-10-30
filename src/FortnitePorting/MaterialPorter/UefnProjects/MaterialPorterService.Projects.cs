using System.Collections.Generic;
using System.Linq;
using FortnitePorting.Exporting.Models;
using FortnitePorting.Models.Map;

namespace FortnitePorting.MaterialPorter;

// The editor's Map tab on UEFN projects: their own group, and cells exported through the project's index.
public partial class MaterialPorterService
{
    static partial void ProjectMapFailed(string? status, ref bool failed) =>
        failed |= status == "The project's actors couldn't be read";

    static partial void ProjectMapKind(WorldPartitionMap map, ref string kind)
    {
        if (map is ProjectMap) kind = "project";
    }

    static partial void ProjectMapCells(WorldPartitionMap map, ExportDataMeta meta, List<string> gridPaths, string[] layers, ref bool handled)
    {
        if (map is not ProjectMap project) return;
        project.CellsExportMeta(meta, gridPaths.Select(ProjectMap.CellOf).ToList(), layers);
        handled = true;
    }
}
