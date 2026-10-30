"""The fork's version as the app shows it (fpv-4.3.4/mpv-1.12.3), from the meta file the app writes at install."""
import functools
import json
import os
import re

_PATTERN = re.compile(r"^v?(\d+\.\d+\.\d+)-mp\.(?:\d+-)?(\d+\.\d+\.\d+)$")


def display(version):
    match = _PATTERN.match(version)
    return "fpv-%s/mpv-%s" % match.groups() if match else version


# drawn on every sidebar redraw; the meta file only changes with an install, which needs a Blender restart
@functools.lru_cache(maxsize=1)
def version_label():
    meta_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fortnite_porting_meta.json")
    try:
        with open(meta_path, "r") as f:
            return display(json.load(f).get("Version", ""))
    except (OSError, ValueError):
        return ""
