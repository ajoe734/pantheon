"""Agora domain write-owner services and authority gates."""
from __future__ import annotations

from .service import AgoraWriteService, build_agora_write_service
from .store import AgoraStore, DictRecord, build_agora_store
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
    "build_agora_store",
    "build_agora_write_service",
    "is_authorized",
    "matrix_as_list",
]
