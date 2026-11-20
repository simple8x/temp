import sys
from pathlib import Path

# Make `ecci` importable when pytest is run from claude/ without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
