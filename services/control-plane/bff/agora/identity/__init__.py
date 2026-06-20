"""Agora identity sub-module — capability: agora.identity.v1 + agora.session.v1."""

from .scope import (
    AgoraScopeResolutionError,
    agora_read_predicate,
    agora_record_matches_user_scope,
    filter_agora_user_records,
    resolve_agora_user_scope,
)

__all__ = [
    "AgoraScopeResolutionError",
    "agora_read_predicate",
    "agora_record_matches_user_scope",
    "filter_agora_user_records",
    "resolve_agora_user_scope",
]
