using FortnitePorting.MaterialPorter;

namespace FortnitePorting.Services;

public partial class CUE4ParseService
{
    // Local UEFN projects, as loose files keyed like game feature plugins (their .uplugin maps "/<Mount>/" there).
    partial void RegisterProjectFiles() => ProjectFiles.Register(Provider);
}
