"""Checks the version the plugin shows, and that the sidebar's first panel draws it.

    blender -b --factory-startup --python-exit-code 1 -P tests/plugin/version_label_check.py -- <plugin parent>

<plugin parent> holds the plugin as package fpmp_baseline (see translator_test.py). Exit code 0 when every check passes."""
import sys

sys.path.insert(0, sys.argv[sys.argv.index("--") + 1])
import fpmp_baseline  # noqa: E402,F401
from fpmp_baseline.material_porter.version_label import display  # noqa: E402
from fpmp_baseline.operator.convert_op import FPMP_PT_Exact  # noqa: E402

CASES = {
    "v4.3.4-mp.1.12.3": "fpv-4.3.4/mpv-1.12.3",
    "4.3.4-mp.154-1.13.0": "fpv-4.3.4/mpv-1.13.0",
    "v4.0.0-mp.153": "v4.0.0-mp.153",
    "v4.3.4-dev": "v4.3.4-dev",
}
fails = ["%s: %r" % (version, display(version)) for version, want in CASES.items() if display(version) != want]
if not hasattr(FPMP_PT_Exact, "draw_header_preset"):
    fails.append("the Materials panel has no version in its header")
for fail in fails:
    print("[version_label_check] FAIL", fail)
print("[version_label_check] %d failed" % len(fails))
sys.exit(1 if fails else 0)
