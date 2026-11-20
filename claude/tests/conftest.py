import sys
from pathlib import Path

# Make `ecci` importable when pytest is run from claude/ without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import os

# tests/kicad needs the real kicad-cli. It is collected only in the CI job that installs KiCad
# (ECCI_KICAD_TESTS=1), where a missing CLI is a FAILURE, not a skip. Elsewhere it is not collected at all,
# so no skipped test can be counted as passed.
collect_ignore = [] if os.environ.get("ECCI_KICAD_TESTS") == "1" else ["kicad"]
