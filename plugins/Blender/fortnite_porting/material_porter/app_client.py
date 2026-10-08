"""Client for the app's bridge (see Bridge.cs)."""
import hashlib
import http.client
import json
import os
import urllib.parse


class AppError(RuntimeError):
    pass


CONNECT_TIMEOUT = 3.0     # seconds
READ_TIMEOUT = 120.0      # a first texture may stream from Epic's CDN


def fetch_dir():
    """Folder for files fetched from the app, under Blender's own %LOCALAPPDATA%."""
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    d = os.path.join(base, "MaterialPorter", "fetched")
    os.makedirs(d, exist_ok=True)
    return d


class AppClient:
    def __init__(self, url):
        self.url = url.rstrip("/")
        self.memo = {}
        u = urllib.parse.urlsplit(self.url)
        self._host, self._port = u.hostname or "localhost", u.port or 80
        self._conn = None
        self.max_texture = 0        # texture size cap in pixels (0: full size)

    def _fetch(self, route, path):
        """(status, body) of GET /route?path=..., over one kept-alive connection."""
        query = {"path": path}
        if route == "texture" and self.max_texture:
            query["cap"] = self.max_texture
        target = "/%s?%s" % (route, urllib.parse.urlencode(query))
        for attempt in (0, 1):
            try:
                if self._conn is None:
                    # short connect timeout so a closed app doesn't hold the import, long read timeout
                    self._conn = http.client.HTTPConnection(self._host, self._port, timeout=CONNECT_TIMEOUT)
                    self._conn.connect()
                    self._conn.sock.settimeout(READ_TIMEOUT)
                self._conn.request("GET", target)
                r = self._conn.getresponse()
                body = r.read()
                if r.getheader("Connection", "").lower() == "close":
                    self._conn.close()
                    self._conn = None
                return r.status, body
            except (http.client.HTTPException, OSError):
                # idle connection closed by the app: retry once on a new one
                if self._conn is not None:
                    self._conn.close()
                self._conn = None
                if attempt:
                    raise

    def get(self, route, path):
        key = (route, path)
        if key not in self.memo:
            try:
                status, body = self._fetch(route, path)
            except OSError as e:
                raise AppError("the Material Porter app isn't answering at %s (%s)" % (self.url, e))
            except http.client.HTTPException as e:
                raise AppError("the Material Porter app's answer broke off (%s)" % e)
            if status == 200:
                self.memo[key] = json.loads(body.decode("utf-8"))
            else:
                try:
                    msg = json.loads(body.decode("utf-8")).get("error", str(status))
                except Exception:
                    msg = "HTTP %d" % status
                self.memo[key] = AppError("%s %s: %s" % (route, path, msg))
        v = self.memo[key]
        if isinstance(v, AppError):
            raise v
        return v

    def local(self, path):
        """Path Blender can open for a file the app exported: the app's path, else a copy fetched over the bridge."""
        if not path or os.path.exists(path):
            return path
        key = ("file", path)
        if key not in self.memo:
            # short name: the app's names run long and Blender's Python can't open paths past 260 characters
            base = os.path.basename(path.replace("\\", "/"))
            stem, ext = os.path.splitext(base)
            digest = hashlib.sha1(path.lower().encode("utf-8")).hexdigest()[:12]
            dest = os.path.join(fetch_dir(), "%s_%s%s" % (digest, stem.split("~")[-1][:48], ext))
            # refetched each job: the app may have written a better copy since (full mips streamed in)
            try:
                status, data = self._fetch("file", path)
            except (http.client.HTTPException, OSError) as e:
                raise AppError("Blender can't open %s and the app didn't hand it over (%s)" % (path, e))
            if status != 200:
                raise AppError("Blender can't open %s and the app didn't hand it over (HTTP %d)" % (path, status))
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as f:
                f.write(data)
            self.memo[key] = dest
        return self.memo[key]

    def graph(self, path):
        """A material or function graph's dump, as a local file."""
        return self.local(self.get("graph", path)["file"])

    def texture(self, path):
        """{"File", "Srgb", "Kind", "Width", "Height", "Depth", "Hdr"}, File local."""
        rec = dict(self.get("texture", path))
        rec["File"] = self.local(rec["File"])
        return rec

    def collection(self, path):
        """{"scalars": {...}, "vectors": {...}}"""
        return self.get("collection", path)
