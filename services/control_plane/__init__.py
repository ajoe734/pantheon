# Prefer the pure-Python internal API implementation for tests and scaffolding.
from .internal import internal_api_min

__all__ = ["internal_api_min"]
"""Import root for control-plane services.

The repository's historical on-disk directory is named ``control-plane``.
Expose that directory through this package so callers use the single valid
Python import root ``services.control_plane`` without mutating ``sys.path`` or
copying service modules into a parallel tree.
"""

from pathlib import Path


_LEGACY_SOURCE_ROOT = Path(__file__).resolve().parent.parent / "control-plane"
if _LEGACY_SOURCE_ROOT.is_dir():
    __path__.append(str(_LEGACY_SOURCE_ROOT))
