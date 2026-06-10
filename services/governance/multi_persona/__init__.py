"""Governance multi-persona sponsor resolution surface."""

from .conflict_resolution_log import (
    ClassifiedConflict,
    ConflictResolutionLog,
    ConflictVetoRecord,
    OpenConflict,
    validate_conflict_resolution_log,
)
from .sponsor_resolver import (
    SponsorResolvedProposal,
    SponsorResolver,
    SponsorResolverError,
    resolve_sponsor,
)

__all__ = [
    "ClassifiedConflict",
    "ConflictResolutionLog",
    "ConflictVetoRecord",
    "OpenConflict",
    "SponsorResolvedProposal",
    "SponsorResolver",
    "SponsorResolverError",
    "resolve_sponsor",
    "validate_conflict_resolution_log",
]
