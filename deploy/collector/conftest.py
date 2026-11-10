"""Pytest bootstrap for deploy/collector under --import-mode=importlib."""

from __future__ import annotations

import sys
from pathlib import Path

COLLECTOR_ROOT = Path(__file__).resolve().parent
if str(COLLECTOR_ROOT) not in sys.path:
    sys.path.insert(0, str(COLLECTOR_ROOT))
