"""Import frozen sources only in the migration regression suite."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "legacy" / "whispersync"), str(ROOT / "legacy" / "forge")]
