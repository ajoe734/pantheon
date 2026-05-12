"""Compatibility wrapper for the relocated internal API module."""
from __future__ import annotations

import sys

from services.control_plane.internal import internal_api as _internal_api

sys.modules[__name__] = _internal_api
