"""What an import does, sent to the FP app's status log.

The plugin's log lines (FP's Log, the exact materials' notes) and an import's begin, end and
summary go to the app's bridge ("log" route) and show in its log drawer under the status line.
They are sent from a background thread in batches, so the import never waits on them, and nothing
is sent when the app isn't listening.
"""
import http.client
import json
import os
import queue
import re
import threading
import time
import urllib.parse

URL = os.environ.get("MATERIAL_PORTER_BRIDGE", "http://localhost:24320")

BATCH_SECONDS = 0.3     # seconds of lines gathered into one request
BATCH_LINES = 30        # past this, a batch keeps its first and last lines and counts the rest
MAX_QUERY = 6000        # characters of lines per request

_ANSI = re.compile(r"\x1b\[[0-9;]*m")    # console colours printed by FP's Log
_queue = queue.Queue()
_thread = None
_lock = threading.Lock()


def post(line, state=None):
    """Send the app a line (and/or an import's "begin"/"end")."""
    global _thread
    if line is not None:
        line = _ANSI.sub("", str(line))
    _queue.put((state, line))
    with _lock:
        if _thread is None or not _thread.is_alive():
            _thread = threading.Thread(target=_run, name="fp-status", daemon=True)
            _thread.start()


def _run():
    u = urllib.parse.urlsplit(URL)
    host, port = u.hostname or "localhost", u.port or 80
    conn = None
    down_until = 0.0
    while True:
        try:
            first = _queue.get(timeout=30)
        except queue.Empty:
            return      # idle: the next post starts a new thread
        items = [first]
        deadline = time.perf_counter() + BATCH_SECONDS
        while True:
            left = deadline - time.perf_counter()
            if left <= 0:
                break
            try:
                items.append(_queue.get(timeout=left))
            except queue.Empty:
                break
        if time.perf_counter() < down_until:
            continue    # the app didn't answer a moment ago: drop, don't stall
        for state, lines in _batches(items):
            query = {"lines": json.dumps(lines)}
            if state:
                query["state"] = state
            try:
                if conn is None:
                    conn = http.client.HTTPConnection(host, port, timeout=2)
                conn.request("GET", "/log?" + urllib.parse.urlencode(query))
                conn.getresponse().read()
            except (OSError, http.client.HTTPException):
                if conn is not None:
                    conn.close()
                conn = None
                down_until = time.perf_counter() + 5.0
                break


def _batches(items):
    """(state, lines) requests: a state change sends what came before it, and a long run of
    lines keeps its first and last ones and counts the rest."""
    out, lines = [], []

    def flush(state=None):
        nonlocal lines
        if len(lines) > BATCH_LINES:
            keep = BATCH_LINES // 2
            lines = lines[:keep] + ["    (%d more lines)" % (len(lines) - 2 * keep)] + lines[-keep:]
        chunk, size = [], 0
        for line in lines:
            if size + len(line) > MAX_QUERY and chunk:
                out.append((None, chunk))
                chunk, size = [], 0
            chunk.append(line[:500])
            size += len(line)
        if chunk or state:
            out.append((state, chunk))
        lines = []

    for state, line in items:
        if line is not None:
            lines.append(line)
        if state:
            flush(state)
    if lines:
        flush()
    return out
