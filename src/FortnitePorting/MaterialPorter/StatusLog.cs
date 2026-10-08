using System;
using System.Collections.ObjectModel;
using Avalonia.Threading;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using Serilog.Core;
using Serilog.Events;

namespace FortnitePorting.MaterialPorter;

/// <summary>What an export does, as Material Porter's app shows it, newest first under a status line.</summary>
// Sources: FP's own log while an export runs, the bridge's answers to Blender, any warning, and what the Blender
// plugin reports while importing (the bridge's "log" route).
public partial class StatusLog : ObservableObject, ILogEventSink
{
    public static StatusLog Instance { get; } = new();

    const int MaxLines = 600;

    public ObservableCollection<string> Lines { get; } = [];

    [ObservableProperty] private string _lastLine = "Ready";
    [ObservableProperty] private bool _isOpen;
    [ObservableProperty] private bool _isBusy;
    [ObservableProperty] private string _busyWhat = string.Empty;

    int _exports;
    bool _blender;

    [RelayCommand]
    private void Clear() => Lines.Clear();

    /// <summary>A line of the log (from any thread); an indented one doesn't take the status line.</summary>
    public void Write(string line)
    {
        if (!Dispatcher.UIThread.CheckAccess())
        {
            Dispatcher.UIThread.Post(() => Write(line));
            return;
        }
        Lines.Insert(0, $"{DateTime.Now:HH:mm:ss}  {line}");
        while (Lines.Count > MaxLines) Lines.RemoveAt(Lines.Count - 1);
        if (!line.StartsWith("    ")) LastLine = line.Trim();
    }

    /// <summary>An export running while the returned scope lives: the status line says so.</summary>
    public IDisposable Exporting(string what)
    {
        Busy(ref _exports, +1, what);
        return new Scope(() => Busy(ref _exports, -1, null));
    }

    /// <summary>What the Blender plugin says (the bridge's "log" route): lines, and "begin"/"end" of an import.</summary>
    public void FromBlender(string? state, string[] lines)
    {
        foreach (var line in lines)
            Write("Blender  " + (line.StartsWith("[Material Porter] ", StringComparison.Ordinal) ? line["[Material Porter] ".Length..] : line));
        if (state == "begin")
        {
            _blender = true;
            Update("Blender importing");
        }
        else if (state == "end")
        {
            _blender = false;
            Update(null);
        }
    }

    void Busy(ref int count, int delta, string? what)
    {
        count = Math.Max(0, count + delta);
        Update(what);
    }

    void Update(string? what)
    {
        if (!Dispatcher.UIThread.CheckAccess())
        {
            Dispatcher.UIThread.Post(() => Update(what));
            return;
        }
        IsBusy = _exports > 0 || _blender;
        if (what is not null) BusyWhat = what;
        else if (!IsBusy) BusyWhat = string.Empty;
    }

    // Serilog: while something runs, FP's steps; always, the bridge's lines and warnings
    public void Emit(LogEvent logEvent)
    {
        if (logEvent.Level < LogEventLevel.Information) return;
        var text = Render(logEvent);
        var ours = text.StartsWith("[Material Porter]", StringComparison.Ordinal);
        if (logEvent.Level == LogEventLevel.Information && !ours && !IsBusy) return;
        if (ours) text = text["[Material Porter]".Length..].TrimStart();
        var prefix = logEvent.Level switch
        {
            LogEventLevel.Warning => "Warning: ",
            >= LogEventLevel.Error => "Error: ",
            _ => "",
        };
        Write(prefix + FirstLine(text));
    }

    /// <summary>The message with its string values as they are (Serilog's own rendering quotes them).</summary>
    static string Render(LogEvent logEvent)
    {
        var writer = new System.IO.StringWriter();
        foreach (var token in logEvent.MessageTemplate.Tokens)
        {
            if (token is Serilog.Parsing.PropertyToken property && logEvent.Properties.TryGetValue(property.PropertyName, out var value)
                && value is ScalarValue { Value: string plain })
                writer.Write(plain);
            else
                token.Render(logEvent.Properties, writer);
        }
        return writer.ToString();
    }

    static string FirstLine(string text)
    {
        var cut = text.IndexOfAny(['\r', '\n']);
        text = cut >= 0 ? text[..cut] : text;
        return text.Length > 400 ? text[..400] + "…" : text;
    }

    sealed class Scope(Action end) : IDisposable
    {
        Action? _end = end;
        public void Dispose()
        {
            _end?.Invoke();
            _end = null;
        }
    }
}
