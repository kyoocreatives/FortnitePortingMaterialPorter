using System;
using System.Threading.Tasks;
using FortnitePorting.Exporting.Models;
using FortnitePorting.Models;

namespace FortnitePorting.Exporting.Types;

public class BaseExport
{
    public string Name;
    public EExportType Type;
    public EPrimitiveExportType PrimitiveType => Type.PrimitiveType;

    protected Context.ExportContext Context;
    
    public BaseExport(string name, EExportType exportType, ExportDataMeta metaData)
    {
        Name = name;
        Type = exportType;

        Context = new Context.ExportContext(metaData);
        Context.TextureCap = metaData.Settings.TextureCapFor(exportType);     // MP
    }
    
    public async Task WaitForExports()
    {
        foreach (var task in Context.ExportTasks)
        {
            await task.WaitAsync(TimeSpan.FromSeconds(60));
        }
    }
}