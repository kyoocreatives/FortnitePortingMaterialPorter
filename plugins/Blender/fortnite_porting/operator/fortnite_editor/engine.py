"""The FP app as the editor's engine: found on its bridge, else launched hidden (--background --parent-pid), else the
reason it can't be (no exe, too old, setup unfinished). Requests run on worker threads; pump() hands their answers
and state changes to the caller's thread (the editor's timer), so drawing never waits."""
import json
import os
import queue
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

MIN_ENGINE = "mpv-1.8.0"
STATES = ("unknown", "starting", "loading", "ready", "error", "too_old", "no_exe", "setup")


def default_info_path():
    """engine.json the app writes at each start (its exe, port, live status), per profile like its other folders."""
    profile = os.environ.get("FORTNITEPORTING_MP_PROFILE", "").strip()
    folder = "FortnitePorting MP" + (" " + profile if profile else "")
    return os.path.join(os.environ.get("LOCALAPPDATA", ""), folder, "engine.json")


def _alive(pid):
    if not pid:
        return False
    try:
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
        if not handle:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return code.value == 259
    except (AttributeError, OSError):
        return False


class Engine:
    def __init__(self, url, exe_hint=None, info_path=None, launcher=subprocess.Popen):
        self.url = url
        self.exe_hint = exe_hint
        self.info_path = info_path or default_info_path()
        self.launcher = launcher
        self.state, self.status_text = "unknown", ""
        self._launched = self._attached = self._wanted = self._checking = False
        self._proc = None
        self.last_error = None      # the last answer callback that raised (shown by the editor)
        self._next_check = 0.0
        self._checks, self._jobs, self._done = queue.Queue(), queue.Queue(), queue.Queue()
        threading.Thread(target=self._check_loop, daemon=True, name="FP engine").start()
        for i in range(2):
            threading.Thread(target=self._request_loop, daemon=True, name="FP engine requests %d" % i).start()

    # ---------------------------------------------------------------- caller's thread

    def ensure(self):
        """Find, attach or launch the engine (non-blocking); later checks follow from pump()."""
        self._wanted = True
        self._next_check = 0.0
        self._schedule()

    def request(self, route, done, timeout=600, **query):
        self._jobs.put((route, query, done, timeout))

    def show(self):
        """The app's window shown (first-time setup, or to use the app itself): a plain launch asks a running app to
        show itself over its single-instance pipe, or opens it."""
        exe = self._exe()
        if exe:
            self.launcher([exe], cwd=os.path.dirname(exe))
        return exe is not None

    def pump(self):
        changed = False
        while not self._done.empty():
            kind, payload = self._done.get()
            if kind == "state":
                self._checking = False
                state, text = payload
                if (state, text) != (self.state, self.status_text):
                    self.state, self.status_text = state, text
                    changed = True
                self._next_check = time.time() + (5.0 if state == "ready" else 1.0)
            else:
                done, result = payload
                try:
                    quiet = done(result) is False       # a callback answers False when nothing it shows changed
                except Exception as e:      # one bad answer must not stop the editor's timer
                    self.last_error, quiet = "%s: %s" % (type(e).__name__, e), False
                changed |= not quiet
        if self._wanted and not self._checking and time.time() >= self._next_check:
            self._schedule()
        return changed

    # ---------------------------------------------------------------- worker threads

    def _schedule(self):
        if not self._checking:
            self._checking = True
            self._checks.put(None)

    def _get(self, route, timeout, **query):
        url = "%s/%s" % (self.url, route) + ("?" + urllib.parse.urlencode(query) if query else "")
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read())

    def _info(self):
        try:
            with open(self.info_path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _exe(self):
        recorded = self._info().get("exe")
        # a build run from its staging folder recorded a path that its swap into the live folder removed
        live = os.path.join(os.path.dirname(recorded).removesuffix("-staging"), os.path.basename(recorded)) if recorded else None
        for path in (os.environ.get("FORTNITEPORTING_MP_EXE"), self.exe_hint, recorded, live):
            if path and os.path.isfile(path):
                return path
        return None

    def _check(self):
        try:
            status = self._get("engine-status", 2)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return "too_old", "This FP can't drive the editor: update it to %s or newer." % MIN_ENGINE
            return "error", "FP answered %s" % e.code
        except (urllib.error.URLError, OSError, ValueError):
            self._attached = False      # a restarted engine must hear from this Blender again
            return self._unreachable()
        if not self._attached:
            try:
                self._get("engine-attach", 2, pid=os.getpid())
                self._attached = True
            except (urllib.error.URLError, OSError, ValueError):
                pass
        if not status.get("setup", True):
            return "setup", "FP needs its first-time setup: Show FP to finish it."
        if not status.get("loaded"):
            return "loading", status.get("status") or "Loading game files..."
        return "ready", ""

    def _unreachable(self):
        """No bridge: an engine still loading (its engine.json says so), one we launched, or launch one now."""
        info = self._info()
        if _alive(info.get("pid")):
            live = info.get("status") or {}
            if not live.get("setup", True):
                return "setup", "FP needs its first-time setup: Show FP to finish it."
            return "starting", live.get("status") or "Starting FP..."
        if self._launched:
            poll = getattr(self._proc, "poll", None)
            if poll is None or poll() is None:
                return "starting", "Starting FP..."
            self._launched = False      # it exited (closed after Show FP, or crashed while loading): start it again
        exe = self._exe()
        if not exe:
            return "no_exe", "FP isn't running and its exe is unknown: set it in the add-on preferences."
        try:
            self._proc = self.launcher([exe, "--background", "--parent-pid", str(os.getpid())], cwd=os.path.dirname(exe))
        except OSError as e:
            return "error", "Couldn't start FP: %s" % e
        self._launched = True
        return "starting", "Starting FP..."

    def _check_loop(self):
        while True:
            self._checks.get()
            try:
                result = self._check()
            except Exception as e:      # never leave the editor waiting on a dead check
                result = ("error", str(e))
            self._done.put(("state", result))

    def _request_loop(self):
        while True:
            route, query, done, timeout = self._jobs.get()
            try:
                result = self._get(route, timeout, **query)
            except Exception as e:      # handed to the caller, shown in the editor
                result = e
            self._done.put(("answer", (done, result)))
