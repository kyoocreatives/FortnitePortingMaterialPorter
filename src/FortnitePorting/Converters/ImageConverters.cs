using System;
using System.Globalization;
using Avalonia.Data.Converters;
using FortnitePorting.Extensions;

namespace FortnitePorting.Converters;

public class ExportTypeIconConverter : IValueConverter
{
    public object? Convert(object? value, Type targetType, object? parameter, CultureInfo culture)
    {
        if (value is EExportType exportType)
        {
            return MaterialPorter.TabIcons.Of(exportType); // MP: a type without an icon of its own takes the plain one
        }
        
        return null;
    }

    public object? ConvertBack(object? value, Type targetType, object? parameter, CultureInfo culture)
    {
        throw new NotImplementedException();
    }
}