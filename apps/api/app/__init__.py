"""ForteVision API package.

Adds the project's own packages (``vision``, ``rules``) to ``sys.path`` so the
service can be started with a plain ``uvicorn app.main:app`` from any directory.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
for _extra in ("packages/vision", "packages/rules", "apps/api"):
    _path = str(ROOT / _extra)
    if _path not in sys.path:
        sys.path.insert(0, _path)
