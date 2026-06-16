"""Make ``nested_countries`` importable when scripts are run directly.

Editable install (`pip install -e .` / `uv pip install -e .`) makes this
unnecessary, but adding ``src`` to sys.path lets the scripts also run straight
from a fresh checkout.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if _SRC.exists() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
