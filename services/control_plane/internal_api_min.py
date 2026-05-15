"""Compatibility wrapper for the relocated minimal internal API module."""
from __future__ import annotations

import sys

from services.control_plane.internal import internal_api_min as _internal_api_min

sys.modules[__name__] = _internal_api_min
