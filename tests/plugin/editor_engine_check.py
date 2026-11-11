"""The Fortnite editor's engine client (operator/fortnite_editor/engine.py) against a fake bridge, headless.

    blender -b --factory-startup --python-exit-code 1 -P editor_engine_check.py -- <plugin parent>
"""
import json
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, sys.argv[sys.argv.index("--") + 1])
from fpmp_baseline.operator.fortnite_editor import engine as eng  # noqa: E402

FAILS, PASSES = [], [0]


def check(name, ok, detail=""):
    if ok:
        PASSES[0] += 1
    else:
        FAILS.append(name)
        print("[editor_engine_check] FAIL %s %s" % (name, detail))


class Fake:
    """A bridge: mode "ok" (loaded engine), "loading", "old" (no engine routes); records attach calls."""

    def __init__(self, mode="ok"):
        self.mode, self.attached, self.stopped = mode, [], []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                route, _, query = self.path.lstrip("/").partition("?")
                if fake.mode == "old" and route.startswith("engine-"):
                    self.send_response(404)
                    self.end_headers()
                    return
                if route == "engine-status":
                    body = {"version": "v4.0.0-dev", "setup": True, "loaded": fake.mode == "ok", "status": "Loading x", "window": False, "background": True}
                elif route == "engine-attach":
                    fake.attached.append(int(query.split("=")[1]))
                    body = {"attached": fake.attached}
                elif route == "engine-stop":
                    fake.stopped.append(int(query.split("=")[1]))
                    body = {"quitting": True, "shown": False, "others": False}
                elif route == "echo":
                    body = {"echo": query}
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
                data = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d" % self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


def settle(engine, until, seconds=8):
    end = time.time() + seconds
    while time.time() < end:
        engine.pump()
        if until(engine):
            return True
        time.sleep(0.05)
    return False


launched = []


def launcher(args, **kw):
    launched.append(args)

    class P:
        pid = 4242
    return P()


tmp = tempfile.mkdtemp()
info = os.path.join(tmp, "engine.json")

# --- nothing happens before Start: no check, attach or launch, even with an engine answering and an exe known
fake = Fake("ok")
dead = "http://127.0.0.1:9"
exe = os.path.join(tmp, "FortnitePortingMP.exe")
open(exe, "w").close()
json.dump({"exe": exe, "port": 9}, open(info, "w"))
for url in (fake.url, dead):
    e = eng.Engine(url, info_path=info, launcher=launcher)
    e.ensure()      # what a Refresh or the quick search asks
    time.sleep(1.5)
    e.pump()
    check("stopped until Start (%s)" % url, e.state == "stopped" and not e.on, e.state)
check("no launch or attach before Start", not launched and not fake.attached, (launched, fake.attached))
os.remove(info)

# --- an engine already answering: attach, never launch
e = eng.Engine(fake.url, info_path=info, launcher=launcher)
e.start()
check("attaches to a running engine", settle(e, lambda x: x.state == "ready") and fake.attached == [os.getpid()] and not launched,
      (e.state, fake.attached, launched))

# --- nothing answers, an exe is recorded: launch with the background flags, then ready once the bridge answers
json.dump({"exe": exe, "port": 9}, open(info, "w"))
e = eng.Engine(dead, info_path=info, launcher=launcher)
e.start()
ok = settle(e, lambda x: x.state == "starting")
check("launches when nothing answers", ok and launched and launched[0][0] == exe and "--background" in launched[0]
      and launched[0][launched[0].index("--parent-pid") + 1] == str(os.getpid()), (e.state, launched))
e.url = fake.url        # the launched engine starts answering
check("ready once the launched engine answers", settle(e, lambda x: x.state == "ready"), e.state)
check("launched only once", len(launched) == 1, launched)

# --- the recorded staging folder was swapped away: the live folder beside it
staged = os.path.join(tmp, "app-staging", "FortnitePortingMP.exe")
live = os.path.join(tmp, "app", "FortnitePortingMP.exe")
os.makedirs(os.path.dirname(live))
open(live, "w").close()
json.dump({"exe": staged, "port": 9}, open(info, "w"))
check("swapped staging path finds the live exe", eng.Engine(dead, info_path=info, launcher=launcher)._exe() == live)
os.remove(live)
check("no live exe beside it either", eng.Engine(dead, info_path=info, launcher=launcher)._exe() is None)

# --- no exe anywhere
os.remove(info)
launched.clear()
e = eng.Engine(dead, info_path=info, launcher=launcher, exe_hint="")
e.start()
check("missing exe reported", settle(e, lambda x: x.state == "no_exe") and "preferences" in e.status_text.lower() and not launched,
      (e.state, e.status_text))

# --- an app without the engine routes
old = Fake("old")
e = eng.Engine(old.url, info_path=info, launcher=launcher)
e.start()
check("old engine reported", settle(e, lambda x: x.state == "too_old") and eng.MIN_ENGINE in e.status_text, (e.state, e.status_text))

# --- loading engine: status text passed through
loading = Fake("loading")
e = eng.Engine(loading.url, info_path=info, launcher=launcher)
e.start()
check("loading shown", settle(e, lambda x: x.state == "loading") and "Loading x" in e.status_text, (e.state, e.status_text))

# --- answers are delivered by pump on the calling thread only
e = eng.Engine(fake.url, info_path=info, launcher=launcher)
seen = []
e.request("echo", lambda result: seen.append((threading.current_thread() is threading.main_thread(), result)), a="1")
time.sleep(1.0)
check("no answer before pump", not seen, seen)
settle(e, lambda x: bool(seen))
check("answers on main thread", seen and seen[0][0] and seen[0][1] == {"echo": "a=1"}, seen)

# --- a launched engine that died (closed after Show FP, or crashed while loading): launched again
class Dead:
    pid = 4343

    def poll(self):
        return 1


launched.clear()
def dying_launcher(args, **kw):
    launched.append(args)
    return Dead()


json.dump({"exe": exe, "port": 9}, open(info, "w"))
e = eng.Engine(dead, info_path=info, launcher=dying_launcher)
e.start()
settle(e, lambda x: len(launched) >= 2, seconds=6)
check("relaunched after the engine died", len(launched) >= 2, launched)

# --- an engine that went away and came back: attached again
fake2 = Fake("ok")
e = eng.Engine(fake2.url, info_path=info, launcher=launcher)
e.start()
settle(e, lambda x: x.state == "ready")
e.url = dead
settle(e, lambda x: x.state != "ready", seconds=8)
fake2.attached.clear()
e.url = fake2.url
settle(e, lambda x: x.state == "ready" and fake2.attached, seconds=8)
check("attaches again after the engine came back", fake2.attached == [os.getpid()], fake2.attached)

# --- Stop: engine-stop with this Blender's pid, then stopped; no checks after it
e = eng.Engine(fake.url, info_path=info, launcher=launcher)
e.start()
settle(e, lambda x: x.state == "ready")
fake.stopped.clear()
e.stop()
check("stop shows stopped at once", e.state == "stopped" and not e.on, e.state)
settle(e, lambda x: bool(fake.stopped), seconds=4)
check("stop asks the app to quit", fake.stopped == [os.getpid()], fake.stopped)
fake.attached.clear()
time.sleep(1.5)
e.pump()
check("no checks after stop", e.state == "stopped" and not fake.attached, (e.state, fake.attached))
e.start()
check("start again reconnects", settle(e, lambda x: x.state == "ready"), e.state)


# --- Stop while our launched engine is still loading (no bridge yet): it's terminated
class Running:
    pid, ended = 4545, False

    def poll(self):
        return 0 if self.ended else None

    def terminate(self):
        self.ended = True


procs = []


def running_launcher(args, **kw):
    procs.append(Running())
    return procs[-1]


json.dump({"exe": exe, "port": 9}, open(info, "w"))
e = eng.Engine(dead, info_path=info, launcher=running_launcher)
e.start()
settle(e, lambda x: x.state == "starting")
e.stop()
settle(e, lambda x: procs and procs[0].ended, seconds=4)
check("stop terminates a launch whose bridge isn't up", procs and procs[0].ended and e.state == "stopped", (procs, e.state))

# --- a callback that raises doesn't escape pump (the editor's timer would stop for good)
e = eng.Engine(fake.url, info_path=info, launcher=launcher)
e.request("echo", lambda result: 1 / 0, a="1")
time.sleep(1.0)
try:
    end = time.time() + 5
    while time.time() < end and not e.last_error:
        e.pump()
        time.sleep(0.05)
    check("a raising callback is caught", "ZeroDivisionError" in (e.last_error or ""), e.last_error)
except Exception as ex:
    check("a raising callback is caught", False, repr(ex))

# --- the guided setup each state shows (setup_page.guide)
from fpmp_baseline.operator.fortnite_editor import setup_page as sp  # noqa: E402

g = sp.guide("stopped")
check("stopped: Start is the main action", g["main"][2] == "start" and g["other"] is None, g)
check("every state short of ready has a guide", all(sp.guide(s) for s in eng.STATES if s != "ready"))
check("ready: none", sp.guide("ready") is None)
g = sp.guide("no_exe", "FP isn't running")
check("no exe: locate first, step 1 now", g["main"][2] == "locate" and [h for _, h in g["steps"]] == ["now", "todo", "todo"],
      g)
g = sp.guide("loading", "Loading game files... 40%")
check("loading: two steps done, its progress as the note", [h for _, h in g["steps"]] == ["done", "done", "now"]
      and g["note"] == "Loading game files... 40%" and g["main"] is None, g)
g = sp.guide("too_old", "update it to mpv-1.8.0 or newer")
check("too old: the engine's words as the body", "mpv-1.8.0" in g["body"] and not g["note"], g)

print("[editor_engine_check] %d passed, %d failed" % (PASSES[0], len(FAILS)))
if FAILS:
    sys.exit(1)
