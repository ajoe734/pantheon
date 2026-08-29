"""Agora domain write-owner services and authority gates."""
from __future__ import annotations

from .service import AgoraWriteService
from .store import AgoraStore, DictRecord
from .write_authority import (
    AgoraWriteForbiddenError,
    WRITE_AUTHORITY_MATRIX,
    assert_authorized,
    is_authorized,
    matrix_as_list,
)

__all__ = [
    "AgoraStore",
    "AgoraWriteForbiddenError",
    "AgoraWriteService",
    "DictRecord",
    "WRITE_AUTHORITY_MATRIX",
    "assert_authorized",
    "is_authorized",
    "matrix_as_list",
]
