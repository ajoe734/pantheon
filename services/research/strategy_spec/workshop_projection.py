"""Workshop-to-Registry version projection.

Implements the workshop version linkage model from:
  - sw001-deep-closure §5 (StrategySpec reference mapping)
  - sw001-deep-closure §7.3 (strategy_workshop_version_link table)
  - design-closure-round2 §A1 (ownership model)

Ownership rule:
  - Strategy Registry owns immutable StrategySpec versions.
  - Workshop owns proposals, conversation context, readiness links.
  - This module projects workshop-version links → Registry content.
  - It never copies StrategySpec JSON into workshop storage.

Usage:
  The BFF/store layer (strategy_workshop/store.py) calls these helpers to:
  - compute SHA-256 of the base document before issuing patch proposals
  - build version-link records for accepted patches
  - project the current workshop state onto the active Registry version
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .patching import compute_document_sha256


# ---------------------------------------------------------------------------
# Version link data model (mirrors strategy_workshop_version_link table)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class WorkshopVersionLink:
    """Immutable link between a workshop version and a Registry version.

    Corresponds to the ``strategy_workshop_version_link`` table defined in
    sw001-deep-closure §7.3.  The workshop never holds StrategySpec JSON;
    it holds only this link record.
    """
    workshop_version_id: str
    workshop_id: str
    strategy_id: str
    strategy_spec_registry_id: str
    sequence_no: int
    created_by: str
    created_at: str
    parent_workshop_version_id: str | None = None
    source_event_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "workshop_version_id": self.workshop_version_id,
            "workshop_id": self.workshop_id,
            "strategy_id": self.strategy_id,
            "strategy_spec_registry_id": self.strategy_spec_registry_id,
            "sequence_no": self.sequence_no,
            "created_by": self.created_by,
            "created_at": self.created_at,
        }
        if self.parent_workshop_version_id is not None:
            d["parent_workshop_version_id"] = self.parent_workshop_version_id
        if self.source_event_id is not None:
            d["source_event_id"] = self.source_event_id
        return d

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "WorkshopVersionLink":
        return cls(
            workshop_version_id=data["workshop_version_id"],
            workshop_id=data["workshop_id"],
            strategy_id=data["strategy_id"],
            strategy_spec_registry_id=data["strategy_spec_registry_id"],
            sequence_no=int(data["sequence_no"]),
            created_by=data["created_by"],
            created_at=data["created_at"],
            parent_workshop_version_id=data.get("parent_workshop_version_id"),
            source_event_id=data.get("source_event_id"),
        )


# ---------------------------------------------------------------------------
# Workshop projection
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class WorkshopProjection:
    """Read-only projection of the workshop's current strategy state.

    The projection is ephemeral; it is computed on demand from the workshop
    session data and the current Registry content.  It is never persisted.
    """
    workshop_id: str
    strategy_id: str | None
    active_workshop_version_id: str | None
    active_strategy_spec_registry_id: str | None
    document_sha256: str | None
    version_sequence_no: int | None

    def has_active_version(self) -> bool:
        return (
            self.active_workshop_version_id is not None
            and self.active_strategy_spec_registry_id is not None
        )


def build_projection(
    *,
    workshop_id: str,
    strategy_id: str | None,
    active_workshop_version_id: str | None,
    active_strategy_spec_registry_id: str | None,
    spec_doc: Mapping[str, Any] | None,
    version_sequence_no: int | None = None,
) -> WorkshopProjection:
    """Build a WorkshopProjection from workshop session data + Registry content.

    Args:
        workshop_id: The workshop session identifier.
        strategy_id: Strategy identifier if the workshop is linked to a strategy.
        active_workshop_version_id: Currently selected workshop version link id.
        active_strategy_spec_registry_id: Currently selected Registry version id.
        spec_doc: The StrategySpec document fetched from Registry. None when no
            strategy is linked yet.
        version_sequence_no: The sequence number of the active version link.

    Returns:
        A WorkshopProjection (immutable read-only view).
    """
    sha256 = compute_document_sha256(spec_doc) if spec_doc is not None else None
    return WorkshopProjection(
        workshop_id=workshop_id,
        strategy_id=strategy_id,
        active_workshop_version_id=active_workshop_version_id,
        active_strategy_spec_registry_id=active_strategy_spec_registry_id,
        document_sha256=sha256,
        version_sequence_no=version_sequence_no,
    )


def compute_patch_base(spec_doc: Mapping[str, Any]) -> str:
    """Return the SHA-256 of the canonical JSON form of a StrategySpec document.

    This value is the ``base_document_sha256`` a patch proposer must supply in
    a VersionPatchProposal, and the patch engine verifies it before applying.
    """
    return compute_document_sha256(spec_doc)


# ---------------------------------------------------------------------------
# Version link helpers
# ---------------------------------------------------------------------------

def build_version_link(
    *,
    workshop_version_id: str,
    workshop_id: str,
    strategy_id: str,
    strategy_spec_registry_id: str,
    sequence_no: int,
    created_by: str,
    created_at: str,
    parent_workshop_version_id: str | None = None,
    source_event_id: str | None = None,
) -> WorkshopVersionLink:
    """Create a new WorkshopVersionLink record.

    Called by the BFF after a patch proposal is accepted and the Registry has
    created a new immutable StrategySpec draft version.
    """
    if not workshop_version_id:
        raise ValueError("workshop_version_id is required")
    if not workshop_id:
        raise ValueError("workshop_id is required")
    if not strategy_id:
        raise ValueError("strategy_id is required")
    if not strategy_spec_registry_id:
        raise ValueError("strategy_spec_registry_id is required")
    if sequence_no < 1:
        raise ValueError("sequence_no must be >= 1")
    if not created_by:
        raise ValueError("created_by is required")
    if not created_at:
        raise ValueError("created_at is required")

    return WorkshopVersionLink(
        workshop_version_id=workshop_version_id,
        workshop_id=workshop_id,
        strategy_id=strategy_id,
        strategy_spec_registry_id=strategy_spec_registry_id,
        sequence_no=sequence_no,
        created_by=created_by,
        created_at=created_at,
        parent_workshop_version_id=parent_workshop_version_id,
        source_event_id=source_event_id,
    )


def select_version(
    version_links: Sequence[WorkshopVersionLink],
    workshop_version_id: str,
) -> WorkshopVersionLink | None:
    """Find a version link by workshop_version_id.

    Returns None if not found.
    This is the 'select-version' operation referenced in §17.2.
    """
    for link in version_links:
        if link.workshop_version_id == workshop_version_id:
            return link
    return None


def latest_version(
    version_links: Sequence[WorkshopVersionLink],
) -> WorkshopVersionLink | None:
    """Return the version link with the highest sequence_no, or None."""
    if not version_links:
        return None
    return max(version_links, key=lambda lnk: lnk.sequence_no)
