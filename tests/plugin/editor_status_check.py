"""The editor's import log, headless: lines and steps, progress from "n/m", levels, the plugin's lines heard live,
the app's lines merged (its echo of the plugin's left out), and the status bar's row in the shell.

    blender -b --factory-startup --python-exit-code 1 -P editor_status_check.py -- <plugin parent>
"""
import sys
import time

sys.path.insert(0, sys.argv[sys.argv.index("--") + 1])
from fpmp_baseline.operator.fortnite_editor import logbook  # noqa: E402
from fpmp_baseline.operator.fortnite_editor import state as st  # noqa: E402
from fpmp_baseline.operator.fortnite_editor.ui import shell  # noqa: E402
from fpmp_baseline.operator.fortnite_editor.ui.theme import Theme  # noqa: E402

FAILS, PASSES = [], [0]


def check(name, ok, detail=""):
    if ok:
        PASSES[0] += 1
    else:
        FAILS.append(name)
        print("[editor_status_check] FAIL %s %s" % (name, detail))


logbook.clear()
check("nothing yet: Ready", logbook.latest() == ("Ready", "info"))
logbook.begin("Exporting Renegade Raider")
logbook.write("    mesh SK_Body (120 ms)")
check("a detail line doesn't take the status", logbook.latest()[0] == "Exporting Renegade Raider")
logbook.write("    actors 200/1400")
check("progress from n/m", abs(logbook.LOG["progress"] - 200 / 1400) < 1e-9, logbook.LOG["progress"])
logbook.write("Warning: a texture is missing", "warning")
check("a step takes the status, with its level", logbook.latest() == ("Warning: a texture is missing", "warning"))
check("levels", [logbook.level_of(t) for t in ("Import failed X", "Error: boom", "Warning: hm", "Imported X")]
      == ["error", "error", "warning", "info"])

# the plugin's lines, heard live (material_porter.status's listener), drawn mid-import
drawn = []
logbook.redraw_now = lambda: drawn.append(1)
logbook._heard("Importing Renegade Raider", "begin")
logbook._heard("    material M_Body: exact (0.4 s)", None)
logbook._heard("Imported Renegade Raider: 4 meshes", "end")
check("the plugin's lines in the log", [l[3] for l in list(logbook.LOG["lines"])[-3:]]
      == ["Importing Renegade Raider", "    material M_Body: exact (0.4 s)", "Imported Renegade Raider: 4 meshes"])
check("redrawn while it imports", len(drawn) == 3 and not logbook.LOG["importing"], len(drawn))


# the app's lines while it exports: its echo of the plugin's lines left out
class Engine:
    def request(self, route, done, **query):
        self.route, self.query = route, query
        if query["since"] > 10 ** 9:        # the catch-up: where the app's log is now
            done({"seq": 40, "lines": []})
            return
        done({"seq": 44, "lines": [{"seq": 41, "time": "", "text": "    texture T_Body_D 2048 35 ms"},
                                   {"seq": 42, "time": "", "text": "Blender  Importing Renegade Raider"},
                                   {"seq": 43, "time": "", "text": "Blender asked for texture T_Icon (12 ms)"},
                                   {"seq": 44, "time": "", "text": "Blender asked for browse-export Renegade Raider (2400 ms)"}]})


st.STATE["engine"] = engine = Engine()
logbook.LOG.update(polled=0.0, asking=False, route=True, since=None)
logbook.tick()
check("first, a catch-up to the app's current line (no replay of its history)", logbook.LOG["since"] == 40, logbook.LOG["since"])
before = len(logbook.LOG["lines"])
logbook.tick()
new = [l[3] for l in list(logbook.LOG["lines"])[before:]]
check("then the app's new lines: its echo of the plugin and the bridge's chatter left out, exports kept",
      engine.query["since"] == 40 and logbook.LOG["since"] == 44
      and new == ["    texture T_Body_D 2048 35 ms", "Blender asked for browse-export Renegade Raider (2400 ms)"], (engine.query, new))
logbook.end()
check("the job over", logbook.LOG["job"] is None)
logbook.LOG.update(polled=0.0)
engine.query = None
logbook.tick()
check("still asked a moment after the job (the app's summary comes as the export returns)", engine.query is not None)
logbook.LOG.update(polled=0.0, ended=0.0)
engine.query = None
logbook.tick()
check("not after that", engine.query is None)
logbook.clear()
logbook.write("Importing Renegade Raider")
logbook.write("Exported Renegade Raider: 4 meshes", when=logbook.LOG["lines"][-1][0] - 1.0)
check("a late line goes in its place by its own time", [l[3] for l in logbook.LOG["lines"]]
      == ["Exported Renegade Raider: 4 meshes", "Importing Renegade Raider"])
check("the app's clock read", abs(logbook._today(time.strftime("%H:%M:%S") + ".5") - time.time()) < 2
      and logbook._today("junk") is None)
st.STATE["engine"] = None
check("copy: every line stamped", logbook.text().count("\n") == len(logbook.LOG["lines"]) - 1 and ":" in logbook.text()[:3])


class Tab:
    side = info = (lambda ui, r: None)


t = Theme(1.0)
c = shell.columns(Tab(), 1300, 800, t, status=True)
m = shell.margin(t, 0)
check("the status bar along the bottom, the nav and panel above it",
      c["status"].y == m and c["status"].h == t.row and c["panel"].y == c["status"].top + t.pad
      and c["nav"].y == c["panel"].y and c["status"].x == c["nav"].x and c["status"].right == c["panel"].right, c)
check("no status bar unless asked", shell.columns(Tab(), 1300, 800, t)["status"] is None)

print("[editor_status_check] %d passed, %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
