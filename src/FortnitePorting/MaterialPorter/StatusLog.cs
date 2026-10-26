using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Linq;
using Avalonia.Threading;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using Serilog.Core;
using Serilog.Events;

namespace FortnitePorting.MaterialPorter;

/// <summary>What an export does, as Material Porter's app shows it, newest first under a status line.</summary>
// Sources: FP's own log while an export runs, the export's steps (ExportTrace), the bridge's answers to Blender, any
// warning, and what the Blender plugin reports while importing (the bridge's "log" route). Since() serves the same
// lines, numbered, to a client that polls.
public partial class StatusLog : ObservableObject, ILogEventSink
{
    public static StatusLog Instance { get; } = new();

    const int MaxLines = 600;
    const int MaxEntries = 2000;

    public ObservableCollection<string> Lines { get; } = [];

    /// <summary>A line as the bridge serves it: numbered in the order written, with its time.</summary>
    public readonly record struct Entry(long Seq, DateTime Time, string Text);

    readonly Queue<Entry> _entries = new();
    long _seq;

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
        Entry entry;
        lock (_entries)
        {
            entry = new Entry(++_seq, DateTime.Now, line);
            _entries.Enqueue(entry);
            while (_entries.Count > MaxEntries) _entries.Dequeue();
        }
        if (Dispatcher.UIThread.CheckAccess()) Show(entry);
        else Dispatcher.UIThread.Post(() => Show(entry));
    }

    /// <summary>The newest sequence number and the lines after <paramref name="since"/>, oldest first.</summary>
    public (long Seq, Entry[] Lines) Since(long since, int most)
    {
        lock (_entries)
            return (_seq, _entries.Where(e => e.Seq > since).Take(most).ToArray());
    }

    void Show(Entry entry)
    {
        Lines.Insert(0, $"{entry.Time:HH:mm:ss}  {entry.Text}");
        while (Lines.Count > MaxLines) Lines.RemoveAt(Lines.Count - 1);
        if (!entry.Text.StartsWith("    ")) LastLine = entry.Text.Trim();
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
        // FP's line per file written and per actor read: ExportTrace says it with sizes and times, and sums up past a budget
        if (logEvent.MessageTemplate.Text is "Exporting {ExportType}: {Path}" or "Processing {ActorName}: {CurrentActor}/{TotalActors}") return;
        var text = Render(logEvent);
        var ours = text.StartsWith("[Material Porter]", StringComparison.Ordinal);
        if (logEvent.Level == LogEventLevel.Information && !ours && !IsBusy) return;
        if (ours) text = Unprefixed(text);
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

    // one space goes with the prefix: an indented line keeps its indent (a detail line)
    static string Unprefixed(string text)
    {
        text = text["[Material Porter]".Length..];
        return text.StartsWith(' ') ? text[1..] : text;
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
